"""Regression tests for non-mergeable media and safety sensor helpers."""

import asyncio
from io import BytesIO
from unittest.mock import AsyncMock, Mock, patch

import pytest
from PIL import Image
from aiohttp import ClientConnectionError
from homeassistant.components.camera import (
    Camera,
    CameraEntityFeature,
    async_handle_snapshot_service,
)
from homeassistant.components.camera.const import StreamType
from homeassistant.components.camera.webrtc import WebRTCClientConfiguration
from homeassistant.components.image import ImageEntity
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_FRIENDLY_NAME,
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    CONF_NAME,
)
from homeassistant.helpers.template import Template

from custom_components.virtual_layer import camera as camera_platform
from custom_components.virtual_layer import image as image_platform
from custom_components.virtual_layer.camera import CAMERA_SCHEMA, VirtualCamera
from custom_components.virtual_layer.config_flow import (
    InvalidEntityReference,
    _reference_entity_defaults,
)
from custom_components.virtual_layer.const import (
    ATTR_UNIQUE_ID,
    CONF_INITIAL_VALUE,
    CONF_POLYGON_GEOJSON,
    CONF_POLYGONAL_ZONE,
    CONF_SOURCE_ENTITIES,
)
from custom_components.virtual_layer.image import IMAGE_SCHEMA, VirtualImage

pytestmark = pytest.mark.unit


def _map_png(color="red"):
    output = BytesIO()
    Image.new("RGBA", (33, 31), color).save(output, "PNG")
    return output.getvalue()


async def test_image_camera_snapshot_refresh_failure_and_retarget(hass):
    source = Mock()
    source.async_image = AsyncMock(return_value=_map_png())
    component = Mock()
    component.get_entity.return_value = source
    hass.data["image"] = component
    entity = VirtualCamera(CAMERA_SCHEMA({
        CONF_NAME: "Map", ATTR_ENTITY_ID: "camera.map",
        ATTR_UNIQUE_ID: "map", "source_entity": "image.map",
    }), False)
    entity.hass = hass
    entity._create_state(entity._config)
    entity._sync_stream_capabilities()
    assert not entity.supported_features & CameraEntityFeature.STREAM
    first = await entity.async_camera_image()
    assert entity.content_type == "image/jpeg"
    with Image.open(BytesIO(first)) as image:
        assert image.format == "JPEG"
        assert image.size == (1280, 720)
    assert await entity.async_camera_image() == first
    source.async_image.return_value = _map_png("blue")
    second = await entity.async_camera_image()
    assert second != first
    for invalid in (None, b"not an image", "invalid bytes"):
        source.async_image.return_value = invalid
        assert await entity.async_camera_image() == second
    source.async_image.side_effect = ClientConnectionError()
    assert await entity.async_camera_image() == second
    entity._apply_native_template_value("source_entity", "image.other")
    assert await entity.async_camera_image() is None
    entity._attr_is_on = False
    source.async_image.reset_mock()
    assert await entity.async_camera_image() is None
    source.async_image.assert_not_awaited()


async def test_image_camera_stream_repeats_static_frames(hass):
    entity = VirtualCamera(CAMERA_SCHEMA({
        CONF_NAME: "Map", ATTR_ENTITY_ID: "camera.map",
        ATTR_UNIQUE_ID: "map", "source_entity": "image.map",
    }), False)
    entity.hass = hass
    entity._create_state(entity._config)
    entity.async_camera_image = AsyncMock(return_value=b"jpeg")
    response = Mock(prepare=AsyncMock(), write=AsyncMock())
    response.write.side_effect = [None, None, ConnectionResetError()]
    with patch.object(camera_platform.web, "StreamResponse", return_value=response):
        assert await entity.handle_async_mjpeg_stream(Mock()) is response
    assert response.write.await_count == 3
    assert response.write.call_args_list[0] == response.write.call_args_list[1]
    entity.async_camera_image.assert_awaited_once()
    with patch.object(camera_platform, "get_url", return_value="http://ha.local:8123"):
        url = await entity.stream_source()
    assert url.startswith("http://ha.local:8123/api/camera_proxy_stream/camera.map?token=")
    assert url.endswith(entity.access_tokens[-1])
    entity._apply_native_template_value("stream_source", "rtsp://camera/live")
    assert await entity.stream_source() == "rtsp://camera/live"
    assert not entity.use_stream_for_stills
    entity._sync_stream_capabilities()
    assert entity.supported_features & CameraEntityFeature.STREAM


async def test_image_source_can_create_camera_helpers(hass):
    from custom_components.virtual_layer.config_flow import (
        CONF_NATIVE_VALUE_TEMPLATES, _source_target_domains,
    )
    hass.states.async_set("image.map", "2026-09-09T00:00:00+00:00", {
        "image_path": "/config/map.png", "stream_source": "invalid",
    })
    assert "camera" in _source_target_domains(["image.map"], "image")
    defaults = _reference_entity_defaults(hass, ["image.map"], target_platform="camera")
    assert defaults[CONF_INITIAL_VALUE] == "on"
    assert Template(defaults["value_template"], hass).async_render() == "on"
    native = defaults[CONF_NATIVE_VALUE_TEMPLATES]
    assert Template(native["source_entity"], hass).async_render() == "image.map"
    assert Template(native["is_on"], hass).async_render() is True
    assert Template(native["image_path"], hass).async_render() is None
    assert Template(native["stream_source"], hass).async_render() is None
    hass.states.async_set("image.map", "unavailable")
    assert Template(native["is_on"], hass).async_render() is True


