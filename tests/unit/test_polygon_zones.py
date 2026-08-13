"""Unit tests for polygon zones and multi-tracker aggregation."""

import json
import re
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock

import pytest
import voluptuous as vol
from homeassistant.const import ATTR_ENTITY_ID, ATTR_LATITUDE, ATTR_LONGITUDE

from custom_components.virtual_layer.const import (
    ATTR_DEVICE_ID,
    ATTR_UNIQUE_ID,
    CONF_INITIAL_AVAILABILITY,
    CONF_INITIAL_VALUE,
    CONF_NAME,
    CONF_PERSISTENT,
    CONF_POLYGON_GEOJSON,
    CONF_POLYGON_PERSON_ENTITY,
    CONF_POLYGON_TRACKER_RULES,
    CONF_POLYGONAL_ZONE,
    CONF_SOURCE_ENTITIES,
)
from custom_components.virtual_layer.device_tracker import (
    ATTR_POLYGON_SELECTED_MEMBERS,
    ATTR_POLYGON_SELECTED_SOURCE,
    ATTR_POLYGON_SELECTION_REASON,
    ATTR_POLYGON_ZONE,
    VirtualDeviceTracker,
    validate_domain_options,
)
from custom_components.virtual_layer.polygon import (
    find_polygon_zone,
    load_polygon_zones,
    parse_geojson_zones,
    render_polygon_map_svg,
    select_tracker_position,
)

pytestmark = pytest.mark.unit


def _feature(name, coordinates, *, geometry_type="Polygon", priority=0):
    return {
        "type": "Feature",
        "properties": {"name": name, "priority": priority},
        "geometry": {"type": geometry_type, "coordinates": coordinates},
    }


SEOUL_OUTER = [
    [126.8, 37.4],
    [127.2, 37.4],
    [127.2, 37.8],
    [126.8, 37.8],
    [126.8, 37.4],
]
SEOUL_HOLE = [
    [126.95, 37.55],
    [127.05, 37.55],
    [127.05, 37.65],
    [126.95, 37.65],
    [126.95, 37.55],
]
GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        _feature("Seoul", [SEOUL_OUTER, SEOUL_HOLE], priority=10),
        _feature(
            "Office",
            [[
                [126.9, 37.45],
                [127.1, 37.45],
                [127.1, 37.75],
                [126.9, 37.75],
                [126.9, 37.45],
            ]],
            priority=1,
        ),
        _feature(
            "Remote",
            [
                [[[128.0, 35.0], [128.1, 35.0], [128.1, 35.1], [128.0, 35.0]]],
                [[[129.0, 36.0], [129.1, 36.0], [129.1, 36.1], [129.0, 36.0]]],
            ],
            geometry_type="MultiPolygon",
        ),
    ],
}


def _sample(entity_id, latitude, longitude, minutes=0, **rule):
    return {
        "entity_id": entity_id,
        "latitude": latitude,
        "longitude": longitude,
        "gps_accuracy": rule.pop("gps_accuracy", 5),
        "last_updated": datetime(2026, 8, 5, 6, minutes, tzinfo=timezone.utc),
        **rule,
    }


def test_geojson_supports_holes_multipolygons_priority_and_accuracy():
    zones = parse_geojson_zones(GEOJSON)

    assert find_polygon_zone(37.50, 127.00, 0, zones)["name"] == "Office"
    assert find_polygon_zone(37.60, 127.00, 0, zones[:1]) is None
    assert find_polygon_zone(35.05, 128.05, 0, zones)["name"] == "Remote"
    assert find_polygon_zone(37.50, 126.7999, 20, zones)["name"] == "Seoul"
    assert find_polygon_zone(34.0, 128.0, 0, zones) is None


def test_polygon_map_svg_renders_multipolygon_and_labels():
    zones = parse_geojson_zones(GEOJSON)

    svg = render_polygon_map_svg(zones)

    assert svg.startswith("<svg ")
    assert 'fill-rule="evenodd"' in svg
    assert "Seoul" in svg
    assert "Office" in svg
    assert "Remote" in svg
    assert svg.count("<path") >= 4


