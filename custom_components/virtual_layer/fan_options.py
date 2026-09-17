"""Fan option migration and source-attribute helpers."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any

from homeassistant.components.fan import FanEntityFeature

from .const import CONF_ATTRIBUTES

FAN_MODE_LIST_FIELD = "modes"

# Only explicit manual-speed families belong here. Auto/smart/nature/sleep,
# eco/pet/turbo/boost and named speed presets have device-specific behavior.
# VeSync uses normal; Xiaomi uses favorite. Keep vendor spelling for writes.
MANUAL_PRESET_PRIORITY = {
    "manual": 0, "manual mode": 0, "수동": 0, "수동 모드": 0,
    "normal": 1, "normal mode": 1, "일반": 1, "일반 모드": 1,
    "favorite": 2, "favourite": 2, "즐겨찾기": 2,
}


def _preset_key(mode: str) -> str:
    return re.sub(r"[\s_-]+", " ", mode.strip().lower())


def manual_preset_mode(modes, current=None):
    """Find an advertised manual preset without changing its spelling."""
    if not isinstance(modes, (list, tuple)):
        return None
    candidates = [mode for mode in modes if isinstance(mode, str)
                  and _preset_key(mode) in MANUAL_PRESET_PRIORITY]
    if current in candidates:
        return current
    return min(candidates, key=lambda mode: MANUAL_PRESET_PRIORITY[_preset_key(mode)], default=None)


FAN_FORM_FIELDS = (
    "speed_count",
    "oscillate",
    "direction",
    FAN_MODE_LIST_FIELD,
    "percentage",
    "preset_mode",
    "oscillating",
    "current_direction",
)

FAN_NATIVE_ATTRIBUTE_FIELDS = frozenset(
    {
        "current_direction",
        "direction",
        "oscillating",
        "percentage",
        "percentage_step",
        "preset_mode",
        "preset_modes",
        "supported_features",
    }
)


def _speed_count_from_step(value: Any) -> int | None:
    """Convert Home Assistant's percentage step to a speed count."""
    if isinstance(value, bool):
        return None
    try:
        step = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(step) or step <= 0:
        return None
    return max(1, round(100 / step))


def extract_fan_options(attributes: Mapping) -> tuple[dict[str, Any], set[str]]:
    """Convert Home Assistant fan state attributes to native options."""
    options: dict[str, Any] = {}
    consumed = set(FAN_NATIVE_ATTRIBUTE_FIELDS & attributes.keys())

    preset_modes = attributes.get("preset_modes")
    if isinstance(preset_modes, (list, tuple)):
        options[FAN_MODE_LIST_FIELD] = list(preset_modes)
    if attributes.get("preset_mode") is not None:
        options["preset_mode"] = attributes["preset_mode"]
    if attributes.get("percentage") is not None:
        options["percentage"] = attributes["percentage"]
    if isinstance(attributes.get("oscillating"), bool):
        options["oscillating"] = attributes["oscillating"]
    direction = attributes.get("current_direction", attributes.get("direction"))
    if direction in {"forward", "reverse"}:
        options["current_direction"] = direction

    speed_count = _speed_count_from_step(attributes.get("percentage_step"))
    if speed_count is not None:
        options["speed_count"] = speed_count

    configured_features = attributes.get("supported_features", 0)
    try:
        if isinstance(configured_features, bool):
            raise TypeError
        supported_features = FanEntityFeature(int(configured_features))
    except (TypeError, ValueError, OverflowError):
        supported_features = FanEntityFeature(0)
    options["oscillate"] = (
        "oscillating" in attributes
        or FanEntityFeature.OSCILLATE in supported_features
    )
    options["direction"] = (
        direction is not None or FanEntityFeature.DIRECTION in supported_features
    )
    if speed_count is None and (
        "percentage" in attributes
        or FanEntityFeature.SET_SPEED in supported_features
    ):
        options["speed_count"] = 100

    return options, consumed


def migrate_legacy_fan_attributes(config: Mapping) -> dict[str, Any]:
    """Promote fan options previously copied as virtual attributes."""
    migrated = dict(config)
    attributes = migrated.get(CONF_ATTRIBUTES)
    if not isinstance(attributes, Mapping):
        return migrated

    options, consumed = extract_fan_options(attributes)
    for key, value in options.items():
        current = migrated.get(key)
        if key in {FAN_MODE_LIST_FIELD, "speed_count", "oscillate", "direction"}:
            if not current and value:
                migrated[key] = value
        elif key in {"preset_mode", "current_direction"}:
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
