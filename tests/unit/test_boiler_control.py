"""Behavioral tests for opt-in boiler feedback and state restoration."""

from datetime import timedelta
import asyncio
from unittest.mock import Mock

import pytest
import voluptuous as vol
from homeassistant.core import State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from custom_components.virtual_layer import boiler_control as bc
from custom_components.virtual_layer.climate import (
    CLIMATE_SCHEMA,
    VirtualClimate,
    validate_domain_options,
)
from custom_components.virtual_layer.config_flow import (
    _build_entity_config,
    _entity_form_defaults,
)


def config(**extra):
    return CLIMATE_SCHEMA(
        {
            "name": "Boiler",
            "entity_id": "climate.virtual_boiler",
            "initial_value": "heat",
            "hvac_modes": ["off", "heat"],
            "source_entities": ["climate.boiler"],
            "boiler_room_temperature_entity_id": ["sensor.room_a", "sensor.room_b"],
            "boiler_temperature_calibration_template": "{{ temperature * 1.5 + 2.5 }}",
            bc.ENABLED: True,
            **extra,
        }
    )


def source(hass, state="heat", **attrs):
    hass.states.async_set(
        "climate.boiler",
        state,
        {
            "current_temperature": 45,
            "temperature": 40,
            "min_temp": 25,
            "max_temp": 65,
            "target_temp_step": 0.5,
            "hvac_modes": ["off", "heat"],
            **attrs,
        },
    )


def entity(hass, **extra):
    source(hass)
    hass.states.async_set("sensor.room_a", "20", {"unit_of_measurement": "°C"})
    hass.states.async_set("sensor.room_b", "22", {"unit_of_measurement": "°C"})
    result = VirtualClimate(config(**extra), False)
    result.hass = hass
    result._create_state(result._config)
    result.async_write_ha_state = Mock()
    result._schedule_state_update = Mock()
    return result


@pytest.mark.parametrize(
    "value", [None, True, False, "NaN", "inf", float("-inf"), {}, []]
)
def test_invalid_numbers(value):
    assert bc.finite(value) is None


def test_thermal_history_accumulates_decays_and_resets_after_gaps():
    history = bc.ThermalHistory()
    history.observe(1000, 20, 50, True)
    values = history.observe(1060, 20.2, 50, True)
    assert values["heating_elapsed_minutes"] == 1
    assert values["room_temperature_rate"] == pytest.approx(0.2)
    assert 0 < values["heat_accumulation"] < 30
    previous = history.accumulation
    history.observe(1120, 20.2, 50, False)
    assert history.heating_minutes == 0
    assert 0 < history.accumulation < previous
    history.observe(5000, 25, 50, True)
    assert history.heating_minutes == 0
    assert len(history.samples) == 1


def test_thermal_history_zero_timestamp_and_missing_water_reset_duration():
    history = bc.ThermalHistory()
    history.observe(0, 20, 50, True)
    assert history.observe(60, 20, 50, True)["heating_elapsed_minutes"] == 1
    assert history.observe(120, 20, None, True)["heating_elapsed_minutes"] == 0
    assert history.observe(180, 20, 50, True)["heating_elapsed_minutes"] == 0


def test_extreme_readings_never_publish_nonfinite_history():
    history = bc.ThermalHistory()
    history.observe(0, -1e308, 1e308, True)
    values = history.observe(60, 1e308, 1e308, True)
    assert all(bc.finite(value) is not None for value in values.values())


async def test_room_sensor_dropout_does_not_create_artificial_temperature_trend(hass):
    boiler = entity(hass)
    await boiler._async_boiler_update(1000)
    hass.states.async_set("sensor.room_a", "unavailable")
    await boiler._async_boiler_update(1060)
    assert list(boiler._boiler_history.samples) == [(1060, 22)]
    hass.states.async_set("sensor.room_a", "20", {"unit_of_measurement": "°C"})
    await boiler._async_boiler_update(1120)
    assert list(boiler._boiler_history.samples) == [(1120, 21)]


