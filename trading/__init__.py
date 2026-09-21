"""LYDIA T.A.I — trading package (read-only market data + account state).

Phase 2A: no order placement. mt5_bridge is strictly read-only — it pulls
account info, open positions and live ticks so the UI can render them.
Execution code lands in Phase 2B, gated behind trade_allowed + explicit
confirmation.
"""
from trading.mt5_bridge import start as start_trading, snapshot
from trading.news import fetch_calendar

__all__ = ["start_trading", "snapshot", "fetch_calendar"]
