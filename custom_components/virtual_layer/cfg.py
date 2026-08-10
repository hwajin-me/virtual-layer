"""Handles Virtual Layer config-entry backed configuration."""

import asyncio
import copy
import json
import logging
import math
import os
import uuid
from collections.abc import Mapping
from importlib import import_module

import aiofiles
import aiofiles.os
import homeassistant.helpers.entity_registry as er
import voluptuous as vol
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_FRIENDLY_NAME,
    CONF_ICON,
    CONF_PLATFORM,
    CONF_UNIT_OF_MEASUREMENT,
    PERCENTAGE,
    Platform,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.util import slugify

from .const import *
from .entity import virtual_schema

_LOGGER = logging.getLogger(__name__)

BINARY_SENSOR_DEFAULT_INITIAL_VALUE = 'off'
BINARY_SENSOR_SCHEMA = vol.Schema(virtual_schema(BINARY_SENSOR_DEFAULT_INITIAL_VALUE, {
    vol.Optional(CONF_CLASS): cv.string,
}))

SENSOR_DEFAULT_INITIAL_VALUE = '0'
SENSOR_SCHEMA = vol.Schema(virtual_schema(SENSOR_DEFAULT_INITIAL_VALUE, {
    vol.Optional(CONF_CLASS): cv.string,
    vol.Optional(CONF_UNIT_OF_MEASUREMENT, default=""): cv.string,
}))

_meta_lock = asyncio.Lock()
STORAGE_VERSION = 1

_CLIMATE_INITIAL_VALUES = {
    "off",
    "heat",
    "cool",
    "heat_cool",
    "auto",
    "dry",
    "fan_only",
}
_DEFAULT_NUMBER_MIN = 0.0
_DEFAULT_NUMBER_MAX = 100.0


def _normalize_event_hooks(value, device_name, index):
    """Keep valid event hooks when loading versioned or damaged UI data."""
    if isinstance(value, Mapping):
        value = [
            {**dict(hook), "name": name}
            if isinstance(hook, Mapping) and "name" not in hook
            else hook
            for name, hook in value.items()
        ]
    if not isinstance(value, list):
        if value is not None:
            _LOGGER.warning(
                "Resetting invalid event hooks for device %s at index %s",
                device_name,
                index,
            )
        return []

    normalized = []
    for hook_index, stored_hook in enumerate(value):
        if not isinstance(stored_hook, Mapping):
            _LOGGER.warning(
                "Ignoring invalid event hook %s for device %s at index %s",
                hook_index,
                device_name,
                index,
            )
            continue

        hook = dict(stored_hook)
        trigger = str(hook.get("trigger", "state")).strip().lower()
        if trigger not in {"state", "event"}:
            _LOGGER.warning(
                "Ignoring event hook %s with invalid trigger for device %s at index %s",
                hook_index,
                device_name,
                index,
            )
            continue
        hook["trigger"] = trigger

        if "enabled" in hook:
            try:
                hook["enabled"] = cv.boolean(hook["enabled"])
            except vol.Invalid:
                hook["enabled"] = True

        if trigger == "state":
            entity_ids = hook.get(ATTR_ENTITY_ID, hook.get("entity_ids", []))
            if isinstance(entity_ids, str):
                entity_ids = [entity_ids]
            if not isinstance(entity_ids, list):
                entity_ids = []
            valid_entity_ids = []
            for source_entity_id in entity_ids:
                try:
                    source_entity_id = cv.entity_id(str(source_entity_id).strip())
                except vol.Invalid:
                    continue
                if source_entity_id not in valid_entity_ids:
                    valid_entity_ids.append(source_entity_id)
            if not valid_entity_ids:
                _LOGGER.warning(
                    "Ignoring state event hook %s without valid sources for device %s at index %s",
                    hook_index,
                    device_name,
                    index,
                )
                continue
            hook[ATTR_ENTITY_ID] = valid_entity_ids
            hook.pop("entity_ids", None)

            changed_attributes = hook.get(
                CONF_ATTRIBUTE,
                hook.get("attributes_changed"),
            )
            if isinstance(changed_attributes, str):
                changed_attributes = [changed_attributes]
            if changed_attributes is not None:
                if not isinstance(changed_attributes, list):
                    changed_attributes = []
                hook[CONF_ATTRIBUTE] = [
                    attribute
                    for attribute in changed_attributes
                    if isinstance(attribute, str) and attribute.strip()
                ]
            hook.pop("attributes_changed", None)
        else:
            event_type = str(hook.get("event_type", "")).strip()
            if not event_type:
                _LOGGER.warning(
                    "Ignoring event hook %s without an event type for device %s at index %s",
                    hook_index,
                    device_name,
                    index,
                )
                continue
            hook["event_type"] = event_type
            if "event_data" in hook and not isinstance(hook["event_data"], Mapping):
                hook.pop("event_data")
            elif isinstance(hook.get("event_data"), Mapping):
                hook["event_data"] = dict(hook["event_data"])

        for field_name in (CONF_ATTRIBUTES, CONF_ATTRIBUTE_TEMPLATES):
            field_value = hook.get(field_name)
            if not isinstance(field_value, Mapping):
                hook.pop(field_name, None)
                continue
            hook[field_name] = {
                name: field_value
                for name, field_value in field_value.items()
                if isinstance(name, str)
                and name.strip()
                and name.strip() not in RESERVED_VIRTUAL_ATTRIBUTE_NAMES
            }

        for field_name in (CONF_VALUE_TEMPLATE, CONF_AVAILABILITY_TEMPLATE):
            if field_name in hook and not isinstance(hook[field_name], str):
                hook.pop(field_name)

        if "debounce" in hook:
            try:
                debounce = float(hook["debounce"])
                if not math.isfinite(debounce):
                    raise ValueError
                hook["debounce"] = max(0, debounce)
            except (TypeError, ValueError):
                hook.pop("debounce")
        if "refresh" in hook:
            try:
                hook["refresh"] = cv.boolean(hook["refresh"])
            except vol.Invalid:
                hook.pop("refresh")

        normalized.append(hook)
    return normalized


