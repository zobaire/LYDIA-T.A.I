"""Schedule / Plans — stores upcoming events, provides live reminders.

Events are stored in memory_data/schedule.json as a list of:
    {
        "id": str,
        "title": str,
        "date": "YYYY-MM-DD" or "YYYY-MM-DD HH:MM",
        "reminders_sent": [lead_seconds already fired]
    }

Reminder windows now come from settings (`reminder_leads`, in minutes) rather
than a hard-coded list — default is 1h 30m and 10m. Ticking more windows in
the settings drawer is picked up on the next pass without a restart.

Delivery (phase 2)
------------------
A reminder used to be broadcast over the websocket and nothing else, which
meant that with no browser tab open it was silently lost — the actual bug
behind "reminders never reach me". Every fire now goes out three ways:
websocket (live UI), Windows toast, and a chime, all funnelled through
brain.notify so quiet hours and per-channel routing apply.

Dedup bug fixed (phase 2)
-------------------------
The old loop iterated `list_events()`, which returns *public* dicts stripped
of `reminders_sent`. So `evt.get("reminders_sent", [])` was always empty and
the "don't fire twice" guard was dead code — a reminder could fire on every
60s pass across its window. The loop now reads raw events off disk.
"""
from __future__ import annotations
import json
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from brain.events import broadcast

_MEMORY_DIR = Path(__file__).parent.parent / "memory_data"
_SCHEDULE_PATH = _MEMORY_DIR / "schedule.json"

# Fallback windows (seconds before the event) if settings are unreadable.
# The live values come from settings["reminder_leads"] (minutes).
REMINDER_LEADS = [90 * 60, 10 * 60]

# A window is a normal fire if its target arrived within this many seconds.
# Must comfortably exceed the 60s loop tick so jitter can't mislabel a
# routine reminder as "missed".
FRESH_S = 120

# How late a missed window may still be delivered. Beyond this the reminder
# is stale enough that firing it is just noise.
CATCHUP_MAX_AGE = 6 * 3600

_lock = threading.Lock()
_started = False


# --- settings-driven windows ----------------------------------------------

def _leads() -> list[int]:
    """Active reminder windows in seconds, longest first.

    Empty list = reminders off (you unticked every window), which is
    respected rather than second-guessed.
    """
    try:
        from trading.settings import get
        mins = get("reminder_leads", [90, 10]) or []
        out = sorted({int(m) * 60 for m in mins if int(m) > 0}, reverse=True)
        return out
    except Exception:
        return list(REMINDER_LEADS)


def _catchup_on() -> bool:
    try:
        from trading.settings import get
        return bool(get("reminder_catchup", True))
    except Exception:
        return True


def _stale_at_add(date_str: str) -> list[int]:
    """Windows that were already in the past when the event was created.

    If you add something 10 minutes out, the 1h 30m window never had a chance
    to fire - delivering it as "missed earlier" a minute later is pure noise,
    and it was part of why reminders felt broken. Retire those up front.

    A real sleep/wake catch-up is untouched: that runs on events which already
    existed when their window arrived, so their windows are still un-sent.
    """
    dt = _parse_dt(date_str)
    if not dt:
        return []
    epoch = dt.timestamp()
    now = time.time()
    return [lead for lead in _leads() if now - (epoch - lead) > FRESH_S]


def _dur_label(seconds: float) -> str:
    """Human '1h 30m' / '10m' label for a duration in seconds.

    Computed from *time remaining right now*, never from the nominal lead —
    otherwise a window delivered late would claim the wrong countdown.
    """
    mins = int(round(max(0, seconds) / 60))
    if mins <= 0:
        return "less than a minute"
    hours, rem = divmod(mins, 60)
    if hours and rem:
        return f"{hours}h {rem}m"
    return f"{hours}h" if hours else f"{mins}m"


# --- storage ---------------------------------------------------------------

