"""
This component provides support for a virtual humidifier or dehumidifier.

"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components.humidifier import (
    ATTR_HUMIDITY,
    HumidifierAction,
    HumidifierDeviceClass,
    HumidifierEntity,
    HumidifierEntityFeature,
)
from homeassistant.components.humidifier import (
    DOMAIN as PLATFORM_DOMAIN,
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
from .humidifier_options import migrate_legacy_humidifier_attributes

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
    try:
        return HumidifierAction(str(value).strip().lower())
    except (TypeError, ValueError):
        return None


def _as_device_class(value) -> HumidifierDeviceClass | None:
    if value is None:
        return None
    if isinstance(value, HumidifierDeviceClass):
        return value
    return HumidifierDeviceClass(str(value).strip().lower())


def _finite_float(value, default: float) -> float:
    """Return a finite configured number or a compatibility default."""
    if isinstance(value, bool):
        return default
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return result if math.isfinite(result) else default


BASE_SCHEMA = virtual_schema(
    DEFAULT_HUMIDIFIER_VALUE,
    {
        vol.Optional(CONF_ACTION): cv.string,
        vol.Optional(
            CONF_CLASS, default=HumidifierDeviceClass.HUMIDIFIER
        ): _as_device_class,
        vol.Optional(CONF_CURRENT_HUMIDITY): vol.Coerce(float),
        vol.Optional(CONF_MAX_HUMIDITY, default=100): vol.Coerce(float),
        vol.Optional(CONF_MIN_HUMIDITY, default=0): vol.Coerce(float),
        vol.Optional(CONF_MODE): cv.string,
        vol.Optional(CONF_MODES, default=list): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional(CONF_TARGET_HUMIDITY): vol.Coerce(float),
        vol.Optional(CONF_TARGET_HUMIDITY_STEP): vol.Coerce(float),
    },
)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(BASE_SCHEMA)
HUMIDIFIER_SCHEMA = vol.Schema(BASE_SCHEMA)


def normalize_domain_options(config):
    """Promote legacy attributes before persisted-data validation."""
    return migrate_legacy_humidifier_attributes(config)


def validate_domain_options(config) -> None:
    """Validate humidifier ranges, modes, and initial values."""
    if str(config.get(CONF_INITIAL_VALUE, "off")).lower() not in {"on", "off"}:
        raise vol.Invalid("initial_value must be on or off")
    action = config.get(CONF_ACTION)
    if action is not None and _as_action(action) is None:
        raise vol.Invalid("action is invalid")
    modes = config.get(CONF_MODES, [])
    if any(not str(mode).strip() for mode in modes):
        raise vol.Invalid("modes cannot contain empty values")
    if len(set(modes)) != len(modes):
        raise vol.Invalid("modes cannot contain duplicate values")
    mode = config.get(CONF_MODE)
    if mode is not None and mode not in modes:
        raise vol.Invalid("mode must be included in modes")

    minimum = _finite_float(config.get(CONF_MIN_HUMIDITY), float("nan"))
    maximum = _finite_float(config.get(CONF_MAX_HUMIDITY), float("nan"))
    if not math.isfinite(minimum) or not math.isfinite(maximum) or minimum > maximum:
        raise vol.Invalid("humidity range is invalid")
    for field_name in (CONF_CURRENT_HUMIDITY, CONF_TARGET_HUMIDITY):
        if field_name not in config:
            continue
        value = _finite_float(config[field_name], float("nan"))
        if not math.isfinite(value) or not minimum <= value <= maximum:
            raise vol.Invalid(f"{field_name} must be within the humidity range")
    if CONF_TARGET_HUMIDITY_STEP in config:
        step = _finite_float(config[CONF_TARGET_HUMIDITY_STEP], 0)
        if step <= 0:
            raise vol.Invalid("target_humidity_step must be positive")


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
            VirtualHumidifier(
                HUMIDIFIER_SCHEMA(migrate_legacy_humidifier_attributes(entity)),
                False,
            )
        )
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

        self._attr_min_humidity = _finite_float(config.get(CONF_MIN_HUMIDITY), 0)
        self._attr_max_humidity = _finite_float(config.get(CONF_MAX_HUMIDITY), 100)
        if self._attr_min_humidity > self._attr_max_humidity:
            self._attr_min_humidity, self._attr_max_humidity = (
                self._attr_max_humidity,
                self._attr_min_humidity,
            )
        target_step = config.get(CONF_TARGET_HUMIDITY_STEP)
        self._attr_target_humidity_step = (
            step
            if target_step is not None and (step := _finite_float(target_step, 0)) > 0
            else None
        )

        _LOGGER.debug(f"VirtualHumidifier: {self.name} created")

    def _create_state(self, config):
        super()._create_state(config)
        self._attr_is_on = config.get(CONF_INITIAL_VALUE).lower() == STATE_ON
        self._attr_action = _as_action(config.get(CONF_ACTION))
        if not self._attr_is_on:
            self._attr_action = HumidifierAction.OFF
        self._attr_current_humidity = self._bounded_humidity(
            config.get(CONF_CURRENT_HUMIDITY),
        )
        self._attr_target_humidity = self._bounded_humidity(
            config.get(CONF_TARGET_HUMIDITY)
        )
        configured_mode = config.get(CONF_MODE)
        self._attr_mode = (
            configured_mode
            if configured_mode in self._attr_available_modes
            else (self._attr_available_modes[0] if self._attr_available_modes else None)
        )

    def _restore_state(self, state, config):
        super()._restore_state(state, config)
        self._attr_is_on = state.state.lower() == STATE_ON
        self._attr_action = _as_action(
            state.attributes.get(CONF_ACTION, config.get(CONF_ACTION))
        )
        if not self._attr_is_on:
            self._attr_action = HumidifierAction.OFF
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
            )
        )
        restored_mode = state.attributes.get(CONF_MODE)
        configured_mode = config.get(CONF_MODE)
        self._attr_mode = (
            restored_mode
            if restored_mode in self._attr_available_modes
            else configured_mode
            if configured_mode in self._attr_available_modes
            else self._attr_available_modes[0]
            if self._attr_available_modes
            else None
        )

    def _bounded_humidity(self, humidity):
        if humidity is None:
            return None
        try:
            humidity = float(humidity)
        except (TypeError, ValueError, OverflowError):
            return None
        if not math.isfinite(humidity):
            return None
        return max(
            self._attr_min_humidity,
            min(self._attr_max_humidity, humidity),
        )

    def _update_attributes(self):
        super()._update_attributes()
        self._attr_extra_state_attributes.update(
            {
                name: value
                for name, value in ((ATTR_DEVICE_CLASS, self._attr_device_class),)
                if value is not None
            }
        )

    def _apply_native_template_value(self, name: str, value) -> bool:
        if name == "humidity":
            name = CONF_TARGET_HUMIDITY
        if name == "available_modes":
            if not isinstance(value, (list, tuple)):
                raise ValueError("available_modes must render a list")
            value = [str(item).strip() for item in value if str(item).strip()]
            if len(set(value)) != len(value):
                raise ValueError("available_modes contains duplicate values")
        elif name == CONF_MODE:
            value = str(value).strip()
        elif name == CONF_ACTION:
            value = _as_action(value)
            if value is None:
                raise ValueError("Invalid humidifier action")
        elif name == "device_class":
            value = _as_device_class(value)
        elif name in {CONF_CURRENT_HUMIDITY, CONF_TARGET_HUMIDITY}:
            if value is None or value == "":
                value = None
            else:
                value = _finite_float(value, float("nan"))
                if not math.isfinite(value):
                    raise ValueError(f"{name} must render a finite number")
                value = self._bounded_humidity(value)
        elif name in {CONF_MIN_HUMIDITY, CONF_MAX_HUMIDITY}:
            value = _finite_float(value, float("nan"))
            if not math.isfinite(value):
                raise ValueError(f"{name} must render a finite number")
        elif name == CONF_TARGET_HUMIDITY_STEP:
            value = _finite_float(value, float("nan"))
            if not math.isfinite(value) or value <= 0:
                raise ValueError("target_humidity_step must be positive")
        elif name in {"state", "is_on"}:
            old_state = self._attr_is_on
            self.set_state(value)
            return old_state != self._attr_is_on
        return super()._apply_native_template_value(name, value)

    def _native_templates_applied(self) -> None:
        if self._attr_min_humidity > self._attr_max_humidity:
            self._attr_min_humidity, self._attr_max_humidity = (
                self._attr_max_humidity,
                self._attr_min_humidity,
            )
        self._attr_current_humidity = self._bounded_humidity(
            self._attr_current_humidity
        )
        self._attr_target_humidity = self._bounded_humidity(
            self._attr_target_humidity
        )
        if self._attr_mode not in self._attr_available_modes:
            self._attr_mode = None
        self._attr_supported_features = HumidifierEntityFeature(0)
        if self._attr_available_modes or "set_mode" in self._command_actions:
            self._attr_supported_features |= HumidifierEntityFeature.MODES

    @property
    def state_attributes(self):
        data = dict(super().state_attributes or {})
        data.update(self._attr_extra_state_attributes or {})
        return data

    async def async_turn_on(self, **kwargs) -> None:
        self._attr_is_on = True
        if self._attr_action in {None, HumidifierAction.OFF}:
            self._attr_action = (
                HumidifierAction.DRYING
                if self._attr_device_class == HumidifierDeviceClass.DEHUMIDIFIER
                else HumidifierAction.HUMIDIFYING
            )
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self._attr_is_on = False
        self._attr_action = HumidifierAction.OFF
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
        self.async_write_ha_state()

    async def async_set_mode(self, mode: str) -> None:
        if mode not in self._attr_available_modes:
            raise ValueError(f"Invalid humidifier mode: {mode}")
        self._attr_mode = mode
        self.async_write_ha_state()

    def set_state(self, value) -> None:
        self._attr_is_on = str(value).lower() in ["y", "yes", "t", "true", "on", "1"]
        if not self._attr_is_on:
            self._attr_action = HumidifierAction.OFF
        elif self._attr_action in {None, HumidifierAction.OFF}:
            self._attr_action = (
                HumidifierAction.DRYING
                if self._attr_device_class == HumidifierDeviceClass.DEHUMIDIFIER
                else HumidifierAction.HUMIDIFYING
            )
