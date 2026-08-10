"""Climate option migration and source-attribute helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .const import CONF_ATTRIBUTES

CLIMATE_SOURCE_ATTRIBUTE_MAP = {
    "current_humidity": "current_humidity",
    "current_temperature": "current_temperature",
    "fan_mode": "fan_mode",
    "fan_modes": "fan_modes",
    "humidity": "target_humidity",
    "hvac_action": "hvac_action",
    "hvac_modes": "hvac_modes",
    "max_humidity": "max_humidity",
    "max_temp": "max_temp",
    "min_humidity": "min_humidity",
    "min_temp": "min_temp",
    "preset_mode": "preset_mode",
    "preset_modes": "preset_modes",
    "swing_horizontal_mode": "swing_horizontal_mode",
    "swing_horizontal_modes": "swing_horizontal_modes",
    "swing_mode": "swing_mode",
    "swing_modes": "swing_modes",
    "target_humidity_step": "target_humidity_step",
    "target_temp_high": "target_temperature_high",
    "target_temp_low": "target_temperature_low",
    "target_temp_step": "target_temperature_step",
    "temperature": "target_temperature",
    "temperature_unit": "temperature_unit",
}

CLIMATE_IGNORED_SOURCE_ATTRIBUTES = frozenset({"supported_features"})

CLIMATE_MODE_LIST_FIELDS = (
    "hvac_modes",
    "fan_modes",
    "preset_modes",
    "swing_modes",
    "swing_horizontal_modes",
)

CLIMATE_CURRENT_MODE_FIELDS = {
    "fan_mode": "fan_modes",
    "preset_mode": "preset_modes",
    "swing_mode": "swing_modes",
    "swing_horizontal_mode": "swing_horizontal_modes",
}

CLIMATE_MODE_FORM_FIELDS = (
    *CLIMATE_MODE_LIST_FIELDS,
    *CLIMATE_CURRENT_MODE_FIELDS,
)

CLIMATE_SCALAR_FORM_FIELDS = (
    "current_humidity",
    "current_temperature",
    "hvac_action",
    "max_humidity",
    "max_temp",
    "min_humidity",
    "min_temp",
    "target_humidity",
    "target_humidity_step",
    "target_temperature",
    "target_temperature_high",
    "target_temperature_low",
    "target_temperature_step",
    "temperature_unit",
)

CLIMATE_FORM_FIELDS = (*CLIMATE_MODE_FORM_FIELDS, *CLIMATE_SCALAR_FORM_FIELDS)


def extract_climate_options(
    attributes: Mapping,
) -> tuple[dict[str, Any], set[str]]:
    """Convert Home Assistant climate state attributes to native options."""
    options = {}
    consumed = set(CLIMATE_IGNORED_SOURCE_ATTRIBUTES & attributes.keys())
    for source_key, option_key in CLIMATE_SOURCE_ATTRIBUTE_MAP.items():
        if source_key not in attributes:
            continue
        consumed.add(source_key)
        value = attributes[source_key]
        if value is not None:
            options[option_key] = value
    return options, consumed


def migrate_legacy_climate_attributes(config: Mapping) -> dict[str, Any]:
    """Promote climate options previously copied as virtual attributes."""
    migrated = dict(config)
    attributes = migrated.get(CONF_ATTRIBUTES)
    if not isinstance(attributes, Mapping):
        return migrated

    options, consumed = extract_climate_options(attributes)
    for key, value in options.items():
        current = migrated.get(key)
        if key in CLIMATE_MODE_LIST_FIELDS:
            if not current and value:
                migrated[key] = value
        elif key in CLIMATE_CURRENT_MODE_FIELDS:
            if current in (None, "") and value not in (None, ""):
                migrated[key] = value
        elif key not in migrated or current is None:
            migrated[key] = value

    remaining_attributes = {
        key: value for key, value in attributes.items() if key not in consumed
    }
    if remaining_attributes:
        migrated[CONF_ATTRIBUTES] = remaining_attributes
    else:
        migrated.pop(CONF_ATTRIBUTES, None)
    return migrated
