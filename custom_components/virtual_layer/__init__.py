"""
This component provides support for virtual components.

"""

import asyncio
import logging
import math
from collections.abc import Mapping
from datetime import timedelta

import homeassistant.helpers.area_registry as ar
import homeassistant.helpers.config_validation as cv
import homeassistant.helpers.device_registry as dr
import homeassistant.helpers.entity_registry as er
import voluptuous as vol
from homeassistant.auth.permissions.const import POLICY_CONTROL
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID, CONF_ICON
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, Unauthorized, UnknownUser
from homeassistant.helpers.entity import async_generate_entity_id
from homeassistant.helpers.event import (
    TrackTemplate,
    async_call_later,
    async_track_state_change_event,
    async_track_template_result,
    async_track_time_interval,
)
from homeassistant.helpers.template import Template, TemplateError

from .cfg import (
    BlendedCfg,
    _delete_meta_data,
)
from .const import *

__version__ = '1.0.8'

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(COMPONENT_DOMAIN)

SERVICE_AVAILABILE = 'set_available'
SERVICE_SCHEMA = vol.Schema({
    vol.Required(ATTR_ENTITY_ID): cv.comp_entity_ids,
    vol.Required('value'): cv.boolean,
})
SERVICE_SET_STATE = "set_state"
SERVICE_SET_ATTRIBUTES = "set_attributes"
SERVICE_CLEAR_ATTRIBUTES = "clear_attributes"


def _finite_service_payload(value):
    """Reject non-finite numbers before they reach HA state or attributes."""
    if isinstance(value, float) and not math.isfinite(value):
        raise vol.Invalid("NaN and infinity are not valid state values")
    if isinstance(value, Mapping):
        for key, item in value.items():
            _finite_service_payload(key)
            _finite_service_payload(item)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            _finite_service_payload(item)
    return value


def _service_state_value(value):
    """Validate a scalar service state without coercing numbers to strings."""
    if not isinstance(value, (str, int, float, bool)):
        raise vol.Invalid("State value must be a string, number, or boolean")
    return _finite_service_payload(value)


SERVICE_SET_STATE_SCHEMA = vol.Schema({
    vol.Required(ATTR_ENTITY_ID): cv.comp_entity_ids,
    vol.Required(ATTR_VALUE): _service_state_value,
})
SERVICE_SET_ATTRIBUTES_SCHEMA = vol.Schema({
    vol.Required(ATTR_ENTITY_ID): cv.comp_entity_ids,
    vol.Required(ATTR_ATTRIBUTES): vol.All(dict, _finite_service_payload),
})
SERVICE_CLEAR_ATTRIBUTES_SCHEMA = vol.Schema({
    vol.Required(ATTR_ENTITY_ID): cv.comp_entity_ids,
    vol.Optional(ATTR_ATTRIBUTES, default=list): vol.All(cv.ensure_list, [cv.string]),
})


async def _async_service_call_user(hass: HomeAssistant, call):
    """Return the authenticated service caller, allowing trusted system calls."""
    user_id = call.context.user_id
    if user_id is None:
        return None
    user = await hass.auth.async_get_user(user_id)
    if user is None:
        raise UnknownUser(
            context=call.context,
            user_id=user_id,
            permission=POLICY_CONTROL,
        )
    return user


async def _async_verify_target_entity_control(hass: HomeAssistant, call) -> None:
    """Require control permission for every entity targeted by a service call."""
    user = await _async_service_call_user(hass, call)
    if user is None:
        return
    for entity_id in call.data.get(ATTR_ENTITY_ID, []):
        if not user.permissions.check_entity(entity_id, POLICY_CONTROL):
            raise Unauthorized(
                context=call.context,
                user_id=user.id,
                entity_id=entity_id,
                permission=POLICY_CONTROL,
            )


async def _async_verify_admin(hass: HomeAssistant, call) -> None:
    """Require an administrator for configuration-wide service operations."""
    user = await _async_service_call_user(hass, call)
    if user is not None and not user.is_admin:
        raise Unauthorized(
            context=call.context,
            user_id=user.id,
            permission=POLICY_CONTROL,
        )

