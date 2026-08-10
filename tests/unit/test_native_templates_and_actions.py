"""Tests for native property templates and command actions."""

from importlib import import_module
from unittest.mock import Mock

import pytest
from homeassistant.components.climate import ClimateEntityFeature, HVACMode
from homeassistant.components.fan import FanEntityFeature
from homeassistant.components.humidifier import (
    HumidifierAction,
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
from homeassistant.const import ATTR_ENTITY_ID

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


@pytest.mark.parametrize("domain", sorted(VIRTUAL_ENTITY_COMMANDS))
def test_command_contract_matches_wrapped_platform_methods(domain):
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

    assert wrapped_commands == VIRTUAL_ENTITY_COMMANDS[domain]


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
                        "hvac_mode": "{{ states('sensor.hvac_profile') }}",
                        "fan_mode": "{{ 'turbo' }}",
                        "preset_mode": "{{ 'sleep' }}",
                        "fan_modes": "{{ state_attr('sensor.hvac_profile', 'fan_modes') }}",
                        "preset_modes": "{{ state_attr('sensor.hvac_profile', 'preset_modes') }}",
                        "temperature": "{{ state_attr('sensor.hvac_profile', 'temperature') }}",
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
    assert entity.fan_modes == ["auto", "turbo"]
    assert entity.fan_mode == "turbo"
    assert entity.preset_modes == ["none", "sleep"]
    assert entity.preset_mode == "sleep"
    assert entity.target_temperature == 22.5
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
                        "percentage": "{{ states('sensor.fan_speed') | int }}",
                        "preset_modes": "{{ ['quiet', 'boost'] }}",
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

    assert entity.percentage == 42
    assert entity.preset_modes == ["quiet", "boost"]
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
                        "current_humidity": "{{ states('sensor.room_humidity') | float }}",
                        "humidity": "{{ 53 }}",
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

    assert entity.current_humidity == 61
    assert entity.target_humidity == 53
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
                        "volume_level": "{{ 0.75 }}",
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
    assert update.installed_version == "1.0"
    assert update.latest_version == "2.0"
    assert UpdateEntityFeature.SPECIFIC_VERSION in update.supported_features
    assert UpdateEntityFeature.BACKUP not in update.supported_features
    assert UpdateEntityFeature.RELEASE_NOTES in update.supported_features


def test_light_number_and_vacuum_templates_use_native_types(hass):
    light = VirtualLight(
        LIGHT_SCHEMA(
            _base(
                "light.dynamic",
                "on",
                **{
                    CONF_NATIVE_TEMPLATES: {
                        "supported_color_modes": "{{ ['hs', 'color_temp'] }}",
                        "color_mode": "{{ 'hs' }}",
                        "brightness": "{{ 128 }}",
                        "hs_color": "{{ [220, 60] }}",
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
    assert light.hs_color == (220, 60)
    assert light.effect == "rainbow"
    assert LightEntityFeature.EFFECT in light.supported_features
    assert number.native_min_value == 10
    assert number.native_max_value == 20
    assert number.native_step == 0.5
    assert number.native_value == 20
    assert vacuum.activity == VacuumActivity.CLEANING
    assert vacuum.fan_speed == "turbo"
    assert VacuumEntityFeature.FAN_SPEED in vacuum.supported_features
    vacuum._update_attributes()
    assert vacuum.extra_state_attributes["battery_level"] == 87


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
    assert camera.is_recording is True
    assert camera.motion_detection_enabled is True
    assert image._source_entity == "image.front_door"
    assert image._image_url == "https://example.test/image.jpg"
    assert image.content_type == "image/jpeg"


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
                **{CONF_NATIVE_TEMPLATES: {"position": "{{ 35 }}"}},
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
    assert cover.is_closed is False
    assert valve.current_valve_position == 65
    assert valve.is_closed is False
    assert lock.is_open is True
    assert sensor.options == ["eco", "turbo"]
    assert sensor.native_value == "eco"
    assert sensor.native_unit_of_measurement == "mode"


def test_generic_domain_native_templates_are_exposed_as_state_attributes(hass):
    entity = GenericVirtualEntity(
        GENERIC_ENTITY_SCHEMA(
            _base(
                "weather.dynamic",
                "sunny",
                **{
                    CONF_NATIVE_TEMPLATES: {
                        "temperature": "{{ 21.5 }}",
                        "forecast": "{{ [{'condition': 'rainy', 'temperature': 18}] }}",
                    }
                },
            )
        ),
        "weather",
        False,
    )

    _render_native_templates(entity, hass)

    assert entity.extra_state_attributes["temperature"] == 21.5
    assert entity.extra_state_attributes["forecast"] == [
        {"condition": "rainy", "temperature": 18}
    ]


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

    for entity in (media, number, tracker):
        _render_native_templates(entity, hass)

    assert media.source_list == ["TV"]
    assert media.source == "TV"
    assert number.native_max_value == 100
    assert number.native_value == 60
    assert tracker.state == "not_home"
    assert tracker.latitude is None
    assert tracker.longitude is None
    assert "Unable to render native template" in caplog.text
