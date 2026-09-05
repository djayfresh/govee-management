#!/usr/bin/env python3
"""Query the Govee OpenAPI using the key in secrets.yaml.

Reads `api_govee_key` from ../secrets.yaml (gitignored) and prints the account's
device inventory plus each device's live state. The key is never printed, never
included in output files, and never echoed in error messages.

Purpose: confirm whether a gateway-bridged sensor (e.g. the H5310 pool
thermometer behind an H5044) is visible to the documented OpenAPI, and learn the
exact SKU string it reports. That determines whether an OpenAPI-based HA
integration can ever see it.

Usage:
    python scripts/govee_api.py              # inventory + state for every device
    python scripts/govee_api.py --devices    # inventory only (1 API call)
    python scripts/govee_api.py --json out.json   # also save raw JSON
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

BASE = "https://openapi.api.govee.com/router/api/v1"
DEVICES_URL = f"{BASE}/user/devices"
STATE_URL = f"{BASE}/device/state"

SECRETS = Path(__file__).resolve().parent.parent / "secrets.yaml"
KEY_NAME = "api_govee_key"


def load_key() -> str:
    """Pull the API key out of secrets.yaml without printing it."""
    if not SECRETS.exists():
        sys.exit(f"secrets.yaml not found at {SECRETS}\n"
                 f"Copy secrets.yaml.example to secrets.yaml and set {KEY_NAME}.")

    text = SECRETS.read_text(encoding="utf-8-sig")

    key = None
    try:
        import yaml  # optional; regex fallback below
        data = yaml.safe_load(text) or {}
        if isinstance(data, dict):
            key = data.get(KEY_NAME)
    except ImportError:
        pass
    except Exception:
        # A malformed or ESPHome-tagged secrets.yaml shouldn't block us.
        pass

    if not key:
        m = re.search(rf"^\s*{KEY_NAME}\s*:\s*(.+?)\s*$", text, re.MULTILINE)
        if m:
            key = m.group(1).strip().strip('"').strip("'")

    if not key:
        sys.exit(f"No '{KEY_NAME}' found in {SECRETS.name}. Add it (see secrets.yaml.example).")

    key = str(key).strip()
    if key.upper().startswith("REPLACE_ME") or key == "thisisaguid":
        sys.exit(f"'{KEY_NAME}' is still the placeholder value. Put your real key in {SECRETS.name}.")

    return key


def call(url: str, key: str, payload: dict | None = None) -> dict:
    """One API call. On failure, report status and body - never the key."""
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url,
        data=body,
        method="POST" if payload is not None else "GET",
        headers={
            "Govee-API-Key": key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:500]
        sys.exit(f"HTTP {e.code} from {url.rsplit('/', 1)[-1]}: {detail}\n"
                 f"(401/403 means the key is rejected; 429 means rate limited.)")
    except urllib.error.URLError as e:
        sys.exit(f"Network error reaching Govee: {e.reason}")


def caps(dev: dict) -> str:
    return ",".join(sorted({c.get("instance", "?") for c in dev.get("capabilities", [])}))


def state_values(state: dict) -> str:
    """Flatten a device/state response into 'instance=value' pairs."""
    out = []
    for cap in (state.get("payload") or {}).get("capabilities", []):
        inst = cap.get("instance", "?")
        val = (cap.get("state") or {}).get("value")
        if isinstance(val, dict):
            val = json.dumps(val, separators=(",", ":"))
        out.append(f"{inst}={val}")
    return "  ".join(out) if out else "(no state reported)"


def main() -> None:
    ap = argparse.ArgumentParser(description="Query the Govee OpenAPI.")
    ap.add_argument("--devices", action="store_true", help="inventory only, skip per-device state")
    ap.add_argument("--json", metavar="FILE", help="save raw JSON (key is never included)")
    args = ap.parse_args()

    key = load_key()
    print(f"Loaded {KEY_NAME} from {SECRETS.name} (value not shown).\n")

    resp = call(DEVICES_URL, key)
    print(f"API code={resp.get('code')} message={resp.get('message')!r}")

    devices = resp.get("data") or []
    print(f"devices returned: {len(devices)}\n")
    if not devices:
        print("No devices. The key authenticates but the account exposes nothing"
              " through the documented API.")
        return

    print(f"{'SKU':<10} {'NAME':<34} {'DEVICE ID':<26} CAPABILITIES")
    print("-" * 110)
    for d in devices:
        print(f"{d.get('sku', '?'):<10} {str(d.get('deviceName', ''))[:33]:<34} "
              f"{str(d.get('device', ''))[:25]:<26} {caps(d)}")

    raw = {"devices": resp, "states": {}}

    if not args.devices:
        print("\n--- live state ---")
        for d in devices:
            sku, dev_id = d.get("sku"), d.get("device")
            if not sku or not dev_id:
                continue
            st = call(STATE_URL, key, {
                "requestId": str(uuid.uuid4()),
                "payload": {"sku": sku, "device": dev_id},
            })
            raw["states"][f"{sku}:{dev_id}"] = st
            print(f"{sku:<10} {str(d.get('deviceName', ''))[:30]:<32} {state_values(st)}")
            time.sleep(1)  # stay under Govee's rate limit

    if args.json:
        Path(args.json).write_text(json.dumps(raw, indent=2), encoding="utf-8")
        print(f"\nRaw JSON written to {args.json} (contains no credentials).")


if __name__ == "__main__":
    main()
