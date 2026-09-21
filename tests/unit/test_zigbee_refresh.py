"""Zigbee refresh uses device capabilities without forging MQTT availability."""

import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.components import mqtt
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.virtual_layer import zigbee_refresh as refresh

ADDRESS = "0x00124b0012345678"
BASE = "house/zigbee"
DEVICE = {
    "ieee_address": ADDRESS, "supported": True, "disabled": False,
    "type": "Router", "power_source": "Mains (single phase)",
    "friendly_name": "room/renamed_light",
    "definition": {"exposes": [{"type": "light", "features": [
        {"type": "binary", "property": "state", "access": 7},
        {"type": "numeric", "property": "brightness", "access": 3},
    ]}]},
}


@pytest.fixture
def broker(hass, monkeypatch):
    entry = MockConfigEntry(domain="mqtt")
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("mqtt", f"zigbee2mqtt_{ADDRESS}")},
    )
    source = er.async_get(hass).async_get_or_create(
        "light", "mqtt", f"{ADDRESS}_light", device_id=device.id,
        config_entry=entry, suggested_object_id="source_light",
    ).entity_id
    hass.data[mqtt.DATA_MQTT] = SimpleNamespace(
        client=SimpleNamespace(connected=True), debug_info_entities={source: {
            "discovery_data": {"discovery_payload": {"availability": [
                {"topic": f"{BASE}/bridge/state"},
                {"topic": f"{BASE}/room/renamed_light/availability"},
            ]}},
        }},
    )
    hass.states.async_set(source, "unavailable")
    callbacks, unsubs = {}, []
    inventory = [copy.deepcopy(DEVICE)]

    async def subscribe(_hass, topic, callback, _qos):
        callbacks[topic] = callback
        callback(SimpleNamespace(topic=topic, payload=json.dumps(
            inventory if topic.endswith("/devices") else {"state": "online"},
        )))
        remove = Mock()
        unsubs.append(remove)
        return remove

    publish = AsyncMock()
    monkeypatch.setattr(mqtt, "async_subscribe", subscribe)
    monkeypatch.setattr(mqtt, "async_publish", publish)
    return SimpleNamespace(source=source, publish=publish, callbacks=callbacks,
                           unsubs=unsubs, inventory=inventory)


async def test_shared_requests_backoff_and_real_source_recovery(hass, broker):
    first = refresh.async_watch_sources(hass, [broker.source])
    second = refresh.async_watch_sources(hass, [broker.source])
    await hass.async_block_till_done()
    manager = hass.data[refresh._DATA]
    target = (BASE, ADDRESS)
    broker.publish.assert_awaited_once_with(
        hass, f"{BASE}/{ADDRESS}/get", '{"state": ""}', qos=0, retain=False,
    )
    assert hass.states.get(broker.source).state == "unavailable"
    assert manager.retry[target][1] == 1
    await manager._refresh()
    assert broker.publish.await_count == 1
    manager.next_publish = 0
    manager.retry[target] = (0, 1)
    await manager._refresh()
    assert manager.retry[target][1] == 2
    assert 119 < manager.retry[target][0] - hass.loop.time() <= 120

    hass.states.async_set(broker.source, "on")
    manager.next_publish = 0
    manager.retry[target] = (0, 8)
    await manager._refresh()
    assert manager.retry[target][1] == 0
    assert 899 < manager.retry[target][0] - hass.loop.time() <= 900
    first()
    assert refresh._DATA in hass.data
    assert all(not remove.called for remove in broker.unsubs)
    second()
    second()
    assert refresh._DATA not in hass.data
    assert all(remove.call_count == 1 for remove in broker.unsubs)


@pytest.mark.parametrize("change", [
    {"power_source": "Battery"}, {"power_source": None}, {"disabled": True},
    {"supported": False}, {"definition": None}, {"type": "Coordinator"},
    {"definition": {"exposes": [{"property": "state", "type": "binary", "access": 3}]}},
])
async def test_no_queries_for_sleeping_disabled_or_non_readable_devices(hass, broker, change):
    broker.inventory[0].update(change)
    remove = refresh.async_watch_sources(hass, [broker.source])
    await hass.async_block_till_done()
    broker.publish.assert_not_awaited()
    remove()


async def test_offline_bridge_and_mqtt_disconnect_pause_queries(hass, broker):
    remove = refresh.async_watch_sources(hass, [broker.source])
    await hass.async_block_till_done()
    manager = hass.data[refresh._DATA]
    broker.publish.reset_mock()
    manager.next_publish = 0
    manager.retry.clear()
    topic = f"{BASE}/bridge/state"
    broker.callbacks[topic](SimpleNamespace(topic=topic, payload='{"state":"offline"}'))
    await manager._refresh()
    broker.publish.assert_not_awaited()
    broker.callbacks[topic](SimpleNamespace(topic=topic, payload='{"state":"online"}'))
    hass.data[mqtt.DATA_MQTT].client.connected = False
    await manager._refresh()
    assert manager.bridges[BASE]["online"] is False
    broker.publish.assert_not_awaited()
    hass.data[mqtt.DATA_MQTT].client.connected = True
    await manager._refresh()
    broker.publish.assert_not_awaited()
    broker.callbacks[topic](SimpleNamespace(topic=topic, payload='{"state":"online"}'))
    await manager._refresh()
    broker.publish.assert_awaited_once()
    remove()


