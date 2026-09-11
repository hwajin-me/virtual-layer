"""Air-quality recipes must generate safe, dynamic, editable helpers."""

import json
from pathlib import Path

import pytest
import voluptuous as vol
from homeassistant.helpers.template import Template

from custom_components.virtual_layer import air_quality_options as aq

pytestmark = pytest.mark.unit


def measurement(**overrides):
    return {
        "mode": "measurement",
        "sources": ["sensor.pm25"],
        "unit": "μg/m³",
        "thresholds": [10, 20, 30, 40, 50],
        "levels": list(aq.LEVELS),
        **overrides,
    }


@pytest.mark.parametrize("quantity,name", [("pm4", "PM4.0"), ("nitrous_oxide", "N₂O")])
def test_documented_pollutants_have_editable_display_defaults(hass, quantity, name):
    source = "sensor.documented_pollutant"
    hass.states.async_set(source, "3", {"friendly_name": name, "unit_of_measurement": "μg/m³"})
    assert aq.infer_quantity(hass.states.get(source)) == quantity
    automatic = aq.automatic_recipe([source], [hass.states.get(source)])
    assert automatic["measurements"]
    assert Template(aq.generate(automatic), hass).async_render() == "good"
    hass.states.async_set(source, "3", {"device_class": quantity, "unit_of_measurement": "μg/m³"})
    recipe = measurement(sources=[source], quantity=quantity)
    assert Template(aq.generate(recipe), hass).async_render() == "good"
    with pytest.raises(vol.Invalid):
        aq.normalize({**recipe, "unit": "ppm"})


def test_every_named_quantity_has_a_starter_profile():
    assert set(aq.QUANTITIES) - {"any"} == set(aq.STARTER_PROFILES)


@pytest.mark.parametrize("mode", ["automatic", "source"])
def test_retained_stale_categories_do_not_outvote_live_sources(hass, mode):
    sources = ["sensor.cached", "sensor.live"]
    hass.states.async_set(sources[0], "extremely_poor", {"air_quality_stale": True})
    hass.states.async_set(sources[1], "good")
    recipe = {"mode": mode, "sources": sources, "aggregation": "worst", "missing": "skip"}
    assert Template(aq.generate(recipe), hass).async_render() == "good"


@pytest.mark.parametrize("quantity", list(aq.STARTER_PROFILES))
def test_every_starter_prefills_and_renders_all_six_bands(hass, quantity):
    unit, boundaries, _ = aq.STARTER_PROFILES[quantity]
    source = "sensor.profile"
    attrs = {"device_class": quantity, "unit_of_measurement": "" if quantity == "aqi" else unit}
    hass.states.async_set(source, "0", attrs)
    defaults, _ = aq.prefill_measurement({"sources": [source]}, [hass.states.get(source)])
    recipe = aq.normalize({"mode": "measurement", **defaults})
    helper = Template(aq.generate(recipe), hass)
    samples = [boundaries[0] / 2, *[(a + b) / 2 for a, b in zip(boundaries, boundaries[1:])], boundaries[-1] + 1]
    for value, level in zip(samples, aq.LEVELS, strict=True):
        hass.states.async_set(source, str(value), attrs)
        assert helper.async_render() == level


@pytest.mark.parametrize("quantity", ["pm4", "nitrous_oxide"])
def test_new_defaults_convert_units_and_preserve_custom_thresholds(hass, quantity):
    source = "sensor.profile"
    hass.states.async_set(source, "0.003", {"device_class": quantity, "unit_of_measurement": "mg/m3"})
    state = hass.states.get(source)
    defaults, label = aq.prefill_measurement({}, [state])
    assert defaults["thresholds"] == pytest.approx([v / 1000 for v in aq.STARTER_PROFILES[quantity][1]])
    assert "not" in label
    custom = {"thresholds": [1, 2, 3, 4, 5], "unit": "mg/m³"}
    assert aq.prefill_measurement(custom, [state])[0]["thresholds"] == custom["thresholds"]
    previous = aq.automatic_recipe([source], [state])
    previous["measurements"][0]["thresholds"] = custom["thresholds"]
    assert aq.automatic_recipe([source], [state], previous)["measurements"] == previous["measurements"]


@pytest.mark.parametrize("quantity,unit,value,expected", [
    ("formaldehyde", "mg/m3", "0.003", "good"),
    ("formaldehyde", " mg/m^3 ", "0.003", "good"),
    ("formaldehyde", "ug/m3", "3", "good"),
    ("pm25", "µg/m3", "40", "moderate"),
    ("pm25", "μg/m^3", "40", "moderate"),
    ("pm25", " mg/m³ ", "0.04", "moderate"),
    ("radon", "Bq/m3", "54.07", "fair"),
    ("radon", "Bq/m^3", "54.07", "fair"),
    ("volatile_organic_compounds_parts", " ppb ", "20", "good"),
])
def test_equivalent_unit_spellings_work_in_prefill_and_live_templates(hass, quantity, unit, value, expected):
    source_id = "sensor.pollutant"
    hass.states.async_set(source_id, value, {"device_class": quantity, "unit_of_measurement": unit})
    original = hass.states.get(source_id)
    recipe = aq.automatic_recipe([source_id], [original])
    assert len(recipe["measurements"]) == 1
    assert recipe["measurements"][0]["unit"] in aq.UNITS
    helper = Template(aq.generate(recipe), hass)
    assert helper.async_render() == expected
    assert hass.states.get(source_id) is original
    # The same saved helper follows spelling changes without a reload.
    hass.states.async_set(source_id, value, {"device_class": quantity, "unit_of_measurement": aq.normalize_unit(unit)})
    assert helper.async_render() == expected
    hass.states.async_set(source_id, value, {"device_class": quantity, "unit_of_measurement": "m³"})
    assert helper.async_render() == "unknown"


@pytest.mark.parametrize("unit", ["m³", "m3", "Mg/m3", "mg/m2", "mg/L", False, [], {}])
def test_unit_aliases_do_not_fabricate_concentrations(hass, unit):
    hass.states.async_set("sensor.formaldehyde", "0.003", {"device_class": "formaldehyde", "unit_of_measurement": unit})
    recipe = aq.automatic_recipe(["sensor.formaldehyde"], [hass.states.get("sensor.formaldehyde")])
    assert not recipe["measurements"]
    assert Template(aq.generate(recipe), hass).async_render() == "unknown"


def test_manual_alias_unit_normalizes_without_changing_custom_thresholds():
    recipe = aq.normalize(measurement(unit="ug/m3"))
    assert recipe["unit"] == "μg/m³"
    assert recipe["thresholds"] == [10, 20, 30, 40, 50]


def test_whitespace_tvoc_parts_unit_is_not_misclassified_as_mass(hass):
    hass.states.async_set("sensor.etvoc", "20", {"unit_of_measurement": " ppb "})
    assert aq.infer_quantity(hass.states.get("sensor.etvoc")) == "volatile_organic_compounds_parts"


@pytest.mark.parametrize("unit", ["mg/m3", "mg/m^3", " mg/m³ "])
def test_native_concentration_helpers_share_unit_aliases(hass, unit):
    from custom_components.virtual_layer.config_flow import _native_source_template
    hass.states.async_set("sensor.pm25", "0.003", {"device_class": "pm25", "unit_of_measurement": unit})
    helper = _native_source_template("sensor.pm25", hass.states.get("sensor.pm25"), "particulate_matter_2_5", "air_quality")
    assert Template(helper, hass).async_render() == 3


def test_composite_conversion_keeps_si_prefix_case():
    from custom_components.virtual_layer.config_flow import _sensor_unit_conversion_profile
    assert _sensor_unit_conversion_profile(["mg/m^3", "ug/m3"]) == ("μg/m³", (1000, 1))
    assert _sensor_unit_conversion_profile(["Mg/m³", "μg/m³"]) is None


@pytest.mark.parametrize("missing,expected", [("skip", "good"), ("unknown", "unknown")])
def test_automatic_custom_profile_is_authoritative_and_missing_is_explicit(hass, missing, expected):
    hass.states.async_set("sensor.pm25", "15", {"device_class": "pm25", "unit_of_measurement": "μg/m³", "air_quality": "extremely_poor"})
    hass.states.async_set("sensor.other", "unavailable")
    recipe = {"mode": "automatic", "sources": ["sensor.pm25", "sensor.other"], "missing": missing,
              "measurements": [measurement(thresholds=[20, 30, 40, 50, 60])]}
    assert Template(aq.generate(recipe), hass).async_render() == expected


