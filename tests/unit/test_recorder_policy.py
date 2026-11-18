"""Record functional history without duplicated diagnostic payloads or ticks."""

import json
from unittest.mock import patch

import pytest
from homeassistant.components.camera import Camera
from homeassistant.components.light import LightEntity
from homeassistant.components.recorder.db_schema import StateAttributes
from homeassistant.const import EVENT_STATE_CHANGED, MATCH_ALL
from homeassistant.core import Context, Event
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.virtual_layer.recorder_policy import (
    DEBUG_RECORDER_EXCLUDED_ATTRIBUTES,
    RECORDER_EXCLUDED_ATTRIBUTES,
    async_publish_state,
)
from custom_components.virtual_layer.debug import DEBUG_SUFFIX
from custom_components.virtual_layer.sensor import VirtualDiagnosticSensor


def recorded_attributes(state):
    event = Event(EVENT_STATE_CHANGED, {"entity_id": state.entity_id, "new_state": state})
    return json.loads(StateAttributes.shared_attrs_bytes_from_event(event, None))


async def setup_entry(hass, domain="sensor", **extra):
    entity_id = f"{domain}.recording_policy"
    record = {
        "platform": domain, "name": "Recording policy", "entity_id": entity_id,
        "entity_key": "recording-policy", "initial_value": "12",
        "attributes": {"calibration": [1, 2], "source_attributes": "user data"},
        **extra,
    }
    entry = MockConfigEntry(
        domain="virtual_layer", data={"group_name": "Recording policy"},
        options={"devices": {"Device": [record]}},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, entity_id


@pytest.mark.parametrize("domain", ["sensor", "light", "camera"])
async def test_native_recorder_serialization_preserves_functional_and_platform_attrs(hass, domain):
    _, entity_id = await setup_entry(hass, domain)
    entity = hass.data[domain].get_entity(entity_id)
    entity._attr_extra_state_attributes.update({
        "source_configuration": {"template": "synthetic" * 1000},
        "source_diagnostics": {"sensor.source": {"source_attributes": "x" * 2000}},
        "diagnostic_attribute_retention": {"trace": "synthetic timestamp"},
        "debug_result": "user functional attribute",
    })
    entity.async_write_ha_state()
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    excluded = state.state_info["unrecorded_attributes"]
    assert RECORDER_EXCLUDED_ATTRIBUTES <= excluded
    assert MATCH_ALL not in excluded
    native = {"light": LightEntity, "camera": Camera}.get(domain)
    if native:
        assert native._entity_component_unrecorded_attributes <= excluded
    stored = recorded_attributes(state)
    assert "source_configuration" not in stored
    assert "source_diagnostics" not in stored
    assert "diagnostic_attribute_retention" not in stored
    assert stored["calibration"] == [1, 2]
    assert stored["source_attributes"] == "user data"
    assert stored["debug_result"] == "user functional attribute"


async def test_state_only_publications_keep_exclusions_on_services_and_updates(hass):
    _, entity_id = await setup_entry(hass, "tag")
    assert RECORDER_EXCLUDED_ATTRIBUTES <= hass.states.get(entity_id).state_info["unrecorded_attributes"]
    for service, data in (
        ("set_attributes", {"attributes": {"runtime_value": 7}}),
        ("set_state", {"value": "changed"}),
        ("clear_attributes", {"attributes": ["runtime_value"]}),
    ):
        await hass.services.async_call(
            "virtual_layer", service, {"entity_id": entity_id, **data}, blocking=True
        )
        await hass.async_block_till_done()
        assert RECORDER_EXCLUDED_ATTRIBUTES <= hass.states.get(entity_id).state_info["unrecorded_attributes"]
    context = Context()
    async_publish_state(hass, entity_id, "final", {
        "source_configuration": {"body": "x" * 3000}, "functional_value": 42,
        "unit_of_measurement": "kWh", "state_class": "total_increasing",
    }, context=context, state_info={"unrecorded_attributes": frozenset({"other_diag"})})
    state = hass.states.get(entity_id)
    assert state.context is context
    assert "other_diag" in state.state_info["unrecorded_attributes"]
    assert recorded_attributes(state) == {
        "functional_value": 42, "unit_of_measurement": "kWh",
        "state_class": "total_increasing",
    }


async def test_diagnostic_refresh_ticks_do_not_emit_rows_but_real_changes_do(hass):
    callbacks = {}

    def diagnostic_setup(_hass, config, callback):
        callbacks[config.get("entity_id")] = callback
        return lambda: None

    with patch("custom_components.virtual_layer.source_diagnostics.async_setup", diagnostic_setup):
        _, entity_id = await setup_entry(hass)
    entity = hass.data["sensor"].get_entity(entity_id)
    events = []
    remove = hass.bus.async_listen(EVENT_STATE_CHANGED, lambda event: (
        events.append(event) if event.data["entity_id"] == entity_id else None
    ))
    try:
        base = dict(entity._attr_extra_state_attributes)
        for tick in range(20):
            changed = {**base, "diagnostic_attribute_retention": {"trace": tick}}
            with patch.object(entity, "_update_attributes", side_effect=lambda: setattr(
                entity, "_attr_extra_state_attributes", changed
            )):
                callbacks[entity_id]()
            await hass.async_block_till_done()
        assert not events
        for attribute, value in (
            ("calibration", [1, 3]),
            ("source_diagnostics", {"sensor.source": {"state": "unavailable"}}),
            ("source_last_updated", "user functional timestamp"),
        ):
            changed = {**entity._attr_extra_state_attributes, attribute: value}
            with patch.object(entity, "_update_attributes", side_effect=lambda: setattr(
                entity, "_attr_extra_state_attributes", changed
            )):
                callbacks[entity_id]()
            await hass.async_block_till_done()
        assert len(events) == 3
        assert recorded_attributes(hass.states.get(entity_id))["calibration"] == [1, 3]
        assert recorded_attributes(hass.states.get(entity_id))["source_last_updated"] == "user functional timestamp"
    finally:
        remove()


def test_generated_debug_payload_is_excluded_without_hiding_user_generic_attrs():
    assert {"debug_result", "debug_expires_at"} <= DEBUG_RECORDER_EXCLUDED_ATTRIBUTES
    assert "debug_result" not in RECORDER_EXCLUDED_ATTRIBUTES
    assert "source_attributes" not in RECORDER_EXCLUDED_ATTRIBUTES


async def test_generated_source_sensor_does_not_record_source_snapshot(hass):
    hass.states.async_set("sensor.physical", "25", {"packet": "x" * 5000})
    _, entity_id = await setup_entry(
        hass, diagnostic_source_entity="sensor.physical",
        source_entities=["sensor.physical"], value_template="{{ states('sensor.physical') }}",
    )
    assert isinstance(hass.data["sensor"].get_entity(entity_id), VirtualDiagnosticSensor)
    state = hass.states.get(entity_id)
    assert state.attributes["source_attributes"]["packet"]
    stored = recorded_attributes(state)
    for key in ("source_attributes", "source_state", "source_last_updated", "source_last_changed"):
        assert key not in stored
    assert stored["calibration"] == [1, 2]


async def test_on_demand_debug_report_remains_live_but_is_not_recorded(hass):
    _, entity_id = await setup_entry(hass)
    registry = er.async_get(hass)
    target = registry.async_get(entity_id)
    button_id = registry.async_get_entity_id("button", "virtual_layer", target.unique_id + DEBUG_SUFFIX)
    await hass.services.async_call("button", "press", {"entity_id": button_id}, blocking=True)
    await hass.async_block_till_done()
    state = hass.states.get(button_id)
    assert state.attributes["debug_result"]["virtual_entity_id"] == entity_id
    assert "debug_expires_at" in state.attributes
    stored = recorded_attributes(state)
    assert "debug_result" not in stored
    assert "debug_expires_at" not in stored