async def test_virtual_image_alias_returns_source_image(hass):
    source = Mock()
    source.async_image = AsyncMock(return_value=b"image-bytes")
    source.content_type = "image/png"
    image_component = Mock()
    image_component.get_entity.return_value = source
    hass.data["image"] = image_component

    entity = VirtualImage(IMAGE_SCHEMA({
        CONF_NAME: "Front Door Image",
        ATTR_ENTITY_ID: "image.front_door_alias",
        ATTR_UNIQUE_ID: "front_door_alias",
        CONF_INITIAL_VALUE: "unknown",
        "source_entity": "image.front_door",
    }), hass, False)
    entity.hass = hass
    entity._create_state(entity._config)
    entity.async_write_ha_state = Mock()

    assert isinstance(entity, ImageEntity)
    assert await entity.async_image() == b"image-bytes"
    assert entity.image_last_updated is not None
    assert entity.state_attributes["content_type"] == "image/png"
    source.async_image.assert_awaited_once_with()
    entity.async_write_ha_state.assert_called_once()


async def test_camera_snapshot_preserves_source_content_type(hass):
    source = Mock()
    source.async_camera_image = AsyncMock(return_value=b"png-image")
    source.content_type = "image/png"
    camera_component = Mock()
    camera_component.get_entity.return_value = source
    hass.data["camera"] = camera_component
    entity = VirtualCamera(CAMERA_SCHEMA({
        CONF_NAME: "PNG Camera Alias",
        ATTR_ENTITY_ID: "camera.png_alias",
        ATTR_UNIQUE_ID: "png-camera-alias",
        CONF_INITIAL_VALUE: "on",
        "source_entity": "camera.png_source",
    }), False)
    entity.hass = hass
    entity._create_state(entity._config)

    assert await entity.async_camera_image() == b"png-image"
    assert entity.content_type == "image/png"


async def test_virtual_image_reads_configured_file(hass, tmp_path):
    image_path = tmp_path / "snapshot.png"
    image_path.write_bytes(b"jpeg-bytes")
    hass.config.allowlist_external_dirs.add(str(tmp_path))
    entity = VirtualImage(IMAGE_SCHEMA({
        CONF_NAME: "Snapshot Image",
        ATTR_ENTITY_ID: "image.snapshot",
        ATTR_UNIQUE_ID: "snapshot",
        CONF_INITIAL_VALUE: "unknown",
        "image_path": str(image_path),
    }), hass, False)
    entity.hass = hass
    entity._create_state(entity._config)
    entity.async_write_ha_state = Mock()

    assert await entity.async_image() == b"jpeg-bytes"
    assert entity.image_last_updated is not None
    assert entity.state_attributes["content_type"] == "image/png"


async def test_virtual_media_blocks_files_outside_allowed_paths(hass, tmp_path):
    allowed_dir = tmp_path / "allowed"
    allowed_dir.mkdir()
    hass.config.allowlist_external_dirs.add(str(allowed_dir))
    secret_path = tmp_path / "secret.txt"
    secret_path.write_bytes(b"private-data")

    image = VirtualImage(IMAGE_SCHEMA({
        CONF_NAME: "Blocked Image",
        ATTR_ENTITY_ID: "image.blocked",
        ATTR_UNIQUE_ID: "blocked-image",
        CONF_INITIAL_VALUE: "unknown",
        "image_path": str(secret_path),
    }), hass, False)
    camera = VirtualCamera(CAMERA_SCHEMA({
        CONF_NAME: "Blocked Camera",
        ATTR_ENTITY_ID: "camera.blocked",
        ATTR_UNIQUE_ID: "blocked-camera",
        CONF_INITIAL_VALUE: "on",
        "image_path": str(secret_path),
    }), False)
    for entity in (image, camera):
        entity.hass = hass
        entity._create_state(entity._config)

    assert await image.async_image() is None
    assert await camera.async_camera_image() is None


async def test_virtual_media_rejects_oversized_local_files(
    hass,
    tmp_path,
    monkeypatch,
):
    image_path = tmp_path / "oversized.png"
    image_path.write_bytes(b"x" * 17)
    hass.config.allowlist_external_dirs.add(str(tmp_path))
    monkeypatch.setattr(image_platform, "MAX_LOCAL_MEDIA_BYTES", 16)
    monkeypatch.setattr(camera_platform, "MAX_LOCAL_MEDIA_BYTES", 16)

    image = VirtualImage(IMAGE_SCHEMA({
        CONF_NAME: "Oversized Image",
        ATTR_ENTITY_ID: "image.oversized",
        ATTR_UNIQUE_ID: "oversized-image",
        CONF_INITIAL_VALUE: "unknown",
        "image_path": str(image_path),
    }), hass, False)
    camera = VirtualCamera(CAMERA_SCHEMA({
        CONF_NAME: "Oversized Camera",
        ATTR_ENTITY_ID: "camera.oversized",
        ATTR_UNIQUE_ID: "oversized-camera",
        CONF_INITIAL_VALUE: "on",
        "image_path": str(image_path),
    }), False)
    for entity in (image, camera):
        entity.hass = hass
        entity._create_state(entity._config)

    assert image.image() is None
    assert await image.async_image() is None
    assert await camera.async_camera_image() is None


