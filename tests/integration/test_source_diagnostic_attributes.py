"""Native and state-only entities carry live source diagnostics without cycles."""

import asyncio

import pytest
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry


@pytest.mark.parametrize("domain", ["sensor", "tag"])
async def test_virtual_source_cycles_settle_and_never_nest_diagnostics(hass, domain):
    first, second = f"{domain}.first", "sensor.second"
    entry = MockConfigEntry(domain="virtual_layer", data={"group_name": "Cycle"}, options={
        "devices": {"Cycle": [
            {"platform": domain, "name": "First", "entity_id": first,
             "initial_value": "ready", "source_entities": [second]},
            {"platform": "sensor", "name": "Second", "entity_id": second,
             "initial_value": "ready", "source_entities": [first]},
        ]},
    })
    entry.add_to_hass(hass)
    assert await async_setup_component(hass, "virtual_layer", {})
    await asyncio.wait_for(hass.async_block_till_done(), 5)
    for target, source in ((first, second), (second, first)):
        record = hass.states.get(target).attributes["source_diagnostics"][source]
        assert record["source_state"] == "ready"
        assert "source_diagnostics" not in record["source_attributes"]
        assert "source_configuration" not in record["source_attributes"]
    await hass.services.async_call("virtual_layer", "set_state",
                                  {"entity_id": second, "value": "changed"}, blocking=True)
    await asyncio.wait_for(hass.async_block_till_done(), 5)
    assert hass.states.get(first).attributes["source_diagnostics"][second]["source_state"] == "changed"
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert "virtual_layer_zigbee_refresh" not in hass.data
