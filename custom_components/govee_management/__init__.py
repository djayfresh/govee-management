"""The Govee Management integration.

REST polling for measurements, MQTT push for events. See CLAUDE.md for why
they are not interchangeable.
"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import GoveeApi
from .const import DOMAIN
from .coordinator import GoveeCoordinator
from .push import GoveePushClient

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]


@dataclass(slots=True)
class GoveeRuntimeData:
    """What the platforms need, hung off the config entry."""

    coordinator: GoveeCoordinator
    push: GoveePushClient


type GoveeConfigEntry = ConfigEntry[GoveeRuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: GoveeConfigEntry) -> bool:
    """Set up Govee Management from a config entry."""
    api = GoveeApi(async_get_clientsession(hass), entry.data[CONF_API_KEY])
    coordinator = GoveeCoordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()

    _async_prune_devices(hass, entry, coordinator)

    push = GoveePushClient(hass, api.api_key, coordinator.async_handle_push)
    entry.runtime_data = GoveeRuntimeData(coordinator=coordinator, push=push)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Only now are the entities listening, so no event can arrive unheard.
    push.async_start()
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: GoveeConfigEntry) -> bool:
    """Tear down the entry, stopping the push listener first."""
    await entry.runtime_data.push.async_stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


@callback
def _async_prune_devices(
    hass: HomeAssistant, entry: GoveeConfigEntry, coordinator: GoveeCoordinator
) -> None:
    """Drop registry devices the user has untracked or removed from Govee.

    Without this, unticking a device in the options flow would leave its device
    and its entities behind as stale, permanently unavailable leftovers.
    """
    registry = dr.async_get(hass)
    tracked = set(coordinator.devices)

    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if any(
            domain == DOMAIN and device_id not in tracked
            for domain, device_id in device.identifiers
        ):
            registry.async_update_device(
                device.id, remove_config_entry_id=entry.entry_id
            )


async def _async_update_listener(hass: HomeAssistant, entry: GoveeConfigEntry) -> None:
    """Reload when options change, so a new poll interval takes effect."""
    await hass.config_entries.async_reload(entry.entry_id)
