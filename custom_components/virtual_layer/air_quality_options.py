"""UI-only air-quality recipes and editable Jinja helper generation."""

import math
import re
import unicodedata
from collections.abc import Mapping
from itertools import pairwise

import voluptuous as vol
from homeassistant.data_entry_flow import section
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import selector

LEVELS = ("good", "fair", "moderate", "poor", "very_poor", "extremely_poor")
MODES = ("automatic", "source", "measurement", "fixed", "custom")
UNITS = ("unitless", "μg/m³", "mg/m³", "ppm", "ppb", "Bq/m³", "pCi/L")
# TVOC mixture approximation (110 g/mol), not a universal gas conversion.
VOC_QUANTITIES = ("volatile_organic_compounds", "volatile_organic_compounds_parts")
VOC_MG_FACTORS = {"mg/m³": 1.0, "μg/m³": 0.001, "ppb": 0.0045, "ppm": 4.5}


def canonical_quantity(quantity):
    """Treat VOC mass and parts metadata as one convertible measurement."""
    return VOC_QUANTITIES[0] if quantity in VOC_QUANTITIES else quantity


def voc_factors(unit):
    """Return source-to-target factors for the agreed TVOC approximation."""
    return {source: factor / VOC_MG_FACTORS[unit] for source, factor in VOC_MG_FACTORS.items()}

