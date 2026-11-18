"""Adding a new domain must not reacquire an options listener's setup lock."""

import asyncio
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.virtual_layer.const import (
    ATTR_DEVICES,
    ATTR_ENTITY_KEY,
    ATTR_GROUP_NAME,
    COMPONENT_DOMAIN,
)

pytestmark = pytest.mark.integration


async def test_add_first_camera_preserves_loaded_entities_without_lock_reentry(hass):
    stable = {"platform": "sensor", "name": "Stable", "entity_id": "sensor.stable"}
    camera = {"platform": "camera", "name": "Added", "entity_id": "camera.added"}
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "Platform lock"},
        options={ATTR_DEVICES: {"Device": [stable]}},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    sensor = hass.data["sensor"].get_entity("sensor.stable")
    original_forward = hass.config_entries.async_forward_entry_setups

    async def forward_with_current_ha_lock_contract(config_entry, platforms):
        assert not (
            config_entry.state is ConfigEntryState.LOADED
            and config_entry.setup_lock.locked()
        ), "A loaded entry would deadlock reacquiring its own setup lock"
        await original_forward(config_entry, platforms)

    with (
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            side_effect=forward_with_current_ha_lock_contract,
        ),
        patch.object(hass.config_entries, "async_reload") as reload_entry,
    ):
        hass.config_entries.async_update_entry(
            entry, options={ATTR_DEVICES: {"Device": [stable, camera]}}
        )
        await asyncio.wait_for(hass.async_block_till_done(), timeout=5)
        assert hass.data["camera"].get_entity("camera.added") is not None
        assert hass.data["sensor"].get_entity("sensor.stable") is sensor
        assert entry.state is ConfigEntryState.LOADED
        assert not entry.setup_lock.locked()
        reload_entry.assert_not_called()


async def test_configured_rename_wins_over_previous_id_guard(hass, monkeypatch):
    from homeassistant.helpers import entity_registry as er

    from custom_components.virtual_layer.cfg import BlendedCfg

    config = {
        "platform": "sensor", "name": "Rename", "entity_id": "sensor.before",
        ATTR_ENTITY_KEY: "rename",
    }
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "Rename guard"},
        options={ATTR_DEVICES: {"Device": [config]}},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    unique_id = registry.async_get("sensor.before").unique_id
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.update_listeners
    original_load = BlendedCfg.async_load

    async def load_with_registry_callbacks(config_loader):
        await original_load(config_loader)
        # Current HA eagerly schedules registry listeners. Let the previous
        # generation's rename guard run while the new config is being loaded.
        for _ in range(3):
            await asyncio.sleep(0)

    monkeypatch.setattr(BlendedCfg, "async_load", load_with_registry_callbacks)
    hass.config_entries.async_update_entry(
        entry,
        options={ATTR_DEVICES: {"Device": [{**config, "entity_id": "sensor.after"}]}},
    )
    await asyncio.wait_for(hass.async_block_till_done(), timeout=5)
    assert registry.async_get("sensor.before") is None
    assert hass.states.get("sensor.before") is None
    assert registry.async_get("sensor.after").unique_id == unique_id
    assert hass.data["sensor"].get_entity("sensor.after") is not None
