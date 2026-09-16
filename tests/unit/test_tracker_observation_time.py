"""Composite-compatible observation clocks and source-local motion estimates."""

from datetime import timedelta

import pytest
from homeassistant.core import State
from homeassistant.util import dt as dt_util

from test_device_tracker_location_helper import _helper_tracker
from test_polygon_zones import GEOJSON

pytestmark = pytest.mark.unit
SOURCE = "device_tracker.travel_phone"


def report(hass, measured, latitude=37.5, **attrs):
    hass.states.async_set(
        SOURCE,
        "not_home",
        {
            "lat": latitude,
            "lon": 127,
            "acc": 5,
            "last_seen": measured,
            **attrs,
        },
    )


@pytest.mark.parametrize("clock", ["last_seen", "last_timestamp"])
@pytest.mark.parametrize(
    "representation", ["epoch", "numeric_string", "iso", "datetime", "naive"]
)
def test_source_clock_formats_and_coordinate_aliases(
    hass, freezer, clock, representation
):
    tracker = _helper_tracker(hass)
    measured = dt_util.utcnow() - timedelta(seconds=120)
    value = {
        "epoch": measured.timestamp(),
        "numeric_string": str(measured.timestamp()),
        "iso": measured.isoformat(),
        "datetime": measured,
        "naive": dt_util.as_local(measured).replace(tzinfo=None),
    }[representation]
    report(hass, None, **{clock: value}) if clock != "last_seen" else report(
        hass, value
    )
    tracker._update_location_from_sources()
    assert tracker.latitude == 37.5
    assert tracker.location_accuracy == 5
    assert tracker._source_history[SOURCE][-1][1] == measured
    freezer.tick(timedelta(seconds=60))
    report(hass, dt_util.utcnow(), latitude=37.51)
    tracker._update_location_from_sources()
    assert tracker.extra_state_attributes["location_priority_source"] == SOURCE
    # 180 s between real fixes, even though HA received them 60 s apart.
    assert tracker.extra_state_attributes["location_speed_m_s"] == pytest.approx(
        6.18, abs=0.05
    )
    assert tracker.extra_state_attributes["location_bearing"] == pytest.approx(0)


@pytest.mark.parametrize("bad", [True, [], {}, "bad", "nan", float("inf"), 10**1000])
def test_invalid_explicit_clock_cannot_be_replaced_by_fresh_ha_timestamp(hass, bad):
    tracker = _helper_tracker(hass)
    report(hass, bad)
    tracker._update_location_from_sources()
    assert tracker.latitude is None
    assert (
        tracker.extra_state_attributes["location_rejected_sources"][SOURCE]
        == "invalid_timestamp"
    )


@pytest.mark.parametrize("age,reason", [(1900, "stale"), (-10, "future_timestamp")])
def test_measurement_age_wins_over_battery_update(hass, age, reason):
    tracker = _helper_tracker(hass)
    report(hass, dt_util.utcnow() - timedelta(seconds=age), battery_level=100)
    tracker._update_location_from_sources()
    assert tracker.latitude is None
    assert tracker.extra_state_attributes["location_rejected_sources"][SOURCE] == reason


@pytest.mark.parametrize("polygon", [False, True])
@pytest.mark.parametrize("bad_clock", ["backwards", "invalid", "future"])
def test_out_of_order_same_position_cannot_refresh_last_seen(
    hass, freezer, polygon, bad_clock
):
    tracker = _helper_tracker(hass)
    if polygon:
        tracker._polygon_config = tracker._normalize_polygon_config(
            {"geojson": GEOJSON, "strategy": "adaptive"}
        )
    update = (
        tracker._update_polygon_from_sources
        if polygon
        else tracker._update_location_from_sources
    )
    initial = dt_util.utcnow()
    report(hass, initial)
    update()
    freezer.tick(timedelta(seconds=60))
    report(hass, dt_util.utcnow(), latitude=37.51)
    update()
    seen = tracker.extra_state_attributes["location_last_seen"]
    freezer.tick(timedelta(seconds=60))
    bad_value = {
        "backwards": initial,
        "invalid": "bad",
        "future": dt_util.utcnow() + timedelta(minutes=10),
    }[bad_clock]
    report(hass, bad_value, latitude=37.51, battery_level=99)
    update()
    assert tracker.latitude == 37.51
    assert tracker.extra_state_attributes["location_last_seen"] == seen
    assert (
        tracker.extra_state_attributes["location_rejected_sources"][SOURCE]
        == {
            "backwards": "out_of_order",
            "invalid": "invalid_timestamp",
            "future": "future_timestamp",
        }[bad_clock]
    )
    assert tracker.extra_state_attributes["location_speed_m_s"] is None


