"""Bulk source-device creation through both UI entry points."""

import asyncio
import pytest
import voluptuous as vol
from unittest.mock import patch
from homeassistant.data_entry_flow import InvalidData
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from custom_components.virtual_layer.const import COMPONENT_DOMAIN
from custom_components.virtual_layer import config_flow
from custom_components.virtual_layer import get_entity_from_domain
from homeassistant.helpers.template import Template
from homeassistant.components.climate import ClimateEntityFeature
from tests.flow_helpers import suggested_form_values
from tests.integration.test_entity_domain_coverage import (
    _raw_ui_entity,
    _native_template_sample,
)
from custom_components.virtual_layer.const import VIRTUAL_ENTITY_DOMAINS

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("domain", sorted(VIRTUAL_ENTITY_DOMAINS))
@pytest.mark.parametrize("source_state", ["sample", "unknown", "unavailable"])
async def test_supported_domains_can_be_prepared(hass, domain, source_state):
    source_entry = MockConfigEntry(domain="test_source")
    source_entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=source_entry.entry_id,
        identifiers={("test_source", "device")},
        name="Appliance",
    )
    source = er.async_get(hass).async_get_or_create(
        domain,
        "test_source",
        domain,
        suggested_object_id="appliance",
        device_id=device.id,
        config_entry=source_entry,
    )
    raw = _raw_ui_entity(domain)
    attrs = {
        config_flow.NATIVE_TEMPLATE_ATTRIBUTE_ALIASES.get(
            prop, prop
        ): _native_template_sample(domain, prop)
        for prop in config_flow.DOMAIN_NATIVE_TEMPLATE_PROPERTIES.get(domain, ())
        if prop not in config_flow.NATIVE_TEMPLATE_STATE_PROPERTIES
    }
    hass.states.async_set(
        source.entity_id,
        raw["initial_value"] if source_state == "sample" else source_state,
        attrs,
    )
    flow = config_flow.VirtualFlowHandler()
    flow.hass = hass
    await flow.async_step_copy_device({"source_device": device.id})
    result = await flow.async_step_copy_device_entities(
        {
            "device_name": "Virtual appliance",
            "source_entities": [source.entity_id],
        }
    )
    assert result.get("step_id") == "copy_device_review", result
    assert flow._copy_device_pending["entities"][0]["platform"] == domain


@pytest.fixture
def appliance(hass, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "custom_components.virtual_layer.cfg._meta_lock", asyncio.Lock()
    )
    monkeypatch.setattr(
        "custom_components.virtual_layer.cfg.default_meta_file",
        lambda _hass: str(tmp_path / "virtual_layer.meta.json"),
    )
    source = MockConfigEntry(domain="smartthings")
    source.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=source.entry_id,
        identifiers={("smartthings", "oven")},
        name="Oven",
        manufacturer="Samsung",
        model="Oven",
        sw_version="1.2",
        hw_version="A",
    )
    samples = {
        "sensor": ("ready", {}),
        "number": (
            "180",
            {"min": 30, "max": 250, "step": 5, "unit_of_measurement": "°C"},
        ),
        "select": ("bake", {"options": ["bake", "grill"]}),
        "button": ("unknown", {}),
        "binary_sensor": ("on", {"device_class": "motion"}),
        "climate": (
            "heat",
            {
                "hvac_modes": ["off", "heat"],
                "temperature": 65,
                "current_temperature": 50,
                "min_temp": 30,
                "max_temp": 90,
                "supported_features": int(ClimateEntityFeature.TARGET_TEMPERATURE),
            },
        ),
    }
    for domain, (state, attrs) in samples.items():
        entry = er.async_get(hass).async_get_or_create(
            domain,
            "smartthings",
            domain,
            suggested_object_id="oven",
            device_id=device.id,
            config_entry=source,
        )
        hass.states.async_set(entry.entity_id, state, attrs)
    return device, samples


async def _review_appliance(hass, appliance, selected=None):
    device, samples = appliance
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN, data={"group_name": "Existing"}, options={}
    )
    entry.add_to_hass(hass)
    manager = hass.config_entries.options
    result = await manager.async_init(entry.entry_id, data={"action": "copy_device"})
    result = await manager.async_configure(
        result["flow_id"], {"source_device": device.id}
    )
    result = await manager.async_configure(
        result["flow_id"],
        {
            "device_name": "Virtual oven",
            "source_entities": selected
            if selected is not None
            else [f"{domain}.oven" for domain in samples],
        },
    )
    assert result.get("step_id") == "copy_device_review", result
    return entry, manager, result


