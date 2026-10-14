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
    "zigbee_mqtt_connected", "zigbee_linkquality", "zigbee_battery", "zigbee_last_seen",
}


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


def _preview(value, depth=0):
    if depth >= 5:
        return "…"
    if isinstance(value, str):
        return value[:1024] + ("…" if len(value) > 1024 else "")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        try:
            return value if math.isfinite(value) else None
        except OverflowError:
            return None
    if isinstance(value, Mapping):
        return {str(key): _preview(item, depth + 1)
                for key, item in list(value.items())[:32] if key not in _EXCLUDED}
    if isinstance(value, (list, tuple)):
        return [_preview(item, depth + 1) for item in value[:32]]
    return str(value)[:1024]


def _size(value):
    return len(json.dumps(value, default=str, ensure_ascii=False).encode())


def fit_snapshot(hass, config, current):
    """Rebudget a cached snapshot after ordinary attributes grow."""
    if current.keys() & _KEYS and _size(current) > 12000:
        for key in _KEYS:
            current.pop(key, None)
        current.update(attributes(hass, config, current))


def attributes(hass, config, current):
    """Keep combined diagnostics within the parent's remaining recorder budget."""
    configuration = config.get(CONF_SOURCE_DIAGNOSTICS)
    if not isinstance(configuration, Mapping):
        return {}
    budget = max(0, 12000 - _size({key: value for key, value in current.items() if key not in _KEYS}))
    if budget < 80:
        return {}
    result = {"source_configuration": {}, "source_diagnostics": {}}
    sources = configuration.get("source_entities", [])
    detailed = details_enabled(config)
    # Reserve most of the budget for live sources. Summaries never recurse into
    # another virtual entity's diagnostics or copy transient camera credentials.
    for key, value in configuration.items():
        if not detailed and key not in {"platform", "source_entities", "persistent"}:
            continue
        candidate = {**result["source_configuration"], key: _preview(value)}
        if _size(candidate) <= min(3000, budget // 3):
            result["source_configuration"] = candidate
        else:
            result["source_diagnostics_truncated"] = True
    for source in sources:
        state = hass.states.get(source)
        record = {
            "source_entity_id": source,
            "source_entity_name": state.name if state else source,
            "source_state": state.state if state else None,
            **{
                key: value for key, value in diagnostic_attributes(hass, source).items()
                if detailed or key in _COMPACT_COMMUNICATION_KEYS
            },
        }
        if detailed:
            record.update({
                "source_last_updated": state.last_updated.isoformat() if state else None,
                "source_last_changed": state.last_changed.isoformat() if state else None,
            })
        # Only add a source if its useful identity/state/communication data fit.
        result["source_diagnostics"][source] = record
        if _size(result) > budget:
            del result["source_diagnostics"][source]
            result["source_diagnostics_truncated"] = True
            continue
        if not detailed:
            continue
        record["source_attributes"] = {}
        for key, value in (state.attributes.items() if state else []):
            if key in _EXCLUDED:
                continue
            record["source_attributes"][key] = _preview(value)
            # Bound each source too, so a noisy first source cannot hide others.
            if _size(record["source_attributes"]) > min(1500, budget // max(1, len(sources) * 2)) or _size(result) > budget:
                del record["source_attributes"][key]
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
