"""Integration coverage for every supported Virtual Layer entity domain."""

from __future__ import annotations

import copy
import importlib
from pathlib import Path

import homeassistant.helpers.device_registry as dr
import homeassistant.helpers.entity_registry as er
import pytest
from homeassistant.const import (
    ATTR_ENTITY_ID,
    CONF_NAME,
    CONF_PLATFORM,
    STATE_UNAVAILABLE,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.virtual_layer.config_flow import (
    DOMAIN_NATIVE_TEMPLATE_PROPERTIES,
    NATIVE_TEMPLATE_ATOMIC_LIST_PROPERTIES,
    NATIVE_TEMPLATE_BITMASK_PROPERTIES,
    NATIVE_TEMPLATE_BOOLEAN_PROPERTIES,
    NATIVE_TEMPLATE_LIST_PROPERTIES,
    NATIVE_TEMPLATE_MAPPING_PROPERTIES,
    NATIVE_TEMPLATE_NUMERIC_PROPERTIES,
)
from custom_components.virtual_layer.const import (
    ATTR_DEVICE_ID,
    ATTR_DEVICES,
    ATTR_GROUP_NAME,
    ATTR_UNIQUE_ID,
    COMPONENT_DOMAIN,
    CONF_INITIAL_AVAILABILITY,
    CONF_INITIAL_VALUE,
    CONF_MAX,
    CONF_MIN,
    CONF_NATIVE_TEMPLATES,
    CONF_PERSISTENT,
    STATE_ONLY_ENTITY_DOMAINS,
    VIRTUAL_ENTITY_DOMAINS,
)
from custom_components.virtual_layer.generic import GenericVirtualEntity

pytestmark = pytest.mark.integration


_NATIVE_TEMPLATE_SAMPLES = {
    "action": "off",
    "activity": "docked",
    "available_modes": ["normal", "eco"],
    "available_tones": ["alarm"],
    "camera_entity": "camera.helper_source",
    "code_format": "number",
    "color_mode": "rgb",
    "color_temp_kelvin": 4000,
    "content_type": "image/png",
    "current_activity": "TV",
    "current_direction": "forward",
    "current_operation": "off",
    "current_option": "eco",
    "default_language": "en",
    "default_options": {"voice": "default"},
    "effect": "none",
    "effect_list": ["none", "rainbow"],
    "entity_picture": "/local/virtual-layer.png",
    "event": {
        "summary": "Virtual event",
        "start": "2026-08-12T10:00:00+09:00",
        "end": "2026-08-12T11:00:00+09:00",
    },
    "event_attributes": {"button": 1},
    "event_type": "pressed",
    "event_types": ["pressed", "released"],
    "fan_mode": "auto",
    "fan_modes": ["auto", "turbo"],
    "fan_speed": "normal",
    "fan_speed_list": ["normal", "turbo"],
    "gps": [37.5, 127.0],
    "group_members": ["media_player.helper_source"],
    "hs_color": [180, 50],
    "hvac_action": "off",
    "hvac_mode": "off",
    "hvac_modes": ["off", "cool"],
    "image_last_updated": "2026-08-12T10:00:00+09:00",
    "image_path": "/tmp/virtual-layer.png",
    "image_url": "https://example.test/virtual-layer.png",
    "last_reset": "2026-08-12T10:00:00+09:00",
    "location": "not_home",
    "max_color_temp_kelvin": 6500,
    "media_state": "idle",
    "media_position_updated_at": "2026-08-12T10:00:00+09:00",
    "min_color_temp_kelvin": 2000,
    "native_precipitation_unit": "mm",
    "native_pressure_unit": "hPa",
    "native_temperature_unit": "°C",
    "native_unit_of_measurement": "unit",
    "native_value": "2026-08-12",
    "native_visibility_unit": "km",
    "native_wind_speed_unit": "m/s",
    "operation_list": ["off", "eco"],
    "options": ["eco", "boost"],
    "pattern": "[A-Za-z]+",
    "preset_mode": "none",
    "preset_modes": ["none", "eco"],
    "repeat": "off",
    "rgb_color": [10, 20, 30],
    "rgbw_color": [10, 20, 30, 40],
    "rgbww_color": [10, 20, 30, 40, 50],
    "sound_mode": "movie",
    "sound_mode_list": ["movie", "music"],
    "source": "virtual_layer",
    "source_list": ["TV", "Radio"],
    "state_class": "measurement",
    "stream_source": "rtsp://example.test/live",
    "supported_color_modes": ["rgb", "color_temp"],
    "supported_formats": ["wav"],
    "supported_languages": ["en", "ko"],
    "supported_options": ["voice"],
    "svg": "<svg xmlns='http://www.w3.org/2000/svg'></svg>",
    "swing_horizontal_mode": "left",
    "swing_horizontal_modes": ["left", "right"],
    "swing_mode": "off",
    "swing_modes": ["off", "vertical"],
    "temperature_unit": "°C",
    "todo_items": [{"summary": "Virtual task"}],
    "tts_options": {"voice": "default"},
    "unit_of_measurement": "µg/m³",
    "versions": ["1.0.0", "1.1.0"],
    "wind_bearing": 180,
    "xy_color": [0.3, 0.4],
}


def _native_template_sample(domain: str, property_name: str):
    if property_name == "source_entity":
        return f"{domain}.helper_source"
    if property_name == "device_class":
        return {
            "binary_sensor": "door",
            "button": "restart",
            "cover": "door",
            "humidifier": "humidifier",
            "image_processing": "presence",
            "light": None,
            "lock": "door",
            "media_player": "speaker",
            "notify": "service",
            "number": "power_factor",
            "sensor": "enum",
            "switch": "outlet",
            "update": "firmware",
            "valve": "water",
        }.get(domain, None)
    if property_name == "mode":
        return {
            "humidifier": "normal",
            "number": "slider",
            "text": "text",
        }.get(domain, "auto")
    if property_name == "native_value":
        return {
            "date": "2026-08-12",
            "datetime": "2026-08-12T10:00:00+09:00",
            "number": 50,
            "text": "Virtual",
            "time": "10:00:00",
        }[domain]
    if domain == "media_player" and property_name == "source":
        return "TV"
    if domain == "media_player" and property_name in {"volume_level", "volume_step"}:
        return 0.5
    if domain == "remote" and property_name == "activity_list":
        return ["TV", "Music"]
    if domain == "sensor" and property_name in {
        "native_unit_of_measurement",
        "suggested_unit_of_measurement",
    }:
        return None
    if domain == "sensor" and property_name == "state_class":
        return "total"
    if property_name == "precision":
        return 1
    if property_name in _NATIVE_TEMPLATE_SAMPLES:
        return _NATIVE_TEMPLATE_SAMPLES[property_name]
    if property_name in NATIVE_TEMPLATE_BOOLEAN_PROPERTIES:
        return True
    if property_name in NATIVE_TEMPLATE_BITMASK_PROPERTIES:
        return 1
    if property_name in NATIVE_TEMPLATE_ATOMIC_LIST_PROPERTIES:
        return [1, 2]
    if property_name in NATIVE_TEMPLATE_LIST_PROPERTIES:
        return [f"{property_name}_value"]
    if property_name in NATIVE_TEMPLATE_MAPPING_PROPERTIES:
        return {"key": "value"}
    if property_name in NATIVE_TEMPLATE_NUMERIC_PROPERTIES:
        return 10
    return f"{property_name}_value"


def _native_template_samples(domain: str) -> dict[str, str]:
    return {
        property_name: "{{ " + repr(_native_template_sample(domain, property_name)) + " }}"
        for property_name in DOMAIN_NATIVE_TEMPLATE_PROPERTIES.get(domain, ())
    }


def _raw_ui_entity(domain: str) -> dict:
    initial_values = {
        "binary_sensor": "off",
        "camera": "on",
        "climate": "off",
        "cover": "closed",
        "date": "2026-08-08",
        "datetime": "2026-08-08T12:34:56+09:00",
        "device_tracker": "not_home",
        "fan": "off",
        "humidifier": "off",
        "lawn_mower": "docked",
        "light": "off",
        "lock": "locked",
        "media_player": "idle",
        "number": "0",
        "remote": "off",
        "select": "eco",
        "siren": "off",
        "switch": "off",
        "text": "hello",
        "time": "12:34:56",
        "update": "1.0.0",
        "vacuum": "docked",
        "valve": "closed",
        "water_heater": "off",
    }
    entity = {
        CONF_PLATFORM: domain,
        CONF_NAME: f"{domain} Entity",
        CONF_INITIAL_VALUE: initial_values.get(domain, "unknown"),
        CONF_INITIAL_AVAILABILITY: True,
        CONF_PERSISTENT: False,
    }
    native_templates = _native_template_samples(domain)
    if native_templates:
        entity[CONF_NATIVE_TEMPLATES] = native_templates
    if domain == "number":
        entity.update({
            CONF_MIN: 0,
            CONF_MAX: 100,
        })
    elif domain == "climate":
        entity.update({
            "hvac_modes": ["off", "heat", "cool"],
            "fan_modes": ["auto", "turbo"],
            "fan_mode": "auto",
            "preset_modes": ["none", "eco"],
            "preset_mode": "none",
            "swing_modes": ["off", "vertical"],
            "swing_mode": "off",
        })
    elif domain == "humidifier":
        entity.update({
            "min_humidity": 30,
            "max_humidity": 70,
            "target_humidity": 50,
            "modes": ["normal", "eco"],
            "mode": "normal",
        })
    elif domain == "select":
        entity["options"] = ["eco", "boost"]
    elif domain == "text":
        entity.update({"min": 1, "max": 32})
    elif domain == "update":
        entity.update({
            "installed_version": "1.0.0",
            "latest_version": "1.1.0",
            "versions": ["1.0.0", "1.1.0"],
        })
    elif domain == "vacuum":
        entity.update({
            "battery_level": 80,
            "fan_speed": "normal",
            "fan_speed_list": ["normal", "turbo"],
        })
    elif domain == "water_heater":
        entity.update({
            "operation_list": ["off", "eco", "heat"],
            "target_temperature": 50,
        })
    return entity


def test_every_supported_domain_has_a_platform_module():
    component_dir = Path(__file__).parents[2] / "custom_components" / "virtual_layer"

    for domain in VIRTUAL_ENTITY_DOMAINS:
        assert (component_dir / f"{domain}.py").is_file()
        module = importlib.import_module(f"custom_components.virtual_layer.{domain}")
        assert hasattr(module, "async_setup_entry")


async def test_real_config_entry_loads_every_supported_domain(
    hass,
    tmp_path,
    monkeypatch,
    caplog,
):
    """Load every advertised domain through Home Assistant's entity platforms."""
    meta_file = tmp_path / "virtual_layer.meta.json"
    monkeypatch.setattr(
        "custom_components.virtual_layer.cfg.default_meta_file",
        lambda _hass: str(meta_file),
    )
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        title="all domains - virtual_layer",
        data={ATTR_GROUP_NAME: "all_domains"},
        options={
            ATTR_DEVICES: {
                "All Domains Device": [
                    _raw_ui_entity(domain)
                    for domain in VIRTUAL_ENTITY_DOMAINS
                ],
            },
        },
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id) is True
    await hass.async_block_till_done()

    missing_domains = [
        domain
        for domain in VIRTUAL_ENTITY_DOMAINS
        if hass.states.get(f"{domain}.{domain}_entity") is None
    ]
    assert not missing_domains, f"Domains missing runtime states: {missing_domains}"
    assert "Unable to render native template" not in caplog.text

    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    registered_device_ids = set()
    for domain in VIRTUAL_ENTITY_DOMAINS:
        entity_id = f"{domain}.{domain}_entity"
        entity_entry = entity_registry.async_get(entity_id)
        assert entity_entry is not None
        assert entity_entry.platform == COMPONENT_DOMAIN
        assert entity_entry.device_id is not None
        registered_device_ids.add(entity_entry.device_id)
        assert device_registry.async_get(entity_entry.device_id) is not None
    assert len(registered_device_ids) == 1
    battery_state = hass.states.get("sensor.vacuum_entity_battery")
    assert battery_state is not None
    assert battery_state.state == "80"
    assert battery_state.attributes["device_class"] == "battery"
    assert battery_state.attributes["unit_of_measurement"] == "%"
    assert (
        entity_registry.async_get("sensor.vacuum_entity_battery").device_id
        in registered_device_ids
    )

    updated_options = copy.deepcopy(dict(entry.options))
    vacuum_config = next(
        entity
        for entity in updated_options[ATTR_DEVICES]["All Domains Device"]
        if entity[CONF_PLATFORM] == "vacuum"
    )
    vacuum_config.pop("battery_level")
    hass.config_entries.async_update_entry(entry, options=updated_options)
    assert await hass.config_entries.async_reload(entry.entry_id) is True
    await hass.async_block_till_done()

    assert hass.states.get("vacuum.vacuum_entity") is not None
    assert entity_registry.async_get("sensor.vacuum_entity_battery") is None
    assert len({
        entity_registry.async_get(f"{domain}.{domain}_entity").device_id
        for domain in VIRTUAL_ENTITY_DOMAINS
    }) == 1

    assert await hass.config_entries.async_unload(entry.entry_id) is True
    await hass.async_block_till_done()
    for domain in VIRTUAL_ENTITY_DOMAINS:
        state = hass.states.get(f"{domain}.{domain}_entity")
        if domain in STATE_ONLY_ENTITY_DOMAINS:
            assert state is None
        else:
            assert state is None or state.state == STATE_UNAVAILABLE


