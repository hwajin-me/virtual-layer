"""Synthetic metre fixtures; no production geometry used for expected values."""

import pytest

from custom_components.virtual_layer.presence_fusion.engine import Engine
from custom_components.virtual_layer.presence_fusion.models import GPS, Device, Settings
from custom_components.virtual_layer.presence_fusion.movement import Path, meters


def fix(t, x=0, accuracy=10):
    return GPS(0, x / 111195.08, accuracy, t, t)


def engine(n=2, **settings):
    return Engine(
        [Device(str(i), str(i), i, True) for i in range(n)],
        Settings(**settings),
        (0, 0, 100),
    )


def feed(e, t, positions):
    for key, x in positions.items():
        e.observe(key, fix(t, x), t, t)
    return e.evaluate(t, t)


def departure():
    e = engine(3)
    for t in (0, 30, 60):
        feed(e, t, {"0": 0, "1": 0, "2": 0})
    for t in range(90, 391, 30):
        feed(e, t, {"0": 0, "1": (t - 60) * 4, "2": 0})
    return e


def test_T02_departure_left_behind():
    e = departure()
    e.local("0", "wifi", "present")
    e.local("2", "ble", "present")
    s = e.evaluate(391, 391)
    assert s.primary == "1"
    assert s.mode == "dynamic"
    assert s.presence != "home"
    assert e.active == {"1"}


def test_T03_stationary_retention():
    e = departure()
    for t in range(420, 1801, 30):
        s = feed(e, t, {"0": 0, "1": 1320, "2": 0})
    assert s.primary == "1" and s.mode == "dynamic"
    assert e.paths["1"].movement(1800)[0] == 0


def test_T08_no_remote_fallback():
    e = departure()
    e.local("0", "wifi", "present")
    for t in range(420, 1101, 30):
        s = feed(e, t, {"0": 0, "2": 0})
    assert s.primary == "1" and s.gps is None
    assert s.presence is None


def test_T15_cold_ambiguous():
    e = engine()
    for t in (0, 30, 60, 120):
        s = feed(e, t, {"0": 0, "1": 2000})
    assert s.primary is None and s.health == "ambiguous"


def test_T30_timer_expiration():
    e = engine(1)
    feed(e, 0, {"0": 0})
    assert feed(e, 60, {"0": 0}).presence == "home"
    assert e.evaluate(360, 360).gps is None
    assert e.evaluate(660, 660).presence is None


def test_T23_ordering():
    e = engine(1)
    assert e.observe("0", fix(100), 100, 100)
    for sample, reason in [
        (fix(100), "duplicate"),
        (fix(99), "out_of_order"),
        (fix(100, 50), "timestamp_conflict"),
        (fix(200), "future"),
    ]:
        assert not e.observe("0", sample, 100, 100)
        assert e.paths["0"].rejection == reason


def test_T44_manual_validation_and_expiry():
    e = engine(2)
    feed(e, 0, {"0": 0, "1": 2000})
    for device, duration in [("other", 10), ("0", 0), ("0", 86401)]:
        with pytest.raises(ValueError):
            e.set_primary(device, duration, 0, 0)
    e.set_primary("1", 60, 0, 0)
    assert e.evaluate(59, 59).mode == "manual"
    s = e.evaluate(60, 60)
    assert s.mode == "dynamic" and s.primary == "1"


@pytest.mark.parametrize("order", [("0", "1", "2"), ("2", "0", "1")])
def test_T01_initial_priority(order):
    e = engine(3)
    for t in (0, 30, 60):
        s = feed(e, t, dict.fromkeys(order, 0))
    assert s.primary == "0" and s.mode == "priority"


def scores(candidate=850):
    e = engine(2)
    for t in (0, 30, 60):
        feed(e, t, {"0": 0, "1": 0})
    # Explicit input to the arbitration layer: real path score calculation,
    # no monkeypatching production functions or expected values.
    for key, length in [("0", 600), ("1", candidate)]:
        e.paths[key].segments.extend([(100, 130, length / 2), (130, 160, length / 2)])
    feed(e, 180, {"0": 0, "1": 0})
    return e