async def test_initial_setup_name_is_optional_only_when_copying(hass, appliance):
    manager = hass.config_entries.flow
    result = await manager.async_init(COMPONENT_DOMAIN, context={"source": "user"})
    marker = next(key for key in result["data_schema"].schema if key == "group_name")
    assert isinstance(marker, vol.Optional)
    result = await manager.async_configure(result["flow_id"], {})
    assert result["errors"] == {"group_name": "required"}
    result = await manager.async_configure(
        result["flow_id"], {"source_device": appliance[0].id}
    )
    assert result["step_id"] == "copy_device_entities"
    assert result["data_schema"]({})["device_name"] == "Oven Virtual"


async def test_empty_device_recovers_without_losing_name(hass, appliance):
    entry, manager, result = await _review_appliance(hass, appliance, ["sensor.oven"])
    for domain in appliance[1]:
        er.async_get(hass).async_update_entity(f"{domain}.oven", device_id=None)
    result = await manager.async_configure(result["flow_id"], {})
    assert result["step_id"] == "copy_device"
    assert result["errors"] == {"base": "device_sources_changed"}
    er.async_get(hass).async_update_entity("sensor.oven", device_id=appliance[0].id)
    result = await manager.async_configure(
        result["flow_id"], {"source_device": appliance[0].id}
    )
    assert result["data_schema"]({})["device_name"] == "Virtual oven"
    assert entry.options == {}


