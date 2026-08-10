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
from .entity import VirtualEntity, virtual_schema

_LOGGER = logging.getLogger(__name__)

DEPENDENCIES = [COMPONENT_DOMAIN]

CONF_DIRECTION = "direction"
CONF_MODES = "modes"
CONF_OSCILLATE = "oscillate"
CONF_PERCENTAGE = "percentage"
CONF_SPEED = "speed"
CONF_SPEED_COUNT = "speed_count"

DEFAULT_FAN_VALUE = "off"

BASE_SCHEMA = virtual_schema(DEFAULT_FAN_VALUE, {
    vol.Optional(CONF_SPEED, default=False): cv.boolean,
    vol.Optional(CONF_SPEED_COUNT, default=0): cv.positive_int,
    vol.Optional(CONF_OSCILLATE, default=False): cv.boolean,
    vol.Optional(CONF_DIRECTION, default=False): cv.boolean,
    vol.Optional(CONF_MODES, default=[]): vol.All(cv.ensure_list, [cv.string]),
})

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(BASE_SCHEMA)

FAN_SCHEMA = vol.Schema(BASE_SCHEMA)


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
        entity = FAN_SCHEMA(entity)
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
        self._attr_supported_features = FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF
        if self._attr_speed_count > 0:
            self._attr_supported_features |= FanEntityFeature.SET_SPEED
        if config.get(CONF_OSCILLATE, False):
            self._attr_supported_features |= FanEntityFeature.OSCILLATE
        if config.get(CONF_DIRECTION, False):
            self._attr_supported_features |= FanEntityFeature.DIRECTION
        if self._attr_preset_modes:
            self._attr_supported_features |= FanEntityFeature.PRESET_MODE

        _LOGGER.debug(f"VirtualFan: {self.name} created")

    def _create_state(self, config):
        super()._create_state(config)

        if self._attr_supported_features & FanEntityFeature.DIRECTION:
            self._attr_current_direction = "forward"
        if self._attr_supported_features & FanEntityFeature.OSCILLATE:
            self._attr_oscillating = False
        self._attr_percentage = None
        self._attr_preset_mode = None

    def _restore_state(self, state, config):
        super()._restore_state(state, config)

        if self._attr_supported_features & FanEntityFeature.DIRECTION:
            direction = state.attributes.get(ATTR_DIRECTION)
            self._attr_current_direction = (
                direction if direction in {"forward", "reverse"} else "forward"
            )
        if self._attr_supported_features & FanEntityFeature.OSCILLATE:
            oscillating = state.attributes.get(ATTR_OSCILLATING)
            self._attr_oscillating = (
                oscillating if isinstance(oscillating, bool) else False
            )
        self._attr_percentage = self._safe_percentage(
            state.attributes.get(ATTR_PERCENTAGE)
        )
        preset_mode = state.attributes.get(ATTR_PRESET_MODE)
        self._attr_preset_mode = (
            preset_mode if preset_mode in self._attr_preset_modes else None
        )

    @staticmethod
    def _safe_percentage(value) -> int | None:
        """Return a valid restored percentage or unknown."""
        if value is None or isinstance(value, bool):
            return None
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(parsed) or not 0 <= parsed <= 100:
            return None
        return round(parsed)

    def _update_attributes(self):
        super()._update_attributes()
        self._attr_extra_state_attributes.update({
            name: value for name, value in (
                (ATTR_DIRECTION, self._attr_current_direction),
                (ATTR_OSCILLATING, self._attr_oscillating),
                (ATTR_PERCENTAGE, self._attr_percentage),
                (ATTR_PRESET_MODE, self._attr_preset_mode),
            ) if value is not None
        })

    def _set_percentage(self, percentage: int) -> None:
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
            percentage = 67
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
