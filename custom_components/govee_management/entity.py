"""Shared device-registry plumbing for Govee entities."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, MANUFACTURER
from .coordinator import GoveeDevice


def device_info(device: GoveeDevice) -> DeviceInfo:
    """Registry entry for one Govee device."""
    return DeviceInfo(
        identifiers={(DOMAIN, device.device_id)},
        manufacturer=MANUFACTURER,
        model=device.model,
        model_id=device.sku,
        name=device.name,
    )


class GoveeEntity(Entity):
    """Mixin giving an entity its device and a name derived from the device."""

    _attr_has_entity_name = True

    def __init__(self, device: GoveeDevice) -> None:
        """Attach the entity to its Govee device."""
        self.device = device
        self._attr_device_info = device_info(device)
