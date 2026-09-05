"""Thin async client for Govee's documented OpenAPI REST endpoints.

Mirrors the request shapes proven in ``tools/govee_api.py``. The API key is
never included in an exception message: it doubles as the MQTT topic name.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import uuid4

from aiohttp import ClientError, ClientResponseError, ClientSession

from .const import API_KEY_HEADER, DEVICES_URL, REQUEST_TIMEOUT, STATE_URL

_LOGGER = logging.getLogger(__name__)


class GoveeApiError(Exception):
    """A call to the Govee API failed."""


class GoveeAuthError(GoveeApiError):
    """The API key was rejected."""


class GoveeRateLimitError(GoveeApiError):
    """Govee is rate limiting us."""


class GoveeApi:
    """Talk to https://openapi.api.govee.com."""

    def __init__(self, session: ClientSession, api_key: str) -> None:
        """Store the shared aiohttp session and the account's API key."""
        self._session = session
        self._api_key = api_key

    @property
    def api_key(self) -> str:
        """The account's API key (also the MQTT credential and topic)."""
        return self._api_key

    async def _request(self, url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """One REST call. GET when there is no payload, POST when there is."""
        headers = {
            API_KEY_HEADER: self._api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                if payload is None:
                    response = await self._session.get(url, headers=headers)
                else:
                    response = await self._session.post(url, headers=headers, json=payload)
                async with response:
                    if response.status in (401, 403):
                        raise GoveeAuthError("Govee rejected the API key")
                    if response.status == 429:
                        raise GoveeRateLimitError("Govee is rate limiting this key")
                    response.raise_for_status()
                    data: dict[str, Any] = await response.json(content_type=None)
        except TimeoutError as err:
            raise GoveeApiError(f"Timeout talking to {_endpoint(url)}") from err
        except ClientResponseError as err:
            raise GoveeApiError(f"HTTP {err.status} from {_endpoint(url)}") from err
        except ClientError as err:
            raise GoveeApiError(f"Network error reaching {_endpoint(url)}: {err}") from err

        # Govee returns HTTP 200 with a non-zero body code for some failures.
        code = data.get("code")
        if code not in (None, 200, 0):
            message = data.get("message") or "unknown error"
            if code in (401, 403):
                raise GoveeAuthError(f"Govee rejected the API key: {message}")
            if code == 429:
                raise GoveeRateLimitError(f"Rate limited: {message}")
            raise GoveeApiError(f"Govee API code {code}: {message}")

        return data

    async def async_get_devices(self) -> list[dict[str, Any]]:
        """GET /user/devices - the account inventory."""
        data = await self._request(DEVICES_URL)
        devices = data.get("data") or []
        if not isinstance(devices, list):
            raise GoveeApiError("Unexpected device list payload")
        return devices

    async def async_get_state(self, sku: str, device: str) -> dict[str, Any]:
        """POST /device/state - flattened to ``{instance: value}``."""
        data = await self._request(
            STATE_URL,
            {"requestId": str(uuid4()), "payload": {"sku": sku, "device": device}},
        )
        return parse_capabilities((data.get("payload") or {}).get("capabilities"))


def parse_capabilities(capabilities: Any) -> dict[str, Any]:
    """Flatten a ``capabilities`` array into ``{instance: value}``.

    Event capabilities return ``state`` as a *list* of dicts rather than a
    dict, so both shapes have to be handled - assuming a dict raises
    ``AttributeError: 'list' object has no attribute 'get'``. For a list the
    most recent entry wins and the whole entry is kept, since leak events
    carry ``probesState`` alongside ``value``.
    """
    result: dict[str, Any] = {}
    if not isinstance(capabilities, list):
        return result

    for capability in capabilities:
        if not isinstance(capability, dict):
            continue
        instance = capability.get("instance")
        if not instance:
            continue
        state = capability.get("state")
        if isinstance(state, list):
            entries = [entry for entry in state if isinstance(entry, dict)]
            if entries:
                result[instance] = entries[-1]
        elif isinstance(state, dict):
            result[instance] = state.get("value")

    return result


def device_instances(raw_device: dict[str, Any]) -> set[str]:
    """Capability instances a raw ``/user/devices`` entry declares."""
    return {
        capability["instance"]
        for capability in raw_device.get("capabilities") or []
        if isinstance(capability, dict) and capability.get("instance")
    }


def _endpoint(url: str) -> str:
    """Last path segment of a URL, for log lines that must not carry the key."""
    return url.rsplit("/", 1)[-1]
