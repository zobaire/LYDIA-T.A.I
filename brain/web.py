"""Lydia Web UI bridge — serves UI and bridges backend events + memories."""
from __future__ import annotations
import asyncio
import json
import os
import queue
import secrets
import threading
from pathlib import Path

import requests
import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from brain.config import load_config, get_providers, set_active_provider, load_dotenv
from brain.events import register, broadcast
from brain.memory import Memory
from brain.battery import start as start_battery, get_latest
from brain.schedule import (
    start as start_schedule,
    list_events as schedule_list_events,
    add_event as schedule_add_event,
    update_event as schedule_update_event,
    delete_event as schedule_delete_event,
)
from brain.main import (
    process_request,
    abort_all,
    reset_abort,
    is_stop_command,
    toggle_mute,
    is_muted,
    speak_streamed_if_unmuted,
    interrupt_and_speak,
    handle_wake,
    Aborted,
)

from trading.mt5_bridge import (
    start as start_trading,
    snapshot as trading_snapshot,
    candles as trading_candles,
    equity_history as trading_equity_history,
    watchlist as trading_watchlist,
    add_symbol as trading_add_symbol,
    remove_symbol as trading_remove_symbol,
    search_symbols as trading_search_symbols,
    place_order as trading_place_order,
    close_position as trading_close_position,
    modify_position as trading_modify_position,
    trade_markers as trading_trade_markers,
)
from trading.news import fetch_calendar as fetch_news_calendar
from trading.settings import all as taia_settings_all, update as taia_settings_update
from trading.smc import analyze as smc_analyze
from trading.watch import start as start_watch, scan_once as watch_scan_once, status as watch_status


# UI language toggle -> human-readable name echoed back to the client.
_LANG_NAMES = {
    "en": "English",
    "ar": "العربية",
    "zh": "中文",
}

_UI_DIR = Path(__file__).parent.parent / "ui"
_MEMORY_DIR = Path(__file__).parent.parent / "memory_data"
_TOKEN_PATH = _MEMORY_DIR / "web_token.txt"

app = FastAPI(title="Lydia")
_clients: list[WebSocket] = []
_loop: asyncio.AbstractEventLoop | None = None
_listening_enabled = True


def _get_web_token() -> str:
    """Return the persistent web token, generating one on first boot."""
    tok = os.environ.get("LYDIA_WEB_TOKEN", "")
    if tok:
        return tok
    if _TOKEN_PATH.exists():
        tok = _TOKEN_PATH.read_text(encoding="utf-8").strip()
        if tok:
            return tok
    tok = secrets.token_urlsafe(24)
    _TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    _TOKEN_PATH.write_text(tok, encoding="utf-8")
    return tok


def _is_loopback(client_ip: str) -> bool:
    return client_ip in ("127.0.0.1", "::1", "localhost")
# Chat requests are processed ONE AT A TIME by a serial dispatcher thread.
# The WebSocket receive loop NEVER waits on a request — it just enqueues and
# returns to receive_text(), so Stop / mute / model-switch / effort messages
# keep flowing while Lydia is mid-thought. That was the old bug: user_text
# awaited the worker's completion inline, which froze the receive loop, so a
# Stop sent during "Thinking..." sat unread in the socket until the request
# finished — the stop button looked completely dead.
#
# Abort semantics stay sticky: abort_all() fires the flag and NEVER clears it
# itself. Only the dispatcher clears it, between requests, so an aborted
# request fully unwinds (chat_with_tools raises Aborted within ~100ms) and can
# never keep thinking or speak afterward. A brand-new user_text aborts the
# in-flight request first, then runs — newest message wins.
_request_queue: queue.Queue = queue.Queue()
_dispatcher_started = False
_active_workers = 0
_lock = threading.Lock()


def _worker_started() -> None:
    global _active_workers
    with _lock:
        _active_workers += 1


def _worker_finished() -> None:
    global _active_workers
    with _lock:
        _active_workers = max(0, _active_workers - 1)


def _ensure_request_dispatcher() -> None:
    """Start the serial request worker the first time a message is sent."""
    global _dispatcher_started
    with _lock:
        if _dispatcher_started:
            return
        _dispatcher_started = True
    threading.Thread(target=_request_dispatcher_loop, daemon=True).start()