def _as_dict(value, label):
    if isinstance(value, Mapping):
        return dict(value)
    if value not in (None, {}):
        _LOGGER.warning("Ignoring invalid %s payload: expected mapping", label)
    return {}


def _meta_payload_and_devices(payload):
    payload = _as_dict(payload, "metadata")
    if not payload:
        return {ATTR_VERSION: STORAGE_VERSION, ATTR_DEVICES: {}}, {}

    devices = payload.get(ATTR_DEVICES)
    if isinstance(devices, Mapping):
        return payload, dict(devices)

    if ATTR_VERSION not in payload and ATTR_DEVICES not in payload:
        return {ATTR_VERSION: STORAGE_VERSION, ATTR_DEVICES: dict(payload)}, dict(payload)

    if devices is not None:
        _LOGGER.warning("Ignoring invalid metadata devices payload: expected mapping")
    payload = dict(payload)
    payload[ATTR_DEVICES] = {}
    return payload, {}


def _meta_group(payload, group_name):
    _, devices = _meta_payload_and_devices(payload)
    group = devices.get(group_name, {})
    if isinstance(group, Mapping):
        return dict(group)
    _LOGGER.warning("Ignoring invalid metadata for group %s: expected mapping", group_name)
    return {}


async def _async_load_json(file_name):
    _LOGGER.debug("_async_load_json1 file_name for %s", file_name)
    try:
        async with aiofiles.open(file_name, 'r') as meta_file:
            _LOGGER.debug("_async_load_json2 file_name for %s", file_name)
            contents = await meta_file.read()
            _LOGGER.debug("_async_load_json3 file_name for %s", file_name)
            return json.loads(
                contents,
                parse_constant=lambda _value: None,
            )
    except FileNotFoundError:
        _LOGGER.debug("json file does not exist: %s", file_name)
        return {}
    except (OSError, json.JSONDecodeError):
        _LOGGER.exception("Unable to load json file: %s", file_name)
        return {}


async def _async_save_json(file_name, data):
    _LOGGER.debug("_async_save_json1 file_name for %s", file_name)
    temporary_file_name = f"{file_name}.{uuid.uuid4().hex}.tmp"
    try:
        directory = os.path.dirname(file_name)
        if directory:
            await aiofiles.os.makedirs(directory, exist_ok=True)
        async with aiofiles.open(temporary_file_name, 'w') as meta_file:
            data = json.dumps(data, indent=4, allow_nan=False)
            await meta_file.write(data)
        await aiofiles.os.replace(temporary_file_name, file_name)
    except OSError:
        _LOGGER.exception("Unable to save json file: %s", file_name)
        raise
    finally:
        try:
            await aiofiles.os.remove(temporary_file_name)
        except FileNotFoundError:
            pass


async def _load_meta_data(hass, group_name: str):
    """Read in meta data for a particular group.
    """
    async with _meta_lock:
        data = await _async_load_json(default_meta_file(hass))
        return _meta_group(data, group_name)


async def _save_meta_data(hass, group_name, meta_data):
    """Save meta data for a particular group name.
    """
    async with _meta_lock:

        # Read in current meta data
        payload, devices = _meta_payload_and_devices(
            await _async_load_json(default_meta_file(hass)),
        )

        # Update (or add) the group piece.
        _LOGGER.debug(f"meta before {devices}")
        devices.update({
            group_name: meta_data
        })
        _LOGGER.debug(f"meta after {devices}")

        # Write it back out.
        payload[ATTR_VERSION] = STORAGE_VERSION
        payload[ATTR_DEVICES] = devices
        await _async_save_json(default_meta_file(hass), payload)


async def _delete_meta_data(hass, group_name):
    """Save meta data for a particular group name.
    """
    async with _meta_lock:

        # Read in current meta data
        payload, devices = _meta_payload_and_devices(
            await _async_load_json(default_meta_file(hass)),
        )

        # Delete the group piece.
        _LOGGER.debug(f"meta before {devices}")
        devices.pop(group_name, None)
        _LOGGER.debug(f"meta after {devices}")

        # Write it back out.
        payload[ATTR_VERSION] = STORAGE_VERSION
        payload[ATTR_DEVICES] = devices
        await _async_save_json(default_meta_file(hass), payload)


async def _rename_meta_data(hass, old_group_name, new_group_name):
    """Move identity metadata when a config entry's group is renamed."""
    if old_group_name == new_group_name:
        return

    async with _meta_lock:
        payload, devices = _meta_payload_and_devices(
            await _async_load_json(default_meta_file(hass)),
        )
        old_group = devices.pop(old_group_name, None)
        if old_group is None:
            return

        new_group = devices.get(new_group_name)
        if isinstance(old_group, Mapping) and isinstance(new_group, Mapping):
            merged_group = dict(new_group)
            for key, value in old_group.items():
                merged_group.setdefault(key, value)
            devices[new_group_name] = merged_group
        else:
            devices[new_group_name] = old_group

        payload[ATTR_VERSION] = STORAGE_VERSION
        payload[ATTR_DEVICES] = devices
        await _async_save_json(default_meta_file(hass), payload)


def _make_original_unique_id(name):
    if name.startswith("+"):
        return slugify(name[1:])
    else:
        return slugify(name)


def _make_name(name):
    name = str(name)
    if name.startswith("+"):
        return name[1:]
    return name


