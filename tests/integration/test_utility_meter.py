"""Real config-entry setup, services, companion grouping and reload."""
from decimal import Decimal
from copy import deepcopy
from datetime import timedelta

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from homeassistant.helpers import entity_registry as er
from homeassistant.core import State
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from freezegun import freeze_time
import voluptuous as vol

from custom_components.virtual_layer.const import COMPONENT_DOMAIN
from custom_components.virtual_layer.config_flow import _entity_form_defaults, _build_entity_config
from custom_components.virtual_layer.config_flow import InvalidFieldValue


async def setup_meter(hass, **options):
    hass.states.async_set("sensor.physical_energy", "100", {"unit_of_measurement": "kWh", "device_class": "energy"})
    entity = {"platform": "sensor", "name": "Billing", "entity_id": "sensor.billing",
              "initial_value": "0", "persistent": True, "source_entities": ["sensor.physical_energy"],
              "utility_meter_enabled": True, "utility_meter_rate": 100, **options}
    entry = MockConfigEntry(domain=COMPONENT_DOMAIN, data={"group_name": "Billing"}, options={"devices": {"Billing": [entity]}})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.billing") is not None
    return entry


async def test_meter_services_cost_and_reload(hass):
    entry = await setup_meter(hass, utility_meter_periodically_resetting=False)
    hass.states.async_set("sensor.physical_energy", "102.5", {"unit_of_measurement": "kWh", "device_class": "energy"})
    await hass.async_block_till_done()
    assert Decimal(hass.states.get("sensor.billing").state) == Decimal("2.5")
    assert Decimal(hass.states.get("sensor.billing").attributes["cost"]) == 250
    await hass.async_block_till_done()
    assert Decimal(hass.states.get("sensor.billing_cost").state) == 250
    registry = er.async_get(hass)
    assert registry.async_get("sensor.billing").device_id == registry.async_get("sensor.billing_cost").device_id
    for action, data, expected in [
        ("adjust_utility_meter", {"amount": "1.5"}, "4"),
        ("calibrate_utility_meter", {"value": "25"}, "25"),
        ("reset_utility_meter", {}, "0"),
    ]:
        await hass.services.async_call(COMPONENT_DOMAIN, action, {"entity_id": "sensor.billing", **data}, blocking=True)
        await hass.async_block_till_done()
        assert Decimal(hass.states.get("sensor.billing").state) == Decimal(expected)
    assert hass.states.get("sensor.billing").attributes["last_period"] == "25"
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set("sensor.physical_energy", "104", {"unit_of_measurement": "kWh"})
    await hass.async_block_till_done()
    assert Decimal(hass.states.get("sensor.billing").state) == Decimal("1.5")


async def test_ui_correction_applied_once(hass):
    entry = await setup_meter(hass, utility_meter_correction="40", utility_meter_correction_id="revision1")
    assert Decimal(hass.states.get("sensor.billing").state) == 40
    await hass.services.async_call(COMPONENT_DOMAIN, "adjust_utility_meter", {"entity_id": "sensor.billing", "amount": 2}, blocking=True)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert Decimal(hass.states.get("sensor.billing").state) == 42


@pytest.mark.parametrize("cycle", ["none", "quarter-hourly", "hourly", "daily", "weekly", "monthly", "bimonthly", "quarterly", "yearly", "days", "cron"])
async def test_all_schedules_load_and_unload(hass, cycle):
    entry = await setup_meter(hass, utility_meter_cycle=cycle, utility_meter_start="2026-01-15T00:00:00")
    state = hass.states.get("sensor.billing")
    assert state.state == "0"
    assert bool(state.attributes["next_reset"]) == (cycle != "none")
    assert await hass.config_entries.async_unload(entry.entry_id)


