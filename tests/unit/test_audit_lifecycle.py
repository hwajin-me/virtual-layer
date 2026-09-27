"""Regressions for cross-domain lifecycle and native capability handling."""

import asyncio
from datetime import timedelta
from unittest.mock import Mock

import pytest
from homeassistant.components.alarm_control_panel import AlarmControlPanelEntityFeature
from homeassistant.util import dt as dt_util
from homeassistant.core import State
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.virtual_layer.alarm_control_panel import (
    ENTITY_SCHEMA,
    VirtualAlarmControlPanel,
)
from custom_components.virtual_layer.switch import SWITCH_SCHEMA, VirtualSwitch
from custom_components.virtual_layer.generic import ENTITY_SCHEMA as GENERIC_SCHEMA, GenericVirtualEntity
from custom_components.virtual_layer.media_player import ENTITY_CLASS as VirtualMediaPlayer, ENTITY_SCHEMA as MEDIA_SCHEMA


@pytest.mark.parametrize("rendered_data", [False, True])
@pytest.mark.parametrize("command_count", [1, 2])
async def test_unload_stops_all_command_scripts_without_optimistic_writes(hass, rendered_data, command_count):
    started = asyncio.Event()
    calls = []

    async def capture(call):
        calls.append(call.data)
        if len(calls) == command_count:
            started.set()

    hass.services.async_register("audit", "capture", capture)
    entity = VirtualSwitch(SWITCH_SCHEMA({
        "name": "Lifecycle", "entity_id": "switch.lifecycle", "initial_value": "off",
        "command_actions": {"turn_on": [
            {"action": "audit.capture", "data": "{{ command_data }}" if rendered_data else {}},
            {"delay": 30},
            {"action": "audit.capture"},
        ]},
    }), False)
    entity.hass = hass
    entity._create_state(entity._config)
    entity.async_write_ha_state = Mock()
    entity._schedule_state_update = Mock()
    tasks = [asyncio.create_task(entity.async_turn_on()) for _ in range(command_count)]
    try:
        await asyncio.wait_for(started.wait(), 1)
        await entity.async_will_remove_from_hass()
        await asyncio.wait_for(asyncio.gather(*tasks), 1)
        assert not entity.is_on
        entity.async_write_ha_state.assert_not_called()
        # A stale entity reference must not dispatch a fresh action after unload.
        await entity.async_turn_on()
        assert len(calls) == command_count
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_alarm_native_features_update_and_clear(hass):
    entity = VirtualAlarmControlPanel(ENTITY_SCHEMA({
        "name": "Alarm", "entity_id": "alarm_control_panel.audit", "initial_value": "disarmed",
        "native_templates": {"supported_features": "{{ states('sensor.alarm_features') | int }}"},
    }), False)
    entity.hass = hass
    entity._create_state(entity._config)
    entity._schedule_state_update = Mock()
    for features in (AlarmControlPanelEntityFeature.ARM_HOME, AlarmControlPanelEntityFeature.ARM_AWAY, 0):
        hass.states.async_set("sensor.alarm_features", str(int(features)))
        entity._apply_templates()
        assert entity.supported_features == features


@pytest.mark.parametrize("mask", [-1, True, 1.5])
def test_alarm_legacy_options_do_not_enable_unrequested_controls(mask):
    entity = VirtualAlarmControlPanel(ENTITY_SCHEMA({
        "name": "Legacy alarm", "supported_features": mask, "code_arm_required": "false",
    }), False)
    assert entity.supported_features == 0
    assert entity.code_arm_required is False


@pytest.mark.parametrize("restored_mode,expected", [("removed", "music"), ("movie", "movie")])
def test_media_restore_respects_current_sound_modes(restored_mode, expected):
    entity = VirtualMediaPlayer(MEDIA_SCHEMA({
        "name": "Media", "entity_id": "media_player.audit",
        "sound_mode_list": ["music", "movie"], "sound_mode": "music",
    }), False)
    entity._restore_state(State(entity.entity_id, "playing", {"sound_mode": restored_mode}), entity._config)
    assert entity.sound_mode == expected


async def test_command_mapping_templates_observe_script_variables_and_branches(hass):
    calls = []

    async def capture(call):
        calls.append(dict(call.data))

    hass.services.async_register("audit", "capture", capture)
    entity = VirtualSwitch(SWITCH_SCHEMA({
        "name": "Script", "entity_id": "switch.script_audit", "initial_value": "off",
        "command_actions": {"turn_on": [
            {"variables": {"local_value": 42}},
            {"if": "{{ false }}", "then": [
                {"action": "audit.capture", "data": "{{ dict(command_data, value=missing_variable) }}"},
            ]},
            {"action": "audit.capture", "data": "{{ dict(command_data, value=local_value) }}"},
        ]},
    }), False)
    entity.hass = hass
    entity._create_state(entity._config)
    entity.async_write_ha_state = Mock()
    await entity.async_turn_on()
    assert calls == [{"value": 42}]


async def test_calendar_tracks_event_boundaries_without_source_updates(hass, freezer):
    now = dt_util.utcnow()
    event = {"summary": "Meeting", "start": (now + timedelta(seconds=30)).isoformat(),
             "end": (now + timedelta(seconds=60)).isoformat()}
    entity = GenericVirtualEntity(GENERIC_SCHEMA({
        "name": "Calendar", "entity_id": "calendar.audit", "initial_value": "off", "event": event,
    }), "calendar", False)
    entity.hass = hass
    entity._create_state(entity._config)
    entity._schedule_state_update = Mock()
    entity._update_attributes()
    assert entity.state == "off"
    for seconds, expected in ((31, "on"), (61, "off")):
        freezer.move_to(now + timedelta(seconds=seconds))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()
        assert entity.state == expected
    entity._apply_native_template_value("event", {
        **event, "end": (now + timedelta(seconds=90)).isoformat(),
    })
    entity._update_attributes()
    assert entity.state == "on"
    await entity.async_will_remove_from_hass()
    entity._schedule_state_update.reset_mock()
    freezer.move_to(now + timedelta(seconds=91))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    entity._schedule_state_update.assert_not_called()


async def test_calendar_invalid_timezone_does_not_break_entity_updates(hass):
    entity = GenericVirtualEntity(GENERIC_SCHEMA({
        "name": "Legacy calendar", "entity_id": "calendar.invalid_timezone",
        "event": {"start": "2026-09-27T12:30:00+25:00", "end": "2026-09-28T12:30:00+09:00"},
    }), "calendar", False)
    entity.hass = hass
    entity._create_state(entity._config)
    entity._update_attributes()
    assert entity.state == "off"
    await entity.async_will_remove_from_hass()
