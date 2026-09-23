"""Server-side T.A.I settings — persisted so the UI and the AI prompt agree.

Why this exists: the UI used to keep a localStorage settings panel, but
anything that changes how the *backend* behaves has to live on disk too.
If a toggle only lived in the browser, then either the prompt would ignore
it, or a page refresh / second window would silently disagree with what the
model is allowed to reason about.

Single source of truth
----------------------
`_SCHEMA` below is *the* definition of every setting: its default, its type,
its bounds, its human label and its group. `_DEFAULTS` is derived from it, so
a default written here can never drift out of sync with the type-checker or
with the UI. The UI renders itself from `schema()` served over
`/api/settings` — no hand-maintained duplicate list in the browser.

Safety rule kept from the original: strategy reasoning (SMC) and the watch
scanner are OFF until explicitly enabled. Every read goes through `get()`,
so a missing or corrupt file can never turn them on.
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any

_PATH = Path(__file__).parent / "settings.json"
_lock = threading.Lock()

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

# Reminder lead options, in minutes before the event.
LEAD_OPTIONS = [90, 60, 30, 10, 5]
ROUTE_OPTIONS = ["off", "toast", "sound", "both"]

# ---------------------------------------------------------------------------
# SCHEMA — the single source of truth.
#
#   key      : flat, namespaced key as stored in settings.json
#   type     : toggle | number | enum | multi | time
#   default  : the value shipped to a fresh install
#   group    : which section of the settings drawer it renders under
#   label    : UI row title
#   desc     : UI row explanation
#   options  : allowed values (enum)
#   allowed  : allowed values (multi, non-empty list only)
#   min/max/step/unit : numeric bounds and display suffix
# ---------------------------------------------------------------------------
_SCHEMA: list[dict[str, Any]] = [
    # --- execution (decision 3: default MANUAL, never auto-click by accident) ---
    {
        "key": "execution_mode", "type": "enum", "default": "manual",
        "options": ["manual", "auto"], "group": "Execution",
        "label": "Order execution",
        "desc": ("manual = prefill the MT5 ticket and wait for your explicit "
                 "yes. auto = fire the order the moment a qualifying setup "
                 "appears. Manual is the default on purpose."),
    },
    {
        "key": "execution_max_lots", "type": "number", "default": 1.0,
        "min": 0.01, "max": 100.0, "step": 0.01, "unit": "lots",
        "group": "Execution",
        "label": "Hard lot cap",
        "desc": ("Absolute ceiling on any single order, applied even in auto "
                 "mode. Nothing can size above this."),
    },
    {
        "key": "execution_daily_loss_cap", "type": "number", "default": 0.0,
        "min": 0.0, "max": 1000000.0, "step": 10.0, "unit": "acct ccy (0 = off)",
        "group": "Execution",
        "label": "Daily loss cap",
        "desc": ("Stop opening new trades once realised daily loss passes this. "
                 "0 disables the cap."),
    },
    {
        "key": "trade_default_lots", "type": "number", "default": 0.01,
        "min": 0.01, "max": 100.0, "step": 0.01, "unit": "lots",
        "group": "Execution",
        "label": "Default order size",
        "desc": ("Lot size used when you approve a proposed trade. The hard cap "
                 "above still applies on top of it."),
    },

    # --- reminders (decision 4: default 1h30 + 10m) ---
    {
        "key": "reminder_leads", "type": "multi", "default": [90, 10],
        "allowed": LEAD_OPTIONS, "unit": "min", "group": "Reminders",
        "label": "Reminder windows",
        "desc": ("How long before an event you get pinged. Default is 1h 30m "
                 "and 10m. Tick extra windows for denser nagging."),
    },
    {
        "key": "reminder_catchup", "type": "toggle", "default": True,
        "group": "Reminders",
        "label": "Late catch-up",
        "desc": ("If the PC was asleep or off when a window passed, fire the "
                 "reminder late instead of silently dropping it."),
    },

    # --- quiet hours (decision 5) ---
    {
        "key": "quiet_hours_enabled", "type": "toggle", "default": False,
        "group": "Quiet hours",
        "label": "Enable quiet hours",
        "desc": "Suppress non-urgent notifications inside the window below.",
    },
    {
        "key": "quiet_hours_from", "type": "time", "default": "23:00",
        "group": "Quiet hours", "label": "Quiet from",
        "desc": "Start of the quiet window (local time).",
    },
    {
        "key": "quiet_hours_to", "type": "time", "default": "07:00",
        "group": "Quiet hours", "label": "Quiet until",
        "desc": "End of the quiet window. Spans midnight if earlier than the start.",
    },
    {
        "key": "quiet_hours_break_through_trades", "type": "toggle", "default": True,
        "group": "Quiet hours",
        "label": "Trade alerts break through",
        "desc": ("Trade fills, stop-outs and take-profits still notify during "
                 "quiet hours. A stop-out at 2 AM is not something to sleep "
                 "through."),
    },

    # --- notification routing (phase 2) ---
    {
        "key": "notify_route_task", "type": "enum", "default": "both",
        "options": ROUTE_OPTIONS, "group": "Notifications",
        "label": "Task-finished cue",
        "desc": "How Lydia tells you a reply or task is done.",
    },
    {
        "key": "notify_route_schedule", "type": "enum", "default": "both",
        "options": ROUTE_OPTIONS, "group": "Notifications",
        "label": "Reminder cue",
        "desc": "How scheduled reminders reach you.",
    },
    {
        "key": "notify_route_trade", "type": "enum", "default": "both",
        "options": ROUTE_OPTIONS, "group": "Notifications",
        "label": "Trade alert cue",
        "desc": "How trading alerts and fills reach you.",
    },
    {
        "key": "notify_done_toast", "type": "toggle", "default": True,
        "group": "Notifications",
        "label": "Done toast",
        "desc": "Windows Action Center toast when a reply finishes.",
    },
    {
        "key": "notify_done_sound", "type": "toggle", "default": True,
        "group": "Notifications",
        "label": "Done chime",
        "desc": "Play the stock Windows notification sound when a reply finishes.",
    },

    # --- risk ---
    {
        "key": "risk_default_risk_pct", "type": "number", "default": 1.0,
        "min": 0.1, "max": 10.0, "step": 0.1, "unit": "% of account",
        "group": "Risk",
        "label": "Default risk per trade",
        "desc": "Account percentage risked on a proposed trade when sizing lots.",
    },
    {
        "key": "smc_min_rr", "type": "number", "default": 2.0,
        "min": 0.5, "max": 10.0, "step": 0.1, "unit": "R:R",
        "group": "Risk",
        "label": "Minimum reward:risk",
        "desc": "Setups below this reward-to-risk are discarded outright.",
    },

    # --- strategy engine ---
    {
        "key": "smc_enabled", "type": "toggle", "default": False,
        "group": "Strategy",
        "label": "SMC strategy reasoning",
        "desc": ("Let Lydia read the market with Smart Money Concepts (order "
                 "blocks, fair value gaps, breaks of structure) and propose "
                 "entries with invalidation levels. Off by default."),
    },
    {
        "key": "smc_min_score", "type": "number", "default": 3,
        "min": 1, "max": 10, "step": 1, "unit": "score",
        "group": "Strategy",
        "label": "Minimum confluence score",
        "desc": "How much confluence a setup needs before it is reported.",
    },
    {
        "key": "smc_timeframe", "type": "enum", "default": "M15",
        "options": ["M1", "M5", "M15", "M30", "H1", "H4", "D1"],
        "group": "Strategy", "label": "Entry timeframe",
        "desc": "Timeframe used for entries and structure.",
    },
    {
        "key": "smc_htf", "type": "enum", "default": "H1",
        "options": ["M15", "M30", "H1", "H4", "D1", "W1"],
        "group": "Strategy", "label": "Bias timeframe",
        "desc": "Higher timeframe used for directional bias alignment.",
    },
    {
        "key": "smc_draw_zones", "type": "toggle", "default": True,
        "group": "Strategy", "label": "Draw zones on chart",
        "desc": "Paint order blocks and fair value gaps on the chart.",
    },

    # --- watch mode ---
    {
        "key": "watch_enabled", "type": "toggle", "default": False,
        "group": "Watch mode",
        "label": "Auto-scan setups",
        "desc": ("Continuously scan your watchlist for high-score SMC setups "
                 "and ping you the moment one appears. Off by default."),
    },
    {
        "key": "watch_min_score", "type": "number", "default": 5,
        "min": 1, "max": 10, "step": 1, "unit": "score",
        "group": "Watch mode", "label": "Alert minimum score",
        "desc": "Only ping for setups at or above this score.",
    },
    {
        "key": "watch_interval", "type": "number", "default": 60,
        "min": 10, "max": 900, "step": 5, "unit": "s",
        "group": "Watch mode", "label": "Scan interval",
        "desc": "Seconds between watchlist scans.",
    },
    {
        "key": "alerts_active_only", "type": "toggle", "default": True,
        "group": "Watch mode", "label": "Active market only",
        "desc": ("Only alert for the market you are currently viewing. Turn "
                 "off to hear about every watched market."),
    },

]

# Derived — never hand-maintained.
_DEFAULTS: dict[str, Any] = {d["key"]: d["default"] for d in _SCHEMA}
_BY_KEY: dict[str, dict[str, Any]] = {d["key"]: d for d in _SCHEMA}

_cache: dict[str, Any] | None = None


# --- validation ------------------------------------------------------------

def _norm_time(value: Any, default: str) -> str:
    """Accept 'HH:MM' (also 'H:MM'). Anything else falls back to the default."""
    s = str(value or "").strip()
    if re.match(r"^\d:\d{2}$", s):
        s = "0" + s
    return s if _TIME_RE.match(s) else default


def _norm_multi(value: Any, allowed: list[int], default: list[int]) -> list[int]:
    """Keep only allowed options, ints only, longest lead first."""
    if value is None:
        return list(default)
    if not isinstance(value, (list, tuple, set)):
        value = [value]
    out: list[int] = []
    for v in value:
        try:
            iv = int(v)
        except (TypeError, ValueError):
            continue
        if iv in allowed and iv not in out:
            out.append(iv)
    return sorted(out, reverse=True)


def _coerce(key: str, value: Any) -> Any:
    """Force a value into the type the schema declares.

    A string "false" must not read as True, a 1..10 score must not store 400,
    and an unknown enum value must not silently become a live-setting typo.
    """
    spec = _BY_KEY[key]
    default = spec["default"]
    t = spec["type"]

    if t == "toggle":
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)

    if t == "number":
        try:
            num = float(value)
        except (TypeError, ValueError):
            return default
        if isinstance(default, int) and not isinstance(default, bool):
            num = int(round(num))
        lo, hi = spec.get("min"), spec.get("max")
        if lo is not None:
            num = max(lo, num)
        if hi is not None:
            num = min(hi, num)
        return num

    if t == "enum":
        s = str(value).strip().lower()
        return s if s in spec.get("options", []) else default

    if t == "time":
        return _norm_time(value, default)

    if t == "multi":
        return _norm_multi(value, spec.get("allowed", []), default)

    return value


# --- persistence -----------------------------------------------------------

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


# --- public API ------------------------------------------------------------

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


def apply_patch(patch: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Validate + merge + persist a patch.

    Returns (new_full_state, rejected_keys). Unknown keys are rejected rather
    than stored, so a stale UI can never inject state the backend doesn't
    understand. Coerced values are what land on disk — a caller sending
    smc_min_score=400 gets 10 back, not an error.
    """
    global _cache
    rejected: list[str] = []
    with _lock:
        if _cache is None:
            _cache = _load()
        for k, v in (patch or {}).items():
            if k in _DEFAULTS:
                _cache[k] = _coerce(k, v)
            else:
                rejected.append(k)
        snapshot = dict(_cache)
        _write(snapshot)
    return snapshot, rejected


def update(patch: dict[str, Any]) -> dict[str, Any]:
    """Merge a patch of known keys and persist. Returns the new full state."""
    snapshot, _ = apply_patch(patch)
    return snapshot


def reset() -> dict[str, Any]:
    """Back to defaults — a way out if a value goes bad."""
    global _cache
    with _lock:
        _cache = dict(_DEFAULTS)
        _write(dict(_cache))
        return dict(_cache)


def defaults() -> dict[str, Any]:
    return dict(_DEFAULTS)


def schema() -> list[dict[str, Any]]:
    """The full field list for the UI renderer, with groups preserved."""
    return [dict(d) for d in _SCHEMA]


def payload() -> dict[str, Any]:
    """Everything the settings drawer needs in one round trip."""
    values = all()
    seen: list[str] = []
    for d in _SCHEMA:
        if d["group"] not in seen:
            seen.append(d["group"])
    return {
        "ok": True,
        "values": values,
        "defaults": defaults(),
        "schema": schema(),
        "groups": seen,
    }
