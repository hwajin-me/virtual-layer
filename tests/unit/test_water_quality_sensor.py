"""Water measurements use native HA units without chemical assumptions."""

import pytest
from homeassistant.helpers.template import Template

from custom_components.virtual_layer.air_quality_options import normalize_unit
from custom_components.virtual_layer.config_flow import (
    _apply_sensor_conversion_defaults,
    _sensor_conversion_choices,
)
from custom_components.virtual_layer.sensor import VirtualSensor

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("device_class,unit,value,expected", [
    ("ph", None, 7.2, 7.2),
    ("ph", None, -0.5, -0.5),
    ("ph", None, 14.5, 14.5),
    ("ph", "pH", 7, None),
    ("conductivity", None, 500, 500),
    ("conductivity", "uS/cm", 500, 500),
    ("conductivity", "μS/cm", 500, 500),
    ("conductivity", "mS/cm", 0.5, 0.5),
    ("conductivity", "µS/cm", -1, None),
    ("conductivity", "ppm", 500, None),
    (None, "ppm", 250, 250),
    (None, "NTU", 1.5, 1.5),
    (None, "mg/L", 8, 8),
    ("voltage", "mV", -200, -200),
])
def test_water_measurement_runtime(device_class, unit, value, expected):
    config = {"name": "Water", "initial_value": value, "state_class": "measurement"}
    if device_class:
        config["class"] = device_class
    if unit:
        config["unit_of_measurement"] = unit
    sensor = VirtualSensor(config, False)
    sensor._schedule_state_update = lambda: None
    sensor._create_state(config)
    assert sensor.native_value == expected
    for invalid in ("nan", "inf", "unavailable", True):
        sensor.set_state(invalid)
        assert sensor.native_value is None
    sensor.set_state(value)
    assert sensor.native_value == expected


def test_conductivity_default_survives_missing_unit_template():
    sensor = VirtualSensor({"name": "EC", "class": "conductivity"}, False)
    sensor._apply_native_template_value("native_unit_of_measurement", None)
    assert sensor.native_unit_of_measurement == "μS/cm"


@pytest.mark.parametrize("alias,expected", [
    ("uS/cm", "μS/cm"), ("μS / cm", "μS/cm"),
    ("mS / cm", "mS/cm"), ("mg/l", "mg/L"), ("ntu", "NTU"),
    ("MS/cm", "MS/cm"), ("ppm", "ppm"), ("ppt", "ppt"),
])
def test_water_unit_aliases_do_not_convert_values(alias, expected):
    assert normalize_unit(alias) == expected


def test_conductivity_composite_converts_units_and_follows_sources(hass):
    sources = ["sensor.ec_a", "sensor.ec_b"]
    hass.states.async_set(sources[0], "500", {"device_class": "conductivity", "unit_of_measurement": "uS/cm"})
    hass.states.async_set(sources[1], "1.5", {"device_class": "conductivity", "unit_of_measurement": "mS/cm"})
    choice = _sensor_conversion_choices(sources, hass)["state"]
    defaults = _apply_sensor_conversion_defaults(hass, {}, choice)
    template = Template(defaults["value_template"], hass)
    assert template.async_render() == pytest.approx(1000)
    hass.states.async_set(sources[1], "0.001", {"device_class": "conductivity", "unit_of_measurement": "S/cm"})
    assert template.async_render() == pytest.approx(750)
    hass.states.async_set(sources[1], "1000", {"device_class": "conductivity", "unit_of_measurement": "ppm"})
    assert template.async_render() == pytest.approx(500)
    hass.states.async_set(sources[1], "unavailable")
    assert template.async_render() == pytest.approx(500)
    hass.states.async_set(sources[0], "-1", {"device_class": "conductivity", "unit_of_measurement": "μS/cm"})
    assert template.async_render() is None
    assert Template(defaults["availability_template"], hass).async_render() is False