def _request_dispatcher_loop() -> None:
    """Serial worker: exactly one chat request in flight at a time.

    Clears the abort flag only between requests, so a Stop fired during a
    request makes it raise Aborted at the next poll (~100ms, or before/after
    each tool call) — no overlap, no zombie speech. A request queued while one
    is running waits for the previous one to abort first (newest message wins).
    """
    while True:
        text = _request_queue.get()
        _worker_started()
        response = None
        try:
            reset_abort()
            try:
                response = process_request(text)
            except Aborted:
                response = None
            if response:
                # Speak in its own thread — interruptible, and the receive
                # loop stays free the whole time.
                threading.Thread(
                    target=_speak_response_bg, args=(response,), daemon=True
                ).start()
            else:
                # Aborted before a reply existed — guarantee the orb goes back
                # to idle even if abort_all()'s "Stopped." raced ahead.
                broadcast({"type": "status", "message": "Ready."})
        finally:
            _worker_finished()
            reset_abort()


@app.on_event("startup")
async def _capture_loop() -> None:
    global _loop
    _loop = asyncio.get_running_loop()


# --- Memory helpers ---

def _load_routines() -> list[dict]:
    routines_dir = _MEMORY_DIR / "routines"
    routines: list[dict] = []
    if routines_dir.exists():
        for rf in sorted(routines_dir.glob("*.json")):
            try:
                d = json.loads(rf.read_text(encoding="utf-8"))
                if d.get("name"):
                    routines.append(d)
            except Exception:
                pass
    return routines


def _load_notes() -> str:
    path = _MEMORY_DIR / "notes.md"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return ""


def _load_remembered() -> list[dict]:
    path = _MEMORY_DIR / "remembered.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def _get_effort_tier() -> str:
    path = _MEMORY_DIR / "effort.json"
    if path.exists():
        try:
            return json.loads(path.read_text()).get("tier", "balanced")
        except Exception:
            pass
    return "balanced"


# --- Broadcast ---

def _translate_event(evt: dict) -> dict | None:
    t = evt.get("type", "")
    if t == "response":
        return {"type": "assistant", "text": evt.get("text", "")}
    elif t == "status":
        msg = evt.get("message", "")
        mapping = {
            "Thinking...": "thinking",
            "Speaking...": "talking",
            "Listening...": "listening",
            "Wake.": "listening",
            "Ready.": "connected",
            "Stopped.": "connected",
        }
        return {"type": "status", "text": mapping.get(msg, "connected")}
    elif t == "user":
        return {"type": "user", "text": evt.get("text", "")}
    elif t == "tool":
        return {"type": "tool", "name": evt.get("name", ""), "args": evt.get("args", {})}
    elif t == "tool_result":
        return {"type": "tool_result", "name": evt.get("name", ""), "text": evt.get("result", "")}
    elif t == "mute":
        return {"type": "mute", "muted": evt.get("muted", False)}
    elif t == "provider_changed":
        return {
            "type": "provider",
            "name": evt.get("provider", ""),
            "label": evt.get("label", ""),
            "model": evt.get("model", ""),
        }
    elif t == "schedule":
        return {"type": "schedule", "events": evt.get("events", [])}
    elif t == "reminder":
        return {"type": "reminder", "text": evt.get("text", ""), "event": evt.get("event", "")}
    elif t == "memory":
        return {"type": "memory", "text": evt.get("text", "")}
    return evt


def _speak_response_bg(response: str) -> None:
    """Speak a chat reply with status broadcasts so the orb cycles
    talking -> connected (mirrors the voice path in _handle_wake_inner).

    The chat WebSocket path never broadcast any status around TTS, so after
    every typed message the orb stayed on "thinking" until the next
    interaction. This broadcasts Speaking... right before TTS starts and
    Ready. once the stream finishes (or immediately if muted).
    """
    if is_muted():
        broadcast({"type": "status", "message": "Ready."})
        return
    broadcast({"type": "status", "message": "Speaking..."})
    try:
        interrupt_and_speak(response)
    finally:
        broadcast({"type": "status", "message": "Ready."})