@pytest.mark.parametrize("candidate", [600, 620, 780])
def test_T04_T05_strict_score_threshold(candidate):
    e = scores(candidate)
    for t in (210, 225, 240):
        s = feed(e, t, {"0": 0, "1": 0})
    assert s.primary == "0"


def test_T05_challenger_boundary():
    e = scores()
    assert feed(e, 224, {"0": 0, "1": 0}).primary == "0"
    assert feed(e, 225, {"0": 0, "1": 0}).primary == "1"


def test_T06_challenger_reset():
    e = scores()
    e.paths["1"].available = False
    e.evaluate(210, 210)
    feed(e, 215, {"0": 0, "1": 0})
    assert feed(e, 259, {"0": 0, "1": 0}).primary == "0"
    assert feed(e, 260, {"0": 0, "1": 0}).primary == "1"


def test_T07_safe_companion():
    e = engine(2, gps_stale_after_s=90, gps_fresh_full_s=30)
    for t in (0, 30, 60):
        feed(e, t, {"0": 2000, "1": 2000})
    for t in (90, 120):
        feed(e, t, {"1": 2000})
    s = feed(e, 150, {"1": 2000})
    assert s.primary == "1" and s.reason == "safe_companion_failover"


@pytest.mark.parametrize("place", [0, 1320])
def test_T09_T11_T13_reunion(place):
    e = departure()
    # Move both to a common location within plausible speed/gap limits.
    feed(e, 420, {"0": place, "1": place})
    feed(e, 480, {"0": place, "1": place})
    assert e.evaluate(539, 539).mode == "dynamic"
    s = feed(e, 540, {"0": place, "1": place})
    assert s.mode == "priority" and s.primary == "0"
    for t in (570, 600, 630):
        s = feed(e, t, {"0": place, "1": place})
    assert s.mode == "priority" and s.primary == "0"


def test_T10_companions_not_reunion():
    e = engine(3)
    for t in (0, 30, 60):
        feed(e, t, {"0": 0, "1": 0, "2": 0})
    for t in range(90, 601, 30):
        feed(e, t, {"0": (t - 60) * 4, "1": (t - 60) * 4, "2": 0})
    # A selected dynamic tracking session with two actual companions.
    e.mode = "dynamic"
    e.active = {"0", "1"}
    e.separated = {"2"}
    for t in range(630, 931, 30):
        s = feed(e, t, {"0": 2160, "1": 2160, "2": 0})
    assert s.mode == "dynamic"


def test_T12_local_return_requires_active_transition():
    e = departure()
    e.local("0", "wifi", "present")
    e.evaluate(700, 700)
    assert e.mode == "dynamic" and e.presence != "home"
    e.local("1", "wifi", "absent")
    e.evaluate(701, 701)
    e.local("1", "wifi", "present")
    e.evaluate(702, 702)
    e.evaluate(822, 822)
    # Local reunion never makes a stale source a GPS supplier.
    assert e.primary == "1"
    assert e.evaluate(823, 823).gps is None
    feed(e, 824, {"0": 0})
    feed(e, 854, {"0": 0})
    assert e.mode == "priority" and e.primary == "0"


def test_T14_successor_and_remote_cluster():
    e = engine(3)
    for t in (0, 30, 60):
        feed(e, t, {"0": 2000, "1": 2000, "2": 10000})
    e.set_primary("0", 1, 60, 60)
    for t in range(90, 451, 30):
        s = feed(e, t, {"0": 2000, "1": 2000 + (t - 60) * 4, "2": 10000 + (t - 60) * 8})
    assert s.primary == "1" and s.mode == "dynamic"
    assert s.health == "ambiguous"


