"""Govee OpenAPI MQTT push listener.

Govee pushes an event message for every device that declares
``devices.capabilities.event`` - the H5059 leak detectors' LEAKED / UN_LEAKED.
There is no REST equivalent: ``/device/state`` on an H5059 returns only
``online``, so push is the *only* way to see a leak.

Connection shape (proven in ``tools/govee_mqtt.py``):

    host   mqtts://mqtt.openapi.govee.com:8883
    auth   the API key as BOTH username and password
    topic  GA/<api-key>

The key is the topic name, so the topic must never be logged verbatim.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import json
import logging
from typing import Any

import aiomqtt

from homeassistant.core import HomeAssistant, callback
from homeassistant.util.ssl import get_default_context

from .const import (
    MQTT_HOST,
    MQTT_KEEPALIVE,
    MQTT_PORT,
    MQTT_RECONNECT_MAX,
    MQTT_RECONNECT_MIN,
    MQTT_TOPIC_TEMPLATE,
)

_LOGGER = logging.getLogger(__name__)


class GoveePushClient:
    """Keep a reconnecting subscription to Govee's event stream."""

    def __init__(
        self,
        hass: HomeAssistant,
        api_key: str,
        on_event: Callable[[dict[str, Any]], None],
    ) -> None:
        """Store what the listener task needs; nothing connects yet."""
        self._hass = hass
        self._api_key = api_key
        self._on_event = on_event
        self._task: asyncio.Task[None] | None = None
        self._connected = False

    @property
    def connected(self) -> bool:
        """Whether the broker connection is currently up."""
        return self._connected

    @callback
    def async_start(self) -> None:
        """Launch the listener task."""
        if self._task is None:
            self._task = self._hass.async_create_background_task(
                self._async_run(), "govee_management_mqtt", eager_start=True
            )

    async def async_stop(self) -> None:
        """Cancel the listener task and wait for it to unwind."""
        if self._task is None:
            return
        task, self._task = self._task, None
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        self._connected = False

    async def _async_run(self) -> None:
        """Connect, subscribe, and stay subscribed across failures."""
        topic = MQTT_TOPIC_TEMPLATE.format(key=self._api_key)
        ssl_context = get_default_context()
        delay = MQTT_RECONNECT_MIN

        while True:
            try:
                async with aiomqtt.Client(
                    hostname=MQTT_HOST,
                    port=MQTT_PORT,
                    username=self._api_key,
                    password=self._api_key,
                    tls_context=ssl_context,
                    keepalive=MQTT_KEEPALIVE,
                ) as client:
                    await client.subscribe(topic, qos=0)
                    self._connected = True
                    delay = MQTT_RECONNECT_MIN
                    _LOGGER.debug("Subscribed to Govee push topic GA/<api-key>")
                    async for message in client.messages:
                        self._handle_message(message.payload)
            except asyncio.CancelledError:
                self._connected = False
                raise
            except aiomqtt.MqttError as err:
                self._connected = False
                # Never let the message carry the topic: it contains the key.
                _LOGGER.warning(
                    "Govee push connection lost (%s); retrying in %ss", err, delay
                )
            except Exception:  # noqa: BLE001 - a bug here must not kill the task
                self._connected = False
                _LOGGER.exception("Unexpected error in the Govee push listener")

            await asyncio.sleep(delay)
            delay = min(delay * 2, MQTT_RECONNECT_MAX)

    def _handle_message(self, raw: Any) -> None:
        """Decode one push payload and hand it to the coordinator."""
        try:
            payload = json.loads(
                raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
            )
        except (ValueError, UnicodeDecodeError):
            _LOGGER.warning("Ignoring non-JSON Govee push payload")
            return

        if not isinstance(payload, dict):
            _LOGGER.warning("Ignoring Govee push payload that is not an object")
            return

        try:
            self._on_event(payload)
        except Exception:  # noqa: BLE001 - one bad event must not end the stream
            _LOGGER.exception("Error handling a Govee push event")
