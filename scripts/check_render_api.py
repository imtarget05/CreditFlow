"""Check Render API — list services, verify creditflow-api exists.

Reads the API key from RENDER_API_KEY (environment) or a local `.env` file.
The key is NEVER hardcoded here — see docs/deployment.md security notes.

Usage:
    RENDER_API_KEY=rnd_xxx python scripts/check_render_api.py
    # or put RENDER_API_KEY=rnd_xxx into .env (gitignored) and just run:
    python scripts/check_render_api.py
"""
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]


def load_dotenv(path: Path) -> dict:
    """Minimal .env parser: KEY=VALUE lines, ignores blanks/comments/quotes."""
    values = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip().strip("'\"")
    return values


def get_api_key() -> str:
    import os

    key = os.environ.get("RENDER_API_KEY", "").strip()
    if key:
        return key
    key = load_dotenv(ROOT / ".env").get("RENDER_API_KEY", "").strip()
    if key:
        return key
    raise SystemExit(
        "RENDER_API_KEY not set.\n"
        "Put RENDER_API_KEY=rnd_... into .env (gitignored) or export it, then rerun.\n"
        "Create/rotate the key at https://dashboard.render.com/u/settings#api-keys"
    )


def masked(key):
    return key[:8] + "..." + key[-4:]


def main():
    api_key = get_api_key()
    headers = {"Authorization": f"Bearer {api_key}"}
    print(f"Checking Render API with key {masked(api_key)}\n")

    try:
        resp = requests.get("https://api.render.com/v1/services", headers=headers, timeout=30)
        print(f"Services endpoint status: {resp.status_code}")

        if resp.status_code != 200:
            print(f"Error message: {resp.text[:500]}")
            return

        services = resp.json()
        if not isinstance(services, list):
            print("Unexpected response format.")
            return

        print(f"Total services found: {len(services)}\n")

        found = False
        for item in services:
            svc = item.get("service", {})
            name = svc.get("name", "")
            if name == "creditflow-api":
                found = True
                details = svc.get("serviceDetails", {})
                print("=== creditflow-api FOUND ===")
                print(f"Service ID  : {svc.get('id')}")
                print(f"Name        : {name}")
                print(f"URL         : {details.get('url', 'N/A')}")
                print(f"Status      : {svc.get('suspended')}")
                print(f"Type        : {svc.get('type')}")
                print(f"Dashboard   : {svc.get('dashboardUrl', 'N/A')}")
                print(f"Created at  : {svc.get('createdAt', 'N/A')}")
                print(f"Updated at  : {svc.get('updatedAt', 'N/A')}")
                break

        if not found:
            print("creditflow-api NOT FOUND — it does not exist yet on this Render account.")
            print("\nExisting services:")
            for item in services:
                svc = item.get("service", {})
                details = svc.get("serviceDetails", {})
                print(f" - {svc.get('name')} ({svc.get('id')}) | {details.get('url', 'N/A')} | {svc.get('suspended')}")

    except requests.exceptions.RequestException as e:
        print(f"Services endpoint status: CONNECTION ERROR")
        print(f"Error message: {e}")


if __name__ == "__main__":
    main()

