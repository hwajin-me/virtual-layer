"""Native HA services validate virtual choices before custom actions run."""

from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.setup import async_setup_component

from custom_components.virtual_layer.select import ENTITY_CLASS, ENTITY_SCHEMA


@pytest.mark.parametrize("optimistic", [False, True])
async def test_invalid_select_service_never_executes_external_action(hass, optimistic):
    assert await async_setup_component(hass, "select", {})
    entity = ENTITY_CLASS(ENTITY_SCHEMA({
        "name": "Validated select", "entity_id": "select.validated",
        "initial_value": "eco", "options": ["eco", "boost"], "persistent": False,
        "command_actions": {"select_option": {
            "sequence": [{"action": "audit.capture"}], "optimistic": optimistic,
        }},
    }), False)
    capture = AsyncMock()
    hass.services.async_register("audit", "capture", capture)
    await hass.data["select"].async_add_entities([entity])
    with pytest.raises((ValueError, HomeAssistantError)):
        await hass.services.async_call("select", "select_option", {
            "entity_id": entity.entity_id, "option": "invalid",
        }, blocking=True)
    capture.assert_not_awaited()
    assert entity.current_option == "eco"
    await hass.services.async_call("select", "select_option", {
        "entity_id": entity.entity_id, "option": "boost",
    }, blocking=True)
    capture.assert_awaited_once()
    assert entity.current_option == ("boost" if optimistic else "eco")


async def test_legacy_media_sound_mode_works_through_native_service(hass):
    from custom_components.virtual_layer.media_player import ENTITY_CLASS, ENTITY_SCHEMA

    assert await async_setup_component(hass, "media_player", {})
    entity = ENTITY_CLASS(ENTITY_SCHEMA({
        "name": "Legacy sound", "entity_id": "media_player.legacy_sound",
        "initial_value": "idle", "persistent": False,
        "sound_mode_list": ["movie", "music"], "sound_mode": "movie",
    }), False)
    await hass.data["media_player"].async_add_entities([entity])
    assert hass.states.get(entity.entity_id).attributes["sound_mode"] == "movie"
    await hass.services.async_call("media_player", "select_sound_mode", {
        "entity_id": entity.entity_id, "sound_mode": "music",
    }, blocking=True)
    state = hass.states.get(entity.entity_id)
    assert state.attributes["sound_mode"] == "music"
    assert state.attributes["sound_mode_list"] == ["movie", "music"]
