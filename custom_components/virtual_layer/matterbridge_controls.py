"""Manage the opt-in label used by Matterbridge's media command switches."""

from collections.abc import Mapping

from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er, label_registry as lr

from .const import COMPONENT_DOMAIN

CONF_MATTERBRIDGE_CONTROL_LABEL = "matterbridge_control_label"
CONF_MATTERBRIDGE_CONTROLS_ENABLED = "matterbridge_controls_enabled"
DEFAULT_CONTROL_LABEL = "matterbridge-virtual"
_MANAGED_LABEL = "matterbridge_managed_label"


def control_label(value) -> str:
    """Validate UI input without accepting malformed stored values as names."""
    if not isinstance(value, str) or len(value) > 255:
        raise ValueError("Control label must be text of at most 255 characters")
    value = value.strip()
    if any(ord(char) < 32 for char in value):
        raise ValueError("Control label must not contain control characters")
    return value


@callback
def async_sync_control_label(hass, entity_id: str, config: Mapping) -> None:
    """Only remove label assignments this integration originally added."""
    name = ""
    if config.get(CONF_MATTERBRIDGE_CONTROLS_ENABLED) is True:
        try:
            name = control_label(
                config.get(CONF_MATTERBRIDGE_CONTROL_LABEL, DEFAULT_CONTROL_LABEL)
            ) or DEFAULT_CONTROL_LABEL
        except ValueError:
            # A damaged optional setting must never prevent loading the player.
            return
    registry = er.async_get(hass)
    entry = registry.async_get(entity_id)
    if entry is None or entry.platform != COMPONENT_DOMAIN:
        return
    options = dict(entry.options.get(COMPONENT_DOMAIN, {}))
    previous = options.get(_MANAGED_LABEL)
    labels = set(entry.labels)
    desired = None
    if name:
        label_registry = lr.async_get(hass)
        label = label_registry.async_get_label_by_name(name)
        if label is None:
            label = label_registry.async_create(name, icon="mdi:remote")
        desired = label.label_id

    if isinstance(previous, str) and previous != desired:
        labels.discard(previous)
    options.pop(_MANAGED_LABEL, None)
    if desired is not None:
        # Do not claim ownership of an existing user-assigned label.
        if desired not in labels or previous == desired:
            options[_MANAGED_LABEL] = desired
        labels.add(desired)
    if labels != entry.labels:
        registry.async_update_entity(entity_id, labels=labels)
    if options != dict(entry.options.get(COMPONENT_DOMAIN, {})):
        registry.async_update_entity_options(
            entity_id, COMPONENT_DOMAIN, options or None,
        )
