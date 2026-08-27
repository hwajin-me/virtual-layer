"""
This component provides support for virtual components.

"""

import asyncio
import inspect
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
from homeassistant.const import (
    ATTR_ENTITY_ID,
    CONF_ICON,
    CONF_PLATFORM,
    EVENT_HOMEASSISTANT_STARTED,
    STATE_UNAVAILABLE,
)
from homeassistant.core import CoreState, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, Unauthorized, UnknownUser
from homeassistant.helpers.entity import async_generate_entity_id
from homeassistant.helpers.event import (
    TrackTemplate,
    async_call_later,
    async_track_state_change_event,
    async_track_template_result,
    async_track_time_interval,
)
from homeassistant.helpers.json import json_bytes
from homeassistant.helpers.restore_state import async_get as async_get_restore_state
from homeassistant.helpers.template import Template, TemplateError

from .cfg import (
    BlendedCfg,
    _delete_meta_data,
)
from .const import *

_LOGGER = logging.getLogger(__name__)
_MISSING = object()
_MAX_SERVICE_PAYLOAD_DEPTH = 100

CONFIG_SCHEMA = cv.config_entry_only_config_schema(COMPONENT_DOMAIN)

SERVICE_AVAILABILE = 'set_available'
SERVICE_SCHEMA = vol.Schema({
    vol.Required(ATTR_ENTITY_ID): cv.comp_entity_ids,
    vol.Required('value'): cv.boolean,
})
SERVICE_SET_STATE = "set_state"
SERVICE_SET_ATTRIBUTES = "set_attributes"
SERVICE_CLEAR_ATTRIBUTES = "clear_attributes"


def _finite_service_payload(value, _seen=None, _depth=0):
    """Reject unsafe recursive values before they reach HA state or attributes."""
    if _depth > _MAX_SERVICE_PAYLOAD_DEPTH:
        raise vol.Invalid("State values may not be nested more than 100 levels")
    if isinstance(value, float) and not math.isfinite(value):
        raise vol.Invalid("NaN and infinity are not valid state values")
    if not isinstance(value, (Mapping, list, tuple, set)):
        return value

    if _seen is None:
        _seen = set()
    identity = id(value)
    if identity in _seen:
        raise vol.Invalid("State values may not contain recursive references")
    _seen.add(identity)
    try:
        if isinstance(value, Mapping):
            for key, item in value.items():
                _finite_service_payload(key, _seen, _depth + 1)
                _finite_service_payload(item, _seen, _depth + 1)
        else:
            for item in value:
                _finite_service_payload(item, _seen, _depth + 1)
    finally:
        _seen.remove(identity)
    return value


def _service_attributes(value):
    """Require valid attribute names and Home Assistant-serializable values."""
    if any(not isinstance(key, str) or not key for key in value):
        raise vol.Invalid("Attribute names must be non-empty strings")
    _finite_service_payload(value)
    try:
        json_bytes(value)
    except (OverflowError, RecursionError, TypeError, ValueError) as err:
        raise vol.Invalid("Attributes must be serializable by Home Assistant") from err
    return value


def _service_state_value(value):
    """Validate a scalar service state without coercing numbers to strings."""
    if not isinstance(value, (str, int, float, bool)):
        raise vol.Invalid("State value must be a string, number, or boolean")
    _finite_service_payload(value)
    try:
        json_bytes(value)
    except (OverflowError, RecursionError, TypeError, ValueError) as err:
        raise vol.Invalid("State value must be serializable by Home Assistant") from err
    return value


