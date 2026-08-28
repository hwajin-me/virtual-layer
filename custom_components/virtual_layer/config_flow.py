"""Config flow for Virtual Layer."""

from __future__ import annotations

import copy
import inspect
import json
import logging
import math
import re
import uuid
from collections.abc import Collection, Mapping
from enum import Enum
from functools import wraps
from importlib import import_module
from typing import Any

import homeassistant.helpers.config_validation as cv
import homeassistant.helpers.device_registry as dr
import homeassistant.helpers.entity_registry as er
import voluptuous as vol
from homeassistant import config_entries, exceptions
from homeassistant.components.camera import CameraEntityFeature
from homeassistant.components.climate import ClimateEntityFeature
from homeassistant.components.cover import CoverEntityFeature
from homeassistant.components.fan import FanEntityFeature
from homeassistant.components.humidifier import HumidifierEntityFeature
from homeassistant.components.lawn_mower import LawnMowerEntityFeature
from homeassistant.components.lock import LockEntityFeature
from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.components.siren import SirenEntityFeature
from homeassistant.components.update import UpdateEntityFeature
from homeassistant.components.vacuum import VacuumEntityFeature
from homeassistant.components.valve import ValveEntityFeature
from homeassistant.components.water_heater import WaterHeaterEntityFeature
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_FRIENDLY_NAME,
    CONF_ICON,
    CONF_PLATFORM,
    CONF_UNIT_OF_MEASUREMENT,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector
from homeassistant.helpers.json import json_bytes
from homeassistant.helpers.template import Template, TemplateError
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify

from .cfg import (
    _platform_command_names,
    _rename_meta_data,
    make_entity_key,
)
from .climate_options import (
    CLIMATE_CURRENT_MODE_FIELDS,
    CLIMATE_FORM_FIELDS,
    CLIMATE_MODE_LIST_FIELDS,
    CLIMATE_SCALAR_FORM_FIELDS,
    extract_climate_options,
    migrate_legacy_climate_attributes,
)
from .const import *
from .entity import (
    VirtualEntity,
    nonnegative_int,
    positive_tick,
    repair_legacy_enum_template,
    repair_legacy_template_data,
)
from .fan_options import (
    FAN_FORM_FIELDS,
    FAN_MODE_LIST_FIELD,
    FAN_NATIVE_ATTRIBUTE_FIELDS,
    extract_fan_options,
    migrate_legacy_fan_attributes,
)
from .humidifier_options import (
    HUMIDIFIER_CURRENT_MODE_FIELD,
    HUMIDIFIER_FORM_FIELDS,
    HUMIDIFIER_MODE_LIST_FIELD,
    extract_humidifier_options,
    migrate_legacy_humidifier_attributes,
)
from .polygon import parse_geojson_zones

_LOGGER = logging.getLogger(__name__)

CONF_ACTION = "action"
CONF_ADD_FIRST_ENTITY = "add_first_entity"
CONF_ATTRIBUTE_SOURCES_JSON = "attribute_sources_json"
CONF_ATTRIBUTES_JSON = "attributes_json"
CONF_ATTRIBUTE_TEMPLATES_JSON = "attribute_templates_json"
CONF_NATIVE_TEMPLATES_JSON = "native_templates_json"
CONF_NATIVE_VALUE_TEMPLATES = "native_value_templates"
CONF_DEVICE_DETAILS = "device_details"
CONF_ADVANCED_SETTINGS = "advanced_settings"
CONF_DOMAIN_SETTINGS = "domain_settings"
CONF_MATTER_LIGHT_TYPE = "matter_light_type"
CONF_COMMAND_ACTIONS_JSON = "command_actions_json"
CONF_DEVICE_NAME = "device_name"
CONF_DEVICE_ID = "device_id"
CONF_DEVICE_MANUFACTURER = "device_manufacturer"
CONF_DEVICE_MODEL = "device_model"
CONF_DEVICE_SW_VERSION = "device_sw_version"
CONF_DEVICE_CONFIGURATION_URL = "device_configuration_url"
CONF_DOMAIN_OPTIONS_JSON = "domain_options_json"
CONF_EVENT_HOOKS_JSON = "event_hooks_json"
CONF_DEVICE_HW_VERSION = "device_hw_version"
CONF_DEVICE_SERIAL_NUMBER = "device_serial_number"
CONF_DEVICE_SUGGESTED_AREA = "device_suggested_area"
CONF_DEVICE_VIA_DEVICE_ID = "device_via_device_id"
CONF_ENTITY_NAME = "entity_name"
CONF_ENTITY_KEY = "entity_key"
CONF_ENTITY_KEYS = "entity_keys"
CONF_MANAGED_DEVICE_NAME = "managed_device_name"
CONF_REFERENCE_ENTITY_ID = "reference_entity_id"
CONF_TARGET_ENTITY_TYPE = "target_entity_type"
CONF_HELPER_UPDATE_MODE = "helper_update_mode"
CONF_USE_TEMPLATE_HELPER = "use_template_helper"
CONF_SOURCE_ENTITIES_TEXT = "source_entities_text"
CONF_TEMPLATE_SOURCES_JSON = "template_sources_json"
CONF_TARGET_DEVICE_NAME = "target_device_name"
CONF_POLYGON_GEOJSON_JSON = "polygon_geojson_json"
CONF_POLYGON_FILES_TEXT = "polygon_files_text"
CONF_POLYGON_PERSON = "polygon_person"
CONF_POLYGON_STRATEGY_INPUT = "polygon_strategy"
CONF_POLYGON_DISTANCE_INPUT = "polygon_distance_meters"
CONF_POLYGON_TRACKER_RULES_JSON = "polygon_tracker_rules_json"
CONF_POLYGON_AWAY_STATE_INPUT = "polygon_away_state"
CAMERA_SOURCE_ENTITY_OPTION = "source_entity"
NEW_DEVICE_TARGET = "__new_device__"
HELPER_UPDATE_AUTO = "automatic"
HELPER_UPDATE_KEEP = "keep_current"
HELPER_UPDATE_FORCE = "force_helper"
NUMERIC_OUTLIER_THRESHOLD = 3
_MISSING_NATIVE_DEFAULT = object()

CLIMATE_NATIVE_TEMPLATE_PROPERTIES = (
    "hvac_modes",
    "hvac_mode",
    "hvac_action",
    "fan_modes",
    "fan_mode",
    "preset_modes",
    "preset_mode",
    "swing_modes",
    "swing_mode",
    "swing_horizontal_modes",
    "swing_horizontal_mode",
    "current_temperature",
    "target_temperature",
    "target_temperature_high",
    "target_temperature_low",
    "min_temp",
    "max_temp",
    "target_temperature_step",
    "temperature_unit",
    "current_humidity",
    "target_humidity",
    "min_humidity",
    "max_humidity",
    "target_humidity_step",
)
FAN_NATIVE_TEMPLATE_PROPERTIES = (
    "is_on",
    "speed_count",
    "percentage",
    "preset_modes",
    "preset_mode",
    "oscillating",
    "current_direction",
)
HUMIDIFIER_NATIVE_TEMPLATE_PROPERTIES = (
    "is_on",
    "device_class",
    "action",
    "available_modes",
    "mode",
    "current_humidity",
    "target_humidity",
    "min_humidity",
    "max_humidity",
    "target_humidity_step",
)
LEGACY_STATIC_NATIVE_FIELD_ALIASES = {
    "climate": {},
    "fan": {
        FAN_MODE_LIST_FIELD: "preset_modes",
    },
    "humidifier": {
        "class": "device_class",
        HUMIDIFIER_MODE_LIST_FIELD: "available_modes",
    },
}
DOMAIN_NATIVE_TEMPLATE_DEFAULT_VALUES = {
    "climate": {
        "hvac_modes": ["off", "heat", "cool", "heat_cool", "auto", "dry", "fan_only"],
        "hvac_mode": "off",
        "hvac_action": "off",
        "fan_modes": [],
        "fan_mode": None,
        "preset_modes": [],
        "preset_mode": None,
        "swing_modes": [],
        "swing_mode": None,
        "swing_horizontal_modes": [],
        "swing_horizontal_mode": None,
        "current_temperature": None,
        "target_temperature": 21,
        "target_temperature_high": None,
        "target_temperature_low": None,
        "min_temp": 7,
        "max_temp": 35,
        "target_temperature_step": 0.1,
        "temperature_unit": "°C",
        "current_humidity": None,
        "target_humidity": None,
        "min_humidity": 30,
        "max_humidity": 99,
        "target_humidity_step": 1,
    },
    "fan": {
        "is_on": False,
        "speed_count": 100,
        "percentage": 0,
        "preset_modes": [],
        "preset_mode": None,
        "oscillating": False,
        "current_direction": "forward",
    },
    "humidifier": {
        "is_on": False,
        "device_class": "humidifier",
        "action": "off",
        "available_modes": [],
        "mode": None,
        "current_humidity": None,
        "target_humidity": 50,
        "min_humidity": 0,
        "max_humidity": 100,
        "target_humidity_step": 1,
    },
}

DOMAIN_NATIVE_SOURCE_TEMPLATE_DEFAULT_VALUES = {
    **DOMAIN_NATIVE_TEMPLATE_DEFAULT_VALUES,
    "calendar": {"event": None},
    "camera": {"frame_interval": 1, "supported_features": 1},
    "climate": {
        **DOMAIN_NATIVE_TEMPLATE_DEFAULT_VALUES["climate"],
        "hvac_action": None,
        "target_humidity": None,
        "target_temperature": None,
    },
    "fan": {
        **DOMAIN_NATIVE_TEMPLATE_DEFAULT_VALUES["fan"],
        "speed_count": 0,
        "percentage": None,
        "oscillating": None,
        "current_direction": None,
    },
    "cover": {
        "current_cover_tilt_position": None,
        "is_closed": True,
        "reports_position": True,
        "supported_features": 15,
    },
    "device_tracker": {"location": "not_home", "gps": [0.0, 0.0]},
    "event": {"event_type": "virtual_event"},
    "image": {"content_type": "image/jpeg"},
    "lawn_mower": {"activity": "docked", "supported_features": 7},
    "light": {
        "supported_color_modes": ["onoff"],
        "color_mode": "onoff",
        "hs_color": [0.0, 0.0],
        "xy_color": [0.0, 0.0],
        "rgb_color": [0, 0, 0],
        "rgbw_color": [0, 0, 0, 0],
        "rgbww_color": [0, 0, 0, 0, 0],
        "color_temp_kelvin": 4000,
        "min_color_temp_kelvin": 1000,
        "max_color_temp_kelvin": 40000,
    },
    "lock": {"support_open": True, "is_locked": True},
    "media_player": {
        "media_state": "idle",
        "volume_level": 0.5,
        "volume_step": 0.05,
        "shuffle": None,
        "repeat": None,
        "supported_features": 20877,
    },
    "number": {
        "native_min_value": 0,
        "native_max_value": 100,
        "native_step": 1,
        "native_value": 0,
        "mode": "auto",
    },
    "sensor": {
        "options": None,
        "suggested_display_precision": None,
    },
    "siren": {
        "support_volume": True,
        "support_duration": True,
        "supported_features": 27,
    },
    "text": {
        "native_min": 0,
        "native_max": 255,
        "mode": "text",
        "native_value": "",
    },
    "update": {
        "installed_version": "0.0.0",
        "latest_version": "0.0.0",
        "in_progress": None,
        "update_percentage": None,
        "support_backup": True,
        "supported_features": 9,
    },
    "vacuum": {
        "activity": "docked",
        "battery_level": None,
        "supported_features": 14108,
    },
    "valve": {
        "is_closed": True,
        "reports_position": True,
        "supported_features": 15,
    },
    "water_heater": {
        "operation_list": ["off"],
        "current_operation": "off",
        "min_temp": 35,
        "max_temp": 85,
        "target_temperature_step": 1,
        "temperature_unit": "°C",
        "is_away_mode_on": None,
        "precision": 1,
        "supported_features": 11,
    },
}
DOMAIN_NATIVE_TEMPLATE_PROPERTIES = {
    "ai_task": ("supported_features",),
    "air_quality": (
        "particulate_matter_2_5",
        "particulate_matter_10",
        "particulate_matter_0_1",
        "air_quality_index",
        "ozone",
        "carbon_monoxide",
        "carbon_dioxide",
        "sulphur_dioxide",
        "nitrogen_oxide",
        "nitrogen_monoxide",
        "nitrogen_dioxide",
        "unit_of_measurement",
    ),
    "alarm_control_panel": (
        "changed_by",
        "code_arm_required",
        "code_format",
        "supported_features",
    ),
    "assist_satellite": (
        "pipeline_entity_id",
        "vad_sensitivity_entity_id",
        "tts_options",
        "supported_features",
    ),
    "binary_sensor": ("device_class",),
    "button": ("device_class",),
    "calendar": ("event",),
    "camera": (
        "source_entity",
        "image_path",
        "stream_source",
        "entity_picture",
        "frame_interval",
        "is_on",
        "is_recording",
        "is_streaming",
        "motion_detection_enabled",
        "supported_features",
    ),
    "climate": CLIMATE_NATIVE_TEMPLATE_PROPERTIES,
    "conversation": (
        "supported_languages",
        "supports_streaming",
        "supported_features",
    ),
    "cover": (
        "current_cover_position",
        "current_cover_tilt_position",
        "is_opening",
        "is_closing",
        "is_closed",
        "device_class",
        "reports_position",
        "supported_features",
    ),
    "date": ("native_value",),
    "datetime": ("native_value",),
    "device_tracker": (
        "location",
        "gps",
        "latitude",
        "longitude",
        "location_accuracy",
    ),
    "event": ("device_class", "event_types", "event_type", "event_attributes"),
    "fan": FAN_NATIVE_TEMPLATE_PROPERTIES,
    "geolocation": ("latitude", "longitude", "source", "unit_of_measurement"),
    "humidifier": HUMIDIFIER_NATIVE_TEMPLATE_PROPERTIES,
    "image": (
        "source_entity",
        "image_path",
        "image_url",
        "entity_picture",
        "image_last_updated",
        "content_type",
        "svg",
    ),
    "image_processing": ("camera_entity", "confidence", "device_class"),
    "lawn_mower": ("activity", "supported_features"),
    "light": (
        "is_on",
        "supported_color_modes",
        "color_mode",
        "brightness",
        "hs_color",
        "xy_color",
        "color_temp_kelvin",
        "min_color_temp_kelvin",
        "max_color_temp_kelvin",
    ),
    "lock": (
        "support_open",
        "is_locked",
        "is_open",
        "is_locking",
        "is_unlocking",
        "is_jammed",
        "is_opening",
        "changed_by",
        "code_format",
    ),
    "media_player": (
        "media_state",
        "device_class",
        "supported_features",
        "source_list",
        "source",
        "sound_mode_list",
        "sound_mode",
        "volume_level",
        "volume_step",
        "is_volume_muted",
        "media_content_id",
        "media_content_type",
        "media_duration",
        "media_position",
        "media_position_updated_at",
        "media_title",
        "media_artist",
        "media_album_artist",
        "media_album_name",
        "media_series_title",
        "media_season",
        "media_episode",
        "media_channel",
        "media_playlist",
        "media_track",
        "media_image_url",
        "media_image_remotely_accessible",
        "app_id",
        "app_name",
        "group_members",
        "shuffle",
        "repeat",
    ),
    "notify": ("device_class", "supported_features"),
    "number": (
        "native_min_value",
        "native_max_value",
        "native_step",
        "native_value",
        "mode",
        "device_class",
        "native_unit_of_measurement",
    ),
    "remote": ("is_on", "activity_list", "current_activity"),
    "select": ("options", "current_option"),
    "sensor": (
        "device_class",
        "state_class",
        "options",
        "native_unit_of_measurement",
        "suggested_display_precision",
        "suggested_unit_of_measurement",
        "last_reset",
    ),
    "siren": (
        "is_on",
        "available_tones",
        "support_volume",
        "support_duration",
        "supported_features",
    ),
    "stt": (
        "supported_languages",
        "supported_formats",
        "supported_codecs",
        "supported_bit_rates",
        "supported_sample_rates",
        "supported_channels",
    ),
    "switch": ("is_on", "device_class"),
    "text": ("native_min", "native_max", "mode", "pattern", "native_value"),
    "time": ("native_value",),
    "todo": ("todo_items", "supported_features"),
    "tts": (
        "supported_languages",
        "default_language",
        "supported_options",
        "default_options",
    ),
    "update": (
        "installed_version",
        "latest_version",
        "title",
        "auto_update",
        "in_progress",
        "update_percentage",
        "display_precision",
        "device_class",
        "versions",
        "support_backup",
        "release_notes",
        "release_summary",
        "release_url",
        "supported_features",
    ),
    "vacuum": (
        "activity",
        "battery_level",
        "fan_speed_list",
        "fan_speed",
        "supported_features",
    ),
    "valve": (
        "current_valve_position",
        "is_opening",
        "is_closing",
        "is_closed",
        "device_class",
        "reports_position",
        "supported_features",
    ),
    "water_heater": (
        "operation_list",
        "current_operation",
        "min_temp",
        "max_temp",
        "current_temperature",
        "target_temperature",
        "target_temperature_high",
        "target_temperature_low",
        "target_temperature_step",
        "temperature_unit",
        "is_away_mode_on",
        "precision",
        "supported_features",
    ),
    "weather": (
        "condition",
        "native_temperature",
        "native_temperature_unit",
        "native_apparent_temperature",
        "native_dew_point",
        "humidity",
        "native_pressure",
        "native_pressure_unit",
        "native_visibility",
        "native_visibility_unit",
        "native_wind_speed",
        "native_wind_gust_speed",
        "native_wind_speed_unit",
        "wind_bearing",
        "cloud_coverage",
        "ozone",
        "uv_index",
        "native_precipitation_unit",
        "precision",
        "supported_features",
    ),
}

NATIVE_TEMPLATE_ATTRIBUTE_ALIASES = {
    "current_cover_position": "current_position",
    "current_cover_tilt_position": "current_tilt_position",
    "current_direction": "direction",
    "current_valve_position": "current_position",
    "is_away_mode_on": "away_mode",
    "motion_detection_enabled": "motion_detection",
    "native_max": "max",
    "native_max_value": "max",
    "native_min": "min",
    "native_min_value": "min",
    "native_step": "step",
    "native_unit_of_measurement": "unit_of_measurement",
    "target_humidity": "humidity",
    "target_temperature": "temperature",
    "target_temperature_high": "target_temp_high",
    "target_temperature_low": "target_temp_low",
    "target_temperature_step": "target_temp_step",
    "temperature_unit": "unit_of_measurement",
}
NATIVE_TEMPLATE_ATTRIBUTE_ALIASES.update(
    {
        "native_apparent_temperature": "apparent_temperature",
        "native_dew_point": "dew_point",
        "native_precipitation_unit": "precipitation_unit",
        "native_pressure": "pressure",
        "native_pressure_unit": "pressure_unit",
        "native_temperature": "temperature",
        "native_temperature_unit": "temperature_unit",
        "native_visibility": "visibility",
        "native_visibility_unit": "visibility_unit",
        "native_wind_gust_speed": "wind_gust_speed",
        "native_wind_speed": "wind_speed",
        "native_wind_speed_unit": "wind_speed_unit",
    }
)
NATIVE_TEMPLATE_STATE_PROPERTIES = frozenset({
    "activity",
    "condition",
    "current_operation",
    "current_option",
    "event_type",
    "hvac_mode",
    "image_last_updated",
    "location",
    "media_state",
    "native_value",
})
NATIVE_TEMPLATE_BOOLEAN_STATE_VALUES = {
    "is_closed": {"closed"},
    "is_closing": {"closing"},
    "is_jammed": {"jammed"},
    "is_locked": {"locked"},
    "is_locking": {"locking"},
    "is_open": {"open"},
    "is_opening": {"opening"},
    "is_recording": {"recording"},
    "is_streaming": {"streaming"},
    "is_unlocking": {"unlocking"},
}
NATIVE_TEMPLATE_SUPPORTED_FEATURE_MASKS = {
    ("lock", "support_open"): 1,
    ("siren", "support_volume"): 8,
    ("siren", "support_duration"): 16,
    ("update", "support_backup"): 8,
}
NATIVE_TEMPLATE_BOOLEAN_PROPERTIES = frozenset({
    "auto_update",
    "code_arm_required",
    "in_progress",
    "is_away_mode_on",
    "is_on",
    "is_recording",
    "is_streaming",
    "is_volume_muted",
    "media_image_remotely_accessible",
    "motion_detection_enabled",
    "oscillating",
    "reports_position",
    "shuffle",
    "support_backup",
    "support_duration",
    "support_open",
    "support_volume",
    "supports_streaming",
}) | frozenset(NATIVE_TEMPLATE_BOOLEAN_STATE_VALUES)
NATIVE_TEMPLATE_BOOLEAN_ANY_PROPERTIES = frozenset({
    "code_arm_required",
    "reports_position",
    "support_backup",
    "support_duration",
    "support_open",
    "support_volume",
    "supports_streaming",
})
NATIVE_TEMPLATE_LIST_PROPERTIES = frozenset({
    property_name
    for properties in DOMAIN_NATIVE_TEMPLATE_PROPERTIES.values()
    for property_name in properties
    if property_name.endswith(("_list", "_modes", "_languages"))
}) | frozenset({
    "activity_list",
    "available_tones",
    "event_types",
    "group_members",
    "options",
    "source_list",
    "supported_color_modes",
    "supported_bit_rates",
    "supported_channels",
    "supported_codecs",
    "supported_formats",
    "supported_sample_rates",
    "todo_items",
    "versions",
})
NATIVE_TEMPLATE_MAPPING_PROPERTIES = frozenset({
    "default_options",
    "event",
    "event_attributes",
    "tts_options",
})
NATIVE_TEMPLATE_ATOMIC_LIST_PROPERTIES = frozenset({
    "gps",
    "hs_color",
    "rgb_color",
    "rgbw_color",
    "rgbww_color",
    "xy_color",
})
NATIVE_TEMPLATE_BITMASK_PROPERTIES = frozenset({"supported_features"})
NATIVE_TEMPLATE_MINIMUM_PROPERTIES = frozenset({
    "min_color_temp_kelvin",
    "min_humidity",
    "min_temp",
    "native_min",
    "native_min_value",
})
NATIVE_TEMPLATE_MAXIMUM_PROPERTIES = frozenset({
    "max_color_temp_kelvin",
    "max_humidity",
    "max_temp",
    "native_max",
    "native_max_value",
})
NATIVE_TEMPLATE_NUMERIC_PROPERTIES = frozenset({
    "air_quality_index",
    "battery_level",
    "brightness",
    "carbon_dioxide",
    "carbon_monoxide",
    "cloud_coverage",
    "confidence",
    "color_temp_kelvin",
    "current_cover_position",
    "current_cover_tilt_position",
    "current_humidity",
    "current_position",
    "current_temperature",
    "current_valve_position",
    "display_precision",
    "frame_interval",
    "humidity",
    "latitude",
    "location_accuracy",
    "longitude",
    "max_humidity",
    "max_temp",
    "min_humidity",
    "min_temp",
    "native_apparent_temperature",
    "native_dew_point",
    "native_max",
    "native_max_value",
    "native_min",
    "native_min_value",
    "native_pressure",
    "native_step",
    "native_temperature",
    "native_visibility",
    "native_wind_gust_speed",
    "native_wind_speed",
    "nitrogen_dioxide",
    "nitrogen_monoxide",
    "nitrogen_oxide",
    "ozone",
    "particulate_matter_0_1",
    "particulate_matter_10",
    "particulate_matter_2_5",
    "percentage",
    "precision",
    "media_duration",
    "media_position",
    "media_track",
    "min_color_temp_kelvin",
    "max_color_temp_kelvin",
    "speed_count",
    "suggested_display_precision",
    "sulphur_dioxide",
    "target_humidity",
    "target_humidity_step",
    "target_temperature",
    "target_temperature_high",
    "target_temperature_low",
    "target_temperature_step",
    "update_percentage",
    "uv_index",
    "volume_level",
    "volume_step",
}) | NATIVE_TEMPLATE_MINIMUM_PROPERTIES | NATIVE_TEMPLATE_MAXIMUM_PROPERTIES
NATIVE_TEMPLATE_DATETIME_PROPERTIES = frozenset({
    "image_last_updated",
    "last_reset",
    "media_position_updated_at",
})


def _native_source_helper_default(platform: str, property_name: str) -> Any:
    """Return a valid fallback used only by generated source helpers."""
    configured = DOMAIN_NATIVE_SOURCE_TEMPLATE_DEFAULT_VALUES.get(platform, {})
    if property_name in configured:
        return copy.deepcopy(configured[property_name])
    if property_name in NATIVE_TEMPLATE_BOOLEAN_PROPERTIES:
        return False
    if property_name in NATIVE_TEMPLATE_BITMASK_PROPERTIES:
        return 0
    if property_name == "gps":
        return [0.0, 0.0]
    if property_name in NATIVE_TEMPLATE_ATOMIC_LIST_PROPERTIES:
        sizes = {
            "hs_color": 2,
            "xy_color": 2,
            "rgb_color": 3,
            "rgbw_color": 4,
            "rgbww_color": 5,
        }
        return [0] * sizes.get(property_name, 0)
    if property_name in NATIVE_TEMPLATE_LIST_PROPERTIES:
        return []
    if property_name in NATIVE_TEMPLATE_MAPPING_PROPERTIES:
        return {}
    if property_name in NATIVE_TEMPLATE_NUMERIC_PROPERTIES:
        return 0
    return None


def _native_source_snapshot_fallback(
    platform: str,
    property_name: str,
    value: Any,
) -> Any:
    """Return a type-safe literal fallback from a source's current value."""
    if value is None:
        return None
    if property_name in NATIVE_TEMPLATE_BOOLEAN_PROPERTIES:
        try:
            return cv.boolean(value)
        except vol.Invalid:
            return None
    if property_name in NATIVE_TEMPLATE_BITMASK_PROPERTIES:
        if isinstance(value, bool):
            return None
        try:
            value = int(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return value if value >= 0 else None
    if property_name in NATIVE_TEMPLATE_NUMERIC_PROPERTIES or (
        platform == "number" and property_name == "native_value"
    ):
        if isinstance(value, bool):
            return None
        try:
            numeric_value = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if not math.isfinite(numeric_value):
            return None
    if property_name in (
        NATIVE_TEMPLATE_LIST_PROPERTIES | NATIVE_TEMPLATE_ATOMIC_LIST_PROPERTIES
    ) and not isinstance(value, (list, tuple, set, frozenset)):
        return None
    if (
        property_name in NATIVE_TEMPLATE_MAPPING_PROPERTIES
        and not isinstance(value, Mapping)
    ):
        return None
    return _json_safe(_plain_options(value))


_AUTO_HELPER_PROFILE_FIELDS = (
    CONF_PLATFORM,
    CONF_INITIAL_VALUE,
    CONF_SOURCE_ENTITIES_TEXT,
    CONF_TEMPLATE_SOURCES_JSON,
    CONF_VALUE_TEMPLATE,
    CONF_AVAILABILITY_TEMPLATE,
    CONF_ICON_TEMPLATE,
    CONF_EVENT_HOOKS_JSON,
    CONF_ATTRIBUTES_JSON,
    CONF_ATTRIBUTE_SOURCES_JSON,
    CONF_ATTRIBUTE_TEMPLATES_JSON,
    CONF_NATIVE_TEMPLATES_JSON,
    CONF_NATIVE_VALUE_TEMPLATES,
    CONF_COMMAND_ACTIONS_JSON,
    CONF_DOMAIN_OPTIONS_JSON,
    *CLIMATE_FORM_FIELDS,
    *FAN_FORM_FIELDS,
    *HUMIDIFIER_FORM_FIELDS,
)

_AUTO_HELPER_JSON_FIELDS = frozenset(
    {
        CONF_TEMPLATE_SOURCES_JSON,
        CONF_EVENT_HOOKS_JSON,
        CONF_ATTRIBUTES_JSON,
        CONF_ATTRIBUTE_SOURCES_JSON,
        CONF_ATTRIBUTE_TEMPLATES_JSON,
        CONF_NATIVE_TEMPLATES_JSON,
        CONF_COMMAND_ACTIONS_JSON,
        CONF_DOMAIN_OPTIONS_JSON,
    }
)

_AUTO_HELPER_TEMPLATE_FIELDS = frozenset(
    {
        CONF_TEMPLATE_SOURCES_JSON,
        CONF_VALUE_TEMPLATE,
        CONF_NATIVE_TEMPLATES_JSON,
    }
)
_AUTO_HELPER_INDEPENDENT_TEMPLATE_FIELDS = frozenset({
    CONF_AVAILABILITY_TEMPLATE,
    CONF_ICON_TEMPLATE,
})

_ATTRIBUTE_HELPER_METADATA_NAMES = frozenset(
    {
        ATTR_FRIENDLY_NAME,
        CONF_ICON,
        CONF_UNIT_OF_MEASUREMENT,
        "attribution",
        "device_class",
        "supported_features",
    }
) | TRANSIENT_SOURCE_ATTRIBUTE_NAMES

ACTION_ADD_ENTITY = "add_entity"
ACTION_DELETE_ENTITY = "delete_entity"
ACTION_DELETE_DEVICE = "delete_device"
ACTION_EDIT_ENTITY = "edit_entity"
ACTION_FINISH = "finish"
ACTION_MANAGE_DEVICES = "manage_devices"

DEFAULT_ENTITY_DOMAIN = "sensor"
DEFAULT_ENTITY_VALUE = "unknown"
MAX_GENERATED_ENTITY_NAME_LENGTH = 80
MAX_GENERATED_ENTITY_OBJECT_ID_LENGTH = 80
DEFAULT_NUMBER_MIN = 0
DEFAULT_NUMBER_MAX = 100
DEFAULT_INITIAL_VALUES = {
    "climate": "off",
    "fan": "off",
    "humidifier": "off",
}
CLIMATE_INITIAL_VALUES = (
    "off",
    "heat",
    "cool",
    "heat_cool",
    "auto",
    "dry",
    "fan_only",
)
CLIMATE_ACTION_VALUES = (
    "off",
    "heating",
    "cooling",
    "drying",
    "fan",
    "idle",
    "preheating",
    "defrosting",
)
TEMPERATURE_UNIT_VALUES = ("°C", "°F", "K")
HUMIDIFIER_ACTION_VALUES = ("off", "humidifying", "drying", "idle")
HUMIDIFIER_CLASS_VALUES = ("humidifier", "dehumidifier")
MATTER_LIGHT_TYPES = ("on_off", "dimmable", "color_temperature", "extended_color")

_DOMAIN_OPTION_RESERVED_KEYS = {
    ATTR_ENTITY_ID,
    ATTR_ENTITY_KEY,
    ATTR_FRIENDLY_NAME,
    ATTR_UNIQUE_ID,
    CONF_PLATFORM,
    CONF_NAME,
    CONF_INITIAL_VALUE,
    CONF_INITIAL_AVAILABILITY,
    CONF_PERSISTENT,
    CONF_SOURCE_ENTITIES,
    CONF_TEMPLATE_SOURCES,
    CONF_PULL_INTERVAL,
    CONF_VALUE_TEMPLATE,
    CONF_AVAILABILITY_TEMPLATE,
    CONF_ATTRIBUTES,
    CONF_ICON,
    CONF_ICON_TEMPLATE,
    CONF_AUTO_HELPER,
    CONF_ATTRIBUTE_SOURCES,
    CONF_ATTRIBUTE_TEMPLATES,
    CONF_NATIVE_TEMPLATES,
    CONF_COMMAND_ACTIONS,
    CONF_EVENT_HOOKS,
    CONF_POLYGONAL_ZONE,
    ATTR_DEVICE_ID,
    CONF_MANUFACTURER,
    CONF_MODEL,
    CONF_SW_VERSION,
    CONF_HW_VERSION,
    CONF_SERIAL_NUMBER,
    CONF_CONFIGURATION_URL,
    CONF_SUGGESTED_AREA,
    CONF_VIA_DEVICE_ID,
}

MULTILINE_TEXT_SELECTOR = selector.TextSelector(
    selector.TextSelectorConfig(multiline=True),
)
TEMPLATE_SELECTOR = selector.TemplateSelector()
ICON_SELECTOR = selector.IconSelector(selector.IconSelectorConfig())
ENTITY_SELECTOR = selector.EntitySelector(
    selector.EntitySelectorConfig(
        multiple=True,
        reorder=True,
    ),
)
PULL_INTERVAL_SELECTOR = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=0,
        step=1,
        mode=selector.NumberSelectorMode.BOX,
    ),
)
POLYGON_DISTANCE_SELECTOR = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=1,
        step="any",
        mode=selector.NumberSelectorMode.BOX,
    ),
)


