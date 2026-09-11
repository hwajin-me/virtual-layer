"""Native SensorEntity values follow the documented HA type contract."""

from datetime import date, datetime, timezone

import pytest

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


@pytest.mark.parametrize("value", [1.5, "2.5", float("nan"), float("inf"), True, -1])
def test_display_precision_does_not_silently_truncate(value):
    sensor = VirtualSensor({"name": "PM", "class": "pm25"}, False)
    with pytest.raises(ValueError):
        sensor._apply_native_template_value("suggested_display_precision", value)


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