# Spelling aliases only: never infer a missing numerator or convert gas mass
# concentrations into ppm. Preserve SI prefix case (mg is not Mg).
UNIT_ALIASES = {unit: unit for unit in (*UNITS, "", "AQI")}
_UNIT_SPELLINGS = {
    "μS/cm": ("uS/cm", "µS/cm", "µS / cm", "uS / cm", "μS / cm", "microsiemens/cm", "microsiemens per centimeter"),
    "mS/cm": ("mS / cm", "millisiemens/cm", "millisiemens per centimeter"),
    "S/cm": ("S / cm", "siemens/cm", "siemens per centimeter"),
    "mg/L": ("mg/l", "mg / L", "mg / l", "milligrams per liter", "milligrams per litre"),
    "NTU": ("ntu",),
    "°C": ("℃", "° C", "degC", "deg C", "celsius", "Celsius", "celcius", "Celcius"),
    "°F": ("℉", "° F", "degF", "deg F", "fahrenheit", "Fahrenheit", "farenheit"),
    "%": ("％", "percent", "percentage"),
    "ppm": ("PPM", "parts per million"),
    "ppb": ("PPB", "parts per billion"),
    "μg/m³": ("micrograms/m3", "micrograms per cubic meter", "micrograms per cubic metre"),
    "mg/m³": ("milligrams/m3", "milligrams per cubic meter", "milligrams per cubic metre"),
    "pCi/L": ("pCi/l", "pCi / L", "pCi / l"),
    "m³": ("m3", "m^3", "㎥"),
    "m²": ("m2", "m^2", "㎡"),
    "kWh": ("kW h", "kW·h", "kW⋅h", "kW-h"),
    "Wh": ("W h", "W·h", "W⋅h", "W-h"),
    "km/h": ("km / h", "kmh", "kph"),
    "m/s": ("m / s", "m/sec"),
    "hPa": ("hpa",),
    "lux": ("lx",),
    "K": ("kelvin", "Kelvin", "켈빈"),
    "W": ("watt", "watts", "와트"),
    "kW": ("kilowatt", "kilowatts", "킬로와트", "㎾"),
    "mW": ("milliwatt", "milliwatts"),
    "MW": ("megawatt", "megawatts"),
    "J": ("joule", "joules"),
    "kJ": ("kilojoule", "kilojoules"),
    "A": ("amp", "amps", "ampere", "amperes", "암페어"),
    "mA": ("milliamp", "milliamps", "milliampere"),
    "V": ("volt", "volts", "볼트"),
    "mV": ("millivolt", "millivolts"),
    "kV": ("kilovolt", "kilovolts"),
    "Hz": ("hertz", "헤르츠"),
    "kHz": ("kilohertz",),
    "MHz": ("megahertz",),
    "Ω": ("Ω", "ohm", "ohms", "옴"),
    "kΩ": ("kΩ", "kohm", "kiloohm", "kiloohms"),
    "Pa": ("pascal", "pascals"),
    "kPa": ("kilopascal", "kilopascals"),
    "mbar": ("millibar", "millibars"),
    "mmHg": ("mm Hg",),
    "inHg": ("in Hg",),
    "inH₂O": ("inH2O", "in H2O", "in H₂O"),
    "g": ("gram", "grams", "그램"),
    "kg": ("kilogram", "kilograms", "킬로그램", "㎏"),
    "mg": ("milligram", "milligrams", "㎎"),
    "μg": ("ug", "µg", "microgram", "micrograms"),
    "lb": ("lbs", "pound", "pounds"),
    "oz": ("ounce", "ounces"),
    "mm": ("millimeter", "millimeters", "millimetre", "millimetres", "㎜"),
    "cm": ("centimeter", "centimeters", "centimetre", "centimetres", "㎝"),
    "m": ("meter", "meters", "metre", "metres", "미터"),
    "km": ("kilometer", "kilometers", "kilometre", "kilometres", "㎞"),
    "in": ("inch", "inches"),
    "ft": ("foot", "feet"),
    "yd": ("yard", "yards"),
    "mi": ("mile", "miles"),
    "L": ("l", "ℓ", "liter", "liters", "litre", "litres", "리터"),
    "mL": ("ml", "mℓ", "milliliter", "milliliters", "millilitre", "millilitres", "㎖"),
    "s": ("sec", "secs", "second", "seconds", "초"),
    "ms": ("msec", "millisecond", "milliseconds"),
    "μs": ("us", "µs", "microsecond", "microseconds"),
    "min": ("mins", "minute", "minutes", "분"),
    "h": ("hr", "hrs", "hour", "hours", "시간"),
    "d": ("day", "days", "일"),
    "w": ("week", "weeks"),
    "kn": ("kt", "kts", "knot", "knots"),
    "B": ("byte", "bytes"),
    "bit": ("bits",),
    "kB": ("kilobyte", "kilobytes"),
    "MB": ("megabyte", "megabytes"),
    "GB": ("gigabyte", "gigabytes"),
    "KiB": ("kibibyte", "kibibytes"),
    "MiB": ("mebibyte", "mebibytes"),
    "GiB": ("gibibyte", "gibibytes"),
    "bit/s": ("bps", "bits/s", "bits per second"),
    "kbit/s": ("kbps", "kbits/s"),
    "Mbit/s": ("Mbps", "Mbits/s"),
    "Gbit/s": ("Gbps", "Gbits/s"),
    "B/s": ("Bps", "bytes/s", "bytes per second"),
    "kB/s": ("kBps",),
    "MB/s": ("MBps",),
    "GB/s": ("GBps",),
}
for _canonical, _spellings in _UNIT_SPELLINGS.items():
    UNIT_ALIASES[_canonical] = _canonical
    UNIT_ALIASES.update(dict.fromkeys(_spellings, _canonical))
for _prefix, _canonical in (("mg", "mg"), ("ug", "μg"), ("µg", "μg"), ("μg", "μg"), ("Bq", "Bq")):
    for _volume in ("m3", "m^3", "m³", "㎥"):
        for _separator in ("/", " /", "/ ", " / "):
            UNIT_ALIASES[f"{_prefix}{_separator}{_volume}"] = f"{_canonical}/m³"

# Generate only declared dimensional variants; no global lowercasing or
# Unicode compatibility folding (which could confuse SI prefixes and symbols).
for _base in ("mm", "cm", "m", "km", "in", "ft", "yd", "mi"):
    for _suffix, _power in (("²", "2"), ("³", "3")):
        _canonical = _base + _suffix
        for _alias in (_canonical, _base + _power, _base + "^" + _power):
            UNIT_ALIASES[_alias] = _canonical
