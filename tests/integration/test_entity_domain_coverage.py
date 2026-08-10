"""Integration coverage for every supported Virtual Layer entity domain."""

from __future__ import annotations

import copy
import importlib
from pathlib import Path
from unittest.mock import AsyncMock, patch

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

from custom_components.virtual_layer import async_setup_entry
from custom_components.virtual_layer.const import (
    ATTR_DEVICE_ID,
    ATTR_DEVICES,
    ATTR_ENTITIES,
    ATTR_GROUP_NAME,
    ATTR_UNIQUE_ID,
    COMPONENT_DOMAIN,
    COMPONENT_SERVICES,
    CONF_INITIAL_AVAILABILITY,
    CONF_INITIAL_VALUE,
    CONF_MAX,
    CONF_MIN,
    CONF_PERSISTENT,
    STATE_ONLY_ENTITY_DOMAINS,
    VIRTUAL_ENTITY_DOMAINS,
)
from custom_components.virtual_layer.generic import GenericVirtualEntity

pytestmark = pytest.mark.integration


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


def _platform_entity(domain: str) -> dict:
    entity = _raw_ui_entity(domain)
    entity.pop(CONF_PLATFORM)
    entity.update({
        ATTR_ENTITY_ID: f"{domain}.virtual_test",
        ATTR_UNIQUE_ID: f"{domain}_virtual_test",
        ATTR_DEVICE_ID: "coverage-device",
    })
    return entity


def test_every_supported_domain_has_a_platform_module():
    component_dir = Path(__file__).parents[2] / "custom_components" / "virtual_layer"

    for domain in VIRTUAL_ENTITY_DOMAINS:
        assert (component_dir / f"{domain}.py").is_file()
        module = importlib.import_module(f"custom_components.virtual_layer.{domain}")
        assert hasattr(module, "async_setup_entry")


async def test_config_entry_setup_forwards_every_supported_domain(hass):
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        title="coverage - virtual_layer",
        data={ATTR_GROUP_NAME: "coverage"},
        options={
            ATTR_DEVICES: {
                "Coverage Device": [
                    _raw_ui_entity(domain)
                    for domain in VIRTUAL_ENTITY_DOMAINS
                ],
            },
        },
    )
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.virtual_layer._async_get_or_create_virtual_device_in_registry",
            AsyncMock(),
        ),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            AsyncMock(return_value=True),
        ) as forward_setups,
    ):
        assert await async_setup_entry(hass, entry) is True

    forwarded_domains = [
        domain
        for domain in VIRTUAL_ENTITY_DOMAINS
        if domain not in STATE_ONLY_ENTITY_DOMAINS
    ]
    forward_setups.assert_awaited_once_with(entry, forwarded_domains)
    group_data = hass.data[COMPONENT_DOMAIN]["coverage"]
    assert set(group_data[ATTR_ENTITIES]) == set(VIRTUAL_ENTITY_DOMAINS)


async def test_config_entry_setup_loads_string_only_entity_domains(hass, tmp_path, monkeypatch):
    """Exercise domains that Home Assistant accepts as strings, not Platform enum values."""
    string_only_domains = ["geolocation", "infrared", "radio_frequency", "tag"]
    meta_file = tmp_path / "virtual_layer.meta.json"
    monkeypatch.setattr(
        "custom_components.virtual_layer.cfg.default_meta_file",
        lambda _hass: str(meta_file),
    )
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        title="string domains - virtual_layer",
        data={ATTR_GROUP_NAME: "string_domains"},
        options={
            ATTR_DEVICES: {
                "String Domain Device": [
                    _raw_ui_entity(domain)
                    for domain in string_only_domains
                ],
            },
        },
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id) is True
    await hass.async_block_till_done()

    for domain in string_only_domains:
        entity_id = f"{domain}.{domain}_entity"
        assert hass.states.get(entity_id) is not None
        entity_entry = er.async_get(hass).async_get(entity_id)
        assert entity_entry is not None
        assert entity_entry.platform == COMPONENT_DOMAIN
        assert entity_entry.device_id is not None
        assert dr.async_get(hass).async_get(entity_entry.device_id) is not None

    assert await hass.config_entries.async_unload(entry.entry_id) is True
    await hass.async_block_till_done()

    for domain in string_only_domains:
        assert hass.states.get(f"{domain}.{domain}_entity") is None


async def test_real_config_entry_loads_every_supported_domain(
    hass,
    tmp_path,
    monkeypatch,
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


@pytest.mark.parametrize("domain", VIRTUAL_ENTITY_DOMAINS)
async def test_platform_setup_entry_creates_virtual_entity_for_domain(hass, domain):
    module = importlib.import_module(f"custom_components.virtual_layer.{domain}")
    hass.data.setdefault(COMPONENT_SERVICES, {})
    hass.data[COMPONENT_DOMAIN] = {
        "coverage": {
            ATTR_ENTITIES: {
                domain: [_platform_entity(domain)],
            },
        },
    }
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "coverage"},
    )
    added_entities = []

    await module.async_setup_entry(
        hass,
        entry,
        lambda entities: added_entities.extend(entities),
    )

    assert len(added_entities) == 1
    entity = added_entities[0]
    assert entity.entity_id == f"{domain}.virtual_test"
    assert entity.unique_id == f"{domain}_virtual_test"


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
