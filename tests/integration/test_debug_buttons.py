"""Generated Debug controls diagnose now and retain only one day of debug data."""

from datetime import timedelta

import pytest
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.virtual_layer.debug import DEBUG_SUFFIX


async def setup(hass, domain="sensor"):
    hass.states.async_set("sensor.original", "1", {"voltage": 3})
    record = {
        "platform": domain, "entity_id": f"{domain}.debug_target", "name": "Target",
        "unique_id": f"debug-target-{domain}", "entity_key": f"debug-target-{domain}",
        "persistent": True, "initial_value": "docked" if domain == "vacuum" else "ready",
        "source_entities": ["sensor.original"],
        "diagnostic_attributes": ["old_trace", "recent_trace"],
        "attributes": {"needed": "configured"},
    }
    entry = MockConfigEntry(domain="virtual_layer", data={"group_name": "Debug Device"},
                            options={"devices": {"Debug Device": [record]}})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, record["entity_id"]


def button_for(hass, target):
    registry = er.async_get(hass)
    uid = registry.async_get(target).unique_id + DEBUG_SUFFIX
    return registry.async_get_entity_id("button", "virtual_layer", uid)


async def advance(hass, freezer, **delta):
    freezer.tick(timedelta(**delta))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()


@pytest.mark.parametrize("domain", ["sensor", "tag", "button", "vacuum"])
async def test_debug_controls_cover_existing_entities_and_companions_without_recursion(hass, domain):
    entry, target = await setup(hass, domain)
    registry = er.async_get(hass)
    rows = er.async_entries_for_config_entry(registry, entry.entry_id)
    targets = [row for row in rows if not row.unique_id.endswith(DEBUG_SUFFIX)
               and "source_usage:" not in row.unique_id]
    debug = [row for row in rows if row.unique_id.endswith(DEBUG_SUFFIX)]
    assert len(debug) == len(targets)
    for row in targets:
        control = registry.async_get(button_for(hass, row.entity_id))
        assert control.device_id == row.device_id
        assert control.original_name == f"Debug - {row.name or row.original_name or hass.states.get(row.entity_id).name}"
        assert not control.disabled
    assert entry.options["devices"]["Debug Device"][0]["platform"] == domain
    assert len(entry.options["devices"]["Debug Device"]) == 1
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert len(er.async_entries_for_config_entry(registry, entry.entry_id)) == len(rows)
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize("domain", ["sensor", "tag"])
async def test_press_refreshes_diagnosis_and_clamps_only_diagnostics_to_one_day(hass, freezer, domain):
    entry, target = await setup(hass, domain)
    button = button_for(hass, target)
    await hass.services.async_call("virtual_layer", "set_attributes", {
        "entity_id": target, "attributes": {"old_trace": ["old"], "functional_history": list(range(100))},
    }, blocking=True)
    await hass.async_block_till_done()
    await advance(hass, freezer, days=2)
    await hass.services.async_call("virtual_layer", "set_attributes", {
        "entity_id": target, "attributes": {"recent_trace": ["today"]},
    }, blocking=True)
    hass.states.async_set("sensor.original", "2", {"voltage": 4})
    await hass.async_block_till_done()
    before = hass.states.get(target).state
    await hass.services.async_call("button", "press", {"entity_id": button}, blocking=True)
    await hass.async_block_till_done()
    attrs = hass.states.get(target).attributes
    assert "old_trace" not in attrs
    assert attrs["recent_trace"] == ["today"]
    assert attrs["needed"] == "configured"
    assert attrs["functional_history"] == list(range(100))
    assert hass.states.get(target).state == before
    report = hass.states.get(button).attributes["debug_result"]
    assert report["cleansed_attributes"] == 1
    assert report["source_diagnostics"]["sensor.original"]["source_state"] == "2"
    assert report["source_diagnostics"]["sensor.original"]["source_attributes"] == {"voltage": 4}
    expiry = hass.states.get(button).attributes["debug_expires_at"]
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(button).attributes["debug_expires_at"] == expiry
    assert "old_trace" not in hass.states.get(target).attributes
    await advance(hass, freezer, days=1, seconds=1)
    assert "recent_trace" not in hass.states.get(target).attributes
    assert "debug_result" not in hass.states.get(button).attributes
    assert hass.states.get(target).attributes["functional_history"] == list(range(100))
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_rename_and_deletion_keep_controls_attached_to_the_target(hass):
    entry, target = await setup(hass)
    registry = er.async_get(hass)
    button = button_for(hass, target)
    uid = registry.async_get(button).unique_id
    registry.async_update_entity(target, name="Renamed target")
    await hass.async_block_till_done()
    assert hass.states.get(button).name == "Debug - Renamed target"
    registry.async_update_entity(button, name="My diagnostic control")
    registry.async_update_entity(target, name="Another name")
    await hass.async_block_till_done()
    assert registry.async_get(button).original_name == "Debug - Another name"
    assert hass.states.get(button).name == "My diagnostic control"
    assert registry.async_get(button).unique_id == uid
    hass.config_entries.async_update_entry(entry, options={"devices": {"Debug Device": []}})
    await hass.async_block_till_done()
    assert registry.async_get(button) is None
    assert hass.states.get(button) is None
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_debug_id_collision_does_not_adopt_a_user_button(hass):
    registry = er.async_get(hass)
    occupied = registry.async_get_or_create(
        "button", "test", "external", suggested_object_id="debug_sensor_debug_target",
    )
    entry, target = await setup(hass)
    control = button_for(hass, target)
    assert control != occupied.entity_id
    assert registry.async_get(occupied.entity_id).unique_id == "external"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_unavailable_target_can_still_be_diagnosed(hass):
    entry, target = await setup(hass)
    await hass.services.async_call("virtual_layer", "set_available", {
        "entity_id": target, "value": False,
    }, blocking=True)
    await hass.async_block_till_done()
    control = button_for(hass, target)
    await hass.services.async_call("button", "press", {"entity_id": control}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(target).state == "unavailable"
    assert hass.states.get(control).attributes["debug_result"]["target_state"] == "unavailable"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_manual_diagnosis_works_after_automatic_details_expired(hass, freezer):
    entry, target = await setup(hass)
    await advance(hass, freezer, days=8)
    assert "source_attributes" not in hass.states.get(target).attributes["source_diagnostics"]["sensor.original"]
    button = button_for(hass, target)
    await hass.services.async_call("button", "press", {"entity_id": button}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(button).attributes["debug_result"]["source_diagnostics"]["sensor.original"]["source_attributes"] == {"voltage": 3}
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_disabled_target_restore_snapshot_is_cleansed_before_reenable(hass, freezer):
    entry, target = await setup(hass)
    await hass.services.async_call("virtual_layer", "set_attributes", {
        "entity_id": target, "attributes": {"old_trace": "old", "functional": "keep"},
    }, blocking=True)
    await hass.async_block_till_done()
    await advance(hass, freezer, days=2)
    registry = er.async_get(hass)
    registry.async_update_entity(target, disabled_by=er.RegistryEntryDisabler.USER)
    await hass.async_block_till_done()
    control = button_for(hass, target)
    await hass.services.async_call("button", "press", {"entity_id": control}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(control).attributes["debug_result"]["cleansed_attributes"] == 1
    registry.async_update_entity(target, disabled_by=None)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert "old_trace" not in hass.states.get(target).attributes
    assert hass.states.get(target).attributes["functional"] == "keep"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_target_control_permission_is_required_before_cleansing(hass, freezer, monkeypatch):
    from homeassistant.exceptions import HomeAssistantError

    entry, target = await setup(hass)
    await hass.services.async_call("virtual_layer", "set_attributes", {
        "entity_id": target, "attributes": {"old_trace": "old"},
    }, blocking=True)
    await hass.async_block_till_done()
    await advance(hass, freezer, days=2)

    async def denied(_hass, call):
        assert call.data["entity_id"] == [target]
        raise HomeAssistantError("Denied")

    monkeypatch.setattr("custom_components.virtual_layer._async_verify_target_entity_control", denied)
    button = hass.data["button"].get_entity(button_for(hass, target))
    with pytest.raises(HomeAssistantError, match="Denied"):
        await button.async_press()
    assert hass.states.get(target).attributes["old_trace"] == "old"
    assert "debug_result" not in hass.states.get(button.entity_id).attributes
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_configured_name_and_id_edits_keep_debug_identity_and_device(hass):
    entry, target = await setup(hass)
    registry = er.async_get(hass)
    old_control = button_for(hass, target)
    old = registry.async_get(old_control)
    record = dict(entry.options["devices"]["Debug Device"][0])
    record.update(name="Changed target", entity_id="sensor.changed_target")
    hass.config_entries.async_update_entry(entry, options={"devices": {"Debug Device": [record]}})
    await hass.async_block_till_done()
    new_control = button_for(hass, "sensor.changed_target")
    current = registry.async_get(new_control)
    assert current.unique_id == old.unique_id
    assert current.device_id == old.device_id
    assert new_control != old_control
    assert registry.async_get(old_control) is None
    assert hass.states.get(new_control).name == "Debug - Changed target"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_reused_entity_id_cannot_restore_another_targets_debug_report(hass):
    entry, target = await setup(hass)
    control = button_for(hass, target)
    await hass.services.async_call("button", "press", {"entity_id": control}, blocking=True)
    await hass.async_block_till_done()
    assert "debug_result" in hass.states.get(control).attributes
    old_uid = er.async_get(hass).async_get(control).unique_id
    record = dict(entry.options["devices"]["Debug Device"][0])
    hass.config_entries.async_update_entry(entry, options={"devices": {"Debug Device": []}})
    await hass.async_block_till_done()
    record.update(unique_id="replacement-target", entity_key="replacement-target")
    hass.config_entries.async_update_entry(entry, options={"devices": {"Debug Device": [record]}})
    await hass.async_block_till_done()
    current = button_for(hass, target)
    assert er.async_get(hass).async_get(current).unique_id != old_uid
    assert "debug_result" not in hass.states.get(current).attributes
    assert await hass.config_entries.async_unload(entry.entry_id)
