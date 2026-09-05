"""Device discovery, REST polling and push fan-out for Govee Management."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
import logging
from time import monotonic
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    GoveeApi,
    GoveeApiError,
    GoveeAuthError,
    device_instances,
    parse_capabilities,
)
from .const import (
    CAP_AIR_QUALITY,
    CAP_HUMIDITY,
    CAP_LEAK_EVENT,
    CAP_TEMPERATURE,
    CONF_DEVICES,
    CONF_KNOWN_DEVICES,
    CONF_POLL_INTERVAL,
    DEFAULT_POLL_INTERVAL,
    DISCOVERY_INTERVAL,
    DOMAIN,
    GOVEE_SKUS,
    INTER_REQUEST_DELAY,
    ISSUE_NEW_DEVICE,
    MIN_POLL_INTERVAL,
    POLLED_INSTANCES,
    PUSHED_INSTANCES,
    SIGNAL_PUSH_EVENT,
)

_LOGGER = logging.getLogger(__name__)


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
        return known.get("reports_fahrenheit", True) if known else True


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
        self._known: set[str] | None = (
            set(known)
            if (known := entry.options.get(CONF_KNOWN_DEVICES)) is not None
            else None
        )
        self._rediscover = False
        self._last_discovery = 0.0

    async def _async_setup(self) -> None:
        """Fetch the account inventory once, before the first poll."""
        await self._async_refresh_devices()

    async def _async_refresh_devices(self) -> None:
        """Re-read the inventory, apply the selection, flag anything new."""
        self.all_devices = await self._async_fetch_devices()
        self._last_discovery = monotonic()
        if self._selected is None:
            self.devices = dict(self.all_devices)
        else:
            self.devices = {
                device_id: device
                for device_id, device in self.all_devices.items()
                if device_id in self._selected
            }
        self._async_sync_new_device_issues()

    @callback
    def _async_sync_new_device_issues(self) -> None:
        """Raise a repair for each device the user has never been offered.

        A device that is untracked but already *known* was unticked on
        purpose, so it stays quiet - only genuinely new hardware is reported.
        An entry predating this bookkeeping treats everything currently
        present as known, so upgrading does not fire a burst of repairs.
        """
        known = self._known if self._known is not None else set(self.all_devices)

        for device_id, device in self.all_devices.items():
            issue_id = ISSUE_NEW_DEVICE.format(device_id)
            if device_id in known or device_id in self.devices:
                ir.async_delete_issue(self.hass, DOMAIN, issue_id)
                continue

            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=True,
                severity=ir.IssueSeverity.WARNING,
                translation_key="new_device",
                translation_placeholders={
                    "name": device.name,
                    "model": f"{device.sku} {device.model}",
                },
                data={
                    "entry_id": self.config_entry.entry_id,
                    "device_id": device_id,
                },
            )

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
            if not sku or not device_id:
                continue

            device = GoveeDevice(
                sku=sku,
                device_id=device_id,
                name=raw.get("deviceName") or f"{sku} {device_id[-5:]}",
                instances=device_instances(raw),
            )
            if not device.polled and not device.pushed:
                # Nothing we can render. This is how a bridging gateway or any
                # other accessory drops out - by what it declares, not by SKU.
                _LOGGER.debug("Skipping %s: declares no capability we handle", sku)
                continue
            devices[device_id] = device

        _LOGGER.debug("Discovered %s Govee devices", len(devices))
        return devices

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        """Poll ``/device/state`` for every device with a measurement.

        Devices whose only capability is an event (the leak detectors) are not
        polled: their state simply is not in ``/device/state``, and a 60s poll
        would miss a transient leak even if it were.
        """
        if self._rediscover or monotonic() - self._last_discovery > DISCOVERY_INTERVAL:
            # Notices hardware paired in the Govee app without waiting for a
            # restart. One extra call every DISCOVERY_INTERVAL seconds.
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
