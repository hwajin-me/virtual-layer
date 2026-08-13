"""Constants for the virtual layer component. """

from collections.abc import Mapping

from homeassistant.const import ATTR_ENTITY_ID, ATTR_FRIENDLY_NAME, CONF_ICON

COMPONENT_DOMAIN = "virtual_layer"
COMPONENT_SERVICES = "virtual_layer-services"
COMPONENT_MANUFACTURER = "twrecked"
COMPONENT_MODEL = "virtual_layer"

ATTR_AVAILABLE = 'available'
ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ATTR_DEVICES = "devices"
ATTR_DEVICE_ATTRIBUTES = "device_attributes"
ATTR_DEVICE_ID = "device_id"
ATTR_ENTITIES = "entities"
ATTR_ENTITY_KEY = "entity_key"
ATTR_GROUP_NAME = "group_name"
ATTR_PARENT_ID = "parent_id"
ATTR_PERSISTENT = 'persistent'
ATTR_UNIQUE_ID = 'unique_id'
ATTR_VALUE = "value"
ATTR_VERSION = "version"
ATTR_ATTRIBUTES = "attributes"
ATTR_VIRTUAL_ATTRIBUTES = "virtual_attributes"
ATTR_CONFIGURED_VIRTUAL_ATTRIBUTES = "configured_virtual_attributes"

RESERVED_VIRTUAL_ATTRIBUTE_NAMES = frozenset({
    ATTR_AVAILABLE,
    ATTR_CONFIGURED_VIRTUAL_ATTRIBUTES,
    ATTR_ENTITY_ID,
    ATTR_PERSISTENT,
    ATTR_UNIQUE_ID,
    ATTR_VIRTUAL_ATTRIBUTES,
})

RESERVED_NATIVE_TEMPLATE_NAMES = frozenset({
    "device_info",
    "entity_id",
    "hass",
    "name",
    "platform",
    "unique_id",
})

CONF_CLASS = "class"
CONF_ATTRIBUTE = "attribute"
CONF_ATTRIBUTE_SOURCES = "attribute_sources"
CONF_INITIAL_AVAILABILITY = "initial_availability"
CONF_ATTRIBUTES = "attributes"
CONF_AUTO_HELPER = "auto_helper"
CONF_ATTRIBUTE_TEMPLATES = "attribute_templates"
CONF_COMMAND_ACTIONS = "command_actions"
CONF_AVAILABILITY_TEMPLATE = "availability_template"
CONF_DIAGNOSTIC_SOURCE_ENTITY = "diagnostic_source_entity"
CONF_EVENT_HOOKS = "event_hooks"
CONF_ICON_TEMPLATE = "icon_template"
CONF_INITIAL_VALUE = "initial_value"
CONF_LOCATION_HELPER = "location_helper"
CONF_POLYGONAL_ZONE = "polygonal_zone"
CONF_POLYGON_GEOJSON = "geojson"
CONF_POLYGON_FILES = "files"
CONF_POLYGON_PERSON_ENTITY = "person_entity_id"
CONF_POLYGON_STRATEGY = "strategy"
CONF_POLYGON_TRACKER_RULES = "tracker_rules"
CONF_POLYGON_AWAY_STATE = "away_state"
CONF_POLYGON_DISTANCE_METERS = "distance_threshold_meters"
CONF_MAX = "max"
CONF_MIN = "min"
CONF_NAME = "name"
CONF_OPEN_CLOSE_DURATION = "open_close_duration"
CONF_OPEN_CLOSE_TICK = "open_close_tick"
CONF_PERSISTENT = "persistent"
CONF_PULL_INTERVAL = "pull_interval"
CONF_SOURCE_ENTITIES = "source_entities"
CONF_TEMPLATE_SOURCES = "template_sources"
CONF_NATIVE_TEMPLATES = "native_templates"
CONF_VALUE_TEMPLATE = "value_template"
CONF_MANUFACTURER = "manufacturer"
CONF_MODEL = "model"
CONF_HW_VERSION = "hw_version"
CONF_SERIAL_NUMBER = "serial_number"
CONF_SW_VERSION = "sw_version"
CONF_CONFIGURATION_URL = "configuration_url"
CONF_SUGGESTED_AREA = "suggested_area"
CONF_VIA_DEVICE_ID = "via_device_id"

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
    CONF_COMMAND_ACTIONS,
    CONF_ATTRIBUTES,
    CONF_AUTO_HELPER,
    CONF_AVAILABILITY_TEMPLATE,
    CONF_CLASS,
    CONF_DIAGNOSTIC_SOURCE_ENTITY,
    CONF_EVENT_HOOKS,
    CONF_HW_VERSION,
    CONF_ICON,
    CONF_ICON_TEMPLATE,
    CONF_INITIAL_AVAILABILITY,
    CONF_INITIAL_VALUE,
    CONF_MANUFACTURER,
    CONF_MODEL,
    CONF_NAME,
    CONF_PERSISTENT,
    CONF_PULL_INTERVAL,
    CONF_SERIAL_NUMBER,
    CONF_CONFIGURATION_URL,
    CONF_SUGGESTED_AREA,
    CONF_SOURCE_ENTITIES,
    CONF_SW_VERSION,
    CONF_TEMPLATE_SOURCES,
    CONF_NATIVE_TEMPLATES,
    CONF_VIA_DEVICE_ID,
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
    "ai_task",
    "assist_satellite",
    "conversation",
    "geolocation",
    "image_processing",
    "infrared",
    "radio_frequency",
    "tag",
    "tts",
]

