"""
This component provides support for a virtual light.

"""
from __future__ import annotations

import logging
import math
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
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.config_validation import PLATFORM_SCHEMA
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import get_entity_configs
from .const import *
from .entity import VirtualEntity, nonnegative_int, virtual_schema

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
            value &= self._matter_color_modes
            if not value:
                value = set(self._matter_color_modes)
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
            if value not in self._attr_supported_color_modes:
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
