"""
This component provides support for a virtual fan.

Borrowed heavily from components/demo/fan.py
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from typing import Any

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components.fan import (
    ATTR_DIRECTION,
    ATTR_OSCILLATING,
    ATTR_PERCENTAGE,
    ATTR_PRESET_MODE,
    FanEntity,
    FanEntityFeature,
)
from homeassistant.components.fan import (
    DOMAIN as PLATFORM_DOMAIN,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.config_validation import PLATFORM_SCHEMA
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import get_entity_configs
from .const import *
from .entity import VirtualEntity, nonnegative_int, virtual_schema
from .fan_options import migrate_legacy_fan_attributes

_LOGGER = logging.getLogger(__name__)

DEPENDENCIES = [COMPONENT_DOMAIN]

CONF_DIRECTION = "direction"
CONF_MODES = "modes"
CONF_OSCILLATE = "oscillate"
CONF_PERCENTAGE = "percentage"
CONF_PRESET_MODE = "preset_mode"
CONF_CURRENT_DIRECTION = "current_direction"
CONF_OSCILLATING = "oscillating"
CONF_SPEED = "speed"
CONF_SPEED_COUNT = "speed_count"

DEFAULT_FAN_VALUE = "off"

BASE_SCHEMA = virtual_schema(
    DEFAULT_FAN_VALUE,
    {
        vol.Optional(CONF_SPEED, default=False): cv.boolean,
        vol.Optional(CONF_SPEED_COUNT, default=0): nonnegative_int,
        vol.Optional(CONF_OSCILLATE, default=False): cv.boolean,
        vol.Optional(CONF_DIRECTION, default=False): cv.boolean,
        vol.Optional(CONF_MODES, default=list): vol.All(
            cv.ensure_list, [cv.string]
        ),
        vol.Optional(CONF_PERCENTAGE): vol.All(
            nonnegative_int, vol.Range(max=100)
        ),
        vol.Optional(CONF_PRESET_MODE): cv.string,
        vol.Optional(CONF_CURRENT_DIRECTION): vol.In({"forward", "reverse"}),
        vol.Optional(CONF_OSCILLATING): cv.boolean,
    },
)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(BASE_SCHEMA)

FAN_SCHEMA = vol.Schema(BASE_SCHEMA)


def normalize_domain_options(config):
    """Promote legacy attributes before persisted-data validation."""
    return migrate_legacy_fan_attributes(config)


def validate_domain_options(config) -> None:
    """Validate fan feature and initial-state relationships."""
    if str(config.get(CONF_INITIAL_VALUE, "off")).lower() not in {"on", "off"}:
        raise vol.Invalid("initial_value must be on or off")
    modes = config.get(CONF_MODES, [])
    if any(not str(mode).strip() for mode in modes):
        raise vol.Invalid("modes cannot contain empty values")
    if len(set(modes)) != len(modes):
        raise vol.Invalid("modes cannot contain duplicate values")
    preset_mode = config.get(CONF_PRESET_MODE)
    if preset_mode is not None and preset_mode not in modes:
        raise vol.Invalid("preset_mode must be included in modes")
    if config.get(CONF_CURRENT_DIRECTION) is not None and not config.get(CONF_DIRECTION):
        raise vol.Invalid("current_direction requires direction support")
    if config.get(CONF_OSCILLATING) and not config.get(CONF_OSCILLATE):
        raise vol.Invalid("oscillating requires oscillation support")


async def async_setup_platform(
        hass: HomeAssistant,
        config: ConfigType,
        async_add_entities: AddEntitiesCallback,
        _discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Ignore platform setup; Virtual Layer entities are config-entry only."""
    _LOGGER.debug("ignoring platform setup")


async def async_setup_entry(
        hass: HomeAssistant,
        entry: ConfigEntry,
        async_add_entities: Callable[[list], None],
) -> None:
    _LOGGER.debug("setting up the entries...")

    entities = []
    for entity in get_entity_configs(hass, entry.data[ATTR_GROUP_NAME], PLATFORM_DOMAIN):
        entity = FAN_SCHEMA(migrate_legacy_fan_attributes(entity))
        entities.append(VirtualFan(entity, False))
    async_add_entities(entities)


