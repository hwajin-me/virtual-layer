"""Boundary motion follows polygon depth, not the Home centre distance."""

import pytest

from custom_components.virtual_layer.polygon import parse_geojson_zones, polygon_clearance
from custom_components.virtual_layer.presence_fusion.models import GPS, Settings
from custom_components.virtual_layer.presence_fusion.movement import Path


def boundary():
    zones = parse_geojson_zones({
        "type": "Feature", "properties": {"name": "Home"},
        "geometry": {"type": "MultiPolygon", "coordinates": [
            [[[0, 0], [.02, 0], [.02, .02], [0, .02], [0, 0]]],
            [[[.03, 0], [.05, 0], [.05, .02], [.03, .02], [.03, 0]]],
        ]},
    })
    return lambda lat, lon: polygon_clearance(lat, lon, zones)


@pytest.mark.parametrize(("longitudes", "expected"), [
    ([-.002, -.001, .001], "towards"),
    ([.001, -.001, -.002], "away_from"),
    ([.029, .031, .033], "towards"),
    ([.005, .00501, .005], "stationary"),
    ([-.003, -.001, -.002], None),
])
def test_boundary_motion_crossings_disjoint_polygons_jitter_and_reversal(longitudes, expected):
    path = Path(Settings())
    for index, lon in enumerate(longitudes):
        stamp = index * 30
        assert path.add(GPS(.01, lon, 5, stamp, stamp), stamp)
    path.advance(90)
    assert path.direction(90, (0, 0, 100), 0, boundary()) == expected
    assert path.direction(1000, (0, 0, 100), 0, boundary()) is None


def test_polygon_depth_can_increase_while_distance_from_home_centre_increases():
    path = Path(Settings())
    for stamp, lon in [(0, .029), (30, .031), (60, .033)]:
        path.add(GPS(.01, lon, 5, stamp, stamp), stamp)
    path.advance(90)
    assert path.direction(90, (0, 0, 100), 0) == "away_from"
    assert path.direction(90, (0, 0, 100), 0, boundary()) == "towards"


def test_engine_exposes_entry_departure_without_overriding_home_presence():
    from custom_components.virtual_layer.presence_fusion.engine import Engine
    from custom_components.virtual_layer.presence_fusion.models import Device

    engine = Engine([Device("phone", "Phone", 0)], Settings(), (.01, .005, 100))
    engine.home_boundary = boundary()
    for stamp, lon in [(0, -.002), (30, -.001), (60, .001)]:
        engine.observe("phone", GPS(.01, lon, 5, stamp, stamp), stamp, stamp)
        engine.evaluate(stamp, stamp)
    result = engine.evaluate(90, 90)
    assert result.boundary_motion == "entering"
    assert result.boundary_distance > 0
    for stamp, lon in [(120, -.001), (150, -.003), (180, -.005)]:
        engine.observe("phone", GPS(.01, lon, 5, stamp, stamp), stamp, stamp)
        engine.evaluate(stamp, stamp)
    assert engine.evaluate(210, 210).boundary_motion == "leaving"
    result = engine.evaluate(1000, 1000)
    assert result.boundary_motion is None
    assert result.boundary_distance is None


@pytest.mark.parametrize("mode", ["off", "attributes", "presence"])
def test_motion_presence_mode(mode):
    from custom_components.virtual_layer.presence_fusion.engine import Engine
    from custom_components.virtual_layer.presence_fusion.models import Device

    engine = Engine([Device("phone", "Phone", 0)], Settings(boundary_motion_mode=mode), (.01, .005, 100))
    engine.home_boundary = boundary()
    for stamp, lon in [(0, -.006), (30, -.004), (60, -.002)]:
        engine.observe("phone", GPS(.01, lon, 5, stamp, stamp), stamp, stamp)
        engine.evaluate(stamp, stamp)
    result = engine.evaluate(90, 90)
    assert result.presence == ("entering" if mode == "presence" else "arriving")
    assert result.boundary_motion == (None if mode == "off" else "entering")


def test_direction_window_validation():
    assert "direction_min_span_s" in Settings(direction_min_span_s=200).errors()
    assert "direction_window_s" in Settings(movement_bucket_s=100).errors()
    assert "boundary_motion_mode" in Settings(boundary_motion_mode="invalid").errors()


@pytest.mark.parametrize(("direction", "motion", "seconds"), [
    ("towards", "entering", 30), ("away_from", "leaving", 60),
])
def test_separate_motion_holds_require_new_observations_and_reset(direction, motion, seconds):
    from custom_components.virtual_layer.presence_fusion.engine import Engine
    from custom_components.virtual_layer.presence_fusion.models import Device

    engine = Engine([Device("phone", "Phone", 0)], Settings(entering_hold_s=30, leaving_hold_s=60), (0, 0, 100))
    engine.primary = "phone"
    first = GPS(0, .001, 5, 100, 100)
    assert engine._confirmed_motion(first, direction, True, 100) is None
    # Repeated timer evaluations of the same fix cannot complete a hold.
    assert engine._confirmed_motion(first, direction, True, 100 + seconds) is None
    latest = GPS(0, .001, 5, 100 + seconds, 100 + seconds)
    assert engine._confirmed_motion(latest, direction, True, 100 + seconds) == motion
    assert engine._confirmed_motion(None, direction, True, 101 + seconds) is None
    assert engine._confirmed_motion(latest, direction, True, 102 + seconds) is None


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), True, 86401])
def test_invalid_motion_hold_rejected(value):
    errors = Settings(entering_hold_s=value, leaving_hold_s=value).errors()
    assert "entering_hold_s" in errors and "leaving_hold_s" in errors
