"""UI-only air-quality recipes and editable Jinja helper generation."""

import math
import re
from collections.abc import Mapping
from itertools import pairwise

import voluptuous as vol
from homeassistant.data_entry_flow import section
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import selector

LEVELS = ("good", "fair", "moderate", "poor", "very_poor", "extremely_poor")
MODES = ("automatic", "source", "measurement", "fixed", "custom")
UNITS = ("unitless", "μg/m³", "mg/m³", "ppm", "ppb", "Bq/m³", "pCi/L")
# Spelling aliases only: never infer a missing numerator or convert gas mass
# concentrations into ppm. Preserve SI prefix case (mg is not Mg).
UNIT_ALIASES = {unit: unit for unit in (*UNITS, "", "AQI")}
for _prefix, _canonical in (("mg", "mg"), ("ug", "μg"), ("µg", "μg"), ("μg", "μg"), ("Bq", "Bq")):
    for _volume in ("m3", "m^3", "m³"):
        UNIT_ALIASES[f"{_prefix}/{_volume}"] = f"{_canonical}/m³"


def normalize_unit(value):
    """Normalize equivalent spellings without guessing physical dimensions."""
    if value is None:
        return ""
    if not isinstance(value, str):
        return None
    value = value.strip()
    return UNIT_ALIASES.get(value, value)


def source_unit_expression(entity_id_expression="entity_id"):
    """Jinja equivalent of normalize_unit for an already escaped entity ID."""
    raw = f"state_attr({entity_id_expression}, 'unit_of_measurement')"
    text = f"(({raw} if {raw} is not none else '') | string | trim)"
    return repr(UNIT_ALIASES) + f".get({text}, {text})"

REDUCERS = ("per_source", "mean", "median", "minimum", "maximum")
QUANTITIES = (
    "any",
    "radon",
    "formaldehyde",
    "pm1",
    "pm25",
    "pm4",
    "pm10",
    "aqi",
    "carbon_dioxide",
    "carbon_monoxide",
    "ozone",
    "nitrogen_dioxide",
    "nitrogen_monoxide",
    "nitrous_oxide",
    "sulphur_dioxide",
    "volatile_organic_compounds",
    "volatile_organic_compounds_parts",
    "benzene",
    "ammonia",
    "hydrogen_sulfide",
)

# Integration-specific quantities, not invented Home Assistant device classes.
CUSTOM_QUANTITIES = frozenset({"benzene", "ammonia", "hydrogen_sulfide"})

# Legacy PM0.1/nitrogen_oxide attributes have ambiguous measurement semantics.
# Never alias PM1 to PM0.1 or NO2 to N2O.
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
    if quantity == "radon":
        allowed = ("Bq/m³", "pCi/L")
    elif quantity in ("pm1", "pm25", "pm4", "pm10", "nitrous_oxide", "volatile_organic_compounds"):
        allowed = ("μg/m³", "mg/m³")
    elif quantity == "aqi":
        allowed = ("unitless",)
    elif quantity == "volatile_organic_compounds_parts":
        allowed = ("ppm", "ppb")
    elif quantity != "any":
        allowed = ("μg/m³", "mg/m³", "ppm", "ppb")
    if unit not in allowed:
        raise vol.Invalid("Unit does not match measured quantity")