for _prefix in ("m", "", "k", "M", "G", "T"):
    _canonical = _prefix + "Wh"
    for _joiner in ("", " ", "·", "⋅", "-"):
        UNIT_ALIASES[_prefix + "W" + _joiner + "h"] = _canonical
for _numerator in ("L", "mL", "m³", "ft³", "gal", "m", "mm", "in", "ft", "km"):
    for _denominator in ("s", "min", "h", "d"):
        _canonical = f"{_numerator}/{_denominator}"
        _numerators = {
            "L": ("L", "l", "ℓ"), "mL": ("mL", "ml"),
            "m³": ("m³", "m3", "m^3", "㎥"), "ft³": ("ft³", "ft3", "ft^3"),
        }.get(_numerator, (_numerator,))
        _denominators = {"s": ("s", "sec"), "min": ("min",), "h": ("h", "hr"), "d": ("d",)}[_denominator]
        for _n in _numerators:
            for _d in _denominators:
                for _separator in ("/", " /", "/ ", " / "):
                    UNIT_ALIASES[f"{_n}{_separator}{_d}"] = _canonical


def normalize_unit(value):
    """Normalize equivalent spellings without guessing physical dimensions."""
    if value is None:
        return ""
    if not isinstance(value, str):
        return None
    value = " ".join(value.split())
    return UNIT_ALIASES.get(value, value)


def source_unit_expression(entity_id_expression="entity_id"):
    """Jinja equivalent of normalize_unit for an already escaped entity ID."""
    raw = f"state_attr({entity_id_expression}, 'unit_of_measurement')"
    text = f"((({raw} if {raw} is not none else '') | string).split() | join(' '))"
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
    elif quantity in VOC_QUANTITIES:
        allowed = tuple(VOC_MG_FACTORS)
    elif quantity in ("pm1", "pm25", "pm4", "pm10", "nitrous_oxide"):
        allowed = ("μg/m³", "mg/m³")
    elif quantity == "aqi":
        allowed = ("unitless",)
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
    "volatile_organic_compounds_parts": ("ppb", tuple(value / 4.5 for value in (200, 300, 500, 750, 950)), "Local TVOC mass bands converted with 1 ppb = 0.0045 mg/m³; approximate, not health limits"),
    "pm25": ("μg/m³", (9, 35.4, 55.4, 125.4, 225.4), "EPA PM2.5 concentration breakpoints; no time averaging"),
    "pm10": ("μg/m³", (54, 154, 254, 354, 424), "EPA PM10 concentration breakpoints; no time averaging"),
    "carbon_monoxide": ("ppm", (4.4, 9.4, 12.4, 15.4, 30.4), "EPA CO concentration breakpoints; no time averaging"),
    "nitrogen_dioxide": ("ppb", (53, 100, 360, 649, 1249), "EPA NO2 concentration breakpoints; no time averaging"),
    "aqi": ("unitless", (50, 150, 250, 350, 450), "matterbridge-hass 1.5.0 AQI mapping"),
    "radon": ("Bq/m³", (50, 75, 100, 125, 148), "Local radon display bands: below 50 good, 148 or above extremely_poor; not official health categories"),
    "formaldehyde": ("μg/m³", (20, 40, 60, 80, 100), "Local indoor HCHO display bands; WHO 100 μg/m³ is a 30-minute guideline, not an instantaneous six-grade scale"),
    "carbon_dioxide": ("ppm", (600, 800, 1100, 1400, 2000), "Local CO2 display bands: 1100 or above poor, 1400 or above very_poor; not health limits"),
    "volatile_organic_compounds": ("μg/m³", (200, 300, 500, 750, 950), "Local indoor TVOC display bands; UBA 950 μg/m³ precautionary reference is not a health threshold or six-grade scale"),
}
NAME_HINTS = {
    "pm25": r"(?:pm|particulate[ _-]*matter)[ _.-]*2[ _.-]*5|초[ _-]*미세[ _-]*먼지",
    "pm10": r"(?:pm|particulate[ _-]*matter)[ _.-]*10(?:[_.]0)?(?![0-9]|[_.][0-9])",
    "pm4": r"(?:pm|particulate[ _-]*matter)[ _.-]*4(?:[_.]0)?(?![0-9]|[_.][0-9])",
    "pm1": r"(?:pm|particulate[ _-]*matter)[ _.-]*1(?:[_.]0)?(?![0-9]|[_.][0-9])",
    "radon": r"radon|라돈",
    "formaldehyde": r"formaldehyde|methanal|hcho|ch2o|포름[ _-]*알데히드|포름[ _-]*알데하이드|메탄알",
    "carbon_dioxide": r"carbon[ _-]*dioxide|co2|co₂|이산화[ _-]*탄소",
    "carbon_monoxide": r"carbon[ _-]*monoxide|co(?![ _-]*[0-9₂])|일산화[ _-]*탄소",
    "nitrogen_dioxide": r"nitrogen[ _-]*dioxide|no2|no₂|이산화[ _-]*질소",
    "nitrogen_monoxide": r"nitrogen[ _-]*monoxide|nitric[ _-]*oxide|일산화[ _-]*질소",
    "nitrous_oxide": r"nitrous[ _-]*oxide|n2o|n₂o|아산화[ _-]*질소",
    "sulphur_dioxide": r"sulphur[ _-]*dioxide|sulfur[ _-]*dioxide|so2|so₂|이산화[ _-]*황",
    "ozone": r"ozone|o3|o₃|오존",
    "volatile_organic_compounds": r"e[ _-]*tvoc|t[ _-]*voc|voc|(?:total[ _-]*)?volatile[ _-]*organic[ _-]*compounds?|(?:총[ _-]*)?휘발성(?:[ _-]*유기[ _-]*화합물)?",
    "benzene": r"benzene|c6h6|c₆h₆|벤젠",
    "ammonia": r"ammonia|nh3|nh₃|암모니아",
    "hydrogen_sulfide": r"hydrogen[ _-]*sulfide|hydrogen[ _-]*sulphide|h2s|h₂s|황화[ _-]*수소",
    "aqi": r"aqi|air[ _-]*quality[ _-]*index",
}


