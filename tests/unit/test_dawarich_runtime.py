"""Dawarich lifecycle, stale data and integration with local aggregation."""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.core import State
from homeassistant.util import dt as dt_util

from custom_components.virtual_layer import device_tracker as platform
from custom_components.virtual_layer.dawarich import (
    DawarichError,
    DawarichSnapshot,
    valid_point,
)
from test_dawarich import CONFIG, point

pytestmark = pytest.mark.unit


def tracker(hass, **extra):
    config = {
        "name": "Dawarich",
        "entity_id": "device_tracker.dawarich",
        "unique_id": "dawarich",
        "device_id": "dawarich",
        "initial_value": "not_home",
        "initial_availability": True,
        "persistent": True,
        "dawarich": CONFIG,
        **extra,
    }
    result = platform.VirtualDeviceTracker(config)
    result.hass = hass
    result.async_schedule_update_ha_state = Mock()
    result._create_state(config)
    return result, config


def snapshot(**kwargs):
    value = valid_point(point(**kwargs))
    return DawarichSnapshot(value, [value], None)


def test_explicit_gps_sources_work_without_generated_helpers(hass):
    result, _ = tracker(hass, source_entities=["device_tracker.offline_phone"])
    # Setup may run before a phone reports or while Dawarich is unreachable.
    hass.states.async_set("device_tracker.offline_phone", "not_home", {
        "latitude": 37.5, "longitude": 127, "gps_accuracy": 5,
    })
    result._update_location_from_sources()
    assert (result.latitude, result.longitude) == (37.5, 127)
    assert result.extra_state_attributes["location_stale"] is False


async def test_old_response_cannot_roll_back_and_restore_preserves_measurement_time(
    hass, monkeypatch
):
    current = snapshot(age=10)
    fetch = AsyncMock(
        side_effect=[
            current,
            snapshot(age=100, latitude=38),
            DawarichError("cannot_connect"),
        ]
    )
    monkeypatch.setattr(platform.DawarichClient, "async_fetch", fetch)
    result, config = tracker(hass)
    await result._async_refresh_dawarich()
    measured = result._dawarich_state.last_updated
    await result._async_refresh_dawarich()
    assert result.latitude == 37.5
    assert result.extra_state_attributes["dawarich_error"] == "older_point"
    await result._async_refresh_dawarich()
    assert result.latitude == 37.5
    assert result.extra_state_attributes["dawarich_error"] == "cannot_connect"
    restored, _ = tracker(hass)
    restored._restore_state(
        State("device_tracker.dawarich", "not_home", result.extra_state_attributes),
        config,
    )
    restored._update_attributes()
    assert restored._dawarich_state.last_updated == measured
    assert restored.extra_state_attributes["dawarich_stale"] is True


async def test_overlapping_refresh_skipped_and_unload_cancels_request(
    hass, monkeypatch
):
    entered = asyncio.Event()
    release = asyncio.Event()

    async def fetch(*args, **kwargs):
        entered.set()
        await release.wait()
        return snapshot()

    fetch_mock = AsyncMock(side_effect=fetch)
    monkeypatch.setattr(platform.DawarichClient, "async_fetch", fetch_mock)
    result, _ = tracker(hass)
    pending = asyncio.create_task(result._async_refresh_dawarich())
    await entered.wait()
    await result._async_refresh_dawarich()
    fetch_mock.assert_awaited_once()
    await result.async_will_remove_from_hass()
    assert pending.cancelled()
    assert result._dawarich_state is None
    await result._async_refresh_dawarich()
    fetch_mock.assert_awaited_once()


async def test_changed_account_does_not_restore_other_person_or_block_older_point(
    hass, monkeypatch
):
    fetch = AsyncMock(return_value=snapshot())
    monkeypatch.setattr(platform.DawarichClient, "async_fetch", fetch)
    original, _ = tracker(hass)
    await original._async_refresh_dawarich()
    saved = State(
        "device_tracker.dawarich", "not_home", original.extra_state_attributes
    )
    changed, config = tracker(hass, dawarich={**CONFIG, "member": "other@example.test"})
    changed._restore_state(saved, config)
    assert changed.latitude is None
    assert changed._dawarich_state is None
    assert "dawarich_point" not in changed._virtual_attributes
    fetch.return_value = snapshot(age=300, latitude=38)
    await changed._async_refresh_dawarich()
    assert changed.latitude == 38
    assert changed.extra_state_attributes["dawarich_error"] is None


async def test_dawarich_motion_participates_in_helper_without_poll_freshness(
    hass, freezer, monkeypatch
):
    hass.states.async_set(
        "device_tracker.tablet",
        "not_home",
        {"latitude": 37.5, "longitude": 127, "gps_accuracy": 10},
    )
    result, _ = tracker(
        hass,
        source_entities=["device_tracker.tablet"],
        location_helper={"distance_threshold_meters": 300},
    )
    fetch = AsyncMock(return_value=snapshot())
    monkeypatch.setattr(platform.DawarichClient, "async_fetch", fetch)
    await result._async_refresh_dawarich()
    freezer.tick(timedelta(seconds=120))
    fetch.return_value = snapshot(latitude=37.51)
    await result._async_refresh_dawarich()
    assert result.latitude == 37.51
    assert result.extra_state_attributes["location_priority_source"] == "dawarich"
    measured = result._dawarich_state.last_updated
    freezer.tick(timedelta(minutes=31))
    await result._async_refresh_dawarich()
    assert result._dawarich_state.last_updated == measured
    assert result.extra_state_attributes["dawarich_stale"] is True
    assert result.extra_state_attributes["location_stale"] is True
    assert result.latitude == 37.51
