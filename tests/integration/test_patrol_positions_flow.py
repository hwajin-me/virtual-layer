"""Teach, revisit and persist routes through the real HA options flow manager."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.virtual_layer import config_flow as cf
from custom_components.virtual_layer.const import ATTR_DEVICES, ATTR_ENTITY_KEY, ATTR_GROUP_NAME, COMPONENT_DOMAIN
from custom_components.virtual_layer.patrol_positions import CONF_PATROL_ROUTE


async def editor(hass, **camera_options):
    entity = {
        "platform": "camera", "name": "Front", "entity_id": "camera.front", ATTR_ENTITY_KEY: "front-key",
        "source_entities": ["camera.frigate"], "onvif_patrol_target": "camera.ptz",
        "frigate_recording_switch": "switch.front_recordings", "vendor_extra": {"preserve": True},
        **camera_options,
    }
    entry = MockConfigEntry(domain=COMPONENT_DOMAIN, data={ATTR_GROUP_NAME: "Home"}, options={
        ATTR_DEVICES: {"Home": [entity]}, "device_attributes": {"Home": {"manufacturer": "Custom"}},
    })
    entry.add_to_hass(hass)
    pan_tilt = SimpleNamespace(x=0.1, y=0.2, space="urn:space")
    status = SimpleNamespace(Position=SimpleNamespace(PanTilt=pan_tilt), MoveStatus=SimpleNamespace(PanTilt="IDLE"))
    commands = []
    async def relative(request):
        commands.append("RelativeMove")
        pan_tilt.x += request["Translation"]["PanTilt"]["x"]
    async def stop(request):
        commands.append("Stop")
    service = SimpleNamespace(GetStatus=AsyncMock(return_value=status), AbsoluteMove=AsyncMock(),
                              RelativeMove=AsyncMock(side_effect=relative), Stop=AsyncMock(side_effect=stop))
    target = SimpleNamespace(profile=SimpleNamespace(token="profile", ptz=SimpleNamespace(absolute=True)), device=SimpleNamespace(
        device=SimpleNamespace(create_ptz_service=AsyncMock(return_value=service)),
    ))
    parent = SimpleNamespace(async_stop_patrol=AsyncMock())
    hass.data["camera"] = SimpleNamespace(get_entity=lambda name: {"camera.ptz": target, "camera.front": parent}.get(name))
    manager = hass.config_entries.options
    result = await manager.async_init(entry.entry_id, data={"action": "camera_patrol"})
    assert result["step_id"] == "patrol_camera"
    key = next(iter(cf._entity_choices(entry.options)))
    result = await manager.async_configure(result["flow_id"], {"entity_key": key})
    assert result["step_id"] == "patrol_settings"
    return entry, manager, result, pan_tilt, service, parent, commands


async def begin(manager, result):
    result = await manager.async_configure(result["flow_id"], {
        "onvif_patrol_target": "camera.ptz", "onvif_patrol_interval": 120, "onvif_patrol_speed": 0.2,
    })
    assert result["step_id"] == "patrol_positions", result
    assert not result["errors"]
    return result["flow_id"]


async def test_teach_save_reopen_and_keep_unrelated_configuration(hass):
    entry, manager, result, coordinates, service, parent, commands = await editor(hass)
    original = deepcopy(dict(entry.options))
    flow_id = await begin(manager, result)
    parent.async_stop_patrol.assert_awaited()
    await manager.async_configure(flow_id, {"action": "capture", "point_name": "Door"})
    await manager.async_configure(flow_id, {"action": "right", "jog_distance": 0.05})
    assert commands == ["RelativeMove", "Stop"]
    await manager.async_configure(flow_id, {"action": "capture", "point_name": "Window"})
    assert entry.options == original  # physical preview is not a config write
    await manager.async_configure(flow_id, {"action": "visit", "point": "0"})
    service.AbsoluteMove.assert_awaited_once_with({
        "ProfileToken": "profile", "Position": {"PanTilt": {"x": 0.1, "y": 0.2, "space": "urn:space"}},
        "Speed": {"PanTilt": {"x": 0.2, "y": 0.2}},
    })
    result = await manager.async_configure(flow_id, {"action": "save"})
    assert result["type"] == "create_entry"
    saved = entry.options[ATTR_DEVICES]["Home"][0]
    for field in (ATTR_ENTITY_KEY, "entity_id", "source_entities", "frigate_recording_switch", "vendor_extra"):
        assert saved[field] == original[ATTR_DEVICES]["Home"][0][field]
    assert saved["onvif_patrol_interval"] == 120
    assert [p["name"] for p in saved[CONF_PATROL_ROUTE]["points"]] == ["Door", "Window"]
    assert entry.options["device_attributes"] == original["device_attributes"]
    result = await manager.async_init(entry.entry_id, data={"action": "camera_patrol"})
    result = await manager.async_configure(result["flow_id"], {"entity_key": next(iter(cf._entity_choices(entry.options)))})
    result = await manager.async_configure(result["flow_id"], result["data_schema"]({}))
    assert result["description_placeholders"]["count"] == "2"
    assert "Door" in result["description_placeholders"]["positions"]


async def test_invalid_capture_and_cancel_do_not_save(hass):
    entry, manager, result, coordinates, service, parent, commands = await editor(hass)
    original = deepcopy(dict(entry.options))
    flow_id = await begin(manager, result)
    result = await manager.async_configure(flow_id, {"action": "save"})
    assert result["errors"]["base"] == "patrol_invalid_points"
    service.GetStatus.side_effect = RuntimeError("unsupported")
    result = await manager.async_configure(flow_id, {"action": "capture", "point_name": "Door"})
    assert result["errors"]["base"] == "patrol_command_failed"
    result = await manager.async_configure(flow_id, {"action": "cancel"})
    assert result["step_id"] == "init"
    assert entry.options == original


async def test_stale_entity_edit_cannot_overwrite_newer_options(hass):
    entry, manager, result, coordinates, service, parent, commands = await editor(hass)
    flow_id = await begin(manager, result)
    options = deepcopy(dict(entry.options))
    options[ATTR_DEVICES]["Home"][0]["name"] = "Renamed concurrently"
    hass.config_entries.async_update_entry(entry, options=options)
    result = await manager.async_configure(flow_id, {"action": "legacy"})
    assert result["errors"]["base"] == "entity_not_found"
    assert entry.options[ATTR_DEVICES]["Home"][0]["name"] == "Renamed concurrently"


async def test_replace_reorder_remove_and_legacy_route(hass):
    entry, manager, result, coordinates, service, parent, commands = await editor(hass)
    flow_id = await begin(manager, result)
    for number in range(3):
        coordinates.x = number * 0.2
        await manager.async_configure(flow_id, {"action": "capture", "point_name": str(number)})
    await manager.async_configure(flow_id, {"action": "earlier", "point": "2"})
    coordinates.x = 0.7
    await manager.async_configure(flow_id, {"action": "replace", "point": "0", "point_name": "Replaced"})
    result = await manager.async_configure(flow_id, {"action": "remove", "point": "2"})
    assert result["description_placeholders"]["count"] == "2"
    assert "Replaced" in result["description_placeholders"]["positions"]
    result = await manager.async_configure(flow_id, {"action": "legacy"})
    assert result["type"] == "create_entry"
    assert CONF_PATROL_ROUTE not in entry.options[ATTR_DEVICES]["Home"][0]


async def test_route_survives_normal_entity_form_edit(hass):
    route = {"target": "camera.ptz", "points": [
        {"name": "A", "position": {"x": -0.2, "y": 0.1}},
        {"name": "B", "position": {"x": 0.2, "y": 0.1}},
    ]}
    configured = {"platform": "camera", "name": "Front", "entity_id": "camera.front",
                  "onvif_patrol_target": "camera.ptz", CONF_PATROL_ROUTE: route}
    defaults = cf._entity_form_defaults("Home", configured)
    _, rebuilt = cf._build_entity_config(defaults)
    assert rebuilt[CONF_PATROL_ROUTE] == route


async def test_changing_onvif_camera_clears_saved_positions(hass):
    from custom_components.virtual_layer import patrol_positions as positions
    route = {"target": "camera.ptz", "points": [
        {"name": "A", "position": {"x": -0.2, "y": 0.1}},
        {"name": "B", "position": {"x": 0.2, "y": 0.1}},
    ]}
    entry, manager, result, coordinates, service, parent, commands = await editor(hass, **{CONF_PATROL_ROUTE: route})
    with patch.object(positions, "client", AsyncMock(return_value=(service, "other-profile"))):
        result = await manager.async_configure(result["flow_id"], {
            "onvif_patrol_target": "camera.other", "onvif_patrol_interval": 120, "onvif_patrol_speed": 0.2,
        })
    assert result["description_placeholders"]["count"] == "0"
    result = await manager.async_configure(result["flow_id"], {"action": "save"})
    assert result["errors"]["base"] == "patrol_invalid_points"
    assert entry.options[ATTR_DEVICES]["Home"][0][CONF_PATROL_ROUTE] == route


async def test_repair_malformed_route_without_destroying_other_options(hass):
    entry, manager, result, coordinates, service, parent, commands = await editor(hass, **{CONF_PATROL_ROUTE: "damaged"})
    flow_id = await begin(manager, result)
    result = await manager.async_configure(flow_id, {"action": "legacy"})
    assert result["type"] == "create_entry"
    assert CONF_PATROL_ROUTE not in entry.options[ATTR_DEVICES]["Home"][0]
    assert entry.options[ATTR_DEVICES]["Home"][0]["vendor_extra"] == {"preserve": True}


async def test_patrol_step_translations_cover_every_visible_input(hass):
    import json
    from pathlib import Path
    entry, manager, result, coordinates, service, parent, commands = await editor(hass)
    settings = result
    flow_id = await begin(manager, result)
    result = await manager.async_configure(flow_id, {"action": "capture", "point_name": "Door"})
    for lang in ("en", "ko"):
        catalog = json.loads((Path(cf.__file__).parent / "translations" / f"{lang}.json").read_text())
        for form in (settings, result):
            labels = catalog["options"]["step"][form["step_id"]]
            for marker in form["data_schema"].schema:
                assert labels["data"][marker.schema]
                assert labels["data_description"][marker.schema]
