"""Unit edits require an explicit, scoped statistics handling decision."""
from copy import deepcopy
from datetime import timedelta
from functools import partial

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from homeassistant.util import dt as dt_util

from custom_components.virtual_layer import config_flow as flow_module, unit_history
from custom_components.virtual_layer.const import (
    COMPONENT_DOMAIN, ATTR_DEVICES, ATTR_GROUP_NAME, CONF_NATIVE_TEMPLATES,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def recorder_db_url(tmp_path):
    """Use an isolated SQLite database, independent of global DB test options."""
    return f"sqlite:///{tmp_path / 'recorder.db'}"


async def make_unit_flow(hass):
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
    defaults[flow_module.CONF_NATIVE_VALUE_TEMPLATES] = {"native_unit_of_measurement": "{{ 'kW' }}"}
    result = await flow.async_step_edit_entity(defaults)
    if result.get("step_id") == "edit_entity" and not result.get("errors"):
        defaults = flow_module._flatten_entity_form_sections(result["data_schema"]({}))
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


@pytest.mark.parametrize("policy,expected", [("relabel", 1000), ("convert", 1), ("keep", 1000)])
async def test_statistics_policy_uses_real_recorder(recorder_mock, hass, policy, expected):
    from homeassistant.components.recorder.statistics import async_import_statistics, statistics_during_period
    from pytest_homeassistant_custom_component.components.recorder.common import async_recorder_block_till_done
    start = dt_util.utcnow().replace(minute=0, second=0, microsecond=0) - timedelta(hours=2)
    metadata = {"source": "recorder", "statistic_id": "sensor.unit_example", "name": "Example",
                "unit_of_measurement": "W", "unit_class": "power", "mean_type": 1, "has_sum": False}
    async_import_statistics(hass, metadata, [{"start": start, "mean": 1000, "min": 900, "max": 1100}])
    await async_recorder_block_till_done(hass)
    snapshot = await unit_history.statistics_snapshot(hass, "sensor.unit_example")
    assert snapshot is not None
    flow, entry, result = await make_unit_flow(hass)
    assert result["step_id"] == "unit_change"
    if policy != "keep":
        result = await flow.async_step_unit_change({"history_policy": policy})
        assert result["errors"]["base"] == "unit_history_failed"
        assert await unit_history.statistics_snapshot(hass, "sensor.unit_example") == snapshot
    result = await flow.async_step_unit_change({"history_policy": policy, "confirm_history": True})
    assert result["type"] == "create_entry"
    updated = await unit_history.statistics_snapshot(hass, "sensor.unit_example")
    assert updated["unit_of_measurement"] == ("W" if policy == "keep" else "kW")
    result = await recorder_mock.async_add_executor_job(partial(statistics_during_period,
        hass, start, None, {"sensor.unit_example"}, "hour", None, {"mean"}))
    assert result["sensor.unit_example"][0]["mean"] == expected
    if policy != "keep":
        with pytest.raises(ValueError):
            await unit_history.apply_statistics_policy(hass, "sensor.unit_example", policy, "kW", snapshot)


def test_unresolved_template_is_not_a_history_unit(hass):
    assert unit_history.configured_unit(hass, {CONF_NATIVE_TEMPLATES: {
        "native_unit_of_measurement": "{{ missing_variable }}"}}) == (False, None)
