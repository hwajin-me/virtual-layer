"""Real Virtual Layer platforms recover only after original MQTT states recover."""

import pytest
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.virtual_layer import zigbee_refresh as refresh
from tests.unit import test_zigbee_refresh

broker = test_zigbee_refresh.broker


@pytest.mark.parametrize("domain", ["sensor", "infrared"])
async def test_source_recovery_reload_and_unload(hass, broker, domain):
    target = f"{domain}.zigbee_virtual"
    entity = {
        "platform": domain, "name": "Zigbee virtual", "entity_id": target,
        "source_entities": [broker.source],
        "value_template": "{{ states('" + broker.source + "') }}",
        "availability_template": "{{ has_value('" + broker.source + "') }}",
    }
    entry = MockConfigEntry(
        domain="virtual_layer", data={"group_name": "Zigbee recovery"},
        options={"devices": {"Zigbee recovery": [entity]}},
    )
    entry.add_to_hass(hass)
    assert await async_setup_component(hass, "virtual_layer", {})
    await hass.async_block_till_done()
    assert refresh._DATA in hass.data
    assert broker.publish.await_count == 1
    state = hass.states.get(target)
    assert state is not None
    assert state.state in {"unknown", "unavailable"}
    assert hass.states.get(broker.source).state == "unavailable"

    # Only an actual source state event restores the virtual entity.
    hass.states.async_set(broker.source, "good")
    await hass.async_block_till_done()
    assert hass.states.get(target).state == "good"
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(target).state == "good"
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert refresh._DATA not in hass.data
    assert all(remove.call_count == 1 for remove in broker.unsubs)