ICON_POLLUTANT_NAME_HINTS = {
    # Historical entity-ID abbreviation, not a chemical formula/profile.
    "formaldehyde": NAME_HINTS["formaldehyde"] + r"|h2ho",
    # Name/icon recognition only: these have no automatic grading profile.
    # Do not collapse individual solvents into TVOC or share mass/ppm factors.
    "toluene": r"toluene|methyl[ _-]*benzene|톨루엔",
    "xylene": r"xylene|dimethyl[ _-]*benzene|자일렌|크실렌",
    "ethylbenzene": r"ethyl[ _-]*benzene|에틸[ _-]*벤젠",
    "styrene": r"styrene|스티렌|스타이렌",
    "acetone": r"acetone|propanone|아세톤",
    "acetaldehyde": r"acetaldehyde|ethanal|아세트[ _-]*알데히드|아세트[ _-]*알데하이드",
    "acrolein": r"acrolein|아크롤레인",
    "methanol": r"methanol|methyl[ _-]*alcohol|메탄올|메틸[ _-]*알코올",
    "methane": r"methane|ch4|메탄",
    "propane": r"propane|c3h8|프로판",
    "butane": r"butane|c4h10|부탄",
    "chlorine": r"chlorine|cl2|염소",
    "hydrogen_chloride": r"hydrogen[ _-]*chloride|hcl|염화[ _-]*수소",
    "hydrogen_cyanide": r"hydrogen[ _-]*cyanide|hcn|시안화[ _-]*수소|청산[ _-]*가스",
    "hydrogen_fluoride": r"hydrogen[ _-]*fluoride|불화[ _-]*수소|플루오린화[ _-]*수소",
    "sulfur_trioxide": r"sulphur[ _-]*trioxide|sulfur[ _-]*trioxide|so3|삼산화[ _-]*황",
    "nitrogen_oxides": r"nitrogen[ _-]*oxides|nox|질소[ _-]*산화물",
    "sulfur_oxides": r"sulphur[ _-]*oxides|sulfur[ _-]*oxides|sox|황[ _-]*산화물",
    "btex": r"btex",
}


