"""Serializable patrol waypoints and bounded access to the existing ONVIF client."""
import asyncio
import math

import voluptuous as vol
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

CONF_PATROL_ROUTE = "onvif_patrol_route"
MAX_POINTS = 32


def position(value):
    """Keep ONVIF coordinate spaces; never guess or normalize device units."""
    if not isinstance(value, dict) or set(value) - {"x", "y", "space"}:
        raise vol.Invalid("Invalid ONVIF position")
    result = {}
    for axis in ("x", "y"):
        number = value.get(axis)
        if isinstance(number, bool) or not isinstance(number, (float, int)):
            raise vol.Invalid("Invalid ONVIF coordinate")
        if abs(number) > 1_000_000 or not math.isfinite(number):
            raise vol.Invalid("Invalid ONVIF coordinate")
        result[axis] = float(number)
    if "space" in value:
        space = value["space"]
        if not isinstance(space, str) or not 1 <= len(space) <= 512:
            raise vol.Invalid("Invalid ONVIF coordinate space")
        result["space"] = space
    return result


def validate_route(value):
    if not isinstance(value, dict) or set(value) != {"target", "points"}:
        raise vol.Invalid("Invalid patrol route")
    target = cv.entity_id(value["target"])
    if not target.startswith("camera."):
        raise vol.Invalid("Patrol target must be a camera")
    points = value["points"]
    if not isinstance(points, list) or not 2 <= len(points) <= MAX_POINTS:
        raise vol.Invalid("Save between 2 and 32 patrol positions")
    normalized = []
    for point in points:
        if not isinstance(point, dict) or set(point) != {"name", "position"}:
            raise vol.Invalid("Invalid patrol point")
        name = point["name"]
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 64:
            raise vol.Invalid("Name each patrol position")
        normalized.append({"name": name.strip(), "position": position(point["position"])})
    if len({(p["position"]["x"], p["position"]["y"]) for p in normalized}) < 2:
        raise vol.Invalid("Choose at least two different patrol positions")
    if len({p["position"].get("space") for p in normalized}) != 1:
        raise vol.Invalid("Patrol positions must use the same coordinate space")
    return {"target": target, "points": normalized}


async def client(hass, entity_id):
    component = hass.data.get("camera")
    get_entity = getattr(component, "get_entity", None)
    target = get_entity(entity_id) if callable(get_entity) else None
    device = getattr(getattr(target, "device", None), "device", None)
    profile = getattr(target, "profile", None)
    token = getattr(profile, "token", None)
    if device is None or token is None:
        raise HomeAssistantError("ONVIF camera is unavailable")
    ptz = getattr(profile, "ptz", None)
    if ptz is not None and not getattr(ptz, "absolute", False):
        raise HomeAssistantError("This camera does not support absolute patrol positions")
    async with asyncio.timeout(10):
        return await device.create_ptz_service(), token


async def capture(hass, entity_id):
    service, token = await client(hass, entity_id)
    async with asyncio.timeout(10):
        status = await service.GetStatus({"ProfileToken": token})
    if str(getattr(getattr(status, "MoveStatus", None), "PanTilt", "")).upper() == "MOVING":
        raise HomeAssistantError("Stop the camera before capturing a position")
    pan_tilt = status.Position.PanTilt
    coordinates = {"x": pan_tilt.x, "y": pan_tilt.y}
    if getattr(pan_tilt, "space", None):
        coordinates["space"] = pan_tilt.space
    return position(coordinates)


async def move_to(hass, entity_id, coordinates, speed=None):
    service, token = await client(hass, entity_id)
    request = {"ProfileToken": token, "Position": {"PanTilt": position(coordinates)}}
    if speed is not None:
        request["Speed"] = {"PanTilt": {"x": speed, "y": speed}}
    async with asyncio.timeout(10):
        await service.AbsoluteMove(request)


async def jog(hass, entity_id, action, distance, speed):
    """Use the existing client's bounded relative or continuous PTZ operation."""
    service, token = await client(hass, entity_id)
    if action == "stop":
        async with asyncio.timeout(10):
            await service.Stop({"ProfileToken": token, "PanTilt": True, "Zoom": False})
        return
    target = hass.data["camera"].get_entity(entity_id)
    capabilities = getattr(target.profile, "ptz", None)
    relative = getattr(capabilities, "relative", True)
    if not relative and not getattr(capabilities, "continuous", False):
        raise HomeAssistantError("The ONVIF camera does not support directional movement")
    vector = {"x": 0.0, "y": 0.0}
    vector["x" if action in {"left", "right"} else "y"] = distance * (-1 if action in {"left", "down"} else 1)
    try:
        async with asyncio.timeout(10):
            if relative:
                await service.RelativeMove({"ProfileToken": token, "Translation": {"PanTilt": vector},
                                            "Speed": {"PanTilt": {"x": speed, "y": speed}}})
            else:
                await service.ContinuousMove({"ProfileToken": token, "Velocity": {"PanTilt": vector}})
            await asyncio.sleep(0.35)
    finally:
        async with asyncio.timeout(10):
            await service.Stop({"ProfileToken": token, "PanTilt": True, "Zoom": False})
