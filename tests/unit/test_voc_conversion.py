"""VOC helpers use an explicit mixture approximation across unit families."""

import pytest
import yaml
from homeassistant.helpers.template import Template

from custom_components.virtual_layer import air_quality_options as aq
from custom_components.virtual_layer.config_flow import (
    CONF_DOMAIN_OPTIONS_JSON,
    _apply_sensor_conversion_defaults,
    _reference_entity_defaults,
    _sensor_conversion_choices,
)
from homeassistant.const import CONF_VALUE_TEMPLATE

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("classes", [aq.VOC_QUANTITIES, (None, None)])
def test_mixed_voc_classes_and_live_unit_changes(hass, classes):
    sources = ["sensor.tvoc_parts", "sensor.tvoc_mass"]
    for source, value, unit, cls in zip(sources, [100, 0.45], ["ppb", "mg/m3"], classes):
        hass.states.async_set(source, str(value), {"device_class": cls, "unit_of_measurement": unit})
    defaults = _apply_sensor_conversion_defaults(hass, {}, _sensor_conversion_choices(sources, hass)["state"])
    options = yaml.safe_load(defaults[CONF_DOMAIN_OPTIONS_JSON])
    assert options["class"] == "volatile_organic_compounds"
    assert options["unit_of_measurement"] == "mg/m³"
    helper = Template(defaults[CONF_VALUE_TEMPLATE], hass)
    assert helper.async_render() == pytest.approx(0.45)
    hass.states.async_set(sources[0], "0.2", {"unit_of_measurement": "ppm"})
    assert helper.async_render() == pytest.approx(0.675)
    hass.states.async_set(sources[0], "100", {"unit_of_measurement": "invalid"})
    assert helper.async_render() == pytest.approx(0.45)
    hass.states.async_set(sources[1], "unavailable", {"unit_of_measurement": "mg/m³"})
    assert helper.async_render() is None
    assert Template(defaults["availability_template"], hass).async_render() is False


def test_single_voc_reference_defaults_use_mass_class(hass):
    hass.states.async_set("sensor.tvoc", "100", {"device_class": aq.VOC_QUANTITIES[1], "unit_of_measurement": "ppb"})
    defaults = _reference_entity_defaults(hass, ["sensor.tvoc"], "sensor")
    assert Template(defaults[CONF_VALUE_TEMPLATE], hass).async_render() == pytest.approx(0.45)
    options = yaml.safe_load(defaults[CONF_DOMAIN_OPTIONS_JSON])
    assert options["class"] == aq.VOC_QUANTITIES[0]
    assert options["unit_of_measurement"] == "mg/m³"


def test_voc_overflow_does_not_generate_infinite_initial_value(hass):
    hass.states.async_set("sensor.tvoc", "1e308", {"device_class": aq.VOC_QUANTITIES[1], "unit_of_measurement": "ppm"})
    defaults = _reference_entity_defaults(hass, ["sensor.tvoc"], "sensor")
    # Reference sensors use the finite, domain-valid fallback while the
    # conversion template is unavailable.
    assert defaults["initial_value"] == "0"
    assert Template(defaults["availability_template"], hass).async_render() is False


@pytest.mark.parametrize("unit,value", [("ppb", "100"), ("ppm", "0.1"), ("mg/m³", "0.45"), ("ug/m3", "450")])
@pytest.mark.parametrize("target,boundaries", [("mg/m³", [0.2, 0.3, 0.5, 0.75, 0.95]), ("ppb", [40, 60, 120, 160, 200])])
def test_voc_bidirectional_recipe_conversion(hass, unit, value, target, boundaries):
    hass.states.async_set("sensor.tvoc", value, {"device_class": aq.VOC_QUANTITIES[1], "unit_of_measurement": unit})
    recipe = aq.normalize({"mode": "measurement", "sources": ["sensor.tvoc"], "quantity": aq.VOC_QUANTITIES[0], "unit": target, "thresholds": boundaries})
    assert Template(aq.generate(recipe), hass).async_render() == "moderate"


def test_voc_prefill_and_saved_recipe_preservation(hass):
    sources = ["sensor.tvoc_parts", "sensor.tvoc_mass"]
    for source, cls, value, unit in zip(sources, aq.VOC_QUANTITIES, [100, 0.45], ["ppb", "mg/m³"]):
        hass.states.async_set(source, str(value), {"device_class": cls, "unit_of_measurement": unit})
    states = [hass.states.get(s) for s in sources]
    defaults, _ = aq.prefill_measurement({}, states)
    assert defaults["unit"] == "mg/m³"
    assert defaults["thresholds"] == [0.2, 0.3, 0.5, 0.75, 0.95]
    saved = {"unit": "ppb", "quantity": aq.VOC_QUANTITIES[1], "thresholds": [1, 2, 3, 4, 5]}
    assert aq.prefill_measurement(saved, states)[0] == saved
    recipe = aq.automatic_recipe(sources, states)
    assert aq.automatic_recipe(sources, states, recipe) == recipe


def test_other_gases_do_not_get_voc_conversion(hass):
    hass.states.async_set("sensor.co2", "100", {"device_class": "carbon_dioxide", "unit_of_measurement": "ppb"})
    recipe = aq.normalize({"mode": "measurement", "sources": ["sensor.co2"], "quantity": "carbon_dioxide", "unit": "mg/m³", "thresholds": [1, 2, 3, 4, 5]})
    assert Template(aq.generate(recipe), hass).async_render() == "unknown"


@pytest.mark.parametrize("mass,grade", [(0.2, "fair"), (0.3, "moderate"), (0.5, "poor"), (0.75, "very_poor"), (0.95, "extremely_poor")])
def test_ppb_conversion_preserves_mass_boundary_equality(hass, mass, grade):
    source = "sensor.tvoc"
    hass.states.async_set(source, str(mass / 0.0045), {"device_class": aq.VOC_QUANTITIES[1], "unit_of_measurement": "ppb"})
    recipe = aq.automatic_recipe([source], [hass.states.get(source)])
    assert Template(aq.generate(recipe), hass).async_render() == grade


@pytest.mark.parametrize("target", ["mg/m³", "ppb"])
async def test_measurement_flow_accepts_mixed_voc_classes(hass, target):
    from custom_components.virtual_layer.config_flow import VirtualFlowHandler

    sources = ["sensor.tvoc_parts", "sensor.tvoc_mass"]
    for source, cls, value, unit in zip(sources, reversed(aq.VOC_QUANTITIES), [100, 0.45], ["ppb", "mg/m³"]):
        hass.states.async_set(source, str(value), {"device_class": cls, "unit_of_measurement": unit})
    flow = VirtualFlowHandler()
    flow.hass = hass
    flow._entity_defaults = {"platform": "air_quality"}
    await flow.async_step_air_quality({"mode": "measurement"})
    result = await flow.async_step_air_quality_sources({"sources": sources, "unit": target, "quantity": "any"})
    assert result["step_id"] == "air_quality_logic"
    assert not result.get("errors")
