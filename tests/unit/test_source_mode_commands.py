"""Mode commands must respect each source's own live choice catalog."""

from unittest.mock import Mock
import json

import pytest

from custom_components.virtual_layer.config_flow import _source_command_actions
from custom_components.virtual_layer.entity import VirtualEntity
from custom_components.virtual_layer.climate import CLIMATE_SCHEMA, VirtualClimate
from custom_components.virtual_layer.humidifier import HUMIDIFIER_SCHEMA, VirtualHumidifier
from custom_components.virtual_layer.generic import ENTITY_SCHEMA, VirtualMediaPlayer, VirtualSelect
from custom_components.virtual_layer.generic import VirtualWaterHeater
from custom_components.virtual_layer.vacuum import VACUUM_SCHEMA, VirtualVacuum


MODE_CASES = [
    ("climate", "set_hvac_mode", "hvac_mode", "hvac_modes"),
    ("climate", "set_preset_mode", "preset_mode", "preset_modes"),
    ("climate", "set_fan_mode", "fan_mode", "fan_modes"),
    ("climate", "set_swing_mode", "swing_mode", "swing_modes"),
    ("climate", "set_swing_horizontal_mode", "swing_horizontal_mode", "swing_horizontal_modes"),
    ("humidifier", "set_mode", "mode", "available_modes"),
    ("media_player", "select_source", "source", "source_list"),
    ("media_player", "select_sound_mode", "sound_mode", "sound_mode_list"),
    ("vacuum", "set_fan_speed", "fan_speed", "fan_speed_list"),
    ("water_heater", "set_operation_mode", "operation_mode", "operation_list"),
    ("select", "select_option", "option", "options"),
]


@pytest.mark.parametrize("domain, command, field, catalog", MODE_CASES)
async def test_helpers_reach_a_source_that_advertises_modes_later(hass, domain, command, field, catalog):
    sources = [f"{domain}.first", f"{domain}.late"]
    hass.states.async_set(sources[0], "on", {catalog: ["first"], "supported_features": (1 << 31) - 1})
    hass.states.async_set(sources[1], "off")
    actions = _source_command_actions(domain, sources, [hass.states.get(s) for s in sources])
    entity = VirtualEntity({"name": "Late", "source_entities": sources, "command_actions": actions}, domain, False)
    entity.hass = hass
    entity.entity_id = f"{domain}.late_composite"
    calls = []

    async def capture(call):
        calls.extend(call.data["entity_id"])

    async def method(self, **kwargs):
        pass

    hass.services.async_register(domain, command, capture)
    hass.states.async_set(sources[1], "on", {catalog: ["late"], "supported_features": (1 << 31) - 1})
    await entity._async_run_command_action(command, method, (), {field: "late"})
    assert calls == [sources[1]]


@pytest.mark.parametrize("kind", ["climate", "humidifier", "media_player", "select"])
@pytest.mark.parametrize("label", ["None", "none", "False", "0"])
def test_native_choice_templates_preserve_opaque_names_and_actual_none(hass, kind, label):
    factories = {
        "climate": (VirtualClimate, CLIMATE_SCHEMA, {"preset_modes": [label]}, "preset_mode"),
        "humidifier": (VirtualHumidifier, HUMIDIFIER_SCHEMA, {"modes": [label]}, "mode"),
        "media_player": (VirtualMediaPlayer, ENTITY_SCHEMA, {"source_list": [label]}, "source"),
        "select": (VirtualSelect, ENTITY_SCHEMA, {"options": [label]}, "current_option"),
    }
    factory, schema, options, field = factories[kind]
    entity = factory(schema({"name": "Opaque", **options}), False)
    entity.hass = hass
    entity._create_state(entity._config)
    for template in (
        "{{ " + repr(label) + " }}",
        "{% if true %}{{ " + repr(label) + " }}{% else %}{{ none }}{% endif %}",
        "{% macro label() %}{{ " + repr(label) + " }}{% endmacro %}{{ label() }}",
        "{% set label %}{{ " + repr(label) + " }}{% endset %}{{ label }}",
    ):
        value = entity._render_native_template(field, template)
        assert value == label
        entity._apply_native_template_value(field, value)
        entity._native_templates_applied()
        assert getattr(entity, field) == label
    entity._apply_native_template_value(field, entity._render_native_template(field, "{{ none }}"))
    entity._native_templates_applied()
    assert getattr(entity, field) is None


async def test_vacuum_cleared_known_speed_list_rejects_commands(hass):
    entity = VirtualVacuum(VACUUM_SCHEMA({"name": "Vacuum", "fan_speed_list": ["turbo"]}), False)
    entity.hass = hass
    entity._create_state(entity._config)
    entity._apply_native_template_value("fan_speed_list", [])
    entity._native_templates_applied()
    with pytest.raises(ValueError):
        await entity.async_set_fan_speed("turbo")