def test_form_roundtrip_retains_meter_settings():
    config = {"platform": "sensor", "name": "Billing", "entity_id": "sensor.billing", "initial_value": "0", "persistent": True,
              "source_entities": ["sensor.physical_energy"], "utility_meter_enabled": True,
              "utility_meter_cycle": "days", "utility_meter_start": "2026-01-15T00:00:00", "utility_meter_days": 17,
              "utility_meter_rate": 200, "utility_meter_tiers": [{"up_to": 100, "rate": 150}]}
    defaults = _entity_form_defaults("Billing", config)
    _, saved = _build_entity_config(defaults)
    for key, value in config.items():
        if key.startswith("utility_meter_"):
            assert saved[key] == value


@pytest.mark.parametrize("periodic,expected", [(True, 0), (False, 5)])
@pytest.mark.parametrize("available", [True, False])
async def test_outages(hass, periodic, expected, available):
    await setup_meter(hass, utility_meter_periodically_resetting=periodic, utility_meter_always_available=available)
    hass.states.async_set("sensor.physical_energy", "unavailable")
    await hass.async_block_till_done()
    assert (hass.states.get("sensor.billing").state == "unavailable") is (not available)
    hass.states.async_set("sensor.physical_energy", "105", {"unit_of_measurement": "kWh"})
    await hass.async_block_till_done()
    assert Decimal(hass.states.get("sensor.billing").state) == expected


async def test_tariff_switch_avoids_charging_inactive_usage(hass):
    hass.states.async_set("input_select.tariff", "offpeak")
    await setup_meter(hass, utility_meter_tariff_entity="input_select.tariff", utility_meter_tariff="peak")
    hass.states.async_set("sensor.physical_energy", "105")
    await hass.async_block_till_done()
    assert Decimal(hass.states.get("sensor.billing").state) == 0
    hass.states.async_set("input_select.tariff", "peak")
    await hass.async_block_till_done()
    hass.states.async_set("sensor.physical_energy", "107")
    await hass.async_block_till_done()
    assert Decimal(hass.states.get("sensor.billing").state) == 2


@pytest.mark.parametrize("delta,net,reading,expected", [(True, False, "3", 3), (False, True, "98", -2)])
async def test_delta_and_net_inputs(hass, delta, net, reading, expected):
    await setup_meter(hass, utility_meter_delta_values=delta, utility_meter_net_consumption=net)
    hass.states.async_set("sensor.physical_energy", reading)
    await hass.async_block_till_done()
    assert Decimal(hass.states.get("sensor.billing").state) == expected
    assert hass.states.get("sensor.billing").attributes["state_class"] == ("total" if net else "total_increasing")


async def test_scheduled_reset_and_listener_cleanup(hass):
    with freeze_time("2026-01-15T10:00:00Z") as clock:
        entry = await setup_meter(hass, utility_meter_cycle="days", utility_meter_days=2, utility_meter_start="2026-01-15T11:00:00+00:00")
        await hass.services.async_call(COMPONENT_DOMAIN, "calibrate_utility_meter", {"entity_id": "sensor.billing", "value": 20}, blocking=True)
        clock.move_to("2026-01-15T11:00:01Z")
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()
        assert Decimal(hass.states.get("sensor.billing").state) == 0
        assert hass.states.get("sensor.billing").attributes["last_period"] == "20"
        assert dt_util.parse_datetime(hass.states.get("sensor.billing").attributes["next_reset"]) > dt_util.utcnow()
        assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize("action,field,value", [
    ("adjust_utility_meter", "amount", "NaN"),
    ("adjust_utility_meter", "amount", -1),
    ("calibrate_utility_meter", "value", "Infinity"),
    ("calibrate_utility_meter", "value", -1),
])
async def test_invalid_service_values_do_not_change_meter(hass, action, field, value):
    await setup_meter(hass)
    with pytest.raises((vol.Invalid, ValueError)):
        await hass.services.async_call(COMPONENT_DOMAIN, action, {"entity_id": "sensor.billing", field: value}, blocking=True)
    assert Decimal(hass.states.get("sensor.billing").state) == 0


