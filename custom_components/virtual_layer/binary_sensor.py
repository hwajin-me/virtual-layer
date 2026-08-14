"""
This component provides support for a virtual binary sensor.

"""

import logging
from collections.abc import Callable

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components.binary_sensor import DOMAIN as PLATFORM_DOMAIN
from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_DEVICE_CLASS, ATTR_ENTITY_ID, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.config_validation import PLATFORM_SCHEMA

from . import (
    _assert_managed_virtual_entities,
    _async_verify_target_entity_control,
    get_entity_configs,
    get_entity_from_domain,
)
from .const import *
from .entity import VirtualEntity, virtual_schema

_LOGGER = logging.getLogger(__name__)

DEPENDENCIES = [COMPONENT_DOMAIN]

DEFAULT_BINARY_SENSOR_VALUE = "off"

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(virtual_schema(DEFAULT_BINARY_SENSOR_VALUE, {
    vol.Optional(CONF_CLASS): cv.string,
}))
BINARY_SENSOR_SCHEMA = vol.Schema(virtual_schema(DEFAULT_BINARY_SENSOR_VALUE, {
    vol.Optional(CONF_CLASS): cv.string,
}))

SERVICE_ON = "turn_on"
SERVICE_OFF = "turn_off"
SERVICE_TOGGLE = "toggle"
SERVICE_SCHEMA = vol.Schema({
    vol.Required(ATTR_ENTITY_ID): cv.comp_entity_ids,
})


def setup_services(hass: HomeAssistant) -> None:

    async def async_virtual_service(call):
        """Call virtual service handler."""
        await _async_verify_target_entity_control(hass, call)
        _LOGGER.debug(f"{call.service} service called")
        if call.service == SERVICE_ON:
            await async_virtual_on_service(hass, call)
        if call.service == SERVICE_OFF:
            await async_virtual_off_service(hass, call)
        if call.service == SERVICE_TOGGLE:
            await async_virtual_toggle_service(hass, call)

    # Build up services...
    if PLATFORM_DOMAIN not in hass.data[COMPONENT_SERVICES]:
        _LOGGER.debug("installing binary_service handlers")
        hass.data[COMPONENT_SERVICES][PLATFORM_DOMAIN] = 'installed'
        hass.services.async_register(
            COMPONENT_DOMAIN, SERVICE_ON, async_virtual_service, schema=SERVICE_SCHEMA,
        )
        hass.services.async_register(
            COMPONENT_DOMAIN, SERVICE_OFF, async_virtual_service, schema=SERVICE_SCHEMA,
        )
        hass.services.async_register(
            COMPONENT_DOMAIN, SERVICE_TOGGLE, async_virtual_service, schema=SERVICE_SCHEMA,
        )


async def async_setup_platform(hass, config, async_add_entities, _discovery_info=None):
    """Ignore platform setup; Virtual Layer entities are config-entry only."""
    _LOGGER.debug("ignoring platform setup")


async def async_setup_entry(
        hass: HomeAssistant,
        entry: ConfigEntry,
        async_add_entities: Callable[[list], None],
) -> None:
    _LOGGER.debug("setting up the entries...")

    entities = []
    for entity in get_entity_configs(hass, entry.data[ATTR_GROUP_NAME], PLATFORM_DOMAIN):
        entity = BINARY_SENSOR_SCHEMA(entity)
        entities.append(VirtualBinarySensor(entity, False))
    async_add_entities(entities)
    setup_services(hass)


class VirtualBinarySensor(VirtualEntity, BinarySensorEntity):
    """An implementation of a Virtual Binary Sensor."""

    def __init__(self, config, old_style: bool):
        """Initialize a Virtual Binary Sensor."""
        super().__init__(config, PLATFORM_DOMAIN, old_style)

        self._attr_device_class = config.get(CONF_CLASS)

        _LOGGER.debug(f"VirtualBinarySensor: {self.name} created")

    def _create_state(self, config):
        super()._create_state(config)

        self._attr_is_on = config.get(CONF_INITIAL_VALUE).lower() == STATE_ON

    def _restore_state(self, state, config):
        super()._restore_state(state, config)
        restored = self._restored_state_value(state, config)
        self._attr_is_on = str(restored).lower() == STATE_ON

    def _update_attributes(self):
        super()._update_attributes()
        self._attr_extra_state_attributes.update({
            name: value for name, value in (
                (ATTR_DEVICE_CLASS, self._attr_device_class),
            ) if value is not None
        })

    def turn_on(self) -> None:
        _LOGGER.debug(f"turning {self.name} on")
        self._attr_is_on = True
        self.async_schedule_update_ha_state()

    def turn_off(self) -> None:
        _LOGGER.debug(f"turning {self.name} off")
        self._attr_is_on = False
        self.async_schedule_update_ha_state()

    def toggle(self) -> None:
        if self.is_on:
            self.turn_off()
        else:
            self.turn_on()

    def set_state(self, value) -> None:
        if self._template_to_bool(value):
            self.turn_on()
        else:
            self.turn_off()

    def _apply_native_template_value(self, name: str, value) -> bool:
        if name == "device_class":
            try:
                value = None if value is None or value == "" else BinarySensorDeviceClass(value)
            except ValueError as err:
                raise ValueError(f"Invalid binary sensor device class: {value}") from err
        return super()._apply_native_template_value(name, value)


async def async_virtual_on_service(hass, call):
    entity_ids = call.data['entity_id']
    _assert_managed_virtual_entities(hass, entity_ids)
    for entity_id in entity_ids:
        _LOGGER.debug(f"turning on {entity_id}")
        get_entity_from_domain(hass, PLATFORM_DOMAIN, entity_id).turn_on()


async def async_virtual_off_service(hass, call):
    entity_ids = call.data['entity_id']
    _assert_managed_virtual_entities(hass, entity_ids)
    for entity_id in entity_ids:
        _LOGGER.debug(f"turning off {entity_id}")
        get_entity_from_domain(hass, PLATFORM_DOMAIN, entity_id).turn_off()


async def async_virtual_toggle_service(hass, call):
    entity_ids = call.data['entity_id']
    _assert_managed_virtual_entities(hass, entity_ids)
    for entity_id in entity_ids:
        _LOGGER.debug(f"toggling {entity_id}")
        get_entity_from_domain(hass, PLATFORM_DOMAIN, entity_id).toggle()
