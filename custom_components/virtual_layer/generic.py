"""
Generic virtual entity support for building-block domains.

This module intentionally implements the smallest common surface: state,
availability, restore, device registration, extra attributes, and templates.
Domain-specific files can use this when Home Assistant exposes a building block
domain but the virtual layer does not need a rich service API yet.
"""

import logging
import math
import re
from datetime import date, datetime, time

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components.button import ButtonEntity
from homeassistant.components.date import DateEntity
from homeassistant.components.datetime import DateTimeEntity
from homeassistant.components.lawn_mower import (
    LawnMowerActivity,
    LawnMowerEntity,
    LawnMowerEntityFeature,
)
from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.components.remote import RemoteEntity
from homeassistant.components.select import SelectEntity
from homeassistant.components.siren import SirenEntity, SirenEntityFeature
from homeassistant.components.text import TextEntity, TextMode
from homeassistant.components.time import TimeEntity
from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
from homeassistant.components.water_heater import (
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_TEMPERATURE,
    CONF_ICON,
    STATE_OFF,
)
from homeassistant.helpers.entity import Entity
from homeassistant.util import dt as dt_util

from . import get_entity_configs
from .const import *
from .entity import VirtualEntity, virtual_schema

_LOGGER = logging.getLogger(__name__)

CONF_STATE_CLASS = "state_class"

DEFAULT_GENERIC_VALUE = "unknown"

GENERIC_SCHEMA = virtual_schema(DEFAULT_GENERIC_VALUE, {
    vol.Optional(CONF_CLASS): cv.string,
    vol.Optional(CONF_ICON): cv.string,
    vol.Optional(CONF_STATE_CLASS): cv.string,
})
ENTITY_SCHEMA = vol.Schema(GENERIC_SCHEMA, extra=vol.ALLOW_EXTRA)


class GenericVirtualEntity(VirtualEntity, Entity):
    """Generic implementation for virtual building-block domains."""

    def __init__(self, config, domain: str, old_style: bool):
        super().__init__(config, domain, old_style)
        self._domain = domain
        self._attr_device_class = config.get(CONF_CLASS)
        self._attr_icon = config.get(CONF_ICON)
        self._attr_state_class = config.get(CONF_STATE_CLASS)
        self._domain_options = generic_entity_options(config)
        _LOGGER.info(f"GenericVirtualEntity: {self.name} ({domain}) created")

    @property
    def state(self):
        return self._attr_state

    def _create_state(self, config):
        super()._create_state(config)
        self._attr_state = config.get(CONF_INITIAL_VALUE)

    def _restore_state(self, state, config):
        super()._restore_state(state, config)
        self._attr_state = state.state

    def _update_attributes(self):
        super()._update_attributes()
        self._attr_extra_state_attributes.update({
            name: value for name, value in (
                (ATTR_DEVICE_CLASS, self._attr_device_class),
                (CONF_STATE_CLASS, self._attr_state_class),
            ) if value is not None
        })
        self._attr_extra_state_attributes.update(self._domain_options)

    def set_state(self, value) -> None:
        self._attr_state = value


def _has_value(value) -> bool:
    return value is not None and str(value).lower() not in {
        "",
        "none",
        "unknown",
        "unavailable",
    }


def _string_list(value, default=()) -> list[str]:
    """Return a safe list of non-empty strings from persisted domain options."""
    if not isinstance(value, (list, tuple, set)):
        value = default
    return [str(item) for item in value if str(item).strip()]


def _safe_float(value, default: float) -> float:
    """Read a finite numeric option without making old settings unloadable."""
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _safe_int(value, default: int, minimum: int = 0) -> int:
    """Read an integer option with a lower bound from persisted data."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, parsed)


def _safe_bool(value, default: bool = False) -> bool:
    """Parse a stored boolean without trusting Python's truthiness rules."""
    try:
        return cv.boolean(value)
    except vol.Invalid:
        return default


