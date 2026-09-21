"""Speech-to-text: local faster-whisper primary, Gemini fallback.

Gemini is the fallback because some cloud providers are region-blocked from
some networks (it returns 403 "Access denied" / kills the TLS handshake),
which made every transcription fail. The chain tries providers in order
with retries for transient network errors, so if one provider is down the
voice pipeline keeps working.
"""
from __future__ import annotations
import base64
import io
import os
import time
import wave

import numpy as np
import sounddevice as sd

from brain.config import load_config, load_dotenv


_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/openai/"

_ABORT_EVENT = None

_LOCAL_MODEL = None
_LOCAL_MODEL_NAME = None


def set_abort_event(event) -> None:
    """Register an optional threading.Event to interrupt recording."""
    global _ABORT_EVENT
    _ABORT_EVENT = event


def _save_wav(audio: np.ndarray, path: str, sample_rate: int = 16000) -> None:
    """Save a float32 mono numpy array as a 16-bit WAV file."""
    pcm = (audio * 32767).astype(np.int16)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())


def _wav_b64(audio: np.ndarray, sample_rate: int = 16000) -> str:
    """Encode float32 audio as base64 WAV for inline API uploads."""
    pcm = (audio * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())
    return base64.b64encode(buf.getvalue()).decode("ascii")


def record_until_silence(
    sample_rate: int = 16000,
    silence_threshold: float = 0.012,
    max_seconds: int = 30,
) -> np.ndarray:
    """Record microphone audio until silence is detected.

    Returns a float32 mono numpy array at sample_rate.
    """
    chunk = int(sample_rate * 0.3)
    recording = []
    silent_chunks = 0
    silent_chunks_needed = 7
    heard_speech = False
    speech_threshold = 0.015

    with sd.InputStream(samplerate=sample_rate, channels=1, dtype="float32") as stream:
        while True:
            if _ABORT_EVENT and _ABORT_EVENT.is_set():
                print("[STT] Recording aborted.")
                break

            data, _ = stream.read(chunk)
            flat = data.flatten()
            recording.append(flat)
            rms = float(np.sqrt(np.mean(flat ** 2)))

            if rms >= speech_threshold:
                heard_speech = True
                silent_chunks = 0
            elif heard_speech and rms < silence_threshold:
                silent_chunks += 1

            if heard_speech and silent_chunks >= silent_chunks_needed:
                print("[STT] Silence detected, stopping recording.")
                break
            if len(recording) * chunk >= max_seconds * sample_rate:
                print(f"[STT] Max recording time reached ({max_seconds}s)")
                break

    if not recording:
        return np.zeros(chunk, dtype=np.float32)
    return np.concatenate(recording)


def transcribe_audio(audio: np.ndarray, language: str | None = None) -> str:
    """Transcribe audio using the configured STT provider chain.

    Tries providers in order (primary from config, then fallbacks), with
    per-provider retries for transient SSL/network blips. Returns the first
    successful transcript, or a '[STT error: ...]' summary if all fail.
    """
    if language is None:
        language = os.environ.get("LYDIA_LANG", "en")
    cfg = load_config().get("stt", {})
    primary = cfg.get("provider", "gemini")
    chain = [(primary, cfg.get("model", "gemini-3.6-flash"))]
    for fb in cfg.get("fallbacks", []) or []:
        pk = fb.get("provider", "")
        if pk and pk != primary:
            chain.append((pk, fb.get("model", "")))
    if not chain:
        chain = [("gemini", "gemini-3.6-flash")]

    errors: list[str] = []
    for provider, model in chain:
        for attempt in range(1, 4):
            try:
                if provider == "local":
                    text, err = _transcribe_local(audio, model, language)
                elif provider == "gemini":
                    text, err = _transcribe_gemini(audio, model, language)
                else:
                    err = f"unknown provider '{provider}'"
                    text = ""
                if text and not err:
                    return text
                errors.append(f"{provider}: {err or 'empty transcript'}")
                break  # definite API error — no point hammering it
            except Exception as e:
                errors.append(f"{provider} attempt {attempt}: {e}")
                if attempt < 3:
                    time.sleep(attempt)  # 1s, 2s backoff for transient network blips
    return f"[STT error: {'; '.join(errors)}]"


def _get_local_model(model_name: str):
    """Load faster-whisper once and reuse it across calls (model stays in RAM)."""
    global _LOCAL_MODEL, _LOCAL_MODEL_NAME
    if _LOCAL_MODEL is None or _LOCAL_MODEL_NAME != model_name:
        from faster_whisper import WhisperModel
        _LOCAL_MODEL = WhisperModel(model_name, device="cpu", compute_type="int8")
        _LOCAL_MODEL_NAME = model_name
    return _LOCAL_MODEL


def warm_local_model() -> None:
    """Preload the local STT model in the background so the first voice
    command doesn't pay the cold-start cost (~20s+). Safe to call from a
    daemon thread at boot — runs once and caches the model in RAM."""
    try:
        cfg = load_config().get("stt", {})
        if cfg.get("provider") == "local":
            _get_local_model(cfg.get("model", "small"))
    except Exception as e:
        print(f"[STT] local model warm-up failed: {e}")


def _transcribe_local(audio: np.ndarray, model: str, language: str) -> tuple[str, str | None]:
    """Transcribe on-device with faster-whisper. No network, no region blocking.

    The default language hint ('en') is treated as auto-detect so mixed
    Darija / French / Arabic / English speech still transcribes correctly.
    An explicit non-default language (set via the UI language toggle) is
    passed through to constrain whisper to that language.
    """
    try:
        model_obj = _get_local_model(model or "small")
        # Darija is code-switched (Arabic + French + English), so let whisper
        # auto-detect instead of forcing Arabic and mangling the French words.
        lang = None if language in ("en", "ar", "auto", "") else language
        segments, _info = model_obj.transcribe(
            np.asarray(audio, dtype=np.float32),
            language=lang,
            beam_size=1,
            vad_filter=True,
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        return text, (None if text else "empty transcript")
    except Exception as e:
        return "", f"local STT error: {e}"


def _transcribe_gemini(audio: np.ndarray, model: str, language: str) -> tuple[str, str | None]:
    """Transcribe via Gemini's OpenAI-compatible endpoint. Returns (text, err)."""
    api_key = load_dotenv().get("GEMINI_API_KEY", "")
    if not api_key:
        return "", "GEMINI_API_KEY not set in .env"
    try:
        from openai import OpenAI
        client = OpenAI(base_url=_GEMINI_BASE, api_key=api_key, timeout=90.0)
        lang_hint = f" The audio is in {language}." if language else ""
        resp = client.chat.completions.create(
            model=model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": (
                        "Transcribe the speech in this audio clip exactly as spoken. "
                        "Reply with ONLY the transcript — no quotes, no commentary." + lang_hint
                    )},
                    {"type": "input_audio", "input_audio": {
                        "data": _wav_b64(audio), "format": "wav",
                    }},
                ],
            }],
            temperature=0.0,
        )
        text = (resp.choices[0].message.content or "").strip()
        return text, (None if text else "empty transcript")
    except Exception as e:
        return "", f"Gemini STT error: {e}"


