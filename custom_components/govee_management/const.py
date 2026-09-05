"""Constants for the Govee Management integration.

Every value here was verified live against a real Govee account on 2026-09-04.
See CLAUDE.md for the captured payloads and the traps behind these choices.
"""

DOMAIN = "govee_management"
MANUFACTURER = "Govee"

# --- Config entry -------------------------------------------------------
CONF_POLL_INTERVAL = "poll_interval"
DEFAULT_POLL_INTERVAL = 60

# Device ids the user chose to track. A missing key means "track everything",
# which is what entries created before device selection existed expect.
CONF_DEVICES = "devices"

# Every device id the user has already been offered in a picker. A device that
# is untracked but *known* was unticked on purpose, so it must stay quiet; only
# ids absent from this list are reported as new.
CONF_KNOWN_DEVICES = "known_devices"

# Repair issue raised when the account grows a device we have never offered.
ISSUE_NEW_DEVICE = "new_device_{}"

# Dispatcher signal carrying MQTT push payloads, formatted with the entry id.
SIGNAL_PUSH_EVENT = "govee_management_push_{}"

# --- REST ---------------------------------------------------------------
API_BASE = "https://openapi.api.govee.com/router/api/v1"
DEVICES_URL = f"{API_BASE}/user/devices"
STATE_URL = f"{API_BASE}/device/state"
API_KEY_HEADER = "Govee-API-Key"

# Govee rate limits the free tier; do not go below this.
MIN_POLL_INTERVAL = 60
# Govee's own docs suggest spacing calls out; tools/govee_api.py sleeps 1s.
INTER_REQUEST_DELAY = 1.0
REQUEST_TIMEOUT = 30
# How often to re-read the inventory looking for devices added in the Govee
# app. One extra call per interval, so this stays well inside the rate limit.
DISCOVERY_INTERVAL = 900

# --- MQTT push ----------------------------------------------------------
# The API key is used as username AND password, and the topic is GA/<key>.
MQTT_HOST = "mqtt.openapi.govee.com"
MQTT_PORT = 8883
MQTT_TOPIC_TEMPLATE = "GA/{key}"
MQTT_KEEPALIVE = 60
MQTT_RECONNECT_MIN = 5
MQTT_RECONNECT_MAX = 300

# --- Capability instances ------------------------------------------------
CAP_TEMPERATURE = "sensorTemperature"
CAP_HUMIDITY = "sensorHumidity"
CAP_AIR_QUALITY = "airQuality"
CAP_ONLINE = "online"
CAP_LEAK_EVENT = "bodyAppearedEvent"

# Fallback only. The device's own `eventState.options` declares these - see
# binary_sensor, which prefers the name Govee sends with the event.
LEAK_VALUE_LEAKED = 1
LEAK_VALUE_CLEARED = 2

# Instances this integration knows how to render. A device declaring none of
# them has nothing to show - that is how gateways and other accessories are
# skipped, rather than by naming their SKUs.
POLLED_INSTANCES = (CAP_TEMPERATURE, CAP_HUMIDITY, CAP_AIR_QUALITY)
PUSHED_INSTANCES = (CAP_LEAK_EVENT,)
HANDLED_INSTANCES = frozenset(POLLED_INSTANCES + PUSHED_INSTANCES)

# --- SKU map -------------------------------------------------------------
# Advisory only. Entities are created from each device's declared capability
# instances, not from this table, so an unlisted SKU still works - it just
# shows its bare model number and assumes degF.
#
# reports_fahrenheit: every device observed live returned degF. It is
# unresolved whether the API is always degF or mirrors the app display unit,
# so re-verify if the Govee app is switched to Celsius. Omitted where the
# device reports no temperature at all; the default is True.
#
# Verified against two accounts (2026-09-04, 2026-09-05). Leak detectors send
# `bodyAppearedEvent` over MQTT push and report only `online` over REST.
GOVEE_SKUS = {
    # Water leak detectors - push only.
    "H5054": {"name": "Water Leak Detector"},
    "H5058": {"name": "Water Leak Detector"},
    "H5059": {"name": "Water Leak Detector"},
    # Thermometers and monitors - REST poll.
    "H5310": {"name": "Pool Thermometer", "reports_fahrenheit": True},
    "H5106": {"name": "Smart Air Quality Monitor", "reports_fahrenheit": True},
    # Bluetooth-only hygrometers. They appear in the account inventory but
    # never reach the cloud: /device/state returns online=false and empty
    # strings for both readings, so their entities stay unavailable. Read
    # these locally with `govee_ble` via a Bluetooth proxy instead.
    "H5074": {"name": "Mini Hygrometer Thermometer", "reports_fahrenheit": True},
    "H5075": {"name": "Hygrometer Thermometer", "reports_fahrenheit": True},
}