def broadcast_to_ws(event: dict) -> None:
    evt = _translate_event(event)
    if evt is None or _loop is None or _loop.is_closed():
        return
    try:
        asyncio.run_coroutine_threadsafe(_async_broadcast(evt), _loop)
    except RuntimeError:
        pass


async def _async_broadcast(event: dict) -> None:
    data = json.dumps(event)
    for ws in list(_clients):
        try:
            await ws.send_text(data)
        except Exception:
            if ws in _clients:
                _clients.remove(ws)


# --- Static UI ---

@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse((_UI_DIR / "indexV2.html").read_text(encoding="utf-8"))


_vendor_dir = _UI_DIR / "vendor"
if _vendor_dir.exists():
    app.mount("/vendor", StaticFiles(directory=str(_vendor_dir)), name="vendor")


# --- Battery / Balance API ---

@app.get("/api/battery")
async def api_battery():
    """Return provider balances/rate-limits for the UI meters."""
    latest = get_latest()
    if latest:
        return latest
    env = load_dotenv()
    result = {}
    # No real telemetry yet — report "unknown" (pct: None) instead of lying
    # with fake 50% meters. The UI renders null as "—".
    if env.get("DEEPSEEK_API_KEY", ""):
        result["deepseek"] = {"pct": None, "currency": "USD", "amount": 0, "available": True}
    return result


# --- Trading (Phase 2A — read-only) ---

@app.get("/api/trading/snapshot")
async def api_trading_snapshot():
    """Cached MT5 snapshot: account, positions, live ticks. Read-only."""
    return trading_snapshot()


@app.get("/api/trading/news")
async def api_trading_news():
    """Upcoming economic calendar events (Forex Factory free feed, cached)."""
    return fetch_news_calendar()


@app.get("/api/trading/candles")
async def api_trading_candles(symbol: str = "EURUSD", tf: str = "M5", count: int = 120):
    """OHLC candles for the selected market (read-only)."""
    return trading_candles(symbol, tf, count)


@app.get("/api/trading/equity")
async def api_trading_equity():
    """Rolling equity samples for the UI sparkline."""
    return {"history": trading_equity_history()}


@app.get("/api/trading/trades")
async def api_trading_trades(symbol: str = "", days: int = 7):
    """Entry/exit markers for the chart (open positions + closed history)."""
    return trading_trade_markers(symbol, days)


@app.get("/api/trading/symbols")
async def api_trading_symbols(q: str = ""):
    """Search every broker symbol so the user can add markets to the watchlist."""
    return trading_search_symbols(q)


@app.post("/api/trading/watch")
async def api_trading_watch(request: Request):
    """Add a market to the watchlist (clean base or broker name)."""
    body = await request.json()
    return trading_add_symbol(body.get("symbol", ""))


@app.post("/api/trading/unwatch")
async def api_trading_unwatch(request: Request):
    """Remove a market from the watchlist."""
    body = await request.json()
    return trading_remove_symbol(body.get("symbol", ""))


# --- Trading (Phase 2B — order execution) -------------------------------

