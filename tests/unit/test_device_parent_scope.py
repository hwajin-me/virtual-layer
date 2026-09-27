"""Parent checks must respect Home Assistant's config-entry-scoped identifiers."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from custom_components.virtual_layer import _device_registry_updates_for_config
from custom_components.virtual_layer.config_flow import (
    InvalidFieldValue,
    _build_device_config,
    _with_existing_device_defaults,
)
from custom_components.virtual_layer.const import COMPONENT_DOMAIN


@pytest.fixture
def scoped_registry(monkeypatch):
    # Current HA permits two entries to use the same integration identifier.
    # The local older HA dependency needs a shim for the scoped lookup API.
    first = SimpleNamespace(id="first", via_device_id=None)
    second = SimpleNamespace(id="second", via_device_id=None)
    registry = Mock()
    registry.async_get_device_by_identifier.side_effect = lambda identifier, entry_id: {
        "entry-a": first, "entry-b": second,
    }[entry_id] if identifier == (COMPONENT_DOMAIN, "shared-id") else None
    registry.async_get_devices.return_value = [first, second]
    registry.async_get.side_effect = {"first": first, "second": second}.get
    monkeypatch.setattr("custom_components.virtual_layer.device_metadata.dr.async_get", lambda _: registry)
    return first, second


def test_parent_form_uses_the_edited_entry(hass, scoped_registry):
    form = {"device_id": "shared-id", "device_via_device_id": "first"}
    assert _build_device_config(form, "Second", hass, config_entry_id="entry-b")["via_device_id"] == "first"
    form["device_via_device_id"] = "second"
    with pytest.raises(InvalidFieldValue):
        _build_device_config(form, "Second", hass, config_entry_id="entry-b")


def test_parent_defaults_and_runtime_preserve_other_entry_parent(hass, scoped_registry):
    first, second = scoped_registry
    config = {"device_id": "shared-id", "via_device_id": first.id}
    options = {"devices": {"Second": []}, "device_attributes": {"Second": config}}
    defaults = _with_existing_device_defaults({}, options, "Second", hass, config_entry_id="entry-b")
    assert defaults["device_via_device_id"] == first.id
    assert _device_registry_updates_for_config(hass, config, second)["via_device_id"] == first.id
    # A self-parent must be cleared even when unscoped lookup finds the first device.
    second.via_device_id = second.id
    updates = _device_registry_updates_for_config(hass, {**config, "via_device_id": second.id}, second)
    assert updates["via_device_id"] is None
