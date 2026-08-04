"""
This component provides support for a virtual humidifier or dehumidifier.

"""
from __future__ import annotations

import logging
import voluptuous as vol
from collections.abc import Callable

import homeassistant.helpers.config_validation as cv
from homeassistant.components.humidifier import (
    DOMAIN as PLATFORM_DOMAIN,
    HumidifierAction,
    HumidifierDeviceClass,
    HumidifierEntity,
    HumidifierEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_DEVICE_CLASS, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.config_validation import PLATFORM_SCHEMA
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import get_entity_configs
from .const import *
from .entity import VirtualEntity, virtual_schema


_LOGGER = logging.getLogger(__name__)

DEPENDENCIES = [COMPONENT_DOMAIN]

CONF_ACTION = "action"
CONF_CURRENT_HUMIDITY = "current_humidity"
CONF_MAX_HUMIDITY = "max_humidity"
CONF_MIN_HUMIDITY = "min_humidity"
CONF_MODE = "mode"
CONF_MODES = "modes"
CONF_TARGET_HUMIDITY = "target_humidity"
CONF_TARGET_HUMIDITY_STEP = "target_humidity_step"

DEFAULT_HUMIDIFIER_VALUE = "off"


def _as_action(value) -> HumidifierAction | None:
    if value is None:
        return None
    if isinstance(value, HumidifierAction):
        return value
    return HumidifierAction(str(value).lower())


def _as_device_class(value) -> HumidifierDeviceClass | None:
    if value is None:
        return None
    if isinstance(value, HumidifierDeviceClass):
        return value
    return HumidifierDeviceClass(str(value).lower())


BASE_SCHEMA = virtual_schema(DEFAULT_HUMIDIFIER_VALUE, {
    vol.Optional(CONF_ACTION): cv.string,
    vol.Optional(CONF_CLASS, default=HumidifierDeviceClass.HUMIDIFIER): _as_device_class,
    vol.Optional(CONF_CURRENT_HUMIDITY): vol.Coerce(float),
    vol.Optional(CONF_MAX_HUMIDITY, default=100): vol.Coerce(float),
    vol.Optional(CONF_MIN_HUMIDITY, default=0): vol.Coerce(float),
    vol.Optional(CONF_MODE): cv.string,
    vol.Optional(CONF_MODES, default=list): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional(CONF_TARGET_HUMIDITY): vol.Coerce(float),
    vol.Optional(CONF_TARGET_HUMIDITY_STEP): vol.Coerce(float),
})

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(BASE_SCHEMA)
HUMIDIFIER_SCHEMA = vol.Schema(BASE_SCHEMA)


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
        entities.append(VirtualHumidifier(HUMIDIFIER_SCHEMA(entity), False))
    async_add_entities(entities)


class VirtualHumidifier(VirtualEntity, HumidifierEntity):
    """Representation of a virtual humidifier."""

    def __init__(self, config, old_style: bool):
        super().__init__(config, PLATFORM_DOMAIN, old_style)

        self._attr_device_class = config.get(CONF_CLASS)
        self._attr_available_modes = config.get(CONF_MODES)
        self._attr_supported_features = HumidifierEntityFeature(0)
        if self._attr_available_modes:
            self._attr_supported_features |= HumidifierEntityFeature.MODES

        self._attr_min_humidity = config.get(CONF_MIN_HUMIDITY)
        self._attr_max_humidity = config.get(CONF_MAX_HUMIDITY)
        self._attr_target_humidity_step = config.get(CONF_TARGET_HUMIDITY_STEP)

        _LOGGER.info(f"VirtualHumidifier: {self.name} created")

    def _create_state(self, config):
        super()._create_state(config)
        self._attr_is_on = config.get(CONF_INITIAL_VALUE).lower() == STATE_ON
        self._attr_action = _as_action(config.get(CONF_ACTION))
        self._attr_current_humidity = config.get(CONF_CURRENT_HUMIDITY)
        self._attr_target_humidity = config.get(CONF_TARGET_HUMIDITY)
        self._attr_mode = config.get(CONF_MODE)

    def _restore_state(self, state, config):
        super()._restore_state(state, config)
        self._attr_is_on = state.state.lower() == STATE_ON
        self._attr_action = _as_action(state.attributes.get(CONF_ACTION, config.get(CONF_ACTION)))
        self._attr_current_humidity = state.attributes.get(CONF_CURRENT_HUMIDITY, config.get(CONF_CURRENT_HUMIDITY))
        self._attr_target_humidity = state.attributes.get(CONF_TARGET_HUMIDITY, config.get(CONF_TARGET_HUMIDITY))
        self._attr_mode = state.attributes.get(CONF_MODE, config.get(CONF_MODE))

    def _update_attributes(self):
        super()._update_attributes()
        self._attr_extra_state_attributes.update({
            name: value for name, value in (
                (ATTR_DEVICE_CLASS, self._attr_device_class),
            ) if value is not None
        })

    @property
    def state_attributes(self):
        data = dict(super().state_attributes or {})
        data.update(self._attr_extra_state_attributes or {})
        return data

    async def async_turn_on(self, **kwargs) -> None:
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self._attr_is_on = False
        self.async_write_ha_state()

    async def async_set_humidity(self, humidity: int) -> None:
        self._attr_target_humidity = humidity
        self.async_write_ha_state()

    async def async_set_mode(self, mode: str) -> None:
        self._attr_mode = mode
        self.async_write_ha_state()

    def set_state(self, value) -> None:
        self._attr_is_on = str(value).lower() in ["y", "yes", "t", "true", "on", "1"]
