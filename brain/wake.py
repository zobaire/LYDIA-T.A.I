"""Wake-word listener supporting Porcupine (custom "Hey Lydia"), openWakeWord, and console fallback."""
from __future__ import annotations
import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np

from brain.config import load_config, load_dotenv


_MIC_PAUSE = threading.Event()
_pending_wake = threading.Event()


def pause_wake_mic() -> None:
    _MIC_PAUSE.set()


def resume_wake_mic() -> None:
    _MIC_PAUSE.clear()


def set_pending_wake() -> None:
    _pending_wake.set()


def consume_pending_wake() -> bool:
    return _pending_wake.is_set()


def listen_for_wake_word(callback: Callable[[], None]) -> None:
    """Continuously listen for wake word. Blocks forever."""
    cfg = load_config().get("wake_word", {})
    if not cfg.get("enabled", True):
        print("[Wake] Wake word disabled in config. Using push-to-talk.")
        _console_loop(callback)
        return

    backend = cfg.get("backend", "openwakeword").lower()
    env = load_dotenv()
    keyword_path = cfg.get("keyword_path", "") or env.get("WAKE_KEYWORD_PATH", "")

    if backend == "porcupine" and keyword_path and Path(keyword_path).exists():
        try:
            _listen_porcupine(callback, cfg)
            return
        except Exception as e:
            print(f"[Wake] Porcupine failed ({e}). Falling back to openWakeWord 'hey_jarvis'.")
    elif backend == "porcupine":
        print("[Wake] Porcupine backend selected but no keyword file found.")
        print("[Wake] To use 'Hey Lydia', train it at https://console.picovoice.ai")
        print(f"[Wake] Set wake_word.keyword_path or WAKE_KEYWORD_PATH to the .ppn file.")
        print("[Wake] Falling back to openWakeWord 'hey_jarvis' for now.")

    try:
        _listen_openwakeword(callback, cfg)
    except Exception as e:
        print(f"[Wake] openWakeWord failed ({e}). Falling back to push-to-talk.")
        _console_loop(callback)


def _listen_porcupine(callback: Callable[[], None], cfg: dict) -> None:
    """Use Picovoice Porcupine with a custom wake word (e.g. 'Hey Lydia')."""
    import pvporcupine
    import pyaudio

    env = load_dotenv()
    access_key = cfg.get("access_key", "") or env.get("PICOVOICE_ACCESS_KEY", "")
    keyword_path = cfg.get("keyword_path", "") or env.get("WAKE_KEYWORD_PATH", "")

    if not access_key:
        raise RuntimeError("Porcupine backend requires access_key or PICOVOICE_ACCESS_KEY")

    porcupine = None
    if keyword_path and Path(keyword_path).exists():
        porcupine = pvporcupine.create(access_key=access_key, keyword_paths=[str(keyword_path)])
        print(f"[Wake] Porcupine listening with custom keyword: {Path(keyword_path).name}")
    else:
        # Try built-in "Hey Lydia"? Porcupine doesn't have one, so fail.
        raise RuntimeError(
            "Porcupine keyword file (.ppn) not found. "
            "Train 'Hey Lydia' at https://console.picovoice.ai and set wake_word.keyword_path."
        )

    pa = pyaudio.PyAudio()
    frame_length = porcupine.frame_length
    sample_rate = porcupine.sample_rate
    stream = pa.open(
        rate=sample_rate,
        channels=1,
        format=pyaudio.paInt16,
        input=True,
        frames_per_buffer=frame_length,
    )

    _busy = threading.Lock()
    print("[Wake] Listening for 'Hey Lydia' via Porcupine...")
    try:
        while True:
            if _MIC_PAUSE.is_set():
                if stream.is_active():
                    stream.stop_stream()
                while _MIC_PAUSE.is_set():
                    time.sleep(0.05)
                stream.start_stream()

            pcm = stream.read(frame_length, exception_on_overflow=False)
            pcm = np.frombuffer(pcm, dtype=np.int16)
            result = porcupine.process(pcm)
            if result >= 0:
                print(f"[Wake] Detected wake word (score={result})")
                if _busy.locked():
                    set_pending_wake()
                    from brain.main import abort_all
                    abort_all()
                    print("[Wake] Interrupted — will continue listening once current reply finishes.")
                else:
                    def _run():
                        with _busy:
                            callback()
                    threading.Thread(target=_run, daemon=True).start()
    finally:
        stream.stop_stream()
        stream.close()
        pa.terminate()
        porcupine.delete()


def _listen_openwakeword(callback: Callable[[], None], cfg: dict) -> None:
    """Use openWakeWord with a built-in model (fallback)."""
    import pyaudio
    from openwakeword.model import Model
    from openwakeword.utils import download_models

    model_name = cfg.get("model", "hey_jarvis")
    chunk_size = cfg.get("chunk_size", 1280)
    threshold = cfg.get("threshold", 0.5)

    try:
        download_models(model_names=[model_name])
    except Exception as e:
        print(f"[Wake] Model download note: {e}")

    for framework in ("onnx", "tflite"):
        try:
            oww = Model(wakeword_models=[model_name], inference_framework=framework)
            print(f"[Wake] openWakeWord loaded '{model_name}' with {framework}.")
            break
        except Exception as e:
            print(f"[Wake] {framework} load failed: {e}")
            continue
    else:
        print("[Wake] All frameworks failed. Falling back to push-to-talk.")
        _console_loop(callback)
        return
    audio = pyaudio.PyAudio()
    mic = audio.open(
        format=pyaudio.paInt16,
        channels=1,
        rate=16000,
        input=True,
        frames_per_buffer=chunk_size,
    )

    _busy = threading.Lock()
    ignore_until = 0.0

    print(f"[Wake] openWakeWord listening for '{model_name}'...")
    print("[Wake] Say the wake word or press F2 to type.")
    try:
        while True:
            if _MIC_PAUSE.is_set():
                if mic.is_active():
                    mic.stop_stream()
                while _MIC_PAUSE.is_set():
                    time.sleep(0.05)
                mic.start_stream()
                oww.reset()
                continue

            try:
                pcm = np.frombuffer(mic.read(chunk_size), dtype=np.int16)
            except Exception:
                time.sleep(0.05)
                continue

            predictions = oww.predict(pcm)
            score = predictions.get(model_name, 0)

            if score >= threshold:
                print(f"[Wake] Detected '{model_name}' (score={score:.2f})")

            if score < threshold:
                continue

            oww.reset()
            now = time.time()
            if now < ignore_until:
                continue

            if _busy.locked():
                set_pending_wake()
                from brain.main import abort_all
                abort_all()
                ignore_until = now + 0.6
                print("[Wake] Interrupted — will continue listening once current reply finishes.")
            else:
                ignore_until = now + 1.5
                def _run():
                    with _busy:
                        callback()
                threading.Thread(target=_run, daemon=True).start()
    finally:
        mic.stop_stream()
        mic.close()
        audio.terminate()


def _console_loop(callback: Callable[[], None]) -> None:
    """Fallback: press Enter to trigger the wake callback."""
    print("[Wake] Push-to-talk mode: press Enter to talk, 'q' + Enter to quit.")
    while True:
        try:
            key = input()
            if key.lower() == "q":
                break
            callback()
        except EOFError:
            time.sleep(0.5)
