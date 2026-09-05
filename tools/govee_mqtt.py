#!/usr/bin/env python3
"""Subscribe to Govee's OpenAPI MQTT event stream.

Govee pushes events for any device whose capabilities include
`devices.capabilities.event` - which is what the H5059 water leak detectors
declare (`bodyAppearedEvent`: LEAKED / UN_LEAKED with per-probe state).

    host  mqtts://mqtt.openapi.govee.com:8883
    auth  API key as both username and password
    topic GA/<api-key>

The key doubles as the topic name, so it is redacted everywhere it would
otherwise be printed.

Usage:
    python scripts/govee_mqtt.py              # listen until Ctrl-C
    python scripts/govee_mqtt.py --seconds 30 # connect, listen briefly, exit
"""

from __future__ import annotations

import argparse
import json
import ssl
import sys
import time
from datetime import datetime

from govee_api import load_key  # same secrets.yaml loader; never prints the key

try:
    import paho.mqtt.client as mqtt
except ImportError:
    sys.exit("paho-mqtt not installed. Use the repo venv:\n"
             r"  .venv\Scripts\python.exe scripts\govee_mqtt.py")

HOST = "mqtt.openapi.govee.com"
PORT = 8883


def stamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print(f"[{stamp()}] connected, subscribing to GA/<api-key>", flush=True)
        client.subscribe(userdata["topic"], qos=0)
    else:
        # 4 = bad username/password, 5 = not authorised
        print(f"[{stamp()}] CONNECT FAILED rc={rc} "
              f"({mqtt.connack_string(rc) if hasattr(mqtt, 'connack_string') else 'see paho rc codes'})")
        userdata["failed"] = True


def on_subscribe(client, userdata, mid, granted_qos):
    print(f"[{stamp()}] subscribed (qos={granted_qos}). Waiting for events...", flush=True)
    print("  Trigger one by bridging a leak probe with a wet finger or damp cloth.")
    userdata["subscribed"] = True


def on_message(client, userdata, msg):
    userdata["count"] += 1
    print(f"\n[{stamp()}] --- event on {msg.topic.split('/')[0]}/<api-key> ---")
    try:
        payload = json.loads(msg.payload.decode())
        print(json.dumps(payload, indent=2)[:3000], flush=True)
    except (ValueError, UnicodeDecodeError):
        print(msg.payload[:1000], flush=True)
        return
    try:
        summarize(payload)
    except Exception as e:
        # The raw JSON above is the point; a summary bug must not kill the
        # subscriber thread and lose subsequent events.
        print(f"  (summary skipped: {type(e).__name__}: {e})", flush=True)


def summarize(payload: dict) -> None:
    """Pull the human-relevant bits out of a Govee event message."""
    name = payload.get("deviceName") or payload.get("device") or "?"
    sku = payload.get("sku", "?")
    for cap in payload.get("capabilities", []) or []:
        inst = cap.get("instance")
        state = cap.get("state")
        entries = state if isinstance(state, list) else [state]
        for st in entries:
            if not isinstance(st, dict):
                print(f"  >>> {sku} {name}: {inst}={st}", flush=True)
                continue
            val = st.get("value")
            if inst == "bodyAppearedEvent":
                probes = st.get("probesState") or st.get("probes") or {}
                word = {1: "LEAKED", 2: "CLEARED"}.get(val, f"value={val}")
                print(f"  >>> {sku} {name}: {word}  probes={probes}", flush=True)
            elif inst:
                print(f"  >>> {sku} {name}: {inst}={val}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="Listen to Govee's MQTT event stream.")
    ap.add_argument("--seconds", type=int, default=0,
                    help="exit after N seconds (0 = run until Ctrl-C)")
    args = ap.parse_args()

    key = load_key()
    userdata = {"topic": f"GA/{key}", "count": 0, "subscribed": False, "failed": False}

    client = mqtt.Client(client_id=f"govee-listener-{int(time.time())}", userdata=userdata)
    client.username_pw_set(key, key)
    client.tls_set(cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLS_CLIENT)
    client.on_connect = on_connect
    client.on_subscribe = on_subscribe
    client.on_message = on_message

    print(f"connecting to {HOST}:{PORT} (TLS)...")
    try:
        client.connect(HOST, PORT, keepalive=60)
    except Exception as e:
        sys.exit(f"connect error: {e}")

    deadline = time.time() + args.seconds if args.seconds else None
    client.loop_start()
    try:
        while True:
            time.sleep(0.5)
            if userdata["failed"]:
                break
            if deadline and time.time() > deadline:
                print(f"\n[{stamp()}] {args.seconds}s elapsed. "
                      f"events received: {userdata['count']}")
                break
    except KeyboardInterrupt:
        print(f"\nstopped. events received: {userdata['count']}")
    finally:
        client.loop_stop()
        client.disconnect()

    if userdata["failed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
