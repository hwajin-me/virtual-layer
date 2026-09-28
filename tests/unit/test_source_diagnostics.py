"""Diagnostics remain bounded and do not recursively mirror virtual sources."""

import json
from types import SimpleNamespace

from custom_components.virtual_layer.source_diagnostics import attributes, meaningful_change


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
