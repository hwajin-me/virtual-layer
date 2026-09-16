"""Reverse source references, device ownership, and companion lifecycle."""

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.virtual_layer.const import COMPONENT_DOMAIN

pytestmark = pytest.mark.integration


def virtual_config(entity_id, sources):
    return {
        "platform": entity_id.split(".")[0],
        "entity_id": entity_id,
        "name": entity_id.split(".")[1],
        "source_entities": sources,
        "persistent": False,
    }


async def setup_virtual(hass, name, records):
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={"group_name": name},
        options={"devices": {name: records}},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def usage_entries(hass, entry):
    return [
        item for item in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        if "source_usage:" in item.unique_id
    ]


def original_device(hass, owner, suffix):
    return dr.async_get(hass).async_get_or_create(
        config_entry_id=owner.entry_id,
        identifiers={("test", suffix)},
        name=f"Original {suffix}",
        manufacturer="Original manufacturer",
        model="Original model",
    )


async def test_source_usage_shared_device_reload_and_removal(hass):
    owner = MockConfigEntry(domain="test")
    owner.add_to_hass(hass)
    device = original_device(hass, owner, "one")
    registry = er.async_get(hass)
    source = registry.async_get_or_create(
        "sensor", "test", "physical", config_entry=owner,
        device_id=device.id, suggested_object_id="physical",
    )
    # Configured usage must remain visible even while a source is unavailable.
    hass.states.async_set(source.entity_id, "unavailable")
    records = [
        virtual_config("sensor.alias_one", [source.entity_id]),
        virtual_config("tag.alias_two", [source.entity_id]),
    ]
    entry = await setup_virtual(hass, "Aliases", records)
    usage, = usage_entries(hass, entry)
    state = hass.states.get(usage.entity_id)
    assert state.state == "2"
    assert state.attributes["virtual_entities"] == ["sensor.alias_one", "tag.alias_two"]
    assert state.attributes["source_entity_id"] == source.entity_id
    assert usage.device_id == device.id
    assert usage.entity_category.value == "diagnostic"
    with pytest.raises(HomeAssistantError, match="read-only source usage"):
        await hass.services.async_call(
            COMPONENT_DOMAIN, "set", {"entity_id": usage.entity_id, "value": "99"},
            blocking=True,
        )
    assert hass.states.get(usage.entity_id).state == "2"
    assert registry.async_get(source.entity_id) == source
    assert dr.async_get(hass).async_get(device.id) == device
    parent_device_id = registry.async_get("sensor.alias_one").device_id
    assert parent_device_id != device.id
    assert registry.async_get("sensor.alias_one_info").device_id == parent_device_id
    assert registry.async_get("sensor.alias_one_debug1").device_id == parent_device_id
    assert entry.options["devices"]["Aliases"] == records

    registry.async_update_entity(usage.entity_id, name="My usage label")
    hass.config_entries.async_update_entry(entry, options={"devices": {"Aliases": records[:1]}})
    await hass.async_block_till_done()
    remaining, = usage_entries(hass, entry)
    assert remaining.entity_id == usage.entity_id
    assert remaining.name == "My usage label"
    assert hass.states.get(usage.entity_id).state == "1"
    assert hass.states.get(usage.entity_id).attributes["virtual_entities"] == ["sensor.alias_one"]

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert usage_entries(hass, entry)[0].device_id == device.id
    assert hass.states.get(usage.entity_id).state == "1"

    hass.config_entries.async_update_entry(entry, options={"devices": {"Aliases": [
        virtual_config("sensor.alias_one", []),
    ]}})
    await hass.async_block_till_done()
    assert usage_entries(hass, entry) == []
    assert hass.states.get(usage.entity_id) is None
    assert registry.async_get(source.entity_id) == source
    assert dr.async_get(hass).async_get(device.id) == device


async def test_source_usage_late_registration_and_device_move(hass):
    entry = await setup_virtual(hass, "Aliases", [
        virtual_config("sensor.alias_one", ["sensor.physical"]),
    ])
    usage, = usage_entries(hass, entry)
    assert usage.device_id is None
    assert hass.states.get(usage.entity_id).state == "1"

    owner = MockConfigEntry(domain="test")
    owner.add_to_hass(hass)
    first = original_device(hass, owner, "first")
    second = original_device(hass, owner, "second")
    registry = er.async_get(hass)
    source = registry.async_get_or_create(
        "sensor", "test", "physical", config_entry=owner,
        device_id=first.id, suggested_object_id="physical",
    )
    await hass.async_block_till_done()
    assert registry.async_get(usage.entity_id).device_id == first.id
    registry.async_update_entity(source.entity_id, device_id=second.id)
    await hass.async_block_till_done()
    assert registry.async_get(usage.entity_id).device_id == second.id
    registry.async_remove(source.entity_id)
    await hass.async_block_till_done()
    assert registry.async_get(usage.entity_id).device_id is None
    assert hass.states.get(usage.entity_id).state == "1"


async def test_source_usage_multiple_entries_collisions_and_entry_deletion(hass):
    registry = er.async_get(hass)
    collision = registry.async_get_or_create(
        "sensor", "test", "collision",
        suggested_object_id="sensor_physical_virtual_layer_usage",
    )
    entries = [
        await setup_virtual(hass, name, [virtual_config(f"sensor.{name}", ["sensor.physical"])])
        for name in ("first", "second")
    ]
    first, second = [usage_entries(hass, entry)[0] for entry in entries]
    assert len({first.entity_id, second.entity_id, collision.entity_id}) == 3
    assert hass.states.get(first.entity_id).attributes["virtual_entities"] == ["sensor.first"]
    assert hass.states.get(second.entity_id).attributes["virtual_entities"] == ["sensor.second"]
    assert await hass.config_entries.async_remove(entries[0].entry_id)
    await hass.async_block_till_done()
    assert registry.async_get(first.entity_id) is None
    assert hass.states.get(first.entity_id) is None
    assert hass.states.get(second.entity_id).state == "1"
    assert registry.async_get(collision.entity_id) == collision


async def test_source_usage_explicit_attribute_sources_and_deduplication(hass):
    record = virtual_config("sensor.alias_one", ["sensor.physical"])
    record["attribute_sources"] = {
        "temperature": {"entity_id": "sensor.physical", "attribute": "temperature"},
        "humidity": {"entity_id": "sensor.other", "attribute": "humidity"},
    }
    entry = await setup_virtual(hass, "Aliases", [record])
    usages = [hass.states.get(item.entity_id) for item in usage_entries(hass, entry)]
    assert {state.attributes["source_entity_id"] for state in usages} == {
        "sensor.physical", "sensor.other",
    }
    assert all(state.state == "1" for state in usages)
    assert all(state.attributes["virtual_entities"] == ["sensor.alias_one"] for state in usages)
