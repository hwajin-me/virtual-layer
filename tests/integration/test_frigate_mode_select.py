"""Frigate mode companion controls and follows the source device."""
import asyncio

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.translation import async_get_translations
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.virtual_layer.const import ATTR_DEVICES, ATTR_GROUP_NAME, COMPONENT_DOMAIN
from custom_components.virtual_layer.frigate_source import frigate_mode_select_config


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("remove", ["disable", "source", "parent"])
@pytest.mark.parametrize("language", ["en", "ko"])
async def test_frigate_mode_lifecycle(hass, enabled, remove, language):
    hass.config.language = language
    frigate = MockConfigEntry(domain="frigate")
    frigate.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=frigate.entry_id, identifiers={("frigate", "front")},
    )
    registry = er.async_get(hass)
    for domain, suffix in (("camera", ""), ("switch", "_recordings"), ("switch", "_detect"), ("switch", "_motion")):
        source = registry.async_get_or_create(
            domain, "frigate", f"front{suffix}", config_entry=frigate,
            device_id=device.id, suggested_object_id=f"source{suffix}",
        )
        hass.states.async_set(source.entity_id, "off")
    calls = []
    async def change(call):
        target = call.data["entity_id"]
        target = target[0] if isinstance(target, list) else target
        calls.append((target, call.service))
        hass.states.async_set(target, call.service.removeprefix("turn_"))
    for service in ("turn_on", "turn_off"):
        hass.services.async_register("switch", service, change)
    assert frigate_mode_select_config(hass, ["camera.not_frigate"]) is None
    assert frigate_mode_select_config(hass, ["camera.source", "camera.other"]) is None
    camera = {"platform": "camera", "name": "Virtual", "entity_id": "camera.virtual",
              "source_entities": ["camera.source"], "source_entity": "camera.source",
              "frigate_mode_select": enabled}
    entry = MockConfigEntry(domain=COMPONENT_DOMAIN, data={ATTR_GROUP_NAME: "Test"},
                            options={ATTR_DEVICES: {"Test": [camera]}})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    select_id = "select.virtual_frigate_mode"
    if not enabled:
        assert hass.states.get(select_id) is None
        await hass.config_entries.async_remove(entry.entry_id)
        return
    assert not calls
    assert registry.async_get(select_id).translation_key == "frigate_mode"
    select = hass.data["select"].get_entity(select_id)
    suffix = "Frigate mode" if language == "en" else "Frigate 모드"
    assert select.name == f"Virtual - {suffix}"
    registry.async_update_entity("camera.virtual", name="Renamed")
    await hass.async_block_till_done()
    assert select.name == f"Renamed - {suffix}"
    registry.async_update_entity(select_id, name="My custom mode")
    registry.async_update_entity("camera.virtual", name="Renamed again")
    await hass.async_block_till_done()
    assert registry.async_get(select_id).name == "My custom mode"
    assert select.name == f"Renamed again - {suffix}"
    translations = await async_get_translations(hass, language, "entity", {COMPONENT_DOMAIN})
    prefix = "component.virtual_layer.entity.select.frigate_mode.state."
    labels = ["Recording only", "Detection only", "Recording and detection", "Off (recording and detection)"] if language == "en" else ["녹화만", "감지만", "녹화 및 감지", "모두 끄기 (녹화·감지)"]
    options = hass.states.get(select_id).attributes["options"]
    assert [translations[prefix + option] for option in options] == labels
    assert registry.async_get(select_id).device_id == registry.async_get("camera.virtual").device_id
    uid = registry.async_get(select_id).unique_id
    for mode, recording, detecting in (("recording", "on", "off"), ("detecting", "off", "on"),
                                       ("recording/detecting", "on", "on"), ("do nothing", "off", "off")):
        await hass.services.async_call("select", "select_option", {"entity_id": select_id, "option": mode}, blocking=True)
        await hass.async_block_till_done()
        assert hass.states.get("switch.source_recordings").state == recording
        assert hass.states.get("switch.source_detect").state == detecting
        assert hass.states.get(select_id).state == mode
    hass.states.async_set("switch.source_detect", "unavailable")
    await hass.async_block_till_done()
    assert hass.states.get(select_id).state == "unavailable"
    hass.states.async_set("switch.source_detect", "on")
    await hass.async_block_till_done()
    assert hass.states.get(select_id).state == "detecting"
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get(select_id).unique_id == uid
    # Registry renames must retain the companion identity and rebind actions.
    registry.async_update_entity("switch.source_recordings", new_entity_id="switch.renamed_recordings")
    hass.states.async_set("switch.renamed_recordings", "on")
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(select_id).state == "recording/detecting"
    assert registry.async_get(select_id).unique_id == uid
    async def failed(call):
        raise HomeAssistantError("Frigate offline")
    hass.services.async_register("switch", "turn_off", failed)
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call("select", "select_option", {"entity_id": select_id, "option": "do nothing"}, blocking=True)
    assert hass.states.get(select_id).state == "recording/detecting"
    if remove == "disable":
        camera["frigate_mode_select"] = False
    elif remove == "source":
        camera["source_entities"] = ["camera.not_frigate"]
        camera["source_entity"] = "camera.not_frigate"
    hass.config_entries.async_update_entry(entry, options={ATTR_DEVICES: {"Test": [] if remove == "parent" else [camera]}})
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get(select_id) is None
    assert hass.states.get(select_id) is None
    await hass.config_entries.async_remove(entry.entry_id)


