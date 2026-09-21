"""Read-only MT5 bridge — account state, positions, live ticks.

Design notes
------------
* The MetaTrader5 terminal must be running + logged in (it is, as Exness).
* The bridge runs in a single dedicated daemon thread that OWNS the mt5
  connection, calls initialize() once, then polls every 2s and caches the
  result. HTTP endpoints only ever read the cache — never touch mt5 directly,
  so a slow tick never blocks the web loop.
* Symbol resolution: broker symbols carry an "m" suffix (EURUSD -> EURUSDm).
  resolve() prefers "<want>m", then "<want>", then the shortest prefix match.
* Symbols are symbol_select()ed once so ticks flow even if the user never
  opened them in Market Watch (US30 wasn't in Market Watch and had no tick).
"""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from pathlib import Path
from typing import Optional

try:
    import MetaTrader5 as mt5
except ImportError:  # pragma: no cover
    mt5 = None

# Watched symbols for the ticker tape (clean names — resolved to broker names).
_DEFAULT_WATCH = ["EURUSD", "GBPUSD", "XAUUSD", "BTCUSD", "US30", "USDJPY"]
_WATCHLIST_PATH = Path(__file__).parent / "watchlist.json"
_watchlist: list[str] = []
_wl_lock = threading.Lock()


_TERMINAL_PATHS = [
    r"C:\Program Files\MetaTrader 5\terminal64.exe",
    r"C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe",
]

_POLL_SECONDS = 2.0

_lock = threading.Lock()
_connected = False
_symbol_map: dict[str, str] = {}
_subscribed: set = set()
_cached: dict = {"connected": False, "error": "not started", "account": None,
                 "positions": [], "ticks": {}, "ts": 0.0}
_last_error = ""
_started = False

# Serializes all mt5.* calls (poller thread + candle fetch from web requests),
# so a copy_rates_from_pos never interleaves with a symbol_info_tick poll.
_mt5_lock = threading.Lock()

# Rolling equity samples for the UI sparkline (capped, ~8 min at 2s).
_equity_hist: deque = deque(maxlen=240)


def _tf_constant(tf: str):
    """Map a timeframe label (M1/M5/.../D1) to an MT5 timeframe constant."""
    if mt5 is None:
        return None
    return getattr(mt5, "TIMEFRAME_" + tf.upper(), mt5.TIMEFRAME_M5)


def _find_terminal_path() -> Optional[str]:
    import os
    for p in _TERMINAL_PATHS:
        if os.path.exists(p):
            return p
    return None


def resolve(want: str) -> Optional[str]:
    """Resolve a clean name like EURUSD to the broker's exact symbol."""
    if mt5 is None:
        return None
    if want in _symbol_map:
        return _symbol_map[want]
    if mt5.symbol_info(want + "m") is not None:
        _symbol_map[want] = want + "m"
    elif mt5.symbol_info(want) is not None:
        _symbol_map[want] = want
    else:
        syms = mt5.symbols_get(want + "*")
        if syms:
            best = min(syms, key=lambda s: len(s.name))
            _symbol_map[want] = best.name
    return _symbol_map.get(want)


def initialize() -> bool:
    global _connected, _last_error
    if mt5 is None:
        _last_error = "MetaTrader5 package not installed"
        return False
    if _connected:
        return True
    path = _find_terminal_path()
    try:
        ok = mt5.initialize(path=path) if path else mt5.initialize()
    except Exception as e:  # pragma: no cover
        ok = False
        _last_error = f"init exception: {e}"
    if not ok:
        _last_error = f"MT5 init failed: {mt5.last_error()}"
        _connected = False
        return False
    _connected = True
    for w in watchlist():
        resolve(w)
    return True


