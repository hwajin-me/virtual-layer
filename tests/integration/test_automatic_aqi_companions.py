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
    CONF_NATIVE_TEMPLATES,
)

pytestmark = pytest.mark.integration


async def test_legacy_sensor_aqi_migrates_domain_without_deleting_parent(hass, tmp_path, monkeypatch):
    from homeassistant.core import State
    from pytest_homeassistant_custom_component.common import mock_restore_cache
    monkeypatch.setattr("custom_components.virtual_layer.cfg.default_meta_file", lambda hass: str(tmp_path / "migration.json"))
    hass.states.async_set("sensor.raw_co2", "1000", {"device_class": "carbon_dioxide", "unit_of_measurement": "ppm"})
    record = {CONF_PLATFORM: "sensor", ATTR_ENTITY_ID: "sensor.room_co2", CONF_NAME: "Room CO2",
              CONF_CLASS: "carbon_dioxide", CONF_UNIT_OF_MEASUREMENT: "ppm",
              CONF_SOURCE_ENTITIES: ["sensor.raw_co2"],
              CONF_VALUE_TEMPLATE: "{{ states('sensor.raw_co2') }}"}
    entry = MockConfigEntry(domain=COMPONENT_DOMAIN, data={ATTR_GROUP_NAME: "Air"},
                            options={ATTR_DEVICES: {"Air": [record]}})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    uid = registry.async_get("air_quality.room_co2_aqi").unique_id
    assert await hass.config_entries.async_unload(entry.entry_id)
    registry.async_remove("air_quality.room_co2_aqi")
    legacy = registry.async_get_or_create("sensor", COMPONENT_DOMAIN, uid,
        suggested_object_id="room_co2_aqi", config_entry=entry)
    registry.async_update_entity(legacy.entity_id, name="Custom AQ", icon="mdi:cloud")
    mock_restore_cache(hass, [State(legacy.entity_id, "poor", {
        "air_quality_last_valid_at": "2026-09-01T00:00:00+00:00", "air_quality_stale": True})])
    hass.states.async_set("sensor.raw_co2", "unavailable")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    migrated = registry.async_get("air_quality.room_co2_aqi")
    assert migrated.unique_id == uid
    assert migrated.name == "Custom AQ"
    assert migrated.icon == "mdi:cloud"
    assert registry.async_get(legacy.entity_id) is None
    assert hass.states.get(legacy.entity_id) is None
    assert registry.async_get("sensor.room_co2") is not None
    assert hass.states.get("air_quality.room_co2_aqi").state == "poor"
    assert hass.states.get("air_quality.room_co2_aqi").attributes["air_quality_stale"] is True
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get("air_quality.room_co2_aqi").unique_id == uid


@pytest.mark.parametrize("device_class,name,eligible", [
    ("carbon_monoxide", "CO alarm", True), ("smoke", "Smoke alarm", True),
    ("gas", "Gas alarm", True), (None, "CO_DETECTOR 2 CO", True),
    (None, "Smoke Detector", True), ("motion", "CO hallway motion", False),
    ("door", "Door", False), (None, "Switch", False),
])
async def test_binary_air_alarm_companion(hass, tmp_path, monkeypatch, device_class, name, eligible):
    monkeypatch.setattr("custom_components.virtual_layer.cfg.default_meta_file", lambda hass: str(tmp_path / "binary-aq.json"))
    source, parent = "binary_sensor.physical", "binary_sensor.virtual_alarm"
    hass.states.async_set(source, "off")
    record = {CONF_PLATFORM: "binary_sensor", ATTR_ENTITY_ID: parent, CONF_NAME: name,
              CONF_SOURCE_ENTITIES: [source], CONF_VALUE_TEMPLATE: "{{ states('binary_sensor.physical') }}"}
    if device_class:
        record[CONF_CLASS] = device_class
    original = deepcopy(record)
    entry = MockConfigEntry(domain=COMPONENT_DOMAIN, data={ATTR_GROUP_NAME: "Alarms"},
                            options={ATTR_DEVICES: {"Alarms": [record]}})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    companion = "air_quality.virtual_alarm_aqi"
    if not eligible:
        assert hass.states.get(companion) is None
        return
    assert hass.states.get(companion).state == "good"
    registry = er.async_get(hass)
    assert registry.async_get(companion).device_id == registry.async_get(parent).device_id
    for value, expected in [("on", "poor"), ("unavailable", "poor"), ("off", "good")]:
        hass.states.async_set(source, value)
        await hass.async_block_till_done()
        await asyncio.sleep(0.1)
        await hass.async_block_till_done()
        assert hass.states.get(companion).state == expected
        assert hass.states.get(companion).attributes["air_quality_stale"] is (value == "unavailable")
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(companion).state == "good"
    assert hass.states.get(parent).state == "off"
    assert entry.options[ATTR_DEVICES]["Alarms"][0] == original


