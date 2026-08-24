"""Virtual vacuum support."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components.vacuum import (
    ATTR_FAN_SPEED,
    ATTR_FAN_SPEED_LIST,
    StateVacuumEntity,
    VacuumActivity,
    VacuumEntityFeature,
)
from homeassistant.components.vacuum import (
    DOMAIN as PLATFORM_DOMAIN,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_BATTERY_LEVEL,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.config_validation import PLATFORM_SCHEMA
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import get_entity_configs
from .const import *
from .entity import VirtualEntity, nonnegative_int, virtual_schema

_LOGGER = logging.getLogger(__name__)

DEPENDENCIES = [COMPONENT_DOMAIN]

CONF_ACTIVITY = "activity"
CONF_BATTERY_LEVEL = "battery_level"
CONF_FAN_SPEED = "fan_speed"
CONF_FAN_SPEED_LIST = "fan_speed_list"
CONF_SUPPORTED_FEATURES = "supported_features"

DEFAULT_VACUUM_VALUE = VacuumActivity.DOCKED.value
DEFAULT_SUPPORTED_FEATURES = (
    VacuumEntityFeature.STATE
    | VacuumEntityFeature.START
    | VacuumEntityFeature.PAUSE
    | VacuumEntityFeature.STOP
    | VacuumEntityFeature.RETURN_HOME
    | VacuumEntityFeature.LOCATE
    | VacuumEntityFeature.CLEAN_SPOT
    | VacuumEntityFeature.SEND_COMMAND
)
_LEGACY_BATTERY_FEATURE = getattr(
    VacuumEntityFeature,
    "BATTERY",
    VacuumEntityFeature(0),
)


def _as_activity(value: Any) -> VacuumActivity | None:
    """Convert UI/string values to Home Assistant's vacuum activity enum."""
    if value is None or isinstance(value, VacuumActivity):
        return value
    try:
        return VacuumActivity(str(value).lower())
    except ValueError:
        return None


def _as_supported_features(value: Any) -> VacuumEntityFeature:
    """Accept a feature bitmask or a UI-friendly list of feature names."""
    if value is None:
        return DEFAULT_SUPPORTED_FEATURES
    if isinstance(value, VacuumEntityFeature):
        return value
    if isinstance(value, bool):
        raise vol.Invalid("supported_features must be a bitmask or feature names")
    if isinstance(value, int):
        if value < 0:
            raise vol.Invalid("supported_features must be non-negative")
        return VacuumEntityFeature(value)

    values = value.split(",") if isinstance(value, str) else value
    if not isinstance(values, (list, tuple, set)):
        raise vol.Invalid("supported_features must be a bitmask or feature names")

    features = VacuumEntityFeature(0)
    for item in values:
        if isinstance(item, bool):
            raise vol.Invalid("vacuum feature names cannot be boolean")
        if isinstance(item, int):
            if item < 0:
                raise vol.Invalid("vacuum feature bitmasks must be non-negative")
            features |= VacuumEntityFeature(item)
            continue
        name = str(item).strip().upper().replace(" ", "_").replace("-", "_")
        try:
            features |= VacuumEntityFeature[name]
        except KeyError as err:
            raise vol.Invalid(f"Unknown vacuum feature: {item}") from err
    return features


def _as_battery_level(value: Any, fallback: int | None = None) -> int | None:
    """Return a valid restored battery percentage."""
    if value is None or isinstance(value, bool):
        return fallback
    try:
        battery_level = int(value)
    except (TypeError, ValueError, OverflowError):
        return fallback
    return battery_level if 0 <= battery_level <= 100 else fallback


def _restore_last_command(value: Any) -> dict[str, Any] | None:
    """Return a detached, valid vacuum command restored from state."""
    if not isinstance(value, Mapping):
        return None
    command = value.get("command")
    if not isinstance(command, str) or not command.strip():
        return None
    restored: dict[str, Any] = {"command": command}
    if "params" not in value:
        return restored
    params = value.get("params")
    if params is None:
        restored["params"] = None
    elif isinstance(params, Mapping):
        restored["params"] = dict(params)
    elif isinstance(params, list):
        restored["params"] = list(params)
    else:
        return None
    return restored


