"""Converted VOC helpers remain native mass sensors after reload."""

import pytest
import yaml
from pytest_homeassistant_custom_component.common import MockConfigEntry
from homeassistant.helpers import entity_registry as er

from custom_components.virtual_layer.config_flow import (
    CONF_DOMAIN_OPTIONS_JSON, CONF_NATIVE_VALUE_TEMPLATES, _reference_entity_defaults,
)
from custom_components.virtual_layer.const import ATTR_DEVICES, ATTR_GROUP_NAME, COMPONENT_DOMAIN

pytestmark = pytest.mark.integration


async def test_voc_mass_sensor_reload_and_companions(hass, tmp_path, monkeypatch):
    monkeypatch.setattr("custom_components.virtual_layer.cfg.default_meta_file", lambda hass: str(tmp_path / "voc.json"))
    source = "sensor.physical_tvoc"
    hass.states.async_set(source, "100", {"device_class": "volatile_organic_compounds_parts", "unit_of_measurement": "ppb"})
    defaults = _reference_entity_defaults(hass, [source], "sensor")
    record = {
        "platform": "sensor", "name": "Indoor VOC", "entity_id": "sensor.indoor_voc",
        "source_entities": [source], "value_template": defaults["value_template"],
        "native_templates": defaults[CONF_NATIVE_VALUE_TEMPLATES],
        **yaml.safe_load(defaults[CONF_DOMAIN_OPTIONS_JSON]),
    }
    entry = MockConfigEntry(domain=COMPONENT_DOMAIN, data={ATTR_GROUP_NAME: "VOC"}, options={ATTR_DEVICES: {"VOC": [record]}})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    for reload in (False, True):
        if reload:
            assert await hass.config_entries.async_reload(entry.entry_id)
            await hass.async_block_till_done()
        state = hass.states.get("sensor.indoor_voc")
        assert float(state.state) == pytest.approx(0.45)
        assert state.attributes["device_class"] == "volatile_organic_compounds"
        assert state.attributes["unit_of_measurement"] == "mg/m³"
        assert hass.states.get("sensor.indoor_voc_aqim").state == "moderate"
        registry = er.async_get(hass)
        parent = registry.async_get("sensor.indoor_voc")
        assert parent.device_id
        assert registry.async_get("sensor.indoor_voc_aqim").device_id == parent.device_id
    assert hass.states.get(source).state == "100"
    hass.config_entries.async_update_entry(entry, options={ATTR_DEVICES: {"VOC": []}})
    await hass.async_block_till_done()
    assert hass.states.get("sensor.indoor_voc") is None
    assert hass.states.get("sensor.indoor_voc_aqim") is None
