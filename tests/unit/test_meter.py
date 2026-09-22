"""Calendar boundaries and billing contracts, including invalid user inputs."""
from datetime import datetime, timezone
from decimal import Decimal

import pytest
import voluptuous as vol
from homeassistant.util import dt as dt_util

from custom_components.virtual_layer import meter


def settings(**values):
    return meter.options({meter.PREFIX + key: value for key, value in values.items()})


@pytest.mark.parametrize("value", [True, None, {}, [], "nan", "Infinity", "1e19", "abc"])
def test_bad_numbers(value):
    with pytest.raises(vol.Invalid):
        meter.number(value)


@pytest.mark.parametrize("values", [
    {"enabled": "false"}, {"compare_previous_month": "false"},
    {"compare_previous_month": 1}, {"cycle": "bad"}, {"days": 0}, {"days": 1.5},
    {"offset": 40320}, {"start": 1}, {"start": "bad"}, {"cycle": "days"},
    {"cycle": "cron", "cron": "bad"}, {"cycle": "cron", "cron": "0 0 31 2 *"},
    {"rate": -1}, {"base_charge": -1}, {"currency": 123}, {"currency": "K"},
    {"currency": "123"}, {"currency": "한글원"}, {"tiers": {}},
    {"tiers": [{}] * 101}, {"tiers": [1]}, {"tiers": [{"up_to": 1}]},
    {"tiers": [{"up_to": 0, "rate": 1}]}, {"tiers": [{"up_to": 1, "rate": -1}]},
])
def test_invalid_options(values):
    with pytest.raises(vol.Invalid):
        settings(**values)


@pytest.mark.parametrize("cycle,expected", [
    ("none", None), ("hourly", "2026-01-10T13:00:00+00:00"),
    ("quarter-hourly", "2026-01-10T12:15:00+00:00"),
    ("daily", "2026-01-11T00:00:00+00:00"),
    ("weekly", "2026-01-12T00:00:00+00:00"),
    ("monthly", "2026-02-01T00:00:00+00:00"),
    ("bimonthly", "2026-03-01T00:00:00+00:00"),
    ("quarterly", "2026-04-01T00:00:00+00:00"),
    ("yearly", "2027-01-01T00:00:00+00:00"),
    ("cron", "2026-02-01T00:00:00+00:00"),
])
def test_cycles(cycle, expected):
    dt_util.set_default_time_zone(timezone.utc)
    actual = meter.next_reset(settings(cycle=cycle), datetime(2026, 1, 10, 12, tzinfo=timezone.utc))
    assert (actual.isoformat() if actual else None) == expected


@pytest.mark.parametrize("start,cycle,after,expected", [
    ("2026-01-31T00:00:00", "monthly", "2026-01-31", "2026-02-28"),
    ("2026-01-31T00:00:00", "monthly", "2026-02-28", "2026-03-31"),
    ("2024-02-29T00:00:00+00:00", "yearly", "2027-03-01", "2028-02-29"),
    ("2026-01-15T00:00:00", "days", "2026-01-15", "2026-02-14"),
    ("2026-01-15T00:00:00", "days", "2026-01-01", "2026-01-15"),
    ("2026-01-15T00:00:00", "monthly", "2025-01-01", "2026-01-15"),
])
def test_anchored_schedules(start, cycle, after, expected):
    dt_util.set_default_time_zone(timezone.utc)
    now = datetime.fromisoformat(after).replace(tzinfo=timezone.utc)
    assert meter.next_reset(settings(start=start, cycle=cycle), now).date().isoformat() == expected


def test_offset_and_dst():
    dt_util.set_default_time_zone(dt_util.get_time_zone("America/New_York"))
    try:
        config = settings(cycle="days", days=1, start="2026-03-07T02:30:00", offset=0)
        result = meter.next_reset(config, dt_util.parse_datetime("2026-03-07T08:00:00Z"))
        assert result.isoformat() == "2026-03-08T03:30:00-04:00"
        result = meter.next_reset(config, result)
        assert result.isoformat() == "2026-03-09T02:30:00-04:00"
    finally:
        dt_util.set_default_time_zone(timezone.utc)
    result = meter.next_reset(settings(offset=60), dt_util.parse_datetime("2026-02-01T00:00:00Z"))
    assert result.hour == 1


@pytest.mark.parametrize("usage,expected", [(0, 10), (50, 110), (100, 210), (150, 360), (200, 510), (250, 710), (-1, 10)])
def test_progressive_cost(usage, expected):
    config = settings(rate=4, base_charge=10, tiers=[{"up_to": 100, "rate": 2}, {"up_to": 200, "rate": 3}])
    assert meter.cost(config, usage) == Decimal(expected)