SERVICE_SET_STATE_SCHEMA = vol.Schema({
    vol.Required(ATTR_ENTITY_ID): cv.comp_entity_ids,
    vol.Required(ATTR_VALUE): _service_state_value,
})
SERVICE_SET_ATTRIBUTES_SCHEMA = vol.Schema({
    vol.Required(ATTR_ENTITY_ID): cv.comp_entity_ids,
    vol.Required(ATTR_ATTRIBUTES): vol.All(dict, _service_attributes),
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
_STATE_ONLY_RESTORE_PROXIES_DATA = f"{COMPONENT_DOMAIN}_state_only_restore_proxies"
_ENTITY_ID_GUARD_LISTENERS_DATA = f"{COMPONENT_DOMAIN}_entity_id_guard_listeners"
_DEVICE_METADATA_GUARD_LISTENERS_DATA = f"{COMPONENT_DOMAIN}_device_metadata_guard_listeners"
_GENERATED_NAME_SUFFIX = "_virtual_layer_generated_name_suffix"

_STATE_ONLY_LIST_NATIVE_PROPERTIES = frozenset({
    "supported_languages",
    "supported_options",
})
_STATE_ONLY_MAPPING_NATIVE_PROPERTIES = frozenset({
    "default_options",
    "tts_options",
})
_STATE_ONLY_BOOLEAN_NATIVE_PROPERTIES = frozenset({"supports_streaming"})


class _StateOnlyRestoreProxy:
    """Expose a state-only entity to Home Assistant's restore-state helper."""

    def __init__(self, entity_id: str) -> None:
        self.entity_id = entity_id

    @property
    def extra_restore_state_data(self):
        return None


def _async_ensure_runtime_data(hass: HomeAssistant) -> None:
    """Initialize integration runtime containers independently and idempotently."""
    hass.data.setdefault(COMPONENT_DOMAIN, {})
    hass.data.setdefault(COMPONENT_SERVICES, {})


def _configured_group_name(hass: HomeAssistant, entry: ConfigEntry) -> str:
    """Return a usable Device group name, repairing damaged entry data once."""
    configured_group_name = entry.data.get(ATTR_GROUP_NAME)
    if isinstance(configured_group_name, str):
        normalized_group_name = configured_group_name.strip()
        if normalized_group_name:
            if normalized_group_name != configured_group_name:
                data = dict(entry.data)
                data[ATTR_GROUP_NAME] = normalized_group_name
                hass.config_entries.async_update_entry(
                    entry,
                    title=normalized_group_name,
                    data=data,
                )
            return normalized_group_name

    # An entry without a group name used to fail before its options flow could
    # expose the malformed records for repair or deletion. The entry ID is
    # stable, unique, and safe to persist as a recovery-only Device name.
    recovered_group_name = f"recovered_{entry.entry_id}"
    _LOGGER.warning(
        "Virtual Layer config entry %s has no valid Device name; recovering it as %s",
        entry.entry_id,
        recovered_group_name,
    )
    data = dict(entry.data)
    data[ATTR_GROUP_NAME] = recovered_group_name
    hass.config_entries.async_update_entry(
        entry,
        title=recovered_group_name,
        data=data,
    )
    return recovered_group_name


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


def _runtime_group_for_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> tuple[object, Mapping | None]:
    """Return this entry's runtime group without borrowing another entry's data."""
    group_name = _runtime_group_name_for_entry(hass, entry)
    group_data = hass.data.get(COMPONENT_DOMAIN, {}).get(group_name)
    if (
        isinstance(group_data, Mapping)
        and group_data.get(ATTR_CONFIG_ENTRY_ID) in (None, entry.entry_id)
    ):
        return group_name, group_data
    return group_name, None


@callback
def _async_remove_runtime_group_for_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove runtime data only when it belongs to this config entry."""
    group_name, group_data = _runtime_group_for_entry(hass, entry)
    if group_data is not None:
        hass.data.get(COMPONENT_DOMAIN, {}).pop(group_name, None)


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
    _LOGGER.debug("Setting up Virtual Layer config entry %s", entry.entry_id)

    _async_ensure_runtime_data(hass)
    configured_group_name = _configured_group_name(hass, entry)
    legacy_title = f"{configured_group_name} - {COMPONENT_DOMAIN}"
    if entry.title == legacy_title:
        hass.config_entries.async_update_entry(entry, title=configured_group_name)
    configured_group = hass.data[COMPONENT_DOMAIN].get(configured_group_name)
    if (
        isinstance(configured_group, Mapping)
        and configured_group.get(ATTR_CONFIG_ENTRY_ID) not in (None, entry.entry_id)
    ):
        _LOGGER.error(
            "Cannot set up Device group %s because it is owned by config entry %s",
            configured_group_name,
            configured_group.get(ATTR_CONFIG_ENTRY_ID),
        )
        return False
    previous_group_name = _runtime_group_name_for_entry(hass, entry)

    # Remember which registry entries predate this setup. Newly configured
    # entities should start with their Device's current area explicitly set so
    # Home Assistant does not enable "Use device area" for them by default.
    entity_registry = er.async_get(hass)
    existing_entity_unique_ids = {
        entity_entry.unique_id
        for entity_entry in er.async_entries_for_config_entry(
            entity_registry,
            entry.entry_id,
        )
    }

    # Get the config.
    _LOGGER.debug("creating new cfg")
    vcfg = BlendedCfg(hass, entry.data, entry.options, entry)
    await vcfg.async_load()

    # create the devices.
    _LOGGER.debug("creating the devices")
    for device in vcfg.devices:
        _LOGGER.debug("Creating virtual Device %s", device.get(ATTR_DEVICE_ID))
        await _async_get_or_create_virtual_device_in_registry(hass, entry, device)
    _async_sync_active_entity_registry_entries(
        hass,
        entry,
        vcfg.entities,
        existing_entity_unique_ids=existing_entity_unique_ids,
    )
    _async_remove_orphaned_diagnostic_registry_entries(hass, entry, vcfg.entities)

    # Delete orphaned devices.
    active_device_ids = {
        device[ATTR_DEVICE_ID]
        for device in vcfg.devices
    }
    active_entity_unique_ids = {
        entity[ATTR_UNIQUE_ID]
        for platform_entities in vcfg.entities.values()
        if isinstance(platform_entities, list)
        for entity in platform_entities
        if isinstance(entity, Mapping)
        and isinstance(entity.get(ATTR_UNIQUE_ID), str)
    }
    for switch, device in vcfg.orphaned_entities.items():
        _LOGGER.debug(f"deleting {switch}/{device}")
        await _async_delete_virtual_entity_from_registry(
            hass,
            entry,
            device,
            active_device_ids,
            active_entity_unique_ids,
        )

    # Update the component data.
    hass.data[COMPONENT_DOMAIN].update({
        configured_group_name: {
            ATTR_CONFIG_ENTRY_ID: entry.entry_id,
            ATTR_ENTITIES: vcfg.entities,
            ATTR_DEVICES: vcfg.devices,
        }
    })
    if previous_group_name != configured_group_name:
        hass.data[COMPONENT_DOMAIN].pop(previous_group_name, None)
    _LOGGER.debug("Updated runtime data for Device group %s", configured_group_name)
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
            _async_remove_runtime_group_for_entry(hass, entry)

    _async_setup_entity_id_guard(hass, entry, vcfg.entities)
    _async_setup_device_metadata_guard(hass, entry, vcfg.devices)
    _async_remove_inactive_virtual_devices(hass, entry, active_device_ids)

    # Install service handlers.
    _async_register_virtual_services(hass)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.debug("unloading virtual group %s", entry.data.get(ATTR_GROUP_NAME))
    # _LOGGER.debug(f"before hass={hass.data[COMPONENT_DOMAIN]}")
    _runtime_group_name, group_data = _runtime_group_for_entry(hass, entry)
    group_data = group_data or {}
    platforms = _entry_platforms_from_entities(group_data.get(ATTR_ENTITIES, {}))
    unload_ok = True
    if platforms:
        unload_ok = await hass.config_entries.async_unload_platforms(entry, platforms)
    if unload_ok:
        _LOGGER.debug("unloaded ok")
        _async_remove_entity_id_guard(hass, entry.entry_id)
        _async_remove_device_metadata_guard(hass, entry.entry_id)
        _async_unload_state_only_entities(hass, entry, group_data.get(ATTR_ENTITIES, {}))
        _async_remove_runtime_group_for_entry(hass, entry)
        # _LOGGER.debug(f"ocfg={ocfg}")
    # _LOGGER.debug(f"after hass={hass.data[COMPONENT_DOMAIN]}")

    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clean up Virtual Layer data when a config entry is removed."""
    group_name = entry.data.get(ATTR_GROUP_NAME)
    _LOGGER.debug("removing virtual group %s", group_name)

    _runtime_group_name, group_data = _runtime_group_for_entry(hass, entry)
    group_data = group_data or {}
    _async_remove_entity_id_guard(hass, entry.entry_id)
    _async_remove_device_metadata_guard(hass, entry.entry_id)
    _async_unload_state_only_entities(hass, entry, group_data.get(ATTR_ENTITIES, {}))
    _async_remove_entry_registry_entries(hass, entry)

    if group_name:
        await _delete_meta_data(hass, group_name)
        _async_remove_runtime_group_for_entry(hass, entry)


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
def _async_update_generated_entity_name(
    hass,
    entity_registry,
    entity_id,
    generated_entity,
    name,
) -> None:
    """Update a generated entity's runtime and registry-owned default name."""
    if isinstance(generated_entity, dict):
        generated_entity[CONF_NAME] = name
    domain = entity_id.split(".", 1)[0]
    entity_component = hass.data.get(domain)
    loaded_entity = (
        entity_component.get_entity(entity_id)
        if entity_component is not None
        and callable(getattr(entity_component, "get_entity", None))
        else None
    )
    if loaded_entity is not None:
        loaded_entity._attr_name = name
        loaded_entity.async_write_ha_state()
        return
    registry_entry = entity_registry.async_get(entity_id)
    if registry_entry is not None and registry_entry.original_name != name:
        entity_registry.async_update_entity(entity_id, original_name=name)


@callback
def _async_remove_orphaned_diagnostic_registry_entries(hass, entry, entities) -> None:
    """Synchronize diagnostic registry defaults and remove obsolete diagnostics."""
    platform_entity_groups = entities.values() if isinstance(entities, Mapping) else []
    platform_entity_items = entities.items() if isinstance(entities, Mapping) else []
    active_generated_entities = {
        entity[ATTR_UNIQUE_ID]: entity
        for platform_entities in platform_entity_groups
        if isinstance(platform_entities, list)
        for entity in platform_entities
        if isinstance(entity, Mapping)
        and isinstance(entity.get(ATTR_UNIQUE_ID), str)
        and DIAGNOSTIC_UNIQUE_ID_MARKER in entity[ATTR_UNIQUE_ID]
    }
    active_primary_entities = {
        entity[ATTR_UNIQUE_ID]: (domain, entity)
        for domain, platform_entities in platform_entity_items
        if isinstance(platform_entities, list)
        for entity in platform_entities
        if isinstance(entity, Mapping)
        and isinstance(entity.get(ATTR_UNIQUE_ID), str)
        and DIAGNOSTIC_UNIQUE_ID_MARKER not in entity[ATTR_UNIQUE_ID]
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
        parent_unique_id = entity_entry.unique_id.split(
            DIAGNOSTIC_UNIQUE_ID_MARKER,
            1,
        )[0]
        parent_data = active_primary_entities.get(parent_unique_id)
        parent_domain, parent_entity = parent_data if parent_data else (None, None)
        parent_registry_id = entity_registry.async_get_entity_id(
            parent_domain,
            COMPONENT_DOMAIN,
            parent_unique_id,
        ) if parent_domain is not None else None
        parent_registry_entry = (
            entity_registry.async_get(parent_registry_id)
            if parent_registry_id is not None
            else None
        )
        configured_parent_name = (
            parent_entity.get(CONF_NAME)
            if isinstance(parent_entity, Mapping)
            else None
        )
        if (
            parent_registry_entry is not None
            and isinstance(configured_parent_name, str)
            and isinstance(generated_name, str)
            and generated_name.startswith(configured_parent_name)
        ):
            if isinstance(generated_entity, dict):
                generated_entity[_GENERATED_NAME_SUFFIX] = generated_name[
                    len(configured_parent_name):
                ]
            parent_name = parent_registry_entry.name or configured_parent_name
            generated_name = f"{parent_name}{generated_name[len(configured_parent_name):]}"
        if isinstance(generated_name, str):
            _async_update_generated_entity_name(
                hass,
                entity_registry,
                entity_entry.entity_id,
                generated_entity,
                generated_name,
            )
        generated_icon = generated_entity.get(CONF_ICON)
        if isinstance(generated_icon, str) and entity_entry.original_icon != generated_icon:
            updates["original_icon"] = generated_icon
        if updates:
            entity_registry.async_update_entity(entity_entry.entity_id, **updates)


@callback
def _async_sync_active_entity_registry_entries(
    hass,
    entry,
    entities,
    *,
    existing_entity_unique_ids=frozenset(),
) -> None:
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
                if device_entry is not None:
                    if entity_entry.device_id != device_entry.id:
                        updates["device_id"] = device_entry.id
                    if (
                        unique_id not in existing_entity_unique_ids
                        and entity_entry.area_id is None
                        and device_entry.area_id is not None
                    ):
                        updates["area_id"] = device_entry.area_id

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
    """Keep configured IDs fixed and cascade primary display-name changes."""
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
    configured_entities = {
        entity[ATTR_UNIQUE_ID]: (domain, entity)
        for domain, platform_entities in entities.items()
        if isinstance(platform_entities, list)
        for entity in platform_entities
        if isinstance(entity, Mapping)
        and isinstance(entity.get(ATTR_UNIQUE_ID), str)
    }
    generated_entities_by_parent = {}
    for unique_id, (domain, entity) in configured_entities.items():
        if DIAGNOSTIC_UNIQUE_ID_MARKER not in unique_id:
            continue
        parent_unique_id = unique_id.split(DIAGNOSTIC_UNIQUE_ID_MARKER, 1)[0]
        parent_data = configured_entities.get(parent_unique_id)
        configured_name = parent_data[1].get(CONF_NAME) if parent_data else None
        generated_name = entity.get(CONF_NAME)
        name_suffix = entity.get(_GENERATED_NAME_SUFFIX)
        if (
            not isinstance(configured_name, str)
            or not isinstance(generated_name, str)
            or (
                not isinstance(name_suffix, str)
                and not generated_name.startswith(configured_name)
            )
        ):
            continue
        if not isinstance(name_suffix, str):
            name_suffix = generated_name[len(configured_name):]
        generated_entities_by_parent.setdefault(parent_unique_id, []).append(
            (domain, entity, name_suffix)
        )
    if not desired_entity_ids:
        return

    entity_registry = er.async_get(hass)
    remove_listener = None

    async def _async_restore_configured_entity_id(current_entity_id) -> None:
        # Let the entity platform complete its rename first so the old state ID
        # is released before the registry moves the entity back.
        await asyncio.sleep(0)
        if (
            hass.data.get(_ENTITY_ID_GUARD_LISTENERS_DATA, {}).get(entry.entry_id)
            is not remove_listener
        ):
            return
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
        if event.data.get("action") != "update":
            return
        current_entity_id = event.data[ATTR_ENTITY_ID]
        if "old_entity_id" in event.data:
            hass.async_create_task(
                _async_restore_configured_entity_id(current_entity_id)
            )
        if "name" not in event.data.get("changes", {}):
            return
        primary_entry = entity_registry.async_get(current_entity_id)
        if (
            primary_entry is None
            or primary_entry.config_entry_id != entry.entry_id
            or primary_entry.unique_id not in generated_entities_by_parent
        ):
            return
        _primary_domain, primary_config = configured_entities[primary_entry.unique_id]
        configured_name = primary_config.get(CONF_NAME)
        if not isinstance(configured_name, str):
            return
        primary_name = primary_entry.name or configured_name
        for generated_domain, generated_entity, name_suffix in generated_entities_by_parent[
            primary_entry.unique_id
        ]:
            generated_unique_id = generated_entity.get(ATTR_UNIQUE_ID)
            if not isinstance(generated_unique_id, str):
                continue
            generated_entity_id = entity_registry.async_get_entity_id(
                generated_domain,
                COMPONENT_DOMAIN,
                generated_unique_id,
            )
            generated_entry = (
                entity_registry.async_get(generated_entity_id)
                if generated_entity_id is not None
                else None
            )
            if generated_entry is None or generated_entry.config_entry_id != entry.entry_id:
                continue
            cascaded_name = f"{primary_name}{name_suffix}"
            if generated_entry.original_name != cascaded_name:
                _async_update_generated_entity_name(
                    hass,
                    entity_registry,
                    generated_entity_id,
                    generated_entity,
                    cascaded_name,
                )

    listeners = hass.data.setdefault(_ENTITY_ID_GUARD_LISTENERS_DATA, {})
    remove_listener = hass.bus.async_listen(
        er.EVENT_ENTITY_REGISTRY_UPDATED,
        _async_handle_registry_update,
    )
    listeners[entry.entry_id] = remove_listener


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
        "sw_version": device.get(CONF_SW_VERSION) or '0.0.1',
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


def _device_metadata_owner_entry_id(hass, registry_entry) -> str | None:
    """Choose one Virtual Layer entry to guard metadata on a shared Device."""
    entry_ids = [
        entry_id
        for entry_id in registry_entry.config_entries
        if (config_entry := hass.config_entries.async_get_entry(entry_id)) is not None
        and config_entry.domain == COMPONENT_DOMAIN
    ]
    return min(entry_ids) if entry_ids else None


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

    remove_listener = None

    async def _async_restore_device_metadata(device_id) -> None:
        await asyncio.sleep(0)
        if (
            hass.data.get(_DEVICE_METADATA_GUARD_LISTENERS_DATA, {}).get(
                entry.entry_id
            )
            is not remove_listener
        ):
            return
        registry_entry = registry.async_get(device_id)
        device = devices_by_registry_id.get(device_id)
        if registry_entry is None or device is None:
            return
        if _device_metadata_owner_entry_id(hass, registry_entry) != entry.entry_id:
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
    remove_listener = hass.bus.async_listen(
        dr.EVENT_DEVICE_REGISTRY_UPDATED,
        _async_handle_device_registry_update,
    )
    listeners[entry.entry_id] = remove_listener


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
    managed_attributes = _state_only_managed_attribute_names(entity)
    attributes = {
        ATTR_PERSISTENT: entity.get(CONF_PERSISTENT),
        ATTR_AVAILABLE: entity.get(CONF_INITIAL_AVAILABILITY, DEFAULT_AVAILABILITY),
        ATTR_VIRTUAL_ATTRIBUTES: sorted(managed_attributes),
    }
    configured_attributes = entity.get(CONF_ATTRIBUTES, {})
    if isinstance(configured_attributes, Mapping):
        attributes.update({
            name: value
            for name, value in configured_attributes.items()
            if name not in EXCLUDED_VIRTUAL_ATTRIBUTE_NAMES
        })
    attributes.update(generic_entity_options(entity))
    return attributes


def _state_only_managed_attribute_names(entity) -> set[str]:
    """Return state attributes owned by the current state-only configuration."""
    managed = set(generic_entity_options(entity))
    for field_name in (
        CONF_ATTRIBUTES,
        CONF_ATTRIBUTE_SOURCES,
        CONF_ATTRIBUTE_TEMPLATES,
        CONF_NATIVE_TEMPLATES,
    ):
        values = entity.get(field_name, {})
        if isinstance(values, Mapping):
            managed.update(
                name
                for name in values
                if isinstance(name, str)
                and name not in (
                    RESERVED_VIRTUAL_ATTRIBUTE_NAMES
                    if field_name == CONF_NATIVE_TEMPLATES
                    else EXCLUDED_VIRTUAL_ATTRIBUTE_NAMES
                )
            )
    return managed


@callback
def _async_setup_state_only_entities(hass, entry, entities) -> None:
    if not isinstance(entities, Mapping):
        return
    _async_remove_state_only_template_listeners(hass, entry.entry_id)
    _async_remove_state_only_restore_proxies(hass, entry.entry_id)
    for domain in STATE_ONLY_ENTITY_DOMAINS:
        for entity in entities.get(domain, []):
            if not isinstance(entity, Mapping):
                continue
            entity_id = _async_register_state_only_entity(hass, entry, entity)
            if entity_id is None:
                continue
            state_value, attributes = _state_only_initial_state(hass, entity)
            hass.states.async_set(
                entity_id,
                state_value,
                attributes,
            )
            _async_register_state_only_restore_proxy(hass, entry.entry_id, entity)
            _async_setup_state_only_templates(hass, entry, entity)


@callback
def _async_unload_state_only_entities(hass, entry, entities) -> None:
    _async_remove_state_only_template_listeners(hass, entry.entry_id)
    _async_remove_state_only_restore_proxies(hass, entry.entry_id)
    if not isinstance(entities, Mapping):
        return
    for domain in STATE_ONLY_ENTITY_DOMAINS:
        for entity in entities.get(domain, []):
            if isinstance(entity, Mapping) and entity.get(ATTR_ENTITY_ID):
                hass.states.async_remove(entity[ATTR_ENTITY_ID])


def _state_only_initial_state(hass, entity) -> tuple[object, dict]:
    """Return configured or restored state for a state-only virtual entity."""
    entity_id = entity.get(ATTR_ENTITY_ID)
    value = entity.get(CONF_INITIAL_VALUE, "unknown")
    attributes = _state_only_attributes(entity)
    restore_data = async_get_restore_state(hass)

    if not entity.get(CONF_PERSISTENT, DEFAULT_PERSISTENT):
        if entity_id:
            restore_data.last_states.pop(entity_id, None)
        return value, attributes

    stored = restore_data.last_states.get(entity_id) if entity_id else None
    if stored is None:
        return value, attributes

    value = stored.state.state
    if str(value).strip().lower() == STATE_UNAVAILABLE:
        value = entity.get(CONF_INITIAL_VALUE, "unknown")
    restored_attributes = dict(stored.state.attributes)
    for name in TRANSIENT_SOURCE_ATTRIBUTE_NAMES:
        restored_attributes.pop(name, None)
    previous_managed = restored_attributes.pop(ATTR_VIRTUAL_ATTRIBUTES, [])
    current_managed = _state_only_managed_attribute_names(entity)
    if isinstance(previous_managed, (list, tuple, set)):
        for name in previous_managed:
            if isinstance(name, str) and name not in current_managed:
                restored_attributes.pop(name, None)
    attributes.update(restored_attributes)
    attributes[ATTR_PERSISTENT] = True
    attributes[ATTR_VIRTUAL_ATTRIBUTES] = sorted(current_managed)
    attributes.setdefault(
        ATTR_AVAILABLE,
        entity.get(CONF_INITIAL_AVAILABILITY, DEFAULT_AVAILABILITY),
    )
    attributes.update(generic_entity_options(entity))
    return value, attributes


@callback
def _async_register_state_only_restore_proxy(hass, entry_id, entity) -> None:
    """Register a state-only entity so HA saves it during shutdown."""
    entity_id = entity.get(ATTR_ENTITY_ID)
    if (
        not entity_id
        or not entity.get(CONF_PERSISTENT, DEFAULT_PERSISTENT)
    ):
        return
    proxy = _StateOnlyRestoreProxy(entity_id)
    proxies = hass.data.setdefault(
        _STATE_ONLY_RESTORE_PROXIES_DATA,
        {},
    ).setdefault(entry_id, {})
    proxies[entity_id] = proxy
    async_get_restore_state(hass).async_restore_entity_added(proxy)


@callback
def _async_remove_state_only_restore_proxies(hass, entry_id) -> None:
    """Snapshot and unregister state-only restore proxies before removal."""
    entry_proxies = hass.data.get(_STATE_ONLY_RESTORE_PROXIES_DATA)
    proxies = entry_proxies.pop(entry_id, {}) if entry_proxies else {}
    restore_data = async_get_restore_state(hass)
    for entity_id, proxy in proxies.items():
        if restore_data.entities.get(entity_id) is proxy:
            remove = restore_data.async_restore_entity_removed
            if "state" in inspect.signature(remove).parameters:
                remove(entity_id, hass.states.get(entity_id), None)
            else:
                remove(entity_id, None)
    if entry_proxies == {}:
        hass.data.pop(_STATE_ONLY_RESTORE_PROXIES_DATA, None)


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


def _render_state_only_template(
    hass,
    entity,
    template,
    extra_variables=None,
    *,
    parse_result=False,
):
    variables = _state_only_template_variables(hass, entity)
    if extra_variables:
        variables.update(extra_variables)
    return Template(str(template), hass).async_render(
        variables=variables,
        parse_result=parse_result,
    )


def _state_only_native_template_value(name: str, value):
    """Validate native values used by domains without an Entity platform."""
    if name in _STATE_ONLY_LIST_NATIVE_PROPERTIES:
        if not isinstance(value, (list, tuple, set)):
            raise ValueError(f"{name} must render a list")
        result = [str(item).strip() for item in value if str(item).strip()]
        if len(result) != len(set(result)):
            raise ValueError(f"{name} contains duplicate values")
        return result
    if name in _STATE_ONLY_MAPPING_NATIVE_PROPERTIES:
        if not isinstance(value, Mapping):
            raise ValueError(f"{name} must render an object")
        return dict(value)
    if name in _STATE_ONLY_BOOLEAN_NATIVE_PROPERTIES:
        return cv.boolean(value)
    if name == "supported_features":
        if isinstance(value, bool):
            raise ValueError("supported_features must be a non-negative integer")
        try:
            value = int(value)
        except (TypeError, ValueError, OverflowError) as err:
            raise ValueError(
                "supported_features must be a non-negative integer"
            ) from err
        if value < 0:
            raise ValueError("supported_features must be a non-negative integer")
        return value
    if name in {"latitude", "longitude"}:
        if isinstance(value, bool):
            raise ValueError(f"{name} is outside its valid range")
        try:
            value = float(value)
        except (TypeError, ValueError, OverflowError) as err:
            raise ValueError(f"{name} is outside its valid range") from err
        limit = 90 if name == "latitude" else 180
        if not math.isfinite(value) or not -limit <= value <= limit:
            raise ValueError(f"{name} is outside its valid range")
        return value
    if name == "confidence":
        if isinstance(value, bool):
            raise ValueError("confidence must be between 0 and 100")
        try:
            value = float(value)
        except (TypeError, ValueError, OverflowError) as err:
            raise ValueError("confidence must be between 0 and 100") from err
        if not math.isfinite(value) or not 0 <= value <= 100:
            raise ValueError("confidence must be between 0 and 100")
        return value
    return value


def _state_only_template_to_bool(value) -> bool:
    """Parse a template result without treating arbitrary text as false."""
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"y", "yes", "t", "true", "on", "1"}:
        return True
    if normalized in {"n", "no", "f", "false", "off", "0"}:
        return False
    raise ValueError(f"Expected a boolean, got {value!r}")


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
    for name, configured_value in domain_options.items():
        changed = attributes.get(name, _MISSING) != configured_value or changed
        attributes[name] = configured_value

    availability_rendered = False
    if entity.get(CONF_AVAILABILITY_TEMPLATE):
        try:
            available = _state_only_template_to_bool(
                _render_state_only_template(
                    hass,
                    entity,
                    entity[CONF_AVAILABILITY_TEMPLATE],
                )
            )
            availability_rendered = True
            changed = attributes.get(ATTR_AVAILABLE) != available or changed
            attributes[ATTR_AVAILABLE] = available
        except (OverflowError, TemplateError, TypeError, ValueError) as err:
            _LOGGER.warning(
                "Unable to render availability template for state-only entity %s: %s",
                entity_id,
                err,
            )

    apply_state_templates = not (
        availability_rendered and not attributes.get(ATTR_AVAILABLE, True)
    )

    if entity.get(CONF_VALUE_TEMPLATE) and apply_state_templates:
        try:
            value = _render_state_only_template(hass, entity, entity[CONF_VALUE_TEMPLATE])
            changed = value != state.state or changed
        except (OverflowError, TemplateError, TypeError, ValueError) as err:
            _LOGGER.warning(
                "Unable to render value template for state-only entity %s: %s",
                entity_id,
                err,
            )

    if entity.get(CONF_ICON_TEMPLATE):
        try:
            rendered_icon = str(
                _render_state_only_template(
                    hass,
                    entity,
                    entity[CONF_ICON_TEMPLATE],
                ),
            ).strip()
            next_icon = rendered_icon or entity.get(CONF_ICON)
            changed = attributes.get(CONF_ICON) != next_icon or changed
            if next_icon:
                attributes[CONF_ICON] = next_icon
            else:
                attributes.pop(CONF_ICON, None)
        except (OverflowError, TemplateError, TypeError, ValueError) as err:
            _LOGGER.warning(
                "Unable to render icon template for state-only entity %s: %s",
                entity_id,
                err,
            )

    native_templates = entity.get(CONF_NATIVE_TEMPLATES, {})
    if apply_state_templates and isinstance(native_templates, Mapping):
        for name, template in native_templates.items():
            if not isinstance(name, str) or not name or not template:
                continue
            try:
                rendered = _state_only_native_template_value(
                    name,
                    _render_state_only_template(
                        hass,
                        entity,
                        template,
                        parse_result=True,
                    ),
                )
            except (
                OverflowError,
                TemplateError,
                TypeError,
                ValueError,
                vol.Invalid,
            ) as err:
                _LOGGER.warning(
                    "Unable to render native template %s for %s: %s",
                    name,
                    entity_id,
                    err,
                )
                continue
            changed = attributes.get(name, _MISSING) != rendered or changed
            attributes[name] = rendered

    attribute_templates = entity.get(CONF_ATTRIBUTE_TEMPLATES, {})
    if isinstance(attribute_templates, Mapping):
        for name, template in attribute_templates.items():
            if name in EXCLUDED_VIRTUAL_ATTRIBUTE_NAMES or name in domain_options:
                continue
            try:
                rendered = _render_state_only_template(
                    hass,
                    entity,
                    template,
                    parse_result=True,
                )
            except (OverflowError, TemplateError, TypeError, ValueError) as err:
                _LOGGER.warning(
                    "Unable to render attribute template %s for state-only entity %s: %s",
                    name,
                    entity_id,
                    err,
                )
                continue
            changed = attributes.get(name, _MISSING) != rendered or changed
            attributes[name] = rendered

    attribute_sources = entity.get(CONF_ATTRIBUTE_SOURCES, {})
    if isinstance(attribute_sources, Mapping):
        for name, source in attribute_sources.items():
            if name in EXCLUDED_VIRTUAL_ATTRIBUTE_NAMES or name in domain_options:
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
            changed = attributes.get(name, _MISSING) != source_value or changed
            attributes[name] = source_value

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

    if hook.get(CONF_AVAILABILITY_TEMPLATE):
        try:
            available = _state_only_template_to_bool(
                _render_state_only_template(
                    hass,
                    entity,
                    hook[CONF_AVAILABILITY_TEMPLATE],
                    variables,
                )
            )
            changed = attributes.get(ATTR_AVAILABLE) != available or changed
            attributes[ATTR_AVAILABLE] = available
        except (OverflowError, TemplateError, TypeError, ValueError) as err:
            _LOGGER.warning(
                "Unable to render event hook availability template for "
                "state-only entity %s: %s",
                entity_id,
                err,
            )

    if hook.get(CONF_VALUE_TEMPLATE):
        try:
            value = _render_state_only_template(
                hass,
                entity,
                hook[CONF_VALUE_TEMPLATE],
                variables,
            )
            changed = value != state.state or changed
        except (OverflowError, TemplateError, TypeError, ValueError) as err:
            _LOGGER.warning(
                "Unable to render event hook value template for state-only entity %s: %s",
                entity_id,
                err,
            )

    configured_attributes = hook.get(CONF_ATTRIBUTES, {})
    if isinstance(configured_attributes, Mapping):
        for name, configured_value in configured_attributes.items():
            if name in EXCLUDED_VIRTUAL_ATTRIBUTE_NAMES or name in domain_options:
                continue
            changed = attributes.get(name, _MISSING) != configured_value or changed
            attributes[name] = configured_value

    attribute_templates = hook.get(CONF_ATTRIBUTE_TEMPLATES, {})
    if isinstance(attribute_templates, Mapping):
        for name, template in attribute_templates.items():
            if name in EXCLUDED_VIRTUAL_ATTRIBUTE_NAMES or name in domain_options:
                continue
            try:
                rendered = _render_state_only_template(
                    hass,
                    entity,
                    template,
                    variables,
                    parse_result=True,
                )
            except (OverflowError, TemplateError, TypeError, ValueError) as err:
                _LOGGER.warning(
                    "Unable to render event hook attribute template %s for "
                    "state-only entity %s: %s",
                    name,
                    entity_id,
                    err,
                )
                continue
            changed = attributes.get(name, _MISSING) != rendered or changed
            attributes[name] = rendered

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
            if isinstance(hook.get("debounce", 0), bool):
                raise TypeError
            delay = float(hook.get("debounce", 0) or 0)
        except (TypeError, ValueError, OverflowError):
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
    else:
        attribute_templates = {
            name: template
            for name, template in attribute_templates.items()
            if name not in EXCLUDED_VIRTUAL_ATTRIBUTE_NAMES
        }
    templates = [
        template
        for template in (
            entity.get(CONF_VALUE_TEMPLATE),
            entity.get(CONF_AVAILABILITY_TEMPLATE),
            entity.get(CONF_ICON_TEMPLATE),
            *attribute_templates.values(),
            *(
                entity.get(CONF_NATIVE_TEMPLATES, {}).values()
                if isinstance(entity.get(CONF_NATIVE_TEMPLATES), Mapping)
                else ()
            ),
        )
        if template
    ]
    if templates:
        listeners.append(async_track_template_result(
            hass,
            [
                TrackTemplate(
                    Template(str(template), hass),
                    _state_only_template_variables(hass, entity),
                )
                for template in templates
            ],
            lambda _event, _updates: _async_apply_state_only_templates(hass, entity),
        ).async_remove)
        if hass.state is not CoreState.running:
            listeners.append(hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STARTED,
                lambda _event: _async_apply_state_only_templates(hass, entity),
            ))

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
    if _get_state_only_entity_config(hass, entity_id) is not None:
        return
    entity_entry = er.async_get(hass).async_get(entity_id)
    if entity_entry is None or entity_entry.platform != COMPONENT_DOMAIN:
        raise HomeAssistantError(f"{entity_id} is not managed by virtual_layer")


def _assert_managed_virtual_entities(hass, entity_ids) -> None:
    """Validate every service target before applying any state change."""
    for entity_id in entity_ids:
        _assert_managed_virtual_entity(hass, entity_id)


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
        original_name=entity.get(CONF_NAME),
        original_icon=entity.get(CONF_ICON),
    )
    entity[ATTR_ENTITY_ID] = entity_entry.entity_id
    if entity_entry.disabled:
        _LOGGER.debug(
            "Skipping disabled state-only virtual entity %s",
            entity_entry.entity_id,
        )
        return None
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

    entity_ids = call.data['entity_id']
    _assert_managed_virtual_entities(hass, entity_ids)
    for entity_id in entity_ids:
        domain = entity_id.split(".")[0]
        _LOGGER.debug("Setting availability for %s", entity_id)
        if _is_state_only_entity_id(entity_id):
            _async_set_state_only_entity(hass, entity_id, attributes={ATTR_AVAILABLE: value})
            continue
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
        _LOGGER.debug("%s service called", call.service)
        await async_virtual_set_availability_service(hass, call)

    async def async_virtual_service_set_state(call) -> None:
        """Call virtual state service handler."""
        await _async_verify_target_entity_control(hass, call)
        _LOGGER.debug("%s service called", call.service)
        await async_virtual_set_state_service(hass, call)

    async def async_virtual_service_set_attributes(call) -> None:
        """Call virtual attribute service handler."""
        await _async_verify_target_entity_control(hass, call)
        _LOGGER.debug("%s service called", call.service)
        await async_virtual_set_attributes_service(hass, call)

    async def async_virtual_service_clear_attributes(call) -> None:
        """Call virtual attribute clearing service handler."""
        await _async_verify_target_entity_control(hass, call)
        _LOGGER.debug("%s service called", call.service)
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
    entity_ids = call.data[ATTR_ENTITY_ID]
    _assert_managed_virtual_entities(hass, entity_ids)
    for entity_id in entity_ids:
        domain = entity_id.split(".")[0]
        _LOGGER.debug("Setting state for %s", entity_id)
        if _is_state_only_entity_id(entity_id):
            _async_set_state_only_entity(hass, entity_id, value=value)
            continue
        entity = get_entity_from_domain(hass, domain, entity_id)
        await _async_apply_virtual_state(entity, value)


