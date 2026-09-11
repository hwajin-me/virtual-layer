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
from homeassistant.core import HomeAssistant
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
})

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(BASE_SCHEMA)

LIGHT_SCHEMA = vol.Schema(BASE_SCHEMA)


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
        self._response_pending = False
        self._group_command_lock = asyncio.Lock()
        self._group_authoritative = False
        self._group_revision = 0
        self._group_dispatching = False
        self._group_refresh_task = None
        self._group_retry_sources = None
        self._group_target = None
        self._group_removed = False
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

    @property
    def brightness(self) -> int | None:
        return self._attr_brightness if self._attr_is_on else None

    @property
    def color_mode(self) -> ColorMode | None:
        return self._attr_color_mode if self._attr_is_on else None

    @property
    def hs_color(self) -> tuple[float, float] | None:
        return self._attr_hs_color if self._attr_is_on else None

    @property
    def xy_color(self) -> tuple[float, float] | None:
        return self._attr_xy_color if self._attr_is_on else None

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        return self._attr_rgb_color if self._attr_is_on else None

    @property
    def rgbw_color(self) -> tuple[int, int, int, int] | None:
        return self._attr_rgbw_color if self._attr_is_on else None

    @property
    def rgbww_color(self) -> tuple[int, int, int, int, int] | None:
        return self._attr_rgbww_color if self._attr_is_on else None

    @property
    def color_temp_kelvin(self) -> int | None:
        return self._attr_color_temp_kelvin if self._attr_is_on else None

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

    def _update_attributes(self):
        """Return the state attributes."""
        super()._update_attributes()
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
        self._schedule_source_reconciliation()

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
        self._schedule_source_reconciliation()

    def _preserve_optimistic_command_state(self, command, args, kwargs) -> bool:
        """Keep a command value visible until slow source bulbs can report it."""
        return bool(
            self._source_entities
            and command in {"turn_on", "turn_off"}
            and self._response_delay > 0
        )

    def _group_sources(self):
        return list(dict.fromkeys(
            source for source in self._source_entities
            if isinstance(source, str) and source.startswith("light.")
            and source != self.entity_id
        ))

    def _command_action_spec(self, command):
        spec = super()._command_action_spec(command)
        sources = self._group_sources()
        if len(sources) < 2 or command not in {"turn_on", "turn_off"}:
            return spec
        # Upgrade only the exact stock pass-through action. Independently
        # configured scripts (including optimistic:false) remain authoritative.
        stock = [{"action": f"light.{command}",
                  "target": {ATTR_ENTITY_ID: sources},
                  "data": "{{ command_data }}"}]
        if spec is not None and spec != (stock, True):
            return spec
        return ([{"parallel": [
            {"sequence": [{"action": f"light.{command}",
                           "target": {ATTR_ENTITY_ID: source},
                           "data": "{{ command_data }}",
                           "continue_on_error": True}]}
            for source in (self._group_retry_sources if self._group_retry_sources is not None else sources)
        ]}], True)

    def _has_stock_group_action(self, command):
        spec = super()._command_action_spec(command)
        return spec is None or spec == ([{
            "action": f"light.{command}",
            "target": {ATTR_ENTITY_ID: self._group_sources()},
            "data": "{{ command_data }}",
        }], True)

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

    async def _async_run_command_action(self, command, method, args, kwargs):
        if len(self._group_sources()) < 2 or command not in {"turn_on", "turn_off"}:
            return await super()._async_run_command_action(command, method, args, kwargs)
        # A callback may invoke the opposite command on this same group. Do
        # not wait for a lock already held by its own action chain.
        if self._group_removed or any(
            entity == id(self) for entity, _command in _COMMAND_ACTION_CHAIN.get()
        ):
            return False
        self._validate_command_action(command, args, kwargs)
        self._group_revision += 1
        revision = self._group_revision
        self._cancel_group_refresh()
        async with self._group_command_lock:
            if self._group_removed:
                return False
            spec = self._command_action_spec(command)
            if spec is not None and not spec[1]:
                self._group_authoritative = False
                self._group_target = None
                self._response_pending = False
                return await super()._async_run_command_action(command, method, args, kwargs)
            # Validate and publish the virtual target before dispatch. Source
            # reports arriving while actions run cannot replace this target.
            self._group_dispatching = True
            try:
                await method(self, *args, **kwargs)
                self._group_authoritative = True
                if self._has_stock_group_action(command):
                    try:
                        async with asyncio.timeout(10):
                            await super()._async_run_command_action(command, method, args, kwargs)
                    except TimeoutError:
                        _LOGGER.debug("Grouped light command timed out for %s", self.entity_id)
                else:
                    await super()._async_run_command_action(command, method, args, kwargs)
            finally:
                self._group_dispatching = False
            if revision == self._group_revision and self._has_stock_group_action(command):
                data = self._command_service_data(command, method, args, kwargs)
                self._group_target = (command, method, copy.deepcopy(data))
                self._schedule_group_refresh(revision, self._response_retries)
            return False  # The native method has already published the target.

    def _cancel_group_refresh(self):
        if self._response_refresh_cancel is not None:
            self._response_refresh_cancel()
            self._response_refresh_cancel = None
        if self._group_refresh_task is not None:
            self._group_refresh_task.cancel()

    def _group_source_matches(self, source, command, data):
        state = self.hass.states.get(source)
        expected = "off" if command == "turn_off" or data.get("brightness") == 0 else "on"
        if state is None or state.state != expected:
            return False
        if expected == "off":
            return True
        data = dict(data)
        modes = state.attributes.get("supported_color_modes", [])
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
                if any(not isinstance(a, (int, float)) or not math.isfinite(a)
                       or abs(a - b) > tolerance for a, b in zip(actual, data[name])):
                    return False
        return True

    def _schedule_group_refresh(self, revision, retries):
        if self._response_delay <= 0 or self._group_removed:
            return
        transition = self._group_target[2].get("transition", 0)
        delay = max(self._response_delay, float(transition) + self._response_delay)

        def refresh(_now):
            self._response_refresh_cancel = None
            if revision == self._group_revision and not self._group_removed:
                self._group_refresh_task = self.hass.async_create_task(
                    self._async_refresh_group(revision, retries)
                )

        self._response_refresh_cancel = async_call_later(self.hass, delay, refresh)

    async def _async_refresh_group(self, revision, retries):
        async with self._group_command_lock:
            if revision != self._group_revision or self._group_removed:
                return
            command, method, data = self._group_target
            pending = [source for source in self._group_sources()
                       if not self._group_source_matches(source, command, data)]

            async def refresh(source):
                try:
                    async with asyncio.timeout(10):
                        await async_update_entity(self.hass, source)
                except Exception:
                    _LOGGER.debug("Unable to refresh grouped light %s", source)

            await asyncio.gather(*(refresh(source) for source in pending))
            if revision != self._group_revision or self._group_removed:
                return
            pending = [source for source in pending
                       if not self._group_source_matches(source, command, data)]
            if retries and pending:
                self._group_retry_sources = [
                    source for source in pending
                    if not self._ignore_unresponsive
                    or ((state := self.hass.states.get(source)) is not None
                        and state.state not in {"unknown", "unavailable"})
                ]
                try:
                    if self._group_retry_sources:
                        async with asyncio.timeout(10):
                            await super()._async_run_command_action(command, method, (), data)
                except TimeoutError:
                    _LOGGER.debug("Grouped light retry timed out for %s", self.entity_id)
                finally:
                    self._group_retry_sources = None
            self._response_pending = False
            self._apply_templates()
            if pending and retries and revision == self._group_revision:
                self._schedule_group_refresh(revision, retries - 1)

    def _schedule_source_reconciliation(self) -> None:
        """Refresh a composite light after slow or sleeping bulbs have replied."""
        if self._group_dispatching or not self._source_entities or self._response_delay <= 0:
            return
        if self._response_refresh_cancel is not None:
            self._response_refresh_cancel()
        self._response_pending = True
        attempts = 0

        def _refresh(_now) -> None:
            nonlocal attempts
            self._response_pending = False
            self._apply_templates()
            attempts += 1
            if attempts <= self._response_retries:
                self._response_refresh_cancel = async_call_later(
                    self.hass, self._response_delay, _refresh
                )
            else:
                self._response_refresh_cancel = None

        self._response_refresh_cancel = async_call_later(
            self.hass, self._response_delay, _refresh
        )

    def _apply_templates(self):
        """Defer source events as well as immediate post-command rendering."""
        if self._response_pending:
            return
        if not self._group_authoritative:
            super()._apply_templates()
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

    async def async_will_remove_from_hass(self) -> None:
        self._group_removed = True
        self._group_revision += 1
        self._cancel_group_refresh()
        if self._group_refresh_task is not None:
            await asyncio.gather(self._group_refresh_task, return_exceptions=True)
        self._response_pending = False
        if self._response_refresh_cancel is not None:
            self._response_refresh_cancel()
            self._response_refresh_cancel = None
        await super().async_will_remove_from_hass()

    def _apply_native_template_value(self, name: str, value) -> bool:
        aliases = {
            "color_temp": "color_temp_kelvin",
        }
        name = aliases.get(name, name)
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
        self._attr_effect = None
        self._attr_effect_list = None
        self._attr_supported_features = LightEntityFeature(0)

    def set_state(self, value) -> None:
        self._attr_is_on = self._template_to_bool(value)
        self._update_attributes()
