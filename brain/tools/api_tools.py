"""API integrations: Spotify, Discord."""
from __future__ import annotations
import json
from pathlib import Path

import requests

from brain.config import load_dotenv

_REPO_ROOT = Path(__file__).parent.parent.parent
_MEMORY_DIR = _REPO_ROOT / "memory_data"


# --- Spotify ---

def _spotify_token() -> tuple[str, str | None]:
    """Return (access_token, error). Reads credentials + refreshes token."""
    env = load_dotenv()
    client_id = env.get("SPOTIFY_CLIENT_ID", "")
    client_secret = env.get("SPOTIFY_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        return "", "Spotify not configured — missing SPOTIFY_CLIENT_ID or SPOTIFY_CLIENT_SECRET."

    token_path = _MEMORY_DIR / "spotify_token.json"
    refresh_token = ""
    if token_path.exists():
        try:
            refresh_token = json.loads(token_path.read_text()).get("refresh_token", "")
        except Exception:
            pass

    if not refresh_token:
        return "", "Spotify not authenticated. Need OAuth refresh token."

    try:
        r = requests.post("https://accounts.spotify.com/api/token", data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }, auth=(client_id, client_secret), timeout=15)
        if r.status_code != 200:
            return "", f"Spotify auth failed: {r.status_code}"
        return r.json()["access_token"], None
    except Exception as e:
        return "", f"Spotify auth error: {e}"


def _spotify_device_id(headers: dict) -> str | None:
    """Return the id of the first available device, or None if none found."""
    try:
        r = requests.get("https://api.spotify.com/v1/me/player/devices",
                         headers=headers, timeout=10)
        if r.status_code != 200:
            return None
        devices = r.json().get("devices", [])
        # Prefer an active device; fall back to any available one.
        active = next((d for d in devices if d.get("is_active")), None)
        return (active or devices[0]).get("id") if devices else None
    except Exception:
        return None


def spotify_control(action: str) -> str:
    """Control Spotify playback: play, pause, next, previous."""
    access_token, err = _spotify_token()
    if err:
        return err

    headers = {"Authorization": f"Bearer {access_token}"}
    action = action.lower().strip()

    endpoints = {
        "play": ("PUT", "https://api.spotify.com/v1/me/player/play"),
        "resume": ("PUT", "https://api.spotify.com/v1/me/player/play"),
        "pause": ("PUT", "https://api.spotify.com/v1/me/player/pause"),
        "next": ("POST", "https://api.spotify.com/v1/me/player/next"),
        "previous": ("POST", "https://api.spotify.com/v1/me/player/previous"),
    }

    if action not in endpoints:
        return f"Unknown Spotify action: {action}. Use play, pause, next, previous."

    method, url = endpoints[action]
    params = {}
    # Target a specific device so playback doesn't fail with NO_ACTIVE_DEVICE.
    if action in ("play", "resume"):
        device_id = _spotify_device_id(headers)
        if device_id:
            params["device_id"] = device_id
    try:
        if method == "PUT":
            r = requests.put(url, headers=headers, params=params, timeout=10)
        else:
            r = requests.post(url, headers=headers, params=params, timeout=10)
        if r.status_code in (200, 202, 204):
            return f"Spotify: {action} OK"
        return f"Spotify {action}: {r.status_code} {r.text[:120]}"
    except Exception as e:
        return f"Spotify error: {e}"


def spotify_search(query: str, limit: int = 5) -> str:
    """Search Spotify for a song/artist. Returns a readable list."""
    access_token, err = _spotify_token()
    if err:
        return err

    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        r = requests.get(
            "https://api.spotify.com/v1/search",
            params={"q": query, "type": "track", "limit": limit},
            headers=headers, timeout=15)
        if r.status_code != 200:
            return f"Spotify search error: {r.status_code}"
        items = r.json().get("tracks", {}).get("items", [])
        if not items:
            return f"No Spotify tracks found for: {query}"
        lines = []
        for i, t in enumerate(items, 1):
            artists = ", ".join(_clean_console_text(a["name"]) for a in t["artists"])
            lines.append(f"{i}. {_clean_console_text(t['name'])} — {artists} ({t['duration_ms'] // 60000}:{((t['duration_ms'] // 1000) % 60):02d})")
        return "Found:\n" + "\n".join(lines)
    except Exception as e:
        return f"Spotify search error: {e}"


def spotify_play_song(query: str) -> str:
    """Search for a song and play the top matching track."""
    access_token, err = _spotify_token()
    if err:
        return err

    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        r = requests.get(
            "https://api.spotify.com/v1/search",
            params={"q": query, "type": "track", "limit": 1},
            headers=headers, timeout=15)
        if r.status_code != 200:
            return f"Spotify search error: {r.status_code}"
        items = r.json().get("tracks", {}).get("items", [])
        if not items:
            return f"No Spotify track found for: {query}"

        track = items[0]
        uri = track["uri"]
        name = track["name"]
        artists = ", ".join(a["name"] for a in track["artists"])

        # Target a specific device so playback doesn't fail with NO_ACTIVE_DEVICE.
        params = {}
        device_id = _spotify_device_id(headers)
        if device_id:
            params["device_id"] = device_id

        p = requests.put(
            "https://api.spotify.com/v1/me/player/play",
            params=params,
            json={"uris": [uri]},
            headers=headers, timeout=10)
        if p.status_code in (200, 202, 204):
            return f"Now playing: {name} — {artists}"
        return f"Spotify play error: {p.status_code} ({name} not started) {p.text[:120]}"
    except Exception as e:
        return f"Spotify play error: {e}"


# --- Discord ---

def discord_check() -> str:
    """Check Discord server status."""
    env = load_dotenv()
    token = env.get("DISCORD_BOT_TOKEN", "")
    guild_id = env.get("DISCORD_GUILD_ID", "")
    if not token or not guild_id:
        return "Discord not configured."

    headers = {"Authorization": f"Bot {token}"}
    try:
        r = requests.get(
            f"https://discord.com/api/v10/guilds/{guild_id}?with_counts=true",
            headers=headers, timeout=15)
        if r.status_code != 200:
            return f"Discord API error: {r.status_code}"
        data = r.json()
        return _clean_console_text(
            f"Discord: {data.get('name', 'Server')}\n"
            f"- Members: {data.get('approximate_member_count', '?')}\n"
            f"- Online: {data.get('approximate_presence_count', '?')}"
        )
    except Exception as e:
        return f"Discord error: {e}"


# --- Shared console helper ---

def _clean_console_text(s: str) -> str:
    """Strip chars that crash Windows cp1252 console printing (emoji etc).

    API data (Spotify track titles, Discord names...) can contain emoji.
    Python on Windows prints to cp1252 by default, so any non-BMP char raises
    UnicodeEncodeError and takes the whole backend down. Keep BMP + common
    currency symbols, drop the rest (the LLM doesn't need emoji from API data
    anyway).
    """
    out = []
    for ch in s:
        code = ord(ch)
        if code < 0x10000 and ch != "\ufe0f":
            out.append(ch)
    return "".join(out)



