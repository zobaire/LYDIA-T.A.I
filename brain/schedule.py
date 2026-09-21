"""Schedule / Plans — stores upcoming events, provides live reminders.

Events are stored in memory_data/schedule.json as a list of:
    {
        "id": str,
        "title": str,
        "date": "YYYY-MM-DD" or "YYYY-MM-DD HH:MM",
        "reminders_sent": [windows already fired]
    }

Reminder windows fire at 1h30m, 1h, 30m, and 10m before the event.
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

# Reminder windows (in seconds before the event).
REMINDER_LEADS = [1 * 3600 + 30 * 60, 1 * 3600, 30 * 60, 10 * 60]

_lock = threading.Lock()
_started = False


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
        "reminders_sent": [],
    }
    with _lock:
        events = _load_events()
        events.append(evt)
        _save_events(events)
    _notify()
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
                # Reset reminders if the date changed.
                e["reminders_sent"] = []
                _save_events(events)
                _notify()
                return _public([e])[0]
    return None


def delete_event(event_id: str) -> bool:
    with _lock:
        events = _load_events()
        remaining = [e for e in events if e.get("id") != event_id]
        if len(remaining) == len(events):
            return False
        _save_events(remaining)
    _notify()
    return True


def _notify() -> None:
    broadcast({"type": "schedule", "events": list_events()})


# --- Reminder loop ---

def _parse_dt(date_str: str) -> datetime | None:
    text = date_str.strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M",
                "%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _reminder_loop() -> None:
    while True:
        try:
            now = time.time()
            events = list_events()
            for evt in events:
                try:
                    dt = _parse_dt(evt.get("date", ""))
                except Exception:
                    continue
                if not dt:
                    continue
                epoch = dt.timestamp()
                sent = evt.get("reminders_sent", [])
                for lead in REMINDER_LEADS:
                    target = epoch - lead
                    # Fire if the target time is in the near past (last check window).
                    if target <= now < target + 60 and lead not in sent:
                        mins = int(lead // 60)
                        hours, rem = divmod(mins, 60)
                        if hours:
                            lead_txt = f"{hours}h {rem}m"
                        else:
                            lead_txt = f"{mins}m"
                        dt_txt = evt.get("date", "")
                        title = evt.get("title", "Your event")
                        msg = f"⏰ Reminder: {title} is in {lead_txt} ({dt_txt})."
                        broadcast({"type": "reminder", "text": msg, "event": title})
                        # Mark as sent so we never double-fire.
                        with _lock:
                            events2 = _load_events()
                            for e2 in events2:
                                if e2.get("id") == evt["id"]:
                                    e2.setdefault("reminders_sent", [])
                                    if lead not in e2["reminders_sent"]:
                                        e2["reminders_sent"].append(lead)
                                    break
                            _save_events(events2)
        except Exception:
            pass
        time.sleep(60)


def start() -> None:
    """Start the reminder background loop (idempotent)."""
    global _started
    if _started:
        return
    _started = True
    t = threading.Thread(target=_reminder_loop, daemon=True)
    t.start()