class _NativeGenericMixin:
    """Common config and attributes for native building-block entities."""

    PLATFORM_DOMAIN: str

    def __init__(self, config, old_style: bool):
        super().__init__(config, self.PLATFORM_DOMAIN, old_style)
        self._attr_device_class = config.get(CONF_CLASS)
        self._attr_icon = config.get(CONF_ICON)
        self._attr_state_class = config.get(CONF_STATE_CLASS)
        self._domain_options = generic_entity_options(config)

    def _update_attributes(self):
        super()._update_attributes()
        self._attr_extra_state_attributes.update(self._domain_options)


class VirtualSelect(_NativeGenericMixin, VirtualEntity, SelectEntity):
    """Virtual select with Home Assistant's native option services."""

    PLATFORM_DOMAIN = "select"

    def __init__(self, config, old_style: bool):
        super().__init__(config, old_style)
        self._attr_options = _string_list(config.get("options"))
        initial = str(config.get(CONF_INITIAL_VALUE, ""))
        if _has_value(initial) and initial not in self._attr_options:
            self._attr_options.append(initial)

    def _create_state(self, config):
        super()._create_state(config)
        value = str(config.get(CONF_INITIAL_VALUE, ""))
        self._attr_current_option = value if value in self._attr_options else None

    def _restore_state(self, state, config):
        super()._restore_state(state, config)
        self.set_state(state.state)

    def set_state(self, value) -> None:
        if not _has_value(value):
            self._attr_current_option = None
            return
        option = str(value)
        if option not in self._attr_options:
            self._attr_options.append(option)
        self._attr_current_option = option

    async def async_select_option(self, option: str) -> None:
        if option not in self._attr_options:
            raise ValueError(f"Invalid select option: {option}")
        self._attr_current_option = option
        self.async_write_ha_state()


class VirtualText(_NativeGenericMixin, VirtualEntity, TextEntity):
    """Virtual text with native length and pattern capabilities."""

    PLATFORM_DOMAIN = "text"

    def __init__(self, config, old_style: bool):
        super().__init__(config, old_style)
        self._attr_native_min = _safe_int(config.get("min", 0), 0)
        self._attr_native_max = _safe_int(config.get("max", 255), 255)
        if self._attr_native_min > self._attr_native_max:
            self._attr_native_min, self._attr_native_max = (
                self._attr_native_max,
                self._attr_native_min,
            )
        try:
            self._attr_mode = TextMode(config.get("mode", TextMode.TEXT))
        except (TypeError, ValueError):
            self._attr_mode = TextMode.TEXT
        pattern = config.get("pattern")
        try:
            self._pattern_regex = re.compile(pattern) if isinstance(pattern, str) else None
        except re.error:
            self._pattern_regex = None
        self._attr_pattern = pattern if self._pattern_regex is not None else None

    def _create_state(self, config):
        super()._create_state(config)
        self._attr_native_value = str(config.get(CONF_INITIAL_VALUE, ""))

    def _restore_state(self, state, config):
        super()._restore_state(state, config)
        self._attr_native_value = state.state

    def set_state(self, value) -> None:
        self._attr_native_value = str(value)

    async def async_set_value(self, value: str) -> None:
        if not isinstance(value, str):
            raise TypeError("Text value must be a string")
        if not self._attr_native_min <= len(value) <= self._attr_native_max:
            raise ValueError("Text value is outside the configured length range")
        if self._pattern_regex is not None and self._pattern_regex.fullmatch(value) is None:
            raise ValueError("Text value does not match the configured pattern")
        self._attr_native_value = value
        self.async_write_ha_state()


class _TemporalEntityMixin(_NativeGenericMixin):
    """Shared state lifecycle for date, time, and datetime entities."""

    def _create_state(self, config):
        super()._create_state(config)
        self._attr_native_value = self._parse_value(config.get(CONF_INITIAL_VALUE))

    def _restore_state(self, state, config):
        super()._restore_state(state, config)
        self._attr_native_value = self._parse_value(state.state)

    def set_state(self, value) -> None:
        self._attr_native_value = self._parse_value(value)

    async def async_set_value(self, value) -> None:
        parsed = self._parse_value(value)
        if parsed is None:
            raise ValueError(f"Invalid {self.PLATFORM_DOMAIN} value: {value}")
        self._attr_native_value = parsed
        self.async_write_ha_state()