async def test_missed_reset_during_downtime(hass):
    with freeze_time("2026-01-15T10:00:00Z") as clock:
        entry = await setup_meter(hass, utility_meter_cycle="hourly")
        await hass.services.async_call(COMPONENT_DOMAIN, "calibrate_utility_meter", {"entity_id": "sensor.billing", "value": 20}, blocking=True)
        await hass.async_block_till_done()
        assert await hass.config_entries.async_unload(entry.entry_id)
        clock.move_to("2026-01-15T12:00:01Z")
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert Decimal(hass.states.get("sensor.billing").state) == 0
        assert hass.states.get("sensor.billing").attributes["last_period"] == "20"


async def test_reject_companion_as_service_target_atomically(hass):
    await setup_meter(hass)
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(COMPONENT_DOMAIN, "adjust_utility_meter", {"entity_id": ["sensor.billing", "sensor.billing_cost"], "amount": 2}, blocking=True)
    assert Decimal(hass.states.get("sensor.billing").state) == 0


async def test_timezone_update_reschedules_and_future_start_does_not_collect(hass):
    with freeze_time("2026-01-01T00:00:00Z"):
        await setup_meter(hass, utility_meter_start="2027-01-15T00:00:00")
        hass.states.async_set("sensor.physical_energy", "105")
        hass.bus.async_fire("core_config_updated", {})
        await hass.async_block_till_done()
        assert Decimal(hass.states.get("sensor.billing").state) == 0


async def test_native_set_calibrates_net_meter(hass):
    await setup_meter(hass, utility_meter_net_consumption=True)
    await hass.services.async_call(COMPONENT_DOMAIN, "set", {"entity_id": "sensor.billing", "value": "-2"}, blocking=True)
    await hass.async_block_till_done()
    assert Decimal(hass.states.get("sensor.billing").state) == -2


async def test_source_missing_on_reload_and_replaced_baseline(hass):
    entry = await setup_meter(hass, utility_meter_periodically_resetting=False)
    hass.states.async_remove("sensor.physical_energy")
    await hass.async_block_till_done()
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set("sensor.physical_energy", "105")
    await hass.async_block_till_done()
    assert Decimal(hass.states.get("sensor.billing").state) == 5


async def test_invalid_direct_configuration_has_no_subscription(hass):
    from custom_components.virtual_layer.sensor import VirtualSensor, validate_domain_options
    entity = VirtualSensor({"name": "Broken", "utility_meter_enabled": True, "initial_value": "0"}, False)
    entity.hass = hass
    entity._create_state(entity._config)
    entity._schedule_state_update = lambda: None
    entity._initialize_utility_meter_source()
    assert not entity.available
    validate_domain_options({})
    for bad in [{"source_entities": []}, {"source_entities": ["sensor.one"], "initial_value": -1}, {"source_entities": ["sensor.one"], "utility_meter_tariff_entity": "select.tariff"}]:
        with pytest.raises(vol.Invalid):
            validate_domain_options({"utility_meter_enabled": True, **bad})


def test_ui_correction_revisions_and_edit_roundtrip():
    config = {"platform": "sensor", "name": "Billing", "entity_id": "sensor.billing",
              "initial_value": "0", "persistent": True, "source_entities": ["sensor.physical_energy"],
              "utility_meter_enabled": True}
    values = _entity_form_defaults("Billing", config)
    values["utility_meter_current_value"] = 40
    _, saved = _build_entity_config(values)
    assert saved["utility_meter_correction"] == "40"
    defaults = _entity_form_defaults("Billing", saved)
    _, unchanged = _build_entity_config(defaults)
    assert unchanged["utility_meter_correction_id"] == saved["utility_meter_correction_id"]
    defaults["utility_meter_current_value"] = 42
    _, corrected = _build_entity_config(defaults)
    assert corrected["utility_meter_correction"] == "42"
    assert corrected["utility_meter_correction_id"] != saved["utility_meter_correction_id"]


