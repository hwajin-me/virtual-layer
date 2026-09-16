"""Partial-device trips, GPS quality, and carried-device handoffs."""

from copy import deepcopy
from datetime import timedelta

import pytest

from test_device_tracker_location_helper import _helper_tracker, _set_position, _set_zone
from test_polygon_zones import GEOJSON
from custom_components.virtual_layer.device_tracker import (
    ATTR_LOCATION_PRIORITY_SOURCE,
    ATTR_LOCATION_SOURCE_OBSERVATIONS,
)

pytestmark = pytest.mark.unit
TRAVELLER = "device_tracker.travel_phone"


def _depart(hass, freezer):
    tracker = _helper_tracker(hass)
    _set_zone(hass, "zone.home", "Home", 37.5, 127)
    for source in tracker._source_entities:
        _set_position(hass, source, 37.5, 127)
    tracker._update_location_from_sources()
    freezer.tick(timedelta(seconds=60))
    hass.states.async_set(TRAVELLER, "not_home", {
        "latitude": 37.51, "longitude": 127, "gps_accuracy": 15,
    })
    tracker._update_location_from_sources()
    assert tracker.latitude == 37.51
    assert tracker.location_accuracy == 15
    return tracker


@pytest.mark.parametrize("radio", ["router", "bluetooth_le", "bluetooth"])
def test_departure_beats_device_left_on_home_radio(hass, freezer, radio):
    tracker = _helper_tracker(hass)
    tracker._presence_classification = True
    _set_zone(hass, "zone.home", "Home", 37.5, 127)
    _set_position(hass, TRAVELLER, 37.5, 127)
    hass.states.async_set("device_tracker.first_phone", "home", {"source_type": radio})
    tracker._source_entities.append("sensor.ble_distance")
    hass.states.async_set("sensor.ble_distance", "3")
    tracker._update_location_from_sources()
    assert tracker.state == "home"
    freezer.tick(timedelta(seconds=60))
    _set_position(hass, TRAVELLER, 37.51, 127)
    tracker._update_location_from_sources()
    assert tracker.latitude == 37.51
    assert tracker.state == "away"
    assert tracker.extra_state_attributes["location_selection_reason"] == "movement"


@pytest.mark.parametrize("polygon", [False, True])
@pytest.mark.parametrize("refresh", ["same_report", "battery", "none", "unavailable", "removed"])
def test_stop_keeps_carried_phone_and_marks_missing_reports(hass, freezer, refresh, polygon):
    tracker = _depart(hass, freezer)
    if polygon:
        tracker._polygon_config = tracker._normalize_polygon_config({"geojson": GEOJSON, "strategy": "adaptive"})
    freezer.tick(timedelta(minutes=31))
    for source in tracker._source_entities[:2]:
        _set_position(hass, source, 37.5, 127)
    attributes = {"latitude": 37.51, "longitude": 127, "gps_accuracy": 15}
    if refresh == "battery":
        attributes["battery_level"] = 80
    if refresh in {"same_report", "battery"}:
        hass.states.async_set(TRAVELLER, "not_home", attributes)
    elif refresh == "unavailable":
        hass.states.async_set(TRAVELLER, "unavailable", attributes)
    elif refresh == "removed":
        hass.states.async_remove(TRAVELLER)
    if polygon:
        tracker._update_polygon_from_sources()
    else:
        tracker._update_location_from_sources()
    assert tracker.latitude == 37.51
    assert tracker.extra_state_attributes["location_stale"] is (refresh not in {"same_report", "battery"})
    assert tracker.extra_state_attributes[ATTR_LOCATION_PRIORITY_SOURCE] == TRAVELLER


def test_two_devices_400m_apart_choose_traveller_despite_newer_battery_update(hass, freezer):
    tracker = _helper_tracker(hass)
    tracker._source_entities = tracker._source_entities[:2]
    for source in tracker._source_entities:
        _set_position(hass, source, 37.5, 127)
    tracker._update_location_from_sources()
    freezer.tick(timedelta(seconds=60))
    _set_position(hass, "device_tracker.second_phone", 37.5036, 127)
    freezer.tick(timedelta(seconds=1))
    hass.states.async_set("device_tracker.first_phone", "not_home", {
        "latitude": 37.5, "longitude": 127, "battery_level": 90,
    })
    tracker._update_location_from_sources()
    assert tracker.latitude == 37.5036
    assert tracker.extra_state_attributes[ATTR_LOCATION_PRIORITY_SOURCE] == "device_tracker.second_phone"


