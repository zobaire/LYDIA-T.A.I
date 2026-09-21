"""Economic calendar for LYDIA T.A.I — free Forex Factory feed, no key.

The nfs.faireconomy.media endpoint only exposes "this week" (no arbitrary
date range, no next-week). On weekends this week's calendar is mostly past,
so the frontend shows "markets closed" — which is accurate. We cache ~10 min
and survive 429 rate-limits by serving the last good cache.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from datetime import datetime

FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
_CACHE_SECONDS = 600.0

_lock = threading.Lock()
_cache: dict = {"events": [], "fetched": 0.0, "ok": False, "error": "", "stale": False}


def _parse_ts(date: str):
    """Parse an ISO date WITH tz offset (e.g. 2026-09-06T21:30:00-04:00) to epoch."""
    try:
        dt = datetime.fromisoformat(date.replace("Z", "+00:00"))
        return dt.timestamp()
    except Exception:
        return None


def _impact_rank(impact: str) -> int:
    return {"High": 3, "Medium": 2, "Low": 1}.get(impact or "", 0)


def cached_calendar() -> dict:
    """Return only what's already cached — never hits the network.

    Used by the prompt-injection path so building the system prompt can't
    block the voice loop on a 12s HTTP fetch."""
    with _lock:
        return dict(_cache)


def fetch_calendar(force: bool = False) -> dict:
    """Return cached (or freshly fetched) calendar events, upcoming only."""
    with _lock:
        if not force and _cache["ok"] and (time.time() - _cache["fetched"]) < _CACHE_SECONDS:
            return dict(_cache)

    fresh: dict = {"events": [], "fetched": time.time(), "ok": False, "error": "", "stale": True}
    try:
        req = urllib.request.Request(FEED_URL, headers={"User-Agent": "LydiaTAI/1.0"})
        with urllib.request.urlopen(req, timeout=12) as r:
            raw = json.loads(r.read().decode("utf-8", errors="replace"))
        now = time.time()
        events = []
        for e in raw:
            ts = _parse_ts(e.get("date", ""))
            if ts is None:
                continue
            events.append({
                "title": e.get("title", ""),
                "country": e.get("country", ""),
                "impact": e.get("impact", ""),
                "forecast": e.get("forecast", ""),
                "previous": e.get("previous", ""),
                "date": e.get("date", ""),
                "ts": ts,
            })
        upcoming = [e for e in events if e["ts"] >= now - 3600]
        upcoming.sort(key=lambda e: e["ts"])
        fresh.update({"events": upcoming, "ok": True, "error": "", "stale": False})
    except Exception as ex:  # 429 / network / etc
        fresh["error"] = str(ex)

    with _lock:
        if fresh["ok"]:
            _cache.update(fresh)
        else:
            # keep serving stale cache if we have one, but flag it
            _cache["stale"] = True
            _cache["fetched"] = time.time()
            if _cache.get("events"):
                _cache["error"] = fresh["error"]
        return dict(_cache)