def _device_config_for_key(device_name, device_attributes):
    device = copy.deepcopy(_as_dict(device_attributes, "device attributes").get(device_name, {}))
    device = _as_dict(device, f"device attributes for {device_name}")
    for key in (
        ATTR_DEVICE_ID,
        CONF_NAME,
        CONF_MANUFACTURER,
        CONF_MODEL,
        CONF_SW_VERSION,
        CONF_HW_VERSION,
        CONF_SERIAL_NUMBER,
        CONF_CONFIGURATION_URL,
        CONF_SUGGESTED_AREA,
        CONF_VIA_DEVICE_ID,
    ):
        value = device.get(key)
        if value is not None and not isinstance(value, (str, int, float)):
            _LOGGER.warning("Ignoring invalid %s for device %s", key, device_name)
            device.pop(key, None)
        elif value is not None:
            device[key] = str(value)
    if not device.get(ATTR_DEVICE_ID):
        device[ATTR_DEVICE_ID] = device_name
    if not device.get(CONF_NAME):
        device[CONF_NAME] = _make_name(device_name)
    return device


def _normalize_source_reference(source, default_attribute=None):
    """Return a safe persisted source reference, or ``None`` when invalid."""
    if isinstance(source, str):
        source = source.strip()
        if default_attribute is not None:
            try:
                return {
                    ATTR_ENTITY_ID: cv.entity_id(source),
                    CONF_ATTRIBUTE: default_attribute,
                }
            except vol.Invalid:
                pass
        entity_id, separator, attribute = source.rpartition(".")
        if not separator:
            return None
    elif isinstance(source, Mapping):
        entity_id = source.get(ATTR_ENTITY_ID)
        attribute = source.get(CONF_ATTRIBUTE, default_attribute)
    else:
        return None

    if not isinstance(entity_id, str) or not isinstance(attribute, str):
        return None
    entity_id = entity_id.strip()
    attribute = attribute.strip()
    if not entity_id or not attribute:
        return None
    try:
        entity_id = cv.entity_id(entity_id)
    except vol.Invalid:
        return None
    return {ATTR_ENTITY_ID: entity_id, CONF_ATTRIBUTE: attribute}


def _normalize_common_entity_config(entity, device_name, index):
    """Normalize versioned UI fields before platform validation."""
    entity = _sanitize_stored_value(dict(entity))
    name = entity.get(CONF_NAME)
    if not isinstance(name, str) or not name.strip():
        entity.pop(CONF_NAME, None)

    initial_value = entity.get(CONF_INITIAL_VALUE)
    if CONF_INITIAL_VALUE in entity and initial_value is None:
        _LOGGER.warning(
            "Resetting non-finite or null initial value for device %s at index %s",
            device_name,
            index,
        )
        entity[CONF_INITIAL_VALUE] = "unknown"
        initial_value = "unknown"
    if initial_value is not None and not isinstance(initial_value, (str, int, float, bool)):
        _LOGGER.warning(
            "Resetting invalid initial value for device %s at index %s",
            device_name,
            index,
        )
        entity[CONF_INITIAL_VALUE] = "unknown"

    if entity.get(CONF_PLATFORM) == "climate":
        climate_initial_value = str(entity.get(CONF_INITIAL_VALUE, "off")).lower()
        if climate_initial_value not in _CLIMATE_INITIAL_VALUES:
            _LOGGER.warning(
                "Resetting invalid climate initial value for device %s at index %s",
                device_name,
                index,
            )
            entity[CONF_INITIAL_VALUE] = "off"

    if entity.get(CONF_PLATFORM) == "number":
        for key, default in ((CONF_MIN, _DEFAULT_NUMBER_MIN), (CONF_MAX, _DEFAULT_NUMBER_MAX)):
            try:
                entity[key] = float(entity.get(key, default))
                if not math.isfinite(entity[key]):
                    raise ValueError
            except (TypeError, ValueError):
                _LOGGER.warning(
                    "Resetting invalid number %s for device %s at index %s",
                    key,
                    device_name,
                    index,
                )
                entity[key] = default
        if entity[CONF_MIN] > entity[CONF_MAX]:
            _LOGGER.warning(
                "Resetting inverted number range for device %s at index %s",
                device_name,
                index,
            )
            entity[CONF_MIN] = _DEFAULT_NUMBER_MIN
            entity[CONF_MAX] = _DEFAULT_NUMBER_MAX

    for key in (CONF_ATTRIBUTES, CONF_ATTRIBUTE_TEMPLATES, CONF_NATIVE_TEMPLATES):
        if key in entity and not isinstance(entity[key], Mapping):
            _LOGGER.warning(
                "Resetting invalid %s for device %s at index %s",
                key,
                device_name,
                index,
            )
            entity[key] = {}

    native_templates = entity.get(CONF_NATIVE_TEMPLATES, {})
    if isinstance(native_templates, Mapping):
        entity[CONF_NATIVE_TEMPLATES] = {
            name.strip(): template
            for name, template in native_templates.items()
            if isinstance(name, str)
            and name.strip().isidentifier()
            and not name.strip().startswith("_")
            and name.strip() not in RESERVED_NATIVE_TEMPLATE_NAMES
            and isinstance(template, str)
            and template.strip()
        }

    command_actions = entity.get(CONF_COMMAND_ACTIONS)
    if command_actions is not None:
        if not isinstance(command_actions, Mapping):
            _LOGGER.warning(
                "Resetting invalid command actions for device %s at index %s",
                device_name,
                index,
            )
            entity[CONF_COMMAND_ACTIONS] = {}
        else:
            normalized_actions = {}
            valid_commands = _platform_command_names(entity.get(CONF_PLATFORM))
            for command, spec in command_actions.items():
                if not isinstance(command, str) or not command.strip().isidentifier():
                    continue
                if command.strip() not in valid_commands:
                    continue
                sequence = spec
                if isinstance(spec, Mapping) and "sequence" in spec:
                    if set(spec) - {"sequence", "optimistic"}:
                        continue
                    if not isinstance(spec.get("optimistic", True), bool):
                        continue
                    sequence = spec.get("sequence")
                elif isinstance(spec, Mapping):
                    sequence = [dict(spec)]
                if not isinstance(sequence, list) or not sequence:
                    continue
                try:
                    cv.SCRIPT_SCHEMA(sequence)
                except vol.Invalid:
                    continue
                normalized_actions[command.strip()] = spec
            entity[CONF_COMMAND_ACTIONS] = normalized_actions

    for key, default_attribute in (
        (CONF_ATTRIBUTE_SOURCES, None),
        (CONF_TEMPLATE_SOURCES, "state"),
    ):
        if key not in entity:
            continue
        if not isinstance(entity[key], Mapping):
            _LOGGER.warning(
                "Resetting invalid %s for device %s at index %s",
                key,
                device_name,
                index,
            )
            entity[key] = {}
            continue

        sources = {}
        for name, source in entity[key].items():
            normalized_source = _normalize_source_reference(source, default_attribute)
            if (
                not isinstance(name, str)
                or not name.strip()
                or (key == CONF_ATTRIBUTE_SOURCES and name.strip() in RESERVED_VIRTUAL_ATTRIBUTE_NAMES)
                or normalized_source is None
            ):
                _LOGGER.warning(
                    "Ignoring invalid %s item for device %s at index %s",
                    key,
                    device_name,
                    index,
                )
                continue
            sources[name.strip()] = normalized_source
        entity[key] = sources

    if CONF_EVENT_HOOKS in entity:
        entity[CONF_EVENT_HOOKS] = _normalize_event_hooks(
            entity[CONF_EVENT_HOOKS],
            device_name,
            index,
        )

    source_entities = entity.get(CONF_SOURCE_ENTITIES)
    if source_entities is not None:
        if not isinstance(source_entities, list):
            source_entities = []
        valid_source_entities = []
        seen_source_entities = set()
        for source_entity_id in source_entities:
            try:
                source_entity_id = cv.entity_id(source_entity_id)
            except vol.Invalid:
                _LOGGER.warning(
                    "Ignoring invalid source entity for device %s at index %s",
                    device_name,
                    index,
                )
                continue
            if source_entity_id not in seen_source_entities:
                seen_source_entities.add(source_entity_id)
                valid_source_entities.append(source_entity_id)
        entity[CONF_SOURCE_ENTITIES] = valid_source_entities

    for key in (
        CONF_VALUE_TEMPLATE,
        CONF_AVAILABILITY_TEMPLATE,
        CONF_ICON_TEMPLATE,
    ):
        if key in entity and not isinstance(entity[key], str):
            _LOGGER.warning(
                "Ignoring invalid %s for device %s at index %s",
                key,
                device_name,
                index,
            )
            entity.pop(key, None)

    pull_interval = entity.get(CONF_PULL_INTERVAL)
    if pull_interval is not None:
        try:
            pull_interval = int(pull_interval)
        except (TypeError, ValueError):
            pull_interval = 0
        entity[CONF_PULL_INTERVAL] = max(0, pull_interval)

    for key, default in (
        (CONF_INITIAL_AVAILABILITY, DEFAULT_AVAILABILITY),
        (CONF_PERSISTENT, DEFAULT_PERSISTENT),
    ):
        if key not in entity:
            continue
        try:
            entity[key] = cv.boolean(entity[key])
        except vol.Invalid:
            _LOGGER.warning(
                "Resetting invalid %s for device %s at index %s",
                key,
                device_name,
                index,
            )
            entity[key] = default

    return entity


