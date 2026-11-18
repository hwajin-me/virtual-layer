"""Bridge entity-ID and registry-ID indexed Home Assistant restore stores."""

from homeassistant.helpers import entity_registry as er


def get_stored_state(restore_data, entity_id):
    """Use HA's registry-aware lookup when available, retaining older HA support."""
    if not entity_id:
        return None
    getter = getattr(restore_data, "async_get_stored_state", None)
    if getter is not None:
        return getter(entity_id)
    return restore_data.last_states.get(entity_id)


def discard_stored_state(hass, restore_data, entity_id):
    """Forget a nonpersistent entity without clearing other restore snapshots."""
    legacy_states = getattr(restore_data, "last_states", None)
    if legacy_states is not None:
        legacy_states.pop(entity_id, None)
    states_by_id = getattr(restore_data, "last_states_by_entity_id", None)
    if states_by_id is not None:
        states_by_id.pop(entity_id, None)
    states_by_registry_id = getattr(
        restore_data, "last_states_by_entity_registry_id", None
    )
    if states_by_registry_id is not None:
        registry_entry = er.async_get(hass).async_get(entity_id)
        if registry_entry is not None:
            states_by_registry_id.pop(registry_entry.id, None)
