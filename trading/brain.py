"""Trading brain helpers — live market context + news formatting.

These build the *state* LYDIA T.A.I injects into her prompt each turn, and
the formatted output of her trading tools. Pure functions over cached data:
they read the MT5 snapshot cache and the news cache but never touch MT5 or
the network directly, so they can't block the voice loop.
"""
from __future__ import annotations

import time

from trading.mt5_bridge import snapshot as mt5_snapshot
from trading.news import cached_calendar, fetch_calendar

# High/medium impact events only get injected into the prompt (low-impact is
# noise). Keep the prompt lean: account + positions + ticks + top events.
_NEWS_IMPACT = {"High", "Medium"}
_MAX_CONTEXT_NEWS = 6
_MAX_NEWS_HOURS = 24 * 3600


def _fmt_price(v) -> str:
    if v is None:
        return "?"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if abs(f) >= 1000:
        return f"{f:,.2f}"
    if abs(f) >= 10:
        return f"{f:.4f}".rstrip("0").rstrip(".")
    return f"{f:.5f}".rstrip("0").rstrip(".")


def _fmt_hours_left(ts: float) -> str:
    secs = ts - time.time()
    if secs < 0:
        return "now"
    m = int(secs // 60)
    if m < 1:
        return "now"
    if m < 60:
        return f"in {m}m"
    h = m // 60
    if h < 24:
        return f"in {h}h {m % 60}m"
    return f"in {h // 24}d {h % 24}h"


def market_context() -> str:
    """A compact live-state block injected into the system prompt each turn.

    Never raises — returns "" if MT5 is down or the cache is empty."""
    try:
        snap = mt5_snapshot()
    except Exception:
        return ""

    if not snap.get("connected"):
        return "## Live Market State\nMT5 offline: " + str(snap.get("error", "unknown"))

    lines: list[str] = []
    acct = snap.get("account") or {}
    lines.append("## Live Market State (MT5, auto-updated every 2s)")
    if acct:
        lines.append(
            f"Account: balance={_fmt_price(acct.get('balance'))} {acct.get('currency', '')}, "
            f"equity={_fmt_price(acct.get('equity'))}, profit={_fmt_price(acct.get('profit'))}, "
            f"free margin={_fmt_price(acct.get('margin_free'))}, "
            f"margin level={acct.get('margin_level')}%, leverage={acct.get('leverage')}, "
            f"trade_allowed={acct.get('trade_allowed')}"
        )

    pos = snap.get("positions") or []
    if pos:
        lines.append(f"Open positions ({len(pos)}):")
        for p in pos:
            side = "BUY" if p.get("type") == 0 else "SELL"
            lines.append(
                f"- #{p.get('ticket')} {side} {p.get('volume')} {p.get('symbol')} "
                f"open={_fmt_price(p.get('price_open'))} now={_fmt_price(p.get('price_current'))} "
                f"SL={_fmt_price(p.get('sl'))} TP={_fmt_price(p.get('tp'))} "
                f"profit={_fmt_price(p.get('profit'))}"
            )
    else:
        lines.append("Open positions: none")

    ticks = snap.get("ticks") or {}
    if ticks:
        lines.append("Watchlist ticks:")
        for base, t in ticks.items():
            if t.get("closed"):
                lines.append(f"- {base}: market closed")
            else:
                lines.append(
                    f"- {base}: bid={_fmt_price(t.get('bid'))} ask={_fmt_price(t.get('ask'))}"
                )

    # Economic calendar — cached only, never fetch here.
    try:
        cal = cached_calendar()
        events = [e for e in (cal.get("events") or [])
                  if e.get("impact") in _NEWS_IMPACT
                  and 0 <= (e.get("ts", 0) - time.time()) <= _MAX_NEWS_HOURS]
        if events:
            lines.append(f"Upcoming calendar (next 24h, top {_MAX_CONTEXT_NEWS}):")
            for e in events[:_MAX_CONTEXT_NEWS]:
                lines.append(
                    f"- [{e.get('impact')}] {e.get('country')} {e.get('title')} "
                    f"{_fmt_hours_left(e.get('ts', 0))}"
                    + (f" (forecast {e.get('forecast')}, prev {e.get('previous')})"
                       if e.get('forecast') else "")
                )
    except Exception:
        pass

    return "\n".join(lines)


def format_news(events: list[dict], max_events: int = 12) -> str:
    """Format calendar events for the LLM (high impact first)."""
    if not events:
        return "No upcoming economic events cached. Markets may be closed this weekend."
    out: list[str] = []
    for e in events[:max_events]:
        out.append(
            f"- [{e.get('impact', '?')}] {e.get('country', '')} {e.get('title', '')} "
            f"{_fmt_hours_left(e.get('ts', 0))}"
            + (f" (forecast {e.get('forecast')}, prev {e.get('previous')})"
               if e.get('forecast') else "")
        )
    return "Economic calendar (upcoming):\n" + "\n".join(out)


def news_tool(max_events: int = 12) -> str:
    """Tool-facing news read — cached, with a one-time fetch if cold."""
    return format_news((fetch_calendar().get("events") or []), max_events)
