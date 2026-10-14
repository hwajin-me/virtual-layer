"""Only explicitly selected debugging payloads have retention and size limits."""

import json
from datetime import timedelta

import pytest
import voluptuous as vol
from homeassistant.util import dt as dt_util

from custom_components.virtual_layer.attribute_retention import diagnostic_names, prune


@pytest.mark.parametrize("value", [{}, [None], ["x" * 129], [f"debug_{i}" for i in range(33)], ["source_entities"]])
def test_diagnostic_selection_rejects_invalid_and_owned_names(value):
    with pytest.raises(vol.Invalid):
        diagnostic_names(value)


def test_only_explicit_runtime_diagnostics_are_bounded_and_total_budget_is_enforced():
    names = [f"trace_{i}" for i in range(32)]
    ordinary = {"payload": "x" * 6000, "items": list(range(100))}
    attrs = {name: {"readings": ["y" * 3000] * 100} for name in names}
    attrs.update({"actual_history": ordinary, "runtime_virtual_attributes": names + ["actual_history"]})
    prune({"diagnostic_attributes": names}, attrs)
    assert attrs["actual_history"] == ordinary
    diagnostic = {name: value for name, value in attrs.items()
                  if name in names or name == "diagnostic_attribute_retention"}
    assert len(json.dumps(diagnostic, ensure_ascii=False).encode()) <= 3000
    assert any(name in attrs for name in names)
    assert sum(name in attrs for name in names) < 32


def test_configured_and_hook_outputs_are_protected_even_if_selected():
    payload = list(range(100))
    attrs = {name: payload for name in ("configured", "from_hook", "not_classified")}
    attrs["runtime_virtual_attributes"] = list(attrs)
    config = {"diagnostic_attributes": ["configured", "from_hook"],
              "event_hooks": [{"attributes": {"from_hook": "configured"}}]}
    prune(config, attrs, {"configured"}, clear=True)
    assert attrs["configured"] == payload
    assert attrs["from_hook"] == payload
    assert attrs["not_classified"] == payload
    assert "diagnostic_attribute_retention" not in attrs


def test_restore_uses_original_deadline_and_updates_cannot_extend_it(freezer):
    attrs = {"trace": [1], "runtime_virtual_attributes": ["trace"]}
    config = {"diagnostic_attributes": ["trace"]}
    prune(config, attrs)
    expiry = attrs["diagnostic_attribute_retention"]["trace"]["expires"]
    freezer.tick(timedelta(days=6))
    attrs["trace"] = [2]
    prune(config, attrs)
    assert attrs["diagnostic_attribute_retention"]["trace"]["expires"] == expiry
    freezer.tick(timedelta(days=1))
    prune(config, attrs)
    assert attrs == {}


@pytest.mark.parametrize("metadata", [{"trace": []}, {"trace": {"expires": "2026-02-31T00:00:00Z"}}])
def test_corrupt_deadline_is_repaired_once(metadata):
    attrs = {"trace": "diagnostic", "runtime_virtual_attributes": ["trace"],
             "diagnostic_attribute_retention": metadata}
    config = {"diagnostic_attributes": ["trace"]}
    prune(config, attrs)
    expiry = attrs["diagnostic_attribute_retention"]["trace"]["expires"]
    assert dt_util.parse_datetime(expiry) > dt_util.utcnow()
    prune(config, attrs)
    assert attrs["diagnostic_attribute_retention"]["trace"]["expires"] == expiry


def test_functional_payload_takes_priority_over_diagnostics():
    attrs = {"trace": "remove", "functional": "x" * 12000, "runtime_virtual_attributes": ["trace", "functional"]}
    prune({"diagnostic_attributes": ["trace"]}, attrs)
    assert attrs["functional"] == "x" * 12000
    assert "trace" not in attrs
    assert "diagnostic_attribute_retention" not in attrs


def test_nested_payload_work_is_bounded_and_nonfinite_diagnostics_are_safe():
    value = {"items": [float("nan"), float("inf")], "history": list(range(100))}
    attrs = {"trace": value, "runtime_virtual_attributes": ["trace"]}
    prune({"diagnostic_attributes": ["trace"]}, attrs)
    assert attrs["trace"]["items"] == [None, None]
    assert attrs["trace"]["history"] == list(range(68, 100))
    json.dumps(attrs, allow_nan=False)


def test_manual_day_cleanup_handles_legacy_deadlines_and_never_renews_them():
    now = dt_util.utcnow()
    attrs = {
        "old": "remove", "recent": "keep", "functional": list(range(100)),
        "runtime_virtual_attributes": ["old", "recent", "functional"],
        "diagnostic_attribute_retention": {
            "old": {"expires": (now + timedelta(days=5)).isoformat()},
            "recent": {"expires": (now + timedelta(days=6, hours=12)).isoformat()},
        },
    }
    config = {"diagnostic_attributes": ["old", "recent"]}
    prune(config, attrs, max_age=timedelta(days=1))
    assert "old" not in attrs
    assert attrs["recent"] == "keep"
    assert attrs["functional"] == list(range(100))
    metadata = attrs["diagnostic_attribute_retention"]
    assert dt_util.parse_datetime(metadata["recent"]["expires"]) == now + timedelta(hours=12)
    prune(config, attrs)
    assert attrs["diagnostic_attribute_retention"] == metadata