@pytest.fixture
async def mode_control(hass, monkeypatch, request):
    controls = {kind: f"switch.test_{kind}" for kind in ("motion", "detect", "recordings")}
    monkeypatch.setattr("custom_components.virtual_layer.frigate_source.frigate_camera_switches", lambda *args: controls)
    monkeypatch.setattr("custom_components.virtual_layer.frigate_source.FRIGATE_SWITCH_TIMEOUT", getattr(request, "param", 10))
    for target in controls.values():
        hass.states.async_set(target, "off")
    entry = MockConfigEntry(domain=COMPONENT_DOMAIN, data={ATTR_GROUP_NAME: "Test"}, options={ATTR_DEVICES: {"Test": [{
        "platform": "camera", "name": "Test", "entity_id": "camera.test",
        "source_entities": ["camera.source"], "frigate_mode_select": True,
    }]}})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    yield controls, entry
    await hass.config_entries.async_remove(entry.entry_id)


async def test_delayed_motion_feedback_and_serial_mode_requests(hass, mode_control):
    controls, _ = mode_control
    motion_requested = asyncio.Event()
    calls = []
    async def change(call):
        target = call.data["entity_id"][0]
        calls.append((target, call.service))
        if target == controls["motion"]:
            motion_requested.set()
            return  # Transport completion is not Frigate state confirmation.
        hass.states.async_set(target, call.service.removeprefix("turn_"))
    for service in ("turn_on", "turn_off"):
        hass.services.async_register("switch", service, change)
    async def select(mode):
        await hass.services.async_call("select", "select_option", {
            "entity_id": "select.test_frigate_mode", "option": mode,
        }, blocking=True)
    first = asyncio.create_task(select("detecting"))
    await asyncio.wait_for(motion_requested.wait(), 2)
    second = asyncio.create_task(select("recording"))
    await asyncio.sleep(0.01)
    assert not first.done()
    assert not second.done()
    assert calls == [(controls["motion"], "turn_on")]
    hass.states.async_set(controls["motion"], "on")
    await asyncio.gather(first, second)
    await hass.async_block_till_done()
    assert calls == [(controls["motion"], "turn_on"), (controls["detect"], "turn_on"),
                     (controls["recordings"], "turn_off"), (controls["detect"], "turn_off"),
                     (controls["recordings"], "turn_on")]
    assert hass.states.get("select.test_frigate_mode").state == "recording"


@pytest.mark.parametrize("mode_control", [0.01], indirect=True)
async def test_unconfirmed_motion_aborts_before_detection(hass, mode_control):
    controls, _ = mode_control
    calls = []
    async def no_feedback(call):
        calls.append(call.data["entity_id"])
    hass.services.async_register("switch", "turn_on", no_feedback)
    with pytest.raises(HomeAssistantError) as error:
        await hass.services.async_call("select", "select_option", {
            "entity_id": "select.test_frigate_mode", "option": "detecting",
        }, blocking=True)
    assert error.value.translation_key == "frigate_mode_timeout"
    for language in ("en", "ko"):
        translations = await async_get_translations(hass, language, "exceptions", {COMPONENT_DOMAIN})
        assert translations["component.virtual_layer.exceptions.frigate_mode_timeout.message"]
    assert calls == [[controls["motion"]]]
    assert hass.states.get("select.test_frigate_mode").state == "do nothing"


async def test_unload_cancels_wait_and_queued_mode(hass, mode_control):
    controls, entry = mode_control
    motion_requested = asyncio.Event()
    calls = []
    async def change(call):
        calls.append(call.data["entity_id"])
        motion_requested.set()
    for service in ("turn_on", "turn_off"):
        hass.services.async_register("switch", service, change)
    async def select(mode):
        await hass.services.async_call("select", "select_option", {
            "entity_id": "select.test_frigate_mode", "option": mode,
        }, blocking=True)
    first = asyncio.create_task(select("detecting"))
    await asyncio.wait_for(motion_requested.wait(), 2)
    second = asyncio.create_task(select("recording"))
    await asyncio.sleep(0.01)
    assert await hass.config_entries.async_unload(entry.entry_id)
    results = await asyncio.gather(first, second, return_exceptions=True)
    assert all(result is None or isinstance(result, asyncio.CancelledError) for result in results)
    assert calls == [[controls["motion"]]]
    assert hass.data["select"].get_entity("select.test_frigate_mode") is None
    assert hass.states.get("select.test_frigate_mode").state == "unavailable"
