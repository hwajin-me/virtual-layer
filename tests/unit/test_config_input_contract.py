"""Structured config fields must retain their meaning when persisted."""

import pytest

from custom_components.virtual_layer.config_flow import InvalidJson, _parse_json_object


@pytest.mark.parametrize("payload", [
    {"nested": [float("nan")]}, {"nested": {"value": float("inf")}},
    {"nested": float("-inf")}, "nested: .nan", "nested: .inf",
    '{"nested": 1e999}', {"nested": {1: "changed key"}},
    "nested:\n  1: changed key", {"nested": {"1": "first", 1: "collision"}},
    {"nested": {float("nan")}}, "nested: !!set {.inf: null}",
    '{"nested": 1, "nested": 2}', '{"nested": {"value": 1, "value": 2}}',
])
def test_non_json_structured_values_are_rejected(payload):
    with pytest.raises(InvalidJson) as error:
        _parse_json_object(payload, "attributes_json")
    assert error.value.field_name == "attributes_json"


def test_recursive_and_excessively_deep_values_are_rejected():
    recursive = {"self": None}
    recursive["self"] = recursive
    deep = {}
    for _ in range(110):
        deep = {"nested": deep}
    for payload in (recursive, deep):
        with pytest.raises(InvalidJson):
            _parse_json_object(payload, "attributes_json")


def test_shared_non_recursive_values_and_finite_numbers_remain_valid():
    shared = {"nested": [None, True, -2, 1.5, "kept"]}
    payload = {"first": shared, "second": shared}
    assert _parse_json_object(payload, "attributes_json") == payload