def path_samples(points, accuracy=10, **settings):
    p = Path(Settings(**settings))
    for t, x in points:
        assert p.add(fix(t, x, accuracy), t)
    p.advance(points[-1][0] + 30)
    return p


def test_T16_jitter():
    p = path_samples([(t, (t % 3 - 1) * 5) for t in range(601)], 20)
    assert p.movement(600)[0] == 0


def test_T17_slow_walking():
    p = path_samples([(t, t * 0.6) for t in range(0, 301, 5)])
    assert p.movement(300)[0] > 100


def test_T18_round_trip():
    p = path_samples(
        [(t, t if t <= 300 else 600 - t) for t in range(0, 601, 30)],
        movement_window_s=900,
    )
    assert p.movement(600)[0] == pytest.approx(600, abs=1)
    assert meters(p.samples[0], p.samples[-1]) == pytest.approx(0, abs=1)
    assert p.displacement(600) == pytest.approx(0, abs=1)


def test_T19_sample_frequency():
    distances = [
        path_samples([(t, t * (1000 / 600)) for t in range(0, 601, step)]).movement(
            600
        )[0]
        for step in (1, 5, 30)
    ]
    assert max(distances) - min(distances) <= 100


def test_T20_jump():
    p = Path(Settings())
    assert p.add(fix(0), 0)
    assert not p.add(fix(10, 10000), 10)
    assert p.rejection == "jump" and p.latest.observed == 0
    assert p.add(fix(20), 20)
    p.advance(60)
    assert p.movement(60)[0] == 0


def test_T21_reacquire():
    p = Path(Settings())
    p.add(fix(0), 0)
    assert not p.add(fix(7200, 100000), 7200)
    assert p.rejection == "reacquiring"
    assert not p.add(fix(7210, 100010), 7210)
    assert p.add(fix(7230, 100020), 7230)
    p.advance(7260)
    assert p.latest.longitude == fix(7230, 100020).longitude
    assert p.movement(7260)[0] == 0


@pytest.mark.parametrize(
    "latitude,longitude",
    [(0, 0), (float("nan"), 0), (0, float("inf")), (91, 0), (0, 181)],
)
def test_T22_coordinate_validation(latitude, longitude):
    p = Path(Settings())
    assert p.add(GPS(latitude, longitude, 10, 0, 0), 0) == (
        latitude == 0 and longitude == 0
    )


def test_T22_poor_accuracy_cannot_poison_anchor():
    p = Path(Settings())
    assert p.add(fix(0), 0)
    assert not p.add(fix(10, 10000, 10000), 10)
    assert p.rejection == "accuracy" and not p.fresh(10)
    assert p.latest.observed == 0
    assert not p.add(fix(20, 10000), 20)
    assert p.rejection == "jump"
    assert p.add(fix(30), 30)
    p.advance(60)
    assert p.movement(60)[0] == 0


def test_T25_identical_fresh_measurements():
    p = path_samples([(t, 0) for t in range(0, 1201, 30)])
    assert p.fresh(1200) and p.movement(1200)[0] == 0


def test_T26_skew_and_uncertainty():
    e = engine()
    for t in (0, 60, 120):
        e.observe("0", fix(t), t, t)
        e.observe("1", fix(t - 100), t, t)
        e.evaluate(t, t)
    assert not e.relations.close("0", "1", 120)
    e = engine()
    for t in (0, 60, 120):
        for key in e.paths:
            e.observe(key, fix(t, accuracy=100), t, t)
        e.evaluate(t, t)
    assert not e.relations.close("0", "1", 120)


def test_T09_interrupted_reunion_restarts_hold():
    e = departure()
    feed(e, 420, {"0": 1320, "1": 1320})
    feed(e, 480, {"0": 1320, "1": 1320})
    e.paths["0"].available = False
    e.evaluate(490, 490)
    feed(e, 500, {"0": 1320, "1": 1320})
    feed(e, 540, {"0": 1320, "1": 1320})
    assert e.mode == "dynamic"
    feed(e, 560, {"0": 1320, "1": 1320})
    assert e.evaluate(619, 619).mode == "dynamic"
    assert feed(e, 620, {"0": 1320, "1": 1320}).mode == "priority"