def _num_or_none(v):
    """Coerce a JSON field to a float, or None when blank/garbage."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@app.post("/api/trading/order")
async def api_trading_order(request: Request):
    """Place a market or pending order. Real money — the UI confirms first."""
    body = await request.json()
    symbol = str(body.get("symbol", "")).strip()
    side = str(body.get("side", "buy")).strip().lower()
    order_type = str(body.get("order_type", "market")).strip().lower()
    volume = _num_or_none(body.get("volume"))
    sl = _num_or_none(body.get("sl"))
    tp = _num_or_none(body.get("tp"))
    price = _num_or_none(body.get("price"))
    comment = str(body.get("comment", "LYDIA T.A.I")).strip() or "LYDIA T.A.I"

    if not symbol:
        return {"ok": False, "error": "no symbol"}
    if side not in ("buy", "sell"):
        return {"ok": False, "error": "side must be 'buy' or 'sell'"}
    if order_type not in ("market", "limit", "stop"):
        return {"ok": False, "error": "order_type must be market/limit/stop"}
    if volume is None or volume <= 0:
        return {"ok": False, "error": "volume must be positive"}
    if volume > 100:
        return {"ok": False, "error": "volume too large (max 100 lots)"}
    if order_type in ("limit", "stop") and price is None:
        return {"ok": False, "error": "pending orders need a price"}

    return trading_place_order(
        symbol, side, volume, sl=sl, tp=tp,
        order_type=order_type, price=price, comment=comment,
    )


@app.post("/api/trading/close")
async def api_trading_close(request: Request):
    """Close an open position (full or partial) by ticket number."""
    body = await request.json()
    ticket = body.get("ticket")
    volume = _num_or_none(body.get("volume"))
    try:
        ticket = int(ticket)
    except (TypeError, ValueError):
        return {"ok": False, "error": "ticket must be an integer"}
    return trading_close_position(ticket, volume=volume)


@app.post("/api/trading/close_all")
async def api_trading_close_all():
    """Kill switch — close every open position. Loops over the cached snapshot
    so this endpoint never touches mt5 directly (each close takes the lock)."""
    snap = trading_snapshot()
    tickets = [p.get("ticket") for p in snap.get("positions", []) if p.get("ticket") is not None]
    if not tickets:
        return {"ok": True, "closed": [], "note": "no open positions"}
    results = [trading_close_position(t) for t in tickets]
    return {"ok": True, "closed": results}


@app.post("/api/trading/modify")
async def api_trading_modify(request: Request):
    """Modify the SL/TP of an open position by ticket number."""
    body = await request.json()
    ticket = body.get("ticket")
    try:
        ticket = int(ticket)
    except (TypeError, ValueError):
        return {"ok": False, "error": "ticket must be an integer"}
    sl = _num_or_none(body.get("sl"))
    tp = _num_or_none(body.get("tp"))
    if sl is None and tp is None:
        return {"ok": False, "error": "provide sl and/or tp"}
    return trading_modify_position(ticket, sl=sl, tp=tp)


# --- Trading (Phase 5 — strategy engine / SMC) --------------------------

@app.get("/api/trading/settings")
async def api_trading_settings():
    """Current backend settings. Source of truth for the UI's SMC toggle."""
    return taia_settings_all()


@app.post("/api/trading/settings")
async def api_trading_settings_update(request: Request):
    """Update backend settings. Only known keys are accepted — anything the
    UI throws in that the backend doesn't recognize is dropped by update()."""
    body = await request.json()
    return taia_settings_update(body if isinstance(body, dict) else {})


@app.get("/api/trading/smc")
async def api_trading_smc(symbol: str = "EURUSD", tf: str = "M15", htf: str = "H1"):
    """Full SMC structure read for one symbol (read-only, no AI). Returns
    order blocks, FVGs, sweeps, BOS/CHoCH events, dealing range and any
    qualifying setups — everything the chart and prompt need."""
    from trading.settings import get as _taia_get

    base = trading_candles(symbol, tf, 160)
    if not base.get("ok"):
        return {"ok": False, "error": base.get("error"), "symbol": symbol, "tf": tf}

    htf_candles = trading_candles(symbol, htf, 160).get("candles") or []
    try:
        return smc_analyze(
            base["candles"], symbol=symbol, tf=tf,
            htf_candles=htf_candles, htf_tf=htf,
            min_rr=float(_taia_get("smc_min_rr", 2.0)),
            min_score=int(_taia_get("smc_min_score", 3)),
        )
    except Exception as e:
        return {"ok": False, "error": f"smc analysis failed: {e}", "symbol": symbol}


@app.get("/api/trading/watch/status")
async def api_trading_watch_status():
    """Watch-mode scanner state: running flag, last scan time, recent alerts."""
    return watch_status()


@app.post("/api/trading/watch/scan")
async def api_trading_watch_scan():
    """Force one immediate scan pass. Returns the alerts that fired."""
    return {"ok": True, "alerts": watch_scan_once()}


_IMPORT_DIR = Path(__file__).parent.parent / "imports"


