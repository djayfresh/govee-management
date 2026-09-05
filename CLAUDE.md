# Govee Management — Home Assistant custom integration

A HACS-installable `custom_components/govee_management` that brings Govee
devices into Home Assistant using **only Govee's documented OpenAPI** — both
its REST endpoints and its MQTT event stream.

Split out of `C:\dev\esp32-ble-proxy` on 2026-09-04. That repo remains the
ESP32 BLE proxy; this one is Govee only. They share nothing but history.

## Status: integration written (v0.1.0), not yet run on the HA host

Everything below was **verified live against the user's real account** in the
originating session. The two scripts in `tools/` work today and are the
reference implementation — read them first, they encode the working request
shapes.

```
.venv/Scripts/python.exe tools/govee_api.py            # REST inventory + state
.venv/Scripts/python.exe tools/govee_api.py --devices  # inventory only, 1 call
.venv/Scripts/python.exe -u tools/govee_mqtt.py        # live event stream
```

## Credentials

`secrets.yaml` (gitignored) holds `api_govee_key`. **Never read the real
`secrets.yaml`** — the user asked for this explicitly. `tools/govee_api.py`
has a `load_key()` that reads it without printing; import that rather than
handling the key yourself. The key doubles as the MQTT topic name, so it must
be redacted from any log line that would echo the topic.

The user's key currently also lives in `esp32-ble-proxy/secrets.yaml`. If this
project's `secrets.yaml` does not exist yet, ask them to copy it over — do not
go read it from the other repo.

## The two APIs, and which one to use for what

**REST** — `https://openapi.api.govee.com/router/api/v1`
- `GET /user/devices` — inventory. Header `Govee-API-Key: <key>`.
- `POST /device/state` — body `{"requestId": <uuid>, "payload": {"sku", "device"}}`.
- Rate limited. Poll no faster than 60s; `govee_api.py` sleeps 1s between calls.

**MQTT push** — `mqtts://mqtt.openapi.govee.com:8883`
- Username **and** password are both the API key.
- Topic: `GA/<api-key>`.
- Delivers events for any device declaring `devices.capabilities.event`.
- Outbound TLS only. No inbound ports, no webhook, nothing exposed.

Use **push for events** (leak detectors) and **REST polling for measurements**
(temperatures). They are not interchangeable — see the traps below.

## Verified device inventory

Seven devices on the account:

| SKU | Name | Type | How to read it |
| --- | --- | --- | --- |
| H5059 x4 | DS_Bathroom, LR_1, LR_2, LR_3 | Water leak detector | **MQTT push only** |
| H5310 | Pool Thermometer | Pool temp, behind H5044 gateway | REST poll |
| H5106 x2 | Bedroom Monitor, Family Room Monitor | Air quality + temp/humidity | REST poll |

Confirmed live values: pool `sensorTemperature=85.46`, Bedroom `71.78 / 52.4 /
airQuality=1`, Family Room `67.1 / 54.2 / airQuality=1`.

## Traps — each of these cost real time to find

1. **`online: false` does not mean offline.** All four H5059 leak detectors
   report `online=false` through REST, yet push events arrive instantly. They
   are sleeping battery devices that wake only to transmit. Do **not** gate
   entity availability on `online` for event devices, and do not conclude they
   are dead.

2. **Event state is a LIST, not a dict.** `capabilities[].state` comes back as
   `[{...}]` for event capabilities. Assuming a dict raises
   `AttributeError: 'list' object has no attribute 'get'`. Handle both.

3. **Leak state is not in `/device/state`.** Polling an H5059 returns only
   `online`. There is no leak field. Polling cannot detect leaks at all, and a
   60s poll would miss a transient event regardless. Push is the only path.

4. **Everything reports Fahrenheit.** Pool 85.46, indoor 71.78/67.1 — all °F.
   Unresolved whether the API always returns °F or mirrors the app's display
   setting; a single sample cannot distinguish them. The pending upstream PR
   for the sibling H5109 adds a `reports_fahrenheit` flag for exactly this.
   If the user switches the Govee app to °C, re-verify.