class VirtualFan(VirtualEntity, FanEntity):
    """A demonstration fan component."""

    def __init__(self, config, old_style: bool):
        """Initialize the entity."""
        super().__init__(config, PLATFORM_DOMAIN, old_style)

        # Modes if supported
        self._attr_preset_modes = config.get(CONF_MODES)

        # Try for speed count then speed.
        #  - speed_count; number of speeds we support
        #  - speed == True; 3 speeds
        #  - speed == False; no speeds
        self._attr_speed_count = config.get(CONF_SPEED_COUNT)
        if config.get(CONF_SPEED, False):
            self._attr_speed_count = 3

        self._enable_turn_on_off_backwards_compatibility = False
        self._attr_current_direction = None
        self._attr_oscillating = None
        self._attr_percentage = None
        self._attr_preset_mode = None
        self._configured_percentage = self._safe_percentage(
            config.get(CONF_PERCENTAGE)
        )
        self._configured_preset_mode = config.get(CONF_PRESET_MODE)
        self._attr_supported_features = FanEntityFeature(0)
        self._refresh_supported_features()

        _LOGGER.debug(f"VirtualFan: {self.name} created")

    def _refresh_supported_features(self) -> None:
        """Recalculate features after a capability template changes."""
        features = FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF
        if (
            self._attr_speed_count > 0
            or "percentage" in self._native_templates
            or "set_percentage" in self._command_actions
        ):
            features |= FanEntityFeature.SET_SPEED
        if (
            self._config.get(CONF_OSCILLATE, False)
            or "oscillating" in self._native_templates
            or "oscillate" in self._command_actions
        ):
            features |= FanEntityFeature.OSCILLATE
        if (
            self._config.get(CONF_DIRECTION, False)
            or "current_direction" in self._native_templates
            or "set_direction" in self._command_actions
        ):
            features |= FanEntityFeature.DIRECTION
        if self._attr_preset_modes or "set_preset_mode" in self._command_actions:
            features |= FanEntityFeature.PRESET_MODE
        self._attr_supported_features = features

    def _create_state(self, config):
        super()._create_state(config)

        if self._attr_supported_features & FanEntityFeature.DIRECTION:
            self._attr_current_direction = config.get(
                CONF_CURRENT_DIRECTION, "forward"
            )
        if self._attr_supported_features & FanEntityFeature.OSCILLATE:
            self._attr_oscillating = config.get(CONF_OSCILLATING, False)
        self._apply_initial_power_state(config.get(CONF_INITIAL_VALUE))

    def _restore_state(self, state, config):
        super()._restore_state(state, config)

        if self._attr_supported_features & FanEntityFeature.DIRECTION:
            direction = state.attributes.get(ATTR_DIRECTION)
            self._attr_current_direction = (
                direction
                if direction in {"forward", "reverse"}
                else config.get(CONF_CURRENT_DIRECTION, "forward")
            )
        if self._attr_supported_features & FanEntityFeature.OSCILLATE:
            oscillating = state.attributes.get(ATTR_OSCILLATING)
            self._attr_oscillating = (
                oscillating
                if isinstance(oscillating, bool)
                else config.get(CONF_OSCILLATING, False)
            )
        restored_percentage = self._safe_percentage(
            state.attributes.get(ATTR_PERCENTAGE)
        )
        preset_mode = state.attributes.get(ATTR_PRESET_MODE)
        restored_preset_mode = (
            preset_mode if preset_mode in self._attr_preset_modes else None
        )
        restored_state = self._restored_state_value(state, config)
        if str(restored_state).lower() == "on":
            self._attr_preset_mode = restored_preset_mode
            self._attr_percentage = restored_percentage
            if self._attr_preset_mode is None and not self._attr_percentage:
                self._apply_initial_power_state("on")
        else:
            self._attr_percentage = 0
            self._attr_preset_mode = None

    def _apply_initial_power_state(self, value) -> None:
        """Apply a configured or fallback power state without writing to HA."""
        if str(value).lower() not in {"y", "yes", "t", "true", "on", "1"}:
            self._attr_percentage = 0
            self._attr_preset_mode = None
            return
        if self._configured_preset_mode in self._attr_preset_modes:
            self._attr_preset_mode = self._configured_preset_mode
            self._attr_percentage = None
            return
        self._attr_preset_mode = None
        self._attr_percentage = self._configured_percentage or 67

    @staticmethod
    def _safe_percentage(value) -> int | None:
        """Return a valid restored percentage or unknown."""
        if value is None or isinstance(value, bool):
            return None
        try:
            parsed = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if not math.isfinite(parsed) or not 0 <= parsed <= 100:
            return None
        return round(parsed)

    def _update_attributes(self):
        super()._update_attributes()
        feature_attributes = (
            (
                ATTR_DIRECTION,
                self._attr_current_direction,
                FanEntityFeature.DIRECTION,
            ),
            (
                ATTR_OSCILLATING,
                self._attr_oscillating,
                FanEntityFeature.OSCILLATE,
            ),
            (ATTR_PERCENTAGE, self._attr_percentage, FanEntityFeature.SET_SPEED),
            (ATTR_PRESET_MODE, self._attr_preset_mode, FanEntityFeature.PRESET_MODE),
        )
        self._attr_extra_state_attributes.update(
            {
                name: value
                for name, value, feature in feature_attributes
                if value is not None and feature in self._attr_supported_features
            }
        )

    def _apply_native_template_value(self, name: str, value) -> bool:
        if name == "modes":
            name = "preset_modes"
        if name == "preset_modes":
            if not isinstance(value, (list, tuple)):
                raise ValueError("preset_modes must render a list")
            value = [str(item).strip() for item in value if str(item).strip()]
            if len(set(value)) != len(value):
                raise ValueError("preset_modes contains duplicate values")
        elif name == "preset_mode":
            value = str(value).strip()
        elif name == "percentage":
            value = self._safe_percentage(value)
            if value is None:
                raise ValueError("percentage must be between 0 and 100")
        elif name == "speed_count":
            if isinstance(value, bool):
                raise ValueError("speed_count must be an integer")
            value = int(value)
            if value < 0:
                raise ValueError("speed_count cannot be negative")
        elif name == "current_direction":
            value = str(value).strip().lower()
            if value not in {"forward", "reverse"}:
                raise ValueError("current_direction must be forward or reverse")
        elif name == "oscillating" and not isinstance(value, bool):
            value = self._template_to_bool(value)
        elif name in {"state", "is_on"}:
            old_is_on = self.is_on
            requested_is_on = self._template_to_bool(value)
            if not requested_is_on:
                self._apply_initial_power_state("off")
            elif not self.is_on:
                self._apply_initial_power_state("on")
            return old_is_on != self.is_on
        return super()._apply_native_template_value(name, value)

    def _native_templates_applied(self) -> None:
        if self._attr_preset_mode not in self._attr_preset_modes:
            self._attr_preset_mode = None
        self._refresh_supported_features()

    def _set_percentage(self, percentage: int) -> None:
        if isinstance(percentage, bool):
            raise ValueError("Fan percentage must be between 0 and 100")
        percentage = int(percentage)
        if not 0 <= percentage <= 100:
            raise ValueError("Fan percentage must be between 0 and 100")
        self._attr_percentage = percentage
        self._attr_preset_mode = None
        self._update_attributes()
        self.async_write_ha_state()

    def _set_preset_mode(self, preset_mode: str) -> None:
        if preset_mode in self.preset_modes:
            self._attr_preset_mode = preset_mode
            self._attr_percentage = None
            self._update_attributes()
            self.async_write_ha_state()
        else:
            raise ValueError(f"Invalid preset mode: {preset_mode}")

    async def async_set_percentage(self, percentage: int) -> None:
        """Set the speed of the fan, as a percentage."""
        _LOGGER.debug(f"setting {self.name} pcent to {percentage}")
        self._set_percentage(percentage)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set new preset mode."""
        _LOGGER.debug(f"setting {self.name} mode to {preset_mode}")
        self._set_preset_mode(preset_mode)

    async def async_turn_on(
            self,
            percentage: int | None = None,
            preset_mode: str | None = None,
            **kwargs: Any,
    ) -> None:
        """Turn on the entity."""
        _LOGGER.debug(f"turning {self.name} on")
        if preset_mode:
            self._set_preset_mode(preset_mode)
            return

        if percentage is None:
            if self._attr_preset_mode in self._attr_preset_modes:
                self._set_preset_mode(self._attr_preset_mode)
                return
            percentage = self._attr_percentage or self._configured_percentage or 67
        self._set_percentage(percentage)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the entity."""
        _LOGGER.debug(f"turning {self.name} off")
        self._set_percentage(0)

    async def async_set_direction(self, direction: str) -> None:
        """Set the direction of the fan."""
        _LOGGER.debug(f"setting direction of {self.name} to {direction}")
        if direction not in {"forward", "reverse"}:
            raise ValueError(f"Invalid fan direction: {direction}")
        self._attr_current_direction = direction
        self._update_attributes()
        self.async_write_ha_state()

    async def async_oscillate(self, oscillating: bool) -> None:
        """Set oscillation."""
        _LOGGER.debug(f"setting oscillate of {self.name} to {oscillating}")
        if not isinstance(oscillating, bool):
            raise TypeError("Oscillating must be a boolean")
        self._attr_oscillating = oscillating
        self._update_attributes()
        self.async_write_ha_state()

    def set_state(self, value) -> None:
        value = str(value).lower()
        if value in ["y", "yes", "t", "true", "on", "1"]:
            self._set_percentage(67)
        elif value in ["n", "no", "f", "false", "off", "0"]:
            self._set_percentage(0)
        else:
            self._set_percentage(int(value))