def validate_domain_options(config) -> None:
    """Reject inconsistent vacuum options entered through the UI."""
    if (
        CONF_ACTIVITY in config
        and config[CONF_ACTIVITY] is not None
        and _as_activity(config[CONF_ACTIVITY]) is None
    ):
        raise vol.Invalid("activity must be a supported vacuum activity")
    if (
        CONF_BATTERY_LEVEL in config
        and _as_battery_level(config[CONF_BATTERY_LEVEL]) is None
    ):
        raise vol.Invalid("battery_level must be between 0 and 100")
    fan_speed = config.get(CONF_FAN_SPEED)
    fan_speeds = config.get(CONF_FAN_SPEED_LIST, [])
    if fan_speed is not None and fan_speeds and fan_speed not in fan_speeds:
        raise vol.Invalid("fan_speed must be in fan_speed_list")


BASE_SCHEMA = virtual_schema(DEFAULT_VACUUM_VALUE, {
    vol.Optional(CONF_ACTIVITY): vol.Any(cv.string, _as_activity),
    vol.Optional(CONF_BATTERY_LEVEL): vol.All(nonnegative_int, vol.Range(max=100)),
    vol.Optional(CONF_FAN_SPEED): cv.string,
    vol.Optional(CONF_FAN_SPEED_LIST, default=list): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional(CONF_SUPPORTED_FEATURES): _as_supported_features,
})

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(BASE_SCHEMA)
VACUUM_SCHEMA = vol.Schema(BASE_SCHEMA, extra=vol.ALLOW_EXTRA)
ENTITY_SCHEMA = VACUUM_SCHEMA


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
    """Create virtual vacuum entities from the UI config entry."""
    entities = [
        VirtualVacuum(VACUUM_SCHEMA(entity), False)
        for entity in get_entity_configs(hass, entry.data[ATTR_GROUP_NAME], PLATFORM_DOMAIN)
    ]
    async_add_entities(entities)


