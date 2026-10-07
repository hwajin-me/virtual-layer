"""
This component provides support for a virtual light.

"""
from __future__ import annotations

import logging
import math
import asyncio
import copy
from collections.abc import Callable
from typing import Any

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_MODE,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ATTR_EFFECT_LIST,
    ATTR_FLASH,
    ATTR_HS_COLOR,
    ATTR_RGB_COLOR,
    ATTR_RGBW_COLOR,
    ATTR_RGBWW_COLOR,
    ATTR_XY_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.components.light import (
    DOMAIN as PLATFORM_DOMAIN,
)
from homeassistant.components.light.const import DATA_COMPONENT as LIGHT_COMPONENT
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON
from homeassistant.core import Context, HomeAssistant, callback
from homeassistant.helpers.config_validation import PLATFORM_SCHEMA
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity_component import async_update_entity
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.util import color as color_util

from . import get_entity_configs
from .const import *
from .entity import VirtualEntity, nonnegative_int, virtual_schema, _COMMAND_ACTION_CHAIN

_LOGGER = logging.getLogger(__name__)

DEPENDENCIES = [COMPONENT_DOMAIN]

CONF_SUPPORT_BRIGHTNESS = "support_brightness"
CONF_INITIAL_BRIGHTNESS = "initial_brightness"
CONF_SUPPORT_COLOR = "support_color"
CONF_INITIAL_COLOR = "initial_color"
CONF_SUPPORT_COLOR_TEMP = "support_color_temp"
CONF_INITIAL_COLOR_TEMP = "initial_color_temp"
CONF_SUPPORT_WHITE_VALUE = "support_white_value"
CONF_INITIAL_WHITE_VALUE = "initial_white_value"
CONF_SUPPORT_EFFECT = "support_effect"
CONF_INITIAL_EFFECT = "initial_effect"
CONF_INITIAL_EFFECT_LIST = "initial_effect_list"
CONF_MATTER_LIGHT_TYPE = "matter_light_type"

MATTER_LIGHT_COLOR_MODES = {
    "on_off": {ColorMode.ONOFF},
    "dimmable": {ColorMode.BRIGHTNESS},
    "color_temperature": {ColorMode.COLOR_TEMP},
    "extended_color": {ColorMode.HS, ColorMode.XY, ColorMode.COLOR_TEMP},
}

DEFAULT_LIGHT_VALUE = "on"
DEFAULT_SUPPORT_BRIGHTNESS = True
DEFAULT_INITIAL_BRIGHTNESS = 255
DEFAULT_SUPPORT_COLOR = False
DEFAULT_INITIAL_COLOR = [0, 100]
DEFAULT_SUPPORT_COLOR_TEMP = False
DEFAULT_INITIAL_COLOR_TEMP = 4000
DEFAULT_SUPPORT_WHITE_VALUE = False
DEFAULT_INITIAL_WHITE_VALUE = 240
DEFAULT_SUPPORT_EFFECT = False
DEFAULT_INITIAL_EFFECT = "none"
DEFAULT_INITIAL_EFFECT_LIST = ["rainbow", "none"]


def _command_seconds(value):
    """Reject non-finite values and booleans before scheduling deadlines."""
    if isinstance(value, bool):
        raise vol.Invalid("command duration must be a finite number")
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError) as err:
        raise vol.Invalid("command duration must be a finite number") from err
    if not math.isfinite(value):
        raise vol.Invalid("command duration must be a finite number")
    return value


BASE_SCHEMA = virtual_schema(DEFAULT_LIGHT_VALUE, {
    vol.Optional(CONF_SUPPORT_BRIGHTNESS, default=DEFAULT_SUPPORT_BRIGHTNESS): cv.boolean,
    vol.Optional(CONF_INITIAL_BRIGHTNESS, default=DEFAULT_INITIAL_BRIGHTNESS): cv.byte,
    vol.Optional(CONF_SUPPORT_COLOR, default=DEFAULT_SUPPORT_COLOR): cv.boolean,
    vol.Optional(
        CONF_INITIAL_COLOR,
        default=lambda: list(DEFAULT_INITIAL_COLOR),
    ): cv.ensure_list,
    vol.Optional(CONF_SUPPORT_COLOR_TEMP, default=DEFAULT_SUPPORT_COLOR_TEMP): cv.boolean,
    vol.Optional(CONF_INITIAL_COLOR_TEMP, default=DEFAULT_INITIAL_COLOR_TEMP): nonnegative_int,
    vol.Optional(CONF_SUPPORT_WHITE_VALUE, default=DEFAULT_SUPPORT_WHITE_VALUE): cv.boolean,
    vol.Optional(CONF_INITIAL_WHITE_VALUE, default=DEFAULT_INITIAL_WHITE_VALUE): cv.byte,
    vol.Optional(CONF_SUPPORT_EFFECT, default=DEFAULT_SUPPORT_EFFECT): cv.boolean,
    vol.Optional(CONF_INITIAL_EFFECT, default=DEFAULT_INITIAL_EFFECT): cv.string,
    vol.Optional(
        CONF_INITIAL_EFFECT_LIST,
        default=lambda: list(DEFAULT_INITIAL_EFFECT_LIST),
    ): cv.ensure_list,
    vol.Optional(CONF_MATTER_LIGHT_TYPE): vol.In(MATTER_LIGHT_COLOR_MODES),
    vol.Optional(CONF_LIGHT_RESPONSE_DELAY, default=2): vol.All(
        vol.Coerce(int), vol.Range(min=0, max=30)
    ),
    vol.Optional(CONF_LIGHT_RESPONSE_RETRIES, default=2): vol.All(
        vol.Coerce(int), vol.Range(min=0, max=10)
    ),
    vol.Optional(CONF_LIGHT_IGNORE_UNRESPONSIVE, default=True): cv.boolean,
    vol.Optional(CONF_LIGHT_STATE_MODE, default="observed"): vol.In(
        ("observed", "bounded_optimistic")
    ),
    vol.Optional(CONF_LIGHT_OPTIMISTIC_WINDOW, default=1): vol.All(
        _command_seconds, vol.Range(min=0, max=5)
    ),
    **{
        vol.Optional(key, default=LIGHT_COMMAND_OPTIONS[key]): vol.All(
            _command_seconds, vol.Range(min=0.05, max=300)
        )
        for key in (CONF_LIGHT_DISPATCH_TIMEOUT, CONF_LIGHT_FEEDBACK_TIMEOUT,
                    CONF_LIGHT_COMMAND_TIMEOUT)
    },
})

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(BASE_SCHEMA)

LIGHT_SCHEMA = vol.Schema(BASE_SCHEMA)


def light_command_options(values, *, repair=False):
    """Validate UI policy fields, or isolate damaged legacy policy values."""
    validated = {}
    for key, default in LIGHT_COMMAND_OPTIONS.items():
        validator = next(value for marker, value in BASE_SCHEMA.items() if marker.schema == key)
        try:
            validated[key] = validator(values.get(key, default))
        except (vol.Invalid, TypeError, ValueError, OverflowError):
            if not repair:
                raise
            validated[key] = default
    return validated