@pytest.mark.parametrize("extra", [
    {"source_entities": None}, {"source_entities": [3]},
    {"source_entities": ["sensor.bad id"]},
    {"utility_meter_correction": "NaN"}, {"utility_meter_correction": -1},
])
def test_corrupt_meter_configuration_is_rejected(extra):
    from custom_components.virtual_layer.sensor import validate_domain_options
    with pytest.raises(vol.Invalid):
        validate_domain_options({"utility_meter_enabled": True,
                                 "source_entities": ["sensor.energy"],
                                 "utility_meter_correction_id": "revision", **extra})


async def edit_meter(hass, entry, **changes):
    """Persist UI-shaped options while retaining stable entity identity."""
    assert await hass.config_entries.async_unload(entry.entry_id)
    options = deepcopy(dict(entry.options))
    options["devices"]["Billing"][0].update(changes)
    hass.config_entries.async_update_entry(entry, options=options)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.parametrize("changes", [
    {"utility_meter_cycle": "hourly"},
    {"utility_meter_cycle": "days", "utility_meter_days": 2, "utility_meter_start": "2026-01-01T00:00:00Z"},
    {"utility_meter_start": "2026-01-10T00:00:00Z"},
    {"utility_meter_cycle": "cron", "utility_meter_cron": "0 0 * * *"},
    {"utility_meter_offset": 60},
    {"utility_meter_cycle": "none"},
])
async def test_edit_schedule_preserves_total_and_replaces_timer(hass, changes):
    with freeze_time("2026-01-01T10:00:00Z") as clock:
        entry = await setup_meter(hass)
        await hass.services.async_call(COMPONENT_DOMAIN, "calibrate_utility_meter", {"entity_id": "sensor.billing", "value": 40}, blocking=True)
        await hass.async_block_till_done()
        last_reset = hass.states.get("sensor.billing").attributes["last_reset"]
        clock.move_to("2026-01-15T10:30:00Z")
        await edit_meter(hass, entry, **changes)
        state = hass.states.get("sensor.billing")
        assert Decimal(state.state) == 40
        assert state.attributes["last_reset"] == last_reset
        next_reset = state.attributes["next_reset"]
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert Decimal(hass.states.get("sensor.billing").state) == 40
        assert hass.states.get("sensor.billing").attributes["next_reset"] == next_reset
        if next_reset:
            clock.move_to(dt_util.parse_datetime(next_reset) + timedelta(seconds=1))
            async_fire_time_changed(hass, dt_util.utcnow())
            await hass.async_block_till_done()
            assert Decimal(hass.states.get("sensor.billing").state) == 0
            assert hass.states.get("sensor.billing").attributes["last_period"] == "40"


async def test_future_start_reload_does_not_backfill_uncollected_usage(hass):
    with freeze_time("2026-01-01T00:00:00Z"):
        entry = await setup_meter(hass, utility_meter_start="2027-01-15T00:00:00Z")
        assert await hass.config_entries.async_unload(entry.entry_id)
        hass.states.async_set("sensor.physical_energy", "120")
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert Decimal(hass.states.get("sensor.billing").state) == 0


async def test_edit_rate_and_correction_then_continue_usage(hass):
    entry = await setup_meter(hass)
    await edit_meter(hass, entry, utility_meter_rate=200,
                     utility_meter_correction="42", utility_meter_correction_id="edited")
    hass.states.async_set("sensor.physical_energy", "102")
    await hass.async_block_till_done()
    await hass.async_block_till_done()
    assert Decimal(hass.states.get("sensor.billing").state) == 44
    assert Decimal(hass.states.get("sensor.billing_cost").state) == 8800
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert Decimal(hass.states.get("sensor.billing").state) == 44


