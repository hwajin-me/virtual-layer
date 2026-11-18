"""Camera registration and stream updates tolerate HA cache API changes."""

from unittest.mock import Mock

import pytest
from homeassistant.components.camera import Camera, CameraEntityFeature

from custom_components.virtual_layer.camera import CAMERA_SCHEMA, VirtualCamera


@pytest.mark.parametrize("legacy_cache_api", [False, True])
async def test_stream_capabilities_refresh_with_optional_cache_api(
    hass, monkeypatch, legacy_cache_api
):
    invalidate = Mock()
    if legacy_cache_api:
        monkeypatch.setattr(
            Camera, "_invalidate_camera_capabilities_cache", invalidate, raising=False
        )
    else:
        monkeypatch.delattr(Camera, "_invalidate_camera_capabilities_cache", raising=False)

    camera = VirtualCamera(CAMERA_SCHEMA({
        "name": "Compatible camera",
        "entity_id": "camera.compatible",
        "unique_id": "compatible",
    }), False)
    camera.hass = hass
    camera._sync_stream_capabilities()
    assert CameraEntityFeature.STREAM not in camera.supported_features

    camera._stream_source = "rtsp://example.invalid/synthetic"
    camera._sync_stream_capabilities()
    assert CameraEntityFeature.STREAM in camera.supported_features

    camera._stream_source = None
    camera._sync_stream_capabilities()
    assert CameraEntityFeature.STREAM not in camera.supported_features
    assert invalidate.call_count == (3 if legacy_cache_api else 0)
