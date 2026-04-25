"""Import every model here so Base.metadata sees them all for create_all."""
from app.models.user import User
from app.models.tenant import Tenant
from app.models.bot_config import BotConfig
from app.models.trading_session import TradingSession
from app.models.position import Position
from app.models.trade import Trade
from app.models.order import Order
from app.models.bot_decision import BotDecision
from app.models.candle import Candle
from app.models.sentiment_snapshot import SentimentSnapshot
from app.models.equity_snapshot import EquitySnapshot
from app.models.market_context_snapshot import MarketContextSnapshot
from app.models.technical_snapshot import TechnicalSnapshot

__all__ = [
    "User",
    "Tenant",
    "BotConfig",
    "TradingSession",
    "Position",
    "Trade",
    "Order",
    "BotDecision",
    "Candle",
    "SentimentSnapshot",
    "EquitySnapshot",
    "MarketContextSnapshot",
    "TechnicalSnapshot",
]
