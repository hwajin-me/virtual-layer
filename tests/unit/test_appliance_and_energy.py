"""Coverage for appliance-oriented options and electrical value units."""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.components.humidifier import HumidifierDeviceClass
from homeassistant.components.number import (
    ATTR_STEP,
    NumberDeviceClass,
    NumberEntity,
    NumberMode,
)
from homeassistant.components.number import (
    DEVICE_CLASS_UNITS as NUMBER_DEVICE_CLASS_UNITS,
)
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import ATTR_ENTITY_ID, CONF_MODE, CONF_NAME
from homeassistant.core import State

from custom_components.virtual_layer.const import (
    ATTR_UNIQUE_ID,
    CONF_INITIAL_VALUE,
    CONF_MAX,
    CONF_MIN,
)
from custom_components.virtual_layer.entity import VirtualEntity
from custom_components.virtual_layer.humidifier import (
    HUMIDIFIER_SCHEMA,
    VirtualHumidifier,
)
from custom_components.virtual_layer.lock import LOCK_SCHEMA, VirtualLock
from custom_components.virtual_layer.number import (
    NUMBER_SCHEMA,
    VirtualNumber,
)
from custom_components.virtual_layer.number import (
    UNITS_OF_MEASUREMENT as NUMBER_UNITS_OF_MEASUREMENT,
)
from custom_components.virtual_layer.sensor import SENSOR_SCHEMA, VirtualSensor
from custom_components.virtual_layer.valve import VALVE_SCHEMA, VirtualValve

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("device_class", "expected_unit"),
    [
        (SensorDeviceClass.POWER, "kW"),
        (SensorDeviceClass.ENERGY, "kWh"),
        (SensorDeviceClass.CURRENT, "A"),
        (SensorDeviceClass.VOLTAGE, "V"),
        (SensorDeviceClass.APPARENT_POWER, "VA"),
        (SensorDeviceClass.REACTIVE_POWER, "var"),
        (SensorDeviceClass.POWER_FACTOR, "%"),
        (SensorDeviceClass.GAS, "m³"),
        (SensorDeviceClass.WATER, "L"),
        (SensorDeviceClass.VOLUME_FLOW_RATE, "m³/h"),
        (SensorDeviceClass.MOISTURE, "%"),
    ],
)
def test_sensor_electrical_device_classes_get_default_units(
    device_class, expected_unit
):
    config = SENSOR_SCHEMA(
        {
            CONF_NAME: "Electrical Sensor",
            ATTR_ENTITY_ID: f"sensor.electrical_{device_class}",
            ATTR_UNIQUE_ID: f"electrical_{device_class}",
            CONF_INITIAL_VALUE: "1",
            "class": device_class,
        }
    )
    entity = VirtualSensor(config, False)

    assert entity._attr_unit_of_measurement == expected_unit


@pytest.mark.parametrize(
    ("device_class", "expected_unit"),
    [
        (NumberDeviceClass.POWER, "kW"),
        (NumberDeviceClass.ENERGY, "kWh"),
        (NumberDeviceClass.CURRENT, "A"),
        (NumberDeviceClass.VOLTAGE, "V"),
        (NumberDeviceClass.APPARENT_POWER, "VA"),
        (NumberDeviceClass.REACTIVE_POWER, "var"),
        (NumberDeviceClass.GAS, "m³"),
        (NumberDeviceClass.WATER, "L"),
        (NumberDeviceClass.VOLUME_FLOW_RATE, "m³/h"),
        (NumberDeviceClass.MOISTURE, "%"),
        (NumberDeviceClass.TEMPERATURE, "°C"),
        (NumberDeviceClass.FREQUENCY, "Hz"),
    ],
)
def test_number_electrical_device_classes_get_default_units(
    device_class, expected_unit
):
    config = NUMBER_SCHEMA(
        {
            CONF_NAME: "Electrical Number",
            ATTR_ENTITY_ID: f"number.electrical_{device_class}",
            ATTR_UNIQUE_ID: f"electrical_number_{device_class}",
            CONF_INITIAL_VALUE: "1",
            CONF_MIN: 0,
            CONF_MAX: 100,
            "class": device_class,
        }
    )
    entity = VirtualNumber(config, False)

    assert isinstance(entity, NumberEntity)
    assert entity._attr_unit_of_measurement == expected_unit


