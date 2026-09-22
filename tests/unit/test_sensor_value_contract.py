"""Native SensorEntity values follow the documented HA type contract."""

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import Mock

import pytest
from homeassistant.core import State

from custom_components.virtual_layer.sensor import VirtualSensor

pytestmark = pytest.mark.unit


def make_sensor(config):
    sensor = VirtualSensor(config, False)
    sensor._create_state(config)
    return sensor


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "not a number", float("nan"), {}, []])
def test_bad_numeric_initial_values_become_unknown(value):
    sensor = make_sensor({"name": "PM", "class": "pm25", "initial_value": value})
    assert sensor.native_value is None


@pytest.mark.parametrize("device_class", ["timestamp", "uptime"])
def test_timestamp_and_uptime_are_aware_datetimes(device_class):
    sensor = make_sensor({"name": "Boot", "class": device_class, "initial_value": "2026-09-11T12:00:00+09:00"})
    assert sensor.native_value == datetime(2026, 9, 11, 3, tzinfo=timezone.utc)


def test_date_never_returns_datetime_subclass():
    sensor = make_sensor({"name": "Date", "class": "date", "initial_value": datetime(2026, 9, 11, 12, tzinfo=timezone.utc)})
    assert type(sensor.native_value) is date
    assert sensor.native_value == date(2026, 9, 11)


@pytest.mark.parametrize(
    "value", [1.5, "2.5", float("nan"), float("inf"), True, -1, 6]
)
def test_display_precision_does_not_silently_truncate(value):
    sensor = VirtualSensor({"name": "PM", "class": "pm25"}, False)
    with pytest.raises(ValueError):
        sensor._apply_native_template_value("suggested_display_precision", value)


def test_display_precision_defaults_to_five_decimal_places():
    sensor = make_sensor({"name": "PM", "class": "pm25"})

    assert sensor.suggested_display_precision == 5


def test_text_information_sensor_is_not_forced_to_numeric_by_precision():
    sensor = make_sensor({"name": "Info", "initial_value": "configured"})
    assert sensor.suggested_display_precision is None
    assert sensor.state == "configured"


@pytest.mark.parametrize("native", [True, False])
@pytest.mark.parametrize("value", ["nan", "inf", "bad", [], {}])
def test_invalid_template_measurement_clears_previous_reading(value, native):
    sensor = make_sensor({"name": "PM", "class": "pm25", "initial_value": 12})
    sensor._schedule_state_update = lambda: None
    if native:
        sensor._apply_native_template_value("state", value)
    else:
        sensor.set_state(value)
    sensor._native_templates_applied()
    assert sensor.native_value is None
    sensor._apply_native_template_value("state", 3)
    sensor._native_templates_applied()
    assert sensor.native_value == 3


def test_numeric_sensor_does_not_publish_enum_options():
    sensor = make_sensor({"name": "PM", "class": "pm25", "initial_value": 12})
    sensor._apply_native_template_value("options", ["low", "high"])
    sensor._native_templates_applied()
    assert sensor.options is None
    assert sensor.native_value == 12


def test_utility_meter_accumulates_source_deltas_and_ignores_source_reset():
    sensor = make_sensor({
        "name": "Monthly energy",
        "initial_value": "0",
        "utility_meter_enabled": True,
        "utility_meter_cycle": "monthly",
    })
    sensor._schedule_state_update = Mock()
    sensor._utility_meter_last_source = Decimal("100")

    sensor._async_utility_meter_reading(State("sensor.energy", "103.5"))
    assert sensor.native_value == Decimal("3.5")

    # A physical total-increasing meter can reset; that reset is not usage.
    sensor._async_utility_meter_reading(State("sensor.energy", "1"))
    assert sensor.native_value == Decimal("3.5")

    sensor._async_utility_meter_reading(State("sensor.energy", "2.25"))
    assert sensor.native_value == Decimal("4.75")


def test_utility_meter_adjustment_and_period_reset_are_finite_and_persistent():
    sensor = make_sensor({
        "name": "Monthly energy",
        "initial_value": "2",
        "utility_meter_enabled": True,
    })
    sensor._schedule_state_update = Mock()
    sensor.adjust_utility_meter(Decimal("1.25"))
    assert sensor.native_value == Decimal("3.25")
    sensor.calibrate_utility_meter(Decimal("9.5"))
    assert sensor.native_value == Decimal("9.5")
    with pytest.raises(ValueError):
        sensor.adjust_utility_meter(Decimal("-1"))
    with pytest.raises(ValueError):
        sensor.adjust_utility_meter(Decimal("Infinity"))
    with pytest.raises(ValueError):
        sensor.calibrate_utility_meter(Decimal("-1"))

    sensor._async_utility_meter_reset(None)
    assert sensor.native_value == Decimal("0")
    assert sensor.extra_state_attributes["last_period"] == "9.5"


@pytest.mark.parametrize("missing", [None, "", "  ", "None", "unknown", "unavailable"])
def test_missing_unit_preserves_last_normalized_unit(missing):
    sensor = make_sensor({"name": "PM", "class": "pm10", "initial_value": 12})
    sensor._apply_native_template_value("native_unit_of_measurement", "µg/m3")
    sensor._apply_native_template_value("native_unit_of_measurement", missing)
    sensor._native_templates_applied()
    assert sensor.native_unit_of_measurement == "μg/m³"
    assert sensor.native_value == 12


def test_unitless_sensor_remains_unitless():
    sensor = make_sensor({"name": "Ratio", "initial_value": 0.5})
    sensor._apply_native_template_value("native_unit_of_measurement", None)
    assert sensor.native_unit_of_measurement is None


@pytest.mark.parametrize("missing", [None, "", "  ", "None", "unknown", "unavailable"])
@pytest.mark.parametrize("device_class", ["pm1", "pm25", "pm4", "pm10"])
def test_first_missing_unit_preserves_particulate_default(missing, device_class):
    sensor = make_sensor({"name": "Particulate", "class": device_class, "initial_value": 12})
    sensor._apply_native_template_value("native_unit_of_measurement", missing)
    sensor._native_templates_applied()
    assert sensor.native_unit_of_measurement == "μg/m³"
    assert sensor.native_value == 12


@pytest.mark.parametrize("missing", [None, "", "  ", "None", "unknown", "unavailable"])
@pytest.mark.parametrize("configured", [None, "mdi:blur"])
def test_missing_native_icon_uses_configured_or_pm10_icon(missing, configured):
    sensor = make_sensor({"name": "PM 10.0", "class": "pm10", "icon": configured})
    sensor._apply_native_template_value("icon", "mdi:weather-dust")
    sensor._apply_native_template_value("icon", missing)
    assert sensor.icon == (configured or "mdi:air-filter")
