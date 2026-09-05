"""Repair flows for Govee Management.

The coordinator raises a repair issue when the account grows a device Home
Assistant has never offered to track - after pairing a new sensor in the Govee
app, for instance. This turns that notification into a single-click "add it"
rather than a trip through the options flow.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.repairs import RepairsFlow
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .const import CONF_DEVICES, CONF_KNOWN_DEVICES


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Build the flow for a new-device issue."""
    entry_id = str((data or {}).get("entry_id", ""))
    device_id = str((data or {}).get("device_id", ""))
    return NewDeviceRepairFlow(entry_id, device_id)


class NewDeviceRepairFlow(RepairsFlow):
    """Offer to start tracking a newly discovered Govee device."""

    def __init__(self, entry_id: str, device_id: str) -> None:
        """Remember which entry and device the issue is about."""
        self._entry_id = entry_id
        self._device_id = device_id

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Repairs always enters here; there is only one question to ask."""
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Add the device to the tracked list, or explain why we cannot."""
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None or not self._device_id:
            # The entry was removed while the notification sat unread.
            return self.async_abort(reason="entry_gone")

        if user_input is not None:
            self._async_track_device(entry)
            # Reloading rebuilds the platforms, which creates the entities and
            # clears the issue via the coordinator's own bookkeeping.
            self.hass.config_entries.async_schedule_reload(entry.entry_id)
            return self.async_create_entry(data={})

        device = self._async_describe(entry)
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            description_placeholders={"name": device},
        )

    def _async_track_device(self, entry: ConfigEntry) -> None:
        """Add this device to the entry's tracked and known device lists."""
        options = dict(entry.options)

        tracked = options.get(CONF_DEVICES)
        if tracked is None:
            # No selection stored means "track everything", and writing a
            # one-element list here would silently drop every other device.
            coordinator = _async_coordinator(entry)
            tracked = list(coordinator.all_devices) if coordinator else []
        options[CONF_DEVICES] = sorted({*tracked, self._device_id})

        known = options.get(CONF_KNOWN_DEVICES) or []
        options[CONF_KNOWN_DEVICES] = sorted({*known, *options[CONF_DEVICES]})

        self.hass.config_entries.async_update_entry(entry, options=options)

    def _async_describe(self, entry: ConfigEntry) -> str:
        """Name the device for the confirmation prompt."""
        coordinator = _async_coordinator(entry)
        if coordinator and (device := coordinator.all_devices.get(self._device_id)):
            return f"{device.name} ({device.sku} {device.model})"
        return self._device_id


def _async_coordinator(entry: ConfigEntry) -> Any:
    """The entry's coordinator, or None if the entry is not loaded.

    ``runtime_data`` simply does not exist on an unloaded entry, so it cannot
    be reached with a plain attribute access.
    """
    runtime = getattr(entry, "runtime_data", None)
    return getattr(runtime, "coordinator", None)
