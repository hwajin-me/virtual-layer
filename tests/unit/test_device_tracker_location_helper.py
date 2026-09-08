"""Unit tests for the aggregate GPS location helper."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import voluptuous as vol
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_FRIENDLY_NAME,
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
)
from homeassistant.util import dt as dt_util

from custom_components.virtual_layer.const import (
    ATTR_DEVICE_ID,
    ATTR_UNIQUE_ID,
    CONF_INITIAL_AVAILABILITY,
    CONF_INITIAL_VALUE,
    CONF_LOCATION_HELPER,
    CONF_NAME,
    CONF_PERSISTENT,
    CONF_SOURCE_ENTITIES,
    CONF_PRESENCE_CLASSIFICATION,
    CONF_DAWARICH,
    CONF_DAWARICH_API_KEY,
    CONF_DAWARICH_AUTH_MODE,
    CONF_DAWARICH_HISTORY_LIMIT,
    CONF_DAWARICH_POLL_INTERVAL,
    CONF_DAWARICH_URL,
)
from custom_components.virtual_layer.device_tracker import (
    ATTR_LOCATION_MEDIAN_LATITUDE,
    ATTR_LOCATION_MEDIAN_LONGITUDE,
    ATTR_LOCATION_PRIORITY_SOURCE,
    ATTR_LOCATION_SOURCE_LAST_MOVED,
    ATTR_LOCATION_SOURCE_POSITIONS,
    ATTR_LOCATION_CLASSIFICATION,
    ATTR_LOCATION_BLE_DISTANCE,
    CONF_GPS,
    SERVICE_SCHEMA,
    VirtualDeviceTracker,
    validate_domain_options,
)
from custom_components.virtual_layer import device_tracker as tracker_platform

pytestmark = pytest.mark.unit


def _helper_tracker(hass):
    config = {
        CONF_NAME: "Family Location",
        ATTR_ENTITY_ID: "device_tracker.family_location",
        ATTR_UNIQUE_ID: "family_location",
        ATTR_DEVICE_ID: "family",
        CONF_INITIAL_VALUE: "not_home",
        CONF_INITIAL_AVAILABILITY: True,
        CONF_PERSISTENT: False,
        CONF_SOURCE_ENTITIES: [
            "device_tracker.first_phone",
            "device_tracker.second_phone",
            "device_tracker.travel_phone",
        ],
        CONF_LOCATION_HELPER: {
            "distance_threshold_meters": 300,
            "priority_window_seconds": 1800,
        },
    }
    tracker = VirtualDeviceTracker(config)
    tracker.hass = hass
    tracker.async_schedule_update_ha_state = Mock()
    tracker._create_state(config)
    return tracker


def _set_position(hass, entity_id, latitude, longitude):
    hass.states.async_set(
        entity_id,
        "not_home",
        {ATTR_LATITUDE: latitude, ATTR_LONGITUDE: longitude},
    )


def _set_zone(hass, entity_id, name, latitude, longitude):
    hass.states.async_set(
        entity_id,
        "0",
        {
            ATTR_FRIENDLY_NAME: name,
            ATTR_LATITUDE: latitude,
            ATTR_LONGITUDE: longitude,
        },
    )


async def test_move_to_coords_schedules_state_safely_from_executor(hass):
    """Synchronous HA entity services may invoke methods on a worker thread."""
    tracker = _helper_tracker(hass)
    tracker.schedule_update_ha_state = Mock()

    await hass.async_add_executor_job(
        tracker.move_to_coords,
        {ATTR_LATITUDE: 37.5, ATTR_LONGITUDE: 127.0},
        12,
    )
    await hass.async_block_till_done()

    tracker.schedule_update_ha_state.assert_called_once_with(force_refresh=False)
    assert tracker.latitude == 37.5
    assert tracker.longitude == 127.0
    assert tracker.location_accuracy == 12


def test_presence_classification_prioritizes_wifi_ble_near_and_far_states(hass):
    tracker = _helper_tracker(hass)
    tracker._config[CONF_PRESENCE_CLASSIFICATION] = True
    tracker._presence_classification = True
    tracker._source_entities.extend(
        ["binary_sensor.phone_wifi", "sensor.phone_distance"]
    )
    _set_zone(hass, "zone.home", "Home", 37.5000, 127.0000)
    _set_position(hass, "device_tracker.first_phone", 37.5000, 127.0000)
    _set_position(hass, "device_tracker.second_phone", 37.5000, 127.0000)
    _set_position(hass, "device_tracker.travel_phone", 37.5000, 127.0000)
    hass.states.async_set("binary_sensor.phone_wifi", "off")
    hass.states.async_set("sensor.phone_distance", "5")

    tracker._update_location_from_sources()
    assert tracker.state == "front_door"
    assert tracker.extra_state_attributes[ATTR_LOCATION_CLASSIFICATION] == "front_door"
    assert tracker.extra_state_attributes[ATTR_LOCATION_BLE_DISTANCE] == 5

    hass.states.async_set("sensor.phone_distance", "unknown")
    _set_position(hass, "device_tracker.first_phone", 37.5050, 127.0000)
    _set_position(hass, "device_tracker.second_phone", 37.5050, 127.0000)
    _set_position(hass, "device_tracker.travel_phone", 37.5050, 127.0000)
    tracker._update_location_from_sources()
    assert tracker.state == "near_home"

    _set_position(hass, "device_tracker.first_phone", 37.5200, 127.0000)
    _set_position(hass, "device_tracker.second_phone", 37.5200, 127.0000)
    _set_position(hass, "device_tracker.travel_phone", 37.5200, 127.0000)
    tracker._update_location_from_sources()
    assert tracker.state == "away"

    _set_position(hass, "device_tracker.first_phone", 37.6000, 127.0000)
    _set_position(hass, "device_tracker.second_phone", 37.6000, 127.0000)
    _set_position(hass, "device_tracker.travel_phone", 37.6000, 127.0000)
    tracker._update_location_from_sources()
    assert tracker.state == "far_away"

    hass.states.async_set("binary_sensor.phone_wifi", "on")
    tracker._update_location_from_sources()
    assert tracker.state == "home"


def test_dawarich_configuration_requires_safe_url_credentials_and_bounds():
    valid = {
        CONF_DAWARICH: {
            CONF_DAWARICH_URL: "https://dawarich.example",
            CONF_DAWARICH_API_KEY: "secret",
            CONF_DAWARICH_AUTH_MODE: "bearer",
            CONF_DAWARICH_POLL_INTERVAL: 60,
            CONF_DAWARICH_HISTORY_LIMIT: 10,
        }
    }
    validate_domain_options(valid)
    for key, value in (
        (CONF_DAWARICH_URL, "ftp://bad"),
        (CONF_DAWARICH_API_KEY, ""),
        (CONF_DAWARICH_POLL_INTERVAL, 1),
        (CONF_DAWARICH_HISTORY_LIMIT, 101),
    ):
        invalid = {CONF_DAWARICH: dict(valid[CONF_DAWARICH], **{key: value})}
        with pytest.raises(vol.Invalid):
            validate_domain_options(invalid)


@pytest.mark.parametrize(
    "anchors",
    [
        {
            "sensor.a": {"latitude": 37.5, "longitude": 127.0},
            "sensor.b": {"latitude": 37.5, "longitude": 127.0001},
        },
        {
            "sensor.a": {"latitude": 37.5, "longitude": 127.0},
            "sensor.b": {"latitude": 37.5, "longitude": 127.0001},
            "sensor.c": {"latitude": 37.5, "longitude": 127.0002},
        },
    ],
)
def test_polygon_rejects_insufficient_or_collinear_espresense_anchors(anchors):
    with pytest.raises(vol.Invalid):
        validate_domain_options(
            {
                "polygonal_zone": {
                    "geojson": {
                        "type": "FeatureCollection",
                        "features": [
                            {
                                "type": "Feature",
                                "properties": {"name": "Room"},
                                "geometry": {
                                    "type": "Polygon",
                                    "coordinates": [
                                        [
                                            [126.9, 37.4],
                                            [127.1, 37.4],
                                            [127.1, 37.6],
                                            [126.9, 37.4],
                                        ]
                                    ],
                                },
                            }
                        ],
                    },
                    "espresense_anchors": anchors,
                }
            }
        )


def test_dawarich_point_envelopes_and_family_member_matching():
    points = VirtualDeviceTracker._dawarich_points(
        {"data": [{"latitude": 37.5, "longitude": 127.0}]}
    )
    assert points == [{"latitude": 37.5, "longitude": 127.0}]
    assert VirtualDeviceTracker._dawarich_family_point(
        {"locations": [{"name": "Alex", "location": {"lat": 37.5, "lon": 127.0}}]},
        "alex",
    ) == {"lat": 37.5, "lon": 127.0}


@pytest.mark.asyncio
async def test_dawarich_refresh_updates_tracker_and_never_puts_api_key_in_state(
    hass, monkeypatch
):
    config = {
        CONF_NAME: "Dawarich",
        ATTR_ENTITY_ID: "device_tracker.dawarich",
        ATTR_UNIQUE_ID: "dawarich",
        ATTR_DEVICE_ID: "dawarich",
        CONF_INITIAL_VALUE: "not_home",
        CONF_INITIAL_AVAILABILITY: True,
        CONF_PERSISTENT: False,
        CONF_DAWARICH: {
            CONF_DAWARICH_URL: "https://dawarich.example",
            CONF_DAWARICH_API_KEY: "not-in-state",
            CONF_DAWARICH_AUTH_MODE: "query",
            CONF_DAWARICH_POLL_INTERVAL: 60,
            CONF_DAWARICH_HISTORY_LIMIT: 2,
        },
    }
    tracker = VirtualDeviceTracker(config)
    tracker.hass = hass
    tracker.async_schedule_update_ha_state = Mock()
    tracker._create_state(config)

    requests = []

    class Response:
        def __init__(self, payload):
            self.payload = payload
            self.status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def raise_for_status(self):
            return None

        async def json(self, **_kwargs):
            return self.payload

    class Session:
        def get(self, url, **kwargs):
            requests.append((url, kwargs))
            if url.endswith("/visits"):
                return Response({"visits": [{"place_name": "Office"}]})
            return Response(
                {
                    "points": [
                        {"lat": 37.5, "lon": 127.0, "timestamp": 9},
                        {"lat": 37.6, "lon": 127.1, "timestamp": 10, "speed": 4.2},
                    ]
                }
            )

    monkeypatch.setattr(
        tracker_platform, "async_get_clientsession", lambda _hass: Session()
    )
    await tracker._async_refresh_dawarich()

    assert (tracker.latitude, tracker.longitude) == (37.6, 127.1)
    assert tracker.extra_state_attributes["dawarich_point"]["speed"] == 4.2
    assert tracker.extra_state_attributes["dawarich_visit"]["place_name"] == "Office"
    assert requests[0][1]["params"]["api_key"] == "not-in-state"
    assert "not-in-state" not in repr(tracker.extra_state_attributes)


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [
        (float("nan"), 127),
        (37.5, float("inf")),
        (91, 127),
        (37.5, -181),
        ("invalid", 127),
        pytest.param(10**10000, 127, id="huge-latitude"),
    ],
)
def test_tracker_restore_rejects_invalid_gps_coordinates(hass, latitude, longitude):
    tracker = _helper_tracker(hass)

    tracker._restore_state(
        SimpleNamespace(
            state="not_home",
            attributes={
                "available": True,
                ATTR_LATITUDE: latitude,
                ATTR_LONGITUDE: longitude,
                "gps_accuracy": float("inf"),
            },
        ),
        tracker._config,
    )

    assert tracker.state == "not_home"
    assert tracker.latitude is None
    assert tracker.longitude is None
    assert tracker.location_accuracy == 0


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {
                ATTR_ENTITY_ID: ["device_tracker.family_location"],
                CONF_GPS: {ATTR_LATITUDE: True, ATTR_LONGITUDE: 127},
            },
            "boolean",
        ),
        (
            {
                ATTR_ENTITY_ID: ["device_tracker.family_location"],
                CONF_GPS: {ATTR_LATITUDE: 37.5, ATTR_LONGITUDE: 127},
                "gps_accuracy": True,
            },
            "boolean",
        ),
        ({ATTR_ENTITY_ID: ["device_tracker.family_location"]}, "exactly one"),
        (
            {
                ATTR_ENTITY_ID: ["device_tracker.family_location"],
                "location": "home",
                CONF_GPS: {ATTR_LATITUDE: 37.5, ATTR_LONGITUDE: 127},
            },
            "exactly one",
        ),
        (
            {
                ATTR_ENTITY_ID: ["device_tracker.family_location"],
                "location": "   ",
            },
            "must not be empty",
        ),
        (
            {
                ATTR_ENTITY_ID: ["device_tracker.family_location"],
                "location": "home",
                "gps_accuracy": 5,
            },
            "only valid with gps",
        ),
    ],
)
def test_tracker_move_schema_rejects_ambiguous_or_invalid_payloads(payload, message):
    with pytest.raises(vol.Invalid, match=message):
        SERVICE_SCHEMA(payload)


def test_tracker_restore_normalizes_valid_gps_coordinates_and_accuracy(hass):
    tracker = _helper_tracker(hass)

    tracker._restore_state(
        SimpleNamespace(
            state="not_home",
            attributes={
                "available": True,
                ATTR_LATITUDE: "37.5",
                ATTR_LONGITUDE: "127.0",
                "gps_accuracy": -10,
            },
        ),
        tracker._config,
    )

    assert tracker._location is None
    assert tracker.latitude == 37.5
    assert tracker.longitude == 127.0
    assert tracker.location_accuracy == 0


def test_tracker_restore_uses_initial_location_after_unavailable(hass):
    tracker = _helper_tracker(hass)
    tracker._config[CONF_INITIAL_VALUE] = "home"

    tracker._restore_state(
        SimpleNamespace(
            state="unavailable",
            attributes={"available": False},
        ),
        tracker._config,
    )
    tracker._attr_available = True

    assert tracker.state == "home"


@pytest.mark.parametrize(
    ("state", "attributes", "expected_location"),
    [
        (
            "not_home",
            {
                ATTR_LATITUDE: "37.5",
                ATTR_LONGITUDE: "127.0",
                "gps_accuracy": 12,
            },
            (None, 37.5, 127.0, 12),
        ),
        ("work", {}, ("work", None, None, 0)),
    ],
)
def test_tracker_restores_legacy_state_without_available_attribute(
    hass,
    state,
    attributes,
    expected_location,
):
    tracker = _helper_tracker(hass)

    tracker._restore_state(
        SimpleNamespace(state=state, attributes=attributes),
        tracker._config,
    )

    assert tracker.available is True
    assert (
        tracker._location,
        tracker.latitude,
        tracker.longitude,
        tracker.location_accuracy,
    ) == expected_location


def test_location_helper_prefers_recent_outlier_and_holds_arrived_device(hass):
    tracker = _helper_tracker(hass)
    _set_position(hass, "device_tracker.first_phone", 37.5000, 127.0000)
    _set_position(hass, "device_tracker.second_phone", 37.5000, 127.0010)
    _set_position(hass, "device_tracker.travel_phone", 37.5200, 127.0200)

    tracker._update_location_from_sources()

    assert tracker.latitude == 37.5200
    assert tracker.longitude == 127.0200
    assert tracker.extra_state_attributes[ATTR_LOCATION_PRIORITY_SOURCE] == (
        "device_tracker.travel_phone"
    )
    assert tracker.extra_state_attributes[ATTR_LOCATION_MEDIAN_LATITUDE] == 37.5000
    assert tracker.extra_state_attributes[ATTR_LOCATION_MEDIAN_LONGITUDE] == 127.0010

    # The selected phone reaches the other devices. It is still the desired
    # tracker while its own GPS report remains inside the 30 minute window.
    _set_position(hass, "device_tracker.travel_phone", 37.5000, 127.0002)
    tracker._update_location_from_sources()

    assert tracker.latitude == 37.5000
    assert tracker.longitude == 127.0002
    assert tracker.extra_state_attributes[ATTR_LOCATION_PRIORITY_SOURCE] == (
        "device_tracker.travel_phone"
    )


def test_location_helper_returns_to_median_when_priority_is_no_longer_recent(hass):
    tracker = _helper_tracker(hass)
    _set_position(hass, "device_tracker.first_phone", 37.5000, 127.0000)
    _set_position(hass, "device_tracker.second_phone", 37.5000, 127.0010)
    _set_position(hass, "device_tracker.travel_phone", 37.5200, 127.0200)
    tracker._update_location_from_sources()

    tracker._source_is_recent = lambda *_args: False
    tracker._update_location_from_sources()

    assert tracker.latitude == 37.5000
    assert tracker.longitude == 127.0010
    assert tracker.extra_state_attributes[ATTR_LOCATION_PRIORITY_SOURCE] is None


def test_location_helper_does_not_treat_non_gps_attribute_updates_as_movement(hass):
    tracker = _helper_tracker(hass)
    _set_position(hass, "device_tracker.first_phone", 37.5000, 127.0000)
    _set_position(hass, "device_tracker.second_phone", 37.5000, 127.0010)
    _set_position(hass, "device_tracker.travel_phone", 37.5200, 127.0200)
    tracker._update_location_from_sources()

    tracker._source_last_moved["device_tracker.travel_phone"] = (
        dt_util.utcnow() - timedelta(minutes=31)
    )
    hass.states.async_set(
        "device_tracker.travel_phone",
        "not_home",
        {
            ATTR_LATITUDE: 37.5200,
            ATTR_LONGITUDE: 127.0200,
            "battery_level": 90,
        },
    )
    tracker._update_location_from_sources()

    assert tracker.latitude == 37.5000
    assert tracker.longitude == 127.0010
    assert tracker.extra_state_attributes[ATTR_LOCATION_PRIORITY_SOURCE] is None


def test_location_helper_restores_source_movement_history(hass):
    tracker = _helper_tracker(hass)
    _set_position(hass, "device_tracker.first_phone", 37.5000, 127.0000)
    _set_position(hass, "device_tracker.second_phone", 37.5000, 127.0010)
    _set_position(hass, "device_tracker.travel_phone", 37.5200, 127.0200)
    tracker._update_location_from_sources()

    restored = _helper_tracker(hass)
    restored._virtual_attributes.update(
        {
            ATTR_LOCATION_PRIORITY_SOURCE: tracker.extra_state_attributes[
                ATTR_LOCATION_PRIORITY_SOURCE
            ],
            ATTR_LOCATION_SOURCE_POSITIONS: tracker.extra_state_attributes[
                ATTR_LOCATION_SOURCE_POSITIONS
            ],
            ATTR_LOCATION_SOURCE_LAST_MOVED: tracker.extra_state_attributes[
                ATTR_LOCATION_SOURCE_LAST_MOVED
            ],
        }
    )
    restored._virtual_attributes[ATTR_LOCATION_SOURCE_POSITIONS][
        "device_tracker.first_phone"
    ] = [True, False]
    restored._virtual_attributes[ATTR_LOCATION_SOURCE_LAST_MOVED][
        "device_tracker.first_phone"
    ] = True
    restored._restore_location_helper_attributes()
    assert "device_tracker.first_phone" not in restored._source_positions
    assert "device_tracker.first_phone" not in restored._source_last_moved
    restored._source_last_moved["device_tracker.travel_phone"] = (
        dt_util.utcnow() - timedelta(minutes=31)
    )
    hass.states.async_set(
        "device_tracker.travel_phone",
        "not_home",
        {
            ATTR_LATITUDE: 37.5200,
            ATTR_LONGITUDE: 127.0200,
            "battery_level": 90,
        },
    )

    restored._update_location_from_sources()

    assert restored.latitude == 37.5000
    assert restored.longitude == 127.0010
    assert restored.extra_state_attributes[ATTR_LOCATION_PRIORITY_SOURCE] is None


def test_location_helper_excludes_its_own_entity_from_median(hass):
    tracker = _helper_tracker(hass)
    tracker._source_entities.append(tracker.entity_id)
    _set_position(hass, "device_tracker.first_phone", 37.5000, 127.0000)
    _set_position(hass, "device_tracker.second_phone", 37.5000, 127.0010)
    _set_position(hass, "device_tracker.travel_phone", 99, 99)
    _set_position(hass, tracker.entity_id, 0, 0)

    tracker._update_location_from_sources()

    assert tracker.latitude == 37.5000
    assert tracker.longitude == 127.0005


def test_location_helper_ignores_invalid_coordinates_and_uses_known_median(hass):
    tracker = _helper_tracker(hass)
    _set_position(hass, "device_tracker.first_phone", 37.5000, 127.0000)
    _set_position(hass, "device_tracker.second_phone", 37.5000, 127.0010)
    _set_position(hass, "device_tracker.travel_phone", 99, 127.0200)

    tracker._update_location_from_sources()

    assert tracker.latitude == 37.5000
    assert tracker.longitude == 127.0005
    assert tracker.extra_state_attributes[ATTR_LOCATION_PRIORITY_SOURCE] is None


def test_location_helper_uses_named_zone_coordinates_for_sources_without_gps(hass):
    tracker = _helper_tracker(hass)
    _set_zone(hass, "zone.home", "Home", 37.5000, 127.0000)
    hass.states.async_set("device_tracker.first_phone", "home")
    hass.states.async_set("device_tracker.second_phone", "home")
    _set_position(hass, "device_tracker.travel_phone", 37.5200, 127.0200)

    tracker._update_location_from_sources()

    assert tracker.extra_state_attributes[ATTR_LOCATION_MEDIAN_LATITUDE] == 37.5000
    assert tracker.extra_state_attributes[ATTR_LOCATION_MEDIAN_LONGITUDE] == 127.0000
    assert tracker.latitude == 37.5200
    assert tracker.longitude == 127.0200
    assert tracker.extra_state_attributes[ATTR_LOCATION_PRIORITY_SOURCE] == (
        "device_tracker.travel_phone"
    )


def test_location_helper_matches_zone_by_friendly_name_when_entity_id_differs(hass):
    tracker = _helper_tracker(hass)
    _set_zone(hass, "zone.stat_zone_1", "StatZon1", 37.7000, 127.2000)
    hass.states.async_set("device_tracker.first_phone", "StatZon1")
    hass.states.async_set("device_tracker.second_phone", "StatZon1")
    hass.states.async_set("device_tracker.travel_phone", "StatZon1")

    tracker._update_location_from_sources()

    assert tracker.latitude == 37.7000
    assert tracker.longitude == 127.2000
    assert tracker.extra_state_attributes[ATTR_LOCATION_MEDIAN_LATITUDE] == 37.7000
    assert tracker.extra_state_attributes[ATTR_LOCATION_MEDIAN_LONGITUDE] == 127.2000
    assert tracker.extra_state_attributes[ATTR_LOCATION_PRIORITY_SOURCE] is None


def test_location_helper_clears_stale_metadata_when_no_gps_is_available(hass):
    tracker = _helper_tracker(hass)
    _set_position(hass, "device_tracker.first_phone", 37.5000, 127.0000)
    _set_position(hass, "device_tracker.second_phone", 37.5000, 127.0010)
    _set_position(hass, "device_tracker.travel_phone", 37.5200, 127.0200)
    tracker._update_location_from_sources()

    for entity_id in tracker._source_entities:
        hass.states.async_set(entity_id, "home")
    tracker._update_location_from_sources()

    assert tracker.state == "home"
    assert tracker.extra_state_attributes[ATTR_LOCATION_MEDIAN_LATITUDE] is None
    assert tracker.extra_state_attributes[ATTR_LOCATION_MEDIAN_LONGITUDE] is None
    assert tracker.extra_state_attributes[ATTR_LOCATION_PRIORITY_SOURCE] is None


def test_location_distance_handles_antipodal_coordinates():
    distance = VirtualDeviceTracker._distance_meters((90, 0), (-90, 180))

    assert 20_000_000 < distance < 20_100_000


def test_location_helper_median_handles_the_international_date_line(hass):
    tracker = _helper_tracker(hass)
    _set_position(hass, "device_tracker.first_phone", 10.0, 179.8)
    _set_position(hass, "device_tracker.second_phone", 10.0, -179.9)
    _set_position(hass, "device_tracker.travel_phone", 10.0, 179.9)

    tracker._source_is_recent = lambda *_args: False
    tracker._update_location_from_sources()

    assert tracker.latitude == 10.0
    assert abs(tracker.longitude) == pytest.approx(179.9)
    assert abs(
        tracker.extra_state_attributes[ATTR_LOCATION_MEDIAN_LONGITUDE]
    ) == pytest.approx(179.9)
