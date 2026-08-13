"""Tests for native property templates and command actions."""

from datetime import timedelta
from importlib import import_module
from unittest.mock import Mock

import pytest
from homeassistant.components.climate import ClimateEntityFeature, HVACMode
from homeassistant.components.climate.const import HVACAction
from homeassistant.components.fan import FanEntityFeature
from homeassistant.components.humidifier import (
    HumidifierAction,
    HumidifierDeviceClass,
    HumidifierEntityFeature,
)
from homeassistant.components.light import ColorMode, LightEntityFeature
from homeassistant.components.media_player import (
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.components.remote import RemoteEntityFeature
from homeassistant.components.siren import SirenEntityFeature
from homeassistant.components.update import UpdateEntityFeature
from homeassistant.components.vacuum import VacuumActivity, VacuumEntityFeature
from homeassistant.const import ATTR_ENTITY_ID, UnitOfTemperature
from homeassistant.util import dt as dt_util

from custom_components.virtual_layer.camera import CAMERA_SCHEMA, VirtualCamera
from custom_components.virtual_layer.climate import CLIMATE_SCHEMA, VirtualClimate
from custom_components.virtual_layer.const import (
    ATTR_UNIQUE_ID,
    CONF_ATTRIBUTE_TEMPLATES,
    CONF_COMMAND_ACTIONS,
    CONF_INITIAL_VALUE,
    CONF_NAME,
    CONF_NATIVE_TEMPLATES,
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
    VirtualMediaPlayer,
    VirtualRemote,
    VirtualSelect,
    VirtualSiren,
    VirtualText,
    VirtualUpdate,
    VirtualWaterHeater,
)
from custom_components.virtual_layer.humidifier import (
    HUMIDIFIER_SCHEMA,
    VirtualHumidifier,
)
from custom_components.virtual_layer.image import IMAGE_SCHEMA, VirtualImage
from custom_components.virtual_layer.light import LIGHT_SCHEMA, VirtualLight
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
    assert entity.percentage == 42
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
                                    "data": {"requested": "{{ percentage }}"},
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

    assert calls == [{"requested": 73}]
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

    assert entity.percentage == 35
    entity.async_write_ha_state.assert_called_once()


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


def _render_native_templates(entity, hass):
    entity.hass = hass
    entity._create_state(entity._config)
    entity.async_schedule_update_ha_state = Mock()
    entity.async_write_ha_state = Mock()
    entity._apply_templates()


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
    assert MediaPlayerEntityFeature.SELECT_SOURCE in media.supported_features
    assert remote.current_activity == "Music"
    assert RemoteEntityFeature.ACTIVITY in remote.supported_features
    assert siren.available_tones == ["alarm", "chime"]
    assert SirenEntityFeature.TONES in siren.supported_features
    assert SirenEntityFeature.VOLUME_SET not in siren.supported_features
    assert SirenEntityFeature.DURATION not in siren.supported_features


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


def test_light_number_and_vacuum_templates_use_native_types(hass):
    light = VirtualLight(
        LIGHT_SCHEMA(
            _base(
                "light.dynamic",
                "on",
                **{
                    CONF_NATIVE_TEMPLATES: {
                        "supported_color_modes": "{{ ['rgb', 'color_temp'] }}",
                        "color_mode": "{{ 'rgb' }}",
                        "brightness": "{{ 128 }}",
                        "rgb_color": "{{ [12, 34, 56] }}",
                        "effect_list": "{{ ['rainbow', 'none'] }}",
                        "effect": "{{ 'rainbow' }}",
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

    assert light.supported_color_modes == {ColorMode.RGB, ColorMode.COLOR_TEMP}
    assert light.color_mode == ColorMode.RGB
    assert light.brightness == 128
    assert light.rgb_color == (12, 34, 56)
    assert light.effect == "rainbow"
    assert LightEntityFeature.EFFECT in light.supported_features
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