def test_leaf_expansion_deduplicates_and_rejects_cycles():
    assert aq.expand_sources(["sensor.a", "sensor.b"], {
        "sensor.a": ["sensor.b", "sensor.c"], "sensor.b": ["sensor.c", "sensor.d"],
    }) == ["sensor.c", "sensor.d"]
    with pytest.raises(vol.Invalid):
        aq.expand_sources(["sensor.a"], {"sensor.a": ["sensor.b"], "sensor.b": ["sensor.a"]})


async def test_leaf_scope_reopens_roots_and_refreshes_changed_dependencies(hass):
    from custom_components.virtual_layer.config_flow import _AirQualityLogicFlow
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    class Flow(_AirQualityLogicFlow):
        def async_show_form(self, **kwargs):
            return kwargs

    entry = MockConfigEntry(domain="virtual_layer", options={"devices": {"Room": [{
        "platform": "sensor", "entity_id": "sensor.composite", "source_entities": ["sensor.first"]}]}})
    entry.add_to_hass(hass)
    flow = Flow()
    flow.hass = hass
    flow._entity_defaults = {"platform": "sensor"}
    flow._aq_pending = {"mode": "automatic", "sources": ["sensor.composite"]}
    await flow.async_step_air_quality_scope({"scope": "leaves", "sources": ["sensor.composite"], "missing": "skip"})
    saved = aq.normalize(flow._aq_pending)
    flow._aq_pending = aq.automatic_recipe(saved["sources"], [None], saved)
    form = await flow.async_step_air_quality_scope()
    values = form["data_schema"]({})
    assert values["sources"] == ["sensor.composite"]
    hass.config_entries.async_update_entry(entry, options={"devices": {"Room": [{
        "platform": "sensor", "entity_id": "sensor.composite", "source_entities": ["sensor.second"]}]}})
    await flow.async_step_air_quality_scope(values)
    assert flow._aq_pending["sources"] == ["sensor.second"]


@pytest.mark.parametrize("roots", [None, "sensor.root", [], ["not an id"], ["sensor.root"] * 65, [True]])
def test_invalid_leaf_roots_are_rejected(roots):
    with pytest.raises(vol.Invalid):
        aq.normalize({"mode": "automatic", "scope": "leaves", "sources": ["sensor.leaf"], "source_roots": roots})


@pytest.mark.parametrize("sources", [["sensor.pm25", "sensor.pm25"], ["sensor.pm25"]])
async def test_scope_deduplicates_before_building_measurement_profiles(hass, sources):
    from custom_components.virtual_layer.config_flow import _AirQualityLogicFlow

    class Flow(_AirQualityLogicFlow):
        def async_show_form(self, **kwargs):
            return kwargs

    hass.states.async_set("sensor.pm25", "15", {"device_class": "pm25", "unit_of_measurement": "μg/m³"})
    flow = Flow()
    flow.hass = hass
    flow._entity_defaults = {"platform": "air_quality"}
    flow._aq_pending = {"mode": "automatic", "sources": []}
    result = await flow.async_step_air_quality_scope({"scope": "sources", "sources": sources})
    assert result["step_id"] == "air_quality_source_rules"
    assert len(flow._aq_pending["measurements"]) == 1


async def test_reset_combined_profile_before_entity_exists_keeps_inferred_defaults(hass):
    from custom_components.virtual_layer.config_flow import _AirQualityLogicFlow, CONF_SOURCE_ENTITIES_TEXT

    class Flow(_AirQualityLogicFlow):
        def async_show_form(self, **kwargs):
            return kwargs

    hass.states.async_set("sensor.pm25", "15", {"device_class": "pm25", "unit_of_measurement": "μg/m³"})
    flow = Flow()
    flow.hass = hass
    flow._entity_defaults = {"platform": "sensor", "entity_id": "sensor.new_combined", CONF_SOURCE_ENTITIES_TEXT: "sensor.pm25"}
    flow._aq_pending = {"mode": "automatic", "sources": ["sensor.pm25"]}
    await flow.async_step_air_quality_scope({"scope": "combined", "missing": "skip"})
    original = flow._aq_pending["measurements"][0]
    await flow.async_step_air_quality_source_rules({"source": "sensor.new_combined", "action": "reset"})
    assert flow._aq_pending["measurements"] == [original]


@pytest.mark.parametrize("platform", ["sensor", "air_quality"])
@pytest.mark.parametrize("source_unit", ["μg/m³", "ug/m^3"])
async def test_per_source_editor_preserves_other_profiles_and_original_templates(hass, platform, source_unit):
    from custom_components.virtual_layer.config_flow import (
        _AirQualityLogicFlow, CONF_SOURCE_ENTITIES_TEXT, CONF_NATIVE_VALUE_TEMPLATES,
    )
    from custom_components.virtual_layer.const import CONF_AIR_QUALITY_LOGIC

    class Flow(_AirQualityLogicFlow):
        def async_show_form(self, **kwargs):
            return kwargs

        async def async_step_entity(self, user_input=None):
            return {"step_id": "entity", "defaults": self._entity_defaults}

    for source in ("sensor.pm25", "sensor.other"):
        hass.states.async_set(source, "15", {"device_class": "pm25", "unit_of_measurement": source_unit})
    flow = Flow()
    flow.hass = hass
    flow._entity_defaults = {"platform": platform, "entity_id": platform + ".combined",
        CONF_SOURCE_ENTITIES_TEXT: "sensor.pm25\nsensor.other", "configure_air_quality_sources": True,
        "value_template": "{{ 15 }}", "availability_template": "{{ true }}",
        CONF_NATIVE_VALUE_TEMPLATES: {"device_class": "{{ 'pm25' }}"} if platform == "sensor" else {}}
    form = await flow._aq_mode_step()
    assert form["step_id"] == "air_quality_scope"
    form = await flow.async_step_air_quality_scope({"scope": "sources", "sources": ["sensor.pm25", "sensor.other"], "missing": "skip"})
    assert form["step_id"] == "air_quality_source_rules"
    original_other = dict(flow._aq_pending["measurements"][1])
    form = await flow.async_step_air_quality_source_rules({"source": "sensor.pm25", "action": "edit"})
    assert form["step_id"] == "air_quality_setup"
    form = await flow._aq_setup_step({"sources": ["sensor.other"], "unit": "μg/m³"})
    assert form["errors"]
    form = await flow._aq_setup_step({"sources": ["sensor.pm25"], "unit": "μg/m³",
        **{f"boundary_{i+1}": value for i, value in enumerate([20, 30, 40, 50, 60])}})
    assert form["step_id"] == "air_quality_source_rules"
    result = await flow.async_step_air_quality_source_rules({"action": "continue"})
    assert result["step_id"] == "entity"
    recipe = result["defaults"][CONF_AIR_QUALITY_LOGIC]
    profiles = {item["sources"][0]: item for item in recipe["measurements"]}
    assert profiles["sensor.pm25"]["thresholds"] == [20, 30, 40, 50, 60]
    assert profiles["sensor.other"] == original_other
    assert result["defaults"]["value_template"] == "{{ 15 }}"
    assert result["defaults"]["availability_template"] == "{{ true }}"
    assert not flow._aq_needs_setup(result["defaults"])
    if platform == "sensor":
        assert {key: value for key, value in result["defaults"][CONF_NATIVE_VALUE_TEMPLATES].items() if value} == {"device_class": "{{ 'pm25' }}"}


def test_combined_profile_rebind_preserves_thresholds():
    recipe = {"mode": "automatic", "scope": "combined", "sources": ["sensor.pm25"], "measurements": [measurement()]}
    rebound = aq.rebind_combined(recipe, "sensor.renamed")
    assert rebound["sources"] == ["sensor.renamed"]
    assert rebound["measurements"][0]["sources"] == ["sensor.renamed"]
    assert rebound["measurements"][0]["thresholds"] == [10, 20, 30, 40, 50]


@pytest.mark.parametrize("missing,available", [("skip", True), ("unknown", False)])
async def test_automatic_availability_tracks_selected_missing_policy(hass, missing, available):
    from custom_components.virtual_layer.config_flow import _AirQualityLogicFlow, _reference_entity_defaults

    class Flow(_AirQualityLogicFlow):
        async def async_step_entity(self, user_input=None):
            return self._entity_defaults

    hass.states.async_set("air_quality.first", "good")
    hass.states.async_set("air_quality.second", "unavailable")
    sources = ["air_quality.first", "air_quality.second"]
    flow = Flow()
    flow.hass = hass
    flow._aq_edit = False
    flow._entity_defaults = _reference_entity_defaults(hass, sources, "air_quality")
    result = await flow._aq_finish({"mode": "automatic", "sources": sources, "missing": missing})
    assert Template(result["availability_template"], hass).async_render() is available


