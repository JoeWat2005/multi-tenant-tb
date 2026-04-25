"""Consumes OrderQueue jobs and applies them.

Order placement goes to Alpaca (paper or live, depending on the tenant's
session.mode and the global ALPACA_PAPER kill-switch). We still maintain a
per-tenant virtual balance on `session.current_balance`: every tenant has
their own synthetic starting balance even though the real orders all clear
through the same Alpaca account. This keeps multi-tenant P&L isolated
without giving each tenant their own Alpaca keys.

Alpaca crypto does NOT support server-side bracket / OCO orders, so SL/TP
legs are recorded locally and the client-side OcoTriggerService watches
prices to enqueue EXIT_POSITION_MARKET when a level is hit.

One worker task per process. Jobs from every tenant go through the same
queue; each job opens its own DB session so there's no cross-tenant state
contamination.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from alpaca.common.exceptions import APIError
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest

from app import state
from app.db.session import SessionLocal
from app.models.bot_config import BotConfig
from app.models.equity_snapshot import EquitySnapshot
from app.models.order import Order
from app.models.position import Position
from app.models.trade import Trade
from app.models.trading_session import TradingSession
from app.services.orchestration.order_queue import (
    CANCEL_OPEN_BUY_ORDERS,
    EXIT_POSITION_MARKET,
    PLACE_MARKET_BUY,
    PLACE_OCO_SELL,
    SYNC_ORDER_STATUS,
)
from app.services.shared import data_feeds
from app.services.tenant import position_service, session_service

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.services.orchestration.order_queue import OrderJob, OrderQueue


class ExecutionWorker:
    def __init__(self, queue: "OrderQueue") -> None:
        self.queue = queue
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._task is not None:
            return
        self._loop = asyncio.get_running_loop()
        self._task = asyncio.create_task(self._run(), name="execution-worker")
        logger.info("ExecutionWorker started")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                job = await self.queue.get()
            except asyncio.CancelledError:
                return
            try:
                await self._handle(job)
            except Exception:
                logger.exception("ExecutionWorker: job %s failed", job.type)
            finally:
                self.queue.task_done()

    # ── Dispatch ──────────────────────────────────────────────────────────

    async def _handle(self, job: "OrderJob") -> None:
        handler = {
            PLACE_MARKET_BUY: self._place_market_buy,
            PLACE_OCO_SELL: self._place_oco_sell,
            CANCEL_OPEN_BUY_ORDERS: self._cancel_open_buy_orders,
            EXIT_POSITION_MARKET: self._exit_position_market,
            SYNC_ORDER_STATUS: self._sync_order_status,
        }.get(job.type)
        if handler is None:
            logger.warning("No handler for job type %s", job.type)
            return
        await asyncio.to_thread(handler, job)

    # ── Handlers (run in threads — synchronous DB + REST) ─────────────────

    def _place_market_buy(self, job: "OrderJob") -> None:
        db = SessionLocal()
        try:
            session, cfg = self._load_session_and_config(db, job)
            if session is None or cfg is None:
                return
            if session.status != session_service.RUNNING:
                logger.info("Skipping BUY — session %s status=%s", session.id, session.status)
                return

            size_pct = float(job.payload.get("size_pct", cfg.position_size_pct))
            sl_pct = float(job.payload.get("sl_pct", cfg.stop_loss_pct))
            tp_pct = float(job.payload.get("tp_pct", cfg.take_profit_pct))

            symbol = data_feeds.to_alpaca_symbol(cfg.symbol)
            ref_price = self._current_price(symbol)
            if ref_price <= 0:
                logger.warning("BUY skipped — no market price for %s", symbol)
                return

            notional = session.current_balance * (size_pct / 100.0)
            if notional <= 0 or notional > session.current_balance:
                notional = min(notional, session.current_balance)
            if notional <= 0:
                logger.warning("BUY skipped — zero notional (balance=%s)", session.current_balance)
                return

            client_order_id = f"ct-buy-{uuid.uuid4().hex[:20]}"
            alpaca_order = self._submit_market_order(
                session=session,
                symbol=symbol,
                side=OrderSide.BUY,
                notional=notional,
                client_order_id=client_order_id,
            )
            if alpaca_order is None:
                return

            fill_price = _fill_price(alpaca_order, fallback=ref_price)
            qty = _fill_qty(alpaca_order, fallback=notional / fill_price if fill_price > 0 else 0)
            fee = _fill_fee(alpaca_order)
            cost = fill_price * qty

            trade = Trade(
                tenant_id=job.tenant_id,
                session_id=session.id,
                position_id=None,
                symbol=symbol,
                side="BUY",
                order_type="MARKET",
                price=fill_price,
                quantity=qty,
                fee=fee,
                status="filled",
                alpaca_order_id=str(alpaca_order.id) if alpaca_order else None,
                alpaca_client_order_id=client_order_id,
                filled_at=datetime.now(timezone.utc),
            )
            db.add(trade)
            db.flush()

            sl_price = fill_price * (1 - sl_pct / 100.0)
            tp_price = fill_price * (1 + tp_pct / 100.0)

            pos = position_service.open_position(
                db,
                tenant_id=job.tenant_id,
                session_id=session.id,
                symbol=symbol,
                entry_price=fill_price,
                quantity=qty,
                size_pct=size_pct,
                stop_loss_price=sl_price,
                take_profit_price=tp_price,
            )
            trade.position_id = pos.id

            session.current_balance -= (cost + fee)
            self._write_equity_snapshot(db, session)
            db.commit()

            # Record local SL/TP legs so OcoTriggerService can watch them.
            if self._loop is not None:
                self.queue.put_threadsafe(
                    self._loop,
                    tenant_id=job.tenant_id,
                    session_id=session.id,
                    type=PLACE_OCO_SELL,
                    payload={"position_id": pos.id, "sl_price": sl_price, "tp_price": tp_price},
                )

        finally:
            db.close()

    def _place_oco_sell(self, job: "OrderJob") -> None:
        """Alpaca crypto can't attach a server-side OCO, so this just records
        two local Order rows that OcoTriggerService scans every few seconds."""
        db = SessionLocal()
        try:
            pos = db.query(Position).filter(Position.id == job.payload["position_id"]).first()
            if pos is None or pos.status != "open":
                return

            group_id = f"oco-{pos.id}-{uuid.uuid4().hex[:6]}"
            sl_price = float(job.payload["sl_price"])
            tp_price = float(job.payload["tp_price"])

            db.add(Order(
                tenant_id=job.tenant_id, session_id=job.session_id, position_id=pos.id,
                symbol=pos.symbol, side="SELL", type="STOP_LOSS",
                status="active", quantity=pos.quantity, stop_price=sl_price,
                oco_group_id=group_id,
            ))
            db.add(Order(
                tenant_id=job.tenant_id, session_id=job.session_id, position_id=pos.id,
                symbol=pos.symbol, side="SELL", type="TAKE_PROFIT",
                status="active", quantity=pos.quantity, price=tp_price,
                oco_group_id=group_id,
            ))
            db.commit()
            logger.info("OCO attached to position %s (SL %.4f / TP %.4f)",
                        pos.id, sl_price, tp_price)
        finally:
            db.close()

    def _cancel_open_buy_orders(self, job: "OrderJob") -> None:
        db = SessionLocal()
        try:
            rows = (
                db.query(Order)
                .filter(
                    Order.session_id == job.session_id,
                    Order.side == "BUY",
                    Order.status.in_(["pending", "submitted"]),
                )
                .all()
            )
            for r in rows:
                r.status = "cancelled"
                if r.alpaca_order_id:
                    self._safe_cancel_alpaca_order(job.session_id, r.alpaca_order_id)
            db.commit()
            if rows:
                logger.info("Cancelled %d pending BUY orders (session=%s)",
                            len(rows), job.session_id)
        finally:
            db.close()

    def _exit_position_market(self, job: "OrderJob") -> None:
        db = SessionLocal()
        try:
            pos = db.query(Position).filter(Position.id == job.payload["position_id"]).first()
            if pos is None or pos.status != "open":
                # Still give session_service a chance to finalise.
                session_service.finalise_if_done(db, job.session_id)
                return

            # Cancel active local SL/TP legs first.
            legs = (
                db.query(Order)
                .filter(Order.position_id == pos.id, Order.status == "active")
                .all()
            )
            for leg in legs:
                leg.status = "cancelled"
            db.flush()

            session = db.query(TradingSession).filter(TradingSession.id == job.session_id).first()
            if session is None:
                return

            symbol = data_feeds.to_alpaca_symbol(pos.symbol)
            ref_price = self._current_price(symbol)

            client_order_id = f"ct-sell-{uuid.uuid4().hex[:20]}"
            alpaca_order = self._submit_market_order(
                session=session,
                symbol=symbol,
                side=OrderSide.SELL,
                qty=pos.quantity,
                client_order_id=client_order_id,
            )
            if alpaca_order is None:
                # Hard error — mark the session ERROR so the UI can surface it.
                session_service.mark_error(db, session, "Alpaca SELL submission failed")
                return

            fill_price = _fill_price(alpaca_order, fallback=ref_price or 0)
            qty = _fill_qty(alpaca_order, fallback=pos.quantity)
            fee = _fill_fee(alpaca_order)

            trade = Trade(
                tenant_id=job.tenant_id,
                session_id=job.session_id,
                position_id=pos.id,
                symbol=symbol,
                side="SELL",
                order_type="MARKET",
                price=fill_price,
                quantity=qty,
                fee=fee,
                status="filled",
                alpaca_order_id=str(alpaca_order.id) if alpaca_order else None,
                alpaca_client_order_id=client_order_id,
                filled_at=datetime.now(timezone.utc),
            )
            db.add(trade)

            reason = job.payload.get("reason") or "AI_SELL"
            gross = fill_price * qty
            position_service.close_position(
                db, pos, exit_price=fill_price, reason=reason, exit_fee=fee,
            )
            session.current_balance += (gross - fee)
            self._write_equity_snapshot(db, session)
            db.commit()

            session_service.finalise_if_done(db, job.session_id)
        finally:
            db.close()

    def _sync_order_status(self, job: "OrderJob") -> None:
        """Reconcile an in-flight order with Alpaca. No-op for now — fills
        are captured synchronously in the place/exit handlers via the returned
        Alpaca Order object."""
        return

    # ── Alpaca helpers ────────────────────────────────────────────────────

    @staticmethod
    def _submit_market_order(
        *,
        session: TradingSession,
        symbol: str,
        side: OrderSide,
        client_order_id: str,
        notional: float | None = None,
        qty: float | None = None,
    ):
        """Submit a market order to Alpaca and return the Alpaca Order object.
        Returns None on failure (already logged). Either `notional` or `qty`
        must be provided."""
        try:
            client = data_feeds.get_trading_client(paper=session.mode != "LIVE")
        except RuntimeError as exc:
            logger.error("Alpaca client unavailable: %s", exc)
            return None

        req_args: dict = {
            "symbol": symbol,
            "side": side,
            "time_in_force": TimeInForce.GTC,
            "client_order_id": client_order_id,
        }
        if notional is not None:
            req_args["notional"] = round(notional, 2)
        if qty is not None:
            req_args["qty"] = qty

        try:
            return client.submit_order(MarketOrderRequest(**req_args))
        except APIError:
            logger.exception("Alpaca submit_order failed (%s %s)", side, symbol)
            return None
        except Exception:
            logger.exception("Alpaca submit_order unexpected failure")
            return None

    @staticmethod
    def _safe_cancel_alpaca_order(session_id: int, alpaca_order_id: str) -> None:
        try:
            client = data_feeds.get_trading_client(paper=True)
            client.cancel_order_by_id(alpaca_order_id)
        except Exception:
            logger.warning("Alpaca cancel_order failed (session=%s id=%s)",
                           session_id, alpaca_order_id)

    # ── Shared helpers ────────────────────────────────────────────────────

    @staticmethod
    def _load_session_and_config(
        db: "Session", job: "OrderJob",
    ) -> tuple[TradingSession | None, BotConfig | None]:
        session = db.query(TradingSession).filter(TradingSession.id == job.session_id).first()
        cfg = db.query(BotConfig).filter(BotConfig.tenant_id == job.tenant_id).first()
        return session, cfg

    @staticmethod
    def _current_price(symbol: str) -> float:
        """Prefer the WS-fed MarketStateStore; fall back to a REST quote
        when the WS hasn't populated the symbol yet (first seconds after a
        session starts)."""
        price = state.market_store.last_price(symbol)
        if price is not None and price > 0:
            return float(price)
        try:
            ticker = data_feeds.fetch_ticker(symbol)
            return float(ticker.get("last") or 0)
        except Exception:
            logger.exception("fetch_ticker failed for %s", symbol)
            return 0.0

    @staticmethod
    def _write_equity_snapshot(db: "Session", session: TradingSession) -> None:
        unrealised = sum(
            (p.unrealised_pnl or 0.0)
            for p in db.query(Position).filter(
                Position.session_id == session.id, Position.status == "open"
            ).all()
        )
        realised = session.current_balance + unrealised - session.starting_balance
        db.add(EquitySnapshot(
            session_id=session.id,
            balance=session.current_balance,
            unrealised_pnl=unrealised,
            realised_pnl=realised,
            total_equity=session.current_balance + unrealised,
        ))


# ── Alpaca order parsing ──────────────────────────────────────────────────


def _fill_price(order, *, fallback: float) -> float:
    """Alpaca market orders usually return `filled_avg_price` synchronously
    in paper mode, but live fills can be async (status=accepted). We fall
    back to the last known market price when the fill isn't ready yet."""
    if order is None:
        return fallback
    v = getattr(order, "filled_avg_price", None)
    try:
        if v is not None:
            price = float(v)
            if price > 0:
                return price
    except (TypeError, ValueError):
        pass
    return fallback


def _fill_qty(order, *, fallback: float) -> float:
    if order is None:
        return fallback
    for attr in ("filled_qty", "qty"):
        v = getattr(order, attr, None)
        try:
            if v is not None:
                q = float(v)
                if q > 0:
                    return q
        except (TypeError, ValueError):
            continue
    return fallback


def _fill_fee(order) -> float:
    """Alpaca discloses crypto commission on filled orders. For paper orders
    that haven't reported a fee back yet, assume zero rather than synthesising
    one — the UI will show slightly off pnl vs. live, but accounting stays
    consistent with what Alpaca reports."""
    if order is None:
        return 0.0
    v = getattr(order, "commission", None)
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0
