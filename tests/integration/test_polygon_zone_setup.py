"""Integration tests for polygon virtual device trackers."""

import copy
from datetime import timedelta
from threading import get_ident

import homeassistant.helpers.entity_registry as er
import pytest
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    CONF_NAME,
    CONF_PLATFORM,
)
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from homeassistant.util import dt as dt_util

from custom_components.virtual_layer.config_flow import (
    ACTION_ADD_ENTITY,
    CONF_ACTION,
    CONF_DOMAIN_SETTINGS,
    CONF_DAWARICH_AUTH_MODE_INPUT,
    CONF_DAWARICH_URL_INPUT,
    CONF_POLYGON_GEOJSON_JSON,
    CONF_POLYGON_STRATEGY_INPUT,
    CONF_REFERENCE_ENTITY_ID,
)
from custom_components.virtual_layer.const import (
    ATTR_DEVICE_ATTRIBUTES,
    ATTR_DEVICE_ID,
    ATTR_DEVICES,
    ATTR_GROUP_NAME,
    COMPONENT_DOMAIN,
    CONF_INITIAL_AVAILABILITY,
    CONF_INITIAL_VALUE,
    CONF_PERSISTENT,
    CONF_POLYGON_GEOJSON,
    CONF_POLYGON_PERSON_ENTITY,
    CONF_POLYGON_TRACKER_RULES,
    CONF_POLYGONAL_ZONE,
    CONF_POLYGON_ESPRESENSE_ANCHORS,
    CONF_PRESENCE_CLASSIFICATION,
    CONF_LOCATION_HELPER,
    CONF_SOURCE_ENTITIES,
)
from custom_components.virtual_layer.device_tracker import (
    VirtualDeviceTracker,
    ATTR_POLYGON_PERSON,
    ATTR_POLYGON_SELECTED_SOURCE,
    ATTR_POLYGON_SELECTED_MEMBERS,
    ATTR_POLYGON_ZONE,
)

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("polygon", [False, True])
async def test_tracker_periodic_refresh_stays_on_event_loop(hass, monkeypatch, polygon):
    """HA timers must not move tracker state/template evaluation to its executor."""
    method = "_update_polygon_from_sources" if polygon else "_update_location_from_sources"
    original = getattr(VirtualDeviceTracker, method)
    threads = []

    def record_refresh(self):
        threads.append(get_ident())
        return original(self)

    monkeypatch.setattr(VirtualDeviceTracker, method, record_refresh)
    hass.states.async_set(
        "device_tracker.timer_source", "not_home",
        {ATTR_LATITUDE: 37.5, ATTR_LONGITUDE: 127.0},
    )
    config = {
        CONF_PLATFORM: "device_tracker",
        CONF_NAME: "Timer Tracker",
        ATTR_ENTITY_ID: "device_tracker.timer_tracker",
        CONF_INITIAL_VALUE: "not_home",
        CONF_INITIAL_AVAILABILITY: True,
        CONF_PERSISTENT: False,
        CONF_SOURCE_ENTITIES: ["device_tracker.timer_source"],
    }
    config.update(
        {CONF_POLYGONAL_ZONE: {CONF_POLYGON_GEOJSON: GEOJSON}}
        if polygon else {CONF_LOCATION_HELPER: {"distance_threshold_meters": 300}}
    )
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "Timer Device"},
        options={ATTR_DEVICES: {"Timer Device": [config]}},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    threads.clear()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=2))
    await hass.async_block_till_done()
    assert threads
    assert set(threads) == {hass.loop_thread_id}
    assert hass.states.get("device_tracker.timer_tracker").attributes[ATTR_LATITUDE] == 37.5

GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {"name": "Seoul Home", "priority": 1},
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [126.9, 37.4],
                        [127.1, 37.4],
                        [127.1, 37.6],
                        [126.9, 37.6],
                        [126.9, 37.4],
                    ]
                ],
            },
        }
    ],
}