# Public native commands implemented by each virtual platform. Config flows and
# damaged-data recovery use this without importing platform modules on HA's loop.
VIRTUAL_ENTITY_COMMANDS = {
    "button": frozenset({"press"}),
    "camera": frozenset({
        "disable_motion_detection", "enable_motion_detection", "turn_off", "turn_on",
    }),
    "climate": frozenset({
        "set_fan_mode", "set_humidity", "set_hvac_mode", "set_preset_mode",
        "set_swing_horizontal_mode", "set_swing_mode", "set_temperature",
        "turn_off", "turn_on",
    }),
    "cover": frozenset({
        "close_cover", "close_cover_tilt", "open_cover", "open_cover_tilt",
        "set_cover_position", "set_cover_tilt_position", "stop_cover",
        "stop_cover_tilt",
    }),
    "date": frozenset({"set_value"}),
    "datetime": frozenset({"set_value"}),
    "fan": frozenset({
        "oscillate", "set_direction", "set_percentage", "set_preset_mode",
        "turn_off", "turn_on",
    }),
    "humidifier": frozenset({"set_humidity", "set_mode", "turn_off", "turn_on"}),
    "lawn_mower": frozenset({"dock", "pause", "start_mowing"}),
    "light": frozenset({"turn_off", "turn_on"}),
    "lock": frozenset({"lock", "open", "unlock"}),
    "media_player": frozenset({
        "media_pause", "media_play", "media_stop", "mute_volume", "select_source",
        "set_volume_level", "turn_off", "turn_on",
    }),
    "number": frozenset({"set_native_value"}),
    "remote": frozenset({"send_command", "turn_off", "turn_on"}),
    "select": frozenset({"select_option"}),
    "siren": frozenset({"turn_off", "turn_on"}),
    "switch": frozenset({"turn_off", "turn_on"}),
    "text": frozenset({"set_value"}),
    "time": frozenset({"set_value"}),
    "update": frozenset({"install", "release_notes"}),
    "vacuum": frozenset({
        "clean_spot", "locate", "pause", "return_to_base", "send_command",
        "set_fan_speed", "start", "stop",
    }),
    "valve": frozenset({
        "close_valve", "open_valve", "set_valve_position", "stop_valve",
    }),
    "water_heater": frozenset({
        "set_operation_mode", "set_temperature", "turn_away_mode_off",
        "turn_away_mode_on", "turn_off", "turn_on",
    }),
}


def default_meta_file(hass) -> str:
    return hass.config.path(".storage/virtual_layer.meta.json")