def test_explicit_stationary_fix_clears_speed_and_replay_does_not_refresh_it(
    hass, freezer
):
    tracker = _helper_tracker(hass)
    report(hass, dt_util.utcnow())
    tracker._update_location_from_sources()
    freezer.tick(timedelta(seconds=60))
    report(hass, dt_util.utcnow(), latitude=37.51)
    tracker._update_location_from_sources()
    assert tracker.extra_state_attributes["location_speed_m_s"] > 0
    freezer.tick(timedelta(seconds=60))
    measured = dt_util.utcnow()
    report(hass, measured, latitude=37.51)
    tracker._update_location_from_sources()
    assert tracker.extra_state_attributes["location_speed_m_s"] == 0
    assert tracker.extra_state_attributes["location_bearing"] is None
    freezer.tick(timedelta(seconds=301))
    report(hass, measured, latitude=37.51, battery_level=50)
    tracker._update_location_from_sources()
    assert tracker.extra_state_attributes["location_speed_m_s"] is None
    assert tracker.extra_state_attributes["location_last_seen"] == measured.isoformat()


def test_reload_does_not_invent_speed_from_restored_coordinate(hass, freezer):
    tracker = _helper_tracker(hass)
    report(hass, dt_util.utcnow())
    tracker._update_location_from_sources()
    freezer.tick(timedelta(seconds=60))
    report(hass, dt_util.utcnow(), latitude=37.51)
    tracker._update_location_from_sources()
    restored = _helper_tracker(hass)
    restored._restore_state(
        State(
            tracker.entity_id,
            "not_home",
            {
                **tracker.extra_state_attributes,
                "latitude": tracker.latitude,
                "longitude": tracker.longitude,
            },
        ),
        {"initial_value": "not_home"},
    )
    restored._update_location_from_sources()
    assert restored.latitude == 37.51
    assert restored.extra_state_attributes["location_speed_m_s"] is None


def test_polygon_latest_and_age_rule_use_measurements_not_receipt_time(hass, freezer):
    tracker = _helper_tracker(hass)
    tracker._polygon_config = tracker._normalize_polygon_config(
        {"geojson": GEOJSON, "strategy": "latest"}
    )
    other = "device_tracker.first_phone"
    measured = dt_util.utcnow()
    report(hass, measured)
    freezer.tick(timedelta(seconds=30))
    hass.states.async_set(
        other,
        "not_home",
        {
            "latitude": 38,
            "longitude": 128,
            "last_seen": measured - timedelta(seconds=120),
        },
    )
    tracker._update_polygon_from_sources()
    assert tracker.latitude == 37.5
    assert tracker.extra_state_attributes["polygon_selected_source"] == SOURCE
    tracker._polygon_config["tracker_rules"] = {
        SOURCE: {"max_age_seconds": 20},
        other: {"max_age_seconds": 20},
    }
    tracker._update_polygon_from_sources()
    assert tracker.extra_state_attributes["polygon_selected_source"] is None


def test_accuracy_alias_is_validated_before_accepting_motion(hass):
    tracker = _helper_tracker(hass)
    report(hass, dt_util.utcnow(), acc=999)
    tracker._update_location_from_sources()
    assert tracker.latitude is None
    assert (
        tracker.extra_state_attributes["location_rejected_sources"][SOURCE]
        == "accuracy"
    )


@pytest.mark.parametrize("seconds", [1, 120])
def test_speed_interval_and_geodesic_bearing_at_date_line(hass, freezer, seconds):
    tracker = _helper_tracker(hass)
    report(hass, dt_util.utcnow(), latitude=0, lon=179.999)
    tracker._update_location_from_sources()
    freezer.tick(timedelta(seconds=seconds))
    report(hass, dt_util.utcnow(), latitude=0, lon=-179.999)
    tracker._update_location_from_sources()
    speed = tracker.extra_state_attributes["location_speed_m_s"]
    if seconds == 1:
        assert speed is None
        assert tracker.extra_state_attributes["location_bearing"] is None
    else:
        assert speed == pytest.approx(1.85, abs=0.05)
        assert tracker.extra_state_attributes["location_bearing"] == pytest.approx(90)


def test_handoff_speed_uses_new_devices_own_path(hass, freezer):
    tracker = _helper_tracker(hass)
    report(hass, dt_util.utcnow())
    tracker._update_location_from_sources()
    freezer.tick(timedelta(seconds=120))
    report(hass, dt_util.utcnow(), latitude=37.51)
    tracker._update_location_from_sources()
    replacement = "device_tracker.first_phone"
    hass.states.async_set(
        replacement,
        "not_home",
        {
            "latitude": 37.511,
            "longitude": 127,
            "gps_accuracy": 5,
            "last_seen": dt_util.utcnow().isoformat(),
        },
    )
    tracker._update_location_from_sources()
    freezer.tick(timedelta(seconds=120))
    hass.states.async_set(
        replacement,
        "not_home",
        {
            "latitude": 37.513,
            "longitude": 127,
            "gps_accuracy": 5,
            "last_seen": dt_util.utcnow().isoformat(),
        },
    )
    tracker._update_location_from_sources()
    assert tracker.extra_state_attributes["location_priority_source"] == replacement
    assert tracker.extra_state_attributes["location_speed_m_s"] == pytest.approx(
        1.85, abs=0.05
    )
