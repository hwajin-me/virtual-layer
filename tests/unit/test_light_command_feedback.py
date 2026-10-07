"""Command goals never hide observed source state beyond a display lease."""

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest
import voluptuous as vol

from custom_components.virtual_layer.light import LIGHT_SCHEMA, VirtualLight


def make_light(hass, count=1, **options):
    sources = [f"light.feedback_{index}" for index in range(count)]
    for source in sources:
        hass.states.async_set(source, "on", {"brightness": 100})
    entity = VirtualLight(LIGHT_SCHEMA({
        "name": "Feedback", "entity_id": "light.feedback_virtual",
        "initial_value": "on", "source_entities": sources,
        "matter_light_type": "dimmable", "persistent": False, **options,
    }), False)
    entity.hass = hass
    entity._create_state(entity._config)
    entity.async_write_ha_state = Mock()
    entity._schedule_state_update = Mock()
    return entity


@pytest.mark.parametrize("count", [1, 2])
@pytest.mark.parametrize("delay,retries", [(0, 0), (0, 2), (2, 0), (2, 2)])
async def test_unconfirmed_off_keeps_observed_state(hass, count, delay, retries):
    entity = make_light(hass, count, light_response_delay=delay,
                        light_response_retries=retries)
    hass.services.async_register("light", "turn_off", AsyncMock())
    try:
        await entity.async_turn_off()
        assert entity.is_on
        assert entity.extra_state_attributes["command_target"]["power"] == "off"
        assert entity.extra_state_attributes["command_status"] == "awaiting_feedback"
        entity._expire_command(entity._group_revision)
        assert entity.is_on
        assert entity.extra_state_attributes["command_status"] == "timed_out"
    finally:
        await entity.async_will_remove_from_hass()


@pytest.mark.parametrize("count", [1, 2])
async def test_display_lease_ends_while_service_still_running(hass, count):
    entity = make_light(hass, count, light_state_mode="bounded_optimistic")
    release = asyncio.Event()

    async def blocked(call):
        await release.wait()

    hass.services.async_register("light", "turn_off", blocked)
    try:
        with patch("custom_components.virtual_layer.light.async_call_later", return_value=Mock()) as later:
            await entity.async_turn_off(transition=60)
            assert not entity.is_on
            lease = next(call.args[2] for call in later.call_args_list if call.args[1] == 1)
            lease(None)
            assert entity.is_on
            assert entity.extra_state_attributes["command_status"] == "dispatching"
            assert entity.extra_state_attributes["command_target"]["power"] == "off"
    finally:
        release.set()
        await entity.async_will_remove_from_hass()


async def test_lost_source_cannot_leave_optimistic_off_as_last_observation(hass):
    entity = make_light(hass, light_state_mode="bounded_optimistic",
                        availability_template="{{ true }}")
    hass.services.async_register("light", "turn_off", AsyncMock())
    try:
        with patch("custom_components.virtual_layer.light.async_call_later", return_value=Mock()) as later:
            await entity.async_turn_off()
            assert not entity.is_on
            hass.states.async_set("light.feedback_0", "unknown")
            lease = next(call.args[2] for call in later.call_args_list if call.args[1] == 1)
            lease(None)
            assert entity.is_on  # Restore the last observation when no new valid one exists.
            assert entity.extra_state_attributes["command_target"]["power"] == "off"
    finally:
        await entity.async_will_remove_from_hass()


async def test_queued_off_expires_without_dispatch_and_old_completion_cannot_restore_goal(hass):
    entity = make_light(hass, light_state_mode="bounded_optimistic")
    release = asyncio.Event()
    calls = []

    async def blocked(call):
        calls.append(call.service)
        await release.wait()

    hass.services.async_register("light", "turn_on", blocked)
    hass.services.async_register("light", "turn_off", blocked)
    try:
        await entity.async_turn_on(brightness=200)
        old_revision = entity._group_revision
        await entity.async_turn_off()
        assert not entity.is_on
        assert entity.extra_state_attributes["command_sources"]["light.feedback_0"]["status"] == "queued"
        assert entity.extra_state_attributes["previous_command_status"] == "superseded"
        entity._expire_command(old_revision)
        assert not entity.is_on  # A superseded deadline cannot release the new lease.
        entity._expire_command(entity._group_revision)
        assert entity.is_on
        release.set()
        await hass.async_block_till_done()
        assert calls == ["turn_on"]
        assert entity.is_on
        assert entity.extra_state_attributes["command_status"] == "timed_out"
    finally:
        release.set()
        await entity.async_will_remove_from_hass()


