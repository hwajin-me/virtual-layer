"""Reverse source references, device ownership, and companion lifecycle."""

import asyncio

import pytest
from homeassistant.core import State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, mock_restore_cache

from custom_components.virtual_layer.const import COMPONENT_DOMAIN, DIAGNOSTIC_UNIQUE_ID_MARKER

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


def assert_mirrors_target(hass, usage, target_id):
    state = hass.states.get(usage.entity_id)
    assert state.state == hass.states.get(target_id).state
    assert state.attributes["virtual_entity_id"] == target_id
    assert state.attributes["virtual_entities"] == [target_id]


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
    usages = usage_entries(hass, entry)
    assert len(usages) == 2
    usage = next(item for item in usages if hass.states.get(item.entity_id).attributes["virtual_entity_id"] == "sensor.alias_one")
    for item in usages:
        target_id = hass.states.get(item.entity_id).attributes["virtual_entity_id"]
        assert_mirrors_target(hass, item, target_id)
        assert item.device_id == device.id
    state = hass.states.get(usage.entity_id)
    assert state.attributes["source_entity_id"] == source.entity_id
    assert usage.device_id == device.id
    assert usage.entity_category.value == "diagnostic"
    with pytest.raises(HomeAssistantError, match="read-only source usage"):
        await hass.services.async_call(
            COMPONENT_DOMAIN, "set", {"entity_id": usage.entity_id, "value": "99"},
            blocking=True,
        )
    assert_mirrors_target(hass, usage, "sensor.alias_one")
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
    assert_mirrors_target(hass, usage, "sensor.alias_one")
    assert hass.states.get(usage.entity_id).attributes["virtual_entities"] == ["sensor.alias_one"]

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert usage_entries(hass, entry)[0].device_id == device.id
    assert_mirrors_target(hass, usage, "sensor.alias_one")

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
    assert_mirrors_target(hass, usage, "sensor.alias_one")

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
    assert_mirrors_target(hass, usage, "sensor.alias_one")


async def test_source_usage_multiple_entries_collisions_and_entry_deletion(hass):
    registry = er.async_get(hass)
    collision = registry.async_get_or_create(
        "sensor", "test", "collision",
        suggested_object_id="sensor_physical_sensor_first_virtual",
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
    assert_mirrors_target(hass, second, "sensor.second")
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
    assert all(state.state == hass.states.get("sensor.alias_one").state for state in usages)
    assert all(state.attributes["virtual_entities"] == ["sensor.alias_one"] for state in usages)


@pytest.mark.parametrize("existing_usage", [False, True])
async def test_saved_entities_get_current_source_links_on_startup(hass, existing_usage):
    """Old saved options need no UI save, regardless of restored diagnostic data."""
    owner = MockConfigEntry(domain="test")
    owner.add_to_hass(hass)
    device = original_device(hass, owner, "physical")
    registry = er.async_get(hass)
    source = registry.async_get_or_create(
        "sensor", "test", "physical", config_entry=owner,
        device_id=device.id, suggested_object_id="physical",
    )
    records = [virtual_config("sensor.existing_alias", [source.entity_id])]
    records[0]["persistent"] = True
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={"group_name": "Existing"},
        options={"devices": {"Existing": records}},
    )
    entry.add_to_hass(hass)
    restored = [State("sensor.existing_alias", "42", {"source_entities": ["sensor.old"]})]
    if existing_usage:
        old_usage = registry.async_get_or_create(
            "sensor", COMPONENT_DOMAIN,
            f"{entry.entry_id}{DIAGNOSTIC_UNIQUE_ID_MARKER}source_usage:{source.entity_id}",
            config_entry=entry, suggested_object_id="existing_usage",
        )
        registry.async_update_entity(old_usage.entity_id, name="My source links")
        restored.append(State(old_usage.entity_id, "99", {
            "source_entity_id": "sensor.old",
            "virtual_entities": ["sensor.deleted_alias"],
        }))
    mock_restore_cache(hass, restored)

    # The physical integration has not published a state yet, as during startup.
    assert hass.states.get(source.entity_id) is None
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    usage, = usage_entries(hass, entry)
    assert usage.device_id == device.id
    assert_mirrors_target(hass, usage, "sensor.existing_alias")
    assert hass.states.get(usage.entity_id).attributes["virtual_entities"] == ["sensor.existing_alias"]
    assert hass.states.get(usage.entity_id).attributes["source_entity_id"] == source.entity_id
    assert hass.states.get("sensor.existing_alias").attributes["source_entities"] == [source.entity_id]
    assert entry.options["devices"]["Existing"] == records
    if existing_usage:
        assert usage.entity_id == old_usage.entity_id
        assert usage.name == "My source links"