async def test_virtual_media_alias_cycles_terminate_without_recursion(hass):
    images = {}
    image_component = Mock()
    image_component.get_entity.side_effect = images.get
    hass.data["image"] = image_component
    for slug, source in (("first", "image.second"), ("second", "image.first")):
        entity = VirtualImage(IMAGE_SCHEMA({
            CONF_NAME: slug.title(),
            ATTR_ENTITY_ID: f"image.{slug}",
            ATTR_UNIQUE_ID: f"cyclic-image-{slug}",
            CONF_INITIAL_VALUE: "unknown",
            "source_entity": source,
        }), hass, False)
        entity.hass = hass
        images[entity.entity_id] = entity

    cameras = {}
    camera_component = Mock()
    camera_component.get_entity.side_effect = cameras.get
    hass.data["camera"] = camera_component
    for slug, source in (("first", "camera.second"), ("second", "camera.first")):
        entity = VirtualCamera(CAMERA_SCHEMA({
            CONF_NAME: slug.title(),
            ATTR_ENTITY_ID: f"camera.{slug}",
            ATTR_UNIQUE_ID: f"cyclic-camera-{slug}",
            CONF_INITIAL_VALUE: "on",
            "source_entity": source,
        }), False)
        entity.hass = hass
        entity._create_state(entity._config)
        cameras[entity.entity_id] = entity

    assert await images["image.first"].async_image() is None
    assert await cameras["camera.first"].async_camera_image() is None
    assert await cameras["camera.first"].stream_source() is None


async def test_virtual_media_allows_independent_concurrent_alias_requests(hass):
    class SourceImage:
        def __init__(self):
            self.calls = 0
            self.both_started = asyncio.Event()
            self.release = asyncio.Event()

        async def async_image(self):
            self.calls += 1
            if self.calls == 2:
                self.both_started.set()
            await self.release.wait()
            return b"image"

    source_image = SourceImage()
    image_component = Mock()
    image_component.get_entity.return_value = source_image
    hass.data["image"] = image_component
    image = VirtualImage(IMAGE_SCHEMA({
        CONF_NAME: "Concurrent Image",
        ATTR_ENTITY_ID: "image.concurrent",
        ATTR_UNIQUE_ID: "concurrent-image",
        CONF_INITIAL_VALUE: "unknown",
        "source_entity": "image.source",
    }), hass, False)
    image.hass = hass

    image_tasks = [asyncio.create_task(image.async_image()) for _ in range(2)]
    await asyncio.wait_for(source_image.both_started.wait(), 1)
    source_image.release.set()
    assert await asyncio.gather(*image_tasks) == [b"image", b"image"]

    class SourceCamera:
        def __init__(self):
            self.image_calls = 0
            self.stream_calls = 0
            self.image_started = asyncio.Event()
            self.stream_started = asyncio.Event()
            self.release_image = asyncio.Event()
            self.release_stream = asyncio.Event()

        async def async_camera_image(self, **_kwargs):
            self.image_calls += 1
            if self.image_calls == 2:
                self.image_started.set()
            await self.release_image.wait()
            return b"camera"

        async def stream_source(self):
            self.stream_calls += 1
            if self.stream_calls == 2:
                self.stream_started.set()
            await self.release_stream.wait()
            return "rtsp://camera"

    source_camera = SourceCamera()
    camera_component = Mock()
    camera_component.get_entity.return_value = source_camera
    hass.data["camera"] = camera_component
    camera = VirtualCamera(CAMERA_SCHEMA({
        CONF_NAME: "Concurrent Camera",
        ATTR_ENTITY_ID: "camera.concurrent",
        ATTR_UNIQUE_ID: "concurrent-camera",
        CONF_INITIAL_VALUE: "on",
        "source_entity": "camera.source",
    }), False)
    camera.hass = hass
    camera._create_state(camera._config)

    camera_image_tasks = [
        asyncio.create_task(camera.async_camera_image()) for _ in range(2)
    ]
    await asyncio.wait_for(source_camera.image_started.wait(), 1)
    source_camera.release_image.set()
    assert await asyncio.gather(*camera_image_tasks) == [b"camera", b"camera"]

    stream_tasks = [asyncio.create_task(camera.stream_source()) for _ in range(2)]
    await asyncio.wait_for(source_camera.stream_started.wait(), 1)
    source_camera.release_stream.set()
    assert await asyncio.gather(*stream_tasks) == [
        "rtsp://camera",
        "rtsp://camera",
    ]


