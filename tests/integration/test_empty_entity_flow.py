"""Regression coverage for creating entities without source entities."""

import pytest
import voluptuous as vol
from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType, section
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.virtual_layer.config_flow import (
    ACTION_ADD_ENTITY,
    CONF_ACTION,
    CONF_ADD_FIRST_ENTITY,
    CONF_DEVICE_NAME,
    CONF_ENTITY_NAME,
    CONF_INITIAL_VALUE,
    _flatten_entity_form_sections,
    _entity_schema,
)
from custom_components.virtual_layer.const import (
    ATTR_DEVICES,
    ATTR_GROUP_NAME,
    COMPONENT_DOMAIN,
)


def _suggested_values(schema):
    values = {}
    for marker, validator in schema.schema.items():
        if isinstance(validator, section):
            values[marker.schema] = _suggested_values(validator.schema)
        elif isinstance(marker, vol.Marker) and marker.default is not vol.UNDEFINED:
            values[marker.schema] = marker.description["suggested_value"]
    return values


def test_empty_entity_frontend_suggestions_are_valid_and_keep_sources_empty():
    """Suggested values are editable frontend values, not placeholder text."""
    schema = _entity_schema()
    submitted = _suggested_values(schema)
    validated = _flatten_entity_form_sections(schema(submitted))
    assert validated["source_entities_text"] == ""
    assert validated["icon_template"] == ""


@pytest.mark.parametrize(
    "platform", ["sensor", "switch", "light", "device_tracker", "vacuum"]
)
@pytest.mark.parametrize("initial_setup", [False, True])
async def test_add_entity_without_sources(hass, platform, initial_setup):
    if initial_setup:
        manager = hass.config_entries.flow
        result = await manager.async_init(
            COMPONENT_DOMAIN,
            context={"source": SOURCE_USER},
            data={ATTR_GROUP_NAME: "Empty", CONF_ADD_FIRST_ENTITY: True},
        )
    else:
        entry = MockConfigEntry(
            domain=COMPONENT_DOMAIN,
            data={ATTR_GROUP_NAME: "Empty"},
            options={ATTR_DEVICES: {}},
        )
        entry.add_to_hass(hass)
        manager = hass.config_entries.options
        result = await manager.async_init(
            entry.entry_id, data={CONF_ACTION: ACTION_ADD_ENTITY}
        )
    result = await manager.async_configure(result["flow_id"], {})
    assert result["step_id"] == "entity"
    submitted = _suggested_values(result["data_schema"])
    submitted.update(
        {
            "platform": platform,
            CONF_DEVICE_NAME: "Empty",
            CONF_ENTITY_NAME: "Placeholder",
            CONF_INITIAL_VALUE: "unknown",
        }
    )
    result = await manager.async_configure(result["flow_id"], submitted)
    if result["type"] == FlowResultType.FORM:
        assert not result["errors"]
        submitted = _suggested_values(result["data_schema"])
        result = await manager.async_configure(result["flow_id"], submitted)
    assert result["type"] == FlowResultType.CREATE_ENTRY, result