def _native_property_selector(platform: str, property_name: str):
    """Return a useful editor while keeping native values template-backed."""
    return TEMPLATE_SELECTOR


def _reference_entity_schema(
    entity_ids: list[str] | None = None,
    device_options: list[dict[str, str]] | None = None,
    default_device_name: str | None = None,
) -> vol.Schema:
    """Build the copy-source selector with the entity's current sources."""
    schema = {
        vol.Optional(
            CONF_REFERENCE_ENTITY_ID,
            default=entity_ids or [],
        ): ENTITY_SELECTOR,
    }
    if device_options:
        valid_device_names = {option["value"] for option in device_options}
        default_device_name = (
            default_device_name
            if default_device_name in valid_device_names
            else NEW_DEVICE_TARGET
        )
        schema[
            vol.Optional(
                CONF_TARGET_DEVICE_NAME,
                default=default_device_name,
            )
        ] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=device_options,
                mode=selector.SelectSelectorMode.DROPDOWN,
            ),
        )
    return vol.Schema(schema)


def _helper_update_schema() -> vol.Schema:
    """Choose how generated templates are handled after source changes."""
    return vol.Schema(
        {
            vol.Required(
                CONF_HELPER_UPDATE_MODE,
                default=HELPER_UPDATE_AUTO,
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        HELPER_UPDATE_AUTO,
                        HELPER_UPDATE_KEEP,
                        HELPER_UPDATE_FORCE,
                    ],
                    translation_key="helper_update_mode",
                    mode=selector.SelectSelectorMode.LIST,
                )
            ),
        }
    )


def _helper_usage_schema() -> vol.Schema:
    """Choose whether source-based helpers populate a new entity form."""
    return vol.Schema(
        {
            vol.Required(
                CONF_USE_TEMPLATE_HELPER,
                default=True,
            ): selector.BooleanSelector(),
        }
    )


_SINGLE_SOURCE_TARGET_DOMAINS = {
    "fan": ("fan", "switch", "light"),
    "humidifier": ("humidifier", "switch", "fan"),
    "input_boolean": ("binary_sensor", "switch", "fan", "light"),
    "light": ("light", "switch", "fan"),
    "switch": ("switch", "fan", "light"),
}


def _source_target_domains(
    source_entity_ids: str | Collection[str],
    inferred_platform: str,
    preserved_platform: str | None = None,
) -> tuple[str, ...]:
    """Return supported target domains for the selected source composition."""
    entity_ids = (
        [source_entity_ids]
        if isinstance(source_entity_ids, str)
        else list(source_entity_ids)
    )
    configured: tuple[str, ...] = ()
    if len(entity_ids) == 1:
        source_domain = entity_ids[0].split(".", 1)[0]
        configured = _SINGLE_SOURCE_TARGET_DOMAINS.get(source_domain, ())
    elif _humidifier_component_profile(entity_ids) is not None:
        configured = ("humidifier",)
    preserved = (
        (preserved_platform,)
        if preserved_platform in VIRTUAL_ENTITY_DOMAINS
        else ()
    )
    return tuple(dict.fromkeys((inferred_platform, *configured, *preserved)))


def _entity_type_schema(
    source_entity_ids: str | Collection[str],
    inferred_platform: str,
    default_platform: str | None = None,
) -> vol.Schema:
    """Choose a compatible target domain for one or more source entities."""
    platforms = _source_target_domains(
        source_entity_ids,
        inferred_platform,
        default_platform,
    )
    selected = default_platform if default_platform in platforms else inferred_platform
    options = [
        {"value": platform, "label": platform.replace("_", " ").title()}
        for platform in platforms
    ]
    return vol.Schema({
        vol.Required(CONF_TARGET_ENTITY_TYPE, default=selected): (
            selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=options,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
        ),
    })


def _has_entity_type_choice(
    source_entity_ids: Collection[str],
    inferred_platform: str,
    preserved_platform: str | None = None,
) -> bool:
    """Return whether the selected sources support more than one target type."""
    return len(
        _source_target_domains(
            source_entity_ids,
            inferred_platform,
            preserved_platform,
        )
    ) > 1


BOOLEAN_SOURCE_DOMAINS = {
    "binary_sensor",
    "fan",
    "humidifier",
    "input_boolean",
    "light",
    "lock",
    "remote",
    "siren",
    "switch",
}
BOOLEAN_TRUE_STATES = {"1", "on", "open", "true", "unlocked", "yes"}
BOOLEAN_FALSE_STATES = {"0", "closed", "false", "locked", "no", "off"}
NUMBER_SOURCE_DOMAINS = {"counter", "input_number", "number"}
DATE_SOURCE_DOMAINS = {"date"}
TIME_SOURCE_DOMAINS = {"time"}
DATETIME_SOURCE_DOMAINS = {"datetime"}
ENUM_SOURCE_DOMAINS = {"input_select", "select"}
LOCATION_SOURCE_DOMAINS = {"device_tracker", "geolocation", "person"}
LOCATION_HELPER_DISTANCE_METERS = 300
LOCATION_HELPER_PRIORITY_WINDOW_SECONDS = 30 * 60
PRESENCE_MOTION_CLEAR_DELAY_SECONDS = 5 * 60
PRESENCE_MOTION_DEVICE_CLASSES = frozenset({"motion", "presence"})
SAFETY_BOOLEAN_DEVICE_CLASSES = frozenset(
    {
        "carbon_monoxide",
        "gas",
        "moisture",
        "problem",
        "safety",
        "smoke",
    }
)
NON_MERGEABLE_SOURCE_DOMAINS = frozenset({"camera", "image"})
FIRST_KNOWN_STATE_SOURCE_DOMAINS = frozenset({
    "ai_task",
    "air_quality",
    "alarm_control_panel",
    "assist_satellite",
    "button",
    "calendar",
    "climate",
    "conversation",
    "cover",
    "event",
    "image_processing",
    "infrared",
    "lawn_mower",
    "media_player",
    "notify",
    "radio_frequency",
    "scene",
    "stt",
    "tag",
    "todo",
    "tts",
    "update",
    "vacuum",
    "valve",
    "wake_word",
    "water_heater",
    "weather",
})
UNKNOWN_STATES = {"", "none", "unknown", "unavailable"}
TEMPLATE_VARIABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
JINJA_RESERVED_VARIABLE_NAMES = {
    "and",
    "as",
    "block",
    "elif",
    "else",
    "endblock",
    "endfilter",
    "endfor",
    "endif",
    "endmacro",
    "endset",
    "endwith",
    "false",
    "filter",
    "for",
    "from",
    "if",
    "import",
    "in",
    "is",
    "macro",
    "none",
    "not",
    "or",
    "set",
    "true",
    "with",
}

# These names are valid Jinja identifiers, so legacy/custom template sources
# may keep using them. Newly generated variables avoid them because they would
# shadow Home Assistant globals or per-render context.
JINJA_CONTEXT_VARIABLE_NAMES = {
    "area_devices",
    "area_entities",
    "area_id",
    "area_name",
    "areas",
    "as_datetime",
    "as_local",
    "as_timestamp",
    "closest",
    "config_entry_attr",
    "config_entry_device",
    "config_entry_entities",
    "config_entry_id",
    "cycler",
    "device_attr",
    "device_entities",
    "device_id",
    "dict",
    "distance",
    "expand",
    "floor_areas",
    "floor_devices",
    "floor_entities",
    "has_value",
    "integration_entities",
    "is_state",
    "is_state_attr",
    "issues",
    "joiner",
    "label_devices",
    "label_entities",
    "labels",
    "lipsum",
    "namespace",
    "now",
    "range",
    "state_attr",
    "states",
    "this",
    "timedelta",
    "today_at",
    "trigger",
    "utcnow",
}

CALENDAR_EVENT_SOURCE_ATTRIBUTES = frozenset({
    "all_day",
    "description",
    "end_time",
    "location",
    "message",
    "start_time",
})


def _options_schema(options: dict[str, Any]) -> vol.Schema:
    actions = [ACTION_ADD_ENTITY]
    if _entity_choices(options):
        actions.append(ACTION_EDIT_ENTITY)
    if _entity_choices(options, include_invalid=True):
        actions.append(ACTION_DELETE_ENTITY)
    if _options_devices(options):
        actions.append(ACTION_MANAGE_DEVICES)
        actions.append(ACTION_DELETE_DEVICE)
    actions.append(ACTION_FINISH)
    return vol.Schema(
        {
            vol.Required(
                CONF_ACTION, default=ACTION_ADD_ENTITY
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=actions,
                    translation_key="options_action",
                ),
            ),
        }
    )


def _setup_schema(
    defaults: dict[str, Any], include_entity_toggle: bool = True
) -> vol.Schema:
    schema = {
        vol.Required(ATTR_GROUP_NAME, default=defaults.get(ATTR_GROUP_NAME, "")): str,
    }
    if include_entity_toggle:
        schema[vol.Optional(CONF_ADD_FIRST_ENTITY, default=False)] = cv.boolean
    return vol.Schema(schema)


def _normalized_group_name(value) -> str:
    """Return a non-empty Device group name without accidental whitespace."""
    if not isinstance(value, str):
        raise MissingGroupName
    name = value.strip()
    if not name:
        raise MissingGroupName
    return name


def _flatten_entity_form_sections(user_input: Mapping | None) -> dict[str, Any]:
    """Return sectioned entity form data in the persisted flat shape."""
    flattened = dict(user_input or {})
    section_values = {}
    for section_name in (
        CONF_DEVICE_DETAILS,
        CONF_DOMAIN_SETTINGS,
        CONF_ADVANCED_SETTINGS,
    ):
        values = flattened.pop(section_name, None)
        if isinstance(values, Mapping):
            section_values.update(values)
    # Flat values win for compatibility with flows opened before an integration
    # reload changed these controls into sections.
    section_values.update(flattened)
    return section_values


def _literal_template(value: Any) -> str:
    """Render a static native value as an editable Jinja literal."""
    return "{{ " + repr(_json_safe(_plain_options(value))) + " }}"


def _native_template_defaults(
    platform: str,
    defaults: Mapping,
) -> dict[str, str]:
    """Build complete native Jinja defaults and migrate old static fields."""
    configured = defaults.get(CONF_NATIVE_VALUE_TEMPLATES)
    configured = _native_template_mapping(configured)
    legacy_aliases = LEGACY_STATIC_NATIVE_FIELD_ALIASES.get(platform, {})
    fallback_values = DOMAIN_NATIVE_TEMPLATE_DEFAULT_VALUES.get(platform, {})
    result = {}
    for property_name in DOMAIN_NATIVE_TEMPLATE_PROPERTIES.get(platform, ()):
        template = configured.get(property_name, "").strip()
        if template:
            result[property_name] = template
            continue

        legacy_names = [property_name]
        legacy_names.extend(
            field_name
            for field_name, native_name in legacy_aliases.items()
            if native_name == property_name
        )
        legacy_value = next(
            (
                defaults[field_name]
                for field_name in legacy_names
                if field_name in defaults
            ),
            fallback_values.get(property_name, _MISSING_NATIVE_DEFAULT),
        )
        result[property_name] = (
            _literal_template(legacy_value)
            if legacy_value is not _MISSING_NATIVE_DEFAULT
            else ""
        )
    return result


def _entity_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    defaults = _flatten_entity_form_sections(defaults)
    platform = defaults.get(CONF_PLATFORM, DEFAULT_ENTITY_DOMAIN)
    managed_native_properties = set(
        DOMAIN_NATIVE_TEMPLATE_PROPERTIES.get(platform, ())
    )
    if managed_native_properties and CONF_NATIVE_VALUE_TEMPLATES not in defaults:
        try:
            stored_templates = _parse_native_templates(
                str(defaults.get(CONF_NATIVE_TEMPLATES_JSON, "") or "").strip()
            )
        except InvalidJson:
            stored_templates = {}
        defaults[CONF_NATIVE_VALUE_TEMPLATES] = {
            property_name: template_value
            for property_name, template_value in stored_templates.items()
            if property_name in managed_native_properties
        }
        if stored_templates:
            defaults[CONF_NATIVE_TEMPLATES_JSON] = _json_default(
                {
                    property_name: template_value
                    for property_name, template_value in stored_templates.items()
                    if property_name not in managed_native_properties
                }
            )
    entity_name = defaults.get(CONF_ENTITY_NAME, "Virtual Entity")
    default_entity_id = defaults.get(ATTR_ENTITY_ID) or _default_virtual_entity_id(
        platform,
        entity_name,
    )
    device_details_schema = vol.Schema({
        vol.Optional(CONF_DEVICE_ID, default=defaults.get(CONF_DEVICE_ID, "")): str,
        vol.Optional(
            CONF_DEVICE_MANUFACTURER, default=defaults.get(CONF_DEVICE_MANUFACTURER, "")
        ): str,
        vol.Optional(
            CONF_DEVICE_MODEL, default=defaults.get(CONF_DEVICE_MODEL, "")
        ): str,
        vol.Optional(
            CONF_DEVICE_SW_VERSION, default=defaults.get(CONF_DEVICE_SW_VERSION, "")
        ): str,
        vol.Optional(
            CONF_DEVICE_HW_VERSION, default=defaults.get(CONF_DEVICE_HW_VERSION, "")
        ): str,
        vol.Optional(
            CONF_DEVICE_SERIAL_NUMBER,
            default=defaults.get(CONF_DEVICE_SERIAL_NUMBER, ""),
        ): str,
        vol.Optional(
            CONF_DEVICE_CONFIGURATION_URL,
            default=defaults.get(CONF_DEVICE_CONFIGURATION_URL, ""),
        ): str,
        vol.Optional(
            CONF_DEVICE_SUGGESTED_AREA,
            default=defaults.get(CONF_DEVICE_SUGGESTED_AREA, ""),
        ): str,
        vol.Optional(
            CONF_DEVICE_VIA_DEVICE_ID,
            default=defaults.get(CONF_DEVICE_VIA_DEVICE_ID, ""),
        ): selector.DeviceSelector(),
    })
    advanced_schema = {
        vol.Optional(
            CONF_TEMPLATE_SOURCES_JSON,
            default=defaults.get(CONF_TEMPLATE_SOURCES_JSON, ""),
        ): MULTILINE_TEXT_SELECTOR,
        vol.Optional(
            CONF_EVENT_HOOKS_JSON, default=defaults.get(CONF_EVENT_HOOKS_JSON, "")
        ): MULTILINE_TEXT_SELECTOR,
        vol.Optional(
            CONF_ATTRIBUTES_JSON, default=defaults.get(CONF_ATTRIBUTES_JSON, "")
        ): MULTILINE_TEXT_SELECTOR,
        vol.Optional(
            CONF_ATTRIBUTE_SOURCES_JSON,
            default=defaults.get(CONF_ATTRIBUTE_SOURCES_JSON, ""),
        ): MULTILINE_TEXT_SELECTOR,
        vol.Optional(
            CONF_ATTRIBUTE_TEMPLATES_JSON,
            default=defaults.get(CONF_ATTRIBUTE_TEMPLATES_JSON, ""),
        ): MULTILINE_TEXT_SELECTOR,
        vol.Optional(
            CONF_COMMAND_ACTIONS_JSON,
            default=defaults.get(CONF_COMMAND_ACTIONS_JSON, ""),
        ): MULTILINE_TEXT_SELECTOR,
        vol.Optional(
            CONF_DOMAIN_OPTIONS_JSON, default=defaults.get(CONF_DOMAIN_OPTIONS_JSON, "")
        ): MULTILINE_TEXT_SELECTOR,
    }
    if platform not in DOMAIN_NATIVE_TEMPLATE_PROPERTIES:
        advanced_schema[
            vol.Optional(
                CONF_NATIVE_TEMPLATES_JSON,
                default=defaults.get(CONF_NATIVE_TEMPLATES_JSON, ""),
            )
        ] = MULTILINE_TEXT_SELECTOR
    schema = {
        vol.Required(
            CONF_DEVICE_NAME, default=defaults.get(CONF_DEVICE_NAME, "Virtual Device")
        ): str,
        vol.Optional(CONF_DEVICE_DETAILS, default=dict): section(
            device_details_schema,
            {"collapsed": True},
        ),
        vol.Required(
            CONF_ENTITY_NAME, default=defaults.get(CONF_ENTITY_NAME, "Virtual Entity")
        ): str,
        vol.Optional(CONF_ICON, default=defaults.get(CONF_ICON, "")): ICON_SELECTOR,
        vol.Optional(
            CONF_ICON_TEMPLATE,
            default=defaults.get(CONF_ICON_TEMPLATE, ""),
        ): TEMPLATE_SELECTOR,
        vol.Optional(ATTR_ENTITY_ID, default=default_entity_id): str,
        vol.Required(
            CONF_PLATFORM, default=defaults.get(CONF_PLATFORM, DEFAULT_ENTITY_DOMAIN)
        ): vol.In(VIRTUAL_ENTITY_DOMAINS),
        vol.Required(
            CONF_INITIAL_VALUE,
            default=defaults.get(CONF_INITIAL_VALUE, DEFAULT_ENTITY_VALUE),
        ): str,
        vol.Optional(
            CONF_INITIAL_AVAILABILITY,
            default=defaults.get(CONF_INITIAL_AVAILABILITY, True),
        ): cv.boolean,
        vol.Optional(
            CONF_PERSISTENT, default=defaults.get(CONF_PERSISTENT, True)
        ): cv.boolean,
        vol.Optional(
            CONF_SOURCE_ENTITIES_TEXT,
            default=defaults.get(CONF_SOURCE_ENTITIES_TEXT, ""),
        ): MULTILINE_TEXT_SELECTOR,
        vol.Optional(
            CONF_PULL_INTERVAL, default=defaults.get(CONF_PULL_INTERVAL, 0)
        ): PULL_INTERVAL_SELECTOR,
        vol.Optional(
            CONF_VALUE_TEMPLATE, default=defaults.get(CONF_VALUE_TEMPLATE, "")
        ): TEMPLATE_SELECTOR,
        vol.Optional(
            CONF_AVAILABILITY_TEMPLATE,
            default=defaults.get(CONF_AVAILABILITY_TEMPLATE, ""),
        ): TEMPLATE_SELECTOR,
        vol.Optional(CONF_ADVANCED_SETTINGS, default=dict): section(
            vol.Schema(advanced_schema),
            {"collapsed": True},
        ),
    }
    domain_schema = {}
    if platform == "device_tracker":
        domain_schema.update(
            {
                vol.Optional(
                    CONF_POLYGON_GEOJSON_JSON,
                    default=defaults.get(CONF_POLYGON_GEOJSON_JSON, ""),
                ): MULTILINE_TEXT_SELECTOR,
                vol.Optional(
                    CONF_POLYGON_FILES_TEXT,
                    default=defaults.get(CONF_POLYGON_FILES_TEXT, ""),
                ): MULTILINE_TEXT_SELECTOR,
                vol.Optional(
                    CONF_POLYGON_STRATEGY_INPUT,
                    default=defaults.get(CONF_POLYGON_STRATEGY_INPUT, "majority"),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=["majority", "priority", "latest", "median"],
                        translation_key="polygon_strategy",
                    )
                ),
                vol.Optional(
                    CONF_POLYGON_DISTANCE_INPUT,
                    default=defaults.get(CONF_POLYGON_DISTANCE_INPUT, 300),
                ): POLYGON_DISTANCE_SELECTOR,
                vol.Optional(
                    CONF_POLYGON_TRACKER_RULES_JSON,
                    default=defaults.get(CONF_POLYGON_TRACKER_RULES_JSON, ""),
                ): MULTILINE_TEXT_SELECTOR,
                vol.Optional(
                    CONF_POLYGON_AWAY_STATE_INPUT,
                    default=defaults.get(CONF_POLYGON_AWAY_STATE_INPUT, "not_home"),
                ): str,
            }
        )
        person_default = defaults.get(CONF_POLYGON_PERSON, "")
        person_marker = (
            vol.Optional(CONF_POLYGON_PERSON, default=person_default)
            if person_default
            else vol.Optional(CONF_POLYGON_PERSON)
        )
        domain_schema[person_marker] = selector.EntitySelector(
            selector.EntitySelectorConfig(domain="person"),
        )
    elif platform == "light":
        domain_schema[
            vol.Required(
                CONF_MATTER_LIGHT_TYPE,
                default=defaults.get(CONF_MATTER_LIGHT_TYPE, "dimmable"),
            )
        ] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=list(MATTER_LIGHT_TYPES),
                translation_key="matter_light_type",
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        )
    if domain_schema:
        schema[vol.Optional(CONF_DOMAIN_SETTINGS, default=dict)] = section(
            vol.Schema(domain_schema),
            {"collapsed": platform != "light"},
        )
    native_template_properties = DOMAIN_NATIVE_TEMPLATE_PROPERTIES.get(platform, ())
    if native_template_properties:
        template_defaults = _native_template_defaults(platform, defaults)
        template_schema = {}
        for property_name in native_template_properties:
            default = template_defaults.get(property_name)
            marker = vol.Optional(
                property_name,
                default=default if isinstance(default, str) else "",
            )
            template_schema[marker] = _native_property_selector(
                platform, property_name
            )
        schema[vol.Optional(CONF_NATIVE_VALUE_TEMPLATES, default=dict)] = section(
            vol.Schema(template_schema),
            {"collapsed": platform not in {"binary_sensor", "number", "sensor"}},
        )
    return vol.Schema(schema, extra=vol.ALLOW_EXTRA)


def _needs_domain_specific_form(user_input) -> bool:
    """Return true when a newly selected domain needs its dedicated fields."""
    platform = user_input.get(CONF_PLATFORM)
    native_template_properties = DOMAIN_NATIVE_TEMPLATE_PROPERTIES.get(platform)
    submitted_native_templates = user_input.get(CONF_NATIVE_VALUE_TEMPLATES)
    if native_template_properties and (
        not isinstance(submitted_native_templates, Mapping)
        or set(submitted_native_templates) != set(native_template_properties)
    ):
        return True
    if platform == "device_tracker":
        return CONF_POLYGON_STRATEGY_INPUT not in user_input
    if platform == "light":
        return CONF_MATTER_LIGHT_TYPE not in user_input
    return False


def _with_hidden_native_template_defaults(
    user_input: dict[str, Any],
    defaults: Mapping | None,
) -> dict[str, Any]:
    """Preserve nonstandard native templates hidden by a dedicated domain form."""
    if (
        user_input.get(CONF_PLATFORM) not in DOMAIN_NATIVE_TEMPLATE_PROPERTIES
        or CONF_NATIVE_TEMPLATES_JSON in user_input
        or not isinstance(defaults, Mapping)
    ):
        return user_input

    hidden_templates = defaults.get(CONF_NATIVE_TEMPLATES_JSON)
    if not hidden_templates:
        return user_input
    return {
        **user_input,
        CONF_NATIVE_TEMPLATES_JSON: hidden_templates,
    }


def _merge_entity_form_defaults(
    user_input: dict[str, Any],
    defaults: Mapping | None,
) -> dict[str, Any]:
    """Keep collapsed form sections while applying submitted edits."""
    if not isinstance(defaults, Mapping):
        return user_input
    merged = _flatten_entity_form_sections(defaults)
    for field, value in user_input.items():
        if field == CONF_NATIVE_VALUE_TEMPLATES and isinstance(value, Mapping):
            previous = merged.get(field)
            if isinstance(previous, Mapping):
                merged[field] = {**previous, **value}
                continue
        merged[field] = value
    return merged


def _complete_domain_form_defaults(user_input: dict[str, Any]) -> dict[str, Any]:
    """Fill the selected domain's dynamic controls before reopening its form."""
    completed = dict(user_input)
    platform = completed.get(CONF_PLATFORM)
    if platform in DOMAIN_NATIVE_TEMPLATE_PROPERTIES:
        completed[CONF_NATIVE_VALUE_TEMPLATES] = _native_template_defaults(
            platform,
            completed,
        )
    if platform == "device_tracker":
        completed.setdefault(CONF_POLYGON_STRATEGY_INPUT, "majority")
    return completed


def _align_form_entity_id_domain(user_input: dict[str, Any]) -> dict[str, Any]:
    """Keep a UI entity ID's object ID while aligning its selected domain."""
    platform = user_input.get(CONF_PLATFORM)
    entity_id = user_input.get(ATTR_ENTITY_ID)
    if platform not in VIRTUAL_ENTITY_DOMAINS or not isinstance(entity_id, str):
        return user_input
    current_domain, separator, object_id = entity_id.strip().partition(".")
    if (
        not separator
        or not object_id
        or current_domain == platform
        or current_domain not in VIRTUAL_ENTITY_DOMAINS
    ):
        return user_input
    updated_input = dict(user_input)
    updated_input[ATTR_ENTITY_ID] = f"{platform}.{object_id}"
    return updated_input