def _refresh() -> None:
    global _cached, _last_error
    with _mt5_lock:
        if not initialize():
            with _lock:
                _cached = {"connected": False, "error": _last_error,
                           "account": None, "positions": [], "ticks": {}, "ts": time.time()}
            return
        try:
            acct = mt5.account_info()
            positions = mt5.positions_get() or []
            # Auto-join: any symbol with an open position becomes watched, so
            # "just trade it in MT5" makes it appear in T.A.I on the next poll.
            with _wl_lock:
                changed = False
                for p in positions:
                    base = _broker_to_base(getattr(p, "symbol", "") or "")
                    if base and base not in _watchlist:
                        _watchlist.append(base)
                        changed = True
                if changed:
                    _save_watchlist()
                wl = list(_watchlist)
            ticks: dict = {}
            now = time.time()
            for base in wl:
                sym = resolve(base)
                if not sym:
                    continue
                # subscribe once so ticks flow even for symbols never opened in MT5
                if sym not in _subscribed:
                    try:
                        mt5.symbol_select(sym, True)
                        _subscribed.add(sym)
                    except Exception:
                        pass
                t = mt5.symbol_info_tick(sym)
                if t is not None:
                    info = mt5.symbol_info(sym)
                    ticks[base] = {
                        "symbol": sym, "bid": t.bid, "ask": t.ask, "last": t.last,
                        "time": t.time_msc, "age_s": int(now - t.time), "closed": False,
                        "contract_size": float(getattr(info, "trade_contract_size", 0) or 0),
                        "point": float(getattr(info, "point", 0) or 0),
                        "tick_value": float(getattr(info, "trade_tick_value", 0) or 0),
                    }
                else:
                    ticks[base] = {"symbol": sym, "bid": None, "ask": None, "last": None,
                                   "time": None, "age_s": None, "closed": True,
                                   "contract_size": 0, "point": 0, "tick_value": 0}
            with _lock:
                _cached = {
                    "connected": True,
                    "error": "",
                    "ts": now,
                    "account": {
                        "login": acct.login, "server": acct.server, "name": acct.name,
                        "company": acct.company, "currency": acct.currency,
                        "balance": acct.balance, "equity": acct.equity, "profit": acct.profit,
                        "margin": acct.margin, "margin_free": acct.margin_free,
                        "margin_level": acct.margin_level, "leverage": acct.leverage,
                        "trade_allowed": bool(acct.trade_allowed),
                    } if acct is not None else None,
                    "positions": [{
                        "ticket": p.ticket, "symbol": p.symbol,
                        "type": p.type,          # 0 = buy, 1 = sell
                        "volume": p.volume, "price_open": p.price_open,
                        "price_current": p.price_current, "sl": p.sl, "tp": p.tp,
                        "profit": p.profit, "swap": p.swap, "comment": p.comment,
                        "time": p.time,
                    } for p in positions],
                    "ticks": ticks,
                }
                if acct is not None:
                    _equity_hist.append({"t": now, "equity": float(acct.equity)})
        except Exception as e:  # pragma: no cover
            with _lock:
                _cached = {"connected": False, "error": str(e),
                           "account": None, "positions": [], "ticks": {}, "ts": time.time()}


def _poll_loop() -> None:
    while True:
        _refresh()
        time.sleep(_POLL_SECONDS)


# --- Watchlist management (search / add / remove) ------------------------

def _broker_to_base(name: str) -> str:
    """Broker symbol (EURUSDm) -> clean base (EURUSD). Non-'m' names pass through."""
    n = (name or "").strip().upper()
    if n.endswith("M") and len(n) > 2:
        return n[:-1]
    return n


