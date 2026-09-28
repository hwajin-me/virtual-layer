"""Source diagnostics report received Zigbee data without inventing health."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from homeassistant.components import mqtt
from homeassistant.util import dt as dt_util

from custom_components.virtual_layer import zigbee_refresh as refresh
from tests.unit.test_zigbee_refresh import ADDRESS, BASE, broker  # noqa: F401


def receive(broker, suffix, value):
    topic = f"{BASE}/{suffix}"
    broker.callbacks[topic](SimpleNamespace(topic=topic, payload=json.dumps(value)))


async def test_live_metrics_are_passive_shared_and_allowlisted(hass, broker):
    remove = refresh.async_watch_sources(hass, [broker.source])
    other = refresh.async_watch_sources(hass, [broker.source])
    await hass.async_block_till_done()
    attrs = refresh.diagnostic_attributes(hass, broker.source)
    assert attrs["source_protocol"] == "zigbee"
    assert attrs["zigbee_ieee_address"] == ADDRESS
    assert attrs["zigbee_linkquality"] is None
    assert attrs["zigbee_last_seen"] is None
    assert attrs["zigbee_bridge_availability"] == "online"
    assert attrs["zigbee_device_type"] == "Router"
    assert len(broker.unsubs) == 6
    now = dt_util.utcnow()
    receive(broker, "room/renamed_light", {
        "linkquality": 123, "battery": 75, "last_seen": now.isoformat(),
        "password": "never expose arbitrary payload fields",
    })
    attrs = refresh.diagnostic_attributes(hass, broker.source)
    assert attrs["zigbee_linkquality"] == 123
    assert attrs["zigbee_battery"] == 75
    assert attrs["zigbee_last_seen"] == now.isoformat()
    assert attrs["zigbee_last_seen_seconds_ago"] == 0
    assert "password" not in str(attrs)
    receive(broker, "room/renamed_light", {"state": "OFF"})
    assert refresh.diagnostic_attributes(hass, broker.source)["zigbee_linkquality"] == 123
    # Receiving diagnostics cannot trigger additional device queries or fix HA states.
    assert broker.publish.await_count == 1
    assert hass.states.get(broker.source).state == "unavailable"
    remove()
    assert all(not unsubscribe.called for unsubscribe in broker.unsubs)
    other()
    assert all(unsubscribe.call_count == 1 for unsubscribe in broker.unsubs)


async def test_unknown_availability_disconnect_and_reconnect(hass, broker, monkeypatch):
    connection = []
    unsubscribe = Mock()
    monkeypatch.setattr(mqtt, "async_subscribe_connection_status",
                        lambda _hass, callback: connection.append(callback) or unsubscribe)
    remove = refresh.async_watch_sources(hass, [broker.source])
    await hass.async_block_till_done()
    receive(broker, "room/renamed_light/availability", {"state": "offline"})
    attrs = refresh.diagnostic_attributes(hass, broker.source)
    assert attrs["zigbee_device_availability"] == "offline"
    assert attrs["zigbee_bridge_availability"] == "online"
    receive(broker, "bridge/state", "offline")
    assert refresh.diagnostic_attributes(hass, broker.source)["zigbee_bridge_availability"] == "offline"
    receive(broker, "room/renamed_light/availability", {"state": []})
    assert refresh.diagnostic_attributes(hass, broker.source)["zigbee_device_availability"] == "unknown"
    hass.data[mqtt.DATA_MQTT].client.connected = False
    connection[0](False)
    await hass.async_block_till_done()
    attrs = refresh.diagnostic_attributes(hass, broker.source)
    assert attrs["zigbee_mqtt_connected"] is False
    assert attrs["zigbee_bridge_availability"] == "unknown"
    hass.data[mqtt.DATA_MQTT].client.connected = True
    connection[0](True)
    await hass.async_block_till_done()
    assert refresh.diagnostic_attributes(hass, broker.source)["zigbee_bridge_availability"] == "unknown"
    receive(broker, "bridge/state", {"state": "online"})
    assert refresh.diagnostic_attributes(hass, broker.source)["zigbee_bridge_availability"] == "online"
    remove()
    unsubscribe.assert_called_once()


async def test_inventory_rename_removal_and_late_discovery(hass, broker):
    cache = hass.data[mqtt.DATA_MQTT].debug_info_entities
    discovery = cache.pop(broker.source)
    remove = refresh.async_watch_sources(hass, [broker.source])
    await hass.async_block_till_done()
    assert refresh.diagnostic_attributes(hass, broker.source) == {}
    cache[broker.source] = discovery
    manager = hass.data[refresh._DATA]
    await manager._refresh()
    receive(broker, "room/renamed_light", {"linkquality": 200})
    old_unsubs = list(broker.unsubs[4:])
    broker.inventory[0]["friendly_name"] = "new/name"
    receive(broker, "bridge/devices", broker.inventory)
    assert refresh.diagnostic_attributes(hass, broker.source)["zigbee_linkquality"] is None
    await hass.async_block_till_done()
    assert all(unsubscribe.call_count == 1 for unsubscribe in old_unsubs)
    receive(broker, "new/name", {"linkquality": 50})
    assert refresh.diagnostic_attributes(hass, broker.source)["zigbee_linkquality"] == 50
    receive(broker, "bridge/devices", [])
    await hass.async_block_till_done()
    attrs = refresh.diagnostic_attributes(hass, broker.source)
    assert attrs["zigbee_friendly_name"] is None
    assert attrs["zigbee_linkquality"] is None
    assert not manager.diagnostics
    cache.pop(broker.source)
    await manager._refresh()
    assert refresh.diagnostic_attributes(hass, broker.source) == {}
    remove()
    assert all(unsubscribe.call_count == 1 for unsubscribe in broker.unsubs)


@pytest.mark.parametrize("value", [None, True, [], {}, "50", -1, 256, float("nan"), float("inf")])
async def test_invalid_values_clear_previous_metrics(hass, broker, value):
    remove = refresh.async_watch_sources(hass, [broker.source])
    await hass.async_block_till_done()
    receive(broker, "room/renamed_light", {"linkquality": 70, "battery": 50})
    receive(broker, "room/renamed_light", {"linkquality": value, "battery": value})
    attrs = refresh.diagnostic_attributes(hass, broker.source)
    assert attrs["zigbee_linkquality"] is None
    assert attrs["zigbee_battery"] is None
    remove()


@pytest.mark.parametrize("value, expected", [
    (0, "1970-01-01T00:00:00+00:00"),
    (1704067200000, "2024-01-01T00:00:00+00:00"),
    ("2024-01-01T09:00:00+09:00", "2024-01-01T00:00:00+00:00"),
    ("2024-01-01T00:00:00", None), ("bad", None),
    (float("inf"), None), (10**500, None), (True, None), ({}, None),
])
def test_last_seen_formats(value, expected):
    assert refresh._last_seen(value) == expected


async def test_normal_mqtt_entities_are_not_misidentified(hass, broker):
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    entry = er.async_get(hass).async_get(broker.source)
    dr.async_get(hass).async_update_device(entry.device_id, new_identifiers={("mqtt", "ordinary")})
    assert refresh.diagnostic_attributes(hass, broker.source) == {}
    assert refresh.diagnostic_attributes(hass, "sensor.missing") == {}


async def test_subscription_failure_cleans_partial_subscription(hass, broker, monkeypatch):
    original = mqtt.async_subscribe

    async def subscribe(hass, topic, callback, qos):
        if topic.endswith("/availability"):
            raise ValueError("subscription failed")
        return await original(hass, topic, callback, qos)

    monkeypatch.setattr(mqtt, "async_subscribe", subscribe)
    remove = refresh.async_watch_sources(hass, [broker.source])
    await hass.async_block_till_done()
    assert not hass.data[refresh._DATA].diagnostics
    assert broker.unsubs[-1].call_count == 1
    assert broker.publish.await_count == 1
    remove()
    assert all(unsubscribe.call_count == 1 for unsubscribe in broker.unsubs)


async def test_sleeping_device_without_availability_or_last_seen(hass, broker, monkeypatch):
    broker.inventory[0]["power_source"] = "Battery"
    original = mqtt.async_subscribe

    async def subscribe(hass, topic, callback, qos):
        if topic.endswith("/availability"):
            broker.callbacks[topic] = callback
            unsubscribe = Mock()
            broker.unsubs.append(unsubscribe)
            return unsubscribe
        return await original(hass, topic, callback, qos)

    monkeypatch.setattr(mqtt, "async_subscribe", subscribe)
    remove = refresh.async_watch_sources(hass, [broker.source])
    await hass.async_block_till_done()
    receive(broker, "room/renamed_light", {"linkquality": 0, "battery": 0})
    attrs = refresh.diagnostic_attributes(hass, broker.source)
    assert attrs["zigbee_linkquality"] == 0
    assert attrs["zigbee_battery"] == 0
    assert attrs["zigbee_device_availability"] == "unknown"
    assert attrs["zigbee_last_seen"] is None
    assert attrs["zigbee_last_seen_seconds_ago"] is None
    broker.publish.assert_not_awaited()
    remove()


@pytest.mark.parametrize("payload", ["not-json", "[]", "null", "{" + "x" * 65536])
async def test_malformed_messages_preserve_last_reported_sample(hass, broker, payload):
    remove = refresh.async_watch_sources(hass, [broker.source])
    await hass.async_block_till_done()
    receive(broker, "room/renamed_light", {"linkquality": 100})
    topic = f"{BASE}/room/renamed_light"
    broker.callbacks[topic](SimpleNamespace(topic=topic, payload=payload))
    assert refresh.diagnostic_attributes(hass, broker.source)["zigbee_linkquality"] == 100
    remove()


async def test_hub_router_and_directional_link_quality(hass, broker):
    remove = refresh.async_watch_sources(hass, [broker.source])
    await hass.async_block_till_done()
    hub, router = "0x0000000000000001", "0x0000000000000002"
    receive(broker, "bridge/info", {
        "coordinator": {"ieee_address": hub, "type": "zStack", "secret": "not exposed"},
        "network": {"channel": 15, "network_key": "secret key"},
        "config": {"password": "secret password"},
    })
    topology = {
        "nodes": [
            {"ieeeAddr": ADDRESS, "friendlyName": "Device", "type": "EndDevice"},
            {"ieeeAddr": hub, "friendlyName": "Hub", "type": "Coordinator"},
            {"ieeeAddr": router, "friendlyName": "Hall Router", "type": "Router"},
        ],
        "links": [
            {"source": {"ieeeAddr": ADDRESS}, "target": {"ieeeAddr": router}, "relationship": 1, "lqi": 180},
            {"source": {"ieeeAddr": ADDRESS}, "target": {"ieeeAddr": hub}, "relationship": 2, "linkquality": 60},
        ],
    }
    receive(broker, "bridge/response/networkmap", {"status": "ok", "data": {"type": "raw", "value": topology}})
    receive(broker, "room/renamed_light", {"rssi": -65})
    attrs = refresh.diagnostic_attributes(hass, broker.source)
    assert attrs["zigbee_hub"] == {"ieee_address": hub, "role": "Coordinator", "type": "zStack"}
    assert attrs["zigbee_channel"] == 15
    assert attrs["zigbee_rssi"] == -65
    assert attrs["zigbee_parent"]["ieee_address"] == router
    assert attrs["zigbee_neighbors"][0]["linkquality"] == 180
    assert attrs["zigbee_neighbors"][0]["reporter"] == router
    assert "secret" not in json.dumps(attrs)
    updated = attrs["zigbee_networkmap_updated"]
    receive(broker, "bridge/response/networkmap", {"status": "error", "error": "private"})
    attrs = refresh.diagnostic_attributes(hass, broker.source)
    assert attrs["zigbee_networkmap_status"] == "error"
    assert attrs["zigbee_networkmap_updated"] == updated
    assert attrs["zigbee_parent"]["ieee_address"] == router
    # Multiple claimed parents are reported as candidates, not arbitrarily selected.
    topology["links"][1]["relationship"] = 1
    receive(broker, "bridge/response/networkmap", {"status": "ok", "data": {"type": "raw", "value": topology}})
    attrs = refresh.diagnostic_attributes(hass, broker.source)
    assert attrs["zigbee_parent"] is None
    assert len(attrs["zigbee_parent_candidates"]) == 2
    # Neighbor says "parent" in this device's table: reverse direction is valid too.
    topology["links"] = [{"source": {"ieeeAddr": router}, "target": {"ieeeAddr": ADDRESS}, "relationship": 0, "lqi": 155}]
    receive(broker, "bridge/response/networkmap", {"status": "ok", "data": {"type": "raw", "value": topology}})
    assert refresh.diagnostic_attributes(hass, broker.source)["zigbee_parent"]["ieee_address"] == router
    remove()


async def test_network_map_is_manual_and_rate_limited(hass, broker):
    from homeassistant.exceptions import HomeAssistantError

    remove = refresh.async_watch_sources(hass, [broker.source])
    await hass.async_block_till_done()
    assert all(not call.args[1].endswith("networkmap") for call in broker.publish.await_args_list)
    await refresh.async_request_networkmap(hass, [broker.source, broker.source])
    broker.publish.assert_awaited_with(hass, f"{BASE}/bridge/request/networkmap",
                                     '{"type":"raw","routes":false}', qos=0, retain=False)
    assert refresh.diagnostic_attributes(hass, broker.source)["zigbee_networkmap_status"] == "pending"
    with pytest.raises(HomeAssistantError, match="two minutes"):
        await refresh.async_request_networkmap(hass, [broker.source])
    receive(broker, "bridge/state", {"state": "offline"})
    with pytest.raises(HomeAssistantError, match="not online"):
        await refresh.async_request_networkmap(hass, [broker.source])
    remove()