async def test_binary_air_alarm_without_initial_response_is_not_good(hass, tmp_path, monkeypatch):
    monkeypatch.setattr("custom_components.virtual_layer.cfg.default_meta_file", lambda hass: str(tmp_path / "missing-binary.json"))
    record = {CONF_PLATFORM: "binary_sensor", ATTR_ENTITY_ID: "binary_sensor.co_alarm",
              CONF_NAME: "CO alarm", CONF_CLASS: "carbon_monoxide",
              CONF_SOURCE_ENTITIES: ["binary_sensor.missing"],
              CONF_VALUE_TEMPLATE: "{{ is_state('binary_sensor.missing', 'on') }}"}
    entry = MockConfigEntry(domain=COMPONENT_DOMAIN, data={ATTR_GROUP_NAME: "Alarm"},
                            options={ATTR_DEVICES: {"Alarm": [record]}})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    companion = hass.states.get("air_quality.co_alarm_aqi")
    assert companion.state == "unknown"
    assert companion.attributes["air_quality_stale"] is True


async def test_classless_co_detector_zero_legacy_unit_survives_reload(hass, tmp_path, monkeypatch):
    """A CO detector needs no device class to expose its zero-ppm AQ grade."""
    monkeypatch.setattr("custom_components.virtual_layer.cfg.default_meta_file", lambda hass: str(tmp_path / "classless-co.json"))
    parent = "sensor.carbon_monoxide_detector_co_detector_2_co"
    record = {CONF_PLATFORM: "sensor", ATTR_ENTITY_ID: parent,
              CONF_NAME: "CO_DETECTOR 2 CO", CONF_INITIAL_VALUE: 0,
              CONF_ATTRIBUTES: {"unit_of_measurement": "ppm"}}
    original = deepcopy(record)
    entry = MockConfigEntry(domain=COMPONENT_DOMAIN, data={ATTR_GROUP_NAME: "Carbon Monoxide Detector"},
                            options={ATTR_DEVICES: {"Carbon Monoxide Detector": [record]}})
    entry.add_to_hass(hass)
    for reload in (False, True):
        if reload:
            assert await hass.config_entries.async_reload(entry.entry_id)
        else:
            assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        source = hass.states.get(parent)
        assert float(source.state) == 0
        assert source.attributes["unit_of_measurement"] == "ppm"
        assert source.attributes.get("device_class") is None
        companion = hass.states.get(parent.replace("sensor.", "air_quality.", 1) + "_aqi")
        assert companion is not None
        assert companion.state == "good"
        assert companion.attributes["air_quality_stale"] is False
        assert entry.options[ATTR_DEVICES]["Carbon Monoxide Detector"][0] == original


