"""Humidifier option migration and source-attribute helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .const import CONF_ATTRIBUTES

HUMIDIFIER_MODE_LIST_FIELD = "modes"
HUMIDIFIER_CURRENT_MODE_FIELD = "mode"
HUMIDIFIER_FORM_FIELDS = (
    "class",
    "action",
    "current_humidity",
    "max_humidity",
    "min_humidity",
    HUMIDIFIER_MODE_LIST_FIELD,
    HUMIDIFIER_CURRENT_MODE_FIELD,
    "target_humidity",
    "target_humidity_step",
)

HUMIDIFIER_SOURCE_ATTRIBUTE_MAP = {
    "action": "action",
    "available_modes": HUMIDIFIER_MODE_LIST_FIELD,
    "current_humidity": "current_humidity",
    "device_class": "class",
    "humidity": "target_humidity",
    "max_humidity": "max_humidity",
    "min_humidity": "min_humidity",
    "mode": HUMIDIFIER_CURRENT_MODE_FIELD,
    "target_humidity_step": "target_humidity_step",
}

HUMIDIFIER_IGNORED_SOURCE_ATTRIBUTES = frozenset({"supported_features"})


def extract_humidifier_options(
    attributes: Mapping,
) -> tuple[dict[str, Any], set[str]]:
    """Convert Home Assistant humidifier attributes to native options."""
    options: dict[str, Any] = {}
    consumed = set(HUMIDIFIER_IGNORED_SOURCE_ATTRIBUTES & attributes.keys())
    for source_key, option_key in HUMIDIFIER_SOURCE_ATTRIBUTE_MAP.items():
        if source_key not in attributes:
            continue
        consumed.add(source_key)
        value = attributes[source_key]
        if value is not None:
            options[option_key] = (
                list(value)
                if option_key == HUMIDIFIER_MODE_LIST_FIELD
                and isinstance(value, (list, tuple))
                else value
            )
    return options, consumed


def migrate_legacy_humidifier_attributes(config: Mapping) -> dict[str, Any]:
    """Promote humidifier options previously copied as virtual attributes."""
    migrated = dict(config)
    attributes = migrated.get(CONF_ATTRIBUTES)
    if not isinstance(attributes, Mapping):
        return migrated

    options, consumed = extract_humidifier_options(attributes)
    for key, value in options.items():
        current = migrated.get(key)
        if key == HUMIDIFIER_MODE_LIST_FIELD:
            if not current and value:
                migrated[key] = value
        elif key == HUMIDIFIER_CURRENT_MODE_FIELD:
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