@app.get("/api/token")
async def api_token(request: Request):
    """Hand out the WS token — loopback clients only.

    When the UI is bound to 0.0.0.0 the token is what keeps LAN randos from
    driving the assistant (open apps, kill processes, shutdown…), so we never
    leak it to non-local requests. The browser page itself fetches this, then
    appends ?token= to the WebSocket URL.
    """
    client = request.client.host if request.client else ""
    if not _is_loopback(client):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    return JSONResponse({"token": _get_web_token()})


@app.post("/api/import")
async def api_import(files: list[UploadFile] = File(...)):
    """Accept file uploads from the UI, save them into /imports, and log a
    memory note with the FULL path + a preview so Lydia can actually find and
    read what was dropped in (not just a bare filename)."""

    _IMPORT_DIR.mkdir(parents=True, exist_ok=True)
    saved: list[dict] = []
    errors: list[str] = []
    for f in files:
        try:
            # sanitize filename
            name = Path(f.filename or "file").name
            dest = _IMPORT_DIR / name
            data = await f.read()
            dest.write_bytes(data)
            saved.append({
                "name": name,
                "path": str(dest.resolve()),
                "size": len(data),
                "preview": _file_preview(dest, data),
            })
        except Exception as e:
            errors.append(f"{f.filename}: {e}")

    if saved:
        try:
            # notes.md is loaded into Lydia's system prompt, so record the
            # absolute address of every imported file there.
            note = _MEMORY_DIR / "notes.md"
            existing = note.read_text(encoding="utf-8") if note.exists() else ""
            lines = [existing.rstrip()] if existing.strip() else []
            for s in saved:
                line = f"- Imported file: {s['path']} (original name: {s['name']}, {s['size']} bytes)"
                if line not in existing:
                    lines.append(line)
            note.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
        except Exception:
            pass

        try:
            # Structured record Lydia can read directly with read_file too.
            record_path = _MEMORY_DIR / "imported_files.json"
            records = []
            if record_path.exists():
                try:
                    records = json.loads(record_path.read_text(encoding="utf-8"))
                except Exception:
                    records = []
            records.extend(saved)
            record_path.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

        # Surface in the UI memory graph so it's obvious what just landed.
        paths = ", ".join(s["path"] for s in saved)
        broadcast({"type": "memory", "text": f"Imported file(s): {paths}"})

    return JSONResponse({"saved": [s["path"] for s in saved], "errors": errors})


# --- Coding Mode: read/save/tree for the editor ---

_CODE_ROOTS = [
    Path(__file__).parent.parent.resolve(),                        # Lydia home (ui/, imports/, brain/…)
    Path(r"C:\Users\book\Desktop\fate's-pair").resolve(),          # main game project
]

# Directories never shown in the file tree (gitignore-style). Server-side
# truth — the UI also filters, but this is the enforcement point.
_TREE_EXCLUDE_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv",
    "venv", ".idea", ".vscode", ".pytest_cache", ".mypy_cache",
    "memory_data", ".claude", ".env",
}


def _resolve_code_path(raw: str) -> Path | None:
    """Resolve a path for the code editor — must live under an allowlisted root."""
    if not raw:
        return None
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = _IMPORT_DIR / p
    try:
        rp = p.resolve()
    except Exception:
        return None
    for root in _CODE_ROOTS:
        try:
            rp.relative_to(root)
            return rp
        except ValueError:
            continue
    return None


@app.get("/api/code/tree")
async def api_code_tree(path: str = ""):
    """List ONE directory level under an allowlisted root.

    The UI calls this lazily: no path = the allowlisted roots themselves,
    then one request per folder the user expands. Exclusions are enforced
    here (server side), not just in the UI.
    """
    target = None
    if path:
        p = _resolve_code_path(path)
        if p is None or not p.is_dir():
            return JSONResponse({"error": "path not allowed or not a directory"}, status_code=403)
        target = p

    dirs: list[dict] = []
    files: list[dict] = []
    if target is None:
        # Top level: list the allowlisted roots as folders.
        for r in _CODE_ROOTS:
            if r.exists() and r.is_dir():
                dirs.append({"name": r.name, "path": str(r.resolve())})
    else:
        try:
            children = sorted(target.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower()))
        except OSError as e:
            return JSONResponse({"error": str(e)}, status_code=500)
        for child in children:
            if child.name in _TREE_EXCLUDE_DIRS or child.name.startswith("."):
                continue
            try:
                if child.is_dir():
                    dirs.append({"name": child.name, "path": str(child.resolve())})
                elif child.is_file():
                    files.append({
                        "name": child.name,
                        "path": str(child.resolve()),
                        "size": child.stat().st_size,
                    })
            except OSError:
                continue
    return JSONResponse({
        "ok": True,
        "path": str(target.resolve()) if target else "",
        "dirs": dirs,
        "files": files,
    })


