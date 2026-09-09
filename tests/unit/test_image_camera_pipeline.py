"""Map rendering, concurrent readers and stalled-source regressions."""

import asyncio
from io import BytesIO
from unittest.mock import AsyncMock, Mock, patch

import pytest
from PIL import Image

from custom_components.virtual_layer import camera as camera_platform
from custom_components.virtual_layer.camera import CAMERA_SCHEMA, VirtualCamera, _image_as_jpeg

pytestmark = pytest.mark.unit


def map_bytes(color="red", size=(101, 99), mode="RGBA", format="PNG"):
    output = BytesIO()
    Image.new(mode, size, color).save(output, format)
    return output.getvalue()


@pytest.fixture
def image_camera(hass):
    source = Mock(async_image=AsyncMock(return_value=map_bytes()))
    component = Mock(get_entity=Mock(return_value=source))
    hass.data["image"] = component
    entity = VirtualCamera(CAMERA_SCHEMA({
        "name": "Map", "entity_id": "camera.map", "source_entity": "image.map",
    }), False)
    entity.hass = hass
    entity._create_state(entity._config)
    return entity, source, component


@pytest.mark.parametrize("mode,format,color", [
    ("RGBA", "PNG", (255, 0, 0, 128)),
    ("RGBA", "WEBP", (255, 0, 0, 128)),
    ("RGB", "JPEG", "red"),
    ("L", "PNG", 128),
    ("P", "PNG", 1),
    ("CMYK", "JPEG", (0, 255, 255, 0)),
])
def test_map_formats_transparency_and_letterbox(mode, format, color):
    jpeg = _image_as_jpeg(map_bytes(color, mode=mode, format=format))
    with Image.open(BytesIO(jpeg)) as result:
        assert result.size == (1280, 720)
        assert result.mode == "RGB"
        assert min(result.getpixel((0, 360))) > 245
        if mode == "RGBA":
            r, g, b = result.getpixel((640, 360))
            assert r > 245 and 115 < g < 140 and 115 < b < 140


def test_map_exif_orientation_is_applied_before_letterboxing():
    original = Image.new("RGB", (200, 100), "red")
    original.paste("blue", (100, 0, 200, 100))
    exif = original.getexif()
    exif[274] = 6  # Rotate 90 degrees clockwise for display.
    output = BytesIO()
    original.save(output, "JPEG", exif=exif)
    with Image.open(BytesIO(_image_as_jpeg(output.getvalue()))) as result:
        assert min(result.getpixel((400, 360))) > 245
        assert result.getpixel((640, 150))[0] > 240
        assert result.getpixel((640, 550))[2] > 240


async def test_concurrent_snapshots_share_fetch_and_survive_one_disconnect(image_camera):
    entity, source, _ = image_camera
    started, release = asyncio.Event(), asyncio.Event()

    async def delayed():
        started.set()
        await release.wait()
        return map_bytes()

    source.async_image.side_effect = delayed
    first = asyncio.create_task(entity.async_camera_image())
    await started.wait()
    second = asyncio.create_task(entity.async_camera_image())
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    release.set()
    assert await asyncio.wait_for(second, 2)
    source.async_image.assert_awaited_once()


async def test_source_edit_discards_late_old_snapshot(image_camera):
    entity, source, component = image_camera
    started = asyncio.Event()

    async def old_source():
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            # Some integrations finish their request even after cancellation.
            return map_bytes("red")

    source.async_image.side_effect = old_source
    old_request = asyncio.create_task(entity.async_camera_image())
    await started.wait()
    entity._apply_native_template_value("source_entity", "image.new_map")
    component.get_entity.return_value = Mock(async_image=AsyncMock(return_value=map_bytes("blue")))
    current = await entity.async_camera_image()
    assert await old_request is None
    with Image.open(BytesIO(current)) as result:
        assert result.getpixel((640, 360))[2] > 240
    assert entity._last_camera_image == current


async def test_slow_refresh_does_not_pause_video_and_removal_cancels_fetch(image_camera, monkeypatch):
    entity, source, _ = image_camera
    entity._apply_native_template_value("frame_interval", 0.01)
    started, cancelled = asyncio.Event(), asyncio.Event()
    call_count = 0

    async def delayed_refresh():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return map_bytes()
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    source.async_image.side_effect = delayed_refresh
    frames_during_refresh = []

    async def write(frame):
        if started.is_set():
            frames_during_refresh.append(frame)
            if len(frames_during_refresh) == 3:
                raise ConnectionResetError()

    response = Mock(prepare=AsyncMock(), write=AsyncMock(side_effect=write))
    with patch.object(camera_platform.web, "StreamResponse", return_value=response):
        await asyncio.wait_for(entity.handle_async_mjpeg_stream(Mock()), 2)
    assert len(frames_during_refresh) == 3
    assert frames_during_refresh[0] == frames_during_refresh[-1]
    with patch.object(camera_platform.VirtualEntity, "async_will_remove_from_hass", new=AsyncMock()):
        await entity.async_will_remove_from_hass()
    assert cancelled.is_set()
    assert entity._image_fetch_task is None
