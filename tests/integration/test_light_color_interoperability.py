"""Run mixed RGB/CCT groups through the real HA light service conversions."""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.components.light import ColorMode, LightEntity, LightEntityFeature
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


async def test_single_source_ignores_stale_off_event_while_turning_on(hass):
    """A source's pre-command state must not undo an optimistic turn-on."""
    assert await async_setup_component(hass, "light", {})
    component = hass.data[DATA_COMPONENT]

    class SlowBulb(RecordingBulb):
        async def async_turn_on(self, **kwargs):
            # Some bridges publish their previous state before the device has
            # acknowledged the command.  The later on report is deliberately
            # omitted here to exercise the reconciliation window.
            self.hass.states.async_set(self.entity_id, "off")

    bulb = SlowBulb("Slow bulb", ColorMode.BRIGHTNESS)
    await component.async_add_entities([bulb])
    light = VirtualLight(LIGHT_SCHEMA({
        "name": "Single-source light", "entity_id": "light.single_source",
        "initial_value": "off", "matter_light_type": "dimmable",
        "source_entities": [bulb.entity_id],
        "native_templates": {"is_on": "{{ is_state(" + repr(bulb.entity_id) + ", 'on') }}"},
        "persistent": False,
    }), False)
    await component.async_add_entities([light])

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": light.entity_id}, blocking=True
    )
    await hass.async_block_till_done()

    assert light.is_on


@pytest.mark.parametrize("responds", [False, True])
@pytest.mark.parametrize("custom_off", [False, True])
async def test_single_slow_bulb_retries_then_resumes_source_state(hass, responds, custom_off):
    """Lost commands retry within a bounded window, without target flicker."""
    assert await async_setup_component(hass, "light", {})
    component = hass.data[DATA_COMPONENT]

    class DelayedBulb(RecordingBulb):
        async def async_turn_on(self, **kwargs):
            self.calls.append(kwargs)
            if responds and len(self.calls) > 1:
                self._attr_is_on = True
                self._attr_brightness = kwargs["brightness"]
                self.async_write_ha_state()

    bulb = DelayedBulb("Retry bulb", ColorMode.BRIGHTNESS)
    await component.async_add_entities([bulb])
    light = VirtualLight(LIGHT_SCHEMA({
        "name": "Retry light", "entity_id": "light.retry_virtual",
        "initial_value": "off", "matter_light_type": "dimmable",
        "source_entities": [bulb.entity_id], "persistent": False,
        "command_actions": {"turn_off": {"sequence": [], "optimistic": False}} if custom_off else {},
        "native_templates": {
            "is_on": "{{ is_state(" + repr(bulb.entity_id) + ", 'on') }}",
        },
    }), False)
    await component.async_add_entities([light])
    with patch("custom_components.virtual_layer.light.async_call_later", return_value=Mock()) as later, patch(
        "custom_components.virtual_layer.light.async_update_entity", new_callable=AsyncMock
    ):
        await light.async_turn_on(brightness=180, transition=5)
        assert light.is_on and light.brightness == 180
        assert later.call_args.args[1] == 7
        if custom_off:
            revision = light._group_revision
            await light.async_turn_off()
            assert not light.is_on
            await light._async_refresh_group(revision, 1)
            assert len(bulb.calls) == 1
            assert light._response_refresh_cancel is None
            return
        later.reset_mock()
        await light._async_refresh_group(light._group_revision, 1)
        await hass.async_block_till_done()
        assert light.is_on  # Stale off reports cannot overwrite the request.
        assert len(bulb.calls) == 2
        if responds:
            later.assert_not_called()  # Acknowledged retries need no extra transition wait.
        await light._async_refresh_group(light._group_revision, 0)
        assert light.is_on == responds
        assert len(bulb.calls) == 2
        bulb._attr_is_on = False
        bulb.async_write_ha_state()
        await hass.async_block_till_done()
        assert not light.is_on


@pytest.mark.parametrize("command", ["turn_on", "turn_off"])
@pytest.mark.parametrize("source_count", [1, 2])
async def test_transition_reaches_sources_through_ha_service(hass, command, source_count):
    """HA must retain transition before forwarding and scheduling a retry."""
    assert await async_setup_component(hass, "light", {})
    component = hass.data[DATA_COMPONENT]
    bulbs = [RecordingBulb(f"Transition {i}", ColorMode.BRIGHTNESS) for i in range(source_count)]
    for bulb in bulbs:
        bulb._attr_supported_features = LightEntityFeature.TRANSITION
        bulb.async_turn_on = AsyncMock()
        bulb.async_turn_off = AsyncMock()
    await component.async_add_entities(bulbs)
    light = VirtualLight(LIGHT_SCHEMA({
        "name": "Transition virtual", "entity_id": "light.transition_virtual",
        "initial_value": "on", "matter_light_type": "dimmable",
        "source_entities": [bulb.entity_id for bulb in bulbs], "persistent": False,
    }), False)
    await component.async_add_entities([light])
    with patch("custom_components.virtual_layer.light.async_call_later", return_value=Mock()) as later:
        await hass.services.async_call("light", command, {
            "entity_id": light.entity_id, "transition": 5,
        }, blocking=True)
        for bulb in bulbs:
            getattr(bulb, f"async_{command}").assert_awaited_once_with(transition=5)
        assert later.call_args.args[1] == 7
        light._cancel_group_refresh()


async def test_delayed_brightness_steps_and_late_on_report_after_off(hass):
    assert await async_setup_component(hass, "light", {})
    component = hass.data[DATA_COMPONENT]
    bulb = RecordingBulb("Delayed brightness", ColorMode.BRIGHTNESS)
    bulb.async_turn_on = AsyncMock()
    bulb.async_turn_off = AsyncMock()
    await component.async_add_entities([bulb])
    light = VirtualLight(LIGHT_SCHEMA({
        "name": "Delayed steps", "entity_id": "light.delayed_steps",
        "initial_value": "off", "matter_light_type": "dimmable",
        "source_entities": [bulb.entity_id], "persistent": False,
        "native_templates": {
            "is_on": "{{ is_state(" + repr(bulb.entity_id) + ", 'on') }}",
            "brightness": "{{ state_attr(" + repr(bulb.entity_id) + ", 'brightness') }}",
        },
    }), False)
    await component.async_add_entities([light])
    with patch("custom_components.virtual_layer.light.async_call_later", return_value=Mock()), patch(
        "custom_components.virtual_layer.light.async_update_entity", new_callable=AsyncMock
    ):
        for payload, expected in [({"brightness": 100}, 100), ({"brightness_step": 30}, 130),
                                  ({"brightness_step": 30}, 160)]:
            await hass.services.async_call("light", "turn_on", {
                "entity_id": light.entity_id, **payload,
            }, blocking=True)
            bulb._attr_is_on = True
            bulb._attr_brightness = 80  # An old response arrives after each command.
            bulb.async_write_ha_state()
            await hass.async_block_till_done()
            assert light.is_on and light.brightness == expected
        revision = light._group_revision
        await hass.services.async_call("light", "turn_on", {
            "entity_id": light.entity_id, "brightness": 0,
        }, blocking=True)
        bulb.async_write_ha_state()
        await hass.async_block_till_done()
        assert not light.is_on
        await light._async_refresh_group(revision, 2)
        assert bulb.async_turn_on.await_count == 3
        await light._async_refresh_group(light._group_revision, 1)
        assert bulb.async_turn_off.await_count == 2
        assert not light.is_on
        light._cancel_group_refresh()
