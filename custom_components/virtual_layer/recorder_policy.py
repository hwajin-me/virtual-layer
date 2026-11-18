"""Keep integration-owned diagnostics out of functional Recorder history."""

from collections.abc import Mapping

from homeassistant.core import callback

from .const import (
    ATTR_CONFIGURED_VIRTUAL_ATTRIBUTES,
    ATTR_DIAGNOSTIC_ATTRIBUTE_RETENTION,
    ATTR_RUNTIME_VIRTUAL_ATTRIBUTES,
    ATTR_VIRTUAL_ATTRIBUTES,
)

_BOOKKEEPING_ATTRIBUTES = frozenset({
    ATTR_CONFIGURED_VIRTUAL_ATTRIBUTES,
    ATTR_RUNTIME_VIRTUAL_ATTRIBUTES,
    ATTR_DIAGNOSTIC_ATTRIBUTE_RETENTION,
    ATTR_VIRTUAL_ATTRIBUTES,
    "source_entities",
})

# These names are reserved by Virtual Layer, so excluding them cannot remove
# an arbitrary user-defined functional attribute with a similar generic name.
RECORDER_EXCLUDED_ATTRIBUTES = _BOOKKEEPING_ATTRIBUTES | frozenset({
    "source_configuration", "source_diagnostics", "source_diagnostics_truncated",
})
DEBUG_RECORDER_EXCLUDED_ATTRIBUTES = RECORDER_EXCLUDED_ATTRIBUTES | frozenset({
    "debug_result", "debug_expires_at",
})
DIAGNOSTIC_SENSOR_EXCLUDED_ATTRIBUTES = RECORDER_EXCLUDED_ATTRIBUTES | frozenset({
    "source_state", "source_attributes", "source_last_updated", "source_last_changed",
    "zigbee_last_seen", "zigbee_last_seen_seconds_ago",
})
DIAGNOSTIC_SENSOR_CLOCK_ATTRIBUTES = frozenset({
    "source_last_updated", "source_last_changed", "zigbee_last_seen_seconds_ago",
})


def recording_attributes(attributes: Mapping | None) -> dict:
    """Return the functional attributes covered by the common recording policy."""
    return {
        key: value for key, value in (attributes or {}).items()
        if key not in RECORDER_EXCLUDED_ATTRIBUTES
    }


def diagnostic_refresh_needed(
    previous: Mapping | None, current: Mapping | None, *, ignored_attributes=frozenset()
) -> bool:
    """Ignore clock/bookkeeping ticks, retaining compact health/state changes."""
    ignored = _BOOKKEEPING_ATTRIBUTES | ignored_attributes
    return {
        key: value for key, value in (previous or {}).items()
        if key not in ignored
    } != {
        key: value for key, value in (current or {}).items()
        if key not in ignored
    }


@callback
def async_publish_state(hass, entity_id, new_state, attributes=None, **kwargs) -> None:
    """Publish a state-only entity with exclusions on every update path."""
    state_info = dict(kwargs.pop("state_info", None) or {})
    state_info["unrecorded_attributes"] = (
        frozenset(state_info.get("unrecorded_attributes", ()))
        | RECORDER_EXCLUDED_ATTRIBUTES
    )
    hass.states.async_set(
        entity_id, new_state, attributes, state_info=state_info, **kwargs
    )
