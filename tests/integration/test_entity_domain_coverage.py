"""Integration coverage for every supported Virtual Layer entity domain."""

from __future__ import annotations

import importlib
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from homeassistant.const import ATTR_ENTITY_ID, CONF_NAME, CONF_PLATFORM
import homeassistant.helpers.device_registry as dr
import homeassistant.helpers.entity_registry as er

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
    entity = {
        CONF_PLATFORM: domain,
        CONF_NAME: f"{domain} Entity",
        CONF_INITIAL_VALUE: "unknown",
        CONF_INITIAL_AVAILABILITY: True,
        CONF_PERSISTENT: False,
    }
    if domain == "number":
        entity.update({
            CONF_INITIAL_VALUE: "0",
            CONF_MIN: 0,
            CONF_MAX: 100,
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
        assert hass.states.get(f"{domain}.{domain}_entity") is not None
        entity_entry = er.async_get(hass).async_get(f"{domain}.{domain}_entity")
        assert entity_entry is not None
        assert entity_entry.platform == COMPONENT_DOMAIN
        assert entity_entry.device_id is not None
        assert dr.async_get(hass).async_get(entity_entry.device_id) is not None

    assert await hass.config_entries.async_unload(entry.entry_id) is True
    await hass.async_block_till_done()

    for domain in string_only_domains:
        assert hass.states.get(f"{domain}.{domain}_entity") is None


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
