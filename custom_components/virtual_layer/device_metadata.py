"""Validation shared by device forms and runtime registry metadata."""

import voluptuous as vol
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .const import COMPONENT_DOMAIN


def async_get_virtual_device(registry, device_id, config_entry_id=None):
    """Get a Virtual Layer device without assuming identifiers are global.

    Device identifiers became unique per config entry in Home Assistant 2026.8.
    Keep the legacy fallback while older supported Home Assistant versions lack
    the scoped lookup API.
    """
    identifier = (COMPONENT_DOMAIN, device_id)
    get_by_identifier = getattr(registry, "async_get_device_by_identifier", None)
    if get_by_identifier is not None and config_entry_id:
        return get_by_identifier(identifier, config_entry_id)

    get_devices = getattr(registry, "async_get_devices", None)
    if get_devices is not None:
        devices = get_devices(
            identifiers={identifier}, config_entry_id=config_entry_id
        )
        return devices[0] if devices else None

    return registry.async_get_device(identifiers={identifier})


def valid_parent_device(hass, parent_id, device_id) -> bool:
    """Require an existing parent whose ancestry does not loop to this Device."""
    if not isinstance(parent_id, str) or not parent_id:
        return False
    registry = dr.async_get(hass)
    child = async_get_virtual_device(registry, device_id)
    seen = {child.id} if child else set()
    while parent_id:
        if parent_id in seen:
            return False
        seen.add(parent_id)
        parent = registry.async_get(parent_id)
        if parent is None:
            return False
        parent_id = parent.via_device_id
    return True


def configuration_url_or_none(value) -> str | None:
    """Ignore malformed legacy URLs without preventing device/entity setup."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return cv.configuration_url(value.strip())
    except (vol.Invalid, ValueError, TypeError):
        return None
