"""Leak detectors - driven entirely by the MQTT push stream.

Three facts shape this file:

* ``/device/state`` on an H5059 returns only ``online``. There is no leak
  field, so polling can never detect a leak.
* An event is delivered once and never replayed, so state has to survive a
  restart. Hence RestoreEntity.
* These are sleeping battery devices that report ``online: false`` while
  perfectly healthy, so availability must not depend on it.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import GoveeConfigEntry
from .const import (
    CAP_LEAK_EVENT,
    LEAK_VALUE_CLEARED,
    LEAK_VALUE_LEAKED,
    SIGNAL_PUSH_EVENT,
)
from .coordinator import GoveeDevice
from .entity import GoveeEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GoveeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create a moisture sensor for every device with a leak event."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        GoveeLeakSensor(entry.entry_id, device)
        for device in coordinator.devices.values()
        if CAP_LEAK_EVENT in device.pushed
    )


class GoveeLeakSensor(GoveeEntity, BinarySensorEntity, RestoreEntity):
    """An H5059 water leak detector."""

    _attr_device_class = BinarySensorDeviceClass.MOISTURE
    _attr_translation_key = "leak"
    # Push-only: never marked unavailable, since silence is the normal state
    # of a sleeping battery sensor.
    _attr_available = True

    def __init__(self, entry_id: str, device: GoveeDevice) -> None:
        """Bind the sensor to a device and the entry's push signal."""
        GoveeEntity.__init__(self, device)
        self._entry_id = entry_id
        self._attr_unique_id = f"{device.device_id}_{CAP_LEAK_EVENT}"
        self._attr_is_on: bool | None = None
        self._probes: dict[str, Any] = {}

    async def async_added_to_hass(self) -> None:
        """Restore the last known state, then listen for push events."""
        await super().async_added_to_hass()

        if self._attr_is_on is None and (last := await self.async_get_last_state()):
            if last.state in ("on", "off"):
                self._attr_is_on = last.state == "on"
            for probe in ("probe_top", "probe_bottom"):
                if probe in last.attributes:
                    self._probes[probe] = last.attributes[probe]

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_PUSH_EVENT.format(self._entry_id),
                self._handle_push,
            )
        )

    @callback
    def _handle_push(self, device_id: str, state: dict[str, Any]) -> None:
        """Apply a LEAKED / UN_LEAKED event addressed to this device."""
        if device_id != self.device.device_id:
            return

        event = state.get(CAP_LEAK_EVENT)
        if not isinstance(event, dict):
            return

        value = event.get("value")
        if value == LEAK_VALUE_LEAKED:
            self._attr_is_on = True
        elif value == LEAK_VALUE_CLEARED:
            self._attr_is_on = False
        else:
            return

        probes = event.get("probesState") or {}
        if isinstance(probes, dict):
            self._probes = {
                "probe_top": probes.get("top"),
                "probe_bottom": probes.get("bot"),
            }

        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Per-probe detail: 1 means water present on that probe."""
        return self._probes