def _device_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Build the Device-only metadata form used by the options flow."""
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_DEVICE_NAME,
                default=defaults.get(CONF_DEVICE_NAME, "Virtual Device"),
            ): str,
            vol.Optional(CONF_DEVICE_ID, default=defaults.get(CONF_DEVICE_ID, "")): str,
            vol.Optional(
                CONF_DEVICE_MANUFACTURER,
                default=defaults.get(CONF_DEVICE_MANUFACTURER, ""),
            ): str,
            vol.Optional(
                CONF_DEVICE_MODEL, default=defaults.get(CONF_DEVICE_MODEL, "")
            ): str,
            vol.Optional(
                CONF_DEVICE_SW_VERSION,
                default=defaults.get(CONF_DEVICE_SW_VERSION, ""),
            ): str,
            vol.Optional(
                CONF_DEVICE_HW_VERSION,
                default=defaults.get(CONF_DEVICE_HW_VERSION, ""),
            ): str,
            vol.Optional(
                CONF_DEVICE_SERIAL_NUMBER,
                default=defaults.get(CONF_DEVICE_SERIAL_NUMBER, ""),
            ): str,
            vol.Optional(
                CONF_DEVICE_CONFIGURATION_URL,
                default=defaults.get(CONF_DEVICE_CONFIGURATION_URL, ""),
            ): str,
            vol.Optional(
                CONF_DEVICE_SUGGESTED_AREA,
                default=defaults.get(CONF_DEVICE_SUGGESTED_AREA, ""),
            ): str,
            vol.Optional(
                CONF_DEVICE_VIA_DEVICE_ID,
                default=defaults.get(CONF_DEVICE_VIA_DEVICE_ID, ""),
            ): selector.DeviceSelector(),
        }
    )


def _default_virtual_entity_id(platform: str, entity_name: str) -> str:
    """Return an entity id with the selected Home Assistant domain prefix."""
    if platform not in VIRTUAL_ENTITY_DOMAINS:
        return ""
    object_id = slugify(str(entity_name).removeprefix("+"))
    if not object_id:
        return ""
    object_id = object_id[:MAX_GENERATED_ENTITY_OBJECT_ID_LENGTH].rstrip("_")
    return f"{platform}.{object_id}"


def _default_virtual_entity_id_for_sources(
    platform: str,
    entity_name: str,
    source_entity_ids: Collection[str],
) -> str:
    """Generate a copy-safe default ID without changing the domain prefix."""
    entity_id = _default_virtual_entity_id(platform, entity_name)
    if entity_id not in source_entity_ids:
        return entity_id

    suffix = "_copy"
    object_id = slugify(str(entity_name).removeprefix("+")) or "virtual_entity"
    object_id = object_id[: MAX_GENERATED_ENTITY_OBJECT_ID_LENGTH - len(suffix)]
    object_id = object_id.rstrip("_") + suffix
    return f"{platform}.{object_id}"


def _reject_json_constant(value: str):
    """Reject Python-only JSON constants such as NaN and Infinity."""
    raise ValueError(f"Invalid JSON constant: {value}")


def _parse_json_object(value: str, field_name: str) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(
            value,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, RecursionError, TypeError, ValueError) as err:
        raise InvalidJson(field_name) from err
    if not isinstance(parsed, dict):
        raise InvalidJson(field_name)
    return _validate_ha_json_value(parsed, field_name)


def _parse_json_value(value: str, field_name: str):
    if not value:
        return None
    try:
        parsed = json.loads(
            value,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, RecursionError, TypeError, ValueError) as err:
        raise InvalidJson(field_name) from err
    return _validate_ha_json_value(parsed, field_name)


def _validate_ha_json_value(value, field_name: str):
    """Reject JSON values that Home Assistant cannot persist."""
    try:
        json_bytes(value)
    except (OverflowError, RecursionError, TypeError, ValueError) as err:
        raise InvalidJson(field_name) from err
    return value


def _parse_source_entities(value: Any) -> list[str]:
    """Normalize source IDs from current and legacy form payload shapes."""
    if value is None:
        return []
    if isinstance(value, str):
        if not value:
            return []
        raw_entities = value.replace(",", "\n").splitlines()
    elif isinstance(value, (list, tuple, set, frozenset)):
        raw_entities = list(value)
        if isinstance(value, (set, frozenset)):
            raw_entities.sort(key=lambda item: str(item))
    else:
        raise InvalidEntityReference(CONF_SOURCE_ENTITIES_TEXT)

    normalized_entities = []
    for entity_id in raw_entities:
        if not isinstance(entity_id, str):
            raise InvalidEntityReference(CONF_SOURCE_ENTITIES_TEXT)
        entity_id = entity_id.strip()
        if entity_id:
            normalized_entities.append(entity_id)
    try:
        source_entities = [
            cv.entity_id(entity_id) for entity_id in normalized_entities
        ]
    except vol.Invalid as err:
        raise InvalidEntityReference(CONF_SOURCE_ENTITIES_TEXT) from err
    return list(dict.fromkeys(source_entities))


def _parse_attribute_sources(value: str) -> dict[str, dict[str, str]]:
    parsed = _parse_json_object(value, CONF_ATTRIBUTE_SOURCES_JSON)
    attribute_sources = {}
    for target_attribute, source in parsed.items():
        if (
            not isinstance(target_attribute, str)
            or not target_attribute.strip()
            or target_attribute.strip() in RESERVED_VIRTUAL_ATTRIBUTE_NAMES
        ):
            raise InvalidJson(CONF_ATTRIBUTE_SOURCES_JSON)

        normalized_name = target_attribute.strip()
        if normalized_name in attribute_sources:
            raise InvalidJson(CONF_ATTRIBUTE_SOURCES_JSON)
        attribute_sources[normalized_name] = _parse_source_reference(
            source,
            CONF_ATTRIBUTE_SOURCES_JSON,
        )
    return attribute_sources


def _parse_template_sources(value: str) -> dict[str, dict[str, str]]:
    parsed = _parse_json_object(value, CONF_TEMPLATE_SOURCES_JSON)
    template_sources = {}
    for variable_name, source in parsed.items():
        if (
            not isinstance(variable_name, str)
            or not TEMPLATE_VARIABLE_NAME.fullmatch(variable_name.strip())
            or variable_name.strip().casefold() in JINJA_RESERVED_VARIABLE_NAMES
        ):
            raise InvalidJson(CONF_TEMPLATE_SOURCES_JSON)
        normalized_name = variable_name.strip()
        if normalized_name in template_sources:
            raise InvalidJson(CONF_TEMPLATE_SOURCES_JSON)
        template_sources[normalized_name] = _parse_source_reference(
            source,
            CONF_TEMPLATE_SOURCES_JSON,
            default_attribute="state",
        )
    return template_sources


def _parse_domain_options(value: str) -> dict[str, Any]:
    domain_options = _parse_json_object(value, CONF_DOMAIN_OPTIONS_JSON)
    if any(key in _DOMAIN_OPTION_RESERVED_KEYS for key in domain_options):
        raise InvalidJson(CONF_DOMAIN_OPTIONS_JSON)
    return domain_options


def _parse_native_templates(value: str) -> dict[str, str]:
    """Parse templates which feed native Home Assistant properties."""
    parsed = _parse_json_object(value, CONF_NATIVE_TEMPLATES_JSON)
    templates = {}
    for name, template in parsed.items():
        if (
            not VirtualEntity._valid_native_template_name(name)
            or not isinstance(template, str)
            or not template.strip()
        ):
            raise InvalidJson(CONF_NATIVE_TEMPLATES_JSON)
        normalized_name = name.strip()
        if normalized_name in templates:
            raise InvalidJson(CONF_NATIVE_TEMPLATES_JSON)
        templates[normalized_name] = repair_legacy_enum_template(template)
    return templates


def _normalize_attribute_mapping(
    value: Mapping,
    field_name: str,
    *,
    templates: bool = False,
) -> dict[str, Any]:
    """Normalize attribute names without silently merging distinct inputs."""
    normalized = {}
    for name, item in value.items():
        if (
            not isinstance(name, str)
            or not name.strip()
            or name.strip() in RESERVED_VIRTUAL_ATTRIBUTE_NAMES
            or templates
            and (not isinstance(item, str) or not item.strip())
        ):
            raise InvalidJson(field_name)
        normalized_name = name.strip()
        if normalized_name in normalized:
            raise InvalidJson(field_name)
        normalized[normalized_name] = (
            repair_legacy_enum_template(item) if templates else item
        )
    return normalized


def _without_transient_source_attributes(value: Mapping) -> dict[str, Any]:
    """Drop Home Assistant-owned source metadata from editable attributes."""
    return {
        name: item
        for name, item in value.items()
        if name not in TRANSIENT_SOURCE_ATTRIBUTE_NAMES
    }


def _parse_command_actions(value: str, platform: str | None = None) -> dict[str, Any]:
    """Parse and validate command-to-HA-action mappings."""
    parsed = repair_legacy_template_data(
        _parse_json_object(value, CONF_COMMAND_ACTIONS_JSON)
    )
    valid_commands = _platform_command_names(platform) if platform else None
    for command, spec in parsed.items():
        if not isinstance(command, str) or not command.strip().isidentifier():
            raise InvalidJson(CONF_COMMAND_ACTIONS_JSON)
        if valid_commands is not None and command.strip() not in valid_commands:
            raise InvalidJson(CONF_COMMAND_ACTIONS_JSON)
        if isinstance(spec, list):
            sequence = spec
        elif isinstance(spec, dict) and "sequence" in spec:
            if set(spec) - {"sequence", "optimistic"}:
                raise InvalidJson(CONF_COMMAND_ACTIONS_JSON)
            sequence = spec.get("sequence")
            if not isinstance(spec.get("optimistic", True), bool):
                raise InvalidJson(CONF_COMMAND_ACTIONS_JSON)
        elif isinstance(spec, dict):
            sequence = [spec]
        else:
            raise InvalidJson(CONF_COMMAND_ACTIONS_JSON)
        if not isinstance(sequence, list) or not sequence:
            raise InvalidJson(CONF_COMMAND_ACTIONS_JSON)
        try:
            cv.SCRIPT_SCHEMA(sequence)
        except vol.Invalid as err:
            raise InvalidJson(CONF_COMMAND_ACTIONS_JSON) from err
    return parsed


def _parse_event_hooks(value: str) -> list[dict[str, Any]]:
    parsed = _parse_json_value(value, CONF_EVENT_HOOKS_JSON)
    if parsed in (None, ""):
        return []

    if isinstance(parsed, dict):
        parsed = [
            {**hook, "name": name}
            if isinstance(hook, dict) and "name" not in hook
            else hook
            for name, hook in parsed.items()
        ]
    if not isinstance(parsed, list):
        raise InvalidJson(CONF_EVENT_HOOKS_JSON)

    hooks = []
    for hook in parsed:
        if not isinstance(hook, dict):
            raise InvalidJson(CONF_EVENT_HOOKS_JSON)
        next_hook = repair_legacy_template_data(_plain_options(hook))
        trigger = str(next_hook.get("trigger", "state")).strip().lower()
        if trigger not in {"state", "event"}:
            raise InvalidJson(CONF_EVENT_HOOKS_JSON)
        next_hook["trigger"] = trigger

        if trigger == "state":
            entity_ids = next_hook.get(ATTR_ENTITY_ID, next_hook.get("entity_ids"))
            if isinstance(entity_ids, str):
                entity_ids = [entity_ids]
            if not isinstance(entity_ids, list) or not entity_ids:
                raise InvalidEntityReference(CONF_EVENT_HOOKS_JSON)
            try:
                entity_ids = list(
                    dict.fromkeys(
                        cv.entity_id(str(entity_id).strip()) for entity_id in entity_ids
                    )
                )
            except vol.Invalid as err:
                raise InvalidEntityReference(CONF_EVENT_HOOKS_JSON) from err
            next_hook[ATTR_ENTITY_ID] = entity_ids
            next_hook.pop("entity_ids", None)

            attributes = next_hook.get(
                CONF_ATTRIBUTE, next_hook.get("attributes_changed")
            )
            if isinstance(attributes, str):
                next_hook[CONF_ATTRIBUTE] = [attributes]
            elif attributes is not None:
                if not isinstance(attributes, list) or any(
                    not isinstance(attribute, str) for attribute in attributes
                ):
                    raise InvalidJson(CONF_EVENT_HOOKS_JSON)
                next_hook[CONF_ATTRIBUTE] = attributes
            next_hook.pop("attributes_changed", None)
        else:
            event_type = str(next_hook.get("event_type", "")).strip()
            if not event_type:
                raise InvalidJson(CONF_EVENT_HOOKS_JSON)
            next_hook["event_type"] = event_type
            if "event_data" in next_hook and not isinstance(
                next_hook["event_data"], dict
            ):
                raise InvalidJson(CONF_EVENT_HOOKS_JSON)

        for field_name in (CONF_ATTRIBUTES, CONF_ATTRIBUTE_TEMPLATES):
            if field_name in next_hook:
                field_value = next_hook[field_name]
                if not isinstance(field_value, dict):
                    raise InvalidJson(CONF_EVENT_HOOKS_JSON)
                next_hook[field_name] = _normalize_attribute_mapping(
                    field_value,
                    CONF_EVENT_HOOKS_JSON,
                    templates=field_name == CONF_ATTRIBUTE_TEMPLATES,
                )

        if "debounce" in next_hook:
            try:
                if isinstance(next_hook["debounce"], bool):
                    raise TypeError
                debounce = float(next_hook["debounce"])
            except (TypeError, ValueError, OverflowError) as err:
                raise InvalidJson(CONF_EVENT_HOOKS_JSON) from err
            if not math.isfinite(debounce):
                raise InvalidJson(CONF_EVENT_HOOKS_JSON)
            next_hook["debounce"] = max(0, debounce)
        for boolean_field in ("enabled", "refresh"):
            if boolean_field not in next_hook:
                continue
            try:
                next_hook[boolean_field] = cv.boolean(next_hook[boolean_field])
            except vol.Invalid as err:
                raise InvalidJson(CONF_EVENT_HOOKS_JSON) from err

        hooks.append(next_hook)
    return hooks


def _platform_schema(platform: str):
    module = import_module(f".{platform}", __package__)
    return getattr(module, f"{platform.upper()}_SCHEMA", None) or module.ENTITY_SCHEMA


def _platform_validator(platform: str):
    """Load the platform schema and optional domain validator."""
    module = import_module(f".{platform}", __package__)
    schema = getattr(module, f"{platform.upper()}_SCHEMA", None) or module.ENTITY_SCHEMA
    return schema, getattr(module, "validate_domain_options", None)


def _validate_platform_entity(
    entity: dict[str, Any],
    schema=None,
    validate_domain_options=None,
) -> None:
    platform = entity[CONF_PLATFORM]
    schema_entity = dict(entity)
    schema_entity.pop(CONF_PLATFORM, None)
    try:
        if schema is None:
            schema = _platform_schema(platform)
            module = import_module(f".{platform}", __package__)
            validate_domain_options = getattr(module, "validate_domain_options", None)
        validated_entity = schema(schema_entity)
        if _contains_non_finite_number(validated_entity):
            raise ValueError("Domain options must contain only finite numbers")
        if validate_domain_options:
            validate_domain_options(validated_entity)
    except (
        AttributeError,
        ImportError,
        OverflowError,
        TypeError,
        ValueError,
        vol.Invalid,
    ) as err:
        _LOGGER.error(
            "Virtual Layer domain validation failed (platform=%s, error=%s)",
            platform,
            err,
        )
        raise InvalidDomainOptions from err


def _contains_non_finite_number(value: Any) -> bool:
    """Return whether nested user input contains NaN or infinity."""
    if isinstance(value, float):
        return not math.isfinite(value)
    if isinstance(value, Mapping):
        return any(
            _contains_non_finite_number(key) or _contains_non_finite_number(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple, set)):
        return any(_contains_non_finite_number(item) for item in value)
    return False


def _parse_source_reference(
    source, field_name: str, default_attribute: str | None = None
) -> dict[str, str]:
    if isinstance(source, str):
        source = source.strip()
        if default_attribute is not None:
            try:
                return {
                    ATTR_ENTITY_ID: cv.entity_id(source),
                    CONF_ATTRIBUTE: default_attribute,
                }
            except vol.Invalid:
                if "." not in source:
                    raise InvalidEntityReference(field_name)
        entity_id, _, attribute = source.rpartition(".")
    elif isinstance(source, dict):
        entity_id = source.get(ATTR_ENTITY_ID, "")
        attribute = source.get(CONF_ATTRIBUTE, default_attribute or "")
    else:
        raise InvalidJson(field_name)

    entity_id = str(entity_id).strip()
    attribute = str(attribute).strip()
    if not entity_id or not attribute:
        raise InvalidJson(field_name)

    try:
        entity_id = cv.entity_id(entity_id)
    except vol.Invalid as err:
        raise InvalidEntityReference(field_name) from err

    return {
        ATTR_ENTITY_ID: entity_id,
        CONF_ATTRIBUTE: attribute,
    }


def _validate_entity_references(entity: dict[str, Any]) -> None:
    """Reject explicit source references back to the virtual entity itself."""
    entity_id = entity.get(ATTR_ENTITY_ID)
    if not entity_id:
        return
    if entity_id in entity.get(CONF_SOURCE_ENTITIES, []):
        raise InvalidEntityReference(CONF_SOURCE_ENTITIES_TEXT)
    for field_name, sources in (
        (CONF_ATTRIBUTE_SOURCES_JSON, entity.get(CONF_ATTRIBUTE_SOURCES, {})),
        (CONF_TEMPLATE_SOURCES_JSON, entity.get(CONF_TEMPLATE_SOURCES, {})),
    ):
        if any(source.get(ATTR_ENTITY_ID) == entity_id for source in sources.values()):
            raise InvalidEntityReference(field_name)
    for hook in entity.get(CONF_EVENT_HOOKS, []):
        if not isinstance(hook, Mapping) or hook.get("trigger") != "state":
            continue
        entity_ids = hook.get(ATTR_ENTITY_ID, [])
        if isinstance(entity_ids, str):
            entity_ids = [entity_ids]
        if entity_id in entity_ids:
            raise InvalidEntityReference(CONF_EVENT_HOOKS_JSON)


def _virtual_entity_id(entity: Mapping) -> str | None:
    """Return the configured or deterministic entity id for a UI entity."""
    platform = entity.get(CONF_PLATFORM)
    if platform not in VIRTUAL_ENTITY_DOMAINS:
        return None

    entity_id = entity.get(ATTR_ENTITY_ID)
    if isinstance(entity_id, str) and entity_id:
        try:
            entity_id = cv.entity_id(entity_id)
        except vol.Invalid:
            return None
        return entity_id if entity_id.startswith(f"{platform}.") else None

    name = entity.get(CONF_NAME)
    if not isinstance(name, str) or not name:
        return None
    return _default_virtual_entity_id(platform, name)


def _entity_dependency_sources(entity: Mapping) -> dict[str, str]:
    """Return explicit source entities and the field that configured each one."""
    sources = {}
    source_entities = entity.get(CONF_SOURCE_ENTITIES, [])
    if isinstance(source_entities, (list, tuple, set)):
        for entity_id in source_entities:
            if isinstance(entity_id, str):
                sources[entity_id] = CONF_SOURCE_ENTITIES_TEXT

    for field_name, source_group in (
        (CONF_ATTRIBUTE_SOURCES_JSON, entity.get(CONF_ATTRIBUTE_SOURCES, {})),
        (CONF_TEMPLATE_SOURCES_JSON, entity.get(CONF_TEMPLATE_SOURCES, {})),
    ):
        if not isinstance(source_group, Mapping):
            continue
        for source in source_group.values():
            if isinstance(source, Mapping) and isinstance(
                source.get(ATTR_ENTITY_ID), str
            ):
                sources[source[ATTR_ENTITY_ID]] = field_name

    camera_source = entity.get(CAMERA_SOURCE_ENTITY_OPTION)
    if isinstance(camera_source, str):
        sources[camera_source] = CONF_DOMAIN_OPTIONS_JSON
    for hook in entity.get(CONF_EVENT_HOOKS, []):
        if not isinstance(hook, Mapping) or hook.get("trigger") != "state":
            continue
        entity_ids = hook.get(ATTR_ENTITY_ID, [])
        if isinstance(entity_ids, str):
            entity_ids = [entity_ids]
        for entity_id in entity_ids:
            if isinstance(entity_id, str):
                sources[entity_id] = CONF_EVENT_HOOKS_JSON
    return sources


def _iter_option_entities(options: Mapping):
    """Yield well-formed persisted entities without trusting stored payloads."""
    devices = options.get(ATTR_DEVICES, {})
    if not isinstance(devices, Mapping):
        return
    for entities in devices.values():
        if not isinstance(entities, list):
            continue
        for entity in entities:
            if isinstance(entity, Mapping):
                yield entity


def _dependency_graph(
    hass, ignored_entity_id: str | None = None
) -> dict[str, set[str]]:
    """Build the explicit Virtual Layer entity dependency graph."""
    graph = {}
    for entry in hass.config_entries.async_entries(COMPONENT_DOMAIN):
        for entity in _iter_option_entities(entry.options):
            entity_id = _virtual_entity_id(entity)
            if entity_id is None or entity_id == ignored_entity_id:
                continue
            graph[entity_id] = set(_entity_dependency_sources(entity))
    return graph


def _has_dependency_path(graph: dict[str, set[str]], start: str, target: str) -> bool:
    """Return whether ``start`` reaches ``target`` through explicit sources."""
    pending = [start]
    visited = set()
    while pending:
        entity_id = pending.pop()
        if entity_id == target:
            return True
        if entity_id in visited:
            continue
        visited.add(entity_id)
        pending.extend(graph.get(entity_id, ()))
    return False


def _validate_virtual_dependency_cycle(
    hass,
    entity: Mapping,
    replacing_entity_id: str | None = None,
) -> None:
    """Reject UI configurations that introduce a direct dependency cycle."""
    entity_id = _virtual_entity_id(entity)
    if entity_id is None:
        return

    graph = _dependency_graph(hass, replacing_entity_id)
    graph.pop(entity_id, None)
    sources = _entity_dependency_sources(entity)
    graph[entity_id] = set(sources)
    for source_entity_id, field_name in sources.items():
        if _has_dependency_path(graph, source_entity_id, entity_id):
            raise InvalidEntityReference(field_name)


def _validate_virtual_entity_id_available(
    hass,
    entity: Mapping,
    replacing_entity_id: str | None = None,
) -> None:
    """Reject IDs that Home Assistant cannot assign to this virtual entity."""
    entity_id = _virtual_entity_id(entity)
    if entity_id is None or entity_id == replacing_entity_id:
        return

    for entry in hass.config_entries.async_entries(COMPONENT_DOMAIN):
        for configured_entity in _iter_option_entities(entry.options):
            if _virtual_entity_id(configured_entity) == entity_id:
                raise EntityIdAlreadyUsed

    if er.async_get(hass).async_get(entity_id) is not None:
        raise EntityIdAlreadyUsed
    if hass.states.get(entity_id) is not None:
        raise EntityIdAlreadyUsed


def _build_entity_config(
    user_input: dict[str, Any],
    schema=None,
    validate_domain_options=None,
) -> tuple[str, dict[str, Any]]:
    user_input = _flatten_entity_form_sections(user_input)
    device_name = _text_default(user_input.get(CONF_DEVICE_NAME)).strip()
    entity_name = _text_default(user_input.get(CONF_ENTITY_NAME)).strip()
    platform = user_input.get(CONF_PLATFORM)

    if not device_name:
        raise MissingDeviceName
    if not entity_name:
        raise MissingEntityName

    initial_value = user_input.get(CONF_INITIAL_VALUE, "")
    if platform in DEFAULT_INITIAL_VALUES and initial_value == DEFAULT_ENTITY_VALUE:
        initial_value = DEFAULT_INITIAL_VALUES[platform]
    if platform == "climate" and (
        not isinstance(initial_value, str)
        or initial_value.lower() not in CLIMATE_INITIAL_VALUES
    ):
        raise InvalidDomainOptions

    entity = {
        CONF_PLATFORM: platform,
        CONF_NAME: entity_name,
        CONF_INITIAL_VALUE: initial_value,
        CONF_INITIAL_AVAILABILITY: user_input[CONF_INITIAL_AVAILABILITY],
        CONF_PERSISTENT: user_input[CONF_PERSISTENT],
    }

    icon = _text_default(user_input.get(CONF_ICON)).strip()
    if icon:
        entity[CONF_ICON] = icon

    icon_template = repair_legacy_enum_template(
        _text_default(user_input.get(CONF_ICON_TEMPLATE)).strip()
    )
    if icon_template:
        entity[CONF_ICON_TEMPLATE] = icon_template

    entity_id = _text_default(user_input.get(ATTR_ENTITY_ID)).strip()
    if entity_id:
        try:
            entity_id = cv.entity_id(entity_id)
        except vol.Invalid as err:
            raise InvalidEntityId from err
        if not entity_id.startswith(f"{platform}."):
            raise InvalidEntityId
        entity[ATTR_ENTITY_ID] = entity_id

    source_entities = _parse_source_entities(
        user_input.get(CONF_SOURCE_ENTITIES_TEXT, "")
    )
    if source_entities:
        entity[CONF_SOURCE_ENTITIES] = source_entities

    template_sources = _parse_template_sources(
        _text_default(user_input.get(CONF_TEMPLATE_SOURCES_JSON)),
    )
    if template_sources:
        entity[CONF_TEMPLATE_SOURCES] = template_sources

    try:
        pull_interval = nonnegative_int(user_input.get(CONF_PULL_INTERVAL, 0) or 0)
    except vol.Invalid as err:
        raise InvalidDomainOptions from err
    if pull_interval:
        entity[CONF_PULL_INTERVAL] = pull_interval

    value_template = repair_legacy_enum_template(
        _text_default(user_input.get(CONF_VALUE_TEMPLATE)).strip()
    )
    if value_template:
        entity[CONF_VALUE_TEMPLATE] = value_template

    availability_template = repair_legacy_enum_template(
        _text_default(user_input.get(CONF_AVAILABILITY_TEMPLATE)).strip()
    )
    if availability_template:
        entity[CONF_AVAILABILITY_TEMPLATE] = availability_template

    event_hooks = _parse_event_hooks(
        _text_default(user_input.get(CONF_EVENT_HOOKS_JSON))
    )
    if event_hooks:
        entity[CONF_EVENT_HOOKS] = event_hooks

    attributes = _parse_json_object(
        _text_default(user_input.get(CONF_ATTRIBUTES_JSON)), CONF_ATTRIBUTES_JSON
    )
    attributes = _normalize_attribute_mapping(attributes, CONF_ATTRIBUTES_JSON)
    attributes = _without_transient_source_attributes(attributes)
    if attributes:
        entity[CONF_ATTRIBUTES] = attributes

    attribute_sources = _parse_attribute_sources(
        _text_default(user_input.get(CONF_ATTRIBUTE_SOURCES_JSON)),
    )
    attribute_sources = _without_transient_source_attributes(attribute_sources)
    if attribute_sources:
        entity[CONF_ATTRIBUTE_SOURCES] = attribute_sources

    attribute_templates = _parse_json_object(
        _text_default(user_input.get(CONF_ATTRIBUTE_TEMPLATES_JSON)),
        CONF_ATTRIBUTE_TEMPLATES_JSON,
    )
    attribute_templates = _normalize_attribute_mapping(
        attribute_templates,
        CONF_ATTRIBUTE_TEMPLATES_JSON,
        templates=True,
    )
    attribute_templates = _without_transient_source_attributes(
        attribute_templates
    )
    if attribute_templates:
        entity[CONF_ATTRIBUTE_TEMPLATES] = attribute_templates

    native_templates = _parse_native_templates(
        _text_default(user_input.get(CONF_NATIVE_TEMPLATES_JSON)),
    )
    native_value_templates = user_input.get(CONF_NATIVE_VALUE_TEMPLATES, {})
    if not isinstance(native_value_templates, Mapping):
        raise InvalidJson(CONF_NATIVE_TEMPLATES_JSON)
    for property_name in DOMAIN_NATIVE_TEMPLATE_PROPERTIES.get(platform, ()):
        native_templates.pop(property_name, None)
        template_value = native_value_templates.get(property_name)
        if template_value is None:
            continue
        if not isinstance(template_value, str):
            raise InvalidJson(CONF_NATIVE_TEMPLATES_JSON)
        template_value = repair_legacy_enum_template(template_value).strip()
        if template_value:
            native_templates[property_name] = template_value
    if native_templates:
        entity[CONF_NATIVE_TEMPLATES] = native_templates

    command_actions = _parse_command_actions(
        _text_default(user_input.get(CONF_COMMAND_ACTIONS_JSON)),
        platform,
    )
    if command_actions:
        entity[CONF_COMMAND_ACTIONS] = command_actions

    domain_options = _parse_domain_options(
        _text_default(user_input.get(CONF_DOMAIN_OPTIONS_JSON)),
    )
    if platform == "light":
        domain_options.pop(CONF_MATTER_LIGHT_TYPE, None)
        matter_light_type = user_input.get(CONF_MATTER_LIGHT_TYPE, "dimmable")
        if matter_light_type not in MATTER_LIGHT_TYPES:
            raise InvalidDomainOptions
        domain_options[CONF_MATTER_LIGHT_TYPE] = matter_light_type
    elif platform == "climate":
        for field_name in CLIMATE_MODE_LIST_FIELDS:
            if field_name in user_input:
                if not isinstance(user_input[field_name], list):
                    raise InvalidDomainOptions
                domain_options.pop(field_name, None)
                domain_options[field_name] = list(user_input[field_name])
        for field_name in CLIMATE_CURRENT_MODE_FIELDS:
            if field_name in user_input:
                domain_options.pop(field_name, None)
                value = str(user_input.get(field_name, "") or "").strip()
                if value:
                    domain_options[field_name] = value
        for field_name in CLIMATE_SCALAR_FORM_FIELDS:
            if field_name not in user_input:
                continue
            domain_options.pop(field_name, None)
            value = user_input[field_name]
            if field_name in {"hvac_action", "temperature_unit"}:
                value = str(value or "").strip()
                if value:
                    domain_options[field_name] = value
            elif value is not None:
                domain_options[field_name] = value
    elif platform == "fan":
        for field_name in FAN_FORM_FIELDS:
            if field_name not in user_input:
                continue
            domain_options.pop(field_name, None)
            value = user_input[field_name]
            if field_name == FAN_MODE_LIST_FIELD:
                if not isinstance(value, list):
                    raise InvalidDomainOptions
                domain_options[field_name] = list(value)
            elif field_name in {"preset_mode", "current_direction"}:
                value = str(value or "").strip()
                if value:
                    domain_options[field_name] = value
            elif field_name == "percentage" and value is None:
                continue
            else:
                domain_options[field_name] = value
    elif platform == "humidifier":
        for field_name in HUMIDIFIER_FORM_FIELDS:
            if field_name not in user_input:
                continue
            domain_options.pop(field_name, None)
            value = user_input[field_name]
            if field_name == HUMIDIFIER_MODE_LIST_FIELD:
                if not isinstance(value, list):
                    raise InvalidDomainOptions
                domain_options[field_name] = list(value)
            elif field_name in {
                "class",
                "action",
                HUMIDIFIER_CURRENT_MODE_FIELD,
            }:
                value = str(value or "").strip()
                if value:
                    domain_options[field_name] = value
            elif value is not None:
                domain_options[field_name] = value
    entity.update(domain_options)

    polygon_geojson_text = _text_default(
        user_input.get(CONF_POLYGON_GEOJSON_JSON)
    ).strip()
    polygon_files = [
        item.strip()
        for item in _multiline_list_default(
            user_input.get(CONF_POLYGON_FILES_TEXT)
        ).splitlines()
        if item.strip()
    ]
    polygon_person = _text_default(user_input.get(CONF_POLYGON_PERSON)).strip()
    if polygon_person:
        try:
            polygon_person = cv.entity_id(polygon_person)
        except vol.Invalid as err:
            raise InvalidEntityReference(CONF_POLYGON_PERSON) from err
        if not polygon_person.startswith("person."):
            raise InvalidEntityReference(CONF_POLYGON_PERSON)
    polygon_rules_text = _text_default(
        user_input.get(CONF_POLYGON_TRACKER_RULES_JSON)
    ).strip()
    if any((polygon_geojson_text, polygon_files, polygon_person, polygon_rules_text)):
        if platform != "device_tracker":
            raise InvalidDomainOptions
        try:
            polygon_distance = positive_tick(
                user_input.get(CONF_POLYGON_DISTANCE_INPUT, 300),
            )
            if polygon_distance < 1:
                raise vol.Invalid("polygon distance must be at least 1 meter")
        except vol.Invalid as err:
            raise InvalidDomainOptions from err
        polygon = {
            CONF_POLYGON_FILES: polygon_files,
            CONF_POLYGON_STRATEGY: user_input.get(
                CONF_POLYGON_STRATEGY_INPUT,
                "majority",
            ),
            CONF_POLYGON_DISTANCE_METERS: polygon_distance,
            CONF_POLYGON_AWAY_STATE: str(
                user_input.get(
                    CONF_POLYGON_AWAY_STATE_INPUT,
                    "not_home",
                )
            ).strip(),
            CONF_POLYGON_TRACKER_RULES: repair_legacy_template_data(
                _parse_json_object(
                    polygon_rules_text,
                    CONF_POLYGON_TRACKER_RULES_JSON,
                )
            ),
        }
        if polygon_geojson_text:
            polygon[CONF_POLYGON_GEOJSON] = _parse_json_object(
                polygon_geojson_text,
                CONF_POLYGON_GEOJSON_JSON,
            )
            try:
                parse_geojson_zones(polygon[CONF_POLYGON_GEOJSON])
            except (TypeError, ValueError) as err:
                raise InvalidJson(CONF_POLYGON_GEOJSON_JSON) from err
        if polygon_person:
            polygon[CONF_POLYGON_PERSON_ENTITY] = polygon_person
        if not source_entities and not polygon_person:
            raise InvalidEntityReference(CONF_SOURCE_ENTITIES_TEXT)
        if any(
            not source_entity_id.startswith("device_tracker.")
            for source_entity_id in source_entities
        ):
            raise InvalidEntityReference(CONF_SOURCE_ENTITIES_TEXT)
        if set(polygon[CONF_POLYGON_TRACKER_RULES]) - set(source_entities):
            raise InvalidEntityReference(CONF_POLYGON_TRACKER_RULES_JSON)
        entity[CONF_POLYGONAL_ZONE] = polygon
    _validate_entity_references(entity)

    # Number entities require a native range. Keep a practical default for the
    # UI flow; richer domain options can be added later.
    if platform == "number":
        entity.setdefault(CONF_MIN, DEFAULT_NUMBER_MIN)
        entity.setdefault(CONF_MAX, DEFAULT_NUMBER_MAX)

    _validate_platform_entity(entity, schema, validate_domain_options)

    # A camera alias remains a normal virtual entity, but follows the source
    # camera state and subscribes to it without requiring a handwritten Jinja
    # template in the UI.
    if platform == "camera" and (
        source_entity := entity.get(CAMERA_SOURCE_ENTITY_OPTION)
    ):
        if source_entity == entity.get(ATTR_ENTITY_ID):
            raise InvalidEntityReference(CONF_DOMAIN_OPTIONS_JSON)
        source_entities = list(entity.get(CONF_SOURCE_ENTITIES, []))
        if source_entity not in source_entities:
            source_entities.append(source_entity)
        entity[CONF_SOURCE_ENTITIES] = source_entities
        entity.setdefault(
            CONF_VALUE_TEMPLATE,
            f"{{{{ states('{source_entity}') }}}}",
        )

    return device_name, entity


def _domain_options_error_field(user_input: Mapping) -> str:
    """Return a visible error location for the selected domain's inputs."""
    if user_input.get(CONF_PLATFORM) in {"climate", "fan", "humidifier", "light"}:
        return "base"
    return CONF_DOMAIN_OPTIONS_JSON


