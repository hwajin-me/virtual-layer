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
        assert len(thresholds.schema) == 11
        assert "unit" in {key.schema for key in sources.schema}
    root = Path(__file__).parents[2] / "custom_components/virtual_layer/translations"
    for language in ("en", "ko"):
        catalog = json.loads((root / f"{language}.json").read_text())
        for section, prefixes in (("config", ("",)), ("options", ("", "edit_"))):
            for prefix in prefixes:
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
