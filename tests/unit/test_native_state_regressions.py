"""Regression coverage for native measurements and movement state updates."""

from unittest.mock import Mock, patch

import pytest
from homeassistant.core import State

from custom_components.virtual_layer.cover import COVER_SCHEMA, VirtualCover
from custom_components.virtual_layer.lock import LOCK_SCHEMA, VirtualLock
from custom_components.virtual_layer.valve import VALVE_SCHEMA, VirtualValve
from custom_components.virtual_layer.water_heater import ENTITY_CLASS, ENTITY_SCHEMA


def water_heater(**values):
    entity = ENTITY_CLASS(ENTITY_SCHEMA({
        "name": "Water heater", "entity_id": "water_heater.regression",
        "initial_value": "off", "min_temp": 35, "max_temp": 85,
        **values,
    }), False)
    entity._create_state(entity._config)
    return entity


@pytest.mark.parametrize("temperature", [-5, 20, 95])
def test_water_heater_measurement_is_independent_of_target_range(temperature):
    entity = water_heater(current_temperature=temperature, target_temperature=50)
    assert entity.current_temperature == temperature
    entity._restore_state(State(entity.entity_id, "off", {
        "current_temperature": temperature, "temperature": 50,
    }), entity._config)
    assert entity.current_temperature == temperature
    entity._apply_native_template_value("current_temperature", temperature)
    entity._native_templates_applied()
    assert entity.current_temperature == temperature
    assert entity.target_temperature == 50


@pytest.mark.parametrize("mode", ["Eco", "HEAT", "heat pump"])
async def test_water_heater_preserves_exact_operation_values(hass, mode):
    entity = water_heater(operation_list=["off", mode], initial_value=mode)
    entity.hass = hass
    entity.async_write_ha_state = Mock()
    assert entity.current_operation == mode
    entity._restore_state(State(entity.entity_id, mode), entity._config)
    assert entity.current_operation == mode
    entity.set_state(mode)
    assert entity.current_operation == mode
    for name in ("current_operation", "state", "operation_mode"):
        entity._apply_native_template_value(name, mode)
        assert entity.current_operation == mode
    await entity.async_set_operation_mode(mode)
    assert entity.current_operation == mode


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), "invalid"])
def test_water_heater_rejects_invalid_measurements(value):
    entity = water_heater(current_temperature=value)
    assert entity.current_temperature is None
    entity._attr_current_temperature = 20
    with pytest.raises(ValueError):
        entity._apply_native_template_value("current_temperature", value)
    assert entity.current_temperature == 20


async def test_water_heater_still_validates_targets_and_clears_removed_modes(hass):
    entity = water_heater(operation_list=["off", "Eco", "heat"], initial_value="Eco")
    entity.hass = hass
    entity.async_write_ha_state = Mock()
    with pytest.raises(ValueError):
        await entity.async_set_temperature(temperature=20)
    entity._apply_native_template_value("operation_list", ["off", "heat"])
    entity._native_templates_applied()
    assert entity.current_operation == "off"
    entity.set_state("HEAT")  # Preserve the legacy lowercase fallback.
    assert entity.current_operation == "heat"


@pytest.fixture(params=["cover", "valve"])
def openable(hass, request):
    domain = request.param
    cls, schema = (VirtualCover, COVER_SCHEMA) if domain == "cover" else (VirtualValve, VALVE_SCHEMA)
    entity = cls(schema({
        "name": "Moving entity", "entity_id": f"{domain}.regression",
        "initial_value": "open", "open_close_duration": 10,
    }), False)
    entity.hass = hass
    entity._create_state(entity._config)
    entity.async_write_ha_state = Mock()
    entity._schedule_state_update = Mock()
    return entity


@pytest.mark.parametrize("position_first", [False, True])
def test_position_and_motion_templates_do_not_depend_on_field_order(openable, position_first):
    templates = [("current_position", "{{ 25 }}"), ("is_closing", "{{ true }}")]
    openable._native_templates = dict(templates if position_first else reversed(templates))
    openable._apply_templates()
    assert openable.state == "closing"
    assert openable._current_position == 25


def test_cover_reconciles_conflicting_direction_flags(openable):
    openable._native_templates = {"is_opening": "{{ true }}", "is_closing": "{{ true }}"}
    openable._apply_templates()
    assert openable.is_opening
    assert not openable.is_closing


