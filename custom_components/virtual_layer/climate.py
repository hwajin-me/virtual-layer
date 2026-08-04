"""
This component provides support for a virtual climate entity.

"""
from __future__ import annotations

import logging
import voluptuous as vol
from collections.abc import Callable
from typing import Any

import homeassistant.helpers.config_validation as cv
from homeassistant.components.climate import (
    DOMAIN as PLATFORM_DOMAIN,
    ClimateEntity,
    ClimateEntityFeature,
)
from homeassistant.components.climate.const import HVACAction, HVACMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, PRECISION_TENTHS, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.config_validation import PLATFORM_SCHEMA
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import get_entity_configs
from .const import *
from .entity import VirtualEntity, virtual_schema


_LOGGER = logging.getLogger(__name__)

DEPENDENCIES = [COMPONENT_DOMAIN]

CONF_CURRENT_HUMIDITY = "current_humidity"
CONF_CURRENT_TEMPERATURE = "current_temperature"
CONF_FAN_MODE = "fan_mode"
CONF_FAN_MODES = "fan_modes"
CONF_HVAC_ACTION = "hvac_action"
CONF_HVAC_MODES = "hvac_modes"
CONF_MAX_HUMIDITY = "max_humidity"
CONF_MAX_TEMP = "max_temp"
CONF_MIN_HUMIDITY = "min_humidity"
CONF_MIN_TEMP = "min_temp"
CONF_PRESET_MODE = "preset_mode"
CONF_PRESET_MODES = "preset_modes"
CONF_SWING_MODE = "swing_mode"
CONF_SWING_MODES = "swing_modes"
CONF_SWING_HORIZONTAL_MODE = "swing_horizontal_mode"
CONF_SWING_HORIZONTAL_MODES = "swing_horizontal_modes"
CONF_TARGET_HUMIDITY = "target_humidity"
CONF_TARGET_HUMIDITY_STEP = "target_humidity_step"
CONF_TARGET_TEMPERATURE = "target_temperature"
CONF_TARGET_TEMPERATURE_HIGH = "target_temperature_high"
CONF_TARGET_TEMPERATURE_LOW = "target_temperature_low"
CONF_TARGET_TEMPERATURE_STEP = "target_temperature_step"
CONF_TEMPERATURE_UNIT = "temperature_unit"

ATTR_TARGET_TEMPERATURE_HIGH = "target_temp_high"
ATTR_TARGET_TEMPERATURE_LOW = "target_temp_low"

DEFAULT_CLIMATE_VALUE = "off"
DEFAULT_HVAC_MODES = [
    HVACMode.OFF,
    HVACMode.HEAT,
    HVACMode.COOL,
    HVACMode.HEAT_COOL,
    HVACMode.AUTO,
    HVACMode.DRY,
    HVACMode.FAN_ONLY,
]


def _as_hvac_mode(value) -> HVACMode:
    if isinstance(value, HVACMode):
        return value
    return HVACMode(str(value).lower())


def _as_hvac_action(value) -> HVACAction | None:
    if value is None:
        return None
    if isinstance(value, HVACAction):
        return value
    return HVACAction(str(value).lower())


