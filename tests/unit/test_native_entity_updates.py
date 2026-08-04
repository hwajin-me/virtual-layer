"""Ensure native Home Assistant commands publish virtual entity changes."""

from unittest.mock import Mock

import pytest

from homeassistant.const import ATTR_ENTITY_ID

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