async def test_selecting_device_tracker_reopens_form_with_polygon_fields(hass):
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "empty"},
        options={ATTR_DEVICES: {}, ATTR_DEVICE_ATTRIBUTES: {}},
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(
        entry.entry_id,
        data={CONF_ACTION: ACTION_ADD_ENTITY},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_REFERENCE_ENTITY_ID: []},
    )
    defaults = result["data_schema"]({})
    assert CONF_POLYGON_GEOJSON_JSON not in defaults

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            **defaults,
            CONF_PLATFORM: "device_tracker",
            ATTR_ENTITY_ID: "device_tracker.family_polygon",
        },
    )

    assert result["type"] == FlowResultType.FORM
    polygon_defaults = result["data_schema"]({})
    polygon_defaults = polygon_defaults[CONF_DOMAIN_SETTINGS]
    assert CONF_POLYGON_GEOJSON_JSON in polygon_defaults
    assert polygon_defaults[CONF_POLYGON_STRATEGY_INPUT] == "majority"
    assert polygon_defaults[CONF_DAWARICH_URL_INPUT] == ""
    assert polygon_defaults[CONF_DAWARICH_AUTH_MODE_INPUT] == "bearer"
    assert polygon_defaults[CONF_PRESENCE_CLASSIFICATION] is False


