"""Watch mode — background SMC scanner that pings the UI when a high-score
setup appears on any watchlisted market.

Design rules
------------
* OFF by default (settings.watch_enabled=False). The scanner never touches MT5
  unless the user explicitly turns it on — the same "opt in, don't surprise"
  rule as SMC reasoning.
* Scans the live watchlist (trading.mt5_bridge.watchlist), so it watches
  whatever markets are on the ticker tape. Add/remove markets in the UI and
  the scanner follows on the next pass.
* Independent of the chat's smc_enabled toggle: that toggle only gates whether
  Lydia *reasons* with structure levels in her prompt. Watch mode is the
  scanning alarm clock, and it runs its own analysis directly.
* One daemon thread, serial scans, so it can never race itself or hammer MT5.
  Alerts go out through the injected notify callback (the web layer passes
  brain.events.broadcast) and are de-duplicated per symbol so a persistent
  setup doesn't re-ping every interval.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

from trading.settings import get, enabled as _enabled

_notify: Callable[[dict], None] | None = None
_stop = threading.Event()
_thread: threading.Thread | None = None

# Dedup: symbol -> (last alert ts, dedup key). The key is direction@entry so a
# new zone or a flip re-alerts immediately, while the same setup stays quiet
# until its re-ping cooldown elapses.
_last: dict[str, tuple[float, str]] = {}

_state: dict = {
    "running": False,
    "last_scan": 0.0,
    "last_error": "",
    "scanned": [],
    "alerts": [],       # capped ring of the most recent alerts
}


def status() -> dict:
    return dict(_state)


def start(notify: Callable[[dict], None]) -> None:
    """Launch the scanner thread (idempotent). `notify` receives each alert dict."""
    global _thread, _notify
    _notify = notify
    if _thread and _thread.is_alive():
        return
    _thread = threading.Thread(target=_loop, daemon=True, name="taia-watch")
    _thread.start()


def _push(alert: dict) -> None:
    _state["alerts"].insert(0, alert)
    if len(_state["alerts"]) > 60:
        _state["alerts"] = _state["alerts"][:60]


def scan_once() -> list[dict]:
    """Run one scan pass immediately and return the alerts that fired.

    Used by both the background loop and the manual `/api/trading/watch/scan`
    endpoint so there's exactly one copy of the scan logic.
    """
    from trading.mt5_bridge import candles as _candles, watchlist as _watchlist
    from trading.smc import analyze as _smc_analyze

    tf = str(get("smc_timeframe", "M15"))
    htf = str(get("smc_htf", "H1"))
    min_score = int(get("watch_min_score", 5))
    min_rr = float(get("smc_min_rr", 2.0))
    interval = max(10, int(get("watch_interval", 60)))

    wl = _watchlist() or []
    _state["scanned"] = list(wl)
    _state["last_scan"] = time.time()

    fired: list[dict] = []
    for sym in wl:
        if _stop.is_set():
            break
        base = _candles(sym, tf, 160).get("candles") or []
        if len(base) < 20:
            continue
        htf_candles = _candles(sym, htf, 160).get("candles") or []
        try:
            a = _smc_analyze(base, symbol=sym, tf=tf,
                             htf_candles=htf_candles, htf_tf=htf,
                             min_rr=min_rr, min_score=min_score)
        except Exception:
            continue

        best = a.get("best")
        if not best:
            continue
        p = best.get("plan") or {}
        key = f"{best.get('direction')}@{round(p.get('entry', 0.0), 2)}"
        now = time.time()
        prev = _last.get(sym)
        re_ping = prev is not None and (now - prev[0]) > interval * 5
        if prev is not None and prev[1] == key and not re_ping:
            continue
        _last[sym] = (now, key)

        alert = {
            "type": "trading_alert",
            "symbol": sym,
            "direction": best.get("direction"),
            "score": best.get("score"),
            "quality": best.get("quality"),
            "entry": p.get("entry"),
            "stop": p.get("stop"),
            "target": p.get("target"),
            "rr": p.get("rr"),
            "tf": tf,
        }
        _push(alert)
        fired.append(alert)
        if _notify:
            try:
                _notify(alert)
            except Exception:
                pass
        # Out-of-band cue: the websocket only reaches an open browser tab, so
        # a setup found while you're in another app would otherwise be missed.
        # notify handles the trade-channel route + quiet-hours break-through.
        try:
            from brain.notify import notify_trade
            notify_trade(
                f"{sym} {str(best.get('direction') or '').upper()} setup",
                (f"Score {best.get('score')} ({best.get('quality')}) \u00b7 "
                 f"entry {p.get('entry')} \u00b7 SL {p.get('stop')} \u00b7 "
                 f"TP {p.get('target')} \u00b7 {p.get('rr')}R {tf}"),
            )
        except Exception:
            pass
    return fired


def _loop() -> None:
    while not _stop.is_set():
        try:
            if not _enabled("watch_enabled"):
                _state["running"] = False
                time.sleep(2.0)
                continue
            _state["running"] = True
            scan_once()
            _state["last_error"] = ""
        except Exception as e:  # pragma: no cover
            _state["last_error"] = str(e)
        time.sleep(max(10, int(get("watch_interval", 60))))
    _state["running"] = False