async def _async_build_entity_config(
    hass,
    user_input: dict[str, Any],
    replacing_entity_id: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Build UI entity configuration without importing platform code on the loop."""
    platform = user_input[CONF_PLATFORM]
    try:
        schema, validate_domain_options = await hass.async_add_executor_job(
            _platform_validator,
            platform,
        )
    except (AttributeError, ImportError, TypeError, ValueError) as err:
        raise InvalidDomainOptions from err
    device_name, entity = _build_entity_config(
        user_input,
        schema,
        validate_domain_options,
    )
    _validate_entity_templates(hass, entity)
    _validate_virtual_dependency_cycle(hass, entity, replacing_entity_id)
    _validate_virtual_entity_id_available(hass, entity, replacing_entity_id)
    return device_name, entity


def _validate_entity_templates(hass, entity: Mapping) -> None:
    """Reject invalid Jinja syntax while the user can still edit the form."""
    platform = str(entity.get(CONF_PLATFORM, "unknown"))
    configured_entity_id = str(entity.get(ATTR_ENTITY_ID, "not_configured"))

    def _validate(
        value,
        field_name: str,
        template_name: str | None = None,
    ) -> None:
        if not isinstance(value, str) or not value.strip():
            return
        try:
            Template(value, hass).ensure_valid()
        except TemplateError as err:
            template_name = template_name or field_name
            _LOGGER.error(
                "Invalid Virtual Layer Jinja template "
                "(platform=%s, entity_id=%s, field=%s, template=%s): %s",
                platform,
                configured_entity_id,
                field_name,
                template_name,
                err,
            )
            raise InvalidTemplate(field_name, template_name, str(err)) from err

    for field_name in (
        CONF_VALUE_TEMPLATE,
        CONF_AVAILABILITY_TEMPLATE,
        CONF_ICON_TEMPLATE,
    ):
        _validate(entity.get(field_name), field_name)

    for attribute_name, template in _mapping_or_empty(
        entity.get(CONF_ATTRIBUTE_TEMPLATES)
    ).items():
        _validate(template, CONF_ATTRIBUTE_TEMPLATES_JSON, attribute_name)

    managed_properties = set(DOMAIN_NATIVE_TEMPLATE_PROPERTIES.get(platform, ()))
    for property_name, template in _mapping_or_empty(
        entity.get(CONF_NATIVE_TEMPLATES)
    ).items():
        _validate(
            template,
            property_name
            if property_name in managed_properties
            else CONF_NATIVE_TEMPLATES_JSON,
            property_name,
        )

    for hook_index, hook in enumerate(entity.get(CONF_EVENT_HOOKS, []), start=1):
        if not isinstance(hook, Mapping):
            continue
        for field_name in (CONF_VALUE_TEMPLATE, CONF_AVAILABILITY_TEMPLATE):
            _validate(
                hook.get(field_name),
                CONF_EVENT_HOOKS_JSON,
                f"hook_{hook_index}.{field_name}",
            )
        for attribute_name, template in _mapping_or_empty(
            hook.get(CONF_ATTRIBUTE_TEMPLATES)
        ).items():
            _validate(
                template,
                CONF_EVENT_HOOKS_JSON,
                f"hook_{hook_index}.{attribute_name}",
            )

    polygon = entity.get(CONF_POLYGONAL_ZONE)
    if isinstance(polygon, Mapping):
        rules = polygon.get(CONF_POLYGON_TRACKER_RULES, {})
        if isinstance(rules, Mapping):
            for rule_name, rule in rules.items():
                if isinstance(rule, Mapping):
                    _validate(
                        rule.get("condition_template"),
                        CONF_POLYGON_TRACKER_RULES_JSON,
                        f"{rule_name}.condition_template",
                    )


def _make_entity_key() -> str:
    return make_entity_key()


def _ensure_entity_key(
    entity: dict[str, Any], fallback: str | None = None
) -> dict[str, Any]:
    entity = _plain_options(entity)
    entity.setdefault(ATTR_ENTITY_KEY, fallback or _make_entity_key())
    return entity


def _build_device_config(
    user_input: dict[str, Any], device_name: str
) -> dict[str, Any]:
    """Build Home Assistant device metadata from the UI form."""
    device_id = _text_default(user_input.get(CONF_DEVICE_ID)).strip() or str(
        uuid.uuid4()
    )

    device = {
        ATTR_DEVICE_ID: device_id,
        CONF_NAME: _make_device_name(device_name),
    }
    optional_fields = {
        CONF_DEVICE_MANUFACTURER: CONF_MANUFACTURER,
        CONF_DEVICE_MODEL: CONF_MODEL,
        CONF_DEVICE_SW_VERSION: CONF_SW_VERSION,
        CONF_DEVICE_HW_VERSION: CONF_HW_VERSION,
        CONF_DEVICE_SERIAL_NUMBER: CONF_SERIAL_NUMBER,
        CONF_DEVICE_CONFIGURATION_URL: CONF_CONFIGURATION_URL,
        CONF_DEVICE_SUGGESTED_AREA: CONF_SUGGESTED_AREA,
        CONF_DEVICE_VIA_DEVICE_ID: CONF_VIA_DEVICE_ID,
    }
    for form_field, config_field in optional_fields.items():
        value = _text_default(user_input.get(form_field)).strip()
        if value:
            device[config_field] = value
    return device


def _make_device_name(device_name: str) -> str:
    return device_name.removeprefix("+")


def _plain_options(value, _seen=None, _depth=0):
    """Convert read-only options without following damaged recursive values."""
    if _depth > 100:
        return None
    if _seen is None:
        _seen = set()
    if isinstance(value, Enum):
        return _plain_options(value.value, _seen, _depth + 1)
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in _seen:
            return None
        _seen.add(identity)
        try:
            return {
                key: _plain_options(item, _seen, _depth + 1)
                for key, item in value.items()
            }
        finally:
            _seen.remove(identity)
    if isinstance(value, (list, tuple)):
        identity = id(value)
        if identity in _seen:
            return None
        _seen.add(identity)
        try:
            items = [_plain_options(item, _seen, _depth + 1) for item in value]
            return tuple(items) if isinstance(value, tuple) else items
        finally:
            _seen.remove(identity)
    try:
        return copy.deepcopy(value)
    except Exception:  # noqa: BLE001 - damaged legacy values must remain removable
        return value


def _text_default(value: Any, default: str = "") -> str:
    """Return a form-safe text value for legacy or partially corrupt options."""
    if value is None:
        return default
    if isinstance(value, str):
        return value
    try:
        return str(value)
    except (RecursionError, TypeError, ValueError, OverflowError):
        return default


def _multiline_list_default(value: Any) -> str:
    """Return stored string/list values as editable multiline text."""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return "\n".join(item for item in value if isinstance(item, str))
    return ""


def _stored_entity_ids(value: Any) -> list[str]:
    """Return valid entity IDs from old list, tuple, or text storage shapes."""
    if isinstance(value, str):
        values = value.replace(",", "\n").splitlines()
    elif isinstance(value, (list, tuple)):
        values = value
    else:
        return []

    entity_ids = []
    for entity_id in values:
        if not isinstance(entity_id, str):
            continue
        try:
            if normalized_entity_id := entity_id.strip():
                entity_ids.append(cv.entity_id(normalized_entity_id))
        except vol.Invalid:
            continue
    return list(dict.fromkeys(entity_ids))


def _boolean_default(value: Any, default: bool) -> bool:
    try:
        return cv.boolean(value)
    except vol.Invalid:
        return default


def _nonnegative_int_default(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        value = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(0, value)


def _positive_float_default(value: Any, default: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return value if math.isfinite(value) and value >= 1 else default


def _mapping_or_empty(value) -> dict[str, Any]:
    return _plain_options(value) if isinstance(value, Mapping) else {}


def _options_devices(options: dict[str, Any] | None) -> dict[str, Any]:
    return _mapping_or_empty(_plain_options(options or {}).get(ATTR_DEVICES, {}))


def _options_device_attributes(options: dict[str, Any] | None) -> dict[str, Any]:
    return _mapping_or_empty(
        _plain_options(options or {}).get(ATTR_DEVICE_ATTRIBUTES, {}),
    )


def _entity_list_or_empty(entities) -> list:
    return list(entities) if isinstance(entities, list) else []


def _set_device_attributes(
    options: dict[str, Any],
    device_key: str,
    device_config: dict[str, Any] | None,
) -> None:
    if device_config is None:
        return
    if not isinstance(options.get(ATTR_DEVICE_ATTRIBUTES), Mapping):
        options[ATTR_DEVICE_ATTRIBUTES] = {}
    device_attributes = options.setdefault(ATTR_DEVICE_ATTRIBUTES, {})
    device_attributes[device_key] = _plain_options(device_config)


def _get_device_attributes(options: dict[str, Any], device_key: str) -> dict[str, Any]:
    device_attributes = _options_device_attributes(options)
    return _mapping_or_empty(device_attributes.get(device_key))


def _device_display_name(device_key: str, device: Mapping[str, Any]) -> str:
    """Return the editable display name without using it as Device identity."""
    return _text_default(device.get(CONF_NAME), _make_device_name(device_key))


def _existing_device_options(hass, options: dict[str, Any]) -> list[dict[str, str]]:
    """Return selectable virtual Devices, including the explicit new-Device choice."""
    device_options = [
        {
            "value": NEW_DEVICE_TARGET,
            "label": (
                "새 장치 만들기"
                if hass.config.language.lower().startswith("ko")
                else "Create a new Device"
            ),
        }
    ]
    for device_key in _options_devices(options):
        device = _get_device_attributes(options, device_key)
        device_id = device.get(ATTR_DEVICE_ID, device_key)
        device_name = _device_display_name(device_key, device)
        device_options.append(
            {
                "value": device_key,
                "label": f"{device_name} ({device_id})",
            }
        )
    return device_options


def _managed_device_choices(options: dict[str, Any]) -> dict[str, str]:
    """Return Devices with entity counts for the standalone management screen."""
    choices = {}
    for device_key, entities in _options_devices(options).items():
        device = _get_device_attributes(options, device_key)
        device_id = device.get(ATTR_DEVICE_ID, device_key)
        device_name = _device_display_name(device_key, device)
        entity_count = len(_entity_list_or_empty(entities))
        choices[device_key] = f"{device_name} ({device_id}, {entity_count} entities)"
    return choices


def _select_device_schema(options: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_MANAGED_DEVICE_NAME): vol.In(
                _managed_device_choices(options)
            ),
        }
    )


def _device_form_defaults(
    options: dict[str, Any],
    device_name: str,
) -> dict[str, Any]:
    """Return just the metadata fields that belong to a logical Device."""
    return _with_existing_device_defaults({}, options, device_name)


def _with_existing_device_defaults(
    defaults: dict[str, Any],
    options: dict[str, Any],
    device_name: str | None,
) -> dict[str, Any]:
    """Overlay an existing Device's stable identity onto entity-form defaults."""
    if not device_name or device_name == NEW_DEVICE_TARGET:
        return defaults
    if device_name not in _options_devices(options):
        return defaults
    device = _get_device_attributes(options, device_name)

    updated_defaults = dict(defaults)
    updated_defaults[CONF_DEVICE_NAME] = _device_display_name(device_name, device)
    updated_defaults[CONF_DEVICE_ID] = _text_default(
        device.get(ATTR_DEVICE_ID),
        device_name,
    )
    for config_field, form_field in (
        (CONF_MANUFACTURER, CONF_DEVICE_MANUFACTURER),
        (CONF_MODEL, CONF_DEVICE_MODEL),
        (CONF_SW_VERSION, CONF_DEVICE_SW_VERSION),
        (CONF_HW_VERSION, CONF_DEVICE_HW_VERSION),
        (CONF_SERIAL_NUMBER, CONF_DEVICE_SERIAL_NUMBER),
        (CONF_CONFIGURATION_URL, CONF_DEVICE_CONFIGURATION_URL),
        (CONF_SUGGESTED_AREA, CONF_DEVICE_SUGGESTED_AREA),
        (CONF_VIA_DEVICE_ID, CONF_DEVICE_VIA_DEVICE_ID),
    ):
        updated_defaults[form_field] = _text_default(device.get(config_field))
    return updated_defaults


def _existing_device_key_for_id(
    options: dict[str, Any],
    device_config: dict[str, Any] | None,
) -> str | None:
    """Return the persisted key for an existing stable Device ID."""
    if not device_config:
        return None
    device_id = device_config.get(ATTR_DEVICE_ID)
    if not isinstance(device_id, str) or not device_id:
        return None
    for existing_device_name in _options_devices(options):
        existing_device = _get_device_attributes(options, existing_device_name)
        if existing_device.get(ATTR_DEVICE_ID, existing_device_name) == device_id:
            return existing_device_name
    return None


def _new_device_key(
    options: dict[str, Any],
    device_name: str,
    device_config: dict[str, Any] | None,
    allowed_key: str | None = None,
) -> str:
    """Use stable Device ID as the persisted key for newly written Devices."""
    if device_config:
        device_id = device_config.get(ATTR_DEVICE_ID)
        if isinstance(device_id, str) and device_id:
            devices = _options_devices(options)
            if device_id == allowed_key or device_id not in devices:
                return device_id
            # A malformed legacy group can already occupy the literal ID key
            # while declaring a different ID in its metadata. Keep both
            # recoverable without making the display name an identity fallback.
            base_key = json.dumps([ATTR_DEVICE_ID, device_id], separators=(",", ":"))
            candidate = base_key
            suffix = 2
            while candidate in devices:
                candidate = f"{base_key}:{suffix}"
                suffix += 1
            return candidate
    return device_name


def _append_ui_entity(
    options: dict[str, Any],
    device_name: str,
    entity: dict[str, Any],
    device_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    next_options = _plain_options(options or {})
    existing_device_key = _existing_device_key_for_id(
        next_options,
        device_config,
    )
    reusing_existing_device = existing_device_key is not None
    device_key = existing_device_key or _new_device_key(
        next_options, device_name, device_config
    )
    if not isinstance(next_options.get(ATTR_DEVICES), Mapping):
        next_options[ATTR_DEVICES] = {}
    devices = next_options.setdefault(ATTR_DEVICES, {})
    if not isinstance(devices.get(device_key), list):
        devices[device_key] = []
    devices[device_key].append(_ensure_entity_key(entity))
    if not reusing_existing_device:
        _set_device_attributes(next_options, device_key, device_config)
    return next_options


def _replace_ui_entity(
    options: dict[str, Any],
    old_device_name: str,
    old_index: int,
    new_device_name: str,
    entity: dict[str, Any],
    device_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    next_options = _plain_options(options or {})
    existing_device_key = _existing_device_key_for_id(
        next_options,
        device_config,
    )
    reusing_existing_device = existing_device_key is not None
    new_device_key = existing_device_key or _new_device_key(
        next_options, new_device_name, device_config, old_device_name
    )
    if not isinstance(next_options.get(ATTR_DEVICES), Mapping):
        next_options[ATTR_DEVICES] = {}
    devices = next_options.setdefault(ATTR_DEVICES, {})
    old_entities = _entity_list_or_empty(devices.get(old_device_name))
    if old_index < 0 or old_index >= len(old_entities):
        raise InvalidEntitySelection

    if old_device_name == new_device_key:
        old_entity = old_entities[old_index]
        if not isinstance(old_entity, Mapping):
            raise InvalidEntitySelection
        old_entity_key = old_entity.get(ATTR_ENTITY_KEY)
        old_entities[old_index] = _ensure_entity_key(entity, old_entity_key)
        devices[old_device_name] = old_entities
        if not reusing_existing_device:
            _set_device_attributes(next_options, new_device_key, device_config)
        return next_options

    old_entity = old_entities.pop(old_index)
    if not isinstance(old_entity, Mapping):
        raise InvalidEntitySelection
    if old_entities:
        devices[old_device_name] = old_entities
    else:
        devices.pop(old_device_name, None)
        device_attributes = _options_device_attributes(next_options)
        device_attributes.pop(old_device_name, None)
        next_options[ATTR_DEVICE_ATTRIBUTES] = device_attributes
    if not isinstance(devices.get(new_device_key), list):
        devices[new_device_key] = []
    devices[new_device_key].append(
        _ensure_entity_key(entity, old_entity.get(ATTR_ENTITY_KEY))
    )
    if not reusing_existing_device:
        _set_device_attributes(next_options, new_device_key, device_config)
    return next_options


def _replace_ui_device(
    options: dict[str, Any],
    old_device_name: str,
    new_device_name: str,
    device_config: dict[str, Any],
) -> dict[str, Any]:
    """Update one Device's metadata and safely merge its entity group if needed."""
    next_options = _plain_options(options or {})
    if not isinstance(next_options.get(ATTR_DEVICES), Mapping):
        next_options[ATTR_DEVICES] = {}
    devices = next_options.setdefault(ATTR_DEVICES, {})
    old_entities = _entity_list_or_empty(devices.get(old_device_name))
    if old_device_name not in devices:
        raise InvalidEntitySelection

    new_device_name = _make_device_name(new_device_name).strip()
    if not new_device_name:
        raise MissingDeviceName
    # A matching stable ID represents the same physical Device even when the
    # requested display name is new. Do not overwrite its existing metadata.
    target_device_name = _new_device_key(
        next_options, new_device_name, device_config, old_device_name
    )
    new_device_id = device_config.get(ATTR_DEVICE_ID)
    if isinstance(new_device_id, str) and new_device_id:
        for existing_name in devices:
            if existing_name == old_device_name:
                continue
            existing = _get_device_attributes(next_options, existing_name)
            if existing.get(ATTR_DEVICE_ID, existing_name) == new_device_id:
                target_device_name = existing_name
                break

    device_attributes = _options_device_attributes(next_options)
    if target_device_name == old_device_name:
        devices[old_device_name] = old_entities
        _set_device_attributes(next_options, old_device_name, device_config)
        return next_options

    target_exists = target_device_name in devices
    target_entities = _entity_list_or_empty(devices.get(target_device_name))
    devices[target_device_name] = target_entities + old_entities
    devices.pop(old_device_name, None)
    device_attributes.pop(old_device_name, None)
    next_options[ATTR_DEVICE_ATTRIBUTES] = device_attributes
    if not target_exists:
        _set_device_attributes(next_options, target_device_name, device_config)
    return next_options


def _entity_key(device_name: str, index: int) -> str:
    return json.dumps([device_name, index], separators=(",", ":"))


def _entity_key_from_stable_key(entity_key: str) -> str:
    return json.dumps(["key", entity_key], separators=(",", ":"))


def _selection_key_for_entity(
    device_name: str,
    index: int,
    entity: Mapping,
    *,
    stable_key_is_unique: bool = True,
) -> str:
    entity_key = entity.get(ATTR_ENTITY_KEY)
    if (
        stable_key_is_unique
        and isinstance(entity_key, str)
        and entity_key
    ):
        return _entity_key_from_stable_key(entity_key)
    return _entity_key(device_name, index)


def _parse_entity_key(value: str) -> tuple[str, int]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError) as err:
        raise InvalidEntitySelection from err
    if (
        not isinstance(parsed, list)
        or len(parsed) != 2
        or not isinstance(parsed[0], str)
        or not isinstance(parsed[1], int)
        or isinstance(parsed[1], bool)
    ):
        raise InvalidEntitySelection
    return parsed[0], parsed[1]


def _find_entity_by_selection_key(
    options: dict[str, Any], value: str
) -> tuple[str, int]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        parsed = None

    if (
        isinstance(parsed, list)
        and len(parsed) == 2
        and parsed[0] == "key"
        and isinstance(parsed[1], str)
    ):
        wanted_key = parsed[1]
        devices = _options_devices(options)
        matches = []
        for device_name, entities in devices.items():
            for index, entity in enumerate(_entity_list_or_empty(entities)):
                if (
                    isinstance(entity, Mapping)
                    and entity.get(ATTR_ENTITY_KEY) == wanted_key
                ):
                    matches.append((device_name, index))
        if len(matches) == 1:
            return matches[0]
        raise InvalidEntitySelection

    return _parse_entity_key(value)


def _entity_choices(
    options: dict[str, Any],
    *,
    include_invalid: bool = False,
) -> dict[str, str]:
    devices = _options_devices(options)
    stable_key_counts: dict[str, int] = {}
    for entities in devices.values():
        for entity in _entity_list_or_empty(entities):
            if not isinstance(entity, Mapping):
                continue
            entity_key = entity.get(ATTR_ENTITY_KEY)
            if isinstance(entity_key, str) and entity_key:
                stable_key_counts[entity_key] = stable_key_counts.get(entity_key, 0) + 1

    choices = {}
    for device_name, entities in devices.items():
        device = _get_device_attributes(options, device_name)
        display_name = _device_display_name(device_name, device)
        for index, entity in enumerate(_entity_list_or_empty(entities)):
            if not isinstance(entity, Mapping):
                if include_invalid:
                    choices[_entity_key(device_name, index)] = (
                        f"{display_name} / #{index + 1} (!)"
                    )
                continue
            platform = entity.get(CONF_PLATFORM, DEFAULT_ENTITY_DOMAIN)
            name = entity.get(CONF_NAME, "Virtual Entity")
            entity_key = entity.get(ATTR_ENTITY_KEY)
            choices[_selection_key_for_entity(
                device_name,
                index,
                entity,
                stable_key_is_unique=(
                    isinstance(entity_key, str)
                    and stable_key_counts.get(entity_key) == 1
                ),
            )] = (
                f"{display_name} / {name} ({platform})"
            )
    return choices


def _select_entity_schema(options: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_ENTITY_KEY): vol.In(_entity_choices(options)),
        }
    )


def _delete_entities_schema(options: dict[str, Any]) -> vol.Schema:
    choices = _entity_choices(options, include_invalid=True)
    return vol.Schema(
        {
            vol.Required(CONF_ENTITY_KEYS): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        {"value": value, "label": label}
                        for value, label in choices.items()
                    ],
                    multiple=True,
                    mode=selector.SelectSelectorMode.LIST,
                ),
            ),
        }
    )


def _get_ui_entity(
    options: dict[str, Any], device_name: str, index: int
) -> dict[str, Any]:
    devices = _options_devices(options)
    entities = _entity_list_or_empty(devices.get(device_name))
    if index < 0 or index >= len(entities):
        raise InvalidEntitySelection
    if not isinstance(entities[index], Mapping):
        raise InvalidEntitySelection
    return entities[index]


def _delete_ui_entities(
    options: dict[str, Any], entity_keys: list[str]
) -> dict[str, Any]:
    parsed_keys = [
        _find_entity_by_selection_key(options, entity_key)
        for entity_key in (entity_keys or [])
    ]
    if not parsed_keys:
        raise InvalidEntitySelection

    next_options = _plain_options(options or {})
    if not isinstance(next_options.get(ATTR_DEVICES), Mapping):
        next_options[ATTR_DEVICES] = {}
    devices = next_options.setdefault(ATTR_DEVICES, {})
    grouped_indexes: dict[str, set[int]] = {}
    for device_name, index in parsed_keys:
        entities = _entity_list_or_empty(devices.get(device_name))
        if index < 0 or index >= len(entities):
            raise InvalidEntitySelection
        grouped_indexes.setdefault(device_name, set()).add(index)

    for device_name, indexes in grouped_indexes.items():
        entities = _entity_list_or_empty(devices.get(device_name))
        for index in sorted(indexes, reverse=True):
            entities.pop(index)

        if entities:
            devices[device_name] = entities
        else:
            devices.pop(device_name, None)
            device_attributes = _options_device_attributes(next_options)
            device_attributes.pop(device_name, None)
            next_options[ATTR_DEVICE_ATTRIBUTES] = device_attributes

    return next_options


def _delete_ui_device(options: dict[str, Any], device_name: str) -> dict[str, Any]:
    """Delete one Device and every entity or malformed item assigned to it."""
    next_options = _plain_options(options or {})
    if not isinstance(next_options.get(ATTR_DEVICES), Mapping):
        raise InvalidEntitySelection
    devices = next_options[ATTR_DEVICES]
    if not isinstance(device_name, str) or device_name not in devices:
        raise InvalidEntitySelection

    devices.pop(device_name, None)
    device_attributes = _options_device_attributes(next_options)
    device_attributes.pop(device_name, None)
    next_options[ATTR_DEVICE_ATTRIBUTES] = device_attributes
    return next_options


def _json_default(value) -> str:
    if not value:
        return ""
    return json.dumps(_json_safe(_plain_options(value)), sort_keys=True)


def _json_safe(value, _seen=None, _depth=0):
    """Return a value that can be displayed and saved as Home Assistant JSON."""
    if _depth > 100:
        return None
    if isinstance(value, Enum):
        return _json_safe(value.value, _seen, _depth + 1)
    try:
        json.dumps(value, allow_nan=False)
        json_bytes(value)
        return value
    except (TypeError, ValueError, OverflowError, RecursionError):
        if _seen is None:
            _seen = set()
        if isinstance(value, Mapping):
            identity = id(value)
            if identity in _seen:
                return None
            _seen.add(identity)
            try:
                result = {}
                for key, item in value.items():
                    try:
                        safe_key = str(key)
                    except (TypeError, ValueError, OverflowError):
                        continue
                    result[safe_key] = _json_safe(item, _seen, _depth + 1)
                return result
            finally:
                _seen.remove(identity)
        if isinstance(value, (list, tuple, set, frozenset)):
            identity = id(value)
            if identity in _seen:
                return None
            _seen.add(identity)
            try:
                return [
                    _json_safe(item, _seen, _depth + 1)
                    for item in value
                ]
            finally:
                _seen.remove(identity)
        if isinstance(value, int) and not isinstance(value, bool):
            return None
        try:
            return str(value)
        except (TypeError, ValueError, OverflowError):
            return None


def _fallback_entity_name(entity_id: str) -> str:
    object_id = entity_id.split(".", 1)[1]
    return object_id.replace("_", " ").title()


def _normalize_reference_entity_ids(value: Any) -> list[str]:
    """Normalize source-picker values without leaking Python type errors."""
    if value is None:
        return []
    if isinstance(value, str):
        if not value:
            return []
        values = [value]
    elif isinstance(value, (list, tuple, set, frozenset)):
        if not value:
            return []
        values = list(value)
        if isinstance(value, (set, frozenset)):
            values.sort(key=lambda item: str(item))
    else:
        raise InvalidEntityReference(CONF_REFERENCE_ENTITY_ID)

    entity_ids = []
    for entity_id in values:
        if not isinstance(entity_id, str):
            raise InvalidEntityReference(CONF_REFERENCE_ENTITY_ID)
        try:
            entity_ids.append(cv.entity_id(entity_id.strip()))
        except vol.Invalid as err:
            raise InvalidEntityReference(CONF_REFERENCE_ENTITY_ID) from err
    return list(dict.fromkeys(entity_ids))


def _validate_mergeable_source_entities(
    entity_ids: list[str],
    field_name: str,
) -> None:
    """Reject binary media sources where a multi-source helper is undefined."""
    if len(entity_ids) > 1 and any(
        entity_id.split(".", 1)[0] in NON_MERGEABLE_SOURCE_DOMAINS
        for entity_id in entity_ids
    ):
        raise InvalidEntityReference(field_name)


def _source_variable_name(entity_id: str, existing: set[str]) -> str:
    object_id = entity_id.split(".", 1)[1]
    variable_name = slugify(object_id) or "source"
    if (
        variable_name[0].isdigit()
        or variable_name.casefold()
        in JINJA_RESERVED_VARIABLE_NAMES | JINJA_CONTEXT_VARIABLE_NAMES
    ):
        variable_name = f"source_{variable_name}"

    candidate = variable_name
    index = 2
    while candidate in existing:
        candidate = f"{variable_name}_{index}"
        index += 1
    existing.add(candidate)
    return candidate


def _source_state_is_boolean(entity_id: str, state) -> bool:
    domain = entity_id.split(".", 1)[0]
    if domain in BOOLEAN_SOURCE_DOMAINS:
        return True
    if domain in NUMBER_SOURCE_DOMAINS:
        return False
    value = str(state.state).lower()
    if value in {"0", "1"}:
        return False
    return value in BOOLEAN_TRUE_STATES | BOOLEAN_FALSE_STATES


def _source_state_is_true(state) -> bool:
    return str(state.state).lower() in BOOLEAN_TRUE_STATES


def _presence_or_motion_device_class(entity_ids: list[str], states: list) -> str | None:
    """Return the virtual class when every source is a motion/presence sensor."""
    if not all(entity_id.startswith("binary_sensor.") for entity_id in entity_ids):
        return None
    device_classes = {
        str(state.attributes.get("device_class", "")).lower() for state in states
    }
    if not device_classes or not device_classes <= PRESENCE_MOTION_DEVICE_CLASSES:
        return None
    return "motion" if "motion" in device_classes else "presence"


def _safety_boolean_sources(entity_ids: list[str], states: list) -> bool:
    """Return true for alarm-like binary sensors where any active source wins."""
    if not all(entity_id.startswith("binary_sensor.") for entity_id in entity_ids):
        return False
    device_classes = {
        str(state.attributes.get("device_class", "")).lower() for state in states
    }
    return bool(device_classes) and device_classes <= SAFETY_BOOLEAN_DEVICE_CLASSES


def _safety_boolean_helper_template(variable_names: list[str]) -> str:
    """Build an OR helper for leak, smoke, gas, and other alarm sensors."""
    active_checks = ", ".join(
        f"(({variable_name} | lower) in {sorted(BOOLEAN_TRUE_STATES)!r})"
        for variable_name in variable_names
    )
    return "{{ ( [" + active_checks + "] | select | list | count ) > 0 }}"


def _presence_motion_helper_template(
    entity_ids: list[str],
    variable_names: list[str],
) -> str:
    """Build a majority detector which clears after every source is off for 5 minutes."""
    active_checks = ", ".join(
        f"(({variable_name} | lower) in {sorted(BOOLEAN_TRUE_STATES)!r})"
        for variable_name in variable_names
    )
    off_checks = ", ".join(
        f"(({variable_name} | lower) == 'off')" for variable_name in variable_names
    )
    last_changed_values = ", ".join(
        "(as_timestamp(states["
        + repr(entity_id)
        + "].last_changed) if states["
        + repr(entity_id)
        + "] is not none else as_timestamp(now()))"
        for entity_id in entity_ids
    )
    return (
        "{% set active = [" + active_checks + "] | select | list %}"
        "{% set all_off = (["
        + off_checks
        + "] | select | list | count) == "
        + str(len(variable_names))
        + " %}"
        "{% set all_off_since = [" + last_changed_values + "] | max %}"
        "{% if (active | count) > " + str(len(variable_names)) + " / 2 %}true"
        "{% elif this is not none and this.state == 'on' and "
        "((active | count) > 0 or not all_off or "
        "(as_timestamp(now()) - all_off_since) < "
        + str(PRESENCE_MOTION_CLEAR_DELAY_SECONDS)
        + ") %}true"
        "{% else %}false{% endif %}"
    )


def _source_state_is_number(entity_id: str, state) -> bool:
    domain = entity_id.split(".", 1)[0]
    if domain in BOOLEAN_SOURCE_DOMAINS:
        return False
    if domain in NUMBER_SOURCE_DOMAINS:
        return True
    try:
        value = float(state.state)
    except (TypeError, ValueError, OverflowError):
        return False
    return math.isfinite(value)


def _source_state_as_float(state) -> float | None:
    try:
        value = float(state.state)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def _average_known_states(states: list) -> str:
    values = []
    for state in states:
        if not _source_state_is_known(state):
            continue
        value = _source_state_as_float(state)
        if value is not None:
            values.append(value)
    values = _filtered_numeric_values(values)
    return str(sum(values) / len(values)) if values else "unknown"


def _filtered_numeric_values(values: list[float]) -> list[float]:
    """Drop positive numeric spikes using a median-relative threshold."""
    if len(values) < 2:
        return values
    ordered = sorted(values)
    if len(ordered) == 2:
        reference = ordered[0]
    else:
        middle = len(ordered) // 2
        reference = (
            ordered[middle]
            if len(ordered) % 2
            else (ordered[middle - 1] + ordered[middle]) / 2
        )
    if reference <= 0:
        return values
    return [
        value
        for value in values
        if value <= 0 or value <= reference * NUMERIC_OUTLIER_THRESHOLD
    ]


def _robust_average_helper_template(
    expressions: list[str], empty_value: str = "none"
) -> str:
    """Build a numeric average helper that ignores invalid values and spikes."""
    return (
        f"{{% set threshold = {NUMERIC_OUTLIER_THRESHOLD} %}}"
        "{% set ns = namespace(values=[]) %}"
        "{% for raw_value in ["
        + ", ".join(expressions)
        + "] %}{% set value = raw_value | float(none) %}"
        "{% if raw_value | is_number and value is not none %}"
        "{% set ns.values = ns.values + [value] %}"
        "{% endif %}{% endfor %}"
        "{% set ordered = ns.values | sort %}"
        "{% set count = ordered | count %}"
        "{% if count >= 3 %}"
        "{% set middle = count // 2 %}"
        "{% set median = ordered[middle] if count % 2 else "
        "(ordered[middle - 1] + ordered[middle]) / 2 %}"
        "{% elif count == 2 %}{% set median = ordered[0] %}"
        "{% else %}{% set median = none %}{% endif %}"
        "{% set kept = namespace(values=[]) %}"
        "{% for value in ordered %}"
        "{% if median is none or median <= 0 or value <= 0 or "
        "value <= median * threshold %}"
        "{% set kept.values = kept.values + [value] %}"
        "{% endif %}{% endfor %}"
        "{{ (kept.values | average) if kept.values else "
        + empty_value
        + " }}"
    )


def _all_source_domains(entity_ids: list[str], domains: set[str]) -> bool:
    return all(entity_id.split(".", 1)[0] in domains for entity_id in entity_ids)


def _source_state_is_known(state) -> bool:
    return str(state.state).lower() not in UNKNOWN_STATES


def _first_known_state(states: list, default: str = "unknown") -> str:
    for state in states:
        if _source_state_is_known(state):
            return str(state.state)
    return default


def _latest_state(states: list) -> str:
    values = [str(state.state) for state in states if _source_state_is_known(state)]
    return max(values) if values else "unknown"


