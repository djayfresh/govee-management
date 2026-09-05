"""Config and options flow.

Setup is two steps: the user pastes their own Govee API key, then picks which
of the account's devices Home Assistant should track. The same picker is
available afterwards from the options flow, where it re-reads the account
inventory - so a sensor added in the Govee app later can be picked up without
removing and re-adding the integration.

The shape follows the proxmoxve integration: select resources during setup,
and re-select from options to pull in ones that appeared since.
"""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_API_KEY
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    GoveeApi,
    GoveeApiError,
    GoveeAuthError,
    GoveeRateLimitError,
    device_instances,
)
from .const import (
    CONF_DEVICES,
    CONF_KNOWN_DEVICES,
    CONF_POLL_INTERVAL,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    GOVEE_SKUS,
    HANDLED_INSTANCES,
    MIN_POLL_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

STEP_API_KEY_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_API_KEY): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        )
    }
)


def selectable_devices(raw_devices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Devices worth offering: those declaring a capability we can render.

    Offering a device we would build no entities for - a bridging gateway, say
    - is just a confusing checkbox, and testing what it *declares* means no
    SKU needs to be known in advance.
    """
    return [
        device
        for device in raw_devices
        if device.get("device") and device_instances(device) & HANDLED_INSTANCES
    ]


def device_label(device: dict[str, Any]) -> str:
    """"Pool Thermometer (H5310 Pool Thermometer)" - the name, then what it is."""
    sku = device.get("sku", "?")
    name = device.get("deviceName") or sku
    known = GOVEE_SKUS.get(sku)
    model = f"{sku} {known['name']}" if known else sku
    return f"{name} ({model})"


def _device_selector(
    raw_devices: list[dict[str, Any]], selected: list[str]
) -> vol.Schema:
    """Multi-select over the account inventory, pre-ticked with `selected`."""
    options = sorted(
        (
            SelectOptionDict(value=device["device"], label=device_label(device))
            for device in selectable_devices(raw_devices)
        ),
        key=lambda option: option["label"].casefold(),
    )
    # Only offer defaults that still exist, or the form refuses to render.
    valid = {option["value"] for option in options}
    return vol.Schema(
        {
            vol.Required(
                CONF_DEVICES,
                default=[device for device in selected if device in valid],
            ): SelectSelector(
                SelectSelectorConfig(
                    options=options,
                    multiple=True,
                    mode=SelectSelectorMode.LIST,
                )
            )
        }
    )


class GoveeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Key entry, then device selection."""

    VERSION = 1

    def __init__(self) -> None:
        """Hold the validated key and inventory between the two steps."""
        self._api_key: str = ""
        self._raw_devices: list[dict[str, Any]] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for an API key and prove it works before going any further."""
        errors: dict[str, str] = {}

        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()
            devices, errors = await self._async_validate(api_key)

            if not errors:
                # The key itself is the account identity, but it is a secret
                # and unique_id is stored in the clear, so hash it.
                await self.async_set_unique_id(_account_id(api_key))
                self._abort_if_unique_id_configured()
                if not selectable_devices(devices):
                    return self.async_abort(reason="no_devices")
                self._api_key = api_key
                self._raw_devices = devices
                return await self.async_step_devices()

        return self.async_show_form(
            step_id="user", data_schema=STEP_API_KEY_SCHEMA, errors=errors
        )

    async def async_step_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user tick the devices to create entities for."""
        if user_input is not None:
            return self.async_create_entry(
                title="Govee Management",
                data={CONF_API_KEY: self._api_key},
                options={
                    CONF_DEVICES: user_input[CONF_DEVICES],
                    # Everything offered here counts as seen, so unticking a
                    # device now does not come back as a "new device" repair.
                    CONF_KNOWN_DEVICES: [
                        device["device"]
                        for device in selectable_devices(self._raw_devices)
                    ],
                    CONF_POLL_INTERVAL: DEFAULT_POLL_INTERVAL,
                },
            )

        # Everything is ticked by default; unticking is the deliberate act.
        all_ids = [device["device"] for device in selectable_devices(self._raw_devices)]
        return self.async_show_form(
            step_id="devices",
            data_schema=_device_selector(self._raw_devices, all_ids),
            description_placeholders={"count": str(len(all_ids))},
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Govee rejected the stored key; ask for a new one."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate and store a replacement key on the existing entry."""
        errors: dict[str, str] = {}

        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()
            _, errors = await self._async_validate(api_key)

            if not errors:
                await self.async_set_unique_id(_account_id(api_key))
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(), data_updates={CONF_API_KEY: api_key}
                )

        return self.async_show_form(
            step_id="reauth_confirm", data_schema=STEP_API_KEY_SCHEMA, errors=errors
        )

    async def _async_validate(
        self, api_key: str
    ) -> tuple[list[dict[str, Any]], dict[str, str]]:
        """Call GET /user/devices to check the key. Never log the key."""
        api = GoveeApi(async_get_clientsession(self.hass), api_key)
        try:
            devices = await api.async_get_devices()
        except GoveeAuthError:
            return [], {"base": "invalid_auth"}
        except GoveeRateLimitError:
            return [], {"base": "rate_limited"}
        except GoveeApiError as err:
            _LOGGER.debug("Govee key validation failed: %s", err)
            return [], {"base": "cannot_connect"}
        return devices, {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Device selection and poll interval, after setup."""
        return GoveeOptionsFlow()


class GoveeOptionsFlow(OptionsFlow):
    """Change which devices are tracked, and how often they are polled."""

    def __init__(self) -> None:
        """Cache the inventory fetched for the device step."""
        self._raw_devices: list[dict[str, Any]] = []

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer the two things worth changing."""
        return self.async_show_menu(step_id="init", menu_options=["devices", "polling"])

    async def async_step_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Re-read the account inventory so new devices can be picked up.

        This is the path for a sensor added in the Govee app after setup: the
        list is fetched live here, so it shows up without recreating the entry.
        """
        if user_input is not None:
            return self._async_save(
                {
                    CONF_DEVICES: user_input[CONF_DEVICES],
                    CONF_KNOWN_DEVICES: [
                        device["device"]
                        for device in selectable_devices(self._raw_devices)
                    ],
                }
            )

        api = GoveeApi(
            async_get_clientsession(self.hass), self.config_entry.data[CONF_API_KEY]
        )
        try:
            self._raw_devices = await api.async_get_devices()
        except GoveeAuthError:
            return self.async_abort(reason="invalid_auth")
        except GoveeApiError as err:
            _LOGGER.debug("Could not refresh the Govee device list: %s", err)
            return self.async_abort(reason="cannot_connect")

        available = selectable_devices(self._raw_devices)
        if not available:
            return self.async_abort(reason="no_devices")

        # An entry created before device selection existed tracks everything.
        selected = self.config_entry.options.get(
            CONF_DEVICES, [device["device"] for device in available]
        )
        known = set(self.config_entry.options.get(CONF_KNOWN_DEVICES, selected))
        new = [
            device_label(device)
            for device in available
            if device["device"] not in known
        ]

        return self.async_show_form(
            step_id="devices",
            data_schema=_device_selector(self._raw_devices, list(selected)),
            description_placeholders={
                "count": str(len(available)),
                "new": ", ".join(new) if new else "none",
            },
        )

    async def async_step_polling(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user slow the REST poll down (never past the rate limit)."""
        if user_input is not None:
            return self._async_save(
                {CONF_POLL_INTERVAL: int(user_input[CONF_POLL_INTERVAL])}
            )

        current = self.config_entry.options.get(
            CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL
        )
        schema = vol.Schema(
            {
                vol.Required(CONF_POLL_INTERVAL, default=current): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_POLL_INTERVAL,
                        max=3600,
                        step=10,
                        unit_of_measurement="seconds",
                        mode=NumberSelectorMode.BOX,
                    )
                )
            }
        )
        return self.async_show_form(step_id="polling", data_schema=schema)

    @callback
    def _async_save(self, updates: dict[str, Any]) -> ConfigFlowResult:
        """Merge one step's answer into the options, keeping the other's."""
        return self.async_create_entry(data={**self.config_entry.options, **updates})


def _account_id(api_key: str) -> str:
    """Stable, non-reversible identifier for an API key."""
    return sha256(api_key.encode()).hexdigest()[:16]