def normalize_pollutant_name(value):
    """Unify Unicode chemical digits, letter case and name separators."""
    if not isinstance(value, str):
        return ""
    value = unicodedata.normalize("NFKC", value).casefold()
    value = re.sub(r"[\s_\-‐‑‒–—−]+", "_", value)
    return value.strip("_")


def default_air_quality_icon(platform, *names):
    """Resolve a shared UI/runtime fallback without overriding explicit icons."""
    if platform == "air_quality":
        return "mdi:air-filter"
    if platform not in ("sensor", "number", "binary_sensor"):
        return ""
    if pollutant_name_matches(*names, include_icon_only=True) or any(
        re.search(
            r"(?<![a-z0-9])(?:air[ _-]*quality|공기[ _-]*질|미세먼지)(?![a-z0-9])",
            normalize_pollutant_name(name),
        ) for name in names
    ):
        return "mdi:air-filter"
    return ""


def pollutant_name_matches(*names, include_icon_only=False):
    """Return canonical names without resolving mixed or ambiguous substances."""
    patterns = dict(NAME_HINTS)
    if include_icon_only:
        patterns.update(ICON_POLLUTANT_NAME_HINTS)
    # Match labels independently; prefer full substance names to embedded ones
    # (ethyl benzene is not benzene, and Korean methanol is not methane).
    result = set()
    for name in names:
        normalized = normalize_pollutant_name(name)
        matches = [
            (key, match.start(), match.end())
            for key, pattern in patterns.items()
            for match in re.finditer(
                r"(?<![a-z0-9])(?:" + pattern + r")(?![a-z0-9])", normalized
            )
        ]
        result.update(key for key, start, end in matches if not any(
            other_start <= start and end <= other_end
            and (other_start < start or end < other_end)
            for _, other_start, other_end in matches
        ))
    return result


def infer_quantity(state):
    """Metadata wins; ambiguous token matches never guess a pollutant."""
    declared = state.attributes.get("device_class")
    if declared:
        if declared in QUANTITIES[1:]:
            return declared
        # Older virtual formaldehyde records could inherit the generic gas or
        # volume class, which also supplied a bare m³ unit. The explicitly
        # named chemical is still unambiguous in those legacy cases.
        if not isinstance(declared, str) or declared not in {
            "gas", "volume", "volume_storage", "volume_flow_rate"
        }:
            return None
    matches = pollutant_name_matches(
        state.entity_id.split('.', 1)[-1], state.attributes.get("friendly_name"),
        include_icon_only=True,
    )
    if matches == {"volatile_organic_compounds"} and normalize_unit(state.attributes.get("unit_of_measurement")) in ("ppm", "ppb"):
        return "volatile_organic_compounds_parts"
    return next(iter(matches)) if len(matches) == 1 and matches <= set(QUANTITIES) else None


def is_air_quality_binary(state):
    """Automatically manage air alarms, not unrelated motion/door sensors."""
    if not state.entity_id.startswith("binary_sensor."):
        return False
    declared = state.attributes.get("device_class")
    if declared:
        return declared in ("carbon_monoxide", "smoke", "gas")
    if infer_quantity(state) is not None:
        return True
    name = f"{state.entity_id.split('.', 1)[-1]} {state.attributes.get('friendly_name', '')}".lower()
    return bool(re.search(r"(?<![a-z0-9])(?:smoke|gas|연기|가스)(?![a-z0-9])", name))


