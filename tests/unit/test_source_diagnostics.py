"""Diagnostics remain bounded and do not recursively mirror virtual sources."""

import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from custom_components.virtual_layer.source_diagnostics import attributes, meaningful_change, prepare_retention
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry


def test_snapshot_filters_transient_and_recursive_data_and_keeps_unknown_sources(hass):
    hass.states.async_set("sensor.original", "4", {
        "access_token": "secret", "entity_picture": "secret-url", "source_diagnostics": {"nested": "secret"},
        "source_configuration": {"nested": "secret"}, "voltage": 3, "nonfinite": float("nan"),
    })
    config = {"_source_diagnostics": {"platform": "sensor", "source_entities": ["sensor.original", "sensor.missing"]}}
    result = attributes(hass, config, {})
    record = result["source_diagnostics"]["sensor.original"]
    assert record["source_state"] == "4"
    assert record["source_attributes"] == {"voltage": 3, "nonfinite": None}
    assert result["source_diagnostics"]["sensor.missing"]["source_state"] is None
    assert "secret" not in json.dumps(result, allow_nan=False)


def test_large_diagnostics_share_a_single_parent_budget(hass):
    sources = [f"sensor.source_{index}" for index in range(100)]
    for source in sources:
        hass.states.async_set(source, "5", {"payload": ["x" * 10000] * 100})
    config = {"_source_diagnostics": {"source_entities": sources, "command_actions": {"payload": "x" * 30000}}}
    result = attributes(hass, config, {"existing": "x" * 6000})
    assert result["source_diagnostics_truncated"] is True
    assert len(json.dumps(result).encode()) < 6200
    assert sources[0] in result["source_diagnostics"]


def test_diagnostic_only_updates_do_not_feed_back_but_real_attributes_do(hass):
    hass.states.async_set("sensor.source", "1", {"value": 5, "source_diagnostics": {"first": 1}})
    before = hass.states.get("sensor.source")
    hass.states.async_set("sensor.source", "1", {"value": 5, "source_diagnostics": {"next": 2}})
    after = hass.states.get("sensor.source")
    assert not meaningful_change(SimpleNamespace(data={"old_state": before, "new_state": after}))
    hass.states.async_set("sensor.source", "1", {"value": 6})
    assert meaningful_change(SimpleNamespace(data={"old_state": after, "new_state": hass.states.get("sensor.source")}))


def test_diagnostics_require_runtime_configuration(hass):
    assert attributes(hass, {}, {}) == {}


def test_expired_snapshot_removes_payload_and_timestamps(hass):
    hass.states.async_set("sensor.original", "4", {"payload": "large detail"})
    config = {
        "_source_diagnostics": {
            "platform": "sensor", "persistent": True,
            "source_entities": ["sensor.original"],
            "command_actions": {"turn_on": "detail"},
        },
        "_diagnostic_details_until": (dt_util.utcnow() - timedelta(seconds=1)).isoformat(),
    }
    result = attributes(hass, config, {})
    assert result["source_diagnostics"]["sensor.original"] == {
        "source_entity_id": "sensor.original", "source_entity_name": "original",
        "source_state": "4",
    }
    assert "command_actions" not in result["source_configuration"]
    assert "large detail" not in json.dumps(result)


def test_no_diagnostics_when_regular_attributes_use_up_recorder_budget(hass):
    config = {"_source_diagnostics": {"source_entities": ["sensor.original"]}}
    assert attributes(hass, config, {"payload": "x" * 12000}) == {}


@pytest.mark.parametrize("stored", [[], {"parent": "2026-02-31T00:00:00Z"}, {"parent": {}}, {"parent": "not a date"}])
def test_malformed_retention_storage_is_repaired_once(hass, stored):
    entry = MockConfigEntry(domain="virtual_layer", data={
        "group_name": "Retention", "_diagnostic_details_until": stored,
    })
    entry.add_to_hass(hass)
    entities = {"sensor": [{"unique_id": "parent", "_source_diagnostics": {}}]}
    prepare_retention(hass, entry, entities)
    deadline = dict(entry.data["_diagnostic_details_until"])
    parsed = dt_util.parse_datetime(deadline["parent"])
    assert timedelta(days=6) < parsed - dt_util.utcnow() <= timedelta(days=7)
    prepare_retention(hass, entry, entities)
    assert entry.data["_diagnostic_details_until"] == deadline
