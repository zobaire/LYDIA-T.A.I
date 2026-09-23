"""Notifications — the only out-of-band cue that something happened.

Since the voice pipeline was removed, Windows is now the only way Lydia can
reach you when you aren't looking at the browser tab. Three outputs:

  1. play_done_sound()   — the stock Windows notification sound (winsound alias),
                           so it feels native instead of some synth blip.
  2. show_windows_toast() — a real Action Center toast via PowerShell WinRT
                           (no extra deps, works on Win10/11).
  3. broadcast()          — the websocket path, for the live UI.

Routing (phase 2)
-----------------
Every notification travels through `notify(title, message, channel=...)`.
`channel` is one of:

    task      — a reply / task finished
    schedule  — a scheduled reminder came due
    trade     — a trading alert, fill, stop-out or take-profit

Each channel has a route setting in trading/settings.json controlling HOW it
arrives: `off` | `toast` | `sound` | `both`. The task channel additionally
honours the legacy `notify_done_toast` / `notify_done_sound` booleans (ANDed
with the route), so you can kill just the chime without killing the toast.

Quiet hours gate (phase 5)
--------------------------
`should_notify(channel)` is the single gate in front of every delivery. It
handles the overnight wrap (23:00 -> 07:00 spans midnight) and lets trade
alerts punch through when `quiet_hours_break_through_trades` is on — the
default, because a stop-out at 2 AM is not something to sleep through.

Source of truth
---------------
Settings live in `trading/settings.json` (schema in trading/settings.py) and
are surfaced in the UI. `config.yaml -> notifications:` is no longer read;
those two flags moved into the settings store so the drawer and the backend
can never disagree.

Everything runs in daemon threads — never blocks the brain.
"""
from __future__ import annotations
import subprocess
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path

# Channel -> settings key holding that channel's route.
_ROUTE_KEYS = {
    "task": "notify_route_task",
    "schedule": "notify_route_schedule",
    "trade": "notify_route_trade",
}


def _setting(key: str, default):
    """Read a setting without ever raising (import-time safety)."""
    try:
        from trading.settings import get
        return get(key, default)
    except Exception:
        return default


# --- routing ---------------------------------------------------------------

def route_for(channel: str) -> str:
    """Delivery route for a channel: 'off' | 'toast' | 'sound' | 'both'."""
    key = _ROUTE_KEYS.get(channel)
    if not key:
        return "both"
    val = str(_setting(key, "both") or "both").strip().lower()
    if val not in ("off", "toast", "sound", "both"):
        return "both"
    if channel == "task":
        # Legacy fine-grained flags, ANDed with the coarse route.
        toast_ok = bool(_setting("notify_done_toast", True))
        sound_ok = bool(_setting("notify_done_sound", True))
        want_toast = val in ("toast", "both") and toast_ok
        want_sound = val in ("sound", "both") and sound_ok
        return ("both" if (want_toast and want_sound)
                else "toast" if want_toast
                else "sound" if want_sound
                else "off")
    return val


# --- quiet hours -----------------------------------------------------------

def _minutes(hhmm: str) -> int | None:
    """'23:00' -> 1380. None if unparseable."""
    try:
        h, m = str(hhmm).split(":")
        h, m = int(h), int(m)
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h * 60 + m
    except Exception:
        pass
    return None


def in_quiet_hours(now: datetime | None = None) -> bool:
    """True if `now` falls inside the configured quiet window.

    Handles the overnight wrap. A zero-length or unparseable window is
    treated as 'not quiet' rather than muting everything forever.
    """
    try:
        if not _setting("quiet_hours_enabled", False):
            return False
        frm = _minutes(_setting("quiet_hours_from", "23:00"))
        to = _minutes(_setting("quiet_hours_to", "07:00"))
        if frm is None or to is None or frm == to:
            return False
        now = now or datetime.now()
        cur = now.hour * 60 + now.minute
        if frm < to:
            return frm <= cur < to           # same-day window
        return cur >= frm or cur < to        # wraps midnight
    except Exception:
        return False


def should_notify(channel: str = "task") -> bool:
    """The single gate in front of every delivery. True = go ahead and fire."""
    if route_for(channel) == "off":
        return False
    if in_quiet_hours():
        # Trade alerts break through by default; everything else waits.
        if channel == "trade":
            return bool(_setting("quiet_hours_break_through_trades", True))
        return False
    return True


def quiet_state() -> dict:
    """Diagnostics for the UI / a health check."""
    return {
        "enabled": bool(_setting("quiet_hours_enabled", False)),
        "active": in_quiet_hours(),
        "from": _setting("quiet_hours_from", "23:00"),
        "to": _setting("quiet_hours_to", "07:00"),
        "break_through_trades": bool(
            _setting("quiet_hours_break_through_trades", True)),
    }


# --- text shaping ----------------------------------------------------------

