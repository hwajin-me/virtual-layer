"""
This component provides support for a virtual climate entity.

"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Mapping
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
from homeassistant.components.climate.const import (
    ATTR_HUMIDITY,
    ATTR_HVAC_MODE,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, PRECISION_TENTHS, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.config_validation import PLATFORM_SCHEMA
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import get_entity_configs
from .climate_options import migrate_legacy_climate_attributes
from .const import *
from .entity import VirtualEntity, number_float, virtual_schema

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
    return HVACMode(str(value).strip().lower())


def _safe_hvac_mode(value) -> HVACMode | None:
    """Parse a restored HVAC mode without failing entity setup."""
    try:
        return _as_hvac_mode(value)
    except (TypeError, ValueError):
        return None


def _as_hvac_action(value) -> HVACAction | None:
    if value is None:
        return None
    if isinstance(value, HVACAction):
        return value
    try:
        return HVACAction(str(value).strip().lower())
    except (TypeError, ValueError):
        return None


def _finite_float(value, default: float) -> float:
    """Return a finite configured number or a compatibility default."""
    if isinstance(value, bool):
        return default
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return result if math.isfinite(result) else default


def _finite_step(value, default: float | None = None) -> float | None:
    """Return a positive finite step, falling back for damaged stored data."""
    if value is None:
        return default
    result = _finite_float(value, default if default is not None else 0)
    return result if result > 0 else default


def _valid_mode_choice(restored, configured, available_modes):
    """Choose a current mode without resurrecting removed stored options."""
    if not available_modes:
        return None
    if restored in available_modes:
        return restored
    if configured in available_modes:
        return configured
    return available_modes[0]


BASE_SCHEMA = virtual_schema(
    DEFAULT_CLIMATE_VALUE,
    {
        vol.Optional(CONF_CURRENT_HUMIDITY): number_float,
        vol.Optional(CONF_CURRENT_TEMPERATURE): number_float,
        vol.Optional(CONF_FAN_MODE): cv.string,
        vol.Optional(CONF_FAN_MODES, default=list): vol.All(
            cv.ensure_list, [cv.string]
        ),
        vol.Optional(CONF_HVAC_ACTION): cv.string,
        vol.Optional(CONF_HVAC_MODES, default=lambda: DEFAULT_HVAC_MODES): vol.All(
            cv.ensure_list, [_as_hvac_mode]
        ),
        vol.Optional(CONF_MAX_HUMIDITY, default=99): number_float,
        vol.Optional(CONF_MAX_TEMP, default=35): number_float,
        vol.Optional(CONF_MIN_HUMIDITY, default=30): number_float,
        vol.Optional(CONF_MIN_TEMP, default=7): number_float,
        vol.Optional(CONF_PRESET_MODE): cv.string,
        vol.Optional(CONF_PRESET_MODES, default=list): vol.All(
            cv.ensure_list, [cv.string]
        ),
        vol.Optional(CONF_SWING_MODE): cv.string,
        vol.Optional(CONF_SWING_MODES, default=list): vol.All(
            cv.ensure_list, [cv.string]
        ),
        vol.Optional(CONF_SWING_HORIZONTAL_MODE): cv.string,
        vol.Optional(CONF_SWING_HORIZONTAL_MODES, default=list): vol.All(
            cv.ensure_list, [cv.string]
        ),
        vol.Optional(CONF_TARGET_HUMIDITY): number_float,
        vol.Optional(CONF_TARGET_HUMIDITY_STEP): number_float,
        vol.Optional(CONF_TARGET_TEMPERATURE): number_float,
        vol.Optional(CONF_TARGET_TEMPERATURE_HIGH): number_float,
        vol.Optional(CONF_TARGET_TEMPERATURE_LOW): number_float,
        vol.Optional(
            CONF_TARGET_TEMPERATURE_STEP, default=PRECISION_TENTHS
        ): number_float,
        vol.Optional(
            CONF_TEMPERATURE_UNIT, default=UnitOfTemperature.CELSIUS
        ): cv.string,
    },
)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(BASE_SCHEMA)
CLIMATE_SCHEMA = vol.Schema(BASE_SCHEMA)


def normalize_domain_options(config):
    """Promote legacy attributes before persisted-data validation."""
    return migrate_legacy_climate_attributes(config)


def validate_domain_options(config) -> None:
    """Validate mode relationships submitted through the config flow."""
    native_templates = config.get(CONF_NATIVE_TEMPLATES, {})
    native_fields = (
        {
            str(name)
            for name, template in native_templates.items()
            if isinstance(template, str) and template.strip()
        }
        if isinstance(native_templates, Mapping)
        else set()
    )

    def is_dynamic(*fields: str) -> bool:
        """Return whether a native Jinja field supersedes static fallback data."""
        return any(field in native_fields for field in fields)

    for field_name in (
        CONF_HVAC_MODES,
        CONF_FAN_MODES,
        CONF_PRESET_MODES,
        CONF_SWING_MODES,
        CONF_SWING_HORIZONTAL_MODES,
    ):
        if is_dynamic(field_name):
            continue
        modes = config.get(field_name, [])
        if any(not str(mode).strip() for mode in modes):
            raise vol.Invalid(f"{field_name} cannot contain empty modes")
        if len(set(modes)) != len(modes):
            raise vol.Invalid(f"{field_name} cannot contain duplicate modes")

    hvac_modes = config.get(CONF_HVAC_MODES, [])
    if not is_dynamic(CONF_HVAC_MODES) and not hvac_modes:
        raise vol.Invalid("At least one HVAC mode is required")
    initial_mode = _safe_hvac_mode(config.get(CONF_INITIAL_VALUE))
    if initial_mode is None:
        raise vol.Invalid("Initial value must be a valid HVAC mode")
    if not is_dynamic(CONF_HVAC_MODES, "hvac_mode") and initial_mode not in hvac_modes:
        raise vol.Invalid("Initial value must be included in HVAC modes")

    for current_field, modes_field in (
        (CONF_FAN_MODE, CONF_FAN_MODES),
        (CONF_PRESET_MODE, CONF_PRESET_MODES),
        (CONF_SWING_MODE, CONF_SWING_MODES),
        (CONF_SWING_HORIZONTAL_MODE, CONF_SWING_HORIZONTAL_MODES),
    ):
        if is_dynamic(current_field, modes_field):
            continue
        current_mode = config.get(current_field)
        if current_mode is not None and current_mode not in config.get(modes_field, []):
            raise vol.Invalid(f"{current_field} must be included in {modes_field}")

    action = config.get(CONF_HVAC_ACTION)
    if not is_dynamic(CONF_HVAC_ACTION) and action is not None and _as_hvac_action(action) is None:
        raise vol.Invalid("hvac_action is invalid")
    if not is_dynamic(CONF_TEMPERATURE_UNIT) and config.get(CONF_TEMPERATURE_UNIT) not in {
        UnitOfTemperature.CELSIUS,
        UnitOfTemperature.FAHRENHEIT,
        UnitOfTemperature.KELVIN,
    }:
        raise vol.Invalid("temperature_unit is invalid")

    min_temp = _finite_float(config.get(CONF_MIN_TEMP), float("nan"))
    max_temp = _finite_float(config.get(CONF_MAX_TEMP), float("nan"))
    min_humidity = _finite_float(config.get(CONF_MIN_HUMIDITY), float("nan"))
    max_humidity = _finite_float(config.get(CONF_MAX_HUMIDITY), float("nan"))
    if not is_dynamic(CONF_MIN_TEMP, CONF_MAX_TEMP) and not all(
        math.isfinite(value)
        for value in (min_temp, max_temp)
    ):
        raise vol.Invalid("temperature ranges must be finite")
    if not is_dynamic(CONF_MIN_HUMIDITY, CONF_MAX_HUMIDITY) and not all(
        math.isfinite(value) for value in (min_humidity, max_humidity)
    ):
        raise vol.Invalid("humidity ranges must be finite")
    if not is_dynamic(CONF_MIN_TEMP, CONF_MAX_TEMP) and min_temp > max_temp:
        raise vol.Invalid("temperature minimum cannot exceed maximum")
    if (
        not is_dynamic(CONF_MIN_HUMIDITY, CONF_MAX_HUMIDITY)
        and min_humidity > max_humidity
    ):
        raise vol.Invalid("humidity minimum cannot exceed maximum")

    for field_name in (
        CONF_CURRENT_TEMPERATURE,
        CONF_TARGET_TEMPERATURE,
        CONF_TARGET_TEMPERATURE_HIGH,
        CONF_TARGET_TEMPERATURE_LOW,
    ):
        if (
            field_name in config
            and not is_dynamic(field_name, CONF_MIN_TEMP, CONF_MAX_TEMP)
            and not min_temp <= float(config[field_name]) <= max_temp
        ):
            raise vol.Invalid(f"{field_name} must be within the temperature range")
    for field_name in (CONF_CURRENT_HUMIDITY, CONF_TARGET_HUMIDITY):
        if (
            field_name in config
            and not is_dynamic(field_name, CONF_MIN_HUMIDITY, CONF_MAX_HUMIDITY)
            and not min_humidity <= float(config[field_name]) <= max_humidity
        ):
            raise vol.Invalid(f"{field_name} must be within the humidity range")
    low = config.get(CONF_TARGET_TEMPERATURE_LOW)
    high = config.get(CONF_TARGET_TEMPERATURE_HIGH)
    if (
        not is_dynamic(
            CONF_TARGET_TEMPERATURE_LOW,
            CONF_TARGET_TEMPERATURE_HIGH,
        )
        and low is not None
        and high is not None
        and float(low) > float(high)
    ):
        raise vol.Invalid("target temperature low cannot exceed high")
    for field_name in (CONF_TARGET_TEMPERATURE_STEP, CONF_TARGET_HUMIDITY_STEP):
        if (
            field_name in config
            and not is_dynamic(field_name)
            and _finite_step(config[field_name]) is None
        ):
            raise vol.Invalid(f"{field_name} must be positive")


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
    for entity in get_entity_configs(
        hass, entry.data[ATTR_GROUP_NAME], PLATFORM_DOMAIN
    ):
        entities.append(
            VirtualClimate(
                CLIMATE_SCHEMA(
                    migrate_legacy_climate_attributes(entity),
                ),
                False,
            )
        )
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
        self._attr_target_temperature = self._bounded_temperature(
            config.get(CONF_TARGET_TEMPERATURE),
        )
        self._attr_target_temperature_high = self._bounded_temperature(
            config.get(CONF_TARGET_TEMPERATURE_HIGH),
        )
        self._attr_target_temperature_low = self._bounded_temperature(
            config.get(CONF_TARGET_TEMPERATURE_LOW),
        )
        self._attr_target_humidity = self._bounded_humidity(
            config.get(CONF_TARGET_HUMIDITY),
        )
        self._attr_temperature_unit = config.get(CONF_TEMPERATURE_UNIT)
        self._attr_fan_modes = config.get(CONF_FAN_MODES)
        self._attr_preset_modes = config.get(CONF_PRESET_MODES)
        self._attr_swing_modes = config.get(CONF_SWING_MODES)
        self._attr_swing_horizontal_modes = config.get(CONF_SWING_HORIZONTAL_MODES)

        self._attr_supported_features = ClimateEntityFeature(0)
        self._refresh_supported_features()
        self._enable_turn_on_off_backwards_compatibility = False

    def _refresh_supported_features(self) -> None:
        """Recalculate features after static or templated capabilities change."""
        features = ClimateEntityFeature(0)
        if any(mode != HVACMode.OFF for mode in self._attr_hvac_modes):
            features |= ClimateEntityFeature.TURN_ON
        if HVACMode.OFF in self._attr_hvac_modes:
            features |= ClimateEntityFeature.TURN_OFF
        target_humidity = self._attr_target_humidity
        target_temperature = self._attr_target_temperature
        target_temperature_high = self._attr_target_temperature_high
        target_temperature_low = self._attr_target_temperature_low
        if target_humidity is not None or "set_humidity" in self._command_actions:
            features |= ClimateEntityFeature.TARGET_HUMIDITY
        if target_temperature_high is not None and target_temperature_low is not None:
            features |= ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
        elif (
            target_temperature is not None
            or "set_temperature" in self._command_actions
        ):
            features |= ClimateEntityFeature.TARGET_TEMPERATURE
        if self._attr_fan_modes or "set_fan_mode" in self._command_actions:
            features |= ClimateEntityFeature.FAN_MODE
        if self._attr_preset_modes or "set_preset_mode" in self._command_actions:
            features |= ClimateEntityFeature.PRESET_MODE
        if self._attr_swing_modes or "set_swing_mode" in self._command_actions:
            features |= ClimateEntityFeature.SWING_MODE
        if (
            self._attr_swing_horizontal_modes
            or "set_swing_horizontal_mode" in self._command_actions
        ):
            features |= ClimateEntityFeature.SWING_HORIZONTAL_MODE
        self._attr_supported_features = features

    def _create_state(self, config):
        super()._create_state(config)
        configured_mode = _safe_hvac_mode(config.get(CONF_INITIAL_VALUE))
        self._attr_hvac_mode = (
            configured_mode
            if configured_mode in self._attr_hvac_modes
            else self._default_hvac_mode()
        )
        self._attr_hvac_action = _as_hvac_action(config.get(CONF_HVAC_ACTION))
        if self._attr_hvac_mode == HVACMode.OFF:
            self._attr_hvac_action = HVACAction.OFF
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
        self._attr_fan_mode = _valid_mode_choice(
            None, config.get(CONF_FAN_MODE), self._attr_fan_modes
        )
        self._attr_preset_mode = _valid_mode_choice(
            None, config.get(CONF_PRESET_MODE), self._attr_preset_modes
        )
        self._attr_swing_mode = _valid_mode_choice(
            None, config.get(CONF_SWING_MODE), self._attr_swing_modes
        )
        self._attr_swing_horizontal_mode = _valid_mode_choice(
            None,
            config.get(CONF_SWING_HORIZONTAL_MODE),
            self._attr_swing_horizontal_modes,
        )
        self._refresh_supported_features()

    def _restore_state(self, state, config):
        super()._restore_state(state, config)
        restored_mode = _safe_hvac_mode(
            self._restored_state_value(state, config),
        )
        configured_mode = _safe_hvac_mode(config.get(CONF_INITIAL_VALUE))
        self._attr_hvac_mode = (
            restored_mode
            if restored_mode in self._attr_hvac_modes
            else configured_mode
            if configured_mode in self._attr_hvac_modes
            else self._default_hvac_mode()
        )
        self._attr_hvac_action = _as_hvac_action(
            state.attributes.get(CONF_HVAC_ACTION, config.get(CONF_HVAC_ACTION))
        )
        if self._attr_hvac_mode == HVACMode.OFF:
            self._attr_hvac_action = HVACAction.OFF
        self._attr_current_temperature = self._bounded_temperature(
            state.attributes.get(
                CONF_CURRENT_TEMPERATURE,
                config.get(CONF_CURRENT_TEMPERATURE),
            ),
        )
        self._attr_target_temperature = self._bounded_temperature(
            state.attributes.get(
                ATTR_TEMPERATURE,
                state.attributes.get(
                    CONF_TARGET_TEMPERATURE,
                    config.get(CONF_TARGET_TEMPERATURE),
                ),
            ),
        )
        self._attr_target_temperature_high = self._bounded_temperature(
            state.attributes.get(
                ATTR_TARGET_TEMPERATURE_HIGH,
                state.attributes.get(
                    CONF_TARGET_TEMPERATURE_HIGH,
                    config.get(CONF_TARGET_TEMPERATURE_HIGH),
                ),
            ),
        )
        self._attr_target_temperature_low = self._bounded_temperature(
            state.attributes.get(
                ATTR_TARGET_TEMPERATURE_LOW,
                state.attributes.get(
                    CONF_TARGET_TEMPERATURE_LOW,
                    config.get(CONF_TARGET_TEMPERATURE_LOW),
                ),
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
                ATTR_HUMIDITY,
                state.attributes.get(
                    CONF_TARGET_HUMIDITY,
                    config.get(CONF_TARGET_HUMIDITY),
                ),
            ),
        )
        self._attr_fan_mode = _valid_mode_choice(
            state.attributes.get(CONF_FAN_MODE),
            config.get(CONF_FAN_MODE),
            self._attr_fan_modes,
        )
        self._attr_preset_mode = _valid_mode_choice(
            state.attributes.get(CONF_PRESET_MODE),
            config.get(CONF_PRESET_MODE),
            self._attr_preset_modes,
        )
        self._attr_swing_mode = _valid_mode_choice(
            state.attributes.get(CONF_SWING_MODE),
            config.get(CONF_SWING_MODE),
            self._attr_swing_modes,
        )
        self._attr_swing_horizontal_mode = _valid_mode_choice(
            state.attributes.get(CONF_SWING_HORIZONTAL_MODE),
            config.get(CONF_SWING_HORIZONTAL_MODE),
            self._attr_swing_horizontal_modes,
        )
        self._refresh_supported_features()

    def _apply_native_template_value(self, name: str, value) -> bool:
        name = {
            "humidity": CONF_TARGET_HUMIDITY,
            "target_temp_step": CONF_TARGET_TEMPERATURE_STEP,
            "temperature": CONF_TARGET_TEMPERATURE,
        }.get(name, name)
        if name == CONF_HVAC_MODES:
            if not isinstance(value, (list, tuple)) or not value:
                raise ValueError("hvac_modes must render a non-empty list")
            modes = [_as_hvac_mode(mode) for mode in value]
            if any(mode is None for mode in modes) or len(set(modes)) != len(modes):
                raise ValueError("hvac_modes contains invalid or duplicate modes")
            value = modes
        elif name in {
            CONF_FAN_MODES,
            CONF_PRESET_MODES,
            CONF_SWING_MODES,
            CONF_SWING_HORIZONTAL_MODES,
        }:
            if not isinstance(value, (list, tuple)):
                raise ValueError(f"{name} must render a list")
            value = [str(item).strip() for item in value if str(item).strip()]
            if len(set(value)) != len(value):
                raise ValueError(f"{name} contains duplicate modes")
        elif name == "hvac_mode":
            value = _as_hvac_mode(value)
            if value not in self._attr_hvac_modes:
                raise ValueError(f"Unsupported HVAC mode: {value}")
        elif name == CONF_HVAC_ACTION:
            rendered_value = value
            value = _as_hvac_action(value)
            if value is None and rendered_value not in (None, ""):
                raise ValueError("Invalid HVAC action")
        elif name in {
            CONF_FAN_MODE,
            CONF_PRESET_MODE,
            CONF_SWING_MODE,
            CONF_SWING_HORIZONTAL_MODE,
        }:
            value = str(value).strip()
        elif name in {
            CONF_CURRENT_TEMPERATURE,
            CONF_TARGET_TEMPERATURE,
            CONF_TARGET_TEMPERATURE_HIGH,
            CONF_TARGET_TEMPERATURE_LOW,
        }:
            if value is None or value == "":
                value = None
            else:
                value = _finite_float(value, float("nan"))
                if not math.isfinite(value):
                    raise ValueError(f"{name} must render a finite number")
                value = self._bounded_temperature(value)
        elif name in {CONF_CURRENT_HUMIDITY, CONF_TARGET_HUMIDITY}:
            if value is None or value == "":
                value = None
            else:
                value = _finite_float(value, float("nan"))
                if not math.isfinite(value):
                    raise ValueError(f"{name} must render a finite number")
                value = self._bounded_humidity(value)
        elif name in {
            CONF_MIN_TEMP,
            CONF_MAX_TEMP,
            CONF_MIN_HUMIDITY,
            CONF_MAX_HUMIDITY,
        }:
            value = _finite_float(value, float("nan"))
            if not math.isfinite(value):
                raise ValueError(f"{name} must render a finite number")
        elif name in {CONF_TARGET_TEMPERATURE_STEP, CONF_TARGET_HUMIDITY_STEP}:
            value = _finite_step(value)
            if value is None:
                raise ValueError(f"{name} must render a positive number")
        elif name == CONF_TEMPERATURE_UNIT:
            value = str(value).strip()
            if value not in set(UnitOfTemperature):
                raise ValueError("Invalid temperature unit")
        return super()._apply_native_template_value(name, value)

    def _native_templates_applied(self) -> None:
        if self._attr_min_temp > self._attr_max_temp:
            self._attr_min_temp, self._attr_max_temp = (
                self._attr_max_temp,
                self._attr_min_temp,
            )
        if self._attr_min_humidity > self._attr_max_humidity:
            self._attr_min_humidity, self._attr_max_humidity = (
                self._attr_max_humidity,
                self._attr_min_humidity,
            )
        for attribute in (
            "_attr_current_temperature",
            "_attr_target_temperature",
            "_attr_target_temperature_high",
            "_attr_target_temperature_low",
        ):
            setattr(
                self,
                attribute,
                self._bounded_temperature(getattr(self, attribute, None)),
            )
        for attribute in ("_attr_current_humidity", "_attr_target_humidity"):
            setattr(
                self,
                attribute,
                self._bounded_humidity(getattr(self, attribute, None)),
            )
        if (
            self._attr_target_temperature_low is not None
            and self._attr_target_temperature_high is not None
            and self._attr_target_temperature_low > self._attr_target_temperature_high
        ):
            (
                self._attr_target_temperature_low,
                self._attr_target_temperature_high,
            ) = (
                self._attr_target_temperature_high,
                self._attr_target_temperature_low,
            )
        if self._attr_hvac_mode not in self._attr_hvac_modes:
            self._attr_hvac_mode = self._default_hvac_mode()
        for current_name, modes_name in (
            ("_attr_fan_mode", "_attr_fan_modes"),
            ("_attr_preset_mode", "_attr_preset_modes"),
            ("_attr_swing_mode", "_attr_swing_modes"),
            ("_attr_swing_horizontal_mode", "_attr_swing_horizontal_modes"),
        ):
            if getattr(self, current_name, None) not in getattr(self, modes_name, []):
                setattr(self, current_name, None)
        if self._attr_hvac_mode == HVACMode.OFF:
            self._attr_hvac_action = HVACAction.OFF
        self._refresh_supported_features()

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
        if hvac_mode == HVACMode.OFF:
            self._attr_hvac_action = HVACAction.OFF
        elif self._attr_hvac_action in {None, HVACAction.OFF}:
            self._attr_hvac_action = HVACAction.IDLE
        self.async_write_ha_state()

    def _default_hvac_mode(self) -> HVACMode:
        if HVACMode.OFF in self._attr_hvac_modes:
            return HVACMode.OFF
        if self._attr_hvac_modes:
            return self._attr_hvac_modes[0]
        return HVACMode.OFF

    def _bounded_temperature(self, temperature):
        if temperature is None or isinstance(temperature, bool):
            return None
        try:
            temperature = float(temperature)
        except (TypeError, ValueError, OverflowError):
            return None
        if not math.isfinite(temperature):
            return None
        return max(self._attr_min_temp, min(self._attr_max_temp, temperature))

    def _bounded_humidity(self, humidity):
        if humidity is None or isinstance(humidity, bool):
            return None
        try:
            humidity = float(humidity)
        except (TypeError, ValueError, OverflowError):
            return None
        if not math.isfinite(humidity):
            return None
        return max(self._attr_min_humidity, min(self._attr_max_humidity, humidity))

    def _validate_temperature(self, temperature) -> float:
        if isinstance(temperature, bool):
            raise ValueError("Temperature must be numeric")
        try:
            temperature = float(temperature)
        except (TypeError, ValueError, OverflowError) as err:
            raise ValueError("Temperature must be numeric") from err
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
        next_mode = next(
            (mode for mode in self._attr_hvac_modes if mode != HVACMode.OFF),
            None,
        )
        if next_mode is None:
            raise NotImplementedError("Climate entity has no non-off HVAC mode")
        self._attr_hvac_mode = next_mode
        if self._attr_hvac_action in {None, HVACAction.OFF}:
            self._attr_hvac_action = HVACAction.IDLE
        self.async_write_ha_state()

    async def async_turn_off(self) -> None:
        if HVACMode.OFF not in self._attr_hvac_modes:
            raise NotImplementedError("Climate entity does not support the off HVAC mode")
        self._attr_hvac_mode = HVACMode.OFF
        self._attr_hvac_action = HVACAction.OFF
        self.async_write_ha_state()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        next_temperature = self._attr_target_temperature
        next_temperature_high = self._attr_target_temperature_high
        next_temperature_low = self._attr_target_temperature_low
        next_hvac_mode = self._attr_hvac_mode
        if ATTR_HVAC_MODE in kwargs:
            next_hvac_mode = _as_hvac_mode(kwargs[ATTR_HVAC_MODE])
            if next_hvac_mode not in self._attr_hvac_modes:
                raise ValueError(f"Unsupported HVAC mode: {next_hvac_mode}")
        if ATTR_TEMPERATURE in kwargs:
            next_temperature = self._validate_temperature(kwargs[ATTR_TEMPERATURE])
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
            raise ValueError(
                "Low target temperature cannot exceed high target temperature"
            )
        self._attr_target_temperature = next_temperature
        self._attr_target_temperature_high = next_temperature_high
        self._attr_target_temperature_low = next_temperature_low
        self._attr_hvac_mode = next_hvac_mode
        if next_hvac_mode == HVACMode.OFF:
            self._attr_hvac_action = HVACAction.OFF
        elif self._attr_hvac_action in {None, HVACAction.OFF}:
            self._attr_hvac_action = HVACAction.IDLE
        self._refresh_supported_features()
        self.async_write_ha_state()

    async def async_set_humidity(self, humidity: int) -> None:
        if isinstance(humidity, bool):
            raise ValueError("Humidity must be numeric")
        try:
            humidity = float(humidity)
        except (TypeError, ValueError, OverflowError) as err:
            raise ValueError("Humidity must be numeric") from err
        if not math.isfinite(humidity):
            raise ValueError("Humidity must be finite")
        if not self._attr_min_humidity <= humidity <= self._attr_max_humidity:
            raise ValueError(
                "Humidity must be within the configured minimum and maximum"
            )
        self._attr_target_humidity = humidity
        self._refresh_supported_features()
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
        if mode == HVACMode.OFF:
            self._attr_hvac_action = HVACAction.OFF
        elif self._attr_hvac_action in {None, HVACAction.OFF}:
            self._attr_hvac_action = HVACAction.IDLE
