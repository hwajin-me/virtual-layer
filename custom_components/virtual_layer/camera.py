"""
This component provides support for a virtual camera entity.

"""
from __future__ import annotations

import logging
import aiofiles
import voluptuous as vol
from collections.abc import Callable

import homeassistant.helpers.config_validation as cv
from homeassistant.components.camera import (
    DOMAIN as PLATFORM_DOMAIN,
    Camera,
    CameraEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.config_validation import PLATFORM_SCHEMA
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import get_entity_configs
from .const import *
from .entity import VirtualEntity, virtual_schema


_LOGGER = logging.getLogger(__name__)

DEPENDENCIES = [COMPONENT_DOMAIN]

CONF_BRAND = "brand"
CONF_IMAGE_PATH = "image_path"
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

        _LOGGER.info(f"VirtualCamera: {self.name} created")

    def _create_state(self, config):
        super()._create_state(config)
        self._attr_is_on = config.get(CONF_INITIAL_VALUE).lower() == STATE_ON
        self._attr_is_recording = config.get(CONF_IS_RECORDING)
        self._attr_is_streaming = config.get(CONF_IS_STREAMING)
        self._attr_motion_detection_enabled = config.get(CONF_MOTION_DETECTION)

    def _restore_state(self, state, config):
        super()._restore_state(state, config)
        self._attr_is_on = state.state.lower() != "off"
        self._attr_is_recording = state.attributes.get(CONF_IS_RECORDING, config.get(CONF_IS_RECORDING))
        self._attr_is_streaming = state.attributes.get(CONF_IS_STREAMING, config.get(CONF_IS_STREAMING))
        self._attr_motion_detection_enabled = state.attributes.get(CONF_MOTION_DETECTION, config.get(CONF_MOTION_DETECTION))

    @property
    def state_attributes(self):
        data = dict(super().state_attributes or {})
        data.update(self._attr_extra_state_attributes or {})
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
            try:
                return await source.async_camera_image(width=width, height=height)
            except (AttributeError, OSError, ValueError) as err:
                _LOGGER.warning(
                    "Unable to get virtual camera image from %s: %s",
                    self._source_entity,
                    err,
                )
                return None
        if not self._image_path:
            return None
        try:
            async with aiofiles.open(self._image_path, "rb") as image_file:
                return await image_file.read()
        except OSError:
            _LOGGER.warning(f"Unable to read virtual camera image {self._image_path}")
            return None

    async def stream_source(self) -> str | None:
        source = self._source_camera()
        if source is not None and not self._stream_source:
            try:
                return await source.stream_source()
            except (AttributeError, OSError, ValueError) as err:
                _LOGGER.warning(
                    "Unable to get virtual camera stream from %s: %s",
                    self._source_entity,
                    err,
                )
                return None
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

    def set_state(self, value) -> None:
        self._attr_is_on = str(value).lower() in ["y", "yes", "t", "true", "on", "1"]
