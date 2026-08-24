"""Virtual image entity support."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import mimetypes
from collections.abc import Callable
from contextvars import ContextVar
from datetime import datetime
from typing import Any

import aiofiles
import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from aiohttp import ClientError
from homeassistant.components.image import (
    DOMAIN as PLATFORM_DOMAIN,
)
from homeassistant.components.image import (
    ImageEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_FRIENDLY_NAME, ATTR_LATITUDE, ATTR_LONGITUDE
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.config_validation import PLATFORM_SCHEMA
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.util import dt as dt_util

from . import get_entity_configs
from .const import *
from .entity import (
    MAX_LOCAL_MEDIA_BYTES,
    VirtualEntity,
    allowed_local_path,
    virtual_schema,
)
from .polygon import load_polygon_zones, render_polygon_map_svg

_LOGGER = logging.getLogger(__name__)
_IMAGE_ALIAS_CHAIN: ContextVar[frozenset[int]] = ContextVar(
    "virtual_layer_image_alias_chain",
    default=frozenset(),
)

DEPENDENCIES = [COMPONENT_DOMAIN]

CONF_CONTENT_TYPE = "content_type"
CONF_IMAGE_PATH = "image_path"
CONF_IMAGE_URL = "image_url"
CONF_SOURCE_ENTITY = "source_entity"
CONF_SVG = "svg"

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
    vol.Optional(CONF_POLYGONAL_ZONE): object,
    vol.Optional(CONF_SOURCE_ENTITY): _image_entity_id,
    vol.Optional(CONF_SVG): cv.string,
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
        self._inline_svg = config.get(CONF_SVG)
        self._polygon_config = config.get(CONF_POLYGONAL_ZONE)
        self._source_entity = config.get(CONF_SOURCE_ENTITY)
        configured_content_type = config.get(CONF_CONTENT_TYPE)
        guessed_content_type = mimetypes.guess_type(
            self._image_path or self._image_url or "",
        )[0]
        self._attr_content_type = configured_content_type or (
            "image/svg+xml"
            if self._inline_svg or self._polygon_config
            else guessed_content_type or "image/jpeg"
        )
        self._attr_image_last_updated: datetime | None = None
        self._image_digest: bytes | None = None
        self._image_refresh_pending = False
        self._tracked_source_image: str | None = None
        self._source_image_remove_listener: Callable[[], None] | None = None
        self._polygon_zones: list[dict[str, Any]] = []
        if self._image_url:
            self._attr_image_url = self._image_url

        _LOGGER.debug("VirtualImage: %s created", self.name)

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
        self._image_digest = None
        self._image_refresh_pending = False
        self._polygon_zones = []

    def _restore_state(self, state, config):
        super()._restore_state(state, config)
        try:
            self._attr_image_last_updated = dt_util.parse_datetime(state.state)
        except (TypeError, ValueError):
            self._attr_image_last_updated = None
        self._image_digest = None
        self._image_refresh_pending = False
        self._polygon_zones = []

    def _update_attributes(self):
        super()._update_attributes()
        self._attr_extra_state_attributes.update({
            "content_type": self._attr_content_type,
            "image_type": "polygon_map" if self._polygon_config else "image",
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

    async def async_added_to_hass(self) -> None:
        """Track image and polygon location sources for cache invalidation."""
        await super().async_added_to_hass()
        source_entities = set(self._source_entities)
        source_entities.discard(self.entity_id)
        if source_entities:
            self._refresh_remove_listeners.append(async_track_state_change_event(
                self.hass,
                source_entities,
                self._async_image_source_changed,
            ))
        self._sync_source_image_listener()

    async def async_will_remove_from_hass(self) -> None:
        """Remove the independently managed aliased-image listener."""
        if self._source_image_remove_listener is not None:
            self._source_image_remove_listener()
            self._source_image_remove_listener = None
        self._tracked_source_image = None
        await super().async_will_remove_from_hass()

    @callback
    def _sync_source_image_listener(self) -> None:
        """Follow a Jinja-selected image source as its entity ID changes."""
        source_entity = self._source_entity
        if source_entity == self.entity_id or source_entity in self._source_entities:
            source_entity = None
        if source_entity == self._tracked_source_image:
            return

        if self._source_image_remove_listener is not None:
            self._source_image_remove_listener()
            self._source_image_remove_listener = None
        self._tracked_source_image = source_entity
        if source_entity:
            self._source_image_remove_listener = async_track_state_change_event(
                self.hass,
                [source_entity],
                self._async_image_source_changed,
            )

    @callback
    def _async_image_source_changed(self, _event) -> None:
        """Invalidate the image URL when an aliased or mapped source changes."""
        # ImageEntity caches downloaded URL content indefinitely.  A source
        # state change is the integration's signal that the bytes behind the
        # same URL may have changed, so the inherited cache must be cleared.
        self._cached_image = None
        self._image_refresh_pending = True
        self._attr_image_last_updated = dt_util.utcnow()
        self._update_attributes()
        self.async_schedule_update_ha_state()

    def _mark_updated(self, image: bytes) -> bool:
        """Update the image timestamp only when its bytes have changed."""
        digest = hashlib.sha256(image).digest()
        if digest == self._image_digest:
            self._image_refresh_pending = False
            return False
        self._image_digest = digest
        if self._image_refresh_pending:
            self._image_refresh_pending = False
            return False
        self._attr_image_last_updated = dt_util.utcnow()
        self._update_attributes()
        self.async_write_ha_state()
        return True

    def _polygon_markers(self) -> list[dict[str, Any]]:
        """Return valid current locations from the map's configured sources."""
        markers = []
        for entity_id in self._source_entities:
            state = self.hass.states.get(entity_id)
            if state is None:
                continue
            try:
                raw_latitude = state.attributes[ATTR_LATITUDE]
                raw_longitude = state.attributes[ATTR_LONGITUDE]
                if isinstance(raw_latitude, bool) or isinstance(raw_longitude, bool):
                    continue
                latitude = float(raw_latitude)
                longitude = float(raw_longitude)
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            if (
                not math.isfinite(latitude)
                or not math.isfinite(longitude)
                or not -90 <= latitude <= 90
                or not -180 <= longitude <= 180
            ):
                continue
            markers.append({
                "entity_id": entity_id,
                "label": state.attributes.get(ATTR_FRIENDLY_NAME) or state.name,
                "latitude": latitude,
                "longitude": longitude,
            })
        return markers

    def image(self) -> bytes | None:
        """Return bytes for the synchronous ImageEntity API."""
        if not self._image_path:
            return None
        image_path = allowed_local_path(self.hass, self._image_path)
        if image_path is None:
            _LOGGER.warning("Blocked disallowed image path for %s", self.entity_id)
            return None
        try:
            with open(image_path, "rb") as image_file:
                image = image_file.read(MAX_LOCAL_MEDIA_BYTES + 1)
        except OSError:
            _LOGGER.warning("Unable to read image for %s", self.entity_id)
            return None
        if len(image) > MAX_LOCAL_MEDIA_BYTES:
            _LOGGER.warning("Local image is too large for %s", self.entity_id)
            return None
        return image

    async def async_image(self) -> bytes | None:
        """Return bytes from the configured source."""
        source = self._source_image()
        if source is not None:
            marker = id(self)
            active_aliases = _IMAGE_ALIAS_CHAIN.get()
            if marker in active_aliases:
                return None
            token = _IMAGE_ALIAS_CHAIN.set(active_aliases | {marker})
            try:
                image = await source.async_image()
            except (
                asyncio.TimeoutError,
                AttributeError,
                ClientError,
                HomeAssistantError,
                OSError,
                ValueError,
            ) as err:
                _LOGGER.warning(
                    "Unable to get virtual image from %s: %s",
                    self._source_entity,
                    err,
                )
                return None
            finally:
                _IMAGE_ALIAS_CHAIN.reset(token)
            if image is not None:
                source_content_type = getattr(source, "content_type", None)
                if isinstance(source_content_type, str) and source_content_type:
                    self._attr_content_type = source_content_type
                self._mark_updated(image)
            return image

        if self._inline_svg:
            image = self._inline_svg.encode()
            self._mark_updated(image)
            return image

        if self._polygon_config:
            try:
                zones, load_errors = await load_polygon_zones(
                    self.hass,
                    self._polygon_config.get(CONF_POLYGON_GEOJSON),
                    self._polygon_config.get(CONF_POLYGON_FILES),
                    return_errors=True,
                )
                # A partially unreadable file set must not make an otherwise
                # working map jump to a different set of zones.  This mirrors
                # the tracker reload policy and keeps the previous complete map.
                if not self._polygon_zones or (zones and not load_errors):
                    self._polygon_zones = zones
                zones = self._polygon_zones
                if not zones:
                    error = "; ".join(load_errors) or "No polygon zones to render"
                    if self._virtual_attributes.get("polygon_map_error") != error:
                        self._virtual_attributes["polygon_map_error"] = error
                        self._virtual_attributes["polygon_zones"] = []
                        self._update_attributes()
                        self.async_write_ha_state()
                    return None
                image = render_polygon_map_svg(
                    zones,
                    markers=self._polygon_markers(),
                ).encode()
            except (
                asyncio.TimeoutError,
                ClientError,
                HomeAssistantError,
                OSError,
                TypeError,
                ValueError,
            ) as err:
                _LOGGER.warning("Unable to render virtual polygon map: %s", err)
                error = str(err)
                if self._virtual_attributes.get("polygon_map_error") != error:
                    self._virtual_attributes["polygon_map_error"] = error
                    self._virtual_attributes["polygon_zones"] = [
                        zone["name"]
                        for zone in self._polygon_zones
                        if isinstance(zone, dict) and isinstance(zone.get("name"), str)
                    ]
                    self._update_attributes()
                    self.async_write_ha_state()
                return None
            polygon_map_error = "; ".join(load_errors) if load_errors else None
            polygon_zones = [zone["name"] for zone in zones]
            metadata_changed = (
                self._virtual_attributes.get("polygon_map_error") != polygon_map_error
                or self._virtual_attributes.get("polygon_zones") != polygon_zones
            )
            self._virtual_attributes["polygon_map_error"] = polygon_map_error
            self._virtual_attributes["polygon_zones"] = polygon_zones
            wrote_state = self._mark_updated(image)
            if metadata_changed and not wrote_state:
                self._update_attributes()
                self.async_write_ha_state()
            return image

        if self._image_path:
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
            self._mark_updated(image)
            return image

        if self._image_url:
            try:
                image = await ImageEntity.async_image(self)
            except (
                asyncio.TimeoutError,
                ClientError,
                HomeAssistantError,
                OSError,
                ValueError,
            ) as err:
                _LOGGER.warning("Unable to fetch image for %s: %s", self.entity_id, err)
                return None
            if image is not None:
                self._mark_updated(image)
            return image
        return None

    def _apply_native_template_value(self, name: str, value) -> bool:
        backing_fields = {
            CONF_IMAGE_PATH: "_image_path",
            CONF_SOURCE_ENTITY: "_source_entity",
            CONF_SVG: "_inline_svg",
        }
        if name in backing_fields:
            value = None if value is None or value == "" else str(value)
            if name == CONF_SOURCE_ENTITY and value is not None:
                value = _image_entity_id(value)
            attribute = backing_fields[name]
            changed = getattr(self, attribute) != value
            setattr(self, attribute, value)
            if changed:
                self._cached_image = None
                self._image_digest = None
            return changed
        if name == CONF_IMAGE_URL:
            value = None if value is None or value == "" else cv.url(str(value))
            changed = self._image_url != value
            self._image_url = value
            self._attr_image_url = value
            if changed:
                self._cached_image = None
                self._image_digest = None
            return changed
        if name == CONF_CONTENT_TYPE:
            value = str(value).strip()
            if not value.startswith("image/"):
                raise ValueError("content_type must be an image MIME type")
            name = "content_type"
        elif name == "image_last_updated":
            if value is None or value == "":
                return super()._apply_native_template_value(name, None)
            if isinstance(value, datetime):
                parsed = value
            else:
                parsed = dt_util.parse_datetime(str(value))
            if parsed is None:
                raise ValueError("image_last_updated must be a datetime")
            value = parsed if parsed.tzinfo else dt_util.as_local(parsed)
        return super()._apply_native_template_value(name, value)

    def _native_templates_applied(self) -> None:
        """Retarget source invalidation after a dynamic alias changes."""
        self._sync_source_image_listener()

    def set_state(self, value) -> None:
        """Keep generic/template state updates harmless for image entities."""
        self.async_schedule_update_ha_state()