class VirtualDate(_TemporalEntityMixin, VirtualEntity, DateEntity):
    PLATFORM_DOMAIN = "date"

    @staticmethod
    def _parse_value(value) -> date | None:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if not _has_value(value):
            return None
        try:
            return date.fromisoformat(str(value))
        except ValueError:
            return None


class VirtualTime(_TemporalEntityMixin, VirtualEntity, TimeEntity):
    PLATFORM_DOMAIN = "time"

    @staticmethod
    def _parse_value(value) -> time | None:
        if isinstance(value, time):
            return value
        if not _has_value(value):
            return None
        try:
            return time.fromisoformat(str(value))
        except ValueError:
            return None


class VirtualDateTime(_TemporalEntityMixin, VirtualEntity, DateTimeEntity):
    PLATFORM_DOMAIN = "datetime"

    @staticmethod
    def _parse_value(value) -> datetime | None:
        if isinstance(value, datetime):
            parsed = value
        elif _has_value(value):
            parsed = dt_util.parse_datetime(str(value))
        else:
            return None
        if parsed is None:
            return None
        if parsed.tzinfo is None:
            parsed = dt_util.as_local(parsed)
        return parsed


class VirtualButton(_NativeGenericMixin, VirtualEntity, ButtonEntity):
    """Virtual stateless button."""

    PLATFORM_DOMAIN = "button"

    def set_state(self, value) -> None:
        self._virtual_attributes["last_value"] = value

    async def async_press(self) -> None:
        self._virtual_attributes["press_count"] = _safe_int(
            self._virtual_attributes.get("press_count", 0),
            0,
        ) + 1
        self._update_attributes()
        self.async_write_ha_state()


class VirtualSiren(_NativeGenericMixin, VirtualEntity, SirenEntity):
    """Virtual siren with tone, volume, and duration capabilities."""

    PLATFORM_DOMAIN = "siren"

    def __init__(self, config, old_style: bool):
        super().__init__(config, old_style)
        self._attr_available_tones = _string_list(config.get("available_tones"))
        self._attr_supported_features = (
            SirenEntityFeature.TURN_ON | SirenEntityFeature.TURN_OFF
        )
        if self._attr_available_tones:
            self._attr_supported_features |= SirenEntityFeature.TONES
        if _safe_bool(config.get("support_volume", True), True):
            self._attr_supported_features |= SirenEntityFeature.VOLUME_SET
        if _safe_bool(config.get("support_duration", True), True):
            self._attr_supported_features |= SirenEntityFeature.DURATION

    def _create_state(self, config):
        super()._create_state(config)
        self.set_state(config.get(CONF_INITIAL_VALUE))

    def _restore_state(self, state, config):
        super()._restore_state(state, config)
        self.set_state(state.state)

    def set_state(self, value) -> None:
        self._attr_is_on = str(value).lower() in {"1", "on", "true", "yes"}

    async def async_turn_on(self, **kwargs) -> None:
        tone = kwargs.get("tone")
        if (
            tone is not None
            and self._attr_available_tones
            and tone not in self._attr_available_tones
        ):
            raise ValueError(f"Invalid siren tone: {tone}")
        for key in ("tone", "volume_level", "duration"):
            if key in kwargs:
                self._virtual_attributes[key] = kwargs[key]
        self._attr_is_on = True
        self._update_attributes()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self._attr_is_on = False
        self._update_attributes()
        self.async_write_ha_state()