def test_dangling_device_parent_is_not_reintroduced_into_entity_form(hass):
    from copy import deepcopy
    from custom_components.virtual_layer.config_flow import _with_existing_device_defaults
    options = {"devices": {"Room": []}, "device_attributes": {"Room": {
        "device_id": "stable", "via_device_id": "removed-parent", "configuration_url": "-"}}}
    original = deepcopy(options)
    result = _with_existing_device_defaults({}, options, "Room", hass)
    assert result["device_via_device_id"] == ""
    assert result["device_configuration_url"] == ""
    assert options == original


async def test_add_flow_source_changes_keep_completed_custom_boundaries(hass):
    from custom_components.virtual_layer.config_flow import _AirQualityLogicFlow, CONF_SOURCE_ENTITIES_TEXT

    class Flow(_AirQualityLogicFlow):
        async def async_step_entity(self, user_input=None):
            return self._entity_defaults

    for source in ("sensor.pm25", "sensor.new"):
        hass.states.async_set(source, "15", {"device_class": "pm25", "unit_of_measurement": "μg/m³"})
    flow = Flow()
    flow.hass = hass
    flow._entity_defaults = {"platform": "air_quality", CONF_SOURCE_ENTITIES_TEXT: "sensor.new\nsensor.pm25",
        "air_quality_logic": {"mode": "automatic", "sources": ["sensor.pm25"],
                              "measurements": [measurement(thresholds=[100, 200, 300, 400, 500])]}}
    result = await flow._aq_mode_step()
    profiles = {item["sources"][0]: item for item in result["air_quality_logic"]["measurements"]}
    assert profiles["sensor.pm25"]["thresholds"] == [100, 200, 300, 400, 500]
    assert profiles["sensor.new"]["thresholds"] == [9, 35.4, 55.4, 125.4, 225.4]


@pytest.mark.parametrize("state,attrs,expected", [
    ("excellent", {}, "good"),
    ("unhealthy", {}, "very_poor"),
    ("0", {"unit_of_measurement": "mg/m³"}, "unknown"),
    ("0", {"device_class": "aqi"}, "good"),
    ("49.9", {"device_class": "aqi"}, "good"),
    ("50", {"device_class": "aqi"}, "fair"),
    ("150", {"device_class": "aqi"}, "moderate"),
    ("500", {"device_class": "aqi", "unit_of_measurement": "AQI"}, "extremely_poor"),
    ("501", {"device_class": "aqi"}, "unknown"),
    ("nan", {"device_class": "aqi"}, "unknown"),
    ("0", {"air_quality": "very poor"}, "very_poor"),
    ("unavailable", {"air_quality": "good"}, "unknown"),
    ("12", {"air_quality_index": 250}, "poor"),
])
def test_automatic_air_quality_matches_bridge_without_guessing_concentrations(hass, state, attrs, expected):
    hass.states.async_set("sensor.source", state, attrs)
    original = hass.states.get("sensor.source")
    helper = Template(aq.generate({"mode": "automatic", "sources": ["sensor.source"]}), hass)
    assert helper.async_render() == expected
    assert hass.states.get("sensor.source") is original


def test_automatic_profiles_classify_separate_pollutants_and_live_updates(hass):
    hass.states.async_set("sensor.radon", "54.07", {"device_class": "radon", "unit_of_measurement": "Bq/m³"})
    hass.states.async_set("sensor.pm25", "40", {"device_class": "pm25", "unit_of_measurement": "μg/m³"})
    sources = ["sensor.radon", "sensor.pm25"]
    originals = [hass.states.get(item) for item in sources]
    recipe = aq.automatic_recipe(sources, originals)
    assert len(recipe["measurements"]) == 2
    helper = Template(aq.generate(recipe), hass)
    assert helper.async_render() == "moderate"
    assert [hass.states.get(item) for item in sources] == originals
    hass.states.async_set("sensor.pm25", "unavailable")
    assert helper.async_render() == "fair"
    hass.states.async_set("sensor.radon", "0", {"device_class": "radon", "unit_of_measurement": "pCi/L"})
    assert helper.async_render() == "good"
    hass.states.async_set("sensor.radon", "4", {"device_class": "radon", "unit_of_measurement": "pCi/L"})
    assert helper.async_render() == "extremely_poor"
    hass.states.async_set("sensor.radon", "4", {"device_class": "radon", "unit_of_measurement": "ppm"})
    assert helper.async_render() == "unknown"


@pytest.mark.parametrize("value,grade", [(0, "good"), (49.99, "good"), (50, "fair"), (74.99, "fair"), (75, "moderate"), (99.99, "moderate"), (100, "poor"), (124.99, "poor"), (125, "very_poor"), (147.99, "very_poor"), (148, "extremely_poor"), (300, "extremely_poor")])
def test_radon_requested_default_boundaries(hass, value, grade):
    source = "sensor.radon"
    hass.states.async_set(source, str(value), {"device_class": "radon", "unit_of_measurement": "Bq/m³"})
    recipe = aq.automatic_recipe([source], [hass.states.get(source)])
    assert Template(aq.generate(recipe), hass).async_render() == grade


def test_radon_custom_boundary_rule_is_not_changed(hass):
    hass.states.async_set("sensor.radon", "50", {"device_class": "radon", "unit_of_measurement": "Bq/m³"})
    custom = measurement(sources=["sensor.radon"], quantity="radon", unit="Bq/m³", thresholds=[50, 75, 100, 125, 148])
    values, _ = aq.prefill_measurement(custom, [hass.states.get("sensor.radon")])
    assert "boundary_rule" not in values
    assert Template(aq.generate(values), hass).async_render() == "good"


@pytest.mark.parametrize("unit,factor", [("ppm", 1), ("ppb", 1000)])
@pytest.mark.parametrize("value,grade", [(599.9, "good"), (600, "fair"), (799.9, "fair"), (800, "moderate"), (1099.9, "moderate"), (1100, "poor"), (1399.9, "poor"), (1400, "very_poor"), (1999.9, "very_poor"), (2000, "extremely_poor")])
def test_co2_requested_default_boundaries(hass, unit, factor, value, grade):
    source = "sensor.co2"
    hass.states.async_set(source, str(value * factor), {"device_class": "carbon_dioxide", "unit_of_measurement": unit})
    recipe = aq.automatic_recipe([source], [hass.states.get(source)])
    assert Template(aq.generate(recipe), hass).async_render() == grade


@pytest.mark.parametrize("quantity,unit,factor,boundaries", [
    ("formaldehyde", "mg/m³", 1, [0.02, 0.04, 0.06, 0.08, 0.1]),
    ("formaldehyde", "μg/m³", 1000, [0.02, 0.04, 0.06, 0.08, 0.1]),
    ("volatile_organic_compounds", "μg/m³", 1, [200, 300, 500, 750, 950]),
    ("volatile_organic_compounds", "mg/m³", 0.001, [200, 300, 500, 750, 950]),
])
def test_indoor_display_bands_boundaries_and_custom_preservation(hass, quantity, unit, factor, boundaries):
    source = "sensor.indoor"
    attrs = {"device_class": quantity, "unit_of_measurement": unit}
    hass.states.async_set(source, "0", attrs)
    state = hass.states.get(source)
    recipe = aq.automatic_recipe([source], [state])
    helper = Template(aq.generate(recipe), hass)
    for index, boundary in enumerate(boundaries):
        for value, expected in ((boundary - 0.00001, aq.LEVELS[index]), (boundary, aq.LEVELS[index + 1])):
            hass.states.async_set(source, str(value * factor), attrs)
            assert helper.async_render() == expected
    recipe["measurements"][0]["thresholds"] = [1, 2, 3, 4, 5]
    recipe["measurements"][0]["boundary_rule"] = "upper_inclusive"
    assert aq.automatic_recipe([source], [state], recipe)["measurements"] == recipe["measurements"]


