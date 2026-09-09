"""UI-only air-quality recipes and editable Jinja helper generation."""

import math
from collections.abc import Mapping
from itertools import pairwise

import voluptuous as vol
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import selector

LEVELS = ("good", "fair", "moderate", "poor", "very_poor", "extremely_poor")
MODES = ("source", "measurement", "fixed", "custom")
UNITS = ("unitless", "μg/m³", "mg/m³", "ppm", "ppb")
REDUCERS = ("per_source", "mean", "median", "minimum", "maximum")
QUANTITIES = (
    "any",
    "pm1",
    "pm25",
    "pm10",
    "aqi",
    "carbon_dioxide",
    "carbon_monoxide",
    "ozone",
    "nitrogen_dioxide",
    "nitrogen_monoxide",
    "sulphur_dioxide",
    "volatile_organic_compounds",
    "volatile_organic_compounds_parts",
)

# PM0.1 and N2O have no matching standard sensor device class. Their named
# attributes remain usable; never alias PM1 to PM0.1 or NO2 to N2O.
NATIVE_MEASUREMENT_CLASSES = {
    "particulate_matter_2_5": "pm25",
    "particulate_matter_10": "pm10",
    "air_quality_index": "aqi",
    "carbon_dioxide": "carbon_dioxide",
    "carbon_monoxide": "carbon_monoxide",
    "ozone": "ozone",
    "nitrogen_dioxide": "nitrogen_dioxide",
    "nitrogen_monoxide": "nitrogen_monoxide",
    "sulphur_dioxide": "sulphur_dioxide",
}


def validate_quantity_unit(quantity, unit):
    allowed = UNITS
    if quantity in ("pm1", "pm25", "pm10", "volatile_organic_compounds"):
        allowed = ("μg/m³", "mg/m³")
    elif quantity == "aqi":
        allowed = ("unitless",)
    elif quantity == "volatile_organic_compounds_parts":
        allowed = ("ppm", "ppb")
    elif quantity != "any":
        allowed = ("μg/m³", "mg/m³", "ppm", "ppb")
    if unit not in allowed:
        raise vol.Invalid("Unit does not match measured quantity")


def calculation_schema(defaults):
    """Configure numeric processing separately from thresholds and categories."""
    fields = {
        vol.Required("reducer", default=defaults.get("reducer", "per_source")): choice(
            REDUCERS, "air_quality_reducer"
        ),
        vol.Required(
            "boundary_rule", default=defaults.get("boundary_rule", "upper_inclusive")
        ): choice(("upper_inclusive", "lower_inclusive"), "air_quality_boundary_rule"),
    }
    for key, default in (("quadratic", 0), ("multiplier", 1), ("offset", 0)):
        fields[vol.Required(key, default=defaults.get(key, default))] = (
            selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=-1e6, max=1e6, step="any", mode=selector.NumberSelectorMode.BOX
                )
            )
        )
    return vol.Schema(fields)


def normalize_calculation(recipe):
    result = {
        "reducer": recipe.get("reducer", "per_source"),
        "boundary_rule": recipe.get("boundary_rule", "upper_inclusive"),
    }
    if result["reducer"] not in REDUCERS or result["boundary_rule"] not in (
        "upper_inclusive",
        "lower_inclusive",
    ):
        raise vol.Invalid("Invalid calculation policy")
    for key, default in (("quadratic", 0), ("multiplier", 1), ("offset", 0)):
        value = recipe.get(key, default)
        if isinstance(value, bool):
            raise vol.Invalid("Invalid coefficient")
        try:
            value = float(value)
        except (ValueError, TypeError, OverflowError) as err:
            raise vol.Invalid("Invalid coefficient") from err
        if not math.isfinite(value) or abs(value) > 1e6:
            raise vol.Invalid("Invalid coefficient")
        result[key] = value
    return result


def choice(options, key):
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=list(options),
            translation_key=key,
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )


