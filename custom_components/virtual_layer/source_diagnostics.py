"""Bounded, live source diagnostics on the configured virtual entity itself."""

import json
import math
from collections.abc import Mapping
from datetime import timedelta

from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import (
    async_track_point_in_utc_time,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_ENTITY_ID, CONF_SOURCE_DIAGNOSTICS, TRANSIENT_SOURCE_ATTRIBUTE_NAMES,
    CONF_DIAGNOSTIC_DETAILS, CONF_DIAGNOSTIC_INTERVAL, DEFAULT_DIAGNOSTIC_INTERVAL,
    CONF_DIAGNOSTIC_DETAILS_UNTIL, ATTR_RUNTIME_VIRTUAL_ATTRIBUTES, ATTR_UNIQUE_ID,
    ATTR_DIAGNOSTIC_ATTRIBUTE_RETENTION,
)
from .zigbee_refresh import DIAGNOSTICS_UPDATED, async_watch_sources, diagnostic_attributes

_KEYS = {"source_configuration", "source_diagnostics", "source_diagnostics_truncated"}
_EXCLUDED = _KEYS | TRANSIENT_SOURCE_ATTRIBUTE_NAMES | {
    ATTR_RUNTIME_VIRTUAL_ATTRIBUTES, ATTR_DIAGNOSTIC_ATTRIBUTE_RETENTION,
}
_COMPACT_COMMUNICATION_KEYS = {
    "source_integration", "zigbee_device_availability", "zigbee_bridge_availability",
    "zigbee_mqtt_connected",
}
COMPACT_DIAGNOSTIC_BYTES = 2048


def _parse_deadline(value):
    """Isolate damaged stored timestamps just like other legacy options."""
    if not isinstance(value, str):
        return None
    try:
        parsed = dt_util.parse_datetime(value)
        return dt_util.as_utc(parsed) if parsed else None
    except (OverflowError, ValueError):
        return None


def prepare_retention(hass, entry, entities):
    """Persist one seven-day window per stable entity identity across reloads."""
    stored = entry.data.get(CONF_DIAGNOSTIC_DETAILS_UNTIL, {})
    stored = stored if isinstance(stored, Mapping) else {}
    deadlines = {}
    now = dt_util.utcnow()
    for records in entities.values():
        for config in records:
            if CONF_SOURCE_DIAGNOSTICS not in config or not config.get(CONF_DIAGNOSTIC_DETAILS, True):
                continue
            uid = config[ATTR_UNIQUE_ID]
            value = stored.get(uid)
            deadline = _parse_deadline(value) or now + timedelta(days=7)
            # Malformed/future legacy data cannot extend retention indefinitely.
            deadline = min(deadline, now + timedelta(days=7))
            deadlines[uid] = config[CONF_DIAGNOSTIC_DETAILS_UNTIL] = deadline.isoformat()
    if stored != deadlines or CONF_DIAGNOSTIC_DETAILS_UNTIL not in entry.data:
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_DIAGNOSTIC_DETAILS_UNTIL: deadlines},
        )


def details_enabled(config):
    """Detailed snapshots expire even when sources never change."""
    if not config.get(CONF_DIAGNOSTIC_DETAILS, True):
        return False
    value = config.get(CONF_DIAGNOSTIC_DETAILS_UNTIL)
    if value is None:
        return True
    deadline = _parse_deadline(value)
    return deadline is not None and deadline > dt_util.utcnow()


def meaningful_change(event):
    """Do not propagate changes caused solely by another virtual's diagnostics."""
    if not isinstance(getattr(event, "data", None), Mapping):
        return True
    before, after = event.data.get("old_state"), event.data.get("new_state")
    return not (before and after and before.state == after.state and {
        key: value for key, value in before.attributes.items() if key not in _KEYS
    } == {key: value for key, value in after.attributes.items() if key not in _KEYS})


def _bounded_items(value, remaining, truncated):
    """Charge every examined key, even excluded or rejected diagnostic copies."""
    examined = 0
    for key in value:
        if examined >= 32 or remaining[0] <= 0:
            truncated[0] = True
            break
        examined += 1
        remaining[0] -= 1
        yield key, value[key]


def _preview(value, depth=0, remaining=None, truncated=None):
    if remaining is None:
        remaining = [128]
    if truncated is None:
        truncated = [False]
    remaining[0] -= 1
    if depth >= 5 or remaining[0] < 0:
        truncated[0] = True
        return "…"
    if isinstance(value, str):
        if len(value) > 1024:
            truncated[0] = True
        return value[:1024] + ("…" if len(value) > 1024 else "")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        try:
            return value if math.isfinite(value) else None
        except OverflowError:
            return None
    if isinstance(value, Mapping):
        return {
            str(key)[:128]: _preview(item, depth + 1, remaining, truncated)
            for key, item in _bounded_items(value, remaining, truncated)
            if key not in _EXCLUDED
        }
    if isinstance(value, (list, tuple)):
        result = []
        for index, item in enumerate(value):
            if index >= 32 or remaining[0] <= 0:
                truncated[0] = True
                break
            result.append(_preview(item, depth + 1, remaining, truncated))
        return result
    return str(value)[:1024]


