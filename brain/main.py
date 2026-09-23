"""LYDIA T.A.I — trading desk brain. Keyboard + screen only.

The voice pipeline (TTS output, microphone/STT input, wake word) was removed
entirely: she is driven from the UI and the console, and replies in text.
"""
from __future__ import annotations
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from brain.config import load_config, set_active_provider, get_active_provider, get_providers, get_effort_settings
from brain.context import ContextManager
from brain.events import broadcast
from brain.llm import chat_with_tools
from brain.memory import Memory
from brain.notify import notify_done
from brain.tools.router import TOOL_SCHEMAS, dispatch
from trading.brain import market_context
from trading.settings import enabled as taia_setting_enabled, get as taia_setting_get

_MAX_TOOL_LOOPS = int(load_config().get("brain", {}).get("max_tool_loops", 15))
_MEMORY_DIR = Path(__file__).parent.parent / "memory_data"

_abort = threading.Event()

_memory = Memory()
# Replay the last few persisted conversation summaries on boot so context
# isn't wiped every restart.
_context = ContextManager(
    initial_summaries=_memory.get_recent_summaries(5),
    save_summary_cb=lambda s: _memory.save_conversation_summary(s),
)


class Aborted(Exception):
    pass


def abort_all() -> None:
    """Force-stop thinking right now.

    Sets the abort flag so any in-flight generation is interrupted, and tells
    the UI we're idle again. The flag stays SET until the running worker
    actually stops, so a background request can't keep going after a Stop.
    """
    _abort.set()
    broadcast({"type": "status", "message": "Stopped."})


def reset_abort() -> None:
    """Re-arm for a fresh request after a stop/abort.

    Only the worker that owns the cancelled request should clear the
    flag once it has fully unwound. This guarantees a fired Stop never
    leaves a half-finished response that keeps running anyway.
    """
    _abort.clear()


def is_stop_command(text: str) -> bool:
    cleaned = text.strip().lower().rstrip(".,!?")
    return cleaned in {"stop", "cancel", "abort", "nevermind"}


def _check_abort() -> None:
    if _abort.is_set():
        raise Aborted()


def _load_legacy_facts_text() -> str:
    facts = _memory.load_legacy_facts()
    if facts:
        return "## Your Memory\n" + "\n".join(facts)
    return ""


def _load_routines_text() -> str:
    routines = _memory.load_routines()
    if not routines:
        return ""
    lines = ["## Learned Routines"]
    for r in routines:
        lines.append(f"- When user says \"{r.get('trigger', '')}\": {r.get('steps', '')}")
    return "\n".join(lines)


# Trading depth per effort tier — makes the effort buttons change how deep she
# analyzes, not just which DeepSeek model runs.
_TRADE_DEPTH = {
    "snappy": "Quick read only — one-line answer, no deep analysis.",
    "balanced": "Standard read — trend, key levels, one honest risk note.",
    "max": "Deep analysis — multi-timeframe read, levels, risk, and a clear lean.",
    "ultra": "Full desk analysis — structure, key levels, risk math, scenarios, "
             "and a confident call with a suggested size.",
}


# Answer language, driven by the UI toggle (set_lang -> LYDIA_LANG).
_LANG_INSTRUCTION = {
    "en": "Reply in English.",
    "zh": "Always reply in Mandarin Chinese (中文).",
    "ar": (
        "Always reply in Modern Standard Arabic (العربية الفصحى), written in Arabic script. "
        "Do not use Darija and do not mix in French. "
        "Trading and technical terms (stop loss, take profit, lot, pip, spread, RSI, "
        "support, resistance, equity, margin, MT5) stay in English, exactly the way a "
        "trader says them. Digits are fine."
    ),
}

