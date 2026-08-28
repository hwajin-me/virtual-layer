"""Tests for native property templates and command actions."""

import asyncio
from datetime import date, datetime, time, timedelta
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import voluptuous as vol
from homeassistant.components.camera import CameraEntityFeature
from homeassistant.components.climate import ClimateEntityFeature, HVACMode
from homeassistant.components.climate.const import HVACAction
from homeassistant.components.cover import CoverEntityFeature
from homeassistant.components.fan import FanEntityFeature
from homeassistant.components.humidifier import (
    HumidifierAction,
    HumidifierDeviceClass,
    HumidifierEntityFeature,
)
from homeassistant.components.light import ATTR_FLASH, ColorMode, LightEntityFeature
from homeassistant.components.media_player import (
    MediaPlayerEntityFeature,
    MediaPlayerState,
    RepeatMode,
)
from homeassistant.components.remote import RemoteEntityFeature
from homeassistant.components.siren import SirenEntityFeature
from homeassistant.components.update import UpdateEntityFeature
from homeassistant.components.vacuum import VacuumActivity, VacuumEntityFeature
from homeassistant.components.water_heater import WaterHeaterEntityFeature
from homeassistant.const import ATTR_ENTITY_ID, UnitOfTemperature
from homeassistant.core import State
from homeassistant.helpers.template import Template
from homeassistant.util import dt as dt_util

from custom_components.virtual_layer.camera import CAMERA_SCHEMA, VirtualCamera
from custom_components.virtual_layer.climate import CLIMATE_SCHEMA, VirtualClimate
from custom_components.virtual_layer.const import (
    ATTR_UNIQUE_ID,
    CONF_ATTRIBUTE_TEMPLATES,
    CONF_AVAILABILITY_TEMPLATE,
    CONF_COMMAND_ACTIONS,
    CONF_INITIAL_VALUE,
    CONF_NAME,
    CONF_NATIVE_TEMPLATES,
    CONF_PERSISTENT,
    CONF_SOURCE_ENTITIES,
    CONF_VALUE_TEMPLATE,
    VIRTUAL_ENTITY_COMMANDS,
)
from custom_components.virtual_layer.cover import COVER_SCHEMA, VirtualCover
from custom_components.virtual_layer.device_tracker import (
    DEVICE_TRACKER_SCHEMA,
    VirtualDeviceTracker,
)
from custom_components.virtual_layer.entity import VirtualEntity
from custom_components.virtual_layer.fan import FAN_SCHEMA, VirtualFan
from custom_components.virtual_layer.generic import (
    ENTITY_SCHEMA as GENERIC_ENTITY_SCHEMA,
)
from custom_components.virtual_layer.generic import (
    GenericVirtualEntity,
    VirtualDate,
    VirtualDateTime,
    VirtualMediaPlayer,
    VirtualRemote,
    VirtualSelect,
    VirtualSiren,
    VirtualText,
    VirtualTime,
    VirtualUpdate,
    VirtualWaterHeater,
)
from custom_components.virtual_layer.humidifier import (
    HUMIDIFIER_SCHEMA,
    VirtualHumidifier,
)
from custom_components.virtual_layer.image import IMAGE_SCHEMA, VirtualImage
from custom_components.virtual_layer.light import (
    CONF_MATTER_LIGHT_TYPE,
    LIGHT_SCHEMA,
    VirtualLight,
)
from custom_components.virtual_layer.lock import LOCK_SCHEMA, VirtualLock
from custom_components.virtual_layer.number import NUMBER_SCHEMA, VirtualNumber
from custom_components.virtual_layer.sensor import SENSOR_SCHEMA, VirtualSensor
from custom_components.virtual_layer.vacuum import VACUUM_SCHEMA, VirtualVacuum
from custom_components.virtual_layer.valve import VALVE_SCHEMA, VirtualValve

pytestmark = pytest.mark.unit


def _base(entity_id: str, initial_value: str, **extra):
    return {
        CONF_NAME: "Templated Native Entity",
        ATTR_ENTITY_ID: entity_id,
        ATTR_UNIQUE_ID: f"{entity_id}.unique",
        CONF_INITIAL_VALUE: initial_value,
        **extra,
    }


def test_command_contracts_match_wrapped_platform_methods():
    for domain, expected_commands in sorted(VIRTUAL_ENTITY_COMMANDS.items()):
        module = import_module(f"custom_components.virtual_layer.{domain}")
        entity_classes = {
            value
            for value in vars(module).values()
            if isinstance(value, type)
            and issubclass(value, VirtualEntity)
            and value is not VirtualEntity
        }
        wrapped_commands = {
            method_name.removeprefix("async_")
            for entity_class in entity_classes
            for method_name in dir(entity_class)
            if method_name.startswith("async_")
            and getattr(
                getattr(entity_class, method_name, None),
                "_virtual_action_wrapped",
                False,
            )
        }

        assert wrapped_commands == expected_commands, domain


@pytest.mark.parametrize(
    ("matter_type", "expected_modes"),
    [
        ("on_off", {ColorMode.ONOFF}),
        ("dimmable", {ColorMode.BRIGHTNESS}),
        ("color_temperature", {ColorMode.COLOR_TEMP}),
        (
            "extended_color",
            {ColorMode.HS, ColorMode.XY, ColorMode.COLOR_TEMP},
        ),
    ],
)
def test_matter_light_types_have_fixed_standard_capabilities(
    matter_type, expected_modes
):
    light = VirtualLight(
        LIGHT_SCHEMA(
            _base(
                f"light.matter_{matter_type}",
                "off",
                **{CONF_MATTER_LIGHT_TYPE: matter_type},
            )
        ),
        False,
    )

    assert light.supported_color_modes == expected_modes
    assert LightEntityFeature.EFFECT not in light.supported_features
    assert LightEntityFeature.FLASH not in light.supported_features


@pytest.mark.asyncio
async def test_matter_light_rejects_nonstandard_effect_and_flash_calls():
    light = VirtualLight(
        LIGHT_SCHEMA(
            _base(
                "light.matter_strict",
                "off",
                **{CONF_MATTER_LIGHT_TYPE: "extended_color"},
            )
        ),
        False,
    )

    with pytest.raises(ValueError, match="effects or flash"):
        await light.async_turn_on(effect="rainbow")
    with pytest.raises(ValueError, match="flash"):
        await light.async_turn_off(**{ATTR_FLASH: "short"})


@pytest.mark.parametrize(
    ("schema", "config"),
    [
        (
            CLIMATE_SCHEMA,
            _base(
                "climate.boolean_config",
                "off",
                hvac_modes=["off", "heat"],
                current_temperature=True,
            ),
        ),
        (
            HUMIDIFIER_SCHEMA,
            _base("humidifier.boolean_config", "off", current_humidity=True),
        ),
        (FAN_SCHEMA, _base("fan.boolean_config", "off", speed_count=True)),
        (NUMBER_SCHEMA, _base("number.boolean_config", "1", min=True, max=10)),
        (VACUUM_SCHEMA, _base("vacuum.boolean_config", "docked", battery_level=True)),
        (COVER_SCHEMA, _base("cover.boolean_config", "open", open_close_duration=True)),
        (VALVE_SCHEMA, _base("valve.boolean_config", "open", open_close_duration=True)),
        (LIGHT_SCHEMA, _base("light.boolean_config", "on", initial_color_temp=True)),
        (LOCK_SCHEMA, _base("lock.boolean_config", "locked", test_jamming=True)),
    ],
)
def test_numeric_schemas_reject_boolean_values(schema, config):
    with pytest.raises(vol.Invalid):
        schema(config)


def test_numeric_native_templates_reject_boolean_values(hass):
    entities_and_fields = [
        (
            VirtualClimate(
                CLIMATE_SCHEMA(
                    _base(
                        "climate.boolean_numeric",
                        "off",
                        hvac_modes=["off", "heat"],
                    )
                ),
                False,
            ),
            "min_temp",
        ),
        (
            VirtualHumidifier(
                HUMIDIFIER_SCHEMA(_base("humidifier.boolean_numeric", "off")),
                False,
            ),
            "min_humidity",
        ),
        (
            VirtualFan(FAN_SCHEMA(_base("fan.boolean_numeric", "off")), False),
            "speed_count",
        ),
        (
            VirtualCamera(
                CAMERA_SCHEMA(_base("camera.boolean_numeric", "on")),
                False,
            ),
            "frame_interval",
        ),
        (
            VirtualCover(
                COVER_SCHEMA(_base("cover.boolean_numeric", "open")),
                False,
            ),
            "current_cover_tilt_position",
        ),
        (
            VirtualDeviceTracker(
                DEVICE_TRACKER_SCHEMA(
                    _base("device_tracker.boolean_numeric", "not_home")
                )
            ),
            "location_accuracy",
        ),
        (
            VirtualLight(
                LIGHT_SCHEMA(_base("light.boolean_numeric", "on")),
                False,
            ),
            "min_color_temp_kelvin",
        ),
        (
            VirtualNumber(
                NUMBER_SCHEMA(
                    _base("number.boolean_numeric", "1", min=0, max=10)
                ),
                False,
            ),
            "native_step",
        ),
        (
            VirtualSensor(
                SENSOR_SCHEMA(_base("sensor.boolean_numeric", "1")),
                False,
            ),
            "suggested_display_precision",
        ),
        (
            VirtualWaterHeater(
                GENERIC_ENTITY_SCHEMA(
                    _base("water_heater.boolean_numeric", "off")
                ),
                False,
            ),
            "target_temperature",
        ),
        (
            GenericVirtualEntity(
                GENERIC_ENTITY_SCHEMA(_base("weather.boolean_numeric", "sunny")),
                "weather",
                False,
            ),
            "wind_bearing",
        ),
    ]

    for entity, field_name in entities_and_fields:
        with pytest.raises(ValueError, match="number|numeric|integer|between"):
            entity._apply_native_template_value(field_name, True)

    with pytest.raises(ValueError, match="coordinates"):
        VirtualDeviceTracker._validated_coordinates(True, 127)
    with pytest.raises(ValueError, match="boolean"):
        entities_and_fields[8][0].set_state(True)