5. **Buffered stdout hides events.** Redirecting the MQTT listener to a file
   without `-u` leaves the payload in the buffer while stderr tracebacks land
   first, making a working subscription look broken.

## Dead ends — do not revisit

- **govee2mqtt** — its undocumented-API login (`app2.govee.com/.../v1/login`)
  returns **status 454** since a Govee backend change. Widespread: issues
  #682, #647, #627, #628, #649. Not a misconfiguration. Its suggested
  workaround (drop credentials, use LAN API) reaches neither the pool sensor
  nor the leak detectors. **We do not need it** — everything required is in
  the documented API.

- **Bluetooth / ESPHome BLE proxy** — H5059 and H5310 are not BLE devices.
  They speak a proprietary sub-GHz radio to an **H5044 gateway**. Fingerprinting
  all 53 devices an ESP32 proxy heard, by Govee manufacturer ID `0x8843` and
  service UUID `ec88`, found no trace of either. **No proxy placement can ever
  reach them.** The gateway itself is loud on BLE (-60 dBm) but carries no
  sensor payload.

- **Pointing HA's MQTT integration at Govee's broker** — HA supports one
  broker, so this would disconnect local Mosquitto and take every local MQTT
  device with it. Govee publishes raw JSON, not HA discovery, so no entities
  would appear anyway.

- **Bridging on the ESP32** — ESPHome's `mqtt:` is a singleton; it cannot talk
  to two brokers. Rejected in favour of this integration.

## Exact payload shapes

Leak event, captured live (LEAKED at 19:48:44, CLEARED 8s later):

```json
{
  "sku": "H5059",
  "device": "03:50:17:B9:FF:FF:FF:19:FF:FF:00:4B:FF:FF:00:29",
  "deviceName": "LR_2 H5059_0029",
  "capabilities": [{
    "type": "devices.capabilities.event",
    "instance": "bodyAppearedEvent",
    "state": [{
      "name": "LEAKED", "value": 1,
      "probesState": {"top": 0, "bot": 1}
    }]
  }]
}
```

`value` 1 = LEAKED, 2 = UN_LEAKED. `probesState.bot` is the lower probe, `top`
the upper; 1 = water present, 0 = clear. The `CLEARED` message is identical
with `"name": "UN_LEAKED", "value": 2` and both probes 0.

Device-list capability instances seen: `bodyAppearedEvent` (H5059),
`sensorTemperature` (H5310), `airQuality`/`sensorHumidity`/`sensorTemperature`
(H5106), plus `online` in state responses.

## Build plan

Decided with the user: **custom integration**, not an add-on. No MQTT broker
dependency, native entities, config flow, and it is the only form that could be
upstreamed. Scope for the first pass is **all three** device families.

```
custom_components/govee_management/
  __init__.py        setup, runtime_data, start/stop the push task
  api.py             async REST client + parse_capabilities (list/dict safe)
  push.py            reconnecting aiomqtt listener, key never logged
  coordinator.py     device discovery, REST poll, push fan-out via dispatcher
  config_flow.py     API key entry, device picker, reauth, options menu
  repairs.py         one-click "track this new device" fix flow
  entity.py          shared DeviceInfo
  binary_sensor.py   leak -> device_class moisture, push + RestoreEntity
  sensor.py          temperature / humidity / PM2.5, from poll
  diagnostics.py     redacts the key
  const.py           SKU map, endpoints, signals
  strings.json + translations/en.json
  manifest.json
hacs.json
```

The user picks which devices to track: a multi-select step after the key is
validated (all ticked by default), stored as `options["devices"]`. The options
flow is a menu of **Devices to track** / **Polling**; the device step re-fetches
`/user/devices` live, so hardware paired after setup shows up without
recreating the entry. Shape follows the proxmoxve integration's node/VM
selection. An entry with no `devices` option tracks everything.
Untracked devices are pruned from the device registry on reload, so unticking
one really removes its entities.

