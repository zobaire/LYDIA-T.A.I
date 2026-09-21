"""Quick diagnostic: test configured providers and report which keys work."""
from __future__ import annotations

from openai import OpenAI

from brain.config import load_config, get_providers


def test_provider(key: str, provider: dict) -> dict:
    print(f"\nTesting provider: {key} ({provider.get('label', key)})")
    if provider.get("type") != "openai":
        return {"ok": False, "error": "not an OpenAI-compatible provider"}

    api_key = provider.get("api_key", "")
    if not api_key:
        return {"ok": False, "error": "no api_key configured"}

    try:
        client = OpenAI(base_url=provider["base_url"], api_key=api_key, timeout=20.0)
        resp = client.chat.completions.create(
            model=provider["model"],
            messages=[{"role": "user", "content": "Say 'OK' only."}],
            max_tokens=5,
        )
        return {"ok": True, "reply": resp.choices[0].message.content}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def main() -> None:
    cfg = load_config()
    active = cfg.get("brain", {}).get("active_provider", "unknown")
    providers = get_providers()

    print("=" * 50)
    print(" Lydia Provider Diagnostics")
    print("=" * 50)
    print(f"Active provider: {active}")

    results: dict[str, dict] = {}
    for key, prov in providers.items():
        results[key] = test_provider(key, prov)

    print("\n" + "=" * 50)
    print(" Summary")
    print("=" * 50)
    for key, res in results.items():
        status = "OK" if res["ok"] else "FAIL"
        detail = res.get("reply", res.get("error", "unknown"))
        marker = ">>>" if key == active else "   "
        print(f"{marker} {key:10s} [{status}] {detail}")

    working = [k for k, r in results.items() if r["ok"]]
    if active not in working and working:
        print(f"\nSuggestion: active provider '{active}' failed. Switch to one that works:")
        print(f"  brain.config.set_active_provider('{working[0]}')")


if __name__ == "__main__":
    main()
