"""Diagnostics for a Govee Management config entry.

The API key is also the MQTT topic name, so it is redacted rather than merely
omitted from the entry data.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant

from . import GoveeConfigEntry

TO_REDACT = {CONF_API_KEY}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: GoveeConfigEntry
) -> dict[str, Any]:
    """Return everything useful for a bug report, minus the key."""
    runtime = entry.runtime_data
    coordinator = runtime.coordinator

    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "push_connected": runtime.push.connected,
        "poll_succeeded": coordinator.last_update_success,
        "devices_available": len(coordinator.all_devices),
        "devices_tracked": len(coordinator.devices),
        "untracked": [
            {"sku": device.sku, "name": device.name}
            for device_id, device in coordinator.all_devices.items()
            if device_id not in coordinator.devices
        ],
        "devices": [
            {
                "sku": device.sku,
                "model": device.model,
                "instances": sorted(device.instances),
                "polled": list(device.polled),
                "pushed": list(device.pushed),
                "state": (coordinator.data or {}).get(device.device_id),
            }
            for device in coordinator.devices.values()
        ],
    }