def _as_color_temp_kelvin(
    value: float | str,
    fallback: int | None = DEFAULT_INITIAL_COLOR_TEMP,
) -> int | None:
    """Normalize legacy mired values while storing modern Kelvin values."""
    if isinstance(value, bool):
        return fallback
    try:
        color_temp = float(value)
    except (TypeError, ValueError, OverflowError):
        return fallback
    if not math.isfinite(color_temp) or color_temp <= 0:
        return fallback
    if color_temp < 1000:
        color_temp = 1_000_000 / color_temp
    return max(1000, min(40000, round(color_temp)))


def _as_brightness(value, fallback=None) -> int | None:
    """Return a valid Home Assistant brightness value."""
    if value is None or isinstance(value, bool):
        return fallback
    try:
        brightness = int(value)
    except (TypeError, ValueError, OverflowError):
        return fallback
    return brightness if 0 <= brightness <= 255 else fallback


def _as_hs_color(value, fallback=None) -> tuple[float, float] | None:
    """Return a finite hue/saturation pair in Home Assistant's ranges."""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return fallback
    if any(isinstance(item, bool) for item in value):
        return fallback
    try:
        hue, saturation = (float(item) for item in value)
    except (TypeError, ValueError, OverflowError):
        return fallback
    if (
        not math.isfinite(hue)
        or not math.isfinite(saturation)
        or not 0 <= hue <= 360
        or not 0 <= saturation <= 100
    ):
        return fallback
    return hue, saturation


def _as_color_tuple(value, length: int, maximum: float, fallback=None):
    """Return a finite Home Assistant color tuple with the requested shape."""
    if not isinstance(value, (list, tuple)) or len(value) != length:
        return fallback
    if any(isinstance(item, bool) for item in value):
        return fallback
    try:
        color = tuple(float(item) for item in value)
    except (TypeError, ValueError, OverflowError):
        return fallback
    if any(not math.isfinite(item) or not 0 <= item <= maximum for item in color):
        return fallback
    if maximum == 255:
        return tuple(int(item) for item in color)
    return color


def validate_domain_options(config) -> None:
    """Reject malformed light colors and effects entered through the UI."""
    if config.get(CONF_SUPPORT_EFFECT):
        raise vol.Invalid("Matter-compatible lights do not support effects")
    native_templates = config.get(CONF_NATIVE_TEMPLATES, {})
    if isinstance(native_templates, dict) and {
        "effect",
        "effects",
        "effect_list",
    } & set(native_templates):
        raise vol.Invalid("Matter-compatible lights do not support effect templates")
    if config.get(CONF_SUPPORT_COLOR) and _as_hs_color(
        config.get(CONF_INITIAL_COLOR)
    ) is None:
        raise vol.Invalid("initial_color must be a valid hue/saturation pair")


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
        entity = LIGHT_SCHEMA(entity)
        entities.append(VirtualLight(entity, False))
    async_add_entities(entities)