@pytest.mark.parametrize("quantity,values,expected", [
    ("carbon_dioxide", [1111, 974], "moderate"),
    ("carbon_monoxide", [2, 12], "fair"),
])
@pytest.mark.parametrize("custom", [False, True])
async def test_unitless_composite_uses_its_value_with_consistent_source_units(hass, tmp_path, monkeypatch, quantity, values, expected, custom):
    monkeypatch.setattr("custom_components.virtual_layer.cfg.default_meta_file", lambda hass: str(tmp_path / "source-fallback.json"))
    sources = ["sensor.physical_a", "sensor.physical_b"]
    for source, value in zip(sources, values):
        hass.states.async_set(source, str(value), {"device_class": quantity, "unit_of_measurement": "ppm"})
    parent = f"sensor.composite_{quantity}"
    record = {CONF_PLATFORM: "sensor", ATTR_ENTITY_ID: parent, CONF_NAME: quantity,
              CONF_CLASS: quantity, CONF_SOURCE_ENTITIES: sources,
              CONF_INITIAL_VALUE: str(sum(values) / 2),
              CONF_NATIVE_TEMPLATES: {"unit_of_measurement": "{{ none }}"}}
    if custom:
        record["air_quality_logic"] = {"mode": "automatic", "scope": "combined", "sources": [parent], "measurements": [{
            "mode": "measurement", "sources": [parent], "quantity": quantity, "unit": "ppm",
            "thresholds": [2000, 3000, 4000, 5000, 6000]}]}
    entry = MockConfigEntry(domain=COMPONENT_DOMAIN, data={ATTR_GROUP_NAME: "fallback"}, options={ATTR_DEVICES: {"Room": [record]}})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(parent.replace("sensor.", "air_quality.", 1) + "_aqi").state == ("unknown" if custom else expected)
    assert "unit_of_measurement" not in hass.states.get(parent).attributes
    assert float(hass.states.get(parent).state) == sum(values) / 2
    if not custom:
        assert hass.states.get(parent.replace("sensor.", "air_quality.", 1) + "_aqi").attributes["air_quality_evaluation_basis"] == "combined_inherited_unit"
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert hass.states.get(parent.replace("sensor.", "air_quality.", 1) + "_aqi").state == expected
        hass.states.async_set(sources[1], "unavailable")
        await hass.async_block_till_done()
        await asyncio.sleep(0.1)
        await hass.async_block_till_done()
        assert hass.states.get(parent.replace("sensor.", "air_quality.", 1) + "_aqi").state == expected
        assert hass.states.get(parent.replace("sensor.", "air_quality.", 1) + "_aqi").attributes["air_quality_stale"] is True
        options = deepcopy(dict(entry.options))
        options[ATTR_DEVICES]["Room"][0][CONF_NATIVE_TEMPLATES]["unit_of_measurement"] = "{{ 'ppm' }}"
        hass.config_entries.async_update_entry(entry, options=options)
        await hass.async_block_till_done()
        assert hass.states.get(parent.replace("sensor.", "air_quality.", 1) + "_aqi").state == ("moderate" if quantity == "carbon_dioxide" else "fair")
        assert hass.states.get(parent.replace("sensor.", "air_quality.", 1) + "_aqi").attributes["air_quality_evaluation_basis"] == "configured"


