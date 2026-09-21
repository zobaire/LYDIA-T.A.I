"""Tool registry, schemas, and dispatcher."""
from __future__ import annotations
import json
from typing import Callable

from brain.tools.web_search import search, fetch_page, get_weather
from brain.tools.app_control import open_app, open_url, kill_process
from brain.tools.desktop import (
    type_text, press_key, click_at, scroll_screen, move_mouse,
    read_screen, find_on_screen, get_open_windows, focus_window,
    media_control, screenshot,
)
from brain.tools.vision import analyze_image
from brain.tools.window_manager import split_windows_equally, check_apps_status
from brain.tools.file_ops import read_file, write_file, list_files, apply_patch
from brain.tools.code_exec import run_python
from brain.tools.system import (
    get_clipboard, set_clipboard, set_volume, get_volume,
    get_system_info, set_brightness, lock_screen, power_command,
    show_notification, set_timer,
)
from brain.tools.api_tools import (
    spotify_control, spotify_search, spotify_play_song,
    discord_check,
)
from brain.tools.project import (
    read_project, grep_project, read_symbols,
    run_shell, run_tests, run_linter, edit_file,
)
from brain.schedule import (
    list_events as schedule_list,
    add_event as schedule_add,
    update_event as schedule_update,
    delete_event as schedule_delete,
)
from trading.mt5_bridge import (
    snapshot as mt5_snapshot,
    candles as mt5_candles,
    search_symbols as mt5_search_symbols,
    add_symbol as mt5_watch,
    remove_symbol as mt5_unwatch,
    place_order as mt5_place_order,
    close_position as mt5_close_position,
    modify_position as mt5_modify_position,
)
from trading.brain import news_tool as mt5_news_tool