async def test_dynamic_room_target_obeys_updated_native_limits(hass):
    boiler = entity(hass)
    await boiler.async_set_temperature(temperature=26)
    boiler._apply_native_template_value("max_temp", 24)
    boiler._native_templates_applied()
    assert boiler.target_temperature == 24
    boiler._attr_extra_state_attributes = {}
    assert boiler.state_attributes["boiler_room_target"] == 24


async def test_room_target_is_stable_and_periodic_water_command_is_bounded(hass):
    boiler = entity(hass)
    calls = []
    hass.services.async_register(
        "climate", "set_temperature", lambda call: calls.append(call)
    )
    await boiler.async_set_temperature(temperature=26)
    assert calls == []
    await boiler._async_boiler_update(1000)
    assert len(calls) == 1
    assert calls[0].data["temperature"] == 42
    assert calls[0].data["entity_id"] == ["climate.boiler"]
    assert boiler.target_temperature == 26
    boiler._apply_native_template_value("target_temperature", 33)
    assert boiler.target_temperature == 26
    await boiler._async_boiler_update(1060)
    assert len(calls) == 1
    assert boiler._boiler_status == "rate_limited"
    await boiler._async_boiler_update(1120)
    assert len(calls) == 2


async def test_disabled_room_to_water_formula_sends_the_room_target_directly(hass):
    """A water boiler can opt out when it already accepts room setpoints."""
    boiler = entity(
        hass,
        **{bc.CALIBRATION_ENABLED: False, bc.FORMULA: "{{ base_water_temperature + 1 }}"},
    )
    source(hass, temperature=26)
    calls = []
    hass.services.async_register(
        "climate", "set_temperature", lambda call: calls.append(call)
    )
    await boiler.async_set_temperature(temperature=26)
    await boiler._async_boiler_update(1000)
    assert calls[0].data["temperature"] == 27


def test_climate_temperature_step_is_always_one_degree(hass):
    boiler = entity(hass, target_temperature_step=0.5)
    assert boiler.target_temperature_step == 1
    boiler._apply_native_template_value("target_temperature_step", 0.5)
    assert boiler.target_temperature_step == 1


@pytest.mark.parametrize(
    "fault", ["off", "unavailable", "no_room", "no_water", "no_limits", "no_target"]
)
async def test_missing_inputs_and_off_never_write(hass, fault):
    boiler = entity(hass)
    calls = []
    hass.services.async_register(
        "climate", "set_temperature", lambda call: calls.append(call)
    )
    if fault != "no_target":
        await boiler.async_set_temperature(temperature=26)
    if fault in {"off", "unavailable"}:
        source(hass, fault)
    elif fault == "no_room":
        hass.states.async_set("sensor.room_a", "unavailable")
        hass.states.async_set("sensor.room_b", "unknown")
    elif fault == "no_water":
        source(hass, current_temperature=None)
    elif fault == "no_limits":
        source(hass, min_temp=None)
    await boiler._async_boiler_update(1000)
    assert calls == []


async def test_formula_receives_mean_water_duration_rate_and_heat_history(hass):
    boiler = entity(
        hass,
        **{
            bc.FORMULA: "{{ boiler_water_temperature - (room_temperature - 21) - heating_elapsed_minutes - room_temperature_rate - heat_accumulation / 100 }}"
        },
    )
    calls = []
    hass.services.async_register(
        "climate", "set_temperature", lambda call: calls.append(call)
    )
    await boiler.async_set_temperature(temperature=26)
    await boiler._async_boiler_update(1000)
    for time in range(1060, 2201, 60):
        await boiler._async_boiler_update(time)
    assert calls[0].data["temperature"] == 42
    assert calls[-1].data["temperature"] == 38
    assert boiler._boiler_history.accumulation > 0
    assert boiler.target_temperature == 26


async def test_one_unavailable_sensor_does_not_poison_average(hass):
    boiler = entity(hass, **{bc.FORMULA: "{{ room_temperature + 20 }}"})
    hass.states.async_set("sensor.room_a", "unavailable")
    calls = []
    hass.services.async_register(
        "climate", "set_temperature", lambda call: calls.append(call)
    )
    await boiler.async_set_temperature(temperature=26)
    await boiler._async_boiler_update(1000)
    assert calls[0].data["temperature"] == 42