async def test_calibration_during_outage_does_not_count_corrected_gap_twice(hass):
    entry = await setup_meter(hass, utility_meter_periodically_resetting=False)
    hass.states.async_set("sensor.physical_energy", "unavailable")
    await hass.async_block_till_done()
    await hass.services.async_call(COMPONENT_DOMAIN, "calibrate_utility_meter", {"entity_id": "sensor.billing", "value": 42}, blocking=True)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set("sensor.physical_energy", "120")
    await hass.async_block_till_done()
    assert Decimal(hass.states.get("sensor.billing").state) == 42
    hass.states.async_set("sensor.physical_energy", "122")
    await hass.async_block_till_done()
    assert Decimal(hass.states.get("sensor.billing").state) == 44


@pytest.mark.parametrize("cycle,old,new", [
    ("days", {"utility_meter_days": 20}, {"utility_meter_days": 3}),
    ("cron", {"utility_meter_cron": "0 0 1 * *"}, {"utility_meter_cron": "0 0 * * *"}),
])
async def test_edit_interval_within_same_cycle(hass, cycle, old, new):
    with freeze_time("2026-01-01T10:00:00Z") as clock:
        entry = await setup_meter(hass, utility_meter_cycle=cycle,
                                 utility_meter_start="2026-01-01T00:00:00Z", **old)
        await hass.services.async_call(COMPONENT_DOMAIN, "calibrate_utility_meter", {"entity_id": "sensor.billing", "value": 40}, blocking=True)
        await hass.async_block_till_done()
        clock.move_to("2026-01-15T10:30:00Z")
        await edit_meter(hass, entry, **new)
        assert Decimal(hass.states.get("sensor.billing").state) == 40
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert Decimal(hass.states.get("sensor.billing").state) == 40


@pytest.mark.parametrize("field,value", [
    ("utility_meter_cycle", "invalid"), ("utility_meter_days", 0),
    ("utility_meter_start", "invalid"), ("utility_meter_currency", "wrong"),
    ("utility_meter_rate", "nan"), ("utility_meter_tiers", [{}]),
    ("utility_meter_tariff_entity", "sensor.energy"),
    ("utility_meter_current_value", "nan"), ("utility_meter_current_value", -1),
])
def test_invalid_meter_settings_identify_the_visible_field(field, value):
    defaults = _entity_form_defaults("Billing", {
        "platform": "sensor", "name": "Billing", "entity_id": "sensor.billing",
        "initial_value": "0", "source_entities": ["sensor.physical_energy"],
        "utility_meter_enabled": True,
    })
    defaults[field] = value
    with pytest.raises(InvalidFieldValue) as error:
        _build_entity_config(defaults)
    assert error.value.field_name == field


def test_disable_broken_meter_preserves_settings_and_removes_old_correction():
    defaults = _entity_form_defaults("Billing", {
        "platform": "sensor", "name": "Billing", "entity_id": "sensor.billing",
        "initial_value": "0", "source_entities": ["sensor.physical_energy"],
        "utility_meter_enabled": True, "utility_meter_cycle": "days",
        "utility_meter_start": "", "utility_meter_days": 17,
        "utility_meter_correction": "42", "utility_meter_correction_id": "old",
    })
    defaults["utility_meter_enabled"] = False
    _, saved = _build_entity_config(defaults)
    assert saved["utility_meter_enabled"] is False
    assert saved["utility_meter_days"] == 17
    assert "utility_meter_correction_id" not in saved
    defaults = _entity_form_defaults("Billing", saved)
    defaults["utility_meter_enabled"] = True
    defaults["utility_meter_start"] = "2026-01-01T00:00:00"
    _, saved = _build_entity_config(defaults)
    assert saved["utility_meter_enabled"] is True
    assert saved["utility_meter_days"] == 17


async def test_outage_baseline_is_persisted_before_recovery(hass):
    entry = await setup_meter(hass, utility_meter_periodically_resetting=True)
    hass.states.async_set("sensor.physical_energy", "unavailable")
    await hass.async_block_till_done()
    assert hass.states.get("sensor.billing").attributes["last_valid_state"] is None
    assert await hass.config_entries.async_unload(entry.entry_id)
    hass.states.async_set("sensor.physical_energy", "120")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert Decimal(hass.states.get("sensor.billing").state) == 0


