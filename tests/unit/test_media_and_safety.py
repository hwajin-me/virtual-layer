"""Regression tests for non-mergeable media and safety sensor helpers."""

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest
from aiohttp import ClientConnectionError
from homeassistant.components.camera import Camera, CameraEntityFeature
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


def test_virtual_camera_explicit_features_override_inferred_stream(hass):
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

    assert entity.supported_features == CameraEntityFeature(0)
    assert entity.camera_capabilities.frontend_stream_types == set()


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
    entity._async_load_image_from_url = AsyncMock(side_effect=[
        Mock(content=b"one", content_type="image/jpeg"),
        Mock(content=b"two", content_type="image/jpeg"),
    ])

    assert await entity.async_image() == b"one"
    assert await entity.async_image() == b"one"
    assert entity._async_load_image_from_url.await_count == 1

    assert entity._apply_native_template_value(
        "image_url",
        "https://example.test/two.jpg",
    )
    assert await entity.async_image() == b"two"
    assert entity._async_load_image_from_url.await_count == 2


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