@pytest.mark.parametrize("name,attrs,expected", [
    ("sensor.living_room_radon", {"unit_of_measurement": "Bq/m³"}, "fair"),
    ("sensor.radon", {}, "unknown"),
    ("sensor.radon", {"unit_of_measurement": "m³"}, "unknown"),
    ("sensor.temperature", {"device_class": "temperature", "unit_of_measurement": "°C"}, "unknown"),
])
def test_automatic_profile_inference_requires_known_quantity_and_units(hass, name, attrs, expected):
    hass.states.async_set(name, "54.07", attrs)
    recipe = aq.automatic_recipe([name], [hass.states.get(name)])
    assert Template(aq.generate(recipe), hass).async_render() == expected


def test_automatic_air_quality_aggregates_live_categories_and_handles_no_sources(hass):
    assert Template(aq.generate({"mode": "automatic", "sources": []}), hass).async_render() == "unknown"
    hass.states.async_set("sensor.a", "fair")
    hass.states.async_set("sensor.b", "hazardous")
    helper = Template(aq.generate({"mode": "automatic", "sources": ["sensor.a", "sensor.b"]}), hass)
    assert helper.async_render() == "extremely_poor"
    hass.states.async_set("sensor.b", "unavailable")
    assert helper.async_render() == "fair"


def test_partial_recipe_form_preserves_intervals_and_explicit_invalid_input():
    saved = measurement(levels=list(reversed(aq.LEVELS)))
    recipe = aq.recipe_from_form("measurement", {**saved, "boundary_2": 21})
    assert recipe["thresholds"] == [10, 21, 30, 40, 50]
    assert recipe["levels"] == saved["levels"]
    for field in ("boundary_2", "grade_2"):
        with pytest.raises(vol.Invalid):
            aq.recipe_from_form("measurement", {**saved, field: None})


@pytest.mark.parametrize("advanced", [None, {}, {"offset": 1}])
async def test_partial_compact_setup_keeps_saved_advanced_recipe(hass, advanced):
    from custom_components.virtual_layer.config_flow import VirtualFlowHandler

    hass.states.async_set("sensor.pm25", "25", {"sample": 5})
    flow = VirtualFlowHandler()
    flow.hass = hass
    flow._aq_edit = False
    saved = aq.normalize(measurement(
        attribute="sample", aggregation="worst", missing="unknown",
        multiplier=2, levels=list(reversed(aq.LEVELS)),
    ))
    flow._aq_pending = saved
    submitted = {"boundary_2": 10}
    if advanced is not None:
        submitted["advanced"] = advanced
    result = await flow.async_step_air_quality_setup(submitted)
    assert result["errors"] == {"base": "invalid_air_quality_logic"}
    result = await flow.async_step_air_quality_setup({"boundary_2": 21})
    assert result["step_id"] == "air_quality_review"
    for key in ("attribute", "aggregation", "missing", "multiplier", "levels"):
        assert flow._aq_pending[key] == saved[key]
    assert flow._aq_pending["offset"] == (1 if advanced else 0)
    assert flow._aq_pending["thresholds"] == [10, 21, 30, 40, 50]
    assert result["description_placeholders"]["result"] == (
        "very_poor" if advanced else "extremely_poor"
    )


@pytest.mark.parametrize("levels", [None, [], ["poor"]])
def test_damaged_grade_defaults_still_offer_all_six_controls(levels):
    values = aq.setup_schema(measurement(levels=levels))({})
    assert all(f"grade_{i}" in values["advanced"] for i in range(1, 7))
    if levels:
        assert values["advanced"]["grade_1"] == "poor"


@pytest.mark.parametrize("per_source", [False, True])
async def test_automatic_flow_keeps_pending_per_source_choice(hass, per_source):
    from custom_components.virtual_layer.config_flow import (
        VirtualFlowHandler, CONF_AIR_QUALITY_LOGIC, CONF_SOURCE_ENTITIES_TEXT,
        _flatten_entity_form_sections,
    )
    flow = VirtualFlowHandler()
    flow.hass = hass
    hass.states.async_set("sensor.pm25", "10", {"device_class": "pm25", "unit_of_measurement": "μg/m³"})
    flow._entity_defaults = {
        "platform": "air_quality", CONF_SOURCE_ENTITIES_TEXT: "sensor.pm25",
        "air_quality_per_source": per_source,
    }
    result = await flow.async_step_air_quality()
    assert result["step_id"] == "entity"
    assert flow._entity_defaults[CONF_AIR_QUALITY_LOGIC]["per_source"] is per_source
    form = _flatten_entity_form_sections(result["data_schema"]({}))
    assert form["air_quality_per_source"] is per_source


async def test_compact_setup_preserves_advanced_values_through_errors_and_back(hass):
    from custom_components.virtual_layer.config_flow import VirtualFlowHandler

    hass.states.async_set("sensor.pm25", "25", {"device_class": "pm25", "unit_of_measurement": "μg/m³"})
    flow = VirtualFlowHandler()
    flow.hass = hass
    flow._entity_defaults = {"platform": "air_quality"}
    result = await flow.async_step_air_quality({"mode": "measurement"})
    assert result["step_id"] == "air_quality_setup"
    values = result["data_schema"]({
        "sources": ["sensor.pm25"], "unit": "μg/m³",
        **{f"boundary_{i}": i * 10 for i in range(1, 6)},
        "advanced": {"multiplier": 2, "grade_5": "good", "missing": "unknown"},
    })
    result = await flow.async_step_air_quality_setup({**values, "boundary_2": 10})
    assert result["step_id"] == "air_quality_setup"
    assert result["errors"] == {"base": "invalid_air_quality_logic"}
    restored = result["data_schema"]({})
    assert restored["advanced"]["multiplier"] == 2
    assert restored["advanced"]["grade_5"] == "good"
    restored["boundary_2"] = 20
    result = await flow.async_step_air_quality_setup(restored)
    assert result["step_id"] == "air_quality_review"
    assert result["description_placeholders"]["result"] == "good"
    assert flow._aq_pending["quantity"] == "pm25"
    result = await flow.async_step_air_quality_review({"next_action": "rules"})
    assert result["step_id"] == "air_quality_setup"
    reopened = result["data_schema"]({})
    assert reopened["advanced"]["multiplier"] == 2
    assert reopened["advanced"]["grade_5"] == "good"
    result = await flow.async_step_air_quality_setup(reopened)
    assert result["description_placeholders"]["result"] == "good"


def test_compact_setup_fields_have_translations():
    root = Path(__file__).parents[2] / "custom_components/virtual_layer/translations"
    schema = aq.setup_schema(measurement())
    for lang in ("en", "ko"):
        catalog = json.loads((root / f"{lang}.json").read_text())
        for group, name in (("config", "air_quality_setup"), ("options", "air_quality_setup"), ("options", "edit_air_quality_setup")):
            text = catalog[group]["step"][name]
            for key, validator in schema.schema.items():
                if key.schema == "advanced":
                    for field in schema({})["advanced"]:
                        assert text["sections"]["advanced"]["data"][field]
                        assert text["sections"]["advanced"]["data_description"][field]
                else:
                    assert text["data"][key.schema]
                    assert text["data_description"][key.schema]


def test_category_hides_missing_measurements_and_numeric_metadata(hass):
    from custom_components.virtual_layer.generic import GenericVirtualEntity

    entity = GenericVirtualEntity({
        "name": "Formaldehyde", "entity_id": "air_quality.formaldehyde",
        "initial_value": "unknown", "unit_of_measurement": "m³",
        "attributes": {"unit_of_measurement": "m³", "device_class": "pm25", "state_class": "measurement"},
    }, "air_quality", False)
    entity.hass = hass
    entity._apply_native_template_value("air_quality", "unknown")
    entity._apply_native_template_value("unit_of_measurement", "m³")
    for name in aq.NATIVE_MEASUREMENT_CLASSES:
        entity._apply_native_template_value(name, None)
    entity._update_attributes()
    attrs = entity.extra_state_attributes
    assert attrs["air_quality"] == "unknown"
    for name in (*aq.NATIVE_MEASUREMENT_CLASSES, "unit_of_measurement", "device_class", "state_class"):
        assert name not in attrs
    assert entity.unit_of_measurement is None
    entity._apply_native_template_value("particulate_matter_2_5", 0)
    entity._update_attributes()
    assert entity.extra_state_attributes["particulate_matter_2_5"] == 0
    entity._apply_native_template_value("particulate_matter_2_5", None)
    entity._update_attributes()
    assert "particulate_matter_2_5" not in entity.extra_state_attributes


