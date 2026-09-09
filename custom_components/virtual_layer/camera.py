"""
This component provides support for a virtual camera entity.

"""
from __future__ import annotations

import asyncio
import logging
import math
import mimetypes
from collections.abc import Callable
from contextvars import ContextVar
from io import BytesIO

import aiofiles
import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from aiohttp import ClientError, web
from PIL import Image, ImageOps
from homeassistant.components.camera import (
    DOMAIN as PLATFORM_DOMAIN,
)
from homeassistant.components.camera import (
    Camera,
    CameraEntityFeature,
    CameraState,
)
from homeassistant.components.camera.const import StreamType
from homeassistant.components.camera.webrtc import (
    WebRTCClientConfiguration,
    WebRTCSendMessage,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.config_validation import PLATFORM_SCHEMA
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.network import get_url
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from webrtc_models import RTCIceCandidateInit

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
_CAMERA_WEBRTC_ALIAS_CHAIN: ContextVar[frozenset[int]] = ContextVar(
    "virtual_layer_camera_webrtc_alias_chain",
    default=frozenset(),
)
# Finish before Home Assistant's 10 second camera/snapshot deadline so a
# last-known-good result can be returned instead of being cancelled outside.
MEDIA_ALIAS_TIMEOUT = 8
IMAGE_REFRESH_INTERVAL = 1
IMAGE_VIDEO_SIZE = (1280, 720)

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
_CAMERA_ACTIVE_STATES = frozenset({
    STATE_ON,
    CameraState.IDLE,
    CameraState.RECORDING,
    CameraState.STREAMING,
})
_CAMERA_INACTIVE_STATES = frozenset({
    STATE_OFF,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
})


def _camera_state_is_on(value) -> bool:
    """Map native camera activity states onto the camera power property."""
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in _CAMERA_ACTIVE_STATES:
        return True
    if normalized in _CAMERA_INACTIVE_STATES:
        return False
    raise ValueError(f"Unsupported camera state: {normalized!r}")


def _camera_entity_id(value: str) -> str:
    """Validate a camera entity id used as an alias source."""
    entity_id = cv.entity_id(value)
    if entity_id.split(".", 1)[0] not in {"camera", "image"}:
        raise vol.Invalid("source_entity must be a camera or image entity")
    return entity_id


def _image_as_jpeg(payload: bytes) -> bytes:
    """Normalize raster maps for MJPEG/HomeKit outside the event loop."""
    with Image.open(BytesIO(payload)) as image:
        if image.width * image.height > 16_000_000:
            raise ValueError("source image exceeds the pixel limit")
        # HomeKit's FFmpeg command does not resize the input. YUV420 requires
        # even dimensions, and a fixed canvas prevents resolution changes in
        # the middle of an H.264 session. Contain rather than crop the map.
        rgba = ImageOps.exif_transpose(image).convert("RGBA")
        scale = min(IMAGE_VIDEO_SIZE[0] / rgba.width, IMAGE_VIDEO_SIZE[1] / rgba.height)
        rgba = rgba.resize(
            (max(1, round(rgba.width * scale)), max(1, round(rgba.height * scale))),
            Image.Resampling.LANCZOS,
        )
        background = Image.new("RGB", IMAGE_VIDEO_SIZE, "white")
        position = ((background.width - rgba.width) // 2, (background.height - rgba.height) // 2)
        background.paste(rgba, position, mask=rgba.getchannel("A"))
        output = BytesIO()
        background.save(output, format="JPEG", quality=85)
        return output.getvalue()


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
        self._configured_supported_features = None
        self._attr_supported_features = CameraEntityFeature.ON_OFF
        if config.get(CONF_STREAM_SOURCE) or config.get(CONF_SOURCE_ENTITY):
            self._attr_supported_features |= CameraEntityFeature.STREAM

        self._image_path = config.get(CONF_IMAGE_PATH)
        self.content_type = (
            mimetypes.guess_type(self._image_path or "")[0] or "image/jpeg"
        )
        self._source_entity = config.get(CONF_SOURCE_ENTITY)
        if self._is_image_source:
            self._attr_frame_interval = IMAGE_REFRESH_INTERVAL
        self._stream_source = config.get(CONF_STREAM_SOURCE)
        self._last_camera_image: bytes | None = None
        self._last_source_image: bytes | None = None
        self._image_fetch_task: asyncio.Task[bytes | None] | None = None
        self._image_generation = 0
        self._last_stream_source: str | None = self._stream_source
        # Camera.__init__ sees the WebRTC proxy methods on this class. The
        # actual capability is source-dependent and is synchronized once the
        # source camera is available in the entity component.
        self._supports_native_async_webrtc = False
        self._camera_internal_added = False
        self._media_removed = False
        self._tracked_source_camera: str | None = None
        self._source_camera_remove_listener: Callable[[], None] | None = None

        _LOGGER.debug(f"VirtualCamera: {self.name} created")

    def _create_state(self, config):
        super()._create_state(config)
        self._last_camera_image = None
        self._last_source_image = None
        try:
            self._attr_is_on = _camera_state_is_on(
                config.get(CONF_INITIAL_VALUE),
            )
        except ValueError:
            self._attr_is_on = False
        self._attr_is_recording = config.get(CONF_IS_RECORDING)
        self._attr_is_streaming = config.get(CONF_IS_STREAMING)
        self._attr_motion_detection_enabled = config.get(CONF_MOTION_DETECTION)

    def _restore_state(self, state, config):
        super()._restore_state(state, config)
        try:
            configured_is_on = _camera_state_is_on(
                config.get(CONF_INITIAL_VALUE),
            )
        except ValueError:
            configured_is_on = False
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

    @property
    def available(self) -> bool:
        """Keep configured availability independent from stream throughput."""
        # Camera.available also considers the stream worker. A slow or
        # reconnecting H.264 input must not hide an otherwise usable virtual
        # camera or prevent its cached snapshot from being served.
        return self._attr_available

    @property
    def use_stream_for_stills(self) -> bool:
        """Use H.264 frames when no independent still-image source exists."""
        return bool(
            self._stream_source
            and not self._image_path
            and not self._is_image_source
            and self._source_camera() is None
        )

    async def async_camera_image(
        self,
        width: int | None = None,
        height: int | None = None,
    ) -> bytes | None:
        if not self._attr_is_on:
            return None
        if self._is_image_source and not self._image_path:
            task = self._image_fetch_task
            if task is None or task.done():
                task = self._image_fetch_task = self.hass.async_create_background_task(
                    self._async_refresh_image(), "Virtual Layer image camera snapshot",
                )
            try:
                # One viewer disconnecting must not cancel another viewer's
                # snapshot. Removal/source edits explicitly cancel this task.
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                if task.cancelled():
                    return None
                raise
        source = self._source_camera()
        if source is not None and not self._image_path:
            marker = id(self)
            active_aliases = _CAMERA_IMAGE_ALIAS_CHAIN.get()
            if marker in active_aliases:
                return None
            token = _CAMERA_IMAGE_ALIAS_CHAIN.set(active_aliases | {marker})
            try:
                async with asyncio.timeout(MEDIA_ALIAS_TIMEOUT):
                    image = await source.async_camera_image(
                        width=width,
                        height=height,
                    )
                if image is not None:
                    if not isinstance(image, (bytes, bytearray, memoryview)):
                        raise TypeError("camera image must be bytes-like")
                    if len(image) > MAX_LOCAL_MEDIA_BYTES:
                        raise ValueError("camera image exceeds the media size limit")
                    source_content_type = getattr(source, "content_type", None)
                    if (
                        isinstance(source_content_type, str)
                        and source_content_type.startswith("image/")
                    ):
                        self.content_type = source_content_type
                    self._last_camera_image = bytes(image)
                return self._last_camera_image
            except (
                asyncio.TimeoutError,
                AttributeError,
                ClientError,
                HomeAssistantError,
                OSError,
                TypeError,
                ValueError,
            ) as err:
                _LOGGER.warning(
                    "Unable to get virtual camera image from %s: %s",
                    self._source_entity,
                    err,
                )
                return self._last_camera_image
            finally:
                _CAMERA_IMAGE_ALIAS_CHAIN.reset(token)
        if not self._image_path:
            return self._last_camera_image if self._source_entity else None
        image_path = await self.hass.async_add_executor_job(
            allowed_local_path,
            self.hass,
            self._image_path,
        )
        if image_path is None:
            _LOGGER.warning("Blocked disallowed image path for %s", self.entity_id)
            return self._last_camera_image
        try:
            async with aiofiles.open(image_path, "rb") as image_file:
                image = await image_file.read(MAX_LOCAL_MEDIA_BYTES + 1)
        except OSError:
            _LOGGER.warning("Unable to read image for %s", self.entity_id)
            return self._last_camera_image
        if len(image) > MAX_LOCAL_MEDIA_BYTES:
            _LOGGER.warning("Local image is too large for %s", self.entity_id)
            return self._last_camera_image
        self._last_camera_image = bytes(image)
        return self._last_camera_image

    async def _async_refresh_image(self) -> bytes | None:
        """Fetch/decode once for concurrent snapshot and stream consumers."""
        generation = self._image_generation
        component = self.hass.data.get("image")
        source = component.get_entity(self._source_entity) if component else None
        if source is None:
            return self._last_camera_image
        try:
            async with asyncio.timeout(MEDIA_ALIAS_TIMEOUT):
                payload = await source.async_image()
                if payload is None:
                    return self._last_camera_image
                if not isinstance(payload, (bytes, bytearray, memoryview)):
                    raise ValueError("image must be bytes-like")
                if len(payload) > MAX_LOCAL_MEDIA_BYTES:
                    raise ValueError("image exceeds the media size limit")
                payload = bytes(payload)
                if payload != self._last_source_image:
                    jpeg = await self.hass.async_add_executor_job(_image_as_jpeg, payload)
                    if generation != self._image_generation or self._media_removed:
                        return None
                    self._last_source_image = payload
                    self._last_camera_image = jpeg
                self.content_type = "image/jpeg"
        except (
            TimeoutError, ClientError, HomeAssistantError, OSError,
            TypeError, ValueError, Image.DecompressionBombError,
        ):
            pass
        return self._last_camera_image if generation == self._image_generation else None

    @callback
    def _invalidate_image_source(self) -> None:
        """Prevent an old in-flight source from repopulating the new cache."""
        self._image_generation += 1
        if self._image_fetch_task is not None:
            self._image_fetch_task.cancel()
            self._image_fetch_task = None
        self._last_camera_image = None
        self._last_source_image = None

    async def stream_source(self) -> str | None:
        if self._is_image_source and not self._stream_source and not self._image_path:
            # Use HA's authenticated camera endpoint. HomeKit's FFmpeg worker
            # encodes this MJPEG input as H.264 on demand.
            return (
                f"{get_url(self.hass, prefer_external=False)}"
                f"/api/camera_proxy_stream/{self.entity_id}"
                f"?token={self.access_tokens[-1]}"
            )
        source = self._source_camera()
        if source is not None and not self._stream_source:
            marker = id(self)
            active_aliases = _CAMERA_STREAM_ALIAS_CHAIN.get()
            if marker in active_aliases:
                return None
            token = _CAMERA_STREAM_ALIAS_CHAIN.set(active_aliases | {marker})
            try:
                async with asyncio.timeout(MEDIA_ALIAS_TIMEOUT):
                    stream_source = await source.stream_source()
                if isinstance(stream_source, str) and stream_source.strip():
                    self._last_stream_source = stream_source.strip()
                return self._last_stream_source
            except (
                asyncio.TimeoutError,
                AttributeError,
                ClientError,
                HomeAssistantError,
                OSError,
                TypeError,
                ValueError,
            ) as err:
                _LOGGER.warning(
                    "Unable to get virtual camera stream from %s: %s",
                    self._source_entity,
                    err,
                )
                return self._last_stream_source
            finally:
                _CAMERA_STREAM_ALIAS_CHAIN.reset(token)
        if self._stream_source:
            self._last_stream_source = self._stream_source
        return self._last_stream_source if self._source_entity else self._stream_source

    @property
    def _is_image_source(self) -> bool:
        return bool(self._source_entity and self._source_entity.startswith("image."))

    async def handle_async_mjpeg_stream(
        self, request: web.Request,
    ) -> web.StreamResponse | None:
        """Repeat unchanged maps so an H.264 encoder never runs out of frames."""
        if not self._is_image_source or self._image_path or self._stream_source:
            return await super().handle_async_mjpeg_stream(request)
        response = web.StreamResponse(headers={
            "Content-Type": "multipart/x-mixed-replace; boundary=frameboundary",
        })
        await response.prepare(request)
        refresh_task: asyncio.Task[bytes | None] | None = None
        generation = self._image_generation
        try:
            payload = await self.async_camera_image()
            refresh_at = self.hass.loop.time() + self.frame_interval
            while (
                self._attr_is_on
                and not self._media_removed
                and generation == self._image_generation
                and self._is_image_source
                and not self._image_path
                and not self._stream_source
            ):
                if refresh_task is not None and refresh_task.done():
                    payload = refresh_task.result() or payload
                    refresh_task = None
                    refresh_at = self.hass.loop.time() + self.frame_interval
                if refresh_task is None and self.hass.loop.time() >= refresh_at:
                    refresh_task = asyncio.create_task(self.async_camera_image())
                if payload is None:
                    break
                await response.write(
                    b"--frameboundary\r\nContent-Type: image/jpeg\r\n"
                    + f"Content-Length: {len(payload)}\r\n\r\n".encode()
                    + payload + b"\r\n"
                )
                await asyncio.sleep(1 / 25)
        except ConnectionResetError:
            pass
        finally:
            if refresh_task is not None:
                refresh_task.cancel()
                await asyncio.gather(refresh_task, return_exceptions=True)
        return response

    async def async_handle_async_webrtc_offer(
        self,
        offer_sdp: str,
        session_id: str,
        send_message: WebRTCSendMessage,
    ) -> None:
        """Proxy WebRTC signaling to an aliased source camera."""
        source = self._source_camera()
        if (
            source is None
            or self._stream_source
            or StreamType.WEB_RTC not in self._source_stream_types(source)
        ):
            await super().async_handle_async_webrtc_offer(
                offer_sdp,
                session_id,
                send_message,
            )
            return

        marker = id(self)
        active_aliases = _CAMERA_WEBRTC_ALIAS_CHAIN.get()
        if marker in active_aliases:
            raise HomeAssistantError("Circular virtual camera WebRTC alias")
        token = _CAMERA_WEBRTC_ALIAS_CHAIN.set(active_aliases | {marker})
        try:
            await source.async_handle_async_webrtc_offer(
                offer_sdp,
                session_id,
                send_message,
            )
        except Exception:
            _LOGGER.exception(
                "Unable to start virtual camera WebRTC stream from %s",
                self._source_entity,
            )
            raise
        finally:
            _CAMERA_WEBRTC_ALIAS_CHAIN.reset(token)

    async def async_on_webrtc_candidate(
        self,
        session_id: str,
        candidate: RTCIceCandidateInit,
    ) -> None:
        """Forward a WebRTC ICE candidate to an aliased source camera."""
        source = self._source_camera()
        if (
            source is None
            or self._stream_source
            or StreamType.WEB_RTC not in self._source_stream_types(source)
        ):
            await super().async_on_webrtc_candidate(session_id, candidate)
            return

        marker = id(self)
        active_aliases = _CAMERA_WEBRTC_ALIAS_CHAIN.get()
        if marker in active_aliases:
            raise HomeAssistantError("Circular virtual camera WebRTC alias")
        token = _CAMERA_WEBRTC_ALIAS_CHAIN.set(active_aliases | {marker})
        try:
            await source.async_on_webrtc_candidate(session_id, candidate)
        except Exception:
            _LOGGER.exception(
                "Unable to send a virtual camera WebRTC candidate to %s",
                self._source_entity,
            )
            raise
        finally:
            _CAMERA_WEBRTC_ALIAS_CHAIN.reset(token)

    @callback
    def close_webrtc_session(self, session_id: str) -> None:
        """Close a WebRTC session on an aliased source camera."""
        source = self._source_camera()
        if (
            source is None
            or self._stream_source
            or StreamType.WEB_RTC not in self._source_stream_types(source)
        ):
            super().close_webrtc_session(session_id)
            return

        marker = id(self)
        active_aliases = _CAMERA_WEBRTC_ALIAS_CHAIN.get()
        if marker in active_aliases:
            return
        token = _CAMERA_WEBRTC_ALIAS_CHAIN.set(active_aliases | {marker})
        try:
            source.close_webrtc_session(session_id)
        except Exception:
            _LOGGER.exception(
                "Unable to close virtual camera WebRTC stream on %s",
                self._source_entity,
            )
        finally:
            _CAMERA_WEBRTC_ALIAS_CHAIN.reset(token)

    @callback
    def _async_get_webrtc_client_configuration(self) -> WebRTCClientConfiguration:
        """Use WebRTC client settings required by the aliased camera."""
        source = self._source_camera()
        if (
            source is not None
            and not self._stream_source
            and StreamType.WEB_RTC in self._source_stream_types(source)
        ):
            get_configuration = getattr(
                source,
                "_async_get_webrtc_client_configuration",
                None,
            )
            if callable(get_configuration):
                return get_configuration()
        return super()._async_get_webrtc_client_configuration()

    async def async_internal_added_to_hass(self) -> None:
        """Finalize stream capabilities after source entities are available."""
        self._sync_stream_capabilities()
        await super().async_internal_added_to_hass()
        self._camera_internal_added = True

    async def async_added_to_hass(self) -> None:
        """Track source replacement and capability changes."""
        await super().async_added_to_hass()
        self._sync_source_camera_listener()

    async def async_will_remove_from_hass(self) -> None:
        """Remove the independently managed source-camera listener."""
        self._media_removed = True
        task = self._image_fetch_task
        self._invalidate_image_source()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        if self._source_camera_remove_listener is not None:
            self._source_camera_remove_listener()
            self._source_camera_remove_listener = None
        self._tracked_source_camera = None
        self._camera_internal_added = False
        await super().async_will_remove_from_hass()

    @callback
    def _sync_source_camera_listener(self) -> None:
        """Follow a changing camera alias without retaining stale listeners."""
        source_entity = self._source_entity
        if source_entity == self.entity_id:
            source_entity = None
        if source_entity == self._tracked_source_camera:
            return

        if self._source_camera_remove_listener is not None:
            self._source_camera_remove_listener()
            self._source_camera_remove_listener = None
        self._tracked_source_camera = source_entity
        if source_entity:
            self._source_camera_remove_listener = async_track_state_change_event(
                self.hass,
                [source_entity],
                self._async_source_camera_changed,
            )

    @callback
    def _async_source_camera_changed(self, _event) -> None:
        """Refresh HLS/WebRTC capabilities after a source reload or update."""
        old_stream_support = bool(
            self.supported_features & CameraEntityFeature.STREAM
        )
        old_native_webrtc = self._supports_native_async_webrtc
        self._sync_stream_capabilities()
        self.async_write_ha_state()
        if (
            self._camera_internal_added
            and (
                old_stream_support
                != bool(self.supported_features & CameraEntityFeature.STREAM)
                or old_native_webrtc != self._supports_native_async_webrtc
            )
        ):
            self.hass.async_create_task(self.async_refresh_providers())

    def _source_camera(self) -> Camera | None:
        """Return the configured source camera without recursing into self."""
        if not self._source_entity or self._is_image_source or self.hass is None:
            return None

        component = self.hass.data.get(PLATFORM_DOMAIN)
        get_entity = getattr(component, "get_entity", None)
        if get_entity is None:
            return None

        source = get_entity(self._source_entity)
        return None if source is self else source

    @staticmethod
    def _source_stream_types(source: Camera) -> frozenset[StreamType]:
        """Return frontend stream types advertised by a source camera."""
        try:
            stream_types = source.camera_capabilities.frontend_stream_types
            return frozenset(StreamType(value) for value in stream_types)
        except (AttributeError, TypeError, ValueError):
            pass

        stream_types = set()
        if getattr(source, "_supports_native_async_webrtc", False) is True:
            stream_types.add(StreamType.WEB_RTC)
        try:
            if CameraEntityFeature.STREAM in source.supported_features:
                stream_types.add(StreamType.HLS)
        except (AttributeError, TypeError, ValueError):
            pass
        return frozenset(stream_types)

    def _sync_stream_capabilities(self) -> None:
        """Synchronize stream flags with a direct or aliased stream source."""
        features = CameraEntityFeature.ON_OFF
        supports_native_webrtc = False
        source = self._source_camera()

        if self._configured_supported_features is not None:
            features = self._configured_supported_features
            # A direct stream_source is an explicit HLS-compatible input to
            # Home Assistant's stream component. It must advertise STREAM even
            # when a copied source camera supplied a stale ON_OFF-only feature
            # template. Otherwise adding an H.264/RTSP URL in the config flow
            # stores the URL but leaves the camera unable to play it.
            if self._stream_source:
                features |= CameraEntityFeature.STREAM
            if (
                CameraEntityFeature.STREAM in features
                and source is not None
                and not self._stream_source
            ):
                supports_native_webrtc = (
                    StreamType.WEB_RTC in self._source_stream_types(source)
                )
        else:
            if source is not None:
                try:
                    features |= CameraEntityFeature(source.supported_features)
                except (TypeError, ValueError, OverflowError):
                    pass

            if self._stream_source:
                features |= CameraEntityFeature.STREAM
            elif source is not None:
                source_stream_types = self._source_stream_types(source)
                if source_stream_types:
                    features |= CameraEntityFeature.STREAM
                supports_native_webrtc = StreamType.WEB_RTC in source_stream_types
            elif self._source_entity:
                # Keep the alias usable while its source integration reloads.
                # A later source update will refine HLS versus WebRTC.
                features |= CameraEntityFeature.STREAM

        self._attr_supported_features = features
        if self._is_image_source and not self._stream_source:
            # HA's HLS worker cannot remux MJPEG. Its frontend uses the native
            # MJPEG endpoint, while HomeKit independently transcodes to H.264.
            self._attr_supported_features &= ~CameraEntityFeature.STREAM
        self._supports_native_async_webrtc = supports_native_webrtc
        self.__dict__.pop("supported_features", None)
        self._invalidate_camera_capabilities_cache()

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
            if name == CONF_SOURCE_ENTITY and changed:
                self._invalidate_image_source()
                self._last_stream_source = self._stream_source
            if name == CONF_IMAGE_PATH and changed:
                self._invalidate_image_source()
                self.content_type = (
                    mimetypes.guess_type(value or "")[0] or "image/jpeg"
                )
            elif name == CONF_STREAM_SOURCE and changed:
                self._last_stream_source = value
                current_stream = self.stream
                if current_stream is not None:
                    if value:
                        current_stream.update_source(value)
                    else:
                        self.stream = None
                        if self.hass is not None:
                            self.hass.async_create_task(current_stream.stop())
            return changed
        if name == "supported_features":
            if isinstance(value, bool):
                raise ValueError("supported_features must be a non-negative integer")
            try:
                parsed = int(value)
            except (TypeError, ValueError, OverflowError) as err:
                raise ValueError(
                    "supported_features must be a non-negative integer"
                ) from err
            if parsed < 0:
                raise ValueError("supported_features must be a non-negative integer")
            value = CameraEntityFeature(parsed)
            changed = self._configured_supported_features != value
            self._configured_supported_features = value
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
        old_stream_support = bool(
            self.supported_features & CameraEntityFeature.STREAM
        )
        old_native_webrtc = self._supports_native_async_webrtc
        self._sync_stream_capabilities()
        self._sync_source_camera_listener()
        if (
            self._camera_internal_added
            and (
                old_stream_support
                != bool(self.supported_features & CameraEntityFeature.STREAM)
                or old_native_webrtc != self._supports_native_async_webrtc
            )
        ):
            self.hass.async_create_task(self.async_refresh_providers())

    def set_state(self, value) -> None:
        self._attr_is_on = _camera_state_is_on(value)
