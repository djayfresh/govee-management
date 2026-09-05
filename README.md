# Govee Management

A Home Assistant custom integration for Govee devices that uses **only Govee's
documented OpenAPI** — no undocumented endpoints, no cloud scraping, no MQTT
broker of your own.

Built because the popular alternatives don't cover gateway-bridged Govee
hardware: water leak detectors and pool thermometers that talk to an **H5044
gateway** over a proprietary radio rather than Wi-Fi or Bluetooth.

## Why this exists

| Approach | Why it doesn't cover these devices |
| --- | --- |
| Bluetooth proxy (ESPHome) | H5059 and H5310 aren't BLE devices at all. Verified: not present in a 53-device BLE scan. |
| `govee_ble` | Same reason — nothing to parse if nothing is broadcast. |
| govee2mqtt | Its undocumented login returns HTTP 454 after a Govee backend change. Its LAN-only fallback reaches neither device. |
| Govee cloud integrations | Generally light on sensors; leak detectors' events aren't exposed by REST at all. |

The documented API *does* carry all of it — you just have to use the right half
for each device.

## How it works

Two channels, each for what it's actually good at:

```
                 REST  GET /user/devices, POST /device/state   (poll, 60s)
  Home                 -> pool temperature, air quality
  Assistant  <──┐
                └── MQTT  mqtts://mqtt.openapi.govee.com:8883  (push, instant)
                          topic GA/<api-key>
                          -> leak detector LEAKED / CLEARED
```

The MQTT connection is **outbound only**. Nothing is exposed to the internet,
no port forwarding, no webhook endpoint, no public URL. Your local Mosquitto
broker isn't involved — if you don't run one, you don't need one.

## Devices

| SKU | Device | Entities | Source |
| --- | --- | --- | --- |
| H5059 | Water Leak Detector | `binary_sensor` (moisture), per-probe detail | MQTT push |
| H5310 | Pool Thermometer | `sensor` (temperature) | REST poll |
| H5106 | Smart Air Quality Monitor | `sensor` temperature, humidity, air quality | REST poll |

Leak detectors report `online: false` over REST even when perfectly healthy —
they're sleeping battery devices that wake only to transmit. Their events still
arrive instantly.

## Setup

1. **Get an API key.** Govee Home app → `User` → `About Us` → **Request API
   Key**. It's emailed within seconds.
2. **Install** via HACS as a custom repository, or copy
   `custom_components/govee_management/` into your HA `config/custom_components/`
   and restart.
3. **Add the integration.** Settings → Devices & Services → Add Integration →
   *Govee Management* → paste the key.
4. **Pick your devices.** The next step lists everything on the account; all of
   them are ticked by default. Untick anything you don't want entities for.

### Adding devices later

Pair a new sensor in the Govee app and Home Assistant will tell you: a repair
notification appears under **Settings → System → Repairs** saying *"New Govee
device found"*, with a button that starts tracking it. Ignore the notification
and the device stays out — it will not nag you about that one again.

The account is re-checked every 15 minutes and at every restart, so new
hardware is usually noticed within a quarter of an hour without you doing
anything.

You can also do it by hand at any time from the integration's
**Configure → Devices to track**. The list is re-read from your account each
time you open it, so new hardware appears there — no need to remove the
integration or re-enter your key. The same screen lists what it found since
you last looked. Unticking a device removes it and its entities.

**Configure → Polling** changes the REST poll interval (60s floor).

If you already receive a device over Bluetooth — the H5106 monitors are
decoded locally by `govee_ble` via an ESPHome BLE proxy — you may prefer to
untick it here and keep the local, instant, rate-limit-free copy.

## Command-line tools

`tools/` holds the standalone scripts the integration is built on. They read
`api_govee_key` from `secrets.yaml` (copy `secrets.yaml.example`) and never
print it.

```bash
python tools/govee_api.py             # inventory + live state for every device
python tools/govee_api.py --devices   # inventory only (a single API call)
python tools/govee_api.py --json out.json
python -u tools/govee_mqtt.py         # stream events; -u matters, see below
python -u tools/govee_mqtt.py --seconds 30
```

Use these to confirm your key works and to see exactly what your account
exposes before touching Home Assistant. To test a leak detector, bridge its
probes with a damp cloth — you should see a `LEAKED` event within a second or
two, and `CLEARED` when it dries.

Run the MQTT tool with `python -u`. Without it, stdout buffering can hold
events back while stderr appears immediately, which makes a working
subscription look dead.

## Rate limits

The free Govee API is rate limited and non-commercial. Poll no faster than
once a minute. Push events don't count against polling and cost nothing, which
is another reason leaks go over MQTT rather than a fast poll.

## A note on leak detection

This path depends on your internet connection, Govee's servers, and the H5044
gateway. The detectors' 105 dB local alarms sound regardless, but Home
Assistant won't know during an outage. If you want alerting that survives all
three, these sensors can also be received directly with an RTL-SDR via
`rtl_433` — worth running alongside this rather than instead of it.

## Brand icon

`custom_components/govee_management/brand/` holds the integration's icon and
logo. Home Assistant 2026.3 and later reads brand images straight out of the
integration, so no PR to `home-assistant/brands` is needed.

`Logo_mat.png` at the repo root is the source artwork. Regenerate the set
after changing it — trim, centre and resize to 256/512 px, transparent PNG.