@pytest.mark.parametrize("count", [1, 2])
async def test_acknowledged_group_resumes_external_source_changes(hass, count):
    entity = make_light(hass, count, light_state_mode="bounded_optimistic")

    async def off(call):
        hass.states.async_set(call.data["entity_id"][0], "off")
        entity._apply_templates()

    hass.services.async_register("light", "turn_off", off)
    try:
        await entity.async_turn_off()
        assert not entity.is_on
        assert entity.extra_state_attributes["command_status"] == "confirmed"
        assert entity._optimistic_cancel is None
        assert entity._command_deadline_cancel is None
        for source in entity._source_entities:
            hass.states.async_set(source, "on")
        entity._apply_templates()
        assert entity.is_on
    finally:
        await entity.async_will_remove_from_hass()


@pytest.mark.parametrize("state", ["unknown", "unavailable", None])
async def test_missing_feedback_is_failure_and_does_not_invent_off(hass, state):
    entity = make_light(hass)
    source = entity._source_entities[0]
    hass.services.async_register("light", "turn_off", AsyncMock())
    try:
        await entity.async_turn_off()
        if state is None:
            hass.states.async_remove(source)
        else:
            hass.states.async_set(source, state)
        entity._apply_templates()
        entity._command_sources[source]["next_check"] = hass.loop.time() - 1
        await entity._async_refresh_group(entity._group_revision, 2)
        assert not entity.available
        assert entity.is_on  # Preserved observation is unavailable, never a fake success.
        assert entity.extra_state_attributes["command_status"] == "failed"
        assert entity.extra_state_attributes["command_sources"][source]["error"] == "source_unavailable"
    finally:
        await entity.async_will_remove_from_hass()


async def test_retries_are_bounded_and_do_not_extend_feedback_deadline(hass):
    entity = make_light(hass, light_response_retries=1)
    send = AsyncMock()
    hass.services.async_register("light", "turn_off", send)
    try:
        await entity.async_turn_off(transition=5)
        source = entity._source_entities[0]
        record = entity._command_sources[source]
        deadline = record["feedback_deadline"]
        record["next_check"] = hass.loop.time() - 1
        with patch("custom_components.virtual_layer.light.async_update_entity", new_callable=AsyncMock) as refresh:
            await entity._async_refresh_group(entity._group_revision, 1)
            await hass.async_block_till_done()
            refresh.assert_not_awaited()  # No generic read-back API for a push source.
        assert send.await_count == 2
        assert record["feedback_deadline"] == deadline
        record["next_check"] = hass.loop.time() - 1
        await entity._async_refresh_group(entity._group_revision, 1)
        assert send.await_count == 2
        record["feedback_deadline"] = hass.loop.time() - 1
        await entity._async_refresh_group(entity._group_revision, 1)
        assert entity.is_on
        assert entity.extra_state_attributes["command_status"] == "timed_out"
    finally:
        await entity.async_will_remove_from_hass()


@pytest.mark.parametrize("failure", ["exception", "timeout"])
async def test_dispatch_failure_is_reported_without_exposing_payload(hass, failure):
    entity = make_light(hass, light_dispatch_timeout=0.05)

    async def fail(call):
        if failure == "exception":
            raise ValueError("private payload must never appear")
        await asyncio.Event().wait()

    hass.services.async_register("light", "turn_off", fail)
    try:
        await entity.async_turn_off()
        await hass.async_block_till_done()
        assert entity.is_on
        attributes = entity.extra_state_attributes
        assert attributes["command_status"] == ("failed" if failure == "exception" else "timed_out")
        assert "private payload" not in str(attributes)
    finally:
        await entity.async_will_remove_from_hass()


async def test_already_matching_state_is_not_a_new_physical_ack(hass):
    entity = make_light(hass)
    hass.services.async_register("light", "turn_on", AsyncMock())
    try:
        await entity.async_turn_on()
        record = entity.extra_state_attributes["command_sources"]["light.feedback_0"]
        assert record["status"] == "already_at_target"
        assert record["confirmation"] == "source_state"
    finally:
        await entity.async_will_remove_from_hass()