def _load_watchlist() -> None:
    global _watchlist
    try:
        if _WATCHLIST_PATH.exists():
            data = json.loads(_WATCHLIST_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                _watchlist = [str(x).strip().upper() for x in data if str(x).strip()]
    except Exception:
        _watchlist = []
    if not _watchlist:
        _watchlist = list(_DEFAULT_WATCH)
        _save_watchlist()


def _save_watchlist() -> None:
    try:
        _WATCHLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        _WATCHLIST_PATH.write_text(json.dumps(_watchlist, indent=2), encoding="utf-8")
    except Exception:
        pass


def watchlist() -> list[str]:
    """Ordered clean base names the ticker / heatmap / chips render."""
    with _wl_lock:
        return list(_watchlist)


def add_symbol(raw: str) -> dict:
    """Add a market to the watchlist. Accepts a clean base (EURUSD) or a broker
    name (EURUSDm); resolves against the live terminal so typos are rejected."""
    n = (raw or "").strip().upper()
    if not n:
        return {"ok": False, "error": "empty symbol", "watchlist": watchlist()}
    base = _broker_to_base(n)
    with _mt5_lock:
        if not initialize():
            return {"ok": False, "error": _last_error, "watchlist": watchlist()}
        sym = resolve(base) or resolve(n)
    if not sym:
        return {"ok": False, "error": "symbol not found: " + n, "watchlist": watchlist()}
    with _wl_lock:
        if base not in _watchlist:
            _watchlist.append(base)
            _save_watchlist()
    return {"ok": True, "symbol": sym, "base": base, "watchlist": watchlist()}


def remove_symbol(raw: str) -> dict:
    """Remove a market from the watchlist (by clean base or broker name)."""
    n = _broker_to_base((raw or "").strip().upper())
    with _wl_lock:
        ok = n in _watchlist
        if ok:
            _watchlist.remove(n)
            _save_watchlist()
    return {"ok": ok, "base": n, "watchlist": watchlist()}


def search_symbols(query: str = "") -> dict:
    """Search every symbol the broker offers (name / base / description)."""
    if mt5 is None:
        return {"ok": False, "error": "MetaTrader5 package not installed", "symbols": []}
    q = (query or "").strip().upper()
    with _mt5_lock:
        if not initialize():
            return {"ok": False, "error": _last_error, "symbols": []}
        all_syms = mt5.symbols_get() or []
    out: list[dict] = []
    seen: set = set()
    for s in all_syms:
        name = getattr(s, "name", "") or ""
        base = _broker_to_base(name)
        hay = (name + " " + (getattr(s, "description", "") or "")).upper()
        if q and q not in name and q not in base and q not in hay:
            continue
        if base in seen:
            continue
        seen.add(base)
        out.append({
            "name": name, "base": base,
            "desc": (getattr(s, "description", "") or "").strip(),
        })
        if len(out) >= 80:
            break
    if q:
        out.sort(key=lambda x: (x["base"] != q, not x["base"].startswith(q), x["base"]))
    return {"ok": True, "query": q, "symbols": out}


_load_watchlist()


def start() -> None:
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_poll_loop, daemon=True, name="mt5-poller").start()


def snapshot() -> dict:
    with _lock:
        d = dict(_cached)
    d["watchlist"] = watchlist()
    return d


def candles(want: str, tf: str = "M5", count: int = 120) -> dict:
    """OHLC candles for a symbol straight from MT5 (read-only)."""
    if mt5 is None:
        return {"ok": False, "error": "MetaTrader5 package not installed", "candles": []}
    with _mt5_lock:
        if not initialize():
            return {"ok": False, "error": _last_error, "candles": []}
        sym = resolve(want)
        if not sym:
            return {"ok": False, "error": f"symbol not found: {want}", "candles": []}
        frame = _tf_constant(tf)
        if frame is None:
            return {"ok": False, "error": "MT5 unavailable", "candles": []}
        try:
            rates = mt5.copy_rates_from_pos(sym, frame, 0, int(count))
        except Exception as e:  # pragma: no cover
            return {"ok": False, "error": str(e), "candles": []}
        if rates is None or len(rates) == 0:
            return {"ok": False, "error": f"no candle data for {sym} on {tf}", "candles": []}
        out = []
        for r in rates:
            try:
                out.append({
                    "t": int(r["time"]),
                    "o": float(r["open"]),
                    "h": float(r["high"]),
                    "l": float(r["low"]),
                    "c": float(r["close"]),
                    "v": int(r["tick_volume"] or 0),
                })
            except Exception:
                continue
        return {"ok": True, "symbol": sym, "tf": tf.upper(), "candles": out}


def equity_history() -> list:
    """Rolling equity samples for the UI sparkline."""
    with _lock:
        return list(_equity_hist)


# --- Order execution (Phase 2B) -----------------------------------------
# These live in the bridge next to the read-only code so every mt5.* call is
# serialized by the same _mt5_lock. Orders are real; the assistant must still
# confirm with the user before calling these (enforced in the system prompt).

_MAGIC = 20260913  # tags orders placed by LYDIA T.A.I


def _pick_filling(info) -> int:
    """Choose a filling mode the symbol actually supports (IOC -> FOK -> RETURN)."""
    if mt5 is None:
        return 1  # ORDER_FILLING_IOC
    try:
        fm = getattr(info, "filling_mode", 0) or 0
    except Exception:
        fm = 0
    if fm & getattr(mt5, "SYMBOL_FILLING_IOC", 2):
        return mt5.ORDER_FILLING_IOC
    if fm & getattr(mt5, "SYMBOL_FILLING_FOK", 1):
        return mt5.ORDER_FILLING_FOK
    return mt5.ORDER_FILLING_RETURN


