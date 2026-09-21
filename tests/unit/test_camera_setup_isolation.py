"""Optional patrol failures must not remove otherwise usable cameras."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.exceptions import HomeAssistantError
import pytest
import voluptuous as vol

from custom_components.virtual_layer.camera import CAMERA_SCHEMA, VirtualCamera, async_setup_entry
from custom_components.virtual_layer.entity import VirtualEntity
from custom_components.virtual_layer.camera import validate_domain_options


async def test_bad_camera_does_not_abort_platform(hass):
    add = Mock()
    good = {"name": "Good", "entity_id": "camera.good", "unique_id": "good"}
    with patch("custom_components.virtual_layer.camera.get_entity_configs", return_value=[
        {**good, "entity_id": "camera.bad", "onvif_patrol_interval": "bad"}, good,
    ]):
        await async_setup_entry(hass, SimpleNamespace(data={"group_name": "Cameras"}), add)
    assert len(add.call_args.args[0]) == 1
    assert add.call_args.args[0][0].entity_id == "camera.good"


async def test_patrol_start_failure_keeps_camera_loaded(hass):
    camera = VirtualCamera(CAMERA_SCHEMA({
        "name": "Camera", "entity_id": "camera.good", "unique_id": "good",
        "onvif_patrol_enabled": True, "onvif_patrol_target": "camera.ptz",
    }), False)
    camera.hass = hass
    camera.async_start_patrol = AsyncMock(side_effect=HomeAssistantError("MQTT unavailable"))
    camera._sync_source_camera_listener = Mock()
    with patch.object(VirtualEntity, "async_added_to_hass", AsyncMock()):
        await camera.async_added_to_hass()
    camera.async_start_patrol.assert_awaited_once()


@pytest.mark.parametrize("config", [
    {"onvif_patrol_target": "image.source"},
    {"entity_id": "camera.self", "onvif_patrol_target": "camera.self"},
    {"frigate_recording_switch": "camera.source"},
    {"onvif_patrol_interval": float("nan")},
    {"onvif_patrol_speed": float("nan")},
    {"onvif_patrol_pan_min": float("inf")},
])
def test_invalid_patrol_control_values(config):
    with pytest.raises(vol.Invalid):
        validate_domain_options(config)