VIRTUAL_PLATFORMS = VIRTUAL_ENTITY_DOMAINS
_STATE_ONLY_TEMPLATE_LISTENERS_DATA = f"{COMPONENT_DOMAIN}_state_only_template_listeners"
_ENTITY_ID_GUARD_LISTENERS_DATA = f"{COMPONENT_DOMAIN}_entity_id_guard_listeners"
_DEVICE_METADATA_GUARD_LISTENERS_DATA = f"{COMPONENT_DOMAIN}_device_metadata_guard_listeners"


def _async_ensure_runtime_data(hass: HomeAssistant) -> None:
    """Initialize integration runtime containers independently and idempotently."""
    hass.data.setdefault(COMPONENT_DOMAIN, {})
    hass.data.setdefault(COMPONENT_SERVICES, {})


def _runtime_group_name_for_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Find cached group data even when the config entry name has changed."""
    groups = hass.data.get(COMPONENT_DOMAIN, {})
    configured_group_name = entry.data.get(ATTR_GROUP_NAME)
    configured_group = groups.get(configured_group_name)
    if (
        isinstance(configured_group, Mapping)
        and configured_group.get(ATTR_CONFIG_ENTRY_ID) == entry.entry_id
    ):
        return configured_group_name
    for group_name, group_data in groups.items():
        if (
            isinstance(group_data, Mapping)
            and group_data.get(ATTR_CONFIG_ENTRY_ID) == entry.entry_id
        ):
            return group_name
    return configured_group_name


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

    _async_ensure_runtime_data(hass)

    _async_register_virtual_services(hass)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    _LOGGER.debug(f'async setup {entry.data}')

    _async_ensure_runtime_data(hass)
    previous_group_name = _runtime_group_name_for_entry(hass, entry)

    # Get the config.
    _LOGGER.debug("creating new cfg")
    vcfg = BlendedCfg(hass, entry.data, entry.options, entry)
    await vcfg.async_load()

    # create the devices.
    _LOGGER.debug("creating the devices")
    for device in vcfg.devices:
        _LOGGER.debug(f"creating-device={device}")
        await _async_get_or_create_virtual_device_in_registry(hass, entry, device)
    _async_remove_orphaned_diagnostic_registry_entries(hass, entry, vcfg.entities)
    _async_sync_active_entity_registry_entries(hass, entry, vcfg.entities)

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
            ATTR_CONFIG_ENTRY_ID: entry.entry_id,
            ATTR_ENTITIES: vcfg.entities,
            ATTR_DEVICES: vcfg.devices,
        }
    })
    if previous_group_name != entry.data[ATTR_GROUP_NAME]:
        hass.data[COMPONENT_DOMAIN].pop(previous_group_name, None)
    _LOGGER.debug(f"update hass data {hass.data[COMPONENT_DOMAIN]}")
    _async_setup_state_only_entities(hass, entry, vcfg.entities)

    # Create the entities.
    _LOGGER.debug("creating the entities")
    platforms = _entry_platforms_from_entities(vcfg.entities)
    platforms_loaded = False
    try:
        if platforms:
            await hass.config_entries.async_forward_entry_setups(entry, platforms)
        platforms_loaded = True
    finally:
        if not platforms_loaded:
            _async_remove_entity_id_guard(hass, entry.entry_id)
            _async_remove_device_metadata_guard(hass, entry.entry_id)
            _async_unload_state_only_entities(hass, entry, vcfg.entities)
            hass.data[COMPONENT_DOMAIN].pop(entry.data[ATTR_GROUP_NAME], None)

    _async_setup_entity_id_guard(hass, entry, vcfg.entities)
    _async_setup_device_metadata_guard(hass, entry, vcfg.devices)
    _async_remove_inactive_virtual_devices(hass, entry, active_device_ids)

    # Install service handlers.
    _async_register_virtual_services(hass)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.debug(f"unloading virtual group {entry.data[ATTR_GROUP_NAME]}")
    # _LOGGER.debug(f"before hass={hass.data[COMPONENT_DOMAIN]}")
    runtime_group_name = _runtime_group_name_for_entry(hass, entry)
    group_data = hass.data.get(COMPONENT_DOMAIN, {}).get(runtime_group_name, {})
    platforms = _entry_platforms_from_entities(group_data.get(ATTR_ENTITIES, {}))
    unload_ok = True
    if platforms:
        unload_ok = await hass.config_entries.async_unload_platforms(entry, platforms)
    if unload_ok:
        _LOGGER.debug("unloaded ok")
        _async_remove_entity_id_guard(hass, entry.entry_id)
        _async_remove_device_metadata_guard(hass, entry.entry_id)
        _async_unload_state_only_entities(hass, entry, group_data.get(ATTR_ENTITIES, {}))
        hass.data[COMPONENT_DOMAIN].pop(runtime_group_name, None)
        # _LOGGER.debug(f"ocfg={ocfg}")
    # _LOGGER.debug(f"after hass={hass.data[COMPONENT_DOMAIN]}")

    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clean up Virtual Layer data when a config entry is removed."""
    group_name = entry.data.get(ATTR_GROUP_NAME)
    _LOGGER.debug("removing virtual group %s", group_name)

    runtime_group_name = _runtime_group_name_for_entry(hass, entry)
    group_data = hass.data.get(COMPONENT_DOMAIN, {}).get(runtime_group_name, {})
    _async_remove_entity_id_guard(hass, entry.entry_id)
    _async_remove_device_metadata_guard(hass, entry.entry_id)
    _async_unload_state_only_entities(hass, entry, group_data.get(ATTR_ENTITIES, {}))
    _async_remove_entry_registry_entries(hass, entry)

    if group_name:
        await _delete_meta_data(hass, group_name)
        hass.data.get(COMPONENT_DOMAIN, {}).pop(runtime_group_name, None)


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