def test_all_default_number_units_are_valid_for_their_device_class():
    for device_class, unit in NUMBER_UNITS_OF_MEASUREMENT.items():
        assert unit in NUMBER_DEVICE_CLASS_UNITS[device_class]


async def test_number_uses_native_state_and_clamps_template_and_service_values():
    config = NUMBER_SCHEMA(
        {
            CONF_NAME: "Electrical Limit",
            ATTR_ENTITY_ID: "number.electrical_limit",
            ATTR_UNIQUE_ID: "electrical_limit",
            CONF_INITIAL_VALUE: "not-a-number",
            CONF_MIN: 10,
            CONF_MAX: 20,
        }
    )
    entity = VirtualNumber(config, False)
    entity._create_state(config)
    entity.async_schedule_update_ha_state = lambda **_kwargs: None

    assert entity.native_value == 10
    await entity.async_set_native_value(15)
    assert entity.native_value == 15
    entity.set_state(99)
    assert entity.native_value == 20


def test_number_supports_step_and_input_mode():
    config = NUMBER_SCHEMA(
        {
            CONF_NAME: "Dimmer Limit",
            ATTR_ENTITY_ID: "number.dimmer_limit",
            ATTR_UNIQUE_ID: "dimmer_limit",
            CONF_INITIAL_VALUE: 12.5,
            CONF_MIN: 0,
            CONF_MAX: 100,
            ATTR_STEP: 0.5,
            CONF_MODE: "slider",
        }
    )

    entity = VirtualNumber(config, False)
    entity._create_state(config)
    entity._update_attributes()

    assert entity.native_step == 0.5
    assert entity.mode is NumberMode.SLIDER
    assert entity.extra_state_attributes[ATTR_STEP] == 0.5


def test_sensor_accepts_washer_and_dryer_options_as_attributes():
    config = SENSOR_SCHEMA(
        {
            CONF_NAME: "Washer Status",
            ATTR_ENTITY_ID: "sensor.washer_status",
            ATTR_UNIQUE_ID: "washer_status",
            CONF_INITIAL_VALUE: "washing",
            "appliance_type": "washer",
            "program": "cotton",
            "remaining_time": 1800,
            "door_locked": True,
        }
    )
    entity = VirtualSensor(config, False)
    entity._create_state(config)
    entity._update_attributes()

    assert entity.extra_state_attributes["appliance_type"] == "washer"
    assert entity.extra_state_attributes["program"] == "cotton"
    assert entity.extra_state_attributes["remaining_time"] == 1800
    assert entity.extra_state_attributes["door_locked"] is True


async def test_dehumidifier_supports_humidity_and_mode_commands():
    config = HUMIDIFIER_SCHEMA(
        {
            CONF_NAME: "Dehumidifier",
            ATTR_ENTITY_ID: "humidifier.dehumidifier",
            ATTR_UNIQUE_ID: "dehumidifier",
            CONF_INITIAL_VALUE: "off",
            "class": HumidifierDeviceClass.DEHUMIDIFIER,
            "target_humidity": 45,
            "current_humidity": 60,
            "modes": ["auto", "sleep"],
            "mode": "auto",
        }
    )
    entity = VirtualHumidifier(config, False)
    entity._create_state(config)
    entity.async_write_ha_state = lambda: None

    assert entity.device_class == HumidifierDeviceClass.DEHUMIDIFIER
    assert entity.target_humidity == 45
    assert entity.current_humidity == 60
    await entity.async_turn_on()
    await entity.async_set_humidity(40)
    await entity.async_set_mode("sleep")

    assert entity.is_on is True
    assert entity.target_humidity == 40
    assert entity.mode == "sleep"


async def test_valve_can_model_a_pump_with_open_close_commands():
    config = VALVE_SCHEMA(
        {
            CONF_NAME: "Pool Pump Valve",
            ATTR_ENTITY_ID: "valve.pool_pump",
            ATTR_UNIQUE_ID: "pool_pump",
            CONF_INITIAL_VALUE: "closed",
            "open_close_duration": 0,
        }
    )
    entity = VirtualValve(config, False)
    entity._create_state(config)
    entity.async_write_ha_state = lambda: None
    entity.async_schedule_update_ha_state = lambda **kwargs: None

    assert entity.current_valve_position == 0
    await entity.async_open_valve()
    assert entity.current_valve_position == 100
    assert entity._open_close_tick == 1
    await entity.async_close_valve()
    assert entity.current_valve_position == 0


