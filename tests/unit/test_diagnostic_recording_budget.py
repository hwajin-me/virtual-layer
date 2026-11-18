"""Primary diagnostics stay stable; detailed snapshots are requested explicitly."""

import json
from collections.abc import Mapping
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.virtual_layer.debug import DEBUG_SUFFIX
from custom_components.virtual_layer.source_diagnostics import (
    COMPACT_DIAGNOSTIC_BYTES,
    attributes,
)


def test_primary_snapshot_omits_raw_configuration_and_clock_only_fields(hass, freezer):
    hass.states.async_set("sensor.source", "4", {"large_trace": "x" * 3000})
    config = {"diagnostic_details": True, "_source_diagnostics": {
        "platform": "sensor", "persistent": True,
        "source_entities": ["sensor.source"], "command_actions": {"run": "private config"},
    }}
    communication = {
        "zigbee_device_availability": "online", "zigbee_mqtt_connected": True,
        "zigbee_last_seen": "2026-11-18T00:00:00Z", "zigbee_last_seen_seconds_ago": 1,
    }
    with patch("custom_components.virtual_layer.source_diagnostics.diagnostic_attributes", return_value=communication):
        first = attributes(hass, config, {})
        freezer.tick(timedelta(seconds=301))
        communication.update(zigbee_last_seen="2026-11-18T00:05:01Z", zigbee_last_seen_seconds_ago=302)
        second = attributes(hass, config, {})
    assert first == second
    assert len(json.dumps(first, ensure_ascii=False).encode()) <= COMPACT_DIAGNOSTIC_BYTES
    assert set(first["source_configuration"]) == {"platform", "persistent", "source_entities"}
    record = first["source_diagnostics"]["sensor.source"]
    assert record["source_available"] is True
    assert "source_state" not in record
    assert record["zigbee_device_availability"] == "online"
    assert record["zigbee_mqtt_connected"] is True
    assert not record.keys() & {"source_attributes", "source_last_updated", "source_last_changed", "zigbee_last_seen", "zigbee_last_seen_seconds_ago"}


@pytest.mark.parametrize("domain", ["sensor", "tag"])
async def test_source_attribute_and_clock_changes_create_no_primary_history_events(hass, freezer, domain):
    source, target = "sensor.source", f"{domain}.compact"
    hass.states.async_set(source, "4", {"trace": 0})
    entry = MockConfigEntry(domain="virtual_layer", data={"group_name": "Compact"}, options={
        "devices": {"Device": [{
            "platform": domain, "name": "Compact", "entity_id": target,
            "source_entities": [source], "value_template": "{{ states('sensor.source') }}",
            "diagnostic_details": True,
            "attributes": {"functional_history": list(range(40))},
        }]},
    })
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    events = []
    remove = hass.bus.async_listen(EVENT_STATE_CHANGED, lambda event: events.append(event) if event.data["entity_id"] == target else None)
    try:
        for index in range(10):
            freezer.tick(timedelta(seconds=301))
            hass.states.async_set(source, "4", {"trace": index + 1, "payload": "x" * 4000})
            async_fire_time_changed(hass, dt_util.utcnow())
            await hass.async_block_till_done()
        assert events == []
        assert hass.states.get(target).state == "4"
        assert hass.states.get(target).attributes["functional_history"] == list(range(40))
        hass.states.async_set(source, "5", {"trace": "changed"})
        await hass.async_block_till_done()
        assert hass.states.get(target).state == "5"
        assert events
    finally:
        remove()
        assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize("include_details", [False, True])
async def test_debug_button_can_request_bounded_details_without_expanding_primary(hass, include_details):
    hass.states.async_set("sensor.source", "4", {"voltage": 3, "payload": "x" * 5000})
    entry = MockConfigEntry(domain="virtual_layer", data={"group_name": "Debug budget"}, options={
        "devices": {"Device": [{
            "platform": "sensor", "name": "Compact", "entity_id": "sensor.compact",
            "source_entities": ["sensor.source"],
            "diagnostic_details": include_details,
        }]},
    })
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    uid = registry.async_get("sensor.compact").unique_id
    button = registry.async_get_entity_id("button", "virtual_layer", uid + DEBUG_SUFFIX)
    saved_data = deepcopy(dict(entry.data))
    saved_options = deepcopy(dict(entry.options))
    await hass.services.async_call("button", "press", {"entity_id": button}, blocking=True)
    await hass.async_block_till_done()
    report = hass.states.get(button).attributes["debug_result"]
    if include_details:
        assert report["source_diagnostics"]["sensor.source"]["source_attributes"]["voltage"] == 3
        assert "source_last_updated" in report["source_diagnostics"]["sensor.source"]
    else:
        assert "source_attributes" not in report["source_diagnostics"]["sensor.source"]
        assert "source_last_updated" not in report["source_diagnostics"]["sensor.source"]
    assert len(json.dumps(report, ensure_ascii=False).encode()) <= 12000
    assert "source_attributes" not in hass.states.get("sensor.compact").attributes["source_diagnostics"]["sensor.source"]
    assert dict(entry.data) == saved_data
    assert dict(entry.options) == saved_options
    assert await hass.config_entries.async_unload(entry.entry_id)