def test_polygon_map_svg_aligns_date_line_features_and_renders_safe_markers():
    zones = parse_geojson_zones({
        "type": "FeatureCollection",
        "features": [
            _feature("East", [[
                [179.0, 0.0], [180.0, 0.0], [180.0, 1.0], [179.0, 0.0],
            ]]),
            _feature("West", [[
                [-180.0, 0.0], [-179.0, 0.0], [-179.0, 1.0], [-180.0, 0.0],
            ]]),
        ],
    })

    svg = render_polygon_map_svg(zones, markers=[{
        "entity_id": 'device_tracker.phone"unsafe',
        "label": "Phone <A>",
        "latitude": 0.5,
        "longitude": -179.5,
    }])

    polygon_paths = re.findall(r'<path d="([^"]+)" fill=', svg)
    path_widths = []
    for path in polygon_paths:
        coordinates = [float(value) for value in re.findall(r"-?\d+(?:\.\d+)?", path)]
        x_values = coordinates[::2]
        path_widths.append(max(x_values) - min(x_values))
    assert min(path_widths) > 100
    assert '<circle cx=' in svg
    assert 'data-entity-id="device_tracker.phone&quot;unsafe"' in svg
    assert "Phone &lt;A&gt;" in svg


@pytest.mark.parametrize(("width", "height"), [(0, 480), (720, -1), (1.5, 480)])
def test_polygon_map_svg_rejects_invalid_dimensions(width, height):
    with pytest.raises(ValueError, match="positive integers"):
        render_polygon_map_svg(parse_geojson_zones(GEOJSON), width, height)


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        ({}, None),
        ({"type": "FeatureCollection", "features": "bad"}, None),
        ({"type": "FeatureCollection", "features": []}, None),
        (_feature("Line", [], geometry_type="LineString"), None),
        (_feature("", [SEOUL_OUTER]), None),
        (_feature("Bad", [[[181, 37], [127, 37], [127, 38]]]), None),
        (
            _feature(
                "Not A Number",
                [[[float("nan"), 37.5], [127.1, 37.5], [127.1, 37.6]]],
            ),
            "valid GPS range",
        ),
        (
            _feature(
                "Infinite",
                [[[127.0, float("inf")], [127.1, 37.5], [127.1, 37.6]]],
            ),
            "valid GPS range",
        ),
    ],
)
def test_geojson_rejects_invalid_features(payload, match):
    with pytest.raises(ValueError, match=match):
        parse_geojson_zones(payload)


def test_geojson_rejects_holes_outside_their_polygon():
    invalid = _feature("Invalid hole", [
        SEOUL_OUTER,
        [[128.0, 35.0], [128.1, 35.0], [128.1, 35.1], [128.0, 35.0]],
    ])

    with pytest.raises(ValueError, match="hole must be inside"):
        parse_geojson_zones(invalid)


def test_majority_uses_weight_then_latest_update_to_break_ties():
    samples = [
        _sample("device_tracker.a", 37.5000, 127.0000, 1),
        _sample("device_tracker.b", 37.5001, 127.0001, 2),
        _sample("device_tracker.c", 35.0000, 129.0000, 8),
        _sample("device_tracker.d", 35.0001, 129.0001, 9),
    ]

    selected = select_tracker_position(samples, "majority", 300)

    assert set(selected["members"]) == {"device_tracker.c", "device_tracker.d"}
    assert selected["selected_source"] == "device_tracker.d"
    assert selected["reason"] == "majority"

    samples[0]["weight"] = 3
    selected = select_tracker_position(samples, "majority", 300)
    assert set(selected["members"]) == {"device_tracker.a", "device_tracker.b"}


def test_dominant_priority_latest_and_median_strategies():
    samples = [
        _sample("device_tracker.a", 37.0, 127.0, 1, priority=20),
        _sample("device_tracker.b", 38.0, 128.0, 2, priority=1),
        _sample("device_tracker.c", 39.0, 129.0, 3, dominant=True),
    ]

    assert select_tracker_position(samples, "priority")["selected_source"] == (
        "device_tracker.c"
    )
    samples[2]["dominant"] = False
    assert select_tracker_position(samples, "priority")["selected_source"] == (
        "device_tracker.b"
    )
    assert select_tracker_position(samples, "latest")["selected_source"] == (
        "device_tracker.c"
    )
    median = select_tracker_position(samples, "median")
    assert (median["latitude"], median["longitude"]) == (38.0, 128.0)


def test_tracker_median_handles_the_international_date_line():
    selected = select_tracker_position([
        _sample("device_tracker.east", 10.0, 179.9, 1),
        _sample("device_tracker.west", 10.0, -179.9, 2),
    ], "median")

    assert abs(selected["longitude"]) == pytest.approx(180.0)