async def test_combined_wifi_ble_and_gps_tracker_classifies_presence_in_hass(hass):
    """The integration publishes the classified state and GPS on a real setup."""
    hass.states.async_set(
        "device_tracker.phone", "not_home", {ATTR_LATITUDE: 37.5, ATTR_LONGITUDE: 127.0}
    )
    hass.states.async_set("binary_sensor.phone_wifi", "on")
    hass.states.async_set("sensor.phone_distance", "4")
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "presence"},
        options={
            ATTR_DEVICES: {
                "Presence": [
                    {
                        CONF_PLATFORM: "device_tracker",
                        CONF_NAME: "Presence",
                        ATTR_ENTITY_ID: "device_tracker.presence",
                        CONF_INITIAL_VALUE: "not_home",
                        CONF_INITIAL_AVAILABILITY: True,
                        CONF_PERSISTENT: False,
                        CONF_SOURCE_ENTITIES: [
                            "device_tracker.phone",
                            "binary_sensor.phone_wifi",
                            "sensor.phone_distance",
                        ],
                        CONF_LOCATION_HELPER: {
                            "distance_threshold_meters": 300,
                            "priority_window_seconds": 1800,
                        },
                        CONF_PRESENCE_CLASSIFICATION: True,
                    }
                ]
            },
            ATTR_DEVICE_ATTRIBUTES: {"Presence": {ATTR_DEVICE_ID: "presence"}},
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("device_tracker.presence")
    assert state.state == "home"
    assert state.attributes["location_classification"] == "home"

    hass.states.async_set("binary_sensor.phone_wifi", "off")
    await hass.async_block_till_done()
    state = hass.states.get("device_tracker.presence")
    assert state.state == "front_door"
    assert state.attributes[ATTR_LATITUDE] == 37.5


@pytest.mark.parametrize("explicit_meters", [False, True])
async def test_polygon_tracker_triangulates_espresense_anchors_into_geojson_zone(hass, explicit_meters, freezer):
    anchors = {
        "sensor.esp_a_distance": {ATTR_LATITUDE: 37.5000, ATTR_LONGITUDE: 126.9999},
        "sensor.esp_b_distance": {ATTR_LATITUDE: 37.5001, ATTR_LONGITUDE: 127.0000},
        "sensor.esp_c_distance": {ATTR_LATITUDE: 37.5000, ATTR_LONGITUDE: 127.0001},
    }
    hass.states.async_set("sensor.esp_a_distance", "885", {"unit_of_measurement": "cm"})
    hass.states.async_set(
        "sensor.esp_b_distance", "1105", {"unit_of_measurement": "cm"}
    )
    hass.states.async_set("sensor.esp_c_distance", "885", {"unit_of_measurement": "cm"})
    if explicit_meters:
        for entity_id, distance in zip(anchors, (8.85, 11.05, 8.85), strict=True):
            hass.states.async_set(entity_id, str(distance * 100), {
                "unit_of_measurement": "cm",
                "distance": distance * 100,
                "distance_meters": distance,
            })
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "espresense"},
        options={
            ATTR_DEVICES: {
                "ESPresense": [
                    {
                        CONF_PLATFORM: "device_tracker",
                        CONF_NAME: "Room Position",
                        ATTR_ENTITY_ID: "device_tracker.room_position",
                        CONF_INITIAL_VALUE: "not_home",
                        CONF_INITIAL_AVAILABILITY: True,
                        CONF_PERSISTENT: False,
                        CONF_SOURCE_ENTITIES: list(anchors),
                        CONF_POLYGONAL_ZONE: {
                            CONF_POLYGON_GEOJSON: GEOJSON,
                            CONF_POLYGON_TRACKER_RULES: {},
                            CONF_POLYGON_ESPRESENSE_ANCHORS: anchors,
                        },
                    }
                ]
            },
            ATTR_DEVICE_ATTRIBUTES: {"ESPresense": {ATTR_DEVICE_ID: "espresense"}},
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    state = hass.states.get("device_tracker.room_position")
    assert state.state == "Seoul Home"
    assert state.attributes[ATTR_LATITUDE] == pytest.approx(37.5, abs=0.00003)
    assert state.attributes[ATTR_LONGITUDE] == pytest.approx(127.0, abs=0.00003)
    assert state.attributes["espresense_sources"] == list(anchors)
    assert state.attributes["espresense_accuracy"] >= 1
    info = hass.states.get("sensor.room_position_info")
    assert info.attributes["configuration"]["source_entities"] == list(anchors)
    assert (
        info.attributes["configuration"]["polygonal_zone"][
            CONF_POLYGON_ESPRESENSE_ANCHORS
        ]
        == anchors
    )
    for index, entity_id in enumerate(anchors, start=1):
        debug = hass.states.get(f"sensor.room_position_debug{index}")
        assert debug is not None
        assert debug.attributes["source_entity_id"] == entity_id

    # A silent device must stop claiming a room without another state event.
    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    expired = hass.states.get("device_tracker.room_position")
    assert expired.state == "not_home"
    assert expired.attributes["espresense_sources"] == []
    assert expired.attributes["polygon_zone"] is None
    assert ATTR_LATITUDE not in expired.attributes

    # Recovery needs a complete fresh set of distances.
    for entity_id in anchors:
        hass.states.async_set(entity_id, "900", {"unit_of_measurement": "cm"})
    await hass.async_block_till_done()
    assert hass.states.get("device_tracker.room_position").state == "Seoul Home"


async def test_polygon_tracker_zone_sensor_and_map_image_share_one_virtual_device(hass):
    hass.states.async_set(
        "device_tracker.phone_a",
        "not_home",
        {ATTR_LATITUDE: 37.5000, ATTR_LONGITUDE: 127.0000, "gps_accuracy": 8},
    )
    hass.states.async_set(
        "device_tracker.phone_b",
        "not_home",
        {ATTR_LATITUDE: 37.5002, ATTR_LONGITUDE: 127.0002, "gps_accuracy": 12},
    )
    hass.states.async_set(
        "device_tracker.tablet",
        "not_home",
        {
            ATTR_LATITUDE: 35.1796,
            ATTR_LONGITUDE: 129.0756,
            "gps_accuracy": 5,
            "include": False,
        },
    )
    hass.states.async_set("person.family", "not_home")
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        title="family - virtual_layer",
        data={ATTR_GROUP_NAME: "family"},
        options={
            ATTR_DEVICES: {
                "Family Location": [
                    {
                        CONF_PLATFORM: "device_tracker",
                        CONF_NAME: "Family Polygon",
                        ATTR_ENTITY_ID: "device_tracker.family_polygon",
                        CONF_INITIAL_VALUE: "not_home",
                        CONF_INITIAL_AVAILABILITY: True,
                        CONF_PERSISTENT: False,
                        CONF_SOURCE_ENTITIES: [
                            "device_tracker.phone_a",
                            "device_tracker.phone_b",
                            "device_tracker.tablet",
                        ],
                        CONF_POLYGONAL_ZONE: {
                            CONF_POLYGON_GEOJSON: GEOJSON,
                            CONF_POLYGON_PERSON_ENTITY: "person.family",
                            CONF_POLYGON_TRACKER_RULES: {
                                "device_tracker.tablet": {
                                    "condition_template": (
                                        "{{ source.attributes.include | default(true) }}"
                                    ),
                                },
                            },
                        },
                    }
                ],
            },
            ATTR_DEVICE_ATTRIBUTES: {
                "Family Location": {
                    ATTR_DEVICE_ID: "family-location-device",
                    CONF_NAME: "Family Location",
                },
            },
        },
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id) is True
    await hass.async_block_till_done()

    tracker_state = hass.states.get("device_tracker.family_polygon")
    zone_state = hass.states.get("sensor.family_polygon_zone")
    map_state = hass.states.get("image.family_polygon_map")
    assert tracker_state.state == "Seoul Home"
    assert tracker_state.attributes[ATTR_LATITUDE] == pytest.approx(37.5001)
    assert tracker_state.attributes[ATTR_LONGITUDE] == pytest.approx(127.0001)
    assert tracker_state.attributes[ATTR_POLYGON_ZONE] == "Seoul Home"
    assert tracker_state.attributes[ATTR_POLYGON_PERSON] == "person.family"
    assert set(tracker_state.attributes[ATTR_POLYGON_SELECTED_MEMBERS]) == {
        "device_tracker.phone_a",
        "device_tracker.phone_b",
    }
    assert zone_state.state == "Seoul Home"
    assert map_state is not None
    assert map_state.attributes["content_type"] == "image/svg+xml"
    assert map_state.attributes["image_type"] == "polygon_map"

    registry = er.async_get(hass)
    tracker_device_id = registry.async_get("device_tracker.family_polygon").device_id
    assert (
        registry.async_get("sensor.family_polygon_zone").device_id == tracker_device_id
    )
    assert registry.async_get("image.family_polygon_map").device_id == tracker_device_id
    image_entity = hass.data["image"].get_entity("image.family_polygon_map")
    rendered = await image_entity.async_image()
    assert rendered.startswith(b"<svg ")
    assert b"Seoul Home" in rendered
    assert b'data-entity-id="device_tracker.family_polygon"' in rendered
    assert b"<circle " in rendered

    info_state = hass.states.get("sensor.family_polygon_info")
    polygon_summary = info_state.attributes["configuration"]["polygonal_zone"]
    assert polygon_summary["inline_geojson"] is True
    assert polygon_summary["person_entity_id"] == "person.family"
    assert "geojson" not in polygon_summary

    updated_options = copy.deepcopy(dict(entry.options))
    updated_options[ATTR_DEVICES]["Family Location"][0].pop(CONF_POLYGONAL_ZONE)
    hass.config_entries.async_update_entry(entry, options=updated_options)
    assert await hass.config_entries.async_reload(entry.entry_id) is True
    await hass.async_block_till_done()

    assert registry.async_get("device_tracker.family_polygon") is not None
    assert registry.async_get("sensor.family_polygon_zone") is None
    assert registry.async_get("image.family_polygon_map") is None