# Who LYDIA T.A.I is — her identity and her job. This is language-independent
# and applies no matter which language she answers in.
_IDENTITY = (
    "You are LYDIA T.A.I — the trading-assistant build of Lydia, a keyboard + screen AI "
    "that lives on the user's Windows PC. Your entire job is trading.\n"
    "\n"
    "WHO YOU ARE\n"
    "- A sharp, focused trading co-pilot. Warm and human in tone (no corporate robot "
    "talk, no 'As an AI' nonsense), but your subject is the markets: live prices, "
    "positions, risk, and order execution.\n"
    "- Keep it real, direct, and concise. No corporate padding, no formalities.\n"
    "- You are not a generic chatbot and not a coding buddy — you are a trading desk.\n"
    "- You have no voice: the user reads your replies in the UI or the console. "
    "Never say you'll 'say' something aloud, and never refer to speaking or listening.\n"
    "\n"
    "YOUR JOB\n"
    "- Read live data from MetaTrader 5: account state, open positions, real-time "
    "ticks, and OHLC candles.\n"
    "- Analyze markets with deep thinking — walk through the setup step by step using "
    "the actual numbers you pulled, and explain what you see and why it matters.\n"
    "- Manage the watchlist of markets the user selects.\n"
    "- Place and execute trades (market and pending orders with SL/TP), close and "
    "modify positions — but only when the user asks and confirms.\n"
    "\n"
    "TRADING TOOLS\n"
    "- mt5_snapshot — account, positions, and live ticks in one call\n"
    "- mt5_candles — OHLC history for a symbol + timeframe (M1..D1)\n"
    "- mt5_search_symbols / mt5_watch / mt5_unwatch — find and manage markets\n"
    "- market_news — upcoming economic calendar (NFP, CPI, rate decisions)\n"
    "- mt5_place_order / mt5_close_position / mt5_modify_position — execute trades\n"
    "\n"
    "GOLDEN RULES (real money)\n"
    "- Never invent a price, balance, position, or trade result. If you have not "
    "pulled it from a tool this turn, say you don't have it yet.\n"
    "- Before executing ANY order, state the exact order (symbol, side, volume, SL, TP) "
    "and wait for the user's explicit confirmation. Never fire on a guess, a hint, or "
    "a vibe — only on a clear yes.\n"
    "- Risk first. Say the downside, not just the upside. If a trade looks dangerous, "
    "tell him plainly.\n"
    "- Check account.trade_allowed before promising an order will go through.\n"
)


# --- SMC strategy context ----------------------------------------------
# Injected into the system prompt ONLY when the user has enabled SMC reasoning
# in settings. When disabled this returns "" — the model then sees zero
# structure levels and cannot lean on them. That is the toggle doing its job.
# Cached with a short TTL so we don't re-fetch candles on every request: the
# poller ticks every 2s but candle structure moves slowly.
_smc_cache: dict = {"ts": 0.0, "key": "", "text": ""}
_SMC_TTL = 60.0
_SMC_MAX_SYMBOLS = 4


def _smc_context() -> str:
    if not taia_setting_enabled("smc_enabled"):
        return ""
    try:
        from trading.mt5_bridge import watchlist as _wl, candles as _candles
        from trading.smc import analyze_and_format

        wl = _wl() or []
        if not wl:
            return ""
        tf = str(taia_setting_get("smc_timeframe", "M15"))
        htf = str(taia_setting_get("smc_htf", "H1"))
        min_rr = float(taia_setting_get("smc_min_rr", 2.0))
        min_score = int(taia_setting_get("smc_min_score", 3))
        key = "|".join(wl) + "|" + tf + "|" + htf
        now = time.time()
        if _smc_cache["key"] == key and (now - _smc_cache["ts"]) < _SMC_TTL:
            return _smc_cache["text"]

        parts: list[str] = []
        for sym in wl[:_SMC_MAX_SYMBOLS]:
            base = _candles(sym, tf, 160).get("candles") or []
            if len(base) < 20:
                continue
            htf_candles = _candles(sym, htf, 160).get("candles") or []
            _, txt = analyze_and_format(
                sym, tf=tf, htf_candles=htf_candles, htf_tf=htf,
                candles=base, min_rr=min_rr, min_score=min_score)
            if txt:
                parts.append(txt)
        text = "\n\n".join(parts)
        _smc_cache.update(ts=now, key=key, text=text)
        return text
    except Exception as e:
        return f"(SMC analysis unavailable: {e})"


def _execution_context() -> str:
    """Execution policy block, driven by the execution_mode setting.

    Manual is the shipped default: she presents the ticket and waits for an
    explicit yes. Auto relaxes that to "announce and fire", but the hard lot
    cap and daily loss cap still apply — and the lot cap is enforced again in
    the order endpoint, not just here in prose, so a prompt-injected model
    can't size past it.
    """
    try:
        mode = str(taia_setting_get("execution_mode", "manual") or "manual").lower()
        cap = taia_setting_get("execution_max_lots", 1.0)
        loss = taia_setting_get("execution_daily_loss_cap", 0.0)
        risk = taia_setting_get("risk_default_risk_pct", 1.0)
    except Exception:
        mode, cap, loss, risk = "manual", 1.0, 0.0, 1.0

    if mode == "auto":
        head = ("## Execution policy (current): AUTO\n"
                "- You may place an order as soon as your own analysis confirms a "
                "qualifying setup \u2014 you do not need a second yes. Announce the "
                "exact order as you place it: symbol, side, volume, SL, TP.\n"
                "- There is no autonomous loop behind this yet: you still act only "
                "during a turn, never while idle.\n")
    else:
        head = ("## Execution policy (current): MANUAL\n"
                "- Never place an order yourself. Present the exact ticket (symbol, "
                "side, volume, SL, TP) and wait for an explicit yes.\n"
                "- This is the shipped default. A hint or a vibe is not a yes.\n")

    tail = f"- Hard cap: never size above {cap} lots on a single order.\n"
    try:
        if risk:
            tail += f"- Default risk: {risk}% of account per trade.\n"
    except Exception:
        pass
    try:
        if loss and float(loss) > 0:
            tail += (f"- Daily loss cap: stop opening new trades once the day is "
                     f"down {loss} account currency.\n")
    except Exception:
        pass
    return head + tail


