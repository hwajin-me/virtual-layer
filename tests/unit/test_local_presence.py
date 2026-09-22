"""AB Gateway advertisements and explicitly configured Wi-Fi connections."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import voluptuous as vol
from homeassistant.components import bluetooth
from homeassistant.util import dt as dt_util

from custom_components.virtual_layer.local_presence import LocalPresence, normalize
from custom_components.virtual_layer.device_tracker import VirtualDeviceTracker
from custom_components.virtual_layer import config_flow as flow

pytestmark = pytest.mark.unit
MAC = "AA:BB:CC:DD:EE:FF"


def tracker(hass, **config):
    config = {
        "name": "Presence",
        "entity_id": "device_tracker.presence_test",
        "initial_value": "not_home",
        "initial_availability": True,
        "local_presence": {"wifi_entities": ["binary_sensor.phone_wifi"]},
        **config,
    }
    entity = VirtualDeviceTracker(config)
    entity.hass = hass
    entity.async_schedule_update_ha_state = Mock()
    entity._create_state(config)
    return entity


@pytest.mark.parametrize(
    "value",
    [
        {"ble_addresses": ["not-a-mac"]},
        {"ble_addresses": [MAC], "ble_sources": []},
        {"wifi_entities": ["switch.wifi"]},
        {"wifi_entities": ["sensor.ssid"]},
        {"wifi_entities": ["binary_sensor.wifi"], "ble_timeout": True},
        {"wifi_entities": ["binary_sensor.wifi"], "ble_min_rssi": -90.1},
        {"wifi_entities": ["binary_sensor.wifi"], "ble_timeout": float("inf")},
        {},
        {"wifi_entities": "binary_sensor.wifi"},
        {"ble_addresses": [MAC], "ble_sources": [None]},
    ],
)
def test_invalid_settings_are_rejected(value):
    with pytest.raises(vol.Invalid):
        normalize(value)


def test_mac_normalization_and_selected_sources_only():
    result = normalize(
        {"ble_addresses": ["aabbccddeeff", MAC], "ble_sources": ["ab_gateway"]}
    )
    assert result["ble_addresses"] == [MAC]


def test_ssid_matching_preserves_case_and_spaces(hass):
    adapter = LocalPresence(
        hass, normalize({"wifi_entities": ["sensor.ssid"], "wifi_ssids": [" Home "]})
    )
    for value, expected in (
        (" Home ", "home"),
        ("Home", "not_home"),
        (" home ", "not_home"),
    ):
        hass.states.async_set("sensor.ssid", value)
        assert adapter.wifi_state("sensor.ssid").state == expected


def test_polygon_presence_home_disconnect_and_missing(hass):
    from test_polygon_zones import GEOJSON

    hass.config.latitude, hass.config.longitude = 37.5, 127
    entity = tracker(hass, polygonal_zone={"strategy": "median", "geojson": GEOJSON})
    hass.states.async_set("binary_sensor.phone_wifi", "on")
    entity._update_polygon_from_sources()
    assert (entity.latitude, entity.longitude) == (37.5, 127)
    hass.states.async_set("binary_sensor.phone_wifi", "off")
    entity._update_polygon_from_sources()
    # Wi-Fi disconnect removes inferred Home coordinates, but cannot prove
    # that the device left an arbitrary GeoJSON area.
    assert entity.state == "unknown"
    assert entity.extra_state_attributes["polygon_inside"] is None
    assert entity.latitude is None
    hass.states.async_set("binary_sensor.phone_wifi", "unavailable")
    entity._update_polygon_from_sources()
    assert entity.state == "unknown"


@pytest.mark.parametrize(
    "entity_id,on,off",
    [
        ("device_tracker.phone_wifi", "home", "not_home"),
        ("binary_sensor.phone_wifi", "on", "off"),
        ("sensor.phone_ssid", "Home WiFi", "Office"),
    ],
)
def test_wifi_home_away_unknown_and_stable_connection(
    hass, freezer, entity_id, on, off
):
    hass.config.latitude, hass.config.longitude = 37.5, 127
    entity = tracker(
        hass, local_presence={"wifi_entities": [entity_id], "wifi_ssids": ["Home WiFi"]}
    )
    hass.states.async_set(entity_id, on)
    entity._update_location_from_sources()
    assert entity.state == "home"
    assert (entity.latitude, entity.longitude) == (37.5, 127)
    freezer.tick(timedelta(hours=12))
    entity._update_location_from_sources()
    assert entity.state == "home"
    for value, expected in (
        (off, "not_home"),
        ("unavailable", "unknown"),
        ("unknown", "unknown"),
    ):
        hass.states.async_set(entity_id, value)
        entity._update_location_from_sources()
        assert entity.state == expected
        assert entity.latitude is None


def test_wifi_any_connected_and_unknown_is_not_disconnected(hass):
    entity = tracker(
        hass,
        local_presence={
            "wifi_entities": ["binary_sensor.phone_wifi", "binary_sensor.watch_wifi"]
        },
    )
    hass.states.async_set("binary_sensor.phone_wifi", "on")
    entity._update_location_from_sources()
    assert entity.state == "home"
    hass.states.async_set("binary_sensor.phone_wifi", "off")
    entity._update_location_from_sources()
    assert entity.state == "unknown"
    hass.states.async_set("binary_sensor.watch_wifi", "off")
    entity._update_location_from_sources()
    assert entity.state == "not_home"


def test_ble_cache_rssi_timeout_and_gateway_filter(hass, monkeypatch):
    clock = [1000.0]
    scanner = SimpleNamespace(
        source="ab_gateway", discovered_device_timestamps={MAC.lower(): 1000.0}
    )
    device = SimpleNamespace(scanner=scanner, advertisement=SimpleNamespace(rssi=-60))
    hass.config.components.add("bluetooth")
    monkeypatch.setattr(bluetooth, "MONOTONIC_TIME", lambda: clock[0])
    monkeypatch.setattr(bluetooth, "async_current_scanners", lambda _hass: [scanner])
    monkeypatch.setattr(
        bluetooth,
        "async_scanner_devices_by_address",
        lambda _hass, address, **kwargs: [device] if address == MAC.lower() else [],
    )
    adapter = LocalPresence(
        hass, normalize({"ble_addresses": [MAC], "ble_timeout": 30})
    )
    assert adapter.ble_states()["ble:" + MAC].state == "home"
    clock[0] += 20
    assert adapter.ble_states()["ble:" + MAC].state == "home"
    # Weak packets and repeated reads do not renew the last strong observation.
    scanner.discovered_device_timestamps[MAC.lower()] = clock[0]
    device.advertisement.rssi = -110
    clock[0] += 11
    assert adapter.ble_states()["ble:" + MAC].state == "not_home"
    scanner.discovered_device_timestamps[MAC.lower()] = clock[0]
    device.advertisement.rssi = -60
    assert adapter.ble_states()["ble:" + MAC].state == "home"
    scanner.source = "other_gateway"
    clock[0] += 31
    assert adapter.ble_states()["ble:" + MAC].state == "unknown"
    assert adapter.error == "scanner_unavailable"


def test_wifi_left_home_does_not_override_carried_gps(hass, freezer):
    entity = tracker(hass, source_entities=["device_tracker.phone_gps"])
    hass.config.latitude, hass.config.longitude = 37.5, 127
    hass.states.async_set("binary_sensor.phone_wifi", "on")
    hass.states.async_set(
        "device_tracker.phone_gps", "not_home", {"latitude": 37.5, "longitude": 127}
    )
    entity._update_location_from_sources()
    freezer.tick(timedelta(seconds=120))
    hass.states.async_set(
        "device_tracker.phone_gps", "not_home", {"latitude": 37.51, "longitude": 127}
    )
    entity._update_location_from_sources()
    assert entity.latitude == 37.51
    assert (
        entity.extra_state_attributes["location_priority_source"]
        == "device_tracker.phone_gps"
    )


def test_settings_create_edit_disable_and_self_reference():
    form = flow._entity_schema({"platform": "device_tracker", "entity_name": "WiFi"})(
        {}
    )
    form.update({"device_name": "Phone", "entity_id": "device_tracker.wifi"})
    settings = form["local_presence_settings"]
    settings.update(
        {
            "presence_enabled": True,
            "presence_wifi_entities": ["sensor.ssid"],
            "presence_wifi_ssids": "Home WiFi\nGuest",
            "presence_ble_addresses": "aabbccddeeff",
        }
    )
    _, entity = flow._build_entity_config(form)
    assert entity["local_presence"]["ble_addresses"] == [MAC]
    reopened = flow._entity_schema(flow._entity_form_defaults("Phone", entity))({})
    assert reopened["local_presence_settings"]["presence_wifi_entities"] == [
        "sensor.ssid"
    ]
    assert (
        flow._build_entity_config(reopened)[1]["local_presence"]
        == entity["local_presence"]
    )
    reopened["local_presence_settings"]["presence_enabled"] = False
    assert "local_presence" not in flow._build_entity_config(reopened)[1]
    settings["presence_wifi_entities"] = ["device_tracker.wifi"]
    with pytest.raises(flow.InvalidEntityReference):
        flow._build_entity_config(form)