async def test_virtual_camera_alias_proxies_native_webrtc_signaling(hass):
    class NativeWebRTCCamera(Camera):
        _attr_supported_features = CameraEntityFeature.STREAM

        def __init__(self):
            super().__init__()
            self.offers = []
            self.candidates = []
            self.closed_sessions = []

        async def async_handle_async_webrtc_offer(
            self,
            offer_sdp,
            session_id,
            send_message,
        ):
            self.offers.append((offer_sdp, session_id))
            send_message("answer")

        async def async_on_webrtc_candidate(self, session_id, candidate):
            self.candidates.append((session_id, candidate))

        def close_webrtc_session(self, session_id):
            self.closed_sessions.append(session_id)

        def _async_get_webrtc_client_configuration(self):
            return WebRTCClientConfiguration(data_channel="camera-data")

    source = NativeWebRTCCamera()
    camera_component = Mock()
    camera_component.get_entity.return_value = source
    hass.data["camera"] = camera_component
    entity = VirtualCamera(CAMERA_SCHEMA({
        CONF_NAME: "WebRTC Alias",
        ATTR_ENTITY_ID: "camera.webrtc_alias",
        ATTR_UNIQUE_ID: "webrtc-alias",
        CONF_INITIAL_VALUE: "on",
        "source_entity": "camera.native_webrtc",
    }), False)
    entity.hass = hass
    entity._create_state(entity._config)
    entity._sync_stream_capabilities()

    send_message = Mock()
    candidate = Mock()
    await entity.async_handle_async_webrtc_offer(
        "offer-sdp",
        "session-1",
        send_message,
    )
    await entity.async_on_webrtc_candidate("session-1", candidate)
    entity.close_webrtc_session("session-1")

    assert entity.supported_features & CameraEntityFeature.STREAM
    assert entity.camera_capabilities.frontend_stream_types == {
        StreamType.WEB_RTC,
    }
    assert entity._async_get_webrtc_client_configuration().data_channel == (
        "camera-data"
    )
    assert source.offers == [("offer-sdp", "session-1")]
    assert source.candidates == [("session-1", candidate)]
    assert source.closed_sessions == ["session-1"]
    send_message.assert_called_once_with("answer")


async def test_virtual_camera_alias_keeps_hls_stream_type(hass):
    class HLSCamera(Camera):
        _attr_supported_features = CameraEntityFeature.STREAM

        async def stream_source(self):
            return "rtsp://camera/live"

    source = HLSCamera()
    camera_component = Mock()
    camera_component.get_entity.return_value = source
    hass.data["camera"] = camera_component
    entity = VirtualCamera(CAMERA_SCHEMA({
        CONF_NAME: "HLS Alias",
        ATTR_ENTITY_ID: "camera.hls_alias",
        ATTR_UNIQUE_ID: "hls-alias",
        CONF_INITIAL_VALUE: "on",
        "source_entity": "camera.hls_source",
    }), False)
    entity.hass = hass
    entity._create_state(entity._config)
    entity._sync_stream_capabilities()

    assert await entity.stream_source() == "rtsp://camera/live"
    assert entity.camera_capabilities.frontend_stream_types == {StreamType.HLS}
    assert CameraEntityFeature.ON_OFF in entity.supported_features


def test_virtual_camera_direct_stream_source_overrides_stale_feature_mask(hass):
    entity = VirtualCamera(CAMERA_SCHEMA({
        CONF_NAME: "Disabled Stream Alias",
        ATTR_ENTITY_ID: "camera.disabled_stream_alias",
        ATTR_UNIQUE_ID: "disabled-stream-alias",
        CONF_INITIAL_VALUE: "on",
        "source_entity": "camera.missing_source",
        "stream_source": "rtsp://example.test/live",
    }), False)
    entity.hass = hass

    assert entity._apply_native_template_value("supported_features", 0)
    entity._sync_stream_capabilities()

    assert entity.supported_features == CameraEntityFeature.STREAM
    assert entity.camera_capabilities.frontend_stream_types == {StreamType.HLS}


async def test_virtual_camera_refreshes_capabilities_when_source_reloads(hass):
    class HLSCamera(Camera):
        _attr_supported_features = CameraEntityFeature.STREAM

    class NativeWebRTCCamera(HLSCamera):
        _supports_native_async_webrtc = True

    source = HLSCamera()
    camera_component = Mock()
    camera_component.get_entity.side_effect = lambda _entity_id: source
    hass.data["camera"] = camera_component
    entity = VirtualCamera(CAMERA_SCHEMA({
        CONF_NAME: "Reloading Alias",
        ATTR_ENTITY_ID: "camera.reloading_alias",
        ATTR_UNIQUE_ID: "reloading-alias",
        CONF_INITIAL_VALUE: "on",
        "source_entity": "camera.reloading_source",
    }), False)
    entity.hass = hass
    entity._create_state(entity._config)
    entity.async_write_ha_state = Mock()
    entity.async_refresh_providers = AsyncMock()
    entity._camera_internal_added = True

    with patch(
        "custom_components.virtual_layer.camera.async_track_state_change_event",
    ) as track_state_change:
        entity._sync_source_camera_listener()
        source = NativeWebRTCCamera()
        source._supports_native_async_webrtc = True
        track_state_change.call_args.args[2](None)
        await hass.async_block_till_done()

    assert entity.camera_capabilities.frontend_stream_types == {
        StreamType.WEB_RTC,
    }
    entity.async_write_ha_state.assert_called_once_with()
    entity.async_refresh_providers.assert_awaited_once_with()