def test_openable_retargeting_current_position_clears_motion_and_timer():
    config = VALVE_SCHEMA(
        {
            CONF_NAME: "Timed Valve",
            ATTR_ENTITY_ID: "valve.timed_valve",
            ATTR_UNIQUE_ID: "timed_valve",
            CONF_INITIAL_VALUE: "closed",
            "open_close_duration": 10,
            "open_close_tick": 1,
        }
    )
    entity = VirtualValve(config, False)
    entity.hass = Mock()
    entity._create_state(config)
    entity.async_write_ha_state = Mock()
    entity.async_schedule_update_ha_state = Mock()
    cancel_timer = Mock()

    with patch(
        "custom_components.virtual_layer.entity.async_call_later",
        return_value=cancel_timer,
    ):
        entity._set_position(100)
        assert entity.is_opening is True
        entity._set_position(0)

    cancel_timer.assert_called_once_with()
    assert entity._target_position is None
    assert entity.is_opening is False
    assert entity.is_closing is False
    assert entity.is_closed is True


@pytest.mark.parametrize(
    ("restored_state", "restored_position", "expected_position"),
    [
        ("open", None, 100),
        ("closed", "invalid", 0),
        ("open", float("nan"), 100),
        ("open", 150, 100),
        ("closed", -20, 0),
    ],
)
def test_openable_repairs_invalid_restored_positions(
    restored_state,
    restored_position,
    expected_position,
):
    config = VALVE_SCHEMA(
        {
            CONF_NAME: "Restored Valve",
            ATTR_ENTITY_ID: "valve.restored_valve",
            ATTR_UNIQUE_ID: "restored_valve",
            CONF_INITIAL_VALUE: "closed",
            "open_close_duration": 0,
        }
    )
    entity = VirtualValve(config, False)

    entity._restore_state(
        State(
            "valve.restored_valve",
            restored_state,
            {"current_position": restored_position},
        ),
        config,
    )

    assert entity.current_valve_position == expected_position


async def test_openable_unload_cancels_pending_movement():
    config = VALVE_SCHEMA(
        {
            CONF_NAME: "Unload Valve",
            ATTR_ENTITY_ID: "valve.unload_valve",
            ATTR_UNIQUE_ID: "unload_valve",
            CONF_INITIAL_VALUE: "closed",
            "open_close_duration": 10,
            "open_close_tick": 1,
        }
    )
    entity = VirtualValve(config, False)
    cancel_timer = Mock()
    entity._timer_handle = cancel_timer

    with patch.object(
        VirtualEntity,
        "async_will_remove_from_hass",
        AsyncMock(),
    ):
        await entity.async_will_remove_from_hass()

    cancel_timer.assert_called_once_with()


def test_lock_replacing_delayed_operation_cancels_previous_timer():
    config = LOCK_SCHEMA(
        {
            CONF_NAME: "Timed Lock",
            ATTR_ENTITY_ID: "lock.timed_lock",
            ATTR_UNIQUE_ID: "timed_lock",
            CONF_INITIAL_VALUE: "locked",
            "locking_time": 5,
        }
    )
    entity = VirtualLock(Mock(), config, False)
    first_cancel = Mock()
    second_cancel = Mock()

    with patch(
        "custom_components.virtual_layer.lock.async_call_later",
        side_effect=[first_cancel, second_cancel],
    ):
        entity._start_operation()
        entity._start_operation()

    first_cancel.assert_called_once_with()
    assert entity._timer_handle is second_cancel


def test_lock_jam_and_direct_transitions_clear_conflicting_activity_flags():
    config = LOCK_SCHEMA(
        {
            CONF_NAME: "Jamming Lock",
            ATTR_ENTITY_ID: "lock.jamming_lock",
            ATTR_UNIQUE_ID: "jamming_lock",
            CONF_INITIAL_VALUE: "locked",
            "jamming_test": 1,
        }
    )
    entity = VirtualLock(Mock(), config, False)
    entity._create_state(config)
    entity._locking()

    with patch("custom_components.virtual_layer.lock.random.randint", return_value=0):
        entity._lock()

    assert entity.is_jammed is True
    assert entity.is_locking is False
    assert entity.is_unlocking is False
    assert entity.is_opening is False

    entity._test_jamming = 0
    entity._attr_is_opening = True
    entity.set_state("locked")
    assert entity.is_locked is True
    assert entity.is_jammed is False
    assert entity.is_opening is False