async def test_numeric_services_reject_boolean_values():
    climate = VirtualClimate(
        CLIMATE_SCHEMA(
            _base(
                "climate.boolean_service",
                "off",
                hvac_modes=["off", "heat"],
                min_temp=0,
                max_temp=30,
                min_humidity=0,
                max_humidity=100,
            )
        ),
        False,
    )
    humidifier = VirtualHumidifier(
        HUMIDIFIER_SCHEMA(
            _base(
                "humidifier.boolean_service",
                "off",
                min_humidity=0,
                max_humidity=100,
            )
        ),
        False,
    )
    fan = VirtualFan(
        FAN_SCHEMA(_base("fan.boolean_service", "off", speed_count=4)),
        False,
    )
    cover = VirtualCover(
        COVER_SCHEMA(_base("cover.boolean_service", "open")),
        False,
    )
    media = VirtualMediaPlayer(
        GENERIC_ENTITY_SCHEMA(_base("media_player.boolean_service", "idle")),
        False,
    )
    heater = VirtualWaterHeater(
        GENERIC_ENTITY_SCHEMA(
            _base(
                "water_heater.boolean_service",
                "off",
                min_temp=0,
                max_temp=100,
            )
        ),
        False,
    )
    for entity in (climate, humidifier, fan, cover, media, heater):
        entity._create_state(entity._config)
        entity.async_write_ha_state = Mock()

    with pytest.raises(ValueError):
        await climate.async_set_temperature(temperature=True)
    with pytest.raises(ValueError):
        await climate.async_set_humidity(True)
    with pytest.raises(ValueError):
        await humidifier.async_set_humidity(True)
    with pytest.raises(ValueError):
        await fan.async_set_percentage(True)
    with pytest.raises(ValueError):
        await cover.async_set_cover_tilt_position(tilt_position=True)
    with pytest.raises(ValueError):
        await media.async_set_volume_level(True)
    with pytest.raises(ValueError):
        await heater.async_set_temperature(temperature=True)
    with pytest.raises(ValueError, match="finite"):
        await heater.async_set_temperature(temperature=float("nan"))


async def test_numeric_commands_snap_mismatched_values_to_advertised_steps():
    fan = VirtualFan(
        FAN_SCHEMA(_base("fan.step_fixer", "off", speed_count=5)),
        False,
    )
    number = VirtualNumber(
        NUMBER_SCHEMA(
            _base(
                "number.step_fixer",
                "10",
                min=10,
                max=20,
                step=2.5,
            )
        ),
        False,
    )
    climate = VirtualClimate(
        CLIMATE_SCHEMA(
            _base(
                "climate.step_fixer",
                "off",
                hvac_modes=["off", "heat"],
                min_temp=10,
                max_temp=30,
                target_temperature_step=2,
                min_humidity=30,
                max_humidity=90,
                target_humidity_step=10,
            )
        ),
        False,
    )
    humidifier = VirtualHumidifier(
        HUMIDIFIER_SCHEMA(
            _base(
                "humidifier.step_fixer",
                "off",
                min_humidity=30,
                max_humidity=90,
                target_humidity_step=10,
            )
        ),
        False,
    )
    media = VirtualMediaPlayer(
        GENERIC_ENTITY_SCHEMA(_base("media_player.step_fixer", "idle")),
        False,
    )
    media._attr_volume_step = 0.2
    heater = VirtualWaterHeater(
        GENERIC_ENTITY_SCHEMA(
            _base(
                "water_heater.step_fixer",
                "off",
                min_temp=35,
                max_temp=85,
                target_temperature_step=5,
            )
        ),
        False,
    )
    for entity in (fan, number, climate, humidifier, media, heater):
        entity._create_state(entity._config)
        entity.async_write_ha_state = Mock()
        entity.async_schedule_update_ha_state = Mock()

    await fan.async_set_percentage(30)
    await number.async_set_native_value(14)
    await climate.async_set_temperature(temperature=15)
    await climate.async_set_humidity(55)
    await humidifier.async_set_humidity(54)
    await media.async_set_volume_level(0.31)
    await heater.async_set_temperature(temperature=43)

    assert fan.percentage == 40
    assert number.native_value == 15
    assert climate.target_temperature == 16
    assert climate.target_humidity == 60
    assert humidifier.target_humidity == 50
    assert media.volume_level == 0.4
    assert heater.target_temperature == 45


async def test_light_turn_on_rolls_back_all_values_when_validation_fails():
    light = VirtualLight(
        LIGHT_SCHEMA(
            _base(
                "light.atomic_turn_on",
                "off",
                support_color=True,
                initial_color=[20, 30],
                support_effect=True,
                initial_effect_list=["none", "rainbow"],
            )
        ),
        False,
    )
    light._create_state(light._config)
    light.async_write_ha_state = Mock()
    initial = (
        light.is_on,
        light.brightness,
        light.color_mode,
        light.hs_color,
        light.effect,
    )

    with pytest.raises(ValueError, match="effect"):
        await light.async_turn_on(
            brightness=200,
            hs_color=(120, 80),
            effect="invalid",
        )

    assert (
        light.is_on,
        light.brightness,
        light.color_mode,
        light.hs_color,
        light.effect,
    ) == initial
    light.async_write_ha_state.assert_not_called()


def test_climate_native_templates_render_lists_enums_and_numbers(hass):
    hass.states.async_set(
        "sensor.hvac_profile",
        "cool",
        {
            "fan_modes": ["auto", "turbo"],
            "preset_modes": ["none", "sleep"],
            "temperature": 22.5,
        },
    )
    entity = VirtualClimate(
        CLIMATE_SCHEMA(
            _base(
                "climate.templated",
                "off",
                hvac_modes=["off", "cool"],
                fan_modes=[],
                preset_modes=[],
                **{
                    CONF_NATIVE_TEMPLATES: {
                        "hvac_modes": "{{ ['off', 'heat', 'cool'] }}",
                        "hvac_mode": "{{ states('sensor.hvac_profile') }}",
                        "hvac_action": "{{ 'cooling' }}",
                        "fan_mode": "{{ 'turbo' }}",
                        "preset_mode": "{{ 'sleep' }}",
                        "fan_modes": "{{ state_attr('sensor.hvac_profile', 'fan_modes') }}",
                        "preset_modes": "{{ state_attr('sensor.hvac_profile', 'preset_modes') }}",
                        "swing_modes": "{{ ['off', 'vertical'] }}",
                        "swing_mode": "{{ 'vertical' }}",
                        "swing_horizontal_modes": "{{ ['off', 'wide'] }}",
                        "swing_horizontal_mode": "{{ 'wide' }}",
                        "current_temperature": "{{ 24.5 }}",
                        "target_temperature": "{{ state_attr('sensor.hvac_profile', 'temperature') }}",
                        "target_temperature_high": "{{ 27 }}",
                        "target_temperature_low": "{{ 19 }}",
                        "min_temp": "{{ 10 }}",
                        "max_temp": "{{ 32 }}",
                        "target_temperature_step": "{{ 0.5 }}",
                        "temperature_unit": "{{ '°F' }}",
                        "current_humidity": "{{ 46 }}",
                        "target_humidity": "{{ 52 }}",
                        "min_humidity": "{{ 20 }}",
                        "max_humidity": "{{ 80 }}",
                        "target_humidity_step": "{{ 2 }}",
                    }
                },
            )
        ),
        False,
    )
    entity.hass = hass
    entity._create_state(entity._config)
    entity.async_schedule_update_ha_state = Mock()

    entity._apply_templates()

    assert entity.hvac_mode == HVACMode.COOL
    assert entity.hvac_modes == [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL]
    assert entity.hvac_action == HVACAction.COOLING
    assert entity.fan_modes == ["auto", "turbo"]
    assert entity.fan_mode == "turbo"
    assert entity.preset_modes == ["none", "sleep"]
    assert entity.preset_mode == "sleep"
    assert entity.swing_modes == ["off", "vertical"]
    assert entity.swing_mode == "vertical"
    assert entity.swing_horizontal_modes == ["off", "wide"]
    assert entity.swing_horizontal_mode == "wide"
    assert entity.current_temperature == 24.5
    assert entity.target_temperature == 22.5
    assert entity.target_temperature_high == 27
    assert entity.target_temperature_low == 19
    assert entity.min_temp == 10
    assert entity.max_temp == 32
    assert entity.target_temperature_step == 0.5
    assert entity.temperature_unit == UnitOfTemperature.FAHRENHEIT
    assert entity.current_humidity == 46
    assert entity.target_humidity == 52
    assert entity.min_humidity == 20
    assert entity.max_humidity == 80
    assert entity._attr_target_humidity_step == 2
    assert ClimateEntityFeature.FAN_MODE in entity.supported_features
    assert ClimateEntityFeature.PRESET_MODE in entity.supported_features