async def test_virtual_camera_refreshes_providers_when_stream_template_changes(hass):
    entity = VirtualCamera(CAMERA_SCHEMA({
        CONF_NAME: "Dynamic Stream",
        ATTR_ENTITY_ID: "camera.dynamic_stream",
        ATTR_UNIQUE_ID: "dynamic-stream",
        CONF_INITIAL_VALUE: "on",
    }), False)
    entity.hass = hass
    entity._create_state(entity._config)
    entity.async_refresh_providers = AsyncMock()
    entity._camera_internal_added = True

    assert not entity.supported_features & CameraEntityFeature.STREAM
    assert entity._apply_native_template_value(
        "stream_source",
        "rtsp://camera/live",
    )
    entity._native_templates_applied()
    await hass.async_block_till_done()

    assert entity.supported_features & CameraEntityFeature.STREAM
    assert entity.camera_capabilities.frontend_stream_types == {StreamType.HLS}
    entity.async_refresh_providers.assert_awaited_once_with()


async def test_virtual_image_renders_polygon_map_svg(hass):
    hass.states.async_set(
        "device_tracker.family_polygon",
        "Home",
        {
            ATTR_FRIENDLY_NAME: "Family <Phone>",
            ATTR_LATITUDE: 37.5,
            ATTR_LONGITUDE: 127.0,
        },
    )
    entity = VirtualImage(IMAGE_SCHEMA({
        CONF_NAME: "Family Polygon Map",
        ATTR_ENTITY_ID: "image.family_polygon_map",
        ATTR_UNIQUE_ID: "family_polygon_map",
        CONF_INITIAL_VALUE: "unknown",
        CONF_SOURCE_ENTITIES: ["device_tracker.family_polygon"],
        CONF_POLYGONAL_ZONE: {
            CONF_POLYGON_GEOJSON: {
                "type": "Feature",
                "properties": {"name": "Home"},
                "geometry": {
                    "type": "MultiPolygon",
                    "coordinates": [[[
                        [126.9, 37.4],
                        [127.1, 37.4],
                        [127.1, 37.6],
                        [126.9, 37.6],
                        [126.9, 37.4],
                    ]]],
                },
            },
        },
    }), hass, False)
    entity.hass = hass
    entity._create_state(entity._config)
    entity.async_write_ha_state = Mock()

    image = await entity.async_image()

    assert image is not None
    assert image.startswith(b"<svg ")
    assert b"Home" in image
    assert b'data-entity-id="device_tracker.family_polygon"' in image
    assert b"Family &lt;Phone&gt;" in image
    assert entity.image_last_updated is not None
    assert entity.state_attributes["content_type"] == "image/svg+xml"
    assert entity.state_attributes["image_type"] == "polygon_map"

    first_updated = entity.image_last_updated
    assert await entity.async_image() == image
    assert entity.image_last_updated == first_updated
    entity.async_write_ha_state.assert_called_once()


def test_virtual_polygon_map_ignores_overflowing_source_coordinates(hass):
    hass.states.async_set(
        "device_tracker.damaged",
        "not_home",
        {
            ATTR_LATITUDE: 10**10000,
            ATTR_LONGITUDE: 127.0,
        },
    )
    entity = VirtualImage(IMAGE_SCHEMA({
        CONF_NAME: "Damaged Polygon Map",
        ATTR_ENTITY_ID: "image.damaged_polygon_map",
        ATTR_UNIQUE_ID: "damaged_polygon_map",
        CONF_INITIAL_VALUE: "unknown",
        CONF_SOURCE_ENTITIES: ["device_tracker.damaged"],
        CONF_POLYGONAL_ZONE: {CONF_POLYGON_GEOJSON: {}},
    }), hass, False)
    entity.hass = hass

    assert entity._polygon_markers() == []