def prefill_measurement(defaults, states):
    """Fill only absent fields; never rewrite stored or rejected user values."""
    result = dict(defaults)
    if result.get("attribute"):
        return result, "Explicit attribute selected; no assumptions from the primary state."
    quantities = {canonical_quantity(infer_quantity(state)) for state in states if state is not None}
    if len(quantities) != 1 or None in quantities or not states or any(state is None for state in states):
        return result, "No unambiguous profile; enter your own thresholds."
    quantity = next(iter(quantities))
    if canonical_quantity(result.get("quantity", "any")) not in ("any", quantity):
        return result, "Selected quantity differs from source metadata; no preset applied."
    if quantity not in STARTER_PROFILES:
        return result, "No preset for this measurement; enter your own thresholds."
    unit, thresholds, label = STARTER_PROFILES[quantity]
    source_unit = normalize_unit(states[0].attributes.get("unit_of_measurement") or unit)
    target_unit = normalize_unit(result["unit"]) if "unit" in result else None
    if "unit" not in result:
        # Formaldehyde has a canonical UI/default presentation even when an
        # upstream device reports its concentration in mg/m³.
        target_unit = (
            "mg/m³" if quantity in VOC_QUANTITIES
            else unit if quantity == "formaldehyde"
            else source_unit if source_unit in UNITS
            else unit
        )
        result["unit"] = target_unit
    factors = {("μg/m³", "mg/m³"): 0.001, ("mg/m³", "μg/m³"): 1000,
               ("ppm", "ppb"): 1000, ("ppb", "ppm"): 0.001,
               ("Bq/m³", "pCi/L"): 1 / 37, ("pCi/L", "Bq/m³"): 37}
    factor = 1 if target_unit == unit else factors.get((unit, target_unit))
    if quantity in VOC_QUANTITIES and target_unit in VOC_MG_FACTORS:
        factor = voc_factors(target_unit)[unit]
        label += "; TVOC approximation: 1 ppb = 0.0045 mg/m³"
    if factor is None:
        return result, "Preset unit is incompatible; enter your own thresholds."
    if not result.get("quantity") or result["quantity"] == "any":
        # Custom quantities explicitly support classless sources. Other
        # name-only inference cannot satisfy a strict device_class filter.
        result["quantity"] = quantity if quantity in (*CUSTOM_QUANTITIES, *VOC_QUANTITIES) or all(s.attributes.get("device_class") == quantity for s in states) else "any"
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
        if entity_id.startswith("binary_sensor."):
            # Binary alarms have no concentration or numeric thresholds.
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


def generate(recipe, *, source_units=None):
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
            "{% macro measurement_" + str(i) + "() %}" + generate(item, source_units=source_units) + "{% endmacro %}"
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
            "{% elif entity_id.startswith('binary_sensor.') %}"
            "{% if states(entity_id) == 'on' %}{% set rank = 3 %}"
            "{% elif states(entity_id) == 'off' %}{% set rank = 0 %}{% endif %}"
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
        if recipe["quantity"] in VOC_QUANTITIES:
            quantity_check = " and state_attr(entity_id, 'device_class') in " + repr(
                [None, "", *VOC_QUANTITIES]
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
        # Only VOC has an explicitly chosen mass/volume approximation.
        factors = {
            "unitless": {"": 1},
            "μg/m³": {"μg/m³": 1, "mg/m³": 1000},
            "mg/m³": {"μg/m³": 0.001, "mg/m³": 1},
            "ppm": {"ppm": 1, "ppb": 0.001},
            "ppb": {"ppm": 1000, "ppb": 1},
            "Bq/m³": {"Bq/m³": 1, "pCi/L": 37},
            "pCi/L": {"Bq/m³": 1 / 37, "pCi/L": 1},
        }[recipe["unit"]]
        if recipe["quantity"] in VOC_QUANTITIES:
            factors = voc_factors(recipe["unit"])
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
        if not attribute and source_units:
            unit = "(" + unit + " or " + repr(source_units) + ".get(entity_id, ''))"
        body += (
            "{% set unit = " + unit + " %}"
            "{% set factor = " + repr(factors) + ".get(unit) %}"
            "{% if value is not boolean and is_number(value) and factor is not none %}"
            "{% set number = (value | float) * factor %}"
            "{% if is_number(number) and number >= 0 %}"
            + ("{% set number = number | round(12) %}" if recipe["quantity"] in VOC_QUANTITIES else "")
            # Multiplication rather than exponentiation avoids overflow exceptions.
            + "{% set number = ("
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