TOOL_MAP: dict[str, Callable[[dict], str]] = {
    # Web
    "web_search": lambda a: search(a["query"], a.get("max_results", 5)),
    "fetch_page": lambda a: fetch_page(a["url"], a.get("max_chars", 3000)),
    "open_url": lambda a: open_url(a["url"]),
    "get_weather": lambda a: get_weather(a.get("location", "")),
    # Apps & processes
    "open_app": lambda a: open_app(a["name"]),
    "kill_process": lambda a: kill_process(a["name"]),
    # Desktop automation
    "read_screen": lambda _: read_screen(),
    "find_on_screen": lambda a: find_on_screen(a["text"]),
    "click_at": lambda a: click_at(int(a["x"]), int(a["y"]), a.get("button", "left")),
    "scroll_screen": lambda a: scroll_screen(a["direction"], int(a.get("amount", 3))),
    "move_mouse": lambda a: move_mouse(int(a["x"]), int(a["y"])),
    "focus_window": lambda a: focus_window(a["title"]),
    "get_open_windows": lambda _: get_open_windows(),
    "type_text": lambda a: type_text(a["text"]),
    "press_key": lambda a: press_key(a["keys"]),
    "media_control": lambda a: media_control(a["action"]),
    "screenshot": lambda a: screenshot(a.get("filename", "")),
    "analyze_image": lambda a: analyze_image(a["path"], a.get("prompt", "Describe this image in detail.")),
    # Files
    "read_file": lambda a: read_file(a["path"]),
    "write_file": lambda a: write_file(a["path"], a["content"]),
    "list_files": lambda a: list_files(a["path"]),
    "apply_patch": lambda a: apply_patch(a["path"], a["patch"]),
    # Code
    "run_python": lambda a: run_python(a["code"]),
    # Clipboard
    "get_clipboard": lambda _: get_clipboard(),
    "set_clipboard": lambda a: set_clipboard(a["text"]),
    # System
    "set_volume": lambda a: set_volume(int(a["level"])),
    "get_volume": lambda _: get_volume(),
    "get_system_info": lambda _: get_system_info(),
    "set_brightness": lambda a: set_brightness(int(a["level"])),
    "lock_screen": lambda _: lock_screen(),
    "power_command": lambda a: power_command(a["action"]),
    # Notifications & timers
    "show_notification": lambda a: show_notification(a["title"], a["message"]),
    "set_timer": lambda a: set_timer(int(a["seconds"]), a.get("message", "Timer done!")),
    # Window splitting & checks
    "split_windows_equally": lambda a: _join_list(split_windows_equally(a["apps"], a.get("direction", "horizontal"))),
    "check_apps_open": lambda a: check_apps_status(a["apps"]),
    # API integrations
    "spotify_control": lambda a: spotify_control(a["action"]),
    "spotify_search": lambda a: spotify_search(a["query"], int(a.get("limit", 5))),
    "spotify_play_song": lambda a: spotify_play_song(a["query"]),
    "discord_check": lambda _: discord_check(),
    # Schedule / plans
    "schedule_list": lambda _: _fmt_schedule(schedule_list()),
    "schedule_add": lambda a: _fmt_schedule([schedule_add(a["title"], a["date"])]),
    "schedule_update": lambda a: _fmt_schedule([schedule_update(a["id"], a.get("title"), a.get("date"))]) if schedule_update(a["id"], a.get("title"), a.get("date")) else "Event not found.",
    "schedule_delete": lambda a: "Event cancelled." if schedule_delete(a["id"]) else "Event not found.",
    # Coding brain
    "read_project": lambda a: read_project(a.get("root", "."), int(a.get("depth", 4))),
    "grep_project": lambda a: grep_project(a["pattern"], a.get("root", "."), int(a.get("max_results", 20))),
    "read_symbols": lambda a: read_symbols(a["path"]),
    "run_shell": lambda a: run_shell(a["command"], int(a.get("timeout", 30))),
    "run_tests": lambda a: run_tests(a.get("path", ".")),
    "run_linter": lambda a: run_linter(a.get("path", ".")),
    "edit_file": lambda a: edit_file(a["path"], a["patch"]),
    # --- Trading (MT5) ---
    "mt5_snapshot": lambda a: _fmt_snapshot(mt5_snapshot()),
    "mt5_candles": lambda a: _fmt_candles(mt5_candles(
        a["symbol"], a.get("tf", "M5"), int(a.get("count", 120)))),
    "mt5_search_symbols": lambda a: _fmt_json(mt5_search_symbols(a.get("query", ""))),
    "mt5_watch": lambda a: _fmt_json(mt5_watch(a["symbol"])),
    "mt5_unwatch": lambda a: _fmt_json(mt5_unwatch(a["symbol"])),
    "mt5_place_order": lambda a: _fmt_json(mt5_place_order(
        a["symbol"], a["side"], a["volume"],
        a.get("sl"), a.get("tp"), a.get("order_type", "market"),
        a.get("price"), a.get("comment", "LYDIA T.A.I"))),
    "mt5_close_position": lambda a: _fmt_json(mt5_close_position(
        int(a["ticket"]), a.get("volume"), a.get("comment", "LYDIA T.A.I"))),
    "mt5_modify_position": lambda a: _fmt_json(mt5_modify_position(
        int(a["ticket"]), a.get("sl"), a.get("tp"))),
    "market_news": lambda a: mt5_news_tool(int(a.get("max_events", 12))),
}