async def test_real_edit_flow_keeps_inputs_and_reports_meter_field(hass):
    import json
    from custom_components.virtual_layer import config_flow as flow
    from tests.flow_helpers import suggested_form_values
    entry = MockConfigEntry(domain=COMPONENT_DOMAIN, data={"group_name": "ui"},
                            options={"devices": {"Room": [{"platform": "sensor", "name": "Selected", "entity_key": "selected"}]}})
    entry.add_to_hass(hass)
    manager = hass.config_entries.options
    result = await manager.async_init(entry.entry_id, data={flow.CONF_ACTION: flow.ACTION_EDIT_ENTITY})
    result = await manager.async_configure(result["flow_id"], {flow.CONF_ENTITY_KEY: json.dumps(["key", "selected"], separators=(",", ":"))})
    result = await manager.async_configure(result["flow_id"], {flow.CONF_REFERENCE_ENTITY_ID: []})
    defaults = flow._flatten_entity_form_sections(suggested_form_values(result["data_schema"]))
    submitted = {**defaults, flow.CONF_SOURCE_ENTITIES_TEXT: "sensor.physical_energy",
                 "utility_meter_enabled": True, "utility_meter_cycle": "cron",
                 "utility_meter_cron": "bad cron", "utility_meter_rate": 123}
    result = await manager.async_configure(result["flow_id"], submitted)
    assert result["type"] == "form"
    assert result["step_id"] == "edit_utility_meter"
    assert entry.options["devices"]["Room"][0].get("utility_meter_enabled") is None
    result = await manager.async_configure(result["flow_id"], suggested_form_values(result["data_schema"]))
    assert result["errors"]["utility_meter_cron"] == "invalid_domain_options"
    reopened = flow._flatten_entity_form_sections(suggested_form_values(result["data_schema"]))
    assert reopened["utility_meter_rate"] == 123
    assert reopened["utility_meter_cron"] == "bad cron"
    result = await manager.async_configure(result["flow_id"], {**reopened, "utility_meter_cron": "0 0 * * *"})
    assert not result.get("errors")
    if result.get("step_id") == "edit_entity_helper":
        result = await manager.async_configure(result["flow_id"], {flow.CONF_HELPER_UPDATE_MODE: flow.HELPER_UPDATE_KEEP})
        values = flow._flatten_entity_form_sections(suggested_form_values(result["data_schema"]))
        result = await manager.async_configure(result["flow_id"], values)
    assert result["type"] == "create_entry"
    saved = entry.options["devices"]["Room"][0]
    assert saved["initial_value"] == "0"
    assert saved["utility_meter_cron"] == "0 0 * * *"


async def test_initial_setup_uses_meter_step_before_creating_entry(hass):
    from custom_components.virtual_layer import config_flow as cf
    from tests.flow_helpers import suggested_form_values
    flow = cf.VirtualFlowHandler()
    flow.hass = hass
    flow._add_use_template_helper = False
    hass.states.async_set("sensor.physical_energy", "100", {"device_class": "energy", "unit_of_measurement": "kWh"})
    flow._pending_title = "Billing"
    flow._pending_data = {"group_name": "Billing"}
    values = cf._entity_form_defaults("Billing", {
        "platform": "sensor", "name": "Billing", "entity_id": "sensor.billing",
        "initial_value": "0", "source_entities": ["sensor.physical_energy"],
        "utility_meter_enabled": True, "utility_meter_rate": 123,
    })
    values = cf._complete_domain_form_defaults(values)
    flow._entity_defaults = values
    result = await flow.async_step_entity(values)
    assert result["step_id"] == "utility_meter"
    assert not hass.config_entries.async_entries(COMPONENT_DOMAIN)
    result = await flow.async_step_utility_meter(result["data_schema"]({}))
    if result.get("step_id") == "entity":
        result = await flow.async_step_entity(suggested_form_values(result["data_schema"]))
    assert result["type"] == "create_entry", (result.get("step_id"), result.get("errors"))
    assert result["options"]["devices"]["Billing"][0]["utility_meter_rate"] == 123


