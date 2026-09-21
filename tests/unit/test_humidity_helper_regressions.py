"""Humidity helpers preserve measurements and dispatch consistent setpoints."""

from unittest.mock import Mock

import pytest
from homeassistant.helpers.template import Template

from custom_components.virtual_layer.climate import CLIMATE_SCHEMA, VirtualClimate
from custom_components.virtual_layer.humidifier import HUMIDIFIER_SCHEMA, VirtualHumidifier
from custom_components.virtual_layer.config_flow import (
    CONF_NATIVE_VALUE_TEMPLATES,
    InvalidDomainOptions,
    _build_entity_config,
    _reference_entity_defaults,
)
from tests.unit.test_config_flow_helpers import _entity_input


@pytest.mark.parametrize("optimistic", [True, False])
@pytest.mark.parametrize("platform,schema,entity_class", [
    ("climate", CLIMATE_SCHEMA, VirtualClimate),
    ("humidifier", HUMIDIFIER_SCHEMA, VirtualHumidifier),
])
async def test_humidity_action_variables_match_rounded_target(
    hass, optimistic, platform, schema, entity_class
):
    calls = []

    async def capture(call):
        calls.append(dict(call.data))

    hass.services.async_register("test", "capture", capture)
    entity = entity_class(schema({
        "name": "Humidity", "entity_id": f"{platform}.humidity",
        "initial_value": "off", "min_humidity": 30, "max_humidity": 80,
        "target_humidity": 40, "target_humidity_step": 10,
        "command_actions": {"set_humidity": {
            "optimistic": optimistic,
            "sequence": [{"action": "test.capture", "data": {
                "direct": "{{ humidity }}",
                "mapping": "{{ command_data.humidity }}",
            }}],
        }},
    }), False)
    entity.hass = hass
    entity._create_state(entity._config)
    entity._schedule_state_update = Mock()
    entity.async_write_ha_state = Mock()
    await entity.async_set_humidity(55)
    assert calls == [{"direct": 60, "mapping": 60}]
    assert entity.target_humidity == (60 if optimistic else 40)


@pytest.mark.parametrize("platform", ["climate", "humidifier"])
def test_dynamic_target_limits_do_not_disable_measurement_validation(platform):
    with pytest.raises(InvalidDomainOptions):
        _build_entity_config(_entity_input({
            "platform": platform, "initial_value": "off",
            "current_humidity": 101,
            CONF_NATIVE_VALUE_TEMPLATES: {"min_humidity": "{{ 30 }}"},
        }))


def test_humidifier_dynamic_values_override_stale_static_options():
    templates = {
        "available_modes": "{{ ['auto'] }}", "mode": "{{ 'auto' }}",
        "min_humidity": "{{ 30 }}", "max_humidity": "{{ 80 }}",
        "target_humidity": "{{ 50 }}", "target_humidity_step": "{{ 5 }}",
        "action": "{{ none }}",
    }
    _, entity = _build_entity_config(_entity_input({
        "platform": "humidifier", "initial_value": "off",
        "modes": ["old"], "mode": "removed", "action": "obsolete",
        "min_humidity": 80, "max_humidity": 30, "target_humidity": 90,
        "target_humidity_step": 0, CONF_NATIVE_VALUE_TEMPLATES: templates,
    }))
    assert entity["native_templates"] == templates


def test_generated_humidifier_action_can_be_unknown_and_recover(hass, caplog):
    source = "humidifier.source"
    hass.states.async_set(source, "on", {"action": "humidifying"})
    defaults = _reference_entity_defaults(hass, [source])
    entity = VirtualHumidifier(HUMIDIFIER_SCHEMA({
        "name": "Humidity", "initial_value": "on",
        "native_templates": defaults[CONF_NATIVE_VALUE_TEMPLATES],
    }), False)
    entity.hass = hass
    entity._create_state(entity._config)
    entity.async_schedule_update_ha_state = Mock()
    entity._apply_templates()
    assert entity.action == "humidifying"
    hass.states.async_set(source, "on", {})
    entity._apply_templates()
    assert entity.action is None
    hass.states.async_set(source, "on", {"action": "idle"})
    entity._apply_templates()
    assert entity.action == "idle"
    assert "Unable to render native template" not in caplog.text


@pytest.mark.parametrize("platform", ["climate", "humidifier"])
@pytest.mark.parametrize("initially_missing", [False, True])
@pytest.mark.parametrize("count", [1, 2])
def test_measurement_helpers_follow_live_sources(hass, platform, initially_missing, count):
    sources = [f"{platform}.room_{index}" for index in range(count)]
    for source in sources:
        hass.states.async_set(source, "off", {} if initially_missing else {"current_humidity": 50})
    defaults = _reference_entity_defaults(hass, sources)
    helper = Template(defaults[CONF_NATIVE_VALUE_TEMPLATES]["current_humidity"], hass)
    assert helper.async_render() == (None if initially_missing else 50)
    for source in sources:
        hass.states.async_set(source, "off", {"current_humidity": 90})
    assert helper.async_render() == 90
    hass.states.async_set(sources[0], "unavailable", {"current_humidity": 10})
    assert helper.async_render() == (90 if count == 2 else None)
    for bad in (None, True, -1, 101, "nan", "inf"):
        for source in sources:
            hass.states.async_set(source, "off", {"current_humidity": bad})
        assert helper.async_render() is None
    for source in sources:
        hass.states.async_set(source, "off", {"current_humidity": 0})
    assert helper.async_render() == 0