@pytest.mark.parametrize("second_unit,parent_unit,expected", [
    ("ppb", None, "unknown"), (None, None, "unknown"),
    ("ppm", "m³", "unknown"), ("ppb", "ppm", "moderate"),
    (" ppm ", None, "moderate"),
])
async def test_composite_unit_inheritance_does_not_guess_or_override(hass, tmp_path, monkeypatch, second_unit, parent_unit, expected):
    monkeypatch.setattr("custom_components.virtual_layer.cfg.default_meta_file", lambda hass: str(tmp_path / "unit-guard.json"))
    sources = ["sensor.co2_one", "sensor.co2_two"]
    for source, unit, value in zip(sources, ["ppm", second_unit], [1111, 974]):
        hass.states.async_set(source, str(value), {"device_class": "carbon_dioxide", "unit_of_measurement": unit})
    record = {CONF_PLATFORM: "sensor", ATTR_ENTITY_ID: "sensor.composite_co2",
              CONF_NAME: "Composite CO2", CONF_CLASS: "carbon_dioxide",
              CONF_SOURCE_ENTITIES: sources, CONF_INITIAL_VALUE: "1042.5",
              CONF_NATIVE_TEMPLATES: {"unit_of_measurement": "{{ " + (repr(parent_unit) if parent_unit else "none") + " }}"}}
    entry = MockConfigEntry(domain=COMPONENT_DOMAIN, data={ATTR_GROUP_NAME: "Air"},
                            options={ATTR_DEVICES: {"Air": [record]}})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("air_quality.composite_co2_aqi").state == expected
    assert float(hass.states.get("sensor.composite_co2").state) == 1042.5


@pytest.mark.parametrize("saved_recipe", [False, True])
async def test_companion_recovers_empty_recipe_when_native_metadata_arrives(hass, tmp_path, monkeypatch, saved_recipe):
    monkeypatch.setattr("custom_components.virtual_layer.cfg.default_meta_file", lambda hass: str(tmp_path / "late-meta.json"))
    source = "sensor.physical_co2"
    parent = "sensor.living_room_carbon_dioxide"
    hass.states.async_set(source, "1093")
    record = {CONF_PLATFORM: "sensor", ATTR_ENTITY_ID: parent,
              CONF_NAME: "Living Room Carbon Dioxide", CONF_SOURCE_ENTITIES: [source],
              CONF_VALUE_TEMPLATE: "{{ states('sensor.physical_co2') }}",
              CONF_NATIVE_TEMPLATES: {
                  "unit_of_measurement": "{{ state_attr('sensor.physical_co2', 'unit_of_measurement') }}",
                  "device_class": "{{ state_attr('sensor.physical_co2', 'device_class') }}",
              }}
    if saved_recipe:
        record["air_quality_logic"] = {"mode": "automatic", "sources": [parent], "measurements": []}
    original = deepcopy(record)
    entry = MockConfigEntry(domain=COMPONENT_DOMAIN, data={ATTR_GROUP_NAME: "late"},
                            options={ATTR_DEVICES: {"Room": [record]}})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(parent.replace("sensor.", "air_quality.", 1) + "_aqi").state == "unknown"
    hass.states.async_set(source, "1093", {"unit_of_measurement": "ppm", "device_class": "carbon_dioxide"})
    await hass.async_block_till_done()
    await asyncio.sleep(0.1)
    await hass.async_block_till_done()
    assert hass.states.get(parent.replace("sensor.", "air_quality.", 1) + "_aqi").state == "moderate"
    assert hass.states.get(parent.replace("sensor.", "air_quality.", 1) + "_aqi").attributes["air_quality_logic"]["measurements"]
    assert hass.states.get(parent).state == "1093"
    assert entry.options[ATTR_DEVICES]["Room"][0] == original
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(parent.replace("sensor.", "air_quality.", 1) + "_aqi").state == "moderate"
    hass.states.async_set(source, "500", {"unit_of_measurement": "ppm", "device_class": "carbon_dioxide"})
    await hass.async_block_till_done()
    await asyncio.sleep(0.1)
    await hass.async_block_till_done()
    assert hass.states.get(parent.replace("sensor.", "air_quality.", 1) + "_aqi").state == "good"
    hass.states.async_set(source, "500", {"unit_of_measurement": "m³", "device_class": "carbon_dioxide"})
    await hass.async_block_till_done()
    await asyncio.sleep(0.1)
    await hass.async_block_till_done()
    assert hass.states.get(parent.replace("sensor.", "air_quality.", 1) + "_aqi").state == "good"
    assert hass.states.get(parent.replace("sensor.", "air_quality.", 1) + "_aqi").attributes["air_quality_stale"] is True


