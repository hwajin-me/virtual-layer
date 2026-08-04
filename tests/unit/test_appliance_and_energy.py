"""Coverage for appliance-oriented options and electrical value units."""

import pytest

from homeassistant.components.number import NumberDeviceClass
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.components.humidifier import HumidifierDeviceClass
from homeassistant.const import ATTR_ENTITY_ID, CONF_NAME

from custom_components.virtual_layer.const import ATTR_UNIQUE_ID, CONF_INITIAL_VALUE, CONF_MAX, CONF_MIN
from custom_components.virtual_layer.number import NUMBER_SCHEMA, VirtualNumber
from custom_components.virtual_layer.sensor import SENSOR_SCHEMA, VirtualSensor
from custom_components.virtual_layer.humidifier import HUMIDIFIER_SCHEMA, VirtualHumidifier
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
def test_sensor_electrical_device_classes_get_default_units(device_class, expected_unit):
    config = SENSOR_SCHEMA({
        CONF_NAME: "Electrical Sensor",
        ATTR_ENTITY_ID: f"sensor.electrical_{device_class}",
        ATTR_UNIQUE_ID: f"electrical_{device_class}",
        CONF_INITIAL_VALUE: "1",
        "class": device_class,
    })
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
    ],
)
def test_number_electrical_device_classes_get_default_units(device_class, expected_unit):
    config = NUMBER_SCHEMA({
        CONF_NAME: "Electrical Number",
        ATTR_ENTITY_ID: f"number.electrical_{device_class}",
        ATTR_UNIQUE_ID: f"electrical_number_{device_class}",
        CONF_INITIAL_VALUE: "1",
        CONF_MIN: 0,
        CONF_MAX: 100,
        "class": device_class,
    })
    entity = VirtualNumber(config, False)

    assert entity._attr_unit_of_measurement == expected_unit


def test_sensor_accepts_washer_and_dryer_options_as_attributes():
    config = SENSOR_SCHEMA({
        CONF_NAME: "Washer Status",
        ATTR_ENTITY_ID: "sensor.washer_status",
        ATTR_UNIQUE_ID: "washer_status",
        CONF_INITIAL_VALUE: "washing",
        "appliance_type": "washer",
        "program": "cotton",
        "remaining_time": 1800,
        "door_locked": True,
    })
    entity = VirtualSensor(config, False)
    entity._create_state(config)
    entity._update_attributes()

    assert entity.extra_state_attributes["appliance_type"] == "washer"
    assert entity.extra_state_attributes["program"] == "cotton"
    assert entity.extra_state_attributes["remaining_time"] == 1800
    assert entity.extra_state_attributes["door_locked"] is True


async def test_dehumidifier_supports_humidity_and_mode_commands():
    config = HUMIDIFIER_SCHEMA({
        CONF_NAME: "Dehumidifier",
        ATTR_ENTITY_ID: "humidifier.dehumidifier",
        ATTR_UNIQUE_ID: "dehumidifier",
        CONF_INITIAL_VALUE: "off",
        "class": HumidifierDeviceClass.DEHUMIDIFIER,
        "target_humidity": 45,
        "current_humidity": 60,
        "modes": ["auto", "sleep"],
        "mode": "auto",
    })
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
    config = VALVE_SCHEMA({
        CONF_NAME: "Pool Pump Valve",
        ATTR_ENTITY_ID: "valve.pool_pump",
        ATTR_UNIQUE_ID: "pool_pump",
        CONF_INITIAL_VALUE: "closed",
        "open_close_duration": 0,
    })
    entity = VirtualValve(config, False)
    entity._create_state(config)
    entity.async_write_ha_state = lambda: None
    entity.async_schedule_update_ha_state = lambda **kwargs: None

    assert entity.current_valve_position == 0
    await entity.async_open_valve()
    assert entity.current_valve_position == 100
    await entity.async_close_valve()
    assert entity.current_valve_position == 0
