"""
Generic virtual entity support for building-block domains.

This module intentionally implements the smallest common surface: state,
availability, restore, device registration, extra attributes, and templates.
Domain-specific files can use this when Home Assistant exposes a building block
domain but the virtual layer does not need a rich service API yet.
"""

import logging
import voluptuous as vol

import homeassistant.helpers.config_validation as cv
from homeassistant.const import ATTR_DEVICE_CLASS, CONF_ICON
from homeassistant.helpers.entity import Entity

from . import get_entity_configs
from .const import *
from .entity import VirtualEntity, virtual_schema


_LOGGER = logging.getLogger(__name__)

CONF_STATE_CLASS = "state_class"

DEFAULT_GENERIC_VALUE = "unknown"

GENERIC_SCHEMA = virtual_schema(DEFAULT_GENERIC_VALUE, {
    vol.Optional(CONF_CLASS): cv.string,
    vol.Optional(CONF_ICON): cv.string,
    vol.Optional(CONF_STATE_CLASS): cv.string,
})
ENTITY_SCHEMA = vol.Schema(GENERIC_SCHEMA, extra=vol.ALLOW_EXTRA)


class GenericVirtualEntity(VirtualEntity, Entity):
    """Generic implementation for virtual building-block domains."""

    def __init__(self, config, domain: str, old_style: bool):
        super().__init__(config, domain, old_style)
        self._domain = domain
        self._attr_device_class = config.get(CONF_CLASS)
        self._attr_icon = config.get(CONF_ICON)
        self._attr_state_class = config.get(CONF_STATE_CLASS)
        self._domain_options = generic_entity_options(config)
        _LOGGER.info(f"GenericVirtualEntity: {self.name} ({domain}) created")

    @property
    def state(self):
        return self._attr_state

    def _create_state(self, config):
        super()._create_state(config)
        self._attr_state = config.get(CONF_INITIAL_VALUE)

    def _restore_state(self, state, config):
        super()._restore_state(state, config)
        self._attr_state = state.state

    def _update_attributes(self):
        super()._update_attributes()
        self._attr_extra_state_attributes.update({
            name: value for name, value in (
                (ATTR_DEVICE_CLASS, self._attr_device_class),
                (CONF_STATE_CLASS, self._attr_state_class),
            ) if value is not None
        })
        self._attr_extra_state_attributes.update(self._domain_options)

    def set_state(self, value) -> None:
        self._attr_state = value


async def async_setup_generic_platform(hass, config, async_add_entities, domain):
    """Ignore platform setup; Virtual Layer entities are config-entry only."""
    _LOGGER.debug("ignoring platform setup for generic %s", domain)


async def async_setup_generic_entry(hass, entry, async_add_entities, domain, schema):
    _LOGGER.debug(f"setting up generic entries for {domain}...")
    entities = [
        GenericVirtualEntity(schema(entity), domain, False)
        for entity in get_entity_configs(hass, entry.data[ATTR_GROUP_NAME], domain)
    ]
    async_add_entities(entities)