# Editable display bands, not a certification or exposure assessment. PM bands
# borrow EPA concentration breakpoints, without calculating a time-averaged AQI.
# Radon bands are user-requested display intervals, NOT official health categories.
STARTER_PROFILES = {
    "benzene": ("μg/m³", (1, 2, 5, 10, 20), "Local benzene display bands; not health limits"),
    "ammonia": ("ppm", (0.1, 0.2, 0.5, 1, 2), "Local ammonia display bands; not health limits"),
    "hydrogen_sulfide": ("ppm", (0.005, 0.01, 0.02, 0.05, 0.1), "Local hydrogen sulfide display bands; not health limits"),
    "pm1": ("μg/m³", (5, 10, 20, 35, 55), "Local PM1 display bands; not PM2.5 AQI or health limits"),
    "pm4": ("μg/m³", (9, 35.4, 55.4, 125.4, 225.4), "PM2.5-shaped display proxy for PM4; not PM4 health limits or official AQI"),
    "nitrous_oxide": ("μg/m³", (1000, 2000, 4000, 8000, 16000), "Local N2O display bands; not health limits or a NIOSH occupational exposure assessment"),
    "ozone": ("ppb", (20, 40, 60, 80, 100), "Local editable display bands; not health limits"),
    "sulphur_dioxide": ("ppb", (20, 40, 80, 160, 320), "Local editable display bands; not health limits"),
    "nitrogen_monoxide": ("ppb", (20, 40, 80, 160, 320), "Local editable display bands; not health limits"),
    "volatile_organic_compounds_parts": ("ppb", (50, 100, 200, 400, 800), "Local editable display bands; not equivalent to VOC mass or health limits"),
    "pm25": ("μg/m³", (9, 35.4, 55.4, 125.4, 225.4), "EPA PM2.5 concentration breakpoints; no time averaging"),
    "pm10": ("μg/m³", (54, 154, 254, 354, 424), "EPA PM10 concentration breakpoints; no time averaging"),
    "carbon_monoxide": ("ppm", (4.4, 9.4, 12.4, 15.4, 30.4), "EPA CO concentration breakpoints; no time averaging"),
    "nitrogen_dioxide": ("ppb", (53, 100, 360, 649, 1249), "EPA NO2 concentration breakpoints; no time averaging"),
    "aqi": ("unitless", (50, 150, 250, 350, 450), "matterbridge-hass 1.5.0 AQI mapping"),
    "radon": ("Bq/m³", (50, 75, 100, 125, 148), "Local radon display bands: below 50 good, 148 or above extremely_poor; not official health categories"),
    "formaldehyde": ("mg/m³", (0.02, 0.04, 0.06, 0.08, 0.10), "Local indoor HCHO display bands; WHO 0.1 mg/m³ is a 30-minute guideline, not an instantaneous six-grade scale"),
    "carbon_dioxide": ("ppm", (600, 800, 1100, 1400, 2000), "Local CO2 display bands: 1100 or above poor, 1400 or above very_poor; not health limits"),
    "volatile_organic_compounds": ("μg/m³", (200, 300, 500, 750, 950), "Local indoor TVOC display bands; UBA 950 μg/m³ precautionary reference is not a health threshold or six-grade scale"),
}
NAME_HINTS = {
    "pm25": r"(?:pm|particulate[ _-]*matter)[ _.-]*2[ _.-]*5|초미세먼지",
    "pm10": r"(?:pm|particulate[ _-]*matter)[ _.-]*10",
    "pm4": r"(?:pm|particulate[ _-]*matter)[ _.-]*4(?:[_.]0)?(?![0-9]|[_.][0-9])",
    "pm1": r"(?:pm|particulate[ _-]*matter)[ _.-]*1(?:[_.]0)?(?![0-9]|[_.][0-9])",
    "radon": r"radon|라돈",
    "formaldehyde": r"formaldehyde|hcho|포름알데히드",
    "carbon_dioxide": r"carbon[ _-]*dioxide|co2|co₂|이산화탄소",
    "carbon_monoxide": r"carbon[ _-]*monoxide|co(?![ _-]*[0-9₂])|일산화탄소",
    "nitrogen_dioxide": r"nitrogen[ _-]*dioxide|no2|no₂|이산화질소",
    "nitrogen_monoxide": r"nitrogen[ _-]*monoxide|nitric[ _-]*oxide|일산화질소",
    "nitrous_oxide": r"nitrous[ _-]*oxide|n2o|n₂o|아산화질소",
    "sulphur_dioxide": r"sulphur[ _-]*dioxide|sulfur[ _-]*dioxide|so2|so₂|이산화황",
    "ozone": r"ozone|o3|o₃|오존",
    "volatile_organic_compounds": r"e[ _-]*tvoc|tvoc|voc|휘발성",
    "benzene": r"benzene|c6h6|c₆h₆|벤젠",
    "ammonia": r"ammonia|nh3|nh₃|암모니아",
    "hydrogen_sulfide": r"hydrogen[ _-]*sulfide|hydrogen[ _-]*sulphide|h2s|h₂s|황화수소",
    "aqi": r"aqi|air[ _-]*quality[ _-]*index",
}