@pytest.mark.parametrize("accuracy,expected", [(5000, "accuracy"), (15, "implausible_speed")])
def test_gps_jump_is_rejected_even_when_rechecked_later(hass, freezer, accuracy, expected):
    tracker = _depart(hass, freezer)
    freezer.tick(timedelta(seconds=1))
    attributes = {"latitude": 35, "longitude": 129, "gps_accuracy": accuracy}
    hass.states.async_set(TRAVELLER, "not_home", attributes)
    tracker._update_location_from_sources()
    assert tracker.latitude == 37.51
    assert tracker.extra_state_attributes["location_rejected_sources"][TRAVELLER] == expected
    freezer.tick(timedelta(minutes=20))
    attributes["battery_level"] = 88
    hass.states.async_set(TRAVELLER, "not_home", attributes)
    tracker._update_location_from_sources()
    assert tracker.latitude == 37.51
    assert tracker.extra_state_attributes["location_stale"] is True
    freezer.tick(timedelta(seconds=60))
    _set_position(hass, TRAVELLER, 37.52, 127)
    tracker._update_location_from_sources()
    assert tracker.latitude == 37.52
    assert tracker.extra_state_attributes["location_stale"] is False


def test_accuracy_jitter_and_small_cumulative_motion(hass, freezer):
    tracker = _helper_tracker(hass)
    for source in tracker._source_entities:
        hass.states.async_set(source, "not_home", {"latitude": 37.5, "longitude": 127, "gps_accuracy": 20})
    tracker._update_location_from_sources()
    for step in range(1, 4):
        freezer.tick(timedelta(seconds=10))
        hass.states.async_set(TRAVELLER, "not_home", {
            "latitude": 37.5 + step * 0.0001, "longitude": 127, "gps_accuracy": 20,
        })
        tracker._update_location_from_sources()
        assert tracker.extra_state_attributes[ATTR_LOCATION_PRIORITY_SOURCE] is None
    freezer.tick(timedelta(seconds=10))
    hass.states.async_set(TRAVELLER, "not_home", {
        "latitude": 37.5004, "longitude": 127, "gps_accuracy": 20,
    })
    tracker._update_location_from_sources()
    assert tracker.extra_state_attributes[ATTR_LOCATION_PRIORITY_SOURCE] == TRAVELLER


def test_nearby_handoff_but_not_unrelated_remote_movement(hass, freezer):
    tracker = _depart(hass, freezer)
    freezer.tick(timedelta(seconds=60))
    _set_position(hass, "device_tracker.first_phone", 37.502, 127)
    tracker._update_location_from_sources()
    assert tracker.extra_state_attributes[ATTR_LOCATION_PRIORITY_SOURCE] == TRAVELLER
    freezer.tick(timedelta(seconds=60))
    _set_position(hass, TRAVELLER, 37.502, 127)
    tracker._update_location_from_sources()
    freezer.tick(timedelta(seconds=60))
    _set_position(hass, "device_tracker.first_phone", 37.512, 127)
    tracker._update_location_from_sources()
    assert tracker.latitude == 37.512
    assert tracker.extra_state_attributes["location_selection_reason"] == "handoff"


@pytest.mark.parametrize("field,value", [
    ("position", [True, False]), ("position", [91, 127]),
    ("anchor", [37, float("nan")]), ("timestamp", float("inf")),
    ("timestamp", True), ("accuracy", "bad"), ("anchor_accuracy", False),
])
def test_damaged_observation_cannot_restore_confirmed_movement(hass, freezer, field, value):
    tracker = _depart(hass, freezer)
    restored = _helper_tracker(hass)
    restored._virtual_attributes.update(deepcopy(tracker.extra_state_attributes))
    restored._virtual_attributes[ATTR_LOCATION_SOURCE_OBSERVATIONS][TRAVELLER][field] = value
    restored._restore_location_helper_attributes()
    assert TRAVELLER not in restored._source_last_moved
    restored._update_location_from_sources()


def test_removed_source_is_pruned_and_history_is_bounded(hass, freezer):
    tracker = _depart(hass, freezer)
    for step in range(20):
        freezer.tick(timedelta(seconds=60))
        _set_position(hass, TRAVELLER, 37.51 + step * .001, 127)
        tracker._update_location_from_sources()
    assert len(tracker._source_history[TRAVELLER]) <= 8
    tracker._source_entities.remove(TRAVELLER)
    tracker._update_location_from_sources()
    assert TRAVELLER not in tracker.extra_state_attributes[ATTR_LOCATION_SOURCE_OBSERVATIONS]
    assert tracker.extra_state_attributes[ATTR_LOCATION_PRIORITY_SOURCE] != TRAVELLER