class VirtualLawnMower(_NativeGenericMixin, VirtualEntity, LawnMowerEntity):
    """Virtual lawn mower with the complete native command surface."""

    PLATFORM_DOMAIN = "lawn_mower"

    def __init__(self, config, old_style: bool):
        super().__init__(config, old_style)
        self._attr_supported_features = (
            LawnMowerEntityFeature.START_MOWING
            | LawnMowerEntityFeature.PAUSE
            | LawnMowerEntityFeature.DOCK
        )

    @staticmethod
    def _parse_activity(value) -> LawnMowerActivity | None:
        if isinstance(value, LawnMowerActivity):
            return value
        if not _has_value(value):
            return None
        try:
            return LawnMowerActivity(str(value).lower())
        except ValueError:
            return None

    def _create_state(self, config):
        super()._create_state(config)
        self._attr_activity = self._parse_activity(config.get(CONF_INITIAL_VALUE))

    def _restore_state(self, state, config):
        super()._restore_state(state, config)
        self._attr_activity = self._parse_activity(state.state)

    def set_state(self, value) -> None:
        activity = self._parse_activity(value)
        if activity is None and _has_value(value):
            raise ValueError(f"Invalid lawn mower activity: {value}")
        self._attr_activity = activity

    async def async_start_mowing(self) -> None:
        self._attr_activity = LawnMowerActivity.MOWING
        self.async_write_ha_state()

    async def async_pause(self) -> None:
        self._attr_activity = LawnMowerActivity.PAUSED
        self.async_write_ha_state()

    async def async_dock(self) -> None:
        self._attr_activity = LawnMowerActivity.RETURNING
        self.async_write_ha_state()


class VirtualRemote(_NativeGenericMixin, VirtualEntity, RemoteEntity):
    """Virtual remote supporting power and command dispatch."""

    PLATFORM_DOMAIN = "remote"

    def __init__(self, config, old_style: bool):
        super().__init__(config, old_style)
        self._attr_activity_list = _string_list(config.get("activity_list"))
        self._attr_current_activity = config.get("current_activity")

    def _create_state(self, config):
        super()._create_state(config)
        self.set_state(config.get(CONF_INITIAL_VALUE))

    def _restore_state(self, state, config):
        super()._restore_state(state, config)
        self.set_state(state.state)

    def set_state(self, value) -> None:
        self._attr_is_on = str(value).lower() in {"1", "on", "true", "yes"}

    async def async_turn_on(self, **kwargs) -> None:
        activity = kwargs.get("activity")
        if activity is not None:
            if self._attr_activity_list and activity not in self._attr_activity_list:
                raise ValueError(f"Invalid remote activity: {activity}")
            self._attr_current_activity = activity
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self._attr_is_on = False
        self.async_write_ha_state()

    async def async_send_command(self, command, **kwargs) -> None:
        self._virtual_attributes["last_command"] = list(command)
        if kwargs:
            self._virtual_attributes["last_command_options"] = dict(kwargs)
        self._update_attributes()
        self.async_write_ha_state()