def test_flat_cost():
    assert meter.cost(settings(rate="0.1"), "0.3") == Decimal("0.03")


@pytest.mark.parametrize("cycle,after,expected", [
    ("hourly", "2026-11-01T05:30:00Z", "2026-11-01T06:00:00Z"),
    ("quarter-hourly", "2026-11-01T05:50:00Z", "2026-11-01T06:00:00Z"),
    ("hourly", "2026-03-08T06:30:00Z", "2026-03-08T07:00:00Z"),
])
def test_subday_dst_boundaries(cycle, after, expected):
    original = dt_util.get_default_time_zone()
    dt_util.set_default_time_zone(dt_util.get_time_zone("America/New_York"))
    try:
        result = meter.next_reset(settings(cycle=cycle), dt_util.parse_datetime(after))
        assert dt_util.as_utc(result) == dt_util.parse_datetime(expected)
    finally:
        dt_util.set_default_time_zone(original)


@pytest.mark.parametrize("values,field", [
    ({"tariff_entity": "sensor.bad"}, "tariff_entity"),
    ({"tariff_entity": "bad id"}, "tariff_entity"),
    ({"tariff_entity": []}, "tariff_entity"),
    ({"tariff": []}, "tariff"),
    ({"tariff_entity": "select.tariff", "tariff": " "}, "tariff"),
    ({"days": "nan"}, "days"),
    ({"tiers": [{"up_to": 100, "rate": "nan"}]}, "tiers"),
])
def test_invalid_option_field_paths(values, field):
    with pytest.raises(vol.Invalid) as error:
        settings(**values)
    assert error.value.path == [meter.PREFIX + field]


def test_tariff_and_currency_normalization():
    assert settings(currency="krw", tariff_entity="select.tariff", tariff="peak")["currency"] == "KRW"


@pytest.mark.parametrize("now,expected", [
    ("2026-03-31T12:34:00Z", "2026-02-28T12:34:00Z"),
    ("2024-03-31T12:34:00Z", "2024-02-29T12:34:00Z"),
    ("2026-01-31T12:34:00Z", "2025-12-31T12:34:00Z"),
])
def test_previous_month_same_time_clamps_calendar_month_ends(now, expected):
    previous_timezone = dt_util.DEFAULT_TIME_ZONE
    try:
        dt_util.set_default_time_zone(timezone.utc)
        assert meter.previous_month_same_time(dt_util.parse_datetime(now)) == dt_util.parse_datetime(expected)
    finally:
        dt_util.set_default_time_zone(previous_timezone)


@pytest.mark.parametrize("zone,now,expected", [
    ("Asia/Seoul", "2026-03-30T16:30:00Z", "2026-02-27T16:30:00Z"),
    # April's 02:30 maps into March's missing hour and normalizes to 03:30.
    ("America/New_York", "2026-04-08T06:30:00Z", "2026-03-08T07:30:00Z"),
    # Prefer the first occurrence of a repeated historical wall-clock time.
    ("America/New_York", "2026-12-01T06:30:00Z", "2026-11-01T05:30:00Z"),
])
def test_previous_month_local_time_dst(zone, now, expected):
    from zoneinfo import ZoneInfo
    previous_timezone = dt_util.DEFAULT_TIME_ZONE
    try:
        dt_util.set_default_time_zone(ZoneInfo(zone))
        actual = meter.previous_month_same_time(dt_util.parse_datetime(now))
        assert dt_util.as_utc(actual) == dt_util.parse_datetime(expected)
    finally:
        dt_util.set_default_time_zone(previous_timezone)


@pytest.mark.parametrize("cycle,keys", [
    ("none", {"cycle", "timezone"}),
    ("cron", {"cycle", "cron", "timezone"}),
    ("monthly", {"cycle", "start", "offset", "timezone"}),
    ("days", {"cycle", "start", "offset", "days", "timezone"}),
])
def test_schedule_profile_ignores_irrelevant_options(cycle, keys):
    config = settings(cycle=cycle, start="2026-01-01T00:00:00")
    profile = meter.schedule_profile(config)
    assert set(profile) == keys
    assert meter.schedule_profile({**config, "rate": 900}) == profile


@pytest.mark.parametrize("start,expected", [("", True), ("2025-01-01T00:00:00", True), ("2027-01-01T00:00:00Z", False)])
def test_collection_start_gate(start, expected):
    assert meter.collection_started(settings(start=start), dt_util.parse_datetime("2026-01-01T00:00:00Z")) is expected
