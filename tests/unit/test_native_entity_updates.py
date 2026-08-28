"""Ensure native Home Assistant commands publish virtual entity changes."""

import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from homeassistant.components.vacuum import (
    StateVacuumEntity,
    VacuumActivity,
    VacuumEntityFeature,
)
from homeassistant.components.fan import FanEntityFeature
from homeassistant.const import ATTR_ENTITY_ID

from custom_components.virtual_layer.binary_sensor import (
    BINARY_SENSOR_SCHEMA,
    VirtualBinarySensor,
)
from custom_components.virtual_layer.camera import CAMERA_SCHEMA, VirtualCamera
from custom_components.virtual_layer.climate import CLIMATE_SCHEMA, VirtualClimate
from custom_components.virtual_layer.const import (
    ATTR_UNIQUE_ID,
    CONF_COMMAND_ACTIONS,
    CONF_INITIAL_VALUE,
    CONF_NAME,
    CONF_PERSISTENT,
)
from custom_components.virtual_layer.cover import COVER_SCHEMA, VirtualCover
from custom_components.virtual_layer.fan import FAN_SCHEMA, VirtualFan
from custom_components.virtual_layer.humidifier import (
    HUMIDIFIER_SCHEMA,
    VirtualHumidifier,
)
from custom_components.virtual_layer.light import LIGHT_SCHEMA, VirtualLight
from custom_components.virtual_layer.lock import LOCK_SCHEMA, VirtualLock
from custom_components.virtual_layer.remote import (
    ENTITY_CLASS as VirtualRemote,
)
from custom_components.virtual_layer.remote import ENTITY_SCHEMA as REMOTE_SCHEMA
from custom_components.virtual_layer.sensor import SENSOR_SCHEMA, VirtualSensor
from custom_components.virtual_layer.siren import ENTITY_CLASS as VirtualSiren
from custom_components.virtual_layer.siren import ENTITY_SCHEMA as SIREN_SCHEMA
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


async def test_state_updates_use_thread_safe_scheduler_outside_event_loop(hass):
    entity = VirtualSensor(
        _config(SENSOR_SCHEMA, "sensor.thread_safe", "idle"),
        False,
    )
    entity.hass = hass
    entity.async_schedule_update_ha_state = Mock()
    entity.schedule_update_ha_state = Mock()

    entity._schedule_state_update()
    entity.async_schedule_update_ha_state.assert_called_once_with(
        force_refresh=False,
    )
    entity.schedule_update_ha_state.assert_not_called()

    entity.async_schedule_update_ha_state.reset_mock()
    await hass.async_add_executor_job(entity._schedule_state_update)
    entity.async_schedule_update_ha_state.assert_not_called()
    entity.schedule_update_ha_state.assert_called_once_with(force_refresh=False)


def test_virtual_entity_debug_logs_do_not_expose_configuration_or_state(caplog):
    secret = "secret-token-that-must-not-be-logged"
    with caplog.at_level(logging.DEBUG, logger="custom_components.virtual_layer.entity"):
        entity = VirtualSensor(
            SENSOR_SCHEMA(
                {
                    CONF_NAME: "Private Sensor",
                    ATTR_ENTITY_ID: "sensor.private",
                    ATTR_UNIQUE_ID: "private.unique",
                    CONF_INITIAL_VALUE: "idle",
                    CONF_PERSISTENT: True,
                    "command_actions": {
                        "set_value": {
                            "action": "rest_command.private",
                            "data": {"authorization": secret},
                        }
                    },
                }
            ),
            False,
        )
        entity._restore_state(
            SimpleNamespace(state=secret, attributes={"private_value": secret}),
            entity._config,
        )

    assert secret not in caplog.text


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


def test_virtual_camera_restores_explicit_power_instead_of_idle_state():
    entity = VirtualCamera(
        _config(CAMERA_SCHEMA, "camera.persistent", "on"),
        False,
    )

    entity._restore_state(
        SimpleNamespace(state="idle", attributes={"is_on": False}),
        entity._config,
    )
    entity._update_attributes()

    assert entity.is_on is False
    assert entity.state_attributes["is_on"] is False


@pytest.mark.parametrize("saved_value", [None, "invalid"])
def test_virtual_camera_uses_configured_power_for_legacy_or_bad_restore(saved_value):
    entity = VirtualCamera(
        _config(CAMERA_SCHEMA, "camera.persistent", "off"),
        False,
    )
    attributes = {} if saved_value is None else {"is_on": saved_value}

    entity._restore_state(
        SimpleNamespace(state="idle", attributes=attributes),
        entity._config,
    )

    assert entity.is_on is False


def test_virtual_camera_normalizes_restored_boolean_attributes():
    entity = VirtualCamera(
        CAMERA_SCHEMA(
            {
                **_config(CAMERA_SCHEMA, "camera.flags", "on"),
                "is_recording": True,
                "is_streaming": False,
                "motion_detection": True,
            }
        ),
        False,
    )

    entity._restore_state(
        SimpleNamespace(
            state="idle",
            attributes={
                "is_on": True,
                "is_recording": "false",
                "is_streaming": "true",
                "motion_detection": "invalid",
            },
        ),
        entity._config,
    )

    assert entity.is_recording is False
    assert entity.is_streaming is True
    assert entity.motion_detection_enabled is True