def test_T27_complete_link():
    e = engine(3)
    for t in (0, 30, 60):
        feed(e, t, {"0": 0, "1": 70, "2": 140})
    assert sorted(map(len, e.relations.groups(e.paths, 60))) == [1, 2]


def test_T28_GPS_only():
    e = engine(1)
    feed(e, 0, {"0": 0})
    assert feed(e, 60, {"0": 0}).presence == "home"
    feed(e, 90, {"0": 300})
    assert feed(e, 149, {"0": 300}).presence == "home"
    assert feed(e, 150, {"0": 300}).presence == "nearby"
    feed(e, 180, {"0": 0})
    assert feed(e, 240, {"0": 0}).presence == "home"


def test_T29_T31_local_home_during_GPS_loss():
    e = engine(1)
    feed(e, 0, {"0": 0})
    e.local("0", "wifi", "present")
    e.local("0", "ble", "present")
    e.evaluate(1, 1)
    assert e.evaluate(6, 6).presence == "home"
    e.local("0", "wifi", "absent")
    assert e.evaluate(16, 16).presence == "home"
    s = e.evaluate(1000, 1000)
    assert s.presence == "home" and s.gps is None


def test_T32_conflict_expiration():
    e = engine(1)
    feed(e, 0, {"0": 0})
    feed(e, 60, {"0": 0})
    e.local("0", "wifi", "present")
    for t in range(90, 421, 30):
        s = feed(e, t, {"0": 2000})
    assert s.health == "ambiguous" and s.presence is None
    assert s.reason == "conflicting_local_and_gps"


def test_T33_direction_hysteresis():
    e = engine(1)
    for t, x in [(0, 900), (30, 700), (60, 500), (90, 500)]:
        s = feed(e, t, {"0": x})
    assert s.direction == "towards" and s.presence == "arriving"
    assert feed(e, 120, {"0": 1100}).presence in {"nearby", "arriving"}
    assert feed(e, 150, {"0": 1201}).presence == "away"


def test_T34_primary_direction_reset():
    e = engine(2)
    for t in (0, 30, 60):
        feed(e, t, {"0": 2000, "1": 2000 - t * 5})
    e.set_primary("1", 120, 60, 60)
    assert e.evaluate(60, 60).direction is None


def test_T35_T36_room_debounce_and_missing():
    e = engine(1)
    feed(e, 0, {"0": 0})
    e.local("0", "room", "present", room="bedroom")
    e.evaluate(0, 0)
    e.evaluate(5, 5)
    for t, room in [(8, "livingroom"), (12, "bedroom"), (16, "livingroom")]:
        e.local("0", "room", "present", room=room)
        assert e.evaluate(t, t).room is None
    assert e.evaluate(23, 23).room is None
    assert e.evaluate(24, 24).room == "livingroom"
    e.local("0", "room", "unknown", room="")
    e.evaluate(25, 25)
    assert e.evaluate(54, 54).room == "livingroom"
    assert e.evaluate(55, 55).room is None
    other = departure()
    other.local("0", "room", "present", room="bedroom")
    assert other.evaluate(400, 400).room is None


def test_T37_window_fraction():
    p = Path(Settings())
    p.segments.append((0, 100, 300))
    assert p.movement(650)[0] == pytest.approx(150)
    p.advance(700)
    assert not p.segments
    p = path_samples([(t, t * 0.2) for t in range(0, 601, 5)])
    assert p.movement(600)[0] > 80
    p = path_samples(
        [(t, t) for t in range(0, 601, 10)],
        movement_bucket_s=200,
        movement_window_s=900,
    )
    assert p.movement(600)[0] == pytest.approx(400, abs=1)


