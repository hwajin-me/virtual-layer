"""Handles Virtual Layer config-entry backed configuration."""

import asyncio
import copy
import json
import logging
import os
import uuid
from collections.abc import Mapping
from importlib import import_module

import aiofiles
import homeassistant.helpers.entity_registry as er
import voluptuous as vol
from homeassistant.const import (
    ATTR_ENTITY_ID,
    CONF_PLATFORM,
    CONF_UNIT_OF_MEASUREMENT,
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


def _normalized_backup_group(group):
    group = _as_dict(group, "backup group")
    if not group:
        return None

    group_name = group.get(ATTR_GROUP_NAME)
    if not isinstance(group_name, str) or not group_name:
        _LOGGER.warning("Ignoring backup group without a valid group name")
        return None

    normalized = dict(group)
    normalized[ATTR_DEVICES] = _as_dict(group.get(ATTR_DEVICES), "backup devices")
    normalized[ATTR_DEVICE_ATTRIBUTES] = _as_dict(
        group.get(ATTR_DEVICE_ATTRIBUTES),
        "backup device attributes",
    )
    return normalized


def _backup_groups_from_payload(payload):
    if isinstance(payload, list):
        groups = payload
    elif isinstance(payload, Mapping):
        if isinstance(payload.get(ATTR_BACKUP_GROUPS), list):
            groups = payload[ATTR_BACKUP_GROUPS]
        elif ATTR_GROUP_NAME in payload:
            groups = [payload]
        else:
            groups = []
    else:
        groups = []

    return [
        group
        for group in (_normalized_backup_group(group) for group in groups)
        if group is not None
    ]


async def _async_load_json(file_name):
    _LOGGER.debug("_async_load_json1 file_name for %s", file_name)
    try:
        async with aiofiles.open(file_name, 'r') as meta_file:
            _LOGGER.debug("_async_load_json2 file_name for %s", file_name)
            contents = await meta_file.read()
            _LOGGER.debug("_async_load_json3 file_name for %s", file_name)
            return json.loads(contents)
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
            os.makedirs(directory, exist_ok=True)
        async with aiofiles.open(temporary_file_name, 'w') as meta_file:
            data = json.dumps(data, indent=4)
            await meta_file.write(data)
        os.replace(temporary_file_name, file_name)
    except OSError:
        _LOGGER.exception("Unable to save json file: %s", file_name)
        raise
    finally:
        try:
            os.remove(temporary_file_name)
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


def _normalize_common_entity_config(entity, device_name, index):
    """Normalize versioned UI/backup fields before platform validation."""
    entity = dict(entity)
    name = entity.get(CONF_NAME)
    if not isinstance(name, str) or not name.strip():
        entity.pop(CONF_NAME, None)

    initial_value = entity.get(CONF_INITIAL_VALUE)
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

    for key in (
        CONF_ATTRIBUTES,
        CONF_ATTRIBUTE_SOURCES,
        CONF_ATTRIBUTE_TEMPLATES,
        CONF_TEMPLATE_SOURCES,
    ):
        if key in entity and not isinstance(entity[key], Mapping):
            _LOGGER.warning(
                "Resetting invalid %s for device %s at index %s",
                key,
                device_name,
                index,
            )
            entity[key] = {}

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

    for key in (CONF_VALUE_TEMPLATE, CONF_AVAILABILITY_TEMPLATE):
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


def _entity_device_info(device_config):
    return {
        key: value
        for key, value in device_config.items()
        if key != CONF_NAME
    }


def _make_entity_id(platform, name):
    if name.startswith("+"):
        return f'{platform}.{COMPONENT_DOMAIN}_{slugify(name[1:])}'
    else:
        return f'{platform}.{slugify(name)}'


def _make_unique_id():
    return f'{uuid.uuid4()}.{COMPONENT_DOMAIN}'


def make_entity_key():
    return str(uuid.uuid4())


def clone_entities_with_new_keys(entities):
    if not isinstance(entities, list):
        _LOGGER.warning("Skipping invalid restored entity list: expected list")
        return []
    cloned_entities = []
    for entity in copy.deepcopy(entities):
        if not isinstance(entity, Mapping):
            _LOGGER.warning("Skipping invalid restored entity: expected mapping")
            continue
        entity[ATTR_ENTITY_KEY] = make_entity_key()
        cloned_entities.append(entity)
    return cloned_entities


def _make_legacy_entity_key(device_name, index, platform, name):
    return json.dumps([device_name, index, platform, name], separators=(",", ":"))


def _entity_id_matches_platform(entity_id, platform):
    return not entity_id or (
        isinstance(entity_id, str)
        and entity_id.split(".", 1)[0] == platform
    )


def _platform_entity_schema(platform):
    """Load a platform schema for persisted entity validation."""
    module = import_module(f".{platform}", __package__)
    return getattr(module, f"{platform.upper()}_SCHEMA", None) or module.ENTITY_SCHEMA


def _is_valid_platform_entity_config(schema, entity) -> bool:
    """Check a stored entity without allowing one bad item to block setup."""
    try:
        schema(entity)
    except (TypeError, ValueError, vol.Invalid):
        return False
    return True


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


async def async_build_entry_backup(entry):
    """Build a serializable backup payload for one config entry."""
    return {
        ATTR_GROUP_NAME: entry.data[ATTR_GROUP_NAME],
        ATTR_DEVICES: copy.deepcopy(entry.options.get(ATTR_DEVICES, {})),
        ATTR_DEVICE_ATTRIBUTES: copy.deepcopy(entry.options.get(ATTR_DEVICE_ATTRIBUTES, {})),
    }


async def async_save_backup(file_name, groups):
    """Save a Virtual Layer backup file."""
    await _async_save_json(file_name, {
        ATTR_VERSION: STORAGE_VERSION,
        "domain": COMPONENT_DOMAIN,
        ATTR_BACKUP_GROUPS: [
            group
            for group in (_normalized_backup_group(group) for group in groups)
            if group is not None
        ],
    })


async def async_load_backup(file_name):
    """Load a Virtual Layer backup file."""
    return _backup_groups_from_payload(await _async_load_json(file_name))


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

        entity_entry = er.async_get(self._hass).async_get_or_create(
            platform,
            COMPONENT_DOMAIN,
            unique_id,
            suggested_object_id=entity_id.split(".", 1)[1],
            config_entry=self._config_entry,
        )
        return entity_entry.entity_id

    async def async_load(self):
        meta_data = await _load_meta_data(self._hass, self._group_name)
        devices = _as_dict(copy.deepcopy(self._options.get(ATTR_DEVICES, {})), "entry devices")
        device_attributes = _as_dict(
            copy.deepcopy(self._options.get(ATTR_DEVICE_ATTRIBUTES, {})),
            "entry device attributes",
        )
        changed = False

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
                device_class = entity.get(CONF_CLASS, None)

                # Figure out the name. We use the one provided and if that isn't
                # there the device name and, optionally, the class.
                name = entity.get(CONF_NAME, None)
                if name is None:
                    name = f"{device_name} {_make_suffix(platform, device_class)}"
                if entity_key is None:
                    entity_key = _make_legacy_entity_key(device_name, index, platform, name)

                # Look up unique id for this device. If not there this is a new
                # device.
                entity_meta = _pop_entity_meta(meta_data, entity_key, name, platform)
                unique_id = entity_meta.get(ATTR_UNIQUE_ID, None)
                if unique_id is None:
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
                if schema is None or not _is_valid_platform_entity_config(schema, entity):
                    _LOGGER.warning(
                        "Skipping invalid %s entity for device %s at index %s; "
                        "the stored UI item remains available for repair or removal",
                        platform,
                        device_name,
                        index,
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
            self._orphaned_entities.update({
                values.get(ATTR_UNIQUE_ID, switch): values
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
