"""Real Virtual Layer platforms recover only after original MQTT states recover."""

import json
from types import SimpleNamespace

import pytest
from homeassistant.helpers import entity_registry as er
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

    debug_id = "sensor.src_zigbee_virtual_debug1"
    def diagnostics():
        # HA omits extra attributes from unavailable native entity states.
        attributes = (hass.data[domain].get_entity(target).extra_state_attributes
                      if domain == "sensor" else hass.states.get(target).attributes)
        return attributes["source_diagnostics"][broker.source]

    debug = diagnostics()
    assert debug["source_integration"] == "zigbee2mqtt"
    assert debug["zigbee_linkquality"] is None
    registry = er.async_get(hass)
    assert registry.async_get(debug_id) is None
    assert hass.states.get(debug_id) is None
    topic = f"{test_zigbee_refresh.BASE}/room/renamed_light"
    broker.callbacks[topic](SimpleNamespace(topic=topic, payload=json.dumps({
        "linkquality": 144, "battery": 81,
        "last_seen": "2026-01-01T00:00:00Z",
    })))
    await hass.async_block_till_done()
    debug = diagnostics()
    assert debug["zigbee_linkquality"] == 144
    assert debug["zigbee_battery"] == 81
    assert debug["zigbee_last_seen"] == "2026-01-01T00:00:00+00:00"
    # Diagnostics update even though the original HA source has not changed.
    assert hass.states.get(broker.source).state == "unavailable"

    # Only an actual source state event restores the virtual entity.
    hass.states.async_set(broker.source, "good")
    await hass.async_block_till_done()
    assert hass.states.get(target).state == "good"
    assert hass.states.get(target).attributes["source_diagnostics"][broker.source]["source_integration"] == "zigbee2mqtt"
    await hass.services.async_call("virtual_layer", "refresh_source_networkmap",
                                  {"entity_id": target}, blocking=True)
    broker.publish.assert_awaited_with(
        hass, f"{test_zigbee_refresh.BASE}/bridge/request/networkmap",
        '{"type":"raw","routes":false}', qos=0, retain=False,
    )
    assert hass.states.get(target).attributes["source_diagnostics"][broker.source]["zigbee_networkmap_status"] == "pending"
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(target).state == "good"
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert refresh._DATA not in hass.data
    assert all(remove.call_count == 1 for remove in broker.unsubs)
