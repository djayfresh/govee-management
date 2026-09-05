"""Constants for the Govee Management integration.

Every value here was verified live against a real Govee account on 2026-09-04.
See CLAUDE.md for the captured payloads and the traps behind these choices.
"""

DOMAIN = "govee_management"

# --- REST ---------------------------------------------------------------
API_BASE = "https://openapi.api.govee.com/router/api/v1"
DEVICES_URL = f"{API_BASE}/user/devices"
STATE_URL = f"{API_BASE}/device/state"
API_KEY_HEADER = "Govee-API-Key"

# Govee rate limits the free tier; do not go below this.
MIN_POLL_INTERVAL = 60

# --- MQTT push ----------------------------------------------------------
# The API key is used as username AND password, and the topic is GA/<key>.
MQTT_HOST = "mqtt.openapi.govee.com"
MQTT_PORT = 8883
MQTT_TOPIC_TEMPLATE = "GA/{key}"

# --- Capability instances ------------------------------------------------
CAP_TEMPERATURE = "sensorTemperature"
CAP_HUMIDITY = "sensorHumidity"
CAP_AIR_QUALITY = "airQuality"
CAP_ONLINE = "online"
CAP_LEAK_EVENT = "bodyAppearedEvent"

# bodyAppearedEvent state values
LEAK_VALUE_LEAKED = 1
LEAK_VALUE_CLEARED = 2

# --- SKU map -------------------------------------------------------------
# source:
#   "push" - state arrives only via the MQTT event stream. REST reports
#            online=false for these even when healthy; they are sleeping
#            battery devices. Never gate availability on `online` here.
#   "poll" - state comes from POST /device/state.
#
# reports_fahrenheit: every observed device returned degF. It is unresolved
# whether the API is always degF or mirrors the app display unit, so re-verify
# if the user switches the Govee app to Celsius.
GOVEE_SKUS = {
    "H5059": {
        "name": "Water Leak Detector",
        "source": "push",
        "binary_sensors": ["moisture"],
        "sensors": [],
        "reports_fahrenheit": False,
    },
    "H5310": {
        "name": "Pool Thermometer",
        "source": "poll",
        "binary_sensors": [],
        "sensors": [CAP_TEMPERATURE],
        "reports_fahrenheit": True,
    },
    "H5106": {
        "name": "Smart Air Quality Monitor",
        "source": "poll",
        "binary_sensors": [],
        "sensors": [CAP_TEMPERATURE, CAP_HUMIDITY, CAP_AIR_QUALITY],
        "reports_fahrenheit": True,
    },
}

# Gateway that bridges H5059 / H5310 over a proprietary sub-GHz radio. It is
# not itself a sensor and is intentionally not exposed as one.
GATEWAY_SKUS = {"H5044"}