@pytest.mark.parametrize("mask", [-1, "-1", True, float("nan")])
def test_cover_rejects_invalid_capabilities_without_exposing_extra_controls(mask):
    entity = VirtualCover(COVER_SCHEMA({"name": "Cover"}), False)
    previous = entity.supported_features
    with pytest.raises(ValueError):
        entity._apply_native_template_value("supported_features", mask)
    entity._native_templates_applied()
    assert entity.supported_features == previous


def test_unchanged_position_report_publishes_stopped_state(openable):
    with patch("custom_components.virtual_layer.entity.async_call_later", return_value=Mock()) as later:
        openable._set_position(0)
        assert openable.is_closing
        openable._native_templates = {"current_position": "{{ 100 }}"}
        openable._schedule_state_update.reset_mock()
        openable._apply_templates()
        assert not openable.is_closing
        later.return_value.assert_called_once()
        openable._schedule_state_update.assert_called_once()


def test_position_template_rejects_boolean_without_cancelling_motion(openable):
    with patch("custom_components.virtual_layer.entity.async_call_later", return_value=Mock()) as later:
        openable._set_position(0)
        with pytest.raises(ValueError):
            openable._apply_native_template_value("current_position", True)
        later.return_value.assert_not_called()
        assert openable.is_closing
        openable._cancel_timer()


async def test_lock_source_state_cancels_old_simulated_completion(hass):
    entity = VirtualLock(hass, LOCK_SCHEMA({
        "name": "Lock", "entity_id": "lock.regression",
        "initial_value": "locked", "locking_time": 5,
    }), False)
    entity.hass = hass
    entity._create_state(entity._config)
    entity.async_write_ha_state = Mock()
    with patch("custom_components.virtual_layer.lock.async_call_later", return_value=Mock()) as later:
        await entity.async_unlock()
        assert entity.is_unlocking
        entity.set_state("locking")
        later.return_value.assert_called_once()
        assert entity.is_locking
        assert entity._timer_handle is None


def test_lock_reported_state_does_not_simulate_a_jam(hass):
    entity = VirtualLock(hass, LOCK_SCHEMA({
        "name": "Lock", "entity_id": "lock.regression", "jamming_test": 1,
    }), False)
    entity._create_state(entity._config)
    with patch("custom_components.virtual_layer.lock.random.randint", return_value=0) as random:
        entity.set_state("locked")
    random.assert_not_called()
    assert entity.is_locked and not entity.is_jammed


@pytest.mark.parametrize("reverse", [False, True])
def test_lock_native_state_conflicts_have_deterministic_priority(hass, reverse):
    values = [("is_jammed", "{{ true }}"), ("is_locked", "{{ true }}")]
    entity = VirtualLock(hass, LOCK_SCHEMA({
        "name": "Lock", "entity_id": "lock.regression",
        "native_templates": dict(reversed(values) if reverse else values),
    }), False)
    entity.hass = hass
    entity._create_state(entity._config)
    entity._schedule_state_update = Mock()
    entity._apply_templates()
    assert entity.is_jammed and not entity.is_locked


@pytest.mark.parametrize("kind", ["native_report", "locked_report", "invalid_report", "open_command"])
async def test_lock_timer_lifecycle_after_replacement(hass, kind):
    entity = VirtualLock(hass, LOCK_SCHEMA({
        "name": "Lock", "entity_id": "lock.regression", "locking_time": 5,
        "support_open": True,
    }), False)
    entity.hass = hass
    entity._create_state(entity._config)
    entity.async_write_ha_state = Mock()
    with patch("custom_components.virtual_layer.lock.async_call_later", return_value=Mock()) as later:
        await entity.async_unlock()
        if kind == "invalid_report":
            with pytest.raises(ValueError):
                entity.set_state("broken")
            later.return_value.assert_not_called()
            assert entity.is_unlocking
            entity._cancel_timer()
        else:
            if kind == "native_report":
                entity._apply_native_template_value("is_unlocking", True)
            elif kind == "locked_report":
                entity._apply_native_template_value("is_locked", True)
                entity._native_templates_applied()
                assert entity.is_locked and not entity.is_unlocking
            else:
                await entity.async_open()
                assert entity.is_open
            later.return_value.assert_called_once()
            assert entity._timer_handle is None