@pytest.mark.parametrize("rendered", [None, False, "", "[]", [], "unavailable"])
def test_climate_keeps_last_hvac_modes_for_empty_transient_template(rendered):
    entity = VirtualClimate(
        CLIMATE_SCHEMA(
            _base(
                "climate.transient_modes",
                "cool",
                hvac_modes=["off", "cool"],
            )
        ),
        False,
    )
    entity._create_state(entity._config)

    assert entity._apply_native_template_value("hvac_modes", rendered) is False
    assert entity.hvac_modes == [HVACMode.OFF, HVACMode.COOL]


def test_climate_extracts_hvac_modes_from_rendered_source_state(hass):
    hass.states.async_set(
        "climate.source",
        "off",
        {"hvac_modes": [HVACMode.OFF, HVACMode.COOL, HVACMode.DRY]},
    )
    rendered_source = Template(
        "{{ states.climate.source }}",
        hass,
    ).async_render(parse_result=True)
    entity = VirtualClimate(
        CLIMATE_SCHEMA(
            _base(
                "climate.rendered_source",
                "off",
                hvac_modes=["off", "heat"],
            )
        ),
        False,
    )
    entity._create_state(entity._config)

    assert entity._apply_native_template_value("hvac_modes", rendered_source)
    assert entity.hvac_modes == [HVACMode.OFF, HVACMode.COOL, HVACMode.DRY]


def test_climate_repairs_legacy_enum_repr_native_template(hass):
    entity = VirtualClimate(
        CLIMATE_SCHEMA(
            _base(
                "climate.legacy_enum",
                "heat",
                hvac_modes=["off", "heat"],
                **{
                    CONF_NATIVE_TEMPLATES: {
                        "hvac_action": "{{ <HVACAction.HEATING: 'heating'> }}",
                    }
                },
            )
        ),
        False,
    )

    _render_native_templates(entity, hass)

    assert entity.hvac_action == HVACAction.HEATING


def test_fan_native_templates_control_capabilities_and_values(hass):
    hass.states.async_set("sensor.fan_speed", "42")
    entity = VirtualFan(
        FAN_SCHEMA(
            _base(
                "fan.templated",
                "off",
                **{
                    CONF_NATIVE_TEMPLATES: {
                        "is_on": "{{ true }}",
                        "speed_count": "{{ 5 }}",
                        "percentage": "{{ states('sensor.fan_speed') | int }}",
                        "preset_modes": "{{ ['quiet', 'boost'] }}",
                        "preset_mode": "{{ 'boost' }}",
                        "current_direction": "{{ 'reverse' }}",
                        "oscillating": "{{ true }}",
                    }
                },
            )
        ),
        False,
    )
    entity.hass = hass
    entity._create_state(entity._config)
    entity.async_schedule_update_ha_state = Mock()
    entity.async_write_ha_state = Mock()

    entity._apply_templates()

    assert entity.is_on is True
    assert entity.speed_count == 5
    assert entity.percentage == 40
    assert entity.preset_modes == ["quiet", "boost"]
    assert entity.preset_mode == "boost"
    assert entity.current_direction == "reverse"
    assert entity.oscillating is True
    assert FanEntityFeature.PRESET_MODE in entity.supported_features
    assert FanEntityFeature.DIRECTION in entity.supported_features
    assert FanEntityFeature.OSCILLATE in entity.supported_features


def test_humidifier_native_templates_render_target_action_and_modes(hass):
    hass.states.async_set("sensor.room_humidity", "61")
    entity = VirtualHumidifier(
        HUMIDIFIER_SCHEMA(
            _base(
                "humidifier.templated",
                "on",
                **{
                    CONF_NATIVE_TEMPLATES: {
                        "is_on": "{{ true }}",
                        "device_class": "{{ 'dehumidifier' }}",
                        "current_humidity": "{{ states('sensor.room_humidity') | float }}",
                        "target_humidity": "{{ 53 }}",
                        "min_humidity": "{{ 20 }}",
                        "max_humidity": "{{ 80 }}",
                        "target_humidity_step": "{{ 5 }}",
                        "available_modes": "{{ ['auto', 'sleep'] }}",
                        "mode": "{{ 'auto' }}",
                        "action": "{{ 'humidifying' }}",
                    }
                },
            )
        ),
        False,
    )
    entity.hass = hass
    entity._create_state(entity._config)
    entity.async_schedule_update_ha_state = Mock()

    entity._apply_templates()

    assert entity.is_on is True
    assert entity.device_class == HumidifierDeviceClass.DEHUMIDIFIER
    assert entity.current_humidity == 61
    assert entity.target_humidity == 53
    assert entity.min_humidity == 20
    assert entity.max_humidity == 80
    assert entity.target_humidity_step == 5
    assert entity.available_modes == ["auto", "sleep"]
    assert entity.mode == "auto"
    assert entity.action == HumidifierAction.HUMIDIFYING
    assert HumidifierEntityFeature.MODES in entity.supported_features


def test_hvac_fan_and_humidifier_templates_normalize_mode_whitespace(hass):
    climate = VirtualClimate(
        CLIMATE_SCHEMA(
            _base(
                "climate.whitespace",
                "off",
                hvac_modes=["off", "cool"],
                **{
                    CONF_NATIVE_TEMPLATES: {
                        "hvac_modes": "{{ [' off ', ' cool '] }}",
                        "hvac_mode": "{{ ' COOL ' }}",
                        "hvac_action": "{{ ' COOLING ' }}",
                        "fan_modes": "{{ [' auto ', ' turbo '] }}",
                        "fan_mode": "{{ ' turbo ' }}",
                        "temperature_unit": "{{ ' °F ' }}",
                    },
                },
            )
        ),
        False,
    )
    fan = VirtualFan(
        FAN_SCHEMA(
            _base(
                "fan.whitespace",
                "off",
                **{
                    CONF_NATIVE_TEMPLATES: {
                        "preset_modes": "{{ [' quiet ', ' boost '] }}",
                        "preset_mode": "{{ ' boost ' }}",
                        "current_direction": "{{ ' REVERSE ' }}",
                    },
                },
            )
        ),
        False,
    )
    humidifier = VirtualHumidifier(
        HUMIDIFIER_SCHEMA(
            _base(
                "humidifier.whitespace",
                "on",
                **{
                    CONF_NATIVE_TEMPLATES: {
                        "available_modes": "{{ [' auto ', ' sleep '] }}",
                        "mode": "{{ ' sleep ' }}",
                        "action": "{{ ' DRYING ' }}",
                        "device_class": "{{ ' DEHUMIDIFIER ' }}",
                    },
                },
            )
        ),
        False,
    )

    for entity in (climate, fan, humidifier):
        entity.hass = hass
        entity._create_state(entity._config)
        entity.async_schedule_update_ha_state = Mock()
        entity._apply_templates()

    assert climate.hvac_modes == [HVACMode.OFF, HVACMode.COOL]
    assert climate.hvac_mode == HVACMode.COOL
    assert climate.hvac_action == HVACAction.COOLING
    assert climate.fan_modes == ["auto", "turbo"]
    assert climate.fan_mode == "turbo"
    assert climate.temperature_unit == UnitOfTemperature.FAHRENHEIT
    assert fan.preset_modes == ["quiet", "boost"]
    assert fan.preset_mode == "boost"
    assert fan.current_direction == "reverse"
    assert humidifier.available_modes == ["auto", "sleep"]
    assert humidifier.mode == "sleep"
    assert humidifier.action == HumidifierAction.DRYING
    assert humidifier.device_class == HumidifierDeviceClass.DEHUMIDIFIER


@pytest.mark.parametrize("restored_state", ["unavailable", "unknown", "invalid"])
def test_climate_restore_uses_configured_initial_mode_for_invalid_state(
    restored_state,
):
    config = CLIMATE_SCHEMA(
        _base(
            "climate.restore_fallback",
            "cool",
            hvac_modes=["off", "cool"],
        )
    )
    entity = VirtualClimate(config, False)

    entity._restore_state(
        State("climate.restore_fallback", restored_state),
        config,
    )

    assert entity.hvac_mode == HVACMode.COOL


def test_attribute_template_preserves_structured_jinja_result(hass):
    entity = VirtualSensor(
        SENSOR_SCHEMA(
            _base(
                "sensor.structured_attribute",
                "ready",
                **{
                    CONF_ATTRIBUTE_TEMPLATES: {
                        "programs": "{{ ['cotton', 'eco'] }}",
                        "details": "{{ {'cycles': 3, 'active': true} }}",
                        "device_trackers": (
                            "{{ <DeviceTrackerSourceType.GPS: 'gps'> }}"
                        ),
                        "max": "{{ <LegacyLimit.MAX: 100> }}",
                    }
                },
            )
        ),
        False,
    )
    entity.hass = hass
    entity._create_state(entity._config)
    entity.async_schedule_update_ha_state = Mock()

    entity._apply_templates()

    assert entity.extra_state_attributes["programs"] == ["cotton", "eco"]
    assert entity.extra_state_attributes["details"] == {"cycles": 3, "active": True}
    assert entity.extra_state_attributes["device_trackers"] == "gps"
    assert entity.extra_state_attributes["max"] == 100