def mode_schema(mode="source"):
    return vol.Schema(
        {vol.Required("mode", default=mode): choice(MODES, "air_quality_mode")}
    )


def logic_schema(mode, defaults):
    """Show only inputs relevant to the selected recipe, before any Jinja."""
    if mode == "fixed":
        return vol.Schema(
            {
                vol.Required("fixed", default=defaults.get("fixed", "unknown")): choice(
                    ("unknown", *LEVELS), "air_quality_grade"
                )
            }
        )
    fields = {
        vol.Required(
            "sources", default=defaults.get("sources", [])
        ): selector.EntitySelector(selector.EntitySelectorConfig(multiple=True)),
        vol.Optional("attribute", default=defaults.get("attribute", "")): str,
        vol.Required(
            "aggregation", default=defaults.get("aggregation", "first")
        ): choice(("first", "worst"), "air_quality_aggregation"),
        vol.Required("missing", default=defaults.get("missing", "skip")): choice(
            ("skip", "unknown"), "air_quality_missing"
        ),
    }
    if mode == "measurement":
        fields[vol.Required("quantity", default=defaults.get("quantity", "any"))] = (
            choice(QUANTITIES, "air_quality_quantity")
        )
        fields[vol.Required("unit", default=defaults.get("unit", "unitless"))] = choice(
            UNITS, "air_quality_unit"
        )
        thresholds = defaults.get("thresholds", [])
        levels = defaults.get("levels", LEVELS)
        for index in range(5):
            marker = vol.Required(
                f"boundary_{index + 1}",
                **({"default": thresholds[index]} if index < len(thresholds) else {}),
            )
            fields[marker] = selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    max=1e12,
                    step="any",
                    mode=selector.NumberSelectorMode.BOX,
                )
            )
        for index, level in enumerate(levels):
            fields[vol.Required(f"grade_{index + 1}", default=level)] = choice(
                LEVELS, "air_quality_grade"
            )
    return vol.Schema(fields)


def normalize(recipe):
    """Reject malformed or unbounded recipes before persisting or rendering."""
    if not isinstance(recipe, Mapping) or recipe.get("mode") not in MODES:
        raise vol.Invalid("Invalid air quality mode")
    mode = recipe["mode"]
    result = {"mode": mode}
    if mode == "custom":
        return result
    if mode == "fixed":
        if recipe.get("fixed") not in ("unknown", *LEVELS):
            raise vol.Invalid("Invalid fixed level")
        return {**result, "fixed": recipe["fixed"]}
    sources = recipe.get("sources")
    if not isinstance(sources, list) or not 1 <= len(sources) <= 64:
        raise vol.Invalid("Select 1 to 64 sources")
    sources = list(dict.fromkeys(cv.entity_id(value) for value in sources))
    attribute = recipe.get("attribute", "")
    if not isinstance(attribute, str) or len(attribute) > 255:
        raise vol.Invalid("Invalid attribute")
    aggregation = recipe.get("aggregation", "first")
    missing = recipe.get("missing", "skip")
    if aggregation not in ("first", "worst") or missing not in ("skip", "unknown"):
        raise vol.Invalid("Invalid source policy")
    result.update(
        sources=sources,
        attribute=attribute.strip(),
        aggregation=aggregation,
        missing=missing,
    )
    if mode == "measurement":
        quantity = recipe.get("quantity", "any")
        if quantity not in QUANTITIES:
            raise vol.Invalid("Invalid measurement quantity")
        result["quantity"] = quantity
        result.update(normalize_calculation(recipe))
        unit = recipe.get("unit")
        if unit not in UNITS:
            raise vol.Invalid("Invalid unit")
        validate_quantity_unit(quantity, unit)
        thresholds = recipe.get("thresholds")
        if not isinstance(thresholds, list) or len(thresholds) != 5:
            raise vol.Invalid("Five boundaries are required")
        values = []
        for value in thresholds:
            if isinstance(value, bool):
                raise vol.Invalid("Invalid boundary")
            try:
                value = float(value)
            except (ValueError, TypeError, OverflowError) as err:
                raise vol.Invalid("Invalid boundary") from err
            if not math.isfinite(value) or not 0 <= value <= 1e12:
                raise vol.Invalid("Invalid boundary")
            values.append(value)
        if any(a >= b for a, b in pairwise(values)):
            raise vol.Invalid("Boundaries must strictly increase")
        levels = recipe.get("levels", list(LEVELS))
        if (
            not isinstance(levels, list)
            or len(levels) != 6
            or any(level not in LEVELS for level in levels)
        ):
            raise vol.Invalid("Six valid grades are required")
        result.update(unit=unit, thresholds=values, levels=list(levels))
    return result


