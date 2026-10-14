"""Startup/daily maintenance is deferred, bounded, and owned by entry lifecycle."""

import asyncio
from datetime import timedelta
from unittest.mock import Mock

import pytest
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.virtual_layer import cleanup
from custom_components.virtual_layer.debug import DEBUG_SUFFIX


async def setup(hass, count=1):
    records = [{
        "platform": "sensor", "name": f"Target {index}",
        "entity_id": f"sensor.cleanup_{index}", "initial_value": "ready",
        "persistent": True, "diagnostic_attributes": ["trace"],
        "attributes": {"required": "keep"},
    } for index in range(count)]
    entry = MockConfigEntry(domain="virtual_layer", data={"group_name": "Cleanup"},
                            options={"devices": {"Cleanup": records}})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry


async def test_startup_waits_for_started_then_runs_once_daily_and_stops(hass, freezer, monkeypatch):
    hass.set_state(CoreState.starting)
    calls = Mock(wraps=cleanup.cleanse)
    monkeypatch.setattr(cleanup, "cleanse", calls)
    entry = await setup(hass)
    assert not calls.called
    previous = hass.states.get("sensor.cleanup_0")
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get("sensor.cleanup_0") is previous
    first = calls.call_count
    assert first > 0
    assert all(not call.args[2].unique_id.endswith(DEBUG_SUFFIX) for call in calls.call_args_list)
    assert all(call.kwargs == {} for call in calls.call_args_list)
    assert all("debug_result" not in state.attributes for state in hass.states.async_all("button"))
    freezer.tick(timedelta(hours=23))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done(wait_background_tasks=True)
    assert calls.call_count == first
    freezer.tick(timedelta(hours=1, seconds=1))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done(wait_background_tasks=True)
    assert calls.call_count == first * 2
    assert await hass.config_entries.async_unload(entry.entry_id)
    freezer.tick(timedelta(days=1))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done(wait_background_tasks=True)
    assert calls.call_count == first * 2


@pytest.mark.parametrize("domain", ["sensor", "tag"])
async def test_automatic_sweep_keeps_two_day_diagnostics_and_functional_attributes(hass, freezer, monkeypatch, domain):
    entry = MockConfigEntry(domain="virtual_layer", data={"group_name": "Retention"}, options={
        "devices": {"Retention": [{"platform": domain, "entity_id": f"{domain}.retention",
            "name": "Retention", "persistent": True, "initial_value": "ready",
            "diagnostic_attributes": ["trace"], "attributes": {"required": "keep"}}]},
    })
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    target = f"{domain}.retention"
    await hass.services.async_call("virtual_layer", "set_attributes", {
        "entity_id": target, "attributes": {"trace": "two days", "functional": list(range(100))},
    }, blocking=True)
    freezer.tick(timedelta(days=2))
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done(wait_background_tasks=True)
    attrs = hass.states.get(target).attributes
    assert attrs["trace"] == "two days"
    assert attrs["required"] == "keep"
    assert attrs["functional"] == list(range(100))
    # An expired stale snapshot is swept even if its exact expiry event was missed.
    freezer.tick(timedelta(days=6))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done(wait_background_tasks=True)
    attrs = hass.states.get(target).attributes
    assert "trace" not in attrs
    assert attrs["functional"] == list(range(100))
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_running_setup_batches_isolates_failures_and_prevents_overlap(hass, monkeypatch, caplog):
    hass.set_state(CoreState.running)
    ticks = []
    monkeypatch.setattr(cleanup, "async_track_time_interval", lambda _h, cb, _interval: (ticks.append(cb) or Mock()))
    monkeypatch.setattr(cleanup, "_BATCH_SIZE", 1)
    calls = []
    yielded = []

    def cleanse(_hass, _entry, target):
        calls.append(target.entity_id)
        if len(calls) == 1:
            hass.loop.call_soon(lambda: yielded.append(True))
            ticks[0]()  # Repeated scheduling while this sweep runs must be ignored.
            raise ValueError("damaged target")
        assert yielded  # Other loop work ran between targets.

    monkeypatch.setattr(cleanup, "cleanse", cleanse)
    entry = await setup(hass, count=12)
    rows = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    expected = sum(row.unique_id.endswith(DEBUG_SUFFIX) for row in rows)
    assert len(calls) == expected
    assert len(set(calls)) == expected
    assert "Diagnostic cleanup failed" in caplog.text
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_unload_cancels_pending_startup_and_running_batch(hass, monkeypatch):
    hass.set_state(CoreState.starting)
    calls = Mock()
    monkeypatch.setattr(cleanup, "cleanse", calls)
    entry = await setup(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert not calls.called
    hass.set_state(CoreState.running)
    monkeypatch.setattr(cleanup, "_BATCH_SIZE", 1)
    gate = asyncio.Event()
    original_sleep = asyncio.sleep

    async def pause(_delay):
        gate.set()
        await original_sleep(3600)

    monkeypatch.setattr(cleanup, "asyncio", Mock(sleep=pause))
    assert await hass.config_entries.async_setup(entry.entry_id)
    await gate.wait()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert not calls.called