def _clean_summary(text: str, limit: int = 150) -> str:
    """Flatten a markdown response into one short readable line for a toast."""
    import re
    s = text or ""
    # Fences + code spans first (so we don't half-strip inside them).
    s = re.sub(r"```[\s\S]*?```", "", s)
    s = re.sub(r"`([^`]*)`", r"\1", s)
    # Emphasis: **bold** / *italic* / __under__ — keep single underscores
    # (they're everywhere in filenames), kill asterisks entirely.
    s = s.replace("**", "").replace("*", "")
    # Headings.
    s = re.sub(r"^#{1,6}\s+", "", s, flags=re.MULTILINE)
    # Inline links -> their label.
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    s = " ".join(s.split())
    if len(s) > limit:
        s = s[: limit - 1].rstrip() + "\u2026"
    return s


# --- delivery --------------------------------------------------------------

def play_done_sound() -> None:
    """Play the stock Windows notification sound, non-blocking.

    winsound is part of the Windows stdlib, so there is no dependency and no
    fallback synth path to maintain. Non-Windows is a silent no-op. Never raises.
    """
    if sys.platform != "win32":
        return

    def _play() -> None:
        try:
            import winsound
            # 'SystemNotification' = the modern Windows toast/chime sound.
            winsound.PlaySound(
                "SystemNotification", winsound.SND_ALIAS | winsound.SND_ASYNC
            )
        except Exception:
            pass

    threading.Thread(target=_play, daemon=True).start()


def show_windows_toast(title: str, message: str) -> None:
    """Fire a real Windows 10/11 Action Center toast via PowerShell WinRT.

    Uses PowerShell's own registered AUMID so the toast shows without any
    app packaging. The toast audio is silenced — we already play our own
    chime (play_done_sound) so we never double-ding. Title/message travel
    through env vars (never string-embedded) and get XML-escaped in PS, so
    quotes/apostrophes/& in responses can't break anything. Fire & forget.
    """
    if sys.platform != "win32":
        return

    def _toast() -> None:
        tmp = None
        try:
            import os
            ps = (
                "[Windows.UI.Notifications.ToastNotificationManager,"
                " Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null\n"
                "[Windows.Data.Xml.Dom.XmlDocument,"
                " Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null\n"
                "$t = [System.Security.SecurityElement]::Escape($env:LYDIA_TITLE)\n"
                "$m = [System.Security.SecurityElement]::Escape($env:LYDIA_MSG)\n"
                "$xmlText = '<toast><audio silent=\"true\"/><visual><binding template=\"ToastGeneric\">'"
                " + '<text>' + $t + '</text><text>' + $m + '</text>"
                "</binding></visual></toast>'\n"
                "$xml = New-Object Windows.Data.Xml.Dom.XmlDocument\n"
                "$xml.LoadXml($xmlText)\n"
                "$appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}"
                "\\WindowsPowerShell\\v1.0\\powershell.exe'\n"
                "[Windows.UI.Notifications.ToastNotificationManager]"
                "::CreateToastNotifier($appId).Show($xml)\n"
            )
            with tempfile.NamedTemporaryFile(
                "w", suffix=".ps1", delete=False, encoding="utf-8-sig"
            ) as f:
                f.write(ps)
                tmp = f.name
            env = os.environ.copy()
            env["LYDIA_TITLE"] = (title or "Lydia")[:60]
            env["LYDIA_MSG"] = (message or "")[:240]
            subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", tmp],
                capture_output=True, timeout=15,
                env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception:
            pass
        finally:
            if tmp:
                try:
                    Path(tmp).unlink(missing_ok=True)
                except Exception:
                    pass

    threading.Thread(target=_toast, daemon=True).start()


def notify(title: str, message: str, channel: str = "task") -> bool:
    """Route one notification. Returns True if it was delivered.

    Never raises, never blocks. Quiet hours and the route setting are both
    applied here so no caller has to remember to check them.
    """
    try:
        if not should_notify(channel):
            return False
        route = route_for(channel)
        if route in ("sound", "both"):
            play_done_sound()
        if route in ("toast", "both"):
            show_windows_toast(title, message)
        return True
    except Exception:
        return False


# --- channel helpers -------------------------------------------------------

def notify_done(response_text: str) -> None:
    """Fire the task-done chime + toast for a finished response.

    Called by every completion path (UI chat, console REPL, remember
    short-circuit). There is nothing to suppress any more — no TTS means
    every finished reply is genuinely silent, so this always fires unless
    you turned the channel off or you're inside quiet hours.
    """
    if not response_text or not response_text.strip():
        return
    notify("Lydia \u2014 done", _clean_summary(response_text), channel="task")


def notify_reminder(title: str, message: str) -> None:
    """A scheduled reminder came due. Channel: schedule."""
    notify(title or "Reminder", message or "", channel="schedule")


def notify_trade(title: str, message: str) -> None:
    """A trading alert / fill / stop-out. Channel: trade."""
    notify(title or "Trade", message or "", channel="trade")
