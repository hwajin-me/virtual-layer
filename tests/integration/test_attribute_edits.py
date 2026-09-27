"""Editing configured attributes must not resurrect an older restored value."""

import copy

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.virtual_layer.const import COMPONENT_DOMAIN


async def test_attribute_edits_override_restore_without_resetting_runtime_values(hass):
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN, data={"group_name": "Attribute edit"},
        options={"devices": {"Attribute edit": [{
            "platform": domain, "name": domain, "entity_id": f"{domain}.attribute_edit",
            "initial_value": "off", "persistent": True,
            "attributes": {"changed": "old", "unchanged": "configured", "removed": "old"},
        } for domain in ("switch", "sensor", "tag")]}},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entity_ids = [f"{domain}.attribute_edit" for domain in ("switch", "sensor", "tag")]
    await hass.services.async_call(COMPONENT_DOMAIN, "set_attributes", {
        "entity_id": entity_ids,
        "attributes": {"runtime": "kept", "unchanged": "runtime-value"},
    }, blocking=True)
    await hass.async_block_till_done()
    options = copy.deepcopy(dict(entry.options))
    for entity in options["devices"]["Attribute edit"]:
        entity["attributes"] = {"changed": "new", "unchanged": "configured", "added": 42}
    hass.config_entries.async_update_entry(entry, options=options)
    await hass.async_block_till_done()
    for _ in range(2):
        for entity_id in entity_ids:
            attrs = hass.states.get(entity_id).attributes
            assert attrs["changed"] == "new", entity_id
            assert attrs["added"] == 42, entity_id
            assert attrs["unchanged"] == "runtime-value", entity_id
            assert attrs["runtime"] == "kept", entity_id
            assert "removed" not in attrs, entity_id
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