@pytest.mark.parametrize("formula", ["{{ 'NaN' }}", "{{ none }}", "{{ 1 / 0 }}"])
async def test_invalid_formula_does_not_dispatch(hass, formula):
    boiler = entity(hass, **{bc.FORMULA: formula})
    calls = []
    hass.services.async_register(
        "climate", "set_temperature", lambda call: calls.append(call)
    )
    await boiler.async_set_temperature(temperature=26)
    await boiler._async_boiler_tick(dt_util.utcnow())
    assert calls == []
    assert boiler._boiler_status == "control_error"


async def test_restore_keeps_room_target_but_does_not_invent_unobserved_heating(hass):
    boiler = entity(hass)
    now = dt_util.utcnow().timestamp()
    state = State(
        boiler.entity_id,
        "heat",
        {
            "temperature": 33,
            "boiler_room_target": 26,
            "boiler_heat_accumulation": 100,
            "boiler_history_timestamp": now - 1800,
        },
    )
    boiler._restore_state(state, boiler._config)
    assert boiler.target_temperature == 26
    assert boiler._boiler_history.accumulation == pytest.approx(
        100 / 2.7182818, rel=0.01
    )
    assert boiler._boiler_history.heating_minutes == 0
    assert boiler._boiler_last_attempt is not None


@pytest.mark.parametrize(
    "extra",
    [
        {"source_entities": []},
        {"source_entities": ["climate.a", "climate.b"]},
        {"boiler_room_temperature_entity_id": []},
        {"boiler_temperature_calibration_template": ""},
        {"temperature_unit": "°F"},
    ],
)
def test_dynamic_configuration_requires_explicit_inputs(extra):
    with pytest.raises(vol.Invalid):
        validate_domain_options(config(**extra))


def test_flow_round_trip_keeps_dynamic_formula_and_sensors():
    stored = {**config(), "platform": "climate"}
    defaults = _entity_form_defaults("Boiler", stored)
    _, reopened = _build_entity_config(defaults)
    assert reopened[bc.ENABLED] is True
    assert reopened[bc.FORMULA] == bc.DEFAULT_FORMULA
    assert reopened["boiler_room_temperature_entity_id"] == [
        "sensor.room_a",
        "sensor.room_b",
    ]


def test_flow_persists_disabled_room_to_water_calibration():
    defaults = _entity_form_defaults("Boiler", {**config(), "platform": "climate"})
    defaults[bc.CALIBRATION_ENABLED] = False
    _, stored = _build_entity_config(defaults)
    assert stored[bc.CALIBRATION_ENABLED] is False


@pytest.mark.parametrize("editing", [False, True])
@pytest.mark.parametrize("custom", [False, True])
def test_boiler_helper_step_exposes_new_formula_and_preserves_custom_values(editing, custom):
    from custom_components.virtual_layer.config_flow import _helper_usage_schema, _helper_update_schema
    from tests.flow_helpers import suggested_form_values

    schema = _helper_update_schema if editing else _helper_usage_schema
    stored = {bc.ENABLED: True, bc.FORMULA: "{{ boiler_water_temperature - 1 }}"} if custom else {}
    defaults = suggested_form_values(schema("{{ temperature * 1.5 + 2.5 }}", stored))
    assert defaults[bc.FORMULA] == stored.get(bc.FORMULA, bc.DEFAULT_FORMULA)
    assert defaults[bc.ENABLED] is custom
    assert bc.FORMULA not in suggested_form_values(schema())


async def test_service_failure_is_rate_limited(hass):
    boiler = entity(hass)
    calls = []

    async def fail(call):
        calls.append(call)
        raise HomeAssistantError("Device is offline")

    hass.services.async_register("climate", "set_temperature", fail)
    await boiler.async_set_temperature(temperature=26)
    now = dt_util.utcnow()
    await boiler._async_boiler_tick(now)
    assert boiler._boiler_status == "control_error"
    await boiler._async_boiler_tick(now + timedelta(seconds=60))
    assert len(calls) == 1
    await boiler._async_boiler_tick(now + timedelta(seconds=120))
    assert len(calls) == 2