def _latest_datetime_state(states: list) -> str:
    """Return the original value with the latest timezone-aware instant."""
    candidates = []
    for state in states:
        if not _source_state_is_known(state):
            continue
        value = str(state.state)
        parsed = dt_util.parse_datetime(value)
        if parsed is not None:
            candidates.append((parsed.timestamp(), value))
    return max(candidates)[1] if candidates else _latest_state(states)


def _latest_datetime_helper_template(
    variable_names: list[str],
    empty_value: str = "'unknown'",
) -> str:
    """Build a timezone-correct helper while preserving the selected source text."""
    return (
        "{% set ns = namespace(value=" + empty_value + ", timestamp=none) %}"
        "{% for value in [" + ", ".join(variable_names) + "] %}"
        "{% if value not in ['unknown', 'unavailable', 'none', '', none] %}"
        "{% set timestamp = as_timestamp(value, none) %}"
        "{% if timestamp is not none and "
        "(ns.timestamp is none or timestamp > ns.timestamp) %}"
        "{% set ns.timestamp = timestamp %}{% set ns.value = value %}"
        "{% endif %}{% endif %}{% endfor %}{{ ns.value }}"
    )


def _location_state(states: list) -> str:
    return "home" if all(str(state.state) == "home" for state in states) else "not_home"


def _reference_states(hass, entity_ids: list[str]) -> list:
    states = []
    for entity_id in entity_ids:
        state = hass.states.get(entity_id)
        if state is None:
            raise InvalidEntityReference(CONF_REFERENCE_ENTITY_ID)
        states.append(state)
    return states


def _device_name_for_source_entity(hass, entity_id: str) -> str:
    entity_entry = er.async_get(hass).async_get(entity_id)
    if entity_entry is None or entity_entry.device_id is None:
        return "Virtual Device"

    device_entry = dr.async_get(hass).async_get(entity_entry.device_id)
    if device_entry is None:
        return "Virtual Device"
    return device_entry.name_by_user or device_entry.name or "Virtual Device"


def _combined_device_name(hass, entity_ids: list[str]) -> str:
    device_names = {
        _device_name_for_source_entity(hass, entity_id) for entity_id in entity_ids
    }
    if len(device_names) == 1:
        return next(iter(device_names))
    return "Virtual Device"


def _combined_entity_name(states: list) -> str:
    names = [
        state.attributes.get(ATTR_FRIENDLY_NAME)
        or _fallback_entity_name(state.entity_id)
        for state in states
    ]
    if len(names) == 1:
        return _shorten_generated_text(str(names[0]), MAX_GENERATED_ENTITY_NAME_LENGTH)

    combined_name = f"Combined {names[0]}"
    if len(names) > 1:
        combined_name = f"{combined_name} + {len(names) - 1} more"
    return _shorten_generated_text(combined_name, MAX_GENERATED_ENTITY_NAME_LENGTH)


def _native_source_template(
    entity_id: str,
    state,
    property_name: str,
    platform: str | None = None,
    use_fallback: bool = True,
) -> str:
    """Build a native-property helper from a source state when possible."""
    attributes = state.attributes
    source_platform = entity_id.split(".", 1)[0]
    attribute_name = property_name
    if attribute_name not in attributes:
        attribute_name = NATIVE_TEMPLATE_ATTRIBUTE_ALIASES.get(property_name, "")
    if attribute_name and attribute_name in attributes:
        expression = f"state_attr({entity_id!r}, {attribute_name!r})"
        if use_fallback and property_name in NATIVE_TEMPLATE_DATETIME_PROPERTIES:
            return _latest_datetime_helper_template([expression], "none")
        source_value = attributes.get(attribute_name)
        fallback = None
        if (
            source_value is not None
            and attribute_name not in TRANSIENT_SOURCE_ATTRIBUTE_NAMES
        ):
            fallback = _native_source_snapshot_fallback(
                platform or source_platform,
                property_name,
                source_value,
            )
        if fallback is None:
            fallback = _native_source_helper_default(
                platform or source_platform,
                property_name,
            )
        if use_fallback and fallback is not None:
            return (
                "{{ "
                + expression
                + " if "
                + expression
                + " is not none else "
                + repr(_plain_options(fallback))
                + " }}"
            )
        return f"{{{{ {expression} }}}}"

    if source_platform == "calendar" and property_name == "event":
        return (
            "{{ {"
            f"'summary': state_attr({entity_id!r}, 'message'), "
            f"'start': state_attr({entity_id!r}, 'start_time'), "
            f"'end': state_attr({entity_id!r}, 'end_time'), "
            f"'all_day': state_attr({entity_id!r}, 'all_day'), "
            f"'location': state_attr({entity_id!r}, 'location'), "
            f"'description': state_attr({entity_id!r}, 'description')"
            "} }}"
        )
    if source_platform == "event" and property_name == "event_attributes":
        return (
            "{{ states["
            + repr(entity_id)
            + "].attributes if states["
            + repr(entity_id)
            + "] else {} }}"
        )

    if property_name == "source_entity":
        return f"{{{{ {entity_id!r} }}}}"
    if property_name in NATIVE_TEMPLATE_STATE_PROPERTIES:
        expression = f"states({entity_id!r})"
        if use_fallback and property_name in NATIVE_TEMPLATE_DATETIME_PROPERTIES:
            return _latest_datetime_helper_template([expression], "none")
        return f"{{{{ {expression} }}}}"
    if property_name == "is_on":
        return (
            f"{{{{ states({entity_id!r}) not in "
            "['off', 'unknown', 'unavailable'] }}"
        )
    if state_values := NATIVE_TEMPLATE_BOOLEAN_STATE_VALUES.get(property_name):
        return (
            f"{{{{ states({entity_id!r}) in "
            f"{sorted(state_values)!r} }}}}"
        )
    if mask := NATIVE_TEMPLATE_SUPPORTED_FEATURE_MASKS.get(
        (source_platform, property_name)
    ):
        features = f"(state_attr({entity_id!r}, 'supported_features') | int(0))"
        return f"{{{{ (({features} // {mask}) % 2) == 1 }}}}"
    if property_name == "reports_position":
        return f"{{{{ state_attr({entity_id!r}, 'current_position') is number }}}}"
    if source_platform == "fan" and property_name == "speed_count":
        percentage_step = (
            f"state_attr({entity_id!r}, 'percentage_step') | float(0)"
        )
        fallback = _native_source_helper_default(
            platform or source_platform,
            property_name,
        )
        fallback_expression = (
            repr(_plain_options(fallback)) if use_fallback else "none"
        )
        return (
            "{{ (100 / ("
            + percentage_step
            + ")) | round(0) | int if ("
            + percentage_step
            + ") > 0 else "
            + fallback_expression
            + " }}"
        )
    attribute_name = NATIVE_TEMPLATE_ATTRIBUTE_ALIASES.get(
        property_name,
        property_name,
    )
    expression = f"state_attr({entity_id!r}, {attribute_name!r})"
    fallback = _native_source_helper_default(
        platform or source_platform,
        property_name,
    )
    if fallback is None or not use_fallback:
        return f"{{{{ {expression} }}}}"
    return (
        "{{ "
        + expression
        + " if "
        + expression
        + " is not none else "
        + repr(_plain_options(fallback))
        + " }}"
    )


def _native_reference_templates(
    platform: str,
    entity_ids: list[str],
    states: list,
) -> dict[str, str]:
    """Generate editable native Jinja helpers for source entities."""
    if not entity_ids or len(entity_ids) != len(states):
        return {}
    templates = {}
    for property_name in DOMAIN_NATIVE_TEMPLATE_PROPERTIES.get(platform, ()):
        source_templates = [
            _native_source_template(
                entity_id,
                state,
                property_name,
                platform,
                use_fallback=len(entity_ids) == 1,
            )
            for entity_id, state in zip(entity_ids, states, strict=True)
        ]
        aliases = NATIVE_TEMPLATE_ATTRIBUTE_ALIASES
        source_has_values = [
            property_name in state.attributes
            or aliases.get(property_name) in state.attributes
            or property_name
            in {
                "is_on",
                "reports_position",
                "source_entity",
                *NATIVE_TEMPLATE_STATE_PROPERTIES,
                *NATIVE_TEMPLATE_BOOLEAN_STATE_VALUES,
            }
            or (platform, property_name) in NATIVE_TEMPLATE_SUPPORTED_FEATURE_MASKS
            or (
                platform == "calendar"
                and property_name == "event"
            )
            or (
                platform == "event"
                and property_name == "event_attributes"
            )
            or (
                platform == "fan"
                and property_name == "speed_count"
                and "percentage_step" in state.attributes
            )
            for state in states
        ]
        attribute_name = NATIVE_TEMPLATE_ATTRIBUTE_ALIASES.get(
            property_name,
            property_name,
        )
        source_templates = [
            template
            or f"{{{{ state_attr({entity_id!r}, {attribute_name!r}) }}}}"
            for entity_id, template in zip(entity_ids, source_templates, strict=True)
        ]
        if len(source_templates) == 1:
            templates[property_name] = source_templates[0] or _literal_template(
                _native_source_helper_default(platform, property_name)
            )
            continue

        values = []
        for state in states:
            attribute_name = property_name
            if attribute_name not in state.attributes:
                attribute_name = NATIVE_TEMPLATE_ATTRIBUTE_ALIASES.get(
                    property_name,
                    "",
                )
            values.append(
                state.attributes.get(attribute_name)
                if attribute_name
                else state.state
            )
        merged_template = _merged_native_template(
            platform,
            property_name,
            source_templates,
            values,
        )
        fallback = _native_source_helper_default(platform, property_name)
        if any(source_has_values):
            templates[property_name] = merged_template
        else:
            templates[property_name] = _literal_template(fallback)
    return templates


def _merged_native_template(
    platform: str,
    property_name: str,
    source_templates: list[str],
    values: list[Any],
) -> str:
    """Combine native source expressions according to their value shape."""
    expressions = [
        template.removeprefix("{{").removesuffix("}}").strip()
        for template in source_templates
    ]
    if property_name in NATIVE_TEMPLATE_BOOLEAN_ANY_PROPERTIES:
        return (
            "{% set values = ["
            + ", ".join(expressions)
            + "] | select('boolean') | list %}"
            "{{ (values | select | list | count) > 0 }}"
        )
    if property_name in NATIVE_TEMPLATE_BOOLEAN_PROPERTIES or all(
        isinstance(value, bool) for value in values
    ):
        return (
            "{% set values = ["
            + ", ".join(expressions)
            + "] | select('boolean') | list %}"
            "{{ (values | count) > 0 and (values | reject | list | count) == 0 }}"
        )
    if property_name in NATIVE_TEMPLATE_BITMASK_PROPERTIES:
        bitmask = f"({expressions[0]} | int(0))"
        for expression in expressions[1:]:
            bitmask = f"({bitmask} | bitwise_or({expression} | int(0)))"
        return f"{{{{ {bitmask} }}}}"
    if property_name in NATIVE_TEMPLATE_MINIMUM_PROPERTIES:
        return (
            "{% set values = ["
            + ", ".join(expressions)
            + "] | select('is_number') | map('float') | list %}"
            "{{ (values | min) if values else none }}"
        )
    if property_name in NATIVE_TEMPLATE_MAXIMUM_PROPERTIES:
        return (
            "{% set values = ["
            + ", ".join(expressions)
            + "] | select('is_number') | map('float') | list %}"
            "{{ (values | max) if values else none }}"
        )
    if platform == "datetime" and property_name == "native_value":
        return _latest_datetime_helper_template(expressions, "none")
    if property_name in NATIVE_TEMPLATE_DATETIME_PROPERTIES:
        return _latest_datetime_helper_template(expressions, "none")
    if platform in {"date", "time"} and property_name == "native_value":
        return (
            "{{ ["
            + ", ".join(expressions)
            + "] | reject('in', ['unknown', 'unavailable', 'none', '', none]) "
            "| list | sort | last | default(none) }}"
        )
    if platform == "text" and property_name == "native_value":
        return (
            "{% set values = ["
            + ", ".join(expressions)
            + "] | reject('in', ['unknown', 'unavailable', 'none', '', none]) | list %}"
            "{{ values | join('') }}"
        )
    if (
        property_name in NATIVE_TEMPLATE_NUMERIC_PROPERTIES
        or (platform == "number" and property_name == "native_value")
        or all(
        isinstance(value, (int, float)) and not isinstance(value, bool)
        for value in values
        )
    ):
        return (
            "{% set values = ["
            + ", ".join(expressions)
            + "] | select('is_number') | map('float') | list %}"
            "{{ (values | average) if values else none }}"
        )
    if property_name in NATIVE_TEMPLATE_ATOMIC_LIST_PROPERTIES:
        return (
            "{% set values = ["
            + ", ".join(expressions)
            + "] | select('list') | list %}"
            "{{ values[0] if values else none }}"
        )
    if property_name in NATIVE_TEMPLATE_LIST_PROPERTIES or all(
        isinstance(value, (list, tuple, set)) for value in values
    ):
        fallback_values = []
        for items in values:
            if isinstance(items, (list, tuple, set)):
                fallback_values.extend(
                    item for item in items if item not in fallback_values
                )
        if not fallback_values:
            fallback = _native_source_helper_default(platform, property_name)
            if isinstance(fallback, list):
                fallback_values = fallback
        result = "ns.values"
        if property_name == "hvac_modes" and fallback_values:
            result += " if ns.values else " + repr(_plain_options(fallback_values))
        return (
            "{% set ns = namespace(values=[]) %}"
            "{% for items in ["
            + ", ".join(expressions)
            + "] %}{% if items is list %}{% for value in items %}"
            "{% if value not in ns.values %}{% set ns.values = ns.values + [value] %}"
            "{% endif %}{% endfor %}{% endif %}{% endfor %}{{ "
            + result
            + " }}"
        )
    if property_name in NATIVE_TEMPLATE_MAPPING_PROPERTIES or all(
        isinstance(value, Mapping) for value in values
    ):
        return (
            "{% set ns = namespace(value={}) %}{% for item in ["
            + ", ".join(expressions)
            + "] %}{% if item is mapping %}"
            "{% set ns.value = dict((ns.value.items() | list) + (item.items() | list)) %}"
            "{% endif %}{% endfor %}{{ ns.value }}"
        )
    return (
        "{% set values = ["
        + ", ".join(expressions)
        + "] | reject('in', ['unknown', 'unavailable', 'none', '', none]) | list %}"
        "{{ values[0] if values else none }}"
    )


def _native_source_attribute_names(platform: str, state) -> set[str]:
    """Return source attributes already represented by native templates."""
    attributes = state.attributes
    names = set()
    if platform == "fan":
        # percentage_step is converted into the native speed_count helper. It
        # must not also become a generic attribute template.
        names.update(FAN_NATIVE_ATTRIBUTE_FIELDS & attributes.keys())
    elif platform == "calendar":
        names.update(CALENDAR_EVENT_SOURCE_ATTRIBUTES & attributes.keys())
    elif platform == "event":
        # event_attributes copies the complete source mapping and the event
        # platform publishes it again as native state attributes.
        names.update(attributes.keys())
    for property_name in DOMAIN_NATIVE_TEMPLATE_PROPERTIES.get(platform, ()):
        if property_name in attributes:
            names.add(property_name)
        alias = NATIVE_TEMPLATE_ATTRIBUTE_ALIASES.get(property_name)
        if alias in attributes:
            names.add(alias)
    return names


def _merged_attribute_template(
    entity_ids: list[str],
    attribute_name: str,
    values: list[Any],
) -> str:
    """Build a type-aware Jinja helper for one common source attribute."""
    expressions = [
        f"state_attr({entity_id!r}, {attribute_name!r})"
        for entity_id in entity_ids
    ]
    if all(isinstance(value, bool) for value in values):
        return (
            "{% set values = ["
            + ", ".join(expressions)
            + "] | select('boolean') | list %}"
            "{{ (values | count) > 0 and (values | reject | list | count) == 0 }}"
        )
    if all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in values):
        return (
            "{% set values = ["
            + ", ".join(expressions)
            + "] | select('is_number') | map('float') | list %}"
            "{{ (values | average) if values else none }}"
        )
    if all(isinstance(value, Mapping) for value in values):
        merged = f"({expressions[0]} | default({{}}, true))"
        for expression in expressions[1:]:
            merged = (
                "dict(("
                + merged
                + ".items() | list) + (("
                + expression
                + " | default({}, true)).items() | list))"
            )
        return f"{{{{ {merged} }}}}"
    if all(isinstance(value, (list, tuple, set)) for value in values):
        lists = [f"({item} | default([], true) | list)" for item in expressions]
        return (
            "{% set ns = namespace(values=[]) %}{% for value in "
            + " + ".join(lists)
            + " %}{% if value not in ns.values %}"
            "{% set ns.values = ns.values + [value] %}"
            "{% endif %}{% endfor %}{{ ns.values }}"
        )
    if all(isinstance(value, str) for value in values):
        return "{{ " + " ~ ".join(
            f"({item} | default('', true))" for item in expressions
        ) + " }}"
    return (
        "{% set values = ["
        + ", ".join(expressions)
        + "] | reject('eq', none) | list %}"
        "{{ values[0] if values else none }}"
    )


def _attribute_reference_templates(
    platform: str,
    entity_ids: list[str],
    states: list,
) -> dict[str, str]:
    """Generate dynamic helpers for source attributes not handled natively."""
    if not states:
        return {}

    attribute_names = {
        name
        for state in states
        for name in state.attributes
        if isinstance(name, str) and name.strip()
    }
    attribute_names.difference_update(_ATTRIBUTE_HELPER_METADATA_NAMES)
    attribute_names.difference_update(RESERVED_VIRTUAL_ATTRIBUTE_NAMES)
    for state in states:
        attribute_names.difference_update(
            _native_source_attribute_names(platform, state),
        )

    templates = {}
    for attribute_name in sorted(attribute_names):
        available = [
            (entity_id, state.attributes[attribute_name])
            for entity_id, state in zip(entity_ids, states, strict=True)
            if attribute_name in state.attributes
        ]
        values = [value for _entity_id, value in available]
        if len(entity_ids) == 1:
            templates[attribute_name] = (
                f"{{{{ state_attr({entity_ids[0]!r}, {attribute_name!r}) }}}}"
            )
        else:
            templates[attribute_name] = _merged_attribute_template(
                entity_ids,
                attribute_name,
                values,
            )
    return templates


def _shorten_generated_text(value: str, max_length: int) -> str:
    """Shorten generated UI defaults without touching user-submitted values."""
    value = " ".join(str(value).split())
    if len(value) <= max_length:
        return value
    return value[: max_length - 3].rstrip() + "..."


def _heating_only_climate(state) -> bool:
    """Return whether a climate source represents a heat-only appliance."""
    raw_modes = state.attributes.get("hvac_modes")
    if not isinstance(raw_modes, (list, tuple, set, frozenset)):
        return False
    modes = {
        str(mode).strip().lower()
        for mode in raw_modes
        if str(mode).strip()
    }
    return {"off", "heat"}.issubset(modes) and not modes.intersection(
        {"cool", "dry", "heat_cool"}
    )


def _boiler_source_profile(
    entity_ids: list[str],
    states: list,
) -> tuple[int, str | None] | None:
    """Return the climate index and optional hot-water switch for a boiler."""
    climate_indexes = [
        index
        for index, entity_id in enumerate(entity_ids)
        if entity_id.startswith("climate.")
    ]
    switch_ids = [
        entity_id for entity_id in entity_ids if entity_id.startswith("switch.")
    ]
    if len(entity_ids) == 2 and len(climate_indexes) == 1 and len(switch_ids) == 1:
        return climate_indexes[0], switch_ids[0]
    if (
        len(entity_ids) == 1
        and len(climate_indexes) == 1
        and _heating_only_climate(states[climate_indexes[0]])
    ):
        return climate_indexes[0], None
    return None


def _humidifier_component_profile(
    entity_ids: Collection[str],
) -> dict[str, int] | None:
    """Return source roles for a humidifier split across helper entities."""
    roles: dict[str, int] = {}
    allowed_domains = {"number", "select", "sensor", "switch"}
    for index, entity_id in enumerate(entity_ids):
        domain = entity_id.split(".", 1)[0]
        if domain not in allowed_domains or domain in roles:
            return None
        roles[domain] = index
    if not {"number", "sensor", "switch"} <= roles.keys():
        return None
    return roles


def _humidifier_component_native_templates(
    entity_ids: list[str],
    states: list,
    profile: Mapping[str, int],
) -> dict[str, str]:
    """Build native humidifier properties from separate control entities."""
    number_id = entity_ids[profile["number"]]
    sensor_id = entity_ids[profile["sensor"]]
    switch_id = entity_ids[profile["switch"]]
    select_id = (
        entity_ids[profile["select"]] if "select" in profile else None
    )
    number_state = states[profile["number"]]
    templates = {
        "is_on": (
            f"{{{{ states({switch_id!r}) not in "
            "['off', 'unknown', 'unavailable'] }}"
        ),
        "device_class": "{{ 'dehumidifier' }}",
        "action": (
            f"{{{{ 'drying' if states({switch_id!r}) == 'on' else 'off' }}}}"
        ),
        "available_modes": (
            f"{{{{ state_attr({select_id!r}, 'options') | default([], true) | list }}}}"
            if select_id
            else "{{ [] }}"
        ),
        "mode": (
            f"{{{{ states({select_id!r}) if states({select_id!r}) not in "
            "['unknown', 'unavailable'] else none }}"
            if select_id
            else "{{ none }}"
        ),
        "current_humidity": (
            f"{{{{ states({sensor_id!r}) | float(none) }}}}"
        ),
        "target_humidity": (
            f"{{{{ states({number_id!r}) | float(none) }}}}"
        ),
        "min_humidity": _native_source_template(
            number_id, number_state, "native_min_value", "number"
        ),
        "max_humidity": _native_source_template(
            number_id, number_state, "native_max_value", "number"
        ),
        "target_humidity_step": _native_source_template(
            number_id, number_state, "native_step", "number"
        ),
    }
    return templates


def _humidifier_component_command_actions(
    entity_ids: list[str],
    profile: Mapping[str, int],
) -> dict[str, Any]:
    """Build humidifier commands for separate number/select/switch helpers."""
    number_id = entity_ids[profile["number"]]
    switch_id = entity_ids[profile["switch"]]
    actions: dict[str, Any] = {
        "turn_off": [{
            "action": "switch.turn_off",
            "target": {ATTR_ENTITY_ID: switch_id},
        }],
        "turn_on": [{
            "action": "switch.turn_on",
            "target": {ATTR_ENTITY_ID: switch_id},
        }],
        "set_humidity": [{
            "action": "number.set_value",
            "target": {ATTR_ENTITY_ID: number_id},
            "data": {"value": "{{ humidity }}"},
        }],
    }
    if "select" in profile:
        select_id = entity_ids[profile["select"]]
        actions["set_mode"] = [{
            "action": "select.select_option",
            "target": {ATTR_ENTITY_ID: select_id},
            "data": {"option": "{{ mode }}"},
        }]
    return actions


def _xiaomi_fan_number_profile(
    entity_ids: list[str],
    states: list,
) -> tuple[int, int] | None:
    """Return source indexes for a Xiaomi-style fan plus speed number."""
    if len(entity_ids) != 2:
        return None
    fan_indexes = [
        index
        for index, entity_id in enumerate(entity_ids)
        if entity_id.startswith("fan.")
    ]
    number_indexes = [
        index
        for index, entity_id in enumerate(entity_ids)
        if entity_id.split(".", 1)[0] in {"input_number", "number"}
    ]
    if len(fan_indexes) != 1 or len(number_indexes) != 1:
        return None
    fan_state = states[fan_indexes[0]]
    modes = fan_state.attributes.get("preset_modes", [])
    if not isinstance(modes, (list, tuple, set, frozenset)):
        modes = []
    advertised_modes = {
        str(mode).strip().lower()
        for mode in [*modes, fan_state.attributes.get("preset_mode")]
        if mode is not None
    }
    if not advertised_modes.intersection({"favorite", "manual"}):
        return None
    return fan_indexes[0], number_indexes[0]


def _finite_source_number(value: Any) -> float | None:
    """Return a finite source capability value or unknown."""
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def _fan_number_speed_kind(number_entity_id: str, number_state) -> str:
    """Classify a separate fan-speed number as percent, level, or RPM."""
    attributes = number_state.attributes
    unit = str(attributes.get(CONF_UNIT_OF_MEASUREMENT, "")).strip().lower()
    identity = " ".join(
        (
            number_entity_id,
            str(attributes.get(ATTR_FRIENDLY_NAME, "")),
            unit,
        )
    ).lower()
    minimum = _finite_source_number(attributes.get("min"))
    maximum = _finite_source_number(attributes.get("max"))

    if unit in {"%", "percent", "percentage"} or (
        minimum == 0 and maximum == 100
    ):
        return "percentage"
    if (
        unit in {"rpm", "r/min", "rev/min"}
        or re.search(r"(^|[^a-z])rpm([^a-z]|$)", identity)
        or maximum is not None
        and maximum > 100
    ):
        return "rpm"
    return "level"


def _xiaomi_fan_speed_count_template(
    number_entity_id: str,
    speed_kind: str,
) -> str:
    """Build a fan speed-count helper appropriate for the number's scale."""
    if speed_kind == "rpm":
        # RPM ranges often contain thousands of integer values. Exposing every
        # RPM as a discrete fan speed makes HA's percentage step unusably tiny.
        return "{{ 100 }}"
    minimum = f"state_attr({number_entity_id!r}, 'min') | float(0)"
    maximum = f"state_attr({number_entity_id!r}, 'max') | float(100)"
    step = f"state_attr({number_entity_id!r}, 'step') | float(1)"
    if speed_kind == "percentage":
        return (
            "{% set step = " + step + " %}"
            "{{ ([1, (100 / step) | round(0) | int] | max) if step > 0 else 100 }}"
        )
    return (
        "{% set minimum = " + minimum + " %}"
        "{% set maximum = " + maximum + " %}"
        "{% set step = " + step + " %}"
        "{{ ((((maximum - minimum) / step) | round(0) | int) + 1) "
        "if step > 0 and maximum >= minimum else 100 }}"
    )


def _xiaomi_fan_percentage_template(
    fan_entity_id: str,
    number_entity_id: str,
    speed_kind: str,
) -> str:
    """Normalize a separate percent, level, or RPM number for HA's fan UI."""
    prefix = (
        "{% set mode = state_attr(" + repr(fan_entity_id)
        + ", 'preset_mode') | string | lower %}"
        "{% set raw = states(" + repr(number_entity_id) + ") | float(none) %}"
        "{% set minimum = state_attr(" + repr(number_entity_id)
        + ", 'min') | float(0) %}"
        "{% set maximum = state_attr(" + repr(number_entity_id)
        + ", 'max') | float(100) %}"
        "{% set step = state_attr(" + repr(number_entity_id)
        + ", 'step') | float(1) %}"
    )
    fallback = "state_attr(" + repr(fan_entity_id) + ", 'percentage')"
    if speed_kind == "percentage":
        normalized = "[0, [100, raw] | min] | max | round(0) | int"
    elif speed_kind == "rpm":
        normalized = (
            "1 if raw <= minimum else "
            "(100 if raw >= maximum else "
            "(1 + ((raw - minimum) * 99 / (maximum - minimum))) | round(0) | int)"
        )
    else:
        normalized = (
            "{% set count = (((maximum - minimum) / step) | round(0) | int) + 1 %}"
            "{% set index = (((raw - minimum) / step) | round(0) | int) + 1 %}"
            "{{ ([1, [count, index] | min] | max) * 100 / count "
            "if mode in ['favorite', 'manual'] and raw is not none and "
            "step > 0 and maximum >= minimum else " + fallback + " }}"
        )
        return prefix + normalized
    return (
        prefix
        + "{{ (" + normalized + ") if mode in ['favorite', 'manual'] "
        "and raw is not none and maximum > minimum else " + fallback + " }}"
    )


def _xiaomi_fan_number_value_template(
    number_entity_id: str,
    speed_kind: str,
) -> str:
    """Convert a requested HA fan percentage back to the source number scale."""
    prefix = (
        "{% set requested = [0, [100, percentage | float(0)] | min] | max %}"
        "{% set minimum = state_attr(" + repr(number_entity_id)
        + ", 'min') | float(0) %}"
        "{% set maximum = state_attr(" + repr(number_entity_id)
        + ", 'max') | float(100) %}"
        "{% set step = state_attr(" + repr(number_entity_id)
        + ", 'step') | float(1) %}"
    )
    if speed_kind == "percentage":
        raw = "requested"
    elif speed_kind == "rpm":
        raw = "minimum if requested <= 1 else minimum + ((requested - 1) * (maximum - minimum) / 99)"
    else:
        raw = (
            "minimum + (((((requested * ((((maximum - minimum) / step) | round(0) | int) + 1) "
            "/ 100) - 1) | round(0) | int)) * step)"
        )
    return (
        prefix
        + "{% set raw = " + raw + " %}"
        "{{ minimum + (((((raw - minimum) / step) + 0.5) | int) * step) "
        "if step > 0 and maximum >= minimum else raw }}"
    )


def _xiaomi_fan_command_actions(
    fan_entity_id: str,
    number_entity_id: str,
    speed_kind: str,
    actions: Mapping[str, Any],
) -> dict[str, Any]:
    """Route fan percentage writes to the range number in manual modes."""
    result = dict(actions)
    number_domain = number_entity_id.split(".", 1)[0]
    choose: dict[str, Any] = {
        "choose": [{
            "conditions": (
                "{{ state_attr(" + repr(fan_entity_id)
                + ", 'preset_mode') | string | lower in ['favorite', 'manual'] "
                "and percentage | float(0) > 0 }}"
            ),
            "sequence": [{
                "action": f"{number_domain}.set_value",
                "target": {ATTR_ENTITY_ID: number_entity_id},
                "data": {
                    "value": _xiaomi_fan_number_value_template(
                        number_entity_id,
                        speed_kind,
                    )
                },
            }],
        }],
    }
    turn_off = result.get("turn_off")
    if isinstance(turn_off, list) and turn_off:
        choose["choose"].append({
            "conditions": "{{ percentage | float(0) == 0 }}",
            "sequence": turn_off,
        })
    existing = result.get("set_percentage")
    if isinstance(existing, list) and existing:
        choose["default"] = existing
    result["set_percentage"] = [choose]
    return result


def _xiaomi_fan_availability_template(
    fan_entity_id: str,
    number_entity_id: str,
) -> str:
    """Require the separate speed number only while its value is selected."""
    return (
        "{% set mode = state_attr(" + repr(fan_entity_id)
        + ", 'preset_mode') | string | lower %}"
        "{{ states(" + repr(fan_entity_id)
        + ") not in ['unknown', 'unavailable'] and "
        "(mode not in ['favorite', 'manual'] or states("
        + repr(number_entity_id) + ") not in ['unknown', 'unavailable']) }}"
    )


def _boiler_mode_template(climate_entity_id: str) -> str:
    """Map a boiler source to the two virtual HVAC modes."""
    return (
        "{{ 'heat' if states("
        + repr(climate_entity_id)
        + ") == 'heat' else 'off' }}"
    )


def _boiler_mode_action_sequence(
    climate_entity_id: str,
    hot_water_switch_id: str | None,
    hvac_mode: str,
) -> list[dict[str, Any]]:
    """Build the source actions for one boiler HVAC mode."""
    sequence = []
    if hot_water_switch_id:
        sequence.append({
            "action": "switch.turn_on",
            "target": {ATTR_ENTITY_ID: hot_water_switch_id},
        })
    source_hvac_mode = (
        "fan_only"
        if hvac_mode == "off" and hot_water_switch_id
        else hvac_mode
    )
    sequence.append({
        "action": "climate.set_hvac_mode",
        "target": {ATTR_ENTITY_ID: climate_entity_id},
        "data": {"hvac_mode": source_hvac_mode},
    })
    return sequence