TOOL_SCHEMAS: list[dict] = [
    # --- Web ---
    {"type": "function", "function": {
        "name": "web_search",
        "description": "Search the web. Returns top result snippets.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer"}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "fetch_page",
        "description": "Fetch a URL and return plain text content.",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string"},
            "max_chars": {"type": "integer"}},
            "required": ["url"]}}},
    {"type": "function", "function": {
        "name": "open_url",
        "description": "Open a URL in the default browser.",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string"}},
            "required": ["url"]}}},
    {"type": "function", "function": {
        "name": "get_weather",
        "description": "Get current weather for a location. Leave empty for auto-detect.",
        "parameters": {"type": "object", "properties": {
            "location": {"type": "string"}},
            "required": []}}},

    # --- Apps & processes ---
    {"type": "function", "function": {
        "name": "open_app",
        "description": "Open app by name: chrome, firefox, edge, brave, discord, telegram, slack, teams, zoom, vscode, terminal, cmd, powershell, git bash, spotify, vlc, steam, epic games, notepad, notepad++, word, excel, powerpoint, paint, snipping tool, explorer, task manager, calculator, settings, control panel.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string"}},
            "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "kill_process",
        "description": "Kill/close a running process by name (e.g. chrome, spotify, discord).",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string"}},
            "required": ["name"]}}},

    # --- Desktop vision & mouse ---
    {"type": "function", "function": {
        "name": "read_screen",
        "description": "OCR the screen. Returns all visible text with coordinates: 'text | x,y'. Use before clicking.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "find_on_screen",
        "description": "Find text on screen via OCR. Returns coordinates to click.",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string"}},
            "required": ["text"]}}},
    {"type": "function", "function": {
        "name": "click_at",
        "description": "Click at screen coordinates (x, y). Button: left (default), right, double.",
        "parameters": {"type": "object", "properties": {
            "x": {"type": "integer"},
            "y": {"type": "integer"},
            "button": {"type": "string", "enum": ["left", "right", "double"]}},
            "required": ["x", "y"]}}},
    {"type": "function", "function": {
        "name": "scroll_screen",
        "description": "Scroll up or down. Amount = number of scroll steps (default 3).",
        "parameters": {"type": "object", "properties": {
            "direction": {"type": "string", "enum": ["up", "down"]},
            "amount": {"type": "integer"}},
            "required": ["direction"]}}},
    {"type": "function", "function": {
        "name": "move_mouse",
        "description": "Move mouse cursor to (x, y) without clicking.",
        "parameters": {"type": "object", "properties": {
            "x": {"type": "integer"},
            "y": {"type": "integer"}},
            "required": ["x", "y"]}}},

    # --- Desktop automation ---
    {"type": "function", "function": {
        "name": "focus_window",
        "description": "Bring a window to foreground by partial title.",
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string"}},
            "required": ["title"]}}},
    {"type": "function", "function": {
        "name": "get_open_windows",
        "description": "List all open window titles.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "type_text",
        "description": "Type text at current cursor position.",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string"}},
            "required": ["text"]}}},
    {"type": "function", "function": {
        "name": "press_key",
        "description": "Press key/hotkey: ctrl+c, alt+tab, win+d, enter, escape, ctrl+shift+esc.",
        "parameters": {"type": "object", "properties": {
            "keys": {"type": "string"}},
            "required": ["keys"]}}},
    {"type": "function", "function": {
        "name": "media_control",
        "description": "Media playback: play, pause, next, previous, mute.",
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string"}},
            "required": ["action"]}}},
    {"type": "function", "function": {
        "name": "screenshot",
        "description": "Take a screenshot, saves to Desktop.",
        "parameters": {"type": "object", "properties": {
            "filename": {"type": "string"}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "analyze_image",
        "description": "Analyze an image file or screenshot with a vision model. Use after screenshot() to truly 'see' the screen — describe layouts, icons, colors, errors, UI state, or read text that OCR struggles with. Returns a text description.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Path to the image file — e.g. the path returned by screenshot(), or a file in Desktop/imports."},
            "prompt": {"type": "string", "description": "What to look for. Be specific: 'What error message is shown?', 'Describe the layout', 'What buttons are visible?'"}},
            "required": ["path"]}}},

    # --- Files ---
    {"type": "function", "function": {
        "name": "read_file",
        "description": "Read a text file (inside allowed paths).",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "write_file",
        "description": "Write text to a file (inside allowed paths).",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"}},
            "required": ["path", "content"]}}},
    {"type": "function", "function": {
        "name": "list_files",
        "description": "List files in directory (inside allowed paths).",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "apply_patch",
        "description": "Apply a SEARCH/REPLACE patch to a file. Format: <<<<<<< SEARCH\\nold\\n=======\\nnew\\n>>>>>>> REPLACE",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "patch": {"type": "string"}},
            "required": ["path", "patch"]}}},

    # --- Code ---
    {"type": "function", "function": {
        "name": "run_python",
        "description": "Execute Python code in a subprocess sandbox, returns stdout/stderr.",
        "parameters": {"type": "object", "properties": {
            "code": {"type": "string"}},
            "required": ["code"]}}},

    # --- Clipboard ---
    {"type": "function", "function": {
        "name": "get_clipboard",
        "description": "Get clipboard text.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "set_clipboard",
        "description": "Set clipboard text.",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string"}},
            "required": ["text"]}}},

    # --- System ---
    {"type": "function", "function": {
        "name": "set_volume",
        "description": "Set system volume 0-100.",
        "parameters": {"type": "object", "properties": {
            "level": {"type": "integer"}},
            "required": ["level"]}}},
    {"type": "function", "function": {
        "name": "get_volume",
        "description": "Get current system volume.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "get_system_info",
        "description": "Get CPU, RAM, and disk usage stats.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "set_brightness",
        "description": "Set screen brightness 0-100 (laptops only).",
        "parameters": {"type": "object", "properties": {
            "level": {"type": "integer"}},
            "required": ["level"]}}},
    {"type": "function", "function": {
        "name": "lock_screen",
        "description": "Lock the workstation.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "power_command",
        "description": "Shutdown, restart, or sleep the PC.",
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["shutdown", "restart", "sleep"]}},
            "required": ["action"]}}},

    # --- Notifications & timers ---
    {"type": "function", "function": {
        "name": "show_notification",
        "description": "Show a Windows toast notification.",
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string"},
            "message": {"type": "string"}},
            "required": ["title", "message"]}}},
    {"type": "function", "function": {
        "name": "set_timer",
        "description": "Set a timer (in seconds) that shows a notification when done.",
        "parameters": {"type": "object", "properties": {
            "seconds": {"type": "integer"},
            "message": {"type": "string"}},
            "required": ["seconds"]}}},

    # --- Window splitting & checks ---
    {"type": "function", "function": {
        "name": "check_apps_open",
        "description": "Report which of the given apps are currently open/running and which are not. Always call this BEFORE opening or arranging apps so you only open what's genuinely missing and never spawn duplicate windows.",
        "parameters": {"type": "object", "properties": {
            "apps": {"type": "array", "items": {"type": "string"},
                     "description": "List of app names to check, e.g. ['brave', 'discord', 'spotify']"}},
            "required": ["apps"]}}},
    {"type": "function", "function": {
        "name": "split_windows_equally",
        "description": "Split the screen into N equal-width (or equal-height) zones and place one app per zone, in the order given. Use this when the user wants several specific apps arranged side by side at the same size - a shape Windows' built-in snap can't do for anything other than 2 or 4 windows.",
        "parameters": {"type": "object", "properties": {
            "apps": {"type": "array", "items": {"type": "string"},
                     "description": "App names in left-to-right (or top-to-bottom) order, e.g. ['discord', 'spotify', 'brave']"},
            "direction": {"type": "string", "enum": ["horizontal", "vertical"],
                          "description": "horizontal = side-by-side columns, vertical = stacked rows"}},
            "required": ["apps"]}}},

    # --- API integrations ---
    {"type": "function", "function": {
        "name": "spotify_control",
        "description": "Control Spotify playback: play, pause, next, previous.",
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string"}},
            "required": ["action"]}}},
    {"type": "function", "function": {
        "name": "spotify_search",
        "description": "Search Spotify for a song or artist. Returns a readable list of the top matches.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer"}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "spotify_play_song",
        "description": "Search Spotify for a song and play the top matching track on the active device.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "discord_check",
        "description": "Check Discord server status.",
        "parameters": {"type": "object", "properties": {}}}},
    # --- Schedule / plans ---
    {"type": "function", "function": {
        "name": "schedule_list",
        "description": "List all planned events in the schedule.",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "schedule_add",
        "description": "Add an event/plan to the schedule. date must be 'YYYY-MM-DD HH:MM'.",
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string"},
            "date": {"type": "string"}},
            "required": ["title", "date"]}}},
    {"type": "function", "function": {
        "name": "schedule_update",
        "description": "Edit an existing event's title and/or date. id is the event id.",
        "parameters": {"type": "object", "properties": {
            "id": {"type": "string"},
            "title": {"type": "string"},
            "date": {"type": "string"}},
            "required": ["id"]}}},
    {"type": "function", "function": {
        "name": "schedule_delete",
        "description": "Cancel/delete an event by id.",
        "parameters": {"type": "object", "properties": {
            "id": {"type": "string"}},
            "required": ["id"]}}},

    # --- Coding brain ---
    {"type": "function", "function": {
        "name": "read_project",
        "description": "Return a tree view of the project up to a given depth.",
        "parameters": {"type": "object", "properties": {
            "root": {"type": "string"},
            "depth": {"type": "integer"}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "grep_project",
        "description": "Search project files for a pattern and return matching lines.",
        "parameters": {"type": "object", "properties": {
            "pattern": {"type": "string"},
            "root": {"type": "string"},
            "max_results": {"type": "integer"}},
            "required": ["pattern"]}}},
    {"type": "function", "function": {
        "name": "read_symbols",
        "description": "Extract function/class symbols from a Python or JS/TS file.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "run_shell",
        "description": "Run a shell command and return stdout/stderr.",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string"},
            "timeout": {"type": "integer"}},
            "required": ["command"]}}},
    {"type": "function", "function": {
        "name": "run_tests",
        "description": "Run the project's test suite (pytest or npm test).",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "run_linter",
        "description": "Run the project's linter (ruff/flake8 or eslint).",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "edit_file",
        "description": "Apply a SEARCH/REPLACE patch to a file. Use read_file first.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "patch": {"type": "string"}},
            "required": ["path", "patch"]}}},

    # --- Trading (MT5) ---
    {"type": "function", "function": {
        "name": "mt5_snapshot",
        "description": "Read live MT5 account state: balance, equity, margin, open positions, and real-time ticks for the watchlist. Read-only.",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "mt5_candles",
        "description": "Get OHLC candlestick history for a symbol and timeframe (M1/M5/M15/M30/H1/H4/D1). Read-only.",
        "parameters": {"type": "object", "properties": {
            "symbol": {"type": "string", "description": "Market to fetch, e.g. XAUUSD, EURUSD, BTCUSD."},
            "tf": {"type": "string", "description": "Timeframe label, e.g. M5 or H1."},
            "count": {"type": "integer"}},
            "required": ["symbol"]}}},
    {"type": "function", "function": {
        "name": "mt5_search_symbols",
        "description": "Search every market the broker offers (by name or description). Read-only.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "mt5_watch",
        "description": "Add a market to the watchlist (e.g. XAUUSD). It then appears in the ticker and starts streaming ticks.",
        "parameters": {"type": "object", "properties": {
            "symbol": {"type": "string"}},
            "required": ["symbol"]}}},
    {"type": "function", "function": {
        "name": "mt5_unwatch",
        "description": "Remove a market from the watchlist.",
        "parameters": {"type": "object", "properties": {
            "symbol": {"type": "string"}},
            "required": ["symbol"]}}},
    {"type": "function", "function": {
        "name": "mt5_place_order",
        "description": "Place a REAL trade on MT5. side: buy or sell. order_type: market (default), limit, or stop. For pending orders a 'price' is required. sl/tp are optional. CONFIRM the exact order with the user before calling this.",
        "parameters": {"type": "object", "properties": {
            "symbol": {"type": "string"},
            "side": {"type": "string", "enum": ["buy", "sell"]},
            "volume": {"type": "number", "description": "Lot size, e.g. 0.01"},
            "sl": {"type": "number"},
            "tp": {"type": "number"},
            "order_type": {"type": "string", "enum": ["market", "limit", "stop"]},
            "price": {"type": "number", "description": "Required for limit/stop orders."},
            "comment": {"type": "string"}},
            "required": ["symbol", "side", "volume"]}}},
    {"type": "function", "function": {
        "name": "mt5_close_position",
        "description": "Close an open MT5 position by ticket number (full or partial). CONFIRM with the user first.",
        "parameters": {"type": "object", "properties": {
            "ticket": {"type": "integer"},
            "volume": {"type": "number", "description": "Optional partial close size."},
            "comment": {"type": "string"}},
            "required": ["ticket"]}}},
    {"type": "function", "function": {
        "name": "mt5_modify_position",
        "description": "Modify the stop loss and/or take profit of an open MT5 position by ticket.",
        "parameters": {"type": "object", "properties": {
            "ticket": {"type": "integer"},
            "sl": {"type": "number"},
            "tp": {"type": "number"}},
            "required": ["ticket"]}}},
    {"type": "function", "function": {
        "name": "market_news",
        "description": "Get the upcoming economic calendar (NFP, CPI, rate decisions, etc.) with time-to-event and impact level. Read-only.",
        "parameters": {"type": "object", "properties": {
            "max_events": {"type": "integer"}},
            "required": []}}},
]