async def test_virtual_polygon_map_keeps_last_complete_zones_after_partial_reload(
    hass,
    monkeypatch,
):
    def zone(name):
        return {
            "name": name,
            "priority": 0,
            "polygons": [{
                "outer": [
                    (126.9, 37.4),
                    (127.1, 37.4),
                    (127.1, 37.6),
                    (126.9, 37.6),
                    (126.9, 37.4),
                ],
                "holes": [],
            }],
            "area": 0.04,
            "properties": {},
        }

    initial_zone = zone("Initial zone")
    updated_zone = zone("Updated zone")
    monkeypatch.setattr(
        image_platform,
        "load_polygon_zones",
        AsyncMock(side_effect=[
            ([initial_zone], []),
            ([updated_zone], ["secondary.geojson: offline"]),
            ([updated_zone], []),
            ([updated_zone], []),
        ]),
    )
    entity = VirtualImage(IMAGE_SCHEMA({
        CONF_NAME: "Cached Polygon Map",
        ATTR_ENTITY_ID: "image.cached_polygon_map",
        ATTR_UNIQUE_ID: "cached_polygon_map",
        CONF_INITIAL_VALUE: "unknown",
        CONF_POLYGONAL_ZONE: {CONF_POLYGON_GEOJSON: {}},
    }), hass, False)
    entity.hass = hass
    entity._create_state(entity._config)
    entity.async_write_ha_state = Mock()

    assert b"Initial zone" in await entity.async_image()
    retained = await entity.async_image()
    assert b"Initial zone" in retained
    assert b"Updated zone" not in retained
    assert entity.state_attributes["polygon_map_error"] == "secondary.geojson: offline"
    assert entity.state_attributes["polygon_zones"] == ["Initial zone"]

    refreshed = await entity.async_image()
    assert b"Updated zone" in refreshed
    assert entity.state_attributes["polygon_map_error"] is None
    assert entity.state_attributes["polygon_zones"] == ["Updated zone"]

    monkeypatch.setattr(
        image_platform,
        "render_polygon_map_svg",
        Mock(side_effect=ValueError("invalid map projection")),
    )
    assert await entity.async_image() is None
    assert entity.state_attributes["polygon_map_error"] == "invalid map projection"
    assert entity.state_attributes["polygon_zones"] == ["Updated zone"]


async def test_virtual_image_source_change_invalidates_once_before_next_fetch(hass):
    source = Mock()
    source.async_image = AsyncMock(return_value=b"image-bytes")
    image_component = Mock()
    image_component.get_entity.return_value = source
    hass.data["image"] = image_component
    entity = VirtualImage(IMAGE_SCHEMA({
        CONF_NAME: "Front Door Image",
        ATTR_ENTITY_ID: "image.front_door_alias",
        ATTR_UNIQUE_ID: "front_door_alias",
        CONF_INITIAL_VALUE: "unknown",
        "source_entity": "image.front_door",
    }), hass, False)
    entity.hass = hass
    entity._create_state(entity._config)
    entity.async_write_ha_state = Mock()
    entity.async_schedule_update_ha_state = Mock()

    await entity.async_image()
    entity._async_image_source_changed(None)
    invalidated_at = entity.image_last_updated
    await entity.async_image()

    assert entity.image_last_updated == invalidated_at
    assert entity.async_write_ha_state.call_count == 1
    entity.async_schedule_update_ha_state.assert_called_once()


async def test_virtual_image_url_change_invalidates_download_cache(hass):
    entity = VirtualImage(IMAGE_SCHEMA({
        CONF_NAME: "Changing URL Image",
        ATTR_ENTITY_ID: "image.changing_url",
        ATTR_UNIQUE_ID: "changing-url",
        CONF_INITIAL_VALUE: "unknown",
        "image_url": "https://example.test/one.jpg",
    }), hass, False)
    entity.hass = hass
    entity._create_state(entity._config)
    entity.async_write_ha_state = Mock()
    fetched_urls = []

    async def load_url(url):
        fetched_urls.append(url)
        return Mock(
            content=b"one" if url.endswith("one.jpg") else b"two",
            content_type="image/jpeg",
        )

    entity._async_load_image_from_url = AsyncMock(side_effect=load_url)

    assert await entity.async_image() == b"one"
    assert await entity.async_image() == b"one"
    assert entity._async_load_image_from_url.await_count == 1

    assert entity._apply_native_template_value(
        "image_url",
        "https://example.test/two.jpg",
    )
    assert await entity.async_image() == b"two"
    assert entity._async_load_image_from_url.await_count == 2
    assert fetched_urls == [
        "https://example.test/one.jpg",
        "https://example.test/two.jpg",
    ]


def test_virtual_image_retargets_dynamic_source_listener(hass):
    entity = VirtualImage(IMAGE_SCHEMA({
        CONF_NAME: "Dynamic Image Alias",
        ATTR_ENTITY_ID: "image.dynamic_alias",
        ATTR_UNIQUE_ID: "dynamic-alias",
        CONF_INITIAL_VALUE: "unknown",
        "source_entity": "image.first",
    }), hass, False)
    entity.hass = hass
    first_remove = Mock()
    second_remove = Mock()

    with patch(
        "custom_components.virtual_layer.image.async_track_state_change_event",
        side_effect=[first_remove, second_remove],
    ) as track_state_change:
        entity._sync_source_image_listener()
        assert entity._apply_native_template_value(
            "source_entity",
            "image.second",
        )
        entity._native_templates_applied()

    assert track_state_change.call_args_list[0].args[1] == ["image.first"]
    assert track_state_change.call_args_list[1].args[1] == ["image.second"]
    first_remove.assert_called_once_with()
    second_remove.assert_not_called()


async def test_virtual_image_source_transport_error_returns_no_image(hass):
    source = Mock()
    source.async_image = AsyncMock(side_effect=ClientConnectionError("offline"))
    image_component = Mock()
    image_component.get_entity.return_value = source
    hass.data["image"] = image_component
    entity = VirtualImage(IMAGE_SCHEMA({
        CONF_NAME: "Offline Image",
        ATTR_ENTITY_ID: "image.offline_alias",
        ATTR_UNIQUE_ID: "offline_alias",
        CONF_INITIAL_VALUE: "unknown",
        "source_entity": "image.offline",
    }), hass, False)
    entity.hass = hass
    entity._create_state(entity._config)

    assert await entity.async_image() is None
    assert entity.image_last_updated is None