def _boiler_command_actions(
    climate_entity_id: str,
    climate_state,
    hot_water_switch_id: str | None,
) -> dict[str, Any]:
    """Build editable command actions for a heat/off boiler alias."""
    heat_sequence = _boiler_mode_action_sequence(
        climate_entity_id,
        hot_water_switch_id,
        "heat",
    )
    off_sequence = _boiler_mode_action_sequence(
        climate_entity_id,
        hot_water_switch_id,
        "off",
    )
    set_hvac_mode = [{
        "choose": [
            {
                "conditions": "{{ hvac_mode == 'heat' }}",
                "sequence": heat_sequence,
            },
            {
                "conditions": "{{ hvac_mode == 'off' }}",
                "sequence": off_sequence,
            },
        ],
    }]

    actions = {
        "set_hvac_mode": set_hvac_mode,
        "turn_on": heat_sequence,
        "turn_off": off_sequence,
    }
    if climate_state.attributes.get("temperature") is not None:
        minimum = (
            f"state_attr({climate_entity_id!r}, 'min_temp') | float(7)"
        )
        maximum = (
            f"state_attr({climate_entity_id!r}, 'max_temp') | float(35)"
        )
        actions["set_temperature"] = [{
            "choose": [
                {
                    "conditions": (
                        "{{ temperature is number and temperature >= ("
                        + minimum
                        + ") and temperature <= ("
                        + maximum
                        + ") }}"
                    ),
                    "sequence": [{
                        "action": "climate.set_temperature",
                        "target": {ATTR_ENTITY_ID: climate_entity_id},
                        "data": {"temperature": "{{ temperature }}"},
                    }],
                },
            ],
        }]
    return actions


_SOURCE_COMMAND_FEATURES = {
    ("camera", "turn_off"): CameraEntityFeature.ON_OFF,
    ("camera", "turn_on"): CameraEntityFeature.ON_OFF,
    ("climate", "set_fan_mode"): ClimateEntityFeature.FAN_MODE,
    ("climate", "set_humidity"): ClimateEntityFeature.TARGET_HUMIDITY,
    ("climate", "set_preset_mode"): ClimateEntityFeature.PRESET_MODE,
    ("climate", "set_swing_horizontal_mode"): (
        ClimateEntityFeature.SWING_HORIZONTAL_MODE
    ),
    ("climate", "set_swing_mode"): ClimateEntityFeature.SWING_MODE,
    ("climate", "set_temperature"): (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
    ),
    ("climate", "turn_off"): ClimateEntityFeature.TURN_OFF,
    ("climate", "turn_on"): ClimateEntityFeature.TURN_ON,
    ("cover", "close_cover"): CoverEntityFeature.CLOSE,
    ("cover", "close_cover_tilt"): CoverEntityFeature.CLOSE_TILT,
    ("cover", "open_cover"): CoverEntityFeature.OPEN,
    ("cover", "open_cover_tilt"): CoverEntityFeature.OPEN_TILT,
    ("cover", "set_cover_position"): CoverEntityFeature.SET_POSITION,
    ("cover", "set_cover_tilt_position"): CoverEntityFeature.SET_TILT_POSITION,
    ("cover", "stop_cover"): CoverEntityFeature.STOP,
    ("cover", "stop_cover_tilt"): CoverEntityFeature.STOP_TILT,
    ("fan", "oscillate"): FanEntityFeature.OSCILLATE,
    ("fan", "set_direction"): FanEntityFeature.DIRECTION,
    ("fan", "set_percentage"): FanEntityFeature.SET_SPEED,
    ("fan", "set_preset_mode"): FanEntityFeature.PRESET_MODE,
    ("fan", "turn_off"): FanEntityFeature.TURN_OFF,
    ("fan", "turn_on"): FanEntityFeature.TURN_ON,
    ("humidifier", "set_mode"): HumidifierEntityFeature.MODES,
    ("lawn_mower", "dock"): LawnMowerEntityFeature.DOCK,
    ("lawn_mower", "pause"): LawnMowerEntityFeature.PAUSE,
    ("lawn_mower", "start_mowing"): LawnMowerEntityFeature.START_MOWING,
    ("lock", "open"): LockEntityFeature.OPEN,
    ("media_player", "media_pause"): MediaPlayerEntityFeature.PAUSE,
    ("media_player", "media_play"): MediaPlayerEntityFeature.PLAY,
    ("media_player", "media_stop"): MediaPlayerEntityFeature.STOP,
    ("media_player", "mute_volume"): MediaPlayerEntityFeature.VOLUME_MUTE,
    ("media_player", "select_source"): MediaPlayerEntityFeature.SELECT_SOURCE,
    ("media_player", "select_sound_mode"): (
        MediaPlayerEntityFeature.SELECT_SOUND_MODE
    ),
    ("media_player", "set_repeat"): MediaPlayerEntityFeature.REPEAT_SET,
    ("media_player", "set_shuffle"): MediaPlayerEntityFeature.SHUFFLE_SET,
    ("media_player", "set_volume_level"): MediaPlayerEntityFeature.VOLUME_SET,
    ("media_player", "turn_off"): MediaPlayerEntityFeature.TURN_OFF,
    ("media_player", "turn_on"): MediaPlayerEntityFeature.TURN_ON,
    ("siren", "turn_off"): SirenEntityFeature.TURN_OFF,
    ("siren", "turn_on"): SirenEntityFeature.TURN_ON,
    ("update", "install"): UpdateEntityFeature.INSTALL,
    ("vacuum", "clean_spot"): VacuumEntityFeature.CLEAN_SPOT,
    ("vacuum", "locate"): VacuumEntityFeature.LOCATE,
    ("vacuum", "pause"): VacuumEntityFeature.PAUSE,
    ("vacuum", "return_to_base"): VacuumEntityFeature.RETURN_HOME,
    ("vacuum", "send_command"): VacuumEntityFeature.SEND_COMMAND,
    ("vacuum", "set_fan_speed"): VacuumEntityFeature.FAN_SPEED,
    ("vacuum", "start"): VacuumEntityFeature.START,
    ("vacuum", "stop"): VacuumEntityFeature.STOP,
    ("valve", "close_valve"): ValveEntityFeature.CLOSE,
    ("valve", "open_valve"): ValveEntityFeature.OPEN,
    ("valve", "set_valve_position"): ValveEntityFeature.SET_POSITION,
    ("valve", "stop_valve"): ValveEntityFeature.STOP,
    ("water_heater", "set_operation_mode"): (
        WaterHeaterEntityFeature.OPERATION_MODE
    ),
    ("water_heater", "set_temperature"): (
        WaterHeaterEntityFeature.TARGET_TEMPERATURE
    ),
    ("water_heater", "turn_away_mode_off"): WaterHeaterEntityFeature.AWAY_MODE,
    ("water_heater", "turn_away_mode_on"): WaterHeaterEntityFeature.AWAY_MODE,
    ("water_heater", "turn_off"): WaterHeaterEntityFeature.ON_OFF,
    ("water_heater", "turn_on"): WaterHeaterEntityFeature.ON_OFF,
}

_SOURCE_COMMAND_CAPABILITY_ATTRIBUTES = {
    ("climate", "set_fan_mode"): ("fan_modes",),
    ("climate", "set_preset_mode"): ("preset_modes",),
    ("climate", "set_swing_horizontal_mode"): ("swing_horizontal_modes",),
    ("climate", "set_swing_mode"): ("swing_modes",),
    ("fan", "set_preset_mode"): ("preset_modes",),
    ("humidifier", "set_mode"): ("available_modes", "modes"),
    ("media_player", "select_sound_mode"): ("sound_mode_list",),
    ("media_player", "select_source"): ("source_list",),
    ("vacuum", "set_fan_speed"): ("fan_speed_list",),
    ("water_heater", "set_operation_mode"): ("operation_list",),
}

_SOURCE_STEPPED_COMMANDS = {
    ("climate", "set_humidity"): (
        "humidity", "min_humidity", "max_humidity", "target_humidity_step",
        0, 100, 1,
    ),
    ("climate", "set_temperature"): (
        "temperature", "min_temp", "max_temp", "target_temp_step",
        7, 35, 0.1,
    ),
    ("humidifier", "set_humidity"): (
        "humidity", "min_humidity", "max_humidity", "target_humidity_step",
        0, 100, 1,
    ),
    ("media_player", "set_volume_level"): (
        "volume_level", None, None, "volume_step", 0, 1, 0.05,
    ),
    ("number", "set_native_value"): (
        "value", "min", "max", "step", 0, 100, 1,
    ),
    ("water_heater", "set_temperature"): (
        "temperature", "min_temp", "max_temp", "target_temp_step",
        7, 35, 1,
    ),
}


def _source_command_data_template(
    platform: str,
    command: str,
    entity_id: str,
) -> str | None:
    """Return a dynamic payload fixer for a source's advertised value grid."""
    if platform == "fan" and command in {"set_percentage", "turn_on"}:
        step = f"state_attr({entity_id!r}, 'percentage_step') | float(1)"
        return (
            "{% set requested = command_data.get('percentage') %}"
            "{% set step = " + step + " %}"
            "{% if requested is number and step > 0 and requested > 0 %}"
            "{% set fixed = [100, (((requested / step) + 0.5) | int) * step] | min %}"
            "{% set fixed = [step, fixed] | max %}"
            "{{ dict(command_data, percentage=(fixed | round(0) | int)) }}"
            "{% else %}{{ command_data }}{% endif %}"
        )

    spec = _SOURCE_STEPPED_COMMANDS.get((platform, command))
    if spec is None:
        return None
    field, min_attr, max_attr, step_attr, default_min, default_max, default_step = spec
    minimum = (
        f"state_attr({entity_id!r}, {min_attr!r}) | float({default_min!r})"
        if min_attr
        else repr(default_min)
    )
    maximum = (
        f"state_attr({entity_id!r}, {max_attr!r}) | float({default_max!r})"
        if max_attr
        else repr(default_max)
    )
    step = f"state_attr({entity_id!r}, {step_attr!r}) | float({default_step!r})"
    return (
        f"{{% set requested = command_data.get({field!r}) %}}"
        f"{{% set minimum = {minimum} %}}"
        f"{{% set maximum = {maximum} %}}"
        f"{{% set step = {step} %}}"
        "{% if requested is number and step > 0 and maximum >= minimum %}"
        "{% set bounded = [minimum, [maximum, requested] | min] | max %}"
        "{% set fixed = minimum + (((((bounded - minimum) / step) + 0.5) | int) * step) %}"
        "{% set fixed = [minimum, [maximum, fixed] | min] | max %}"
        f"{{{{ dict(command_data, {field}=fixed) }}}}"
        "{% else %}{{ command_data }}{% endif %}"
    )


def _source_supports_command(
    state,
    required_features,
    capability_attributes: tuple[str, ...] = (),
) -> bool:
    """Return whether a source state advertises a required command feature."""
    raw_features = state.attributes.get("supported_features", 0)
    if isinstance(raw_features, bool):
        return False
    try:
        supported_features = int(raw_features)
    except (TypeError, ValueError, OverflowError):
        return False
    if not supported_features & int(required_features):
        return False
    if not capability_attributes:
        return True
    return any(
        bool(state.attributes.get(attribute))
        for attribute in capability_attributes
    )


def _source_command_actions(
    platform: str,
    entity_ids: list[str],
    states: list,
) -> dict[str, Any]:
    """Build editable pass-through actions for compatible source entities."""
    actions = {}
    if len(entity_ids) == 1:
        source_entity_id = entity_ids[0]
        source_platform = source_entity_id.split(".", 1)[0]
        if (
            source_platform != platform
            and source_platform in CROSS_DOMAIN_POWER_SOURCE_DOMAINS
            and platform in CROSS_DOMAIN_POWER_TARGET_DOMAINS
        ):
            for command in ("turn_off", "turn_on"):
                if command not in VIRTUAL_ENTITY_COMMANDS.get(platform, ()):
                    continue
                actions[command] = [{
                    "action": f"{source_platform}.{command}",
                    "target": {ATTR_ENTITY_ID: source_entity_id},
                }]
            return actions
    for command in sorted(VIRTUAL_ENTITY_COMMANDS.get(platform, ())):
        key = (platform, command)
        if key in VIRTUAL_ENTITY_NON_SERVICE_COMMANDS:
            continue
        required_features = _SOURCE_COMMAND_FEATURES.get(key)
        capability_attributes = _SOURCE_COMMAND_CAPABILITY_ATTRIBUTES.get(key, ())
        source_entities = list(dict.fromkeys(
            entity_id
            for entity_id, state in zip(entity_ids, states, strict=True)
            if entity_id.startswith(f"{platform}.")
            and (
                required_features is None
                or _source_supports_command(
                    state,
                    required_features,
                    capability_attributes,
                )
            )
        ))
        if not source_entities:
            continue
        service = VIRTUAL_ENTITY_PROXY_SERVICE_OVERRIDES.get(key, command)
        fixed_data = {
            entity_id: _source_command_data_template(platform, command, entity_id)
            for entity_id in source_entities
        }
        if any(fixed_data.values()):
            actions[command] = [
                {
                    "action": f"{platform}.{service}",
                    "target": {ATTR_ENTITY_ID: entity_id},
                    "data": fixed_data[entity_id] or "{{ command_data }}",
                }
                for entity_id in source_entities
            ]
            continue
        target_entity_id: str | list[str]
        if len(source_entities) == 1:
            target_entity_id = source_entities[0]
        else:
            target_entity_id = source_entities
        actions[command] = [{
            "action": f"{platform}.{service}",
            "target": {ATTR_ENTITY_ID: target_entity_id},
            "data": "{{ command_data }}",
        }]
    return actions


def _reference_entity_defaults(
    hass,
    entity_ids,
    target_platform: str | None = None,
    additional_target_platforms: Collection[str] = (),
) -> dict[str, Any]:
    entity_ids = _normalize_reference_entity_ids(entity_ids)
    if not entity_ids:
        return {}

    _validate_mergeable_source_entities(entity_ids, CONF_REFERENCE_ENTITY_ID)

    states = _reference_states(hass, entity_ids)
    source_domains = [entity_id.split(".", 1)[0] for entity_id in entity_ids]
    all_boolean = all(
        _source_state_is_boolean(entity_id, state)
        for entity_id, state in zip(entity_ids, states, strict=True)
    )
    number_sources = [
        _source_state_is_number(entity_id, state)
        for entity_id, state in zip(entity_ids, states, strict=True)
    ]
    all_number = all(number_sources) or (
        sum(number_sources) >= 1
        and all(
            is_number
            or (
                entity_id.startswith("sensor.")
                and not _source_state_is_known(state)
            )
            for entity_id, state, is_number in zip(
                entity_ids, states, number_sources, strict=True
            )
        )
    )
    all_datetime = _all_source_domains(entity_ids, DATETIME_SOURCE_DOMAINS)
    all_date = _all_source_domains(entity_ids, DATE_SOURCE_DOMAINS)
    all_time = _all_source_domains(entity_ids, TIME_SOURCE_DOMAINS)
    all_enum = _all_source_domains(entity_ids, ENUM_SOURCE_DOMAINS)
    all_location = _all_source_domains(entity_ids, LOCATION_SOURCE_DOMAINS)
    boiler_profile = _boiler_source_profile(entity_ids, states)
    fan_number_profile = _xiaomi_fan_number_profile(entity_ids, states)
    humidifier_component_profile = _humidifier_component_profile(entity_ids)
    presence_or_motion_class = (
        _presence_or_motion_device_class(entity_ids, states) if all_boolean else None
    )
    safety_boolean_sources = (
        _safety_boolean_sources(entity_ids, states) if all_boolean else False
    )
    if boiler_profile is not None:
        platform = "climate"
    elif fan_number_profile is not None:
        platform = "fan"
    elif len(entity_ids) > 1 and all_location:
        platform = "device_tracker"
    elif (
        len(set(source_domains)) == 1
        and source_domains[0] in VIRTUAL_ENTITY_DOMAINS
    ):
        platform = source_domains[0]
    elif all_location:
        platform = "device_tracker"
    elif all_boolean:
        platform = "binary_sensor"
    elif all_datetime:
        platform = "datetime"
    elif all_date:
        platform = "date"
    elif all_time:
        platform = "time"
    elif all_enum:
        platform = "select"
    else:
        platform = "sensor"

    if target_platform is not None:
        allowed_target_platforms = set(
            _source_target_domains(entity_ids, platform)
        )
        allowed_target_platforms.update(
            candidate
            for candidate in additional_target_platforms
            if candidate in VIRTUAL_ENTITY_DOMAINS
        )
        if target_platform not in allowed_target_platforms:
            raise vol.Invalid("unsupported target entity type")
        platform = target_platform

    first_state = states[0]
    if boiler_profile is not None:
        climate_index, _hot_water_switch_id = boiler_profile
        initial_value = (
            "heat" if states[climate_index].state == "heat" else "off"
        )
    elif fan_number_profile is not None:
        initial_value = states[fan_number_profile[0]].state
    elif platform == "humidifier" and humidifier_component_profile is not None:
        initial_value = states[humidifier_component_profile["switch"]].state
    elif platform == "binary_sensor":
        if presence_or_motion_class:
            initial_value = (
                "on"
                if sum(_source_state_is_true(state) for state in states)
                > len(states) / 2
                else "off"
            )
        elif safety_boolean_sources:
            initial_value = (
                "on" if any(_source_state_is_true(state) for state in states) else "off"
            )
        else:
            initial_value = (
                "on" if all(_source_state_is_true(state) for state in states) else "off"
            )
    elif len(states) == 1:
        initial_value = first_state.state
    elif all_number:
        initial_value = _average_known_states(states)
    elif all_datetime:
        initial_value = _latest_datetime_state(states)
    elif all_date or all_time:
        initial_value = _latest_state(states)
    elif all_location:
        initial_value = _location_state(states)
    elif all_enum or (
        len(set(source_domains)) == 1
        and platform == source_domains[0]
        and platform != "sensor"
        and platform in DOMAIN_NATIVE_TEMPLATE_PROPERTIES
    ):
        initial_value = _first_known_state(states)
    else:
        initial_value = "".join(str(state.state) for state in states)

    attributes = {}
    if len(states) == 1 and not all_location:
        attributes = {
            name: _json_safe(value)
            for name, value in dict(first_state.attributes).items()
            if name != ATTR_FRIENDLY_NAME
            and name != CONF_ICON
            and name not in TRANSIENT_SOURCE_ATTRIBUTE_NAMES
            and name not in RESERVED_VIRTUAL_ATTRIBUTE_NAMES
        }
    entity_name_states = (
        [states[boiler_profile[0]]]
        if boiler_profile is not None and len(states) > 1
        else [states[fan_number_profile[0]]]
        if fan_number_profile is not None
        else states
    )
    defaults = {
        CONF_DEVICE_NAME: _combined_device_name(hass, entity_ids),
        CONF_ENTITY_NAME: _combined_entity_name(entity_name_states),
        CONF_PLATFORM: platform,
        CONF_INITIAL_VALUE: initial_value,
        CONF_SOURCE_ENTITIES_TEXT: "\n".join(entity_ids),
        CONF_AVAILABILITY_TEMPLATE: (
            "{{ "
            + " and ".join(
                f"states({entity_id!r}) not in ['unknown', 'unavailable']"
                for entity_id in entity_ids
            )
            + " }}"
        ),
    }
    if platform == "light":
        source_modes = set(first_state.attributes.get("supported_color_modes", []))
        defaults[CONF_MATTER_LIGHT_TYPE] = (
            "extended_color"
            if source_modes & {"hs", "xy"}
            else "color_temperature"
            if "color_temp" in source_modes
            else "dimmable"
            if (
                "brightness" in source_modes
                or source_modes - {"onoff"}
                or "brightness" in first_state.attributes
            )
            else "on_off"
        )
    if fan_number_profile is not None:
        fan_index, number_index = fan_number_profile
        defaults[CONF_AVAILABILITY_TEMPLATE] = _xiaomi_fan_availability_template(
            entity_ids[fan_index],
            entity_ids[number_index],
        )
    generated_entity_id = _default_virtual_entity_id_for_sources(
        platform,
        defaults[CONF_ENTITY_NAME],
        entity_ids,
    )
    if generated_entity_id != _default_virtual_entity_id(
        platform,
        defaults[CONF_ENTITY_NAME],
    ):
        # Copying an existing virtual entity commonly preserves its friendly
        # name. Avoid making the new form self-reference its source before the
        # user has had a chance to customize the generated ID.
        defaults[ATTR_ENTITY_ID] = generated_entity_id
    if boiler_profile is not None:
        climate_entity_id = entity_ids[boiler_profile[0]]
        defaults[CONF_ICON_TEMPLATE] = (
            f"{{{{ state_attr({climate_entity_id!r}, {CONF_ICON!r}) "
            "| default('', true) }}"
        )
    elif fan_number_profile is not None:
        fan_entity_id = entity_ids[fan_number_profile[0]]
        defaults[CONF_ICON_TEMPLATE] = (
            f"{{{{ state_attr({fan_entity_id!r}, {CONF_ICON!r}) "
            "| default('', true) }}"
        )
    elif len(entity_ids) == 1:
        defaults[CONF_ICON_TEMPLATE] = (
            f"{{{{ state_attr({entity_ids[0]!r}, {CONF_ICON!r}) "
            "| default('', true) }}"
        )
    elif entity_ids:
        defaults[CONF_ICON_TEMPLATE] = (
            "{% set icons = ["
            + ", ".join(
                f"state_attr({entity_id!r}, {CONF_ICON!r})"
                for entity_id in entity_ids
            )
            + "] | reject('in', [none, '']) | list %}"
            "{{ icons[0] if icons else '' }}"
        )
    source_device_classes = {
        str(state.attributes.get("device_class", "")).lower() for state in states
    }
    source_units = {
        str(state.attributes.get(CONF_UNIT_OF_MEASUREMENT, "")) for state in states
    }
    if (
        platform == "sensor"
        and len(source_device_classes) == 1
        and "" not in source_device_classes
    ):
        domain_options = {CONF_CLASS: next(iter(source_device_classes))}
        if len(source_units) == 1 and "" not in source_units:
            domain_options[CONF_UNIT_OF_MEASUREMENT] = next(iter(source_units))
        defaults[CONF_DOMAIN_OPTIONS_JSON] = _json_default(domain_options)
    elif platform == "climate" and (
        len(states) == 1 or boiler_profile is not None
    ):
        climate_index = boiler_profile[0] if boiler_profile is not None else 0
        climate_attributes = {
            name: _json_safe(value)
            for name, value in dict(states[climate_index].attributes).items()
            if name != ATTR_FRIENDLY_NAME
            and name != CONF_ICON
            and name not in TRANSIENT_SOURCE_ATTRIBUTE_NAMES
            and name not in RESERVED_VIRTUAL_ATTRIBUTE_NAMES
        }
        domain_options, consumed_attributes = extract_climate_options(
            climate_attributes
        )
        if boiler_profile is not None:
            domain_options["hvac_modes"] = ["off", "heat"]
        defaults.update(
            {
                key: value
                for key, value in domain_options.items()
                if key in CLIMATE_FORM_FIELDS
            }
        )
        advanced_domain_options = {
            key: value
            for key, value in domain_options.items()
            if key not in CLIMATE_FORM_FIELDS
        }
        if advanced_domain_options:
            defaults[CONF_DOMAIN_OPTIONS_JSON] = _json_default(
                advanced_domain_options,
            )
        if len(states) == 1:
            attributes = {
                key: value
                for key, value in attributes.items()
                if key not in consumed_attributes
            }
    elif platform == "fan" and (
        len(states) == 1 or fan_number_profile is not None
    ):
        fan_state = (
            states[fan_number_profile[0]]
            if fan_number_profile is not None
            else states[0]
        )
        fan_attributes = {
            name: _json_safe(value)
            for name, value in dict(fan_state.attributes).items()
            if name != ATTR_FRIENDLY_NAME
            and name != CONF_ICON
            and name not in TRANSIENT_SOURCE_ATTRIBUTE_NAMES
            and name not in RESERVED_VIRTUAL_ATTRIBUTE_NAMES
        }
        domain_options, consumed_attributes = extract_fan_options(fan_attributes)
        defaults.update(domain_options)
        attributes = {
            key: value
            for key, value in attributes.items()
            if key not in consumed_attributes
        }
    elif platform == "humidifier" and len(states) == 1:
        domain_options, consumed_attributes = extract_humidifier_options(attributes)
        defaults.update(domain_options)
        attributes = {
            key: value
            for key, value in attributes.items()
            if key not in consumed_attributes
        }

    if attributes:
        defaults[CONF_ATTRIBUTES_JSON] = _json_default(attributes)

    variable_names: list[str] = []
    template_sources: dict[str, str] = {}
    existing_variables: set[str] = set()
    for entity_id in entity_ids:
        variable_name = _source_variable_name(entity_id, existing_variables)
        variable_names.append(variable_name)
        template_sources[variable_name] = entity_id

    defaults[CONF_TEMPLATE_SOURCES_JSON] = _json_default(template_sources)
    if platform == "camera" and len(entity_ids) == 1 and source_domains[0] == "camera":
        defaults[CONF_DOMAIN_OPTIONS_JSON] = _json_default(
            {
                CAMERA_SOURCE_ENTITY_OPTION: entity_ids[0],
            }
        )
    attribute_templates: dict[str, str] = {}
    source_command_actions = (
        _humidifier_component_command_actions(
            entity_ids,
            humidifier_component_profile,
        )
        if platform == "humidifier" and humidifier_component_profile is not None
        else _source_command_actions(platform, entity_ids, states)
    )
    if fan_number_profile is not None:
        fan_index, number_index = fan_number_profile
        speed_kind = _fan_number_speed_kind(
            entity_ids[number_index],
            states[number_index],
        )
        source_command_actions = _xiaomi_fan_command_actions(
            entity_ids[fan_index],
            entity_ids[number_index],
            speed_kind,
            source_command_actions,
        )
    if source_command_actions:
        defaults[CONF_COMMAND_ACTIONS_JSON] = _json_default(
            source_command_actions,
        )
    if boiler_profile is not None:
        climate_index, hot_water_switch_id = boiler_profile
        defaults[CONF_VALUE_TEMPLATE] = _boiler_mode_template(
            entity_ids[climate_index]
        )
        defaults[CONF_COMMAND_ACTIONS_JSON] = _json_default(
            _boiler_command_actions(
                entity_ids[climate_index],
                states[climate_index],
                hot_water_switch_id,
            )
        )
    elif platform == "binary_sensor" and presence_or_motion_class:
        defaults[CONF_DOMAIN_OPTIONS_JSON] = _json_default(
            {
                CONF_CLASS: presence_or_motion_class,
            }
        )
        defaults[CONF_VALUE_TEMPLATE] = _presence_motion_helper_template(
            entity_ids,
            variable_names,
        )
    elif platform == "binary_sensor" and safety_boolean_sources:
        defaults[CONF_VALUE_TEMPLATE] = _safety_boolean_helper_template(variable_names)
    elif all_location and platform == "device_tracker":
        # Device tracker coordinates need stateful priority retention after an
        # outlying device reaches its destination. The platform helper performs
        # that calculation and keeps this policy visible/editable in the UI.
        defaults[CONF_DOMAIN_OPTIONS_JSON] = _json_default(
            {
                CONF_LOCATION_HELPER: {
                    "distance_threshold_meters": LOCATION_HELPER_DISTANCE_METERS,
                    "priority_window_seconds": LOCATION_HELPER_PRIORITY_WINDOW_SECONDS,
                },
            }
        )
        defaults[CONF_VALUE_TEMPLATE] = ""
    elif fan_number_profile is not None:
        defaults[CONF_VALUE_TEMPLATE] = (
            f"{{{{ {variable_names[fan_number_profile[0]]} }}}}"
        )
    elif platform == "humidifier" and humidifier_component_profile is not None:
        defaults[CONF_VALUE_TEMPLATE] = (
            f"{{{{ {variable_names[humidifier_component_profile['switch']]} }}}}"
        )
    elif len(entity_ids) == 1:
        defaults[CONF_VALUE_TEMPLATE] = f"{{{{ {variable_names[0]} }}}}"
    elif all_boolean and platform in BOOLEAN_SOURCE_DOMAINS | {"binary_sensor"}:
        boolean_checks = [
            f"(({variable_name} | lower) in ['1', 'on', 'open', 'true', 'unlocked', 'yes'])"
            for variable_name in variable_names
        ]
        defaults[CONF_VALUE_TEMPLATE] = "{{ " + " and ".join(boolean_checks) + " }}"
    elif all_number:
        defaults[CONF_VALUE_TEMPLATE] = _robust_average_helper_template(
            variable_names,
            "'unknown'",
        )
        defaults[CONF_AVAILABILITY_TEMPLATE] = (
            "{{ (["
            + ", ".join(
                f"states({entity_id!r}) | is_number" for entity_id in entity_ids
            )
            + "] | select | list | count) > 0 }}"
        )
    elif all_datetime:
        defaults[CONF_VALUE_TEMPLATE] = _latest_datetime_helper_template(variable_names)
    elif all_date or all_time:
        defaults[CONF_VALUE_TEMPLATE] = (
            "{{ ["
            + ", ".join(variable_names)
            + "] | reject('in', ['unknown', 'unavailable', 'none', '', none]) | list | sort | last | default('unknown') }}"
        )
    elif all_enum or len(set(source_domains)) == 1 and (
        source_domains[0] in FIRST_KNOWN_STATE_SOURCE_DOMAINS
    ):
        defaults[CONF_VALUE_TEMPLATE] = (
            "{% set values = ["
            + ", ".join(variable_names)
            + "] | reject('in', ['unknown', 'unavailable', 'none', '', none]) | list %}"
            "{{ values[0] if values else 'unknown' }}"
        )
    else:
        defaults[CONF_VALUE_TEMPLATE] = (
            "{% set values = ["
            + ", ".join(variable_names)
            + "] | reject('in', ['unknown', 'unavailable', 'none', '', none]) | list %}"
            "{{ values | join('') }}"
        )
        attribute_templates.update(
            {
                variable_name: f"{{{{ {variable_name} }}}}"
                for variable_name in variable_names
            }
        )

    attribute_source_ids = entity_ids
    attribute_source_states = states
    if fan_number_profile is not None:
        fan_index, _number_index = fan_number_profile
        attribute_source_ids = [entity_ids[fan_index]]
        attribute_source_states = [states[fan_index]]
    generated_attribute_templates = _attribute_reference_templates(
        platform,
        attribute_source_ids,
        attribute_source_states,
    )
    if platform == "humidifier" and humidifier_component_profile is not None:
        for attribute_name in {
            "device_class",
            "humidity",
            "max",
            "min",
            "mode",
            "options",
            "step",
            "unit_of_measurement",
        }:
            generated_attribute_templates.pop(attribute_name, None)
    for attribute_name, template in generated_attribute_templates.items():
        attribute_templates.setdefault(attribute_name, template)
    if attribute_templates:
        defaults[CONF_ATTRIBUTE_TEMPLATES_JSON] = _json_default(attribute_templates)

    if boiler_profile is not None:
        climate_index, _hot_water_switch_id = boiler_profile
        native_templates = _native_reference_templates(
            platform,
            [entity_ids[climate_index]],
            [states[climate_index]],
        )
        native_templates.update({
            "hvac_modes": "{{ ['off', 'heat'] }}",
            "hvac_mode": _boiler_mode_template(entity_ids[climate_index]),
        })
    elif fan_number_profile is not None:
        fan_index, number_index = fan_number_profile
        speed_kind = _fan_number_speed_kind(
            entity_ids[number_index],
            states[number_index],
        )
        native_templates = _native_reference_templates(
            platform,
            [entity_ids[fan_index]],
            [states[fan_index]],
        )
        native_templates["percentage"] = _xiaomi_fan_percentage_template(
            entity_ids[fan_index],
            entity_ids[number_index],
            speed_kind,
        )
        native_templates["speed_count"] = _xiaomi_fan_speed_count_template(
            entity_ids[number_index],
            speed_kind,
        )
    elif platform == "humidifier" and humidifier_component_profile is not None:
        native_templates = _humidifier_component_native_templates(
            entity_ids,
            states,
            humidifier_component_profile,
        )
    else:
        native_templates = (
            {}
            if all_location and platform == "device_tracker"
            else _native_reference_templates(platform, entity_ids, states)
        )
    if platform in DOMAIN_NATIVE_TEMPLATE_PROPERTIES and not (
        all_location and platform == "device_tracker"
    ):
        defaults[CONF_NATIVE_VALUE_TEMPLATES] = _native_template_defaults(
            platform,
            {CONF_NATIVE_VALUE_TEMPLATES: native_templates},
        )

    return defaults


