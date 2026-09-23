"""Task-done notifications — a Windows chime + toast when Lydia finishes.

Fires when a request fully completes (response text is final). Two outputs:
  1. play_done_sound()  — the stock Windows notification sound (winsound alias),
     so it feels native instead of some synth blip.
  2. show_windows_toast() — a real notification balloon via PowerShell WinRT
     (no extra deps, works on Win10/11).

Both are gated by config.yaml -> notifications:
    notifications:
      done_sound: true
      done_toast: true

NOTE (voice removal): this used to carry "smart-skip" logic that deliberately
stayed silent when TTS was about to read the answer aloud — the reasoning
being that speech WAS the notification. That whole branch is gone along with
the voice pipeline, because with no TTS it would have suppressed every toast
she ever fired. This module no longer imports anything from a voice subsystem;
it is now the ONLY out-of-band cue that a task finished.

Everything runs in daemon threads — never blocks the brain.
"""
from __future__ import annotations
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from brain.config import load_config


def _flag(name: str, default: bool = True) -> bool:
    """Read a notifications.* toggle from config.yaml (default: on)."""
    try:
        cfg = load_config().get("notifications", {})
        val = cfg.get(name, default)
        return bool(val)
    except Exception:
        return default


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
        s = s[: limit - 1].rstrip() + "…"
    return s


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


def notify_done(response_text: str) -> None:
    """Fire the task-done chime + toast for a finished response.

    Called by every completion path (UI chat, console REPL, remember
    short-circuit). There is nothing to suppress any more — no TTS means
    every finished reply is genuinely silent, so the notification always fires.
    """
    if not response_text or not response_text.strip():
        return
    sound_on = _flag("done_sound", True)
    toast_on = _flag("done_toast", True)
    if not sound_on and not toast_on:
        return
    summary = _clean_summary(response_text)
    if sound_on:
        play_done_sound()
    if toast_on:
        show_windows_toast("Lydia — done", summary)