def _fmt_json(d) -> str:
    """Compact JSON for LLM-facing tool results."""
    try:
        return json.dumps(d, ensure_ascii=False, default=str)
    except Exception:
        return str(d)


def _fmt_snapshot(d: dict) -> str:
    """Compact account + positions + ticks summary for the LLM."""
    if not d.get("connected"):
        return "MT5 not connected: " + str(d.get("error", "unknown"))
    acct = d.get("account") or {}
    lines = ["MT5 account:"]
    lines.append(
        f"- {acct.get('name')} (login {acct.get('login')} @ {acct.get('server')}): "
        f"balance={acct.get('balance')} {acct.get('currency')}, "
        f"equity={acct.get('equity')}, profit={acct.get('profit')}, "
        f"free margin={acct.get('margin_free')}, "
        f"margin level={acct.get('margin_level')}%, leverage={acct.get('leverage')}, "
        f"trade_allowed={acct.get('trade_allowed')}"
    )
    pos = d.get("positions") or []
    if pos:
        lines.append(f"Open positions ({len(pos)}):")
        for p in pos:
            side = "BUY" if p.get("type") == 0 else "SELL"
            lines.append(
                f"- #{p.get('ticket')} {side} {p.get('volume')} {p.get('symbol')} "
                f"open={p.get('price_open')} now={p.get('price_current')} "
                f"SL={p.get('sl')} TP={p.get('tp')} profit={p.get('profit')}"
            )
    else:
        lines.append("Open positions: none")
    ticks = d.get("ticks") or {}
    if ticks:
        lines.append(f"Live ticks ({len(ticks)} symbols):")
        for base, t in ticks.items():
            if t.get("closed"):
                lines.append(f"- {base}: market closed")
            else:
                lines.append(f"- {base}: bid={t.get('bid')} ask={t.get('ask')} last={t.get('last')}")
    return "\n".join(lines)