class VirtualVacuum(VirtualEntity, StateVacuumEntity):
    """A stateful virtual robot vacuum with native HA vacuum commands."""

    def __init__(self, config: dict, old_style: bool):
        super().__init__(config, PLATFORM_DOMAIN, old_style)

        self._attr_activity = _as_activity(config.get(CONF_ACTIVITY))
        self._battery_level = _as_battery_level(config.get(CONF_BATTERY_LEVEL))
        self._attr_fan_speed = config.get(CONF_FAN_SPEED)
        self._attr_fan_speed_list = list(config.get(CONF_FAN_SPEED_LIST, []))
        self._base_supported_features = config.get(
            CONF_SUPPORTED_FEATURES, DEFAULT_SUPPORTED_FEATURES
        )
        self._attr_supported_features = VacuumEntityFeature(0)
        self._refresh_supported_features()

        self._last_command: dict[str, Any] | None = None
        _LOGGER.debug("VirtualVacuum: %s created", self.name)

    def _refresh_supported_features(self) -> None:
        features = VacuumEntityFeature(self._base_supported_features)
        if self._attr_fan_speed_list or "set_fan_speed" in self._command_actions:
            features |= VacuumEntityFeature.FAN_SPEED
        # HA removes the legacy vacuum battery feature in 2026.8. A linked
        # battery sensor is generated from this configuration instead.
        features &= ~_LEGACY_BATTERY_FEATURE
        self._attr_supported_features = features

    @property
    def activity(self) -> VacuumActivity | None:
        """Return the current activity without cached-property staleness."""
        return self._attr_activity

    @property
    def supported_features(self) -> VacuumEntityFeature:
        """Return the configured feature set."""
        return self._attr_supported_features

    @property
    def fan_speed(self) -> str | None:
        return self._attr_fan_speed

    @property
    def fan_speed_list(self) -> list[str]:
        return self._attr_fan_speed_list

    def _create_state(self, config):
        super()._create_state(config)
        self._attr_activity = _as_activity(
            config.get(CONF_ACTIVITY, config.get(CONF_INITIAL_VALUE))
        )

    def _restore_state(self, state, config):
        super()._restore_state(state, config)
        self._attr_activity = _as_activity(state.state) or _as_activity(
            config.get(CONF_ACTIVITY, config.get(CONF_INITIAL_VALUE))
        )
        self._battery_level = _as_battery_level(
            state.attributes.get(ATTR_BATTERY_LEVEL),
            _as_battery_level(config.get(CONF_BATTERY_LEVEL)),
        )
        restored_fan_speed = state.attributes.get(
            ATTR_FAN_SPEED, config.get(CONF_FAN_SPEED)
        )
        self._attr_fan_speed = (
            restored_fan_speed
            if not self._attr_fan_speed_list
            or restored_fan_speed in self._attr_fan_speed_list
            else config.get(CONF_FAN_SPEED)
        )
        self._last_command = _restore_last_command(
            state.attributes.get("last_command")
        )

    def _update_attributes(self):
        super()._update_attributes()
        self._attr_extra_state_attributes.update({
            ATTR_BATTERY_LEVEL: self._battery_level,
            ATTR_FAN_SPEED: self._attr_fan_speed,
            ATTR_FAN_SPEED_LIST: self._attr_fan_speed_list,
        })
        if self._last_command is not None:
            self._attr_extra_state_attributes["last_command"] = self._last_command

    def _write_state(self) -> None:
        self._update_attributes()
        self.async_write_ha_state()

    def _set_activity(self, activity: VacuumActivity) -> None:
        self._attr_activity = activity
        self._write_state()

    async def async_start(self) -> None:
        self._set_activity(VacuumActivity.CLEANING)

    async def async_pause(self) -> None:
        self._set_activity(VacuumActivity.PAUSED)

    async def async_stop(self, **kwargs) -> None:
        self._set_activity(VacuumActivity.IDLE)

    async def async_return_to_base(self, **kwargs) -> None:
        self._set_activity(VacuumActivity.RETURNING)

    async def async_clean_spot(self, **kwargs) -> None:
        self._set_activity(VacuumActivity.CLEANING)

    async def async_locate(self, **kwargs) -> None:
        self._last_command = {"command": "locate"}
        self._write_state()

    async def async_set_fan_speed(self, fan_speed: str, **kwargs) -> None:
        if self._attr_fan_speed_list and fan_speed not in self._attr_fan_speed_list:
            raise ValueError(f"Unsupported vacuum fan speed: {fan_speed}")
        self._attr_fan_speed = fan_speed
        self._write_state()

    async def async_send_command(
        self,
        command: str,
        params: dict[str, Any] | list[Any] | None = None,
        **kwargs,
    ) -> None:
        self._last_command = {"command": command, "params": params}
        self._write_state()

    def set_state(self, value) -> None:
        """Allow templates and the generic service to set vacuum activity."""
        activity = _as_activity(value)
        if activity is None:
            value = str(value).lower()
            if value in {"on", "true", "1", "yes", "y", "t"}:
                activity = VacuumActivity.CLEANING
            elif value in {"off", "false", "0", "no", "n", "f"}:
                activity = VacuumActivity.IDLE
            else:
                raise ValueError(f"Invalid vacuum activity: {value}")
        self._attr_activity = activity
        self.async_schedule_update_ha_state()

    def _apply_native_template_value(self, name: str, value) -> bool:
        if name in {"state", CONF_ACTIVITY, "activity"}:
            activity = _as_activity(value)
            if activity is None:
                raise ValueError(f"Invalid vacuum activity: {value}")
            name = "activity"
            value = activity
        elif name == CONF_BATTERY_LEVEL:
            if value is None or value == "":
                changed = self._battery_level is not None
                self._battery_level = None
                return changed
            value = _as_battery_level(value)
            if value is None:
                raise ValueError("battery_level must be between 0 and 100")
            changed = self._battery_level != value
            self._battery_level = value
            return changed
        elif name == CONF_FAN_SPEED_LIST:
            if not isinstance(value, (list, tuple, set)):
                raise ValueError("fan_speed_list must render a list")
            value = [str(item).strip() for item in value if str(item).strip()]
            if len(set(value)) != len(value):
                raise ValueError("fan_speed_list contains duplicate values")
        elif name == CONF_FAN_SPEED:
            value = None if value is None or value == "" else str(value)
            if value is not None and self._attr_fan_speed_list and value not in self._attr_fan_speed_list:
                raise ValueError(f"Unsupported vacuum fan speed: {value}")
        elif name == CONF_SUPPORTED_FEATURES:
            value = _as_supported_features(value)
            changed = self._base_supported_features != value
            self._base_supported_features = value
            return changed
        return super()._apply_native_template_value(name, value)

    def _native_templates_applied(self) -> None:
        if (
            self._attr_fan_speed_list
            and self._attr_fan_speed not in self._attr_fan_speed_list
        ):
            self._attr_fan_speed = None
        self._refresh_supported_features()