@callback
def _async_remove_orphaned_diagnostic_registry_entries(hass, entry, entities) -> None:
    """Synchronize diagnostic registry defaults and remove obsolete diagnostics."""
    platform_entity_groups = entities.values() if isinstance(entities, Mapping) else []
    active_generated_entities = {
        entity[ATTR_UNIQUE_ID]: entity
        for platform_entities in platform_entity_groups
        if isinstance(platform_entities, list)
        for entity in platform_entities
        if isinstance(entity, Mapping)
        and isinstance(entity.get(ATTR_UNIQUE_ID), str)
        and DIAGNOSTIC_UNIQUE_ID_MARKER in entity[ATTR_UNIQUE_ID]
    }
    entity_registry = er.async_get(hass)
    for entity_entry in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        if DIAGNOSTIC_UNIQUE_ID_MARKER not in entity_entry.unique_id:
            continue
        generated_entity = active_generated_entities.get(entity_entry.unique_id)
        if generated_entity is None:
            entity_registry.async_remove(entity_entry.entity_id)
            continue

        updates = {}
        generated_name = generated_entity.get(CONF_NAME)
        if isinstance(generated_name, str) and entity_entry.original_name != generated_name:
            updates["original_name"] = generated_name
        generated_icon = generated_entity.get(CONF_ICON)
        if isinstance(generated_icon, str) and entity_entry.original_icon != generated_icon:
            updates["original_icon"] = generated_icon
        if updates:
            entity_registry.async_update_entity(entity_entry.entity_id, **updates)


@callback
def _async_sync_active_entity_registry_entries(hass, entry, entities) -> None:
    """Keep registry metadata aligned with the current virtual configuration."""
    if not isinstance(entities, Mapping):
        return

    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    for domain, platform_entities in entities.items():
        if not isinstance(platform_entities, list):
            continue
        for entity in platform_entities:
            if not isinstance(entity, Mapping):
                continue
            unique_id = entity.get(ATTR_UNIQUE_ID)
            if not isinstance(unique_id, str):
                continue
            current_entity_id = entity_registry.async_get_entity_id(
                domain,
                COMPONENT_DOMAIN,
                unique_id,
            )
            if current_entity_id is None:
                continue
            entity_entry = entity_registry.async_get(current_entity_id)
            if entity_entry is None or entity_entry.config_entry_id != entry.entry_id:
                continue

            updates = {}
            configured_entity_id = entity.get(ATTR_ENTITY_ID)
            if (
                isinstance(configured_entity_id, str)
                and configured_entity_id
                and configured_entity_id != current_entity_id
            ):
                updates["new_entity_id"] = configured_entity_id

            virtual_device_id = entity.get(ATTR_DEVICE_ID)
            if isinstance(virtual_device_id, str) and virtual_device_id:
                device_entry = device_registry.async_get_device(
                    identifiers={(COMPONENT_DOMAIN, virtual_device_id)},
                )
                if device_entry is not None and entity_entry.device_id != device_entry.id:
                    updates["device_id"] = device_entry.id

            original_name = entity.get(CONF_NAME)
            if isinstance(original_name, str) and entity_entry.original_name != original_name:
                updates["original_name"] = original_name
            original_icon = entity.get(CONF_ICON)
            if not isinstance(original_icon, str):
                original_icon = None
            if entity_entry.original_icon != original_icon:
                updates["original_icon"] = original_icon

            if not updates:
                continue
            try:
                entity_registry.async_update_entity(current_entity_id, **updates)
            except ValueError as err:
                _LOGGER.warning(
                    "Unable to synchronize registry metadata for %s: %s",
                    current_entity_id,
                    err,
                )


