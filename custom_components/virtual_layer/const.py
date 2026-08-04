"""Constants for the virtual layer component. """

from collections.abc import Mapping

from homeassistant.const import ATTR_ENTITY_ID, ATTR_FRIENDLY_NAME, CONF_ICON

COMPONENT_DOMAIN = "virtual_layer"
COMPONENT_SERVICES = "virtual_layer-services"
COMPONENT_MANUFACTURER = "twrecked"
COMPONENT_MODEL = "virtual_layer"

ATTR_AVAILABLE = 'available'
ATTR_BACKUP_GROUPS = "groups"
ATTR_DEVICES = "devices"
ATTR_DEVICE_ATTRIBUTES = "device_attributes"
ATTR_DEVICE_ID = "device_id"
ATTR_ENTITIES = "entities"
ATTR_ENTITY_KEY = "entity_key"
ATTR_FILE_NAME = "file_name"
ATTR_GROUP_NAME = "group_name"
ATTR_PARENT_ID = "parent_id"
ATTR_PERSISTENT = 'persistent'
ATTR_UNIQUE_ID = 'unique_id'
ATTR_VALUE = "value"
ATTR_VERSION = "version"
ATTR_ATTRIBUTES = "attributes"
ATTR_VIRTUAL_ATTRIBUTES = "virtual_attributes"

RESERVED_VIRTUAL_ATTRIBUTE_NAMES = frozenset({
    ATTR_AVAILABLE,
    ATTR_ENTITY_ID,
    ATTR_PERSISTENT,
    ATTR_UNIQUE_ID,
    ATTR_VIRTUAL_ATTRIBUTES,
})

CONF_CLASS = "class"
CONF_ATTRIBUTE = "attribute"
CONF_ATTRIBUTE_SOURCES = "attribute_sources"
CONF_INITIAL_AVAILABILITY = "initial_availability"
CONF_ATTRIBUTES = "attributes"
CONF_AUTO_HELPER = "auto_helper"
CONF_ATTRIBUTE_TEMPLATES = "attribute_templates"
CONF_AVAILABILITY_TEMPLATE = "availability_template"
CONF_DIAGNOSTIC_SOURCE_ENTITY = "diagnostic_source_entity"
CONF_INITIAL_VALUE = "initial_value"
CONF_LOCATION_HELPER = "location_helper"
CONF_MAX = "max"
CONF_MIN = "min"
CONF_NAME = "name"
CONF_OPEN_CLOSE_DURATION = "open_close_duration"
CONF_OPEN_CLOSE_TICK = "open_close_tick"
CONF_PERSISTENT = "persistent"
CONF_PULL_INTERVAL = "pull_interval"
CONF_SOURCE_ENTITIES = "source_entities"
CONF_TEMPLATE_SOURCES = "template_sources"
CONF_VALUE_TEMPLATE = "value_template"
CONF_MANUFACTURER = "manufacturer"
CONF_MODEL = "model"
CONF_HW_VERSION = "hw_version"
CONF_SERIAL_NUMBER = "serial_number"
CONF_SW_VERSION = "sw_version"

# Options supplied through the UI's domain-options JSON are kept separate from
# Virtual Layer storage and template configuration. Generic domains expose the
# remaining options as state attributes.
GENERIC_ENTITY_OPTION_EXCLUDED_KEYS = frozenset({
    ATTR_DEVICE_ID,
    ATTR_ENTITY_ID,
    ATTR_ENTITY_KEY,
    ATTR_FRIENDLY_NAME,
    ATTR_UNIQUE_ID,
    CONF_ATTRIBUTE_SOURCES,
    CONF_ATTRIBUTE_TEMPLATES,
    CONF_ATTRIBUTES,
    CONF_AUTO_HELPER,
    CONF_AVAILABILITY_TEMPLATE,
    CONF_CLASS,
    CONF_DIAGNOSTIC_SOURCE_ENTITY,
    CONF_HW_VERSION,
    CONF_ICON,
    CONF_INITIAL_AVAILABILITY,
    CONF_INITIAL_VALUE,
    CONF_MANUFACTURER,
    CONF_MODEL,
    CONF_NAME,
    CONF_PERSISTENT,
    CONF_PULL_INTERVAL,
    CONF_SERIAL_NUMBER,
    CONF_SOURCE_ENTITIES,
    CONF_SW_VERSION,
    CONF_TEMPLATE_SOURCES,
    CONF_VALUE_TEMPLATE,
    "unit_of_measurement",
    "platform",
    "state_class",
})


def generic_entity_options(config: Mapping) -> dict:
    """Extract user-supplied generic domain options from an entity config."""
    return {
        key: value
        for key, value in config.items()
        if isinstance(key, str)
        and key not in GENERIC_ENTITY_OPTION_EXCLUDED_KEYS
        and key not in RESERVED_VIRTUAL_ATTRIBUTE_NAMES
    }

DEFAULT_AVAILABILITY = True
DEFAULT_PERSISTENT = True
DIAGNOSTIC_UNIQUE_ID_MARKER = ".virtual_layer_diagnostic."

IMPORTED_GROUP_NAME = "imported"

VIRTUAL_ENTITY_DOMAINS = [
    "ai_task",
    "air_quality",
    "alarm_control_panel",
    "assist_satellite",
    "binary_sensor",
    "button",
    "calendar",
    "camera",
    "climate",
    "conversation",
    "cover",
    "date",
    "datetime",
    "device_tracker",
    "event",
    "fan",
    "geolocation",
    "humidifier",
    "image",
    "image_processing",
    "infrared",
    "lawn_mower",
    "light",
    "lock",
    "media_player",
    "notify",
    "number",
    "radio_frequency",
    "remote",
    "scene",
    "select",
    "sensor",
    "siren",
    "stt",
    "switch",
    "tag",
    "text",
    "time",
    "todo",
    "tts",
    "update",
    "vacuum",
    "valve",
    "wake_word",
    "water_heater",
    "weather",
]

STATE_ONLY_ENTITY_DOMAINS = [
    "geolocation",
    "infrared",
    "radio_frequency",
    "tag",
]


def default_meta_file(hass) -> str:
    return hass.config.path(".storage/virtual_layer.meta.json")