New hardware is surfaced through the **repairs** platform rather than a
persistent notification: `coordinator._async_sync_new_device_issues()` raises a
fixable issue per unseen device, and `repairs.py` turns it into a one-click
"track it". The quiet-when-unticked rule depends on `options["known_devices"]`
- every id the pickers have already offered. Without it, unticking a device
would immediately raise a repair asking to add it back. Both pickers rewrite
`known_devices` on save. Re-discovery runs every `DISCOVERY_INTERVAL` (900s)
from inside the poll, so a new device appears without a restart.

Entities are derived from each device's declared **capability instances**, not
from a SKU allowlist - `GOVEE_SKUS` only supplies friendly names and the
`reports_fahrenheit` flag. Unknown Govee sensors that report
`sensorTemperature` / `sensorHumidity` / `airQuality` therefore work with no
code change, defaulting to degF.

Min HA version is 2025.2.0 (hacs.json). The user runs core 2026.9.0, so every
API used is available.

**No local HA test rig is possible on this machine**: current HA needs Python
3.13+, and only 3.12 and 3.14 are installed. `.venv` exists with
paho-mqtt/pyyaml/aiomqtt for `tools/` only. Verification so far is the live
`tools/govee_api.py --devices` call (7 devices, matches the table above) plus
a direct test of `parse_capabilities` against the captured leak payload, plus
the device picker exercised against the live inventory behind minimal HA stubs
(scratchpad only). `airQuality` = PM2.5 in ug/m3 is confirmed: `govee_ble`
decodes the same H5106 that way over the BLE proxy.
Testing the integration itself means copying `custom_components/` to the HA
host and restarting.

Notes for whoever builds it:
- Use `aiomqtt` (or `paho` in an executor) for the push task; keep it
  reconnecting. `tools/govee_mqtt.py` shows the working connect/auth/subscribe.
- Poll interval 60s minimum. One `/user/devices` call at setup, then
  `/device/state` per measurement device.
- Leak sensors must be driven by push, with state surviving restarts — an
  event fires once and is not replayed. Consider restoring last state.
- Test with no Docker available on this machine: copy `custom_components/` to
  `/config/custom_components/` on the HA host and restart.

## Upstream opportunity

[Conexo-Casa/govee-thermometer-ha](https://github.com/Conexo-Casa/govee-thermometer-ha)
is an existing OpenAPI thermometer integration with a **SKU allowlist**
(`GOVEE_SENSOR_SKUS` in `const.py`, entries carry `category`, `has_humidity`,
`reports_fahrenheit`). PR #4 (open, from `tomanb07:add-h5109-support`) adds the
H5109 pool thermometer. **H5310 is absent and would work with a two-line
addition** — the user has confirmed live data proving it reports
`sensorTemperature` in °F. Offering that upstream was discussed and is still
open. It does not cover the H5059 leak detectors, which is the main reason this
project exists.

## Environment

- Windows 10, PowerShell 5.1 (no `&&`, no ternary, no `??`). Git Bash also
  available. **No Docker on this machine.**
- Python 3.12.10 at `%LOCALAPPDATA%\Programs\Python\Python312`. Machine default
  is 3.14. A venv with `paho-mqtt` exists at `esp32-ble-proxy\.venv`; this
  project should get its own.
- Home Assistant OS 18.2, core 2026.9.0, Supervisor 2026.08.0, on a VM.
  HACS 2.0.5 installed. Custom components already present include spook,
  hacs, truenas, proxmoxve, sonoff, wyzeapi.
- User also runs TrueNAS and Proxmox, viable hosts if anything ever needs a
  container.

## Related

`C:\dev\esp32-ble-proxy` — ESP32-S3 BLE proxy, flashed and live at
`ble-proxy-s3.local` / 10.0.0.9. Still useful for genuinely-BLE Govee gear
(the H5106 air quality monitors are heard well there). Its `CLAUDE.md` holds
the BLE coverage analysis and ESP32-S3 flashing gotchas.