async def test_command_action_receives_native_arguments_and_can_disable_optimism(hass):
    calls = []

    async def _capture(call):
        calls.append(dict(call.data))

    hass.services.async_register("virtual_test", "capture", _capture)
    entity = VirtualFan(
        FAN_SCHEMA(
            _base(
                "fan.action_target",
                "off",
                speed_count=3,
                **{
                    CONF_COMMAND_ACTIONS: {
                        "set_percentage": {
                            "optimistic": False,
                            "sequence": [
                                {
                                    "action": "virtual_test.capture",
                                    "data": {
                                        "requested": "{{ percentage }}",
                                        "legacy_limit": (
                                            "{{ <LegacyLimit.MAX: 100> }}"
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                },
            )
        ),
        False,
    )
    entity.hass = hass
    entity._create_state(entity._config)
    entity.async_write_ha_state = Mock()

    await entity.async_set_percentage(73)

    assert calls == [{"requested": 67, "legacy_limit": 100}]
    assert entity.percentage == 0
    entity.async_write_ha_state.assert_not_called()


async def test_command_action_defaults_to_optimistic_native_update(hass):
    hass.services.async_register("virtual_test", "capture", Mock())
    entity = VirtualFan(
        FAN_SCHEMA(
            _base(
                "fan.optimistic",
                "off",
                speed_count=3,
                **{
                    CONF_COMMAND_ACTIONS: {
                        "set_percentage": [
                            {"action": "virtual_test.capture"},
                        ]
                    }
                },
            )
        ),
        False,
    )
    entity.hass = hass
    entity._create_state(entity._config)
    entity.async_write_ha_state = Mock()

    await entity.async_set_percentage(35)

    assert entity.percentage == 33
    entity.async_write_ha_state.assert_called_once()


async def test_command_actions_run_for_independent_concurrent_commands(hass):
    calls = []
    both_started = asyncio.Event()
    release = asyncio.Event()

    async def _capture(call):
        calls.append(call.data["requested"])
        if len(calls) == 2:
            both_started.set()
        await release.wait()

    hass.services.async_register("virtual_test", "capture_parallel", _capture)
    entity = VirtualFan(
        FAN_SCHEMA(
            _base(
                "fan.parallel_actions",
                "off",
                speed_count=3,
                **{
                    CONF_COMMAND_ACTIONS: {
                        "set_percentage": {
                            "optimistic": False,
                            "sequence": [{
                                "action": "virtual_test.capture_parallel",
                                "data": {"requested": "{{ percentage }}"},
                            }],
                        }
                    }
                },
            )
        ),
        False,
    )
    entity.hass = hass
    entity._create_state(entity._config)
    entity.async_write_ha_state = Mock()

    tasks = [
        asyncio.create_task(entity.async_set_percentage(25)),
        asyncio.create_task(entity.async_set_percentage(75)),
    ]
    await asyncio.wait_for(both_started.wait(), 1)
    release.set()
    await asyncio.gather(*tasks)

    assert sorted(calls) == [33, 67]
    assert entity.percentage == 0


async def test_command_action_chain_still_prevents_recursive_reentry(hass):
    calls = 0
    entity = None

    async def _reenter(_call):
        nonlocal calls
        calls += 1
        await entity.async_set_percentage(60)

    hass.services.async_register("virtual_test", "reenter", _reenter)
    entity = VirtualFan(
        FAN_SCHEMA(
            _base(
                "fan.recursive_action",
                "off",
                speed_count=3,
                **{
                    CONF_COMMAND_ACTIONS: {
                        "set_percentage": [{"action": "virtual_test.reenter"}],
                    }
                },
            )
        ),
        False,
    )
    entity.hass = hass
    entity._create_state(entity._config)
    entity.async_write_ha_state = Mock()

    await entity.async_set_percentage(30)

    assert calls == 1
    assert entity.percentage == 33


async def test_command_action_flattens_kwargs_for_climate_templates(hass):
    calls = []

    async def _capture(call):
        calls.append(dict(call.data))

    hass.services.async_register("virtual_test", "capture_temperature", _capture)
    entity = VirtualClimate(
        CLIMATE_SCHEMA(
            _base(
                "climate.action_target",
                "off",
                hvac_modes=["off", "heat"],
                **{
                    CONF_COMMAND_ACTIONS: {
                        "set_temperature": [
                            {
                                "action": "virtual_test.capture_temperature",
                                "data": {"requested": "{{ temperature }}"},
                            }
                        ]
                    }
                },
            )
        ),
        False,
    )
    entity.hass = hass
    entity._create_state(entity._config)
    entity.async_write_ha_state = Mock()

    await entity.async_set_temperature(temperature=24)

    assert calls == [{"requested": 24}]
    assert entity.target_temperature == 24


async def _exercise_source_proxy(hass, domain, entity, commands):
    calls = []

    def _register(service):
        async def _capture(call):
            calls.append((service, dict(call.data)))

        hass.services.async_register(domain, service, _capture)

    for service, _, _ in commands:
        _register(service)
    entity.hass = hass
    entity._create_state(entity._config)
    entity.async_write_ha_state = Mock()

    for _, command, _ in commands:
        await command(entity)

    assert calls == [
        (
            service,
            {**data, ATTR_ENTITY_ID: [f"{domain}.source"]},
        )
        for service, _, data in commands
    ]
    assert entity.async_write_ha_state.call_count == len(commands)


async def test_climate_commands_proxy_to_source_entity(hass):
    entity = VirtualClimate(
        CLIMATE_SCHEMA(
            _base(
                "climate.proxy",
                "off",
                hvac_modes=["off", "cool"],
                fan_modes=["auto", "turbo"],
                preset_modes=["none", "sleep"],
                swing_modes=["off", "vertical"],
                min_temp=10,
                max_temp=30,
                **{CONF_SOURCE_ENTITIES: ["climate.source"]},
            )
        ),
        False,
    )
    await _exercise_source_proxy(hass, "climate", entity, [
        (
            "set_hvac_mode",
            lambda item: item.async_set_hvac_mode(HVACMode.COOL),
            {"hvac_mode": HVACMode.COOL},
        ),
        (
            "set_temperature",
            lambda item: item.async_set_temperature(temperature=23),
            {"temperature": 23},
        ),
        (
            "set_fan_mode",
            lambda item: item.async_set_fan_mode("turbo"),
            {"fan_mode": "turbo"},
        ),
        (
            "set_preset_mode",
            lambda item: item.async_set_preset_mode("sleep"),
            {"preset_mode": "sleep"},
        ),
        (
            "set_swing_mode",
            lambda item: item.async_set_swing_mode("vertical"),
            {"swing_mode": "vertical"},
        ),
    ])


async def test_fan_and_humidifier_commands_proxy_to_source_entities(hass):
    fan = VirtualFan(
        FAN_SCHEMA(
            _base(
                "fan.proxy",
                "off",
                speed_count=5,
                modes=["auto", "sleep"],
                oscillate=True,
                direction=True,
                **{CONF_SOURCE_ENTITIES: ["fan.source"]},
            )
        ),
        False,
    )
    await _exercise_source_proxy(hass, "fan", fan, [
        (
            "set_percentage",
            lambda item: item.async_set_percentage(60),
            {"percentage": 60},
        ),
        (
            "set_preset_mode",
            lambda item: item.async_set_preset_mode("sleep"),
            {"preset_mode": "sleep"},
        ),
        (
            "set_direction",
            lambda item: item.async_set_direction("reverse"),
            {"direction": "reverse"},
        ),
        (
            "oscillate",
            lambda item: item.async_oscillate(True),
            {"oscillating": True},
        ),
    ])

    humidifier = VirtualHumidifier(
        HUMIDIFIER_SCHEMA(
            _base(
                "humidifier.proxy",
                "off",
                min_humidity=20,
                max_humidity=80,
                modes=["auto", "sleep"],
                **{CONF_SOURCE_ENTITIES: ["humidifier.source"]},
            )
        ),
        False,
    )
    await _exercise_source_proxy(hass, "humidifier", humidifier, [
        (
            "set_humidity",
            lambda item: item.async_set_humidity(55),
            {"humidity": 55},
        ),
        (
            "set_mode",
            lambda item: item.async_set_mode("sleep"),
            {"mode": "sleep"},
        ),
    ])


async def test_vacuum_and_camera_commands_proxy_to_source_entities(hass):
    vacuum = VirtualVacuum(
        VACUUM_SCHEMA(
            _base(
                "vacuum.proxy",
                "docked",
                fan_speed_list=["quiet", "turbo"],
                **{CONF_SOURCE_ENTITIES: ["vacuum.source"]},
            )
        ),
        False,
    )
    await _exercise_source_proxy(hass, "vacuum", vacuum, [
        ("start", lambda item: item.async_start(), {}),
        (
            "set_fan_speed",
            lambda item: item.async_set_fan_speed("turbo"),
            {"fan_speed": "turbo"},
        ),
        (
            "send_command",
            lambda item: item.async_send_command(
                "clean_room",
                params={"room": 1},
            ),
            {"command": "clean_room", "params": {"room": 1}},
        ),
        ("return_to_base", lambda item: item.async_return_to_base(), {}),
    ])

    camera = VirtualCamera(
        CAMERA_SCHEMA(
            _base(
                "camera.proxy",
                "on",
                **{CONF_SOURCE_ENTITIES: ["camera.source"]},
            )
        ),
        False,
    )
    await _exercise_source_proxy(hass, "camera", camera, [
        ("turn_off", lambda item: item.async_turn_off(), {}),
        ("turn_on", lambda item: item.async_turn_on(), {}),
        (
            "enable_motion_detection",
            lambda item: item.async_enable_motion_detection(),
            {},
        ),
        (
            "disable_motion_detection",
            lambda item: item.async_disable_motion_detection(),
            {},
        ),
    ])


async def test_unconfigured_native_command_proxies_to_all_same_domain_sources(hass):
    calls = []

    async def _capture(call):
        calls.append(dict(call.data))

    hass.services.async_register("fan", "set_percentage", _capture)
    entity = VirtualFan(
        FAN_SCHEMA(
            _base(
                "fan.proxy",
                "off",
                speed_count=5,
                **{
                    CONF_SOURCE_ENTITIES: [
                        "fan.first",
                        "sensor.unrelated",
                        "fan.second",
                        "fan.first",
                    ]
                },
            )
        ),
        False,
    )
    entity.hass = hass
    entity._create_state(entity._config)
    entity.async_write_ha_state = Mock()

    await entity.async_set_percentage(40)

    assert calls == [{
        "percentage": 40,
        ATTR_ENTITY_ID: ["fan.first", "fan.second"],
    }]


async def test_generated_command_action_forwards_complete_command_data(hass):
    calls = []

    async def _capture(call):
        calls.append(dict(call.data))

    hass.services.async_register("fan", "set_percentage", _capture)
    entity = VirtualFan(
        FAN_SCHEMA(
            _base(
                "fan.generated_action",
                "off",
                speed_count=5,
                **{
                    CONF_COMMAND_ACTIONS: {
                        "set_percentage": [{
                            "action": "fan.set_percentage",
                            "data": "{{ command_data }}",
                            "target": {ATTR_ENTITY_ID: "fan.source"},
                        }],
                    },
                },
            )
        ),
        False,
    )
    entity.hass = hass
    entity._create_state(entity._config)
    entity.async_write_ha_state = Mock()

    await entity.async_set_percentage(75)

    assert calls == [{"percentage": 80, ATTR_ENTITY_ID: ["fan.source"]}]


@pytest.mark.parametrize(
    ("domain", "entity_class", "initial_value", "value", "service_field"),
    [
        ("date", VirtualDate, "2026-08-24", date(2026, 8, 25), "date"),
        ("time", VirtualTime, "10:00:00", time(11, 30), "time"),
        (
            "datetime",
            VirtualDateTime,
            "2026-08-24T10:00:00+09:00",
            datetime.fromisoformat("2026-08-25T11:30:00+09:00"),
            "datetime",
        ),
    ],
)
async def test_temporal_commands_use_home_assistant_service_field_names(
    hass,
    domain,
    entity_class,
    initial_value,
    value,
    service_field,
):
    calls = []

    async def _capture(call):
        calls.append(dict(call.data))

    hass.services.async_register(domain, "set_value", _capture)
    entity = entity_class(
        GENERIC_ENTITY_SCHEMA(
            _base(
                f"{domain}.proxy",
                initial_value,
                **{CONF_SOURCE_ENTITIES: [f"{domain}.source"]},
            )
        ),
        False,
    )
    entity.hass = hass
    entity._create_state(entity._config)
    entity.async_write_ha_state = Mock()

    await entity.async_set_value(value)

    assert calls == [{
        service_field: value,
        ATTR_ENTITY_ID: [f"{domain}.source"],
    }]


def _render_native_templates(entity, hass):
    entity.hass = hass
    entity._create_state(entity._config)
    entity.async_schedule_update_ha_state = Mock()
    entity.schedule_update_ha_state = Mock()
    entity.async_write_ha_state = Mock()
    entity._apply_templates()


@pytest.mark.parametrize(
    ("factory", "entity_id", "initial_value", "feature"),
    [
        (
            lambda config: VirtualCamera(CAMERA_SCHEMA(config), False),
            "camera.feature_copy",
            "on",
            CameraEntityFeature.STREAM,
        ),
        (
            lambda config: VirtualCover(COVER_SCHEMA(config), False),
            "cover.feature_copy",
            "open",
            CoverEntityFeature.OPEN,
        ),
        (
            lambda config: VirtualMediaPlayer(GENERIC_ENTITY_SCHEMA(config), False),
            "media_player.feature_copy",
            "idle",
            MediaPlayerEntityFeature.TURN_ON,
        ),
        (
            lambda config: VirtualSiren(GENERIC_ENTITY_SCHEMA(config), False),
            "siren.feature_copy",
            "off",
            SirenEntityFeature.TURN_OFF,
        ),
        (
            lambda config: VirtualUpdate(GENERIC_ENTITY_SCHEMA(config), False),
            "update.feature_copy",
            "1.0.0",
            UpdateEntityFeature.RELEASE_NOTES,
        ),
        (
            lambda config: VirtualWaterHeater(GENERIC_ENTITY_SCHEMA(config), False),
            "water_heater.feature_copy",
            "off",
            WaterHeaterEntityFeature.OPERATION_MODE,
        ),
    ],
)
def test_supported_feature_templates_are_authoritative(
    hass,
    factory,
    entity_id,
    initial_value,
    feature,
):
    entity = factory(
        _base(
            entity_id,
            initial_value,
            **{
                CONF_NATIVE_TEMPLATES: {
                    "supported_features": f"{{{{ {int(feature)} }}}}",
                }
            },
        )
    )

    _render_native_templates(entity, hass)

    assert entity.supported_features == feature


@pytest.mark.parametrize(
    "factory",
    [
        lambda: VirtualCover(
            COVER_SCHEMA(_base("cover.invalid_features", "open")),
            False,
        ),
        lambda: VirtualMediaPlayer(
            GENERIC_ENTITY_SCHEMA(_base("media_player.invalid_features", "idle")),
            False,
        ),
    ],
)
def test_supported_feature_templates_reject_boolean_values(factory):
    with pytest.raises(ValueError, match="non-negative integer"):
        factory()._apply_native_template_value("supported_features", True)


def test_unavailable_source_preserves_strict_native_values_until_recovery(
    hass,
    caplog,
):
    source_entity_id = "number.strict_source"
    hass.states.async_set(source_entity_id, "25")
    number = VirtualNumber(
        NUMBER_SCHEMA(
            _base(
                "number.strict_copy",
                "10",
                min=0,
                max=100,
                **{
                    CONF_AVAILABILITY_TEMPLATE: (
                        f"{{{{ states({source_entity_id!r}) not in "
                        f"['unknown', 'unavailable'] }}}}"
                    ),
                    CONF_VALUE_TEMPLATE: f"{{{{ states({source_entity_id!r}) }}}}",
                    CONF_NATIVE_TEMPLATES: {
                        "native_value": f"{{{{ states({source_entity_id!r}) }}}}",
                    },
                },
            )
        ),
        False,
    )
    number.hass = hass
    number.async_schedule_update_ha_state = Mock()
    number.async_write_ha_state = Mock()
    number._create_state(number._config)

    number._apply_templates()
    assert number.available is True
    assert number.native_value == 25

    caplog.clear()
    hass.states.async_set(source_entity_id, "unavailable")
    number._apply_templates()
    assert number.available is False
    assert number.native_value == 25
    assert "Unable to render" not in caplog.text

    hass.states.async_set(source_entity_id, "40")
    number._apply_templates()
    assert number.available is True
    assert number.native_value == 40


@pytest.mark.asyncio
async def test_persistent_entities_restore_against_templated_capabilities(hass):
    select = VirtualSelect(
        GENERIC_ENTITY_SCHEMA(
            _base(
                "select.restored_dynamic",
                "old",
                options=["old"],
                **{
                    CONF_PERSISTENT: True,
                    CONF_NATIVE_TEMPLATES: {
                        "options": "{{ ['eco', 'turbo'] }}",
                    },
                },
            )
        ),
        False,
    )
    number = VirtualNumber(
        NUMBER_SCHEMA(
            _base(
                "number.restored_dynamic",
                "10",
                min=0,
                max=100,
                **{
                    CONF_PERSISTENT: True,
                    CONF_NATIVE_TEMPLATES: {
                        "max": "{{ 200 }}",
                    },
                },
            )
        ),
        False,
    )
    light = VirtualLight(
        LIGHT_SCHEMA(
            _base(
                "light.restored_dynamic",
                "on",
                support_effect=False,
                **{
                    CONF_PERSISTENT: True,
                    CONF_NATIVE_TEMPLATES: {
                        "effects": "{{ ['none', 'rainbow'] }}",
                    },
                },
            )
        ),
        False,
    )
    remote = VirtualRemote(
        GENERIC_ENTITY_SCHEMA(
            _base(
                "remote.restored_dynamic",
                "off",
                **{
                    CONF_PERSISTENT: True,
                    CONF_NATIVE_TEMPLATES: {
                        "activity_list": "{{ ['TV', 'Music'] }}",
                    },
                },
            )
        ),
        False,
    )
    media = VirtualMediaPlayer(
        GENERIC_ENTITY_SCHEMA(
            _base(
                "media_player.restored_dynamic",
                "idle",
                **{
                    CONF_PERSISTENT: True,
                    CONF_NATIVE_TEMPLATES: {
                        "source_list": "{{ ['TV', 'Radio'] }}",
                    },
                },
            )
        ),
        False,
    )
    water_heater = VirtualWaterHeater(
        GENERIC_ENTITY_SCHEMA(
            _base(
                "water_heater.restored_dynamic",
                "off",
                **{
                    CONF_PERSISTENT: True,
                    CONF_NATIVE_TEMPLATES: {
                        "operation_list": "{{ ['off', 'eco'] }}",
                    },
                },
            )
        ),
        False,
    )
    fan = VirtualFan(
        FAN_SCHEMA(
            _base(
                "fan.restored_dynamic",
                "off",
                **{
                    CONF_PERSISTENT: True,
                    CONF_NATIVE_TEMPLATES: {
                        "preset_modes": "{{ ['quiet', 'boost'] }}",
                    },
                },
            )
        ),
        False,
    )
    humidifier = VirtualHumidifier(
        HUMIDIFIER_SCHEMA(
            _base(
                "humidifier.restored_dynamic",
                "off",
                **{
                    CONF_PERSISTENT: True,
                    CONF_NATIVE_TEMPLATES: {
                        "available_modes": "{{ ['normal', 'dry'] }}",
                    },
                },
            )
        ),
        False,
    )
    climate = VirtualClimate(
        CLIMATE_SCHEMA(
            _base(
                "climate.restored_dynamic",
                "off",
                hvac_modes=["off", "cool"],
                **{
                    CONF_PERSISTENT: True,
                    CONF_NATIVE_TEMPLATES: {
                        "fan_modes": "{{ ['auto', 'turbo'] }}",
                    },
                },
            )
        ),
        False,
    )
    cases = (
        (select, SimpleNamespace(state="turbo", attributes={})),
        (number, SimpleNamespace(state="150", attributes={})),
        (
            light,
            SimpleNamespace(
                state="on",
                attributes={"color_mode": "brightness", "effect": "rainbow"},
            ),
        ),
        (
            remote,
            SimpleNamespace(state="on", attributes={"current_activity": "Music"}),
        ),
        (
            media,
            SimpleNamespace(state="playing", attributes={"source": "Radio"}),
        ),
        (water_heater, SimpleNamespace(state="eco", attributes={})),
        (
            fan,
            SimpleNamespace(state="on", attributes={"preset_mode": "boost"}),
        ),
        (
            humidifier,
            SimpleNamespace(state="on", attributes={"mode": "dry"}),
        ),
        (
            climate,
            SimpleNamespace(state="cool", attributes={"fan_mode": "turbo"}),
        ),
    )

    for entity, restored_state in cases:
        entity.hass = hass
        entity.async_get_last_state = AsyncMock(return_value=restored_state)
        entity.async_schedule_update_ha_state = Mock()
        entity.async_write_ha_state = Mock()
        await entity.async_added_to_hass()

    assert select.options == ["eco", "turbo"]
    assert select.current_option == "turbo"
    assert number.native_max_value == 200
    assert number.native_value == 150
    assert light.effect_list is None
    assert light.effect is None
    assert LightEntityFeature.EFFECT not in light.supported_features
    assert remote.activity_list == ["TV", "Music"]
    assert remote.current_activity == "Music"
    assert media.source_list == ["TV", "Radio"]
    assert media.source == "Radio"
    assert water_heater.operation_list == ["off", "eco"]
    assert water_heater.current_operation == "eco"
    assert fan.preset_modes == ["quiet", "boost"]
    assert fan.preset_mode == "boost"
    assert humidifier.available_modes == ["normal", "dry"]
    assert humidifier.mode == "dry"
    assert climate.fan_modes == ["auto", "turbo"]
    assert climate.fan_mode == "turbo"

    for entity, _restored_state in cases:
        await entity.async_will_remove_from_hass()


def test_select_and_text_templates_validate_dynamic_contracts(hass):
    select = VirtualSelect(
        GENERIC_ENTITY_SCHEMA(
            _base(
                "select.dynamic",
                "old",
                options=["old"],
                **{
                    CONF_NATIVE_TEMPLATES: {
                        "options": "{{ ['eco', 'turbo'] }}",
                        "current_option": "{{ 'turbo' }}",
                    }
                },
            )
        ),
        False,
    )
    text = VirtualText(
        GENERIC_ENTITY_SCHEMA(
            _base(
                "text.dynamic",
                "legacy",
                min=0,
                max=255,
                **{
                    CONF_NATIVE_TEMPLATES: {
                        "min": "{{ 2 }}",
                        "max": "{{ 4 }}",
                        "pattern": "{{ '[A-Z]+' }}",
                        "value": "{{ 'OK' }}",
                    }
                },
            )
        ),
        False,
    )

    _render_native_templates(select, hass)
    _render_native_templates(text, hass)

    assert select.options == ["eco", "turbo"]
    assert select.current_option == "turbo"
    assert text.native_min == 2
    assert text.native_max == 4
    assert text.native_value == "OK"
    assert text.pattern == "[A-Z]+"


def test_media_remote_and_siren_templates_refresh_features(hass):
    media = VirtualMediaPlayer(
        GENERIC_ENTITY_SCHEMA(
            _base(
                "media_player.dynamic",
                "off",
                **{
                    CONF_NATIVE_TEMPLATES: {
                        "source_list": "{{ ['HDMI 1', 'TV'] }}",
                        "source": "{{ 'HDMI 1' }}",
                        "sound_mode_list": "{{ ['movie', 'music'] }}",
                        "sound_mode": "{{ 'movie' }}",
                        "volume_level": "{{ 0.75 }}",
                        "volume_step": "{{ 0.05 }}",
                        "media_title": "{{ 'Virtual Track' }}",
                        "media_duration": "{{ 240 }}",
                        "media_position": "{{ 42.5 }}",
                        "media_position_updated_at": "{{ '2026-08-11T10:00:00+09:00' }}",
                        "media_image_remotely_accessible": "{{ true }}",
                        "group_members": "{{ ['media_player.dynamic'] }}",
                        "shuffle": "{{ true }}",
                        "repeat": "{{ 'all' }}",
                        "state": "{{ 'playing' }}",
                    }
                },
            )
        ),
        False,
    )
    remote = VirtualRemote(
        GENERIC_ENTITY_SCHEMA(
            _base(
                "remote.dynamic",
                "on",
                **{
                    CONF_NATIVE_TEMPLATES: {
                        "activity_list": "{{ ['Watch TV', 'Music'] }}",
                        "current_activity": "{{ 'Music' }}",
                    }
                },
            )
        ),
        False,
    )
    siren = VirtualSiren(
        GENERIC_ENTITY_SCHEMA(
            _base(
                "siren.dynamic",
                "off",
                **{
                    CONF_NATIVE_TEMPLATES: {
                        "available_tones": "{{ ['alarm', 'chime'] }}",
                        "support_volume": "{{ false }}",
                        "support_duration": "{{ false }}",
                    }
                },
            )
        ),
        False,
    )

    for entity in (media, remote, siren):
        _render_native_templates(entity, hass)

    assert media.state == MediaPlayerState.PLAYING
    assert media.source == "HDMI 1"
    assert media.volume_level == 0.75
    assert media.volume_step == 0.05
    assert media.sound_mode == "movie"
    assert media.media_title == "Virtual Track"
    assert media.media_duration == 240
    assert media.media_position == 42.5
    assert media.media_position_updated_at.isoformat() == "2026-08-11T10:00:00+09:00"
    assert media.media_image_remotely_accessible is True
    assert media.group_members == ["media_player.dynamic"]
    assert media.shuffle is True
    assert media.repeat == RepeatMode.ALL
    assert MediaPlayerEntityFeature.SELECT_SOURCE in media.supported_features
    assert MediaPlayerEntityFeature.SELECT_SOUND_MODE in media.supported_features
    assert MediaPlayerEntityFeature.SHUFFLE_SET in media.supported_features
    assert MediaPlayerEntityFeature.REPEAT_SET in media.supported_features
    assert remote.current_activity == "Music"
    assert RemoteEntityFeature.ACTIVITY in remote.supported_features
    assert siren.available_tones == ["alarm", "chime"]
    assert SirenEntityFeature.TONES in siren.supported_features
    assert SirenEntityFeature.VOLUME_SET not in siren.supported_features
    assert SirenEntityFeature.DURATION not in siren.supported_features


async def test_media_player_sound_shuffle_and_repeat_services_update_native_values(
    hass,
):
    media = VirtualMediaPlayer(
        GENERIC_ENTITY_SCHEMA(
            _base(
                "media_player.controls",
                "idle",
                **{
                    CONF_NATIVE_TEMPLATES: {
                        "sound_mode_list": "{{ ['movie', 'music'] }}",
                        "sound_mode": "{{ 'movie' }}",
                        "shuffle": "{{ false }}",
                        "repeat": "{{ 'off' }}",
                    },
                },
            )
        ),
        False,
    )
    _render_native_templates(media, hass)
    media.async_write_ha_state = Mock()

    await media.async_select_sound_mode("music")
    await media.async_set_shuffle(True)
    await media.async_set_repeat(RepeatMode.ONE)

    assert media.sound_mode == "music"
    assert media.shuffle is True
    assert media.repeat == RepeatMode.ONE
    assert media.async_write_ha_state.call_count == 3


def test_media_player_restores_sound_shuffle_and_repeat_native_values(hass):
    media = VirtualMediaPlayer(
        GENERIC_ENTITY_SCHEMA(
            _base(
                "media_player.restore_controls",
                "idle",
                source_list=["TV", "Radio"],
            )
        ),
        False,
    )
    media.hass = hass

    media._restore_state(
        SimpleNamespace(
            state="playing",
            attributes={
                "available": True,
                "sound_mode": " music ",
                "shuffle": True,
                "repeat": "one",
            },
        ),
        media._config,
    )

    assert media.sound_mode == "music"
    assert media.shuffle is True
    assert media.repeat == RepeatMode.ONE


def test_water_heater_and_update_templates_reconcile_ranges_and_features(hass):
    heater = VirtualWaterHeater(
        GENERIC_ENTITY_SCHEMA(
            _base(
                "water_heater.dynamic",
                "off",
                **{
                    CONF_NATIVE_TEMPLATES: {
                        "operation_list": "{{ ['eco'] }}",
                        "state": "{{ 'eco' }}",
                        "min_temp": "{{ 45 }}",
                        "max_temp": "{{ 60 }}",
                        "current_temperature": "{{ 80 }}",
                        "temperature": "{{ 30 }}",
                        "target_temperature_low": "{{ 48 }}",
                        "target_temperature_high": "{{ 58 }}",
                        "is_away_mode_on": "{{ true }}",
                        "precision": "{{ 0.5 }}",
                    }
                },
            )
        ),
        False,
    )
    update = VirtualUpdate(
        GENERIC_ENTITY_SCHEMA(
            _base(
                "update.dynamic",
                "1.0",
                **{
                    CONF_NATIVE_TEMPLATES: {
                        "installed_version": "{{ '1.0' }}",
                        "latest_version": "{{ '2.0' }}",
                        "versions": "{{ ['1.0', '2.0'] }}",
                        "support_backup": "{{ false }}",
                        "release_notes": "{{ 'Important fixes' }}",
                        "title": "{{ 'Virtual Firmware' }}",
                        "auto_update": "{{ true }}",
                        "in_progress": "{{ true }}",
                        "update_percentage": "{{ 42.5 }}",
                        "display_precision": "{{ 1 }}",
                    }
                },
            )
        ),
        False,
    )

    _render_native_templates(heater, hass)
    _render_native_templates(update, hass)

    assert heater.operation_list == ["off", "eco"]
    assert heater.current_operation == "eco"
    assert heater.current_temperature == 60
    assert heater.target_temperature == 45
    assert heater.target_temperature_low == 48
    assert heater.target_temperature_high == 58
    assert heater.is_away_mode_on is True
    assert heater.precision == 0.5
    assert update.installed_version == "1.0"
    assert update.latest_version == "2.0"
    assert update.title == "Virtual Firmware"
    assert update.auto_update is True
    assert update.in_progress is True
    assert update.update_percentage == 42.5
    assert update.display_precision == 1
    assert UpdateEntityFeature.SPECIFIC_VERSION in update.supported_features
    assert UpdateEntityFeature.BACKUP not in update.supported_features
    assert UpdateEntityFeature.RELEASE_NOTES in update.supported_features
    assert UpdateEntityFeature.PROGRESS in update.supported_features


def test_empty_optional_templates_do_not_advertise_unsupported_features(hass):
    cover = VirtualCover(
        COVER_SCHEMA(
            _base(
                "cover.no_tilt",
                "closed",
                **{
                    CONF_NATIVE_TEMPLATES: {
                        "current_cover_tilt_position": "{{ none }}",
                    }
                },
            )
        ),
        False,
    )
    heater = VirtualWaterHeater(
        GENERIC_ENTITY_SCHEMA(
            _base(
                "water_heater.no_away",
                "off",
                **{CONF_NATIVE_TEMPLATES: {"is_away_mode_on": "{{ none }}"}},
            )
        ),
        False,
    )
    update = VirtualUpdate(
        GENERIC_ENTITY_SCHEMA(
            _base(
                "update.no_progress",
                "1.0",
                **{
                    CONF_NATIVE_TEMPLATES: {
                        "in_progress": "{{ none }}",
                        "update_percentage": "{{ none }}",
                    }
                },
            )
        ),
        False,
    )
    media = VirtualMediaPlayer(
        GENERIC_ENTITY_SCHEMA(
            _base(
                "media_player.no_shuffle_repeat",
                "idle",
                **{
                    CONF_NATIVE_TEMPLATES: {
                        "shuffle": "{{ none }}",
                        "repeat": "{{ none }}",
                    }
                },
            )
        ),
        False,
    )

    for entity in (cover, heater, update, media):
        _render_native_templates(entity, hass)

    assert CoverEntityFeature.SET_TILT_POSITION not in cover.supported_features
    assert WaterHeaterEntityFeature.AWAY_MODE not in heater.supported_features
    assert UpdateEntityFeature.PROGRESS not in update.supported_features
    assert MediaPlayerEntityFeature.SHUFFLE_SET not in media.supported_features
    assert MediaPlayerEntityFeature.REPEAT_SET not in media.supported_features


def test_light_number_and_vacuum_templates_use_native_types(hass):
    light = VirtualLight(
        LIGHT_SCHEMA(
            _base(
                "light.dynamic",
                "on",
                matter_light_type="extended_color",
                **{
                    CONF_NATIVE_TEMPLATES: {
                        "supported_color_modes": "{{ ['hs', 'color_temp'] }}",
                        "color_mode": "{{ 'hs' }}",
                        "brightness": "{{ 128 }}",
                        "hs_color": "{{ [120, 50] }}",
                    }
                },
            )
        ),
        False,
    )
    number = VirtualNumber(
        NUMBER_SCHEMA(
            _base(
                "number.dynamic",
                "0",
                min=0,
                max=100,
                **{
                    CONF_NATIVE_TEMPLATES: {
                        "min": "{{ 10 }}",
                        "max": "{{ 20 }}",
                        "step": "{{ 0.5 }}",
                        "value": "{{ 25 }}",
                    }
                },
            )
        ),
        False,
    )
    vacuum = VirtualVacuum(
        VACUUM_SCHEMA(
            _base(
                "vacuum.dynamic",
                "docked",
                **{
                    CONF_NATIVE_TEMPLATES: {
                        "activity": "{{ 'cleaning' }}",
                        "battery_level": "{{ 87 }}",
                        "fan_speed_list": "{{ ['quiet', 'turbo'] }}",
                        "fan_speed": "{{ 'turbo' }}",
                        "supported_features": "{{ ['start', 'pause'] }}",
                    }
                },
            )
        ),
        False,
    )

    for entity in (light, number, vacuum):
        _render_native_templates(entity, hass)

    assert light.supported_color_modes == {ColorMode.HS, ColorMode.COLOR_TEMP}
    assert light.color_mode == ColorMode.HS
    assert light.brightness == 128
    assert light.hs_color == (120, 50)
    assert light.effect is None
    assert LightEntityFeature.EFFECT not in light.supported_features
    assert number.native_min_value == 10
    assert number.native_max_value == 20
    assert number.native_step == 0.5
    assert number.native_value == 20
    assert vacuum.activity == VacuumActivity.CLEANING
    assert vacuum.fan_speed == "turbo"
    assert VacuumEntityFeature.START in vacuum.supported_features
    assert VacuumEntityFeature.PAUSE in vacuum.supported_features
    assert VacuumEntityFeature.FAN_SPEED in vacuum.supported_features
    vacuum._update_attributes()
    assert vacuum.extra_state_attributes["battery_level"] == 87


@pytest.mark.parametrize(
    ("mode", "service_key", "service_value", "expected"),
    [
        ("xy", "xy_color", [0.25, 0.75], (0.25, 0.75)),
        ("hs", "hs_color", [120, 75], (120, 75)),
    ],
)
async def test_light_color_modes_update_and_restore_native_colors(
    hass,
    mode,
    service_key,
    service_value,
    expected,
):
    config = LIGHT_SCHEMA(
        _base(
            f"light.{mode}",
            "on",
            matter_light_type="extended_color",
            **{
                CONF_NATIVE_TEMPLATES: {
                    "supported_color_modes": "{{ [" + repr(mode) + "] }}",
                },
            },
        )
    )
    entity = VirtualLight(config, False)
    _render_native_templates(entity, hass)

    await entity.async_turn_on(**{service_key: service_value, "brightness": 123})

    assert entity.color_mode == ColorMode(mode)
    assert getattr(entity, service_key) == expected
    assert entity.brightness == 123

    restored = VirtualLight(config, False)
    restored.hass = hass
    restored._apply_restore_prerequisite_templates()
    restored._restore_state(
        SimpleNamespace(
            state="on",
            attributes={
                "color_mode": mode,
                service_key: service_value,
                "brightness": 123,
            },
        ),
        config,
    )

    assert restored.color_mode == ColorMode(mode)
    assert getattr(restored, service_key) == expected
    assert restored.brightness == 123


def test_vacuum_fan_speed_template_does_not_require_a_speed_list(hass):
    vacuum = VirtualVacuum(
        VACUUM_SCHEMA(
            _base(
                "vacuum.dynamic_speed",
                "docked",
                **{CONF_NATIVE_TEMPLATES: {"fan_speed": "{{ 'automatic' }}"}},
            )
        ),
        False,
    )

    _render_native_templates(vacuum, hass)

    assert vacuum.fan_speed_list == []
    assert vacuum.fan_speed == "automatic"
    assert VacuumEntityFeature.FAN_SPEED not in vacuum.supported_features


def test_camera_and_image_templates_update_backing_sources(hass):
    camera = VirtualCamera(
        CAMERA_SCHEMA(
            _base(
                "camera.dynamic",
                "off",
                **{
                    CONF_NATIVE_TEMPLATES: {
                        "source_entity": "{{ 'camera.front_door' }}",
                        "stream_source": "{{ 'rtsp://example.test/live' }}",
                        "frame_interval": "{{ 0.5 }}",
                        "is_recording": "{{ true }}",
                        "motion_detection_enabled": "{{ true }}",
                    }
                },
            )
        ),
        False,
    )
    image = VirtualImage(
        IMAGE_SCHEMA(
            _base(
                "image.dynamic",
                "unknown",
                **{
                    CONF_NATIVE_TEMPLATES: {
                        "image_url": "{{ 'https://example.test/image.jpg' }}",
                        "content_type": "{{ 'image/jpeg' }}",
                        "source_entity": "{{ 'image.front_door' }}",
                        "image_last_updated": "{{ '2026-08-11T11:30:00+09:00' }}",
                    }
                },
            )
        ),
        hass,
        False,
    )

    _render_native_templates(camera, hass)
    _render_native_templates(image, hass)

    assert camera._source_entity == "camera.front_door"
    assert camera._stream_source == "rtsp://example.test/live"
    assert camera.frame_interval == 0.5
    assert camera.is_recording is True
    assert camera.motion_detection_enabled is True
    assert image._source_entity == "image.front_door"
    assert image._image_url == "https://example.test/image.jpg"
    assert image.content_type == "image/jpeg"
    assert image.image_last_updated.isoformat() == "2026-08-11T11:30:00+09:00"


def test_tracker_openable_and_sensor_templates_update_native_properties(hass):
    tracker = VirtualDeviceTracker(
        DEVICE_TRACKER_SCHEMA(
            _base(
                "device_tracker.dynamic",
                "not_home",
                **{
                    CONF_NATIVE_TEMPLATES: {
                        "gps": "{{ [37.5417, 126.9907] }}",
                        "location_accuracy": "{{ 12 }}",
                    }
                },
            )
        )
    )
    cover = VirtualCover(
        COVER_SCHEMA(
            _base(
                "cover.dynamic",
                "closed",
                **{
                    CONF_NATIVE_TEMPLATES: {
                        "position": "{{ 35 }}",
                        "current_cover_tilt_position": "{{ 70 }}",
                    }
                },
            )
        ),
        False,
    )
    valve = VirtualValve(
        VALVE_SCHEMA(
            _base(
                "valve.dynamic",
                "closed",
                **{CONF_NATIVE_TEMPLATES: {"current_position": "{{ 65 }}"}},
            )
        ),
        False,
    )
    lock = VirtualLock(
        hass,
        LOCK_SCHEMA(
            _base(
                "lock.dynamic",
                "locked",
                **{
                    CONF_NATIVE_TEMPLATES: {
                        "support_open": "{{ true }}",
                        "state": "{{ 'open' }}",
                    }
                },
            )
        ),
        False,
    )
    sensor = VirtualSensor(
        SENSOR_SCHEMA(
            _base(
                "sensor.dynamic",
                "legacy",
                **{
                    CONF_NATIVE_TEMPLATES: {
                        "options": "{{ ['eco', 'turbo'] }}",
                        "state": "{{ 'eco' }}",
                        "unit_of_measurement": "{{ 'mode' }}",
                    }
                },
            )
        ),
        False,
    )

    for entity in (tracker, cover, valve, lock, sensor):
        _render_native_templates(entity, hass)

    assert tracker.latitude == 37.5417
    assert tracker.longitude == 126.9907
    assert tracker.location_accuracy == 12
    assert cover.current_cover_position == 35
    assert cover.current_cover_tilt_position == 70
    assert cover.is_closed is False
    assert valve.current_valve_position == 65
    assert valve.is_closed is False
    assert lock.is_open is True
    assert sensor.options == ["eco", "turbo"]
    assert sensor.native_value == "eco"
    assert sensor.native_unit_of_measurement == "mode"


def test_generic_domain_native_templates_follow_domain_state_contracts(hass):
    weather = GenericVirtualEntity(
        GENERIC_ENTITY_SCHEMA(
            _base(
                "weather.dynamic",
                "unknown",
                **{
                    CONF_NATIVE_TEMPLATES: {
                        "condition": "{{ 'sunny' }}",
                        "native_temperature": "{{ 21.5 }}",
                        "native_temperature_unit": "{{ '°C' }}",
                        "humidity": "{{ 48 }}",
                        "native_wind_speed": "{{ 4.2 }}",
                        "native_wind_speed_unit": "{{ 'm/s' }}",
                        "supported_features": "{{ 3 }}",
                    }
                },
            )
        ),
        "weather",
        False,
    )
    now = dt_util.now()
    calendar = GenericVirtualEntity(
        GENERIC_ENTITY_SCHEMA(
            _base(
                "calendar.dynamic",
                "off",
                **{
                    CONF_NATIVE_TEMPLATES: {
                        "event": "{{ event }}",
                    },
                    "template_sources": {},
                },
            )
        ),
        "calendar",
        False,
    )
    calendar._render_template = lambda _template, parse_result=False: {
        "summary": "Current meeting",
        "start": (now - timedelta(minutes=5)).isoformat(),
        "end": (now + timedelta(minutes=5)).isoformat(),
        "location": "Office",
    }
    air_quality = GenericVirtualEntity(
        GENERIC_ENTITY_SCHEMA(
            _base(
                "air_quality.dynamic",
                "unknown",
                **{
                    CONF_NATIVE_TEMPLATES: {
                        "particulate_matter_2_5": "{{ 12.5 }}",
                    }
                },
            )
        ),
        "air_quality",
        False,
    )
    event = GenericVirtualEntity(
        GENERIC_ENTITY_SCHEMA(
            _base(
                "event.dynamic",
                "unknown",
                **{
                    CONF_NATIVE_TEMPLATES: {
                        "event_types": "{{ ['pressed', 'released'] }}",
                        "event_type": "{{ 'pressed' }}",
                        "event_attributes": "{{ {'button': 2} }}",
                    }
                },
            )
        ),
        "event",
        False,
    )
    manual_calendar = GenericVirtualEntity(
        GENERIC_ENTITY_SCHEMA(_base("calendar.manual", "off")),
        "calendar",
        False,
    )

    _render_native_templates(weather, hass)
    _render_native_templates(calendar, hass)
    _render_native_templates(air_quality, hass)
    _render_native_templates(event, hass)
    manual_calendar._create_state(manual_calendar._config)
    manual_calendar.set_state("on")
    manual_calendar._update_attributes()

    assert weather.state == "sunny"
    assert weather.extra_state_attributes["temperature"] == 21.5
    assert weather.extra_state_attributes["temperature_unit"] == "°C"
    assert weather.extra_state_attributes["humidity"] == 48
    assert weather.extra_state_attributes["wind_speed"] == 4.2
    assert weather.extra_state_attributes["wind_speed_unit"] == "m/s"
    assert "native_temperature" not in weather.extra_state_attributes
    assert weather.supported_features == 3
    assert calendar.state == "on"
    assert calendar.extra_state_attributes["message"] == "Current meeting"
    assert calendar.extra_state_attributes["location"] == "Office"
    assert air_quality.state == 12.5
    assert air_quality.extra_state_attributes["particulate_matter_2_5"] == 12.5
    assert dt_util.parse_datetime(event.state) is not None
    assert event.extra_state_attributes["event_type"] == "pressed"
    assert event.extra_state_attributes["button"] == 2
    assert "event_attributes" not in event.extra_state_attributes
    assert manual_calendar.state == "on"


def test_invalid_native_template_values_are_isolated(hass, caplog):
    media = VirtualMediaPlayer(
        GENERIC_ENTITY_SCHEMA(
            _base(
                "media_player.invalid_template",
                "idle",
                source_list=["TV"],
                source="TV",
                **{CONF_NATIVE_TEMPLATES: {"source_list": "{{ 'not-a-list' }}"}},
            )
        ),
        False,
    )
    number = VirtualNumber(
        NUMBER_SCHEMA(
            _base(
                "number.invalid_template",
                "50",
                min=0,
                max=100,
                **{
                    CONF_NATIVE_TEMPLATES: {
                        "max": "{{ 'nan' }}",
                        "value": "{{ 60 }}",
                    }
                },
            )
        ),
        False,
    )
    tracker = VirtualDeviceTracker(
        DEVICE_TRACKER_SCHEMA(
            _base(
                "device_tracker.invalid_template",
                "not_home",
                **{CONF_NATIVE_TEMPLATES: {"gps": "{{ [200, 300] }}"}},
            )
        )
    )
    weather = GenericVirtualEntity(
        GENERIC_ENTITY_SCHEMA(
            _base(
                "weather.invalid_template",
                "sunny",
                **{
                    CONF_NATIVE_TEMPLATES: {
                        "humidity": "{{ 101 }}",
                        "native_temperature": "{{ -5 }}",
                    }
                },
            )
        ),
        "weather",
        False,
    )

    for entity in (media, number, tracker, weather):
        _render_native_templates(entity, hass)

    assert media.source_list == ["TV"]
    assert media.source == "TV"
    assert number.native_max_value == 100
    assert number.native_value == 60
    assert tracker.state == "not_home"
    assert tracker.latitude is None
    assert tracker.longitude is None
    assert "humidity" not in weather.extra_state_attributes
    assert weather.extra_state_attributes["temperature"] == -5
    assert "Unable to render native template" in caplog.text


def test_sensor_native_templates_reconcile_incompatible_metadata(hass):
    sensor = VirtualSensor(
        SENSOR_SCHEMA(
            _base(
                "sensor.dynamic_enum",
                "eco",
                **{
                    CONF_NATIVE_TEMPLATES: {
                        "device_class": "{{ 'enum' }}",
                        "state_class": "{{ 'measurement' }}",
                        "options": "{{ ['eco', 'boost'] }}",
                        "native_unit_of_measurement": "{{ 'mode' }}",
                        "suggested_unit_of_measurement": "{{ 'mode' }}",
                        "last_reset": "{{ '2026-08-12T10:00:00+09:00' }}",
                    }
                },
            )
        ),
        False,
    )

    _render_native_templates(sensor, hass)

    assert sensor.device_class.value == "enum"
    assert sensor.state_class is None
    assert sensor.native_unit_of_measurement is None
    assert sensor.suggested_unit_of_measurement is None
    assert sensor.last_reset is None
    assert sensor.options == ["eco", "boost"]
