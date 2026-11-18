"""Preserve restore state across HA's registry-indexed storage transition."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from homeassistant.core import State
from homeassistant.helpers import entity_registry as er

import custom_components.virtual_layer as integration
from custom_components.virtual_layer.restore_state_compat import (
    discard_stored_state,
    get_stored_state,
)


@pytest.mark.parametrize("current_api", [False, True])
def test_restore_lookup_uses_supported_store_api(current_api):
    stored = object()
    if current_api:
        lookup = Mock(return_value=stored)
        data = SimpleNamespace(async_get_stored_state=lookup)
    else:
        data = SimpleNamespace(last_states={"sensor.saved": stored})
    assert get_stored_state(data, "sensor.saved") is stored
    assert get_stored_state(data, None) is None
    if current_api:
        lookup.assert_called_once_with("sensor.saved")


@pytest.mark.parametrize("current_api", [False, True])
async def test_nonpersistent_discard_preserves_unrelated_snapshots(hass, current_api):
    registry = er.async_get(hass)
    entry = registry.async_get_or_create("tag", "virtual_layer", "forget")
    saved, unrelated = object(), object()
    if current_api:
        data = SimpleNamespace(
            last_states_by_entity_id={entry.entity_id: saved, "tag.other": unrelated},
            last_states_by_entity_registry_id={entry.id: saved, "other": unrelated},
        )
    else:
        data = SimpleNamespace(last_states={entry.entity_id: saved, "tag.other": unrelated})
    discard_stored_state(hass, data, entry.entity_id)
    if current_api:
        assert data.last_states_by_entity_id == {"tag.other": unrelated}
        assert data.last_states_by_entity_registry_id == {"other": unrelated}
    else:
        assert data.last_states == {"tag.other": unrelated}


async def test_attribute_edit_uses_registry_aware_restore_lookup(hass, monkeypatch):
    stored = SimpleNamespace(state=State("sensor.saved", "12", {
        "edited": "old", "runtime": "retained", "unchanged": "runtime value",
    }))
    lookup = Mock(return_value=stored)
    monkeypatch.setattr(
        integration, "async_get_restore_state",
        lambda _hass: SimpleNamespace(async_get_stored_state=lookup),
    )
    integration._async_update_restored_attributes(
        hass,
        {"attributes": {"edited": "old", "unchanged": "configured"}},
        {"entity_id": "sensor.saved",
         "attributes": {"edited": "new", "unchanged": "configured"}},
    )
    lookup.assert_called_once_with("sensor.saved")
    assert stored.state.attributes["edited"] == "new"
    assert stored.state.attributes["unchanged"] == "runtime value"
    assert stored.state.attributes["runtime"] == "retained"


@pytest.mark.parametrize("current_api", [False, True])
async def test_state_only_proxy_snapshots_with_registry_identity(
    hass, monkeypatch, current_api
):
    registry_entry = er.async_get(hass).async_get_or_create(
        "tag", "virtual_layer", "persistent-proxy"
    )
    entity_id = registry_entry.entity_id
    hass.states.async_set(entity_id, "runtime", {"runtime": "retained"})
    calls = []
    data = SimpleNamespace(entities={})

    def added(proxy):
        data.entities[proxy.entity_id] = proxy

    if current_api:
        def removed(entity_id, state, extra_data, entity_registry_id=None):
            calls.append((entity_id, state, extra_data, entity_registry_id))
            data.entities.pop(entity_id)
    else:
        def removed(entity_id, extra_data):
            calls.append((entity_id, extra_data))
            data.entities.pop(entity_id)

    data.async_restore_entity_added = added
    data.async_restore_entity_removed = removed
    monkeypatch.setattr(integration, "async_get_restore_state", lambda _hass: data)
    integration._async_register_state_only_restore_proxy(
        hass, "entry", {"entity_id": entity_id, "persistent": True}
    )
    proxy = data.entities[entity_id]
    assert proxy.registry_entry is registry_entry
    assert proxy.extra_restore_state_data is None
    integration._async_remove_state_only_restore_proxies(hass, "entry")
    assert data.entities == {}
    if current_api:
        assert calls == [(entity_id, hass.states.get(entity_id), None, registry_entry.id)]
    else:
        assert calls == [(entity_id, None)]
