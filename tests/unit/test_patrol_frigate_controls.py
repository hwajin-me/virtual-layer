"""Patrol controls all Frigate functions, with acknowledgements and isolation."""
import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.virtual_layer.camera import CAMERA_SCHEMA, VirtualCamera
from custom_components.virtual_layer.frigate_source import frigate_camera_switches


def setup_camera(hass, **options):
    entry = MockConfigEntry(domain="frigate")
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    for camera_name in ("front", "other"):
        device = dr.async_get(hass).async_get_or_create(
            config_entry_id=entry.entry_id, identifiers={("frigate", camera_name)},
        )
        for domain, suffix in (("camera", ""), ("switch", "_detect"), ("switch", "_motion"), ("switch", "_recordings")):
            entity = registry.async_get_or_create(
                domain, "frigate", f"{entry.entry_id}:{domain}:{camera_name}{suffix}",
                suggested_object_id=f"{camera_name}{suffix}", config_entry=entry, device_id=device.id,
            )
            hass.states.async_set(entity.entity_id, "on")
    entity = VirtualCamera(CAMERA_SCHEMA({
        "name": "Patrol", "entity_id": "camera.patrol", "unique_id": "patrol",
        "source_entities": ["camera.front"], "onvif_patrol_target": "camera.ptz",
        "is_recording": True, **options,
    }), False)
    entity.hass = hass
    entity.async_write_ha_state = Mock()
    entity._async_call_onvif_ptz = AsyncMock()
    return entity


@pytest.mark.parametrize("previous", ["on", "off"])
async def test_patrol_disables_all_controls_and_restores_exact_states(hass, previous):
    entity = setup_camera(hass)
    hass.states.async_set("switch.front_recordings", previous)
    calls = []

    async def switch(call):
        target = call.data["entity_id"]
        value = call.service.removeprefix("turn_")
        if target == "switch.front_motion" and value == "off":
            assert hass.states.get("switch.front_detect").state == "off"
        calls.append((target, value))
        hass.states.async_set(target, value)

    hass.services.async_register("switch", "turn_off", switch)
    hass.services.async_register("switch", "turn_on", switch)
    await entity.async_start_patrol()
    assert entity.is_recording is False
    assert entity.state != "recording"
    assert calls == [(f"switch.front_{kind}", "off") for kind in ("detect", "motion", "recordings")]
    assert hass.states.get("switch.other_recordings").state == "on"
    await entity.async_stop_patrol()
    assert calls[3:] == [("switch.front_recordings", previous), ("switch.front_motion", "on"), ("switch.front_detect", "on")]
    assert entity.is_recording == (previous == "on")


async def test_discovery_survives_entity_rename(hass):
    setup_camera(hass)
    er.async_get(hass).async_update_entity("switch.front_detect", new_entity_id="switch.renamed")
    assert frigate_camera_switches(hass, "camera.front")["detect"] == "switch.renamed"
    assert frigate_camera_switches(hass, "camera.not_frigate") == {}


async def test_unconfirmed_off_prevents_patrol_and_unsubscribes(hass):
    entity = setup_camera(hass)
    hass.services.async_register("switch", "turn_off", AsyncMock())
    hass.services.async_register("switch", "turn_on", AsyncMock())
    timeout = asyncio.timeout
    with patch("custom_components.virtual_layer.camera.asyncio.timeout", side_effect=lambda _: timeout(0.01)):
        with pytest.raises(HomeAssistantError, match="did not confirm"):
            await entity.async_start_patrol()
    entity._async_call_onvif_ptz.assert_not_called()
    assert entity._patrol_task is None
    assert entity._frigate_previous_switch_states == {}


async def test_keep_does_not_change_frigate(hass):
    entity = setup_camera(hass, frigate_recording_during_patrol="keep")
    entity._async_confirm_frigate_switch = AsyncMock()
    await entity._async_set_frigate_patrol_switches(restore=False)
    entity._async_confirm_frigate_switch.assert_not_called()


async def test_mqtt_uses_all_three_topics_in_dependency_order(hass):
    entity = setup_camera(hass, frigate_mqtt_recordings_topic="custom/front/recordings")
    events = []

    class Controller:
        def __init__(self, hass, topic):
            self.topic = topic
        async def apply(self, value):
            events.append((self.topic, value))
        async def restore(self):
            events.append((self.topic, "restore"))

    with patch("custom_components.virtual_layer.camera.PatrolRecording", Controller):
        await entity._async_set_frigate_patrol_switches(restore=False)
        assert entity.is_recording is False
        await entity._async_set_frigate_patrol_switches(restore=True)
    assert events == [(f"custom/front/{kind}", "off") for kind in ("detect", "motion", "recordings")] + [
        (f"custom/front/{kind}", "restore") for kind in ("recordings", "motion", "detect")]