BASE_SCHEMA = virtual_schema(DEFAULT_CLIMATE_VALUE, {
    vol.Optional(CONF_CURRENT_HUMIDITY): vol.Coerce(float),
    vol.Optional(CONF_CURRENT_TEMPERATURE): vol.Coerce(float),
    vol.Optional(CONF_FAN_MODE): cv.string,
    vol.Optional(CONF_FAN_MODES, default=list): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional(CONF_HVAC_ACTION): cv.string,
    vol.Optional(CONF_HVAC_MODES, default=lambda: DEFAULT_HVAC_MODES): vol.All(cv.ensure_list, [_as_hvac_mode]),
    vol.Optional(CONF_MAX_HUMIDITY, default=99): vol.Coerce(float),
    vol.Optional(CONF_MAX_TEMP, default=35): vol.Coerce(float),
    vol.Optional(CONF_MIN_HUMIDITY, default=30): vol.Coerce(float),
    vol.Optional(CONF_MIN_TEMP, default=7): vol.Coerce(float),
    vol.Optional(CONF_PRESET_MODE): cv.string,
    vol.Optional(CONF_PRESET_MODES, default=list): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional(CONF_SWING_MODE): cv.string,
    vol.Optional(CONF_SWING_MODES, default=list): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional(CONF_SWING_HORIZONTAL_MODE): cv.string,
    vol.Optional(CONF_SWING_HORIZONTAL_MODES, default=list): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional(CONF_TARGET_HUMIDITY): vol.Coerce(float),
    vol.Optional(CONF_TARGET_HUMIDITY_STEP): vol.Coerce(float),
    vol.Optional(CONF_TARGET_TEMPERATURE): vol.Coerce(float),
    vol.Optional(CONF_TARGET_TEMPERATURE_HIGH): vol.Coerce(float),
    vol.Optional(CONF_TARGET_TEMPERATURE_LOW): vol.Coerce(float),
    vol.Optional(CONF_TARGET_TEMPERATURE_STEP, default=PRECISION_TENTHS): vol.Coerce(float),
    vol.Optional(CONF_TEMPERATURE_UNIT, default=UnitOfTemperature.CELSIUS): cv.string,
})

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(BASE_SCHEMA)
CLIMATE_SCHEMA = vol.Schema(BASE_SCHEMA)


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
        entities.append(VirtualClimate(CLIMATE_SCHEMA(entity), False))
    async_add_entities(entities)


