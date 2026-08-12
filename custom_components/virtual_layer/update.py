import voluptuous as vol
from homeassistant.helpers.config_validation import PLATFORM_SCHEMA

from .const import COMPONENT_DOMAIN
from .generic import (
    GENERIC_SCHEMA,
    VirtualUpdate,
    async_setup_generic_entry,
    async_setup_generic_platform,
)

PLATFORM_DOMAIN = "update"
DEPENDENCIES = [COMPONENT_DOMAIN]
PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(GENERIC_SCHEMA)
ENTITY_SCHEMA = vol.Schema(GENERIC_SCHEMA, extra=vol.ALLOW_EXTRA)
ENTITY_CLASS = VirtualUpdate


def validate_domain_options(config) -> None:
    """Reject invalid update progress while it is still editable in the UI."""
    if (value := config.get("update_percentage")) is not None:
        VirtualUpdate._bounded_update_percentage(value)


async def async_setup_platform(hass, config, async_add_entities, _discovery_info=None):
    await async_setup_generic_platform(hass, config, async_add_entities, PLATFORM_DOMAIN)


async def async_setup_entry(hass, entry, async_add_entities):
    await async_setup_generic_entry(
        hass, entry, async_add_entities, PLATFORM_DOMAIN, ENTITY_SCHEMA, ENTITY_CLASS
    )