@app.get("/codedock.mjs")
async def codedock_js():
    """The lazy-loaded Coding Mode module (CodeMirror + tab state). Served
    from disk so edits go live without a backend restart, like indexV2.html."""
    return Response(
        (_UI_DIR / "codedock.mjs").read_text(encoding="utf-8"),
        media_type="text/javascript",
    )


@app.post("/api/code/read")
async def api_code_read(request: Request):
    body = await request.json()
    path = _resolve_code_path(body.get("path", ""))
    if path is None:
        return JSONResponse({"error": "path not allowed"}, status_code=403)
    try:
        content = path.read_text(encoding="utf-8")
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    return JSONResponse({"ok": True, "path": str(path), "content": content})


@app.post("/api/code/save")
async def api_code_save(request: Request):
    body = await request.json()
    path = _resolve_code_path(body.get("path", ""))
    if path is None:
        return JSONResponse({"error": "path not allowed"}, status_code=403)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body.get("content", ""), encoding="utf-8")
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    return JSONResponse({"ok": True, "path": str(path)})


def _file_preview(dest: Path, data: bytes, max_chars: int = 1200) -> str:
    """Best-effort text preview for an imported file (empty for binaries)."""
    text_exts = {
        ".txt", ".md", ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml",
        ".yml", ".toml", ".ini", ".cfg", ".csv", ".log", ".html", ".htm",
        ".css", ".scss", ".xml", ".gd", ".sh", ".ps1", ".bat", ".env",
    }
    if dest.suffix.lower() not in text_exts:
        return ""
    try:
        text = data.decode("utf-8", errors="ignore")
    except Exception:
        return ""
    if not text.strip():
        return ""
    return text[:max_chars]