class VirtualClimate(VirtualEntity, ClimateEntity):
    """Representation of a virtual climate device."""

    def __init__(self, config, old_style: bool):
        super().__init__(config, PLATFORM_DOMAIN, old_style)

        self._attr_hvac_modes = config.get(CONF_HVAC_MODES)
        self._attr_min_temp = config.get(CONF_MIN_TEMP)
        self._attr_max_temp = config.get(CONF_MAX_TEMP)
        self._attr_min_humidity = config.get(CONF_MIN_HUMIDITY)
        self._attr_max_humidity = config.get(CONF_MAX_HUMIDITY)
        self._attr_target_temperature_step = config.get(CONF_TARGET_TEMPERATURE_STEP)
        self._attr_target_humidity_step = config.get(CONF_TARGET_HUMIDITY_STEP)
        self._attr_temperature_unit = config.get(CONF_TEMPERATURE_UNIT)

        self._attr_supported_features = (
            ClimateEntityFeature.TARGET_HUMIDITY
            | ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TURN_OFF
        )
        if (
            config.get(CONF_TARGET_TEMPERATURE_HIGH) is not None
            or config.get(CONF_TARGET_TEMPERATURE_LOW) is not None
        ):
            self._attr_supported_features |= ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
        else:
            self._attr_supported_features |= ClimateEntityFeature.TARGET_TEMPERATURE
        self._enable_turn_on_off_backwards_compatibility = False

        self._attr_fan_modes = config.get(CONF_FAN_MODES)
        if self._attr_fan_modes:
            self._attr_supported_features |= ClimateEntityFeature.FAN_MODE
        self._attr_preset_modes = config.get(CONF_PRESET_MODES)
        if self._attr_preset_modes:
            self._attr_supported_features |= ClimateEntityFeature.PRESET_MODE
        self._attr_swing_modes = config.get(CONF_SWING_MODES)
        if self._attr_swing_modes:
            self._attr_supported_features |= ClimateEntityFeature.SWING_MODE
        self._attr_swing_horizontal_modes = config.get(CONF_SWING_HORIZONTAL_MODES)
        if self._attr_swing_horizontal_modes:
            self._attr_supported_features |= ClimateEntityFeature.SWING_HORIZONTAL_MODE

    def _create_state(self, config):
        super()._create_state(config)
        self._attr_hvac_mode = _as_hvac_mode(config.get(CONF_INITIAL_VALUE))
        self._attr_hvac_action = _as_hvac_action(config.get(CONF_HVAC_ACTION))
        self._attr_current_temperature = config.get(CONF_CURRENT_TEMPERATURE)
        self._attr_target_temperature = config.get(CONF_TARGET_TEMPERATURE)
        self._attr_target_temperature_high = config.get(CONF_TARGET_TEMPERATURE_HIGH)
        self._attr_target_temperature_low = config.get(CONF_TARGET_TEMPERATURE_LOW)
        self._attr_current_humidity = config.get(CONF_CURRENT_HUMIDITY)
        self._attr_target_humidity = config.get(CONF_TARGET_HUMIDITY)
        self._attr_fan_mode = config.get(CONF_FAN_MODE)
        self._attr_preset_mode = config.get(CONF_PRESET_MODE)
        self._attr_swing_mode = config.get(CONF_SWING_MODE)
        self._attr_swing_horizontal_mode = config.get(CONF_SWING_HORIZONTAL_MODE)

    def _restore_state(self, state, config):
        super()._restore_state(state, config)
        self._attr_hvac_mode = _as_hvac_mode(state.state)
        self._attr_hvac_action = _as_hvac_action(state.attributes.get(CONF_HVAC_ACTION, config.get(CONF_HVAC_ACTION)))
        self._attr_current_temperature = state.attributes.get(CONF_CURRENT_TEMPERATURE, config.get(CONF_CURRENT_TEMPERATURE))
        self._attr_target_temperature = state.attributes.get(CONF_TARGET_TEMPERATURE, config.get(CONF_TARGET_TEMPERATURE))
        self._attr_target_temperature_high = state.attributes.get(CONF_TARGET_TEMPERATURE_HIGH, config.get(CONF_TARGET_TEMPERATURE_HIGH))
        self._attr_target_temperature_low = state.attributes.get(CONF_TARGET_TEMPERATURE_LOW, config.get(CONF_TARGET_TEMPERATURE_LOW))
        self._attr_current_humidity = state.attributes.get(CONF_CURRENT_HUMIDITY, config.get(CONF_CURRENT_HUMIDITY))
        self._attr_target_humidity = state.attributes.get(CONF_TARGET_HUMIDITY, config.get(CONF_TARGET_HUMIDITY))
        self._attr_fan_mode = state.attributes.get(CONF_FAN_MODE, config.get(CONF_FAN_MODE))
        self._attr_preset_mode = state.attributes.get(CONF_PRESET_MODE, config.get(CONF_PRESET_MODE))
        self._attr_swing_mode = state.attributes.get(CONF_SWING_MODE, config.get(CONF_SWING_MODE))
        self._attr_swing_horizontal_mode = state.attributes.get(CONF_SWING_HORIZONTAL_MODE, config.get(CONF_SWING_HORIZONTAL_MODE))

    @property
    def state_attributes(self):
        data = dict(super().state_attributes or {})
        data.update(self._attr_extra_state_attributes or {})
        return data

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        self._attr_hvac_mode = _as_hvac_mode(hvac_mode)

    async def async_turn_on(self) -> None:
        self._attr_hvac_mode = next((mode for mode in self._attr_hvac_modes if mode != HVACMode.OFF), HVACMode.HEAT)

    async def async_turn_off(self) -> None:
        self._attr_hvac_mode = HVACMode.OFF

    async def async_set_temperature(self, **kwargs: Any) -> None:
        if ATTR_TEMPERATURE in kwargs:
            self._attr_target_temperature = kwargs[ATTR_TEMPERATURE]
        if ATTR_TARGET_TEMPERATURE_HIGH in kwargs:
            self._attr_target_temperature_high = kwargs[ATTR_TARGET_TEMPERATURE_HIGH]
        if ATTR_TARGET_TEMPERATURE_LOW in kwargs:
            self._attr_target_temperature_low = kwargs[ATTR_TARGET_TEMPERATURE_LOW]

    async def async_set_humidity(self, humidity: int) -> None:
        self._attr_target_humidity = humidity

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        self._attr_fan_mode = fan_mode

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        self._attr_preset_mode = preset_mode

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        self._attr_swing_mode = swing_mode

    async def async_set_swing_horizontal_mode(self, swing_horizontal_mode: str) -> None:
        self._attr_swing_horizontal_mode = swing_horizontal_mode

    def set_state(self, value) -> None:
        self._attr_hvac_mode = _as_hvac_mode(value)
