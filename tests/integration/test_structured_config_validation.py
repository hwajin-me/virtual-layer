"""Malformed structured values remain editable and never replace stored options."""

import copy

import pytest
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.virtual_layer.config_flow import (
    CONF_ATTRIBUTES_JSON, VirtualFlowHandler, VirtualOptionsFlowHandler,
    _complete_domain_form_defaults, _entity_form_defaults,
)


@pytest.mark.parametrize("scope", ["create", "add", "edit"])
@pytest.mark.parametrize("payload", [{"value": float("inf")}, "nested:\n  1: wrong"])
async def test_invalid_structured_value_can_be_corrected_in_each_entity_flow(hass, scope, payload):
    original = {"platform": "switch", "name": "Audit switch", "entity_id": "switch.audit",
                "initial_value": "off", "attributes": {"original": True}}
    entry = MockConfigEntry(domain="virtual_layer", data={"group_name": "Audit"},
                            options={"devices": {"Audit": [original]}})
    entry.add_to_hass(hass)
    snapshot = copy.deepcopy(dict(entry.options))
    flow = VirtualFlowHandler() if scope == "create" else VirtualOptionsFlowHandler()
    flow.hass = hass
    if scope == "create":
        flow._pending_title = "Audit"
        flow._pending_data = {"group_name": "Audit"}
    else:
        flow.handler = entry.entry_id
    if scope == "edit":
        flow._edit_device_name, flow._edit_index = "Audit", 0
        flow._edit_source_entities = []
    form = _complete_domain_form_defaults(_entity_form_defaults("Audit", original))
    if scope != "edit":
        form["entity_id"] = "switch.audit_new"
    form[CONF_ATTRIBUTES_JSON] = payload
    submit = flow.async_step_edit_entity if scope == "edit" else flow.async_step_entity
    result = await submit(form)
    assert result["type"] == FlowResultType.FORM
    assert result["errors"][CONF_ATTRIBUTES_JSON] == "invalid_json"
    assert dict(entry.options) == snapshot
    form[CONF_ATTRIBUTES_JSON] = {"corrected": [1, "kept", None]}
    result = await submit(form)
    assert result["type"] == FlowResultType.CREATE_ENTRY
