"""Patrol must not proxy camera-only commands or strand recording disabled."""
import asyncio
from unittest.mock import AsyncMock, Mock
from unittest.mock import patch, call
from types import SimpleNamespace

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
        hass.states.async_set(call.data["entity_id"], call.service.removeprefix("turn_"))
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
        hass.states.async_set(call.data["entity_id"], call.service.removeprefix("turn_"))
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


@pytest.mark.parametrize("mode", ["horizontal", "vertical", "grid"])
async def test_asymmetric_patrol_has_zero_net_displacement(hass, mode):
    entity = camera(hass)
    entity._patrol_mode = mode
    entity._patrol_pan = (0.2, 0.7)
    entity._patrol_tilt = (0.1, 0.3)
    moves = entity._patrol_moves()
    assert sum(distance * (1 if pan == "RIGHT" else -1) for pan, tilt, distance in moves if pan) == pytest.approx(0)
    assert sum(distance * (1 if tilt == "UP" else -1) for pan, tilt, distance in moves if tilt) == pytest.approx(0)
    assert all(not (pan and tilt) for pan, tilt, distance in moves)
    assert all(distance <= (0.7 if pan else 0.3) for pan, tilt, distance in moves)


async def test_zero_range_rejected_before_disabling_recording(hass):
    entity = camera(hass)
    entity._patrol_pan = (0, 0)
    entity._async_set_frigate_patrol_switches = AsyncMock()
    with pytest.raises(HomeAssistantError):
        await entity.async_start_patrol()
    entity._async_set_frigate_patrol_switches.assert_not_called()


async def test_patrol_captures_and_returns_exact_coordinates(hass):
    entity = camera(hass)
    service = SimpleNamespace(
        GetStatus=AsyncMock(return_value=SimpleNamespace(Position=SimpleNamespace(
            PanTilt=SimpleNamespace(x=-0.25, y=0.4, space="urn:test:space"),
        ))), AbsoluteMove=AsyncMock(),
    )
    target = SimpleNamespace(
        profile=SimpleNamespace(token="profile1"),
        device=SimpleNamespace(device=SimpleNamespace(create_ptz_service=AsyncMock(return_value=service))),
    )
    hass.data["camera"] = SimpleNamespace(get_entity=lambda _: target)
    await entity._async_capture_patrol_origin()
    await entity._async_return_patrol_origin()
    service.AbsoluteMove.assert_awaited_once_with({
        "ProfileToken": "profile1",
        "Position": {"PanTilt": {"x": -0.25, "y": 0.4, "space": "urn:test:space"}},
    })
    await entity._async_return_patrol_origin()
    assert service.AbsoluteMove.await_count == 1


async def test_automatic_cycle_runs_on_and_off_periods(hass):
    entity = camera(hass)
    entity._patrol_on_seconds = 60
    entity._patrol_off_seconds = 600
    entity._async_start_patrol = AsyncMock()
    entity._async_stop_patrol = AsyncMock()
    with patch("custom_components.virtual_layer.camera.asyncio.sleep", AsyncMock(side_effect=[None, asyncio.CancelledError])) as sleep:
        with pytest.raises(asyncio.CancelledError):
            await entity._async_patrol_schedule()
    assert sleep.await_args_list == [call(60), call(600)]
    entity._async_start_patrol.assert_awaited_once()
    entity._async_stop_patrol.assert_awaited_once()


async def test_manual_stop_cancels_future_automatic_starts(hass):
    entity = camera(hass)
    entity._patrol_schedule_task = asyncio.create_task(asyncio.Event().wait())
    task = entity._patrol_schedule_task
    await entity.async_stop_patrol()
    assert task.cancelled()
    assert entity._patrol_schedule_task is None


async def test_movement_recording_is_restored_before_quiet_interval(hass):
    entity = camera(hass)
    entity._patrol_recording_scope = "movement"
    events = []
    async def recording(*, restore):
        events.append("restore" if restore else "disable")
    async def move(data):
        events.append("move")
    async def settle():
        events.append("settle_stop")
    entity._async_set_frigate_patrol_switches = recording
    entity._async_call_onvif_ptz = move
    entity._async_settle_patrol_move = settle
    with patch("custom_components.virtual_layer.camera.asyncio.sleep", AsyncMock(side_effect=asyncio.CancelledError)):
        with pytest.raises(asyncio.CancelledError):
            await entity._async_patrol_loop()
    assert events == ["disable", "move", "settle_stop", "restore"]