def test_polygon_crossing_the_date_line_is_detected():
    zones = parse_geojson_zones({
        "type": "FeatureCollection",
        "features": [_feature("Date Line", [[
            [179.5, 9.0],
            [-179.5, 9.0],
            [-179.5, 11.0],
            [179.5, 11.0],
            [179.5, 9.0],
        ]])],
    })

    assert find_polygon_zone(10.0, 179.9, 0, zones)["name"] == "Date Line"
    assert find_polygon_zone(10.0, -179.9, 0, zones)["name"] == "Date Line"
    assert find_polygon_zone(10.0, 0.0, 0, zones) is None


def test_polygon_domain_options_validate_rules_and_survive_invalid_stored_data():
    valid = {
        CONF_POLYGON_GEOJSON: GEOJSON,
        CONF_POLYGON_TRACKER_RULES: {
            "device_tracker.phone": {
                "dominant": True,
                "weight": 2,
                "max_age_seconds": 1800,
                "condition_template": "{{ source.state != 'unavailable' }}",
            },
        },
    }
    validate_domain_options({CONF_POLYGONAL_ZONE: valid})

    with pytest.raises(vol.Invalid):
        validate_domain_options({
            CONF_POLYGONAL_ZONE: {
                CONF_POLYGON_GEOJSON: GEOJSON,
                CONF_POLYGON_TRACKER_RULES: {
                    "device_tracker.phone": {"weight": 0},
                },
            },
        })

    with pytest.raises(vol.Invalid):
        validate_domain_options({
            CONF_POLYGONAL_ZONE: {
                CONF_POLYGON_GEOJSON: GEOJSON,
                "distance_threshold_meters": float("nan"),
            },
        })

    assert VirtualDeviceTracker._normalize_polygon_config({"broken": True}) is None
    assert VirtualDeviceTracker._normalize_polygon_config(valid) is not None
    assert VirtualDeviceTracker._normalize_location_helper({
        "distance_threshold_meters": float("nan"),
    }) is None


def test_virtual_tracker_applies_jinja_rules_and_person_metadata(hass):
    config = {
        CONF_NAME: "Family Polygon",
        ATTR_ENTITY_ID: "device_tracker.family_polygon",
        ATTR_UNIQUE_ID: "family_polygon",
        ATTR_DEVICE_ID: "family",
        CONF_INITIAL_VALUE: "not_home",
        CONF_INITIAL_AVAILABILITY: True,
        CONF_PERSISTENT: False,
        CONF_SOURCE_ENTITIES: [
            "device_tracker.phone_a",
            "device_tracker.phone_b",
            "device_tracker.bad_gps",
            "device_tracker.non_finite_gps",
        ],
        CONF_POLYGONAL_ZONE: {
            CONF_POLYGON_GEOJSON: GEOJSON,
            CONF_POLYGON_PERSON_ENTITY: "person.family",
            CONF_POLYGON_TRACKER_RULES: {
                "device_tracker.bad_gps": {
                    "condition_template": "{{ source.attributes.accepted }}",
                },
            },
        },
    }
    tracker = VirtualDeviceTracker(config)
    tracker.hass = hass
    tracker.async_schedule_update_ha_state = Mock()
    tracker._create_state(config)
    tracker._polygon_zones = parse_geojson_zones(GEOJSON)
    hass.states.async_set("person.family", "not_home")
    for entity_id, latitude, longitude, accepted in (
        ("device_tracker.phone_a", 37.50, 127.00, True),
        ("device_tracker.phone_b", 37.501, 127.001, True),
        ("device_tracker.bad_gps", 35.05, 128.05, False),
    ):
        hass.states.async_set(
            entity_id,
            "not_home",
            {
                ATTR_LATITUDE: latitude,
                ATTR_LONGITUDE: longitude,
                "gps_accuracy": 5,
                "accepted": accepted,
            },
        )
    hass.states.async_set(
        "device_tracker.non_finite_gps",
        "not_home",
        {
            ATTR_LATITUDE: float("nan"),
            ATTR_LONGITUDE: 127.0,
            "gps_accuracy": 5,
        },
    )

    tracker._update_polygon_from_sources()

    assert tracker.state == "Office"
    assert tracker.latitude == pytest.approx(37.5005)
    assert tracker.extra_state_attributes[ATTR_POLYGON_ZONE] == "Office"
    assert tracker.extra_state_attributes[ATTR_POLYGON_SELECTION_REASON] == "majority"
    assert tracker.extra_state_attributes[ATTR_POLYGON_SELECTED_SOURCE] == (
        "device_tracker.phone_b"
    )
    assert set(tracker.extra_state_attributes[ATTR_POLYGON_SELECTED_MEMBERS]) == {
        "device_tracker.phone_a",
        "device_tracker.phone_b",
    }