async def test_late_discovery_and_inventory_removal(hass, broker):
    cache = hass.data[mqtt.DATA_MQTT].debug_info_entities
    saved = cache.pop(broker.source)
    remove = refresh.async_watch_sources(hass, [broker.source])
    await hass.async_block_till_done()
    manager = hass.data[refresh._DATA]
    broker.publish.assert_not_awaited()
    cache[broker.source] = saved
    await manager._refresh()
    broker.publish.assert_awaited_once()
    topic = f"{BASE}/bridge/devices"
    broker.callbacks[topic](SimpleNamespace(topic=topic, payload="[]"))
    manager.next_publish = 0
    manager.retry.clear()
    await manager._refresh()
    assert broker.publish.await_count == 1
    remove()


async def test_missing_mqtt_does_not_break_non_mqtt_sources(hass):
    remove = refresh.async_watch_sources(hass, ["sensor.normal"])
    await hass.async_block_till_done()
    assert not hass.data[refresh._DATA].bridges
    remove()


@pytest.mark.parametrize("payload", [None, [], {}, {"availability": False},
    {"availability": [{"topic": "house/+/bridge/state"}]},
    {"availability": [{"topic": "/bridge/state"}]},
])
def test_invalid_discovery_is_not_used(hass, broker, payload):
    hass.data[mqtt.DATA_MQTT].debug_info_entities[broker.source]["discovery_data"][
        "discovery_payload"
    ] = payload
    assert refresh._source_target(hass, broker.source) is None


def test_capability_filter_does_not_flatten_composites_or_request_settings():
    device = copy.deepcopy(DEVICE)
    device["definition"]["exposes"] += [
        {"property": "color", "type": "composite", "access": 7, "features": [
            {"property": "temperature", "type": "numeric", "access": 7},
        ]},
        {"property": "voltage", "type": "numeric", "access": 7, "category": "config"},
        {"property": "energy", "type": "numeric", "access": "7"},
        {"property": "power", "type": "numeric", "access": 5},
    ]
    assert refresh._read_payload(device) == {"state": "", "power": ""}


def test_multi_endpoint_and_malformed_capabilities_are_isolated():
    device = copy.deepcopy(DEVICE)
    device["definition"]["exposes"] = [{"type": "switch", "features": [
        {"property": "state_left", "name": "state", "endpoint": "left",
         "type": "binary", "access": 7},
        {"property": "state", "type": [], "access": 7},
        {"property": "temperature", "type": "numeric", "access": 5},
    ]}]
    assert refresh._read_payload(device) == {"state_left": "", "temperature": ""}
    device["power_source"] = []
    assert refresh._read_payload(device) == {}


async def test_publish_failure_is_bounded_and_does_not_force_availability(hass, broker):
    from homeassistant.exceptions import HomeAssistantError

    broker.publish.side_effect = HomeAssistantError("broker unavailable")
    remove = refresh.async_watch_sources(hass, [broker.source])
    await hass.async_block_till_done()
    manager = hass.data[refresh._DATA]
    await manager._refresh()
    assert broker.publish.await_count == 1
    assert hass.states.get(broker.source).state == "unavailable"
    remove()


async def test_invalid_inventory_stops_using_previous_capabilities(hass, broker):
    remove = refresh.async_watch_sources(hass, [broker.source])
    await hass.async_block_till_done()
    manager = hass.data[refresh._DATA]
    manager.next_publish = 0
    manager.retry.clear()
    topic = f"{BASE}/bridge/devices"
    broker.callbacks[topic](SimpleNamespace(topic=topic, payload="not-json"))
    await manager._refresh()
    assert broker.publish.await_count == 1
    remove()


async def test_new_outage_does_not_wait_for_healthy_poll(hass, broker):
    hass.states.async_set(broker.source, "on")
    remove = refresh.async_watch_sources(hass, [broker.source])
    await hass.async_block_till_done()
    manager = hass.data[refresh._DATA]
    assert manager.retry[(BASE, ADDRESS)][0] > hass.loop.time() + 890
    hass.states.async_set(broker.source, "unavailable")
    manager.next_publish = 0
    await manager._refresh()
    assert broker.publish.await_count == 2
    assert manager.retry[(BASE, ADDRESS)][1] == 1
    remove()