@pytest.mark.parametrize(
    "entity",
    [
        VirtualBinarySensor(
            _config(BINARY_SENSOR_SCHEMA, "binary_sensor.restore_on", "on"),
            False,
        ),
        VirtualSwitch(_config(SWITCH_SCHEMA, "switch.restore_on", "on"), False),
        VirtualFan(_config(FAN_SCHEMA, "fan.restore_on", "on"), False),
        VirtualHumidifier(
            _config(HUMIDIFIER_SCHEMA, "humidifier.restore_on", "on"),
            False,
        ),
        VirtualLight(_config(LIGHT_SCHEMA, "light.restore_on", "on"), False),
        VirtualSiren(_config(SIREN_SCHEMA, "siren.restore_on", "on"), False),
        VirtualRemote(_config(REMOTE_SCHEMA, "remote.restore_on", "on"), False),
    ],
)
def test_power_entities_restore_configured_state_after_unavailable(entity):
    entity._restore_state(
        SimpleNamespace(state="unavailable", attributes={"available": False}),
        entity._config,
    )

    assert entity.is_on is True


@pytest.mark.parametrize(
    "entity",
    [
        VirtualBinarySensor(
            _config(BINARY_SENSOR_SCHEMA, "binary_sensor.reject_bad", "on"),
            False,
        ),
        VirtualSwitch(_config(SWITCH_SCHEMA, "switch.reject_bad", "on"), False),
        VirtualFan(_config(FAN_SCHEMA, "fan.reject_bad", "on"), False),
        VirtualHumidifier(
            _config(HUMIDIFIER_SCHEMA, "humidifier.reject_bad", "on"),
            False,
        ),
        VirtualLight(_config(LIGHT_SCHEMA, "light.reject_bad", "on"), False),
        VirtualCamera(_config(CAMERA_SCHEMA, "camera.reject_bad", "on"), False),
        VirtualSiren(_config(SIREN_SCHEMA, "siren.reject_bad", "on"), False),
        VirtualRemote(_config(REMOTE_SCHEMA, "remote.reject_bad", "on"), False),
    ],
)
def test_power_entities_reject_invalid_state_without_turning_off(entity):
    entity._create_state(entity._config)

    with pytest.raises(ValueError):
        entity.set_state("definitely-not-a-power-state")

    assert entity.is_on is True


async def test_fan_action_without_speed_count_is_safe_from_executor(hass):
    entity = VirtualFan(
        FAN_SCHEMA(
            {
                **_config(FAN_SCHEMA, "fan.continuous", "off"),
                CONF_COMMAND_ACTIONS: {
                    "set_percentage": {"action": "script.set_fan_percentage"},
                },
            }
        ),
        False,
    )
    entity.hass = hass
    entity._create_state(entity._config)
    entity.async_schedule_update_ha_state = Mock()
    entity.schedule_update_ha_state = Mock()

    assert FanEntityFeature.SET_SPEED in entity.supported_features
    assert entity.speed_count == 100
    assert entity.percentage_step == 1

    await hass.async_add_executor_job(entity.set_state, "67")

    assert entity.percentage == 67
    entity.async_schedule_update_ha_state.assert_not_called()
    entity.schedule_update_ha_state.assert_called_once_with(force_refresh=False)


@pytest.mark.parametrize(("restored", "expected"), [(65, 65), ("invalid", None)])
def test_virtual_cover_restores_supported_tilt_position(restored, expected):
    entity = VirtualCover(
        COVER_SCHEMA(
            {
                CONF_NAME: "Persistent Cover",
                ATTR_ENTITY_ID: "cover.persistent",
                ATTR_UNIQUE_ID: "persistent-cover",
                CONF_INITIAL_VALUE: "open",
                CONF_COMMAND_ACTIONS: {
                    "set_cover_tilt_position": {
                        "action": "script.virtual_cover_tilt",
                    }
                },
            }
        ),
        False,
    )

    entity._restore_state(
        SimpleNamespace(
            state="open",
            attributes={
                "current_position": 100,
                "current_tilt_position": restored,
            },
        ),
        entity._config,
    )

    assert entity.current_cover_tilt_position == expected


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
    legacy_battery = getattr(
        VacuumEntityFeature,
        "BATTERY",
        VacuumEntityFeature(0),
    )
    assert not entity.supported_features & legacy_battery
    entity._update_attributes()
    assert entity.extra_state_attributes["battery_level"] == 82

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


def test_virtual_vacuum_rejects_invalid_state_without_stopping():
    entity = VirtualVacuum(
        VACUUM_SCHEMA({
            CONF_NAME: "Robot Vacuum",
            ATTR_ENTITY_ID: "vacuum.safe_state",
            ATTR_UNIQUE_ID: "safe_state.unique",
            CONF_INITIAL_VALUE: "cleaning",
        }),
        False,
    )
    entity._create_state(entity._config)
    entity.async_schedule_update_ha_state = Mock()

    with pytest.raises(ValueError, match="Invalid vacuum activity"):
        entity.set_state("cleanign")

    assert entity.activity == VacuumActivity.CLEANING
    entity.async_schedule_update_ha_state.assert_not_called()


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


@pytest.mark.parametrize(
    ("restored", "expected"),
    [
        (
            {"command": "clean_room", "params": {"room": 1}},
            {"command": "clean_room", "params": {"room": 1}},
        ),
        ({"command": "locate"}, {"command": "locate"}),
        ({"command": []}, None),
        ({"command": "clean_room", "params": "invalid"}, None),
        (["locate"], None),
    ],
)
def test_virtual_vacuum_restores_only_valid_last_command(restored, expected):
    entity = VirtualVacuum(
        VACUUM_SCHEMA({
            CONF_NAME: "Robot Vacuum",
            ATTR_ENTITY_ID: "vacuum.robot_vacuum_restore",
            ATTR_UNIQUE_ID: "robot_vacuum_restore.unique",
            CONF_INITIAL_VALUE: "docked",
        }),
        False,
    )

    entity._restore_state(
        SimpleNamespace(
            state="docked",
            attributes={"last_command": restored},
        ),
        entity._config,
    )
    entity._update_attributes()

    assert entity.extra_state_attributes.get("last_command") == expected
