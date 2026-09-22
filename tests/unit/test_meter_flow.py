"""Draft transitions and field contracts for the dedicated utility meter step."""
import pytest

from custom_components.virtual_layer.meter_flow import MeterFlow, meter_form, meter_schema
from custom_components.virtual_layer.config_flow import _entity_schema, _flatten_entity_form_sections


class DraftFlow(MeterFlow):
    def __init__(self):
        self._entity_defaults = {"platform": "sensor", "entity_id": "sensor.billing", "utility_meter_enabled": True}

    def async_show_form(self, **kwargs):
        return {"type": "form", **kwargs}

    def async_abort(self, **kwargs):
        return {"type": "abort", **kwargs}

    @meter_form
    async def async_step_entity(self, user_input=None):
        return {"type": "create_entry" if user_input is not None else "form", "data": user_input}

    @meter_form
    async def async_step_edit_entity(self, user_input=None):
        return {"type": "create_entry" if user_input is not None else "form", "data": user_input}


@pytest.mark.parametrize("edit", [False, True])
async def test_separate_step_back_clear_and_confirm(edit):
    flow = DraftFlow()
    method = flow.async_step_edit_entity if edit else flow.async_step_entity
    result = await method({**flow._entity_defaults, "utility_meter_start": "2026-01-01T00:00:00", "utility_meter_rate": 100})
    assert result["step_id"] == ("edit_utility_meter" if edit else "utility_meter")
    fields = result["data_schema"]({})
    assert "utility_meter_start" not in fields  # omission deliberately clears
    result = await flow._async_meter_step({**fields, "utility_meter_rate": 200, "back": True})
    assert result["type"] == "form"
    assert flow._entity_defaults["utility_meter_rate"] == 200
    result = await method(flow._entity_defaults)
    assert result["type"] == "form"
    result = await flow._async_meter_step(result["data_schema"]({}))
    assert result["type"] == "create_entry"
    assert result["data"]["utility_meter_start"] == ""
    assert result["data"]["utility_meter_rate"] == 200
    # Source/helper steps can re-submit the confirmed draft without loops.
    assert (await method(result["data"]))["type"] == "create_entry"


@pytest.mark.parametrize("edit", [False, True])
@pytest.mark.parametrize("enabled", [False, True])
async def test_comparison_choice_roundtrip(edit, enabled):
    flow = DraftFlow()
    method = flow.async_step_edit_entity if edit else flow.async_step_entity
    result = await method({**flow._entity_defaults, "utility_meter_compare_previous_month": enabled})
    values = result["data_schema"]({})
    assert values["utility_meter_compare_previous_month"] is enabled
    result = await flow._async_meter_step(values)
    assert result["type"] == "create_entry"
    assert result["data"]["utility_meter_compare_previous_month"] is enabled


async def test_bad_correction_keeps_draft_and_disable_allows_recovery():
    flow = DraftFlow()
    result = await flow.async_step_entity(flow._entity_defaults)
    values = result["data_schema"]({})
    result = await flow.async_step_utility_meter({**values, "utility_meter_current_value": "nan"})
    assert result["errors"] == {"utility_meter_current_value": "invalid_domain_options"}
    assert result["data_schema"]({})["utility_meter_current_value"] == "nan"
    result = await flow.async_step_utility_meter({**values, "utility_meter_enabled": False})
    assert result["type"] == "create_entry"
    assert not result["data"]["utility_meter_enabled"]


async def test_uninitialized_step_aborts_without_writing():
    assert (await DraftFlow().async_step_edit_utility_meter())["type"] == "abort"


def test_general_form_has_only_meter_toggle():
    fields = _flatten_entity_form_sections(_entity_schema({"platform": "sensor"})({}))
    assert {field for field in fields if field.startswith("utility_meter_")} == {"utility_meter_enabled"}
    assert "utility_meter_cycle" in meter_schema({})({})
