"""Task-done notifications — a Windows chime + toast when Lydia finishes.

Fires when a request fully completes (response text is final). Two outputs:
  1. play_done_sound()  — the stock Windows notification sound (winsound alias),
     so it feels native instead of some synth blip.
  2. show_windows_toast() — a real notification balloon via PowerShell
     NotifyIcon (no extra deps, works on Win10/11).

Both are gated by config.yaml -> notifications:
    notifications:
      done_sound: true
      done_toast: true

Smart-skip logic: if Lydia is about to / currently reading the answer aloud,
the voice itself IS the notification, so we stay quiet and avoid dinging over
her speech. The "Speaking..." status broadcast + tts.is_speaking() tell us.
Only when the reply is NOT spoken (muted, background task, remember, ...) do
we chime + toast. Everything runs in daemon threads — never blocks the brain.
"""
from __future__ import annotations
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from brain.config import load_config
from brain.events import register
from brain.tts import is_speaking

# How long to wait for a "Speaking..." broadcast before assuming the reply
# won't be voiced (slightly generous — the broadcast comes right after
# process_request returns in every speak path).
_SPEAK_GRACE_S = 2.5
# After a Speaking broadcast, how long to wait for TTS to ACTUALLY start.
# Muted paths broadcast Speaking... but never produce audio — those should
# still notify instead of going silent.
_MUTED_GRACE_S = 1.8

_speaking_evt = threading.Event()
_listener_armed = False
_listener_lock = threading.Lock()


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

    Falls back to a tiny two-tone chime (via sounddevice) if winsound isn't
    available. Never raises.
    """
    def _play() -> None:
        try:
            if sys.platform == "win32":
                import winsound
                # 'SystemNotification' = the modern Windows toast/chime sound.
                winsound.PlaySound("SystemNotification", winsound.SND_ALIAS | winsound.SND_ASYNC)
                return
        except Exception:
            pass
        # Fallback: gentle synthesized double-blip.
        try:
            import numpy as np
            import sounddevice as sd
            t1 = np.linspace(0, 0.09, int(24000 * 0.09), endpoint=False)
            t2 = np.linspace(0, 0.14, int(24000 * 0.14), endpoint=False)
            tone = np.concatenate([
                0.22 * np.sin(2 * np.pi * 880 * t1),
                0.22 * np.sin(2 * np.pi * 1174.66 * t2),
            ]).astype(np.float32)
            sd.play(tone, samplerate=24000)
            sd.wait()
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


def _ensure_listener() -> None:
    """Arm the single module-level listener watching for Speaking broadcasts."""
    global _listener_armed
    with _listener_lock:
        if _listener_armed:
            return
        _listener_armed = True

    def _watch(evt: dict) -> None:
        if evt.get("type") == "status" and evt.get("message") == "Speaking...":
            _speaking_evt.set()

    register(_watch)


def notify_done(response_text: str) -> None:
    """Schedule the task-done notification for a finished response.

    Skips cleanly when the reply is about to be (or is being) spoken aloud —
    the speech is the cue then. Notifies only when the answer stays silent.
    """
    if not response_text or not response_text.strip():
        return
    sound_on = _flag("done_sound", True)
    toast_on = _flag("done_toast", True)
    if not sound_on and not toast_on:
        return
    _ensure_listener()
    # Only count a Speaking... broadcast that happens for THIS response —
    # reset the sticky flag so a previous reply's broadcast can't confuse us.
    _speaking_evt.clear()
    threading.Thread(
        target=_notify_worker, args=(response_text, sound_on, toast_on), daemon=True
    ).start()


def _notify_worker(response_text: str, sound_on: bool, toast_on: bool) -> None:
    try:
        # 1) Did a Speaking... broadcast arrive? (voice + web chat paths)
        got_speaking = _speaking_evt.wait(_SPEAK_GRACE_S)

        if got_speaking:
            # 2) Speaking was announced — wait to see if TTS actually starts.
            # If it does, she's reading the answer: no extra cue needed.
            deadline = time.time() + _MUTED_GRACE_S
            while time.time() < deadline:
                if is_speaking():
                    return  # real speech — skip, voice is the notification
                time.sleep(0.1)
            # Announced but never spoke (muted path) → fall through to notify.
        else:
            # No Speaking broadcast (terminal F2 path, remember replies, ...).
            # Double-check she isn't mid-audio anyway before we ding.
            if is_speaking():
                return

        summary = _clean_summary(response_text)
        if sound_on:
            play_done_sound()
        if toast_on:
            show_windows_toast("Lydia — done", summary)
    except Exception:
        pass
