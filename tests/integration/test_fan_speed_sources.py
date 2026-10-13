"""Real platform lifecycle coverage for a fan with a separate speed source."""

import pytest
import json
from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.virtual_layer.config_flow import (
    _build_entity_config, _reference_entity_defaults,
    _entity_schema, _flatten_entity_form_sections,
    _auto_helper_profile, _entity_form_defaults, _reference_edit_defaults,
)


@pytest.mark.parametrize("multiple", [False, True])
@pytest.mark.parametrize("legacy", [False, True, "mapping"])
@pytest.mark.parametrize("policy", ["automatic", "keep_current", "force_helper"])
async def test_wind_alias_services_with_native_fans_survive_reload(hass, multiple, legacy, policy):
    calls = []

    class SourceFan(FanEntity):
        _attr_is_on = True
        _attr_supported_features = (FanEntityFeature.PRESET_MODE |
                                    FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF)

        def __init__(self, object_id, modes):
            self.entity_id = f"fan.{object_id}"
            self._attr_name = object_id
            self._attr_unique_id = object_id
            self._attr_preset_modes = modes
            self._attr_preset_mode = modes[0]

        async def async_set_preset_mode(self, preset_mode):
            assert preset_mode in self.preset_modes
            calls.append((self.entity_id, preset_mode))
            self._attr_preset_mode = preset_mode
            self.async_write_ha_state()

        async def async_turn_on(self, percentage=None, preset_mode=None, **kwargs):
            await self.async_set_preset_mode(preset_mode)

    assert await async_setup_component(hass, "fan", {})
    sources = [SourceFan("physical_direct", ["Direct Mode", "Natural Mode", "Sleep Mode"])]
    if multiple:
        sources.extend([
            SourceFan("physical_straight", ["Straight Wind", "Natural Wind", "Smart"]),
            SourceFan("physical_stepless", ["Straight Wind", "Natural Wind"]),
        ])
    await hass.data["fan"].async_add_entities(sources)
    ids = [source.entity_id for source in sources]
    defaults = _reference_entity_defaults(hass, ids)
    target = "fan.virtual_wind"
    defaults.update({"device_name": "Wind", "entity_name": "Virtual wind", "entity_id": target})
    device, config = _build_entity_config(_flatten_entity_form_sections(_entity_schema(defaults)({})))
    if legacy:
        config["command_actions"] = {
            command: [{"action": f"fan.{command}", "target": {"entity_id": ids},
                       "data": {"preset_mode": "{{ preset_mode }}"} if legacy == "mapping" else "{{ command_data }}"}]
            for command in ("set_preset_mode", "turn_on")
        }
    config["auto_helper"] = _auto_helper_profile(defaults)
    if legacy:
        config["auto_helper"]["command_actions_json"] = json.dumps(config["command_actions"])
    entry = MockConfigEntry(domain="virtual_layer", data={"group_name": "Wind"},
                            options={"devices": {device: [config]}})
    entry.add_to_hass(hass)
    assert await async_setup_component(hass, "virtual_layer", {})
    await hass.async_block_till_done()
    for reload in (False, True):
        if reload:
            current = _entity_form_defaults(device, config)
            if policy != "keep_current":
                current = _reference_edit_defaults(current, _reference_entity_defaults(hass, ids),
                    config["auto_helper"], force_template_helper=policy == "force_helper")
            _, config = _build_entity_config(_flatten_entity_form_sections(_entity_schema(current)({})))
            config["entity_id"] = target
            config["auto_helper"] = _auto_helper_profile(current)
            if legacy == "mapping" and policy == "keep_current":
                from custom_components.virtual_layer.config_flow import _command_action_mapping
                assert _command_action_mapping(config["auto_helper"]["command_actions_json"]) == config["command_actions"]
            hass.config_entries.async_update_entry(entry, options={"devices": {device: [config]}})
            await hass.async_block_till_done()
            assert await hass.config_entries.async_reload(entry.entry_id)
            await hass.async_block_till_done()
        for service in ("set_preset_mode", "turn_on"):
            calls.clear()
            await hass.services.async_call("fan", service, {
                "entity_id": target, "preset_mode": "Straight Wind",
            }, blocking=True)
            await hass.async_block_till_done()
            assert calls == [(ids[0], "Direct Mode")] + (
                [(ids[1], "Straight Wind"), (ids[2], "Straight Wind")] if multiple else [])
            assert hass.states.get(target).attributes["preset_mode"] == "Direct Mode"
            if multiple:
                for mode, expected in (
                    ("Natural Wind", [(ids[0], "Natural Mode"), (ids[1], "Natural Wind"), (ids[2], "Natural Wind")]),
                    ("Smart", [(ids[1], "Smart")]),
                    ("Sleep Mode", [(ids[0], "Sleep Mode")]),
                ):
                    calls.clear()
                    await hass.services.async_call("fan", service, {
                        "entity_id": target, "preset_mode": mode,
                    }, blocking=True)
                    await hass.async_block_till_done()
                    assert calls == expected
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize("number_domain", ["number", "input_number"])
@pytest.mark.parametrize("reverse_sources", [False, True])
async def test_number_speed_follows_events_in_every_preset_and_survives_reload(
    hass, number_domain, reverse_sources,
):
    fan_id = "fan.physical_purifier"
    number_id = f"{number_domain}.purifier_speed"
    target = "fan.virtual_purifier"
    fan_attrs = {"percentage": 35, "percentage_step": 1, "preset_modes": ["Auto", "Sleep", "Manual"],
                 "preset_mode": "Auto", "supported_features": 57}
    number_attrs = {"min": 0, "max": 100, "step": 1, "unit_of_measurement": "%"}
    hass.states.async_set(fan_id, "on", fan_attrs)
    hass.states.async_set(number_id, "10", number_attrs)
    sources = [fan_id, number_id]
    if reverse_sources:
        sources.reverse()
    defaults = _reference_entity_defaults(hass, sources)
    defaults.update({"device_name": "Purifier", "entity_name": "Virtual purifier", "entity_id": target})
    device, entity = _build_entity_config(_flatten_entity_form_sections(_entity_schema(defaults)({})))
    entry = MockConfigEntry(domain="virtual_layer", data={"group_name": "Purifiers"},
                            options={"devices": {device: [entity]}})
    entry.add_to_hass(hass)
    assert await async_setup_component(hass, "virtual_layer", {})
    await hass.async_block_till_done()

    def check(percentage, mode):
        state = hass.states.get(target)
        assert state is not None and state.state == "on"
        assert state.attributes.get("percentage") == percentage
        assert state.attributes.get("preset_mode") == mode

    check(10, "Auto")
    # Only the number changes here: fan attributes and preset stay untouched.
    hass.states.async_set(number_id, "80", number_attrs)
    await hass.async_block_till_done()
    check(80, "Auto")
    for mode, speed in [("Sleep", 20), ("Manual", 60), ("Auto", 40)]:
        fan_attrs["preset_mode"] = mode
        hass.states.async_set(fan_id, "on", fan_attrs)
        hass.states.async_set(number_id, str(speed), number_attrs)
        await hass.async_block_till_done()
        check(speed, mode)

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    check(40, "Auto")
    hass.states.async_set(number_id, "70", number_attrs)
    await hass.async_block_till_done()
    check(70, "Auto")

    hass.states.async_set(number_id, "unavailable", number_attrs)
    await hass.async_block_till_done()
    check(35, "Auto")
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    check(35, "Auto")
    fan_attrs["percentage"] = None
    hass.states.async_set(fan_id, "on", fan_attrs)
    await hass.async_block_till_done()
    check(None, "Auto")
    hass.states.async_set(number_id, "90", number_attrs)
    await hass.async_block_till_done()
    check(90, "Auto")

    calls = []

    async def set_source_preset(call):
        calls.append(("preset", call.data["preset_mode"]))
        assert call.data["entity_id"] == [fan_id]
        fan_attrs["preset_mode"] = call.data["preset_mode"]
        hass.states.async_set(fan_id, "on", fan_attrs)

    async def set_source_speed(call):
        calls.append(("speed", call.data["value"]))
        assert call.data["entity_id"] == [number_id]
        hass.states.async_set(number_id, str(call.data["value"]), number_attrs)

    hass.services.async_register("fan", "set_preset_mode", set_source_preset)
    hass.services.async_register(number_domain, "set_value", set_source_speed)
    await hass.services.async_call("fan", "set_percentage", {
        "entity_id": target, "percentage": 70,
    }, blocking=True)
    await hass.async_block_till_done()
    assert calls == [("preset", "Manual"), ("speed", 70)]
    check(70, "Manual")

    hass.states.async_set(fan_id, "off", fan_attrs)
    await hass.async_block_till_done()
    assert hass.states.get(target).state == "off"
    assert hass.states.get(target).attributes["percentage"] == 0
    hass.states.async_set(number_id, "99", number_attrs)
    await hass.async_block_till_done()
    assert hass.states.get(target).state == "off"
    assert hass.states.get(target).attributes["percentage"] == 0
    assert await hass.config_entries.async_unload(entry.entry_id)