@pytest.mark.parametrize("source_unit", ["m³", "mg/m³", "µg/m³", None])
async def test_measurement_flow_reports_incompatible_source_units(hass, source_unit):
    from custom_components.virtual_layer.config_flow import VirtualFlowHandler

    hass.states.async_set("sensor.formaldehyde", "0", {"unit_of_measurement": source_unit})
    flow = VirtualFlowHandler()
    flow.hass = hass
    flow._entity_defaults = {"platform": "air_quality"}
    await flow.async_step_air_quality({"mode": "measurement"})
    result = await flow.async_step_air_quality_sources({
        "sources": ["sensor.formaldehyde"], "unit": "mg/m³", "quantity": "any",
    })
    if source_unit in ("m³", None):
        assert result["errors"] == {"base": "incompatible_air_quality_source_unit"}
        assert result["step_id"] == "air_quality_sources"
    else:
        assert result["step_id"] == "air_quality_logic"
    # A named attribute has an explicitly declared unit, independent of state.
    result = await flow.async_step_air_quality_sources({
        "sources": ["sensor.formaldehyde"], "unit": "mg/m³", "attribute": "raw_state",
    })
    assert result["step_id"] == "air_quality_logic"


@pytest.mark.parametrize(
    "quantity,unit",
    [
        ("pm25", "ppm"),
        ("pm1", "unitless"),
        ("aqi", "μg/m³"),
        ("volatile_organic_compounds_parts", "mg/m³"),
        ("carbon_dioxide", "unitless"),
        ("invalid", "ppm"),
    ],
)
def test_quantity_and_unit_mismatches_are_rejected(quantity, unit):
    with pytest.raises(vol.Invalid):
        aq.normalize(measurement(quantity=quantity, unit=unit))
    with pytest.raises(vol.Invalid):
        aq.normalize_sources(
            "measurement",
            {"sources": ["sensor.test"], "quantity": quantity, "unit": unit},
        )


def test_pm01_and_nitrogen_oxide_do_not_alias_other_pollutants(hass):
    from custom_components.virtual_layer.config_flow import _native_source_template

    for device_class, property_name in (
        ("pm1", "particulate_matter_0_1"),
        ("nitrogen_dioxide", "nitrogen_oxide"),
    ):
        hass.states.async_set(
            "sensor.pollutant",
            "25",
            {"device_class": device_class, "unit_of_measurement": "μg/m³"},
        )
        helper = _native_source_template(
            "sensor.pollutant",
            hass.states.get("sensor.pollutant"),
            property_name,
            "air_quality",
        )
        assert Template(helper, hass).async_render() is None
        hass.states.async_set("sensor.pollutant", "25", {property_name: 0.5})
        assert Template(helper, hass).async_render() == 0.5


@pytest.mark.parametrize(
    "property_name,device_class", list(aq.NATIVE_MEASUREMENT_CLASSES.items())
)
@pytest.mark.parametrize(
    "unit,value",
    [("μg/m³", "0.5"), ("mg/m³", "0.001"), ("ppm", "10"), ("μg/m³", "bad")],
)
def test_all_native_measurement_classes(hass, property_name, device_class, unit, value):
    from custom_components.virtual_layer.config_flow import _native_source_template

    hass.states.async_set(
        "sensor.pollutant",
        value,
        {"device_class": device_class, "unit_of_measurement": unit},
    )
    helper = _native_source_template(
        "sensor.pollutant",
        hass.states.get("sensor.pollutant"),
        property_name,
        "air_quality",
    )
    expected = (
        None
        if device_class == "aqi" or unit == "ppm" or value == "bad"
        else (0.5 if unit == "μg/m³" else 1)
    )
    assert Template(helper, hass).async_render() == expected


@pytest.mark.parametrize("domain", ["sensor", "number"])
@pytest.mark.parametrize(
    "property_name,device_class", list(aq.NATIVE_MEASUREMENT_CLASSES.items())
)
def test_native_measurements_follow_live_class_and_recover(
    hass, domain, property_name, device_class
):
    from custom_components.virtual_layer.config_flow import _native_source_template

    entity_id = f"{domain}.pollutant"
    hass.states.async_set(entity_id, "unavailable")
    helper = Template(
        _native_source_template(
            entity_id, hass.states.get(entity_id), property_name, "air_quality"
        ),
        hass,
    )
    assert helper.async_render() is None
    attrs = {
        "device_class": device_class,
        "unit_of_measurement": "AQI" if device_class == "aqi" else "mg/m³",
    }
    hass.states.async_set(entity_id, "0.5", attrs)
    assert helper.async_render() == (0.5 if device_class == "aqi" else 500)
    hass.states.async_set(entity_id, "0.5", {**attrs, "device_class": "temperature"})
    assert helper.async_render() is None
    hass.states.async_set(
        entity_id, "20", {"unit_of_measurement": "°C", property_name: 0.25}
    )
    assert helper.async_render() == 0.25
    hass.states.async_set(entity_id, "unavailable", {property_name: 0.25})
    assert helper.async_render() is None
    hass.states.async_set(entity_id, "0.5", attrs)
    assert helper.async_render() == (0.5 if device_class == "aqi" else 500)


@pytest.mark.parametrize("source_unit", [None, "", "AQI", "ppm"])
def test_aqi_recipe_supports_dimensionless_index_unit(hass, source_unit):
    hass.states.async_set(
        "sensor.pm25", "25", {"device_class": "aqi", "unit_of_measurement": source_unit}
    )
    helper = Template(aq.generate(measurement(quantity="aqi", unit="unitless")), hass)
    assert helper.async_render() == ("unknown" if source_unit == "ppm" else "moderate")


async def test_aqi_flow_infers_quantity_and_previews_labeled_index(hass):
    from custom_components.virtual_layer.config_flow import VirtualFlowHandler

    flow = VirtualFlowHandler()
    flow.hass = hass
    flow._entity_defaults = {"platform": "air_quality"}
    hass.states.async_set(
        "sensor.aqi", "25", {"device_class": "aqi", "unit_of_measurement": "AQI"}
    )
    result = await flow.async_step_air_quality({"mode": "measurement"})
    assert result["step_id"] == "air_quality_setup"
    result = await flow.async_step_air_quality_sources(
        {"sources": ["sensor.aqi"], "unit": "unitless"}
    )
    assert flow._aq_pending["quantity"] == "aqi"
    assert result["step_id"] == "air_quality_logic"
    result = await flow.async_step_air_quality_calculation({})
    assert result["step_id"] == "air_quality_logic"
    result = await flow.async_step_air_quality_logic(
        {f"boundary_{i}": i * 10 for i in range(1, 6)}
    )
    assert result["step_id"] == "air_quality_review"
    assert not result["errors"]
    assert Template(aq.generate(flow._aq_pending), hass).async_render() == "moderate"


@pytest.mark.parametrize("device_class", [None, "formaldehyde", "volatile_organic_compounds"])
async def test_numeric_formaldehyde_source_defaults_to_measurement(hass, device_class):
    from custom_components.virtual_layer.config_flow import (
        CONF_SOURCE_ENTITIES_TEXT, VirtualFlowHandler,
    )

    source_id = "sensor.formaldehyde_detector_formaldehyde_detector_living_room_formaldehyde"
    hass.states.async_set(source_id, "0.05", {
        "unit_of_measurement": "mg/m³", "device_class": device_class,
    })
    flow = VirtualFlowHandler()
    flow.hass = hass
    flow._entity_defaults = {"platform": "air_quality", CONF_SOURCE_ENTITIES_TEXT: source_id, "configure_air_quality_rules": True}
    result = await flow.async_step_air_quality()
    assert result["data_schema"]({})["mode"] == "measurement"
    result = await flow.async_step_air_quality({"mode": "measurement"})
    defaults = result["data_schema"]({f"boundary_{i}": i * 10 for i in range(1, 6)})
    assert defaults["unit"] == "mg/m³"
    assert defaults["advanced"]["quantity"] == (
        device_class if device_class in ("volatile_organic_compounds", "formaldehyde") else "any"
    )


@pytest.mark.parametrize(
    "property_name",
    [*aq.NATIVE_MEASUREMENT_CLASSES, "particulate_matter_0_1", "nitrogen_oxide"],
)
@pytest.mark.parametrize("bad", [None, True, -1, float("nan"), float("inf"), "bad"])
def test_all_concentrations_clear_invalid_runtime_values(property_name, bad):
    from custom_components.virtual_layer.generic import GenericVirtualEntity

    entity = GenericVirtualEntity(
        {"name": "Air", "entity_id": "air_quality.test", "initial_value": "unknown"},
        "air_quality",
        False,
    )
    entity._apply_native_template_value(property_name, 12.5)
    entity._apply_native_template_value(property_name, bad)
    assert entity._domain_options[property_name] is None
    entity._apply_native_template_value("air_quality", "good")
    entity._apply_native_template_value("air_quality", bad)
    assert entity.state == "unknown"