async def test_concurrent_ticks_do_not_duplicate_commands(hass):
    boiler = entity(hass)
    started, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def slow(call):
        calls.append(call)
        started.set()
        await release.wait()

    hass.services.async_register("climate", "set_temperature", slow)
    await boiler.async_set_temperature(temperature=26)
    task = asyncio.create_task(boiler._async_boiler_tick(dt_util.utcnow()))
    try:
        await asyncio.wait_for(started.wait(), 2)
        await boiler._async_boiler_tick(dt_util.utcnow() + timedelta(minutes=5))
        assert len(calls) == 1
    finally:
        release.set()
        await task


async def test_sensor_fahrenheit_conversion_and_invalid_unit_filter(hass):
    boiler = entity(hass, **{bc.FORMULA: "{{ room_temperature + 20 }}"})
    hass.states.async_set("sensor.room_a", "71.6", {"unit_of_measurement": "°F"})
    hass.states.async_set("sensor.room_b", "900", {"unit_of_measurement": "W"})
    calls = []
    hass.services.async_register(
        "climate", "set_temperature", lambda call: calls.append(call)
    )
    await boiler.async_set_temperature(temperature=26)
    await boiler._async_boiler_update(1000)
    assert calls[0].data["temperature"] == 42


async def test_dynamic_ui_room_helper_converts_mixed_units(hass):
    from homeassistant.helpers.template import Template

    defaults = _entity_form_defaults("Boiler", {**config(), "platform": "climate"})
    _, stored = _build_entity_config(defaults)
    hass.states.async_set("sensor.room_a", "68", {"unit_of_measurement": "°F"})
    hass.states.async_set("sensor.room_b", "295.15", {"unit_of_measurement": "K"})
    helper = Template(stored["native_templates"]["current_temperature"], hass)
    assert helper.async_render() == pytest.approx(21)
    hass.states.async_set("sensor.room_b", "99", {"unit_of_measurement": "W"})
    assert helper.async_render() == pytest.approx(20)


async def test_invalid_mode_does_not_change_saved_target(hass):
    boiler = entity(hass)
    await boiler.async_set_temperature(temperature=26)
    with pytest.raises(ValueError):
        await boiler.async_set_temperature(temperature=27, hvac_mode="cool")
    assert boiler.target_temperature == 26


async def test_actual_room_reading_is_not_clamped_to_setpoint_range(hass):
    boiler = entity(hass, min_temp=22, max_temp=40)
    boiler._apply_native_template_value("current_temperature", 19)
    boiler._native_templates_applied()
    assert boiler.current_temperature == 19


async def test_coarse_source_step_cannot_exceed_maximum_change(hass):
    boiler = entity(hass, **{bc.FORMULA: "{{ 65 }}"})
    source(hass, min_temp=25, temperature=40, target_temp_step=5)
    calls = []
    hass.services.async_register(
        "climate", "set_temperature", lambda call: calls.append(call)
    )
    await boiler.async_set_temperature(temperature=26)
    await boiler._async_boiler_update(1000)
    assert calls == []


@pytest.mark.parametrize("value", [None, "nan", "inf", True, {}, -100])
def test_corrupt_restored_history_does_not_trigger_writes(hass, value):
    boiler = entity(hass)
    state = State(
        boiler.entity_id,
        "heat",
        {
            "boiler_heat_accumulation": value,
            "boiler_history_timestamp": value,
            "boiler_room_target": value,
        },
    )
    boiler._restore_state(state, boiler._config)
    assert boiler._boiler_history.accumulation == 0


async def test_dynamic_flow_rejects_invalid_jinja(hass):
    from custom_components.virtual_layer.config_flow import (
        _async_build_entity_config,
        InvalidTemplate,
    )

    defaults = _entity_form_defaults("Boiler", {**config(), "platform": "climate"})
    defaults[bc.FORMULA] = "{{ temperature + }}"
    with pytest.raises(InvalidTemplate):
        await _async_build_entity_config(hass, defaults)
