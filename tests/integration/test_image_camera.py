"""Real camera HTTP endpoint, registry lifecycle and H.264 encoding coverage."""

import asyncio
import shutil
import shlex
import json
from io import BytesIO
from unittest.mock import patch

import pytest
from PIL import Image
from homeassistant.helpers import entity_registry as er
from homeassistant.components.homekit.type_cameras import VIDEO_OUTPUT
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.virtual_layer.const import (
    ATTR_DEVICES, ATTR_GROUP_NAME, COMPONENT_DOMAIN,
)
from custom_components.virtual_layer.camera import _image_as_jpeg

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("size", [(33, 31), (1, 1), (501, 1501), (10000, 1)])
async def test_map_h264_accepts_odd_and_portrait_dimensions(size):
    binary = shutil.which("ffmpeg")
    if not binary:
        pytest.skip("FFmpeg is required for the real H.264 encoder test")
    output = BytesIO()
    Image.new("RGBA", size, (255, 0, 0, 128)).save(output, "PNG")
    jpeg = _image_as_jpeg(output.getvalue())
    process = await asyncio.create_subprocess_exec(
        binary, "-hide_banner", "-loglevel", "error", "-f", "image2pipe",
        "-i", "pipe:0", "-frames:v", "1", "-c:v", "libx264",
        "-profile:v", "baseline", "-tune", "zerolatency", "-pix_fmt", "yuv420p",
        "-f", "h264", "pipe:1", stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    encoded, errors = await asyncio.wait_for(process.communicate(jpeg), 10)
    assert process.returncode == 0, errors.decode()
    assert len(encoded) > 100


async def test_h264_decodes_updated_maps_without_resolution_changes():
    binary = shutil.which("ffmpeg")
    if not binary:
        pytest.skip("FFmpeg is required for the real H.264 decoder test")
    frames = []
    for size, color in [((801, 799), "red"), ((999, 1111), "lime"), ((1601, 401), "blue")]:
        output = BytesIO()
        Image.new("RGB", size, color).save(output, "PNG")
        frames.append(_image_as_jpeg(output.getvalue()))
    encoder = await asyncio.create_subprocess_exec(
        binary, "-hide_banner", "-loglevel", "error", "-f", "image2pipe", "-i", "pipe:0",
        "-c:v", "libx264", "-profile:v", "baseline", "-tune", "zerolatency",
        "-pix_fmt", "yuv420p", "-frames:v", "3", "-f", "h264", "pipe:1",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    encoded, errors = await asyncio.wait_for(encoder.communicate(b"".join(frames)), 10)
    assert encoder.returncode == 0, errors.decode()
    decoder = await asyncio.create_subprocess_exec(
        binary, "-hide_banner", "-loglevel", "error", "-f", "h264", "-i", "pipe:0",
        "-pix_fmt", "rgb24", "-frames:v", "3", "-f", "rawvideo", "pipe:1",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    decoded, errors = await asyncio.wait_for(decoder.communicate(encoded), 10)
    assert decoder.returncode == 0, errors.decode()
    frame_length = 1280 * 720 * 3
    assert len(decoded) == frame_length * 3
    for frame_index in range(3):
        center = frame_index * frame_length + (360 * 1280 + 640) * 3
        pixel = decoded[center:center + 3]
        assert pixel[frame_index] > 230
        assert all(value < 30 for channel, value in enumerate(pixel) if channel != frame_index)


@pytest.mark.parametrize("profile", ["baseline", "main", "high"])
async def test_image_camera_http_h264_and_reload(hass, hass_client, tmp_path, profile):
    image_path = tmp_path / "map.png"
    Image.new("RGB", (320, 240), "red").save(image_path)
    hass.config.allowlist_external_dirs.add(str(tmp_path))
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        title="Maps",
        data={ATTR_GROUP_NAME: "Maps"},
        options={ATTR_DEVICES: {"Vacuum": [
            {"platform": "image", "name": "Map Source", "image_path": str(image_path)},
            {"platform": "camera", "name": "Map Camera", "source_entity": "image.map_source"},
        ]}},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    client = await hass_client()
    entity = hass.data["camera"].get_entity("camera.map_camera")
    registry = er.async_get(hass)
    assert registry.async_get("camera.map_camera").device_id == registry.async_get("image.map_source").device_id
    snapshot = await entity.async_camera_image()
    assert snapshot.startswith(b"\xff\xd8")
    with patch("custom_components.virtual_layer.camera.get_url", return_value=str(client.make_url("/")).rstrip("/")):
        url = await entity.stream_source()
    response = await client.get(url.split(str(client.make_url("/")), 1)[-1])
    assert response.status == 200
    assert b"Content-Type: image/jpeg" in await response.content.readuntil(b"\r\n\r\n")
    response.close()

    # A real encoder verifies constant maps provide enough frames to produce
    # H.264. The normal suite remains runnable without a system FFmpeg binary.
    if binary := shutil.which("ffmpeg"):
        # Exercise HA HomeKit's shipped encoder flags, replacing only the
        # SRTP transport with a pipe so we can inspect the encoded bitstream.
        encoder_flags = VIDEO_OUTPUT.split("-payload_type", 1)[0].format(
            v_map="0:v:0", v_codec="libx264", v_profile=f"-profile:v {profile} ",
            fps=30, v_max_bitrate=300, v_bufsize=1200,
        )
        process = await asyncio.create_subprocess_exec(
            binary, "-hide_banner", "-loglevel", "error", "-i", url,
            *shlex.split(encoder_flags), "-frames:v", "5",
            "-f", "h264", "pipe:1",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            encoded, errors = await asyncio.wait_for(process.communicate(), 30)
            assert process.returncode == 0, errors.decode()
            assert len(encoded) > 100
            assert b"\x00\x00\x00\x01" in encoded
            if probe := shutil.which("ffprobe"):
                inspect = await asyncio.create_subprocess_exec(
                    probe, "-v", "error", "-f", "h264", "-i", "pipe:0",
                    "-show_streams", "-of", "json", stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                metadata, errors = await inspect.communicate(encoded)
                assert inspect.returncode == 0, errors.decode()
                video = json.loads(metadata)["streams"][0]
                assert video["codec_name"] == "h264"
                # JPEG input can preserve full-range signalling in recent
                # FFmpeg versions; both names describe 8-bit 4:2:0 H.264.
                assert video["pix_fmt"] in {"yuv420p", "yuvj420p"}
                assert (video["width"], video["height"]) == (1280, 720)
                assert profile in video["profile"].lower()
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

    Image.new("RGB", (320, 240), "blue").save(image_path)
    assert await entity.async_camera_image() != snapshot
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.data["camera"].get_entity("camera.map_camera").async_camera_image()
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("camera.map_camera") is None