def test_generic_entity_exposes_direct_ui_options_as_state_attributes():
    module = importlib.import_module("custom_components.virtual_layer.weather")
    config = module.ENTITY_SCHEMA({
        CONF_NAME: "Virtual Forecast",
        ATTR_ENTITY_ID: "weather.virtual_forecast",
        ATTR_UNIQUE_ID: "weather_virtual_forecast",
        ATTR_DEVICE_ID: "coverage-device",
        "temperature": 21.5,
        "humidity": 48,
        "forecast_provider": "virtual",
    })
    entity = GenericVirtualEntity(config, "weather", False)

    entity._create_state(config)
    entity._update_attributes()

    assert entity.extra_state_attributes["temperature"] == 21.5
    assert entity.extra_state_attributes["humidity"] == 48
    assert entity.extra_state_attributes["forecast_provider"] == "virtual"


async def test_native_building_block_services_update_virtual_entities(
    hass, tmp_path, monkeypatch
):
    """Verify that platform services work after a real config-entry setup."""
    meta_file = tmp_path / "virtual_layer.meta.json"
    monkeypatch.setattr(
        "custom_components.virtual_layer.cfg.default_meta_file",
        lambda _hass: str(meta_file),
    )
    entities = [
        {
            CONF_PLATFORM: "select",
            CONF_NAME: "Native Select",
            CONF_INITIAL_VALUE: "eco",
            "options": ["eco", "boost"],
        },
        {
            CONF_PLATFORM: "text",
            CONF_NAME: "Native Text",
            CONF_INITIAL_VALUE: "hello",
            "min": 1,
            "max": 20,
        },
        {
            CONF_PLATFORM: "button",
            CONF_NAME: "Native Button",
            CONF_INITIAL_VALUE: "unknown",
        },
        {
            CONF_PLATFORM: "siren",
            CONF_NAME: "Native Siren",
            CONF_INITIAL_VALUE: "off",
            "available_tones": ["alarm"],
        },
        {
            CONF_PLATFORM: "lawn_mower",
            CONF_NAME: "Native Mower",
            CONF_INITIAL_VALUE: "docked",
        },
    ]
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        title="native services - virtual_layer",
        data={ATTR_GROUP_NAME: "native_services"},
        options={ATTR_DEVICES: {"Native Device": entities}},
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id) is True
    await hass.async_block_till_done()

    await hass.services.async_call(
        "select",
        "select_option",
        {ATTR_ENTITY_ID: "select.native_select", "option": "boost"},
        blocking=True,
    )
    await hass.services.async_call(
        "text",
        "set_value",
        {ATTR_ENTITY_ID: "text.native_text", "value": "updated"},
        blocking=True,
    )
    await hass.services.async_call(
        "button",
        "press",
        {ATTR_ENTITY_ID: "button.native_button"},
        blocking=True,
    )
    await hass.services.async_call(
        "siren",
        "turn_on",
        {ATTR_ENTITY_ID: "siren.native_siren", "tone": "alarm"},
        blocking=True,
    )
    await hass.services.async_call(
        "lawn_mower",
        "start_mowing",
        {ATTR_ENTITY_ID: "lawn_mower.native_mower"},
        blocking=True,
    )

    assert hass.states.get("select.native_select").state == "boost"
    assert hass.states.get("text.native_text").state == "updated"
    assert hass.states.get("button.native_button").state != "unknown"
    assert hass.states.get("siren.native_siren").state == "on"
    assert hass.states.get("lawn_mower.native_mower").state == "mowing"
