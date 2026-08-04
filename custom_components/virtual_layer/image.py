"""Virtual image entity support."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

import aiofiles
import voluptuous as vol
import homeassistant.helpers.config_validation as cv
from homeassistant.components.image import (
    DOMAIN as PLATFORM_DOMAIN,
    ImageEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.config_validation import PLATFORM_SCHEMA
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.util import dt as dt_util

from . import get_entity_configs
from .const import *
from .entity import VirtualEntity, virtual_schema


_LOGGER = logging.getLogger(__name__)

DEPENDENCIES = [COMPONENT_DOMAIN]

CONF_CONTENT_TYPE = "content_type"
CONF_IMAGE_PATH = "image_path"
CONF_IMAGE_URL = "image_url"
CONF_SOURCE_ENTITY = "source_entity"

DEFAULT_IMAGE_VALUE = "unknown"


def _image_entity_id(value: str) -> str:
    """Validate an image entity used as an alias source."""
    entity_id = cv.entity_id(value)
    if not entity_id.startswith(f"{PLATFORM_DOMAIN}."):
        raise vol.Invalid("source_entity must be an image entity")
    return entity_id


BASE_SCHEMA = virtual_schema(DEFAULT_IMAGE_VALUE, {
    vol.Optional(CONF_CONTENT_TYPE): cv.string,
    vol.Optional(CONF_IMAGE_PATH): cv.string,
    vol.Optional(CONF_IMAGE_URL): cv.url,
    vol.Optional(CONF_SOURCE_ENTITY): _image_entity_id,
})

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(BASE_SCHEMA)
IMAGE_SCHEMA = vol.Schema(BASE_SCHEMA, extra=vol.ALLOW_EXTRA)
ENTITY_SCHEMA = IMAGE_SCHEMA


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
    """Create virtual image entities from the UI config entry."""
    entities = [
        VirtualImage(IMAGE_SCHEMA(entity), hass, False)
        for entity in get_entity_configs(hass, entry.data[ATTR_GROUP_NAME], PLATFORM_DOMAIN)
    ]
    async_add_entities(entities)


class VirtualImage(VirtualEntity, ImageEntity):
    """An image entity backed by a file, URL, or another image entity."""

    def __init__(self, config: dict, hass: HomeAssistant, old_style: bool):
        ImageEntity.__init__(self, hass)
        VirtualEntity.__init__(self, config, PLATFORM_DOMAIN, old_style)

        self._image_path = config.get(CONF_IMAGE_PATH)
        self._image_url = config.get(CONF_IMAGE_URL)
        self._source_entity = config.get(CONF_SOURCE_ENTITY)
        self._attr_content_type = config.get(
            CONF_CONTENT_TYPE,
            "image/jpeg",
        )
        self._attr_image_last_updated: datetime | None = None
        if self._image_url:
            self._attr_image_url = self._image_url

        _LOGGER.info("VirtualImage: %s created", self.name)

    @property
    def image_last_updated(self) -> datetime | None:
        """Return the latest successful image fetch time."""
        return self._attr_image_last_updated

    @property
    def state_attributes(self):
        data = dict(super().state_attributes or {})
        data.update(self._attr_extra_state_attributes or {})
        return data

    def _create_state(self, config):
        super()._create_state(config)
        self._attr_image_last_updated = None

    def _restore_state(self, state, config):
        super()._restore_state(state, config)
        try:
            self._attr_image_last_updated = dt_util.parse_datetime(state.state)
        except (TypeError, ValueError):
            self._attr_image_last_updated = None

    def _update_attributes(self):
        super()._update_attributes()
        self._attr_extra_state_attributes.update({
            "content_type": self._attr_content_type,
            "source_entity": self._source_entity,
        })

    def _source_image(self) -> Any | None:
        if not self._source_entity or self.hass is None:
            return None
        component = self.hass.data.get(PLATFORM_DOMAIN)
        get_entity = getattr(component, "get_entity", None)
        if get_entity is None:
            return None
        source = get_entity(self._source_entity)
        return None if source is self else source

    def _mark_updated(self) -> None:
        self._attr_image_last_updated = dt_util.utcnow()
        self._update_attributes()
        self.async_write_ha_state()

    def image(self) -> bytes | None:
        """Return bytes for the synchronous ImageEntity API."""
        if not self._image_path:
            return None
        try:
            with open(self._image_path, "rb") as image_file:
                return image_file.read()
        except OSError:
            _LOGGER.warning("Unable to read virtual image %s", self._image_path)
            return None

    async def async_image(self) -> bytes | None:
        """Return bytes from the configured source."""
        source = self._source_image()
        if source is not None:
            try:
                image = await source.async_image()
            except (AttributeError, OSError, ValueError) as err:
                _LOGGER.warning(
                    "Unable to get virtual image from %s: %s",
                    self._source_entity,
                    err,
                )
                return None
            if image is not None:
                self._mark_updated()
            return image

        if self._image_path:
            try:
                async with aiofiles.open(self._image_path, "rb") as image_file:
                    image = await image_file.read()
            except OSError:
                _LOGGER.warning("Unable to read virtual image %s", self._image_path)
                return None
            self._mark_updated()
            return image

        if self._image_url:
            image = await ImageEntity.async_image(self)
            if image is not None:
                self._mark_updated()
            return image
        return None

    def set_state(self, value) -> None:
        """Keep generic/template state updates harmless for image entities."""
        self.async_schedule_update_ha_state()
