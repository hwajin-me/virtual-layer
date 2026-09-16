"""Create/edit Frigate camera stream defaults through real HA options flows."""

import pytest

from tests.flow_helpers import suggested_form_values
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.template import Template
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.virtual_layer.config_flow import (
    ACTION_ADD_ENTITY, ACTION_EDIT_ENTITY, CONF_ACTION, CONF_ENTITY_KEY,
    CONF_REFERENCE_ENTITY_ID, CONF_TARGET_ENTITY_TYPE, CONF_USE_TEMPLATE_HELPER,
    CONF_NATIVE_VALUE_TEMPLATES, CONF_HELPER_UPDATE_MODE, HELPER_UPDATE_KEEP,
    _selection_key_for_entity, _flatten_entity_form_sections,
)
from custom_components.virtual_layer.const import ATTR_DEVICES, ATTR_GROUP_NAME, COMPONENT_DOMAIN, CONF_NATIVE_TEMPLATES

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("helper_enabled", [True, False])
@pytest.mark.parametrize("submitted", ["", "{{ 'rtsp://custom.example/live' }}"])
async def test_frigate_camera_add_blank_and_edit_keep(hass, helper_enabled, submitted):
    frigate = MockConfigEntry(domain="frigate", data={"url": "https://frigate.example"}, options={
        "rtsp_url_template": "rtsp://go2rtc.myong.us:8554/{{ name }}",
    })
    frigate.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=frigate.entry_id,
        identifiers={("frigate", f"{frigate.entry_id}:camera1")}, name="Bedroom",
    )
    source = er.async_get(hass).async_get_or_create(
        "camera", "frigate", f"{frigate.entry_id}:camera:camera1",
        config_entry=frigate, device_id=device.id, suggested_object_id="bedroom",
    )
    hass.states.async_set(source.entity_id, "idle", {"camera_name": "camera1"})
    entry = MockConfigEntry(domain=COMPONENT_DOMAIN, data={ATTR_GROUP_NAME: "Test"}, options={ATTR_DEVICES: {}})
    entry.add_to_hass(hass)
    manager = hass.config_entries.options
    result = await manager.async_init(entry.entry_id, data={CONF_ACTION: ACTION_ADD_ENTITY})
    result = await manager.async_configure(result["flow_id"], {CONF_REFERENCE_ENTITY_ID: [source.entity_id]})
    if result["step_id"] == "entity_type":
        result = await manager.async_configure(result["flow_id"], {CONF_TARGET_ENTITY_TYPE: "camera"})
    assert result["step_id"] == "entity_helper"
    result = await manager.async_configure(result["flow_id"], {CONF_USE_TEMPLATE_HELPER: helper_enabled})
    assert result["step_id"] == "entity"
    form = _flatten_entity_form_sections(suggested_form_values(result["data_schema"]))
    expected = "rtsp://go2rtc.myong.us:8554/camera1?video=h264&audio=all"
    assert Template(form[CONF_NATIVE_VALUE_TEMPLATES]["stream_source"], hass).async_render() == expected
    form[CONF_NATIVE_VALUE_TEMPLATES]["stream_source"] = submitted
    form["entity_id"] = "camera.bedroom_virtual"
    result = await manager.async_configure(result["flow_id"], form)
    assert result["type"] == "create_entry", result
    options = result["data"]
    device_name = next(iter(options[ATTR_DEVICES]))
    configured = options[ATTR_DEVICES][device_name][0]
    expected = "rtsp://custom.example/live" if submitted else expected
    assert Template(configured[CONF_NATIVE_TEMPLATES]["stream_source"], hass).async_render() == expected

    # Reopen the persisted record through the normal edit flow; keep_current
    # must not silently replace a URL after Frigate's override changes.
    hass.config_entries.async_update_entry(entry, options=options)
    hass.config_entries.async_update_entry(frigate, options={"rtsp_url_template": "rtsp://changed/{{ name }}"})
    result = await manager.async_init(entry.entry_id, data={CONF_ACTION: ACTION_EDIT_ENTITY})
    result = await manager.async_configure(result["flow_id"], {
        CONF_ENTITY_KEY: _selection_key_for_entity(device_name, 0, configured),
    })
    result = await manager.async_configure(result["flow_id"], {CONF_REFERENCE_ENTITY_ID: [source.entity_id]})
    if result["step_id"] == "edit_entity_type":
        result = await manager.async_configure(result["flow_id"], {CONF_TARGET_ENTITY_TYPE: "camera"})
    if result["step_id"] == "edit_entity_helper":
        result = await manager.async_configure(result["flow_id"], {CONF_HELPER_UPDATE_MODE: HELPER_UPDATE_KEEP})
    assert result["step_id"] == "edit_entity"
    form = _flatten_entity_form_sections(suggested_form_values(result["data_schema"]))
    assert Template(form[CONF_NATIVE_VALUE_TEMPLATES]["stream_source"], hass).async_render() == expected
    result = await manager.async_configure(result["flow_id"], form)
    assert result["type"] == "create_entry", result
    hass.config_entries.async_update_entry(entry, options=result["data"])
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    camera = hass.data["camera"].get_entity("camera.bedroom_virtual")
    assert await camera.stream_source() == expected
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.data["camera"].get_entity("camera.bedroom_virtual").stream_source() == expected
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