@callback
def _async_remove_entity_id_guard(hass, entry_id) -> None:
    listeners = hass.data.get(_ENTITY_ID_GUARD_LISTENERS_DATA)
    if listeners is None:
        return
    remove_listener = listeners.pop(entry_id, None)
    if remove_listener is not None:
        remove_listener()
    if not listeners:
        hass.data.pop(_ENTITY_ID_GUARD_LISTENERS_DATA, None)


@callback
def _async_setup_entity_id_guard(hass, entry, entities) -> None:
    """Keep configured Virtual Layer entity IDs fixed after registry renames."""
    _async_remove_entity_id_guard(hass, entry.entry_id)
    if not isinstance(entities, Mapping):
        return
    desired_entity_ids = {
        entity[ATTR_UNIQUE_ID]: entity[ATTR_ENTITY_ID]
        for platform_entities in entities.values()
        if isinstance(platform_entities, list)
        for entity in platform_entities
        if isinstance(entity, Mapping)
        and isinstance(entity.get(ATTR_UNIQUE_ID), str)
        and isinstance(entity.get(ATTR_ENTITY_ID), str)
    }
    if not desired_entity_ids:
        return

    entity_registry = er.async_get(hass)

    async def _async_restore_configured_entity_id(current_entity_id) -> None:
        # Let the entity platform complete its rename first so the old state ID
        # is released before the registry moves the entity back.
        await asyncio.sleep(0)
        entity_entry = entity_registry.async_get(current_entity_id)
        if (
            entity_entry is None
            or entity_entry.config_entry_id != entry.entry_id
        ):
            return
        configured_entity_id = desired_entity_ids.get(entity_entry.unique_id)
        if not configured_entity_id or configured_entity_id == current_entity_id:
            return
        try:
            entity_registry.async_update_entity(
                current_entity_id,
                new_entity_id=configured_entity_id,
            )
        except ValueError as err:
            _LOGGER.warning(
                "Unable to restore configured entity id %s for %s: %s",
                configured_entity_id,
                current_entity_id,
                err,
            )

    @callback
    def _async_handle_registry_update(event) -> None:
        if (
            event.data.get("action") != "update"
            or "old_entity_id" not in event.data
        ):
            return
        hass.async_create_task(
            _async_restore_configured_entity_id(event.data[ATTR_ENTITY_ID])
        )

    listeners = hass.data.setdefault(_ENTITY_ID_GUARD_LISTENERS_DATA, {})
    listeners[entry.entry_id] = hass.bus.async_listen(
        er.EVENT_ENTITY_REGISTRY_UPDATED,
        _async_handle_registry_update,
    )


@callback
def _async_remove_device_metadata_guard(hass, entry_id) -> None:
    listeners = hass.data.get(_DEVICE_METADATA_GUARD_LISTENERS_DATA)
    if listeners is None:
        return
    remove_listener = listeners.pop(entry_id, None)
    if remove_listener is not None:
        remove_listener()
    if not listeners:
        hass.data.pop(_DEVICE_METADATA_GUARD_LISTENERS_DATA, None)