async def test_options_add_routes_through_meter_settings(hass):
    from custom_components.virtual_layer import config_flow as cf
    from tests.flow_helpers import suggested_form_values
    hass.states.async_set("sensor.physical_energy", "100", {"device_class": "energy", "unit_of_measurement": "kWh"})
    entry = MockConfigEntry(domain=COMPONENT_DOMAIN, data={"group_name": "Billing"}, options={"devices": {"Billing": []}})
    entry.add_to_hass(hass)
    manager = hass.config_entries.options
    result = await manager.async_init(entry.entry_id, data={cf.CONF_ACTION: cf.ACTION_ADD_ENTITY})
    result = await manager.async_configure(result["flow_id"], {cf.CONF_REFERENCE_ENTITY_ID: ["sensor.physical_energy"], cf.CONF_TARGET_DEVICE_NAME: "Billing"})
    if result.get("step_id") == "entity_type":
        result = await manager.async_configure(result["flow_id"], {cf.CONF_TARGET_ENTITY_TYPE: "sensor"})
    assert result["step_id"] == "entity_helper"
    result = await manager.async_configure(result["flow_id"], {cf.CONF_USE_TEMPLATE_HELPER: False})
    values = cf._flatten_entity_form_sections(suggested_form_values(result["data_schema"]))
    values["utility_meter_enabled"] = True
    result = await manager.async_configure(result["flow_id"], values)
    assert result["step_id"] == "utility_meter"
    assert entry.options["devices"]["Billing"] == []
    result = await manager.async_configure(result["flow_id"], {**result["data_schema"]({}), "utility_meter_current_value": "42"})
    assert result["type"] == "create_entry", (result.get("step_id"), result.get("errors"))
    saved = entry.options["devices"]["Billing"][0]
    assert saved["utility_meter_correction"] == "42"


async def test_previous_month_same_time_companion_reads_recorder(recorder_mock, hass, monkeypatch):
    from custom_components.virtual_layer import sensor as sensor_module

    calls = []
    def history_at_same_time(_hass, start, end, entity_ids, **_kwargs):
        calls.append((start, end, entity_ids))
        return {"sensor.billing": [State("sensor.billing", "17.5", {"unit_of_measurement": "kWh"}, last_updated=start)]}

    monkeypatch.setattr(sensor_module.recorder_history, "get_significant_states", history_at_same_time)
    with freeze_time("2026-03-31T12:00:00Z") as clock:
        entry = await setup_meter(hass, utility_meter_compare_previous_month=True)
        await hass.async_block_till_done()
        state = hass.states.get("sensor.billing")
        assert state.attributes["last_month_same_time_usage"] == "17.5"
        assert dt_util.parse_datetime(state.attributes["last_month_same_time"]).date().isoformat() == "2026-02-28"
        comparison = hass.states.get("sensor.billing_last_month_same_time")
        await hass.async_block_till_done()
        assert Decimal(comparison.state) == Decimal("17.5")
        assert calls and calls[0][2] == ["sensor.billing"]
        registry = er.async_get(hass)
        assert registry.async_get("sensor.billing").device_id == registry.async_get(comparison.entity_id).device_id
        clock.tick(timedelta(minutes=1))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()
        assert len(calls) >= 2
        assert calls[-1][0] == calls[0][0] + timedelta(minutes=1)
        assert await hass.config_entries.async_reload(entry.entry_id)


