"""Device discovery, REST polling and push fan-out for Govee Management."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import GoveeApi, GoveeApiError, GoveeAuthError, parse_capabilities
from .const import (
    CAP_AIR_QUALITY,
    CONF_DEVICES,
    CAP_HUMIDITY,
    CAP_LEAK_EVENT,
    CAP_TEMPERATURE,
    CONF_POLL_INTERVAL,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    GATEWAY_SKUS,
    GOVEE_SKUS,
    INTER_REQUEST_DELAY,
    MIN_POLL_INTERVAL,
    SIGNAL_PUSH_EVENT,
)

_LOGGER = logging.getLogger(__name__)

# Measurement capabilities worth polling. Anything else a device declares is
# ignored rather than guessed at.
POLLED_INSTANCES = (CAP_TEMPERATURE, CAP_HUMIDITY, CAP_AIR_QUALITY)
# Event capabilities that arrive over MQTT push only.
PUSHED_INSTANCES = (CAP_LEAK_EVENT,)


@dataclass(slots=True)
class GoveeDevice:
    """One device from ``GET /user/devices``."""

    sku: str
    device_id: str
    name: str
    instances: set[str] = field(default_factory=set)

    @property
    def polled(self) -> tuple[str, ...]:
        """Measurement instances this device exposes over REST."""
        return tuple(i for i in POLLED_INSTANCES if i in self.instances)

    @property
    def pushed(self) -> tuple[str, ...]:
        """Event instances this device only reports over MQTT push."""
        return tuple(i for i in PUSHED_INSTANCES if i in self.instances)

    @property
    def model(self) -> str:
        """Human-readable model name, falling back to the raw SKU."""
        known = GOVEE_SKUS.get(self.sku)
        return known["name"] if known else self.sku

    @property
    def reports_fahrenheit(self) -> bool:
        """Whether temperatures come back in degF.

        Every device observed live reported degF. It is unresolved whether the
        API is always degF or mirrors the Govee app's display unit, so this
        stays a per-SKU flag defaulting to True for unknown hardware.
        """
        known = GOVEE_SKUS.get(self.sku)
        return known["reports_fahrenheit"] if known else True


class GoveeCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Poll measurement devices and fan push events out to entities."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, api: GoveeApi) -> None:
        """Set up the coordinator with the entry's configured poll interval."""
        interval = max(
            entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
            MIN_POLL_INTERVAL,
        )
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=timedelta(seconds=interval),
        )
        self.api = api
        # Everything the account exposes, and the subset the user picked. The
        # option is absent on entries created before device selection existed,
        # which means "track everything".
        self.all_devices: dict[str, GoveeDevice] = {}
        self.devices: dict[str, GoveeDevice] = {}
        self._selected: list[str] | None = entry.options.get(CONF_DEVICES)
        self._rediscover = False

    async def _async_setup(self) -> None:
        """Fetch the account inventory once, before the first poll."""
        await self._async_refresh_devices()

    async def _async_refresh_devices(self) -> None:
        """Re-read the inventory and apply the user's device selection."""
        self.all_devices = await self._async_fetch_devices()
        if self._selected is None:
            self.devices = dict(self.all_devices)
        else:
            self.devices = {
                device_id: device
                for device_id, device in self.all_devices.items()
                if device_id in self._selected
            }

    async def _async_fetch_devices(self) -> dict[str, GoveeDevice]:
        """Build the device map from ``GET /user/devices``."""
        try:
            raw_devices = await self.api.async_get_devices()
        except GoveeAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except GoveeApiError as err:
            raise UpdateFailed(str(err)) from err

        devices: dict[str, GoveeDevice] = {}
        for raw in raw_devices:
            sku = raw.get("sku")
            device_id = raw.get("device")
            if not sku or not device_id or sku in GATEWAY_SKUS:
                # The H5044 gateway bridges other devices but is not a sensor.
                continue
            instances = {
                capability.get("instance")
                for capability in raw.get("capabilities") or []
                if isinstance(capability, dict) and capability.get("instance")
            }
            devices[device_id] = GoveeDevice(
                sku=sku,
                device_id=device_id,
                name=raw.get("deviceName") or f"{sku} {device_id[-5:]}",
                instances=instances,
            )

        _LOGGER.debug("Discovered %s Govee devices", len(devices))
        return devices

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        """Poll ``/device/state`` for every device with a measurement.

        Devices whose only capability is an event (the leak detectors) are not
        polled: their state simply is not in ``/device/state``, and a 60s poll
        would miss a transient leak even if it were.
        """
        if self._rediscover:
            self._rediscover = False
            await self._async_refresh_devices()

        results: dict[str, dict[str, Any]] = dict(self.data or {})
        pollable = [device for device in self.devices.values() if device.polled]

        errors: list[str] = []
        for index, device in enumerate(pollable):
            if index:
                # Stay well under Govee's rate limit, as tools/govee_api.py does.
                await asyncio.sleep(INTER_REQUEST_DELAY)
            try:
                results[device.device_id] = await self.api.async_get_state(
                    device.sku, device.device_id
                )
            except GoveeAuthError as err:
                raise ConfigEntryAuthFailed(str(err)) from err
            except GoveeApiError as err:
                # One unreachable device must not blank out the others.
                errors.append(f"{device.sku}: {err}")

        if errors and len(errors) == len(pollable):
            raise UpdateFailed("; ".join(errors))
        for error in errors:
            _LOGGER.warning("Govee state poll failed for %s", error)

        return results

    @callback
    def async_handle_push(self, payload: dict[str, Any]) -> None:
        """Route one MQTT push payload to the entities that care about it."""
        device_id = payload.get("device")
        if not device_id:
            _LOGGER.debug("Ignoring Govee push payload with no device id")
            return

        state = parse_capabilities(payload.get("capabilities"))
        if not state:
            _LOGGER.debug("Govee push payload for %s carried no state", payload.get("sku"))
            return

        if device_id not in self.devices:
            if device_id not in self.all_devices:
                # A device added in the Govee app after setup. Re-read the
                # inventory on the next poll; the user still has to tick it in
                # the options flow before it gets entities.
                _LOGGER.debug("Push event from an unknown device; will re-discover")
                self._rediscover = True
            # Otherwise it is a device the user deliberately untracked.
            return

        async_dispatcher_send(
            self.hass,
            SIGNAL_PUSH_EVENT.format(self.config_entry.entry_id),
            device_id,
            state,
        )
