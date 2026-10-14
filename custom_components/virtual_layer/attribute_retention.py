"""Expire only explicitly classified diagnostics; retain functional attributes."""

from collections.abc import Mapping
from datetime import timedelta

import voluptuous as vol
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_point_in_utc_time, async_track_state_change_event
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_DIAGNOSTIC_ATTRIBUTE_RETENTION, ATTR_ENTITY_ID, ATTR_RUNTIME_VIRTUAL_ATTRIBUTES,
    CONF_DIAGNOSTIC_ATTRIBUTES, CONF_EVENT_HOOKS, CONF_ATTRIBUTES,
    CONF_ATTRIBUTE_TEMPLATES, EXCLUDED_VIRTUAL_ATTRIBUTE_NAMES,
)
from .source_diagnostics import _KEYS, _parse_deadline, _size

MAX_DIAGNOSTIC_AGE = timedelta(days=7)
MAX_DIAGNOSTIC_ITEMS = 32
MAX_DIAGNOSTIC_BYTES = 3000


def diagnostic_names(value):
    """Validate an explicit list, never guess from words in an attribute name."""
    if isinstance(value, str):
        value = value.replace(",", "\n").splitlines()
    if not isinstance(value, (list, tuple)):
        raise vol.Invalid("diagnostic attributes must be a list of names")
    result = []
    for name in value:
        if not isinstance(name, str) or len(name.strip()) > 128:
            raise vol.Invalid("invalid diagnostic attribute name")
        name = name.strip()
        if name and name not in result:
            if name in EXCLUDED_VIRTUAL_ATTRIBUTE_NAMES:
                raise vol.Invalid("integration metadata cannot be selected")
            result.append(name)
    if len(result) > MAX_DIAGNOSTIC_ITEMS:
        raise vol.Invalid("at most 32 diagnostic attributes may be selected")
    return result


def hook_attribute_names(config):
    """Hook outputs are configured behavior, even before the first hook fires."""
    names = set()
    for hook in config.get(CONF_EVENT_HOOKS, []):
        if not isinstance(hook, Mapping):
            continue
        for key in (CONF_ATTRIBUTES, CONF_ATTRIBUTE_TEMPLATES):
            values = hook.get(key)
            if isinstance(values, Mapping):
                names.update(values)
    return names


def _preview(value, remaining=None, depth=0):
    """Bound diagnostic work too, including highly branching nested payloads."""
    if remaining is None:
        remaining = [96]
    remaining[0] -= 1
    if remaining[0] < 0 or depth >= 5:
        return "…"
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            if len(result) >= MAX_DIAGNOSTIC_ITEMS or remaining[0] <= 0:
                break
            result[str(key)[:128]] = _preview(item, remaining, depth + 1)
            if _size(result) > 1500:
                result.pop(str(key)[:128], None)
        return result
    if isinstance(value, (list, tuple)):
        # Recent diagnostic samples are more useful than the oldest samples.
        result = []
        for item in value[-MAX_DIAGNOSTIC_ITEMS:]:
            if remaining[0] <= 0:
                break
            result.append(_preview(item, remaining, depth + 1))
            while _size(result) > 1500 and result:
                result.pop(0)
        return result
    if value is None or isinstance(value, (bool, int, float)):
        from math import isfinite

        try:
            return value if value is None or isfinite(value) else None
        except OverflowError:
            return None
    return str(value)[:1024]


def prune(config, attributes, protected=(), *, clear=False, max_age=None):
    """Mutate a snapshot, bounding/expiring only designated service attributes.

    Persist the first publication's deadline: updates and reloads never renew
    the window. Ordinary runtime values remain untouched.
    """
    try:
        selected = set(diagnostic_names(config.get(CONF_DIAGNOSTIC_ATTRIBUTES, [])))
    except vol.Invalid:
        selected = set()
    tracked = attributes.get(ATTR_RUNTIME_VIRTUAL_ATTRIBUTES, [])
    tracked = {name for name in tracked if isinstance(name, str)} if isinstance(tracked, (list, tuple)) else set()
    tracked.intersection_update(attributes)
    selected &= tracked
    selected -= set(protected) | hook_attribute_names(config) | EXCLUDED_VIRTUAL_ATTRIBUTE_NAMES
    stored = attributes.pop(ATTR_DIAGNOSTIC_ATTRIBUTE_RETENTION, {})
    stored = stored if isinstance(stored, Mapping) else {}
    metadata = {}
    now = dt_util.utcnow()
    used = budget = 0
    if selected:
        used = _size({ATTR_DIAGNOSTIC_ATTRIBUTE_RETENTION: {}})
        budget = min(MAX_DIAGNOSTIC_BYTES, max(0, 12000 - _size({
            key: value for key, value in attributes.items() if key not in selected and key not in _KEYS
        })))
    for name in sorted(selected):
        if name not in attributes:
            continue
        value = _preview(attributes[name])
        # A noisy diagnostic never consumes the budget of functional fields.
        while _size(value) > 1500 and isinstance(value, (dict, list)) and value:
            if isinstance(value, dict):
                value.pop(next(reversed(value)))
            else:
                value.pop(0)
        record = stored.get(name)
        record = record if isinstance(record, Mapping) else {}
        deadline = _parse_deadline(record.get("expires"))
        created = _parse_deadline(record.get("created"))
        if created is None:
            try:
                created = deadline - MAX_DIAGNOSTIC_AGE if deadline else now
            except OverflowError:
                created = deadline
        created = min(created, now)
        if deadline is None:
            deadline = created + MAX_DIAGNOSTIC_AGE
        deadline = min(deadline, created + (max_age or MAX_DIAGNOSTIC_AGE))
        next_record = {"created": created.isoformat(), "expires": deadline.isoformat()}
        cost = _size({name: value}) + _size({name: next_record}) + _size(name)
        if clear or deadline <= now or used + cost > budget:
            attributes.pop(name, None)
            tracked.discard(name)
            continue
        attributes[name] = value
        metadata[name] = next_record
        used += cost
    if metadata:
        attributes[ATTR_DIAGNOSTIC_ATTRIBUTE_RETENTION] = metadata
    if tracked:
        attributes[ATTR_RUNTIME_VIRTUAL_ATTRIBUTES] = sorted(tracked)
    else:
        attributes.pop(ATTR_RUNTIME_VIRTUAL_ATTRIBUTES, None)


@callback
def async_setup(hass, config, cleanup):
    """Schedule the next actual expiry and rearm when service values change."""
    entity_id = config.get(ATTR_ENTITY_ID)
    if not entity_id or not config.get(CONF_DIAGNOSTIC_ATTRIBUTES):
        return lambda: None
    cancel = None
    active = True

    @callback
    def rearm(_event=None):
        nonlocal cancel
        if cancel:
            cancel()
            cancel = None
        state = hass.states.get(entity_id)
        metadata = state.attributes.get(ATTR_DIAGNOSTIC_ATTRIBUTE_RETENTION, {}) if state else {}
        if not active or not isinstance(metadata, Mapping):
            return
        deadlines = [deadline for record in metadata.values() if isinstance(record, Mapping)
                     if (deadline := _parse_deadline(record.get("expires"))) is not None]
        if deadlines:
            cancel = async_track_point_in_utc_time(hass, expire, min(deadlines))

    @callback
    def expire(_now):
        nonlocal cancel
        cancel = None
        if active:
            cleanup()
            rearm()

    remove = async_track_state_change_event(hass, [entity_id], rearm)
    rearm()

    @callback
    def stop():
        nonlocal active
        active = False
        if cancel:
            cancel()
        remove()

    return stop