class VirtualMediaPlayer(_NativeGenericMixin, VirtualEntity, MediaPlayerEntity):
    """Virtual media player with common playback and volume services."""

    PLATFORM_DOMAIN = "media_player"

    def __init__(self, config, old_style: bool):
        super().__init__(config, old_style)
        self._attr_source_list = _string_list(config.get("source_list"))
        self._attr_source = config.get("source")
        self._attr_volume_level = self._bounded_volume(config.get("volume_level", 0.5))
        self._attr_is_volume_muted = _safe_bool(config.get("is_volume_muted", False))
        self._attr_supported_features = (
            MediaPlayerEntityFeature.TURN_ON
            | MediaPlayerEntityFeature.TURN_OFF
            | MediaPlayerEntityFeature.PLAY
            | MediaPlayerEntityFeature.PAUSE
            | MediaPlayerEntityFeature.STOP
            | MediaPlayerEntityFeature.VOLUME_SET
            | MediaPlayerEntityFeature.VOLUME_MUTE
        )
        if self._attr_source_list:
            self._attr_supported_features |= MediaPlayerEntityFeature.SELECT_SOURCE

    @staticmethod
    def _bounded_volume(volume) -> float:
        return max(0.0, min(1.0, _safe_float(volume, 0.5)))

    @staticmethod
    def _parse_media_state(value) -> MediaPlayerState | None:
        if isinstance(value, MediaPlayerState):
            return value
        if not _has_value(value):
            return None
        try:
            return MediaPlayerState(str(value).lower())
        except ValueError:
            return None

    def _create_state(self, config):
        super()._create_state(config)
        self._attr_state = self._parse_media_state(config.get(CONF_INITIAL_VALUE))

    def _restore_state(self, state, config):
        super()._restore_state(state, config)
        self._attr_state = self._parse_media_state(state.state)
        self._attr_volume_level = self._bounded_volume(
            state.attributes.get("volume_level", self._attr_volume_level)
        )
        self._attr_source = state.attributes.get("source", self._attr_source)

    def set_state(self, value) -> None:
        state = self._parse_media_state(value)
        if state is None and _has_value(value):
            raise ValueError(f"Invalid media player state: {value}")
        self._attr_state = state

    async def async_turn_on(self) -> None:
        self._attr_state = MediaPlayerState.ON
        self.async_write_ha_state()

    async def async_turn_off(self) -> None:
        self._attr_state = MediaPlayerState.OFF
        self.async_write_ha_state()

    async def async_media_play(self) -> None:
        self._attr_state = MediaPlayerState.PLAYING
        self.async_write_ha_state()

    async def async_media_pause(self) -> None:
        self._attr_state = MediaPlayerState.PAUSED
        self.async_write_ha_state()

    async def async_media_stop(self) -> None:
        self._attr_state = MediaPlayerState.IDLE
        self.async_write_ha_state()

    async def async_set_volume_level(self, volume: float) -> None:
        if not 0 <= volume <= 1:
            raise ValueError("Media player volume must be between 0 and 1")
        self._attr_volume_level = float(volume)
        self.async_write_ha_state()

    async def async_mute_volume(self, mute: bool) -> None:
        self._attr_is_volume_muted = mute
        self.async_write_ha_state()

    async def async_select_source(self, source: str) -> None:
        if source not in self._attr_source_list:
            raise ValueError(f"Invalid media source: {source}")
        self._attr_source = source
        self.async_write_ha_state()


class VirtualWaterHeater(_NativeGenericMixin, VirtualEntity, WaterHeaterEntity):
    """Virtual water heater with target temperature and operation modes."""

    PLATFORM_DOMAIN = "water_heater"

    def __init__(self, config, old_style: bool):
        super().__init__(config, old_style)
        self._attr_min_temp = _safe_float(config.get("min_temp", 35), 35)
        self._attr_max_temp = _safe_float(config.get("max_temp", 85), 85)
        if self._attr_min_temp > self._attr_max_temp:
            self._attr_min_temp, self._attr_max_temp = (
                self._attr_max_temp,
                self._attr_min_temp,
            )
        self._attr_temperature_unit = config.get("temperature_unit", "°C")
        self._attr_target_temperature_step = _safe_float(
            config.get("target_temperature_step", 1),
            1,
        )
        if self._attr_target_temperature_step <= 0:
            self._attr_target_temperature_step = 1
        self._attr_current_temperature = self._bounded_temperature(
            config.get("current_temperature")
        )
        self._attr_target_temperature = self._bounded_temperature(
            config.get("target_temperature")
        )
        self._attr_operation_list = _string_list(
            config.get("operation_list"),
            (STATE_OFF, "heat"),
        )
        if STATE_OFF not in self._attr_operation_list:
            self._attr_operation_list.insert(0, STATE_OFF)
        self._attr_supported_features = (
            WaterHeaterEntityFeature.TARGET_TEMPERATURE
            | WaterHeaterEntityFeature.OPERATION_MODE
            | WaterHeaterEntityFeature.ON_OFF
        )

    def _bounded_temperature(self, value):
        if value is None:
            return None
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(parsed):
            return None
        return max(self._attr_min_temp, min(self._attr_max_temp, parsed))

    def _create_state(self, config):
        super()._create_state(config)
        self.set_state(config.get(CONF_INITIAL_VALUE))

    def _restore_state(self, state, config):
        super()._restore_state(state, config)
        self.set_state(state.state)

    def set_state(self, value) -> None:
        operation = str(value).lower()
        if operation not in self._attr_operation_list:
            if not _has_value(value):
                operation = STATE_OFF
            else:
                self._attr_operation_list.append(operation)
        self._attr_current_operation = operation

    async def async_set_temperature(self, **kwargs) -> None:
        temperature = float(kwargs[ATTR_TEMPERATURE])
        if not self._attr_min_temp <= temperature <= self._attr_max_temp:
            raise ValueError("Water heater temperature is outside its configured range")
        self._attr_target_temperature = temperature
        self.async_write_ha_state()

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        if operation_mode not in self._attr_operation_list:
            raise ValueError(f"Invalid water heater operation mode: {operation_mode}")
        self._attr_current_operation = operation_mode
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs) -> None:
        self._attr_current_operation = next(
            (mode for mode in self._attr_operation_list if mode != STATE_OFF),
            STATE_OFF,
        )
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self._attr_current_operation = STATE_OFF
        self.async_write_ha_state()


