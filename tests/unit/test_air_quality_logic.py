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
    flow._entity_defaults = {"platform": "air_quality", CONF_SOURCE_ENTITIES_TEXT: source_id}
    result = await flow.async_step_air_quality()
    assert result["data_schema"]({})["mode"] == "measurement"
    result = await flow.async_step_air_quality({"mode": "measurement"})
    defaults = result["data_schema"]({f"boundary_{i}": i * 10 for i in range(1, 6)})
    assert defaults["unit"] == "mg/m³"
    assert defaults["advanced"]["quantity"] == (
        "volatile_organic_compounds" if device_class == "volatile_organic_compounds" else "any"
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