async def test_disable_comparison_removes_companion(hass):
    entry = await setup_meter(hass, utility_meter_compare_previous_month=True)
    comparison_id = "sensor.billing_last_month_same_time"
    options = {**entry.options, "devices": {"Billing": [dict(entry.options["devices"]["Billing"][0])]}}
    options["devices"]["Billing"][0]["utility_meter_compare_previous_month"] = False
    hass.config_entries.async_update_entry(entry, options=options)
    await hass.async_block_till_done()
    assert hass.states.get(comparison_id) is None
    assert er.async_get(hass).async_get(comparison_id) is None
    assert hass.states.get("sensor.billing") is not None


@pytest.fixture
def recorder_db_url(tmp_path):
    return f"sqlite:///{tmp_path / 'recorder.db'}"


@pytest.mark.parametrize("historical_state,unit,expected", [
    ("17.5", "kWh", "17.5"), ("unavailable", "kWh", None),
    ("unknown", "kWh", None), ("17000", "Wh", None),
])
async def test_comparison_real_recorder_point_in_time(recorder_mock, hass, monkeypatch,
                                                       historical_state, unit, expected):
    from custom_components.virtual_layer import sensor as sensor_module
    from pytest_homeassistant_custom_component.components.recorder.common import async_recorder_block_till_done

    hass.states.async_set("sensor.billing", historical_state, {"unit_of_measurement": unit})
    await hass.async_block_till_done()
    await async_recorder_block_till_done(hass)
    target = dt_util.utcnow()
    hass.states.async_set("sensor.billing", "999", {"unit_of_measurement": "kWh"})
    await hass.async_block_till_done()
    await async_recorder_block_till_done(hass)
    monkeypatch.setattr(sensor_module.meter, "previous_month_same_time", lambda now: target)
    hass.states.async_remove("sensor.billing")
    entry = await setup_meter(hass, utility_meter_compare_previous_month=True)
    assert hass.states.get("sensor.billing").attributes["last_month_same_time_usage"] == expected
    assert hass.states.get("sensor.billing_last_month_same_time").state == (expected or "unknown")
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_previous_month_companion_is_unavailable_without_history(hass, monkeypatch):
    from custom_components.virtual_layer import sensor as sensor_module
    monkeypatch.setattr(sensor_module.recorder_history, "get_significant_states", lambda *_args: {})
    await setup_meter(hass, utility_meter_compare_previous_month=True)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.billing").attributes["last_month_same_time_usage"] is None
    assert hass.states.get("sensor.billing_last_month_same_time").state == "unknown"


async def test_comparison_cancels_inflight_lookup_on_unload(recorder_mock, hass, monkeypatch):
    import asyncio
    from custom_components.virtual_layer import sensor as sensor_module

    entry = await setup_meter(hass, utility_meter_compare_previous_month=True)
    entity = sensor_module.get_entity_from_domain(hass, "sensor", "sensor.billing")
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def blocked_lookup(*args):
        entered.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    monkeypatch.setattr(recorder_mock, "async_add_executor_job", blocked_lookup)
    entity._start_previous_month_refresh()
    task = entity._meter_compare_task
    await entered.wait()
    entity._start_previous_month_refresh()
    assert entity._meter_compare_task is task
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert cancelled.is_set()
    assert entity._meter_compare_cancel is None
    assert entity._meter_compare_task is None


@pytest.mark.parametrize("mode", ["future", "failure"])
async def test_comparison_does_not_fabricate_missing_history(recorder_mock, hass, monkeypatch, mode):
    from custom_components.virtual_layer import sensor as sensor_module

    def read_history(_hass, target, *args, **kwargs):
        if mode == "failure":
            raise RuntimeError("Recorder temporarily unavailable")
        return {"sensor.billing": [State("sensor.billing", "123", {"unit_of_measurement": "kWh"},
                                         last_updated=target + timedelta(microseconds=1))]}

    monkeypatch.setattr(sensor_module.recorder_history, "get_significant_states", read_history)
    await setup_meter(hass, utility_meter_compare_previous_month=True)
    assert hass.states.get("sensor.billing_last_month_same_time").state == "unknown"
