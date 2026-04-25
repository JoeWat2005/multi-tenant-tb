// Shared response types. Keep in sync with backend until we generate from OpenAPI.

export type BotStatus = {
  tenant_id: number;
  running: boolean;
  mode: "PAPER" | "LIVE";
  symbol: string;
  session_status: SessionStatus;
  session_id: number | null;
  paper_balance_usd: number;
};

export type BotConfig = {
  running: boolean;
  symbol: string;
  interval: string;
  mode: "PAPER" | "LIVE";
  position_size_pct: number;
  min_confidence: number;
  stop_loss_pct: number;
  take_profit_pct: number;
  max_session_loss_pct: number;
  sentiment_interval_minutes: number;
  decision_lookback_candles: number;
  use_sentiment: boolean;
  use_ai: boolean;
  paper_balance_usd: number;
  model_name: string;
};

export type SessionStatus =
  | "IDLE"
  | "STARTING"
  | "RUNNING"
  | "STOPPING"
  | "EXITING"
  | "ENDED"
  | "ERROR";

export type TradingSession = {
  id: number;
  status: SessionStatus;
  symbol: string;
  interval: string;
  mode: "PAPER" | "LIVE";
  starting_balance: number;
  current_balance: number;
  ending_balance: number | null;
  session_pnl: number;
  session_pnl_pct: number;
  open_positions: number;
  started_at: string | null;
  stop_requested_at: string | null;
  ended_at: string | null;
  error_message: string | null;
};

export type EquityPoint = {
  balance: number;
  unrealised_pnl: number;
  realised_pnl: number;
  total_equity: number;
  created_at: string | null;
};

export type BotDecision = {
  id: number;
  session_id: number;
  symbol: string;
  action: "BUY" | "SELL" | "HOLD";
  confidence: number;
  reason: string;
  sentiment_score: number | null;
  weighted_sentiment_score: number | null;
  price_snapshot: number | null;
  approved: boolean;
  rejection_reason: string | null;
  created_at: string | null;
};

export type OpenPosition = {
  id: number;
  symbol: string;
  entry_price: number;
  quantity: number;
  position_value: number;
  size_pct: number;
  stop_loss_price: number | null;
  take_profit_price: number | null;
  unrealised_pnl: number;
  opened_at: string | null;
};

export type Trade = {
  id: number;
  session_id: number;
  position_id: number | null;
  symbol: string;
  side: "BUY" | "SELL";
  order_type: "MARKET" | "LIMIT" | "STOP_LOSS" | "TAKE_PROFIT";
  price: number;
  quantity: number;
  fee: number;
  status: "filled" | "rejected";
  created_at: string | null;
  filled_at: string | null;
};