def _load_events() -> list[dict]:
    if _SCHEDULE_PATH.exists():
        try:
            data = json.loads(_SCHEDULE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except Exception:
            pass
    return []


def _save_events(events: list[dict]) -> None:
    _MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    _SCHEDULE_PATH.write_text(
        json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")


def _public(events: list[dict]) -> list[dict]:
    out = []
    for e in events:
        out.append({
            "id": e.get("id"),
            "title": e.get("title", ""),
            "date": e.get("date", ""),
        })
    return sorted(out, key=lambda e: e.get("date", ""))


# --- CRUD ---

def list_events() -> list[dict]:
    with _lock:
        return _public(_load_events())


def add_event(title: str, date: str) -> dict:
    """Add an event. date may be 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM'."""
    evt = {
        "id": uuid.uuid4().hex[:8],
        "title": title.strip(),
        "date": date.strip(),
        "reminders_sent": _stale_at_add(date),
    }
    with _lock:
        events = _load_events()
        events.append(evt)
        _save_events(events)
    _broadcast_schedule()
    return _public([evt])[0]


def update_event(event_id: str, title: str | None = None, date: str | None = None) -> dict | None:
    with _lock:
        events = _load_events()
        for e in events:
            if e.get("id") == event_id:
                if title is not None:
                    e["title"] = title.strip()
                if date is not None:
                    e["date"] = date.strip()
                # Reset reminders if the date changed - but retire any window
                # that is already behind us, so dragging an event closer does
                # not immediately fire a "missed earlier" for one that never
                # had a chance to run.
                e["reminders_sent"] = _stale_at_add(e["date"])
                _save_events(events)
                _broadcast_schedule()
                return _public([e])[0]
    return None


def delete_event(event_id: str) -> bool:
    with _lock:
        events = _load_events()
        remaining = [e for e in events if e.get("id") != event_id]
        if len(remaining) == len(events):
            return False
        _save_events(remaining)
    _broadcast_schedule()
    return True


def _broadcast_schedule() -> None:
    broadcast({"type": "schedule", "events": list_events()})


# --- Reminder loop ---

def _parse_dt(date_str: str) -> datetime | None:
    text = (date_str or "").strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M",
                "%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _mark_sent(event_id: str, leads: list[int]) -> None:
    """Persist fired windows for an event (raw events, not public view)."""
    with _lock:
        events = _load_events()
        for e in events:
            if e.get("id") == event_id:
                sent = list(e.get("reminders_sent") or [])
                for lead in leads:
                    if lead not in sent:
                        sent.append(lead)
                e["reminders_sent"] = sent
                break
        _save_events(events)


def _fire(evt: dict, remaining: float, late: bool) -> None:
    """Deliver one reminder: websocket + toast + chime.

    `remaining` is seconds until the event *right now*, so a late fire
    reports the true countdown rather than the window that triggered it.
    """
    title = evt.get("title") or "Your event"
    when = evt.get("date", "")
    label = _dur_label(remaining)
    if late:
        text = (f"Reminder (missed earlier): {title} starts in {label} "
                f"\u2014 {when}.")
    else:
        text = f"Reminder: {title} starts in {label} ({when})."
    broadcast({"type": "reminder", "text": text, "event": title})
    try:
        from brain.notify import notify_reminder
        notify_reminder(f"Lydia \u2014 {title}", f"Starts in {label} \u2014 {when}")
    except Exception:
        pass


def check_once(now: float | None = None) -> list[dict]:
    """One pass over the schedule. Returns the reminders that fired.

    Split out of the loop so it can be unit-tested without waiting a minute.
    """
    now = time.time() if now is None else now
    leads = _leads()
    if not leads:
        return []

    fired: list[dict] = []
    for evt in _load_events():
        dt = _parse_dt(evt.get("date", ""))
        if not dt:
            continue
        epoch = dt.timestamp()
        if now >= epoch:
            continue          # event already started/passed - nothing to warn about
        sent = set(evt.get("reminders_sent") or [])

        # Windows whose target time has arrived and hasn't fired yet.
        due = [l for l in leads if l not in sent and epoch - l <= now]
        if not due:
            continue

        # due is longest-first, so the last entry is the nearest/most useful.
        nearest = due[-1]
        remaining = epoch - now
        # FRESH_S = the target arrived recently enough to be a normal fire,
        # with headroom over the 60s loop tick so jitter never mislabels it.
        late_by = now - (epoch - nearest)
        if late_by <= FRESH_S:
            _fire(evt, remaining, late=False)
            _mark_sent(evt["id"], [nearest])
            fired.append({"id": evt["id"], "lead": nearest, "late": False})
        elif _catchup_on() and late_by <= CATCHUP_MAX_AGE:
            # Missed while the machine slept. Deliver the nearest window once
            # and swallow the older ones — a 1h30 warning is useless by the
            # time the 10m mark has passed.
            _fire(evt, remaining, late=True)
            _mark_sent(evt["id"], due)
            fired.append({"id": evt["id"], "lead": nearest, "late": True})
        else:
            # Too stale to be useful, or catch-up disabled: retire them.
            _mark_sent(evt["id"], due)
    return fired


def _reminder_loop() -> None:
    while True:
        try:
            check_once()
        except Exception:
            pass
        time.sleep(60)


def start() -> None:
    """Start the reminder background loop (idempotent)."""
    global _started
    if _started:
        return
    _started = True
    t = threading.Thread(target=_reminder_loop, daemon=True, name="taia-reminders")
    t.start()
