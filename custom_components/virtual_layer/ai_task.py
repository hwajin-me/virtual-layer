import voluptuous as vol
from homeassistant.helpers.config_validation import PLATFORM_SCHEMA

from .const import COMPONENT_DOMAIN
from .generic import GENERIC_SCHEMA, async_setup_generic_entry, async_setup_generic_platform

PLATFORM_DOMAIN = "ai_task"
DEPENDENCIES = [COMPONENT_DOMAIN]
PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(GENERIC_SCHEMA)
ENTITY_SCHEMA = vol.Schema(GENERIC_SCHEMA)


async def async_setup_platform(hass, config, async_add_entities, _discovery_info=None):
    await async_setup_generic_platform(hass, config, async_add_entities, PLATFORM_DOMAIN)


async def async_setup_entry(hass, entry, async_add_entities):
    await async_setup_generic_entry(hass, entry, async_add_entities, PLATFORM_DOMAIN, ENTITY_SCHEMA)