def _retcode_text(code: int) -> str:
    """Human-readable name for a trade retcode (TRADE_RETCODE_* -> short name)."""
    if mt5 is None:
        return f"retcode {code}"
    for n in dir(mt5):
        if n.startswith("TRADE_RETCODE_") and getattr(mt5, n) == code:
            return n.replace("TRADE_RETCODE_", "")
    return f"retcode {code}"


def _send_order(request: dict) -> dict:
    """Send a prepared request and normalize the broker result into a clean dict."""
    try:
        result = mt5.order_send(request)
    except Exception as e:  # pragma: no cover
        return {"ok": False, "error": f"order_send exception: {e}"}
    if result is None:
        return {"ok": False, "error": f"order_send failed: {mt5.last_error()}"}
    done = result.retcode == mt5.TRADE_RETCODE_DONE
    out = {
        "ok": done,
        "retcode": result.retcode,
        "retcode_text": _retcode_text(result.retcode),
        "ticket": getattr(result, "order", None),
        "deal": getattr(result, "deal", None),
        "price": getattr(result, "price", None),
        "volume": getattr(result, "volume", None),
        "comment": getattr(result, "comment", None) or "",
    }
    if not done:
        out["error"] = _retcode_text(result.retcode)
    return out


def place_order(symbol: str, side: str, volume: float,
                sl: float | None = None, tp: float | None = None,
                order_type: str = "market", price: float | None = None,
                comment: str = "LYDIA T.A.I") -> dict:
    """Place a market or pending order.

    side: 'buy' or 'sell'.
    order_type: 'market' (default), 'limit', or 'stop'.
    For pending orders a 'price' is required; for market orders the current
    ask (buy) / bid (sell) is used. sl/tp are optional.
    """
    if mt5 is None:
        return {"ok": False, "error": "MetaTrader5 package not installed"}
    side = (side or "").lower()
    order_type = (order_type or "").lower()
    with _mt5_lock:
        if not initialize():
            return {"ok": False, "error": _last_error}
        sym = resolve(symbol)
        if not sym:
            return {"ok": False, "error": f"symbol not found: {symbol}"}
        info = mt5.symbol_info(sym)
        if info is None:
            return {"ok": False, "error": f"no symbol info for {sym}"}
        tick = mt5.symbol_info_tick(sym)
        if tick is None:
            return {"ok": False, "error": f"no live tick for {sym}"}

        is_buy = side in ("buy", "long")
        is_sell = side in ("sell", "short")
        if not (is_buy or is_sell):
            return {"ok": False, "error": "side must be 'buy' or 'sell'"}

        if order_type == "market":
            action = mt5.TRADE_ACTION_DEAL
            otype = mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL
            fill_price = tick.ask if is_buy else tick.bid
        elif order_type in ("limit", "stop"):
            if price is None:
                return {"ok": False, "error": "pending orders need a 'price'"}
            action = mt5.TRADE_ACTION_PENDING
            if order_type == "limit":
                otype = mt5.ORDER_TYPE_BUY_LIMIT if is_buy else mt5.ORDER_TYPE_SELL_LIMIT
            else:
                otype = mt5.ORDER_TYPE_BUY_STOP if is_buy else mt5.ORDER_TYPE_SELL_STOP
            fill_price = float(price)
        else:
            return {"ok": False, "error": "order_type must be market/limit/stop"}

        request = {
            "action": action,
            "symbol": sym,
            "volume": float(volume),
            "type": otype,
            "price": float(fill_price),
            "sl": float(sl) if sl is not None else 0.0,
            "tp": float(tp) if tp is not None else 0.0,
            "deviation": 30,
            "magic": _MAGIC,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": _pick_filling(info),
        }
        return _send_order(request)


def close_position(ticket: int, volume: float | None = None,
                   comment: str = "LYDIA T.A.I") -> dict:
    """Close an open position (full or partial) by ticket number."""
    if mt5 is None:
        return {"ok": False, "error": "MetaTrader5 package not installed"}
    with _mt5_lock:
        if not initialize():
            return {"ok": False, "error": _last_error}
        positions = mt5.positions_get(ticket=int(ticket))
        if not positions:
            return {"ok": False, "error": f"no open position with ticket {ticket}"}
        pos = positions[0]
        sym = pos.symbol
        info = mt5.symbol_info(sym)
        tick = mt5.symbol_info_tick(sym)
        if info is None or tick is None:
            return {"ok": False, "error": f"no market data for {sym}"}
        is_buy = pos.type == mt5.POSITION_TYPE_BUY
        close_price = tick.bid if is_buy else tick.ask
        vol = float(volume) if volume is not None else float(pos.volume)
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": sym,
            "volume": vol,
            "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
            "position": int(ticket),
            "price": float(close_price),
            "deviation": 30,
            "magic": _MAGIC,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": _pick_filling(info),
        }
        return _send_order(request)