@pytest.mark.parametrize("damage", ["deleted", "stale"])
async def test_reload_recreates_and_repairs_source_links_without_options_edit(hass, damage):
    owner = MockConfigEntry(domain="test")
    owner.add_to_hass(hass)
    device = original_device(hass, owner, "physical")
    registry = er.async_get(hass)
    source = registry.async_get_or_create(
        "sensor", "test", "physical", config_entry=owner,
        device_id=device.id, suggested_object_id="physical",
    )
    hass.states.async_set(source.entity_id, "23", {"unit_of_measurement": "°C"})
    source_state = hass.states.get(source.entity_id)
    entry = await setup_virtual(hass, "Existing", [
        virtual_config("sensor.existing_alias", [source.entity_id]),
    ])
    usage, = usage_entries(hass, entry)
    original_options = entry.options
    if damage == "deleted":
        registry.async_remove(usage.entity_id)
        await hass.async_block_till_done()
        assert hass.states.get(usage.entity_id) is None
    else:
        registry.async_update_entity(usage.entity_id, device_id=None)
        hass.states.async_set(usage.entity_id, "99", {"virtual_entities": ["sensor.deleted_alias"]})
        await hass.async_block_till_done()

    for _ in range(2):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        repaired, = usage_entries(hass, entry)
        assert repaired.entity_id == usage.entity_id
        assert repaired.unique_id == usage.unique_id
        assert repaired.device_id == device.id
        assert_mirrors_target(hass, repaired, "sensor.existing_alias")
        assert hass.states.get(repaired.entity_id).attributes["virtual_entities"] == ["sensor.existing_alias"]
        assert entry.options == original_options
        assert registry.async_get(source.entity_id) == source
        assert hass.states.get(source.entity_id) is source_state
        assert dr.async_get(hass).async_get(device.id) == device


async def test_usage_names_identify_each_target_and_values_update_live(hass):
    hass.states.async_set("sensor.physical", "10")
    records = []
    for suffix, multiplier in (("one", 2), ("two", 3)):
        record = virtual_config(f"sensor.alias_{suffix}", ["sensor.physical"])
        record.update({
            "name": "거실 온도",
            "entity_key": suffix,
            "value_template": "{{ states('sensor.physical') | float(0) * " + str(multiplier) + " }}",
        })
        records.append(record)
    entry = await setup_virtual(hass, "Aliases", records)
    usages = {
        hass.states.get(item.entity_id).attributes["virtual_entity_id"]: item
        for item in usage_entries(hass, entry)
    }
    assert len(usages) == 2
    for target_id, usage in usages.items():
        state = hass.states.get(usage.entity_id)
        assert state.attributes["friendly_name"] == f"거실 온도 ({target_id})"
        assert_mirrors_target(hass, usage, target_id)
    assert float(hass.states.get(usages["sensor.alias_one"].entity_id).state) == 20
    assert float(hass.states.get(usages["sensor.alias_two"].entity_id).state) == 30
    hass.states.async_set("sensor.physical", "7")
    await asyncio.sleep(0.1)
    await hass.async_block_till_done()
    assert float(hass.states.get(usages["sensor.alias_one"].entity_id).state) == 14
    assert float(hass.states.get(usages["sensor.alias_two"].entity_id).state) == 21

    usage = usages["sensor.alias_one"]
    for value in ("unavailable", "unknown", "on", "23.5"):
        hass.states.async_set("sensor.alias_one", value)
        await hass.async_block_till_done()
        assert hass.states.get(usage.entity_id).state == value
    hass.states.async_remove("sensor.alias_one")
    await hass.async_block_till_done()
    assert hass.states.get(usage.entity_id).state == "unavailable"
    hass.states.async_set("sensor.alias_one", "42")
    await hass.async_block_till_done()
    assert hass.states.get(usage.entity_id).state == "42"

    registry = er.async_get(hass)
    registry.async_update_entity("sensor.alias_one", name="거실 평균 온도")
    await hass.async_block_till_done()
    assert registry.async_get(usage.entity_id).original_name == "거실 평균 온도 (sensor.alias_one)"
    registry.async_update_entity(usage.entity_id, name="My custom label")
    registry.async_update_entity("sensor.alias_one", name="거실 현재 온도")
    await hass.async_block_till_done()
    assert registry.async_get(usage.entity_id).name == "My custom label"
    assert registry.async_get(usage.entity_id).original_name == "거실 현재 온도 (sensor.alias_one)"

    renamed_records = [{**records[0], "entity_id": "sensor.alias_renamed"}, records[1]]
    hass.config_entries.async_update_entry(entry, options={"devices": {"Aliases": renamed_records}})
    await hass.async_block_till_done()
    assert registry.async_get(usage.entity_id).unique_id == usage.unique_id
    assert_mirrors_target(hass, usage, "sensor.alias_renamed")
    assert registry.async_get(usage.entity_id).original_name.endswith("(sensor.alias_renamed)")
    assert registry.async_get(usage.entity_id).name == "My custom label"


@pytest.mark.parametrize("disabled", [False, True])
async def test_legacy_shared_count_sensor_migrates_to_separate_live_targets(hass, disabled):
    records = [virtual_config(f"sensor.alias_{suffix}", ["sensor.physical"]) for suffix in ("a", "b")]
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN, data={"group_name": "Aliases"},
        options={"devices": {"Aliases": records}},
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    legacy_unique_id = f"{entry.entry_id}{DIAGNOSTIC_UNIQUE_ID_MARKER}source_usage:sensor.physical"
    legacy = registry.async_get_or_create(
        "sensor", COMPONENT_DOMAIN, legacy_unique_id,
        config_entry=entry, suggested_object_id="legacy_count",
    )
    registry.async_update_entity(
        legacy.entity_id, name="My existing label",
        disabled_by=er.RegistryEntryDisabler.USER if disabled else None,
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    migrated = registry.async_get(legacy.entity_id)
    assert migrated.unique_id != legacy_unique_id
    assert migrated.name == "My existing label"
    assert bool(migrated.disabled_by) == disabled
    assert len(usage_entries(hass, entry)) == 2
    if not disabled:
        assert_mirrors_target(hass, migrated, "sensor.alias_a")
    assert registry.async_get_entity_id("sensor", COMPONENT_DOMAIN, legacy_unique_id) is None
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert len(usage_entries(hass, entry)) == 2
    assert registry.async_get(legacy.entity_id).unique_id == migrated.unique_id
