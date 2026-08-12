"""
This component provides support for a virtual cover.

"""

import logging
from collections.abc import Callable
from typing import Any

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components.cover import DOMAIN as PLATFORM_DOMAIN
from homeassistant.components.cover import CoverEntity, CoverEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.config_validation import PLATFORM_SCHEMA

from . import get_entity_configs
from .const import *
from .entity import (
    VirtualOpenableEntity,
    positive_tick,
    virtual_schema,
)

_LOGGER = logging.getLogger(__name__)

DEPENDENCIES = [COMPONENT_DOMAIN]

DEFAULT_COVER_VALUE = "open"

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(virtual_schema(DEFAULT_COVER_VALUE, {
    vol.Optional(CONF_CLASS): cv.string,
    vol.Optional(CONF_OPEN_CLOSE_DURATION, default=10): cv.positive_int,
    vol.Optional(CONF_OPEN_CLOSE_TICK, default=1): positive_tick,
}))
COVER_SCHEMA = vol.Schema(virtual_schema(DEFAULT_COVER_VALUE, {
    vol.Optional(CONF_CLASS): cv.string,
    vol.Optional(CONF_OPEN_CLOSE_DURATION, default=10): cv.positive_int,
    vol.Optional(CONF_OPEN_CLOSE_TICK, default=1): positive_tick,
}))


async def async_setup_platform(hass, config, async_add_entities, _discovery_info=None):
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
        entity = COVER_SCHEMA(entity)
        entities.append(VirtualCover(entity, False))
    async_add_entities(entities)


class VirtualCover(VirtualOpenableEntity, CoverEntity):
    """Representation of a Virtual cover."""

    def __init__(self, config, old_style : bool):
        """Initialize the Virtual cover device."""
        super().__init__(config, PLATFORM_DOMAIN, old_style)

        self._attr_supported_features = CoverEntityFeature(
            CoverEntityFeature.OPEN |
            CoverEntityFeature.CLOSE |
            CoverEntityFeature.STOP |
            CoverEntityFeature.SET_POSITION
        )
        self._attr_current_cover_tilt_position = None
        if "current_cover_tilt_position" in self._native_templates or any(
            command in self._command_actions
            for command in (
                "open_cover_tilt",
                "close_cover_tilt",
                "stop_cover_tilt",
                "set_cover_tilt_position",
            )
        ):
            self._attr_supported_features |= (
                CoverEntityFeature.OPEN_TILT
                | CoverEntityFeature.CLOSE_TILT
                | CoverEntityFeature.STOP_TILT
                | CoverEntityFeature.SET_TILT_POSITION
            )

        _LOGGER.debug(f"VirtualCover: {self.name} created")

    @property
    def current_cover_position(self) -> int | None:
        return self._current_position

    async def async_open_cover(self, **kwargs: Any) -> None:
        _LOGGER.debug(f"opening {self.name}")
        self._set_position(100)

    async def async_close_cover(self, **kwargs: Any) -> None:
        _LOGGER.debug(f"closing {self.name}")
        self._set_position(0)

    async def async_stop_cover(self, **kwargs: Any) -> None:
        _LOGGER.debug(f"stopping {self.name}")
        self._stop()

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        _LOGGER.debug(f"setting {self.name} position {kwargs['position']}")
        self._set_position(kwargs['position'])

    async def async_open_cover_tilt(self, **kwargs: Any) -> None:
        self._attr_current_cover_tilt_position = 100
        self.async_write_ha_state()

    async def async_close_cover_tilt(self, **kwargs: Any) -> None:
        self._attr_current_cover_tilt_position = 0
        self.async_write_ha_state()

    async def async_stop_cover_tilt(self, **kwargs: Any) -> None:
        self.async_write_ha_state()

    async def async_set_cover_tilt_position(self, **kwargs: Any) -> None:
        position = int(kwargs["tilt_position"])
        if not 0 <= position <= 100:
            raise ValueError("tilt_position must be between 0 and 100")
        self._attr_current_cover_tilt_position = position
        self.async_write_ha_state()

    def _apply_native_template_value(self, name: str, value) -> bool:
        if name == "current_cover_tilt_position":
            try:
                value = int(value)
            except (TypeError, ValueError, OverflowError) as err:
                raise ValueError(
                    "current_cover_tilt_position must be between 0 and 100"
                ) from err
            if not 0 <= value <= 100:
                raise ValueError(
                    "current_cover_tilt_position must be between 0 and 100"
                )
        return super()._apply_native_template_value(name, value)