@pytest.mark.parametrize("quantity", aq.QUANTITIES[1:])
def test_quantity_filter_follows_live_device_class(hass, quantity):
    unit = (
        "unitless"
        if quantity == "aqi"
        else ("ppm" if quantity == "volatile_organic_compounds_parts" else "μg/m³")
    )
    source_unit = None if unit == "unitless" else unit
    if quantity == "radon":
        unit = source_unit = "Bq/m³"
    recipe = measurement(unit=unit, quantity=quantity)
    hass.states.async_set(
        "sensor.pm25",
        "25",
        {"device_class": quantity, "unit_of_measurement": source_unit},
    )
    helper = Template(aq.generate(recipe), hass)
    assert helper.async_render() == "moderate"
    hass.states.async_set(
        "sensor.pm25",
        "25",
        {"device_class": "temperature", "unit_of_measurement": source_unit},
    )
    assert helper.async_render() == "unknown"


@pytest.mark.parametrize("entity_id,name,declared,expected", [
    ("sensor.bedroom_pm2_5", "Bedroom", None, "pm25"),
    ("sensor.bedroom_pm10", "Bedroom", None, "pm10"),
    ("sensor.bedroom_pm1_0", "PM1.0", None, "pm1"),
    ("sensor.bedroom_pm1", "Particulate Matter 1.0", None, "pm1"),
    ("sensor.pm1_5", "Sensor", None, None),
    ("sensor.pm1_01", "Sensor", None, None),
    ("sensor.pm0_1", "Sensor", None, None),
    ("sensor.ozone", "오존", None, "ozone"),
    ("sensor.so2", "Sensor", None, "sulphur_dioxide"),
    ("sensor.living_room_c6h6", "C₆H₆", None, "benzene"),
    ("sensor.nh3", "NH₃ 암모니아", None, "ammonia"),
    ("sensor.h2s", "H₂S 황화수소", None, "hydrogen_sulfide"),
    ("sensor.etvoc", "eTVOC", None, "volatile_organic_compounds"),
    ("sensor.co", "CO", None, "carbon_monoxide"),
    ("sensor.no2", "NO₂", None, "nitrogen_dioxide"),
    ("sensor.o3", "O₃", None, "ozone"),
    ("sensor.air_pressure", "대기압", "atmospheric_pressure", None),
    ("binary_sensor.pir", "PIR", "motion", None),
    ("sensor.environment", "거실 라돈", None, "radon"),
    ("sensor.hcho", "Formaldehyde", None, "formaldehyde"),
    ("sensor.pm10", "PM2.5", "carbon_dioxide", "carbon_dioxide"),
    ("sensor.pm10", "PM2.5", None, None),
    ("sensor.pm10", "PM2.5", "temperature", None),
    ("sensor.random", "Environment Sensor", None, None),
    ("sensor.pm100", "Sensor", None, None),
])
def test_air_quality_profile_name_inference(hass, entity_id, name, declared, expected):
    hass.states.async_set(entity_id, "0", {"friendly_name": name, "device_class": declared})
    assert aq.infer_quantity(hass.states.get(entity_id)) == expected


@pytest.mark.parametrize("quantity", list(aq.STARTER_PROFILES))
def test_all_automatic_profiles_use_their_own_units_and_boundaries(hass, quantity):
    unit, boundaries, _ = aq.STARTER_PROFILES[quantity]
    if quantity == "aqi":
        unit = "AQI"
    attrs = {"device_class": quantity, "unit_of_measurement": unit}
    hass.states.async_set("sensor.sample", str(boundaries[0] / 2), attrs)
    recipe = aq.automatic_recipe(["sensor.sample"], [hass.states.get("sensor.sample")])
    helper = Template(aq.generate(recipe), hass)
    assert helper.async_render() == "good"
    hass.states.async_set("sensor.sample", str((boundaries[0] + boundaries[1]) / 2), attrs)
    assert helper.async_render() == "fair"


@pytest.mark.parametrize("name,unit,value,quantity,expected", [
    ("c6h6", "μg/m³", "3", "benzene", "moderate"),
    ("nh3", "ppb", "300", "ammonia", "moderate"),
    ("h2s", "ppm", "0.015", "hydrogen_sulfide", "moderate"),
    ("etvoc", "ppb", "150", "volatile_organic_compounds_parts", "moderate"),
    ("tvoc", "mg/m³", "0.3", "volatile_organic_compounds", "moderate"),
    ("etvoc", "", "150", "volatile_organic_compounds", "unknown"),
])
def test_named_gases_without_native_classes_generate_live_profiles(hass, name, unit, value, quantity, expected):
    source_id = f"sensor.{name}"
    hass.states.async_set(source_id, value, {"unit_of_measurement": unit})
    source = hass.states.get(source_id)
    assert aq.infer_quantity(source) == quantity
    recipe = aq.automatic_recipe([source_id], [source])
    assert Template(aq.generate(recipe), hass).async_render() == expected
    assert hass.states.get(source_id) is source


@pytest.mark.parametrize("quantity", sorted(aq.CUSTOM_QUANTITIES))
def test_manual_custom_gas_quantity_accepts_classless_source_not_wrong_class(hass, quantity):
    unit, thresholds, _ = aq.STARTER_PROFILES[quantity]
    recipe = measurement(quantity=quantity, unit=unit, thresholds=list(thresholds))
    hass.states.async_set("sensor.pm25", "0", {"unit_of_measurement": unit})
    helper = Template(aq.generate(recipe), hass)
    assert helper.async_render() == "good"
    hass.states.async_set("sensor.pm25", "0", {"unit_of_measurement": unit, "device_class": "temperature"})
    assert helper.async_render() == "unknown"


@pytest.mark.parametrize("quantity,unit,factor", [
    ("pm25", "μg/m³", 1), ("pm25", "mg/m³", 0.001),
    ("pm10", "μg/m³", 1), ("radon", "Bq/m³", 1),
    ("radon", "pCi/L", 1 / 37), ("formaldehyde", "μg/m³", 1000),
    ("carbon_dioxide", "ppb", 1000), ("aqi", "unitless", 1),
])
async def test_preset_fills_all_required_fields_and_preserves_edits(hass, quantity, unit, factor):
    from custom_components.virtual_layer.config_flow import VirtualFlowHandler
    source = f"sensor.{quantity}"
    hass.states.async_set(source, "0", {"device_class": quantity, "unit_of_measurement": None if unit == "unitless" else unit})
    flow = VirtualFlowHandler()
    flow.hass = hass
    flow._aq_edit = False
    flow._aq_pending = {"mode": "measurement", "sources": [source]}
    result = await flow.async_step_air_quality_setup()
    values = result["data_schema"]({})
    assert values["sources"] == [source]
    assert values["unit"] == unit
    assert [values[f"boundary_{i}"] for i in range(1, 6)] == pytest.approx(
        [v * factor for v in aq.STARTER_PROFILES[quantity][1]])
    result = await flow.async_step_air_quality_setup(values)
    assert result["step_id"] == "air_quality_review"
    assert result["description_placeholders"]["result"] == "good"
    existing = {**flow._aq_pending, "thresholds": [1, 2, 3, 4, 5], "levels": ["poor"] * 6}
    filled, _ = aq.prefill_measurement(existing, [hass.states.get(source)])
    assert filled["thresholds"] == [1, 2, 3, 4, 5]
    assert filled["levels"] == ["poor"] * 6


def test_prefill_rejects_mixed_sources_and_explicit_other_quantity(hass):
    hass.states.async_set("sensor.pm25", "1", {"device_class": "pm25"})
    hass.states.async_set("sensor.radon", "1", {"device_class": "radon"})
    states = [hass.states.get("sensor.pm25"), hass.states.get("sensor.radon")]
    assert "thresholds" not in aq.prefill_measurement({}, states)[0]
    assert "thresholds" not in aq.prefill_measurement({"quantity": "radon"}, states[:1])[0]
    assert "thresholds" not in aq.prefill_measurement({"attribute": "other"}, states[:1])[0]


