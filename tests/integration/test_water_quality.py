"""Water sensor UI configuration publishes native properties after reload."""

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from homeassistant.helpers import entity_registry as er

from custom_components.virtual_layer import config_flow as flow
from custom_components.virtual_layer.const import ATTR_DEVICES, ATTR_GROUP_NAME, COMPONENT_DOMAIN
from tests.flow_helpers import suggested_form_values


async def test_water_conversion_ui_setup_reload_and_device_grouping(hass):
    hass.states.async_set("sensor.ec_a", "500", {"device_class": "conductivity", "unit_of_measurement": "uS/cm"})
    hass.states.async_set("sensor.ec_b", "1.5", {"device_class": "conductivity", "unit_of_measurement": "mS/cm"})
    entry = MockConfigEntry(domain=COMPONENT_DOMAIN, data={ATTR_GROUP_NAME: "Water"}, options={ATTR_DEVICES: {}})
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id, data={flow.CONF_ACTION: flow.ACTION_ADD_ENTITY})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {flow.CONF_REFERENCE_ENTITY_ID: ["sensor.ec_a", "sensor.ec_b"]})
    assert result["step_id"] == "sensor_conversion"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {flow.CONF_SENSOR_CONVERSION: "state", flow.CONF_SENSOR_AGGREGATION: "average"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {flow.CONF_USE_TEMPLATE_HELPER: True})
    defaults = flow._flatten_entity_form_sections(suggested_form_values(result["data_schema"]))
    result = await hass.config_entries.options.async_configure(result["flow_id"], defaults)
    assert result["type"] == "create_entry"
    options = result["data"]
    record = next(iter(options[ATTR_DEVICES].values()))[0]
    entity_id = record["entity_id"]
    hass.config_entries.async_update_entry(entry, options=options)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert float(state.state) == pytest.approx(1000)
    assert state.attributes["device_class"] == "conductivity"
    assert state.attributes["unit_of_measurement"] == "μS/cm"
    assert state.attributes["state_class"] == "measurement"
    registry = er.async_get(hass)
    parent = registry.async_get(entity_id)
    companions = [item for item in er.async_entries_for_config_entry(registry, entry.entry_id)
                  if item.entity_id.endswith(("_info", "_debug1", "_debug2"))]
    assert len(companions) == 3
    assert parent.device_id and all(item.device_id == parent.device_id for item in companions)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set("sensor.ec_b", "1.0", {"device_class": "conductivity", "unit_of_measurement": "mS/cm"})
    await hass.async_block_till_done()
    assert float(hass.states.get(entity_id).state) == pytest.approx(750)
    hass.states.async_set("sensor.ec_b", "0.001", {"device_class": "conductivity", "unit_of_measurement": "S/cm"})
    await hass.async_block_till_done()
    assert float(hass.states.get(entity_id).state) == pytest.approx(750)
    assert await hass.config_entries.async_unload(entry.entry_id)
