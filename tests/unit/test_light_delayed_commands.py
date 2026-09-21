"""Ordering and response matching for delayed light commands."""

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.virtual_layer.light import LIGHT_SCHEMA, VirtualLight


def make_light(hass, actions=None, delay=2):
    entity = VirtualLight(LIGHT_SCHEMA({
        "name": "Delayed light", "entity_id": "light.delayed_target",
        "initial_value": "off", "source_entities": ["light.slow"],
        "matter_light_type": "dimmable", "persistent": False,
        "command_actions": actions or {}, "light_response_delay": delay,
        "native_templates": {"is_on": "{{ is_state('light.slow', 'on') }}"},
    }), False)
    entity.hass = hass
    entity._create_state(entity._config)
    entity.async_write_ha_state = Mock()
    entity._schedule_state_update = Mock()
    return entity


@pytest.mark.parametrize("custom_first", [False, True])
@pytest.mark.parametrize("delay", [0, 2])
async def test_custom_and_stock_commands_share_order(hass, custom_first, delay):
    """An old delayed action cannot finish after the newer opposite command."""
    command = "turn_on" if custom_first else "turn_off"
    actions = {command: [{"action": "test.custom"}]}
    entity = make_light(hass, actions, delay)
    started, release = asyncio.Event(), asyncio.Event()
    calls = []
    cancelers = []

    def schedule(*args):
        cancel = Mock()
        cancelers.append(cancel)
        return cancel

    async def handle(call):
        calls.append(call.service)
        if len(calls) == 1:
            started.set()
            await release.wait()

    for domain, service in [("light", "turn_on"), ("light", "turn_off"), ("test", "custom")]:
        hass.services.async_register(domain, service, handle)
    with patch("custom_components.virtual_layer.light.async_call_later", side_effect=schedule):
        first = asyncio.create_task(entity.async_turn_on(brightness=100))
        await asyncio.wait_for(started.wait(), 1)
        second = asyncio.create_task(entity.async_turn_off())
        await asyncio.sleep(0)
        assert len(calls) == 1
        release.set()
        await asyncio.wait_for(asyncio.gather(first, second), 1)
        assert calls == (["custom", "turn_off"] if custom_first else ["turn_on", "custom"])
        assert not entity.is_on
        if custom_first and delay:
            cancelers[0].assert_called_once()
        entity._cancel_group_refresh()


async def test_cancelled_custom_action_releases_source_hold(hass):
    entity = make_light(hass, {"turn_on": [{"action": "test.custom"}]})
    started = asyncio.Event()

    async def handle(call):
        started.set()
        await asyncio.Event().wait()

    hass.services.async_register("test", "custom", handle)
    task = asyncio.create_task(entity.async_turn_on(brightness=100))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert not entity._response_pending
    hass.states.async_set("light.slow", "on")
    entity._apply_templates()
    assert entity.is_on


async def test_nonoptimistic_off_waits_for_inflight_stock_on(hass):
    entity = make_light(hass, {"turn_off": {
        "sequence": [{"action": "test.off"}], "optimistic": False,
    }})
    started, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def turn_on(call):
        calls.append("on")
        started.set()
        await release.wait()
        hass.states.async_set("light.slow", "on")

    async def turn_off(call):
        calls.append("off")
        # The device rejects this command; its source state must still win.

    hass.services.async_register("light", "turn_on", turn_on)
    hass.services.async_register("test", "off", turn_off)
    with patch("custom_components.virtual_layer.light.async_call_later", return_value=Mock()):
        first = asyncio.create_task(entity.async_turn_on())
        await asyncio.wait_for(started.wait(), 1)
        second = asyncio.create_task(entity.async_turn_off())
        await asyncio.sleep(0)
        assert calls == ["on"]
        release.set()
        await asyncio.wait_for(asyncio.gather(first, second), 1)
        assert calls == ["on", "off"]
        assert entity.is_on
        assert entity._response_refresh_cancel is None


@pytest.mark.parametrize("reported,expected", [(359.5, True), (0.5, True), (180, False), (True, False)])
async def test_hue_response_uses_circular_distance(hass, reported, expected):
    entity = make_light(hass)
    hass.states.async_set("light.slow", "on", {"hs_color": [reported, 100]})
    assert entity._group_source_matches("light.slow", "turn_on", {"hs_color": [0, 100]}) == expected


async def test_onoff_member_is_not_retried_for_unsupported_brightness(hass):
    entity = make_light(hass)
    hass.states.async_set("light.slow", "on", {"supported_color_modes": ["onoff"]})
    calls = AsyncMock()
    hass.services.async_register("light", "turn_on", calls)
    with patch("custom_components.virtual_layer.light.async_call_later", return_value=Mock()), patch(
        "custom_components.virtual_layer.light.async_update_entity", new_callable=AsyncMock
    ) as refresh:
        await entity.async_turn_on(brightness=150)
        calls.reset_mock()
        await entity._async_refresh_group(entity._group_revision, 2)
        calls.assert_not_awaited()
        refresh.assert_not_awaited()
        entity._cancel_group_refresh()