def test_person_is_used_as_position_when_no_trackers_are_configured(hass):
    config = {
        CONF_NAME: "Solo Polygon",
        ATTR_ENTITY_ID: "device_tracker.solo_polygon",
        ATTR_UNIQUE_ID: "solo_polygon",
        ATTR_DEVICE_ID: "solo",
        CONF_INITIAL_VALUE: "not_home",
        CONF_INITIAL_AVAILABILITY: True,
        CONF_PERSISTENT: False,
        CONF_POLYGONAL_ZONE: {
            CONF_POLYGON_GEOJSON: GEOJSON,
            CONF_POLYGON_PERSON_ENTITY: "person.solo",
        },
    }
    tracker = VirtualDeviceTracker(config)
    tracker.hass = hass
    tracker.async_schedule_update_ha_state = Mock()
    tracker._create_state(config)
    tracker._polygon_zones = parse_geojson_zones(GEOJSON)
    hass.states.async_set(
        "person.solo",
        "not_home",
        {ATTR_LATITUDE: 37.5, ATTR_LONGITUDE: 127.0},
    )

    tracker._update_polygon_from_sources()

    assert tracker.state == "Office"
    assert tracker.extra_state_attributes[ATTR_POLYGON_SELECTED_SOURCE] == "person.solo"


def test_unavailable_tracker_coordinates_are_not_used(hass):
    config = {
        CONF_NAME: "Unavailable Polygon",
        ATTR_ENTITY_ID: "device_tracker.unavailable_polygon",
        ATTR_UNIQUE_ID: "unavailable_polygon",
        ATTR_DEVICE_ID: "unavailable",
        CONF_INITIAL_VALUE: "not_home",
        CONF_INITIAL_AVAILABILITY: True,
        CONF_PERSISTENT: False,
        CONF_SOURCE_ENTITIES: ["device_tracker.offline_phone"],
        CONF_POLYGONAL_ZONE: {CONF_POLYGON_GEOJSON: GEOJSON},
    }
    tracker = VirtualDeviceTracker(config)
    tracker.hass = hass
    tracker.async_schedule_update_ha_state = Mock()
    tracker._create_state(config)
    tracker._polygon_zones = parse_geojson_zones(GEOJSON)
    hass.states.async_set(
        "device_tracker.offline_phone",
        "unavailable",
        {ATTR_LATITUDE: 37.5, ATTR_LONGITUDE: 127.0},
    )

    tracker._update_polygon_from_sources()

    assert tracker.state == "not_home"
    assert tracker.latitude is None


@pytest.mark.asyncio
async def test_polygon_zones_load_from_a_local_geojson_file(hass, tmp_path, monkeypatch):
    geojson_file = tmp_path / "family-zones.geojson"
    geojson_file.write_text(json.dumps(GEOJSON), encoding="utf-8")
    resolve = AsyncMock(return_value=str(geojson_file))
    monkeypatch.setattr(
        "custom_components.virtual_layer.polygon._local_geojson_path",
        resolve,
    )

    zones = await load_polygon_zones(hass, files=["family-zones.geojson"])

    assert [zone["name"] for zone in zones] == ["Seoul", "Office", "Remote"]
    resolve.assert_awaited_once_with(hass, "family-zones.geojson")


@pytest.mark.asyncio
async def test_one_broken_geojson_file_does_not_discard_valid_zones(
    hass,
    tmp_path,
    monkeypatch,
):
    geojson_file = tmp_path / "valid.geojson"
    geojson_file.write_text(json.dumps(GEOJSON), encoding="utf-8")
    missing_file = tmp_path / "missing.geojson"
    resolve = AsyncMock(side_effect=[str(geojson_file), str(missing_file)])
    monkeypatch.setattr(
        "custom_components.virtual_layer.polygon._local_geojson_path",
        resolve,
    )

    zones, errors = await load_polygon_zones(
        hass,
        files=["valid.geojson", "missing.geojson"],
        return_errors=True,
    )

    assert [zone["name"] for zone in zones] == ["Seoul", "Office", "Remote"]
    assert len(errors) == 1
    assert errors[0].startswith("missing.geojson:")


