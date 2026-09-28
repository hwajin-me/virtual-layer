"""UI-managed Matterbridge labels survive reload without owning user labels."""

import json

import pytest
from homeassistant.helpers import entity_registry as er, label_registry as lr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.virtual_layer.config_flow import (
    _flatten_entity_form_sections,
)
from custom_components.virtual_layer.matterbridge_controls import (
    CONF_MATTERBRIDGE_CONTROL_LABEL as LABEL,
    CONF_MATTERBRIDGE_CONTROLS_ENABLED as ENABLED,
)
from tests.flow_helpers import suggested_form_values

pytestmark = pytest.mark.integration


def defaults(result):
    return _flatten_entity_form_sections(suggested_form_values(result["data_schema"]))


@pytest.mark.parametrize("policy", ["automatic", "keep_current", "force_helper"])
async def test_ui_creates_edits_and_disables_media_control_labels(hass, policy):
    hass.states.async_set("media_player.source", "playing", {"supported_features": 1036})
    entry = MockConfigEntry(
        domain="virtual_layer", title="Media", data={"group_name": "Media"},
        options={"devices": {}},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    manager = hass.config_entries.options
    result = await manager.async_init(entry.entry_id, data={"action": "add_entity"})
    result = await manager.async_configure(result["flow_id"], {
        "reference_entity_id": ["media_player.source"],
    })
    for _ in range(3):
        if result.get("step_id") == "entity":
            break
        result = await manager.async_configure(result["flow_id"], defaults(result))
    assert result["step_id"] == "entity"
    assert defaults(result)[ENABLED] is False
    assert defaults(result)[LABEL] == "matterbridge-virtual"
    result = await manager.async_configure(result["flow_id"], {
        **defaults(result), "entity_name": "TV", "entity_id": "media_player.virtual_tv",
        "device_name": "Media", ENABLED: True,
    })
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    labels = lr.async_get(hass)
    player = registry.async_get("media_player.virtual_tv")
    assert player is not None
    first = labels.async_get_label_by_name("matterbridge-virtual")
    assert player.labels == {first.label_id}
    unrelated = labels.async_create("Living room")
    registry.async_update_entity(player.entity_id, labels=player.labels | {unrelated.label_id})
    unique_id, device_id = player.unique_id, player.device_id
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get(player.entity_id).labels == {first.label_id, unrelated.label_id}

    for enabled, name in ((True, "My media controls"), (False, "My media controls")):
        stored = next(item for items in entry.options["devices"].values() for item in items)
        result = await manager.async_init(entry.entry_id, data={"action": "edit_entity"})
        result = await manager.async_configure(result["flow_id"], {
            "entity_key": json.dumps(["key", stored["entity_key"]], separators=(",", ":")),
        })
        result = await manager.async_configure(result["flow_id"], {})
        if result["step_id"] == "edit_entity_type":
            result = await manager.async_configure(result["flow_id"], defaults(result))
        assert result["step_id"] == "edit_entity_helper"
        result = await manager.async_configure(result["flow_id"], {"helper_update_mode": policy})
        assert result["step_id"] == "edit_entity"
        assert defaults(result)[ENABLED] is True
        assert defaults(result)[LABEL] == ("matterbridge-virtual" if enabled else name)
        result = await manager.async_configure(result["flow_id"], {
            **defaults(result), ENABLED: enabled, LABEL: name,
        })
        assert result["type"] == "create_entry"
        await hass.async_block_till_done()
        current = registry.async_get(player.entity_id)
        expected = {unrelated.label_id}
        if enabled:
            expected.add(labels.async_get_label_by_name(name).label_id)
        assert current.labels == expected
        assert (current.unique_id, current.device_id) == (unique_id, device_id)
        for generated in er.async_entries_for_config_entry(registry, entry.entry_id):
            if generated.entity_id != current.entity_id:
                assert not generated.labels
    await hass.config_entries.async_remove(entry.entry_id)
    assert registry.async_get(player.entity_id) is None
    # Shared label definitions belong to HA, not to this one player.
    assert labels.async_get_label(first.label_id) is not None


@pytest.mark.parametrize("preassigned", [False, True])
async def test_existing_labels_and_other_registry_options_are_preserved(hass, preassigned):
    labels = lr.async_get(hass)
    shared = labels.async_create("matterbridge-virtual")
    player = {"platform": "media_player", "name": "TV", "entity_id": "media_player.tv",
              "unique_id": "tv", "initial_value": "off", ENABLED: False}
    entry = MockConfigEntry(domain="virtual_layer", data={"group_name": "Media"},
                            options={"devices": {"Media": [player]}})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    if preassigned:
        registry.async_update_entity("media_player.tv", labels={shared.label_id})
    registry.async_update_entity_options("media_player.tv", "virtual_layer", {"other": True})
    for enabled in (True, True, False):
        player = {**player, ENABLED: enabled}
        hass.config_entries.async_update_entry(entry, options={"devices": {"Media": [player]}})
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        current = registry.async_get("media_player.tv")
        assert current.labels == ({shared.label_id} if enabled or preassigned else set())
        assert current.options["virtual_layer"]["other"] is True
        assert len(list(labels.async_list_labels())) == 1
    await hass.config_entries.async_remove(entry.entry_id)


@pytest.mark.parametrize("bad_label", [None, [], {}, 42, "x" * 256, "bad\nlabel"])
async def test_malformed_optional_label_does_not_break_loading_or_deletion(hass, bad_label):
    entry = MockConfigEntry(domain="virtual_layer", data={"group_name": "Media"},
                            options={"devices": {"Media": [{
                                "platform": "media_player", "name": "TV",
                                "entity_id": "media_player.tv", "initial_value": "off",
                                ENABLED: True, LABEL: bad_label,
                            }]}})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("media_player.tv") is not None
    assert not er.async_get(hass).async_get("media_player.tv").labels
    assert await hass.config_entries.async_remove(entry.entry_id)