def _system_prompt() -> str:
    now = datetime.now().strftime("%A, %B %d %Y, %I:%M %p")
    old_facts = _load_legacy_facts_text()
    old_routines = _load_routines_text()

    lang = os.environ.get("LYDIA_LANG", "en")
    lang_instruction = _LANG_INSTRUCTION.get(lang, _LANG_INSTRUCTION["en"])

    tier = get_effort_settings().get("tier", "balanced")
    trade_mode = _TRADE_DEPTH.get(tier, _TRADE_DEPTH["balanced"])

    prompt = f"""{_IDENTITY}

{lang_instruction}

## Trading mode (current): {tier.upper()}
{trade_mode}

{_execution_context()}

{market_context()}

{_smc_context()}

## Other Capabilities
- See the screen, click, type, scroll, take screenshots, and vision/image analysis.
- Web search and market news (economic calendar, headlines).
- Open apps, control media, manage windows.
- Notifications, timers, and the schedule (e.g. economic-event reminders).
- File read/write and Python/shell if a quick calculation or note is needed.

## Tool-Using Rules
- JUST DO IT for safe read-only actions (snapshot, candles, watchlist, news).
- Before any order, confirm the exact parameters with the user first.
- Chain up to {_MAX_TOOL_LOOPS} tool calls. After placing an order, verify it landed
  by re-reading positions or the account snapshot.

{old_facts}

{old_routines}

Current time: {now}. Answer concisely unless more detail is needed."""
    return prompt.strip()


# Only re-run tools that are safe to repeat after a transient failure —
# read-only or idempotent ones. Side-effectful tools (write_file,
# kill_process, power_command, ...) must NEVER be auto-retried: a "failure"
# there can actually be a partial success, and re-dispatching doubles the
# damage. Let the LLM see the error and decide instead.
_RETRYABLE_TOOLS = {
    "web_search", "fetch_page", "get_weather",
    "read_screen", "find_on_screen", "get_open_windows",
    "read_file", "list_files", "get_clipboard", "get_volume",
    "get_system_info", "read_project", "grep_project", "read_symbols",
    "run_tests", "run_linter", "check_apps_open", "discord_check",
    "schedule_list", "spotify_search", "analyze_image",
}


def _execute_tool(name: str, args: dict) -> str:
    result = dispatch(name, args)
    if name in _RETRYABLE_TOOLS and result.startswith("Tool '") and "failed:" in result:
        time.sleep(0.5)
        result = dispatch(name, args)
    return result


_REMEMBER_PATTERNS = [
    "remember that", "remember this", "remember:",
    "remember ", "don't forget", "don't forget that", "keep in mind",
    "note that", "note this", "save this", "save that",
]


def _extract_remember(user_text: str) -> str | None:
    """If the user is asking Lydia to remember something, return the memory text."""
    lowered = user_text.strip().lower()
    for pat in _REMEMBER_PATTERNS:
        idx = lowered.find(pat)
        if idx != -1:
            # Grab everything after the trigger phrase.
            rest = user_text[idx + len(pat):].strip().strip(":'\".,!?")
            # Clean up "remember that X" / "remember X"
            rest = rest.lstrip("that ").strip()
            if rest and len(rest) > 2:
                return rest
    return None


def _handle_remember(user_text: str) -> str | None:
    """If it's a remember request, store it, broadcast to UI, return confirmation."""
    memory_text = _extract_remember(user_text)
    if not memory_text:
        return None
    _memory.store_remember(memory_text)
    broadcast({"type": "memory", "text": memory_text})
    return f"Got it — I'll remember that: \"{memory_text}\""