def _configured_area_id(hass, device: Mapping) -> str | None:
    """Resolve the configured Home Assistant area ID or name."""
    configured_area = device.get(CONF_SUGGESTED_AREA)
    if not isinstance(configured_area, str) or not configured_area.strip():
        return None
    area_registry = ar.async_get(hass)
    area = area_registry.async_get_area(configured_area.strip())
    if area is None:
        area = area_registry.async_get_area_by_name(configured_area.strip())
    return area.id if area is not None else None


def _device_registry_updates_for_config(hass, device: Mapping, registry_entry=None) -> dict:
    """Return registry updates needed to match a Virtual Layer device config."""
    updates = {}
    desired = {
        "name": device.get(CONF_NAME),
        "manufacturer": device.get(CONF_MANUFACTURER) or COMPONENT_MANUFACTURER,
        "model": device.get(CONF_MODEL) or COMPONENT_MODEL,
        "sw_version": device.get(CONF_SW_VERSION) or __version__,
        "area_id": _configured_area_id(hass, device),
    }
    for config_key, registry_key in (
        (CONF_HW_VERSION, "hw_version"),
        (CONF_SERIAL_NUMBER, "serial_number"),
        (CONF_CONFIGURATION_URL, "configuration_url"),
        (CONF_VIA_DEVICE_ID, "via_device_id"),
    ):
        desired[registry_key] = device.get(config_key)

    for registry_key, value in desired.items():
        current = getattr(registry_entry, registry_key, None) if registry_entry else None
        if current != value:
            updates[registry_key] = value
    return updates


@callback
def _async_setup_device_metadata_guard(hass, entry, devices) -> None:
    """Keep configured Virtual Layer Device registry metadata aligned."""
    _async_remove_device_metadata_guard(hass, entry.entry_id)
    if not isinstance(devices, list):
        return

    registry = dr.async_get(hass)
    devices_by_registry_id = {}
    for device in devices:
        if not isinstance(device, Mapping):
            continue
        virtual_device_id = device.get(ATTR_DEVICE_ID)
        if not isinstance(virtual_device_id, str) or not virtual_device_id:
            continue
        registry_entry = registry.async_get_device(
            identifiers={(COMPONENT_DOMAIN, virtual_device_id)},
        )
        if registry_entry is None:
            continue
        devices_by_registry_id[registry_entry.id] = device

    if not devices_by_registry_id:
        return

    async def _async_restore_device_metadata(device_id) -> None:
        await asyncio.sleep(0)
        registry_entry = registry.async_get(device_id)
        device = devices_by_registry_id.get(device_id)
        if registry_entry is None or device is None:
            return
        updates = _device_registry_updates_for_config(hass, device, registry_entry)
        if not updates:
            return
        try:
            registry.async_update_device(device_id, **updates)
        except ValueError as err:
            _LOGGER.warning(
                "Unable to restore configured device metadata for %s: %s",
                device_id,
                err,
            )

    @callback
    def _async_handle_device_registry_update(event) -> None:
        if event.data.get("action") != "update":
            return
        device_id = event.data.get("device_id")
        if device_id not in devices_by_registry_id:
            return
        hass.async_create_task(_async_restore_device_metadata(device_id))

    listeners = hass.data.setdefault(_DEVICE_METADATA_GUARD_LISTENERS_DATA, {})
    listeners[entry.entry_id] = hass.bus.async_listen(
        dr.EVENT_DEVICE_REGISTRY_UPDATED,
        _async_handle_device_registry_update,
    )


def get_entity_configs(hass, group_name, domain):
    return hass.data.get(COMPONENT_DOMAIN, {}).get(group_name, {}).get(ATTR_ENTITIES, {}).get(domain, [])


def get_entity_from_domain(hass, domain, entity_id):
    component = hass.data.get(domain)
    if component is None:
        raise HomeAssistantError(f"{domain} component not set up")

    entity = component.get_entity(entity_id)
    if entity is None:
        raise HomeAssistantError(f"{entity_id} not found")

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
    entity_id = entity.get(ATTR_ENTITY_ID)
    variables = {"this": hass.states.get(entity_id) if entity_id else None}
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


def _render_state_only_template(hass, entity, template, extra_variables=None):
    variables = _state_only_template_variables(hass, entity)
    if extra_variables:
        variables.update(extra_variables)
    return Template(str(template), hass).async_render(
        variables=variables,
        parse_result=False,
    )