def source_schema(mode, defaults):
    """Source controls are separate from calibration and interval controls."""
    keys = {"sources", "attribute", "aggregation", "missing", "unit", "quantity"}
    return vol.Schema(
        {
            key: value
            for key, value in logic_schema(mode, defaults).schema.items()
            if key.schema in keys
        }
    )


def threshold_schema(mode, defaults):
    keys = {"sources", "attribute", "aggregation", "missing", "unit", "quantity"}
    return vol.Schema(
        {
            key: value
            for key, value in logic_schema(mode, defaults).schema.items()
            if key.schema not in keys
        }
    )


def normalize_sources(mode, values):
    result = normalize({**values, "mode": "source"})
    result["mode"] = mode
    if mode == "measurement":
        quantity = values.get("quantity", "any")
        if quantity not in QUANTITIES:
            raise vol.Invalid("Invalid measurement quantity")
        result["quantity"] = quantity
        if values.get("unit") not in UNITS:
            raise vol.Invalid("Invalid unit")
        validate_quantity_unit(quantity, values["unit"])
        result["unit"] = values["unit"]
    return result


def review_schema(mode):
    actions = ["continue", "refresh", "rules"]
    if mode in ("source", "measurement"):
        actions.append("sources")
    if mode == "measurement":
        actions.append("calculation")
    if mode == "source":
        actions.remove("rules")
    return vol.Schema(
        {
            vol.Required("next_action", default="continue"): choice(
                actions, "air_quality_review_action"
            )
        }
    )


def recipe_from_form(mode, values):
    recipe = {**values, "mode": mode}
    if mode == "measurement":
        recipe["thresholds"] = [values.get(f"boundary_{i}") for i in range(1, 6)]
        recipe["levels"] = [
            values.get(f"grade_{i}", LEVELS[i - 1]) for i in range(1, 7)
        ]
    return normalize(recipe)