async def test_virtual_camera_alias_transport_errors_return_no_media(hass):
    source = Mock()
    source.async_camera_image = AsyncMock(
        side_effect=ClientConnectionError("offline"),
    )
    source.stream_source = AsyncMock(side_effect=ClientConnectionError("offline"))
    camera_component = Mock()
    camera_component.get_entity.return_value = source
    hass.data["camera"] = camera_component
    entity = VirtualCamera(CAMERA_SCHEMA({
        CONF_NAME: "Offline Camera Alias",
        ATTR_ENTITY_ID: "camera.offline_alias",
        CONF_INITIAL_VALUE: "on",
        "source_entity": "camera.offline",
    }), False)
    entity.hass = hass
    entity._create_state(entity._config)

    assert await entity.async_camera_image() is None
    assert await entity.stream_source() is None


async def test_slow_camera_alias_reuses_last_snapshot_and_stream_source(
    hass,
    monkeypatch,
):
    """Bound slow aliases and preserve the last usable camera media."""
    class SlowCamera:
        image_calls = 0
        stream_calls = 0

        async def async_camera_image(self, **_kwargs):
            self.image_calls += 1
            if self.image_calls == 1:
                return b"last-snapshot"
            await asyncio.Event().wait()

        async def stream_source(self):
            self.stream_calls += 1
            if self.stream_calls == 1:
                return "rtsp://camera/last-good"
            await asyncio.Event().wait()

    source = SlowCamera()
    camera_component = Mock()
    camera_component.get_entity.return_value = source
    hass.data["camera"] = camera_component
    monkeypatch.setattr(camera_platform, "MEDIA_ALIAS_TIMEOUT", 0.01)
    entity = VirtualCamera(CAMERA_SCHEMA({
        CONF_NAME: "Slow Camera Alias",
        ATTR_ENTITY_ID: "camera.slow_alias",
        ATTR_UNIQUE_ID: "slow-camera-alias",
        CONF_INITIAL_VALUE: "on",
        "source_entity": "camera.slow",
    }), False)
    entity.hass = hass
    entity._create_state(entity._config)

    assert await entity.async_camera_image() == b"last-snapshot"
    assert await entity.stream_source() == "rtsp://camera/last-good"
    assert await entity.async_camera_image() == b"last-snapshot"
    assert await entity.stream_source() == "rtsp://camera/last-good"


async def test_slow_image_alias_reuses_last_successful_image(hass, monkeypatch):
    """Keep image snapshots available during a transient source timeout."""
    class SlowImage:
        calls = 0
        content_type = "image/png"

        async def async_image(self):
            self.calls += 1
            if self.calls == 1:
                return b"last-image"
            await asyncio.Event().wait()

    source = SlowImage()
    image_component = Mock()
    image_component.get_entity.return_value = source
    hass.data["image"] = image_component
    monkeypatch.setattr(image_platform, "MEDIA_ALIAS_TIMEOUT", 0.01)
    entity = VirtualImage(IMAGE_SCHEMA({
        CONF_NAME: "Slow Image Alias",
        ATTR_ENTITY_ID: "image.slow_alias",
        ATTR_UNIQUE_ID: "slow-image-alias",
        CONF_INITIAL_VALUE: "unknown",
        "source_entity": "image.slow",
    }), hass, False)
    entity.hass = hass
    entity._create_state(entity._config)
    entity.async_write_ha_state = Mock()

    assert await entity.async_image() == b"last-image"
    assert await entity.async_image() == b"last-image"
    assert entity.content_type == "image/png"


async def test_explicit_image_url_overrides_copied_image_alias(hass):
    """Honor a URL added after copying a source image entity."""
    source = Mock()
    source.async_image = AsyncMock(return_value=b"aliased-image")
    image_component = Mock()
    image_component.get_entity.return_value = source
    hass.data["image"] = image_component
    entity = VirtualImage(IMAGE_SCHEMA({
        CONF_NAME: "URL Override",
        ATTR_ENTITY_ID: "image.url_override",
        ATTR_UNIQUE_ID: "url-override",
        CONF_INITIAL_VALUE: "unknown",
        "source_entity": "image.source",
        "image_url": "https://example.test/current.png",
    }), hass, False)
    entity.hass = hass
    entity._create_state(entity._config)
    entity.async_write_ha_state = Mock()
    entity._async_load_image_from_url = AsyncMock(
        return_value=Mock(content=b"url-image", content_type="image/png"),
    )

    assert await entity.async_image() == b"url-image"
    source.async_image.assert_not_awaited()


