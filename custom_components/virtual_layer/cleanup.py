"""Non-blocking startup and daily diagnostic maintenance per config entry."""

import asyncio
import logging
from datetime import timedelta

from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_time_interval

from .debug import DEBUG_SUFFIX, cleanse

_LOGGER = logging.getLogger(__name__)
CLEANUP_INTERVAL = timedelta(days=1)
_BATCH_SIZE = 10


@callback
def async_setup(hass, entry):
    """Start after HA startup; cancel all work when the entry unloads."""
    task = None
    stopped = False
    cancel_started = None
    cancel_interval = None

    async def sweep():
        # Yield before enumerating targets so integration setup can finish.
        await asyncio.sleep(0)
        registry = er.async_get(hass)
        rows = er.async_entries_for_config_entry(registry, entry.entry_id)
        targets = {row.unique_id.removesuffix(DEBUG_SUFFIX) for row in rows
                   if row.unique_id.endswith(DEBUG_SUFFIX)}
        for index, row in enumerate(row for row in rows if row.unique_id in targets):
            if stopped:
                return
            if index and index % _BATCH_SIZE == 0:
                await asyncio.sleep(0)
            # Configuration may change while a batch is yielding.
            current = registry.async_get(row.entity_id)
            if current is None or current.unique_id != row.unique_id:
                continue
            try:
                cleanse(hass, entry, current)
            except Exception:
                # Isolate damaged targets without logging their attributes.
                _LOGGER.exception("Diagnostic cleanup failed for %s", row.entity_id)

    @callback
    def schedule(_event=None):
        nonlocal task
        if not stopped and (task is None or task.done()):
            task = hass.async_create_background_task(
                sweep(), f"virtual_layer diagnostic cleanup {entry.entry_id}"
            )

    @callback
    def start(_event=None):
        nonlocal cancel_interval, cancel_started
        cancel_started = None
        if stopped:
            return
        schedule()
        cancel_interval = async_track_time_interval(hass, schedule, CLEANUP_INTERVAL)

    @callback
    def stop():
        nonlocal stopped
        stopped = True
        if cancel_started is not None:
            cancel_started()
        if cancel_interval is not None:
            cancel_interval()
        if task is not None and not task.done():
            task.cancel()

    entry.async_on_unload(stop)
    if hass.state is CoreState.running:
        start()
    else:
        cancel_started = hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, start)
