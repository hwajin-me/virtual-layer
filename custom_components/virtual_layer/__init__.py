"""
This component provides support for virtual components.

"""

from collections.abc import Mapping
from datetime import timedelta
import logging
import voluptuous as vol

import homeassistant.helpers.config_validation as cv
import homeassistant.helpers.device_registry as dr
from homeassistant.helpers.entity import async_generate_entity_id
import homeassistant.helpers.entity_registry as er
from homeassistant.helpers.event import (
    TrackTemplate,
    async_track_state_change_event,
    async_track_template_result,
    async_track_time_interval,
)
from homeassistant.helpers.config_validation import PLATFORM_SCHEMA
from homeassistant.helpers.service import verify_domain_control
from homeassistant.helpers.typing import ConfigType
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import (
    HomeAssistant,
    callback
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.template import Template

from .const import *
from .cfg import (
    BlendedCfg,
    _delete_meta_data,
    async_build_entry_backup,
    async_load_backup,
    async_save_backup,
    clone_entities_with_new_keys,
)


__version__ = '1.0.0'

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = vol.Schema({
        COMPONENT_DOMAIN: vol.Schema({}),
    },
    extra=vol.ALLOW_EXTRA,
)

SERVICE_AVAILABILE = 'set_available'
SERVICE_SCHEMA = vol.Schema({
    vol.Required(ATTR_ENTITY_ID): cv.comp_entity_ids,
    vol.Required('value'): cv.boolean,
})
SERVICE_SET_STATE = "set_state"
SERVICE_SET_ATTRIBUTES = "set_attributes"
SERVICE_CLEAR_ATTRIBUTES = "clear_attributes"
SERVICE_BACKUP_DEVICES = "backup_devices"
SERVICE_RESTORE_DEVICES = "restore_devices"
SERVICE_SET_STATE_SCHEMA = vol.Schema({
    vol.Required(ATTR_ENTITY_ID): cv.comp_entity_ids,
    vol.Required(ATTR_VALUE): vol.Any(cv.string, int, float, bool),
})
SERVICE_SET_ATTRIBUTES_SCHEMA = vol.Schema({
    vol.Required(ATTR_ENTITY_ID): cv.comp_entity_ids,
    vol.Required(ATTR_ATTRIBUTES): dict,
})
SERVICE_CLEAR_ATTRIBUTES_SCHEMA = vol.Schema({
    vol.Required(ATTR_ENTITY_ID): cv.comp_entity_ids,
    vol.Optional(ATTR_ATTRIBUTES, default=list): vol.All(cv.ensure_list, [cv.string]),
})
SERVICE_BACKUP_DEVICES_SCHEMA = vol.Schema({
    vol.Required(ATTR_FILE_NAME): cv.string,
    vol.Optional(ATTR_GROUP_NAME): cv.string,
})
SERVICE_RESTORE_MODE_MERGE = "merge"
SERVICE_RESTORE_MODE_REPLACE = "replace"
SERVICE_RESTORE_DEVICES_SCHEMA = vol.Schema({
    vol.Required(ATTR_FILE_NAME): cv.string,
    vol.Optional(ATTR_GROUP_NAME): cv.string,
    vol.Optional("mode", default=SERVICE_RESTORE_MODE_MERGE): vol.In([
        SERVICE_RESTORE_MODE_MERGE,
        SERVICE_RESTORE_MODE_REPLACE,
    ]),
})

VIRTUAL_PLATFORMS = VIRTUAL_ENTITY_DOMAINS
_STATE_ONLY_TEMPLATE_LISTENERS_DATA = f"{COMPONENT_DOMAIN}_state_only_template_listeners"


def _entry_platforms_from_entities(entities):
    if not isinstance(entities, Mapping):
        return []
    return [
        platform
        for platform in VIRTUAL_PLATFORMS
        if platform not in STATE_ONLY_ENTITY_DOMAINS and entities.get(platform)
    ]


def str_to_bool(value) -> bool:
    value = value.lower()
    if value in ["y", "yes", "t", "true", "on", "1"]:
        return True
    if value in ["n", "no", "f", "false", "off", "0"]:
        return False
    raise ValueError


async def async_setup(hass, config):
    """Set up Virtual Layer."""

    # Set up hass data if necessary
    if COMPONENT_DOMAIN not in hass.data:
        hass.data[COMPONENT_DOMAIN] = {}
        hass.data[COMPONENT_SERVICES] = {}

    _async_register_virtual_services(hass)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    _LOGGER.debug(f'async setup {entry.data}')

    # Set up hass data if necessary
    if COMPONENT_DOMAIN not in hass.data:
        hass.data[COMPONENT_DOMAIN] = {}
        hass.data[COMPONENT_SERVICES] = {}

    # Get the config.
    _LOGGER.debug(f"creating new cfg")
    vcfg = BlendedCfg(hass, entry.data, entry.options, entry)
    await vcfg.async_load()

    # create the devices.
    _LOGGER.debug("creating the devices")
    for device in vcfg.devices:
        _LOGGER.debug(f"creating-device={device}")
        await _async_get_or_create_virtual_device_in_registry(hass, entry, device)

    # Delete orphaned devices.
    active_device_ids = {
        device[ATTR_DEVICE_ID]
        for device in vcfg.devices
    }
    for switch, device in vcfg.orphaned_entities.items():
        _LOGGER.debug(f"deleting {switch}/{device}")
        await _async_delete_virtual_entity_from_registry(hass, entry, device, active_device_ids)

    # Update the component data.
    hass.data[COMPONENT_DOMAIN].update({
        entry.data[ATTR_GROUP_NAME]: {
            ATTR_ENTITIES: vcfg.entities,
            ATTR_DEVICES: vcfg.devices,
        }
    })
    _LOGGER.debug(f"update hass data {hass.data[COMPONENT_DOMAIN]}")
    _async_setup_state_only_entities(hass, entry, vcfg.entities)

    # Create the entities.
    _LOGGER.debug("creating the entities")
    platforms = _entry_platforms_from_entities(vcfg.entities)
    if platforms:
        await hass.config_entries.async_forward_entry_setups(entry, platforms)

    # Install service handlers.
    _async_register_virtual_services(hass)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.debug(f"unloading virtual group {entry.data[ATTR_GROUP_NAME]}")
    # _LOGGER.debug(f"before hass={hass.data[COMPONENT_DOMAIN]}")
    group_data = hass.data.get(COMPONENT_DOMAIN, {}).get(entry.data[ATTR_GROUP_NAME], {})
    platforms = _entry_platforms_from_entities(group_data.get(ATTR_ENTITIES, {}))
    unload_ok = True
    if platforms:
        unload_ok = await hass.config_entries.async_unload_platforms(entry, platforms)
    if unload_ok:
        _LOGGER.debug("unloaded ok")
        _async_unload_state_only_entities(hass, entry, group_data.get(ATTR_ENTITIES, {}))
        hass.data[COMPONENT_DOMAIN].pop(entry.data[ATTR_GROUP_NAME], None)
        # _LOGGER.debug(f"ocfg={ocfg}")
    # _LOGGER.debug(f"after hass={hass.data[COMPONENT_DOMAIN]}")

    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clean up Virtual Layer data when a config entry is removed."""
    group_name = entry.data.get(ATTR_GROUP_NAME)
    _LOGGER.debug("removing virtual group %s", group_name)

    group_data = hass.data.get(COMPONENT_DOMAIN, {}).get(group_name, {})
    _async_unload_state_only_entities(hass, entry, group_data.get(ATTR_ENTITIES, {}))
    _async_remove_entry_registry_entries(hass, entry)

    if group_name:
        await _delete_meta_data(hass, group_name)
        hass.data.get(COMPONENT_DOMAIN, {}).pop(group_name, None)


@callback
def _async_remove_entry_registry_entries(hass: HomeAssistant, entry: ConfigEntry) -> None:
    entity_registry = er.async_get(hass)
    for entity_entry in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        entity_registry.async_remove(entity_entry.entity_id)

    device_registry = dr.async_get(hass)
    for device_entry in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        if len(device_entry.config_entries) <= 1:
            device_registry.async_remove_device(device_entry.id)
    device_registry.async_clear_config_entry(entry.entry_id)


def get_entity_configs(hass, group_name, domain):
    return hass.data.get(COMPONENT_DOMAIN, {}).get(group_name, {}).get(ATTR_ENTITIES, {}).get(domain, [])


def get_entity_from_domain(hass, domain, entity_id):
    component = hass.data.get(domain)
    if component is None:
        raise HomeAssistantError("{} component not set up".format(domain))

    entity = component.get_entity(entity_id)
    if entity is None:
        raise HomeAssistantError("{} not found".format(entity_id))

    return entity


def _state_only_attributes(entity):
    attributes = {
        ATTR_PERSISTENT: entity.get(CONF_PERSISTENT),
        ATTR_AVAILABLE: entity.get(CONF_INITIAL_AVAILABILITY, DEFAULT_AVAILABILITY),
    }
    configured_attributes = entity.get(CONF_ATTRIBUTES, {})
    if isinstance(configured_attributes, Mapping):
        attributes.update({
            name: value
            for name, value in configured_attributes.items()
            if name not in RESERVED_VIRTUAL_ATTRIBUTE_NAMES
        })
    attributes.update(generic_entity_options(entity))
    return attributes


@callback
def _async_setup_state_only_entities(hass, entry, entities) -> None:
    if not isinstance(entities, Mapping):
        return
    _async_remove_state_only_template_listeners(hass, entry.entry_id)
    for domain in STATE_ONLY_ENTITY_DOMAINS:
        for entity in entities.get(domain, []):
            if not isinstance(entity, Mapping):
                continue
            entity_id = _async_register_state_only_entity(hass, entry, entity)
            if entity_id is None:
                continue
            hass.states.async_set(
                entity_id,
                entity.get(CONF_INITIAL_VALUE, "unknown"),
                _state_only_attributes(entity),
            )
            _async_setup_state_only_templates(hass, entry, entity)


@callback
def _async_unload_state_only_entities(hass, entry, entities) -> None:
    _async_remove_state_only_template_listeners(hass, entry.entry_id)
    if not isinstance(entities, Mapping):
        return
    for domain in STATE_ONLY_ENTITY_DOMAINS:
        for entity in entities.get(domain, []):
            if isinstance(entity, Mapping) and entity.get(ATTR_ENTITY_ID):
                hass.states.async_remove(entity[ATTR_ENTITY_ID])


@callback
def _async_remove_state_only_template_listeners(hass, entry_id) -> None:
    entry_listeners = hass.data.get(_STATE_ONLY_TEMPLATE_LISTENERS_DATA)
    listeners = entry_listeners.pop(entry_id, []) if entry_listeners else []
    for remove_listener in listeners:
        remove_listener()
    if entry_listeners == {}:
        hass.data.pop(_STATE_ONLY_TEMPLATE_LISTENERS_DATA, None)


def _state_only_template_variables(hass, entity) -> dict:
    variables = {}
    template_sources = entity.get(CONF_TEMPLATE_SOURCES, {})
    if not isinstance(template_sources, Mapping):
        return variables
    for name, source in template_sources.items():
        if not isinstance(source, Mapping):
            continue
        entity_id = source.get(ATTR_ENTITY_ID)
        attribute = source.get(CONF_ATTRIBUTE, "state")
        state = hass.states.get(entity_id) if entity_id else None
        if state is None:
            variables[name] = None
        elif attribute == "state":
            variables[name] = state.state
        else:
            variables[name] = state.attributes.get(attribute)
    return variables


def _render_state_only_template(hass, entity, template):
    return Template(str(template), hass).async_render(
        variables=_state_only_template_variables(hass, entity),
        parse_result=False,
    )


@callback
def _async_apply_state_only_templates(hass, entity) -> None:
    entity_id = entity.get(ATTR_ENTITY_ID)
    state = hass.states.get(entity_id) if entity_id else None
    if state is None:
        return

    value = state.state
    attributes = dict(state.attributes)
    domain_options = generic_entity_options(entity)
    changed = False
    try:
        for name, configured_value in domain_options.items():
            changed = attributes.get(name) != configured_value or changed
            attributes[name] = configured_value

        if entity.get(CONF_VALUE_TEMPLATE):
            value = _render_state_only_template(hass, entity, entity[CONF_VALUE_TEMPLATE])
            changed = value != state.state or changed

        if entity.get(CONF_AVAILABILITY_TEMPLATE):
            available = str(
                _render_state_only_template(hass, entity, entity[CONF_AVAILABILITY_TEMPLATE])
            ).lower() in {"y", "yes", "t", "true", "on", "1"}
            changed = attributes.get(ATTR_AVAILABLE) != available or changed
            attributes[ATTR_AVAILABLE] = available

        attribute_templates = entity.get(CONF_ATTRIBUTE_TEMPLATES, {})
        if isinstance(attribute_templates, Mapping):
            for name, template in attribute_templates.items():
                if name in RESERVED_VIRTUAL_ATTRIBUTE_NAMES or name in domain_options:
                    continue
                rendered = _render_state_only_template(hass, entity, template)
                changed = attributes.get(name) != rendered or changed
                attributes[name] = rendered

        attribute_sources = entity.get(CONF_ATTRIBUTE_SOURCES, {})
        if isinstance(attribute_sources, Mapping):
            for name, source in attribute_sources.items():
                if name in RESERVED_VIRTUAL_ATTRIBUTE_NAMES or name in domain_options:
                    continue
                if not isinstance(source, Mapping):
                    continue
                source_entity_id = source.get(ATTR_ENTITY_ID)
                source_state = hass.states.get(source_entity_id) if source_entity_id else None
                attribute = source.get(CONF_ATTRIBUTE)
                source_value = None if source_state is None else (
                    source_state.state if attribute == "state"
                    else source_state.attributes.get(attribute)
                )
                changed = attributes.get(name) != source_value or changed
                attributes[name] = source_value
    except Exception as err:
        _LOGGER.warning("Unable to update state-only virtual entity %s: %s", entity_id, err)
        return

    if changed:
        hass.states.async_set(entity_id, value, attributes)


@callback
def _async_setup_state_only_templates(hass, entry, entity) -> None:
    entity_id = entity.get(ATTR_ENTITY_ID)
    if not entity_id:
        return
    source_entities = set(entity.get(CONF_SOURCE_ENTITIES, []))
    for source_group in (CONF_ATTRIBUTE_SOURCES, CONF_TEMPLATE_SOURCES):
        sources = entity.get(source_group, {})
        if isinstance(sources, Mapping):
            source_entities.update(
                source.get(ATTR_ENTITY_ID)
                for source in sources.values()
                if isinstance(source, Mapping) and source.get(ATTR_ENTITY_ID)
            )
    source_entities.discard(entity_id)

    listeners = hass.data.setdefault(
        _STATE_ONLY_TEMPLATE_LISTENERS_DATA, {}
    ).setdefault(entry.entry_id, [])

    if source_entities:
        listeners.append(async_track_state_change_event(
            hass,
            source_entities,
            lambda _event: _async_apply_state_only_templates(hass, entity),
        ))

    templates = [
        template
        for template in (
            entity.get(CONF_VALUE_TEMPLATE),
            entity.get(CONF_AVAILABILITY_TEMPLATE),
            *dict(entity.get(CONF_ATTRIBUTE_TEMPLATES, {})).values(),
        )
        if template
    ]
    for template in templates:
        tracked_template = Template(str(template), hass)
        listeners.append(async_track_template_result(
            hass,
            [TrackTemplate(tracked_template, _state_only_template_variables(hass, entity))],
            lambda _event, _updates: _async_apply_state_only_templates(hass, entity),
        ).async_remove)

    pull_interval = entity.get(CONF_PULL_INTERVAL, 0)
    if pull_interval:
        listeners.append(async_track_time_interval(
            hass,
            lambda _now: _async_apply_state_only_templates(hass, entity),
            timedelta(seconds=pull_interval),
        ))
    _async_apply_state_only_templates(hass, entity)


def _is_state_only_entity_id(entity_id):
    return entity_id.split(".", 1)[0] in STATE_ONLY_ENTITY_DOMAINS


def _assert_managed_virtual_entity(hass, entity_id) -> None:
    """Reject service calls targeting entities outside this integration."""
    entity_entry = er.async_get(hass).async_get(entity_id)
    if entity_entry is None or entity_entry.platform != COMPONENT_DOMAIN:
        raise HomeAssistantError(f"{entity_id} is not managed by virtual_layer")


def _get_state_only_entity_config(hass, entity_id):
    domain = entity_id.split(".", 1)[0]
    if domain not in STATE_ONLY_ENTITY_DOMAINS:
        return None

    for group_data in hass.data.get(COMPONENT_DOMAIN, {}).values():
        if not isinstance(group_data, Mapping):
            continue
        entities = group_data.get(ATTR_ENTITIES, {})
        if not isinstance(entities, Mapping):
            continue
        for entity in entities.get(domain, []):
            if not isinstance(entity, Mapping):
                continue
            if entity.get(ATTR_ENTITY_ID) == entity_id:
                return entity
    return None


def _state_only_suggested_object_id(entity_id):
    if "." not in entity_id:
        return None
    return entity_id.split(".", 1)[1]


def _async_available_state_only_entity_id(hass, entity_id):
    state = hass.states.get(entity_id)
    if state is None:
        return entity_id

    registry_entry = er.async_get(hass).async_get(entity_id)
    if registry_entry is not None and registry_entry.platform == COMPONENT_DOMAIN:
        return entity_id

    domain, object_id = entity_id.split(".", 1)
    next_entity_id = async_generate_entity_id(f"{domain}.{{}}", object_id, hass=hass)
    _LOGGER.warning(
        "State-only virtual entity %s already exists outside virtual_layer; using %s",
        entity_id,
        next_entity_id,
    )
    return next_entity_id


@callback
def _async_register_state_only_entity(hass, entry, entity) -> str | None:
    entity_id = entity.get(ATTR_ENTITY_ID)
    unique_id = entity.get(ATTR_UNIQUE_ID)
    if not entity_id or not unique_id:
        _LOGGER.warning("Skipping state-only virtual entity without entity_id or unique_id")
        return None

    entity_id = _async_available_state_only_entity_id(hass, entity_id)
    domain = entity_id.split(".", 1)[0]
    device_id = None

    device_registry = dr.async_get(hass)
    if entity.get(ATTR_DEVICE_ID):
        device_entry = device_registry.async_get_device(
            identifiers={(COMPONENT_DOMAIN, entity[ATTR_DEVICE_ID])},
        )
        if device_entry is not None:
            device_id = device_entry.id

    entity_entry = er.async_get(hass).async_get_or_create(
        domain,
        COMPONENT_DOMAIN,
        unique_id,
        suggested_object_id=_state_only_suggested_object_id(entity_id),
        config_entry=entry,
        device_id=device_id,
    )
    entity[ATTR_ENTITY_ID] = entity_entry.entity_id
    return entity_entry.entity_id


def _get_state_only_state(hass, entity_id):
    if _get_state_only_entity_config(hass, entity_id) is None:
        raise HomeAssistantError(f"{entity_id} is not managed by virtual_layer")

    state = hass.states.get(entity_id)
    if state is None:
        raise HomeAssistantError("{} not found".format(entity_id))
    return state


def _state_only_direct_options(hass, entity_id) -> dict:
    """Return configured generic options that runtime services must not replace."""
    config = _get_state_only_entity_config(hass, entity_id)
    return generic_entity_options(config) if config is not None else {}


@callback
def _async_set_state_only_entity(hass, entity_id, value=None, attributes=None):
    state = _get_state_only_state(hass, entity_id)
    next_attributes = dict(state.attributes)
    direct_options = _state_only_direct_options(hass, entity_id)
    if attributes:
        next_attributes.update({
            name: item
            for name, item in attributes.items()
            if name not in direct_options
        })
    next_attributes.update(direct_options)
    hass.states.async_set(
        entity_id,
        state.state if value is None else value,
        next_attributes,
    )


@callback
def _async_clear_state_only_attributes(hass, entity_id, attributes):
    state = _get_state_only_state(hass, entity_id)
    next_attributes = dict(state.attributes)
    direct_options = _state_only_direct_options(hass, entity_id)
    if attributes:
        for attribute in attributes:
            if attribute in (ATTR_PERSISTENT, ATTR_AVAILABLE) or attribute in direct_options:
                continue
            next_attributes.pop(attribute, None)
    else:
        for attribute in list(next_attributes):
            if attribute not in (ATTR_PERSISTENT, ATTR_AVAILABLE) and attribute not in direct_options:
                next_attributes.pop(attribute, None)
    next_attributes.update(direct_options)
    hass.states.async_set(entity_id, state.state, next_attributes)


async def async_virtual_set_availability_service(hass, call):
    value = call.data['value']
    if type(value) is not bool:
        value = str_to_bool(value)

    for entity_id in call.data['entity_id']:
        domain = entity_id.split(".")[0]
        _LOGGER.info("{} set_avilable(value={})".format(entity_id, value))
        if _is_state_only_entity_id(entity_id):
            _async_set_state_only_entity(hass, entity_id, attributes={ATTR_AVAILABLE: value})
            continue
        _assert_managed_virtual_entity(hass, entity_id)
        get_entity_from_domain(hass, domain, entity_id).set_available(value)


@callback
def _async_register_virtual_services(hass) -> None:
    if COMPONENT_DOMAIN in hass.data[COMPONENT_SERVICES]:
        return

    @verify_domain_control(COMPONENT_DOMAIN)
    async def async_virtual_service_set_available(call) -> None:
        """Call virtual availability service handler."""
        _LOGGER.info(f"{call.service} service called")
        await async_virtual_set_availability_service(hass, call)

    @verify_domain_control(COMPONENT_DOMAIN)
    async def async_virtual_service_set_state(call) -> None:
        """Call virtual state service handler."""
        _LOGGER.info(f"{call.service} service called")
        await async_virtual_set_state_service(hass, call)

    @verify_domain_control(COMPONENT_DOMAIN)
    async def async_virtual_service_set_attributes(call) -> None:
        """Call virtual attribute service handler."""
        _LOGGER.info(f"{call.service} service called")
        await async_virtual_set_attributes_service(hass, call)

    @verify_domain_control(COMPONENT_DOMAIN)
    async def async_virtual_service_clear_attributes(call) -> None:
        """Call virtual attribute clearing service handler."""
        _LOGGER.info(f"{call.service} service called")
        await async_virtual_clear_attributes_service(hass, call)

    @verify_domain_control(COMPONENT_DOMAIN)
    async def async_virtual_service_backup_devices_handler(call) -> None:
        """Call virtual backup service handler."""
        _LOGGER.info(f"{call.service} service called")
        await async_virtual_backup_devices_service(hass, call)

    @verify_domain_control(COMPONENT_DOMAIN)
    async def async_virtual_service_restore_devices_handler(call) -> None:
        """Call virtual restore service handler."""
        _LOGGER.info(f"{call.service} service called")
        await async_virtual_restore_devices_service(hass, call)

    _LOGGER.debug("installing virtual layer handlers")
    hass.data[COMPONENT_SERVICES][COMPONENT_DOMAIN] = "installed"
    hass.services.async_register(
        COMPONENT_DOMAIN,
        SERVICE_AVAILABILE,
        async_virtual_service_set_available,
        schema=SERVICE_SCHEMA,
    )
    hass.services.async_register(
        COMPONENT_DOMAIN,
        SERVICE_SET_STATE,
        async_virtual_service_set_state,
        schema=SERVICE_SET_STATE_SCHEMA,
    )
    hass.services.async_register(
        COMPONENT_DOMAIN,
        SERVICE_SET_ATTRIBUTES,
        async_virtual_service_set_attributes,
        schema=SERVICE_SET_ATTRIBUTES_SCHEMA,
    )
    hass.services.async_register(
        COMPONENT_DOMAIN,
        SERVICE_CLEAR_ATTRIBUTES,
        async_virtual_service_clear_attributes,
        schema=SERVICE_CLEAR_ATTRIBUTES_SCHEMA,
    )
    hass.services.async_register(
        COMPONENT_DOMAIN,
        SERVICE_BACKUP_DEVICES,
        async_virtual_service_backup_devices_handler,
        schema=SERVICE_BACKUP_DEVICES_SCHEMA,
    )
    hass.services.async_register(
        COMPONENT_DOMAIN,
        SERVICE_RESTORE_DEVICES,
        async_virtual_service_restore_devices_handler,
        schema=SERVICE_RESTORE_DEVICES_SCHEMA,
    )


async def async_virtual_set_state_service(hass, call):
    value = call.data[ATTR_VALUE]
    for entity_id in call.data[ATTR_ENTITY_ID]:
        domain = entity_id.split(".")[0]
        _LOGGER.info(f"{entity_id} set_state(value={value})")
        if _is_state_only_entity_id(entity_id):
            _async_set_state_only_entity(hass, entity_id, value=value)
            continue
        _assert_managed_virtual_entity(hass, entity_id)
        entity = get_entity_from_domain(hass, domain, entity_id)
        await _async_apply_virtual_state(entity, value)


async def async_virtual_set_attributes_service(hass, call):
    attributes = {
        name: value
        for name, value in call.data[ATTR_ATTRIBUTES].items()
        if name not in RESERVED_VIRTUAL_ATTRIBUTE_NAMES
    }
    for entity_id in call.data[ATTR_ENTITY_ID]:
        domain = entity_id.split(".")[0]
        _LOGGER.info(f"{entity_id} set_attributes(attributes={attributes})")
        if _is_state_only_entity_id(entity_id):
            _async_set_state_only_entity(hass, entity_id, attributes=attributes)
            continue
        _assert_managed_virtual_entity(hass, entity_id)
        get_entity_from_domain(hass, domain, entity_id).set_attributes(attributes)


async def async_virtual_clear_attributes_service(hass, call):
    attributes = call.data[ATTR_ATTRIBUTES]
    for entity_id in call.data[ATTR_ENTITY_ID]:
        domain = entity_id.split(".")[0]
        _LOGGER.info(f"{entity_id} clear_attributes(attributes={attributes})")
        if _is_state_only_entity_id(entity_id):
            _async_clear_state_only_attributes(hass, entity_id, attributes)
            continue
        _assert_managed_virtual_entity(hass, entity_id)
        get_entity_from_domain(hass, domain, entity_id).clear_attributes(attributes)


async def async_virtual_backup_devices_service(hass, call) -> None:
    """Back up configured virtual devices."""
    file_name = call.data[ATTR_FILE_NAME]
    group_name = call.data.get(ATTR_GROUP_NAME)
    entries = _entries_for_backup_restore(hass, group_name)
    backups = [await async_build_entry_backup(entry) for entry in entries]
    await async_save_backup(file_name, backups)
    _LOGGER.info(f"backed up {len(backups)} virtual layer group(s) to {file_name}")


async def async_virtual_restore_devices_service(hass, call) -> None:
    """Restore configured virtual devices."""
    file_name = call.data[ATTR_FILE_NAME]
    group_name = call.data.get(ATTR_GROUP_NAME)
    mode = call.data["mode"]

    backup_groups = await async_load_backup(file_name)
    if not backup_groups:
        raise HomeAssistantError(f"No virtual layer groups found in {file_name}")

    entries = _entries_for_backup_restore(hass, group_name)
    reloaded_entries = []
    for entry in entries:
        backup_group = _find_backup_group_for_entry(backup_groups, entry, group_name)
        if backup_group is None:
            _LOGGER.info(f"no backup group found for {entry.data[ATTR_GROUP_NAME]}")
            continue

        restored_ui_devices = backup_group.get(ATTR_DEVICES, {})
        restored_device_attributes = backup_group.get(ATTR_DEVICE_ATTRIBUTES, {})
        if mode == SERVICE_RESTORE_MODE_MERGE:
            restored_ui_devices = _merge_device_sets(
                entry.options.get(ATTR_DEVICES, {}),
                restored_ui_devices,
            )
            restored_device_attributes = _merge_device_attributes(
                entry.options.get(ATTR_DEVICE_ATTRIBUTES, {}),
                restored_device_attributes,
            )

        options = dict(entry.options)
        options[ATTR_DEVICES] = restored_ui_devices
        options[ATTR_DEVICE_ATTRIBUTES] = restored_device_attributes
        hass.config_entries.async_update_entry(entry, options=options)
        reloaded_entries.append(entry)

    if not reloaded_entries:
        raise HomeAssistantError(f"No matching virtual layer groups found in {file_name}")

    for entry in reloaded_entries:
        await hass.config_entries.async_reload(entry.entry_id)

    _LOGGER.info(f"restored {len(reloaded_entries)} virtual layer group(s) from {file_name}")


def _entries_for_backup_restore(hass, group_name):
    entries = list(hass.config_entries.async_entries(COMPONENT_DOMAIN))
    if group_name is None:
        return entries
    matching = [
        entry
        for entry in entries
        if entry.data.get(ATTR_GROUP_NAME) == group_name
    ]
    if not matching:
        raise HomeAssistantError(f"{group_name} virtual layer group not found")
    return matching


def _find_backup_group_for_entry(backup_groups, entry, requested_group_name):
    if requested_group_name and len(backup_groups) == 1:
        return backup_groups[0]

    entry_group_name = entry.data[ATTR_GROUP_NAME]
    for backup_group in backup_groups:
        if backup_group.get(ATTR_GROUP_NAME) == entry_group_name:
            return backup_group
    return None


def _merge_device_sets(existing_devices, restored_devices):
    if not isinstance(existing_devices, Mapping):
        existing_devices = {}
    if not isinstance(restored_devices, Mapping):
        restored_devices = {}
    merged = {
        device_name: list(entities or [])
        for device_name, entities in (existing_devices or {}).items()
        if isinstance(entities, list)
    }
    for device_name, entities in (restored_devices or {}).items():
        if not isinstance(entities, list):
            _LOGGER.warning("Skipping invalid restored entity list for %s", device_name)
            continue
        merged.setdefault(device_name, [])
        merged[device_name].extend(clone_entities_with_new_keys(entities))
    return merged


def _merge_device_attributes(existing_attributes, restored_attributes):
    if not isinstance(existing_attributes, Mapping):
        existing_attributes = {}
    if not isinstance(restored_attributes, Mapping):
        restored_attributes = {}
    merged = {
        device_name: dict(attributes or {})
        for device_name, attributes in (existing_attributes or {}).items()
        if isinstance(attributes, Mapping)
    }
    for device_name, attributes in (restored_attributes or {}).items():
        if not isinstance(attributes, Mapping):
            _LOGGER.warning("Skipping invalid restored device attributes for %s", device_name)
            continue
        merged[device_name] = dict(attributes or {})
    return merged


async def _async_apply_virtual_state(entity, value):
    if hasattr(entity, "set_state"):
        entity.set_state(value)
        entity.async_schedule_update_ha_state()
        return

    if hasattr(entity, "set"):
        entity.set(value)
        entity.async_schedule_update_ha_state()
        return

    value = str(value).lower()
    if value in ["y", "yes", "t", "true", "on", "1"]:
        if hasattr(entity, "async_turn_on"):
            await entity.async_turn_on()
        elif hasattr(entity, "turn_on"):
            entity.turn_on()
        else:
            raise HomeAssistantError(f"{entity.entity_id} does not support turn on")
        entity.async_schedule_update_ha_state()
        return

    if value in ["n", "no", "f", "false", "off", "0"]:
        if hasattr(entity, "async_turn_off"):
            await entity.async_turn_off()
        elif hasattr(entity, "turn_off"):
            entity.turn_off()
        else:
            raise HomeAssistantError(f"{entity.entity_id} does not support turn off")
        entity.async_schedule_update_ha_state()
        return

    if value in ["locked", "lock"] and hasattr(entity, "async_lock"):
        await entity.async_lock()
        entity.async_schedule_update_ha_state()
        return

    if value in ["unlocked", "unlock"] and hasattr(entity, "async_unlock"):
        await entity.async_unlock()
        entity.async_schedule_update_ha_state()
        return

    if value in ["open", "opened"]:
        if hasattr(entity, "async_open_cover"):
            await entity.async_open_cover()
        elif hasattr(entity, "async_open_valve"):
            await entity.async_open_valve()
        elif hasattr(entity, "async_open"):
            await entity.async_open()
        else:
            raise HomeAssistantError(f"{entity.entity_id} does not support open")
        entity.async_schedule_update_ha_state()
        return

    if value in ["closed", "close"]:
        if hasattr(entity, "async_close_cover"):
            await entity.async_close_cover()
        elif hasattr(entity, "async_close_valve"):
            await entity.async_close_valve()
        else:
            raise HomeAssistantError(f"{entity.entity_id} does not support close")
        entity.async_schedule_update_ha_state()
        return

    if hasattr(entity, "move_to_location"):
        entity.move_to_location(value)
        entity.async_schedule_update_ha_state()
        return

    raise HomeAssistantError(f"{entity.entity_id} does not support set_state")


async def _async_get_or_create_virtual_device_in_registry(
        hass: HomeAssistant, entry: ConfigEntry, device
) -> None:
    registry = dr.async_get(hass)
    device_info = {
        "config_entry_id": entry.entry_id,
        "identifiers": {(COMPONENT_DOMAIN, device[ATTR_DEVICE_ID])},
        "manufacturer": device.get(CONF_MANUFACTURER) or COMPONENT_MANUFACTURER,
        "model": device.get(CONF_MODEL) or COMPONENT_MODEL,
        "name": device[CONF_NAME],
        "sw_version": device.get(CONF_SW_VERSION) or __version__,
    }
    for config_key, info_key in (
        (CONF_HW_VERSION, "hw_version"),
        (CONF_SERIAL_NUMBER, "serial_number"),
    ):
        if device.get(config_key):
            device_info[info_key] = device[config_key]
    registry.async_get_or_create(
        **device_info,
    )


async def _async_delete_virtual_device_from_registry(
        hass: HomeAssistant, _entry: ConfigEntry, device
) -> None:
    device_id = device.get(ATTR_DEVICE_ID)
    if not device_id:
        _LOGGER.info("orphaned virtual device metadata has no device_id")
        return

    registery = dr.async_get(hass)
    device_in_registry = registery.async_get_device(
        identifiers={(COMPONENT_DOMAIN, device_id)},
    )
    if device_in_registry:
        _LOGGER.debug(f"found something to delete! {device_in_registry.id}")
        registery.async_remove_device(device_in_registry.id)
    else:
        _LOGGER.info(f"have orphaned device in meta {device_id}")


async def _async_delete_virtual_entity_from_registry(
        hass: HomeAssistant, entry: ConfigEntry, entity, active_device_ids
) -> None:
    entity_id = entity.get(ATTR_ENTITY_ID)
    if entity_id:
        registry = er.async_get(hass)
        if registry.async_get(entity_id):
            _LOGGER.debug("removing orphaned entity %s", entity_id)
            registry.async_remove(entity_id)

    device_id = entity.get(ATTR_DEVICE_ID)
    if device_id and device_id not in active_device_ids:
        await _async_delete_virtual_device_from_registry(hass, entry, entity)
