"""Integration tests for polygon virtual device trackers."""

import copy

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
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.virtual_layer.config_flow import (
    ACTION_ADD_ENTITY,
    CONF_ACTION,
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
    CONF_SOURCE_ENTITIES,
)
from custom_components.virtual_layer.device_tracker import (
    ATTR_POLYGON_PERSON,
    ATTR_POLYGON_SELECTED_MEMBERS,
    ATTR_POLYGON_ZONE,
)

pytestmark = pytest.mark.integration

GEOJSON = {
    "type": "FeatureCollection",
    "features": [{
        "type": "Feature",
        "properties": {"name": "Seoul Home", "priority": 1},
        "geometry": {
            "type": "Polygon",
            "coordinates": [[
                [126.9, 37.4],
                [127.1, 37.4],
                [127.1, 37.6],
                [126.9, 37.6],
                [126.9, 37.4],
            ]],
        },
    }],
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
    assert CONF_POLYGON_GEOJSON_JSON in polygon_defaults
    assert polygon_defaults[CONF_POLYGON_STRATEGY_INPUT] == "majority"


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
                "Family Location": [{
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
                }],
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
    assert registry.async_get("sensor.family_polygon_zone").device_id == tracker_device_id
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
