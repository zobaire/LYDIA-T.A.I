"""Server-side T.A.I settings — persisted so the UI and the AI prompt agree.

Why this exists: the UI already has a localStorage settings panel, but
anything that changes how the *backend* behaves has to live on disk too.
If the SMC toggle only lived in the browser, then either the prompt would
ignore it, or a page refresh / second window would silently disagree with
what the model is allowed to reason about.

Rule enforced here: strategy reasoning (SMC) is OFF until explicitly
enabled. `_DEFAULTS` is the single source of truth and every read goes
through `get()`, so a missing or corrupt file can never turn it on.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

_PATH = Path(__file__).parent / "settings.json"
_lock = threading.Lock()

# Keys the backend actually reads. Anything not in here is ignored on load,
# so a stale UI key can never inject state we don't expect.
_DEFAULTS: dict[str, Any] = {
    # --- strategy engine (Phase 5) ---
    "smc_enabled": False,        # master switch. OFF by default, on purpose.
    "smc_min_score": 3,          # minimum confluence score before a setup is reported
    "smc_timeframe": "M15",      # entry timeframe
    "smc_htf": "H1",             # higher timeframe used for bias alignment
    "smc_draw_zones": True,      # paint zones on the chart (UI only)
    "smc_min_rr": 2.0,           # below this R:R a setup is discarded outright
    # --- alerts ---
    "alerts_active_only": True,  # only alert on the market being viewed
    # --- watch mode (Phase 5 step 4) ---
    "watch_enabled": False,      # auto-scan the watchlist for SMC setups. OFF by default.
    "watch_min_score": 5,        # only ping for setups at/above this confluence score
    "watch_interval": 60,        # seconds between scan passes
}

_cache: dict[str, Any] | None = None


def _coerce(key: str, value: Any) -> Any:
    """Keep types honest — a string "false" must not read as True."""
    default = _DEFAULTS[key]
    if isinstance(default, bool):
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    if isinstance(default, int):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    if isinstance(default, float):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
    if isinstance(default, str):
        return str(value)
    return value


def _load() -> dict[str, Any]:
    """Read settings.json, falling back to defaults on any failure."""
    data: dict[str, Any] = dict(_DEFAULTS)
    try:
        raw = json.loads(_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            for k, v in raw.items():
                if k in _DEFAULTS:
                    data[k] = _coerce(k, v)
    except FileNotFoundError:
        pass
    except Exception:
        # Corrupt file: defaults win rather than an exception at import time.
        pass
    return data


def _write(data: dict[str, Any]) -> None:
    try:
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(_PATH)   # atomic-ish: never leave a half-written file
    except Exception:
        pass


def all() -> dict[str, Any]:
    """Current settings (cached after first read)."""
    global _cache
    with _lock:
        if _cache is None:
            _cache = _load()
        return dict(_cache)


def get(key: str, default: Any = None) -> Any:
    """Single setting. Unknown keys return `default` — never raise."""
    if key not in _DEFAULTS:
        return default
    return all().get(key, _DEFAULTS[key])


def enabled(key: str) -> bool:
    return bool(get(key, False))


def update(patch: dict[str, Any]) -> dict[str, Any]:
    """Merge a patch of known keys and persist. Returns the new full state."""
    global _cache
    with _lock:
        if _cache is None:
            _cache = _load()
        for k, v in (patch or {}).items():
            if k in _DEFAULTS:
                _cache[k] = _coerce(k, v)
        snapshot = dict(_cache)
        _write(snapshot)
    return snapshot


def reset() -> dict[str, Any]:
    """Back to defaults — used by tests, and a way out if a value goes bad."""
    global _cache
    with _lock:
        _cache = dict(_DEFAULTS)
        _write(dict(_cache))
        return dict(_cache)


def defaults() -> dict[str, Any]:
    return dict(_DEFAULTS)