async def test_appliance_native_properties_commands_and_availability(hass, appliance):
    entry, manager, result = await _review_appliance(hass, appliance)
    result = await manager.async_configure(result["flow_id"], {})
    assert result["type"] == "create_entry"
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("number.oven_virtual").state == "180.0"
    assert hass.states.get("number.oven_virtual").attributes["max"] == 250
    assert hass.states.get("select.oven_virtual").attributes["options"] == [
        "bake",
        "grill",
    ]
    climate = hass.states.get("climate.oven_virtual")
    assert climate.attributes["temperature"] == 65
    assert climate.attributes["min_temp"] == 30
    assert climate.attributes["max_temp"] == 90
    assert hass.states.get("button.oven_virtual").state != "unavailable"
    hass.states.async_set("binary_sensor.oven", "off", {"device_class": "motion"})
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.oven_virtual").state == "off"
    calls = []

    async def record(call):
        calls.append((call.domain, call.service, dict(call.data)))

    for domain, service, method, args in [
        ("button", "press", "async_press", ()),
        ("number", "set_value", "async_set_native_value", (200,)),
        ("select", "select_option", "async_select_option", ("grill",)),
    ]:
        hass.services.async_register(domain, service, record)
        entity = get_entity_from_domain(hass, domain, f"{domain}.oven_virtual")
        await getattr(entity, method)(*args)
    assert calls == [
        ("button", "press", {"entity_id": ["button.oven"]}),
        ("number", "set_value", {"entity_id": ["number.oven"], "value": 200}),
        ("select", "select_option", {"entity_id": ["select.oven"], "option": "grill"}),
    ]
    for domain in ("number", "binary_sensor"):
        original = hass.states.get(f"{domain}.oven")
        hass.states.async_set(original.entity_id, "unknown", dict(original.attributes))
        await hass.async_block_till_done()
        assert hass.states.get(f"{domain}.oven_virtual").state == "unavailable"
        hass.states.async_set(
            original.entity_id, original.state, dict(original.attributes)
        )
    hass.states.async_set("button.oven", "unavailable")
    await hass.async_block_till_done()
    assert hass.states.get("button.oven_virtual").state == "unavailable"
    hass.states.async_set("button.oven", "unknown")
    await hass.async_block_till_done()
    assert hass.states.get("button.oven_virtual").state != "unavailable"
    device_key = next(iter(entry.options["devices"]))
    virtual_device = er.async_get(hass).async_get("number.oven_virtual").device_id
    companions = {
        entity.entity_id
        for entity in er.async_entries_for_device(er.async_get(hass), virtual_device)
    }
    assert len(companions) > len(appliance[1])
    result = await manager.async_init(entry.entry_id, data={"action": "delete_device"})
    result = await manager.async_configure(
        result["flow_id"], {"managed_device_name": device_key}
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    assert all(hass.states.get(entity_id) is None for entity_id in companions)
    assert dr.async_get(hass).async_get(appliance[0].id) is not None
    assert hass.states.get("number.oven") is not None


@pytest.mark.parametrize(
    "change", ["remove", "move", "disable", "replace", "id_collision"]
)
async def test_review_rejects_stale_sources_and_ids(hass, appliance, change):
    entry, manager, result = await _review_appliance(hass, appliance, ["sensor.oven"])
    registry = er.async_get(hass)
    source = registry.async_get("sensor.oven")
    if change == "remove":
        hass.states.async_remove("sensor.oven")
    elif change == "move":
        registry.async_update_entity(source.entity_id, device_id=None)
    elif change == "disable":
        registry.async_update_entity(
            source.entity_id, disabled_by=er.RegistryEntryDisabler.USER
        )
    elif change == "replace":
        registry.async_remove(source.entity_id)
        registry.async_get_or_create(
            "sensor",
            "smartthings",
            "replacement",
            suggested_object_id="oven",
            device_id=appliance[0].id,
        )
    else:
        hass.states.async_set("sensor.oven_virtual", "reserved")
    result = await manager.async_configure(result["flow_id"], {})
    assert result["step_id"] == "copy_device_entities"
    assert result["errors"] == {"base": "device_sources_changed"}
    assert entry.options == {}


async def test_review_preserves_concurrent_options_and_can_go_back(hass, appliance):
    entry, manager, result = await _review_appliance(hass, appliance, ["sensor.oven"])
    result = await manager.async_configure(result["flow_id"], {"action": "edit"})
    values = result["data_schema"]({})
    assert values == {"device_name": "Virtual oven", "source_entities": ["sensor.oven"]}
    result = await manager.async_configure(result["flow_id"], values)
    existing = {"devices": {"Legacy": ["malformed"]}, "vendor_data": {"keep": True}}
    hass.config_entries.async_update_entry(entry, options=existing)
    result = await manager.async_configure(result["flow_id"], {})
    assert result["type"] == "create_entry"
    assert entry.options["vendor_data"] == {"keep": True}
    assert entry.options["devices"]["Legacy"] == ["malformed"]
    assert (
        sum(
            len(entities)
            for key, entities in entry.options["devices"].items()
            if key != "Legacy"
        )
        == 1
    )


@pytest.mark.parametrize("domain", ["climate", "binary_sensor", "button"])
@pytest.mark.parametrize("policy", ["automatic", "keep_current", "force_helper"])
@pytest.mark.parametrize("change_source", [False, True])
async def test_edit_copy_retains_direct_helpers(hass, appliance, domain, policy, change_source):
    entry, manager, result = await _review_appliance(
        hass, appliance, [f"{domain}.oven"]
    )
    await manager.async_configure(result["flow_id"], {})
    before = next(iter(entry.options["devices"].values()))[0]
    selected_source = f"{domain}.oven"
    if change_source:
        selected_source = f"{domain}.replacement"
        original = hass.states.get(f"{domain}.oven")
        attrs = dict(original.attributes)
        if domain == "climate":
            attrs["temperature"] = 75
        hass.states.async_set(selected_source, original.state, attrs)
    result = await manager.async_init(entry.entry_id, data={"action": "edit_entity"})
    key = next(iter(config_flow._entity_choices(entry.options)))
    result = await manager.async_configure(result["flow_id"], {"entity_key": key})
    result = await manager.async_configure(
        result["flow_id"], {"reference_entity_id": [selected_source]}
    )
    assert result["step_id"] == "edit_entity_type"
    result = await manager.async_configure(
        result["flow_id"], {"target_entity_type": domain}
    )
    assert result["step_id"] == "edit_entity_helper"
    result = await manager.async_configure(
        result["flow_id"], {"helper_update_mode": policy}
    )
    assert result["step_id"] == "edit_entity", result
    result = await manager.async_configure(
        result["flow_id"], suggested_form_values(result["data_schema"])
    )
    assert result["type"] == "create_entry", result
    after = next(iter(entry.options["devices"].values()))[0]
    if not change_source or policy == "keep_current":
        assert after["value_template"] == before["value_template"]
        assert after["availability_template"] == before["availability_template"]
    else:
        assert after["value_template"] != before["value_template"]
        assert selected_source in after["availability_template"]
    assert after["source_entities"] == [selected_source]
    assert after["auto_helper"]["source_device_copy"] is True
    if domain == "climate":
        target = after["native_templates"]["target_temperature"]
        assert Template(target, hass).async_render() == (75 if change_source and policy != "keep_current" else 65)


@pytest.mark.parametrize("options_flow", [False, True])
async def test_copy_device(hass, options_flow, monkeypatch, tmp_path):
    # Metadata storage has a process-wide lock; each HA fixture owns a new loop.
    monkeypatch.setattr(
        "custom_components.virtual_layer.cfg._meta_lock", asyncio.Lock()
    )
    monkeypatch.setattr(
        "custom_components.virtual_layer.cfg.default_meta_file",
        lambda _hass: str(tmp_path / "virtual_layer.meta.json"),
    )
    source = MockConfigEntry(domain="smartthings")
    source.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=source.entry_id,
        identifiers={("smartthings", "washer")},
        name="Washer",
        manufacturer="Samsung",
        model="Washer model",
    )
    for domain, value, attrs in [("sensor", "washing", {}), ("switch", "on", {})]:
        entity = er.async_get(hass).async_get_or_create(
            domain,
            "smartthings",
            domain,
            suggested_object_id="washer",
            device_id=device.id,
            config_entry=source,
        )
        hass.states.async_set(entity.entity_id, value, attrs)
    excluded = er.async_get(hass).async_get_or_create(
        "sensor",
        "smartthings",
        "disabled",
        device_id=device.id,
        config_entry=source,
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    hass.states.async_set(excluded.entity_id, "10")
    hass.states.async_set("sensor.washer_virtual", "reserved")
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN, data={"group_name": "Existing"}, options={}
    )
    entry.add_to_hass(hass)
    if options_flow:
        manager = hass.config_entries.options
        result = await manager.async_init(
            entry.entry_id, data={"action": "copy_device"}
        )
        result = await manager.async_configure(
            result["flow_id"], {"source_device": device.id}
        )
    else:
        manager = hass.config_entries.flow
        result = await manager.async_init(
            "virtual_layer",
            context={"source": "user"},
            data={"group_name": "", "source_device": device.id},
        )
    assert result["step_id"] == "copy_device_entities"
    for invalid in ([excluded.entity_id], ["sensor.unrelated"]):
        with pytest.raises(InvalidData):
            await manager.async_configure(
                result["flow_id"],
                {
                    "device_name": "Virtual washer",
                    "source_entities": invalid,
                },
            )
    for invalid in ([],):
        result = await manager.async_configure(
            result["flow_id"],
            {
                "device_name": "Virtual washer",
                "source_entities": invalid,
            },
        )
        assert result["errors"] == {"source_entities": "no_device_entities"}
        assert entry.options == {}
    original_builder = config_flow._async_build_entity_config

    async def fail_second(hass, defaults):
        if defaults["platform"] == "switch":
            raise config_flow.InvalidDomainOptions
        return await original_builder(hass, defaults)

    with patch.object(
        config_flow, "_async_build_entity_config", side_effect=fail_second
    ):
        result = await manager.async_configure(
            result["flow_id"],
            {
                "device_name": "Virtual washer",
                "source_entities": ["sensor.washer", "switch.washer"],
            },
        )
    assert result["errors"] == {"base": "device_copy_failed"}
    assert entry.options == {}
    assert hass.states.get("sensor.washer_virtual_2") is None
    result = await manager.async_configure(
        result["flow_id"],
        {
            "device_name": "Virtual washer",
            "source_entities": ["sensor.washer", "switch.washer"],
        },
    )
    assert result["step_id"] == "copy_device_review"
    assert "sensor.washer_virtual_2" in result["description_placeholders"]["entities"]
    assert entry.options == {}
    result = await manager.async_configure(result["flow_id"], {})
    assert result["type"] == "create_entry", result
    options = result["data"] if options_flow else result["options"]
    entities = next(iter(options["devices"].values()))
    assert len(entities) == 2
    assert {e["platform"] for e in entities} == {"sensor", "switch"}
    assert {e["entity_id"] for e in entities} == {
        "sensor.washer_virtual_2",
        "switch.washer_virtual",
    }
    assert all(e["source_entities"] for e in entities)
    assert len(options["device_attributes"]) == 1
    metadata = next(iter(options["device_attributes"].values()))
    assert metadata["manufacturer"] == "Samsung"
    assert metadata["model"] == "Washer model"
    assert metadata["device_id"] != device.id
    assert entities[1]["command_actions"]
    assert dr.async_get(hass).async_get(device.id).name == "Washer"
    created = entry if options_flow else result["result"]
    await hass.async_block_till_done()
    if hass.states.get("sensor.washer_virtual_2") is None:
        assert await hass.config_entries.async_setup(created.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.washer_virtual_2").state == "washing"
    registry = er.async_get(hass)
    virtual_device = registry.async_get("sensor.washer_virtual_2").device_id
    assert virtual_device != device.id
    assert registry.async_get("switch.washer_virtual").device_id == virtual_device
    hass.states.async_set("sensor.washer", "finished")
    await hass.async_block_till_done()
    assert hass.states.get("sensor.washer_virtual_2").state == "finished"
    assert await hass.config_entries.async_reload(created.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get("sensor.washer_virtual_2").device_id == virtual_device