def _state_only_hook_values_match(configured, actual) -> bool:
    if configured is None:
        return True
    values = configured if isinstance(configured, list) else [configured]
    return str(actual) in {str(value) for value in values}


def _state_only_hook_attributes(hook) -> list[str]:
    attributes = hook.get(CONF_ATTRIBUTE, hook.get("attributes_changed", []))
    if isinstance(attributes, str):
        attributes = [attributes]
    if not isinstance(attributes, list):
        return []
    return [
        attribute
        for attribute in attributes
        if isinstance(attribute, str) and attribute
    ]


def _state_only_hook_matches(hook, event) -> bool:
    old_state = event.data.get("old_state")
    new_state = event.data.get("new_state")
    if not _state_only_hook_values_match(
        hook.get("from"),
        old_state.state if old_state else None,
    ):
        return False
    if not _state_only_hook_values_match(
        hook.get("to"),
        new_state.state if new_state else None,
    ):
        return False

    attributes = _state_only_hook_attributes(hook)
    if not attributes:
        return True
    return any(
        (old_state.attributes.get(attribute) if old_state else None)
        != (new_state.attributes.get(attribute) if new_state else None)
        for attribute in attributes
    )


def _state_only_event_hook_matches(hook, event) -> bool:
    event_data = hook.get("event_data")
    if not isinstance(event_data, Mapping):
        return True
    return all(event.data.get(key) == value for key, value in event_data.items())


def _state_only_hook_template_variables(hook, event) -> dict:
    if str(hook.get("trigger", "state")).lower() == "event":
        return {
            "trigger": {
                "platform": "event",
                "event": event,
                "event_type": event.event_type,
                "data": event.data,
            },
        }

    old_state = event.data.get("old_state")
    new_state = event.data.get("new_state")
    return {
        "trigger": {
            "platform": "state",
            "event": event,
            "entity_id": event.data.get(ATTR_ENTITY_ID),
            "from_state": old_state,
            "to_state": new_state,
            "from": old_state.state if old_state else None,
            "to": new_state.state if new_state else None,
        },
    }


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
    except (TemplateError, KeyError, TypeError, ValueError) as err:
        _LOGGER.warning("Unable to update state-only virtual entity %s: %s", entity_id, err)
        return

    if changed:
        hass.states.async_set(entity_id, value, attributes)


@callback
def _async_apply_state_only_event_hook(hass, entity, hook, event) -> None:
    """Apply one configured event hook to a state-only virtual entity."""
    entity_id = entity.get(ATTR_ENTITY_ID)
    state = hass.states.get(entity_id) if entity_id else None
    if state is None:
        return

    value = state.state
    attributes = dict(state.attributes)
    domain_options = generic_entity_options(entity)
    variables = _state_only_hook_template_variables(hook, event)
    changed = False

    try:
        if hook.get(CONF_AVAILABILITY_TEMPLATE):
            available = str(_render_state_only_template(
                hass,
                entity,
                hook[CONF_AVAILABILITY_TEMPLATE],
                variables,
            )).lower() in {"y", "yes", "t", "true", "on", "1"}
            changed = attributes.get(ATTR_AVAILABLE) != available or changed
            attributes[ATTR_AVAILABLE] = available

        if hook.get(CONF_VALUE_TEMPLATE):
            value = _render_state_only_template(
                hass,
                entity,
                hook[CONF_VALUE_TEMPLATE],
                variables,
            )
            changed = value != state.state or changed

        configured_attributes = hook.get(CONF_ATTRIBUTES, {})
        if isinstance(configured_attributes, Mapping):
            for name, configured_value in configured_attributes.items():
                if name in RESERVED_VIRTUAL_ATTRIBUTE_NAMES or name in domain_options:
                    continue
                changed = attributes.get(name) != configured_value or changed
                attributes[name] = configured_value

        attribute_templates = hook.get(CONF_ATTRIBUTE_TEMPLATES, {})
        if isinstance(attribute_templates, Mapping):
            for name, template in attribute_templates.items():
                if name in RESERVED_VIRTUAL_ATTRIBUTE_NAMES or name in domain_options:
                    continue
                rendered = _render_state_only_template(
                    hass,
                    entity,
                    template,
                    variables,
                )
                changed = attributes.get(name) != rendered or changed
                attributes[name] = rendered
    except (TemplateError, TypeError, ValueError) as err:
        _LOGGER.warning(
            "Unable to apply event hook for state-only virtual entity %s: %s",
            entity_id,
            err,
        )
        return

    should_refresh = hook.get(
        "refresh",
        not any(
            hook.get(field)
            for field in (
                CONF_AVAILABILITY_TEMPLATE,
                CONF_VALUE_TEMPLATE,
                CONF_ATTRIBUTES,
                CONF_ATTRIBUTE_TEMPLATES,
            )
        ),
    )
    if should_refresh:
        if changed:
            hass.states.async_set(entity_id, value, attributes)
        _async_apply_state_only_templates(hass, entity)
        return

    if changed:
        hass.states.async_set(entity_id, value, attributes)


