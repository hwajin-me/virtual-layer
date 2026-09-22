"""Absolute patrol route validation and runtime movement ordering."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
import voluptuous as vol
from homeassistant.exceptions import HomeAssistantError

from custom_components.virtual_layer.camera import CAMERA_SCHEMA, VirtualCamera, validate_domain_options
from custom_components.virtual_layer import patrol_positions as positions


ROUTE = {"target": "camera.ptz", "points": [
    {"name": "Door", "position": {"x": -0.4, "y": 0.1, "space": "urn:space"}},
    {"name": "Window", "position": {"x": 0.3, "y": -0.2, "space": "urn:space"}},
]}


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, "0.5", 10**500, None])
def test_invalid_coordinates(value):
    route = deepcopy(ROUTE)
    route["points"][0]["position"]["x"] = value
    with pytest.raises(vol.Invalid):
        positions.validate_route(route)


@pytest.mark.parametrize("mutation", [
    lambda route: route.update(points=[]),
    lambda route: route.update(points=route["points"] * 17),
    lambda route: route.update(points=[route["points"][0]] * 2),
    lambda route: route["points"][0].update(name=" "),
    lambda route: route["points"][0]["position"].update(space="urn:other"),
    lambda route: route.update(target="switch.ptz"),
])
def test_invalid_routes(mutation):
    route = deepcopy(ROUTE)
    mutation(route)
    with pytest.raises(vol.Invalid):
        positions.validate_route(route)


def test_wrong_camera_cannot_reuse_route():
    with pytest.raises(vol.Invalid):
        validate_domain_options({"onvif_patrol_target": "camera.other", positions.CONF_PATROL_ROUTE: ROUTE})


@pytest.mark.parametrize("scope", ["patrol", "movement"])
async def test_runtime_repeats_actual_positions_with_existing_recording_scope(hass, scope):
    entity = VirtualCamera(CAMERA_SCHEMA({
        "name": "Patrol", "entity_id": "camera.front", "unique_id": "front",
        "onvif_patrol_target": "camera.ptz", positions.CONF_PATROL_ROUTE: ROUTE,
        "onvif_patrol_speed": 0.2, "onvif_patrol_interval": 120,
        "patrol_recording_scope": scope,
    }), False)
    entity.hass = hass
    entity.async_write_ha_state = Mock()
    entity._async_set_frigate_patrol_switches = AsyncMock()
    entity._async_settle_patrol_move = AsyncMock()
    entity._async_call_onvif_ptz = AsyncMock()
    with patch.object(positions, "move_to", AsyncMock()) as move, patch(
        "custom_components.virtual_layer.camera.asyncio.sleep", AsyncMock(side_effect=[None, None, asyncio.CancelledError]),
    ) as sleep:
        with pytest.raises(asyncio.CancelledError):
            await entity._async_patrol_loop()
    assert [call.args[2] for call in move.await_args_list] == [ROUTE["points"][i]["position"] for i in (0, 1, 0)]
    assert all(call.args[3] == 0.2 for call in move.await_args_list)
    assert all(call.args == (120,) for call in sleep.await_args_list)
    entity._async_call_onvif_ptz.assert_not_called()  # no relative patrol commands
    assert entity._async_settle_patrol_move.await_count == (3 if scope == "movement" else 0)
    assert entity._async_set_frigate_patrol_switches.await_count == (6 if scope == "movement" else 0)


async def test_capture_requires_stationary_valid_coordinates(hass):
    service = SimpleNamespace(GetStatus=AsyncMock(return_value=SimpleNamespace(
        Position=SimpleNamespace(PanTilt=SimpleNamespace(x=0.1, y=-0.2, space="urn:space")),
        MoveStatus=SimpleNamespace(PanTilt="MOVING"),
    )))
    with patch.object(positions, "client", AsyncMock(return_value=(service, "profile"))):
        with pytest.raises(HomeAssistantError, match="Stop"):
            await positions.capture(hass, "camera.ptz")
        service.GetStatus.return_value.MoveStatus.PanTilt = "IDLE"
        assert await positions.capture(hass, "camera.ptz") == {"x": 0.1, "y": -0.2, "space": "urn:space"}


@pytest.mark.parametrize("relative", [True, False])
async def test_jog_stops_even_when_movement_fails(hass, relative):
    service = SimpleNamespace(RelativeMove=AsyncMock(side_effect=HomeAssistantError("offline")),
                              ContinuousMove=AsyncMock(side_effect=HomeAssistantError("offline")), Stop=AsyncMock())
    target = SimpleNamespace(profile=SimpleNamespace(ptz=SimpleNamespace(relative=relative, continuous=True)))
    hass.data["camera"] = SimpleNamespace(get_entity=lambda _: target)
    with patch.object(positions, "client", AsyncMock(return_value=(service, "profile"))):
        with pytest.raises(HomeAssistantError):
            await positions.jog(hass, "camera.ptz", "left", 0.05, 0.2)
    service.Stop.assert_awaited_once_with({"ProfileToken": "profile", "PanTilt": True, "Zoom": False})


async def test_unavailable_absolute_client_fails_before_disabling_frigate(hass):
    entity = VirtualCamera(CAMERA_SCHEMA({
        "name": "Patrol", "entity_id": "camera.front", "unique_id": "front",
        "onvif_patrol_target": "camera.ptz", positions.CONF_PATROL_ROUTE: ROUTE,
    }), False)
    entity.hass = hass
    entity._async_set_frigate_patrol_switches = AsyncMock()
    with pytest.raises(HomeAssistantError):
        await entity.async_start_patrol()
    entity._async_set_frigate_patrol_switches.assert_not_called()
