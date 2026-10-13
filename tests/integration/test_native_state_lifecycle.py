"""Native source state stays correct across config-entry edits and reloads."""

import copy

import pytest

from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.virtual_layer.const import COMPONENT_DOMAIN
from custom_components.virtual_layer.config_flow import (
    _reference_entity_defaults, _build_entity_config, _entity_schema,
    _flatten_entity_form_sections,
)


@pytest.mark.parametrize("domain, mode, modes", [
    ("fan", "preset_mode", "preset_modes"),
    ("humidifier", "mode", "available_modes"),
])
async def test_generated_mode_helpers_recover_late_catalogs_and_reload(hass, tmp_path, monkeypatch, domain, mode, modes):
    monkeypatch.setattr("custom_components.virtual_layer.cfg.default_meta_file",
                        lambda _hass: str(tmp_path / "virtual_layer.meta.json"))
    sources = [f"{domain}.physical_first", f"{domain}.physical_second"]
    for source in sources:
        hass.states.async_set(source, "off")
    defaults = _reference_entity_defaults(hass, sources)
    defaults.update({"device_name": "Modes", "entity_name": "Modes", "entity_id": f"{domain}.late_modes"})
    device, config = _build_entity_config(_flatten_entity_form_sections(_entity_schema(defaults)({})))
    config["entity_id"] = f"{domain}.late_modes"
    config["availability_template"] = "{{ true }}"
    config["native_templates"]["is_on"] = "{{ true }}"
    entry = MockConfigEntry(domain=COMPONENT_DOMAIN, title=device,
                            data={"group_name": "late_modes"},
                            options={"devices": {device: [config]}})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    virtual_id = f"{domain}.late_modes"
    registry = er.async_get(hass)
    device_id = registry.async_get(virtual_id).device_id
    assert device_id is not None
    for status in ("unavailable", "on"):
        hass.states.async_set(sources[0], status, {mode: "Stale", modes: ["Stale"]})
        hass.states.async_set(sources[1], "on", {mode: "none", modes: ["none", "Custom"]})
        await hass.async_block_till_done()
        state = hass.states.get(virtual_id)
        assert state.attributes[mode] == ("none" if status == "unavailable" else "Stale")
        assert state.attributes[modes] == (["none", "Custom"] if status == "unavailable" else ["Stale", "none", "Custom"])
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(virtual_id).attributes[mode] == "Stale"
    assert registry.async_get(virtual_id).device_id == device_id
    for source in sources:
        hass.states.async_set(source, "off")
    await hass.async_block_till_done()
    state = hass.states.get(virtual_id)
    assert state.attributes.get(modes, []) == []
    assert state.attributes.get(mode) is None
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(virtual_id) is None


async def test_native_measurement_mode_and_movement_survive_reload(hass, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "custom_components.virtual_layer.cfg.default_meta_file",
        lambda _hass: str(tmp_path / "virtual_layer.meta.json"),
    )
    entities = [{
        "platform": "water_heater", "name": "Measured heater",
        "entity_id": "water_heater.native_regression", "initial_value": "Eco",
        "operation_list": ["off", "Eco"], "min_temp": 35, "max_temp": 85,
        "current_temperature": 20, "target_temperature": 50,
        "native_templates": {
            "current_temperature": "{{ 20 }}", "current_operation": "{{ 'Eco' }}",
        },
    }]
    for domain in ("cover", "valve"):
        entities.append({
            "platform": domain, "name": f"Moving {domain}",
            "entity_id": f"{domain}.native_regression", "initial_value": "open",
            "native_templates": {"is_closing": "{{ true }}", "current_position": "{{ 25 }}"},
        })
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN, title="Native regression",
        data={"group_name": "native_regression"},
        options={"devices": {"Native regression": entities}},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    def assert_states(temperature):
        heater = hass.states.get("water_heater.native_regression")
        assert heater.state == "Eco"
        assert heater.attributes["current_temperature"] == temperature
        assert heater.attributes["temperature"] == 50
        for domain in ("cover", "valve"):
            state = hass.states.get(f"{domain}.native_regression")
            assert state.state == "closing"
            assert state.attributes["current_position"] == 25

    assert_states(20)
    registry = er.async_get(hass)
    device_ids = {registry.async_get(entity["entity_id"]).device_id for entity in entities}
    assert len(device_ids) == 1 and None not in device_ids
    edited = copy.deepcopy(dict(entry.options))
    edited["devices"]["Native regression"][0]["native_templates"]["current_temperature"] = "{{ 95 }}"
    hass.config_entries.async_update_entry(entry, options=edited)
    await hass.async_block_till_done()
    assert_states(95)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert_states(95)
    assert {registry.async_get(entity["entity_id"]).device_id for entity in entities} == device_ids
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert all(hass.states.get(entity["entity_id"]) is None for entity in entities)
