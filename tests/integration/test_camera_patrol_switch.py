"""Generated patrol control follows its parent camera's actual scheduler."""
from unittest.mock import AsyncMock

from pytest_homeassistant_custom_component.common import MockConfigEntry
from homeassistant.helpers import entity_registry as er
from homeassistant.exceptions import HomeAssistantError

from custom_components.virtual_layer.const import ATTR_DEVICES, ATTR_GROUP_NAME, COMPONENT_DOMAIN


async def test_patrol_switch_controls_parent_and_is_removed(hass):
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN, title="Patrol",
        data={ATTR_GROUP_NAME: "Patrol"},
        options={ATTR_DEVICES: {"Patrol": [{
            "platform": "camera", "name": "Front", "entity_id": "camera.front",
            "onvif_patrol_target": "camera.onvif_front",
        }]}},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    camera = hass.data["camera"].get_entity("camera.front")
    camera._async_call_onvif_ptz = AsyncMock()
    registry = er.async_get(hass)
    assert registry.async_get("switch.front_patrol").device_id == registry.async_get("camera.front").device_id
    assert hass.states.get("switch.front_patrol").state == "off"
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.front_patrol"}, blocking=True)
    await hass.async_block_till_done()
    assert camera._patrol_task is not None
    assert hass.states.get("switch.front_patrol").state == "on"
    await camera.async_stop_patrol()
    await hass.async_block_till_done()
    assert hass.states.get("switch.front_patrol").state == "off"
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.front_patrol"}, blocking=True)
    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.front_patrol"}, blocking=True)
    assert camera._patrol_task is None
    camera._async_call_onvif_ptz = AsyncMock(side_effect=HomeAssistantError("offline"))
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.front_patrol"}, blocking=True)
    task = camera._patrol_task
    if task is not None:
        await task
    await hass.async_block_till_done()
    assert hass.states.get("switch.front_patrol").state == "off"
    switch_uid = registry.async_get("switch.front_patrol").unique_id
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get("switch.front_patrol").unique_id == switch_uid
    assert hass.states.get("switch.front_patrol").state == "off"
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("switch.front_patrol") is None
    assert registry.async_get("switch.front_patrol") is None
