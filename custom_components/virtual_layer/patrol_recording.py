"""Temporarily control Frigate recordings through Home Assistant MQTT."""
import asyncio

from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.importlib import async_import_module


class PatrolRecording:
    """Capture a known state before sending a non-retained recording command."""

    def __init__(self, hass, topic):
        self.hass = hass
        self.topic = topic
        self.previous = None

    async def apply(self, value):
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
                if previous == value.upper():
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
        mqtt = await async_import_module(self.hass, "homeassistant.components.mqtt")
        await mqtt.async_publish(self.hass, self.topic + "/set", self.previous, qos=1, retain=False)
        self.previous = None
