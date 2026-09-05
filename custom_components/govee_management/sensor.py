"""Measurement sensors: pool temperature, indoor temp/humidity/air quality.

All of these come from REST polling. Leak detectors are not here - their state
is not in ``/device/state`` at all (see binary_sensor.py).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
    PERCENTAGE,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import GoveeConfigEntry
from .const import CAP_AIR_QUALITY, CAP_HUMIDITY, CAP_TEMPERATURE
from .coordinator import GoveeCoordinator, GoveeDevice
from .entity import GoveeEntity


@dataclass(frozen=True, kw_only=True)
class GoveeSensorDescription(SensorEntityDescription):
    """A capability instance mapped onto a Home Assistant sensor."""

    # Temperature is the one unit that depends on the device, so the unit is a
    # callable rather than a constant.
    unit_fn: Callable[[GoveeDevice], str | None] = lambda device: None


SENSOR_DESCRIPTIONS: dict[str, GoveeSensorDescription] = {
    CAP_TEMPERATURE: GoveeSensorDescription(
        key=CAP_TEMPERATURE,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        unit_fn=lambda device: (
            UnitOfTemperature.FAHRENHEIT
            if device.reports_fahrenheit
            else UnitOfTemperature.CELSIUS
        ),
    ),
    CAP_HUMIDITY: GoveeSensorDescription(
        key=CAP_HUMIDITY,
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        unit_fn=lambda device: PERCENTAGE,
    ),
    # The H5106 reports air quality as a PM2.5 concentration.
    CAP_AIR_QUALITY: GoveeSensorDescription(
        key=CAP_AIR_QUALITY,
        device_class=SensorDeviceClass.PM25,
        state_class=SensorStateClass.MEASUREMENT,
        unit_fn=lambda device: CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
    ),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GoveeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create one sensor per polled capability instance."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        GoveeSensor(coordinator, device, SENSOR_DESCRIPTIONS[instance])
        for device in coordinator.devices.values()
        for instance in device.polled
        if instance in SENSOR_DESCRIPTIONS
    )


class GoveeSensor(CoordinatorEntity[GoveeCoordinator], GoveeEntity, SensorEntity):
    """One polled measurement from one Govee device."""

    entity_description: GoveeSensorDescription

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
        description: GoveeSensorDescription,
    ) -> None:
        """Bind the sensor to a device and a capability instance."""
        CoordinatorEntity.__init__(self, coordinator)
        GoveeEntity.__init__(self, device)
        self.entity_description = description
        self._attr_unique_id = f"{device.device_id}_{description.key}"
        self._attr_native_unit_of_measurement = description.unit_fn(device)
        self._attr_translation_key = description.key

    @property
    def native_value(self) -> float | int | None:
        """Latest polled value, or None if the last poll had nothing for us."""
        raw = (self.coordinator.data or {}).get(self.device.device_id, {}).get(
            self.entity_description.key
        )
        return _as_number(raw)

    @property
    def available(self) -> bool:
        """Available while the poll succeeds and carries a usable value.

        Deliberately not gated on the device's ``online`` flag - Govee reports
        ``online: false`` for healthy battery devices.
        """
        return super().available and self.native_value is not None


def _as_number(raw: Any) -> float | int | None:
    """Coerce a capability value to a number, tolerating nested payloads."""
    if isinstance(raw, dict):
        # Some capabilities wrap the reading; take the first numeric field.
        raw = next(
            (value for value in raw.values() if isinstance(value, (int, float))), None
        )
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, (int, float)):
        return raw
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None
