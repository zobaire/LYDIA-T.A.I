"""Simple internal event bus for broadcasting state to the web UI."""
from __future__ import annotations
from typing import Callable

Listener = Callable[[dict], None]
_listeners: list[Listener] = []


def register(listener: Listener) -> None:
    """Register a callback that receives every broadcast event."""
    _listeners.append(listener)


def broadcast(event: dict) -> None:
    """Send an event to all registered listeners. Exceptions are swallowed."""
    for fn in _listeners:
        try:
            fn(event)
        except Exception:
            pass
