"""Patrol must not proxy camera-only commands or strand recording disabled."""
import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.virtual_layer.camera import CAMERA_SCHEMA, VirtualCamera


def camera(hass):
    entity = VirtualCamera(CAMERA_SCHEMA({
        "name": "Patrol", "entity_id": "camera.patrol", "unique_id": "patrol",
        "source_entities": ["camera.video"],
        "onvif_patrol_target": "camera.ptz",
        "frigate_recording_switch": "switch.recordings",
        "frigate_recording_during_patrol": "off",
    }), False)
    entity.hass = hass
    entity.async_write_ha_state = Mock()
    hass.states.async_set("switch.recordings", "on")
    return entity


async def test_start_stop_restores_recording_even_when_ptz_stop_fails(hass):
    entity = camera(hass)
    calls = []
    async def switch(call):
        calls.append(call.service)
    hass.services.async_register("switch", "turn_off", switch)
    hass.services.async_register("switch", "turn_on", switch)
    entered = asyncio.Event()
    async def ptz(data):
        if data["move_mode"] == "Stop":
            raise HomeAssistantError("offline")
        entered.set()
    entity._async_call_onvif_ptz = ptz
    await entity.async_start_patrol()
    await entered.wait()
    await entity.async_start_patrol()
    with pytest.raises(HomeAssistantError):
        await entity.async_stop_patrol()
    assert calls == ["turn_off", "turn_on"]
    assert entity._patrol_task is None
    assert entity._frigate_previous_switch_states == {}


async def test_move_failure_restores_recording(hass):
    entity = camera(hass)
    calls = []
    async def switch(call):
        calls.append(call.service)
    hass.services.async_register("switch", "turn_off", switch)
    hass.services.async_register("switch", "turn_on", switch)
    entity._async_call_onvif_ptz = AsyncMock(side_effect=HomeAssistantError("offline"))
    await entity.async_start_patrol()
    task = entity._patrol_task
    await task
    assert calls == ["turn_off", "turn_on"]
    assert entity._patrol_task is None


async def test_unknown_recording_state_prevents_movement(hass):
    entity = camera(hass)
    hass.states.async_set("switch.recordings", "unavailable")
    with pytest.raises(HomeAssistantError):
        await entity.async_start_patrol()
    assert entity._patrol_task is None