def infer_quantity(state):
    """Metadata wins; ambiguous token matches never guess a pollutant."""
    declared = state.attributes.get("device_class")
    if declared:
        return declared if declared in QUANTITIES[1:] else None
    name = f"{state.entity_id.split('.', 1)[-1]} {state.attributes.get('friendly_name', '')}".lower()
    matches = {key for key, pattern in NAME_HINTS.items()
               if re.search(r"(?<![a-z0-9])(?:" + pattern + r")(?![a-z0-9])", name)}
    if matches == {"volatile_organic_compounds"} and normalize_unit(state.attributes.get("unit_of_measurement")) in ("ppm", "ppb"):
        return "volatile_organic_compounds_parts"
    return next(iter(matches)) if len(matches) == 1 else None


def prefill_measurement(defaults, states):
    """Fill only absent fields; never rewrite stored or rejected user values."""
    result = dict(defaults)
    if result.get("attribute"):
        return result, "Explicit attribute selected; no assumptions from the primary state."
    quantities = {infer_quantity(state) for state in states if state is not None}
    if len(quantities) != 1 or None in quantities or not states or any(state is None for state in states):
        return result, "No unambiguous profile; enter your own thresholds."
    quantity = next(iter(quantities))
    if result.get("quantity", "any") not in ("any", quantity):
        return result, "Selected quantity differs from source metadata; no preset applied."
    if quantity not in STARTER_PROFILES:
        return result, "No preset for this measurement; enter your own thresholds."
    unit, thresholds, label = STARTER_PROFILES[quantity]
    source_unit = normalize_unit(states[0].attributes.get("unit_of_measurement") or unit)
    target_unit = normalize_unit(result["unit"]) if "unit" in result else None
    if "unit" not in result:
        target_unit = source_unit if source_unit in UNITS else unit
        result["unit"] = target_unit
    factors = {("μg/m³", "mg/m³"): 0.001, ("mg/m³", "μg/m³"): 1000,
               ("ppm", "ppb"): 1000, ("ppb", "ppm"): 0.001,
               ("Bq/m³", "pCi/L"): 1 / 37, ("pCi/L", "Bq/m³"): 37}
    factor = 1 if target_unit == unit else factors.get((unit, target_unit))
    if factor is None:
        return result, "Preset unit is incompatible; enter your own thresholds."
    if not result.get("quantity") or result["quantity"] == "any":
        # Custom quantities explicitly support classless sources. Other
        # name-only inference cannot satisfy a strict device_class filter.
        result["quantity"] = quantity if quantity in CUSTOM_QUANTITIES or all(s.attributes.get("device_class") == quantity for s in states) else "any"
    if quantity in ("radon", "carbon_dioxide", "formaldehyde", "volatile_organic_compounds") and "thresholds" not in result:
        result.setdefault("boundary_rule", "lower_inclusive")
    if "thresholds" not in result:
        result["thresholds"] = [round(value * factor, 10) for value in thresholds]
    if quantity == "aqi":
        result.setdefault("boundary_rule", "lower_inclusive")
    return result, f"{quantity} / {target_unit}: {label}"


