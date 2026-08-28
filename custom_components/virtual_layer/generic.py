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
    RepeatMode,
)
from homeassistant.components.remote import RemoteEntity, RemoteEntityFeature
from homeassistant.components.select import SelectEntity
from homeassistant.components.siren import SirenEntity, SirenEntityFeature
from homeassistant.components.text import TextEntity, TextMode
from homeassistant.components.time import TimeEntity
from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
from homeassistant.components.water_heater import (
    ATTR_AWAY_MODE,
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
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
from .entity import VirtualEntity, nearest_step_value, virtual_schema

_LOGGER = logging.getLogger(__name__)

CONF_STATE_CLASS = "state_class"

DEFAULT_GENERIC_VALUE = "unknown"

GENERIC_LIST_TEMPLATE_PROPERTIES = frozenset({
    "event_types",
    "group_members",
    "supported_bit_rates",
    "supported_channels",
    "supported_codecs",
    "supported_formats",
    "supported_languages",
    "supported_options",
    "supported_sample_rates",
})
GENERIC_MAPPING_TEMPLATE_PROPERTIES = frozenset({
    "default_options",
    "event",
    "event_attributes",
    "tts_options",
})
GENERIC_BOOLEAN_TEMPLATE_PROPERTIES = frozenset({
    "code_arm_required",
    "reports_position",
    "supports_streaming",
})
GENERIC_FINITE_TEMPLATE_PROPERTIES = frozenset({
    "native_apparent_temperature",
    "native_dew_point",
    "native_temperature",
})
GENERIC_NONNEGATIVE_TEMPLATE_PROPERTIES = frozenset({
    "air_quality_index",
    "carbon_dioxide",
    "carbon_monoxide",
    "cloud_coverage",
    "confidence",
    "humidity",
    "native_pressure",
    "native_visibility",
    "native_wind_gust_speed",
    "native_wind_speed",
    "nitrogen_dioxide",
    "nitrogen_monoxide",
    "nitrogen_oxide",
    "ozone",
    "particulate_matter_0_1",
    "particulate_matter_10",
    "particulate_matter_2_5",
    "sulphur_dioxide",
    "uv_index",
})
WEATHER_STATE_ATTRIBUTE_ALIASES = {
    "native_apparent_temperature": "apparent_temperature",
    "native_dew_point": "dew_point",
    "native_precipitation_unit": "precipitation_unit",
    "native_pressure": "pressure",
    "native_pressure_unit": "pressure_unit",
    "native_temperature": "temperature",
    "native_temperature_unit": "temperature_unit",
    "native_visibility": "visibility",
    "native_visibility_unit": "visibility_unit",
    "native_wind_gust_speed": "wind_gust_speed",
    "native_wind_speed": "wind_speed",
    "native_wind_speed_unit": "wind_speed_unit",
}

GENERIC_SCHEMA = virtual_schema(
    DEFAULT_GENERIC_VALUE,
    {
        vol.Optional(CONF_CLASS): cv.string,
        vol.Optional(CONF_ICON): cv.string,
        vol.Optional(CONF_STATE_CLASS): cv.string,
    },
)
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
        _LOGGER.debug(f"GenericVirtualEntity: {self.name} ({domain}) created")

    @property
    def state(self):
        return self._attr_state

    def _create_state(self, config):
        super()._create_state(config)
        self._attr_state = config.get(CONF_INITIAL_VALUE)

    def _restore_state(self, state, config):
        super()._restore_state(state, config)
        self._attr_state = self._restored_state_value(state, config)

    def _update_attributes(self):
        super()._update_attributes()
        self._attr_extra_state_attributes.update(
            {
                name: value
                for name, value in (
                    (ATTR_DEVICE_CLASS, self._attr_device_class),
                    (CONF_STATE_CLASS, self._attr_state_class),
                )
                if value is not None
            }
        )
        domain_options = dict(self._domain_options)
        if self._domain == "weather":
            domain_options = {
                WEATHER_STATE_ATTRIBUTE_ALIASES.get(name, name): value
                for name, value in domain_options.items()
                if name != "condition"
            }
        elif self._domain == "calendar":
            domain_options.pop("initial_color", None)
            if "event" in domain_options:
                event = domain_options.pop("event")
                if event is not None:
                    domain_options.update(_calendar_event_attributes(event))
                    self._attr_state = _calendar_event_state(event)
                else:
                    self._attr_state = STATE_OFF
        elif self._domain == "event":
            event_attributes = domain_options.pop("event_attributes", {})
            if isinstance(event_attributes, dict):
                domain_options.update({
                    name: value
                    for name, value in event_attributes.items()
                    if name not in EXCLUDED_VIRTUAL_ATTRIBUTE_NAMES
                    and name != "event_type"
                })
        self._attr_extra_state_attributes.update(domain_options)

    def set_state(self, value) -> None:
        self._attr_state = value

    def _apply_native_template_value(self, name: str, value) -> bool:
        if name in GENERIC_LIST_TEMPLATE_PROPERTIES:
            value = _template_string_list(value, name)
        elif name in GENERIC_MAPPING_TEMPLATE_PROPERTIES:
            if name == "event" and value is None:
                pass
            elif not isinstance(value, dict):
                raise ValueError(f"{name} must render an object")
            else:
                value = dict(value)
        elif name == "todo_items":
            if not isinstance(value, list) or not all(
                isinstance(item, dict) for item in value
            ):
                raise ValueError("todo_items must render a list of objects")
        elif name in GENERIC_BOOLEAN_TEMPLATE_PROPERTIES:
            value = value if isinstance(value, bool) else self._template_to_bool(value)
        elif name == "supported_features":
            value = _safe_int(value, -1, -1)
            if value < 0:
                raise ValueError("supported_features must be a non-negative integer")
        elif name in {"latitude", "longitude"}:
            value = _safe_float(value, float("nan"))
            limit = 90 if name == "latitude" else 180
            if not math.isfinite(value) or not -limit <= value <= limit:
                raise ValueError(f"{name} is outside its valid range")
        elif name in GENERIC_FINITE_TEMPLATE_PROPERTIES:
            value = _safe_float(value, float("nan"))
            if not math.isfinite(value):
                raise ValueError(f"{name} must be a finite number")
        elif name in GENERIC_NONNEGATIVE_TEMPLATE_PROPERTIES:
            value = _safe_float(value, float("nan"))
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be a non-negative number")
            if name in {"cloud_coverage", "confidence", "humidity"} and value > 100:
                raise ValueError(f"{name} must be between 0 and 100")
        elif name == "wind_bearing":
            if isinstance(value, bool):
                raise ValueError("wind_bearing must be a number or compass direction")
            try:
                numeric_bearing = float(value)
            except (TypeError, ValueError, OverflowError):
                value = str(value).strip()
                if not value:
                    raise ValueError("wind_bearing must not be empty")
            else:
                if not math.isfinite(numeric_bearing) or not 0 <= numeric_bearing <= 360:
                    raise ValueError("wind_bearing must be between 0 and 360")
                value = numeric_bearing
        elif self._domain == "event" and name == "event_type":
            value = str(value).strip()
            if not value:
                raise ValueError("event_type must not be empty")
            event_types = getattr(self, "_attr_event_types", [])
            if event_types and value not in event_types:
                raise ValueError("event_type must be present in event_types")
        event_type_changed = (
            self._domain == "event"
            and name == "event_type"
            and self._domain_options.get("event_type") != value
        )
        if self._domain == "weather" and name == "condition":
            value = None if value is None else str(value).strip()
            changed = self._attr_state != value
            self._attr_state = value
        else:
            changed = super()._apply_native_template_value(name, value)
        if (
            self._domain == "air_quality"
            and name == "particulate_matter_2_5"
            and self._attr_state != value
        ):
            self._attr_state = value
            changed = True
        if name not in {
            "available",
            "condition",
            "device_class",
            "icon",
            "state",
            "supported_features",
        }:
            rendered = getattr(self, f"_attr_{name}", value)
            if (
                name not in self._domain_options
                or self._domain_options[name] != rendered
            ):
                self._domain_options[name] = rendered
                changed = True
        if event_type_changed:
            self._attr_state = dt_util.utcnow().isoformat(timespec="milliseconds")
            changed = True
        return changed

    def _native_template_priority(self, name: str) -> int:
        if name in GENERIC_LIST_TEMPLATE_PROPERTIES:
            return 0
        return super()._native_template_priority(name)


def _calendar_event_value(event: dict, *names: str):
    for name in names:
        if name in event:
            return event[name]
    return None


def _calendar_event_datetime(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=dt_util.DEFAULT_TIME_ZONE)
    if not isinstance(value, str) or not value.strip():
        return None
    if parsed := dt_util.parse_datetime(value):
        return parsed
    if parsed_date := dt_util.parse_date(value):
        return datetime.combine(
            parsed_date,
            time.min,
            tzinfo=dt_util.DEFAULT_TIME_ZONE,
        )
    return None


def _calendar_event_attributes(event: dict) -> dict:
    start = _calendar_event_value(event, "start", "start_time")
    end = _calendar_event_value(event, "end", "end_time")
    start_datetime = _calendar_event_datetime(start)
    end_datetime = _calendar_event_datetime(end)
    all_day = bool(event.get("all_day")) or (
        isinstance(start, date)
        and not isinstance(start, datetime)
        or isinstance(start, str)
        and dt_util.parse_date(start) is not None
        and "T" not in start
        and " " not in start
    )

    def _display(value, parsed):
        if parsed is not None:
            return parsed.strftime("%Y-%m-%d %H:%M:%S")
        return "" if value is None else str(value)

    return {
        "message": str(
            _calendar_event_value(event, "summary", "message", "title") or ""
        ),
        "all_day": all_day,
        "start_time": _display(start, start_datetime),
        "end_time": _display(end, end_datetime),
        "location": str(event.get("location") or ""),
        "description": str(event.get("description") or ""),
    }


def _calendar_event_state(event: dict) -> str:
    start = _calendar_event_datetime(
        _calendar_event_value(event, "start", "start_time")
    )
    end = _calendar_event_datetime(_calendar_event_value(event, "end", "end_time"))
    if start is None or end is None:
        return STATE_OFF
    if start.tzinfo is None:
        start = start.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
    if end.tzinfo is None:
        end = end.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
    now = dt_util.now()
    return "on" if start <= now < end else STATE_OFF


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
    return [str(item).strip() for item in value if str(item).strip()]


def _template_string_list(value, name: str) -> list[str]:
    """Validate and normalize a list rendered by a native template."""
    if not isinstance(value, (list, tuple, set)):
        raise TypeError(f"{name} must render a list")
    result = [str(item).strip() for item in value if str(item).strip()]
    if len(set(result)) != len(result):
        raise ValueError(f"{name} contains duplicate values")
    return result


def _safe_float(value, default: float) -> float:
    """Read a finite numeric option without making old settings unloadable."""
    if isinstance(value, bool):
        return default
    try:
        parsed = float(value)
    except (OverflowError, TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _safe_int(value, default: int, minimum: int = 0) -> int:
    """Read an integer option with a lower bound from persisted data."""
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (OverflowError, TypeError, ValueError):
        return default
    return max(minimum, parsed)


def _safe_bool(value, default: bool = False) -> bool:
    """Parse a stored boolean without trusting Python's truthiness rules."""
    try:
        return cv.boolean(value)
    except vol.Invalid:
        return default


def _supported_feature_mask(value, feature_type, field_name="supported_features"):
    """Return a validated Home Assistant feature bitmask."""
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a non-negative integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError) as err:
        raise ValueError(f"{field_name} must be a non-negative integer") from err
    if parsed < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return feature_type(parsed)


class _NativeGenericMixin:
    """Common config and attributes for native building-block entities."""

    PLATFORM_DOMAIN: str
    NATIVE_OPTION_KEYS = frozenset()

    def __init__(self, config, old_style: bool):
        super().__init__(config, self.PLATFORM_DOMAIN, old_style)
        self._attr_device_class = config.get(CONF_CLASS)
        self._attr_icon = config.get(CONF_ICON)
        self._attr_state_class = config.get(CONF_STATE_CLASS)
        self._domain_options = {
            key: value
            for key, value in generic_entity_options(config).items()
            if key not in self.NATIVE_OPTION_KEYS
        }

    def _update_attributes(self):
        super()._update_attributes()
        self._attr_extra_state_attributes.update(self._domain_options)


class VirtualSelect(_NativeGenericMixin, VirtualEntity, SelectEntity):
    """Virtual select with Home Assistant's native option services."""

    PLATFORM_DOMAIN = "select"
    NATIVE_OPTION_KEYS = frozenset({"options"})

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
        restored = str(self._restored_state_value(state, config))
        initial = str(config.get(CONF_INITIAL_VALUE, ""))
        self._attr_current_option = (
            restored
            if restored in self._attr_options
            else initial
            if initial in self._attr_options
            else None
        )

    def set_state(self, value) -> None:
        if not _has_value(value):
            self._attr_current_option = None
            return
        option = str(value)
        if option not in self._attr_options:
            raise ValueError(f"Invalid select option: {option}")
        self._attr_current_option = option

    async def async_select_option(self, option: str) -> None:
        if option not in self._attr_options:
            raise ValueError(f"Invalid select option: {option}")
        self._attr_current_option = option
        self.async_write_ha_state()

    def _apply_native_template_value(self, name: str, value) -> bool:
        if name == "options":
            value = _template_string_list(value, name)
        elif name in {"state", "value", "current_option"}:
            name = "current_option"
            if not _has_value(value):
                value = None
            else:
                value = str(value)
                if value not in self._attr_options:
                    raise ValueError(f"Invalid select option: {value}")
        return super()._apply_native_template_value(name, value)

    def _native_templates_applied(self) -> None:
        if self._attr_current_option not in self._attr_options:
            self._attr_current_option = None


class VirtualText(_NativeGenericMixin, VirtualEntity, TextEntity):
    """Virtual text with native length and pattern capabilities."""

    PLATFORM_DOMAIN = "text"
    NATIVE_OPTION_KEYS = frozenset({"max", "min", "mode", "pattern"})

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
        except (TypeError, ValueError, OverflowError):
            self._attr_mode = TextMode.TEXT
        pattern = config.get("pattern")
        try:
            self._pattern_regex = (
                re.compile(pattern) if isinstance(pattern, str) else None
            )
        except re.error:
            self._pattern_regex = None
        self._attr_pattern = pattern if self._pattern_regex is not None else None

    def _create_state(self, config):
        super()._create_state(config)
        value = str(config.get(CONF_INITIAL_VALUE, ""))
        self._attr_native_value = value if self._value_is_valid(value) else None

    def _restore_state(self, state, config):
        super()._restore_state(state, config)
        restored = str(self._restored_state_value(state, config))
        fallback = str(config.get(CONF_INITIAL_VALUE, ""))
        self._attr_native_value = (
            restored
            if self._value_is_valid(restored)
            else fallback
            if self._value_is_valid(fallback)
            else None
        )

    def set_state(self, value) -> None:
        value = str(value)
        if not self._value_is_valid(value):
            raise ValueError("Text value does not satisfy its configured constraints")
        self._attr_native_value = value

    def _value_is_valid(self, value: str) -> bool:
        return self._attr_native_min <= len(value) <= self._attr_native_max and (
            self._pattern_regex is None
            or self._pattern_regex.fullmatch(value) is not None
        )

    async def async_set_value(self, value: str) -> None:
        if not isinstance(value, str):
            raise TypeError("Text value must be a string")
        if not self._attr_native_min <= len(value) <= self._attr_native_max:
            raise ValueError("Text value is outside the configured length range")
        if (
            self._pattern_regex is not None
            and self._pattern_regex.fullmatch(value) is None
        ):
            raise ValueError("Text value does not match the configured pattern")
        self._attr_native_value = value
        self.async_write_ha_state()

    def _apply_native_template_value(self, name: str, value) -> bool:
        aliases = {
            "min": "native_min",
            "max": "native_max",
            "value": "native_value",
            "state": "native_value",
        }
        name = aliases.get(name, name)
        if name in {"native_min", "native_max"}:
            value = _safe_int(value, -1, -1)
            if value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        elif name == "mode":
            try:
                value = TextMode(value)
            except (TypeError, ValueError) as err:
                raise ValueError("mode must be text or password") from err
        elif name == "pattern":
            if value in {None, ""}:
                value = None
                regex = None
            elif not isinstance(value, str):
                raise ValueError("pattern must render a string")
            else:
                try:
                    regex = re.compile(value)
                except re.error as err:
                    raise ValueError("pattern must be a valid regular expression") from err
            changed = self._attr_pattern != value
            self._attr_pattern = value
            self._pattern_regex = regex
            return changed
        elif name == "native_value":
            value = str(value)
        return super()._apply_native_template_value(name, value)

    def _native_templates_applied(self) -> None:
        if self._attr_native_min > self._attr_native_max:
            self._attr_native_min, self._attr_native_max = (
                self._attr_native_max,
                self._attr_native_min,
            )
        value = self._attr_native_value
        if value is None:
            return
        if not self._value_is_valid(value):
            self._attr_native_value = None


class _TemporalEntityMixin(_NativeGenericMixin):
    """Shared state lifecycle for date, time, and datetime entities."""

    def _create_state(self, config):
        super()._create_state(config)
        self._attr_native_value = self._parse_value(config.get(CONF_INITIAL_VALUE))

    def _restore_state(self, state, config):
        super()._restore_state(state, config)
        self._attr_native_value = self._parse_value(state.state)
        if self._attr_native_value is None:
            self._attr_native_value = self._parse_value(
                config.get(CONF_INITIAL_VALUE)
            )

    def set_state(self, value) -> None:
        parsed = self._parse_value(value)
        if parsed is None and _has_value(value):
            raise ValueError(f"Invalid {self.PLATFORM_DOMAIN} value: {value}")
        self._attr_native_value = parsed

    async def async_set_value(self, value) -> None:
        parsed = self._parse_value(value)
        if parsed is None:
            raise ValueError(f"Invalid {self.PLATFORM_DOMAIN} value: {value}")
        self._attr_native_value = parsed
        self.async_write_ha_state()

    def _apply_native_template_value(self, name: str, value) -> bool:
        if name in {"state", "value", "native_value"}:
            parsed = self._parse_value(value)
            if parsed is None and _has_value(value):
                raise ValueError(f"Invalid {self.PLATFORM_DOMAIN} value: {value}")
            name = "native_value"
            value = parsed
        return super()._apply_native_template_value(name, value)


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
        self._virtual_attributes["press_count"] = (
            _safe_int(
                self._virtual_attributes.get("press_count", 0),
                0,
            )
            + 1
        )
        self._update_attributes()
        self.async_write_ha_state()


class VirtualSiren(_NativeGenericMixin, VirtualEntity, SirenEntity):
    """Virtual siren with tone, volume, and duration capabilities."""

    PLATFORM_DOMAIN = "siren"
    NATIVE_OPTION_KEYS = frozenset(
        {
            "available_tones",
            "support_duration",
            "support_volume",
        }
    )

    def __init__(self, config, old_style: bool):
        super().__init__(config, old_style)
        self._configured_supported_features = None
        self._attr_available_tones = _string_list(config.get("available_tones"))
        self._support_volume = _safe_bool(config.get("support_volume", True), True)
        self._support_duration = _safe_bool(config.get("support_duration", True), True)
        self._refresh_supported_features()

    def _refresh_supported_features(self) -> None:
        if self._configured_supported_features is not None:
            self._attr_supported_features = self._configured_supported_features
            return
        features = SirenEntityFeature.TURN_ON | SirenEntityFeature.TURN_OFF
        if self._attr_available_tones:
            features |= SirenEntityFeature.TONES
        if self._support_volume:
            features |= SirenEntityFeature.VOLUME_SET
        if self._support_duration:
            features |= SirenEntityFeature.DURATION
        self._attr_supported_features = features

    def _create_state(self, config):
        super()._create_state(config)
        self.set_state(config.get(CONF_INITIAL_VALUE))

    def _restore_state(self, state, config):
        super()._restore_state(state, config)
        self.set_state(self._restored_state_value(state, config))

    def set_state(self, value) -> None:
        self._attr_is_on = self._template_to_bool(value)

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

    def _apply_native_template_value(self, name: str, value) -> bool:
        if name == "supported_features":
            value = _supported_feature_mask(value, SirenEntityFeature)
            changed = self._configured_supported_features != value
            self._configured_supported_features = value
            return changed
        if name == "available_tones":
            value = _template_string_list(value, name)
        elif name in {"support_volume", "support_duration"}:
            value = value if isinstance(value, bool) else self._template_to_bool(value)
            attribute = f"_{name}"
            changed = getattr(self, attribute) != value
            setattr(self, attribute, value)
            return changed
        elif name in {"state", "is_on"}:
            old_state = self._attr_is_on
            self.set_state(value)
            return old_state != self._attr_is_on
        return super()._apply_native_template_value(name, value)

    def _native_templates_applied(self) -> None:
        tone = self._virtual_attributes.get("tone")
        if self._attr_available_tones and tone not in self._attr_available_tones:
            self._virtual_attributes.pop("tone", None)
        self._refresh_supported_features()


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
        self._attr_activity = self._parse_activity(state.state) or self._parse_activity(
            config.get(CONF_INITIAL_VALUE)
        )

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

    def _apply_native_template_value(self, name: str, value) -> bool:
        if name in {"state", "activity"}:
            activity = self._parse_activity(value)
            if activity is None and _has_value(value):
                raise ValueError(f"Invalid lawn mower activity: {value}")
            name = "activity"
            value = activity
        return super()._apply_native_template_value(name, value)


class VirtualRemote(_NativeGenericMixin, VirtualEntity, RemoteEntity):
    """Virtual remote supporting power and command dispatch."""

    PLATFORM_DOMAIN = "remote"
    NATIVE_OPTION_KEYS = frozenset({"activity_list", "current_activity"})

    def __init__(self, config, old_style: bool):
        super().__init__(config, old_style)
        self._attr_activity_list = _string_list(config.get("activity_list"))
        self._attr_current_activity = config.get("current_activity")
        if (
            _has_value(self._attr_current_activity)
            and self._attr_current_activity not in self._attr_activity_list
        ):
            self._attr_activity_list.append(str(self._attr_current_activity))
        self._attr_supported_features = RemoteEntityFeature(0)
        if self._attr_activity_list:
            self._attr_supported_features |= RemoteEntityFeature.ACTIVITY

    def _create_state(self, config):
        super()._create_state(config)
        self.set_state(config.get(CONF_INITIAL_VALUE))

    def _restore_state(self, state, config):
        super()._restore_state(state, config)
        self.set_state(self._restored_state_value(state, config))
        restored_activity = state.attributes.get("current_activity")
        if (
            _has_value(restored_activity)
            and restored_activity in self._attr_activity_list
        ):
            self._attr_current_activity = restored_activity

    def set_state(self, value) -> None:
        self._attr_is_on = self._template_to_bool(value)

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

    def _apply_native_template_value(self, name: str, value) -> bool:
        if name == "activity_list":
            value = _template_string_list(value, name)
        elif name == "current_activity":
            value = None if not _has_value(value) else str(value)
            if value is not None and self._attr_activity_list and value not in self._attr_activity_list:
                raise ValueError(f"Invalid remote activity: {value}")
        elif name in {"state", "is_on"}:
            old_state = self._attr_is_on
            self.set_state(value)
            return old_state != self._attr_is_on
        return super()._apply_native_template_value(name, value)

    def _native_templates_applied(self) -> None:
        if self._attr_current_activity not in self._attr_activity_list:
            self._attr_current_activity = None
        self._attr_supported_features = RemoteEntityFeature(0)
        if self._attr_activity_list or "turn_on" in self._command_actions:
            self._attr_supported_features |= RemoteEntityFeature.ACTIVITY


class VirtualMediaPlayer(_NativeGenericMixin, VirtualEntity, MediaPlayerEntity):
    """Virtual media player with common playback and volume services."""

    PLATFORM_DOMAIN = "media_player"
    NATIVE_OPTION_KEYS = frozenset(
        {
            "is_volume_muted",
            "source",
            "source_list",
            "volume_level",
        }
    )

    def __init__(self, config, old_style: bool):
        super().__init__(config, old_style)
        self._configured_supported_features = None
        self._attr_source_list = _string_list(config.get("source_list"))
        self._attr_source = config.get("source")
        self._attr_volume_level = self._bounded_volume(config.get("volume_level", 0.5))
        self._attr_is_volume_muted = _safe_bool(config.get("is_volume_muted", False))
        self._refresh_supported_features()

    def _refresh_supported_features(self) -> None:
        if self._configured_supported_features is not None:
            self._attr_supported_features = self._configured_supported_features
            return
        features = (
            MediaPlayerEntityFeature.TURN_ON
            | MediaPlayerEntityFeature.TURN_OFF
            | MediaPlayerEntityFeature.PLAY
            | MediaPlayerEntityFeature.PAUSE
            | MediaPlayerEntityFeature.STOP
            | MediaPlayerEntityFeature.VOLUME_SET
            | MediaPlayerEntityFeature.VOLUME_MUTE
        )
        if self._attr_source_list:
            features |= MediaPlayerEntityFeature.SELECT_SOURCE
        self._attr_supported_features = features

    @staticmethod
    def _bounded_volume(volume, default: float = 0.5) -> float:
        return max(0.0, min(1.0, _safe_float(volume, default)))

    @staticmethod
    def _parse_media_state(value) -> MediaPlayerState | None:
        if isinstance(value, MediaPlayerState):
            return value
        if isinstance(value, bool):
            # Older multi-source helpers classified media players whose
            # snapshot states were on/off as boolean sources.
            return MediaPlayerState.ON if value else MediaPlayerState.OFF
        if not _has_value(value):
            return None
        normalized = str(value).strip().lower()
        if normalized in {"false", "0", "no"}:
            return MediaPlayerState.OFF
        if normalized in {"true", "1", "yes"}:
            return MediaPlayerState.ON
        try:
            return MediaPlayerState(normalized)
        except ValueError:
            return None

    def _create_state(self, config):
        super()._create_state(config)
        self._attr_state = self._parse_media_state(config.get(CONF_INITIAL_VALUE))

    def _restore_state(self, state, config):
        super()._restore_state(state, config)
        self._attr_state = self._parse_media_state(
            state.state
        ) or self._parse_media_state(config.get(CONF_INITIAL_VALUE))
        self._attr_volume_level = self._bounded_volume(
            state.attributes.get("volume_level", self._attr_volume_level),
            self._attr_volume_level,
        )
        restored_source = state.attributes.get("source")
        if restored_source in self._attr_source_list:
            self._attr_source = restored_source
        self._attr_is_volume_muted = _safe_bool(
            state.attributes.get("is_volume_muted", self._attr_is_volume_muted),
            self._attr_is_volume_muted,
        )
        restored_sound_mode = state.attributes.get("sound_mode")
        if _has_value(restored_sound_mode):
            self._attr_sound_mode = str(restored_sound_mode).strip()
        if "shuffle" in state.attributes:
            self._attr_shuffle = _safe_bool(state.attributes["shuffle"])
        restored_repeat = state.attributes.get("repeat")
        if _has_value(restored_repeat):
            try:
                self._attr_repeat = RepeatMode(
                    str(restored_repeat).strip().lower(),
                )
            except ValueError:
                self._attr_repeat = None

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
        if isinstance(volume, bool) or not 0 <= volume <= 1:
            raise ValueError("Media player volume must be between 0 and 1")
        self._attr_volume_level = nearest_step_value(
            float(volume),
            0,
            1,
            self.volume_step,
        )
        self.async_write_ha_state()

    async def async_mute_volume(self, mute: bool) -> None:
        self._attr_is_volume_muted = mute
        self.async_write_ha_state()

    async def async_select_source(self, source: str) -> None:
        if source not in self._attr_source_list:
            raise ValueError(f"Invalid media source: {source}")
        self._attr_source = source
        self.async_write_ha_state()

    async def async_select_sound_mode(self, sound_mode: str) -> None:
        sound_modes = getattr(self, "_attr_sound_mode_list", None) or []
        if sound_mode not in sound_modes:
            raise ValueError(f"Invalid media sound mode: {sound_mode}")
        self._attr_sound_mode = sound_mode
        self.async_write_ha_state()

    async def async_set_shuffle(self, shuffle: bool) -> None:
        if not isinstance(shuffle, bool):
            raise TypeError("Media shuffle must be a boolean")
        self._attr_shuffle = shuffle
        self.async_write_ha_state()

    async def async_set_repeat(self, repeat: RepeatMode) -> None:
        try:
            repeat = RepeatMode(str(repeat).strip().lower())
        except ValueError as err:
            raise ValueError(f"Invalid media repeat mode: {repeat}") from err
        self._attr_repeat = repeat
        self.async_write_ha_state()

    def _apply_native_template_value(self, name: str, value) -> bool:
        if name == "supported_features":
            value = _supported_feature_mask(value, MediaPlayerEntityFeature)
            changed = self._configured_supported_features != value
            self._configured_supported_features = value
            return changed
        if name in {"source_list", "sound_mode_list", "group_members"}:
            value = _template_string_list(value, name)
        elif name == "source":
            value = None if not _has_value(value) else str(value).strip()
            if value is not None and self._attr_source_list and value not in self._attr_source_list:
                raise ValueError(f"Invalid media source: {value}")
        elif name == "sound_mode":
            value = None if not _has_value(value) else str(value).strip()
            sound_modes = getattr(self, "_attr_sound_mode_list", None) or []
            if value is not None and sound_modes and value not in sound_modes:
                raise ValueError(f"Invalid media sound mode: {value}")
        elif name == "repeat":
            if value is not None and value != "":
                try:
                    value = RepeatMode(str(value).strip().lower())
                except ValueError as err:
                    raise ValueError(f"Invalid media repeat mode: {value}") from err
            else:
                value = None
        elif name == "volume_level":
            parsed = _safe_float(value, float("nan"))
            if not math.isfinite(parsed) or not 0 <= parsed <= 1:
                raise ValueError("volume_level must be between 0 and 1")
            value = parsed
        elif name == "volume_step":
            parsed = _safe_float(value, float("nan"))
            if not math.isfinite(parsed) or not 0 < parsed <= 1:
                raise ValueError("volume_step must be greater than 0 and at most 1")
            value = parsed
        elif name in {"media_duration", "media_position"}:
            parsed = _safe_float(value, float("nan"))
            if not math.isfinite(parsed) or parsed < 0:
                raise ValueError(f"{name} must be a non-negative number")
            value = parsed
        elif name == "media_track":
            value = _safe_int(value, -1, -1)
            if value < 0:
                raise ValueError("media_track must be a non-negative integer")
        elif name == "media_position_updated_at":
            if value is None or value == "":
                return super()._apply_native_template_value(name, None)
            if isinstance(value, datetime):
                parsed = value
            else:
                parsed = dt_util.parse_datetime(str(value))
            if parsed is None:
                raise ValueError("media_position_updated_at must be a datetime")
            value = parsed if parsed.tzinfo else dt_util.as_local(parsed)
        elif name in {
            "is_volume_muted",
            "media_image_remotely_accessible",
            "shuffle",
        } and value is not None:
            value = value if isinstance(value, bool) else self._template_to_bool(value)
        elif name in {"state", "media_state"}:
            state = self._parse_media_state(value)
            if state is None and _has_value(value):
                raise ValueError(f"Invalid media player state: {value}")
            name = "state"
            value = state
        elif value is not None:
            value = str(value)
        return super()._apply_native_template_value(name, value)

    def _native_templates_applied(self) -> None:
        if self._attr_source_list and self._attr_source not in self._attr_source_list:
            self._attr_source = None
        sound_modes = getattr(self, "_attr_sound_mode_list", None) or []
        if sound_modes and getattr(self, "_attr_sound_mode", None) not in sound_modes:
            self._attr_sound_mode = None
        if self._configured_supported_features is not None:
            self._attr_supported_features = self._configured_supported_features
            return
        features = (
            MediaPlayerEntityFeature.TURN_ON
            | MediaPlayerEntityFeature.TURN_OFF
            | MediaPlayerEntityFeature.PLAY
            | MediaPlayerEntityFeature.PAUSE
            | MediaPlayerEntityFeature.STOP
            | MediaPlayerEntityFeature.VOLUME_SET
            | MediaPlayerEntityFeature.VOLUME_MUTE
        )
        if self._attr_source_list or "select_source" in self._command_actions:
            features |= MediaPlayerEntityFeature.SELECT_SOURCE
        if sound_modes or "select_sound_mode" in self._command_actions:
            features |= MediaPlayerEntityFeature.SELECT_SOUND_MODE
        if (
            getattr(self, "_attr_shuffle", None) is not None
            or "set_shuffle" in self._command_actions
        ):
            features |= MediaPlayerEntityFeature.SHUFFLE_SET
        if (
            getattr(self, "_attr_repeat", None) is not None
            or "set_repeat" in self._command_actions
        ):
            features |= MediaPlayerEntityFeature.REPEAT_SET
        self._attr_supported_features = features


class VirtualWaterHeater(_NativeGenericMixin, VirtualEntity, WaterHeaterEntity):
    """Virtual water heater with target temperature and operation modes."""

    PLATFORM_DOMAIN = "water_heater"
    NATIVE_OPTION_KEYS = frozenset(
        {
            "current_temperature",
            "max_temp",
            "min_temp",
            "operation_list",
            "target_temperature",
            "target_temperature_high",
            "target_temperature_low",
            "target_temperature_step",
            "temperature_unit",
            "is_away_mode_on",
            "precision",
        }
    )

    def __init__(self, config, old_style: bool):
        super().__init__(config, old_style)
        self._configured_supported_features = None
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
        self._attr_target_temperature_high = self._bounded_temperature(
            config.get("target_temperature_high")
        )
        self._attr_target_temperature_low = self._bounded_temperature(
            config.get("target_temperature_low")
        )
        self._attr_is_away_mode_on = (
            _safe_bool(config.get("is_away_mode_on"))
            if config.get("is_away_mode_on") is not None
            else None
        )
        precision = config.get("precision")
        self._attr_precision = (
            _safe_float(precision, 1.0) if precision is not None else None
        )
        self._attr_operation_list = _string_list(
            config.get("operation_list"),
            (STATE_OFF, "heat"),
        )
        if STATE_OFF not in self._attr_operation_list:
            self._attr_operation_list.insert(0, STATE_OFF)
        self._refresh_supported_features()

    def _refresh_supported_features(self) -> None:
        if self._configured_supported_features is not None:
            self._attr_supported_features = self._configured_supported_features
            return
        self._attr_supported_features = (
            WaterHeaterEntityFeature.TARGET_TEMPERATURE
            | WaterHeaterEntityFeature.OPERATION_MODE
            | WaterHeaterEntityFeature.ON_OFF
        )
        if (
            self._attr_is_away_mode_on is not None
            or "turn_away_mode_on" in self._command_actions
            or "turn_away_mode_off" in self._command_actions
        ):
            self._attr_supported_features |= WaterHeaterEntityFeature.AWAY_MODE

    def _bounded_temperature(self, value):
        if value is None or isinstance(value, bool):
            return None
        try:
            parsed = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if not math.isfinite(parsed):
            return None
        return max(self._attr_min_temp, min(self._attr_max_temp, parsed))

    def _create_state(self, config):
        super()._create_state(config)
        operation = str(config.get(CONF_INITIAL_VALUE, STATE_OFF)).lower()
        self._attr_current_operation = (
            operation if operation in self._attr_operation_list else STATE_OFF
        )

    def _restore_state(self, state, config):
        super()._restore_state(state, config)
        restored_operation = str(state.state).lower()
        configured_operation = str(config.get(CONF_INITIAL_VALUE, STATE_OFF)).lower()
        self._attr_current_operation = (
            restored_operation
            if restored_operation in self._attr_operation_list
            else configured_operation
            if configured_operation in self._attr_operation_list
            else STATE_OFF
        )
        self._attr_current_temperature = self._bounded_temperature(
            state.attributes.get(
                "current_temperature",
                self._attr_current_temperature,
            )
        )
        self._attr_target_temperature = self._bounded_temperature(
            state.attributes.get(
                "temperature",
                self._attr_target_temperature,
            )
        )
        for attribute_name, target_name in (
            (ATTR_TARGET_TEMP_HIGH, "_attr_target_temperature_high"),
            (ATTR_TARGET_TEMP_LOW, "_attr_target_temperature_low"),
        ):
            if (restored := state.attributes.get(attribute_name)) is not None:
                if (temperature := self._bounded_temperature(restored)) is not None:
                    setattr(self, target_name, temperature)
        if ATTR_AWAY_MODE in state.attributes:
            self._attr_is_away_mode_on = _safe_bool(
                state.attributes[ATTR_AWAY_MODE],
                self._attr_is_away_mode_on is True,
            )

    def set_state(self, value) -> None:
        operation = str(value).lower()
        if operation not in self._attr_operation_list:
            if not _has_value(value):
                operation = STATE_OFF
            else:
                raise ValueError(f"Invalid water heater operation mode: {operation}")
        self._attr_current_operation = operation

    async def async_set_temperature(self, **kwargs) -> None:
        requested_temperature = kwargs[ATTR_TEMPERATURE]
        if isinstance(requested_temperature, bool):
            raise ValueError("Water heater temperature must be a finite number")
        try:
            temperature = float(requested_temperature)
        except (TypeError, ValueError, OverflowError) as err:
            raise ValueError(
                "Water heater temperature must be a finite number"
            ) from err
        if not math.isfinite(temperature):
            raise ValueError("Water heater temperature must be a finite number")
        if not self._attr_min_temp <= temperature <= self._attr_max_temp:
            raise ValueError("Water heater temperature is outside its configured range")
        self._attr_target_temperature = nearest_step_value(
            temperature,
            self._attr_min_temp,
            self._attr_max_temp,
            self._attr_target_temperature_step,
        )
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

    async def async_turn_away_mode_on(self) -> None:
        self._attr_is_away_mode_on = True
        self.async_write_ha_state()

    async def async_turn_away_mode_off(self) -> None:
        self._attr_is_away_mode_on = False
        self.async_write_ha_state()

    def _apply_native_template_value(self, name: str, value) -> bool:
        aliases = {
            "temperature": "target_temperature",
            "operation_mode": "current_operation",
            "modes": "operation_list",
        }
        name = aliases.get(name, name)
        if name == "supported_features":
            value = _supported_feature_mask(value, WaterHeaterEntityFeature)
            changed = self._configured_supported_features != value
            self._configured_supported_features = value
            return changed
        if name == "operation_list":
            value = _template_string_list(value, name)
            if STATE_OFF not in value:
                value.insert(0, STATE_OFF)
        elif name == "current_operation":
            value = str(value).lower()
            if value not in self._attr_operation_list:
                raise ValueError(f"Invalid water heater operation mode: {value}")
        elif name in {"min_temp", "max_temp"}:
            value = _safe_float(value, float("nan"))
            if not math.isfinite(value):
                raise ValueError(f"{name} must be a finite number")
        elif name in {
            "current_temperature",
            "target_temperature",
            "target_temperature_high",
            "target_temperature_low",
        }:
            if value is None or value == "":
                value = None
            else:
                parsed = _safe_float(value, float("nan"))
                if not math.isfinite(parsed):
                    raise ValueError(f"{name} must be a finite number")
                value = self._bounded_temperature(parsed)
        elif name == "target_temperature_step":
            value = _safe_float(value, float("nan"))
            if not math.isfinite(value) or value <= 0:
                raise ValueError("target_temperature_step must be positive")
        elif name == "temperature_unit":
            value = str(value).strip()
            if not value:
                raise ValueError("temperature_unit must not be empty")
        elif (
            name == "is_away_mode_on"
            and value is not None
            and not isinstance(value, bool)
        ):
            value = self._template_to_bool(value)
        elif name == "precision":
            value = _safe_float(value, float("nan"))
            if not math.isfinite(value) or value <= 0:
                raise ValueError("precision must be a positive number")
        elif name == "state":
            name = "current_operation"
            value = str(value).lower()
            if value not in self._attr_operation_list:
                raise ValueError(f"Invalid water heater operation mode: {value}")
        return super()._apply_native_template_value(name, value)

    def _native_templates_applied(self) -> None:
        if self._attr_min_temp > self._attr_max_temp:
            self._attr_min_temp, self._attr_max_temp = (
                self._attr_max_temp,
                self._attr_min_temp,
            )
        self._attr_current_temperature = self._bounded_temperature(
            self._attr_current_temperature
        )
        self._attr_target_temperature = self._bounded_temperature(
            self._attr_target_temperature
        )
        self._attr_target_temperature_high = self._bounded_temperature(
            self._attr_target_temperature_high
        )
        self._attr_target_temperature_low = self._bounded_temperature(
            self._attr_target_temperature_low
        )
        if (
            self._attr_target_temperature_high is not None
            and self._attr_target_temperature_low is not None
            and self._attr_target_temperature_low
            > self._attr_target_temperature_high
        ):
            (
                self._attr_target_temperature_low,
                self._attr_target_temperature_high,
            ) = (
                self._attr_target_temperature_high,
                self._attr_target_temperature_low,
            )
        if self._attr_current_operation not in self._attr_operation_list:
            self._attr_current_operation = STATE_OFF
        self._refresh_supported_features()


class VirtualUpdate(_NativeGenericMixin, VirtualEntity, UpdateEntity):
    """Virtual software update entity."""

    PLATFORM_DOMAIN = "update"
    NATIVE_OPTION_KEYS = frozenset(
        {
            "auto_update",
            "display_precision",
            "in_progress",
            "installed_version",
            "latest_version",
            "release_notes",
            "release_summary",
            "release_url",
            "support_backup",
            "title",
            "update_percentage",
            "versions",
        }
    )

    def __init__(self, config, old_style: bool):
        super().__init__(config, old_style)
        self._configured_supported_features = None
        initial = config.get(CONF_INITIAL_VALUE)
        self._attr_installed_version = str(config.get("installed_version", initial))
        self._attr_latest_version = str(
            config.get("latest_version", self._attr_installed_version)
        )
        self._attr_release_summary = config.get("release_summary")
        self._attr_release_url = config.get("release_url")
        self._attr_title = config.get("title")
        self._attr_auto_update = _safe_bool(config.get("auto_update", False))
        self._attr_in_progress = _safe_bool(config.get("in_progress", False))
        self._attr_display_precision = _safe_int(
            config.get("display_precision", 0), 0
        )
        update_percentage = config.get("update_percentage")
        try:
            self._attr_update_percentage = (
                self._bounded_update_percentage(update_percentage)
                if update_percentage is not None
                else None
            )
        except (OverflowError, ValueError):
            _LOGGER.warning("Ignoring invalid stored update percentage")
            self._attr_update_percentage = None
        self._release_notes = config.get("release_notes")
        self._versions = _string_list(config.get("versions"))
        self._support_backup = _safe_bool(config.get("support_backup", True), True)
        self._refresh_supported_features()

    def _refresh_supported_features(self) -> None:
        if self._configured_supported_features is not None:
            self._attr_supported_features = self._configured_supported_features
            return
        self._attr_supported_features = UpdateEntityFeature.INSTALL
        if self._versions:
            self._attr_supported_features |= UpdateEntityFeature.SPECIFIC_VERSION
        if self._support_backup:
            self._attr_supported_features |= UpdateEntityFeature.BACKUP
        if self._release_notes is not None:
            self._attr_supported_features |= UpdateEntityFeature.RELEASE_NOTES
        if (
            self._attr_update_percentage is not None
            or self._attr_in_progress is True
        ):
            self._attr_supported_features |= UpdateEntityFeature.PROGRESS

    @staticmethod
    def _bounded_update_percentage(value) -> float:
        parsed = _safe_float(value, float("nan"))
        if not math.isfinite(parsed) or not 0 <= parsed <= 100:
            raise ValueError("update_percentage must be between 0 and 100")
        return parsed

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
        if self._versions and str(target_version) not in self._versions:
            raise ValueError(f"Invalid update version: {target_version}")
        self._attr_installed_version = str(target_version)
        self._virtual_attributes["last_install_backup"] = backup
        self._update_attributes()
        self.async_write_ha_state()

    async def async_release_notes(self) -> str | None:
        return self._release_notes

    def _apply_native_template_value(self, name: str, value) -> bool:
        if name == "supported_features":
            value = _supported_feature_mask(value, UpdateEntityFeature)
            changed = self._configured_supported_features != value
            self._configured_supported_features = value
            return changed
        if name == "versions":
            value = _template_string_list(value, name)
            changed = self._versions != value
            self._versions = value
            return changed
        if name == "support_backup":
            value = value if isinstance(value, bool) else self._template_to_bool(value)
            changed = self._support_backup != value
            self._support_backup = value
            return changed
        if name == "release_notes":
            value = None if value is None else str(value)
            changed = self._release_notes != value
            self._release_notes = value
            return changed
        if (
            name in {"auto_update", "in_progress"}
            and value is not None
            and not isinstance(value, bool)
        ):
            value = self._template_to_bool(value)
        elif name == "update_percentage":
            value = (
                None
                if value is None or value == ""
                else self._bounded_update_percentage(value)
            )
        elif name == "display_precision":
            value = _safe_int(value, -1, -1)
            if value < 0:
                raise ValueError("display_precision must be a non-negative integer")
        elif name in {
            "device_class",
            "installed_version",
            "latest_version",
            "release_summary",
            "release_url",
            "title",
        }:
            value = None if value is None else str(value)
        if name == "state":
            name = "latest_version"
            value = str(value)
        return super()._apply_native_template_value(name, value)

    def _native_templates_applied(self) -> None:
        self._refresh_supported_features()


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
