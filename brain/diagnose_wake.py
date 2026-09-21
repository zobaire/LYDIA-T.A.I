"""Quick wake-word diagnostic: shows which backend is active and mic audio levels."""
from __future__ import annotations
import time
from pathlib import Path

import numpy as np
import sounddevice as sd

from brain.config import load_config


def main() -> None:
    cfg = load_config().get("wake_word", {})
    print("=" * 50)
    print(" Lydia Wake-Word Diagnostic")
    print("=" * 50)
    print(f"enabled:  {cfg.get('enabled', True)}")
    print(f"backend:  {cfg.get('backend', 'openwakeword')}")

    env = {}
    env_file = Path(__file__).parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")

    keyword_path = cfg.get("keyword_path", "") or env.get("WAKE_KEYWORD_PATH", "")
    print(f"keyword_path: {keyword_path or '(not set)'}")
    if keyword_path:
        print(f"keyword exists: {Path(keyword_path).exists()}")

    if cfg.get("backend", "").lower() == "porcupine":
        try:
            import pvporcupine
            print("pvporcupine: OK")
        except ImportError:
            print("pvporcupine: NOT INSTALLED (pip install pvporcupine)")
        access_key = cfg.get("access_key", "") or env.get("PICOVOICE_ACCESS_KEY", "")
        print(f"PICOVOICE_ACCESS_KEY: {'set' if access_key else 'NOT SET'}")

    try:
        import pyaudio
        print("pyaudio: OK")
    except ImportError:
        print("pyaudio: NOT INSTALLED (pip install pyaudio)")

    try:
        from openwakeword.model import Model
        print("openwakeword: OK")
    except ImportError:
        print("openwakeword: NOT INSTALLED (pip install openwakeword)")

    print("\nMic level test (speak now, Ctrl+C to stop):")
    try:
        with sd.InputStream(samplerate=16000, channels=1, dtype="float32") as stream:
            while True:
                data, _ = stream.read(int(16000 * 0.3))
                rms = float(np.sqrt(np.mean(data ** 2)))
                bar = "|" * int(rms * 200)
                print(f"  RMS: {rms:.4f} {bar:<30}", end="\r")
                time.sleep(0.1)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
