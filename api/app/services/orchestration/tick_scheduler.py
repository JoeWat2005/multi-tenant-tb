"""Scans bot_configs every 60s and fires cycles for tenants whose
next_run_at <= now. `running` + `next_run_at` live in the DB so scheduling
survives restarts.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from app.db.session import SessionLocal
from app.models.bot_config import BotConfig

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.services.orchestration.tenant_registry import TenantRegistry


MAX_CONCURRENT_CYCLES = 5
# All intervals are configured in minutes on BotConfig, but the tick runs
# every 60s — that's the polling resolution. A user-configured 15m interval
# fires a cycle every 15 tick scans.
DEFAULT_INTERVAL_MINUTES = 15


class TickScheduler:
    def __init__(self, tenant_registry: "TenantRegistry") -> None:
        self.tenant_registry = tenant_registry
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_CYCLES)

    async def tick(self) -> None:
        now = datetime.now(timezone.utc)
        due: list[int] = []

        db = SessionLocal()
        try:
            configs = (
                db.query(BotConfig)
                .filter(BotConfig.running == True)  # noqa: E712
                .all()
            )
            for cfg in configs:
                next_run = cfg.next_run_at
                if next_run is not None and next_run.tzinfo is None:
                    next_run = next_run.replace(tzinfo=timezone.utc)
                if next_run is not None and next_run > now:
                    continue

                self.tenant_registry.ensure(cfg.tenant_id, cfg)

                # Advance before firing so a slow cycle can't trigger itself.
                interval_minutes = _interval_to_minutes(cfg.interval, DEFAULT_INTERVAL_MINUTES)
                cfg.next_run_at = now + timedelta(minutes=interval_minutes)
                due.append(cfg.tenant_id)
            db.commit()
        finally:
            db.close()

        if not due:
            return

        logger.info("Firing cycles for %d tenants", len(due))
        await asyncio.gather(*[self._run_one(tid) for tid in due])

    async def _run_one(self, tenant_id: int) -> None:
        async with self._semaphore:
            services = self.tenant_registry.get(tenant_id)
            if services is None:
                logger.warning("Tenant %s not registered at cycle time", tenant_id)
                return

            db = SessionLocal()
            try:
                config = (
                    db.query(BotConfig)
                    .filter(BotConfig.tenant_id == tenant_id)
                    .first()
                )
                if config is None or not config.running:
                    return
                try:
                    await services.cycle_service.run(db, config)
                except Exception:
                    logger.exception("Cycle failed for tenant %s", tenant_id)
                    db.rollback()
            finally:
                db.close()


def _interval_to_minutes(interval: str, default: int) -> int:
    """Turn a short-form "15m" / "1h" / "4h" / "1d" into minutes."""
    if not interval:
        return default
    try:
        unit = interval[-1].lower()
        n = int(interval[:-1])
    except (ValueError, IndexError):
        return default
    if unit == "m":
        return n
    if unit == "h":
        return n * 60
    if unit == "d":
        return n * 60 * 24
    return default