class VirtualUpdate(_NativeGenericMixin, VirtualEntity, UpdateEntity):
    """Virtual software update entity."""

    PLATFORM_DOMAIN = "update"

    def __init__(self, config, old_style: bool):
        super().__init__(config, old_style)
        initial = config.get(CONF_INITIAL_VALUE)
        self._attr_installed_version = str(config.get("installed_version", initial))
        self._attr_latest_version = str(
            config.get("latest_version", self._attr_installed_version)
        )
        self._attr_release_summary = config.get("release_summary")
        self._attr_release_url = config.get("release_url")
        self._release_notes = config.get("release_notes")
        self._attr_supported_features = UpdateEntityFeature.INSTALL
        if config.get("versions"):
            self._attr_supported_features |= UpdateEntityFeature.SPECIFIC_VERSION
        if _safe_bool(config.get("support_backup", True), True):
            self._attr_supported_features |= UpdateEntityFeature.BACKUP
        if self._release_notes is not None:
            self._attr_supported_features |= UpdateEntityFeature.RELEASE_NOTES

    def _create_state(self, config):
        super()._create_state(config)

    def _restore_state(self, state, config):
        super()._restore_state(state, config)
        self._attr_installed_version = state.attributes.get(
            "installed_version", self._attr_installed_version
        )
        self._attr_latest_version = state.attributes.get(
            "latest_version", self._attr_latest_version
        )

    def set_state(self, value) -> None:
        self._attr_latest_version = str(value)

    async def async_install(self, version, backup: bool, **kwargs) -> None:
        target_version = version or self._attr_latest_version
        configured_versions = _string_list(self._config.get("versions"))
        if configured_versions and str(target_version) not in configured_versions:
            raise ValueError(f"Invalid update version: {target_version}")
        self._attr_installed_version = str(target_version)
        self._virtual_attributes["last_install_backup"] = backup
        self._update_attributes()
        self.async_write_ha_state()

    async def async_release_notes(self) -> str | None:
        return self._release_notes


async def async_setup_generic_platform(hass, config, async_add_entities, domain):
    """Ignore platform setup; Virtual Layer entities are config-entry only."""
    _LOGGER.debug("ignoring platform setup for generic %s", domain)


async def async_setup_generic_entry(
    hass,
    entry,
    async_add_entities,
    domain,
    schema,
    entity_class=GenericVirtualEntity,
):
    _LOGGER.debug(f"setting up generic entries for {domain}...")
    entities = [
        entity_class(schema(entity), False)
        if entity_class is not GenericVirtualEntity
        else GenericVirtualEntity(schema(entity), domain, False)
        for entity in get_entity_configs(hass, entry.data[ATTR_GROUP_NAME], domain)
    ]
    async_add_entities(entities)
