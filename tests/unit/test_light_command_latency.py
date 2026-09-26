"""Slow source calls must not stall controls or build a stale command queue."""

import asyncio
from unittest.mock import Mock, patch

import pytest

from custom_components.virtual_layer.light import LIGHT_SCHEMA, VirtualLight


def make_light(hass, sources):
    entity = VirtualLight(LIGHT_SCHEMA({
        "name": "Responsive light", "entity_id": "light.responsive",
        "initial_value": "off", "source_entities": sources,
        "matter_light_type": "dimmable", "persistent": False,
        "native_templates": {"is_on": "{{ is_state('light.slow', 'on') }}"},
    }), False)
    entity.hass = hass
    entity._create_state(entity._config)
    entity.async_write_ha_state = Mock()
    entity._schedule_state_update = Mock()
    for source in sources:
        hass.states.async_set(source, "off")
    return entity


async def test_slow_member_does_not_block_next_command_to_healthy_bulb(hass):
    entity = make_light(hass, ["light.slow", "light.fast"])
    release = asyncio.Event()
    calls = []

    async def handle(call):
        source = call.data["entity_id"][0]
        calls.append((source, call.service))
        if source == "light.slow" and call.service == "turn_on":
            await release.wait()
        hass.states.async_set(source, "on" if call.service == "turn_on" else "off")

    for command in ("turn_on", "turn_off"):
        hass.services.async_register("light", command, handle)
    try:
        with patch("custom_components.virtual_layer.light.async_call_later", return_value=Mock()):
            await asyncio.wait_for(entity.async_turn_on(), 0.5)
            assert entity.is_on
            await asyncio.wait_for(entity.async_turn_off(), 0.5)
            assert not entity.is_on
            assert ("light.fast", "turn_off") in calls
            assert ("light.slow", "turn_off") not in calls
            release.set()
            await hass.async_block_till_done()
            assert calls[-1] == ("light.slow", "turn_off")
            assert not entity.is_on
    finally:
        release.set()
        await entity.async_will_remove_from_hass()


async def test_busy_bulb_keeps_only_latest_pending_brightness(hass):
    entity = make_light(hass, ["light.slow"])
    release = asyncio.Event()
    calls = []

    async def handle(call):
        calls.append(call.data["brightness"])
        if len(calls) == 1:
            await release.wait()
        hass.states.async_set("light.slow", "on", {"brightness": call.data["brightness"]})

    hass.services.async_register("light", "turn_on", handle)
    try:
        with patch("custom_components.virtual_layer.light.async_call_later", return_value=Mock()):
            for brightness in (50, 100, 150, 200):
                await asyncio.wait_for(entity.async_turn_on(brightness=brightness), 0.5)
                assert entity.brightness == brightness
            assert calls == [50]
            release.set()
            await hass.async_block_till_done()
            assert calls == [50, 200]
            assert entity.brightness == 200
            assert entity._response_refresh_cancel is None
    finally:
        release.set()
        await entity.async_will_remove_from_hass()


async def test_fast_report_is_accepted_before_slow_service_returns(hass):
    entity = make_light(hass, ["light.slow"])
    release = asyncio.Event()

    async def handle(call):
        hass.states.async_set("light.slow", "on")
        entity._apply_templates()
        await release.wait()

    hass.services.async_register("light", "turn_on", handle)
    try:
        await asyncio.wait_for(entity.async_turn_on(), 0.5)
        assert not entity._group_authoritative
        hass.states.async_set("light.slow", "off")
        entity._apply_templates()
        assert not entity.is_on
        release.set()
        await hass.async_block_till_done()
        assert not entity.is_on
        assert entity._response_refresh_cancel is None
    finally:
        release.set()
        await entity.async_will_remove_from_hass()


async def test_unload_cancels_slow_dispatch_and_drops_pending_command(hass):
    entity = make_light(hass, ["light.slow"])
    calls = []
    cancelled = asyncio.Event()

    async def handle(call):
        calls.append(call.service)
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    for command in ("turn_on", "turn_off"):
        hass.services.async_register("light", command, handle)
    try:
        await asyncio.wait_for(entity.async_turn_on(), 0.5)
        await asyncio.wait_for(entity.async_turn_off(), 0.5)
    finally:
        await entity.async_will_remove_from_hass()
    assert cancelled.is_set()
    assert calls == ["turn_on"]
    assert entity._response_refresh_cancel is None


async def test_pending_brightness_retains_unsent_color_without_duplicate_descriptors(hass):
    entity = make_light(hass, ["light.slow"])
    entity._attr_supported_color_modes = {"hs", "color_temp"}
    release = asyncio.Event()
    calls = []

    async def handle(call):
        calls.append(dict(call.data))
        if len(calls) == 1:
            await release.wait()

    hass.services.async_register("light", "turn_on", handle)
    try:
        with patch("custom_components.virtual_layer.light.async_call_later", return_value=Mock()):
            for kwargs in ({"brightness": 50}, {"color_temp_kelvin": 4000},
                           {"hs_color": [120, 50]}, {"brightness": 200}):
                await asyncio.wait_for(entity.async_turn_on(**kwargs), 0.5)
            release.set()
            await hass.async_block_till_done()
            assert len(calls) == 2
            assert calls[-1]["brightness"] == 200
            assert calls[-1]["hs_color"] == [120, 50]
            assert "color_temp_kelvin" not in calls[-1]
    finally:
        release.set()
        await entity.async_will_remove_from_hass()