@pytest.mark.parametrize("quantity", ["pm4", "nitrous_oxide"])
async def test_documented_pollutant_companion_loads_with_custom_thresholds(hass, tmp_path, monkeypatch, quantity):
    from custom_components.virtual_layer.air_quality_options import LEVELS
    monkeypatch.setattr("custom_components.virtual_layer.cfg.default_meta_file", lambda hass: str(tmp_path / "documented.meta.json"))
    parent = f"sensor.virtual_{quantity}"
    record = {CONF_PLATFORM: "sensor", ATTR_ENTITY_ID: parent, CONF_NAME: quantity,
              CONF_CLASS: quantity, CONF_UNIT_OF_MEASUREMENT: "μg/m³", CONF_INITIAL_VALUE: "3"}
    entry = MockConfigEntry(domain=COMPONENT_DOMAIN, data={ATTR_GROUP_NAME: "air"}, options={ATTR_DEVICES: {"Room": [record]}})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(parent.replace("sensor.", "air_quality.", 1) + "_aqi").state == "good"
    options = deepcopy(dict(entry.options))
    options[ATTR_DEVICES]["Room"][0]["air_quality_logic"] = {
        "mode": "automatic", "scope": "combined", "sources": [parent], "measurements": [{
            "mode": "measurement", "sources": [parent], "quantity": quantity, "unit": "μg/m³",
            "thresholds": [0.1, 0.2, 0.3, 0.4, 0.5], "levels": list(LEVELS)}]}
    hass.config_entries.async_update_entry(entry, options=options)
    await hass.async_block_till_done()
    assert hass.states.get(parent.replace("sensor.", "air_quality.", 1) + "_aqi").state == "extremely_poor"
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(parent).state == "3"
    assert hass.states.get(parent.replace("sensor.", "air_quality.", 1) + "_aqi").state == "extremely_poor"


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
    assert hass.states.get("air_quality.virtual_pm25_aqi").state == "good"
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("air_quality.virtual_pm25_aqi").state == "good"
    assert hass.states.get("sensor.physical").state == "25"


@pytest.mark.parametrize("quantity,unit,value,grade", [
    ("pm1", "μg/m³", "15", "moderate"),
    ("pm25", "μg/m³", "5", "good"),
    ("radon", "Bq/m³", "54.07", "fair"),
    ("benzene", "μg/m³", "3", "moderate"),
    ("aqi", "", "150", "moderate"),
    ("formaldehyde", "mg/m3", "0.003", "good"),
    ("formaldehyde", "mg/m³", "0.07", "poor"),
    ("volatile_organic_compounds", "μg/m³", "150", "good"),
    ("volatile_organic_compounds", "μg/m³", "950", "extremely_poor"),
    ("pm25", "ug/m^3", "5", "good"),
    ("radon", "Bq/m3", "54.07", "fair"),
])
async def test_existing_measurement_gets_live_companion_and_cleanup(hass, tmp_path, monkeypatch, quantity, unit, value, grade):
    monkeypatch.setattr("custom_components.virtual_layer.cfg.default_meta_file", lambda hass: str(tmp_path / "meta.json"))
    parent_id = f"sensor.virtual_{quantity}"
    child_id = parent_id.replace("sensor.", "air_quality.", 1) + "_aqi"
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
    child_id = parent_id.replace("sensor.", "air_quality.", 1) + "_aqi"
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

    children = [s for s in hass.states.async_all("air_quality") if s.attributes.get("source_entity_id") == "sensor.pm25" and s.attributes.get("sensor_type") == "matter_air_quality"]
    assert len(children) == 1
    assert children[0].entity_id == "air_quality.pm25_aqi"
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
    assert hass.states.get("air_quality.legacy_pm25_aqi") is None
    assert hass.states.get("air_quality.valid_pm1_aqi").state == "good"
    assert not any(record.levelno >= 40 for record in caplog.records)