class VirtualLight(VirtualEntity, LightEntity):

    _COLOR_ATTRIBUTES = (
        "_attr_hs_color",
        "_attr_xy_color",
        "_attr_rgb_color",
        "_attr_rgbw_color",
        "_attr_rgbww_color",
        "_attr_color_temp_kelvin",
    )

    def __init__(self, config, old_style: bool):
        """Initialize a Virtual light."""
        super().__init__(config, PLATFORM_DOMAIN, old_style)

        self._attr_supported_features = LightEntityFeature(0)
        self._attr_supported_color_modes = set()
        self._attr_color_mode = ColorMode.UNKNOWN
        self._attr_min_color_temp_kelvin = 1000
        self._attr_max_color_temp_kelvin = 40000
        self._attr_brightness = None
        self._attr_hs_color = None
        self._attr_xy_color = None
        self._attr_rgb_color = None
        self._attr_rgbw_color = None
        self._attr_rgbww_color = None
        self._attr_color_temp_kelvin = None
        self._attr_effect = None
        self._attr_effect_list = None
        self._response_delay = config.get(CONF_LIGHT_RESPONSE_DELAY, 2)
        self._response_retries = config.get(CONF_LIGHT_RESPONSE_RETRIES, 2)
        self._ignore_unresponsive = config.get(CONF_LIGHT_IGNORE_UNRESPONSIVE, True)
        self._response_refresh_cancel = None
        self._group_command_lock = asyncio.Lock()
        self._group_authoritative = False
        self._group_revision = 0
        self._group_dispatching = False
        self._group_refresh_task = None
        self._group_target = None
        self._group_removed = False
        self._stock_revision = None
        self._source_command_tasks = {}
        self._source_pending_commands = {}
        self._source_dispatch_revisions = {}
        self._command_sources = {}
        self._command_deadline = 0
        self._command_deadline_cancel = None
        self._optimistic_cancel = None
        self._command_observed_snapshot = None
        self._command_last_status = None
        self._command_state_mode = config.get(CONF_LIGHT_STATE_MODE, "observed")
        self._optimistic_window = config.get(CONF_LIGHT_OPTIMISTIC_WINDOW, 1)
        self._dispatch_timeout = config.get(CONF_LIGHT_DISPATCH_TIMEOUT, 10)
        self._feedback_timeout = config.get(CONF_LIGHT_FEEDBACK_TIMEOUT, 10)
        self._command_timeout = config.get(CONF_LIGHT_COMMAND_TIMEOUT, 30)
        matter_type = config.get(CONF_MATTER_LIGHT_TYPE)
        if matter_type:
            self._matter_color_modes = set(MATTER_LIGHT_COLOR_MODES[matter_type])
        else:
            # Load legacy entries safely while restricting them to Matter color
            # capabilities. Effects are intentionally never restored.
            self._matter_color_modes = set()
            if config.get(CONF_SUPPORT_COLOR_TEMP):
                self._matter_color_modes.add(ColorMode.COLOR_TEMP)
            if config.get(CONF_SUPPORT_COLOR):
                self._matter_color_modes.add(ColorMode.HS)
            if config.get(CONF_SUPPORT_BRIGHTNESS) and not self._matter_color_modes:
                self._matter_color_modes.add(ColorMode.BRIGHTNESS)
            if not self._matter_color_modes:
                self._matter_color_modes.add(ColorMode.ONOFF)
        self._attr_supported_color_modes = set(self._matter_color_modes)

    async def async_added_to_hass(self) -> None:
        """Publish a capability-sanitized state after every setup or reload."""
        await super().async_added_to_hass()
        # ``VirtualEntity`` restores state and then starts source templates.
        # Publish one final normalized snapshot after both steps complete so a
        # bridge reconnecting during a config-entry reload cannot cache old
        # brightness or colour attributes from RestoreState.
        self._reconcile_color_capabilities()
        self._update_attributes()
        self.async_write_ha_state()

    @property
    def brightness(self) -> int | None:
        """Return level only when this light actually supports level control."""
        return (
            self._attr_brightness
            if self._attr_is_on
            and self._attr_supported_color_modes != {ColorMode.ONOFF}
            else None
        )

    @property
    def color_mode(self) -> ColorMode | None:
        return self._attr_color_mode if self._attr_is_on else None

    @property
    def hs_color(self) -> tuple[float, float] | None:
        return (
            self._attr_hs_color
            if self._attr_is_on and ColorMode.HS in self._attr_supported_color_modes
            else None
        )

    @property
    def xy_color(self) -> tuple[float, float] | None:
        return (
            self._attr_xy_color
            if self._attr_is_on and ColorMode.XY in self._attr_supported_color_modes
            else None
        )

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        return (
            self._attr_rgb_color
            if self._attr_is_on and ColorMode.RGB in self._attr_supported_color_modes
            else None
        )

    @property
    def rgbw_color(self) -> tuple[int, int, int, int] | None:
        return (
            self._attr_rgbw_color
            if self._attr_is_on and ColorMode.RGBW in self._attr_supported_color_modes
            else None
        )

    @property
    def rgbww_color(self) -> tuple[int, int, int, int, int] | None:
        return (
            self._attr_rgbww_color
            if self._attr_is_on and ColorMode.RGBWW in self._attr_supported_color_modes
            else None
        )

    @property
    def color_temp_kelvin(self) -> int | None:
        return (
            self._attr_color_temp_kelvin
            if self._attr_is_on
            and ColorMode.COLOR_TEMP in self._attr_supported_color_modes
            else None
        )

    def _create_state(self, config):
        super()._create_state(config)

        self._attr_is_on = config.get(CONF_INITIAL_VALUE).lower() == STATE_ON

        if ColorMode.BRIGHTNESS in self._attr_supported_color_modes:
            self._attr_color_mode = ColorMode.BRIGHTNESS
            self._attr_brightness = _as_brightness(
                config.get(CONF_INITIAL_BRIGHTNESS),
                DEFAULT_INITIAL_BRIGHTNESS,
            )
        if ColorMode.HS in self._attr_supported_color_modes:
            self._attr_color_mode = ColorMode.HS
            self._attr_hs_color = _as_hs_color(
                config.get(CONF_INITIAL_COLOR),
                tuple(DEFAULT_INITIAL_COLOR),
            )
            self._attr_brightness = _as_brightness(
                config.get(CONF_INITIAL_BRIGHTNESS),
                DEFAULT_INITIAL_BRIGHTNESS,
            )
        if ColorMode.COLOR_TEMP in self._attr_supported_color_modes:
            self._attr_color_mode = ColorMode.COLOR_TEMP
            self._attr_color_temp_kelvin = _as_color_temp_kelvin(
                config.get(CONF_INITIAL_COLOR_TEMP)
            )
            self._attr_brightness = config.get(CONF_INITIAL_BRIGHTNESS)
        if self._attr_color_mode == ColorMode.UNKNOWN:
            self._attr_color_mode = ColorMode.ONOFF
        self._reconcile_color_capabilities()
        if self._attr_supported_features & LightEntityFeature.EFFECT:
            self._attr_effect = config.get(CONF_INITIAL_EFFECT)

    def _restore_state(self, state, config):
        super()._restore_state(state, config)
        restored = self._restored_state_value(state, config)
        self._attr_is_on = str(restored).lower() == STATE_ON

        try:
            restored_color_mode = ColorMode(
                state.attributes.get(ATTR_COLOR_MODE, ColorMode.ONOFF),
            )
        except (TypeError, ValueError):
            restored_color_mode = ColorMode.ONOFF
        self._attr_color_mode = (
            restored_color_mode
            if restored_color_mode in self._attr_supported_color_modes
            else next(iter(self._attr_supported_color_modes), ColorMode.ONOFF)
        )
        if self._attr_color_mode == ColorMode.BRIGHTNESS:
            self._attr_brightness = _as_brightness(
                state.attributes.get(ATTR_BRIGHTNESS),
                _as_brightness(
                    config.get(CONF_INITIAL_BRIGHTNESS),
                    DEFAULT_INITIAL_BRIGHTNESS,
                ),
            )
        if self._attr_color_mode == ColorMode.HS:
            self._attr_hs_color = _as_hs_color(
                state.attributes.get(ATTR_HS_COLOR),
                _as_hs_color(
                    config.get(CONF_INITIAL_COLOR),
                    tuple(DEFAULT_INITIAL_COLOR),
                ),
            )
            self._attr_brightness = _as_brightness(
                state.attributes.get(ATTR_BRIGHTNESS),
                _as_brightness(
                    config.get(CONF_INITIAL_BRIGHTNESS),
                    DEFAULT_INITIAL_BRIGHTNESS,
                ),
            )
        if self._attr_color_mode == ColorMode.COLOR_TEMP:
            self._attr_color_temp_kelvin = _as_color_temp_kelvin(
                state.attributes.get(
                    ATTR_COLOR_TEMP_KELVIN,
                    config.get(CONF_INITIAL_COLOR_TEMP),
                )
            )
            self._attr_brightness = _as_brightness(
                state.attributes.get(ATTR_BRIGHTNESS),
                _as_brightness(
                    config.get(CONF_INITIAL_BRIGHTNESS),
                    DEFAULT_INITIAL_BRIGHTNESS,
                ),
            )
        color_specs = {
            ColorMode.XY: ("_attr_xy_color", ATTR_XY_COLOR, 2, 1),
            ColorMode.RGB: ("_attr_rgb_color", ATTR_RGB_COLOR, 3, 255),
            ColorMode.RGBW: ("_attr_rgbw_color", ATTR_RGBW_COLOR, 4, 255),
            ColorMode.RGBWW: ("_attr_rgbww_color", ATTR_RGBWW_COLOR, 5, 255),
        }
        if spec := color_specs.get(self._attr_color_mode):
            attribute_name, state_name, length, maximum = spec
            setattr(
                self,
                attribute_name,
                _as_color_tuple(
                    state.attributes.get(state_name),
                    length,
                    maximum,
                ),
            )
            self._attr_brightness = _as_brightness(
                state.attributes.get(ATTR_BRIGHTNESS),
                _as_brightness(
                    config.get(CONF_INITIAL_BRIGHTNESS),
                    DEFAULT_INITIAL_BRIGHTNESS,
                ),
            )
        if self._attr_effect_list:
            effect = state.attributes.get(ATTR_EFFECT, config.get(CONF_INITIAL_EFFECT))
            self._attr_effect = (
                effect
                if effect in (self._attr_effect_list or [])
                else config.get(CONF_INITIAL_EFFECT)
            )
        # A restart may restore a state saved before its source capability was
        # reduced.  Reconcile before Home Assistant publishes that first state,
        # so MatterBridge never rediscovers stale colour clusters.
        self._reconcile_color_capabilities()

    def _update_attributes(self):
        """Return the state attributes."""
        super()._update_attributes()
        # These attributes are integration-owned mirrors of LightEntity
        # properties.  Remove a value left by an older capability profile
        # before adding the properties that are valid now; ``dict.update`` on
        # its own otherwise leaves a former colour value visible indefinitely.
        for name in (
            ATTR_BRIGHTNESS,
            ATTR_COLOR_MODE,
            ATTR_COLOR_TEMP_KELVIN,
            ATTR_EFFECT,
            ATTR_EFFECT_LIST,
            ATTR_HS_COLOR,
            ATTR_XY_COLOR,
            ATTR_RGB_COLOR,
            ATTR_RGBW_COLOR,
            ATTR_RGBWW_COLOR,
        ):
            self._attr_extra_state_attributes.pop(name, None)
        self._attr_extra_state_attributes.update({
            name: value for name, value in (
                (ATTR_BRIGHTNESS, self.brightness),
                (ATTR_COLOR_MODE, self.color_mode),
                (ATTR_COLOR_TEMP_KELVIN, self.color_temp_kelvin),
                (ATTR_EFFECT, self._attr_effect),
                (ATTR_EFFECT_LIST, self._attr_effect_list),
                (ATTR_HS_COLOR, self.hs_color),
                (ATTR_XY_COLOR, self.xy_color),
                (ATTR_RGB_COLOR, self.rgb_color),
                (ATTR_RGBW_COLOR, self.rgbw_color),
                (ATTR_RGBWW_COLOR, self.rgbww_color),
            ) if value is not None
        })
        if self._command_sources:
            self._attr_extra_state_attributes.update({
                "command_status": self._command_status(),
                "command_target": {
                    "power": "off" if self._group_target[0] == "turn_off"
                    or self._group_target[2].get("brightness") == 0 else "on",
                    **{key: copy.deepcopy(value)
                       for key, value in self._group_target[2].items()
                       if key in {"brightness", "color_temp_kelvin", "hs_color",
                                  "xy_color", "rgb_color", "rgbw_color", "rgbww_color"}},
                },
                "command_sources": {
                    source: {key: record[key] for key in
                             ("status", "attempts", "error", "confirmation")
                             if key in record}
                    for source, record in list(self._command_sources.items())[:64]
                },
                "command_sources_truncated": len(self._command_sources) > 64,
            })
            if self._command_last_status:
                self._attr_extra_state_attributes["previous_command_status"] = self._command_last_status

    def _select_color(self, color_mode, attribute_name, value) -> None:
        for current_attribute in self._COLOR_ATTRIBUTES:
            if current_attribute != attribute_name:
                setattr(self, current_attribute, None)
        self._attr_color_mode = color_mode
        setattr(self, attribute_name, value)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on."""
        if ATTR_EFFECT in kwargs or ATTR_FLASH in kwargs:
            raise ValueError("Matter-compatible lights do not support effects or flash")
        _LOGGER.debug("turning %s on %s", self.name, kwargs)
        snapshot = {
            name: getattr(self, name, None)
            for name in (
                "_attr_is_on",
                "_attr_brightness",
                "_attr_color_mode",
                "_attr_effect",
                *self._COLOR_ATTRIBUTES,
            )
        }
        try:
            self._apply_turn_on_values(kwargs)
        except Exception:
            for name, value in snapshot.items():
                setattr(self, name, value)
            raise

        self._attr_is_on = True
        self._update_attributes()
        self.async_write_ha_state()

    def _apply_turn_on_values(self, kwargs: dict[str, Any]) -> None:
        """Validate and stage light service values before publishing state."""
        hs_color = kwargs.get(ATTR_HS_COLOR)

        if hs_color is not None and ColorMode.HS in self._attr_supported_color_modes:
            parsed_hs_color = _as_hs_color(hs_color)
            if parsed_hs_color is None:
                raise ValueError("hs_color must be a valid hue/saturation pair")
            self._select_color(ColorMode.HS, "_attr_hs_color", parsed_hs_color)

        for color_mode, state_name, attribute_name, length, maximum in (
            (ColorMode.XY, ATTR_XY_COLOR, "_attr_xy_color", 2, 1),
            (ColorMode.RGB, ATTR_RGB_COLOR, "_attr_rgb_color", 3, 255),
            (ColorMode.RGBW, ATTR_RGBW_COLOR, "_attr_rgbw_color", 4, 255),
            (ColorMode.RGBWW, ATTR_RGBWW_COLOR, "_attr_rgbww_color", 5, 255),
        ):
            if state_name not in kwargs or color_mode not in self._attr_supported_color_modes:
                continue
            color = _as_color_tuple(kwargs[state_name], length, maximum)
            if color is None:
                raise ValueError(f"{state_name} contains invalid color channels")
            self._select_color(color_mode, attribute_name, color)

        ct = kwargs.get(ATTR_COLOR_TEMP_KELVIN, None)
        if ct is not None and ColorMode.COLOR_TEMP in self._attr_supported_color_modes:
            parsed_color_temp = _as_color_temp_kelvin(ct, None)
            if parsed_color_temp is None:
                raise ValueError("color_temp_kelvin must be a positive number")
            self._select_color(
                ColorMode.COLOR_TEMP,
                "_attr_color_temp_kelvin",
                parsed_color_temp,
            )

        brightness = kwargs.get(ATTR_BRIGHTNESS, None)
        if brightness is not None:
            parsed_brightness = _as_brightness(brightness)
            if parsed_brightness is None:
                raise ValueError("brightness must be between 0 and 255")
            if self._attr_color_mode == ColorMode.UNKNOWN:
                self._attr_color_mode = ColorMode.BRIGHTNESS
            self._attr_brightness = parsed_brightness

        if self._attr_color_mode == ColorMode.UNKNOWN:
            self._attr_color_mode = ColorMode.ONOFF

        effect = kwargs.get(ATTR_EFFECT, None)
        if effect is not None and self._attr_supported_features & LightEntityFeature.EFFECT:
            if self._attr_effect_list and effect not in self._attr_effect_list:
                raise ValueError(f"Invalid light effect: {effect}")
            self._attr_effect = effect

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        if ATTR_FLASH in kwargs:
            raise ValueError("Matter-compatible lights do not support flash")
        _LOGGER.debug("turning %s off %s", self.name, kwargs)
        self._attr_is_on = False
        self._update_attributes()
        self.async_write_ha_state()

    @property
    def supported_features(self):
        """Forward transitions only when a physical source supports them."""
        if self.hass is not None:
            for source in self._group_sources():
                state = self.hass.states.get(source)
                features = state.attributes.get("supported_features", 0) if state else 0
                if (isinstance(features, int) and not isinstance(features, bool)
                        and features >= 0 and features & LightEntityFeature.TRANSITION):
                    return LightEntityFeature.TRANSITION
        return LightEntityFeature(0)

    def _preserve_optimistic_command_state(self, command, args, kwargs) -> bool:
        """Only preserve a requested value during its independent display lease."""
        return self._group_authoritative

    def _group_sources(self):
        return list(dict.fromkeys(
            source for source in self._source_entities
            if isinstance(source, str) and source.startswith("light.")
            and source != self.entity_id
        ))

    def _command_action_spec(self, command):
        spec = super()._command_action_spec(command)
        sources = self._group_sources()
        if (not sources or command not in {"turn_on", "turn_off"}
                or len(sources) == 1 and self._response_delay <= 0):
            return spec
        # Upgrade only the exact stock pass-through action. Independently
        # configured scripts (including optimistic:false) remain authoritative.
        if not self._has_stock_group_action(command):
            return spec
        return ([{"parallel": [
            {"sequence": [{"action": f"light.{command}",
                           "target": {ATTR_ENTITY_ID: source},
                           "data": "{{ command_data }}",
                           "continue_on_error": True}]}
            for source in sources
        ]}], True)

    def _has_stock_group_action(self, command):
        spec = super()._command_action_spec(command)
        sources = self._group_sources()
        # UI-generated single-source actions use a scalar target; old profiles
        # and groups use lists. Both exact pass-through forms are stock actions.
        targets = [sources, *([sources[0]] if len(sources) == 1 else [])]
        return spec is None or any(spec == ([{
            "action": f"light.{command}",
            "target": {ATTR_ENTITY_ID: target},
            "data": "{{ command_data }}",
        }], True) for target in targets)

    def _command_service_data(self, command, method, args, kwargs):
        data = super()._command_service_data(command, method, args, kwargs)
        # Core passes a compatibility mired value to LightEntity methods.
        # Re-entering the public service with both descriptors is invalid.
        if "color_temp_kelvin" in data:
            data.pop("color_temp", None)
            data.pop("kelvin", None)
        return data

    def _validate_command_action(self, command, args, kwargs):
        if command not in {"turn_on", "turn_off"}:
            return
        if ATTR_EFFECT in kwargs or ATTR_FLASH in kwargs:
            raise ValueError("Matter-compatible lights do not support effects or flash")
        transition = kwargs.get("transition", 0)
        if (isinstance(transition, bool) or not isinstance(transition, (int, float))
                or not math.isfinite(transition) or transition < 0):
            raise ValueError("transition must be a finite nonnegative number")
        if command == "turn_on":
            names = ("_attr_brightness", "_attr_color_mode", "_attr_effect", *self._COLOR_ATTRIBUTES)
            snapshot = {name: getattr(self, name) for name in names}
            try:
                self._apply_turn_on_values(kwargs)
            finally:
                for name, value in snapshot.items():
                    setattr(self, name, value)

    _ACTIVE_COMMAND_STATES = frozenset({"queued", "dispatching", "awaiting_feedback"})

    def _command_status(self):
        statuses = {record["status"] for record in self._command_sources.values()}
        for status in ("queued", "dispatching", "awaiting_feedback"):
            if status in statuses:
                return status
        if statuses <= {"confirmed", "already_at_target"}:
            return "confirmed"
        if statuses & {"confirmed", "already_at_target"}:
            return "partial"
        return "failed" if "failed" in statuses else "timed_out"

    def _clear_command_display(self):
        if self._group_authoritative and self._command_observed_snapshot is not None:
            for name, value in self._command_observed_snapshot.items():
                setattr(self, name, value)
        self._group_authoritative = False
        if self._optimistic_cancel is not None:
            self._optimistic_cancel()
            self._optimistic_cancel = None

    def _release_command_display(self):
        self._clear_command_display()
        self._apply_templates()

    def _finish_command_check(self):
        if any(record["status"] in self._ACTIVE_COMMAND_STATES
               for record in self._command_sources.values()):
            return
        if self._command_deadline_cancel is not None:
            self._command_deadline_cancel()
            self._command_deadline_cancel = None
        if self._response_refresh_cancel is not None:
            self._response_refresh_cancel()
            self._response_refresh_cancel = None
        self._release_command_display()

    def _expire_command(self, revision):
        """Bound queue, dispatch and feedback even if an integration stalls."""
        if revision != self._group_revision or self._group_removed:
            return
        self._command_deadline_cancel = None
        for record in self._command_sources.values():
            if record["status"] in self._ACTIVE_COMMAND_STATES:
                record["status"] = "timed_out"
                record["error"] = "command_timeout"
        self._source_pending_commands = {
            source: pending for source, pending in self._source_pending_commands.items()
            if pending[0] != revision
        }
        self._finish_command_check()

    def _begin_command(self, command, method, data, sources, *, optimistic=True):
        if self._group_authoritative:
            self._release_command_display()
        if self._command_sources:
            self._command_last_status = (
                "superseded" if any(record["status"] in self._ACTIVE_COMMAND_STATES
                                    for record in self._command_sources.values())
                else self._command_status()
            )
        self._group_revision += 1
        revision = self._group_revision
        self._cancel_group_refresh()
        for name in ("_command_deadline_cancel", "_optimistic_cancel"):
            cancel = getattr(self, name)
            if cancel is not None:
                cancel()
                setattr(self, name, None)
        self._group_target = (command, method, copy.deepcopy(data))
        self._command_observed_snapshot = {
            name: copy.deepcopy(getattr(self, name))
            for name in ("_attr_is_on", "_attr_brightness", "_attr_color_mode",
                         *self._COLOR_ATTRIBUTES)
        }
        self._command_sources = {
            source: {"status": "queued", "attempts": 0} for source in sources
        }
        self._command_deadline = self.hass.loop.time() + self._command_timeout
        @callback
        def expire(_now):
            self._expire_command(revision)

        self._command_deadline_cancel = async_call_later(
            self.hass, self._command_timeout, expire
        )
        self._group_authoritative = bool(
            optimistic and self._command_state_mode == "bounded_optimistic"
            and self._optimistic_window > 0
        )
        if self._group_authoritative:
            @callback
            def release(_now):
                if revision == self._group_revision and not self._group_removed:
                    self._optimistic_cancel = None
                    self._release_command_display()

            self._optimistic_cancel = async_call_later(
                self.hass, self._optimistic_window, release
            )
        return revision

    async def _async_run_command_action(self, command, method, args, kwargs):
        sources = self._group_sources()
        if not sources or command not in {"turn_on", "turn_off"}:
            # Non-light proxies keep HA's action semantics, without a state hold.
            return await super()._async_run_command_action(command, method, args, kwargs)
        if self._group_removed or any(
            entity == id(self) for entity, _command in _COMMAND_ACTION_CHAIN.get()
        ):
            return False
        self._validate_command_action(command, args, kwargs)
        if all(self._has_stock_group_action(name) for name in ("turn_on", "turn_off")):
            return await self._async_run_stock_command(command, method, args, kwargs)

        # Serialize explicitly customized actions, but never replay them.
        spec = self._command_action_spec(command)
        data = self._command_service_data(command, method, args, kwargs)
        revision = self._begin_command(
            command, method, data, sources, optimistic=spec is None or spec[1]
        )
        try:
            async with asyncio.timeout(self._command_timeout):
                async with self._group_command_lock:
                    if (revision != self._group_revision or self._group_removed
                            or self.hass.loop.time() >= self._command_deadline
                            or not any(record["status"] in self._ACTIVE_COMMAND_STATES
                                       for record in self._command_sources.values())):
                        return False
                    for source in sources:
                        self._mark_source_dispatch(source, revision, data)
                    await super()._async_run_command_action(command, method, args, kwargs)
                    if revision != self._group_revision or self._group_removed:
                        return False
                    if self._group_authoritative:
                        await method(self, *args, **kwargs)
                    self._acknowledge_group_target()
                    for record in self._command_sources.values():
                        if record["status"] == "dispatching":
                            record["status"] = "awaiting_feedback"
                            now = self.hass.loop.time()
                            record["next_check"] = now + float(data.get("transition", 0)) + self._response_delay
                            record["feedback_deadline"] = min(
                                self._command_deadline,
                                now + float(data.get("transition", 0)) + self._feedback_timeout,
                            )
                    if not self._acknowledge_group_target():
                        self._schedule_group_refresh(revision, 0)
                    self._apply_templates()
        except BaseException as err:
            if revision == self._group_revision:
                for record in self._command_sources.values():
                    if record["status"] in self._ACTIVE_COMMAND_STATES:
                        record["status"] = "timed_out" if isinstance(err, TimeoutError) else "failed"
                        record["error"] = "action_timeout" if isinstance(err, TimeoutError) else type(err).__name__
                self._finish_command_check()
            raise
        return False

    async def _async_run_stock_command(self, command, method, args, kwargs):
        """Keep only the latest target; observed state never waits for dispatch."""
        previous = self._group_target if (
            self._command_sources
            and any(record["status"] in self._ACTIVE_COMMAND_STATES
                    for record in self._command_sources.values())
        ) else None
        data = self._command_service_data(command, method, args, kwargs)
        if previous is not None and previous[0] == command == "turn_on":
            colors = {ATTR_HS_COLOR, ATTR_XY_COLOR, ATTR_RGB_COLOR, ATTR_RGBW_COLOR,
                      ATTR_RGBWW_COLOR, ATTR_COLOR_TEMP_KELVIN}
            keep = {ATTR_BRIGHTNESS} | (set() if colors & data.keys() else colors)
            data = {**{key: value for key, value in previous[2].items() if key in keep}, **data}
        sources = self._group_sources()
        revision = self._stock_revision = self._begin_command(command, method, data, sources)
        self._group_dispatching = True
        try:
            if self._group_authoritative:
                await method(self, **data)
        finally:
            self._group_dispatching = False
        tasks = {
            self._queue_source_command(source, revision, command, data)
            for source in sources
        }
        self._apply_templates()
        # This waits only for fast dispatch, never for physical feedback.
        await asyncio.wait(tasks, timeout=0.05)
        return False

    def _queue_source_command(self, source, revision, command, data):
        self._source_pending_commands[source] = (
            revision, command, copy.deepcopy(data), self._context or Context(),
            _COMMAND_ACTION_CHAIN.get(),
        )
        task = self._source_command_tasks.get(source)
        if task is None or task.done():
            task = self.hass.async_create_task(
                self._async_drain_source_commands(source),
                f"Virtual light command {source}",
            )
            self._source_command_tasks[source] = task

            def finished(done):
                if self._source_command_tasks.get(source) is done:
                    self._source_command_tasks.pop(source, None)

            task.add_done_callback(finished)
        return task

    def _mark_source_dispatch(self, source, revision, data):
        record = self._command_sources[source]
        now = self.hass.loop.time()
        self._source_dispatch_revisions[source] = revision
        record["status"] = "dispatching"
        record["attempts"] += 1
        record["before"] = self.hass.states.get(source)
        # Transitions count once per actual dispatch, never once per poll.
        record["next_check"] = now + float(data.get("transition", 0)) + self._response_delay
        record.setdefault("feedback_deadline", min(
            self._command_deadline,
            now + float(data.get("transition", 0)) + self._feedback_timeout,
        ))
        self._update_attributes()
        self._schedule_state_update()
        if self._stock_revision == revision:
            self._schedule_group_refresh(revision, self._response_retries)

    async def _async_drain_source_commands(self, source):
        while not self._group_removed:
            pending = self._source_pending_commands.pop(source, None)
            if pending is None:
                return
            revision, command, data, context, chain = pending
            if (revision != self._group_revision
                    or self._command_sources[source]["status"] not in self._ACTIVE_COMMAND_STATES):
                continue
            if self.hass.loop.time() >= self._command_deadline:
                self._expire_command(revision)
                continue
            record = self._command_sources[source]
            self._mark_source_dispatch(source, revision, data)
            token = _COMMAND_ACTION_CHAIN.set(chain | {(id(self), command)})
            try:
                async with asyncio.timeout(min(
                    self._dispatch_timeout, max(0, self._command_deadline - self.hass.loop.time())
                )):
                    await self.hass.services.async_call(
                        PLATFORM_DOMAIN, command, {**data, ATTR_ENTITY_ID: [source]},
                        blocking=True, context=context,
                    )
            except Exception as err:
                if revision == self._group_revision and record["status"] in self._ACTIVE_COMMAND_STATES:
                    record["status"] = "timed_out" if isinstance(err, TimeoutError) else "failed"
                    # Never expose service payloads, credentials or exception messages.
                    record["error"] = "dispatch_timeout" if isinstance(err, TimeoutError) else type(err).__name__
            finally:
                _COMMAND_ACTION_CHAIN.reset(token)
            if revision == self._group_revision:
                if record["status"] == "dispatching":
                    record["status"] = "awaiting_feedback"
                if not self._acknowledge_group_target():
                    self._schedule_group_refresh(revision, self._response_retries)
                self._finish_command_check()
                self._apply_templates()

    def _cancel_group_refresh(self):
        if self._response_refresh_cancel is not None:
            self._response_refresh_cancel()
            self._response_refresh_cancel = None
        if self._group_refresh_task is not None:
            if self._group_refresh_task is not asyncio.current_task():
                self._group_refresh_task.cancel()

    def _acknowledge_group_target(self):
        """A matching source state is evidence about HA, not physical arrival."""
        if self._group_dispatching or self._group_target is None:
            return False
        command, _method, data = self._group_target
        for source, record in self._command_sources.items():
            if (record["status"] not in self._ACTIVE_COMMAND_STATES
                    or self._source_dispatch_revisions.get(source) != self._group_revision):
                continue
            if (self._stock_revision == self._group_revision
                    and self.hass.loop.time() >= record["feedback_deadline"]):
                record["status"] = "timed_out"
                record["error"] = "feedback_timeout"
                continue
            if not self._group_source_matches(source, command, data):
                continue
            state = self.hass.states.get(source)
            record["status"] = (
                "already_at_target" if state is record.get("before") else "confirmed"
            )
            record["confirmation"] = "source_state"
        if not self._command_sources or any(
            record["status"] in self._ACTIVE_COMMAND_STATES
            for record in self._command_sources.values()
        ):
            return False
        successful = all(record["status"] in {"confirmed", "already_at_target"}
                         for record in self._command_sources.values())
        # Both groups and aliases resume live source templates on completion.
        # Avoid recursive rendering here; callers render after checking.
        self._clear_command_display()
        if self._command_deadline_cancel is not None:
            self._command_deadline_cancel()
            self._command_deadline_cancel = None
        if self._response_refresh_cancel is not None:
            self._response_refresh_cancel()
            self._response_refresh_cancel = None
        return successful

    def _group_source_matches(self, source, command, data):
        state = self.hass.states.get(source)
        expected = "off" if command == "turn_off" or data.get("brightness") == 0 else "on"
        if state is None or state.state != expected:
            return False
        if expected == "off":
            return True
        data = dict(data)
        modes = state.attributes.get("supported_color_modes", [])
        if (isinstance(modes, (list, tuple, set)) and modes
                and all(mode == "onoff" for mode in modes)):
            # Core strips brightness/colour for on/off-only group members.
            # Once power matches, resending those unsupported values is futile.
            return True
        if ("color_temp_kelvin" in data and isinstance(modes, (list, tuple, set))
                and "color_temp" not in modes):
            kelvin = data.pop("color_temp_kelvin")
            if "rgbww" in modes:
                minimum = state.attributes.get("min_color_temp_kelvin")
                maximum = state.attributes.get("max_color_temp_kelvin")
                # Core uses the entity's white-channel calibration even when
                # no CT mode exists; those bounds are then absent from state.
                component = self.hass.data.get(LIGHT_COMPONENT)
                physical = component.get_entity(source) if component is not None else None
                if physical is not None:
                    minimum = physical.min_color_temp_kelvin
                    maximum = physical.max_color_temp_kelvin
                if (not isinstance(minimum, (int, float))
                        or not isinstance(maximum, (int, float))
                        or not 0 < minimum < maximum):
                    return False
                data["rgbww_color"] = color_util.color_temperature_to_rgbww(
                    kelvin, data.get("brightness", state.attributes.get("brightness", 255)),
                    minimum, maximum,
                )
            elif set(modes) & {"hs", "rgb", "rgbw", "xy"}:
                # HA emulates Kelvin via HS, then translates to the bulb's
                # native mode. Match that representation, not a Kelvin state
                # attribute which RGB-only bulbs never report.
                hs = color_util.color_temperature_to_hs(kelvin)
                if "hs" in modes:
                    data["hs_color"] = hs
                elif "rgb" in modes:
                    data["rgb_color"] = color_util.color_hs_to_RGB(*hs)
                elif "rgbw" in modes:
                    data["rgbw_color"] = color_util.color_rgb_to_rgbw(
                        *color_util.color_hs_to_RGB(*hs)
                    )
                else:
                    data["xy_color"] = color_util.color_hs_to_xy(*hs)
            else:
                return False
        for name, tolerance in (("brightness", 2), ("color_temp_kelvin", 50)):
            if name in data:
                actual = state.attributes.get(name)
                if not isinstance(actual, (int, float)) or isinstance(actual, bool):
                    return False
                if not math.isfinite(actual) or abs(actual - data[name]) > tolerance:
                    return False
        for name in ("hs_color", "xy_color", "rgb_color", "rgbw_color", "rgbww_color"):
            if name in data:
                actual = state.attributes.get(name)
                if not isinstance(actual, (list, tuple)) or len(actual) != len(data[name]):
                    return False
                tolerance = 0.005 if name == "xy_color" else 2
                for index, (a, b) in enumerate(zip(actual, data[name])):
                    if isinstance(a, bool) or not isinstance(a, (int, float)) or not math.isfinite(a):
                        return False
                    distance = abs(a - b)
                    if name == "hs_color" and index == 0:
                        distance = abs((a - b + 180) % 360 - 180)
                    if distance > tolerance:
                        return False
        return True

    def _schedule_group_refresh(self, revision, retries):
        if revision != self._group_revision or self._group_removed:
            return
        records = [
            record for record in self._command_sources.values()
            if record["status"] in {"dispatching", "awaiting_feedback"}
        ]
        if not records:
            return
        now = self.hass.loop.time()
        due = min(
            (record["feedback_deadline"] if record["status"] == "dispatching"
             else min(record["next_check"], record["feedback_deadline"]))
            for record in records
        )
        if self._response_refresh_cancel is not None:
            self._response_refresh_cancel()

        @callback
        def refresh(_now):
            self._response_refresh_cancel = None
            if revision == self._group_revision and not self._group_removed:
                self._group_refresh_task = self.hass.async_create_task(
                    self._async_refresh_group(revision, retries)
                )

        self._response_refresh_cancel = async_call_later(
            self.hass, max(0, due - now), refresh
        )

    async def _async_refresh_group(self, revision, retries):
        if revision != self._group_revision or self._group_removed:
            return
        if self.hass.loop.time() >= self._command_deadline:
            self._expire_command(revision)
            return
        self._acknowledge_group_target()
        command, _method, data = self._group_target
        now = self.hass.loop.time()
        records = self._command_sources
        pending = [
            source for source, record in records.items()
            if record["status"] == "awaiting_feedback"
            and now >= min(record["next_check"], record["feedback_deadline"])
        ]

        async def refresh(source):
            # Push integrations (including MQTT) have no generic read-back API.
            component = self.hass.data.get(LIGHT_COMPONENT)
            physical = component.get_entity(source) if component is not None else None
            if physical is None or not physical.should_poll:
                return
            try:
                async with asyncio.timeout(min(
                    self._dispatch_timeout,
                    max(0, records[source]["feedback_deadline"] - self.hass.loop.time()),
                )):
                    await async_update_entity(self.hass, source)
            except Exception:
                if (revision == self._group_revision
                        and records[source]["status"] in self._ACTIVE_COMMAND_STATES):
                    records[source]["error"] = "refresh_failed"

        await asyncio.gather(*(refresh(source) for source in pending))
        if revision != self._group_revision or self._group_removed:
            return
        self._acknowledge_group_target()
        for source in pending:
            record = records[source]
            if record["status"] != "awaiting_feedback":
                continue
            state = self.hass.states.get(source)
            if self._ignore_unresponsive and (
                state is None or state.state in {"unknown", "unavailable"}
            ):
                record["status"] = "failed"
                record["error"] = "source_unavailable"
            elif self.hass.loop.time() >= record["feedback_deadline"]:
                record["status"] = "timed_out"
                record["error"] = "feedback_timeout"
            elif (retries and self._response_delay > 0 and self._stock_revision == revision
                  and record["attempts"] <= self._response_retries):
                record["status"] = "queued"
                self._queue_source_command(source, revision, command, data)
            else:
                # Exhausted retransmissions still await source feedback until
                # their fixed deadline. Polling never extends that deadline.
                record["next_check"] = record["feedback_deadline"]
        self._finish_command_check()
        self._schedule_group_refresh(revision, retries)
        self._apply_templates()

    def _sync_untemplated_source_state(self):
        """Keep legacy aliases without state helpers tied to known sources."""
        sources = self._group_sources()
        if not sources or self.hass is None:
            return
        states = [self.hass.states.get(source) for source in sources]
        known = [state for state in states if state and state.state in {"on", "off"}]
        if not self._availability_template:
            self._attr_available = bool(known) and (
                self._ignore_unresponsive or len(known) == len(states)
            )
        if not known or self._group_authoritative:
            return
        if not self._value_template and not ({"state", "is_on"} & self._native_templates.keys()):
            self._attr_is_on = all(state.state == "on" for state in known)
        active = [state for state in known if state.state == "on"]
        if not active:
            return
        if "brightness" not in self._native_templates:
            levels = [_as_brightness(state.attributes.get("brightness")) for state in active]
            levels = [level for level in levels if level is not None]
            if levels:
                self._attr_brightness = round(sum(levels) / len(levels))
        # Preserve custom helpers; use a valid native representation as a
        # fallback only when no helper owns that colour property.
        color_properties = {
            "color_temp_kelvin": ColorMode.COLOR_TEMP, "hs_color": ColorMode.HS,
            "xy_color": ColorMode.XY, "rgb_color": ColorMode.RGB,
            "rgbw_color": ColorMode.RGBW, "rgbww_color": ColorMode.RGBWW,
        }
        if not (set(color_properties) | {"color_mode", "color_temp"}) & self._native_templates.keys():
            for name, mode in color_properties.items():
                if mode not in self._attr_supported_color_modes:
                    continue
                for state in active:
                    if name not in state.attributes:
                        continue
                    try:
                        self._apply_native_template_value(name, state.attributes[name])
                    except (ValueError, TypeError, vol.Invalid):
                        continue
                    break
        self._reconcile_color_capabilities()

    def _apply_templates(self):
        """Observe source feedback independently of the display lease."""
        self._acknowledge_group_target()
        self._sync_untemplated_source_state()
        if not self._group_authoritative:
            super()._apply_templates()
            self._update_attributes()
            self._schedule_state_update()
            return
        # Continue refreshing availability, capabilities and diagnostics while
        # retaining the group's requested power, level and color values.
        native = self._native_templates
        value = self._value_template
        self._value_template = None
        self._native_templates = {
            key: template for key, template in native.items()
            if key not in {"state", "is_on", "brightness", "color_mode",
                           "hs_color", "xy_color", "rgb_color", "rgbw_color",
                           "rgbww_color", "color_temp", "color_temp_kelvin"}
        }
        try:
            super()._apply_templates()
        finally:
            self._native_templates = native
            self._value_template = value
        self._update_attributes()
        self._schedule_state_update()

    async def async_will_remove_from_hass(self) -> None:
        self._group_removed = True
        self._group_revision += 1
        self._cancel_group_refresh()
        for name in ("_command_deadline_cancel", "_optimistic_cancel"):
            cancel = getattr(self, name)
            if cancel is not None:
                cancel()
                setattr(self, name, None)
        self._source_pending_commands.clear()
        tasks = list(self._source_command_tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._source_command_tasks.clear()
        if self._group_refresh_task is not None:
            await asyncio.gather(self._group_refresh_task, return_exceptions=True)
        if self._response_refresh_cancel is not None:
            self._response_refresh_cancel()
            self._response_refresh_cancel = None
        await super().async_will_remove_from_hass()

    def _apply_native_template_value(self, name: str, value) -> bool:
        aliases = {
            "color_temp": "color_temp_kelvin",
        }
        name = aliases.get(name, name)
        # Lights omit level/color attributes while off, unavailable, or using
        # another color mode. Missing readings must not reject otherwise valid
        # source templates or erase the last usable target.
        if name in {
            "brightness", "color_mode", "color_temp_kelvin", "hs_color",
            "xy_color", "rgb_color", "rgbw_color", "rgbww_color",
        } and (
            value is None
            or isinstance(value, str)
            and value.strip().lower() in {"", "none", "unknown", "unavailable"}
        ):
            return False
        if name in {"effect", "effects", "effect_list"}:
            raise ValueError("Matter-compatible lights do not support effects")
        if name == "supported_color_modes":
            if not isinstance(value, (list, tuple, set)):
                raise ValueError("supported_color_modes must render a list")
            try:
                value = {ColorMode(str(item)) for item in value}
            except ValueError as err:
                raise ValueError("supported_color_modes contains an invalid mode") from err
            value.discard(ColorMode.UNKNOWN)
            if not value:
                value = {ColorMode.ONOFF}
            if ColorMode.ONOFF in value and len(value) > 1:
                value.discard(ColorMode.ONOFF)
            # ``matter_light_type`` is the maximum Matter contract selected in
            # the editor, not a reason to invent capabilities at runtime. In
            # particular, an extended-color source can temporarily (or
            # permanently) render ``["onoff"]``. ``ONOFF`` is a valid subset
            # for every light contract; intersecting it with the configured
            # color modes used to produce an empty set and restored the full
            # extended-color profile. Matter Bridge then saw RGB support even
            # though Home Assistant was presenting an on/off control.
            allowed_modes = self._matter_color_modes | {ColorMode.ONOFF}
            if self._matter_color_modes != {ColorMode.ONOFF}:
                allowed_modes.add(ColorMode.BRIGHTNESS)
            value &= allowed_modes
            # Color modes already imply level control in Home Assistant.
            # BRIGHTNESS is valid alone, but not alongside a color mode.
            if ColorMode.BRIGHTNESS in value and len(value) > 1:
                value.discard(ColorMode.BRIGHTNESS)
            if not value:
                value = {ColorMode.ONOFF}
            current = self._attr_supported_color_modes
            if current == value:
                return False
            self._attr_supported_color_modes = value
            return True
        if name == "color_mode":
            try:
                value = ColorMode(str(value))
            except ValueError as err:
                raise ValueError(f"Invalid light color mode: {value}") from err
            if value not in self._attr_supported_color_modes and value != ColorMode.ONOFF:
                raise ValueError(f"Unsupported light color mode: {value}")
        elif name == "brightness":
            value = _as_brightness(value)
            if value is None:
                raise ValueError("brightness must be between 0 and 255")
        elif name == "hs_color":
            value = _as_hs_color(value)
            if value is None:
                raise ValueError("hs_color must be a valid hue/saturation pair")
        elif name == "xy_color":
            value = _as_color_tuple(value, 2, 1)
            if value is None:
                raise ValueError("xy_color must be a pair between 0 and 1")
        elif name in {"rgb_color", "rgbw_color", "rgbww_color"}:
            lengths = {"rgb_color": 3, "rgbw_color": 4, "rgbww_color": 5}
            value = _as_color_tuple(value, lengths[name], 255)
            if value is None:
                raise ValueError(f"{name} must contain valid 0..255 channels")
        elif name == "color_temp_kelvin":
            value = _as_color_temp_kelvin(value, None)
            if value is None:
                raise ValueError("color_temp_kelvin must be a positive number")
        elif name in {"min_color_temp_kelvin", "max_color_temp_kelvin"}:
            if isinstance(value, bool):
                raise ValueError(f"{name} must be an integer")
            try:
                value = int(value)
            except (TypeError, ValueError, OverflowError) as err:
                raise ValueError(f"{name} must be an integer") from err
            if not 1000 <= value <= 40000:
                raise ValueError(f"{name} must be between 1000 and 40000")
        elif name in {"state", "is_on"}:
            old_state = self._attr_is_on
            self.set_state(value)
            return old_state != self._attr_is_on
        return super()._apply_native_template_value(name, value)

    def _native_templates_applied(self) -> None:
        if self._attr_min_color_temp_kelvin > self._attr_max_color_temp_kelvin:
            self._attr_min_color_temp_kelvin, self._attr_max_color_temp_kelvin = (
                self._attr_max_color_temp_kelvin,
                self._attr_min_color_temp_kelvin,
            )
        if self._attr_color_mode not in self._attr_supported_color_modes:
            self._attr_color_mode = next(
                (
                    mode
                    for mode in (
                        ColorMode.COLOR_TEMP,
                        ColorMode.HS,
                        ColorMode.XY,
                        ColorMode.RGB,
                        ColorMode.RGBW,
                        ColorMode.RGBWW,
                        ColorMode.BRIGHTNESS,
                        ColorMode.ONOFF,
                    )
                    if mode in self._attr_supported_color_modes
                ),
                ColorMode.ONOFF,
            )
        if self._attr_brightness is not None:
            self._attr_brightness = _as_brightness(self._attr_brightness)
        if self._attr_hs_color is not None:
            self._attr_hs_color = _as_hs_color(self._attr_hs_color)
        if self._attr_color_temp_kelvin is not None:
            self._attr_color_temp_kelvin = max(
                self._attr_min_color_temp_kelvin,
                min(self._attr_max_color_temp_kelvin, self._attr_color_temp_kelvin),
            )
        self._reconcile_color_capabilities()
        self._attr_effect = None
        self._attr_effect_list = None
        self._attr_supported_features = LightEntityFeature(0)

    def _reconcile_color_capabilities(self) -> None:
        """Discard colour values that the current Matter profile cannot expose."""
        # Source helpers retain safe fallback values for properties absent on
        # a source.  Those fallbacks must never advertise a Matter colour
        # cluster when the effective light is on/off-only (or less capable
        # than its previous profile).
        if self._attr_supported_color_modes == {ColorMode.ONOFF}:
            self._attr_brightness = None
        supported_color_attributes = {
            ColorMode.HS: "_attr_hs_color",
            ColorMode.XY: "_attr_xy_color",
            ColorMode.RGB: "_attr_rgb_color",
            ColorMode.RGBW: "_attr_rgbw_color",
            ColorMode.RGBWW: "_attr_rgbww_color",
            ColorMode.COLOR_TEMP: "_attr_color_temp_kelvin",
        }
        for mode, attribute_name in supported_color_attributes.items():
            if mode not in self._attr_supported_color_modes:
                setattr(self, attribute_name, None)

    def set_state(self, value) -> None:
        self._attr_is_on = self._template_to_bool(value)
        self._update_attributes()
