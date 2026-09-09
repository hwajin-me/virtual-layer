"""Validation shared by device forms and runtime registry metadata."""

import voluptuous as vol
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .const import COMPONENT_DOMAIN


def valid_parent_device(hass, parent_id, device_id) -> bool:
    """Require an existing parent whose ancestry does not loop to this Device."""
    if not isinstance(parent_id, str) or not parent_id:
        return False
    registry = dr.async_get(hass)
    child = registry.async_get_device(identifiers={(COMPONENT_DOMAIN, device_id)})
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
