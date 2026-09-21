"""Temporarily control Frigate recordings through Home Assistant MQTT."""
import asyncio
from importlib import import_module

from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError


async def async_import_module(hass, name):
    """Load optional MQTT off the loop without requiring newer HA import helpers."""
    return await hass.async_add_executor_job(import_module, name)


class PatrolRecording:
    """Capture a known state before sending a non-retained recording command."""

    def __init__(self, hass, topic):
        self.hass = hass
        self.topic = topic
        self.previous = None

    async def apply(self, value, *, force=False):
        mqtt = await async_import_module(self.hass, "homeassistant.components.mqtt")
        result = asyncio.get_running_loop().create_future()

        @callback
        def received(message):
            if message.payload in {"ON", "OFF"} and not result.done():
                result.set_result(message.payload)

        unsubscribe = await mqtt.async_subscribe(self.hass, self.topic + "/state", received)
        try:
            async with asyncio.timeout(10):
                previous = await result
                if self.previous is None:
                    self.previous = previous
                if previous == value.upper() and not force:
                    return
                result = asyncio.get_running_loop().create_future()
                await mqtt.async_publish(self.hass, self.topic + "/set", value.upper(), qos=1, retain=False)
                while await result != value.upper():
                    result = asyncio.get_running_loop().create_future()
        except TimeoutError as err:
            raise HomeAssistantError("Frigate MQTT recording state was not confirmed") from err
        finally:
            unsubscribe()

    async def restore(self):
        if self.previous is None:
            return
        # Confirm motion is restored before re-enabling dependent detection.
        await self.apply(self.previous.lower(), force=True)
        self.previous = None
