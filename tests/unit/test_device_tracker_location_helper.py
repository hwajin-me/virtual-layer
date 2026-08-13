"""Unit tests for the aggregate GPS location helper."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
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
)
from custom_components.virtual_layer.device_tracker import (
    ATTR_LOCATION_MEDIAN_LATITUDE,
    ATTR_LOCATION_MEDIAN_LONGITUDE,
    ATTR_LOCATION_PRIORITY_SOURCE,
    ATTR_LOCATION_SOURCE_LAST_MOVED,
    ATTR_LOCATION_SOURCE_POSITIONS,
    VirtualDeviceTracker,
)

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
    restored._virtual_attributes.update({
        ATTR_LOCATION_PRIORITY_SOURCE: tracker.extra_state_attributes[
            ATTR_LOCATION_PRIORITY_SOURCE
        ],
        ATTR_LOCATION_SOURCE_POSITIONS: tracker.extra_state_attributes[
            ATTR_LOCATION_SOURCE_POSITIONS
        ],
        ATTR_LOCATION_SOURCE_LAST_MOVED: tracker.extra_state_attributes[
            ATTR_LOCATION_SOURCE_LAST_MOVED
        ],
    })
    restored._restore_location_helper_attributes()
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