def _size(value):
    return len(json.dumps(value, default=str, ensure_ascii=False).encode())


def fit_snapshot(hass, config, current):
    """Rebudget a cached snapshot after ordinary attributes grow."""
    if current.keys() & _KEYS and _size(current) > 12000:
        for key in _KEYS:
            current.pop(key, None)
        current.update(attributes(hass, config, current))


def attributes(hass, config, current, *, include_details=False):
    """Publish stable compact metadata; detailed copies require an explicit request."""
    configuration = config.get(CONF_SOURCE_DIAGNOSTICS)
    if not isinstance(configuration, Mapping):
        return {}
    detailed = include_details and details_enabled(config)
    budget = min(
        12000 if detailed else COMPACT_DIAGNOSTIC_BYTES,
        max(0, 12000 - _size({key: value for key, value in current.items() if key not in _KEYS})),
    )
    if budget < 80:
        return {}
    result = {"source_configuration": {}, "source_diagnostics": {}}
    sources = configuration.get("source_entities", [])
    remaining, truncated = [128], [False]
    # Reserve most of the budget for live sources. Summaries never recurse into
    # another virtual entity's diagnostics or copy transient camera credentials.
    for key, value in _bounded_items(configuration, remaining, truncated):
        if not detailed and key not in {"platform", "source_entities", "persistent"}:
            continue
        candidate = {**result["source_configuration"], key: _preview(value, remaining=remaining, truncated=truncated)}
        if _size(candidate) <= min(3000, budget // 3):
            result["source_configuration"] = candidate
        else:
            result["source_diagnostics_truncated"] = True
    for index, source in enumerate(sources):
        if index >= 32 or remaining[0] <= 0:
            truncated[0] = True
            break
        remaining[0] -= 1
        state = hass.states.get(source)
        communication = diagnostic_attributes(hass, source)
        if not detailed:
            communication = {key: communication[key] for key in _COMPACT_COMMUNICATION_KEYS if key in communication}
        record = {
            "source_entity_id": source,
            "source_entity_name": state.name if state else source,
            "source_available": state is not None and state.state not in {"unknown", "unavailable"},
            **{
                key: _preview(value, remaining=remaining, truncated=truncated)
                for key, value in _bounded_items(communication, remaining, truncated)
                if detailed or key in _COMPACT_COMMUNICATION_KEYS
            },
        }
        if detailed:
            record.update({
                "source_state": state.state if state else None,
                "source_last_updated": state.last_updated.isoformat() if state else None,
                "source_last_changed": state.last_changed.isoformat() if state else None,
            })
        # Only add a source if its useful identity/state/communication data fit.
        result["source_diagnostics"][source] = record
        if _size(result) > budget - 48:
            del result["source_diagnostics"][source]
            result["source_diagnostics_truncated"] = True
            continue
        if not detailed:
            continue
        record["source_attributes"] = {}
        for key, value in _bounded_items(state.attributes if state else {}, remaining, truncated):
            if key in _EXCLUDED:
                continue
            record["source_attributes"][key] = _preview(value, remaining=remaining, truncated=truncated)
            # Bound each source too, so a noisy first source cannot hide others.
            if _size(record["source_attributes"]) > min(1500, budget // max(1, len(sources) * 2)) or _size(result) > budget - 48:
                del record["source_attributes"][key]
                result["source_diagnostics_truncated"] = True
    if truncated[0]:
        result["source_diagnostics_truncated"] = True
    return result


@callback
def async_setup(hass, config, update):
    configuration = config.get(CONF_SOURCE_DIAGNOSTICS)
    if not isinstance(configuration, Mapping):
        return lambda: None
    sources = set(configuration.get("source_entities", [])) - {config.get(ATTR_ENTITY_ID)}
    @callback
    def changed(event):
        if not meaningful_change(event):
            # Prevent A -> B -> A chains from feeding back diagnostic timestamps.
            return
        update()

    removers = [
        async_watch_sources(hass, sources),
        async_track_state_change_event(hass, sources, changed),
        async_dispatcher_connect(hass, DIAGNOSTICS_UPDATED, update),
    ] if sources else []
    interval = config.get(CONF_DIAGNOSTIC_INTERVAL, DEFAULT_DIAGNOSTIC_INTERVAL)

    @callback
    def refresh(_now):
        update()

    if interval:
        removers.append(async_track_time_interval(
            hass, refresh, timedelta(seconds=interval),
        ))
    value = config.get(CONF_DIAGNOSTIC_DETAILS_UNTIL)
    deadline = _parse_deadline(value)
    if deadline and deadline > dt_util.utcnow():
        removers.append(async_track_point_in_utc_time(
            hass, refresh, deadline,
        ))

    @callback
    def remove():
        while removers:
            removers.pop()()

    return remove