def test_compact_source_count_and_nested_preview_have_bounded_work(hass):
    class Branch(Mapping):
        reads = 0

        def __len__(self):
            return 100000

        def __iter__(self):
            return (str(index) for index in range(100000))

        def __getitem__(self, key):
            self.reads += 1
            return self

    branch = Branch()
    sources = [f"sensor.source_{index}" for index in range(100)]
    for source in sources:
        hass.states.async_set(source, "4", {"payload": "x" * 5000})
    config = {"_source_diagnostics": {
        "platform": "sensor", "source_entities": sources, "command_actions": branch,
    }}
    compact = attributes(hass, config, {})
    assert len(json.dumps(compact, ensure_ascii=False).encode()) <= COMPACT_DIAGNOSTIC_BYTES
    assert compact["source_diagnostics_truncated"] is True
    assert branch.reads == 0
    detailed = attributes(hass, config, {}, include_details=True)
    assert len(json.dumps(detailed, ensure_ascii=False).encode()) <= 12000
    assert branch.reads <= 128


@pytest.mark.parametrize("domain", ["sensor", "tag"])
async def test_raw_source_values_and_quality_do_not_record_unchanged_virtual_output(hass, freezer, domain):
    source, target = "sensor.numeric_source", f"{domain}.rounded"
    hass.states.async_set(source, "40")
    communication = {"source_integration": "zigbee2mqtt", "zigbee_linkquality": 10, "zigbee_battery": 90}
    entry = MockConfigEntry(domain="virtual_layer", data={"group_name": "Rounded"}, options={
        "devices": {"Device": [{
            "platform": domain, "name": "Rounded", "entity_id": target,
            "source_entities": [source], "diagnostic_interval": 0,
            "value_template": "{{ (states('sensor.numeric_source') | float(40) // 10) | int }}",
        }]},
    })
    entry.add_to_hass(hass)
    with patch("custom_components.virtual_layer.source_diagnostics.diagnostic_attributes", return_value=communication):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        events = []
        remove = hass.bus.async_listen(EVENT_STATE_CHANGED, lambda event: events.append(event) if event.data["entity_id"] == target else None)
        try:
            for value in range(41, 50):
                freezer.tick(timedelta(seconds=301))
                communication.update(zigbee_linkquality=value, zigbee_battery=value)
                hass.states.async_set(source, str(value))
                async_fire_time_changed(hass, dt_util.utcnow())
                await hass.async_block_till_done()
            assert hass.states.get(target).state == "4"
            assert events == []
            hass.states.async_set(source, "unavailable")
            await hass.async_block_till_done()
            assert hass.states.get(target).state == "4"
            assert hass.states.get(target).attributes["source_diagnostics"][source]["source_available"] is False
            assert len(events) == 1
            hass.states.async_set(source, "40")
            await hass.async_block_till_done()
            assert hass.states.get(target).attributes["source_diagnostics"][source]["source_available"] is True
            assert len(events) == 2
            events.clear()
            hass.states.async_set(source, "50")
            await hass.async_block_till_done()
            assert hass.states.get(target).state == "5"
            assert len(events) == 1
        finally:
            remove()
            assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize("location", ["source", "configuration", "excluded_source"])
def test_snapshot_bounds_examined_lazy_keys_even_when_every_value_is_rejected(hass, location):
    class Wide(Mapping):
        reads = 0

        def __len__(self):
            return 1000

        def __iter__(self):
            return (f"key_{index}" for index in range(1000))

        def __getitem__(self, key):
            self.reads += 1
            return "é" * 2000

    class ExcludedWide(Wide):
        def __iter__(self):
            return iter(["access_token"] * 1000)

    wide = ExcludedWide() if location == "excluded_source" else Wide()
    now = dt_util.utcnow()
    state = SimpleNamespace(name="Synthetic", state="4", attributes=wide, last_updated=now, last_changed=now)
    configuration = wide if location == "configuration" else {"source_entities": ["sensor.synthetic"]}
    if location == "configuration":
        # Keep the source-list lookup independent from lazy configuration keys.
        wide.get = lambda key, default=None: []
    fake_hass = SimpleNamespace(states=SimpleNamespace(get=lambda entity_id: state))
    with patch("custom_components.virtual_layer.source_diagnostics.diagnostic_attributes", return_value={}):
        result = attributes(fake_hass, {"_source_diagnostics": configuration}, {}, include_details=True)
    assert wide.reads <= 32
    assert result["source_diagnostics_truncated"] is True
    assert len(json.dumps(result, ensure_ascii=False).encode()) <= 12000


@pytest.mark.parametrize("include_details", [False, True])
def test_source_inspection_and_preview_work_are_bounded_across_entire_snapshot(include_details):
    class Wide(Mapping):
        reads = 0

        def __len__(self):
            return 1000

        def __iter__(self):
            return (f"key_{index}" for index in range(1000))

        def __getitem__(self, key):
            self.reads += 1
            return "x" * 2000

    wide, inspected = Wide(), []
    now = dt_util.utcnow()
    state = SimpleNamespace(name="Synthetic", state="4", attributes=wide, last_updated=now, last_changed=now)

    def get(entity_id):
        inspected.append(entity_id)
        return state

    fake_hass = SimpleNamespace(states=SimpleNamespace(get=get))
    config = {"_source_diagnostics": {"source_entities": [f"sensor.source_{index}" for index in range(1000)]}}
    with patch("custom_components.virtual_layer.source_diagnostics.diagnostic_attributes", return_value={}):
        result = attributes(fake_hass, config, {}, include_details=include_details)
    assert len(inspected) <= 32
    assert wide.reads <= 128
    assert result["source_diagnostics_truncated"] is True
    assert len(json.dumps(result, ensure_ascii=False).encode()) <= (12000 if include_details else COMPACT_DIAGNOSTIC_BYTES)