def automatic_recipe(sources, states, previous=None):
    """Classify each pollutant separately; never average unlike concentrations."""
    previous = previous or {}
    saved = {item["sources"][0]: item for item in previous.get("measurements", [])}
    measurements = []
    for entity_id, state in zip(sources, states, strict=True):
        if entity_id in saved:
            measurements.append(saved[entity_id])
            continue
        if state is None or infer_quantity(state) not in STARTER_PROFILES:
            continue
        # AQI already has bounded 0..500 handling in the category converter.
        if infer_quantity(state) == "aqi":
            continue
        if normalize_unit(state.attributes.get("unit_of_measurement")) not in UNITS:
            continue
        values, _ = prefill_measurement({"mode": "measurement", "sources": [entity_id]}, [state])
        if "thresholds" in values:
            measurements.append(normalize(values))
    return normalize({"mode": "automatic", "sources": sources, "measurements": measurements,
                      **({"source_roots": previous["source_roots"]}
                         if previous.get("scope") == "leaves" and "source_roots" in previous else {}),
                      "per_source": previous.get("per_source", False),
                      "scope": previous.get("scope", "sources"),
                      "missing": previous.get("missing", "skip")})


def expand_sources(sources, source_map):
    """Expand configured virtual dependencies once, rejecting cycles and excess."""
    leaves = []
    visited = set()
    def visit(entity_id, path):
        if entity_id in path or len(path) >= 16:
            raise vol.Invalid("Cyclic or excessively deep source graph")
        if entity_id in visited:
            return
        children = source_map.get(entity_id, [])
        if not isinstance(children, (list, tuple, set)) or len(children) > 64:
            raise vol.Invalid("Invalid source graph")
        if children:
            for child in children:
                visit(cv.entity_id(child), (*path, entity_id))
        elif entity_id not in leaves:
            leaves.append(entity_id)
            if len(leaves) > 64:
                raise vol.Invalid("Too many source entities")
        visited.add(entity_id)
        if len(visited) > 1024:
            raise vol.Invalid("Source graph is too large")
    for entity_id in sources:
        visit(cv.entity_id(entity_id), ())
    return leaves


def rebind_combined(recipe, entity_id):
    """A measurement-result recipe follows the stable parent after an ID edit."""
    recipe = normalize(recipe)
    if recipe.get("scope") != "combined":
        return recipe
    profiles = recipe.get("measurements", [])
    return normalize({**recipe, "sources": [entity_id], "measurements": [
        {**profiles[0], "sources": [entity_id]}
    ] if profiles else []})


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
        thresholds = thresholds if isinstance(thresholds, (list, tuple)) else []
        levels = measurement_form_values(defaults)["levels"]
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
    if not isinstance(sources, list) or not (0 if mode == "automatic" else 1) <= len(sources) <= 64:
        raise vol.Invalid("Select 1 to 64 sources")
    sources = list(dict.fromkeys(cv.entity_id(value) for value in sources))
    if mode == "automatic":
        result = {"mode": mode, "sources": sources}
        for key, allowed in (
            ("scope", ("combined", "sources", "leaves")),
            ("missing", ("skip", "unknown")),
        ):
            if key in recipe:
                if recipe[key] not in allowed:
                    raise vol.Invalid("Invalid automatic source policy")
                result[key] = recipe[key]
        if result.get("scope") == "combined" and len(sources) != 1:
            raise vol.Invalid("Combined measurement requires one source")
        if result.get("scope") == "leaves" and "source_roots" in recipe:
            roots = recipe["source_roots"]
            if not isinstance(roots, list) or not 1 <= len(roots) <= 64:
                raise vol.Invalid("Select 1 to 64 source roots")
            result["source_roots"] = list(dict.fromkeys(cv.entity_id(value) for value in roots))
        if "per_source" in recipe:
            if not isinstance(recipe["per_source"], bool):
                raise vol.Invalid("Invalid per-source setting")
            result["per_source"] = recipe["per_source"]
        if "measurements" in recipe:
            measurements = recipe["measurements"]
            if not isinstance(measurements, list) or len(measurements) > len(sources):
                raise vol.Invalid("Invalid automatic measurement profiles")
            normalized = []
            seen = set()
            for item in measurements:
                if not isinstance(item, Mapping) or item.get("mode") != "measurement":
                    raise vol.Invalid("Expected a measurement profile")
                item = normalize(item)
                if len(item["sources"]) != 1 or item["sources"][0] not in sources or item["sources"][0] in seen:
                    raise vol.Invalid("Invalid profile source")
                seen.add(item["sources"][0])
                normalized.append(item)
            result["measurements"] = normalized
        return result
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
        unit = normalize_unit(recipe.get("unit"))
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