@pytest.mark.parametrize("domain, field, catalog", [
    ("climate", "hvac_mode", "hvac_modes"),
    ("water_heater", "operation_mode", "operation_list"),
])
@pytest.mark.parametrize("style", ["generated", "legacy", "proxy"])
async def test_temperature_with_mode_routes_the_complete_payload(hass, domain, field, catalog, style):
    sources = [f"{domain}.first", f"{domain}.second"]
    for source, modes in zip(sources, (["off", "heat"], ["off", "cool"])):
        hass.states.async_set(source, "on", {catalog: modes, "supported_features": (1 << 31) - 1})
    actions = _source_command_actions(domain, sources, [hass.states.get(s) for s in sources])
    if style == "legacy":
        actions = {"set_temperature": [{"action": f"{domain}.set_temperature",
            "target": {"entity_id": sources}, "data": "{{ command_data }}"}]}
    elif style == "proxy":
        actions = {}
    entity = VirtualEntity({"name": "Compound", "source_entities": sources, "command_actions": actions}, domain, False)
    entity.hass = hass
    entity.entity_id = f"{domain}.compound"
    calls = []

    async def receive(call):
        calls.append(dict(call.data))

    async def method(self, **kwargs):
        pass

    hass.services.async_register(domain, "set_temperature", receive)
    await entity._async_run_command_action("set_temperature", method, (), {field: "cool", "temperature": 30})
    assert calls == [{field: "cool", "temperature": 30, "entity_id": [sources[1]]}]
    calls.clear()
    await entity._async_run_command_action("set_temperature", method, (), {"temperature": 30})
    assert [source for data in calls for source in data["entity_id"]] == sources


@pytest.mark.parametrize("domain, field", [("climate", "hvac_mode"), ("water_heater", "operation_mode")])
async def test_compound_mode_validation_runs_before_custom_actions(hass, domain, field):
    action = {"set_temperature": [{"action": "test.temperature", "data": "{{ command_data }}"}]}
    if domain == "climate":
        entity = VirtualClimate(CLIMATE_SCHEMA({"name": "Compound", "command_actions": action}), False)
        temperature = 25
    else:
        entity = VirtualWaterHeater(ENTITY_SCHEMA({"name": "Compound", "operation_list": ["off", "Eco"],
                                                 "command_actions": action}), False)
        temperature = 50
    entity.hass = hass
    entity._create_state(entity._config)
    entity.async_write_ha_state = Mock()
    calls = []

    async def receive(call):
        calls.append(call.data)

    hass.services.async_register("test", "temperature", receive)
    with pytest.raises(ValueError):
        await entity.async_set_temperature(temperature=temperature, **{field: "missing"})
    assert calls == []
    valid = "heat" if domain == "climate" else "Eco"
    await entity.async_set_temperature(temperature=temperature, **{field: valid})
    assert len(calls) == 1
    assert getattr(entity, "hvac_mode" if domain == "climate" else "current_operation") == valid


@pytest.mark.parametrize("domain, command, field, catalog", MODE_CASES)
@pytest.mark.parametrize("style", ["generated", "legacy", "mapping", "proxy"])
async def test_mode_commands_only_reach_compatible_sources(hass, domain, command, field, catalog, style):
    sources = [f"{domain}.first", f"{domain}.second"]
    for source, modes in zip(sources, (["first", "shared"], ["second", "shared"])):
        hass.states.async_set(source, "on", {catalog: modes, "supported_features": (1 << 31) - 1})
    actions = _source_command_actions(domain, sources, [hass.states.get(s) for s in sources])
    if style in {"legacy", "mapping"}:
        actions = {command: [{"action": f"{domain}.{command}", "target": {"entity_id": sources},
            "data": "{{ command_data }}" if style == "legacy" else {field: "{{ " + field + " }}"}}]}
    elif style == "proxy":
        actions = {}
    entity = VirtualEntity({"name": "Composite", "source_entities": sources,
        "command_actions": actions, "auto_helper": {"command_actions_json": json.dumps(actions)}}, domain, False)
    entity.hass = hass
    entity.entity_id = f"{domain}.composite"
    entity._schedule_state_update = Mock()
    calls = []

    async def capture(call):
        for source in call.data["entity_id"]:
            assert call.data[field] in hass.states.get(source).attributes[catalog]
            calls.append(source)

    async def method(self, **kwargs):
        pass

    hass.services.async_register(domain, command, capture)
    for requested, expected in [("second", [sources[1]]), ("shared", sources)]:
        calls.clear()
        await entity._async_run_command_action(command, method, (), {field: requested})
        assert calls == expected
    # A catalog can disappear or change while the integration remains loaded.
    hass.states.async_set(sources[0], "unavailable", {catalog: ["shared"]})
    hass.states.async_set(sources[1], "on", {catalog: ["shared", "late"], "supported_features": (1 << 31) - 1})
    calls.clear()
    await entity._async_run_command_action(command, method, (), {field: "late"})
    assert calls == [sources[1]]
    hass.states.async_set(sources[1], "on", {catalog: [], "supported_features": (1 << 31) - 1})
    calls.clear()
    await entity._async_run_command_action(command, method, (), {field: "shared"})
    assert calls == []
    for label in ("None", "False", "0"):
        hass.states.async_set(sources[1], "on", {catalog: [label], "supported_features": (1 << 31) - 1})
        calls.clear()
        await entity._async_run_command_action(command, method, (), {field: label})
        assert calls == [sources[1]]
