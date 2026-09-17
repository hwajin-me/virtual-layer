"""Regression coverage for creating entities without source entities."""

import pytest
import voluptuous as vol
from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType, section
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.virtual_layer.config_flow import (
    ACTION_ADD_ENTITY,
    ACTION_COPY_ENTITY,
    CONF_ACTION,
    CONF_ADD_FIRST_ENTITY,
    CONF_DEVICE_NAME,
    CONF_ENTITY_NAME,
    CONF_INITIAL_VALUE,
    CONF_ENTITY_KEY,
    _flatten_entity_form_sections,
    _entity_schema,
)
from custom_components.virtual_layer.const import (
    ATTR_DEVICES,
    ATTR_ENTITY_ID,
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
    if result.get("step_id") == "tracker_settings":
        result = await manager.async_configure(result["flow_id"], _suggested_values(result["data_schema"]))
    if result["type"] == FlowResultType.FORM:
        assert not result["errors"]
        submitted = _suggested_values(result["data_schema"])
        result = await manager.async_configure(result["flow_id"], submitted)
    assert result["type"] == FlowResultType.CREATE_ENTRY, result


async def test_copy_entity_opens_editable_copy_with_new_id_and_preserves_original(hass):
    """Copying starts a new entity flow and never replaces the source record."""
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "Kitchen"},
        options={
            ATTR_DEVICES: {
                "Kitchen": [
                    {
                        "platform": "sensor",
                        "name": "Temperature",
                        ATTR_ENTITY_ID: "sensor.kitchen_temperature",
                        "source_entities": ["sensor.source_temperature"],
                        "value_template": "{{ states('sensor.source_temperature') }}",
                    }
                ]
            }
        },
    )
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.source_temperature", "21")

    result = await hass.config_entries.options.async_init(
        entry.entry_id, data={CONF_ACTION: ACTION_COPY_ENTITY}
    )
    assert result["step_id"] == "copy_entity"
    selection = _suggested_values(result["data_schema"])[CONF_ENTITY_KEY]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_ENTITY_KEY: selection}
    )
    assert result["step_id"] == "entity"

    submitted = _flatten_entity_form_sections(_suggested_values(result["data_schema"]))
    assert submitted[CONF_ENTITY_NAME] == "Temperature Copy"
    assert submitted[ATTR_ENTITY_ID] == "sensor.kitchen_temperature_copy"
    result = await hass.config_entries.options.async_configure(result["flow_id"], submitted)
    assert result["type"] == FlowResultType.CREATE_ENTRY, result
    copied = result["data"][ATTR_DEVICES]["Kitchen"]
    assert len(copied) == 2
    assert copied[0][ATTR_ENTITY_ID] == "sensor.kitchen_temperature"
    assert copied[1][ATTR_ENTITY_ID] == "sensor.kitchen_temperature_copy"
    assert copied[1]["source_entities"] == ["sensor.source_temperature"]