async def async_virtual_set_attributes_service(hass, call):
    attributes = {
        name: value
        for name, value in call.data[ATTR_ATTRIBUTES].items()
        if name not in EXCLUDED_VIRTUAL_ATTRIBUTE_NAMES
    }
    entity_ids = call.data[ATTR_ENTITY_ID]
    _assert_managed_virtual_entities(hass, entity_ids)
    for entity_id in entity_ids:
        domain = entity_id.split(".")[0]
        _LOGGER.debug("Setting %s attributes for %s", len(attributes), entity_id)
        if _is_state_only_entity_id(entity_id):
            _async_set_state_only_entity(hass, entity_id, attributes=attributes)
            continue
        get_entity_from_domain(hass, domain, entity_id).set_attributes(attributes)


async def async_virtual_clear_attributes_service(hass, call):
    attributes = call.data[ATTR_ATTRIBUTES]
    entity_ids = call.data[ATTR_ENTITY_ID]
    _assert_managed_virtual_entities(hass, entity_ids)
    for entity_id in entity_ids:
        domain = entity_id.split(".")[0]
        _LOGGER.debug("Clearing attributes for %s", entity_id)
        if _is_state_only_entity_id(entity_id):
            _async_clear_state_only_attributes(hass, entity_id, attributes)
            continue
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
        "sw_version": device.get(CONF_SW_VERSION) or "0.0.1",
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
    if device_in_registry and entry.entry_id in device_in_registry.config_entries:
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
        hass: HomeAssistant,
        entry: ConfigEntry,
        entity,
        active_device_ids,
        active_entity_unique_ids=None,
) -> None:
    """Remove stale registry data without trusting damaged metadata ownership."""
    unique_id = entity.get(ATTR_UNIQUE_ID)
    platform = entity.get(CONF_PLATFORM)
    active_entity_unique_ids = active_entity_unique_ids or set()
    if (
        isinstance(unique_id, str)
        and unique_id
        and unique_id not in active_entity_unique_ids
        and isinstance(platform, str)
        and platform
    ):
        registry = er.async_get(hass)
        entity_id = registry.async_get_entity_id(
            platform,
            COMPONENT_DOMAIN,
            unique_id,
        )
        entity_entry = registry.async_get(entity_id) if entity_id else None
        if entity_entry is not None and entity_entry.config_entry_id == entry.entry_id:
            _LOGGER.debug("removing orphaned entity %s", entity_id)
            registry.async_remove(entity_id)

    device_id = entity.get(ATTR_DEVICE_ID)
    if device_id and device_id not in active_device_ids:
        await _async_delete_virtual_device_from_registry(hass, entry, entity)