def generate(recipe):
    """Render finite measurements in a declared unit into explicit categories."""
    recipe = normalize(recipe)
    mode = recipe["mode"]
    if mode == "custom":
        return None
    if mode == "fixed":
        return "{{ " + repr(recipe["fixed"]) + " }}"
    attribute = recipe["attribute"]
    quantity_check = ""
    if mode == "measurement" and not attribute and recipe["quantity"] != "any":
        quantity_check = " and state_attr(entity_id, 'device_class') == " + repr(
            recipe["quantity"]
        )
    read = f"state_attr(entity_id, {attribute!r})" if attribute else "states(entity_id)"
    if mode == "source" and not attribute:
        read = "state_attr(entity_id, 'air_quality') if state_attr(entity_id, 'air_quality') is not none else states(entity_id)"
    body = (
        "{% set ns = namespace(grades=[], numbers=[], missing=false) %}"
        "{% for entity_id in " + repr(recipe["sources"]) + " %}"
        "{% set grade = 'unknown' %}"
        "{% if states(entity_id) not in ['unknown', 'unavailable']"
        + quantity_check
        + " %}"
        "{% set value = " + read + " %}"
    )
    if mode == "source":
        body += (
            "{% set value = value | string | trim | lower | replace('-', '_') | replace(' ', '_') %}"
            "{% if value in "
            + repr(list(LEVELS))
            + " %}{% set grade = value %}{% endif %}"
        )
    else:
        # No mass/volume conversion: ppm to micrograms needs gas-specific data.
        factors = {
            "unitless": {"": 1},
            "μg/m³": {"μg/m³": 1, "mg/m³": 1000},
            "mg/m³": {"μg/m³": 0.001, "mg/m³": 1},
            "ppm": {"ppm": 1, "ppb": 0.001},
            "ppb": {"ppm": 1000, "ppb": 1},
        }[recipe["unit"]]
        if recipe["unit"] == "unitless" and recipe["quantity"] == "aqi":
            # HA sources may explicitly label the dimensionless AQI index.
            # Do not accept this label for unrelated unitless measurements.
            factors["AQI"] = 1
        # For attributes the user explicitly declares the unit; an entity's
        # state unit can describe an unrelated primary measurement.
        unit = (
            repr("" if recipe["unit"] == "unitless" else recipe["unit"])
            if attribute
            else "(state_attr(entity_id, 'unit_of_measurement') or '') | replace('µ', 'μ')"
        )
        body += (
            "{% set unit = " + unit + " %}"
            "{% set factor = " + repr(factors) + ".get(unit) %}"
            "{% if value is not boolean and is_number(value) and factor is not none %}"
            "{% set number = (value | float) * factor %}"
            "{% if is_number(number) and number >= 0 %}"
            # Multiplication rather than exponentiation avoids overflow exceptions.
            "{% set number = ("
            + repr(recipe["quadratic"])
            + " * number + "
            + repr(recipe["multiplier"])
            + ") * number + "
            + repr(recipe["offset"])
            + " %}"
            "{% if is_number(number) and number >= 0 %}"
            "{% set ns.numbers = ns.numbers + [number] %}"
            + _classify(recipe)
            + "{% endif %}"
            "{% endif %}{% endif %}"
        )
    body += (
        "{% endif %}{% if grade == 'unknown' %}{% set ns.missing = true %}"
        "{% else %}{% set ns.grades = ns.grades + [grade] %}{% endif %}{% endfor %}"
    )
    missing = "ns.missing or " if recipe["missing"] == "unknown" else ""
    body += "{% if " + missing + "not ns.grades %}unknown"
    if mode == "measurement" and recipe["reducer"] != "per_source":
        expression = {
            "mean": "ns.numbers | average",
            "median": "ns.numbers | median",
            "minimum": "ns.numbers | min",
            "maximum": "ns.numbers | max",
        }[recipe["reducer"]]
        return (
            body
            + "{% else %}{% set number = "
            + expression
            + " %}{% if is_number(number) %}"
            + _classify(recipe)
            + "{{ grade }}{% else %}unknown{% endif %}{% endif %}"
        )
    if recipe["aggregation"] == "first":
        return body + "{% else %}{{ ns.grades[0] }}{% endif %}"
    return body + (
        "{% else %}{% set worst = namespace(rank=0) %}"
        "{% for grade in ns.grades %}{% set rank = "
        + repr(list(LEVELS))
        + ".index(grade) %}"
        "{% if rank > worst.rank %}{% set worst.rank = rank %}{% endif %}{% endfor %}"
        "{{ " + repr(list(LEVELS)) + "[worst.rank] }}{% endif %}"
    )


def _classify(recipe):
    operator = ">=" if recipe["boundary_rule"] == "lower_inclusive" else ">"
    return (
        "{% set band = namespace(index=0) %}"
        "{% for boundary in " + repr(recipe["thresholds"]) + " %}"
        "{% if number "
        + operator
        + " boundary %}{% set band.index = band.index + 1 %}{% endif %}"
        "{% endfor %}{% set grade = " + repr(recipe["levels"]) + "[band.index] %}"
    )
