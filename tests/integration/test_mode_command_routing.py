"""Native HA services after helper regeneration, reload, and removal."""

import pytest
from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature, HVACMode
from homeassistant.components.humidifier import HumidifierEntity
from homeassistant.components.media_player import MediaPlayerEntity, MediaPlayerState
from homeassistant.components.select import SelectEntity
from homeassistant.components.vacuum import StateVacuumEntity, VacuumActivity
from homeassistant.components.water_heater import WaterHeaterEntity
from homeassistant.helpers import entity_registry as er
from homeassistant.const import UnitOfTemperature
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.virtual_layer.config_flow import (
    _auto_helper_profile, _build_entity_config, _entity_form_defaults, _entity_schema,
    _flatten_entity_form_sections, _reference_edit_defaults, _reference_entity_defaults,
)
from custom_components.virtual_layer.mode_commands import MODE_COMMANDS, MODE_FEATURES


SOURCE_CLASSES = {
    "climate": ClimateEntity, "humidifier": HumidifierEntity,
    "media_player": MediaPlayerEntity, "select": SelectEntity,
    "vacuum": StateVacuumEntity, "water_heater": WaterHeaterEntity,
}


@pytest.mark.parametrize("domain, command", [key for key in MODE_COMMANDS if key[0] != "fan"])
@pytest.mark.parametrize("policy", ["automatic", "keep_current", "force_helper"])
async def test_native_mode_routing_after_helper_edit(hass, domain, command, policy):
    field, catalogs = MODE_COMMANDS[(domain, command)]
    calls = []

    async def receive(self, *args, **kwargs):
        requested = args[0] if args else kwargs[field]
        assert requested in getattr(self, "_attr_" + catalogs[0])
        calls.append((self.entity_id, requested))

    source_class = type("SourceChoice", (SOURCE_CLASSES[domain],), {"async_" + command: receive})
    assert await async_setup_component(hass, domain, {})
    sources = []
    requested = "cool" if command == "set_hvac_mode" else "second"
    for index, choices in enumerate((
        ["off", "heat", "dry"] if command == "set_hvac_mode" else ["first", "shared"],
        ["off", "cool", "dry"] if command == "set_hvac_mode" else ["second", "shared"],
    )):
        source = source_class()
        source.entity_id = f"{domain}.physical_{index}"
        source._attr_name = f"Physical {index}"
        source._attr_unique_id = f"physical-{domain}-{index}"
        source._attr_supported_features = MODE_FEATURES.get((domain, command), 0)
        if domain == "climate":
            source._attr_supported_features = ClimateEntityFeature(source._attr_supported_features)
        source._attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL]
        source._attr_hvac_mode = HVACMode.HEAT
        source._attr_is_on = True
        source._attr_temperature_unit = UnitOfTemperature.CELSIUS
        source._attr_device_class = None
        source._attr_state = MediaPlayerState.PLAYING
        source._attr_activity = VacuumActivity.CLEANING
        source._attr_current_option = "shared"
        source._attr_current_operation = "shared"
        setattr(source, "_attr_" + catalogs[0], choices)
        for attribute in ("mode", "source", "sound_mode", "fan_speed", "preset_mode",
                          "fan_mode", "swing_mode", "swing_horizontal_mode"):
            setattr(source, "_attr_" + attribute, choices[-1])
        sources.append(source)
    await hass.data[domain].async_add_entities(sources)
    ids = [source.entity_id for source in sources]
    defaults = _reference_entity_defaults(hass, ids)
    target = f"{domain}.composite_modes"
    defaults.update({"device_name": "Modes", "entity_name": "Composite modes", "entity_id": target})
    device, config = _build_entity_config(_flatten_entity_form_sections(_entity_schema(defaults)({})))
    config["entity_id"] = target
    config["auto_helper"] = _auto_helper_profile(defaults)
    entry = MockConfigEntry(domain="virtual_layer", data={"group_name": "Modes"},
        options={"devices": {device: [config]}})
    entry.add_to_hass(hass)
    assert await async_setup_component(hass, "virtual_layer", {})
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    device_id = registry.async_get(target).device_id
    assert device_id is not None

    for edited in (False, True):
        if edited:
            current = _entity_form_defaults(device, config)
            refreshed = _reference_entity_defaults(hass, ids)
            if policy != "keep_current":
                current = _reference_edit_defaults(current, refreshed, config["auto_helper"],
                    force_template_helper=policy == "force_helper")
            _, config = _build_entity_config(_flatten_entity_form_sections(_entity_schema(current)({})))
            config["entity_id"] = target
            config["auto_helper"] = _auto_helper_profile(current)
            hass.config_entries.async_update_entry(entry, options={"devices": {device: [config]}})
            await hass.async_block_till_done()
            assert await hass.config_entries.async_reload(entry.entry_id)
            await hass.async_block_till_done()
        calls.clear()
        required = int(MODE_FEATURES.get((domain, command), 0))
        assert hass.states.get(target).attributes.get("supported_features", 0) & required == required, (
            config.get("native_templates"), hass.states.get(target).attributes
        )
        await hass.services.async_call(domain, command, {"entity_id": target, field: requested}, blocking=True)
        await hass.async_block_till_done()
        assert calls == [(ids[1], requested)]
        assert registry.async_get(target).device_id == device_id
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(target) is None
