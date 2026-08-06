"""
This component provides support for a virtual climate entity.

"""
from __future__ import annotations

import logging
import math
from collections.abc import Callable
from typing import Any

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components.climate import (
    DOMAIN as PLATFORM_DOMAIN,
)
from homeassistant.components.climate import (
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


def _safe_hvac_mode(value) -> HVACMode | None:
    """Parse a restored HVAC mode without failing entity setup."""
    try:
        return _as_hvac_mode(value)
    except (TypeError, ValueError):
        return None


def _as_hvac_action(value) -> HVACAction | None:
    if value is None:
        return None


def _finite_float(value, default: float) -> float:
    """Return a finite configured number or a compatibility default."""
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _finite_step(value, default: float | None = None) -> float | None:
    """Return a positive finite step, falling back for damaged stored data."""
    if value is None:
        return default
    result = _finite_float(value, default if default is not None else 0)
    return result if result > 0 else default
    if isinstance(value, HVACAction):
        return value
    try:
        return HVACAction(str(value).lower())
    except (TypeError, ValueError):
        return None


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
        self._attr_min_temp = _finite_float(config.get(CONF_MIN_TEMP), 7)
        self._attr_max_temp = _finite_float(config.get(CONF_MAX_TEMP), 35)
        if self._attr_min_temp > self._attr_max_temp:
            self._attr_min_temp, self._attr_max_temp = (
                self._attr_max_temp,
                self._attr_min_temp,
            )
        self._attr_min_humidity = _finite_float(config.get(CONF_MIN_HUMIDITY), 30)
        self._attr_max_humidity = _finite_float(config.get(CONF_MAX_HUMIDITY), 99)
        if self._attr_min_humidity > self._attr_max_humidity:
            self._attr_min_humidity, self._attr_max_humidity = (
                self._attr_max_humidity,
                self._attr_min_humidity,
            )
        self._attr_target_temperature_step = _finite_step(
            config.get(CONF_TARGET_TEMPERATURE_STEP),
            PRECISION_TENTHS,
        )
        self._attr_target_humidity_step = _finite_step(
            config.get(CONF_TARGET_HUMIDITY_STEP),
        )
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
        configured_mode = _safe_hvac_mode(config.get(CONF_INITIAL_VALUE))
        self._attr_hvac_mode = (
            configured_mode
            if configured_mode in self._attr_hvac_modes
            else self._default_hvac_mode()
        )
        self._attr_hvac_action = _as_hvac_action(config.get(CONF_HVAC_ACTION))
        self._attr_current_temperature = self._bounded_temperature(
            config.get(CONF_CURRENT_TEMPERATURE),
        )
        self._attr_target_temperature = self._bounded_temperature(
            config.get(CONF_TARGET_TEMPERATURE)
        )
        self._attr_target_temperature_high = self._bounded_temperature(
            config.get(CONF_TARGET_TEMPERATURE_HIGH)
        )
        self._attr_target_temperature_low = self._bounded_temperature(
            config.get(CONF_TARGET_TEMPERATURE_LOW)
        )
        self._attr_current_humidity = self._bounded_humidity(
            config.get(CONF_CURRENT_HUMIDITY),
        )
        self._attr_target_humidity = self._bounded_humidity(
            config.get(CONF_TARGET_HUMIDITY),
        )
        self._attr_fan_mode = config.get(CONF_FAN_MODE)
        self._attr_preset_mode = config.get(CONF_PRESET_MODE)
        self._attr_swing_mode = config.get(CONF_SWING_MODE)
        self._attr_swing_horizontal_mode = config.get(CONF_SWING_HORIZONTAL_MODE)

    def _restore_state(self, state, config):
        super()._restore_state(state, config)
        restored_mode = _safe_hvac_mode(state.state)
        self._attr_hvac_mode = (
            restored_mode
            if restored_mode in self._attr_hvac_modes
            else self._default_hvac_mode()
        )
        self._attr_hvac_action = _as_hvac_action(state.attributes.get(CONF_HVAC_ACTION, config.get(CONF_HVAC_ACTION)))
        self._attr_current_temperature = self._bounded_temperature(
            state.attributes.get(
                CONF_CURRENT_TEMPERATURE,
                config.get(CONF_CURRENT_TEMPERATURE),
            ),
        )
        self._attr_target_temperature = self._bounded_temperature(
            state.attributes.get(
                CONF_TARGET_TEMPERATURE,
                config.get(CONF_TARGET_TEMPERATURE),
            ),
        )
        self._attr_target_temperature_high = self._bounded_temperature(
            state.attributes.get(
                CONF_TARGET_TEMPERATURE_HIGH,
                config.get(CONF_TARGET_TEMPERATURE_HIGH),
            ),
        )
        self._attr_target_temperature_low = self._bounded_temperature(
            state.attributes.get(
                CONF_TARGET_TEMPERATURE_LOW,
                config.get(CONF_TARGET_TEMPERATURE_LOW),
            ),
        )
        self._attr_current_humidity = self._bounded_humidity(
            state.attributes.get(
                CONF_CURRENT_HUMIDITY,
                config.get(CONF_CURRENT_HUMIDITY),
            ),
        )
        self._attr_target_humidity = self._bounded_humidity(
            state.attributes.get(
                CONF_TARGET_HUMIDITY,
                config.get(CONF_TARGET_HUMIDITY),
            ),
        )
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
        hvac_mode = _as_hvac_mode(hvac_mode)
        if hvac_mode not in self._attr_hvac_modes:
            raise ValueError(f"Unsupported HVAC mode: {hvac_mode}")
        self._attr_hvac_mode = hvac_mode
        self.async_write_ha_state()

    def _default_hvac_mode(self) -> HVACMode:
        if HVACMode.OFF in self._attr_hvac_modes:
            return HVACMode.OFF
        if self._attr_hvac_modes:
            return self._attr_hvac_modes[0]
        return HVACMode.OFF

    def _bounded_temperature(self, temperature):
        if temperature is None:
            return None
        try:
            temperature = float(temperature)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(temperature):
            return None
        return max(self._attr_min_temp, min(self._attr_max_temp, temperature))

    def _bounded_humidity(self, humidity):
        if humidity is None:
            return None
        try:
            humidity = float(humidity)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(humidity):
            return None
        return max(self._attr_min_humidity, min(self._attr_max_humidity, humidity))

    def _validate_temperature(self, temperature) -> float:
        temperature = float(temperature)
        if not math.isfinite(temperature):
            raise ValueError("Temperature must be finite")
        if not self._attr_min_temp <= temperature <= self._attr_max_temp:
            raise ValueError(
                "Temperature must be within the configured minimum and maximum"
            )
        return temperature

    @staticmethod
    def _validate_choice(value: str, values: list[str], label: str) -> str:
        if value not in values:
            raise ValueError(f"Invalid {label}: {value}")
        return value

    async def async_turn_on(self) -> None:
        self._attr_hvac_mode = next((mode for mode in self._attr_hvac_modes if mode != HVACMode.OFF), HVACMode.HEAT)
        self.async_write_ha_state()

    async def async_turn_off(self) -> None:
        self._attr_hvac_mode = HVACMode.OFF
        self.async_write_ha_state()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        next_temperature = self._attr_target_temperature
        next_temperature_high = self._attr_target_temperature_high
        next_temperature_low = self._attr_target_temperature_low
        if ATTR_TEMPERATURE in kwargs:
            next_temperature = self._validate_temperature(
                kwargs[ATTR_TEMPERATURE]
            )
        if ATTR_TARGET_TEMPERATURE_HIGH in kwargs:
            next_temperature_high = self._validate_temperature(
                kwargs[ATTR_TARGET_TEMPERATURE_HIGH]
            )
        if ATTR_TARGET_TEMPERATURE_LOW in kwargs:
            next_temperature_low = self._validate_temperature(
                kwargs[ATTR_TARGET_TEMPERATURE_LOW]
            )
        if (
            next_temperature_low is not None
            and next_temperature_high is not None
            and next_temperature_low > next_temperature_high
        ):
            raise ValueError("Low target temperature cannot exceed high target temperature")
        self._attr_target_temperature = next_temperature
        self._attr_target_temperature_high = next_temperature_high
        self._attr_target_temperature_low = next_temperature_low
        self.async_write_ha_state()

    async def async_set_humidity(self, humidity: int) -> None:
        humidity = float(humidity)
        if not math.isfinite(humidity):
            raise ValueError("Humidity must be finite")
        if not self._attr_min_humidity <= humidity <= self._attr_max_humidity:
            raise ValueError(
                "Humidity must be within the configured minimum and maximum"
            )
        self._attr_target_humidity = humidity
        self.async_write_ha_state()

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        self._attr_fan_mode = self._validate_choice(
            fan_mode, self._attr_fan_modes, "fan mode"
        )
        self.async_write_ha_state()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        self._attr_preset_mode = self._validate_choice(
            preset_mode, self._attr_preset_modes, "preset mode"
        )
        self.async_write_ha_state()

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        self._attr_swing_mode = self._validate_choice(
            swing_mode, self._attr_swing_modes, "swing mode"
        )
        self.async_write_ha_state()

    async def async_set_swing_horizontal_mode(self, swing_horizontal_mode: str) -> None:
        self._attr_swing_horizontal_mode = self._validate_choice(
            swing_horizontal_mode,
            self._attr_swing_horizontal_modes,
            "horizontal swing mode",
        )
        self.async_write_ha_state()

    def set_state(self, value) -> None:
        mode = _as_hvac_mode(value)
        if mode not in self._attr_hvac_modes:
            raise ValueError(f"Unsupported HVAC mode: {mode}")
        self._attr_hvac_mode = mode