@pytest.mark.asyncio
async def test_broken_inline_geojson_does_not_discard_valid_file_zones(
    hass,
    tmp_path,
    monkeypatch,
):
    geojson_file = tmp_path / "valid.geojson"
    geojson_file.write_text(json.dumps(GEOJSON), encoding="utf-8")
    monkeypatch.setattr(
        "custom_components.virtual_layer.polygon._local_geojson_path",
        AsyncMock(return_value=str(geojson_file)),
    )

    zones, errors = await load_polygon_zones(
        hass,
        inline_geojson="{bad-json",
        files=["valid.geojson"],
        return_errors=True,
    )

    assert [zone["name"] for zone in zones] == ["Seoul", "Office", "Remote"]
    assert len(errors) == 1
    assert errors[0].startswith("inline GeoJSON:")


@pytest.mark.asyncio
async def test_oversized_geojson_file_is_rejected_without_loading_it(
    hass,
    tmp_path,
    monkeypatch,
):
    geojson_file = tmp_path / "oversized.geojson"
    geojson_file.write_bytes(b"{" + b" " * 32 + b"}")
    monkeypatch.setattr(
        "custom_components.virtual_layer.polygon.MAX_GEOJSON_BYTES",
        16,
    )
    monkeypatch.setattr(
        "custom_components.virtual_layer.polygon._local_geojson_path",
        AsyncMock(return_value=str(geojson_file)),
    )

    zones, errors = await load_polygon_zones(
        hass,
        files=["oversized.geojson"],
        return_errors=True,
    )

    assert zones == []
    assert len(errors) == 1
    assert "too large" in errors[0]


@pytest.mark.asyncio
async def test_remote_geojson_reads_all_network_chunks(hass, monkeypatch):
    document = json.dumps(GEOJSON).encode()

    class ChunkedContent:
        async def iter_chunked(self, _chunk_size):
            yield document[:17]
            yield document[17:103]
            yield document[103:]

    class Response:
        content_length = None
        content = ChunkedContent()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def raise_for_status(self):
            return None

    session = Mock()
    session.get.return_value = Response()
    monkeypatch.setattr(
        "custom_components.virtual_layer.polygon.async_get_clientsession",
        Mock(return_value=session),
    )

    zones = await load_polygon_zones(
        hass,
        files=["https://example.test/family.geojson"],
    )

    assert [zone["name"] for zone in zones] == ["Seoul", "Office", "Remote"]


def test_geojson_complexity_budget_rejects_excessive_coordinates(monkeypatch):
    monkeypatch.setattr(
        "custom_components.virtual_layer.polygon.MAX_GEOJSON_POINTS",
        3,
    )

    with pytest.raises(ValueError, match="too many coordinate points"):
        parse_geojson_zones(GEOJSON)


@pytest.mark.asyncio
async def test_polygon_file_reload_keeps_last_working_zones_on_transient_failure(
    hass,
    monkeypatch,
):
    config = {
        CONF_NAME: "Reloading Polygon",
        ATTR_ENTITY_ID: "device_tracker.reloading_polygon",
        ATTR_UNIQUE_ID: "reloading_polygon",
        ATTR_DEVICE_ID: "reloading",
        CONF_INITIAL_VALUE: "not_home",
        CONF_INITIAL_AVAILABILITY: True,
        CONF_PERSISTENT: False,
        CONF_POLYGONAL_ZONE: {
            "files": ["zones.geojson"],
        },
    }
    tracker = VirtualDeviceTracker(config)
    tracker.hass = hass
    tracker.async_schedule_update_ha_state = Mock()
    tracker._create_state(config)
    original_zones = parse_geojson_zones(GEOJSON)
    tracker._polygon_zones = original_zones
    loader = AsyncMock(return_value=([], ["zones.geojson: offline"]))
    monkeypatch.setattr(
        "custom_components.virtual_layer.device_tracker.load_polygon_zones",
        loader,
    )

    await tracker._async_reload_polygon_zones()

    assert tracker._polygon_zones is original_zones
    assert tracker._virtual_attributes["polygon_load_error"] == (
        "zones.geojson: offline"
    )

    updated_zones = parse_geojson_zones({
        "type": "FeatureCollection",
        "features": [_feature("Updated", [SEOUL_OUTER])],
    })
    loader.return_value = (updated_zones, ["secondary.geojson: offline"])
    await tracker._async_reload_polygon_zones()

    assert tracker._polygon_zones is original_zones

    loader.return_value = (updated_zones, [])
    await tracker._async_reload_polygon_zones()

    assert tracker._polygon_zones == updated_zones
    assert tracker._virtual_attributes["polygon_load_error"] is None