@pytest.mark.parametrize("rule", [
    {"enabled": False}, {"condition_template": "{{ false }}"},
    {"max_gps_accuracy": 5}, {"max_age_seconds": 30},
])
def test_adaptive_polygon_respects_explicit_source_exclusions(hass, freezer, rule):
    tracker = _depart(hass, freezer)
    freezer.tick(timedelta(seconds=60))
    tracker._polygon_config = tracker._normalize_polygon_config({
        "geojson": GEOJSON, "strategy": "adaptive", "tracker_rules": {TRAVELLER: rule},
    })
    tracker._update_polygon_from_sources()
    assert tracker.latitude == 37.5
    assert tracker.extra_state_attributes["polygon_selected_source"] != TRAVELLER


def test_adaptive_polygon_dominant_rule_overrides_motion(hass, freezer):
    tracker = _depart(hass, freezer)
    tracker._polygon_config = tracker._normalize_polygon_config({
        "geojson": GEOJSON, "strategy": "adaptive",
        "tracker_rules": {"device_tracker.first_phone": {"dominant": True}},
    })
    tracker._update_polygon_from_sources()
    assert tracker.latitude == 37.5
    assert tracker.extra_state_attributes["polygon_selection_reason"] == "dominant"


def test_polygon_distance_controls_handoff_when_location_helper_also_exists(hass, freezer):
    tracker = _depart(hass, freezer)
    freezer.tick(timedelta(seconds=60))
    _set_position(hass, "device_tracker.first_phone", 37.508, 127)
    tracker._update_location_from_sources()
    tracker._polygon_config = tracker._normalize_polygon_config({
        "geojson": GEOJSON, "strategy": "adaptive", "distance_threshold_meters": 100,
    })
    freezer.tick(timedelta(seconds=60))
    _set_position(hass, "device_tracker.first_phone", 37.52, 127)
    tracker._update_polygon_from_sources()
    assert tracker.latitude == 37.51
    assert tracker.extra_state_attributes["polygon_selected_source"] == TRAVELLER


def test_latest_identical_report_limits_next_jump_speed(hass, freezer):
    tracker = _depart(hass, freezer)
    freezer.tick(timedelta(hours=2))
    old = hass.states.get(TRAVELLER)
    hass.states.async_set(TRAVELLER, old.state, dict(old.attributes))
    tracker._update_location_from_sources()
    freezer.tick(timedelta(seconds=1))
    _set_position(hass, TRAVELLER, 35, 129)
    tracker._update_location_from_sources()
    assert tracker.latitude == 37.51
    assert tracker.extra_state_attributes["location_rejected_sources"][TRAVELLER] == "implausible_speed"


def test_no_movement_evidence_reports_median_uncertainty(hass):
    tracker = _helper_tracker(hass)
    tracker._source_entities = tracker._source_entities[:2]
    for source, latitude in zip(tracker._source_entities, [37.5, 37.5036], strict=True):
        _set_position(hass, source, latitude, 127)
    tracker._update_location_from_sources()
    assert tracker.latitude == pytest.approx(37.5018)
    assert tracker.location_accuracy > 199
    assert tracker.extra_state_attributes["location_selection_reason"] == "median"
    assert not tracker._source_last_moved


def test_accuracy_only_update_is_not_movement(hass, freezer):
    tracker = _helper_tracker(hass)
    for latitude, accuracy in [(37.5, 20), (37.50025, 20), (37.50025, 1)]:
        freezer.tick(timedelta(seconds=30))
        hass.states.async_set(TRAVELLER, "not_home", {
            "latitude": latitude, "longitude": 127, "gps_accuracy": accuracy,
        })
        tracker._update_location_from_sources()
    assert not tracker._source_last_moved


def test_rejected_jump_is_not_accepted_after_reload_and_battery_update(hass, freezer):
    tracker = _depart(hass, freezer)
    freezer.tick(timedelta(seconds=1))
    _set_position(hass, TRAVELLER, 35, 129)
    tracker._update_location_from_sources()
    restored = _helper_tracker(hass)
    restored._virtual_attributes.update(deepcopy(tracker.extra_state_attributes))
    restored._restore_location_helper_attributes()
    freezer.tick(timedelta(minutes=20))
    hass.states.async_set(TRAVELLER, "not_home", {"latitude": 35, "longitude": 129, "battery_level": 80})
    restored._update_location_from_sources()
    assert restored.latitude == 37.51
    assert restored.extra_state_attributes["location_stale"] is True
