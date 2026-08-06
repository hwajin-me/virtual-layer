"""Config Flow tests for polygon virtual device trackers."""

import json

import pytest
from homeassistant.const import CONF_PLATFORM

from custom_components.virtual_layer.config_flow import (
    CONF_DEVICE_NAME,
    CONF_ENTITY_NAME,
    CONF_POLYGON_AWAY_STATE_INPUT,
    CONF_POLYGON_DISTANCE_INPUT,
    CONF_POLYGON_FILES_TEXT,
    CONF_POLYGON_GEOJSON_JSON,
    CONF_POLYGON_PERSON,
    CONF_POLYGON_STRATEGY_INPUT,
    CONF_POLYGON_TRACKER_RULES_JSON,
    CONF_SOURCE_ENTITIES_TEXT,
    InvalidEntityReference,
    InvalidJson,
    _build_entity_config,
    _entity_form_defaults,
    _entity_schema,
)
from custom_components.virtual_layer.const import (
    ATTR_DEVICE_ID,
    ATTR_ENTITY_ID,
    CONF_INITIAL_VALUE,
    CONF_NAME,
    CONF_POLYGON_AWAY_STATE,
    CONF_POLYGON_DISTANCE_METERS,
    CONF_POLYGON_FILES,
    CONF_POLYGON_GEOJSON,
    CONF_POLYGON_PERSON_ENTITY,
    CONF_POLYGON_STRATEGY,
    CONF_POLYGON_TRACKER_RULES,
    CONF_POLYGONAL_ZONE,
    CONF_SOURCE_ENTITIES,
)
from custom_components.virtual_layer.device_tracker import (
    DEVICE_TRACKER_SCHEMA,
    validate_domain_options,
)

pytestmark = pytest.mark.unit

GEOJSON = {
    "type": "FeatureCollection",
    "features": [{
        "type": "Feature",
        "properties": {"name": "Home"},
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


def test_config_flow_builds_and_restores_polygon_fields():
    form = _entity_schema({
        CONF_PLATFORM: "device_tracker",
        CONF_ENTITY_NAME: "Family Location",
    })({})
    form.update({
        CONF_DEVICE_NAME: "Family",
        ATTR_ENTITY_ID: "device_tracker.family_location",
        CONF_SOURCE_ENTITIES_TEXT: (
            "device_tracker.phone_a\ndevice_tracker.phone_b"
        ),
        CONF_POLYGON_GEOJSON_JSON: json.dumps(GEOJSON),
        CONF_POLYGON_FILES_TEXT: "zones/work.geojson\nhttps://example.test/trip.geojson",
        CONF_POLYGON_PERSON: "person.family",
        CONF_POLYGON_STRATEGY_INPUT: "majority",
        CONF_POLYGON_DISTANCE_INPUT: 250,
        CONF_POLYGON_TRACKER_RULES_JSON: json.dumps({
            "device_tracker.phone_a": {"dominant": True, "priority": 1},
        }),
        CONF_POLYGON_AWAY_STATE_INPUT: "away",
    })

    device_name, entity = _build_entity_config(
        form,
        DEVICE_TRACKER_SCHEMA,
        validate_domain_options,
    )

    assert device_name == "Family"
    assert entity[CONF_SOURCE_ENTITIES] == [
        "device_tracker.phone_a",
        "device_tracker.phone_b",
    ]
    assert entity[CONF_POLYGONAL_ZONE] == {
        CONF_POLYGON_GEOJSON: GEOJSON,
        CONF_POLYGON_FILES: [
            "zones/work.geojson",
            "https://example.test/trip.geojson",
        ],
        CONF_POLYGON_PERSON_ENTITY: "person.family",
        CONF_POLYGON_STRATEGY: "majority",
        CONF_POLYGON_DISTANCE_METERS: 250,
        CONF_POLYGON_TRACKER_RULES: {
            "device_tracker.phone_a": {"dominant": True, "priority": 1},
        },
        CONF_POLYGON_AWAY_STATE: "away",
    }

    defaults = _entity_form_defaults(
        "Family",
        {
            **entity,
            ATTR_DEVICE_ID: "family-device",
            CONF_NAME: "Family Location",
            CONF_INITIAL_VALUE: "not_home",
        },
    )
    assert json.loads(defaults[CONF_POLYGON_GEOJSON_JSON]) == GEOJSON
    assert defaults[CONF_POLYGON_FILES_TEXT].splitlines() == [
        "zones/work.geojson",
        "https://example.test/trip.geojson",
    ]
    assert defaults[CONF_POLYGON_PERSON] == "person.family"
    assert defaults[CONF_POLYGON_DISTANCE_INPUT] == 250
    assert json.loads(defaults[CONF_POLYGON_TRACKER_RULES_JSON]) == {
        "device_tracker.phone_a": {"dominant": True, "priority": 1},
    }


def test_polygon_fields_are_only_shown_for_device_trackers():
    sensor_fields = {
        str(getattr(key, "schema", key))
        for key in _entity_schema({CONF_PLATFORM: "sensor"}).schema
    }
    tracker_fields = {
        str(getattr(key, "schema", key))
        for key in _entity_schema({CONF_PLATFORM: "device_tracker"}).schema
    }

    assert CONF_POLYGON_GEOJSON_JSON not in sensor_fields
    assert CONF_POLYGON_GEOJSON_JSON in tracker_fields
    assert CONF_POLYGON_PERSON in tracker_fields


def test_invalid_polygon_geometry_is_reported_on_the_geojson_field():
    form = _entity_schema({CONF_PLATFORM: "device_tracker"})({})
    form.update({
        CONF_POLYGON_GEOJSON_JSON: json.dumps({
            "type": "FeatureCollection",
            "features": [],
        }),
    })

    with pytest.raises(InvalidJson) as err:
        _build_entity_config(form, DEVICE_TRACKER_SCHEMA, validate_domain_options)

    assert err.value.field_name == CONF_POLYGON_GEOJSON_JSON


def test_polygon_configuration_requires_a_tracker_or_person():
    form = _entity_schema({CONF_PLATFORM: "device_tracker"})({})
    form.update({
        CONF_POLYGON_GEOJSON_JSON: json.dumps(GEOJSON),
    })

    with pytest.raises(InvalidEntityReference) as err:
        _build_entity_config(form, DEVICE_TRACKER_SCHEMA, validate_domain_options)

    assert err.value.field_name == CONF_SOURCE_ENTITIES_TEXT
