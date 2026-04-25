"""In-memory registry of per-tenant services.

Each tenant has exactly one TradingCycleService that the tick scheduler
calls when the tenant is due. Session + position state lives in the DB,
so this registry is intentionally thin — just the cycle object and a
cached symbol set for the Alpaca WS subscription union.

`ensure` is idempotent — safe to call on `/start`, on config-change, or
from the tick scheduler when a tenant first shows up after a restart.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from threading import RLock
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.models.bot_config import BotConfig
    from app.services.orchestration.order_queue import OrderQueue
    from app.services.shared.decision_provider import DecisionProvider
    from app.services.tenant.trading_cycle import TradingCycleService


@dataclass
class TenantServices:
    tenant_id: int
    cycle_service: "TradingCycleService"
    tracked_symbols: set[str]


class TenantRegistry:
    def __init__(
        self,
        decision_provider: "DecisionProvider",
        order_queue: "OrderQueue",
    ) -> None:
        self._lock = RLock()
        self._tenants: dict[int, TenantServices] = {}
        self._decision_provider = decision_provider
        self._order_queue = order_queue

    def ensure(self, tenant_id: int, config: "BotConfig") -> TenantServices:
        from app.services.tenant.trading_cycle import TradingCycleService

        symbol = (config.symbol or "BTC/USD").strip().upper()
        symbols = {symbol} if symbol else set()

        with self._lock:
            existing = self._tenants.get(tenant_id)
            if existing:
                existing.tracked_symbols = symbols
                return existing

            cycle = TradingCycleService(
                tenant_id=tenant_id,
                decision_provider=self._decision_provider,
                order_queue=self._order_queue,
            )
            services = TenantServices(
                tenant_id=tenant_id,
                cycle_service=cycle,
                tracked_symbols=symbols,
            )
            self._tenants[tenant_id] = services
            logger.info("Tenant %s services built (symbol=%s)", tenant_id, symbol)
            return services

    def unregister(self, tenant_id: int) -> None:
        with self._lock:
            self._tenants.pop(tenant_id, None)

    def get(self, tenant_id: int) -> TenantServices | None:
        with self._lock:
            return self._tenants.get(tenant_id)

    def all(self) -> list[TenantServices]:
        with self._lock:
            return list(self._tenants.values())

    def union_of_tracked_symbols(self) -> set[str]:
        with self._lock:
            result: set[str] = set()
            for t in self._tenants.values():
                result |= t.tracked_symbols
            return result
