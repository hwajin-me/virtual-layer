"""Configured source IDs remain visible through config-entry reloads."""

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.virtual_layer.const import COMPONENT_DOMAIN

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("domain", ["sensor", "tag"])
async def test_source_entities_attributes_follow_configuration_on_reload(hass, domain):
    entity_id = f"{domain}.source_list"
    entity_config = {
        "platform": domain,
        "name": "Source List",
        "entity_id": entity_id,
        "persistent": False,
        "source_entities": ["sensor.second", "sensor.first"],
    }
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={"group_name": "Sources"},
        options={"devices": {"Sources": [entity_config]}},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes["source_entities"] == [
        "sensor.second", "sensor.first",
    ]

    for sources in (["sensor.replacement"], []):
        hass.config_entries.async_update_entry(entry, options={
            "devices": {"Sources": [{**entity_config, "source_entities": sources}]},
        })
        await hass.async_block_till_done()
        assert hass.states.get(entity_id).attributes["source_entities"] == sources