def setup_schema(defaults):
    """One measurement screen; only sources, unit and boundaries are expanded."""
    basic = {"sources", "unit", *(f"boundary_{i}" for i in range(1, 6))}
    fields = logic_schema("measurement", defaults).schema
    advanced = {key: value for key, value in fields.items() if key.schema not in basic}
    advanced.update(calculation_schema(defaults).schema)
    return vol.Schema({
        **{key: value for key, value in fields.items() if key.schema in basic},
        vol.Optional("advanced", default=dict): section(vol.Schema(advanced), {"collapsed": True}),
    })


def threshold_schema(mode, defaults):
    keys = {"sources", "attribute", "aggregation", "missing", "unit", "quantity"}
    fields = {
            key: value
            for key, value in logic_schema(mode, defaults).schema.items()
            if key.schema not in keys
    }
    if mode == "measurement":
        fields[vol.Optional("calibration", default=dict)] = section(
            calculation_schema(defaults), {"collapsed": True}
        )
    return vol.Schema(fields)


def normalize_sources(mode, values):
    result = normalize({**values, "mode": "source"})
    result["mode"] = mode
    if mode == "measurement":
        quantity = values.get("quantity", "any")
        if quantity not in QUANTITIES:
            raise vol.Invalid("Invalid measurement quantity")
        result["quantity"] = quantity
        unit = normalize_unit(values.get("unit"))
        if unit not in UNITS:
            raise vol.Invalid("Invalid unit")
        validate_quantity_unit(quantity, unit)
        result["unit"] = unit
    return result


def review_schema(mode):
    actions = ["continue", "refresh", "rules"]
    if mode == "source":
        actions.append("sources")
    if mode == "source":
        actions.remove("rules")
    return vol.Schema(
        {
            vol.Required("next_action", default="continue"): choice(
                actions, "air_quality_review_action"
            )
        }
    )


def measurement_form_values(values):
    """Overlay submitted fields without resetting untouched saved intervals.

    Explicit empty/invalid input must survive so validation can reject it.
    Missing entries in damaged legacy arrays remain repairable in the form.
    """
    result = dict(values)
    for key, prefix, defaults in (
        ("thresholds", "boundary", [None] * 5),
        ("levels", "grade", LEVELS),
    ):
        previous = values.get(key)
        previous = previous if isinstance(previous, (list, tuple)) else []
        result[key] = [
            values.get(f"{prefix}_{i + 1}", previous[i] if i < len(previous) else default)
            for i, default in enumerate(defaults)
        ]
    return result


def recipe_from_form(mode, values):
    recipe = {**values, "mode": mode}
    if mode == "measurement":
        recipe = measurement_form_values(recipe)
    return normalize(recipe)


