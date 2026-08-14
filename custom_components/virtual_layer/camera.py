"""
This component provides support for a virtual camera entity.

"""
from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Callable
from contextvars import ContextVar

import aiofiles
import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from aiohttp import ClientError
from homeassistant.components.camera import (
    DOMAIN as PLATFORM_DOMAIN,
)
from homeassistant.components.camera import (
    Camera,
    CameraEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.config_validation import PLATFORM_SCHEMA
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import get_entity_configs
from .const import *
from .entity import (
    MAX_LOCAL_MEDIA_BYTES,
    VirtualEntity,
    allowed_local_path,
    virtual_schema,
)

_LOGGER = logging.getLogger(__name__)
_CAMERA_IMAGE_ALIAS_CHAIN: ContextVar[frozenset[int]] = ContextVar(
    "virtual_layer_camera_image_alias_chain",
    default=frozenset(),
)
_CAMERA_STREAM_ALIAS_CHAIN: ContextVar[frozenset[int]] = ContextVar(
    "virtual_layer_camera_stream_alias_chain",
    default=frozenset(),
)

DEPENDENCIES = [COMPONENT_DOMAIN]

CONF_BRAND = "brand"
CONF_IMAGE_PATH = "image_path"
CONF_IS_ON = "is_on"
CONF_IS_RECORDING = "is_recording"
CONF_IS_STREAMING = "is_streaming"
CONF_MODEL = "model"
CONF_MOTION_DETECTION = "motion_detection"
CONF_SOURCE_ENTITY = "source_entity"
CONF_STREAM_SOURCE = "stream_source"

DEFAULT_CAMERA_VALUE = "on"


def _camera_entity_id(value: str) -> str:
    """Validate a camera entity id used as an alias source."""
    entity_id = cv.entity_id(value)
    if not entity_id.startswith(f"{PLATFORM_DOMAIN}."):
        raise vol.Invalid("source_entity must be a camera entity")
    return entity_id


BASE_SCHEMA = virtual_schema(DEFAULT_CAMERA_VALUE, {
    vol.Optional(CONF_BRAND): cv.string,
    vol.Optional(CONF_IMAGE_PATH): cv.string,
    vol.Optional(CONF_IS_RECORDING, default=False): cv.boolean,
    vol.Optional(CONF_IS_STREAMING, default=False): cv.boolean,
    vol.Optional(CONF_MODEL): cv.string,
    vol.Optional(CONF_MOTION_DETECTION, default=False): cv.boolean,
    vol.Optional(CONF_SOURCE_ENTITY): _camera_entity_id,
    vol.Optional(CONF_STREAM_SOURCE): cv.string,
})

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(BASE_SCHEMA)
CAMERA_SCHEMA = vol.Schema(BASE_SCHEMA)


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
        entities.append(VirtualCamera(CAMERA_SCHEMA(entity), False))
    async_add_entities(entities)


class VirtualCamera(VirtualEntity, Camera):
    """Representation of a virtual camera."""

    def __init__(self, config, old_style: bool):
        Camera.__init__(self)
        VirtualEntity.__init__(self, config, PLATFORM_DOMAIN, old_style)

        self._attr_brand = config.get(CONF_BRAND)
        self._attr_model = config.get(CONF_MODEL)
        self._attr_supported_features = CameraEntityFeature.ON_OFF
        if config.get(CONF_STREAM_SOURCE) or config.get(CONF_SOURCE_ENTITY):
            self._attr_supported_features |= CameraEntityFeature.STREAM

        self._image_path = config.get(CONF_IMAGE_PATH)
        self._source_entity = config.get(CONF_SOURCE_ENTITY)
        self._stream_source = config.get(CONF_STREAM_SOURCE)

        _LOGGER.debug(f"VirtualCamera: {self.name} created")

    def _create_state(self, config):
        super()._create_state(config)
        self._attr_is_on = config.get(CONF_INITIAL_VALUE).lower() == STATE_ON
        self._attr_is_recording = config.get(CONF_IS_RECORDING)
        self._attr_is_streaming = config.get(CONF_IS_STREAMING)
        self._attr_motion_detection_enabled = config.get(CONF_MOTION_DETECTION)

    def _restore_state(self, state, config):
        super()._restore_state(state, config)
        configured_is_on = config.get(CONF_INITIAL_VALUE).lower() == STATE_ON
        try:
            self._attr_is_on = cv.boolean(
                state.attributes.get(CONF_IS_ON, configured_is_on)
            )
        except vol.Invalid:
            self._attr_is_on = configured_is_on
        for attribute_name, config_name in (
            ("_attr_is_recording", CONF_IS_RECORDING),
            ("_attr_is_streaming", CONF_IS_STREAMING),
            ("_attr_motion_detection_enabled", CONF_MOTION_DETECTION),
        ):
            fallback = config.get(config_name, False)
            try:
                value = cv.boolean(state.attributes.get(config_name, fallback))
            except vol.Invalid:
                value = fallback
            setattr(self, attribute_name, value)

    @property
    def state_attributes(self):
        data = dict(super().state_attributes or {})
        data.update(self._attr_extra_state_attributes or {})
        data[CONF_IS_ON] = self._attr_is_on
        return data

    async def async_camera_image(
        self,
        width: int | None = None,
        height: int | None = None,
    ) -> bytes | None:
        if not self._attr_is_on:
            return None
        source = self._source_camera()
        if source is not None and not self._image_path:
            marker = id(self)
            active_aliases = _CAMERA_IMAGE_ALIAS_CHAIN.get()
            if marker in active_aliases:
                return None
            token = _CAMERA_IMAGE_ALIAS_CHAIN.set(active_aliases | {marker})
            try:
                return await source.async_camera_image(width=width, height=height)
            except (
                asyncio.TimeoutError,
                AttributeError,
                ClientError,
                HomeAssistantError,
                OSError,
                ValueError,
            ) as err:
                _LOGGER.warning(
                    "Unable to get virtual camera image from %s: %s",
                    self._source_entity,
                    err,
                )
                return None
            finally:
                _CAMERA_IMAGE_ALIAS_CHAIN.reset(token)
        if not self._image_path:
            return None
        image_path = await self.hass.async_add_executor_job(
            allowed_local_path,
            self.hass,
            self._image_path,
        )
        if image_path is None:
            _LOGGER.warning("Blocked disallowed image path for %s", self.entity_id)
            return None
        try:
            async with aiofiles.open(image_path, "rb") as image_file:
                image = await image_file.read(MAX_LOCAL_MEDIA_BYTES + 1)
        except OSError:
            _LOGGER.warning("Unable to read image for %s", self.entity_id)
            return None
        if len(image) > MAX_LOCAL_MEDIA_BYTES:
            _LOGGER.warning("Local image is too large for %s", self.entity_id)
            return None
        return image

    async def stream_source(self) -> str | None:
        source = self._source_camera()
        if source is not None and not self._stream_source:
            marker = id(self)
            active_aliases = _CAMERA_STREAM_ALIAS_CHAIN.get()
            if marker in active_aliases:
                return None
            token = _CAMERA_STREAM_ALIAS_CHAIN.set(active_aliases | {marker})
            try:
                return await source.stream_source()
            except (
                asyncio.TimeoutError,
                AttributeError,
                ClientError,
                HomeAssistantError,
                OSError,
                ValueError,
            ) as err:
                _LOGGER.warning(
                    "Unable to get virtual camera stream from %s: %s",
                    self._source_entity,
                    err,
                )
                return None
            finally:
                _CAMERA_STREAM_ALIAS_CHAIN.reset(token)
        return self._stream_source

    def _source_camera(self) -> Camera | None:
        """Return the configured source camera without recursing into self."""
        if not self._source_entity or self.hass is None:
            return None

        component = self.hass.data.get(PLATFORM_DOMAIN)
        get_entity = getattr(component, "get_entity", None)
        if get_entity is None:
            return None

        source = get_entity(self._source_entity)
        return None if source is self else source

    async def async_turn_on(self) -> None:
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self) -> None:
        self._attr_is_on = False
        self.async_write_ha_state()

    async def async_enable_motion_detection(self) -> None:
        self._attr_motion_detection_enabled = True
        self.async_write_ha_state()

    async def async_disable_motion_detection(self) -> None:
        self._attr_motion_detection_enabled = False
        self.async_write_ha_state()

    def _apply_native_template_value(self, name: str, value) -> bool:
        backing_fields = {
            CONF_IMAGE_PATH: "_image_path",
            CONF_SOURCE_ENTITY: "_source_entity",
            CONF_STREAM_SOURCE: "_stream_source",
        }
        if name in backing_fields:
            value = None if value is None or value == "" else str(value).strip()
            if name == CONF_SOURCE_ENTITY and value is not None:
                value = _camera_entity_id(value)
            attribute = backing_fields[name]
            changed = getattr(self, attribute) != value
            setattr(self, attribute, value)
            return changed
        if name in {
            CONF_IS_RECORDING,
            CONF_IS_STREAMING,
            "motion_detection_enabled",
            CONF_MOTION_DETECTION,
        }:
            if name == CONF_MOTION_DETECTION:
                name = "motion_detection_enabled"
            value = value if isinstance(value, bool) else self._template_to_bool(value)
        elif name in {"state", CONF_IS_ON}:
            old_state = self._attr_is_on
            self.set_state(value)
            return old_state != self._attr_is_on
        elif name == "frame_interval":
            if isinstance(value, bool):
                raise ValueError("frame_interval must be a positive number")
            try:
                value = float(value)
            except (TypeError, ValueError, OverflowError) as err:
                raise ValueError("frame_interval must be a positive number") from err
            if not math.isfinite(value) or value <= 0:
                raise ValueError("frame_interval must be a positive number")
        return super()._apply_native_template_value(name, value)

    def _native_templates_applied(self) -> None:
        features = CameraEntityFeature.ON_OFF
        if self._stream_source or self._source_entity:
            features |= CameraEntityFeature.STREAM
        self._attr_supported_features = features

    def set_state(self, value) -> None:
        self._attr_is_on = self._template_to_bool(value)
