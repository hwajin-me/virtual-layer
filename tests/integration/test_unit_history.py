"""Unit edits require an explicit, scoped statistics handling decision."""
from copy import deepcopy
from datetime import timedelta
from functools import partial

import pytest

from tests.flow_helpers import suggested_form_values
from pytest_homeassistant_custom_component.common import MockConfigEntry
from homeassistant.util import dt as dt_util

from custom_components.virtual_layer import config_flow as flow_module, unit_history
from custom_components.virtual_layer.const import (
    COMPONENT_DOMAIN, ATTR_DEVICES, ATTR_GROUP_NAME, CONF_NATIVE_TEMPLATES,
    CONF_SOURCE_ENTITIES,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def recorder_db_url(tmp_path):
    """Use an isolated SQLite database, independent of global DB test options."""
    return f"sqlite:///{tmp_path / 'recorder.db'}"


async def make_unit_flow(hass, unit="kW"):
    record = {"platform": "sensor", "entity_id": "sensor.unit_example", "name": "Example",
              "initial_value": "1000", "unit_of_measurement": "W"}
    entry = MockConfigEntry(domain=COMPONENT_DOMAIN, data={ATTR_GROUP_NAME: "Units"},
                            options={ATTR_DEVICES: {"Units": [record]}})
    entry.add_to_hass(hass)
    flow = flow_module.VirtualOptionsFlowHandler()
    flow.hass = hass
    flow.handler = entry.entry_id
    flow.context = {"entry_id": entry.entry_id}
    flow._edit_device_name = "Units"
    flow._edit_index = 0
    flow._edit_source_entities = []
    defaults = flow_module._entity_form_defaults("Units", record, entry.options)
    flow._entity_defaults = deepcopy(defaults)
    defaults[flow_module.CONF_NATIVE_VALUE_TEMPLATES] = {"native_unit_of_measurement": flow_module._literal_template(unit)}
    result = await flow.async_step_edit_entity(defaults)
    if result.get("step_id") == "edit_entity" and not result.get("errors"):
        defaults = flow_module._flatten_entity_form_sections(suggested_form_values(result["data_schema"]))
        result = await flow.async_step_edit_entity(defaults)
    assert result.get("errors", {}) == {}
    return flow, entry, result


async def test_unit_edit_requires_step_and_keep_preserves_history(hass):
    flow, entry, result = await make_unit_flow(hass)
    assert result["step_id"] == "unit_change"
    assert entry.options[ATTR_DEVICES]["Units"][0]["unit_of_measurement"] == "W"
    result = await flow.async_step_unit_change({"history_policy": "keep"})
    assert result["type"] == "create_entry"
    saved = result["data"][ATTR_DEVICES]["Units"][0]
    assert unit_history.configured_unit(hass, saved) == (True, "kW")
    assert saved["initial_value"] == "1000"


async def test_unit_edit_rejects_unconfirmed_history_and_stale_options(hass):
    flow, entry, _ = await make_unit_flow(hass)
    result = await flow.async_step_unit_change({"history_policy": "relabel"})
    assert result["errors"]["base"] == "unit_history_failed"
    hass.config_entries.async_update_entry(entry, options={ATTR_DEVICES: {"Units": []}})
    result = await flow.async_step_unit_change({"history_policy": "keep"})
    assert result["type"] == "abort"


async def test_edit_flow_recommends_and_applies_formaldehyde_microgram_conversion(hass):
    source = "sensor.physical_hcho"
    target = "sensor.legacy_hcho"
    hass.states.async_set(source, "0.05", {
        "device_class": "formaldehyde", "unit_of_measurement": "mg/m³",
    })
    hass.states.async_set(target, "0.05", {"unit_of_measurement": "m³"})
    record = {
        "platform": "sensor", "entity_id": target, "name": "Legacy HCHO",
        "unit_of_measurement": "m³", CONF_SOURCE_ENTITIES: [source],
        "value_template": "{{ states('sensor.physical_hcho') }}",
    }
    entry = MockConfigEntry(domain=COMPONENT_DOMAIN, data={ATTR_GROUP_NAME: "Air"},
                            options={ATTR_DEVICES: {"Air": [record]}})
    entry.add_to_hass(hass)
    flow = flow_module.VirtualOptionsFlowHandler()
    flow.hass = hass
    flow.handler = entry.entry_id
    flow.context = {"entry_id": entry.entry_id}
    flow._edit_device_name = "Air"
    flow._edit_index = 0
    flow._entity_defaults = flow_module._entity_form_defaults("Air", record, entry.options)
    result = await flow.async_step_edit_entity()
    assert result["step_id"] == "recommended_formaldehyde_unit"
    result = await flow.async_step_recommended_formaldehyde_unit({"apply": True})
    assert result["step_id"] == "edit_entity"
    values = flow._entity_defaults
    assert "* 1000.0" in values["value_template"]
    assert "unit_of_measurement: μg/m³" in values["domain_options_json"]
    assert values["entity_id"] == target
    submitted = flow_module._flatten_entity_form_sections(
        suggested_form_values(result["data_schema"])
    )
    result = await flow.async_step_edit_entity(submitted)
    assert result["step_id"] == "unit_change"
    result = await flow.async_step_unit_change({"history_policy": "keep"})
    assert result["type"] == "create_entry"
    saved = result["data"][ATTR_DEVICES]["Air"][0]
    assert saved["entity_id"] == target
    assert "* 1000.0" in saved["value_template"]


@pytest.mark.parametrize("policy,expected", [("relabel", 1000), ("convert", 1), ("keep", 1000), ("restore", 1000)])
async def test_statistics_policy_uses_real_recorder(recorder_mock, hass, policy, expected):
    hass.states.async_set("sensor.unit_example", "1000", {"unit_of_measurement": "W"})
    from homeassistant.components.recorder.statistics import async_import_statistics, statistics_during_period
    from pytest_homeassistant_custom_component.components.recorder.common import async_recorder_block_till_done
    start = dt_util.utcnow().replace(minute=0, second=0, microsecond=0) - timedelta(hours=2)
    metadata = {"source": "recorder", "statistic_id": "sensor.unit_example", "name": "Example",
                "unit_of_measurement": "W", "unit_class": "power", "mean_type": 1, "has_sum": False}
    async_import_statistics(hass, metadata, [{"start": start, "mean": 1000, "min": 900, "max": 1100}])
    await async_recorder_block_till_done(hass)
    snapshot = await unit_history.statistics_snapshot(hass, "sensor.unit_example")
    assert snapshot is not None
    if policy == "restore":
        from unittest.mock import patch
        from custom_components.virtual_layer.sensor import VirtualSensor
        sensor = VirtualSensor({"name": "Example", "native_templates": {
            "native_unit_of_measurement": "{{ none }}"}}, False)
        sensor.hass = hass
        sensor.entity_id = "sensor.unit_example"
        with patch("custom_components.virtual_layer.entity.VirtualEntity.async_added_to_hass"):
            await sensor.async_added_to_hass()
        assert sensor.native_unit_of_measurement == "W"
    flow, entry, result = await make_unit_flow(hass, None if policy == "restore" else "kW")
    assert result["step_id"] == "unit_change"
    if policy == "restore":
        assert suggested_form_values(result["data_schema"])["history_policy"] == "restore"
    if policy not in ("keep", "restore"):
        result = await flow.async_step_unit_change({"history_policy": policy})
        assert result["errors"]["base"] == "unit_history_failed"
        assert await unit_history.statistics_snapshot(hass, "sensor.unit_example") == snapshot
    result = await flow.async_step_unit_change({"history_policy": policy, "confirm_history": policy != "restore"})
    assert result["type"] == "create_entry"
    if policy == "restore":
        assert unit_history.configured_unit(hass, result["data"][ATTR_DEVICES]["Units"][0]) == (True, "W")
    updated = await unit_history.statistics_snapshot(hass, "sensor.unit_example")
    assert updated["unit_of_measurement"] == ("W" if policy in ("keep", "restore") else "kW")
    # Query the stored statistics unit without HA converting to the live unit.
    hass.states.async_remove("sensor.unit_example")
    result = await recorder_mock.async_add_executor_job(partial(statistics_during_period,
        hass, start, None, {"sensor.unit_example"}, "hour", None, {"mean"}))
    assert result["sensor.unit_example"][0]["mean"] == expected
    if policy not in ("keep", "restore"):
        with pytest.raises(ValueError):
            await unit_history.apply_statistics_policy(hass, "sensor.unit_example", policy, "kW", snapshot)


def test_unresolved_template_is_not_a_history_unit(hass):
    assert unit_history.configured_unit(hass, {CONF_NATIVE_TEMPLATES: {
        "native_unit_of_measurement": "{{ missing_variable }}"}}) == (False, None)


@pytest.mark.parametrize("template", ["{{ none }}", "{{ '' }}", "{{ state_attr('sensor.missing', 'unit_of_measurement') }}"])
def test_missing_dynamic_unit_is_not_a_history_unit(hass, template):
    assert unit_history.configured_unit(hass, {CONF_NATIVE_TEMPLATES: {
        "native_unit_of_measurement": template}}) == (False, None)


@pytest.mark.parametrize("state", [None, "unknown", "unavailable"])
async def test_unknown_entity_excluded_from_unit_changes(hass, state):
    hass.states.async_set("sensor.unit_example", "1000", {"unit_of_measurement": "W"})
    flow, entry, _ = await make_unit_flow(hass)
    flow._unit_metadata = {"source": "recorder", "unit_of_measurement": "W", "unit_class": "power"}
    if state is None:
        hass.states.async_remove("sensor.unit_example")
    else:
        hass.states.async_set("sensor.unit_example", state)
    result = await flow.async_step_unit_change()
    policy_selector = next(value for key, value in result["data_schema"].schema.items()
                           if key.schema == "history_policy")
    assert policy_selector.config["options"] == ["restore", "keep"]
    for policy in ("relabel", "convert"):
        result = await flow.async_step_unit_change({"history_policy": policy, "confirm_history": True})
        assert result["errors"] == {"base": "unit_history_failed"}
    assert entry.options[ATTR_DEVICES]["Units"][0]["unit_of_measurement"] == "W"


async def test_normalized_unit_selection_overrides_template(hass):
    flow, entry, _ = await make_unit_flow(hass)
    record = entry.options[ATTR_DEVICES]["Units"][0]
    defaults = flow_module._entity_form_defaults("Units", record, entry.options)
    defaults["sensor_unit"] = "μg/m³"
    _, entity = await flow_module._async_build_entity_config(hass, defaults, "sensor.unit_example")
    assert unit_history.configured_unit(hass, entity) == (True, "μg/m³")