async def test_person_only_polygon_tracker_tracks_person_coordinates(hass):
    """A configured person is an active polygon source when no trackers exist."""
    hass.states.async_set(
        "person.alex",
        "not_home",
        {ATTR_LATITUDE: 37.5000, ATTR_LONGITUDE: 127.0000, "gps_accuracy": 15},
    )
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "person-only"},
        options={
            ATTR_DEVICES: {
                "Alex Location": [
                    {
                        CONF_PLATFORM: "device_tracker",
                        CONF_NAME: "Alex Polygon",
                        ATTR_ENTITY_ID: "device_tracker.alex_polygon",
                        CONF_INITIAL_VALUE: "not_home",
                        CONF_INITIAL_AVAILABILITY: True,
                        CONF_PERSISTENT: False,
                        CONF_POLYGONAL_ZONE: {
                            CONF_POLYGON_GEOJSON: GEOJSON,
                            CONF_POLYGON_PERSON_ENTITY: "person.alex",
                            CONF_POLYGON_TRACKER_RULES: {},
                        },
                    }
                ],
            },
            ATTR_DEVICE_ATTRIBUTES: {
                "Alex Location": {
                    ATTR_DEVICE_ID: "alex-location-device",
                    CONF_NAME: "Alex Location",
                },
            },
        },
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id) is True
    await hass.async_block_till_done()

    state = hass.states.get("device_tracker.alex_polygon")
    assert state.state == "Seoul Home"
    assert state.attributes[ATTR_LATITUDE] == 37.5
    assert state.attributes[ATTR_LONGITUDE] == 127.0
    assert state.attributes[ATTR_POLYGON_PERSON] == "person.alex"
    assert state.attributes[ATTR_POLYGON_SELECTED_SOURCE] == "person.alex"
    assert state.attributes[ATTR_POLYGON_SELECTED_MEMBERS] == ["person.alex"]

    hass.states.async_set(
        "person.alex",
        "not_home",
        {ATTR_LATITUDE: 35.1796, ATTR_LONGITUDE: 129.0756, "gps_accuracy": 10},
    )
    await hass.async_block_till_done()

    state = hass.states.get("device_tracker.alex_polygon")
    assert state.state == "not_home"
    assert state.attributes[ATTR_LATITUDE] == 35.1796
    assert state.attributes[ATTR_LONGITUDE] == 129.0756