def _platform_command_names(platform) -> set[str]:
    """Return action-capable native commands implemented for a domain."""
    return set(VIRTUAL_ENTITY_COMMANDS.get(platform, ()))


def _sanitize_stored_value(value):
    """Make damaged config-entry values JSON-safe without dropping the entity."""
    if isinstance(value, Mapping):
        return {
            key: _sanitize_stored_value(item)
            for key, item in value.items()
            if isinstance(key, str)
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize_stored_value(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return None


def _entity_device_info(device_config):
    return {
        key: value
        for key, value in device_config.items()
        if key != CONF_NAME
    }


def _make_entity_id(platform, name):
    object_id = slugify(str(name).removeprefix("+")) or "virtual_entity"
    return f'{platform}.{object_id}'


def _diagnostic_source_entities(entity):
    """Collect every explicit external source used by a virtual entity."""
    sources = []
    for source_entity_id in entity.get(CONF_SOURCE_ENTITIES, []):
        if isinstance(source_entity_id, str):
            sources.append(source_entity_id)
    for source_group in (CONF_TEMPLATE_SOURCES, CONF_ATTRIBUTE_SOURCES):
        for source in _as_dict(entity.get(source_group), source_group).values():
            if isinstance(source, Mapping) and isinstance(source.get(ATTR_ENTITY_ID), str):
                sources.append(source[ATTR_ENTITY_ID])
    source_entity_id = entity.get("source_entity")
    if isinstance(source_entity_id, str):
        sources.append(source_entity_id)
    event_hooks = entity.get(CONF_EVENT_HOOKS, [])
    if isinstance(event_hooks, list):
        for hook in event_hooks:
            if not isinstance(hook, Mapping) or hook.get("trigger") != "state":
                continue
            hook_entity_ids = hook.get(ATTR_ENTITY_ID, [])
            if isinstance(hook_entity_ids, str):
                hook_entity_ids = [hook_entity_ids]
            if isinstance(hook_entity_ids, list):
                sources.extend(
                    source_entity_id
                    for source_entity_id in hook_entity_ids
                    if isinstance(source_entity_id, str)
                )
    return list(dict.fromkeys(sources))


def _diagnostic_configuration(entity, platform):
    """Return a serializable summary of the virtual entity's configuration."""
    configuration = {
        "platform": platform,
        "initial_value": entity.get(CONF_INITIAL_VALUE),
        "persistent": entity.get(CONF_PERSISTENT),
        "value_template": entity.get(CONF_VALUE_TEMPLATE),
        "availability_template": entity.get(CONF_AVAILABILITY_TEMPLATE),
        "source_entities": _diagnostic_source_entities(entity),
        "template_sources": copy.deepcopy(entity.get(CONF_TEMPLATE_SOURCES, {})),
        "attribute_sources": copy.deepcopy(entity.get(CONF_ATTRIBUTE_SOURCES, {})),
        "attribute_templates": copy.deepcopy(entity.get(CONF_ATTRIBUTE_TEMPLATES, {})),
        "native_templates": copy.deepcopy(entity.get(CONF_NATIVE_TEMPLATES, {})),
        "command_actions": copy.deepcopy(entity.get(CONF_COMMAND_ACTIONS, {})),
        "attributes": copy.deepcopy(entity.get(CONF_ATTRIBUTES, {})),
        "event_hooks": copy.deepcopy(entity.get(CONF_EVENT_HOOKS, [])),
    }
    polygon = entity.get(CONF_POLYGONAL_ZONE)
    if isinstance(polygon, Mapping):
        configuration[CONF_POLYGONAL_ZONE] = {
            "inline_geojson": bool(polygon.get(CONF_POLYGON_GEOJSON)),
            CONF_POLYGON_FILES: copy.deepcopy(polygon.get(CONF_POLYGON_FILES, [])),
            CONF_POLYGON_PERSON_ENTITY: polygon.get(CONF_POLYGON_PERSON_ENTITY),
            CONF_POLYGON_STRATEGY: polygon.get(CONF_POLYGON_STRATEGY, "majority"),
            CONF_POLYGON_DISTANCE_METERS: polygon.get(
                CONF_POLYGON_DISTANCE_METERS,
                300,
            ),
            CONF_POLYGON_AWAY_STATE: polygon.get(CONF_POLYGON_AWAY_STATE, "not_home"),
            CONF_POLYGON_TRACKER_RULES: copy.deepcopy(
                polygon.get(CONF_POLYGON_TRACKER_RULES, {}),
            ),
        }
    return configuration


def _diagnostic_source_name(hass, entity_id):
    """Return a readable source name without depending on the registry."""
    state = hass.states.get(entity_id)
    if state is not None:
        friendly_name = state.attributes.get(ATTR_FRIENDLY_NAME)
        if isinstance(friendly_name, str) and friendly_name.strip():
            return friendly_name
    return entity_id.split(".", 1)[-1].replace("_", " ").title()


def _make_unique_id():
    return f'{uuid.uuid4()}.{COMPONENT_DOMAIN}'


def make_entity_key():
    return str(uuid.uuid4())


def _make_legacy_entity_key(device_name, index, platform, name):
    return json.dumps([device_name, index, platform, name], separators=(",", ":"))


def _entity_id_matches_platform(entity_id, platform):
    if not entity_id:
        return True
    if not isinstance(entity_id, str):
        return False
    try:
        normalized = cv.entity_id(entity_id)
    except vol.Invalid:
        return False
    return normalized.split(".", 1)[0] == platform


def _platform_entity_schema(platform):
    """Load a platform schema for persisted entity validation."""
    module = import_module(f".{platform}", __package__)
    return getattr(module, f"{platform.upper()}_SCHEMA", None) or module.ENTITY_SCHEMA


def _platform_entity_validation_error(schema, entity):
    """Return a stored entity validation error without blocking setup."""
    try:
        schema(entity)
    except (TypeError, ValueError, vol.Invalid) as err:
        return err
    return None


def _pop_entity_meta(meta_data, entity_key, name, platform):
    """Return matching meta data from either the current or legacy layout."""
    if entity_key in meta_data:
        values = meta_data[entity_key]
        if isinstance(values, Mapping) and _entity_id_matches_platform(values.get(ATTR_ENTITY_ID), platform):
            return dict(meta_data.pop(entity_key))

    values = meta_data.get(name)
    if isinstance(values, Mapping) and _entity_id_matches_platform(values.get(ATTR_ENTITY_ID), platform):
        return dict(meta_data.pop(name))

    return {}


def _make_suffix(platform, device_class):
    """Make a suitable suffix for an unnamed entity.
    
    Binary sensors, covers and sensors have a class so we append that,
    everything else gets left as-is.
    """
    if platform in [Platform.BINARY_SENSOR, Platform.COVER, Platform.SENSOR]:
        if device_class is None:
            return platform
        else:
            return f"{device_class}"
    return ""


class BlendedCfg:
    """Helper class to get at Virtual configuration options.

    Reads UI-managed devices from config entry options.
    """

    def __init__(self, hass, flow_data, options=None, config_entry=None):
        self._hass = hass
        self._group_name = flow_data[ATTR_GROUP_NAME]
        self._options = options or {}
        self._config_entry = config_entry

        self._meta_data = {}
        self._orphaned_entities = {}
        self._devices = []
        self._entities = {}

    def _reserve_entity_id(self, platform, entity_id, unique_id):
        """Reserve an entity registry id before platform setup can race."""
        if self._config_entry is None:
            return entity_id

        registry = er.async_get(self._hass)
        current_entity_id = registry.async_get_entity_id(
            platform,
            COMPONENT_DOMAIN,
            unique_id,
        )
        if current_entity_id is not None:
            current_entry = registry.async_get(current_entity_id)
            if (
                current_entry is not None
                and current_entry.config_entry_id != self._config_entry.entry_id
            ):
                return None
            if current_entity_id == entity_id:
                return current_entity_id
            try:
                return registry.async_update_entity(
                    current_entity_id,
                    new_entity_id=entity_id,
                ).entity_id
            except ValueError as err:
                _LOGGER.warning(
                    "Unable to restore configured entity id %s for %s: %s",
                    entity_id,
                    current_entity_id,
                    err,
                )
                return current_entity_id

        entity_entry = registry.async_get_or_create(
            platform,
            COMPONENT_DOMAIN,
            unique_id,
            suggested_object_id=entity_id.split(".", 1)[1],
            config_entry=self._config_entry,
        )
        return entity_entry.entity_id

    def _append_diagnostic_sensors(self, entity, platform):
        """Add runtime-only information and source-state sensors to its device."""
        if self._config_entry is None:
            return

        entity_id = entity[ATTR_ENTITY_ID]
        unique_id = entity[ATTR_UNIQUE_ID]
        device_id = entity[ATTR_DEVICE_ID]
        object_id = entity_id.split(".", 1)[1]
        source_entities = _diagnostic_source_entities(entity)
        configuration = _diagnostic_configuration(entity, platform)
        diagnostics = [(
            "info",
            entity_id,
            f"{entity[CONF_NAME]} - Configuration",
            "mdi:information-outline",
            {
                "diagnostic_type": "configuration",
                "virtual_entity_id": entity_id,
                "virtual_entity_platform": platform,
                "configured_source_entities": source_entities,
                "configuration": configuration,
            },
        )]
        diagnostics.extend(
            (
                f"debug{index}",
                source_entity_id,
                (
                    f"{entity[CONF_NAME]} - Source {index}: "
                    f"{_diagnostic_source_name(self._hass, source_entity_id)}"
                ),
                "mdi:bug-outline",
                {
                    "diagnostic_type": "source_state",
                    "virtual_entity_id": entity_id,
                    "source_entity_id": source_entity_id,
                    "source_entity_name": _diagnostic_source_name(
                        self._hass,
                        source_entity_id,
                    ),
                    "source_index": index,
                },
            )
            for index, source_entity_id in enumerate(source_entities, start=1)
        )

        sensor_entities = self._entities.setdefault("sensor", [])
        if platform == "vacuum" and entity.get("battery_level") is not None:
            battery_unique_id = (
                f"{unique_id}{DIAGNOSTIC_UNIQUE_ID_MARKER}battery"
            )
            battery_entity_id = self._reserve_entity_id(
                "sensor",
                f"sensor.{object_id}_battery",
                battery_unique_id,
            )
            sensor_entities.append({
                CONF_NAME: f"{entity[CONF_NAME]} - Battery",
                ATTR_ENTITY_ID: battery_entity_id,
                ATTR_UNIQUE_ID: battery_unique_id,
                ATTR_DEVICE_ID: device_id,
                CONF_INITIAL_VALUE: entity["battery_level"],
                CONF_INITIAL_AVAILABILITY: True,
                CONF_PERSISTENT: False,
                CONF_CLASS: "battery",
                CONF_UNIT_OF_MEASUREMENT: PERCENTAGE,
                CONF_ATTRIBUTES: {
                    "virtual_entity_id": entity_id,
                    "sensor_type": "battery",
                },
                CONF_ICON: "mdi:battery",
                **_entity_device_info({
                    key: entity[key]
                    for key in (
                        ATTR_DEVICE_ID,
                        CONF_MANUFACTURER,
                        CONF_MODEL,
                        CONF_SW_VERSION,
                        CONF_HW_VERSION,
                        CONF_SERIAL_NUMBER,
                        CONF_CONFIGURATION_URL,
                        CONF_SUGGESTED_AREA,
                        CONF_VIA_DEVICE_ID,
                    )
                    if key in entity
                }),
            })
        if platform == "device_tracker" and isinstance(
            entity.get(CONF_POLYGONAL_ZONE),
            Mapping,
        ):
            image_entities = self._entities.setdefault("image", [])
            map_unique_id = f"{unique_id}{DIAGNOSTIC_UNIQUE_ID_MARKER}map"
            map_entity_id = self._reserve_entity_id(
                "image",
                f"image.{object_id}_map",
                map_unique_id,
            )
            image_entities.append({
                CONF_NAME: f"{entity[CONF_NAME]} - Polygon Map",
                ATTR_ENTITY_ID: map_entity_id,
                ATTR_UNIQUE_ID: map_unique_id,
                ATTR_DEVICE_ID: device_id,
                CONF_INITIAL_VALUE: "unknown",
                CONF_INITIAL_AVAILABILITY: True,
                CONF_PERSISTENT: False,
                CONF_SOURCE_ENTITIES: [entity_id],
                CONF_ATTRIBUTES: {
                    "virtual_entity_id": entity_id,
                    "image_type": "polygon_map",
                },
                CONF_ICON: "mdi:map-outline",
                "content_type": "image/svg+xml",
                CONF_POLYGONAL_ZONE: copy.deepcopy(entity[CONF_POLYGONAL_ZONE]),
                **_entity_device_info({
                    key: entity[key]
                    for key in (
                        ATTR_DEVICE_ID,
                        CONF_MANUFACTURER,
                        CONF_MODEL,
                        CONF_SW_VERSION,
                        CONF_HW_VERSION,
                        CONF_SERIAL_NUMBER,
                        CONF_CONFIGURATION_URL,
                        CONF_SUGGESTED_AREA,
                        CONF_VIA_DEVICE_ID,
                    )
                    if key in entity
                }),
            })

            zone_unique_id = f"{unique_id}{DIAGNOSTIC_UNIQUE_ID_MARKER}zone"
            zone_entity_id = self._reserve_entity_id(
                "sensor",
                f"sensor.{object_id}_zone",
                zone_unique_id,
            )
            sensor_entities.append({
                CONF_NAME: f"{entity[CONF_NAME]} - Polygon Zone",
                ATTR_ENTITY_ID: zone_entity_id,
                ATTR_UNIQUE_ID: zone_unique_id,
                ATTR_DEVICE_ID: device_id,
                CONF_INITIAL_VALUE: entity.get(CONF_INITIAL_VALUE, "not_home"),
                CONF_INITIAL_AVAILABILITY: True,
                CONF_PERSISTENT: False,
                CONF_SOURCE_ENTITIES: [entity_id],
                CONF_TEMPLATE_SOURCES: {
                    "source": {
                        ATTR_ENTITY_ID: entity_id,
                        CONF_ATTRIBUTE: "state",
                    },
                },
                CONF_VALUE_TEMPLATE: "{{ source }}",
                CONF_ATTRIBUTES: {
                    "virtual_entity_id": entity_id,
                    "sensor_type": "polygon_zone",
                },
                CONF_ICON: "mdi:vector-polygon",
                **_entity_device_info({
                    key: entity[key]
                    for key in (
                        ATTR_DEVICE_ID,
                        CONF_MANUFACTURER,
                        CONF_MODEL,
                        CONF_SW_VERSION,
                        CONF_HW_VERSION,
                        CONF_SERIAL_NUMBER,
                        CONF_CONFIGURATION_URL,
                        CONF_SUGGESTED_AREA,
                        CONF_VIA_DEVICE_ID,
                    )
                    if key in entity
                }),
            })

        for suffix, source_entity_id, diagnostic_name, icon, attributes in diagnostics:
            diagnostic_unique_id = f"{unique_id}{DIAGNOSTIC_UNIQUE_ID_MARKER}{suffix}"
            diagnostic_entity_id = self._reserve_entity_id(
                "sensor",
                f"sensor.{object_id}_{suffix}",
                diagnostic_unique_id,
            )
            sensor_entities.append({
                CONF_NAME: diagnostic_name,
                ATTR_ENTITY_ID: diagnostic_entity_id,
                ATTR_UNIQUE_ID: diagnostic_unique_id,
                ATTR_DEVICE_ID: device_id,
                CONF_INITIAL_VALUE: "unknown",
                CONF_INITIAL_AVAILABILITY: True,
                CONF_PERSISTENT: False,
                CONF_SOURCE_ENTITIES: [source_entity_id],
                CONF_TEMPLATE_SOURCES: {
                    "source": {
                        ATTR_ENTITY_ID: source_entity_id,
                        CONF_ATTRIBUTE: "state",
                    },
                },
                CONF_VALUE_TEMPLATE: "{{ source }}",
                CONF_ATTRIBUTES: attributes,
                CONF_ICON: icon,
                CONF_DIAGNOSTIC_SOURCE_ENTITY: source_entity_id,
                **_entity_device_info({
                    key: entity[key]
                    for key in (
                        ATTR_DEVICE_ID,
                        CONF_MANUFACTURER,
                        CONF_MODEL,
                        CONF_SW_VERSION,
                        CONF_HW_VERSION,
                        CONF_SERIAL_NUMBER,
                        CONF_CONFIGURATION_URL,
                        CONF_SUGGESTED_AREA,
                        CONF_VIA_DEVICE_ID,
                    )
                    if key in entity
                }),
            })

    async def async_load(self):
        meta_data = await _load_meta_data(self._hass, self._group_name)
        devices = _as_dict(copy.deepcopy(self._options.get(ATTR_DEVICES, {})), "entry devices")
        device_attributes = _as_dict(
            copy.deepcopy(self._options.get(ATTR_DEVICE_ATTRIBUTES, {})),
            "entry device attributes",
        )
        changed = False
        seen_entity_keys = set()

        _LOGGER.debug(f"loaded-meta-data={meta_data}")
        _LOGGER.debug(f"loaded-devices={devices}")

        # Let's fix up the devices/entities
        for device_name, entities in devices.items():
            if not isinstance(entities, list):
                _LOGGER.warning(
                    "Skipping invalid entity list for device %s: expected list",
                    device_name,
                )
                changed = True
                continue
            device_config = _device_config_for_key(device_name, device_attributes)
            device_id = device_config[ATTR_DEVICE_ID]

            # Create device. One per all entities.
            self._devices.append(device_config)

            for index, entity in enumerate(entities):
                if not isinstance(entity, Mapping):
                    _LOGGER.warning(
                        "Skipping invalid entity config for device %s at index %s",
                        device_name,
                        index,
                    )
                    changed = True
                    continue
                entity = _normalize_common_entity_config(
                    entity,
                    device_name,
                    index,
                )
                if not entity.get(CONF_PLATFORM):
                    _LOGGER.warning(
                        "Skipping entity config for device %s at index %s without a platform",
                        device_name,
                        index,
                    )
                    changed = True
                    continue

                platform = entity.pop(CONF_PLATFORM)
                if platform not in VIRTUAL_ENTITY_DOMAINS:
                    _LOGGER.warning(
                        "Skipping entity config for device %s at index %s with unsupported platform %s",
                        device_name,
                        index,
                        platform,
                    )
                    changed = True
                    continue
                entity_key = entity.pop(ATTR_ENTITY_KEY, None)
                if not isinstance(entity_key, str) or not entity_key:
                    entity_key = None
                    changed = True
                device_class = entity.get(CONF_CLASS, None)

                # Figure out the name. We use the one provided and if that isn't
                # there the device name and, optionally, the class.
                name = entity.get(CONF_NAME, None)
                if name is None:
                    name = f"{device_name} {_make_suffix(platform, device_class)}"
                if entity_key is None:
                    entity_key = _make_legacy_entity_key(device_name, index, platform, name)
                elif entity_key in seen_entity_keys:
                    _LOGGER.warning(
                        "Duplicate entity key %s for device %s at index %s; repairing metadata identity",
                        entity_key,
                        device_name,
                        index,
                    )
                    entity_key = _make_legacy_entity_key(device_name, index, platform, name)
                    changed = True
                seen_entity_keys.add(entity_key)

                # Look up unique id for this device. If not there this is a new
                # device.
                entity_meta = _pop_entity_meta(meta_data, entity_key, name, platform)
                unique_id = entity_meta.get(ATTR_UNIQUE_ID, None)
                if not isinstance(unique_id, str) or not unique_id:
                    _LOGGER.debug(f"creating {name}")
                    unique_id = _make_unique_id()
                    changed = True

                # Now copy over the entity id of the device. Not having this is a
                # bug.
                configured_entity_id = entity.get(ATTR_ENTITY_ID)
                entity_id = configured_entity_id or entity_meta.get(ATTR_ENTITY_ID)
                if not _entity_id_matches_platform(entity_id, platform):
                    _LOGGER.warning(
                        "Ignoring stale entity_id %s for %s because the platform is %s",
                        entity_id,
                        name,
                        platform,
                    )
                    entity_id = None
                    changed = True
                entity_id = entity_id or _make_entity_id(platform, name)
                if entity_id is None:
                    _LOGGER.info(f"problem creating {name}, no entity id")
                    continue

                # Add device entry
                if entity_meta and entity_meta.get(ATTR_DEVICE_ID, None) is None:
                    _LOGGER.info(f"problem creating {name}, no device id")
                    changed = True

                if entity_meta.get(ATTR_DEVICE_ID) != device_id:
                    changed = True

                if entity_meta.get(ATTR_ENTITY_ID) != entity_id:
                    changed = True

                entity_meta.update({
                    ATTR_UNIQUE_ID: unique_id,
                    ATTR_ENTITY_ID: entity_id,
                    ATTR_DEVICE_ID: device_id,
                    CONF_NAME: name,
                    CONF_PLATFORM: platform,
                })

                # Update the entity.
                entity.update({
                    CONF_NAME: _make_name(name),
                    ATTR_ENTITY_ID: entity_id,
                    ATTR_UNIQUE_ID: unique_id,
                    ATTR_DEVICE_ID: device_id,
                })
                entity.update(_entity_device_info(device_config))

                # Platform modules are imported lazily to validate historical
                # UI data. Only import off the event loop; Home Assistant
                # template schema validation itself needs the loop context.
                try:
                    schema = await self._hass.async_add_executor_job(
                        _platform_entity_schema,
                        platform,
                    )
                except (AttributeError, ImportError, TypeError, ValueError):
                    schema = None
                validation_error = (
                    None
                    if schema is None
                    else _platform_entity_validation_error(schema, entity)
                )
                if schema is None or validation_error is not None:
                    _LOGGER.warning(
                        "Skipping invalid %s entity for device %s at index %s; "
                        "the stored UI item remains available for repair or removal: %s",
                        platform,
                        device_name,
                        index,
                        validation_error or "platform schema is unavailable",
                    )
                    # Keep identity metadata so repairing the item through the
                    # UI does not produce a duplicate entity or device.
                    self._meta_data[entity_key] = entity_meta
                    continue

                reserved_entity_id = self._reserve_entity_id(
                    platform,
                    entity_id,
                    unique_id,
                )
                if reserved_entity_id is None:
                    _LOGGER.warning(
                        "Unique id %s belongs to another config entry; generating a new identity for %s",
                        unique_id,
                        name,
                    )
                    unique_id = _make_unique_id()
                    entity[ATTR_UNIQUE_ID] = unique_id
                    entity_meta[ATTR_UNIQUE_ID] = unique_id
                    reserved_entity_id = self._reserve_entity_id(
                        platform,
                        entity_id,
                        unique_id,
                    )
                    changed = True
                if reserved_entity_id != entity_id:
                    _LOGGER.warning(
                        "Entity id %s is already in use; assigned %s for %s",
                        entity_id,
                        reserved_entity_id,
                        name,
                    )
                    entity_id = reserved_entity_id
                    entity[ATTR_ENTITY_ID] = entity_id
                    entity_meta[ATTR_ENTITY_ID] = entity_id
                    changed = True

                _LOGGER.debug(f"added entity {platform}/{entity}")

                # Now store in the correct place. Move off temporary meta
                # data list.
                # _LOGGER.debug(f"entities={self._entities}")
                if platform not in self._entities:
                    self._entities[platform] = []
                self._entities[platform].append(entity)
                self._append_diagnostic_sensors(entity, platform)
                self._meta_data.update({
                    entity_key: entity_meta
                })

        # Create orphaned list. If we have anything here we need to update
        # the saved meta data.
        for switch, values in meta_data.items():
            if not isinstance(values, Mapping):
                changed = True
                continue
            values = dict(values)
            values[CONF_NAME] = switch
            orphan_key = values.get(ATTR_UNIQUE_ID)
            if not isinstance(orphan_key, str) or not orphan_key:
                orphan_key = str(switch)
                changed = True
            self._orphaned_entities.update({
                orphan_key: values
            })
            changed = True

        # Make sure changes are kept.
        if changed:
            await _save_meta_data(self._hass, self._group_name, self._meta_data)

        _LOGGER.debug(f"meta-data={self._meta_data}")
        _LOGGER.debug(f"devices={self._devices}")
        _LOGGER.debug(f"entities={self._entities}")
        _LOGGER.debug(f"orphaned-entities={self._orphaned_entities}")

    async def async_delete(self):
        _LOGGER.debug(f"deleting {self._group_name}")
        await _delete_meta_data(self._hass, self._group_name)

    @property
    def devices(self):
        return self._devices

    @property
    def entities(self):
        return self._entities

    @property
    def orphaned_entities(self):
        return self._orphaned_entities

    @property
    def binary_sensor_config(self):
        return self._entities.get(Platform.BINARY_SENSOR, [])

    @property
    def sensor_config(self):
        return self._entities.get(Platform.SENSOR, [])

    @property
    def switch_config(self):
        return self._entities.get(Platform.SWITCH, [])