def process_request(user_text: str) -> str:
    """Process a user message and return the assistant's text response."""
    _check_abort()
    broadcast({"type": "user", "text": user_text})
    broadcast({"type": "status", "message": "Thinking..."})

    # "Remember X" — short-circuit before the LLM: store it, show it as a
    # bubble in the UI, and reply immediately.
    remember_reply = _handle_remember(user_text)
    if remember_reply:
        _context.add("user", user_text)
        _context.add("assistant", remember_reply)
        broadcast({"type": "response", "text": remember_reply})
        # Notify so the user knows it landed without having to watch the screen.
        notify_done(remember_reply)
        return remember_reply

    facts = _memory.search_facts(user_text)
    messages = _context.get_messages()
    if facts:
        messages = [{"role": "system", "content": "Relevant context: " + "; ".join(facts)}] + messages
    messages.append({"role": "user", "content": user_text})
    full_messages = [{"role": "system", "content": _system_prompt()}] + messages

    try:
        response_text = chat_with_tools(
            messages=full_messages,
            tool_schemas=TOOL_SCHEMAS,
            execute_tool=_execute_tool,
            max_loops=_MAX_TOOL_LOOPS,
            abort_check=_check_abort,
            on_tool_call=lambda name, args: broadcast({"type": "tool", "name": name, "args": args}),
            on_tool_result=lambda name, result: broadcast({"type": "tool_result", "name": name, "result": result[:200]}),
            on_delta=lambda text: broadcast({"type": "assistant_delta", "text": text}),
            on_reasoning=lambda text: broadcast({"type": "thought_delta", "text": text}),
        )
    except Aborted:
        raise
    except Exception as e:
        print(f"[WARN] LLM call failed: {e}")
        response_text = f"Oops, my brain's having a moment — try again?"

    _context.add("user", user_text)
    _context.add("assistant", response_text)
    _memory.extract_and_store_facts(response_text, user_text)

    broadcast({"type": "response", "text": response_text})
    # Task finished — Windows chime + Action Center toast.
    notify_done(response_text)
    return response_text


def _handle_typed_command(text: str) -> None:
    """Run one request straight from the console and print the answer."""
    _abort.clear()
    try:
        response = process_request(text)
        print(f"\nLydia: {response}")
    except Aborted:
        print("\n[Lydia] Stopped.")
    finally:
        _abort.clear()


def _keyboard_listener() -> None:
    """Console hotkeys: Esc = stop, F2 = type a command."""
    try:
        import msvcrt
    except ImportError:
        return
    while True:
        try:
            if msvcrt.kbhit():
                key = msvcrt.getch()
                if key == b'\x1b':
                    abort_all()
                elif key in (b'\x00', b'\xe0'):
                    special = msvcrt.getch()
                    if special == b'<':
                        cmd = input("\n[Type your command] ")
                        if cmd.strip():
                            _handle_typed_command(cmd.strip())
            time.sleep(0.05)
        except Exception:
            time.sleep(0.1)


def _keyboard_repl() -> None:
    """Console REPL — the terminal is a first-class interface now.

    Blocks forever, replacing the old wake-word listener that used to own the
    main thread. When the console has no TTY (run.bat launches the backend
    minimised, or output is piped) there is nothing to read, so we idle
    instead of spinning on a closed stdin.
    """
    try:
        interactive = sys.stdin is not None and sys.stdin.isatty()
    except Exception:
        interactive = False

    if not interactive:
        while True:
            time.sleep(3600)

    print("  Type a message and press Enter. Ctrl+C to quit.")
    while True:
        try:
            line = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[Lydia] console closed — the UI keeps running.")
            while True:
                time.sleep(3600)
        if not line:
            continue
        if is_stop_command(line):
            abort_all()
            continue
        _handle_typed_command(line)


def main() -> None:
    import webbrowser
    from brain.web import start_web_background
    from brain.discord_bot import start_discord_bot

    cfg = load_config()
    host = cfg.get("ui", {}).get("host", "0.0.0.0")
    port = cfg.get("ui", {}).get("port", 8765)

    print("=" * 50)
    print("  LYDIA T.A.I — trading desk")
    print("=" * 50)
    print(f"  UI: http://localhost:{port}")
    print("  Keys: Esc = stop | F2 = type a command")
    print("  Voice pipeline removed — keyboard + UI only.")
    print("=" * 50)

    start_web_background(port=port, host=host)
    start_discord_bot()
    # Warm the economic-calendar cache in the background so the injected
    # market context has news from the first turn (never blocks startup).
    def _warm_news() -> None:
        try:
            from trading.news import fetch_calendar
            fetch_calendar()
        except Exception:
            pass
    threading.Thread(target=_warm_news, daemon=True).start()
    threading.Thread(target=_keyboard_listener, daemon=True).start()

    if not os.environ.get("LYDIA_OPEN_UI") == "0":
        webbrowser.open(f"http://localhost:{port}")

    _keyboard_repl()


if __name__ == "__main__":
    main()
