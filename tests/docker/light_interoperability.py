"""Standalone official-HA-container test: real services and timers, simulated bulbs."""

import asyncio
import json
import logging
import os
from pathlib import Path
import tempfile

from homeassistant import bootstrap, loader
from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.components.light.const import DATA_COMPONENT
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.virtual_layer.light import LIGHT_SCHEMA, VirtualLight


class Bulb(LightEntity):
    _attr_should_poll = False
    _attr_is_on = False
    _attr_brightness = 128
    _attr_min_color_temp_kelvin = 2000
    _attr_max_color_temp_kelvin = 6500

    def __init__(self, name, mode):
        self._attr_name = name
        self._attr_supported_color_modes = {mode}
        self._attr_color_mode = mode
        self.calls = []
        self.drop_remaining = 0
        self.updates = 0
        self.responded = asyncio.Event()

    async def async_turn_on(self, **kwargs):
        self.calls.append(dict(kwargs))
        if self.drop_remaining:
            self.drop_remaining -= 1
            return
        self._attr_is_on = True
        for name, value in kwargs.items():
            setattr(self, f"_attr_{name}", value)
        self.async_write_ha_state()
        self.responded.set()

    async def async_turn_off(self, **kwargs):
        self._attr_is_on = False
        self.async_write_ha_state()

    async def async_update(self):
        self.updates += 1


class Errors(logging.Handler):
    def __init__(self):
        super().__init__(logging.ERROR)
        self.errors = []

    def emit(self, record):
        self.errors.append(record.getMessage())


async def add_group(hass, suffix, mode, delay=0):
    component = hass.data[DATA_COMPONENT]
    rgb = Bulb(f"RGB {suffix}", mode)
    cct = Bulb(f"CCT {suffix}", ColorMode.COLOR_TEMP)
    await component.async_add_entities([rgb, cct])
    group = VirtualLight(LIGHT_SCHEMA({
        "name": f"Group {suffix}", "entity_id": f"light.group_{suffix}",
        "initial_value": "off", "matter_light_type": "color_temperature",
        "source_entities": [rgb.entity_id, cct.entity_id],
        "light_response_delay": delay, "light_response_retries": 2,
        "persistent": False,
    }), False)
    await component.async_add_entities([group])
    return rgb, cct, group


async def main():
    errors = Errors()
    logging.getLogger().addHandler(errors)
    with tempfile.TemporaryDirectory(prefix="virtual-light-docker-") as directory:
        custom = Path(directory) / "custom_components"
        custom.mkdir()
        os.symlink("/config/custom_components/virtual_layer", custom / "virtual_layer")
        hass = HomeAssistant(directory)
        loader.async_setup(hass)
        results = []
        try:
            assert await bootstrap.async_from_config_dict({}, hass) is hass
            assert await async_setup_component(hass, "light", {})
            await hass.async_start()
            for mode in (ColorMode.HS, ColorMode.RGB, ColorMode.RGBW, ColorMode.RGBWW, ColorMode.XY):
                for kelvin in (2000, 4000, 6500):
                    rgb, cct, group = await add_group(hass, f"{mode.value}_{kelvin}", mode)
                    target = {"brightness": 123, "color_temp_kelvin": kelvin}
                    await hass.services.async_call("light", "turn_on", {
                        "entity_id": group.entity_id, **target,
                    }, blocking=True)
                    assert cct.calls[-1]["color_temp_kelvin"] == kelvin
                    assert "color_temp_kelvin" not in rgb.calls[-1]
                    assert f"{mode.value}_color" in rgb.calls[-1]
                    assert group._group_source_matches(rgb.entity_id, "turn_on", target)
                    assert group._group_source_matches(cct.entity_id, "turn_on", target)
                    assert group.brightness == 123 and group.color_mode == ColorMode.COLOR_TEMP
                    await hass.services.async_call("light", "turn_on", {
                        "entity_id": group.entity_id, "rgb_color": [255, 180, 100],
                        "brightness": 254,
                    }, blocking=True)
                    target = group._group_target[2]
                    assert "color_temp" not in target
                    assert group._group_source_matches(rgb.entity_id, "turn_on", target)
                    assert group._group_source_matches(cct.entity_id, "turn_on", target)
                    await hass.services.async_call("light", "turn_on", {
                        "entity_id": group.entity_id, "brightness": 0,
                    }, blocking=True)
                    assert not group.is_on and not rgb.is_on and not cct.is_on
                    results.append({"mode": mode.value, "kelvin": kelvin, "passed": True})

            # One bulb silently ignores the initial request. Let real HA
            # timers, entity updates and light services perform the retry.
            slow, fast, group = await add_group(hass, "delayed", ColorMode.RGB, delay=1)
            slow.drop_remaining = 1
            await hass.services.async_call("light", "turn_on", {
                "entity_id": group.entity_id, "brightness": 150, "color_temp_kelvin": 4000,
            }, blocking=True)
            assert group.brightness == 150 and group.is_on
            async with asyncio.timeout(8):
                await slow.responded.wait()
            await asyncio.sleep(1.2)
            assert len(slow.calls) == 2 and len(fast.calls) == 1
            assert slow.updates >= 1
            assert group.brightness == 150 and group.color_temp_kelvin == 4000
            await hass.data[DATA_COMPONENT].async_remove_entity(group.entity_id)
            assert group._response_refresh_cancel is None
            assert group._group_refresh_task is None or group._group_refresh_task.done()
        finally:
            await hass.async_stop()
            logging.getLogger().removeHandler(errors)
        assert not errors.errors, errors.errors
        print(json.dumps({
            "home_assistant": HA_VERSION, "matrix": results,
            "delayed_bulb_retry": "passed", "healthy_bulb_not_resent": "passed",
            "timer_cleanup": "passed", "error_logs": errors.errors,
            "source_devices": "simulated LightEntity instances",
        }, indent=2))


asyncio.run(main())