def test_slow_stream_does_not_override_virtual_camera_availability(hass):
    """A reconnecting stream must not make snapshots and controls unavailable."""
    entity = VirtualCamera(CAMERA_SCHEMA({
        CONF_NAME: "Slow H.264 Camera",
        ATTR_ENTITY_ID: "camera.slow_h264",
        ATTR_UNIQUE_ID: "slow-h264",
        CONF_INITIAL_VALUE: "on",
        "stream_source": "rtsp://camera/live",
    }), False)
    entity.hass = hass
    entity._create_state(entity._config)
    entity.stream = Mock(available=False)

    assert entity.available is True
    entity._attr_available = False
    assert entity.available is False


async def test_h264_only_camera_uses_stream_frames_for_snapshots(hass, tmp_path):
    """Route camera.snapshot through HA's stream for an H.264-only camera."""
    entity = VirtualCamera(CAMERA_SCHEMA({
        CONF_NAME: "H.264 Snapshot Camera",
        ATTR_ENTITY_ID: "camera.h264_snapshot",
        ATTR_UNIQUE_ID: "h264-snapshot",
        CONF_INITIAL_VALUE: "on",
        "stream_source": "rtsp://camera/live",
    }), False)
    entity.hass = hass
    entity._create_state(entity._config)

    assert entity.use_stream_for_stills is True

    stream = Mock(available=True)
    stream.async_get_image = AsyncMock(return_value=b"h264-keyframe")
    entity.stream = stream
    snapshot_path = tmp_path / "snapshots" / "h264.jpg"
    hass.config.allowlist_external_dirs.add(str(tmp_path))
    service_call = Mock(data={"filename": Template(str(snapshot_path), hass)})

    await async_handle_snapshot_service(entity, service_call)

    assert snapshot_path.read_bytes() == b"h264-keyframe"
    stream.async_get_image.assert_awaited_once_with(
        width=None,
        height=None,
        wait_for_next_keyframe=True,
    )

    entity._image_path = "/config/www/fallback.jpg"
    assert entity.use_stream_for_stills is False


def test_dynamic_h264_url_updates_existing_home_assistant_stream(hass):
    """Restart an existing HA stream worker with a changed template URL."""
    entity = VirtualCamera(CAMERA_SCHEMA({
        CONF_NAME: "Dynamic H.264 Camera",
        ATTR_ENTITY_ID: "camera.dynamic_h264",
        ATTR_UNIQUE_ID: "dynamic-h264",
        CONF_INITIAL_VALUE: "on",
        "stream_source": "rtsp://camera/old",
    }), False)
    entity.hass = hass
    current_stream = Mock()
    entity.stream = current_stream

    assert entity._apply_native_template_value(
        "stream_source",
        "rtsp://camera/new",
    )
    current_stream.update_source.assert_called_once_with("rtsp://camera/new")


def test_dynamic_image_paths_update_inferred_content_types(hass):
    camera = VirtualCamera(CAMERA_SCHEMA({
        CONF_NAME: "Dynamic Camera Image",
        ATTR_ENTITY_ID: "camera.dynamic_image",
        ATTR_UNIQUE_ID: "dynamic-camera-image",
        CONF_INITIAL_VALUE: "on",
    }), False)
    camera.hass = hass
    assert camera._apply_native_template_value("image_path", "/config/image.png")
    assert camera.content_type == "image/png"

    image = VirtualImage(IMAGE_SCHEMA({
        CONF_NAME: "Dynamic Image",
        ATTR_ENTITY_ID: "image.dynamic_content_type",
        ATTR_UNIQUE_ID: "dynamic-image-content-type",
        CONF_INITIAL_VALUE: "unknown",
    }), hass, False)
    image.hass = hass
    assert image._apply_native_template_value(
        "image_url",
        "https://example.test/image.webp",
    )
    assert image.content_type == "image/webp"
    assert image._apply_native_template_value("svg", "<svg></svg>")
    assert image.content_type == "image/svg+xml"


def test_media_entities_cannot_be_merged(hass):
    hass.states.async_set("image.one", "unknown")
    hass.states.async_set("image.two", "unknown")
    hass.states.async_set("camera.one", "on")

    with pytest.raises(InvalidEntityReference):
        _reference_entity_defaults(hass, ["image.one", "image.two"])
    with pytest.raises(InvalidEntityReference):
        _reference_entity_defaults(hass, ["camera.one", "image.one"])


@pytest.mark.parametrize("device_class", ["smoke", "moisture", "gas"])
async def test_alarm_sensor_helper_uses_any_active_source(hass, device_class):
    hass.states.async_set(
        f"binary_sensor.{device_class}_one",
        "on",
        {"device_class": device_class},
    )
    hass.states.async_set(
        f"binary_sensor.{device_class}_two",
        "off",
        {"device_class": device_class},
    )

    defaults = _reference_entity_defaults(hass, [
        f"binary_sensor.{device_class}_one",
        f"binary_sensor.{device_class}_two",
    ])

    assert defaults[CONF_INITIAL_VALUE] == "on"
    assert " > 0 }}" in defaults["value_template"]
    assert " and " not in defaults["value_template"]
    template = Template(defaults["value_template"], hass)
    assert template.async_render(
        variables={
            f"{device_class}_one": "on",
            f"{device_class}_two": "off",
        },
        parse_result=False,
    ) == "True"
