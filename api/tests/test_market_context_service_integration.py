"""End-to-end integration test for MarketContextService.build()."""
from __future__ import annotations

from app.models.bot_decision import BotDecision
from app.models.market_context_snapshot import MarketContextSnapshot
from app.models.technical_snapshot import TechnicalSnapshot
from app.services.shared.market_context_service import MarketContextService


def test_build_persists_snapshot_with_all_blocks(
    db, tenant, bot_config, running_session, seeded_candles,
):
    ctx = MarketContextService.build(db, session=running_session, config=bot_config)

    # Returned object has the blocks populated.
    assert ctx.symbol == "BTC/USD"
    assert ctx.interval == "30m"
    assert len(ctx.candles) > 0
    assert ctx.technicals is not None
    assert ctx.technicals.samples > 0
    assert ctx.current_balance == 10_000.0

    # Persisted rows exist.
    persisted = db.query(MarketContextSnapshot).first()
    assert persisted is not None
    assert persisted.session_id == running_session.id
    assert persisted.symbol == "BTC/USD"

    # Technical snapshot also written.
    tech = db.query(TechnicalSnapshot).first()
    assert tech is not None


def test_attach_decision_id_links_back(db, tenant, bot_config, running_session, seeded_candles):
    ctx = MarketContextService.build(db, session=running_session, config=bot_config)

    # Pretend we just wrote a BotDecision row.
    bd = BotDecision(
        tenant_id=tenant.id, session_id=running_session.id, symbol="BTC/USD",
        action="HOLD", confidence=0.4, reason="test",
    )
    db.add(bd); db.commit()

    MarketContextService.attach_decision_id(db, ctx, bd.id)

    snap = db.query(MarketContextSnapshot).first()
    assert snap.decision_id == bd.id
