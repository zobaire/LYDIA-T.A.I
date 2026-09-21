"""Text-to-speech using Edge TTS."""
from __future__ import annotations
import asyncio
import re
import shutil
import subprocess
import tempfile
import threading
import wave
from pathlib import Path

import edge_tts
import numpy as np
import sounddevice as sd

from brain.config import load_config


_SPEAKING = threading.Event()
_INTERRUPT = threading.Event()
_MUTED = threading.Event()

_EMOJI_RE = re.compile(
    "[\U0001F600-\U0001F64F"
    "\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F6FF"
    "\U0001F700-\U0001F77F"
    "\U0001F780-\U0001F7FF"
    "\U0001F800-\U0001F8FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FA6F"
    "\U0001FA70-\U0001FAFF"
    "\U00002702-\U000027B0"
    "\U0000FE0F"
    "\U0000200D"
    "]+",
    flags=re.UNICODE,
)


def is_speaking() -> bool:
    return _SPEAKING.is_set()


def stop_speaking() -> None:
    _INTERRUPT.set()
    sd.stop()
    _SPEAKING.clear()


def is_muted() -> bool:
    return _MUTED.is_set()


def set_muted(muted: bool) -> None:
    """Enable/disable ALL voice output. Turning mute ON cuts any audio instantly
    and the flag stays set, so no speech path can start while muted."""
    if muted:
        _MUTED.set()
        stop_speaking()
    else:
        _MUTED.clear()


def _pcm_from_mp3(path: str) -> np.ndarray | None:
    """Decode an MP3 file to a normalized float32 numpy array using ffmpeg."""
    if not shutil.which("ffmpeg"):
        return None
    wav_path = path.replace(".mp3", ".wav")
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", path, "-ar", "24000", "-ac", "1", wav_path],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return None
        with wave.open(wav_path, "rb") as wf:
            frames = wf.readframes(wf.getnframes())
            samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32767.0
        return samples
    except Exception:
        return None
    finally:
        try:
            Path(wav_path).unlink(missing_ok=True)
        except Exception:
            pass


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r'(?<=[.!?])\s+', text)
    return [p.strip() for p in parts if p.strip()]


def _clean_for_speech(text: str) -> str:
    """Strip emoji, markdown, and other non-spoken Unicode so TTS reads clean speech."""
    text = _EMOJI_RE.sub("", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"#{1,6}\s+", "", text)
    text = re.sub(r"^\s*[-*]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"[\u201c\u201d\u2018\u2019]", '"', text)
    text = re.sub(r"[\u2014\u2013]", "-", text)
    text = re.sub(r"[\u2026]", "...", text)
    return text.strip()


def _wait_or_interrupt() -> bool:
    while sd.get_stream() and sd.get_stream().active:
        if _INTERRUPT.is_set():
            sd.stop()
            return True
        _INTERRUPT.wait(0.05)
    return _INTERRUPT.is_set()


async def _edge_tts_to_file(text: str, path: str, voice: str, speed: str) -> None:
    communicate = edge_tts.Communicate(text, voice=voice, rate=speed)
    await communicate.save(path)


_VOICE_DEFAULTS = {
    "en": "en-US-AriaNeural",
    "ar": "ar-EG-SalmaNeural",   # Modern Standard Arabic (Egyptian accent), female
    "zh": "zh-CN-XiaoxiaoNeural",
}


def _pick_voice(cfg: dict, lang: str | None = None) -> str:
    """Return the TTS voice matching the current UI language."""
    if lang is None:
        import os
        lang = os.environ.get("LYDIA_LANG", "en")
    if lang in _VOICE_DEFAULTS:
        return cfg.get(f"voice_{lang}", _VOICE_DEFAULTS[lang])
    return cfg.get("voice", _VOICE_DEFAULTS["en"])


def _speak_file(path: str) -> None:
    if _MUTED.is_set() or _INTERRUPT.is_set():
        return
    audio = _pcm_from_mp3(path)
    if audio is None:
        return
    if _MUTED.is_set() or _INTERRUPT.is_set():
        return
    _SPEAKING.set()
    sd.play(audio, samplerate=24000)
    _wait_or_interrupt()
    _SPEAKING.clear()


def speak(text: str) -> None:
    """Speak text aloud. Interruptible via stop_speaking()."""
    if _MUTED.is_set():
        return
    text = _clean_for_speech(text)
    if not text:
        return
    _INTERRUPT.clear()
    cfg = load_config().get("tts", {})
    provider = cfg.get("provider", "edge")

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        out_path = f.name

    try:
        if provider == "edge":
            asyncio.run(_edge_tts_to_file(
                text, out_path,
                _pick_voice(cfg),
                cfg.get("speed", "+0%"),
            ))
        else:
            return
        # Newest message wins: if a stop/mute fired while the audio was being
        # generated, drop it entirely — never play stale speech over the
        # message that just came in.
        if _MUTED.is_set() or _INTERRUPT.is_set():
            return
        _speak_file(out_path)
    finally:
        # NOTE: do NOT clear _INTERRUPT here. If we were interrupted mid-speech
        # the flag must stay SET so the caller (speak_streamed) knows to stop.
        # The next speak() call clears it at entry.
        try:
            Path(out_path).unlink(missing_ok=True)
        except Exception:
            pass


def speak_streamed(text: str) -> None:
    """Speak text sentence by sentence for lower latency."""
    if _MUTED.is_set():
        return
    sentences = _split_sentences(text)
    if not sentences:
        return
    if len(sentences) == 1:
        speak(text)
        return

    _INTERRUPT.clear()
    _SPEAKING.set()
    try:
        for sentence in sentences:
            if _MUTED.is_set() or _INTERRUPT.is_set():
                break
            speak(sentence)
            # speak() no longer clears the interrupt flag, so a mute/stop
            # fired mid-sentence reliably halts the rest of the stream.
            if _MUTED.is_set() or _INTERRUPT.is_set():
                break
    finally:
        _SPEAKING.clear()
        _INTERRUPT.clear()