def test_T42_T43_restore_no_extended_grace():
    e = departure()
    data = e.metadata()
    restored = engine(3)
    restored.restore(data, 1000, 0)
    assert restored.primary == "1" and restored.active == {"1"}
    s = restored.evaluate(1000, 0)
    assert s.presence is None and s.gps is None
    assert not restored.paths["1"].segments and restored.candidate is None
    for bad in (None, {}, {"version": 2}, {"version": 1, "primary": []}):
        engine().restore(bad, 1000, 0)


def test_T45_geometry_and_monotonic():
    e = engine(1)
    feed(e, 0, {"0": 0})
    e.evaluate(10, 59)
    assert e.presence is None
    assert e.evaluate(10, 60).presence == "home"
    e.home = (0, 0, 2000)
    assert e.evaluate(20, 70).health == "ambiguous"


def test_T48_bounded_history():
    e = engine(8)
    for t in range(3601):
        feed(e, t, {str(i): t * 0.5 for i in range(8)})
    for p in e.paths.values():
        assert len(p.samples) <= 22 and len(p.segments) <= 22
    assert len(e.relations.pairs) == 28
    assert len(e.history) <= 32


def test_T23_closed_bucket_not_rewritten_by_delayed_data():
    path = Path(Settings())
    assert path.add(fix(0), 0)
    path.advance(30)
    assert path.add(fix(29, 20), 40)
    assert path.latest.observed == 29
    assert len(path.samples) == 1 and path.samples[0].observed == 0
    assert path.bucket is None


def test_T02_primary_itself_leads_departure():
    e = engine(2)
    for t in (0, 30, 60):
        feed(e, t, {"0": 0, "1": 0})
    e.local("1", "wifi", "present")
    for t in range(90, 391, 30):
        s = feed(e, t, {"0": (t - 60) * 4, "1": 0})
    assert s.primary == "0" and s.mode == "dynamic" and e.active == {"0"}
    assert "1" in e.separated and s.presence != "home"


def test_T12_unrelated_local_source_cannot_fabricate_return():
    e = departure()
    e.local("1", "wifi", "absent")
    e.local("1", "ble", "present")
    assert "1" not in e.returned


def test_T12_local_reunion_deferred_priority():
    e = departure()
    e.local("0", "wifi", "present")
    e.local("1", "wifi", "absent")
    e.evaluate(700, 700)
    e.local("1", "wifi", "present")
    e.evaluate(701, 701)
    for t in (710, 740, 770, 800, 821):
        s = feed(e, t, {"1": 0})
    assert s.mode == "priority" and s.primary == "1"
    assert e.active == {"0", "1"}
    feed(e, 830, {"0": 0, "1": 0})
    s = feed(e, 860, {"0": 0, "1": 0})
    assert s.primary == "0" and s.mode == "priority"


def test_T13_session_does_not_reuse_crossing_segment():
    p = Path(Settings())
    p.segments.append((0, 100, 300))
    assert p.movement(110, since=90) == (0, 0)
    assert p.movement(650)[0] == pytest.approx(150)


def test_T36_ineligible_room_is_not_held_after_manual_selection():
    e = engine(2)
    feed(e, 0, {"0": 0, "1": 2000})
    e.set_primary("0", 600, 0, 0)
    e.local("0", "room", "present", room="private_room")
    e.evaluate(0, 0)
    e.evaluate(5, 5)
    assert e.evaluate(13, 13).room == "private_room"
    e.set_primary("1", 600, 14, 14)
    assert e.evaluate(14, 14).room is None


@pytest.mark.parametrize(
    "bad",
    [
        {"presence": []},
        {"active": []},
        {"separated": ["1"]},
        {"session": 2000},
        {"missing_since": 2000},
    ],
)
def test_T43_restore_is_atomic_and_rejects_inconsistent_hints(bad):
    data = departure().metadata()
    data.update(bad)
    e = engine(3)
    e.restore(data, 1000, 0)
    assert e.primary is None and not e.active and not e.separated


