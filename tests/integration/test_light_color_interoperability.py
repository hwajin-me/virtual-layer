"""Run mixed RGB/CCT groups through the real HA light service conversions."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.components.light.const import DATA_COMPONENT
from homeassistant.setup import async_setup_component

from custom_components.virtual_layer.light import LIGHT_SCHEMA, VirtualLight


class RecordingBulb(LightEntity):
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

    async def async_turn_on(self, **kwargs):
        self.calls.append(kwargs)
        self._attr_is_on = True
        for name, value in kwargs.items():
            setattr(self, f"_attr_{name}", value)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        self._attr_is_on = False
        self.async_write_ha_state()


@pytest.mark.parametrize("mode", [ColorMode.HS, ColorMode.RGB, ColorMode.RGBW,
                                  ColorMode.RGBWW, ColorMode.XY])
@pytest.mark.parametrize("kelvin", [2000, 4000, 6500])
async def test_kelvin_group_converts_to_native_color_and_accepts_response(hass, mode, kelvin):
    assert await async_setup_component(hass, "light", {})
    component = hass.data[DATA_COMPONENT]
    rgb = RecordingBulb("Interop RGB", mode)
    cct = RecordingBulb("Interop CCT", ColorMode.COLOR_TEMP)
    await component.async_add_entities([rgb, cct])
    group = VirtualLight(LIGHT_SCHEMA({
        "name": "Interop Group", "entity_id": "light.interop_group",
        "initial_value": "off", "matter_light_type": "color_temperature",
        "source_entities": [rgb.entity_id, cct.entity_id],
        "light_response_delay": 0, "persistent": False,
    }), False)
    await component.async_add_entities([group])
    await hass.services.async_call("light", "turn_on", {
        "entity_id": group.entity_id, "brightness": 123, "color_temp_kelvin": kelvin,
    }, blocking=True)
    await hass.async_block_till_done()
    assert cct.calls[-1]["color_temp_kelvin"] == kelvin
    assert "color_temp_kelvin" not in rgb.calls[-1]
    assert f"{mode.value}_color" in rgb.calls[-1]
    assert group.supported_color_modes == {ColorMode.COLOR_TEMP}
    assert group.brightness == 123
    target = {"brightness": 123, "color_temp_kelvin": kelvin}
    assert group._group_source_matches(rgb.entity_id, "turn_on", target)
    assert group._group_source_matches(cct.entity_id, "turn_on", target)
    with patch("custom_components.virtual_layer.light.async_update_entity", new_callable=AsyncMock) as update:
        await group._async_refresh_group(group._group_revision, 2)
        update.assert_not_awaited()
    assert len(rgb.calls) == len(cct.calls) == 1
    # HA converts a direct RGB request to the CT-only virtual contract before
    # the group forwards it. This is a white approximation, not RGB gamut.
    await hass.services.async_call("light", "turn_on", {
        "entity_id": group.entity_id, "rgb_color": [255, 180, 100], "brightness": 254,
    }, blocking=True)
    assert group.color_mode == ColorMode.COLOR_TEMP
    assert "color_temp_kelvin" in cct.calls[-1]
    target = group._group_target[2]
    assert "color_temp" not in target
    assert group._group_source_matches(rgb.entity_id, "turn_on", target)
    assert group._group_source_matches(cct.entity_id, "turn_on", target)
    # HA's level-zero rule must switch every member off, including RGBWW.
    await hass.services.async_call("light", "turn_on", {
        "entity_id": group.entity_id, "brightness": 0,
    }, blocking=True)
    assert not group.is_on and not rgb.is_on and not cct.is_on