@callback
def _async_setup_state_only_event_hooks(hass, entity, listeners) -> None:
    """Register state and event hooks for a state-only virtual entity."""
    entity_id = entity.get(ATTR_ENTITY_ID)
    hooks = entity.get(CONF_EVENT_HOOKS, [])
    if not entity_id or not isinstance(hooks, list):
        return

    debounce_cancelers = {}

    @callback
    def _cancel_debounce_callbacks():
        for cancel in debounce_cancelers.values():
            cancel()
        debounce_cancelers.clear()

    listeners.append(_cancel_debounce_callbacks)

    @callback
    def _schedule(index, hook, event):
        try:
            delay = float(hook.get("debounce", 0) or 0)
        except (TypeError, ValueError):
            delay = 0
        if not math.isfinite(delay):
            delay = 0
        if cancel := debounce_cancelers.pop(index, None):
            cancel()
        if delay <= 0:
            _async_apply_state_only_event_hook(hass, entity, hook, event)
            return

        @callback
        def _async_apply_later(_now):
            debounce_cancelers.pop(index, None)
            _async_apply_state_only_event_hook(hass, entity, hook, event)

        debounce_cancelers[index] = async_call_later(
            hass,
            delay,
            _async_apply_later,
        )

    for index, hook in enumerate(hooks):
        if not isinstance(hook, Mapping) or not hook.get("enabled", True):
            continue
        trigger = str(hook.get("trigger", "state")).lower()
        if trigger == "state":
            source_entity_ids = hook.get(ATTR_ENTITY_ID, [])
            if isinstance(source_entity_ids, str):
                source_entity_ids = [source_entity_ids]
            if not isinstance(source_entity_ids, (list, tuple, set)):
                source_entity_ids = []
            source_entity_ids = {
                source_entity_id
                for source_entity_id in source_entity_ids
                if isinstance(source_entity_id, str)
                and source_entity_id != entity_id
            }
            if not source_entity_ids:
                continue

            @callback
            def _async_state_changed(event, hook=hook, index=index):
                if _state_only_hook_matches(hook, event):
                    _schedule(index, hook, event)

            listeners.append(async_track_state_change_event(
                hass,
                source_entity_ids,
                _async_state_changed,
            ))
            continue

        if trigger == "event":
            event_type = str(hook.get("event_type", "")).strip()
            if not event_type:
                continue

            @callback
            def _async_event(event, hook=hook, index=index):
                if _state_only_event_hook_matches(hook, event):
                    _schedule(index, hook, event)

            listeners.append(hass.bus.async_listen(event_type, _async_event))