def test_T26_sequential_packets_do_not_interrupt_valid_pair_run():
    e = engine(2)
    for t in (0, 90, 180):
        for device in ("0", "1"):
            e.observe(device, fix(t, 2000), t, t)
            e.evaluate(t, t)
    assert e.primary == "0" and e.active == {"0", "1"}


def test_T21_T23_reacquisition_duplicates_preserve_confirmation_window():
    p = Path(Settings())
    assert p.add(fix(0), 0)
    assert not p.add(fix(300, 10000), 300)
    assert not p.add(fix(320, 10000), 320)
    candidate = p.reacquire
    for sample in (fix(320, 10000), fix(310, 10000), fix(320, 11000)):
        assert not p.add(sample, 320)
        assert p.reacquire == candidate
    assert p.add(fix(330, 10000), 330)
    assert p.movement(330)[0] == 0


def test_T32_conflict_requires_new_observations_not_timer_counts():
    e = engine(1)
    feed(e, 0, {"0": 2000})
    e.local("0", "wifi", "present")
    for t in range(1, 30):
        s = e.evaluate(t, t)
        assert s.health == "degraded"
    s = feed(e, 30, {"0": 2000})
    assert s.health == "ambiguous" and s.reason == "conflicting_local_and_gps"
    assert s.missing_since == 1


def test_T28_exit_hold_survives_nearby_boundary():
    e = engine(1)
    feed(e, 0, {"0": 0})
    assert feed(e, 60, {"0": 0}).presence == "home"
    feed(e, 90, {"0": 900})
    assert feed(e, 120, {"0": 1300}).presence == "home"
    assert feed(e, 149, {"0": 1300}).presence == "home"
    assert feed(e, 150, {"0": 1300}).presence == "away"


def test_T12_gps_return_can_confirm_home_local_reunion():
    e = departure()
    e.local("0", "wifi", "present")
    for t in (710, 740, 770, 800, 830, 860):
        s = feed(e, t, {"1": 0})
    assert s.mode == "priority" and e.active == {"0", "1"}
    assert s.primary == "1"  # The local anchor's stale GPS remains ineligible.


def test_T06_changing_candidate_restarts_hold():
    e = engine(3)
    for t in (0, 30, 60):
        feed(e, t, dict.fromkeys(("0", "1", "2"), 0))
    for key, length in (("0", 600), ("1", 850), ("2", 800)):
        e.paths[key].segments.extend([(100, 130, length / 2), (130, 160, length / 2)])
    feed(e, 180, dict.fromkeys(("0", "1", "2"), 0))
    assert e.candidate == ("1", 180)
    e.paths["2"].segments.append((160, 190, 200))
    feed(e, 210, dict.fromkeys(("0", "1", "2"), 0))
    assert e.candidate == ("2", 210)
    assert feed(e, 254, dict.fromkeys(("0", "1", "2"), 0)).primary == "0"
    assert feed(e, 255, dict.fromkeys(("0", "1", "2"), 0)).primary == "2"


def test_T12_local_reunion_without_any_valid_gps():
    e = departure()
    e.local("0", "wifi", "present")
    e.local("1", "wifi", "absent")
    e.evaluate(700, 700)
    e.local("1", "wifi", "present")
    e.evaluate(701, 701)
    s = e.evaluate(821, 821)
    assert s.mode == "priority" and s.gps is None and s.presence == "home"
    assert e.active == {"0", "1"} and e.deferred_priority == {"0", "1"}
    restored = engine(3)
    restored.restore(e.metadata(), 830, 0)
    assert restored.deferred_priority == {"0", "1"}
    assert restored.evaluate(830, 0).gps is None


@pytest.mark.parametrize("duration", [0.5, 86401])
def test_T38_T44_default_override_duration_range(duration):
    assert Settings(manual_override_default_s=duration).errors() == {
        "manual_override_default_s": "duration"
    }
