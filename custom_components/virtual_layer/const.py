"""Constants for the virtual layer component. """

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

CONF_CLASS = "class"
CONF_ATTRIBUTE = "attribute"
CONF_ATTRIBUTE_SOURCES = "attribute_sources"
CONF_INITIAL_AVAILABILITY = "initial_availability"
CONF_ATTRIBUTES = "attributes"
CONF_ATTRIBUTE_TEMPLATES = "attribute_templates"
CONF_AVAILABILITY_TEMPLATE = "availability_template"
CONF_INITIAL_VALUE = "initial_value"
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

DEFAULT_AVAILABILITY = True
DEFAULT_PERSISTENT = True

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