async def test_flow_rejects_mixed_pollutants_before_calculation(hass):
    from custom_components.virtual_layer.config_flow import VirtualFlowHandler

    flow = VirtualFlowHandler()
    flow.hass = hass
    flow._entity_defaults = {"platform": "air_quality"}
    hass.states.async_set("sensor.pm", "1", {"device_class": "pm25"})
    hass.states.async_set("sensor.co", "1", {"device_class": "carbon_monoxide", "unit_of_measurement": "ppm"})
    await flow.async_step_air_quality({"mode": "measurement"})
    result = await flow.async_step_air_quality_sources(
        {"sources": ["sensor.pm", "sensor.co"], "unit": "μg/m³"}
    )
    assert result["step_id"] == "air_quality_sources"
    assert result["errors"] == {"base": "mixed_air_quality_measurements"}
    result = await flow.async_step_air_quality_sources(
        {"sources": ["sensor.co"], "unit": "ppm"}
    )
    assert result["step_id"] == "air_quality_logic"
    assert flow._aq_pending["quantity"] == "carbon_monoxide"


@pytest.mark.parametrize(
    "unit,value,expected",
    [
        ("μg/m³", "0.5", 0.5),
        ("mg/m³", "0.001", 1),
        ("ppm", "1", None),
        ("μg/m³", "unavailable", None),
    ],
)
def test_pm25_native_helper_preserves_measurement_and_missing(
    hass, unit, value, expected
):
    from custom_components.virtual_layer.config_flow import _native_source_template

    hass.states.async_set(
        "sensor.pm", value, {"device_class": "pm25", "unit_of_measurement": unit}
    )
    state = hass.states.get("sensor.pm")
    helper = _native_source_template(
        "sensor.pm", state, "particulate_matter_2_5", "air_quality"
    )
    assert Template(helper, hass).async_render() == expected
    for name in ("carbon_dioxide", "ozone", "particulate_matter_10"):
        helper = _native_source_template("sensor.pm", state, name, "air_quality")
        assert Template(helper, hass).async_render() is None


def test_measurement_to_category_replacement_is_rejected_without_mutation():
    from copy import deepcopy
    from custom_components.virtual_layer.config_flow import (
        _replace_ui_entity,
        AirQualityConversionRequiresNewEntity,
    )
    from custom_components.virtual_layer.const import ATTR_DEVICES

    options = {
        ATTR_DEVICES: {
            "Room": [
                {
                    "platform": "sensor",
                    "name": "PM2.5",
                    "entity_id": "sensor.pm",
                    "initial_value": 0.5,
                }
            ]
        }
    }
    before = deepcopy(options)
    with pytest.raises(AirQualityConversionRequiresNewEntity):
        _replace_ui_entity(
            options, "Room", 0, "Room", {"platform": "air_quality", "name": "Air"}
        )
    assert options == before


def test_multiple_pm25_sources_average_only_real_measurements(hass):
    from custom_components.virtual_layer.config_flow import _native_reference_templates

    ids = ["sensor.pm_a", "sensor.pm_b"]
    for entity_id, value in zip(ids, (0.5, 1.5)):
        hass.states.async_set(
            entity_id,
            str(value),
            {"device_class": "pm25", "unit_of_measurement": "μg/m³"},
        )
    helpers = _native_reference_templates(
        "air_quality", ids, [hass.states.get(item) for item in ids]
    )
    helper = Template(helpers["particulate_matter_2_5"], hass)
    assert helper.async_render() == 1
    assert Template(helpers["ozone"], hass).async_render() is None
    hass.states.async_set(ids[0], "unavailable")
    assert helper.async_render() == 1.5


def test_native_missing_concentration_clears_old_reading():
    from custom_components.virtual_layer.generic import GenericVirtualEntity

    entity = GenericVirtualEntity(
        {"name": "Air", "entity_id": "air_quality.test", "initial_value": "unknown"},
        "air_quality",
        False,
    )
    entity._apply_native_template_value("air_quality", "good")
    entity._apply_native_template_value("particulate_matter_2_5", 0.5)
    entity._apply_native_template_value("particulate_matter_2_5", None)
    assert entity._domain_options["particulate_matter_2_5"] is None
    assert entity.state == "good"


@pytest.mark.parametrize(
    "value,expected",
    [
        (0, "good"),
        (10, "good"),
        (10.1, "fair"),
        (20, "fair"),
        (30, "moderate"),
        (40, "poor"),
        (50, "very_poor"),
        (51, "extremely_poor"),
        (-1, "unknown"),
        ("nan", "unknown"),
        ("inf", "unknown"),
        ("unavailable", "unknown"),
        ("bad", "unknown"),
    ],
)
def test_measurement_boundaries_and_invalid_values(hass, value, expected):
    hass.states.async_set("sensor.pm25", str(value), {"unit_of_measurement": "μg/m³"})
    assert Template(aq.generate(measurement()), hass).async_render() == expected


@pytest.mark.parametrize(
    "unit,value,expected",
    [
        ("mg/m³", "0.02", "fair"),
        ("µg/m³", "20", "fair"),
        ("ppm", "20", "unknown"),
        ("", "20", "unknown"),
    ],
)
def test_measurement_units(hass, unit, value, expected):
    hass.states.async_set("sensor.pm25", value, {"unit_of_measurement": unit})
    assert Template(aq.generate(measurement()), hass).async_render() == expected


@pytest.mark.parametrize(
    "unit,source_unit,value,expected",
    [
        ("unitless", None, "25", "moderate"),
        ("ppm", "ppb", "25000", "moderate"),
        ("ppb", "ppm", "0.025", "moderate"),
        ("mg/m³", "μg/m³", "25000", "moderate"),
    ],
)
def test_other_units(hass, unit, source_unit, value, expected):
    hass.states.async_set("sensor.pm25", value, {"unit_of_measurement": source_unit})
    assert (
        Template(aq.generate(measurement(unit=unit)), hass).async_render() == expected
    )


def test_attribute_uses_declared_unit_and_custom_grades(hass):
    hass.states.async_set(
        "sensor.pm25", "ready", {"pm": 25, "unit_of_measurement": "ppm"}
    )
    recipe = measurement(attribute="pm", levels=["good"] * 3 + ["poor"] * 3)
    helper = Template(aq.generate(recipe), hass)
    assert helper.async_render() == "good"
    hass.states.async_set("sensor.pm25", "ready", {"pm": 35})
    assert helper.async_render() == "poor"
    hass.states.async_set("sensor.pm25", "ready", {"pm": True})
    assert helper.async_render() == "unknown"


@pytest.mark.parametrize("mode", ["source", "measurement"])
@pytest.mark.parametrize("aggregation", ["first", "worst"])
@pytest.mark.parametrize("missing", ["skip", "unknown"])
def test_multiple_source_policies(hass, mode, aggregation, missing):
    recipe = measurement(
        mode=mode,
        sources=["sensor.first", "sensor.second"],
        aggregation=aggregation,
        missing=missing,
    )
    hass.states.async_set(
        "sensor.first",
        "5" if mode == "measurement" else " Good ",
        {"unit_of_measurement": "μg/m³"},
    )
    hass.states.async_set(
        "sensor.second",
        "35" if mode == "measurement" else "poor",
        {"unit_of_measurement": "μg/m³"},
    )
    helper = Template(aq.generate(recipe), hass)
    assert helper.async_render() == ("good" if aggregation == "first" else "poor")
    hass.states.async_set("sensor.first", "unavailable", {"air_quality": "good"})
    assert helper.async_render() == ("poor" if missing == "skip" else "unknown")
    hass.states.async_set("sensor.second", "unknown")
    assert helper.async_render() == "unknown"


@pytest.mark.parametrize(
    "overrides",
    [
        {"thresholds": [1, 1, 2, 3, 4]},
        {"thresholds": [5, 4, 3, 2, 1]},
        {"thresholds": [True, 2, 3, 4, 5]},
        {"thresholds": [float("nan")] * 5},
        {"thresholds": [1, 2, 3, 4, float("inf")]},
        {"thresholds": [1]},
        {"thresholds": [1, 2, 3, 4, 1e20]},
        {"sources": []},
        {"sources": ["invalid id"]},
        {"levels": ["unknown"] * 6},
        {"unit": "°C"},
        {"missing": "last"},
        {"aggregation": "average"},
    ],
)
def test_invalid_recipes_are_rejected(overrides):
    with pytest.raises(vol.Invalid):
        aq.normalize(measurement(**overrides))