# --- WebSocket ---

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    # If the server is exposed beyond localhost, require the token. Bound to
    # 127.0.0.1 (the default), the OS already keeps LAN clients out, so we
    # skip the check to keep the localhost flow frictionless.
    host = load_config().get("ui", {}).get("host", "127.0.0.1")
    if host in ("0.0.0.0", "::", ""):
        raw = ws.query_params.get("token", "")
        token = raw[0] if isinstance(raw, list) and raw else raw
        if token != _get_web_token():
            await ws.close(code=4401, reason="Unauthorized")
            return
    await ws.accept()
    _clients.append(ws)

    memory = Memory()
    await ws.send_text(json.dumps({"type": "state",
        "routines": _load_routines(),
        "notes": _load_notes(),
        "remembered": _load_remembered(),
    }))
    await ws.send_text(json.dumps({"type": "effort", "tier": _get_effort_tier()}))
    _boot_lang = os.environ.get("LYDIA_LANG", "en")
    if _boot_lang not in _LANG_NAMES:
        _boot_lang = "en"
    await ws.send_text(json.dumps({"type": "lang", "lang": _boot_lang, "name": _LANG_NAMES[_boot_lang]}))
    await ws.send_text(json.dumps({"type": "mute", "muted": is_muted()}))
    await ws.send_text(json.dumps({"type": "schedule", "events": schedule_list_events()}))
    # Tell the UI which provider/model is currently active so the buttons light up on load.
    ctx = load_config().get("brain", {})
    await ws.send_text(json.dumps({
        "type": "provider",
        "name": ctx.get("active_provider", ""),
        "model": ctx.get("active_provider", ""),
        "label": get_providers().get(ctx.get("active_provider", ""), {}).get("label", ""),
        "current": True,
    }))
    initial_batt = get_latest()
    if initial_batt:
        await ws.send_text(json.dumps({"type": "battery", **initial_batt}))

    try:
        while True:
            data = await ws.receive_text()
            try:
                msg = json.loads(data)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type", "")

            if msg_type == "user_text":
                text = msg.get("text", "").strip()
                if not text:
                    continue
                if is_stop_command(text):
                    abort_all()
                    await ws.send_text(json.dumps({"type": "assistant", "text": "Stopped."}))
                    await ws.send_text(json.dumps({"type": "status", "text": "connected"}))
                    continue

                # Brand-new user request: newest message wins, so hard-stop
                # any in-flight thinking or speaking, then queue this one.
                # abort_all() sets the sticky abort flag — the dispatcher is
                # the only thing that clears it (between requests), so the
                # old request truly unwinds before the new one starts.
                # We DON'T await anything here: the receive loop must stay
                # free so a Stop fired mid-thought is read immediately.
                abort_all()
                _ensure_request_dispatcher()
                _request_queue.put(text)

            elif msg_type == "wake":
                await ws.send_text(json.dumps({"type": "status", "text": "listening"}))
                def _run_mic_pipeline():
                    try:
                        handle_wake()
                    except Exception as e:
                        print(f"[Mic] Error: {e}")
                threading.Thread(target=_run_mic_pipeline, daemon=True).start()

            elif msg_type == "stop":
                # Stop talking AND thinking: fire the sticky abort flag and cut
                # any audio. The dispatcher's in-flight request polls the flag
                # every ~100ms (and before/after each tool call) and raises
                # Aborted, so it stops fast and can never speak afterward.
                abort_all()
                # If no request is in flight, nobody will clear the abort flag
                # for us — re-arm now so the next request isn't stuck.
                with _lock:
                    if _active_workers == 0:
                        reset_abort()
                await ws.send_text(json.dumps({"type": "status", "text": "connected"}))

            elif msg_type == "set_lang":
                lang = msg.get("lang", "en")
                if lang not in _LANG_NAMES:
                    lang = "en"
                name = _LANG_NAMES[lang]
                os.environ["LYDIA_LANG"] = lang
                await ws.send_text(json.dumps({"type": "lang", "lang": lang, "name": name}))

            elif msg_type == "set_effort":
                tier = msg.get("tier", "balanced")
                # DeepSeek is the only brain now. The tier just picks which
                # DeepSeek model + thinking depth runs (see get_effort_settings).
                provider = "deepseek"
                set_active_provider(provider)
                try:
                    (_MEMORY_DIR / "effort.json").write_text(
                        json.dumps({"tier": tier}), encoding="utf-8")
                except Exception:
                    pass

                # Resolve the concrete DeepSeek model for this tier so the UI
                # pill shows deepseek-flash vs deepseek-v4-pro correctly.
                eff_cfg = (load_config().get("brain", {}) or {}).get("effort", {})
                chosen = eff_cfg.get(tier, eff_cfg.get("balanced", {}))
                provs = get_providers()
                label = provs.get(provider, {}).get("label", "DeepSeek")
                model = chosen.get("model") or provs.get(provider, {}).get("model", "deepseek-flash")
                broadcast({
                    "type": "provider_changed",
                    "provider": provider,
                    "label": label,
                    "model": model,
                })
                await ws.send_text(json.dumps({
                    "type": "provider",
                    "name": provider,
                    "label": label,
                    "model": model,
                    "current": True,
                }))
                await ws.send_text(json.dumps({"type": "effort", "tier": tier}))

                # Which vision model does this tier use? (vision.tiers.<tier>
                # overrides the default in config.yaml — see vision.py.)
                vis_cfg = load_config().get("vision", {}) or {}
                vis_ovr = (vis_cfg.get("tiers") or {}).get(tier) or {}
                vis_provider = vis_ovr.get("provider", vis_cfg.get("provider", ""))
                vis_model = vis_ovr.get("model") or vis_cfg.get("model", "")
                vis_tag = vis_provider or "no vision"
                if vis_model:
                    vis_tag = f"{vis_model}"
                await ws.send_text(json.dumps({
                    "type": "model_msg",
                    "text": (
                        f"Effort <strong>{tier}</strong> &middot; brain: "
                        f"<strong>{label}</strong><br>vision: <strong>{vis_tag}</strong>"
                    ),
                }))

            elif msg_type == "switch_model":
                # Only one brain (DeepSeek) — model selection is done via the
                # effort tiers now. This keeps the UI's model buttons sane.
                key = msg.get("model", "") or msg.get("provider", "") or "deepseek"
                if key != "deepseek":
                    await ws.send_text(json.dumps({"type": "model_msg",
                        "text": "Only DeepSeek is available now."}))
                    continue
                set_active_provider("deepseek")
                label = "DeepSeek"
                model = get_providers().get("deepseek", {}).get("model", "deepseek-flash")
                broadcast({
                    "type": "provider_changed",
                    "provider": "deepseek",
                    "label": label,
                    "model": model,
                })
                await ws.send_text(json.dumps({
                    "type": "provider",
                    "name": "deepseek",
                    "label": label,
                    "model": model,
                    "current": True,
                }))
                await ws.send_text(json.dumps({
                    "type": "model_msg",
                    "text": f"Model switched to {label}.",
                }))

            elif msg_type == "set_mute":
                try:
                    muted = msg.get("muted", False)
                    if muted != is_muted():
                        toggle_mute()
                    await ws.send_text(json.dumps({"type": "mute", "muted": is_muted()}))
                except Exception as e:
                    print(f"[WS] set_mute error: {e}")
                    await ws.send_text(json.dumps({"type": "mute", "muted": is_muted()}))

            elif msg_type == "set_listen":
                global _listening_enabled
                _listening_enabled = msg.get("listening", True)
                if not _listening_enabled:
                    from brain.wake import pause_wake_mic
                    pause_wake_mic()
                else:
                    from brain.wake import resume_wake_mic
                    resume_wake_mic()
                await ws.send_text(json.dumps({"type": "listen", "listening": _listening_enabled}))

            elif msg_type == "schedule_list":
                await ws.send_text(json.dumps({"type": "schedule", "events": schedule_list_events()}))

            elif msg_type == "schedule_add":
                title = msg.get("title", "").strip()
                date = msg.get("date", "").strip()
                if title and date:
                    evt = schedule_add_event(title, date)
                    await ws.send_text(json.dumps({"type": "schedule_msg", "text": f"✅ Added: {evt['title']} at {evt['date']}"}))
                else:
                    await ws.send_text(json.dumps({"type": "schedule_msg", "text": "Need a title and a date (YYYY-MM-DD HH:MM)."}))

            elif msg_type == "schedule_update":
                updated = schedule_update_event(
                    msg.get("id", ""),
                    title=msg.get("title"),
                    date=msg.get("date"),
                )
                await ws.send_text(json.dumps({"type": "schedule_msg",
                    "text": f"✅ Updated: {updated['title']} at {updated['date']}" if updated else "Event not found."}))

            elif msg_type == "schedule_delete":
                ok = schedule_delete_event(msg.get("id", ""))
                await ws.send_text(json.dumps({"type": "schedule_msg",
                    "text": "🗑️ Event cancelled." if ok else "Event not found."}))

    except WebSocketDisconnect:
        if ws in _clients:
            _clients.remove(ws)


def start_web_background(port: int = 8765, host: str = "127.0.0.1") -> None:
    if host in ("0.0.0.0", "::"):
        print(f"[WARN] UI bound to {host}:{port} — LAN clients must present the "
              f"WS token (served to loopback only via /api/token).")
    register(broadcast_to_ws)
    start_battery()
    start_schedule()
    start_trading()
    # Watch mode: background SMC scanner that broadcasts trading_alert events
    # to the UI when a high-score setup appears on a watchlisted market.
    start_watch(broadcast)

    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        config = uvicorn.Config(app, host=host, port=port, log_level="warning")
        server = uvicorn.Server(config)
        loop.run_until_complete(server.serve())

    t = threading.Thread(target=_run, daemon=True)
    t.start()