def generate(recipe):
    """Render finite measurements in a declared unit into explicit categories."""
    recipe = normalize(recipe)
    mode = recipe["mode"]
    if mode == "custom":
        return None
    if mode == "fixed":
        return "{{ " + repr(recipe["fixed"]) + " }}"
    if mode == "automatic":
        # Match matterbridge-hass 1.5.0 category aliases and its AQI scale,
        # not concentration thresholds or numeric Matter enum values.
        aliases = {**{level: level for level in LEVELS},
                   "excellent": "good", "healthy": "good", "fine": "good",
                   "unhealthy_for_sensitive_groups": "poor", "unhealthy": "very_poor",
                   "very_unhealthy": "extremely_poor", "hazardous": "extremely_poor"}
        # Macros isolate each measurement's namespace from the overall rank.
        profiles = recipe.get("measurements", [])
        macros = "".join(
            "{% macro measurement_" + str(i) + "() %}" + generate(item) + "{% endmacro %}"
            for i, item in enumerate(profiles)
        )
        overrides = "{% set overrides = {" + ", ".join(
            repr(item["sources"][0]) + ": measurement_" + str(i) + "() | trim"
            for i, item in enumerate(profiles)
        ) + "} %}"
        return macros + (
            overrides + "{% set ns = namespace(rank=-1, missing=false) %}{% for entity_id in " + repr(recipe["sources"]) + " %}"
            "{% set rank = -1 %}"
            "{% if states(entity_id) not in ['unknown', 'unavailable'] and not state_attr(entity_id, 'air_quality_stale') %}"
            "{% if entity_id in overrides %}"
            "{% set category = overrides[entity_id] %}"
            "{% if category in " + repr(list(LEVELS)) + " %}"
            "{% set rank = " + repr(list(LEVELS)) + ".index(category) %}{% endif %}"
            "{% else %}"
            "{% set raw = state_attr(entity_id, 'air_quality') %}"
            "{% set raw = states(entity_id) if raw is none else raw %}"
            "{% set category = " + repr(aliases) + ".get(raw | string | trim | lower | replace('-', '_') | replace(' ', '_')) %}"
            "{% set rank = -1 %}{% if category is not none %}"
            "{% set rank = " + repr(list(LEVELS)) + ".index(category) %}"
            "{% else %}{% set index = state_attr(entity_id, 'air_quality_index') %}"
            "{% if index is none and state_attr(entity_id, 'device_class') == 'aqi' "
            "and " + source_unit_expression() + " in ['', 'AQI'] %}"
            "{% set index = states(entity_id) %}{% endif %}"
            "{% if index is not boolean and is_number(index) and 0 <= (index | float) <= 500 %}"
            "{% set rank = ((index | float) / 100 + 0.5) | round(0, 'floor') | int %}"
            "{% endif %}{% endif %}{% endif %}{% endif %}"
            "{% if rank > ns.rank %}{% set ns.rank = rank %}{% endif %}"
            "{% if rank < 0 %}{% set ns.missing = true %}{% endif %}"
            "{% endfor %}{{ " + repr(list(LEVELS)) + "[ns.rank] if ns.rank >= 0"
            + (" and not ns.missing" if recipe.get("missing") == "unknown" else "")
            + " else 'unknown' }}"
        )
    attribute = recipe["attribute"]
    quantity_check = ""
    if mode == "measurement" and not attribute and recipe["quantity"] != "any":
        quantity_check = " and state_attr(entity_id, 'device_class') == " + repr(
            recipe["quantity"]
        )
        if recipe["quantity"] in CUSTOM_QUANTITIES:
            # Explicit UI quantity supplies semantics for classless gas sensors.
            # Never reinterpret a sensor declaring a different device class.
            quantity_check = " and state_attr(entity_id, 'device_class') in " + repr(
                [None, "", recipe["quantity"]]
            )
    read = f"state_attr(entity_id, {attribute!r})" if attribute else "states(entity_id)"
    if mode == "source" and not attribute:
        read = "state_attr(entity_id, 'air_quality') if state_attr(entity_id, 'air_quality') is not none else states(entity_id)"
    body = (
        "{% set ns = namespace(grades=[], numbers=[], missing=false) %}"
        "{% for entity_id in " + repr(recipe["sources"]) + " %}"
        "{% set grade = 'unknown' %}"
        "{% if states(entity_id) not in ['unknown', 'unavailable']"
        " and not state_attr(entity_id, 'air_quality_stale')"
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
            "Bq/m³": {"Bq/m³": 1, "pCi/L": 37},
            "pCi/L": {"Bq/m³": 1 / 37, "pCi/L": 1},
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
            else source_unit_expression()
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
