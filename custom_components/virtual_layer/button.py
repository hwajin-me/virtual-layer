import voluptuous as vol
from homeassistant.helpers.config_validation import PLATFORM_SCHEMA

from .const import COMPONENT_DOMAIN, CONF_ATTRIBUTES, CONF_DEBUG_TARGET, ATTR_UNIQUE_ID
from .debug import DEBUG_SUFFIX, DebugButton, fusion_buttons
from .generic import (
    GENERIC_SCHEMA,
    VirtualButton,
    async_setup_generic_entry,
    async_setup_generic_platform,
)

PLATFORM_DOMAIN = "button"
DEPENDENCIES = [COMPONENT_DOMAIN]
PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(GENERIC_SCHEMA)
ENTITY_SCHEMA = vol.Schema(GENERIC_SCHEMA, extra=vol.ALLOW_EXTRA)
ENTITY_CLASS = VirtualButton


async def async_setup_platform(hass, config, async_add_entities, _discovery_info=None):
    await async_setup_generic_platform(hass, config, async_add_entities, PLATFORM_DOMAIN)


async def async_setup_entry(hass, entry, async_add_entities):
    if entry.data.get("presence_fusion"):
        async_add_entities(fusion_buttons(hass, entry))
        return

    def button_entity(config, old_style):
        if (isinstance(config.get(CONF_DEBUG_TARGET), str)
                and config.get(ATTR_UNIQUE_ID, "").endswith(DEBUG_SUFFIX)
                and isinstance(config.get(CONF_ATTRIBUTES, {}).get("virtual_entity_id"), str)):
            return DebugButton(entry, config)
        return VirtualButton(config, old_style)

    await async_setup_generic_entry(
        hass, entry, async_add_entities, PLATFORM_DOMAIN, ENTITY_SCHEMA, button_entity
    )
