"""Air quality retains last-known grades without presenting them as fresh."""

import asyncio

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.virtual_layer import air_quality_options as aq
from custom_components.virtual_layer.const import COMPONENT_DOMAIN

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("domain", ["sensor", "air_quality"])
async def test_partial_sources_outage_reload_and_recovery(hass, tmp_path, monkeypatch, domain):
    monkeypatch.setattr("custom_components.virtual_layer.cfg.default_meta_file", lambda hass: str(tmp_path / "last-valid.json"))
    sources = ["sensor.pm_a", "sensor.pm_b"]
    attrs = {"device_class": "pm25", "unit_of_measurement": "μg/m³"}
    for source in sources:
        hass.states.async_set(source, "unknown", attrs)
    recipe = aq.automatic_recipe(sources, [hass.states.get(s) for s in sources])
    record = {"platform": domain, "entity_id": f"{domain}.combined_pm25", "name": "Combined PM25",
              "persistent": False, "source_entities": sources, "air_quality_logic": recipe,
              "availability_template": "{{ has_value('sensor.pm_a') and has_value('sensor.pm_b') }}"}
    if domain == "air_quality":
        record["native_templates"] = {"air_quality": aq.generate(recipe)}
    else:
        record.update({"class": "pm25", "unit_of_measurement": "μg/m³", "initial_value": "20"})
    entry = MockConfigEntry(domain=COMPONENT_DOMAIN, data={"group_name": "lastvalid"}, options={"devices": {"Room": [record]}})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    target = "sensor.combined_pm25_aqi" if domain == "sensor" else "air_quality.combined_pm25"
    assert hass.states.get(target).state in ("unknown", "unavailable")

    async def change(source, value):
        hass.states.async_set(source, value, attrs)
        await hass.async_block_till_done()
        await asyncio.sleep(0.1)
        await hass.async_block_till_done()

    await change(sources[0], "40")
    assert hass.states.get(target).state == "moderate"
    assert hass.states.get(target).attributes["air_quality_partial"] is True
    if domain == "air_quality":
        bridge = hass.states.get("sensor.combined_pm25_air_quality")
        assert bridge.attributes["air_quality_partial"] is True
        assert sources[1] in bridge.attributes["air_quality_missing_sources"]
    await change(sources[1], "80")
    assert hass.states.get(target).state == "poor"
    await change(sources[1], "unavailable")
    assert hass.states.get(target).state == "moderate"
    previous_time = hass.states.get(target).attributes["air_quality_last_valid_at"]
    observed = []
    remove = hass.bus.async_listen("state_changed", lambda event: observed.append(event.data["new_state"].state) if event.data["entity_id"] == target and event.data["new_state"] else None)
    await change(sources[0], "unknown")
    assert hass.states.get(target).state == "moderate"
    assert hass.states.get(target).attributes["air_quality_stale"] is True
    assert hass.states.get(target).attributes["air_quality_last_valid_at"] == previous_time
    assert "unknown" not in observed and "unavailable" not in observed
    remove()
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(target).state == "moderate"
    assert hass.states.get(target).attributes["air_quality_stale"] is True
    assert hass.states.get(target).attributes["air_quality_last_valid_at"] == previous_time
    await change(sources[1], "3")
    assert hass.states.get(target).state == "good"
    assert hass.states.get(target).attributes["air_quality_stale"] is False
    assert hass.states.get(target).attributes["air_quality_partial"] is True


@pytest.mark.parametrize("native", [False, True])
async def test_template_failure_retains_history_and_recovers(hass, tmp_path, monkeypatch, native):
    from custom_components.virtual_layer import get_entity_from_domain
    monkeypatch.setattr("custom_components.virtual_layer.cfg.default_meta_file", lambda hass: str(tmp_path / "template-error.json"))
    hass.states.async_set("sensor.trigger", "valid")
    template = "{{ 'good' if states('sensor.trigger') == 'valid' else 1 / 0 }}"
    record = {"platform": "air_quality", "entity_id": "air_quality.fallback", "name": "Fallback",
              "source_entities": ["sensor.trigger"]}
    record.update({"native_templates": {"air_quality": template}} if native else {"value_template": template})
    entry = MockConfigEntry(domain=COMPONENT_DOMAIN, data={"group_name": "failure"}, options={"devices": {"Room": [record]}})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    target = "air_quality.fallback"
    assert hass.states.get(target).state == "good"
    timestamp = hass.states.get(target).attributes["air_quality_last_valid_at"]
    entity = get_entity_from_domain(hass, "air_quality", target)
    entity._apply_templates()
    assert hass.states.get(target).attributes["air_quality_last_valid_at"] == timestamp
    hass.states.async_set("sensor.trigger", "invalid")
    await hass.async_block_till_done()
    await asyncio.sleep(0.1)
    await hass.async_block_till_done()
    result = hass.states.get(target)
    assert result.state == "good"
    assert result.attributes["air_quality_stale"] is True
    assert result.attributes["air_quality_fallback_reason"] == "template_error"
    assert result.attributes["air_quality_last_valid_at"] == timestamp
    hass.states.async_set("sensor.trigger", "valid")
    await hass.async_block_till_done()
    await asyncio.sleep(0.1)
    await hass.async_block_till_done()
    assert hass.states.get(target).attributes["air_quality_stale"] is False
    assert hass.states.get(target).attributes["air_quality_fallback_reason"] is None


@pytest.mark.parametrize("custom", [False, True])
async def test_late_metadata_repairs_parent_and_per_source_helpers_only(hass, tmp_path, monkeypatch, custom):
    monkeypatch.setattr("custom_components.virtual_layer.cfg.default_meta_file", lambda hass: str(tmp_path / "late-all.json"))
    source = "sensor.late_co2"
    hass.states.async_set(source, "1093")
    recipe = {"mode": "automatic", "sources": [source], "measurements": [], "per_source": True}
    helper = "{{ 'fair' }}" if custom else aq.generate(recipe)
    record = {"platform": "air_quality", "entity_id": "air_quality.whole", "name": "Whole",
              "source_entities": [source], "air_quality_logic": recipe,
              "native_templates": {"air_quality": helper}}
    entry = MockConfigEntry(domain=COMPONENT_DOMAIN, data={"group_name": "lateall"}, options={"devices": {"Room": [record]}})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set(source, "1093", {"device_class": "carbon_dioxide", "unit_of_measurement": "ppm"})
    await hass.async_block_till_done()
    await asyncio.sleep(0.1)
    await hass.async_block_till_done()
    assert hass.states.get("air_quality.whole").state == ("fair" if custom else "moderate")
    assert hass.states.get("sensor.whole_sensor_late_co2_air_quality").state == "moderate"
    assert entry.options["devices"]["Room"][0]["native_templates"]["air_quality"] == helper
