"""Lydia — voice AI assistant for Windows. API-only brain, voice, tools, memory."""
from __future__ import annotations
import json
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import sounddevice as sd

from brain.config import load_config, set_active_provider, get_active_provider, get_providers, get_effort_settings
from brain.context import ContextManager
from brain.events import broadcast
from brain.llm import chat_with_tools
from brain.memory import Memory
from brain.notify import notify_done
from brain.stt import record_until_silence, transcribe_audio, set_abort_event, warm_local_model
from brain.tts import speak, speak_streamed, is_speaking, stop_speaking
from brain.tools.router import TOOL_SCHEMAS, dispatch
from brain.wake import listen_for_wake_word
from trading.brain import market_context
from trading.settings import enabled as taia_setting_enabled, get as taia_setting_get

_MAX_TOOL_LOOPS = int(load_config().get("brain", {}).get("max_tool_loops", 15))
_MEMORY_DIR = Path(__file__).parent.parent / "memory_data"

_abort = threading.Event()
set_abort_event(_abort)
_muted = threading.Event()

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
    """Force-stop talking / thinking right now.

    Sets the abort flag so any in-flight generation is interrupted,
    kills whatever is being spoken, and tells the UI we're idle again.
    The flag stays SET until the running worker actually stops, so a
    background request can't keep going (or speak) after a Stop.
    """
    _abort.set()
    stop_speaking()
    broadcast({"type": "status", "message": "Stopped."})


def reset_abort() -> None:
    """Re-arm for a fresh request after a stop/abort.

    Only the worker that owns the cancelled request should clear the
    flag once it has fully unwound. This guarantees a fired Stop never
    leaves a half-finished response that keeps talking anyway.
    """
    _abort.clear()


def is_stop_command(text: str) -> bool:
    cleaned = text.strip().lower().rstrip(".,!?")
    return cleaned in {"stop", "cancel", "abort", "shut up", "be quiet", "nevermind"}


def _check_abort() -> None:
    if _abort.is_set():
        raise Aborted()


def is_muted() -> bool:
    return _muted.is_set()


def toggle_mute() -> None:
    if _muted.is_set():
        _muted.clear()
        broadcast({"type": "mute", "muted": False})
    else:
        _muted.set()
        stop_speaking()
        broadcast({"type": "mute", "muted": True})


def speak_if_unmuted(text: str) -> None:
    if not _muted.is_set():
        speak(text)


def speak_streamed_if_unmuted(text: str) -> None:
    if not _muted.is_set():
        speak_streamed(text)


def interrupt_and_speak(text: str) -> None:
    """Mute-gated speech that ALWAYS cuts off any in-progress audio
    before speaking the new message. Newest message wins."""
    if _muted.is_set():
        return
    # Cancel whatever is currently being spoken so the new message takes over.
    stop_speaking()
    speak_streamed(text)


def _play_beep() -> None:
    try:
        t = np.linspace(0, 0.12, int(24000 * 0.12), endpoint=False)
        tone = 0.3 * np.sin(2 * np.pi * 600 * t).astype(np.float32)
        sd.play(tone, samplerate=24000)
        sd.wait()
    except Exception:
        pass


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
    "You are LYDIA T.A.I — the trading-assistant build of Lydia, a voice + screen AI "
    "that lives on the user's Windows PC. Your entire job is trading.\n"
    "\n"
    "WHO YOU ARE\n"
    "- A sharp, focused trading co-pilot. Warm and human in tone (no corporate robot "
    "talk, no 'As an AI' nonsense), but your subject is the markets: live prices, "
    "positions, risk, and order execution.\n"
    "- Keep it real, direct, and concise. No corporate padding, no formalities.\n"
    "- You are not a generic chatbot and not a coding buddy — you are a trading desk.\n"
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


