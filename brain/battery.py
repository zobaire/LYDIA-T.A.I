"""Provider balance / rate-limit polling for the UI battery meters."""
from __future__ import annotations
import threading
import time

import requests

from brain.config import get_providers
from brain.events import broadcast

_POLL_SECONDS = 30
_LATEST: dict[str, dict] = {}


def _poll_deepseek(provider: dict) -> dict | None:
    key = provider.get("api_key", "")
    if not key:
        return None
    try:
        r = requests.get(
            "https://api.deepseek.com/user/balance",
            headers={"Authorization": f"Bearer {key}"},
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            bi = (data.get("balance_infos") or [{}])[0]
            amount = float(bi.get("total_balance", 0))
            return {
                "pct": 100 if amount > 0 else 0,
                "currency": bi.get("currency", "USD"),
                "amount": amount,
                "available": True,
            }
    except Exception:
        pass
    return None


_POLLERS = {
    "deepseek": _poll_deepseek,
}


def _loop() -> None:
    while True:
        try:
            providers = get_providers()
            result: dict[str, dict] = {}
            for key, provider in providers.items():
                poller = _POLLERS.get(key)
                if not poller:
                    continue
                data = poller(provider)
                if data is not None:
                    result[key] = data
                    _LATEST[key] = data
            if result:
                broadcast({"type": "battery", **result})
        except Exception:
            pass
        time.sleep(_POLL_SECONDS)


def start() -> None:
    """Start the background battery poller thread."""
    threading.Thread(target=_loop, daemon=True).start()


def get_latest() -> dict[str, dict]:
    """Return the most recently cached battery data for the REST endpoint."""
    return dict(_LATEST)
