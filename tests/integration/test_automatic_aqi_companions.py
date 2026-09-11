"""Existing UI measurements acquire managed, non-destructive AQ companions."""

from copy import deepcopy
import asyncio

import pytest
from homeassistant.const import ATTR_ENTITY_ID, CONF_NAME, CONF_PLATFORM, CONF_UNIT_OF_MEASUREMENT
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.virtual_layer.const import (
    ATTR_DEVICES, ATTR_ENTITY_KEY, ATTR_GROUP_NAME, COMPONENT_DOMAIN,
    CONF_ATTRIBUTES, CONF_CLASS, CONF_INITIAL_VALUE, CONF_SOURCE_ENTITIES, CONF_VALUE_TEMPLATE,
)

pytestmark = pytest.mark.integration


async def test_source_profile_edit_after_removed_platform_and_reload(hass, tmp_path, monkeypatch):
    """Historical HA platform objects must not re-enter the active unload set."""
    from custom_components.virtual_layer import air_quality_options as aq

    monkeypatch.setattr("custom_components.virtual_layer.cfg.default_meta_file", lambda hass: str(tmp_path / "reload.meta.json"))
    hass.states.async_set("sensor.physical", "25", {"device_class": "pm25", "unit_of_measurement": "μg/m³"})
    record = {CONF_PLATFORM: "sensor", ATTR_ENTITY_ID: "sensor.virtual_pm25",
              ATTR_ENTITY_KEY: "stable", CONF_NAME: "PM25", CONF_CLASS: "pm25",
              CONF_UNIT_OF_MEASUREMENT: "μg/m³", CONF_SOURCE_ENTITIES: ["sensor.physical"],
              CONF_VALUE_TEMPLATE: "{{ states('sensor.physical') }}"}
    entry = MockConfigEntry(domain=COMPONENT_DOMAIN, data={ATTR_GROUP_NAME: "reload"},
                            options={ATTR_DEVICES: {"Room": [record]}})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    for records in ([record, {CONF_PLATFORM: "switch", CONF_NAME: "Temporary", ATTR_ENTITY_ID: "switch.temporary"}], [record]):
        hass.config_entries.async_update_entry(entry, options={ATTR_DEVICES: {"Room": deepcopy(records)}})
        await hass.async_block_till_done()
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    recipe = aq.automatic_recipe(["sensor.physical"], [hass.states.get("sensor.physical")])
    recipe["measurements"][0]["thresholds"] = [100, 200, 300, 400, 500]
    options = deepcopy(dict(entry.options))
    options[ATTR_DEVICES]["Room"][0]["air_quality_logic"] = recipe
    hass.config_entries.async_update_entry(entry, options=options)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.virtual_pm25").state == "25"
    assert hass.states.get("sensor.virtual_pm25_aqi").state == "good"
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.virtual_pm25_aqi").state == "good"
    assert hass.states.get("sensor.physical").state == "25"


@pytest.mark.parametrize("quantity,unit,value,grade", [
    ("pm1", "μg/m³", "15", "moderate"),
    ("pm25", "μg/m³", "5", "good"),
    ("radon", "Bq/m³", "54.07", "fair"),
    ("benzene", "μg/m³", "3", "moderate"),
    ("aqi", "", "150", "moderate"),
])
async def test_existing_measurement_gets_live_companion_and_cleanup(hass, tmp_path, monkeypatch, quantity, unit, value, grade):
    monkeypatch.setattr("custom_components.virtual_layer.cfg.default_meta_file", lambda hass: str(tmp_path / "meta.json"))
    parent_id = f"sensor.virtual_{quantity}"
    child_id = f"{parent_id}_aqi"
    hass.states.async_set("sensor.physical", value)
    record = {
        CONF_PLATFORM: "sensor", ATTR_ENTITY_ID: parent_id,
        ATTR_ENTITY_KEY: "stable-measurement",
        CONF_NAME: quantity, CONF_UNIT_OF_MEASUREMENT: unit,
        CONF_INITIAL_VALUE: value, CONF_SOURCE_ENTITIES: ["sensor.physical"],
        CONF_VALUE_TEMPLATE: "{{ states('sensor.physical') }}",
    }
    if quantity != "benzene":
        record[CONF_CLASS] = quantity
    entry = MockConfigEntry(domain=COMPONENT_DOMAIN, data={ATTR_GROUP_NAME: "air"}, options={ATTR_DEVICES: {"Room": [record]}})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(parent_id).state == value
    assert hass.states.get(child_id).state == grade
    assert hass.states.get(child_id).attributes["source_entity_id"] == parent_id
    assert "unit_of_measurement" not in hass.states.get(child_id).attributes
    assert "device_class" not in hass.states.get(child_id).attributes
    registry = er.async_get(hass)
    child = registry.async_get(child_id)
    assert child.device_id == registry.async_get(parent_id).device_id
    assert child.original_name == f"{quantity} Air Quality"
    uid = child.unique_id
    # Companions are runtime-derived, not recursively added to stored options.
    assert len(entry.options[ATTR_DEVICES]["Room"]) == 1
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get(child_id).unique_id == uid
    assert hass.states.get(child_id + "_aqi") is None
    # Renaming the configured parent moves the generated ID with stable identity.
    options = deepcopy(dict(entry.options))
    old_child_id = child_id
    parent_id = f"sensor.renamed_{quantity}"
    child_id = parent_id + "_aqi"
    options[ATTR_DEVICES]["Room"][0][ATTR_ENTITY_ID] = parent_id
    options[ATTR_DEVICES]["Room"][0][CONF_NAME] = f"Renamed {quantity}"
    hass.config_entries.async_update_entry(entry, options=options)
    await hass.async_block_till_done()
    assert hass.states.get(old_child_id) is None
    assert registry.async_get(child_id).unique_id == uid
    assert registry.async_get(child_id).original_name == f"Renamed {quantity} Air Quality"
    hass.states.async_set("sensor.physical", "0")
    await hass.async_block_till_done()
    assert hass.states.get(parent_id).state == "0"
    await asyncio.sleep(0.1)
    await hass.async_block_till_done()
    assert hass.states.get(child_id).state == "good"
    options = deepcopy(dict(entry.options))
    options[ATTR_DEVICES]["Room"] = []
    hass.config_entries.async_update_entry(entry, options=options)
    await hass.async_block_till_done()
    assert hass.states.get(child_id) is None
    assert registry.async_get(child_id) is None
    assert hass.states.get("sensor.physical").state == "0"