# --- SMC strategy context (Phase 5) ------------------------------------
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
    # Newest message wins: immediately cut any audio still being spoken
    # so we never keep talking over a brand-new request.
    stop_speaking()
    _play_beep()
    broadcast({"type": "user", "text": user_text})
    broadcast({"type": "status", "message": "Thinking..."})

    # "Remember X" — short-circuit before the LLM: store it, show it as a
    # 3D bubble in the UI, and reply immediately.
    remember_reply = _handle_remember(user_text)
    if remember_reply:
        _context.add("user", user_text)
        _context.add("assistant", remember_reply)
        broadcast({"type": "response", "text": remember_reply})
        # Remember replies are never spoken aloud — notify so the user knows
        # it landed without having to watch the screen.
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
            on_progress=lambda: speak_if_unmuted("Working on it."),
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
    # Task finished — Windows chime + toast when the reply isn't being read
    # aloud (voice is the cue then; we only ding for silent completions).
    notify_done(response_text)
    return response_text


def handle_wake() -> None:
    _abort.clear()
    try:
        _handle_wake_inner()
    except Aborted:
        pass
    except Exception as e:
        import traceback
        print(f"[ERROR] {e}")
        traceback.print_exc()
        if not _abort.is_set():
            speak_if_unmuted("Sorry, something went wrong.")
    finally:
        _abort.clear()
        from brain.wake import consume_pending_wake
        if consume_pending_wake():
            time.sleep(0.4)
            handle_wake()
        else:
            broadcast({"type": "status", "message": "Ready."})


def _handle_wake_inner() -> None:
    if is_speaking():
        stop_speaking()
        broadcast({"type": "status", "message": "Ready."})
        return
    broadcast({"type": "status", "message": "Wake."})
    speak_if_unmuted("Yes?")

    broadcast({"type": "status", "message": "Listening..."})
    from brain.wake import pause_wake_mic, resume_wake_mic
    pause_wake_mic()
    time.sleep(0.15)
    try:
        audio = record_until_silence()
    finally:
        resume_wake_mic()
    _check_abort()

    if len(audio) / 16000 < 0.5:
        speak_if_unmuted("Didn't quite catch that — go ahead?")
        return

    user_text = transcribe_audio(audio)
    if not user_text.strip():
        speak_if_unmuted("Didn't quite catch that — go ahead?")
        return

    if is_stop_command(user_text):
        abort_all()
        raise Aborted()

    response_text = process_request(user_text)
    _check_abort()
    broadcast({"type": "status", "message": "Speaking..."})
    speak_streamed_if_unmuted(response_text)


def _keyboard_listener() -> None:
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
                    elif special == b'R':
                        toggle_mute()
            time.sleep(0.05)
        except Exception:
            time.sleep(0.1)


def _handle_typed_command(text: str) -> None:
    _abort.clear()
    try:
        response = process_request(text)
        interrupt_and_speak(response)
    except Aborted:
        pass
    finally:
        _abort.clear()


def main() -> None:
    import webbrowser
    from brain.web import start_web_background
    from brain.discord_bot import start_discord_bot

    cfg = load_config()
    host = cfg.get("ui", {}).get("host", "0.0.0.0")
    port = cfg.get("ui", {}).get("port", 8765)

    print("=" * 50)
    print("  Lydia — Voice AI Assistant")
    print("=" * 50)
    print(f"  UI: http://localhost:{port}")
    print("  Keys: Esc = stop | F2 = type | Insert = mute")
    print("=" * 50)

    start_web_background(port=port, host=host)
    start_discord_bot()
    # Preload the local STT model in the background so the first voice
    # command doesn't stall for ~20s while whisper loads.
    threading.Thread(target=warm_local_model, daemon=True).start()
    # Warm the economic-calendar cache in the background so the injected
    # market context has news from the first turn (never blocks the voice loop).
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

    speak_if_unmuted("Lydia online.")
    listen_for_wake_word(handle_wake)


if __name__ == "__main__":
    main()
