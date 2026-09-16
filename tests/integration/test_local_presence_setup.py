"""UI configuration, real HA Bluetooth scanner data, Wi-Fi events and cleanup."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.components import bluetooth
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from habluetooth import BaseHaRemoteScanner
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.virtual_layer import config_flow as flow
from custom_components.virtual_layer.const import (
    ATTR_DEVICES,
    ATTR_GROUP_NAME,
    COMPONENT_DOMAIN,
)

pytestmark = pytest.mark.integration
MAC = "AA:BB:CC:DD:EE:FF"


@pytest.fixture(autouse=True)
def empty_bluetooth_host_cache():
    # Host BlueZ cache is absent on macOS; keep the real HA manager/scanners.
    with patch(
        "homeassistant.components.bluetooth.manager.async_load_history_from_system",
        return_value=({}, {}),
    ):
        yield


@pytest.mark.usefixtures("enable_bluetooth")
async def test_ab_gateway_wifi_ui_reload_and_absence(hass, freezer):
    hass.config.latitude, hass.config.longitude = 37.5, 127
    scanner = BaseHaRemoteScanner("ab_gateway", "AB Gateway", connectable=False)
    stop = scanner.async_setup()
    unregister = bluetooth.async_register_scanner(hass, scanner)
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "Phone"},
        options={ATTR_DEVICES: {}},
    )
    entry.add_to_hass(hass)
    try:
        manager = hass.config_entries.options
        result = await manager.async_init(
            entry.entry_id, data={flow.CONF_ACTION: flow.ACTION_ADD_ENTITY}
        )
        result = await manager.async_configure(
            result["flow_id"], {flow.CONF_REFERENCE_ENTITY_ID: []}
        )
        form = result["data_schema"]({})
        form.update(
            {"platform": "device_tracker", "entity_id": "device_tracker.local_phone"}
        )
        result = await manager.async_configure(result["flow_id"], form)
        form = result["data_schema"]({})
        form["local_presence_settings"].update(
            {
                "presence_enabled": True,
                "presence_wifi_entities": ["binary_sensor.phone_wifi"],
                "presence_ble_addresses": MAC,
                "presence_ble_sources": "ab_gateway",
                "presence_ble_timeout": 30,
            }
        )
        result = await manager.async_configure(result["flow_id"], form)
        assert result["type"] == "create_entry", result
        hass.config_entries.async_update_entry(entry, options=result["data"])
        hass.states.async_set("binary_sensor.phone_wifi", "off")
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert hass.states.get("device_tracker.local_phone").state == "not_home"
        # This is the same HA scanner API used by component-ab-gateway.
        scanner._async_on_advertisement(
            MAC.lower(), -65, "Phone", [], {}, {}, None, {}, bluetooth.MONOTONIC_TIME()
        )
        freezer.tick(timedelta(seconds=5))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()
        state = hass.states.get("device_tracker.local_phone")
        assert state.state == "home"
        assert state.attributes["latitude"] == 37.5
        assert state.attributes["location_presence_sources"] == ["ble:" + MAC]
        registry = er.async_get(hass)
        assert (
            registry.async_get("sensor.local_phone_debug1").device_id
            == registry.async_get("device_tracker.local_phone").device_id
        )
        freezer.tick(timedelta(seconds=31))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()
        assert hass.states.get("device_tracker.local_phone").state == "not_home"
        hass.states.async_set("binary_sensor.phone_wifi", "on")
        await hass.async_block_till_done()
        assert hass.states.get("device_tracker.local_phone").state == "home"
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert hass.states.get("device_tracker.local_phone").state == "home"
        hass.states.async_set("binary_sensor.phone_wifi", "off")
        await hass.async_block_till_done()
        assert hass.states.get("device_tracker.local_phone").state == "not_home"
        device_name, configured_entities = next(
            iter(entry.options[ATTR_DEVICES].items())
        )
        configured = configured_entities[0]
        result = await manager.async_init(
            entry.entry_id, data={flow.CONF_ACTION: flow.ACTION_EDIT_ENTITY}
        )
        result = await manager.async_configure(
            result["flow_id"],
            {
                flow.CONF_ENTITY_KEY: flow._selection_key_for_entity(
                    device_name, 0, configured
                )
            },
        )
        result = await manager.async_configure(
            result["flow_id"], {flow.CONF_REFERENCE_ENTITY_ID: []}
        )
        form = result["data_schema"]({})
        assert form["local_presence_settings"]["presence_ble_addresses"] == MAC
        form["local_presence_settings"]["presence_enabled"] = False
        result = await manager.async_configure(result["flow_id"], form)
        assert result["type"] == "create_entry", result
        assert "local_presence" not in result["data"][ATTR_DEVICES][device_name][0]
        hass.config_entries.async_update_entry(entry, options=result["data"])
        await hass.async_block_till_done()
        assert registry.async_get("sensor.local_phone_debug1") is None
        assert await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()
        freezer.tick(timedelta(seconds=40))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()
        assert hass.states.get("device_tracker.local_phone") is None
    finally:
        unregister()
        stop()