async def test_configured_aqi_id_wins_over_generated_companion(hass, tmp_path, monkeypatch):
    monkeypatch.setattr("custom_components.virtual_layer.cfg.default_meta_file", lambda hass: str(tmp_path / "meta.json"))
    entry = MockConfigEntry(domain=COMPONENT_DOMAIN, data={ATTR_GROUP_NAME: "air"}, options={ATTR_DEVICES: {"Room": [
        {CONF_PLATFORM: "sensor", ATTR_ENTITY_ID: "sensor.pm25", CONF_NAME: "PM2.5", CONF_CLASS: "pm25", CONF_UNIT_OF_MEASUREMENT: "μg/m³", CONF_INITIAL_VALUE: "5"},
        {CONF_PLATFORM: "sensor", ATTR_ENTITY_ID: "sensor.pm25_aqi", CONF_NAME: "User-owned", CONF_CLASS: "temperature", CONF_UNIT_OF_MEASUREMENT: "°C", CONF_INITIAL_VALUE: "23"},
    ]}})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.pm25_aqi").state == "23"

    children = [s for s in hass.states.async_all("sensor") if s.attributes.get("source_entity_id") == "sensor.pm25" and s.attributes.get("sensor_type") == "matter_air_quality"]
    assert len(children) == 1
    assert children[0].entity_id != "sensor.pm25_aqi"
    assert children[0].state == "good"
    # A device-class change makes the source ineligible, cleaning up only its
    # generated companion even when its old name still contains PM2.5.
    options = deepcopy(dict(entry.options))
    options[ATTR_DEVICES]["Room"][0][CONF_CLASS] = "temperature"
    options[ATTR_DEVICES]["Room"][0][CONF_UNIT_OF_MEASUREMENT] = "°C"
    hass.config_entries.async_update_entry(entry, options=options)
    await hass.async_block_till_done()
    assert hass.states.get(children[0].entity_id) is None
    assert hass.states.get("sensor.pm25").state == "5"
    assert hass.states.get("sensor.pm25_aqi").state == "23"


@pytest.mark.parametrize("attributes", [
    {"device_class": ["pm25"]},
    {"device_class": {"value": "pm25"}},
    {"device_class": "pm25", "unit_of_measurement": ["μg/m³"]},
    {"device_class": "temperature", "unit_of_measurement": "°C", "state_class": {"bad": "measurement"}},
])
async def test_invalid_air_quality_metadata_does_not_block_device(hass, tmp_path, monkeypatch, caplog, attributes):
    monkeypatch.setattr("custom_components.virtual_layer.cfg.default_meta_file", lambda hass: str(tmp_path / "meta.json"))
    entry = MockConfigEntry(domain=COMPONENT_DOMAIN, data={ATTR_GROUP_NAME: "air"}, options={ATTR_DEVICES: {"Room": [
        {CONF_PLATFORM: "sensor", ATTR_ENTITY_ID: "sensor.legacy_pm25", CONF_NAME: "PM2.5", CONF_ATTRIBUTES: attributes, CONF_INITIAL_VALUE: "5"},
        {CONF_PLATFORM: "sensor", ATTR_ENTITY_ID: "sensor.valid_pm1", CONF_NAME: "PM1", CONF_CLASS: "pm1", CONF_UNIT_OF_MEASUREMENT: "μg/m³", CONF_INITIAL_VALUE: "1"},
    ]}})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.legacy_pm25") is not None
    assert hass.states.get("sensor.legacy_pm25_aqi") is None
    assert hass.states.get("sensor.valid_pm1_aqi").state == "good"
    assert not any(record.levelno >= 40 for record in caplog.records)