def _reference_edit_defaults(
    current_defaults: dict[str, Any],
    reference_defaults: dict[str, Any],
    auto_helper: Mapping | bool | None = None,
    *,
    force_template_helper: bool = False,
    source_entities_text: str | None = None,
) -> dict[str, Any]:
    if not reference_defaults and source_entities_text is None:
        return current_defaults

    merged = dict(current_defaults)
    # The source selector is authoritative even when the helper/template was
    # customized. Otherwise changing the selector silently keeps subscriptions
    # to entities that the user explicitly removed.
    merged[CONF_SOURCE_ENTITIES_TEXT] = (
        source_entities_text
        if source_entities_text is not None
        else reference_defaults.get(CONF_SOURCE_ENTITIES_TEXT, "")
    )
    if isinstance(auto_helper, Mapping):
        auto_profile = _auto_helper_profile(dict(auto_helper))
    elif auto_helper is True:
        auto_profile = _auto_helper_profile(current_defaults)
    else:
        auto_profile = None
    if auto_profile is None and not force_template_helper:
        return merged

    templates_are_generated = force_template_helper or (
        auto_profile is not None
        and _auto_helper_templates_match(current_defaults, auto_profile)
    )
    for field in _AUTO_HELPER_PROFILE_FIELDS:
        if field == CONF_SOURCE_ENTITIES_TEXT:
            continue
        if field == CONF_NATIVE_VALUE_TEMPLATES:
            merged[field] = _merge_native_helper_templates(
                current_defaults,
                reference_defaults,
                auto_profile,
                force_template_helper=force_template_helper,
            )
            continue
        if field == CONF_ATTRIBUTE_TEMPLATES_JSON:
            merged[field] = _merge_attribute_helper_templates(
                current_defaults,
                reference_defaults,
                auto_profile,
                force_template_helper=force_template_helper,
            )
            continue
        if field in _AUTO_HELPER_TEMPLATE_FIELDS:
            if templates_are_generated:
                merged[field] = reference_defaults.get(field, "")
            continue
        if field in _AUTO_HELPER_INDEPENDENT_TEMPLATE_FIELDS:
            current_value = _canonical_auto_helper_value(
                field,
                current_defaults.get(field, ""),
            )
            baseline_value = (
                auto_profile.get(field, "") if auto_profile is not None else ""
            )
            if force_template_helper or current_value == baseline_value:
                merged[field] = reference_defaults.get(field, "")
            continue
        if (
            reference_defaults
            and (
                force_template_helper
                or (
                    auto_profile is not None
                    and _canonical_auto_helper_value(
                        field,
                        current_defaults.get(
                            field,
                            [] if field in CLIMATE_MODE_LIST_FIELDS else "",
                        ),
                    )
                    == auto_profile.get(
                        field,
                        [] if field in CLIMATE_MODE_LIST_FIELDS else "",
                    )
                )
            )
        ):
            if field in reference_defaults:
                merged[field] = reference_defaults[field]
            else:
                merged.pop(field, None)

    old_entity_id = _text_default(current_defaults.get(ATTR_ENTITY_ID)).strip()
    new_platform = merged.get(CONF_PLATFORM)
    if old_entity_id and new_platform in VIRTUAL_ENTITY_DOMAINS:
        _, separator, object_id = old_entity_id.partition(".")
        if separator and object_id:
            merged[ATTR_ENTITY_ID] = f"{new_platform}.{object_id}"
    return merged


def _refresh_add_reference_defaults(
    hass,
    user_input: dict[str, Any],
    reference_defaults: dict[str, Any],
    *,
    use_template_helper: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Refresh untouched generated fields after sources change on an add form."""
    submitted_sources = _parse_source_entities(
        user_input.get(CONF_SOURCE_ENTITIES_TEXT, ""),
    )
    reference_sources = _stored_entity_ids(
        reference_defaults.get(CONF_SOURCE_ENTITIES_TEXT),
    )
    submitted_platform = user_input.get(CONF_PLATFORM)
    reference_platform = reference_defaults.get(CONF_PLATFORM)
    if submitted_sources == reference_sources and (
        len(submitted_sources) != 1
        or submitted_platform == reference_platform
    ):
        return user_input, reference_defaults

    _validate_mergeable_source_entities(
        submitted_sources,
        CONF_SOURCE_ENTITIES_TEXT,
    )
    try:
        if len(submitted_sources) == 1 and submitted_platform in VIRTUAL_ENTITY_DOMAINS:
            refreshed_reference_defaults = _reference_entity_defaults(
                hass,
                submitted_sources,
                submitted_platform,
                (submitted_platform,),
            )
        else:
            refreshed_reference_defaults = _reference_entity_defaults(
                hass,
                submitted_sources,
            )
    except InvalidEntityReference:
        # A syntactically valid future/unloaded source can still be saved, but
        # cannot provide state or attributes for helper generation yet.
        refreshed_reference_defaults = {}

    if use_template_helper:
        refreshed_input = _reference_edit_defaults(
            user_input,
            refreshed_reference_defaults,
            _auto_helper_profile(reference_defaults),
            source_entities_text="\n".join(submitted_sources),
        )
    else:
        refreshed_input = dict(user_input)
        refreshed_input[CONF_SOURCE_ENTITIES_TEXT] = "\n".join(submitted_sources)
    return refreshed_input, refreshed_reference_defaults


def _without_template_helpers(reference_defaults: Mapping) -> dict[str, Any]:
    """Keep copied source values while removing generated helper templates."""
    defaults = dict(reference_defaults)
    for field_name in (
        CONF_VALUE_TEMPLATE,
        CONF_AVAILABILITY_TEMPLATE,
        CONF_ICON_TEMPLATE,
        CONF_TEMPLATE_SOURCES_JSON,
        CONF_ATTRIBUTE_TEMPLATES_JSON,
        CONF_NATIVE_TEMPLATES_JSON,
        CONF_NATIVE_VALUE_TEMPLATES,
        CONF_COMMAND_ACTIONS_JSON,
    ):
        defaults.pop(field_name, None)

    domain_options_text = _text_default(
        defaults.get(CONF_DOMAIN_OPTIONS_JSON),
    ).strip()
    if domain_options_text:
        try:
            domain_options = _parse_domain_options(domain_options_text)
        except InvalidJson:
            domain_options = None
        if isinstance(domain_options, dict) and CONF_LOCATION_HELPER in domain_options:
            domain_options.pop(CONF_LOCATION_HELPER, None)
            if domain_options:
                defaults[CONF_DOMAIN_OPTIONS_JSON] = _json_default(domain_options)
            else:
                defaults.pop(CONF_DOMAIN_OPTIONS_JSON, None)
    return defaults


def _attribute_template_mapping(value: Any) -> dict[str, str]:
    """Normalize attribute helper JSON without trusting legacy stored data."""
    if isinstance(value, str):
        if not value.strip():
            return {}
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return {}
    if not isinstance(value, Mapping):
        return {}
    return {
        str(attribute_name): repair_legacy_enum_template(template)
        for attribute_name, template in _plain_options(value).items()
        if isinstance(attribute_name, str)
        and attribute_name.strip()
        and isinstance(template, str)
        and template.strip()
    }


def _merge_attribute_helper_templates(
    current_defaults: Mapping,
    reference_defaults: Mapping,
    auto_profile: Mapping | None,
    *,
    force_template_helper: bool,
) -> str:
    """Refresh generated attribute helpers while preserving per-key edits."""
    current = _attribute_template_mapping(
        current_defaults.get(CONF_ATTRIBUTE_TEMPLATES_JSON),
    )
    generated = _attribute_template_mapping(
        reference_defaults.get(CONF_ATTRIBUTE_TEMPLATES_JSON),
    )
    baseline = _attribute_template_mapping(
        auto_profile.get(CONF_ATTRIBUTE_TEMPLATES_JSON) if auto_profile else None,
    )

    if force_template_helper:
        return _json_default(generated)

    merged = {}
    for attribute_name in current.keys() | generated.keys() | baseline.keys():
        current_value = current.get(attribute_name, "")
        if current_value == baseline.get(attribute_name, ""):
            next_value = generated.get(attribute_name, "")
        else:
            next_value = current_value
        if next_value:
            merged[attribute_name] = next_value
    return _json_default(merged)


def _native_template_mapping(value: Any) -> dict[str, str]:
    """Normalize editable native helper templates into a plain mapping."""
    if not isinstance(value, Mapping):
        return {}
    return {
        str(property_name): repair_legacy_enum_template(template)
        for property_name, template in _plain_options(value).items()
        if isinstance(template, str)
    }


def _merge_native_helper_templates(
    current_defaults: Mapping,
    reference_defaults: Mapping,
    auto_profile: Mapping | None,
    *,
    force_template_helper: bool,
) -> dict[str, str]:
    """Refresh generated native templates while preserving per-field edits."""
    platform = reference_defaults.get(
        CONF_PLATFORM,
        current_defaults.get(CONF_PLATFORM),
    )
    properties = DOMAIN_NATIVE_TEMPLATE_PROPERTIES.get(platform, ())
    current = _native_template_mapping(
        current_defaults.get(CONF_NATIVE_VALUE_TEMPLATES),
    )
    generated = _native_template_mapping(
        reference_defaults.get(CONF_NATIVE_VALUE_TEMPLATES),
    )
    baseline = _native_template_mapping(
        auto_profile.get(CONF_NATIVE_VALUE_TEMPLATES) if auto_profile else None,
    )

    merged = {}
    for property_name in properties:
        current_value = current.get(property_name, "")
        if force_template_helper or current_value == baseline.get(property_name, ""):
            next_value = generated.get(property_name, "")
        else:
            next_value = current_value
        if next_value:
            merged[property_name] = next_value
    return merged


def _auto_helper_field_default(field: str) -> Any:
    """Return the canonical empty value for an auto-helper field."""
    if field in CLIMATE_MODE_LIST_FIELDS:
        return []
    if field == CONF_NATIVE_VALUE_TEMPLATES:
        return {}
    return ""


def _canonical_auto_helper_value(field: str, value: Any) -> Any:
    """Normalize helper values that round-trip through entity storage."""
    value = _plain_options(value)
    if field == CONF_SOURCE_ENTITIES_TEXT:
        try:
            return "\n".join(_parse_source_entities(value))
        except (AttributeError, InvalidEntityReference):
            return value

    if field == CONF_TEMPLATE_SOURCES_JSON:
        try:
            return _json_default(_parse_template_sources(value))
        except (AttributeError, InvalidJson):
            return value

    if field == CONF_NATIVE_VALUE_TEMPLATES:
        return _native_template_mapping(value)

    if field in _AUTO_HELPER_JSON_FIELDS:
        try:
            parsed = _parse_json_value(value, field)
        except (AttributeError, InvalidJson):
            return value
        return _json_default(parsed)

    return value


def _auto_helper_profile(defaults: dict[str, Any]) -> dict[str, Any]:
    """Create a stable record of the generated helper fields."""
    return {
        field: _canonical_auto_helper_value(
            field,
            defaults.get(field, _auto_helper_field_default(field)),
        )
        for field in _AUTO_HELPER_PROFILE_FIELDS
    }


def _auto_helper_templates_match(
    defaults: dict[str, Any],
    profile: Mapping,
) -> bool:
    """Return whether the editable templates still match a helper baseline."""
    normalized_profile = _auto_helper_profile(dict(profile))
    return all(
        _canonical_auto_helper_value(
            field,
            defaults.get(field, _auto_helper_field_default(field)),
        )
        == normalized_profile.get(field, _auto_helper_field_default(field))
        for field in _AUTO_HELPER_TEMPLATE_FIELDS
    )


def _template_source_entity_ids(entity: Mapping) -> list[str]:
    """Extract source IDs in their stored template-variable order."""
    template_sources = entity.get(CONF_TEMPLATE_SOURCES)
    if not isinstance(template_sources, Mapping):
        return []

    entity_ids = []
    for source in template_sources.values():
        if isinstance(source, str):
            entity_id = source
        elif isinstance(source, Mapping):
            entity_id = source.get(ATTR_ENTITY_ID)
        else:
            continue
        if not isinstance(entity_id, str):
            continue
        try:
            entity_ids.append(cv.entity_id(entity_id.strip()))
        except vol.Invalid:
            continue
    return list(dict.fromkeys(entity_ids))


def _template_source_entity_ids_by_usage(
    entity: Mapping,
    defaults: Mapping,
) -> list[str]:
    """Recover source order from variable usage when JSON keys were sorted."""
    template_sources = entity.get(CONF_TEMPLATE_SOURCES)
    value_template = defaults.get(CONF_VALUE_TEMPLATE)
    if not isinstance(template_sources, Mapping) or not isinstance(value_template, str):
        return []

    ranked_sources = []
    for index, (variable_name, source) in enumerate(template_sources.items()):
        if not isinstance(variable_name, str):
            continue
        if isinstance(source, str):
            entity_id = source
        elif isinstance(source, Mapping):
            entity_id = source.get(ATTR_ENTITY_ID)
        else:
            continue
        if not isinstance(entity_id, str):
            continue
        try:
            entity_id = cv.entity_id(entity_id.strip())
        except vol.Invalid:
            continue
        match = re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(variable_name)}(?![A-Za-z0-9_])",
            value_template,
        )
        if match is None:
            return []
        ranked_sources.append((match.start(), index, entity_id))

    if not ranked_sources:
        return []
    ranked_sources.sort()
    return list(dict.fromkeys(item[2] for item in ranked_sources))


def _legacy_auto_helper_profiles(
    reference_defaults: dict[str, Any],
    source_entities: list[str],
) -> list[dict[str, Any]]:
    """Return exact helper formats generated by older Virtual Layer releases."""
    if (
        len(source_entities) < 2
        or reference_defaults.get(CONF_PLATFORM) != "binary_sensor"
    ):
        return []

    existing_variables: set[str] = set()
    variable_names = [
        _source_variable_name(entity_id, existing_variables)
        for entity_id in source_entities
    ]
    boolean_checks = [
        f"(({variable_name} | lower) in ['1', 'on', 'open', 'true', 'unlocked', 'yes'])"
        for variable_name in variable_names
    ]
    legacy_defaults = dict(reference_defaults)
    legacy_defaults[CONF_VALUE_TEMPLATE] = "{{ " + " or ".join(boolean_checks) + " }}"
    return [_auto_helper_profile(legacy_defaults)]


def _existing_auto_helper_profile(
    hass, entity, defaults: dict[str, Any]
) -> dict[str, Any] | None:
    """Return the generated baseline used to detect per-field customization."""
    saved_profile = entity.get(CONF_AUTO_HELPER)
    if isinstance(saved_profile, Mapping):
        primary_profile = _auto_helper_profile(_plain_options(saved_profile))
    elif saved_profile is True:
        primary_profile = _auto_helper_profile(defaults)
    else:
        primary_profile = None

    # Recover entries left half-updated by older edit flows: source_entities
    # and auto_helper may already describe the new selection while the actual
    # templates still contain an untouched helper for the previous sources.
    candidate_source_lists = [
        _template_source_entity_ids_by_usage(entity, defaults),
        _template_source_entity_ids(entity),
        _stored_entity_ids(entity.get(CONF_SOURCE_ENTITIES)),
    ]
    checked_source_lists: set[tuple[str, ...]] = set()
    for source_entities in candidate_source_lists:
        source_key = tuple(source_entities)
        if not source_entities or source_key in checked_source_lists:
            continue
        checked_source_lists.add(source_key)
        try:
            reference_defaults = _reference_entity_defaults(hass, source_entities)
        except InvalidEntityReference:
            continue
        candidates = [
            _auto_helper_profile(reference_defaults),
            *_legacy_auto_helper_profiles(reference_defaults, source_entities),
        ]
        for candidate in candidates:
            if _auto_helper_templates_match(defaults, candidate):
                return candidate

    return primary_profile


def _set_auto_helper_profile(
    entity: dict[str, Any],
    _submitted_defaults: dict[str, Any],
    reference_defaults: dict[str, Any],
    current_profile: dict[str, Any] | None = None,
) -> None:
    """Persist the generated baseline for later per-field customization checks."""
    if not reference_defaults and current_profile is None:
        return
    expected_profile = (
        _auto_helper_profile(reference_defaults)
        if reference_defaults
        else current_profile
    )
    if expected_profile:
        entity[CONF_AUTO_HELPER] = expected_profile


def _entity_form_defaults(
    device_name: str,
    entity: dict[str, Any],
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    entity = _plain_options(entity)
    for field_name in (
        CONF_ATTRIBUTES,
        CONF_ATTRIBUTE_SOURCES,
        CONF_ATTRIBUTE_TEMPLATES,
    ):
        field_value = entity.get(field_name)
        if isinstance(field_value, Mapping):
            cleaned = _without_transient_source_attributes(field_value)
            if field_name == CONF_ATTRIBUTE_TEMPLATES:
                cleaned = repair_legacy_template_data(cleaned)
            if cleaned:
                entity[field_name] = cleaned
            else:
                entity.pop(field_name, None)
    if entity.get(CONF_PLATFORM) == "climate":
        entity = migrate_legacy_climate_attributes(entity)
    elif entity.get(CONF_PLATFORM) == "fan":
        entity = migrate_legacy_fan_attributes(entity)
    elif entity.get(CONF_PLATFORM) == "humidifier":
        entity = migrate_legacy_humidifier_attributes(entity)
    device = _get_device_attributes(options or {}, device_name)
    platform = entity.get(CONF_PLATFORM, DEFAULT_ENTITY_DOMAIN)
    if platform not in VIRTUAL_ENTITY_DOMAINS:
        platform = DEFAULT_ENTITY_DOMAIN
    stored_native_templates = _native_template_mapping(
        entity.get(CONF_NATIVE_TEMPLATES)
    )
    managed_native_properties = set(
        DOMAIN_NATIVE_TEMPLATE_PROPERTIES.get(platform, ())
    )
    native_value_templates = {
        property_name: template_value
        for property_name, template_value in stored_native_templates.items()
        if property_name in managed_native_properties
        and isinstance(template_value, str)
        and template_value
    }
    additional_native_templates = {
        property_name: template_value
        for property_name, template_value in stored_native_templates.items()
        if property_name not in managed_native_properties
    }
    defaults = {
        CONF_DEVICE_NAME: _device_display_name(device_name, device),
        CONF_DEVICE_ID: _text_default(device.get(ATTR_DEVICE_ID), device_name),
        CONF_DEVICE_MANUFACTURER: _text_default(device.get(CONF_MANUFACTURER)),
        CONF_DEVICE_MODEL: _text_default(device.get(CONF_MODEL)),
        CONF_DEVICE_SW_VERSION: _text_default(device.get(CONF_SW_VERSION)),
        CONF_DEVICE_HW_VERSION: _text_default(device.get(CONF_HW_VERSION)),
        CONF_DEVICE_SERIAL_NUMBER: _text_default(device.get(CONF_SERIAL_NUMBER)),
        CONF_DEVICE_CONFIGURATION_URL: _text_default(
            device.get(CONF_CONFIGURATION_URL)
        ),
        CONF_DEVICE_SUGGESTED_AREA: _text_default(device.get(CONF_SUGGESTED_AREA)),
        CONF_DEVICE_VIA_DEVICE_ID: _text_default(device.get(CONF_VIA_DEVICE_ID)),
        CONF_ENTITY_NAME: _text_default(entity.get(CONF_NAME), "Virtual Entity"),
        CONF_ICON: _text_default(entity.get(CONF_ICON)),
        CONF_ICON_TEMPLATE: repair_legacy_enum_template(
            _text_default(entity.get(CONF_ICON_TEMPLATE))
        ),
        ATTR_ENTITY_ID: _text_default(entity.get(ATTR_ENTITY_ID)),
        CONF_PLATFORM: platform,
        CONF_INITIAL_VALUE: _text_default(
            entity.get(CONF_INITIAL_VALUE),
            DEFAULT_ENTITY_VALUE,
        ),
        CONF_INITIAL_AVAILABILITY: _boolean_default(
            entity.get(CONF_INITIAL_AVAILABILITY, True),
            True,
        ),
        CONF_PERSISTENT: _boolean_default(entity.get(CONF_PERSISTENT, True), True),
        CONF_SOURCE_ENTITIES_TEXT: _multiline_list_default(
            entity.get(CONF_SOURCE_ENTITIES),
        ),
        CONF_TEMPLATE_SOURCES_JSON: _json_default(entity.get(CONF_TEMPLATE_SOURCES)),
        CONF_PULL_INTERVAL: _nonnegative_int_default(entity.get(CONF_PULL_INTERVAL)),
        CONF_VALUE_TEMPLATE: repair_legacy_enum_template(
            _text_default(entity.get(CONF_VALUE_TEMPLATE))
        ),
        CONF_AVAILABILITY_TEMPLATE: repair_legacy_enum_template(
            _text_default(entity.get(CONF_AVAILABILITY_TEMPLATE))
        ),
        CONF_EVENT_HOOKS_JSON: _json_default(
            repair_legacy_template_data(entity.get(CONF_EVENT_HOOKS))
        ),
        CONF_ATTRIBUTES_JSON: _json_default(entity.get(CONF_ATTRIBUTES)),
        CONF_ATTRIBUTE_SOURCES_JSON: _json_default(entity.get(CONF_ATTRIBUTE_SOURCES)),
        CONF_ATTRIBUTE_TEMPLATES_JSON: _json_default(
            entity.get(CONF_ATTRIBUTE_TEMPLATES)
        ),
        CONF_NATIVE_TEMPLATES_JSON: _json_default(additional_native_templates),
        CONF_NATIVE_VALUE_TEMPLATES: native_value_templates,
        CONF_COMMAND_ACTIONS_JSON: _json_default(
            repair_legacy_template_data(entity.get(CONF_COMMAND_ACTIONS))
        ),
    }
    polygon = entity.get(CONF_POLYGONAL_ZONE)
    if not isinstance(polygon, Mapping):
        polygon = {}
    defaults.update(
        {
            CONF_POLYGON_GEOJSON_JSON: _json_default(polygon.get(CONF_POLYGON_GEOJSON)),
            CONF_POLYGON_FILES_TEXT: _multiline_list_default(
                polygon.get(CONF_POLYGON_FILES),
            ),
            CONF_POLYGON_PERSON: _text_default(
                polygon.get(CONF_POLYGON_PERSON_ENTITY),
            ),
            CONF_POLYGON_STRATEGY_INPUT: (
                polygon.get(CONF_POLYGON_STRATEGY)
                if polygon.get(CONF_POLYGON_STRATEGY)
                in {
                    "majority",
                    "priority",
                    "latest",
                    "median",
                }
                else "majority"
            ),
            CONF_POLYGON_DISTANCE_INPUT: _positive_float_default(
                polygon.get(CONF_POLYGON_DISTANCE_METERS),
                300,
            ),
            CONF_POLYGON_TRACKER_RULES_JSON: _json_default(
                repair_legacy_template_data(
                    polygon.get(CONF_POLYGON_TRACKER_RULES)
                ),
            ),
            CONF_POLYGON_AWAY_STATE_INPUT: (
                _text_default(polygon.get(CONF_POLYGON_AWAY_STATE), "not_home")
                or "not_home"
            ),
        }
    )
    domain_options = {
        key: value
        for key, value in entity.items()
        if key not in _DOMAIN_OPTION_RESERVED_KEYS
    }
    if platform == "climate":
        defaults.update(
            {
                key: value
                for key, value in domain_options.items()
                if key in CLIMATE_FORM_FIELDS
            }
        )
        domain_options = {
            key: value
            for key, value in domain_options.items()
            if key not in CLIMATE_FORM_FIELDS
        }
    elif platform == "fan":
        defaults.update(
            {
                key: value
                for key, value in domain_options.items()
                if key in FAN_FORM_FIELDS
            }
        )
        domain_options = {
            key: value
            for key, value in domain_options.items()
            if key not in FAN_FORM_FIELDS
        }
    elif platform == "humidifier":
        defaults.update(
            {
                key: value
                for key, value in domain_options.items()
                if key in HUMIDIFIER_FORM_FIELDS
            }
        )
        domain_options = {
            key: value
            for key, value in domain_options.items()
            if key not in HUMIDIFIER_FORM_FIELDS
        }
    elif platform == "light":
        defaults[CONF_MATTER_LIGHT_TYPE] = domain_options.pop(
            CONF_MATTER_LIGHT_TYPE,
            "dimmable",
        )
    defaults[CONF_DOMAIN_OPTIONS_JSON] = _json_default(domain_options)
    return defaults


class _FlowErrors(dict[str, str]):
    """Config-flow errors that leave an actionable log record."""

    def __init__(self, flow: Any, step: str) -> None:
        super().__init__()
        self._flow = type(flow).__name__
        self._step = step
        entry = getattr(flow, "config_entry", None)
        self._entry_id = getattr(entry, "entry_id", None)

    def __setitem__(self, field: str, error: str) -> None:
        _LOGGER.error(
            "Virtual Layer config-flow validation error "
            "(flow=%s, step=%s, entry_id=%s, field=%s, error=%s)",
            self._flow,
            self._step,
            self._entry_id or "new",
            field,
            error,
        )
        super().__setitem__(field, error)

    def update(self, *args: Any, **kwargs: str) -> None:
        """Preserve logging when an initial error mapping is supplied."""
        values = dict(*args, **kwargs)
        for field, error in values.items():
            self[field] = error


def _flow_errors(flow: Any, step: str, initial: Mapping[str, str] | None = None):
    """Create a logging error mapping for a config-flow form."""
    errors = _FlowErrors(flow, step)
    if initial:
        errors.update(initial)
    return errors


def _log_unhandled_flow_errors(cls):
    """Log exceptions that would otherwise become an opaque UI error."""
    for name, method in list(vars(cls).items()):
        if not name.startswith("async_step_") or not inspect.iscoroutinefunction(
            method
        ):
            continue

        @wraps(method)
        async def logged_step(self, *args, __method=method, __name=name, **kwargs):
            try:
                return await __method(self, *args, **kwargs)
            except Exception:
                entry = getattr(self, "config_entry", None)
                user_input = args[0] if args else kwargs.get("user_input")
                input_keys = (
                    sorted(str(key) for key in user_input)
                    if isinstance(user_input, Mapping)
                    else []
                )
                _LOGGER.exception(
                    "Unhandled Virtual Layer config-flow error "
                    "(flow=%s, step=%s, entry_id=%s, input_keys=%s)",
                    type(self).__name__,
                    __name.removeprefix("async_step_"),
                    getattr(entry, "entry_id", None) or "new",
                    input_keys,
                )
                raise

        setattr(cls, name, logged_step)
    return cls


@_log_unhandled_flow_errors
class VirtualFlowHandler(config_entries.ConfigFlow, domain=COMPONENT_DOMAIN):
    """Virtual Layer config flow."""

    VERSION = 1

    def __init__(self) -> None:
        self._pending_data: dict[str, Any] | None = None
        self._pending_title: str | None = None
        self._entity_defaults: dict[str, Any] | None = None
        self._reference_defaults: dict[str, Any] = {}
        self._source_entities: list[str] = []
        self._add_use_template_helper = True

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Create the options flow."""
        return VirtualOptionsFlowHandler()

    async def validate_input(self, user_input, current_entry=None):
        group_name = _normalized_group_name(user_input.get(ATTR_GROUP_NAME, ""))
        for entry in self.hass.config_entries.async_entries(COMPONENT_DOMAIN):
            if current_entry and entry.entry_id == current_entry.entry_id:
                continue
            existing_group_name = str(entry.data.get(ATTR_GROUP_NAME, "")).strip()
            if existing_group_name == group_name:
                raise GroupNameAlreadyUsed

        if current_entry:
            return {
                "title": group_name,
                ATTR_GROUP_NAME: group_name,
            }

        for group in self.hass.data.get(COMPONENT_DOMAIN, {}):
            _LOGGER.debug(f"checking {group}")
            if str(group).strip() == group_name:
                raise GroupNameAlreadyUsed
        return {
            "title": group_name,
            ATTR_GROUP_NAME: group_name,
        }

    async def async_step_user(self, user_input=None):
        _LOGGER.debug("Starting Virtual Layer user configuration step")

        errors = _flow_errors(self, "user")
        if user_input is not None:
            try:
                info = await self.validate_input(user_input)
                self._pending_title = info["title"]
                self._pending_data = {
                    ATTR_GROUP_NAME: info[ATTR_GROUP_NAME],
                }
                if user_input.get(CONF_ADD_FIRST_ENTITY):
                    return await self.async_step_entity_source()

                return self.async_create_entry(
                    title=self._pending_title,
                    data=self._pending_data,
                    options={ATTR_DEVICES: {}, ATTR_DEVICE_ATTRIBUTES: {}},
                )
            except GroupNameAlreadyUsed:
                errors["base"] = "group_name_used"
            except MissingGroupName:
                errors[ATTR_GROUP_NAME] = "required"

        defaults = user_input or {}

        return self.async_show_form(
            step_id="user",
            data_schema=_setup_schema(defaults),
            errors=errors,
        )

    async def async_step_reconfigure(self, user_input=None):
        """Reconfigure group metadata."""
        entry = self._get_reconfigure_entry()
        errors = _flow_errors(self, "reconfigure")

        if user_input is not None:
            try:
                await self.validate_input(user_input, current_entry=entry)
                old_group_name = entry.data[ATTR_GROUP_NAME]
                new_group_name = _normalized_group_name(user_input[ATTR_GROUP_NAME])
                await _rename_meta_data(self.hass, old_group_name, new_group_name)
                return self.async_update_reload_and_abort(
                    entry,
                    title=new_group_name,
                    data_updates={
                        ATTR_GROUP_NAME: new_group_name,
                    },
                )
            except GroupNameAlreadyUsed:
                errors["base"] = "group_name_used"
            except MissingGroupName:
                errors[ATTR_GROUP_NAME] = "required"

        defaults = user_input or {
            ATTR_GROUP_NAME: entry.data[ATTR_GROUP_NAME],
        }

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_setup_schema(defaults, include_entity_toggle=False),
            errors=errors,
        )

    async def async_step_entity_source(self, user_input=None):
        """Choose an existing entity to prefill a new virtual entity."""
        errors = _flow_errors(self, "entity_source")
        if user_input is not None:
            try:
                self._source_entities = _normalize_reference_entity_ids(
                    user_input.get(CONF_REFERENCE_ENTITY_ID),
                )
                self._reference_defaults = _reference_entity_defaults(
                    self.hass,
                    self._source_entities,
                )
                if self._reference_defaults:
                    if len(self._source_entities) == 1 or _has_entity_type_choice(
                        self._source_entities,
                        self._reference_defaults[CONF_PLATFORM],
                    ):
                        return await self.async_step_entity_type()
                    return await self.async_step_entity_helper()
                self._entity_defaults = {}
                return await self.async_step_entity()
            except (
                InvalidEntityReference,
                KeyError,
                TypeError,
                ValueError,
                OverflowError,
                RecursionError,
                vol.Invalid,
            ) as err:
                _LOGGER.exception(
                    "Unable to build defaults for selected source entities "
                    "(flow=%s, step=entity_source): %s",
                    type(self).__name__,
                    err,
                )
                errors[CONF_REFERENCE_ENTITY_ID] = "invalid_entity_id"

        return self.async_show_form(
            step_id="entity_source",
            data_schema=_reference_entity_schema(),
            errors=errors,
        )

    async def async_step_entity_type(self, user_input=None):
        """Choose the virtual entity domain for selected sources."""
        if not self._source_entities or not self._reference_defaults:
            return await self.async_step_entity_source()

        errors = _flow_errors(self, "entity_type")
        inferred_platform = self._reference_defaults[CONF_PLATFORM]
        if user_input is not None:
            try:
                self._reference_defaults = _reference_entity_defaults(
                    self.hass,
                    self._source_entities,
                    user_input[CONF_TARGET_ENTITY_TYPE],
                )
                return await self.async_step_entity_helper()
            except InvalidEntityReference:
                errors["base"] = "source_unavailable"

        return self.async_show_form(
            step_id="entity_type",
            data_schema=_entity_type_schema(
                self._source_entities,
                inferred_platform,
            ),
            errors=errors,
        )

    async def async_step_entity_helper(self, user_input=None):
        """Choose whether helpers populate a newly copied entity."""
        if not self._reference_defaults:
            return await self.async_step_entity_source()

        if user_input is not None:
            self._add_use_template_helper = cv.boolean(
                user_input[CONF_USE_TEMPLATE_HELPER],
            )
            self._entity_defaults = (
                dict(self._reference_defaults)
                if self._add_use_template_helper
                else _without_template_helpers(self._reference_defaults)
            )
            return await self.async_step_entity()

        return self.async_show_form(
            step_id="entity_helper",
            data_schema=_helper_usage_schema(),
        )

    async def async_step_entity(self, user_input=None):
        """Add the first UI-managed virtual entity."""
        errors = _flow_errors(self, "entity")
        if user_input is not None:
            user_input = _flatten_entity_form_sections(user_input)
            user_input = _align_form_entity_id_domain(user_input)
            user_input = _merge_entity_form_defaults(
                user_input,
                self._entity_defaults,
            )
            try:
                user_input, self._reference_defaults = (
                    _refresh_add_reference_defaults(
                        self.hass,
                        user_input,
                        self._reference_defaults,
                        use_template_helper=self._add_use_template_helper,
                    )
                )
            except InvalidEntityReference as err:
                errors[err.field_name] = "invalid_entity_id"
        if (
            user_input is not None
            and not errors
            and _needs_domain_specific_form(user_input)
        ):
            user_input = _complete_domain_form_defaults(user_input)
            self._entity_defaults = user_input
            return self.async_show_form(
                step_id="entity",
                data_schema=_entity_schema(user_input),
            )
        if user_input is not None and not errors:
            user_input = _with_hidden_native_template_defaults(
                user_input,
                self._entity_defaults,
            )
            try:
                device_name, entity = await _async_build_entity_config(
                    self.hass, user_input
                )
                _set_auto_helper_profile(
                    entity,
                    user_input,
                    (
                        self._reference_defaults
                        if self._add_use_template_helper
                        else {}
                    ),
                )
                device_config = _build_device_config(user_input, device_name)
                options = _append_ui_entity(
                    {ATTR_DEVICES: {}, ATTR_DEVICE_ATTRIBUTES: {}},
                    device_name,
                    entity,
                    device_config,
                )
                return self.async_create_entry(
                    title=self._pending_title,
                    data=self._pending_data,
                    options=options,
                )
            except InvalidJson as err:
                errors[err.field_name] = "invalid_json"
            except InvalidTemplate as err:
                errors[err.field_name] = "invalid_template"
            except InvalidEntityReference as err:
                errors[err.field_name] = "invalid_entity_id"
            except InvalidEntityId:
                errors[ATTR_ENTITY_ID] = "invalid_entity_id"
            except EntityIdAlreadyUsed:
                errors[ATTR_ENTITY_ID] = "entity_id_used"
            except InvalidDomainOptions:
                errors[_domain_options_error_field(user_input)] = "invalid_domain_options"
            except MissingDeviceName:
                errors[CONF_DEVICE_NAME] = "required"
            except MissingEntityName:
                errors[CONF_ENTITY_NAME] = "required"

        if user_input is not None:
            self._entity_defaults = user_input
        return self.async_show_form(
            step_id="entity",
            data_schema=_entity_schema(user_input or self._entity_defaults),
            errors=errors,
        )

    async def async_step_import(self, import_data):
        """Reject non-UI import. Virtual Layer is UI-only."""
        return self.async_abort(reason="import_not_supported")


