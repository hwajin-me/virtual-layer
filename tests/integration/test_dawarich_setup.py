"""Dawarich config-entry lifecycle using documented API responses."""

import json

import pytest

from tests.flow_helpers import suggested_form_values
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.virtual_layer import config_flow as flow
from custom_components.virtual_layer.const import (
    ATTR_DEVICES,
    ATTR_GROUP_NAME,
    COMPONENT_DOMAIN,
)

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("polygon", [False, True])
async def test_dawarich_ui_create_edit_reload_and_disable(
    hass, aioclient_mock, polygon
):
    timestamp = dt_util.utcnow().timestamp()
    aioclient_mock.get(
        "https://example.test/api/v1/points",
        json=[
            {
                "latitude": "37.5",
                "longitude": "127.0",
                "timestamp": timestamp,
                "accuracy": 12,
                "raw_data": {"api_key": "private-test-key"},
            }
        ],
    )
    aioclient_mock.get(
        "https://example.test/api/v1/visits",
        json=[
            {
                "name": "Office",
                "started_at": timestamp - 120,
                "ended_at": timestamp - 60,
            }
        ],
    )
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "Travel"},
        options={ATTR_DEVICES: {}},
    )
    entry.add_to_hass(hass)
    manager = hass.config_entries.options
    result = await manager.async_init(
        entry.entry_id, data={flow.CONF_ACTION: flow.ACTION_ADD_ENTITY}
    )
    result = await manager.async_configure(
        result["flow_id"], {flow.CONF_REFERENCE_ENTITY_ID: []}
    )
    form = suggested_form_values(result["data_schema"])
    form.update(
        {"platform": "device_tracker", "entity_id": "device_tracker.dawarich_trip"}
    )
    result = await manager.async_configure(result["flow_id"], form)
    form = suggested_form_values(result["data_schema"])
    form[flow.CONF_DAWARICH_SETTINGS].update(
        {
            "dawarich_enabled": True,
            "dawarich_url": "https://example.test",
            "dawarich_api_key": "private-test-key",
            "dawarich_test_connection": True,
        }
    )
    if polygon:
        form[flow.CONF_DOMAIN_SETTINGS][flow.CONF_POLYGON_GEOJSON_JSON] = json.dumps(
            {
                "type": "Feature",
                "properties": {"name": "Office"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [126.9, 37.4],
                            [127.1, 37.4],
                            [127.1, 37.6],
                            [126.9, 37.6],
                            [126.9, 37.4],
                        ]
                    ],
                },
            }
        )
    result = await manager.async_configure(result["flow_id"], form)
    assert result["type"] == "create_entry", result
    options = result["data"]
    device_name = next(iter(options[ATTR_DEVICES]))
    configured = options[ATTR_DEVICES][device_name][0]
    hass.config_entries.async_update_entry(entry, options=options)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    state = hass.states.get("device_tracker.dawarich_trip")
    assert state.attributes["latitude"] == 37.5
    assert state.attributes["dawarich_visit"]["name"] == "Office"
    assert state.attributes["dawarich_stale"] is False
    if polygon:
        assert state.state == "Office"
        registry = er.async_get(hass)
        assert (
            registry.async_get("sensor.dawarich_trip_zone").device_id
            == registry.async_get("device_tracker.dawarich_trip").device_id
        )
    for entity in hass.states.async_all():
        assert "private-test-key" not in str(entity.attributes)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert (
        hass.states.get("device_tracker.dawarich_trip").attributes["latitude"] == 37.5
    )

    result = await manager.async_init(
        entry.entry_id, data={flow.CONF_ACTION: flow.ACTION_EDIT_ENTITY}
    )
    result = await manager.async_configure(
        result["flow_id"],
        {
            flow.CONF_ENTITY_KEY: flow._selection_key_for_entity(
                device_name, 0, configured
            ),
        },
    )
    result = await manager.async_configure(
        result["flow_id"], {flow.CONF_REFERENCE_ENTITY_ID: []}
    )
    assert result["step_id"] == "edit_entity", result
    form = suggested_form_values(result["data_schema"])
    assert form[flow.CONF_DAWARICH_SETTINGS]["dawarich_api_key"] == "private-test-key"
    assert form[flow.CONF_DAWARICH_SETTINGS]["dawarich_test_connection"] is False
    form[flow.CONF_DAWARICH_SETTINGS]["dawarich_enabled"] = False
    if polygon:
        form[flow.CONF_DOMAIN_SETTINGS][flow.CONF_POLYGON_GEOJSON_JSON] = ""
    result = await manager.async_configure(result["flow_id"], form)
    assert result["type"] == "create_entry", result
    assert "dawarich" not in result["data"][ATTR_DEVICES][device_name][0]
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