def _fmt_candles(d: dict) -> str:
    """Compact OHLC summary for the LLM (last 40 candles)."""
    if not d.get("ok"):
        return "Candles error: " + str(d.get("error", "unknown"))
    cs = d.get("candles") or []
    lines = [f"{d.get('symbol')} {d.get('tf')} candles ({len(cs)}):"]
    for c in cs[-40:]:
        lines.append(
            f"  t={c.get('t')} O={c.get('o')} H={c.get('h')} L={c.get('l')} C={c.get('c')}"
        )
    return "\n".join(lines)


def _join_list(results: list) -> str:
    """Join a list of result strings into a single reply."""
    return "; ".join(str(r) for r in results)


def _fmt_schedule(events: list[dict]) -> str:
    """Format schedule events for the LLM / voice reply."""
    if not events:
        return "No upcoming events."
    lines = []
    for e in events:
        if e:
            lines.append(f"- {e.get('date', '')}: {e.get('title', '')}")
    return "Schedule:\n" + "\n".join(lines) if lines else "No upcoming events."


def dispatch(tool_name: str, tool_args: dict) -> str:
    """Execute a named tool and return string result."""
    fn = TOOL_MAP.get(tool_name)
    if fn is None:
        return f"Unknown tool: {tool_name}"
    try:
        return str(fn(tool_args))
    except Exception as e:
        return f"Tool '{tool_name}' failed: {e}"