@_log_unhandled_flow_errors
class VirtualOptionsFlowHandler(config_entries.OptionsFlowWithReload):
    """Virtual Layer options flow."""

    def __init__(self) -> None:
        self._edit_device_name: str | None = None
        self._edit_index: int | None = None
        self._managed_device_name: str | None = None
        self._entity_defaults: dict[str, Any] | None = None
        self._reference_defaults: dict[str, Any] = {}
        self._add_target_device_name: str | None = None
        self._add_source_entities: list[str] = []
        self._add_use_template_helper = True
        self._edit_auto_helper_profile: dict[str, Any] | None = None
        self._edit_current_defaults: dict[str, Any] | None = None
        self._edit_original_platform: str | None = None
        self._edit_target_device_name: str | None = None
        self._edit_target_platform: str | None = None
        self._edit_source_entities: list[str] | None = None
        self._edit_helper_update_mode: str | None = None
        self._edit_cross_domain_conversion = False
        self._edit_platform_changed = False
        self._edit_sources_changed = False

    async def async_step_init(self, user_input=None):
        errors = _flow_errors(self, "init")
        if user_input is not None:
            if user_input[CONF_ACTION] == ACTION_FINISH:
                return self.async_create_entry(
                    data=_plain_options(self.config_entry.options)
                )
            if user_input[CONF_ACTION] == ACTION_EDIT_ENTITY:
                return await self.async_step_select_entity()
            if user_input[CONF_ACTION] == ACTION_DELETE_ENTITY:
                return await self.async_step_delete_entities()
            if user_input[CONF_ACTION] == ACTION_MANAGE_DEVICES:
                return await self.async_step_select_device()
            if user_input[CONF_ACTION] == ACTION_DELETE_DEVICE:
                return await self.async_step_delete_device()
            return await self.async_step_entity_source()

        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(self.config_entry.options),
            errors=errors,
        )

    async def async_step_select_device(self, user_input=None):
        """Select a logical Device without exposing entity-specific settings."""
        errors = _flow_errors(self, "select_device")
        if not _managed_device_choices(self.config_entry.options):
            return self.async_show_form(
                step_id="init",
                data_schema=_options_schema(self.config_entry.options),
                errors=_flow_errors(self, "init", {"base": "no_devices"}),
            )

        if user_input is not None:
            device_name = user_input.get(CONF_MANAGED_DEVICE_NAME)
            if device_name in _managed_device_choices(self.config_entry.options):
                self._managed_device_name = device_name
                return await self.async_step_edit_device()
            errors[CONF_MANAGED_DEVICE_NAME] = "device_not_found"

        return self.async_show_form(
            step_id="select_device",
            data_schema=_select_device_schema(self.config_entry.options),
            errors=errors,
        )

    async def async_step_edit_device(self, user_input=None):
        """Edit Device metadata independently from its virtual entities."""
        if self._managed_device_name is None:
            return await self.async_step_select_device()
        if self._managed_device_name not in _options_devices(self.config_entry.options):
            return await self.async_step_select_device()

        errors = _flow_errors(self, "edit_device")
        if user_input is not None:
            try:
                new_device_name = _make_device_name(
                    user_input[CONF_DEVICE_NAME],
                ).strip()
                device_config = _build_device_config(user_input, new_device_name)
                options = _replace_ui_device(
                    self.config_entry.options,
                    self._managed_device_name,
                    new_device_name,
                    device_config,
                )
                return self.async_create_entry(data=options)
            except MissingDeviceName:
                errors[CONF_DEVICE_NAME] = "required"
            except InvalidEntitySelection:
                errors["base"] = "device_not_found"

        defaults = user_input or _device_form_defaults(
            self.config_entry.options,
            self._managed_device_name,
        )
        return self.async_show_form(
            step_id="edit_device",
            data_schema=_device_schema(defaults),
            errors=errors,
        )

    async def async_step_entity_source(self, user_input=None):
        """Choose an existing entity to prefill a new virtual entity."""
        errors = _flow_errors(self, "entity_source")
        if user_input is not None:
            try:
                self._add_source_entities = _normalize_reference_entity_ids(
                    user_input.get(CONF_REFERENCE_ENTITY_ID),
                )
                self._reference_defaults = _reference_entity_defaults(
                    self.hass,
                    self._add_source_entities,
                )
                self._add_target_device_name = user_input.get(
                    CONF_TARGET_DEVICE_NAME,
                )
                if self._reference_defaults:
                    if len(self._add_source_entities) == 1 or _has_entity_type_choice(
                        self._add_source_entities,
                        self._reference_defaults[CONF_PLATFORM],
                    ):
                        return await self.async_step_entity_type()
                    return await self.async_step_entity_helper()
                self._entity_defaults = _with_existing_device_defaults(
                    {},
                    self.config_entry.options,
                    self._add_target_device_name,
                )
                return await self.async_step_entity()
            except (
                InvalidEntityReference,
                KeyError,
                TypeError,
                ValueError,
                OverflowError,
                RecursionError,
                vol.Invalid,
            ) as err:
                _LOGGER.exception(
                    "Unable to build defaults for selected source entities "
                    "(flow=%s, step=entity_source): %s",
                    type(self).__name__,
                    err,
                )
                errors[CONF_REFERENCE_ENTITY_ID] = "invalid_entity_id"

        return self.async_show_form(
            step_id="entity_source",
            data_schema=_reference_entity_schema(
                device_options=_existing_device_options(
                    self.hass, self.config_entry.options
                ),
            ),
            errors=errors,
        )

    async def async_step_entity_type(self, user_input=None):
        """Choose the virtual entity domain for selected sources."""
        if not self._add_source_entities or not self._reference_defaults:
            return await self.async_step_entity_source()

        errors = _flow_errors(self, "entity_type")
        inferred_platform = self._reference_defaults[CONF_PLATFORM]
        if user_input is not None:
            try:
                self._reference_defaults = _reference_entity_defaults(
                    self.hass,
                    self._add_source_entities,
                    user_input[CONF_TARGET_ENTITY_TYPE],
                )
                return await self.async_step_entity_helper()
            except InvalidEntityReference:
                errors["base"] = "source_unavailable"

        return self.async_show_form(
            step_id="entity_type",
            data_schema=_entity_type_schema(
                self._add_source_entities,
                inferred_platform,
            ),
            errors=errors,
        )

    async def async_step_entity_helper(self, user_input=None):
        """Choose whether helpers populate a newly copied entity."""
        if not self._reference_defaults:
            return await self.async_step_entity_source()

        if user_input is not None:
            self._add_use_template_helper = cv.boolean(
                user_input[CONF_USE_TEMPLATE_HELPER],
            )
            defaults = (
                dict(self._reference_defaults)
                if self._add_use_template_helper
                else _without_template_helpers(self._reference_defaults)
            )
            self._entity_defaults = _with_existing_device_defaults(
                defaults,
                self.config_entry.options,
                self._add_target_device_name,
            )
            return await self.async_step_entity()

        return self.async_show_form(
            step_id="entity_helper",
            data_schema=_helper_usage_schema(),
        )

    async def async_step_entity(self, user_input=None):
        """Add a UI-managed virtual entity."""
        errors = _flow_errors(self, "entity")
        if user_input is not None:
            user_input = _flatten_entity_form_sections(user_input)
            user_input = _align_form_entity_id_domain(user_input)
            user_input = _merge_entity_form_defaults(
                user_input,
                self._entity_defaults,
            )
            try:
                user_input, self._reference_defaults = (
                    _refresh_add_reference_defaults(
                        self.hass,
                        user_input,
                        self._reference_defaults,
                        use_template_helper=self._add_use_template_helper,
                    )
                )
            except InvalidEntityReference as err:
                errors[err.field_name] = "invalid_entity_id"
        if (
            user_input is not None
            and not errors
            and _needs_domain_specific_form(user_input)
        ):
            user_input = _complete_domain_form_defaults(user_input)
            self._entity_defaults = user_input
            return self.async_show_form(
                step_id="entity",
                data_schema=_entity_schema(user_input),
            )
        if user_input is not None and not errors:
            user_input = _with_hidden_native_template_defaults(
                user_input,
                self._entity_defaults,
            )
            try:
                device_name, entity = await _async_build_entity_config(
                    self.hass, user_input
                )
                _set_auto_helper_profile(
                    entity,
                    user_input,
                    (
                        self._reference_defaults
                        if self._add_use_template_helper
                        else {}
                    ),
                )
                device_config = _build_device_config(user_input, device_name)
                options = _append_ui_entity(
                    self.config_entry.options,
                    device_name,
                    entity,
                    device_config,
                )
                return self.async_create_entry(data=options)
            except InvalidJson as err:
                errors[err.field_name] = "invalid_json"
            except InvalidTemplate as err:
                errors[err.field_name] = "invalid_template"
            except InvalidEntityReference as err:
                errors[err.field_name] = "invalid_entity_id"
            except InvalidEntityId:
                errors[ATTR_ENTITY_ID] = "invalid_entity_id"
            except EntityIdAlreadyUsed:
                errors[ATTR_ENTITY_ID] = "entity_id_used"
            except InvalidDomainOptions:
                errors[_domain_options_error_field(user_input)] = "invalid_domain_options"
            except MissingDeviceName:
                errors[CONF_DEVICE_NAME] = "required"
            except MissingEntityName:
                errors[CONF_ENTITY_NAME] = "required"

        if user_input is not None:
            self._entity_defaults = user_input
        return self.async_show_form(
            step_id="entity",
            data_schema=_entity_schema(user_input or self._entity_defaults),
            errors=errors,
        )

    async def async_step_select_entity(self, user_input=None):
        """Select a UI-managed virtual entity to edit."""
        errors = _flow_errors(self, "select_entity")
        if not _entity_choices(self.config_entry.options):
            return self.async_show_form(
                step_id="init",
                data_schema=_options_schema(self.config_entry.options),
                errors=_flow_errors(self, "init", {"base": "no_entities"}),
            )

        if user_input is not None:
            try:
                device_name, index = _find_entity_by_selection_key(
                    self.config_entry.options,
                    user_input[CONF_ENTITY_KEY],
                )
                _get_ui_entity(self.config_entry.options, device_name, index)
                self._edit_device_name = device_name
                self._edit_index = index
                return await self.async_step_edit_entity_source()
            except InvalidEntitySelection:
                errors[CONF_ENTITY_KEY] = "entity_not_found"

        return self.async_show_form(
            step_id="select_entity",
            data_schema=_select_entity_schema(self.config_entry.options),
            errors=errors,
        )

    async def async_step_delete_entities(self, user_input=None):
        """Delete one or more UI-managed virtual entities."""
        errors = _flow_errors(self, "delete_entities")
        if not _entity_choices(self.config_entry.options, include_invalid=True):
            return self.async_show_form(
                step_id="init",
                data_schema=_options_schema(self.config_entry.options),
                errors=_flow_errors(self, "init", {"base": "no_entities"}),
            )

        if user_input is not None:
            try:
                options = _delete_ui_entities(
                    self.config_entry.options,
                    user_input.get(CONF_ENTITY_KEYS, []),
                )
                return self.async_create_entry(data=options)
            except InvalidEntitySelection:
                errors[CONF_ENTITY_KEYS] = "entity_not_found"

        return self.async_show_form(
            step_id="delete_entities",
            data_schema=_delete_entities_schema(self.config_entry.options),
            errors=errors,
        )

    async def async_step_delete_device(self, user_input=None):
        """Delete a Device, including malformed groups that cannot be edited."""
        errors = _flow_errors(self, "delete_device")
        if not _managed_device_choices(self.config_entry.options):
            return self.async_show_form(
                step_id="init",
                data_schema=_options_schema(self.config_entry.options),
                errors=_flow_errors(self, "init", {"base": "no_devices"}),
            )

        if user_input is not None:
            try:
                options = _delete_ui_device(
                    self.config_entry.options,
                    user_input.get(CONF_MANAGED_DEVICE_NAME),
                )
                return self.async_create_entry(data=options)
            except InvalidEntitySelection:
                errors[CONF_MANAGED_DEVICE_NAME] = "device_not_found"

        return self.async_show_form(
            step_id="delete_device",
            data_schema=_select_device_schema(self.config_entry.options),
            errors=errors,
        )

    async def async_step_edit_entity_source(self, user_input=None):
        """Choose an existing entity to prefill an edited virtual entity."""
        errors = _flow_errors(self, "edit_entity_source")
        if self._edit_device_name is None or self._edit_index is None:
            return await self.async_step_select_entity()

        try:
            entity = _get_ui_entity(
                self.config_entry.options,
                self._edit_device_name,
                self._edit_index,
            )
            current_defaults = _entity_form_defaults(
                self._edit_device_name,
                entity,
                self.config_entry.options,
            )
            self._edit_auto_helper_profile = _existing_auto_helper_profile(
                self.hass,
                entity,
                current_defaults,
            )
        except InvalidEntitySelection:
            return await self.async_step_select_entity()

        if user_input is not None:
            try:
                selected_sources = _normalize_reference_entity_ids(
                    user_input.get(CONF_REFERENCE_ENTITY_ID),
                )
                self._reference_defaults = _reference_entity_defaults(
                    self.hass,
                    selected_sources,
                )
                self._edit_current_defaults = current_defaults
                self._edit_original_platform = current_defaults.get(CONF_PLATFORM)
                self._edit_target_device_name = user_input.get(
                    CONF_TARGET_DEVICE_NAME,
                )
                self._edit_source_entities = selected_sources
                self._edit_target_platform = None
                self._edit_cross_domain_conversion = False
                self._edit_platform_changed = False
                self._edit_sources_changed = selected_sources != _stored_entity_ids(
                    entity.get(CONF_SOURCE_ENTITIES),
                )
                if selected_sources and (
                    len(selected_sources) == 1
                    or _has_entity_type_choice(
                        selected_sources,
                        self._reference_defaults[CONF_PLATFORM],
                        self._edit_original_platform,
                    )
                ):
                    return await self.async_step_edit_entity_type()
                if self._edit_sources_changed or selected_sources:
                    return await self.async_step_edit_entity_helper()

                self._prepare_edit_entity_defaults(
                    helper_update_mode=HELPER_UPDATE_AUTO,
                )
                return await self.async_step_edit_entity()
            except InvalidEntityReference:
                errors[CONF_REFERENCE_ENTITY_ID] = "invalid_entity_id"

        return self.async_show_form(
            step_id="edit_entity_source",
            data_schema=_reference_entity_schema(
                _stored_entity_ids(entity.get(CONF_SOURCE_ENTITIES)),
                _existing_device_options(self.hass, self.config_entry.options),
                self._edit_device_name,
            ),
            errors=errors,
        )

    async def async_step_edit_entity_type(self, user_input=None):
        """Choose the virtual entity domain for edited sources."""
        if (
            self._edit_current_defaults is None
            or self._edit_source_entities is None
            or not self._edit_source_entities
            or not self._reference_defaults
        ):
            return await self.async_step_edit_entity_source()

        errors = _flow_errors(self, "edit_entity_type")
        inferred_platform = self._reference_defaults[CONF_PLATFORM]
        current_platform = self._edit_current_defaults.get(CONF_PLATFORM)
        original_platform = self._edit_original_platform or current_platform
        if user_input is not None:
            target_platform = user_input[CONF_TARGET_ENTITY_TYPE]
            try:
                self._reference_defaults = _reference_entity_defaults(
                    self.hass,
                    self._edit_source_entities,
                    target_platform,
                    (current_platform,),
                )
                self._edit_target_platform = target_platform
                self._edit_cross_domain_conversion = (
                    target_platform != inferred_platform
                )
                self._edit_platform_changed = target_platform != original_platform
                self._edit_sources_changed = (
                    self._edit_sources_changed or self._edit_platform_changed
                )
                return await self.async_step_edit_entity_helper()
            except InvalidEntityReference:
                errors["base"] = "source_unavailable"

        return self.async_show_form(
            step_id="edit_entity_type",
            data_schema=_entity_type_schema(
                self._edit_source_entities,
                inferred_platform,
                self._edit_target_platform or current_platform,
            ),
            errors=errors,
        )

    def _prepare_edit_entity_defaults(self, *, helper_update_mode: str) -> None:
        """Apply the selected helper policy to the pending source change."""
        if self._edit_current_defaults is None or self._edit_source_entities is None:
            return
        self._edit_helper_update_mode = helper_update_mode
        if helper_update_mode == HELPER_UPDATE_KEEP:
            self._entity_defaults = _reference_edit_defaults(
                self._edit_current_defaults,
                {},
                None,
                source_entities_text="\n".join(self._edit_source_entities),
            )
            if self._edit_platform_changed:
                self._edit_auto_helper_profile = None
        else:
            auto_helper_profile = self._edit_auto_helper_profile
            if auto_helper_profile is None and (
                self._edit_platform_changed
                or self._edit_cross_domain_conversion
            ):
                # A type change needs compatible empty/missing fields even for
                # legacy entries without a saved helper baseline. An empty
                # baseline fills only absent fields and preserves nonempty
                # templates or actions the user previously customized.
                auto_helper_profile = _auto_helper_profile({})
            self._entity_defaults = _reference_edit_defaults(
                self._edit_current_defaults,
                self._reference_defaults,
                auto_helper_profile,
                force_template_helper=(helper_update_mode == HELPER_UPDATE_FORCE),
                source_entities_text=(
                    "\n".join(self._edit_source_entities)
                    if not self._reference_defaults
                    else None
                ),
            )
            if self._edit_sources_changed:
                self._edit_auto_helper_profile = _auto_helper_profile(
                    self._reference_defaults,
                )
        target_platform = self._reference_defaults.get(CONF_PLATFORM)
        if target_platform in VIRTUAL_ENTITY_DOMAINS:
            self._entity_defaults[CONF_PLATFORM] = target_platform
            self._entity_defaults = _align_form_entity_id_domain(
                self._entity_defaults,
            )
        self._entity_defaults = _with_existing_device_defaults(
            self._entity_defaults,
            self.config_entry.options,
            self._edit_target_device_name,
        )

    async def async_step_edit_entity_helper(self, user_input=None):
        """Choose automatic detection or forced helper regeneration."""
        if self._edit_current_defaults is None or self._edit_source_entities is None:
            return await self.async_step_edit_entity_source()

        if user_input is not None:
            try:
                self._prepare_edit_entity_defaults(
                    helper_update_mode=user_input[CONF_HELPER_UPDATE_MODE],
                )
                return await self.async_step_edit_entity()
            except Exception:
                # A damaged legacy helper must not make the entity impossible
                # to edit. Keep the newly selected sources and current values,
                # then let the user repair templates in the normal form.
                _LOGGER.exception(
                    "Unable to apply Virtual Layer helper update; preserving "
                    "current templates (flow=%s, entry_id=%s)",
                    type(self).__name__,
                    getattr(self.config_entry, "entry_id", None) or "new",
                )
                fallback_defaults = dict(self._edit_current_defaults)
                fallback_defaults[CONF_SOURCE_ENTITIES_TEXT] = "\n".join(
                    self._edit_source_entities
                )
                self._edit_helper_update_mode = HELPER_UPDATE_KEEP
                if self._edit_platform_changed:
                    self._edit_auto_helper_profile = None
                self._entity_defaults = _with_existing_device_defaults(
                    fallback_defaults,
                    self.config_entry.options,
                    self._edit_target_device_name,
                )
                target_platform = self._reference_defaults.get(CONF_PLATFORM)
                if target_platform in VIRTUAL_ENTITY_DOMAINS:
                    self._entity_defaults[CONF_PLATFORM] = target_platform
                    self._entity_defaults = _align_form_entity_id_domain(
                        self._entity_defaults,
                    )
                errors = _flow_errors(self, "edit_entity", {
                    "base": "helper_update_failed",
                })
                return self.async_show_form(
                    step_id="edit_entity",
                    data_schema=_entity_schema(self._entity_defaults),
                    errors=errors,
                )

        return self.async_show_form(
            step_id="edit_entity_helper",
            data_schema=_helper_update_schema(),
        )

    async def async_step_edit_entity(self, user_input=None):
        """Edit a UI-managed virtual entity."""
        errors = _flow_errors(self, "edit_entity")
        if self._edit_device_name is None or self._edit_index is None:
            return await self.async_step_select_entity()

        if user_input is not None:
            user_input = _flatten_entity_form_sections(user_input)
            user_input = _align_form_entity_id_domain(user_input)
            user_input = _merge_entity_form_defaults(
                user_input,
                self._entity_defaults,
            )
        if user_input is not None and _needs_domain_specific_form(user_input):
            user_input = _complete_domain_form_defaults(user_input)
            self._entity_defaults = user_input
            return self.async_show_form(
                step_id="edit_entity",
                data_schema=_entity_schema(user_input),
            )
        if user_input is not None:
            user_input = _with_hidden_native_template_defaults(
                user_input,
                self._entity_defaults,
            )
            try:
                current_entity = _get_ui_entity(
                    self.config_entry.options,
                    self._edit_device_name,
                    self._edit_index,
                )
                submitted_sources = _parse_source_entities(
                    user_input.get(CONF_SOURCE_ENTITIES_TEXT, ""),
                )
                sources_changed = submitted_sources != self._edit_source_entities
                target_platform = user_input.get(CONF_PLATFORM)
                target_platform_changed = (
                    len(submitted_sources) == 1
                    and bool(self._reference_defaults)
                    and target_platform in VIRTUAL_ENTITY_DOMAINS
                    and target_platform != self._reference_defaults.get(CONF_PLATFORM)
                )
                if sources_changed or target_platform_changed:
                    _validate_mergeable_source_entities(
                        submitted_sources,
                        CONF_SOURCE_ENTITIES_TEXT,
                    )
                    # Validate the complete pending edit first so dependency
                    # cycles and malformed input remain attached to the
                    # detailed form fields instead of the source picker.
                    await _async_build_entity_config(
                        self.hass,
                        user_input,
                        _virtual_entity_id(current_entity),
                    )
                    inferred_reference_defaults: dict[str, Any] = {}
                    try:
                        inferred_reference_defaults = _reference_entity_defaults(
                            self.hass,
                            submitted_sources,
                        )
                        if (
                            len(submitted_sources) == 1
                            and target_platform in VIRTUAL_ENTITY_DOMAINS
                        ):
                            self._reference_defaults = _reference_entity_defaults(
                                self.hass,
                                submitted_sources,
                                target_platform,
                                (target_platform,),
                            )
                        else:
                            self._reference_defaults = inferred_reference_defaults
                    except InvalidEntityReference:
                        # Valid future or unloaded entities cannot prefill a
                        # helper yet, but users can still keep current Jinja.
                        self._reference_defaults = {}
                    self._edit_current_defaults = user_input
                    # The detailed form is now authoritative. Reapplying the
                    # source-step Device choice would discard metadata edits.
                    self._edit_target_device_name = None
                    self._edit_source_entities = submitted_sources
                    self._edit_target_platform = (
                        target_platform
                        if target_platform in VIRTUAL_ENTITY_DOMAINS
                        else None
                    )
                    self._edit_cross_domain_conversion = (
                        len(submitted_sources) == 1
                        and self._edit_target_platform is not None
                        and self._edit_target_platform
                        != inferred_reference_defaults.get(CONF_PLATFORM)
                    )
                    self._edit_platform_changed = (
                        self._edit_target_platform is not None
                        and self._edit_target_platform != self._edit_original_platform
                    )
                    self._edit_sources_changed = True
                    if (
                        sources_changed
                        and len(submitted_sources) == 1
                        and self._reference_defaults
                    ):
                        return await self.async_step_edit_entity_type()
                    return await self.async_step_edit_entity_helper()

                device_name, entity = await _async_build_entity_config(
                    self.hass,
                    user_input,
                    _virtual_entity_id(current_entity),
                )
                _set_auto_helper_profile(
                    entity,
                    user_input,
                    (
                        {}
                        if self._edit_helper_update_mode == HELPER_UPDATE_KEEP
                        else self._reference_defaults
                    ),
                    (
                        self._edit_auto_helper_profile
                        if self._edit_helper_update_mode == HELPER_UPDATE_KEEP
                        else (
                            _auto_helper_profile({})
                            if self._edit_sources_changed
                            and not self._reference_defaults
                            else self._edit_auto_helper_profile
                        )
                    ),
                )
                device_config = _build_device_config(user_input, device_name)
                options = _replace_ui_entity(
                    self.config_entry.options,
                    self._edit_device_name,
                    self._edit_index,
                    device_name,
                    entity,
                    device_config,
                )
                return self.async_create_entry(data=options)
            except InvalidJson as err:
                errors[err.field_name] = "invalid_json"
            except InvalidTemplate as err:
                errors[err.field_name] = "invalid_template"
            except InvalidEntityReference as err:
                errors[err.field_name] = "invalid_entity_id"
            except InvalidEntityId:
                errors[ATTR_ENTITY_ID] = "invalid_entity_id"
            except EntityIdAlreadyUsed:
                errors[ATTR_ENTITY_ID] = "entity_id_used"
            except InvalidDomainOptions:
                errors[_domain_options_error_field(user_input)] = "invalid_domain_options"
            except MissingDeviceName:
                errors[CONF_DEVICE_NAME] = "required"
            except MissingEntityName:
                errors[CONF_ENTITY_NAME] = "required"
            except InvalidEntitySelection:
                errors["base"] = "entity_not_found"

        if user_input is not None:
            self._entity_defaults = user_input
        defaults = user_input
        if defaults is None:
            if self._entity_defaults is not None:
                defaults = self._entity_defaults
            else:
                try:
                    entity = _get_ui_entity(
                        self.config_entry.options,
                        self._edit_device_name,
                        self._edit_index,
                    )
                    defaults = _entity_form_defaults(
                        self._edit_device_name,
                        entity,
                        self.config_entry.options,
                    )
                except InvalidEntitySelection:
                    return await self.async_step_select_entity()

        return self.async_show_form(
            step_id="edit_entity",
            data_schema=_entity_schema(defaults),
            errors=errors,
        )


class GroupNameAlreadyUsed(exceptions.HomeAssistantError):
    """Error indicating group name already used."""


class MissingGroupName(exceptions.HomeAssistantError):
    """Error indicating an empty Device group name."""


class MissingDeviceName(exceptions.HomeAssistantError):
    """Error indicating missing device name."""


class MissingEntityName(exceptions.HomeAssistantError):
    """Error indicating missing entity name."""


class InvalidJson(exceptions.HomeAssistantError):
    """Error indicating an invalid JSON field."""

    def __init__(self, field_name: str) -> None:
        super().__init__(field_name)
        self.field_name = field_name


class InvalidTemplate(exceptions.HomeAssistantError):
    """Error indicating invalid Jinja syntax in a form field."""

    def __init__(
        self,
        field_name: str,
        template_name: str | None = None,
        reason: str | None = None,
    ) -> None:
        super().__init__(field_name)
        self.field_name = field_name
        self.template_name = template_name or field_name
        self.reason = reason


class InvalidEntityReference(exceptions.HomeAssistantError):
    """Error indicating an invalid source entity reference."""

    def __init__(self, field_name: str) -> None:
        super().__init__(field_name)
        self.field_name = field_name


class InvalidEntityId(exceptions.HomeAssistantError):
    """Error indicating an invalid entity ID."""


class EntityIdAlreadyUsed(exceptions.HomeAssistantError):
    """Error indicating an entity ID is already owned by another entity."""


class InvalidDomainOptions(exceptions.HomeAssistantError):
    """Error indicating invalid domain-specific options."""


class InvalidEntitySelection(exceptions.HomeAssistantError):
    """Error indicating an invalid entity selection."""
