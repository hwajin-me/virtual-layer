"""Diagnostic throttling/expiry and opt-in cleanup on both runtime paths."""

from datetime import timedelta

import pytest
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed


async def setup_entity(hass, domain, **options):
    target = f"{domain}.retention"
    record = {
        "platform": domain, "name": "Retention", "entity_id": target,
        "initial_value": "ready", "source_entities": ["sensor.original"],
        "persistent": True, **options,
    }
    entry = MockConfigEntry(
        domain="virtual_layer", data={"group_name": "Retention"},
        options={"devices": {"Retention": [record]}},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, target


async def advance(hass, freezer, **delta):
    freezer.tick(timedelta(**delta))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()


@pytest.mark.parametrize("domain", ["sensor", "tag"])
async def test_diagnostics_throttle_while_native_state_updates_and_expire_without_events(hass, freezer, domain):
    hass.states.async_set("sensor.original", "1", {"payload": "first"})
    entry, target = await setup_entity(
        hass, domain, value_template="{{ states('sensor.original') }}",
    )
    deadline = dict(entry.data["_diagnostic_details_until"])
    assert hass.states.get(target).attributes["source_diagnostics"]["sensor.original"]["source_attributes"] == {"payload": "first"}

    hass.states.async_set("sensor.original", "2", {"payload": "second"})
    await hass.async_block_till_done()
    state = hass.states.get(target)
    assert state.state == "2"
    assert state.attributes["source_diagnostics"]["sensor.original"]["source_state"] == "1"
    await advance(hass, freezer, seconds=301)
    assert hass.states.get(target).attributes["source_diagnostics"]["sensor.original"]["source_state"] == "2"

    await advance(hass, freezer, days=6)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.data["_diagnostic_details_until"] == deadline
    await advance(hass, freezer, days=1)
    state = hass.states.get(target)
    record = state.attributes["source_diagnostics"]["sensor.original"]
    assert "source_attributes" not in record
    assert "source_last_updated" not in record
    assert set(state.attributes["source_configuration"]) == {"platform", "persistent", "source_entities"}
    assert state.state == "2"
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.data["_diagnostic_details_until"] == deadline
    assert "source_attributes" not in hass.states.get(target).attributes["source_diagnostics"]["sensor.original"]
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    await advance(hass, freezer, days=1)
    remaining = hass.states.get(target)
    assert remaining is None or remaining.state == "unavailable"
    if remaining:
        assert "source_diagnostics" not in remaining.attributes


@pytest.mark.parametrize("domain", ["sensor", "tag"])
@pytest.mark.parametrize("interval", [0, 60])
async def test_cleanup_preserves_configured_attributes_and_restores_tracking(hass, freezer, domain, interval):
    entry, target = await setup_entity(
        hass, domain, attribute_cleanup_interval=interval, diagnostic_details=False,
        diagnostic_attributes=["temporary"],
        attributes={"fixed": "configured"},
        attribute_templates={"computed": "{{ 42 }}"},
    )
    await hass.services.async_call("virtual_layer", "set_attributes", {
        "entity_id": target, "attributes": {"temporary": "remove", "fixed": "override"},
    }, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(target).attributes["runtime_virtual_attributes"] == ["temporary"]
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await advance(hass, freezer, seconds=61)
    state = hass.states.get(target)
    assert state.attributes["fixed"] == "override"
    assert state.attributes["computed"] == 42
    assert ("temporary" in state.attributes) is (interval == 0)
    assert ("runtime_virtual_attributes" in state.attributes) is (interval == 0)
    assert "source_entities" in state.attributes
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_expiry_without_sources_and_renew_only_after_saved_disable(hass, freezer):
    entry, target = await setup_entity(hass, "sensor", source_entities=[])
    await advance(hass, freezer, days=8)
    assert "initial_value" not in hass.states.get(target).attributes["source_configuration"]
    record = dict(entry.options["devices"]["Retention"][0])
    record["diagnostic_details"] = False
    hass.config_entries.async_update_entry(entry, options={"devices": {"Retention": [record]}})
    await hass.async_block_till_done()
    assert entry.data["_diagnostic_details_until"] == {}
    record = {**record, "diagnostic_details": True}
    hass.config_entries.async_update_entry(entry, options={"devices": {"Retention": [dict(record)]}})
    await hass.async_block_till_done()
    assert "initial_value" in hass.states.get(target).attributes["source_configuration"]
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize("domain", ["sensor", "tag"])
async def test_diagnostic_only_service_values_expire_without_affecting_functional_data(hass, freezer, domain):
    entry, target = await setup_entity(
        hass, domain, diagnostic_attributes=["trace", "fixed", "computed", "hook_value"],
        attributes={"fixed": "configured"}, attribute_templates={"computed": "{{ 42 }}"},
        event_hooks=[{"trigger": "event", "event_type": "retention_hook", "attributes": {"hook_value": "configured hook"}}],
    )
    ordinary = {"items": list(range(100)), "payload": "x" * 2000}
    await hass.services.async_call("virtual_layer", "set_attributes", {
        "entity_id": target, "attributes": {
            "trace": list(range(100)), "operational_history": ordinary,
            "debug_named_but_required": "keep", "fixed": "override", "hook_value": "keep",
        },
    }, blocking=True)
    await hass.async_block_till_done()
    attrs = hass.states.get(target).attributes
    assert attrs["trace"] == list(range(68, 100))
    assert attrs["operational_history"] == ordinary
    deadline = dict(attrs["diagnostic_attribute_retention"])
    await advance(hass, freezer, days=6)
    await hass.services.async_call("virtual_layer", "set_attributes", {
        "entity_id": target, "attributes": {"trace": ["updated"]},
    }, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(target).attributes["diagnostic_attribute_retention"] == deadline
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(target).attributes["diagnostic_attribute_retention"] == deadline
    await advance(hass, freezer, days=1, seconds=1)
    attrs = hass.states.get(target).attributes
    assert "trace" not in attrs
    assert "diagnostic_attribute_retention" not in attrs
    assert attrs["operational_history"] == ordinary
    assert attrs["debug_named_but_required"] == "keep"
    assert attrs["hook_value"] == "keep"
    assert attrs["fixed"] == "override"
    assert attrs["computed"] == 42
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert "trace" not in hass.states.get(target).attributes
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.parametrize("domain", ["sensor", "tag"])
async def test_cleanup_interval_never_deletes_unclassified_service_attributes(hass, freezer, domain):
    entry, target = await setup_entity(hass, domain, attribute_cleanup_interval=60)
    await hass.services.async_call("virtual_layer", "set_attributes", {
        "entity_id": target, "attributes": {"debug": "needed", "history": list(range(100))},
    }, blocking=True)
    await advance(hass, freezer, days=8)
    assert hass.states.get(target).attributes["debug"] == "needed"
    assert hass.states.get(target).attributes["history"] == list(range(100))
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.parametrize("domain", ["sensor", "tag"])
async def test_big_functional_service_payload_rebudgets_only_diagnostics(hass, domain):
    hass.states.async_set("sensor.original", "1", {"payload": "x" * 1000})
    entry, target = await setup_entity(hass, domain, diagnostic_attributes=["trace"])
    await hass.services.async_call("virtual_layer", "set_attributes", {
        "entity_id": target, "attributes": {"functional": "x" * 12000, "trace": "remove"},
    }, blocking=True)
    await hass.async_block_till_done()
    attrs = hass.states.get(target).attributes
    assert attrs["functional"] == "x" * 12000
    assert "trace" not in attrs
    assert "source_diagnostics" not in attrs
    assert await hass.config_entries.async_unload(entry.entry_id)