def test_all_logic_step_fields_have_translations():
    root = Path(__file__).parents[2] / "custom_components/virtual_layer/translations"
    for language in ("en", "ko"):
        catalog = json.loads((root / f"{language}.json").read_text())
        for section, step in (
            ("config", "air_quality_calculation"),
            ("options", "air_quality_calculation"),
            ("options", "edit_air_quality_calculation"),
        ):
            for marker in aq.calculation_schema({}).schema:
                assert catalog[section]["step"][step]["data"][marker.schema]
                assert catalog[section]["step"][step]["data_description"][marker.schema]
        for mode in ("source", "measurement", "fixed"):
            schema = aq.logic_schema(mode, measurement())
            for section, step in (
                ("config", "air_quality_logic"),
                ("options", "air_quality_logic"),
                ("options", "edit_air_quality_logic"),
            ):
                translation = catalog[section]["step"][step]
                for marker in schema.schema:
                    assert translation["data"][marker.schema]
                    assert translation["data_description"][marker.schema]


@pytest.mark.parametrize("policy", ["automatic", "keep_current", "force_helper"])
@pytest.mark.parametrize("customized", [False, True])
@pytest.mark.parametrize("old_mode", ["fixed", "custom"])
async def test_recipe_edit_respects_helper_policy(policy, customized, old_mode):
    from custom_components.virtual_layer.config_flow import (
        CONF_NATIVE_VALUE_TEMPLATES,
        _AirQualityLogicFlow,
    )
    from custom_components.virtual_layer.const import CONF_AIR_QUALITY_LOGIC

    class Flow(_AirQualityLogicFlow):
        async def async_step_edit_entity(self, user_input=None):
            return self._entity_defaults

    old = "{{ 'good' }}"
    template = "{{ 'poor' if true else 'good' }}" if customized else old
    flow = Flow()
    flow._aq_edit = True
    flow._edit_helper_update_mode = policy
    flow._entity_defaults = {
        "platform": "air_quality",
        CONF_NATIVE_VALUE_TEMPLATES: {"air_quality": template},
        CONF_AIR_QUALITY_LOGIC: {
            "mode": old_mode,
            "fixed": "good",
            "generated_template": old if old_mode == "fixed" else None,
        },
    }
    flow._edit_current_defaults = dict(flow._entity_defaults)
    result = await flow._aq_finish(measurement())
    expected = (
        template
        if policy == "keep_current"
        or (policy == "automatic" and (customized or old_mode == "custom"))
        else aq.generate(measurement())
    )
    assert result[CONF_NATIVE_VALUE_TEMPLATES]["air_quality"] == expected
    assert result[CONF_AIR_QUALITY_LOGIC]["mode"] == "measurement"


@pytest.mark.parametrize(
    "reducer,expected",
    [
        ("per_source", "good"),
        ("mean", "moderate"),
        ("median", "fair"),
        ("minimum", "good"),
        ("maximum", "extremely_poor"),
    ],
)
@pytest.mark.parametrize("missing", ["skip", "unknown"])
def test_numeric_reducers_with_missing_sources(hass, reducer, expected, missing):
    sources = ["sensor.a", "sensor.b", "sensor.c"]
    for entity_id, value in zip(sources, (5, 15, 55)):
        hass.states.async_set(entity_id, str(value), {"unit_of_measurement": "μg/m³"})
    helper = Template(
        aq.generate(measurement(sources=sources, reducer=reducer, missing=missing)),
        hass,
    )
    assert helper.async_render() == expected
    hass.states.async_set("sensor.b", "unavailable")
    assert helper.async_render() == (
        "unknown"
        if missing == "unknown"
        else {
            "per_source": "good",
            "mean": "moderate",
            "median": "moderate",
            "minimum": "good",
            "maximum": "extremely_poor",
        }[reducer]
    )
    for entity_id in sources:
        hass.states.async_set(entity_id, "unknown")
    assert helper.async_render() == "unknown"


@pytest.mark.parametrize(
    "coefficients,expected",
    [
        ({}, "good"),
        ({"multiplier": 2, "offset": 5}, "moderate"),
        ({"quadratic": 0.2, "multiplier": 0}, "fair"),
        ({"offset": -11}, "unknown"),
        ({"multiplier": -1, "offset": 45}, "poor"),
    ],
)
def test_calibration_follows_unit_conversion(hass, coefficients, expected):
    hass.states.async_set("sensor.pm25", "0.01", {"unit_of_measurement": "mg/m³"})
    assert (
        Template(aq.generate(measurement(**coefficients)), hass).async_render()
        == expected
    )


@pytest.mark.parametrize("boundary", [10, 20, 30, 40, 50])
@pytest.mark.parametrize("rule", ["upper_inclusive", "lower_inclusive"])
def test_every_boundary_equality_rule(hass, boundary, rule):
    hass.states.async_set(
        "sensor.pm25", str(boundary), {"unit_of_measurement": "μg/m³"}
    )
    expected = aq.LEVELS[boundary // 10 - (rule == "upper_inclusive")]
    assert (
        Template(aq.generate(measurement(boundary_rule=rule)), hass).async_render()
        == expected
    )


@pytest.mark.parametrize("field", ["quadratic", "multiplier", "offset"])
@pytest.mark.parametrize("value", [True, "bad", float("nan"), float("inf"), 1e7, None])
def test_invalid_calibration_is_rejected(field, value):
    with pytest.raises(vol.Invalid):
        aq.normalize(measurement(**{field: value}))


def test_calibration_overflow_and_legacy_defaults(hass):
    hass.states.async_set("sensor.pm25", "1e300", {"unit_of_measurement": "μg/m³"})
    assert (
        Template(aq.generate(measurement(quadratic=1)), hass).async_render()
        == "unknown"
    )
    normalized = aq.normalize(measurement())
    assert normalized["reducer"] == "per_source"
    assert normalized["boundary_rule"] == "upper_inclusive"
    assert aq.normalize(normalized) == normalized


@pytest.mark.parametrize("mode", ["source", "measurement", "fixed"])
def test_split_steps_and_review_actions_have_translations(mode):
    sources = aq.source_schema(mode, measurement())
    thresholds = aq.threshold_schema(mode, measurement())
    assert not (
        {key.schema for key in sources.schema}
        & {key.schema for key in thresholds.schema}
    )
    if mode == "measurement":
        assert len(thresholds.schema) == 12
        assert "unit" in {key.schema for key in sources.schema}
    root = Path(__file__).parents[2] / "custom_components/virtual_layer/translations"
    for language in ("en", "ko"):
        catalog = json.loads((root / f"{language}.json").read_text())
        for section, prefixes in (("config", ("",)), ("options", ("", "edit_"))):
            for prefix in prefixes:
                calibration = catalog[section]["step"][prefix + "air_quality_logic"]["sections"]["calibration"]
                for marker in aq.calculation_schema({}).schema:
                    assert calibration["data"][marker.schema]
                    assert calibration["data_description"][marker.schema]
                for name, schema in (
                    ("air_quality_sources", sources),
                    ("air_quality_review", aq.review_schema(mode)),
                ):
                    for marker in schema.schema:
                        assert catalog[section]["step"][prefix + name]["data"][
                            marker.schema
                        ]
                        assert catalog[section]["step"][prefix + name][
                            "data_description"
                        ][marker.schema]


async def test_initial_source_flow_validation_preview_refresh_and_back(hass):
    from custom_components.virtual_layer.config_flow import VirtualFlowHandler

    flow = VirtualFlowHandler()
    flow.hass = hass
    flow._entity_defaults = {"platform": "air_quality"}
    result = await flow.async_step_air_quality({"mode": "source"})
    assert result["step_id"] == "air_quality_sources"
    result = await flow.async_step_air_quality_sources({"sources": []})
    assert result["errors"] == {"base": "invalid_air_quality_logic"}
    hass.states.async_set("sensor.grade", "poor")
    result = await flow.async_step_air_quality_sources({"sources": ["sensor.grade"]})
    assert result["step_id"] == "air_quality_review"
    assert result["description_placeholders"]["result"] == "poor"
    hass.states.async_set("sensor.grade", "good")
    result = await flow.async_step_air_quality_review({"next_action": "refresh"})
    assert result["description_placeholders"]["result"] == "good"
    result = await flow.async_step_air_quality_review({"next_action": "calculation"})
    assert result["errors"] == {"base": "invalid_air_quality_logic"}
    result = await flow.async_step_air_quality_review({"next_action": "sources"})
    assert result["data_schema"]({})["sources"] == ["sensor.grade"]
    assert flow._entity_defaults == {"platform": "air_quality"}