async def test_feedback_deadline_does_not_wait_for_service_return(hass):
    entity = make_light(hass, light_dispatch_timeout=1, light_feedback_timeout=0.05)
    release = asyncio.Event()

    async def blocked(call):
        await release.wait()

    hass.services.async_register("light", "turn_off", blocked)
    try:
        await entity.async_turn_off()
        async with asyncio.timeout(1):
            while entity.extra_state_attributes["command_status"] != "timed_out":
                await asyncio.sleep(0.01)
        assert not release.is_set()
        assert entity.is_on
        assert entity.extra_state_attributes["command_sources"]["light.feedback_0"]["error"] == "feedback_timeout"
        release.set()
        await hass.async_block_till_done()
        assert entity.extra_state_attributes["command_status"] == "timed_out"
    finally:
        release.set()
        await entity.async_will_remove_from_hass()


async def test_responsive_member_retries_without_waiting_for_blocked_member(hass):
    entity = make_light(hass, 2)
    release, confirmed = asyncio.Event(), asyncio.Event()
    calls = []

    async def off(call):
        source = call.data["entity_id"][0]
        calls.append(source)
        if source == "light.feedback_0":
            await release.wait()
        elif calls.count(source) == 2:
            hass.states.async_set(source, "off")
            entity._apply_templates()
            confirmed.set()

    hass.services.async_register("light", "turn_off", off)
    try:
        with patch("custom_components.virtual_layer.light.async_call_later", return_value=Mock()):
            await entity.async_turn_off()
            entity._command_sources["light.feedback_1"]["next_check"] = hass.loop.time() - 1
            await entity._async_refresh_group(entity._group_revision, 2)
            await asyncio.wait_for(confirmed.wait(), 1)
            assert calls.count("light.feedback_1") == 2
            assert calls.count("light.feedback_0") == 1
            assert entity.extra_state_attributes["command_sources"]["light.feedback_0"]["status"] == "dispatching"
    finally:
        release.set()
        await entity.async_will_remove_from_hass()


async def test_zero_interval_never_retransmits_even_with_retry_budget(hass):
    entity = make_light(hass, light_response_delay=0, light_response_retries=10)
    send = AsyncMock()
    hass.services.async_register("light", "turn_off", send)
    try:
        await entity.async_turn_off()
        await entity._async_refresh_group(entity._group_revision, 10)
        await hass.async_block_till_done()
        assert send.await_count == 1
        assert entity.is_on
    finally:
        await entity.async_will_remove_from_hass()


async def test_real_total_deadline_drops_queue_when_old_transport_ignores_cancellation(hass):
    entity = make_light(hass, light_command_timeout=0.1)
    release = asyncio.Event()
    calls = []

    async def noncooperative_transport(call):
        calls.append(call.service)
        try:
            await release.wait()
        except asyncio.CancelledError:
            # An integration may already have sent the command or ignore a
            # local timeout. Its worker must not pin the virtual display/queue.
            await release.wait()

    hass.services.async_register("light", "turn_on", noncooperative_transport)
    hass.services.async_register("light", "turn_off", noncooperative_transport)
    try:
        await entity.async_turn_on(brightness=200)
        await entity.async_turn_off()
        async with asyncio.timeout(1):
            while entity.extra_state_attributes["command_status"] != "timed_out":
                await asyncio.sleep(0.01)
        assert entity.is_on
        assert calls == ["turn_on"]
        assert not entity._source_pending_commands
        release.set()
        await hass.async_block_till_done()
        assert calls == ["turn_on"]
    finally:
        release.set()
        await entity.async_will_remove_from_hass()


@pytest.mark.parametrize("key,value", [
    ("light_state_mode", "forever"), ("light_optimistic_window", 6),
    ("light_dispatch_timeout", True), ("light_feedback_timeout", float("nan")),
    ("light_command_timeout", float("inf")), ("light_command_timeout", -1),
])
def test_invalid_command_policies_are_rejected(key, value):
    with pytest.raises(vol.Invalid):
        LIGHT_SCHEMA({"name": "Invalid", "entity_id": "light.invalid", key: value})
