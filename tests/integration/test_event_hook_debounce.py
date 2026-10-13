"""Delayed hooks must not replay a state that has already disappeared."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.virtual_layer import entity as virtual_entity
from custom_components.virtual_layer import _async_setup_state_only_event_hooks

pytestmark = pytest.mark.integration


@pytest.fixture
def hook_timers(monkeypatch):
    scheduled = []

    def call_later(hass, delay, callback):
        cancel = Mock()
        scheduled.append((callback, cancel))
        return cancel

    monkeypatch.setattr(virtual_entity, "async_call_later", call_later)
    monkeypatch.setattr("custom_components.virtual_layer.async_call_later", call_later)
    return scheduled


async def setup_hook(hass, domain, hook):
    entity_id = f"{domain}.debounced"
    entry = MockConfigEntry(
        domain="virtual_layer",
        data={"group_name": "Debounce Device"},
        options={"devices": {"Debounce Device": [{
            "platform": domain, "name": "Debounced", "entity_id": entity_id,
            "initial_value": "waiting", "persistent": False,
            "event_hooks": [{"debounce": 30, "value_template": "received", **hook}],
        }]}},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, entity_id


@pytest.mark.parametrize("domain", ["sensor", "tag"])
@pytest.mark.parametrize("change", ["off", "unknown", "unavailable", "removed", "cycle", "attribute", "unrelated"])
async def test_state_debounce_revalidates_source(hass, hook_timers, domain, change):
    hass.states.async_set("sensor.debounce_source", "off", {"mode": "idle"})
    hook = {"trigger": "state", "entity_id": ["sensor.debounce_source"], "to": "on"}
    if change in {"attribute", "unrelated"}:
        hook.update({"attribute": "mode", "from": "off"})
    entry, entity_id = await setup_hook(hass, domain, hook)
    hass.states.async_set("sensor.debounce_source", "on", {"mode": "active"})
    await hass.async_block_till_done()
    callback, _ = hook_timers[-1]
    assert hass.states.get(entity_id).state == "waiting"

    if change == "removed":
        hass.states.async_remove("sensor.debounce_source")
    elif change == "cycle":
        hass.states.async_set("sensor.debounce_source", "off")
        hass.states.async_set("sensor.debounce_source", "on", {"mode": "active"})
    elif change in {"attribute", "unrelated"}:
        hass.states.async_set("sensor.debounce_source", "on", {
            "mode": "idle" if change == "attribute" else "active", "diagnostic": 42,
        })
    else:
        hass.states.async_set("sensor.debounce_source", change)
    await hass.async_block_till_done()
    callback(None)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == ("received" if change == "unrelated" else "waiting")
    if change == "cycle":
        hook_timers[-1][0](None)
        await hass.async_block_till_done()
        assert hass.states.get(entity_id).state == "received"
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize("domain", ["sensor", "tag"])
async def test_event_debounce_keeps_latest_and_ignores_cancelled_callbacks(hass, hook_timers, domain):
    entry, entity_id = await setup_hook(hass, domain, {
        "trigger": "event", "event_type": "virtual_layer_debounce_test",
        "event_data": {"target": "selected"}, "value_template": "{{ trigger.data.value }}",
    })
    for value in ("first", "latest"):
        hass.bus.async_fire("virtual_layer_debounce_test", {"target": "selected", "value": value})
        await hass.async_block_till_done()
    first, cancel_first = hook_timers[-2]
    latest, _ = hook_timers[-1]
    cancel_first.assert_called_once_with()
    first(None)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "waiting"
    # An unrelated event must not cancel the selected target's pending update.
    hass.bus.async_fire("virtual_layer_debounce_test", {"target": "other", "value": "ignored"})
    await hass.async_block_till_done()
    latest(None)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "latest"

    hass.bus.async_fire("virtual_layer_debounce_test", {"target": "selected", "value": "after_unload"})
    await hass.async_block_till_done()
    pending, cancel = hook_timers[-1]
    assert await hass.config_entries.async_unload(entry.entry_id)
    cancel.assert_called_once_with()
    state_after_unload = hass.states.get(entity_id)
    # Even an already-queued callback cannot resurrect the removed entity.
    pending(None)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id) is state_after_unload


@pytest.mark.parametrize("domain", ["sensor", "tag"])
async def test_state_debounce_runs_with_real_ha_timer(hass, freezer, domain):
    entry, entity_id = await setup_hook(hass, domain, {
        "trigger": "state", "entity_id": ["sensor.debounce_source"], "to": "on",
    })
    hass.states.async_set("sensor.debounce_source", "on")
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=29))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "waiting"
    freezer.tick(timedelta(seconds=2))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "received"
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize("domain", ["sensor", "tag"])
async def test_debounced_source_removal_hook_remains_supported(hass, hook_timers, domain):
    hass.states.async_set("sensor.debounce_source", "on")
    entry, entity_id = await setup_hook(hass, domain, {
        "trigger": "state", "entity_id": ["sensor.debounce_source"], "to": "None",
    })
    hass.states.async_remove("sensor.debounce_source")
    await hass.async_block_till_done()
    hook_timers[-1][0](None)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "received"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_state_only_queued_listener_cannot_schedule_after_unload(hass, hook_timers, monkeypatch):
    callbacks = []

    def track(hass, sources, callback):
        callbacks.append(callback)
        return Mock()

    monkeypatch.setattr("custom_components.virtual_layer.async_track_state_change_event", track)
    listeners = []
    _async_setup_state_only_event_hooks(hass, {
        "entity_id": "tag.debounced", "event_hooks": [{
            "trigger": "state", "entity_id": ["sensor.debounce_source"],
            "debounce": 30, "value_template": "received",
        }],
    }, listeners)
    for remove in listeners:
        remove()
    # HA may already have queued a listener when removal unsubscribes it.
    callbacks[0](SimpleNamespace(data={"entity_id": "sensor.debounce_source"}))
    assert not hook_timers


@pytest.mark.parametrize("domain", ["sensor", "tag"])
async def test_state_filter_debounce_is_not_starved_by_attribute_updates(hass, hook_timers, domain):
    entry, entity_id = await setup_hook(hass, domain, {
        "trigger": "state", "entity_id": ["sensor.debounce_source"], "to": "on",
    })
    hass.states.async_set("sensor.debounce_source", "on", {"signal": 0})
    await hass.async_block_till_done()
    count = len(hook_timers)
    apply, cancel = hook_timers[-1]
    for signal in range(1, 5):
        hass.states.async_set("sensor.debounce_source", "on", {"signal": signal})
        await hass.async_block_till_done()
    assert len(hook_timers) == count
    cancel.assert_not_called()
    apply(None)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "received"
    assert await hass.config_entries.async_unload(entry.entry_id)
