"""Native source state stays correct across config-entry edits and reloads."""

import copy

from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.virtual_layer.const import COMPONENT_DOMAIN


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