def modify_position(ticket: int, sl: float | None = None,
                    tp: float | None = None) -> dict:
    """Modify the SL/TP of an open position by ticket number."""
    if mt5 is None:
        return {"ok": False, "error": "MetaTrader5 package not installed"}
    with _mt5_lock:
        if not initialize():
            return {"ok": False, "error": _last_error}
        positions = mt5.positions_get(ticket=int(ticket))
        if not positions:
            return {"ok": False, "error": f"no open position with ticket {ticket}"}
        pos = positions[0]
        sym = pos.symbol
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": sym,
            "position": int(ticket),
            "sl": float(sl) if sl is not None else float(pos.sl),
            "tp": float(tp) if tp is not None else float(pos.tp),
        }
        return _send_order(request)



# --- Trade history / chart markers --------------------------------------

def trade_markers(symbol: str = "", days: int = 7) -> dict:
    """Entry/exit markers for the candlestick chart.

    Sources:
      * open positions  -> live 'entry' markers (price_open + open time)
      * MT5 deal history -> 'entry' (DEAL_ENTRY_IN) and 'exit' (DEAL_ENTRY_OUT)
        markers for closed trades.

    Markers are resolved to clean base names (EURUSDm -> EURUSD) so the frontend
    can filter by the active market. Persistence is free: MT5 keeps the deal
    history on disk, so a page refresh re-fetches the same closed trades — the
    markers survive switching markets and reloading the app.
    """
    if mt5 is None:
        return {"ok": False, "error": "MetaTrader5 package not installed", "markers": []}

    want_base = _broker_to_base((symbol or "").strip().upper()) if (symbol or "").strip() else ""
    markers: list[dict] = []

    def _keep(base: str) -> bool:
        return (not want_base) or base == want_base

    with _mt5_lock:
        if not initialize():
            return {"ok": False, "error": _last_error, "markers": []}
        now = time.time()

        # 1) Open positions -> live entry markers.
        try:
            positions = mt5.positions_get() or []
        except Exception:
            positions = []
        for p in positions:
            base = _broker_to_base(getattr(p, "symbol", "") or "")
            if not base or not _keep(base):
                continue
            pt = getattr(p, "time", None)
            markers.append({
                "symbol": base,
                "side": "buy" if getattr(p, "type", 0) == 0 else "sell",
                "price": float(getattr(p, "price_open", 0) or 0),
                "time": int(pt) if pt else int(now),
                "kind": "entry",
                "ticket": int(getattr(p, "ticket", 0) or 0),
                "open": True,
            })

        # 2) Closed deals from the terminal's on-disk history.
        try:
            deals = mt5.history_deals_get(int(now - days * 86400), int(now)) or []
        except Exception:
            deals = []
        for d in deals:
            base = _broker_to_base(getattr(d, "symbol", "") or "")
            if not base or not _keep(base):
                continue
            entry = getattr(d, "entry", None)   # 0 IN, 1 OUT, 2 INOUT, 3 OUT_BY
            dtype = getattr(d, "type", None)    # 0 BUY, 1 SELL
            if entry in (0, 2):                 # opened here
                kind = "entry"
                side = "buy" if dtype == 0 else "sell"
            elif entry in (1, 3):               # closed here (side is the closing leg)
                kind = "exit"
                side = "buy" if dtype == 1 else "sell"
            else:
                continue
            markers.append({
                "symbol": base,
                "side": side,
                "price": float(getattr(d, "price", 0) or 0),
                "time": int(getattr(d, "time", 0) or 0),
                "kind": kind,
                "ticket": int(getattr(d, "position_id", 0) or getattr(d, "ticket", 0) or 0),
                "open": False,
            })

    markers.sort(key=lambda m: m["time"])
    if len(markers) > 500:
        markers = markers[-500:]
    return {"ok": True, "symbol": want_base, "markers": markers}