@callback
def _async_setup_state_only_templates(hass, entry, entity) -> None:
    entity_id = entity.get(ATTR_ENTITY_ID)
    if not entity_id:
        return
    configured_sources = entity.get(CONF_SOURCE_ENTITIES, [])
    if not isinstance(configured_sources, (list, tuple, set)):
        configured_sources = []
    source_entities = {
        source_entity_id
        for source_entity_id in configured_sources
        if isinstance(source_entity_id, str)
    }
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

    attribute_templates = entity.get(CONF_ATTRIBUTE_TEMPLATES, {})
    if not isinstance(attribute_templates, Mapping):
        attribute_templates = {}
    templates = [
        template
        for template in (
            entity.get(CONF_VALUE_TEMPLATE),
            entity.get(CONF_AVAILABILITY_TEMPLATE),
            *attribute_templates.values(),
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
    _async_setup_state_only_event_hooks(hass, entity, listeners)
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
        original_icon=entity.get(CONF_ICON),
    )
    entity[ATTR_ENTITY_ID] = entity_entry.entity_id
    return entity_entry.entity_id


def _get_state_only_state(hass, entity_id):
    if _get_state_only_entity_config(hass, entity_id) is None:
        raise HomeAssistantError(f"{entity_id} is not managed by virtual_layer")

    state = hass.states.get(entity_id)
    if state is None:
        raise HomeAssistantError(f"{entity_id} not found")
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
        _LOGGER.info(f"{entity_id} set_avilable(value={value})")
        if _is_state_only_entity_id(entity_id):
            _async_set_state_only_entity(hass, entity_id, attributes={ATTR_AVAILABLE: value})
            continue
        _assert_managed_virtual_entity(hass, entity_id)
        get_entity_from_domain(hass, domain, entity_id).set_available(value)


@callback
def _async_register_virtual_services(hass) -> None:
    # Remove services registered by releases that still exposed file backups.
    for removed_service in ("backup_devices", "restore_devices"):
        if hass.services.has_service(COMPONENT_DOMAIN, removed_service):
            hass.services.async_remove(COMPONENT_DOMAIN, removed_service)

    if COMPONENT_DOMAIN in hass.data[COMPONENT_SERVICES]:
        return

    async def async_virtual_service_set_available(call) -> None:
        """Call virtual availability service handler."""
        await _async_verify_target_entity_control(hass, call)
        _LOGGER.info(f"{call.service} service called")
        await async_virtual_set_availability_service(hass, call)

    async def async_virtual_service_set_state(call) -> None:
        """Call virtual state service handler."""
        await _async_verify_target_entity_control(hass, call)
        _LOGGER.info(f"{call.service} service called")
        await async_virtual_set_state_service(hass, call)

    async def async_virtual_service_set_attributes(call) -> None:
        """Call virtual attribute service handler."""
        await _async_verify_target_entity_control(hass, call)
        _LOGGER.info(f"{call.service} service called")
        await async_virtual_set_attributes_service(hass, call)

    async def async_virtual_service_clear_attributes(call) -> None:
        """Call virtual attribute clearing service handler."""
        await _async_verify_target_entity_control(hass, call)
        _LOGGER.info(f"{call.service} service called")
        await async_virtual_clear_attributes_service(hass, call)

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
        (CONF_CONFIGURATION_URL, "configuration_url"),
    ):
        if device.get(config_key):
            device_info[info_key] = device[config_key]
    device_entry = registry.async_get_or_create(**device_info)
    updates = _device_registry_updates_for_config(hass, device, device_entry)
    if updates:
        try:
            registry.async_update_device(device_entry.id, **updates)
        except ValueError as err:
            _LOGGER.warning(
                "Unable to synchronize device metadata for %s: %s",
                device_entry.id,
                err,
            )


@callback
def _async_remove_inactive_virtual_devices(
    hass: HomeAssistant,
    entry: ConfigEntry,
    active_device_ids: set[str],
) -> None:
    """Remove old Device registry entries after a Device ID is changed.

    Entity metadata is intentionally stable across Device ID changes, so it
    cannot be used alone to identify an old Device. Inspecting the registry
    entry ownership closes that gap without touching Devices used by another
    config entry.
    """
    registry = dr.async_get(hass)
    for device_entry in dr.async_entries_for_config_entry(registry, entry.entry_id):
        virtual_ids = {
            identifier[1]
            for identifier in device_entry.identifiers
            if identifier[0] == COMPONENT_DOMAIN
        }
        if not virtual_ids or virtual_ids & active_device_ids:
            continue
        if len(device_entry.config_entries) <= 1:
            registry.async_remove_device(device_entry.id)
        else:
            registry.async_update_device(
                device_entry.id,
                remove_config_entry_id=entry.entry_id,
            )


async def _async_delete_virtual_device_from_registry(
        hass: HomeAssistant, entry: ConfigEntry, device
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
        if len(device_in_registry.config_entries) <= 1:
            registery.async_remove_device(device_in_registry.id)
        else:
            registery.async_update_device(
                device_in_registry.id,
                remove_config_entry_id=entry.entry_id,
            )
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
