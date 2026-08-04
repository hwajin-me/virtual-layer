"""Ensure native Home Assistant commands publish virtual entity changes."""

from unittest.mock import Mock

import pytest

from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.components.vacuum import (
    StateVacuumEntity,
    VacuumActivity,
    VacuumEntityFeature,
)

from custom_components.virtual_layer.camera import CAMERA_SCHEMA, VirtualCamera
from custom_components.virtual_layer.climate import CLIMATE_SCHEMA, VirtualClimate
from custom_components.virtual_layer.const import (
    ATTR_UNIQUE_ID,
    CONF_INITIAL_VALUE,
    CONF_NAME,
)
from custom_components.virtual_layer.fan import FAN_SCHEMA, VirtualFan
from custom_components.virtual_layer.humidifier import (
    HUMIDIFIER_SCHEMA,
    VirtualHumidifier,
)
from custom_components.virtual_layer.light import LIGHT_SCHEMA, VirtualLight
from custom_components.virtual_layer.lock import LOCK_SCHEMA, VirtualLock
from custom_components.virtual_layer.switch import SWITCH_SCHEMA, VirtualSwitch
from custom_components.virtual_layer.vacuum import VACUUM_SCHEMA, VirtualVacuum


pytestmark = pytest.mark.unit


def _config(schema, entity_id, initial_value):
    return schema({
        CONF_NAME: "Native Command Entity",
        ATTR_ENTITY_ID: entity_id,
        ATTR_UNIQUE_ID: f"{entity_id}.unique",
        CONF_INITIAL_VALUE: initial_value,
    })


@pytest.mark.parametrize(
    ("entity", "command"),
    [
        (
            VirtualSwitch(_config(SWITCH_SCHEMA, "switch.native", "off"), False),
            lambda entity: entity.async_turn_on(),
        ),
        (
            VirtualFan(_config(FAN_SCHEMA, "fan.native", "off"), False),
            lambda entity: entity.async_set_percentage(50),
        ),
        (
            VirtualHumidifier(
                _config(HUMIDIFIER_SCHEMA, "humidifier.native", "off"),
                False,
            ),
            lambda entity: entity.async_set_humidity(55),
        ),
        (
            VirtualClimate(_config(CLIMATE_SCHEMA, "climate.native", "off"), False),
            lambda entity: entity.async_set_temperature(temperature=21),
        ),
        (
            VirtualLight(_config(LIGHT_SCHEMA, "light.native", "off"), False),
            lambda entity: entity.async_turn_on(),
        ),
        (
            VirtualCamera(_config(CAMERA_SCHEMA, "camera.native", "off"), False),
            lambda entity: entity.async_turn_on(),
        ),
        (
            VirtualLock(None, _config(LOCK_SCHEMA, "lock.native", "locked"), False),
            lambda entity: entity.async_lock(),
        ),
    ],
)
async def test_native_commands_write_the_new_state(entity, command):
    entity._create_state(entity._config)
    entity.async_write_ha_state = Mock()

    await command(entity)

    entity.async_write_ha_state.assert_called_once()


async def test_virtual_vacuum_exposes_state_and_native_commands():
    entity = VirtualVacuum(
        VACUUM_SCHEMA({
            CONF_NAME: "Robot Vacuum",
            ATTR_ENTITY_ID: "vacuum.robot_vacuum",
            ATTR_UNIQUE_ID: "robot_vacuum.unique",
            CONF_INITIAL_VALUE: "docked",
            "battery_level": 82,
            "fan_speed_list": ["quiet", "standard", "turbo"],
        }),
        False,
    )
    entity._create_state(entity._config)
    entity.async_write_ha_state = Mock()

    assert isinstance(entity, StateVacuumEntity)
    assert entity.state == VacuumActivity.DOCKED
    assert VacuumEntityFeature.START in entity.supported_features
    assert VacuumEntityFeature.FAN_SPEED in entity.supported_features
    assert entity.battery_level == 82

    await entity.async_start()
    assert entity.state == VacuumActivity.CLEANING
    await entity.async_pause()
    assert entity.state == VacuumActivity.PAUSED
    await entity.async_return_to_base()
    assert entity.state == VacuumActivity.RETURNING
    await entity.async_set_fan_speed("turbo")
    assert entity.fan_speed == "turbo"
    await entity.async_send_command("clean_room", params={"room": 1})
    assert entity.extra_state_attributes["last_command"] == {
        "command": "clean_room",
        "params": {"room": 1},
    }
    assert entity.async_write_ha_state.call_count == 5


async def test_virtual_vacuum_rejects_unknown_fan_speed():
    entity = VirtualVacuum(
        VACUUM_SCHEMA({
            CONF_NAME: "Robot Vacuum",
            ATTR_ENTITY_ID: "vacuum.robot_vacuum_invalid",
            ATTR_UNIQUE_ID: "robot_vacuum_invalid.unique",
            CONF_INITIAL_VALUE: "idle",
            "fan_speed_list": ["quiet"],
        }),
        False,
    )
    entity._create_state(entity._config)
    entity.async_write_ha_state = Mock()

    with pytest.raises(ValueError):
        await entity.async_set_fan_speed("turbo")
