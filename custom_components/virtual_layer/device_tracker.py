"""
This component provides support for a virtual device tracker.

"""

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from math import asin, atan2, cos, degrees, isfinite, radians, sin, sqrt

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from aiohttp import ClientError
from homeassistant.components.device_tracker import (
    DOMAIN as PLATFORM_DOMAIN,
)
from homeassistant.components.device_tracker import (
    SourceType,
    TrackerEntity,
)
from homeassistant.components.zone import ATTR_RADIUS
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_FRIENDLY_NAME,
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    CONF_DEVICES,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers.config_validation import PLATFORM_SCHEMA
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.template import Template, TemplateError
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify

from . import (
    _assert_managed_virtual_entities,
    _async_verify_target_entity_control,
    get_entity_configs,
    get_entity_from_domain,
)
from .const import *
from .entity import VirtualEntity, repair_legacy_template_data, virtual_schema
from .dawarich import (
    DawarichClient, DawarichError, family_point, normalize_config as normalize_dawarich_config,
    point_time as dawarich_point_time, records as dawarich_records,
    summary as dawarich_summary, valid_point as valid_dawarich_point,
)
from .local_presence import BLE_PREFIX, LocalPresence, normalize as normalize_local_presence
from .polygon import (
    find_polygon_zone,
    load_polygon_zones,
    median_longitude,
    parse_geojson_zones,
    select_tracker_position,
)

_LOGGER = logging.getLogger(__name__)

DEPENDENCIES = [COMPONENT_DOMAIN]

CONF_LOCATION = "location"
CONF_GPS = "gps"
CONF_GPS_ACCURACY = "gps_accuracy"
CONF_LOCATION_HELPER_DISTANCE_METERS = "distance_threshold_meters"
CONF_LOCATION_HELPER_PRIORITY_WINDOW_SECONDS = "priority_window_seconds"
DEFAULT_DEVICE_TRACKER_VALUE = "home"
DEFAULT_LOCATION = "home"
DEFAULT_LOCATION_HELPER_DISTANCE_METERS = 300
DEFAULT_LOCATION_HELPER_PRIORITY_WINDOW_SECONDS = 30 * 60
FRONT_DOOR_DISTANCE_METERS = 20
NEAR_HOME_DISTANCE_METERS = 1_000
FAR_AWAY_DISTANCE_METERS = 5_000
POLYGON_FILE_RELOAD_INTERVAL = timedelta(minutes=5)
LOCATION_CHANGE_DISTANCE_METERS = 10
# Allow ordinary air travel, but reject impossible GPS teleports. Accuracy
# circles are subtracted before applying this deliberately generous ceiling.
MAX_LOCATION_SPEED_METERS_PER_SECOND = 350
LOCATION_HISTORY_LIMIT = 8
SPEED_MIN_INTERVAL_SECONDS = 5
SPEED_MAX_AGE_SECONDS = 300
ATTR_LOCATION_LAST_SEEN = "location_last_seen"
ATTR_LOCATION_SPEED = "location_speed_m_s"
ATTR_LOCATION_BEARING = "location_bearing"
ATTR_LOCATION_MEDIAN_LATITUDE = "location_median_latitude"
ATTR_LOCATION_MEDIAN_LONGITUDE = "location_median_longitude"
ATTR_LOCATION_PRIORITY_SOURCE = "location_priority_source"
ATTR_LOCATION_SOURCE_LAST_MOVED = "location_source_last_moved"
ATTR_LOCATION_SOURCE_POSITIONS = "location_source_positions"
ATTR_LOCATION_SOURCE_OBSERVATIONS = "location_source_observations"
ATTR_LOCATION_SELECTION_REASON = "location_selection_reason"
ATTR_LOCATION_STALE = "location_stale"
ATTR_LOCATION_REJECTED_SOURCES = "location_rejected_sources"
ATTR_POLYGON_ZONE = "polygon_zone"
ATTR_POLYGON_ZONES = "polygon_zones"
ATTR_POLYGON_PERSON = "polygon_person"
ATTR_POLYGON_STRATEGY = "polygon_strategy"
ATTR_POLYGON_SELECTION_REASON = "polygon_selection_reason"
ATTR_POLYGON_SELECTED_SOURCE = "polygon_selected_source"
ATTR_POLYGON_SELECTED_MEMBERS = "polygon_selected_members"
ATTR_POLYGON_LOAD_ERROR = "polygon_load_error"
ATTR_ESPRESENSE_POSITION = "espresense_position"
ATTR_ESPRESENSE_SOURCES = "espresense_sources"
ATTR_ESPRESENSE_ACCURACY = "espresense_accuracy"
ATTR_DAWARICH_LAST_UPDATED = "dawarich_last_updated"
ATTR_DAWARICH_POINT = "dawarich_point"
ATTR_DAWARICH_HISTORY = "dawarich_history"
ATTR_DAWARICH_VISIT = "dawarich_visit"
ATTR_DAWARICH_ERROR = "dawarich_error"
ATTR_DAWARICH_POINT_TIME = "dawarich_point_time"
ATTR_DAWARICH_STALE = "dawarich_stale"
ATTR_DAWARICH_VISIT_ERROR = "dawarich_visit_error"
ATTR_DAWARICH_SOURCE_ID = "dawarich_source_id"
ATTR_DAWARICH_ACTIVITY = "dawarich_activity"
DAWARICH_SOURCE = "dawarich"
ATTR_LOCATION_PRESENCE_SOURCES = "location_presence_sources"
ATTR_LOCATION_CLASSIFICATION = "location_classification"
ATTR_LOCATION_HOME_DISTANCE = "location_home_distance"
ATTR_LOCATION_BLE_DISTANCE = "location_ble_distance"
DEFAULT_DAWARICH_POLL_INTERVAL = 60
DEFAULT_DAWARICH_HISTORY_LIMIT = 10
POLYGON_STRATEGIES = {"adaptive", "majority", "priority", "latest", "median"}
POLYGON_RULE_KEYS = {
    "condition_template",
    "dominant",
    "enabled",
    "max_age_seconds",
    "max_gps_accuracy",
    "priority",
    "weight",
}
ESPRESENSE_ANCHOR_KEYS = {
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    "max_age_seconds",
    "distance_scale",
    "distance_offset",
}
DEFAULT_ESPRESENSE_MAX_AGE_SECONDS = 30


def _safe_gps_accuracy(value) -> float | None:
    """Return a finite non-negative GPS accuracy without coercing booleans."""
    if isinstance(value, bool):
        return None
    try:
        accuracy = float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return None
    if not isfinite(accuracy):
        return None
    return max(0.0, accuracy)


PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {vol.Required(CONF_DEVICES, default=list): cv.ensure_list}
)

DEVICE_TRACKER_SCHEMA = vol.Schema(
    virtual_schema(
        DEFAULT_DEVICE_TRACKER_VALUE,
        {
            # Keep the helper flexible so older stored data with an invalid setting can
            # still load and be edited or removed through the UI.
            vol.Optional(CONF_LOCATION_HELPER): object,
            vol.Optional(CONF_PRESENCE_CLASSIFICATION): bool,
            vol.Optional(CONF_POLYGONAL_ZONE): object,
            vol.Optional(CONF_DAWARICH): object,
            vol.Optional(CONF_LOCAL_PRESENCE): object,
        },
    )
)


def validate_domain_options(config) -> None:
    """Validate UI-supplied location helper settings."""
    if config.get(CONF_LOCAL_PRESENCE) is not None:
        normalize_local_presence(config[CONF_LOCAL_PRESENCE])
    value = config.get(CONF_LOCATION_HELPER)
    if value is not None:
        if not isinstance(value, dict):
            raise vol.Invalid("location_helper must be an object")

        allowed = {
            CONF_LOCATION_HELPER_DISTANCE_METERS,
            CONF_LOCATION_HELPER_PRIORITY_WINDOW_SECONDS,
        }
        if set(value) - allowed:
            raise vol.Invalid("unknown location_helper option")

        raw_distance_threshold = value.get(
            CONF_LOCATION_HELPER_DISTANCE_METERS,
            DEFAULT_LOCATION_HELPER_DISTANCE_METERS,
        )
        raw_priority_window = value.get(
            CONF_LOCATION_HELPER_PRIORITY_WINDOW_SECONDS,
            DEFAULT_LOCATION_HELPER_PRIORITY_WINDOW_SECONDS,
        )
        if isinstance(raw_distance_threshold, bool) or isinstance(
            raw_priority_window, bool
        ):
            raise vol.Invalid("invalid location_helper option")
        try:
            distance_threshold = float(raw_distance_threshold)
            priority_window = int(raw_priority_window)
        except (TypeError, ValueError, OverflowError) as err:
            raise vol.Invalid("invalid location_helper option") from err

        if (
            not isfinite(distance_threshold)
            or distance_threshold <= 0
            or priority_window <= 0
        ):
            raise vol.Invalid("location_helper options must be positive")

    if CONF_PRESENCE_CLASSIFICATION in config and not isinstance(
        config[CONF_PRESENCE_CLASSIFICATION], bool
    ):
        raise vol.Invalid("presence_classification must be a boolean")

    dawarich = config.get(CONF_DAWARICH)
    if dawarich is not None:
        normalize_dawarich_config(dawarich)

    polygon = config.get(CONF_POLYGONAL_ZONE)
    if polygon is None:
        return
    if not isinstance(polygon, dict):
        raise vol.Invalid("polygonal_zone must be an object")
    allowed = {
        CONF_POLYGON_AWAY_STATE,
        CONF_POLYGON_DISTANCE_METERS,
        CONF_POLYGON_FILES,
        CONF_POLYGON_GEOJSON,
        CONF_POLYGON_PERSON_ENTITY,
        CONF_POLYGON_STRATEGY,
        CONF_POLYGON_TRACKER_RULES,
        CONF_POLYGON_ESPRESENSE_ANCHORS,
    }
    if set(polygon) - allowed:
        raise vol.Invalid("unknown polygonal_zone option")
    if not polygon.get(CONF_POLYGON_GEOJSON) and not polygon.get(CONF_POLYGON_FILES):
        raise vol.Invalid("polygonal_zone needs GeoJSON or at least one file")
    if polygon.get(CONF_POLYGON_GEOJSON):
        try:
            parse_geojson_zones(polygon[CONF_POLYGON_GEOJSON])
        except (TypeError, ValueError) as err:
            raise vol.Invalid("invalid polygon GeoJSON") from err
    files = polygon.get(CONF_POLYGON_FILES, [])
    if not isinstance(files, list) or any(
        not isinstance(item, str) or not item.strip() for item in files
    ):
        raise vol.Invalid("polygon files must be a list of paths or URLs")
    if polygon.get(CONF_POLYGON_STRATEGY, "majority") not in POLYGON_STRATEGIES:
        raise vol.Invalid("invalid polygon tracker strategy")
    person = polygon.get(CONF_POLYGON_PERSON_ENTITY)
    if person:
        try:
            cv.entity_id(person)
        except vol.Invalid as err:
            raise vol.Invalid("invalid polygon person entity") from err
        if not person.startswith("person."):
            raise vol.Invalid("polygon person entity must use the person domain")
    if not str(polygon.get(CONF_POLYGON_AWAY_STATE, "not_home")).strip():
        raise vol.Invalid("polygon away state must not be empty")
    raw_distance = polygon.get(
        CONF_POLYGON_DISTANCE_METERS,
        DEFAULT_LOCATION_HELPER_DISTANCE_METERS,
    )
    if isinstance(raw_distance, bool):
        raise vol.Invalid("invalid polygon distance threshold")
    try:
        distance = float(raw_distance)
    except (TypeError, ValueError, OverflowError) as err:
        raise vol.Invalid("invalid polygon distance threshold") from err
    if not isfinite(distance) or distance <= 0:
        raise vol.Invalid("polygon distance threshold must be positive")
    rules = polygon.get(CONF_POLYGON_TRACKER_RULES, {})
    anchors = polygon.get(CONF_POLYGON_ESPRESENSE_ANCHORS, {})
    if not isinstance(anchors, dict):
        raise vol.Invalid("polygon ESPresense anchors must be an object")
    if anchors and len(anchors) < 3:
        raise vol.Invalid(
            "polygon ESPresense triangulation needs at least three anchors"
        )
    anchor_positions = []
    for entity_id, anchor in anchors.items():
        if (
            not isinstance(entity_id, str)
            or not entity_id.startswith("sensor.")
            or not isinstance(anchor, dict)
        ):
            raise vol.Invalid("invalid ESPresense anchor")
        if set(anchor) - ESPRESENSE_ANCHOR_KEYS:
            raise vol.Invalid("unknown ESPresense anchor option")
        try:
            latitude, longitude = (
                float(anchor[ATTR_LATITUDE]),
                float(anchor[ATTR_LONGITUDE]),
            )
        except (KeyError, TypeError, ValueError, OverflowError) as err:
            raise vol.Invalid("invalid ESPresense anchor coordinates") from err
        if (
            not isfinite(latitude)
            or not isfinite(longitude)
            or not -90 <= latitude <= 90
            or not -180 <= longitude <= 180
        ):
            raise vol.Invalid("invalid ESPresense anchor coordinates")
        anchor_positions.append((latitude, longitude))
        for key, default in (
            ("max_age_seconds", DEFAULT_ESPRESENSE_MAX_AGE_SECONDS),
            ("distance_scale", 1),
            ("distance_offset", 0),
        ):
            raw = anchor.get(key, default)
            if isinstance(raw, bool):
                raise vol.Invalid(f"invalid ESPresense anchor {key}")
            try:
                parsed = float(raw)
            except (TypeError, ValueError, OverflowError) as err:
                raise vol.Invalid(f"invalid ESPresense anchor {key}") from err
            if not isfinite(parsed) or (key != "distance_offset" and parsed <= 0):
                raise vol.Invalid(f"invalid ESPresense anchor {key}")
    if anchor_positions:
        origin = anchor_positions[0]
        if not any(
            abs(
                (first[1] - origin[1]) * (second[0] - origin[0])
                - (second[1] - origin[1]) * (first[0] - origin[0])
            )
            > 1e-12
            for index, first in enumerate(anchor_positions[1:])
            for second in anchor_positions[index + 2 :]
        ):
            raise vol.Invalid("ESPresense anchors must not be collinear")
    _validate_polygon_rules(rules)
    if CONF_SOURCE_ENTITIES in config:
        source_entities = config.get(CONF_SOURCE_ENTITIES, [])
        if not isinstance(source_entities, list):
            raise vol.Invalid("polygon sources must be a list")
        if any(
            not isinstance(entity_id, str)
            or (
                not entity_id.startswith("device_tracker.") and entity_id not in anchors
                and entity_id not in (config.get(CONF_LOCAL_PRESENCE) or {}).get("wifi_entities", [])
            )
            for entity_id in source_entities
        ):
            raise vol.Invalid(
                "polygon sources must be device_tracker entities or configured ESPresense anchors"
            )
        if set(rules) - set(source_entities):
            raise vol.Invalid("polygon tracker rules must reference selected sources")


def _validate_polygon_rules(rules) -> None:
    """Validate per-source polygon tracker policies."""
    if not isinstance(rules, dict):
        raise vol.Invalid("polygon tracker_rules must be an object")
    for entity_id, rule in rules.items():
        try:
            cv.entity_id(entity_id)
        except vol.Invalid as err:
            raise vol.Invalid("invalid polygon tracker entity") from err
        if not entity_id.startswith("device_tracker."):
            raise vol.Invalid("polygon tracker rules require device_tracker entities")
        if not isinstance(rule, dict) or set(rule) - POLYGON_RULE_KEYS:
            raise vol.Invalid("invalid polygon tracker rule")
        for key in ("max_age_seconds", "max_gps_accuracy", "weight"):
            if key in rule:
                if isinstance(rule[key], bool):
                    raise vol.Invalid(f"invalid polygon tracker {key}")
                try:
                    value = float(rule[key])
                except (TypeError, ValueError, OverflowError) as err:
                    raise vol.Invalid(f"invalid polygon tracker {key}") from err
                if not isfinite(value) or value <= 0:
                    raise vol.Invalid(f"polygon tracker {key} must be positive")
        if "priority" in rule:
            if isinstance(rule["priority"], bool):
                raise vol.Invalid("invalid polygon tracker priority")
            try:
                if not isfinite(float(rule["priority"])):
                    raise ValueError
            except (TypeError, ValueError, OverflowError) as err:
                raise vol.Invalid("invalid polygon tracker priority") from err
        if "condition_template" in rule and not isinstance(
            rule["condition_template"], str
        ):
            raise vol.Invalid("polygon tracker condition_template must be a string")
        for key in ("dominant", "enabled"):
            if key in rule and not isinstance(rule[key], bool):
                raise vol.Invalid(f"polygon tracker {key} must be a boolean")


SERVICE_MOVE = "move"


def _reject_boolean_number(value):
    """Reject booleans before Home Assistant numeric coercion turns them into 0/1."""
    if isinstance(value, bool):
        raise vol.Invalid("boolean is not a numeric value")
    return value


def _validate_move_service_data(data):
    """Require one unambiguous move target and target-specific options."""
    has_location = CONF_LOCATION in data
    has_gps = CONF_GPS in data
    if has_location == has_gps:
        raise vol.Invalid("exactly one of location or gps is required")
    if has_location:
        if not data[CONF_LOCATION].strip():
            raise vol.Invalid("location must not be empty")
        if CONF_GPS_ACCURACY in data:
            raise vol.Invalid("gps_accuracy is only valid with gps")
    return data


SERVICE_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Required(ATTR_ENTITY_ID): cv.comp_entity_ids,
            vol.Optional(CONF_LOCATION): cv.string,
            vol.Optional(CONF_GPS): {
                vol.Required(ATTR_LATITUDE): vol.All(
                    _reject_boolean_number, cv.latitude
                ),
                vol.Required(ATTR_LONGITUDE): vol.All(
                    _reject_boolean_number, cv.longitude
                ),
                vol.Optional(ATTR_RADIUS): cv.string,
            },
            vol.Optional(CONF_GPS_ACCURACY): vol.All(
                _reject_boolean_number,
                cv.positive_int,
            ),
        }
    ),
    _validate_move_service_data,
)


async def async_setup_scanner(hass, config, async_see, _discovery_info=None):
    """Ignore scanner setup; Virtual Layer entities are config-entry only."""
    _LOGGER.debug("ignoring scanner setup")
    return True


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: Callable[[list], None],
) -> None:
    _LOGGER.debug("setting up the device_tracker entries...")

    entities = []
    for entity in get_entity_configs(
        hass, entry.data[ATTR_GROUP_NAME], PLATFORM_DOMAIN
    ):
        entity = DEVICE_TRACKER_SCHEMA(entity)
        entities.append(VirtualDeviceTracker(entity))
    async_add_entities(entities)

    async def async_virtual_service(call):
        """Call virtual service handler."""
        await _async_verify_target_entity_control(hass, call)
        _LOGGER.debug(f"{call.service} service called")
        if call.service == SERVICE_MOVE:
            await async_virtual_move_service(hass, call)

    # Build up services...
    if PLATFORM_DOMAIN not in hass.data[COMPONENT_SERVICES]:
        _LOGGER.debug("installing handlers")
        hass.data[COMPONENT_SERVICES][PLATFORM_DOMAIN] = "installed"
        hass.services.async_register(
            COMPONENT_DOMAIN,
            SERVICE_MOVE,
            async_virtual_service,
            schema=SERVICE_SCHEMA,
        )


class VirtualDeviceTracker(TrackerEntity, VirtualEntity):
    """Represent a tracked device."""

    def __init__(self, config):
        """Initialize a Virtual Device Tracker."""

        # Handle deprecated option.
        if config.get(CONF_LOCATION, None) is not None:
            _LOGGER.info(
                "'location' option is deprecated for virtual device trackers, please use 'initial_value'"
            )
            config[CONF_INITIAL_VALUE] = config.pop(CONF_LOCATION)

        super().__init__(config, PLATFORM_DOMAIN)

        self._location = None
        self._coords = {}
        self._gps_accuracy = 0
        self._location_helper = self._normalize_location_helper(
            config.get(CONF_LOCATION_HELPER),
        )
        try:
            self._local_presence_config = normalize_local_presence(config[CONF_LOCAL_PRESENCE]) if config.get(CONF_LOCAL_PRESENCE) else None
        except vol.Invalid:
            self._local_presence_config = None
        self._presence_adapter = None
        self._ble_states = {}
        if self._local_presence_config and not self._location_helper:
            self._location_helper = self._normalize_location_helper({})
        self._presence_classification = bool(
            config.get(CONF_PRESENCE_CLASSIFICATION, False)
        )
        self._polygon_config = self._normalize_polygon_config(
            config.get(CONF_POLYGONAL_ZONE),
            config.get(CONF_SOURCE_ENTITIES),
        )
        self._polygon_zones = []
        self._dawarich_config = self._normalize_dawarich_config(
            config.get(CONF_DAWARICH)
        )
        if self._dawarich_config and config.get(CONF_SOURCE_ENTITIES) and not self._location_helper:
            # Explicit GPS sources must participate even when helper generation
            # could not inspect them during setup (for example, offline phones).
            self._location_helper = self._normalize_location_helper({})
        self._dawarich_state = None
        self._dawarich_task = None
        self._dawarich_removed = False
        self._priority_source = None
        self._source_positions = {}
        self._source_last_moved = {}
        self._source_history = {}
        self._source_motion_anchors = {}
        self._source_rejected_positions = {}
        self._source_reported = {}

        _LOGGER.debug(f"{self._attr_name}, available={self._attr_available}")
        _LOGGER.debug(f"{self._attr_name}, entity={self.entity_id}")

    def _create_state(self, config):
        _LOGGER.debug("Creating device tracker state for %s", self.entity_id)
        super()._create_state(config)
        self._location = config.get(CONF_INITIAL_VALUE)
        self._restore_location_helper_attributes()

    def _restore_state(self, state, config):
        _LOGGER.debug("Restoring device tracker state for %s", self.entity_id)
        super()._restore_state(state, config)
        position = self._coordinates_from_state(state)
        if position is not None:
            latitude, longitude = position
            self._location = None
            self._coords = {
                ATTR_LONGITUDE: longitude,
                ATTR_LATITUDE: latitude,
                ATTR_RADIUS: 0,
            }
            self._gps_accuracy = (
                _safe_gps_accuracy(state.attributes.get(CONF_GPS_ACCURACY, 0)) or 0
            )
        else:
            self._location = self._restored_state_value(state, config)
            self._coords = {}
            self._gps_accuracy = 0
        saved_source = self._virtual_attributes.get(ATTR_DAWARICH_SOURCE_ID)
        if self._virtual_attributes.get(ATTR_DAWARICH_POINT) and (
            not self._dawarich_config or saved_source != self._dawarich_source_id()
        ):
            # Editing the account/member must not restore another person's
            # location or reject their older point as an out-of-order update.
            selected = self._virtual_attributes.get(ATTR_LOCATION_PRIORITY_SOURCE)
            polygon_selected = self._virtual_attributes.get(ATTR_POLYGON_SELECTED_SOURCE)
            for key in list(self._virtual_attributes):
                if key.startswith("dawarich_"):
                    self._virtual_attributes.pop(key)
            for key in (ATTR_LOCATION_SOURCE_OBSERVATIONS, ATTR_LOCATION_SOURCE_POSITIONS, ATTR_LOCATION_SOURCE_LAST_MOVED):
                saved = self._virtual_attributes.get(key)
                if isinstance(saved, dict):
                    self._virtual_attributes[key] = {k: v for k, v in saved.items() if k != DAWARICH_SOURCE}
            if selected == DAWARICH_SOURCE:
                self._virtual_attributes[ATTR_LOCATION_PRIORITY_SOURCE] = None
            if (not self._location_helper and not self._polygon_config) or selected == DAWARICH_SOURCE or polygon_selected == DAWARICH_SOURCE:
                self._coords = {}
                self._location = config.get(CONF_INITIAL_VALUE, "not_home")
                self._gps_accuracy = 0
        self._restore_location_helper_attributes()
        if self._dawarich_config:
            raw = self._virtual_attributes.get(ATTR_DAWARICH_POINT)
            if isinstance(raw, dict) and (point := valid_dawarich_point(raw)) is not None:
                self._set_dawarich_sample(point)
            # A saved coordinate is not evidence of a successful current poll.
            self._virtual_attributes[ATTR_DAWARICH_STALE] = True

    @staticmethod
    def _normalize_dawarich_config(value):
        """Invalid legacy settings must not prevent loading/editing the tracker."""
        if not isinstance(value, dict):
            return None
        try:
            return normalize_dawarich_config(value)
        except vol.Invalid:
            _LOGGER.warning("Ignoring invalid Dawarich configuration")
            return None

    @staticmethod
    def _normalize_location_helper(value):
        """Return a safe policy for a stored location helper configuration."""
        if not isinstance(value, dict):
            return None

        raw_distance_threshold = value.get(
            CONF_LOCATION_HELPER_DISTANCE_METERS,
            DEFAULT_LOCATION_HELPER_DISTANCE_METERS,
        )
        raw_priority_window = value.get(
            CONF_LOCATION_HELPER_PRIORITY_WINDOW_SECONDS,
            DEFAULT_LOCATION_HELPER_PRIORITY_WINDOW_SECONDS,
        )
        if isinstance(raw_distance_threshold, bool) or isinstance(
            raw_priority_window, bool
        ):
            _LOGGER.warning("Ignoring invalid location helper configuration")
            return None
        try:
            distance_threshold = float(raw_distance_threshold)
            priority_window = int(raw_priority_window)
        except (TypeError, ValueError, OverflowError):
            _LOGGER.warning("Ignoring invalid location helper configuration")
            return None

        if (
            not isfinite(distance_threshold)
            or distance_threshold <= 0
            or priority_window <= 0
        ):
            _LOGGER.warning("Ignoring invalid location helper configuration")
            return None

        return {
            CONF_LOCATION_HELPER_DISTANCE_METERS: distance_threshold,
            CONF_LOCATION_HELPER_PRIORITY_WINDOW_SECONDS: priority_window,
        }

    @staticmethod
    def _normalize_polygon_config(value, source_entities=None):
        """Return a safe polygon configuration without blocking old settings."""
        if not isinstance(value, dict):
            return None
        value = repair_legacy_template_data(value)
        try:
            polygon_validation_config = {CONF_POLYGONAL_ZONE: value}
            if source_entities is not None:
                polygon_validation_config[CONF_SOURCE_ENTITIES] = source_entities
            validate_domain_options(polygon_validation_config)
        except vol.Invalid as err:
            _LOGGER.warning("Ignoring invalid polygonal zone configuration: %s", err)
            return None
        normalized = dict(value)
        normalized.setdefault(CONF_POLYGON_STRATEGY, "majority")
        normalized.setdefault(CONF_POLYGON_AWAY_STATE, "not_home")
        normalized.setdefault(
            CONF_POLYGON_DISTANCE_METERS,
            DEFAULT_LOCATION_HELPER_DISTANCE_METERS,
        )
        normalized.setdefault(CONF_POLYGON_TRACKER_RULES, {})
        normalized.setdefault(CONF_POLYGON_FILES, [])
        normalized.setdefault(CONF_POLYGON_ESPRESENSE_ANCHORS, {})
        return normalized

    def _restore_location_helper_attributes(self):
        """Restore the selected source so its priority survives restarts."""
        if not self._location_helper and not (
            self._polygon_config and self._polygon_config[CONF_POLYGON_STRATEGY] == "adaptive"
        ):
            return
        source_entities = (
            self._polygon_source_entities() if self._polygon_config
            else self._location_source_entities()
        )
        priority_source = self._virtual_attributes.get(ATTR_LOCATION_PRIORITY_SOURCE)
        if priority_source in source_entities:
            self._priority_source = priority_source

        source_positions = self._virtual_attributes.get(ATTR_LOCATION_SOURCE_POSITIONS)
        if isinstance(source_positions, dict):
            for entity_id, position in source_positions.items():
                if entity_id not in source_entities or not isinstance(position, list):
                    continue
                if len(position) != 2:
                    continue
                try:
                    latitude, longitude = self._validated_coordinates(*position)
                except (TypeError, ValueError, OverflowError):
                    continue
                self._source_positions[entity_id] = (latitude, longitude)

        observations = self._virtual_attributes.get(ATTR_LOCATION_SOURCE_OBSERVATIONS)
        if isinstance(observations, dict):
            for entity_id, observation in observations.items():
                if entity_id not in source_entities or not isinstance(observation, dict):
                    continue
                try:
                    raw_position = observation["position"]
                    raw_anchor = observation["anchor"]
                    if not isinstance(raw_position, list) or not isinstance(raw_anchor, list):
                        continue
                    position = self._validated_coordinates(*raw_position)
                    anchor = self._validated_coordinates(*raw_anchor)
                    raw_timestamp = observation["timestamp"]
                    if isinstance(raw_timestamp, bool):
                        continue
                    timestamp = datetime.fromtimestamp(float(raw_timestamp), tz=timezone.utc)
                    raw_reported = observation.get("reported", raw_timestamp)
                    if isinstance(raw_reported, bool):
                        continue
                    reported = datetime.fromtimestamp(float(raw_reported), tz=timezone.utc)
                    accuracy = _safe_gps_accuracy(observation["accuracy"])
                    anchor_accuracy = _safe_gps_accuracy(observation["anchor_accuracy"])
                    if not timestamp <= reported <= dt_util.utcnow() or accuracy is None or anchor_accuracy is None:
                        continue
                except (KeyError, TypeError, ValueError, OSError, OverflowError):
                    continue
                self._source_history[entity_id] = [(position, timestamp, accuracy)]
                self._source_reported[entity_id] = reported
                self._source_motion_anchors[entity_id] = (anchor, anchor_accuracy)
                self._source_positions[entity_id] = position
                rejected_position = observation.get("rejected_position")
                if isinstance(rejected_position, list):
                    try:
                        self._source_rejected_positions[entity_id] = self._validated_coordinates(*rejected_position)
                    except (TypeError, ValueError, OverflowError):
                        pass

        source_last_moved = self._virtual_attributes.get(
            ATTR_LOCATION_SOURCE_LAST_MOVED
        )
        if isinstance(source_last_moved, dict):
            for entity_id, timestamp in source_last_moved.items():
                # Old versions marked first observations as movement. Only new,
                # validated observation records can restore confirmed motion.
                if entity_id not in self._source_history:
                    continue
                try:
                    if isinstance(timestamp, bool):
                        raise TypeError
                    moved = datetime.fromtimestamp(
                        float(timestamp),
                        tz=timezone.utc,
                    )
                    if moved <= self._source_history[entity_id][-1][1]:
                        self._source_last_moved[entity_id] = moved
                except (TypeError, ValueError, OSError, OverflowError):
                    continue

    async def async_added_to_hass(self) -> None:
        """Start aggregate GPS tracking after normal virtual setup."""
        await super().async_added_to_hass()
        if self._dawarich_config:
            self._refresh_remove_listeners.append(
                async_track_time_interval(
                    self.hass,
                    self._async_refresh_dawarich,
                    timedelta(
                        seconds=self._dawarich_config[CONF_DAWARICH_POLL_INTERVAL]
                    ),
                )
            )
            await self._async_refresh_dawarich()
        if self._polygon_config:
            await self._async_setup_polygon_tracking()
            return
        if not self._location_helper:
            return

        source_entities = set(self._location_source_entities())
        source_entities.discard(DAWARICH_SOURCE)
        source_entities = {entity_id for entity_id in source_entities if not entity_id.startswith(BLE_PREFIX)}
        if source_entities:
            self._refresh_remove_listeners.append(
                async_track_state_change_event(
                    self.hass,
                    source_entities,
                    self._async_location_source_changed,
                )
            )
        # Re-evaluate the 30 minute priority window even when no source emits
        # a new state event.
        self._refresh_remove_listeners.append(
            async_track_time_interval(
                self.hass,
                self._async_location_source_changed,
                timedelta(seconds=5 if self._local_presence_config else 60),
            )
        )
        self._update_location_from_sources()

    _dawarich_points = staticmethod(dawarich_records)
    _dawarich_family_point = staticmethod(family_point)
    _dawarich_point_summary = staticmethod(dawarich_summary)

    @staticmethod
    def _dawarich_point_timestamp(point):
        return dawarich_point_time(point) or datetime.min.replace(tzinfo=timezone.utc)

    def _dawarich_person_name(self):
        person_id = self._dawarich_config.get(CONF_DAWARICH_PERSON_ENTITY, "")
        person = self.hass.states.get(person_id) if person_id else None
        return str(person.name if person else "").strip()

    def _dawarich_source_id(self):
        """Opaque restore identity; never publish URL, API key or member."""
        identity = {
            key: self._dawarich_config.get(key, "") for key in (
                CONF_DAWARICH_URL, CONF_DAWARICH_API_KEY,
                CONF_DAWARICH_MEMBER, CONF_DAWARICH_PERSON_ENTITY,
            )
        }
        return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()

    def _set_dawarich_sample(self, point):
        """Use measurement time, never poll time, as the source's GPS clock."""
        measured = dawarich_point_time(point)
        self._dawarich_state = State(
            "device_tracker.virtual_layer_dawarich", "not_home",
            {ATTR_LATITUDE: point["latitude"], ATTR_LONGITUDE: point["longitude"],
             CONF_GPS_ACCURACY: point["accuracy"], "source_type": "gps", "last_seen": measured.isoformat()},
            last_changed=measured, last_updated=measured, last_reported=measured,
        )

    async def _async_refresh_dawarich(self, _now=None) -> None:
        """Serialize refreshes and publish a complete validated snapshot."""
        if not self._dawarich_config or self._dawarich_removed or self._dawarich_task:
            return
        self._dawarich_task = asyncio.current_task()
        try:
            client = DawarichClient(async_get_clientsession(self.hass), self._dawarich_config)
            snapshot = await client.async_fetch(self._dawarich_person_name())
            if self._dawarich_removed:
                return
            measured = dawarich_point_time(snapshot.point)
            previous = self._dawarich_state.last_updated if self._dawarich_state else None
            if previous is not None and measured < previous:
                raise DawarichError("older_point")
            self._set_dawarich_sample(snapshot.point)
            now = dt_util.utcnow()
            self._virtual_attributes.update({
                ATTR_DAWARICH_LAST_UPDATED: now.isoformat(),
                ATTR_DAWARICH_POINT_TIME: measured.isoformat(),
                ATTR_DAWARICH_SOURCE_ID: self._dawarich_source_id(),
                ATTR_DAWARICH_POINT: snapshot.point,
                ATTR_DAWARICH_HISTORY: snapshot.history,
                ATTR_DAWARICH_VISIT: snapshot.visit,
                ATTR_DAWARICH_ERROR: None,
                ATTR_DAWARICH_VISIT_ERROR: snapshot.visit_error,
                ATTR_DAWARICH_ACTIVITY: snapshot.analysis or {},
                ATTR_DAWARICH_STALE: now - measured > timedelta(
                    seconds=self._location_policy()[CONF_LOCATION_HELPER_PRIORITY_WINDOW_SECONDS]
                ),
            })
            # Polygon resolution and local-source aggregation consume Dawarich
            # as a source; independent timers must not overwrite each other.
            if self._polygon_config:
                self._update_polygon_from_sources()
            elif self._location_helper:
                self._update_location_from_sources()
            else:
                self._update_attributes()
                self.move_to_coords({ATTR_LATITUDE: snapshot.point["latitude"],
                                     ATTR_LONGITUDE: snapshot.point["longitude"], ATTR_RADIUS: 0},
                                    snapshot.point["accuracy"])
        except DawarichError as err:
            if self._dawarich_removed:
                return
            # HTTP exception strings may contain an api_key query parameter.
            _LOGGER.warning("Unable to refresh Dawarich for %s (%s)", self.entity_id, err.code)
            self._virtual_attributes.update({ATTR_DAWARICH_ERROR: err.code, ATTR_DAWARICH_STALE: True})
            self._update_attributes()
            self._schedule_state_update()
        finally:
            self._dawarich_task = None

    async def async_will_remove_from_hass(self) -> None:
        self._dawarich_removed = True
        task = self._dawarich_task
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await super().async_will_remove_from_hass()

    async def _async_setup_polygon_tracking(self) -> None:
        """Load polygon definitions and start source aggregation."""
        await self._async_reload_polygon_zones(keep_existing=False)

        source_entities = set(self._polygon_source_entities())
        source_entities.discard(DAWARICH_SOURCE)
        source_entities = {entity_id for entity_id in source_entities if not entity_id.startswith(BLE_PREFIX)}
        if source_entities:
            self._refresh_remove_listeners.append(
                async_track_state_change_event(
                    self.hass,
                    source_entities,
                    self._async_polygon_source_changed,
                )
            )
        anchors = self._polygon_config.get(CONF_POLYGON_ESPRESENSE_ANCHORS, {})
        refresh_seconds = min(
            [5.0] + [
                float(anchor.get("max_age_seconds", DEFAULT_ESPRESENSE_MAX_AGE_SECONDS))
                for anchor in anchors.values()
            ]
        ) if anchors else (5.0 if self._local_presence_config else 60.0)
        self._refresh_remove_listeners.append(
            async_track_time_interval(
                self.hass,
                self._async_polygon_source_changed,
                timedelta(seconds=max(1.0, refresh_seconds)),
            )
        )
        if self._polygon_config.get(CONF_POLYGON_FILES):
            self._refresh_remove_listeners.append(
                async_track_time_interval(
                    self.hass,
                    self._async_reload_polygon_zones,
                    POLYGON_FILE_RELOAD_INTERVAL,
                )
            )
        self._update_polygon_from_sources()

    async def _async_reload_polygon_zones(
        self,
        _now=None,
        *,
        keep_existing=True,
    ) -> None:
        """Reload file-backed zones while preserving working data on failures."""
        try:
            zones, load_errors = await load_polygon_zones(
                self.hass,
                self._polygon_config.get(CONF_POLYGON_GEOJSON),
                self._polygon_config.get(CONF_POLYGON_FILES),
                return_errors=True,
            )
            if (
                not keep_existing
                or not self._polygon_zones
                or (zones and not load_errors)
            ):
                self._polygon_zones = zones
            self._virtual_attributes[ATTR_POLYGON_LOAD_ERROR] = (
                "; ".join(load_errors) if load_errors else None
            )
        except (
            asyncio.TimeoutError,
            ClientError,
            OSError,
            TypeError,
            ValueError,
        ) as err:
            _LOGGER.error(
                "Unable to load polygon zones for %s: %s", self.entity_id, err
            )
            if not keep_existing:
                self._polygon_zones = []
            self._virtual_attributes[ATTR_POLYGON_LOAD_ERROR] = str(err)
        if _now is not None:
            self._update_polygon_from_sources()

    def _polygon_source_entities(self) -> list[str]:
        """Return explicit trackers, falling back to the configured person."""
        source_entities = [
            entity_id for entity_id in self._location_source_entities()
            if entity_id != DAWARICH_SOURCE
        ]
        dawarich = [DAWARICH_SOURCE] if self._dawarich_config else []
        if source_entities:
            return list(
                dict.fromkeys(
                    source_entities + dawarich
                    + list(
                        self._polygon_config.get(CONF_POLYGON_ESPRESENSE_ANCHORS, {})
                    )
                )
            )
        person = self._polygon_config.get(CONF_POLYGON_PERSON_ENTITY)
        sources = [person] if person and person != self.entity_id else []
        return sources + dawarich + list(
            self._polygon_config.get(CONF_POLYGON_ESPRESENSE_ANCHORS, {})
        )

    def _espresense_position(self):
        """Triangulate GPS coordinates from three or more anchor distances."""
        anchors = self._polygon_config.get(CONF_POLYGON_ESPRESENSE_ANCHORS, {})
        samples = []
        now = dt_util.utcnow()
        for entity_id, anchor in anchors.items():
            state = self.hass.states.get(entity_id)
            if state is None or state.state in {STATE_UNKNOWN, STATE_UNAVAILABLE}:
                continue
            max_age = float(
                anchor.get("max_age_seconds", DEFAULT_ESPRESENSE_MAX_AGE_SECONDS)
            )
            if now - state.last_updated >= timedelta(seconds=max_age):
                continue
            # Explicit metre measurements are independent of the state unit.
            if "distance_meters" in state.attributes:
                raw = state.attributes["distance_meters"]
                unit = "m"
            else:
                raw = state.attributes.get("distance", state.state)
                unit = str(state.attributes.get("unit_of_measurement", "m")).strip().lower()
            try:
                distance = float(raw)
                latitude, longitude = self._validated_coordinates(
                    anchor[ATTR_LATITUDE], anchor[ATTR_LONGITUDE]
                )
            except (TypeError, ValueError, OverflowError, KeyError):
                continue
            unit_scale = {"m": 1, "cm": 0.01, "mm": 0.001, "ft": 0.3048}.get(unit)
            if unit_scale is None:
                continue
            distance = distance * unit_scale * float(
                anchor.get("distance_scale", 1)
            ) + float(anchor.get("distance_offset", 0))
            if isfinite(distance) and distance >= 0:
                samples.append(
                    (entity_id, latitude, longitude, distance, state.last_updated)
                )
        if len(samples) < 3:
            return None
        origin_lat, origin_lon = samples[0][1:3]
        scale_x = 111_320 * cos(radians(origin_lat))
        scale_y = 110_540
        if abs(scale_x) < 1e-6:
            return None
        d1 = samples[0][3]
        rows = []
        for _entity_id, latitude, longitude, distance, _updated in samples[1:]:
            x = (longitude - origin_lon) * scale_x
            y = (latitude - origin_lat) * scale_y
            rows.append((2 * x, 2 * y, x * x + y * y + d1 * d1 - distance * distance))
        a = sum(x * x for x, _y, _b in rows)
        b = sum(x * y for x, y, _b in rows)
        c = sum(y * y for _x, y, _b in rows)
        dx = sum(x * value for x, _y, value in rows)
        dy = sum(y * value for _x, y, value in rows)
        determinant = a * c - b * b
        if abs(determinant) < 1e-6:
            return None
        x = (dx * c - b * dy) / determinant
        y = (a * dy - b * dx) / determinant
        try:
            position = self._validated_coordinates(
                origin_lat + y / scale_y, origin_lon + x / scale_x
            )
        except ValueError:
            return None
        residuals = []
        for _entity_id, latitude, longitude, distance, _updated in samples:
            anchor_x = (longitude - origin_lon) * scale_x
            anchor_y = (latitude - origin_lat) * scale_y
            residuals.append(
                abs(sqrt((x - anchor_x) ** 2 + (y - anchor_y) ** 2) - distance)
            )
        accuracy = max(
            1.0, sqrt(sum(value * value for value in residuals) / len(residuals))
        )
        return {
            "position": position,
            "accuracy": accuracy,
            "sources": [sample[0] for sample in samples],
        }

    @callback
    def _async_polygon_source_changed(self, _event) -> None:
        self._update_polygon_from_sources()

    def _polygon_rule_matches(self, entity_id, state, rule) -> bool:
        if rule.get("enabled", True) is False:
            return False
        now = dt_util.utcnow()
        observed = self._source_observation_time(state)
        max_age = rule.get("max_age_seconds")
        if max_age is not None and (observed is None or not timedelta(0) <= now - observed <= timedelta(
            seconds=float(max_age)
        )):
            return False
        accuracy = self._source_accuracy(state)
        if accuracy is None:
            return False
        max_accuracy = rule.get("max_gps_accuracy")
        if max_accuracy is not None and accuracy > float(max_accuracy):
            return False
        condition = rule.get("condition_template")
        if condition:
            try:
                person_entity_id = self._polygon_config.get(
                    CONF_POLYGON_PERSON_ENTITY,
                )
                result = Template(condition, self.hass).async_render(
                    variables={
                        "source": state,
                        "source_entity_id": entity_id,
                        "person": (
                            self.hass.states.get(person_entity_id)
                            if person_entity_id
                            else None
                        ),
                        "this": self.hass.states.get(self.entity_id),
                    },
                    parse_result=True,
                )
                if not cv.boolean(result):
                    return False
            except (TemplateError, TypeError, ValueError) as err:
                _LOGGER.warning("Ignoring polygon source %s: %s", entity_id, err)
                return False
        return True

    @callback
    def _update_polygon_from_sources(self) -> None:
        """Aggregate source positions and resolve the selected polygon zone."""
        if not self._polygon_config:
            return
        self._refresh_local_presence()
        self._update_motion_attributes(None)
        rules = self._polygon_config[CONF_POLYGON_TRACKER_RULES]
        samples = []
        for entity_id in self._polygon_source_entities():
            state = self._location_source_state(entity_id)
            if (
                state is None
                or state.state in {STATE_UNAVAILABLE, STATE_UNKNOWN}
                or (position := self._position_from_state(self.hass, state)) is None
                or (observed := self._source_observation_time(state)) is None
                or observed > dt_util.utcnow()
            ):
                continue
            rule = rules.get(entity_id, {})
            if not self._polygon_rule_matches(entity_id, state, rule):
                continue
            try:
                accuracy = self._source_accuracy(state)
                if accuracy is None:
                    continue
                samples.append(
                    {
                        "entity_id": entity_id,
                        "latitude": position[0],
                        "longitude": position[1],
                        "gps_accuracy": accuracy,
                        "last_updated": observed,
                        "dominant": bool(rule.get("dominant", False)),
                        "priority": float(rule.get("priority", 100)),
                        "weight": float(rule.get("weight", 1)),
                    }
                )
            except (TypeError, ValueError, OverflowError):
                continue

        espresense = self._espresense_position()
        self._virtual_attributes.update(
            {
                ATTR_ESPRESENSE_POSITION: (
                    list(espresense["position"]) if espresense else None
                ),
                ATTR_ESPRESENSE_SOURCES: espresense["sources"] if espresense else [],
                ATTR_ESPRESENSE_ACCURACY: espresense["accuracy"]
                if espresense
                else None,
            }
        )
        if espresense is not None:
            position = espresense["position"]
            samples.append(
                {
                    "entity_id": "espresense_triangulated",
                    "latitude": position[0],
                    "longitude": position[1],
                    "gps_accuracy": espresense["accuracy"],
                    "last_updated": dt_util.utcnow(),
                    "dominant": True,
                    "priority": -1,
                    "weight": 1,
                }
            )

        if self._polygon_config[CONF_POLYGON_STRATEGY] == "adaptive" and not any(
            sample.get("dominant") for sample in samples
        ):
            selected = self._adaptive_polygon_position(samples)
        else:
            selected = select_tracker_position(
                samples,
                self._polygon_config[CONF_POLYGON_STRATEGY],
                float(self._polygon_config[CONF_POLYGON_DISTANCE_METERS]),
            )
            if self._polygon_config[CONF_POLYGON_STRATEGY] == "adaptive":
                self._virtual_attributes.update({
                    ATTR_LOCATION_SELECTION_REASON: "dominant",
                    ATTR_LOCATION_STALE: False,
                })
        self._virtual_attributes.update(
            {
                ATTR_POLYGON_PERSON: self._polygon_config.get(
                    CONF_POLYGON_PERSON_ENTITY
                ),
                ATTR_POLYGON_STRATEGY: self._polygon_config[CONF_POLYGON_STRATEGY],
                ATTR_POLYGON_ZONES: [zone["name"] for zone in self._polygon_zones],
                ATTR_POLYGON_SELECTION_REASON: selected["reason"] if selected else None,
                ATTR_POLYGON_SELECTED_SOURCE: selected["selected_source"]
                if selected
                else None,
                ATTR_POLYGON_SELECTED_MEMBERS: selected["members"] if selected else [],
            }
        )
        if selected is None:
            self._virtual_attributes[ATTR_POLYGON_ZONE] = None
            self._update_attributes()
            missing_presence = self._local_presence_config and any(
                (state := self._location_source_state(source)) is None
                or state.state in {STATE_UNKNOWN, STATE_UNAVAILABLE}
                for source in self._polygon_source_entities()
            )
            self.move_to_location(
                STATE_UNKNOWN if missing_presence else self._polygon_config[CONF_POLYGON_AWAY_STATE]
            )
            return

        zone = find_polygon_zone(
            selected["latitude"],
            selected["longitude"],
            selected["gps_accuracy"],
            self._polygon_zones,
        )
        location = (
            zone["name"] if zone else self._polygon_config[CONF_POLYGON_AWAY_STATE]
        )
        self._virtual_attributes[ATTR_POLYGON_ZONE] = zone["name"] if zone else None
        self._update_attributes()
        self._location = location
        self._coords = {
            ATTR_LATITUDE: selected["latitude"],
            ATTR_LONGITUDE: selected["longitude"],
            ATTR_RADIUS: 0,
        }
        self._gps_accuracy = selected["gps_accuracy"]
        self._schedule_state_update()

    @callback
    def _async_location_source_changed(self, _event) -> None:
        self._update_location_from_sources()

    @staticmethod
    def _coordinates_from_state(state):
        """Read usable GPS coordinates from a Home Assistant state."""
        raw_latitude = state.attributes.get(ATTR_LATITUDE, state.attributes.get("lat"))
        raw_longitude = state.attributes.get(ATTR_LONGITUDE, state.attributes.get("lon"))
        if isinstance(raw_latitude, bool) or isinstance(raw_longitude, bool):
            return None
        try:
            latitude = float(raw_latitude)
            longitude = float(raw_longitude)
        except (KeyError, TypeError, ValueError, OverflowError):
            return None

        if (
            not isfinite(latitude)
            or not isfinite(longitude)
            or not -90 <= latitude <= 90
            or not -180 <= longitude <= 180
        ):
            return None
        return latitude, longitude

    @staticmethod
    def _has_observation_time(state):
        return any(state.attributes.get(key) is not None for key in ("last_seen", "last_timestamp"))

    @staticmethod
    def _source_observation_time(state):
        """Honor source measurement clocks; malformed explicit clocks fail closed.

        Composite Tracker-compatible timestamps may be epoch seconds, ISO text
        or datetime objects. HA interprets naive datetimes in its local zone.
        """
        raw = next((state.attributes[key] for key in ("last_seen", "last_timestamp")
                    if state.attributes.get(key) is not None), None)
        if raw is None:
            return state.last_updated
        if isinstance(raw, bool):
            return None
        try:
            if isinstance(raw, datetime):
                return dt_util.as_utc(raw)
            if not isinstance(raw, (str, int, float)):
                return None
            try:
                value = float(raw)
            except (TypeError, ValueError):
                parsed = dt_util.parse_datetime(raw)
                return dt_util.as_utc(parsed) if parsed is not None else None
            if not isfinite(value):
                return None
            return dt_util.utc_from_timestamp(value)
        except (TypeError, ValueError, OverflowError, OSError):
            return None

    @classmethod
    def _source_report_time(cls, state):
        if cls._has_observation_time(state):
            return cls._source_observation_time(state)
        return getattr(state, "last_reported", state.last_updated)

    @staticmethod
    def _source_accuracy(state):
        return _safe_gps_accuracy(state.attributes.get(CONF_GPS_ACCURACY, state.attributes.get("acc", 0)))

    @staticmethod
    def _zone_matches(zone_state, location_name) -> bool:
        """Return true when a zone state represents a source's location name."""
        wanted = slugify(str(location_name))
        if not wanted:
            return False
        candidates = {
            zone_state.entity_id.split(".", 1)[1],
            zone_state.name,
            zone_state.attributes.get(ATTR_FRIENDLY_NAME),
        }
        return any(
            slugify(str(candidate)) == wanted for candidate in candidates if candidate
        )

    @classmethod
    def _coordinates_from_location_state(cls, hass, location_name):
        """Resolve a named Home Assistant zone to GPS coordinates."""
        if str(location_name).lower() in {
            "",
            "none",
            "not_home",
            "unknown",
            "unavailable",
        }:
            return None

        for zone_state in hass.states.async_all("zone"):
            if cls._zone_matches(zone_state, location_name):
                return cls._coordinates_from_state(zone_state)
        return None

    @classmethod
    def _position_from_state(cls, hass, state):
        """Read source coordinates directly or from its named zone state."""
        position = cls._coordinates_from_state(
            state
        ) or cls._coordinates_from_location_state(hass, state.state)
        if position is None and state.state == "home" and state.attributes.get("source_type") in {"router", "bluetooth", "bluetooth_le"}:
            try:
                return cls._validated_coordinates(hass.config.latitude, hass.config.longitude)
            except (TypeError, ValueError, OverflowError):
                return None
        return position

    def _adaptive_polygon_position(self, samples):
        """Apply the same carried-device policy before resolving polygon zones."""
        rules = self._polygon_config[CONF_POLYGON_TRACKER_RULES]
        states = {}
        for entity_id in self._polygon_source_entities():
            state = self._location_source_state(entity_id)
            rule = rules.get(entity_id, {})
            if state is None:
                # A missing HA state is a lost report, not removal from the
                # configuration. Explicit filters still exclude that source.
                if rule.get("enabled", True) and not any(
                    key in rule for key in ("condition_template", "max_age_seconds", "max_gps_accuracy")
                ):
                    states[entity_id] = None
            elif self._polygon_rule_matches(entity_id, state, rule):
                states[entity_id] = state
        positions = self._record_source_movements({
            sample["entity_id"]: (sample["latitude"], sample["longitude"])
            for sample in samples if sample["entity_id"] in states
        }, states)
        selected, reason, stale = self._select_location_source(positions, states, dt_util.utcnow())
        self._priority_source = selected
        self._store_location_observations()
        self._update_motion_attributes(selected, stale)
        self._virtual_attributes.update({
            ATTR_LOCATION_PRIORITY_SOURCE: selected,
            ATTR_LOCATION_SELECTION_REASON: reason,
            ATTR_LOCATION_STALE: stale,
        })
        if selected:
            position = self._source_positions[selected]
            history = self._source_history.get(selected)
            return {
                "latitude": position[0], "longitude": position[1],
                "gps_accuracy": history[-1][2] if history else 0,
                "selected_source": selected, "members": [selected], "reason": reason,
            }
        valid = [sample for sample in samples if sample["entity_id"] in positions]
        result = select_tracker_position(valid, "median")
        if result:
            center = (result["latitude"], result["longitude"])
            result["gps_accuracy"] = max(
                self._distance_meters(center, positions[sample["entity_id"]])
                + sample["gps_accuracy"] for sample in valid
            )
        return result

    def _location_source_entities(self) -> list[str]:
        """Return configured sources without the virtual tracker itself."""
        local = self._local_presence_config or {}
        return list(dict.fromkeys([
            entity_id
            for entity_id in [*self._source_entities, *local.get("wifi_entities", [])]
            if entity_id != self.entity_id
        ] + ([DAWARICH_SOURCE] if self._dawarich_config else [])
        + [BLE_PREFIX + address for address in local.get("ble_addresses", [])]))

    def _refresh_local_presence(self):
        if not self._local_presence_config:
            return
        if self._presence_adapter is None:
            self._presence_adapter = LocalPresence(self.hass, self._local_presence_config)
        self._ble_states = self._presence_adapter.ble_states()
        self._virtual_attributes["local_presence_error"] = self._presence_adapter.error

    def _location_source_state(self, entity_id):
        if entity_id.startswith(BLE_PREFIX):
            return self._ble_states.get(entity_id)
        if self._presence_adapter and entity_id in self._local_presence_config["wifi_entities"]:
            return self._presence_adapter.wifi_state(entity_id)
        return self._dawarich_state if entity_id == DAWARICH_SOURCE else self.hass.states.get(entity_id)

    @staticmethod
    def _median(values: list[float]) -> float:
        ordered = sorted(values)
        midpoint = len(ordered) // 2
        if len(ordered) % 2:
            return ordered[midpoint]
        return (ordered[midpoint - 1] + ordered[midpoint]) / 2

    @staticmethod
    def _distance_meters(first, second) -> float:
        """Return great-circle distance for two (latitude, longitude) pairs."""
        latitude_1, longitude_1 = map(radians, first)
        latitude_2, longitude_2 = map(radians, second)
        latitude_delta = latitude_2 - latitude_1
        longitude_delta = longitude_2 - longitude_1
        value = (
            sin(latitude_delta / 2) ** 2
            + cos(latitude_1) * cos(latitude_2) * sin(longitude_delta / 2) ** 2
        )
        return 6_371_000 * 2 * asin(sqrt(max(0.0, min(1.0, value))))

    def _location_policy(self):
        """Share movement inference with the opt-in polygon strategy."""
        policy = self._location_helper or {
            CONF_LOCATION_HELPER_DISTANCE_METERS: DEFAULT_LOCATION_HELPER_DISTANCE_METERS,
            CONF_LOCATION_HELPER_PRIORITY_WINDOW_SECONDS: DEFAULT_LOCATION_HELPER_PRIORITY_WINDOW_SECONDS,
        }
        if self._polygon_config and self._polygon_config[CONF_POLYGON_STRATEGY] == "adaptive":
            return {
                **policy,
                CONF_LOCATION_HELPER_DISTANCE_METERS: self._polygon_config[CONF_POLYGON_DISTANCE_METERS],
            }
        return policy

    def _record_source_movements(self, positions, source_states) -> dict:
        """Accept plausible observations; a first observation is not movement.

        Keep a bounded path in memory. Persist only its last point and motion
        anchor, so reloads preserve evidence without publishing travel history.
        Attribute-only updates never advance the GPS observation clock.
        """
        now = dt_util.utcnow()
        policy = self._location_policy()
        window = timedelta(seconds=policy[CONF_LOCATION_HELPER_PRIORITY_WINDOW_SECONDS])
        max_accuracy = max(300, policy[CONF_LOCATION_HELPER_DISTANCE_METERS])
        accepted = {}
        rejected = {
            entity_id: "unavailable" for entity_id, state in source_states.items()
            if state is None or state.state in {STATE_UNKNOWN, STATE_UNAVAILABLE}
        }
        for entity_id, state in source_states.items():
            if state is not None:
                reported = self._source_report_time(state)
                if reported is None:
                    rejected[entity_id] = "invalid_timestamp"
                elif not timedelta(0) <= now - reported <= window:
                    rejected[entity_id] = "future_timestamp" if reported > now else "stale"
        for mapping in (
            self._source_positions, self._source_last_moved, self._source_history,
            self._source_motion_anchors, self._source_rejected_positions, self._source_reported,
        ):
            for entity_id in set(mapping) - set(source_states):
                mapping.pop(entity_id, None)
        for entity_id, position in positions.items():
            state = source_states[entity_id]
            timestamp = self._source_observation_time(state)
            reported = self._source_report_time(state)
            accuracy = self._source_accuracy(state)
            reason = None
            if state.state in {STATE_UNKNOWN, STATE_UNAVAILABLE}:
                reason = "unavailable"
            elif timestamp is None or reported is None:
                reason = "invalid_timestamp"
            elif timestamp > now or reported > now:
                reason = "future_timestamp"
            elif not timedelta(0) <= now - reported <= window:
                reason = "stale"
            elif accuracy is None or accuracy > max_accuracy:
                reason = "accuracy"
            if reason:
                rejected[entity_id] = reason
                continue

            # Zone centers and network/radio presence are useful fallback
            # locations, but cannot demonstrate a physical GPS trajectory.
            gps = (
                self._coordinates_from_state(state) is not None
                and state.attributes.get("source_type", "gps") == "gps"
            )
            history = self._source_history.setdefault(entity_id, [])
            if not gps:
                history.clear()
                self._source_last_moved.pop(entity_id, None)
                self._source_motion_anchors.pop(entity_id, None)
            if history and gps:
                previous, previous_time, previous_accuracy = history[-1]
                distance = self._distance_meters(previous, position)
                elapsed = (timestamp - self._source_reported.get(entity_id, previous_time)).total_seconds()
                if timestamp < previous_time or (position != previous and elapsed <= 0):
                    rejected[entity_id] = "out_of_order"
                    continue
                if position == self._source_rejected_positions.get(entity_id):
                    rejected[entity_id] = "implausible_speed"
                    continue
                if (
                    position != previous
                    and max(0, distance - accuracy - previous_accuracy)
                    > MAX_LOCATION_SPEED_METERS_PER_SECOND * max(1, elapsed)
                ):
                    self._source_rejected_positions[entity_id] = position
                    rejected[entity_id] = "implausible_speed"
                    continue
                anchor, anchor_accuracy = self._source_motion_anchors.get(
                    entity_id, (previous, previous_accuracy)
                )
                if position != previous and self._distance_meters(anchor, position) > max(
                    LOCATION_CHANGE_DISTANCE_METERS, accuracy + anchor_accuracy
                ):
                    self._source_last_moved[entity_id] = timestamp
                    self._source_motion_anchors[entity_id] = (position, accuracy)
            if gps and (not history or history[-1][0] != position or (
                self._has_observation_time(state) and timestamp > self._source_reported.get(entity_id, history[-1][1])
            )):
                history.append((position, timestamp, accuracy))
                del history[:-LOCATION_HISTORY_LIMIT]
                self._source_motion_anchors.setdefault(entity_id, (position, accuracy))
            elif gps and history:
                history[-1] = (position, history[-1][1], accuracy)
            self._source_rejected_positions.pop(entity_id, None)
            self._source_reported[entity_id] = reported
            self._source_positions[entity_id] = position
            accepted[entity_id] = position
        self._virtual_attributes[ATTR_LOCATION_REJECTED_SOURCES] = rejected
        return accepted

    def _update_motion_attributes(self, selected, stale=False):
        """Estimate motion within one source, never between different devices."""
        history = self._source_history.get(selected, [])
        seen = self._source_reported.get(selected)
        speed = bearing = None
        if not stale and len(history) >= 2:
            previous, previous_time, previous_accuracy = history[-2]
            current, current_time, accuracy = history[-1]
            elapsed = (current_time - previous_time).total_seconds()
            age = (dt_util.utcnow() - current_time).total_seconds()
            if elapsed >= SPEED_MIN_INTERVAL_SECONDS and 0 <= age <= min(
                SPEED_MAX_AGE_SECONDS, self._location_policy()[CONF_LOCATION_HELPER_PRIORITY_WINDOW_SECONDS]
            ):
                distance = self._distance_meters(previous, current)
                if distance <= previous_accuracy + accuracy:
                    speed = 0.0
                else:
                    speed = distance / elapsed
                    lat1, lat2 = radians(previous[0]), radians(current[0])
                    delta_lon = radians(current[1] - previous[1])
                    bearing = (degrees(atan2(
                        sin(delta_lon) * cos(lat2),
                        cos(lat1) * sin(lat2) - sin(lat1) * cos(lat2) * cos(delta_lon),
                    )) + 360) % 360
        self._virtual_attributes.update({
            ATTR_LOCATION_LAST_SEEN: seen.isoformat() if seen else None,
            ATTR_LOCATION_SPEED: speed,
            ATTR_LOCATION_BEARING: bearing,
        })

    def _source_is_recent(self, entity_id, now) -> bool:
        """Return whether the source has actually moved inside the policy window."""
        last_moved = self._source_last_moved.get(entity_id)
        if last_moved is None:
            return False
        return timedelta(0) <= now - last_moved <= timedelta(
            seconds=self._location_policy()[CONF_LOCATION_HELPER_PRIORITY_WINDOW_SECONDS],
        )

    def _select_location_source(self, positions, source_states, now):
        """Follow observed travel through stops and permit nearby handoffs."""
        threshold = self._location_policy()[CONF_LOCATION_HELPER_DISTANCE_METERS]
        moving = [
            entity_id for entity_id in positions
            if self._source_is_recent(entity_id, now)
        ]
        # Stable source ordering breaks exact timestamp ties, not attribute
        # refreshes such as battery updates on a device left behind.
        moving.sort(key=lambda entity_id: self._source_last_moved[entity_id], reverse=True)
        current = self._priority_source
        if current in self._source_last_moved and current in self._source_positions:
            for candidate in moving:
                history = self._source_history.get(candidate, [])
                if candidate == current or len(history) < 2:
                    continue
                if self._source_last_moved[candidate] <= self._source_last_moved[current]:
                    continue
                # A different device may be picked up where the person stopped.
                # Remote motion at home must not steal a fresh tracker at work.
                if current not in positions or self._distance_meters(
                    history[-2][0], self._source_positions[current]
                ) <= threshold:
                    return candidate, "handoff", False
            return current, "following" if current in positions else "last_known", current not in positions
        if moving:
            return moving[0], "movement", False
        if not positions:
            return None, "no_position", True
        median = (
            self._median([position[0] for position in positions.values()]),
            median_longitude(position[1] for position in positions.values()),
        )
        window = timedelta(seconds=self._location_policy()[CONF_LOCATION_HELPER_PRIORITY_WINDOW_SECONDS])
        # Retain the legacy bootstrap heuristic when there is no motion history,
        # but label it as an unconfirmed outlier and use its GPS observation time.
        outliers = []
        for entity_id, position in positions.items():
            history = self._source_history.get(entity_id, [])
            observed = history[-1][1] if history else self._source_observation_time(source_states[entity_id])
            if (
                timedelta(0) <= now - observed <= window
                and (entity_id == current or self._distance_meters(position, median) > threshold)
            ):
                outliers.append(entity_id)
        if outliers:
            selected = current if current in outliers else max(
                outliers,
                key=lambda entity_id: self._source_history[entity_id][-1][1]
                if self._source_history.get(entity_id) else self._source_observation_time(source_states[entity_id]),
            )
            return selected, "unconfirmed_outlier", False
        return None, "median", False

    def _store_location_observations(self):
        """Expose bounded restart data, not the path kept in working memory."""
        self._virtual_attributes.update({
            ATTR_LOCATION_SOURCE_POSITIONS: {
                entity_id: list(position) for entity_id, position in self._source_positions.items()
            },
            ATTR_LOCATION_SOURCE_LAST_MOVED: {
                entity_id: value.timestamp() for entity_id, value in self._source_last_moved.items()
            },
            ATTR_LOCATION_SOURCE_OBSERVATIONS: {
                entity_id: {
                    "position": list(history[-1][0]),
                    "timestamp": history[-1][1].timestamp(),
                    "reported": self._source_reported[entity_id].timestamp(),
                    "accuracy": history[-1][2],
                    "anchor": list(self._source_motion_anchors[entity_id][0]),
                    "anchor_accuracy": self._source_motion_anchors[entity_id][1],
                    "rejected_position": (
                        list(self._source_rejected_positions[entity_id])
                        if entity_id in self._source_rejected_positions else None
                    ),
                }
                for entity_id, history in self._source_history.items() if history
            },
        })

    @classmethod
    def _is_local_presence_source(cls, entity_id, state) -> bool:
        """Recognize affirmative Wi-Fi/ESPresense-style presence states."""
        if state is None or state.state in {STATE_UNAVAILABLE, STATE_UNKNOWN}:
            return False
        if entity_id.startswith(BLE_PREFIX):
            return state.state == "home"
        if state.attributes.get("local_connection") is True:
            return state.state == "home"
        domain = entity_id.split(".", 1)[0]
        if domain in {"binary_sensor", "switch", "input_boolean"}:
            return str(state.state).lower() in {"on", "home", "true", "present"}
        if domain == "device_tracker":
            # Existing radio trackers already expose their presence decision.
            # Do not interpret RSSI as distance or GPS home as radio presence.
            return state.attributes.get("source_type") in {
                "bluetooth", "bluetooth_le", "router"
            } and state.state == "home"
        return False

    def _ble_distance_meters(self, source_entities, source_states) -> float | None:
        """Return the nearest finite ESPresense/BLE distance from configured sources."""
        distances = []
        now = dt_util.utcnow()
        window = timedelta(seconds=self._location_policy()[CONF_LOCATION_HELPER_PRIORITY_WINDOW_SECONDS])
        for entity_id in source_entities:
            state = source_states.get(entity_id)
            if (
                state is None or entity_id.split(".", 1)[0] != "sensor"
                or state.state in {STATE_UNKNOWN, STATE_UNAVAILABLE}
                or (reported := self._source_report_time(state)) is None
                or not timedelta(0) <= now - reported <= window
            ):
                continue
            value = state.attributes.get(
                "distance", state.attributes.get("distance_meters")
            )
            if value is None:
                value = state.state
            if isinstance(value, bool):
                continue
            try:
                distance = float(value)
            except (TypeError, ValueError, OverflowError):
                continue
            if isfinite(distance) and distance >= 0:
                distances.append(distance)
        return min(distances) if distances else None

    def _classify_presence_location(self, position, ble_distance):
        """Classify an opted-in aggregate without sacrificing GPS attributes."""
        home = self._coordinates_from_location_state(self.hass, "home")
        home_distance = self._distance_meters(position, home) if home else None
        if ble_distance is not None and ble_distance <= FRONT_DOOR_DISTANCE_METERS:
            classification = "front_door"
        elif home_distance is not None and home_distance <= NEAR_HOME_DISTANCE_METERS:
            classification = "near_home"
        elif home_distance is not None and home_distance >= FAR_AWAY_DISTANCE_METERS:
            classification = "far_away"
        else:
            classification = "away"
        self._virtual_attributes.update(
            {
                ATTR_LOCATION_CLASSIFICATION: classification,
                ATTR_LOCATION_HOME_DISTANCE: home_distance,
                ATTR_LOCATION_BLE_DISTANCE: ble_distance,
            }
        )
        return classification

    @callback
    def _update_location_from_sources(self) -> None:
        """Follow credible movement, preserving a carried device during stops."""
        if not self._location_helper:
            return
        self._refresh_local_presence()
        self._update_motion_attributes(None)

        now = dt_util.utcnow()
        source_entities = self._location_source_entities()
        source_states = {
            entity_id: self._location_source_state(entity_id) for entity_id in source_entities
        }
        local_presence = [
            entity_id
            for entity_id, state in source_states.items()
            if self._is_local_presence_source(entity_id, state)
            and (state.attributes.get("source_type") == "router" or (
                (reported := self._source_report_time(state)) is not None
                and timedelta(0) <= now - reported
                <= timedelta(seconds=self._location_policy()[CONF_LOCATION_HELPER_PRIORITY_WINDOW_SECONDS])
            ))
        ]
        ble_distance = self._ble_distance_meters(source_entities, source_states)
        positions = {
            entity_id: coordinates
            for entity_id, state in source_states.items()
            if state is not None
            if (coordinates := self._position_from_state(self.hass, state)) is not None
        }
        positions = self._record_source_movements(positions, source_states)
        selected, reason, stale = self._select_location_source(positions, source_states, now)
        self._store_location_observations()
        self._virtual_attributes.update(
            {
                ATTR_LOCATION_PRESENCE_SOURCES: local_presence,
                ATTR_LOCATION_BLE_DISTANCE: ble_distance,
                ATTR_LOCATION_SELECTION_REASON: reason,
                ATTR_LOCATION_STALE: stale,
            }
        )
        # Radio presence proves that device is home, not that the person is.
        # Only use it ahead of GPS when there is no confirmed travelling device
        # or that device's accepted GPS position has also returned home.
        home = self._coordinates_from_location_state(self.hass, "home")
        if home is None and local_presence:
            home = self._position_from_state(self.hass, source_states[local_presence[0]])
        selected_home = bool(
            selected and home and not stale
            and self._distance_meters(self._source_positions[selected], home) <= 100
        )
        if local_presence and (selected not in self._source_last_moved or selected_home):
            # Preserve a confirmed source across home stops for the next trip.
            if selected not in self._source_last_moved:
                selected = local_presence[0]
            self._priority_source = selected
            self._virtual_attributes.update(
                {
                    ATTR_LOCATION_MEDIAN_LATITUDE: None,
                    ATTR_LOCATION_MEDIAN_LONGITUDE: None,
                    ATTR_LOCATION_PRIORITY_SOURCE: selected,
                    ATTR_LOCATION_SELECTION_REASON: "local_presence",
                    ATTR_LOCATION_STALE: False,
                }
            )
            if self._presence_classification:
                self._virtual_attributes.update(
                    {
                        ATTR_LOCATION_CLASSIFICATION: "home",
                        ATTR_LOCATION_HOME_DISTANCE: 0,
                    }
                )
            self._location = "home"
            self._coords = {}
            self._gps_accuracy = 0
            if home:
                self._coords = {ATTR_LATITUDE: home[0], ATTR_LONGITUDE: home[1], ATTR_RADIUS: 0}
                zone = self.hass.states.get("zone.home")
                self._gps_accuracy = (_safe_gps_accuracy(zone.attributes.get(ATTR_RADIUS, 100)) or 100) if zone else 100
            self._update_attributes()
            self._schedule_state_update()
            return
        if not positions and selected is None:
            known_states = [
                state.state for entity_id, state in source_states.items()
                if state is not None
                and entity_id not in self._virtual_attributes[ATTR_LOCATION_REJECTED_SOURCES]
            ]
            self._priority_source = None
            self._virtual_attributes.update(
                {
                    ATTR_LOCATION_MEDIAN_LATITUDE: None,
                    ATTR_LOCATION_MEDIAN_LONGITUDE: None,
                    ATTR_LOCATION_PRIORITY_SOURCE: None,
                }
            )
            if self._presence_classification:
                self._virtual_attributes.update(
                    {
                        ATTR_LOCATION_CLASSIFICATION: "away",
                        ATTR_LOCATION_HOME_DISTANCE: None,
                    }
                )
            self._update_attributes()
            self.move_to_location(
                STATE_UNKNOWN if self._local_presence_config and len(known_states) < len(source_states) else "home"
                if known_states and all(value == "home" for value in known_states)
                else "not_home",
            )
            return

        median = (
            (
                self._median([position[0] for position in positions.values()]),
                median_longitude(position[1] for position in positions.values()),
            ) if positions else self._source_positions[selected]
        )
        self._virtual_attributes.update(
            {
                ATTR_LOCATION_MEDIAN_LATITUDE: median[0],
                ATTR_LOCATION_MEDIAN_LONGITUDE: median[1],
            }
        )

        self._priority_source = selected
        self._virtual_attributes[ATTR_LOCATION_PRIORITY_SOURCE] = selected
        latitude, longitude = self._source_positions[selected] if selected else median
        if selected:
            history = self._source_history.get(selected)
            accuracy = history[-1][2] if history else (
                self._source_accuracy(source_states[selected]) or 0
            )
        else:
            accuracy = max(
                self._distance_meters(position, median)
                + (self._source_accuracy(source_states[entity_id]) or 0)
                for entity_id, position in positions.items()
            )
        self._update_motion_attributes(selected, stale)
        if self._presence_classification:
            classification = self._classify_presence_location(
                (latitude, longitude),
                # A BLE sensor at home must not classify an off-site GPS source
                # as standing at the front door.
                ble_distance if home and self._distance_meters((latitude, longitude), home) <= 100 else None,
            )
            self._location = classification
            self._coords = {
                ATTR_LATITUDE: latitude,
                ATTR_LONGITUDE: longitude,
                ATTR_RADIUS: 0,
            }
            self._gps_accuracy = accuracy
            self._update_attributes()
            self._schedule_state_update()
            return
        self._update_attributes()
        self.move_to_coords(
            {
                ATTR_LATITUDE: latitude,
                ATTR_LONGITUDE: longitude,
                ATTR_RADIUS: 0,
            },
            accuracy,
        )

    @property
    def state(self) -> str | None:
        """Return a named location or let HA resolve GPS coordinates to a zone."""
        if self._location is not None:
            return self._location
        return super().state

    @property
    def source_type(self) -> SourceType:
        if self._coords:
            return SourceType.GPS
        return SourceType.ROUTER

    @property
    def latitude(self) -> float | None:
        """Return latitude value of the device."""
        return self._coords.get(ATTR_LATITUDE, None)

    @property
    def longitude(self) -> float | None:
        """Return longitude value of the device."""
        return self._coords.get(ATTR_LONGITUDE, None)

    @property
    def location_accuracy(self) -> int:
        return self._gps_accuracy

    def move_to_location(self, new_location):
        _LOGGER.debug("Moving %s to a new location", self.entity_id)
        self._location = new_location
        self._coords = {}
        self._gps_accuracy = 0
        self._schedule_state_update()

    def move_to_coords(self, new_coords, accuracy):
        if not isinstance(new_coords, dict):
            raise ValueError("GPS coordinates must be an object")
        latitude, longitude = self._validated_coordinates(
            new_coords.get(ATTR_LATITUDE),
            new_coords.get(ATTR_LONGITUDE),
        )
        if isinstance(accuracy, bool):
            raise ValueError("GPS accuracy must be a non-negative integer")
        try:
            accuracy = int(accuracy)
        except (TypeError, ValueError, OverflowError) as err:
            raise ValueError("GPS accuracy must be a non-negative integer") from err
        if accuracy < 0:
            raise ValueError("GPS accuracy must be a non-negative integer")
        new_coords = {
            **new_coords,
            ATTR_LATITUDE: latitude,
            ATTR_LONGITUDE: longitude,
        }
        _LOGGER.debug(
            "%s moving via GPS to %s (%sm)",
            self._attr_name,
            new_coords,
            accuracy,
        )
        self._location = None
        self._coords = new_coords
        self._gps_accuracy = accuracy
        self._schedule_state_update()

    def set_state(self, value) -> None:
        if self._location_helper or self._polygon_config or self._dawarich_config:
            return
        self.move_to_location(value)

    def _apply_native_template_value(self, name: str, value) -> bool:
        if name in {"state", "location"}:
            value = None if value is None or value == "" else str(value)
            changed = self._location != value or bool(self._coords)
            self._location = value
            if value is not None:
                self._coords = {}
                self._gps_accuracy = 0
            return changed
        if name == CONF_GPS:
            if not isinstance(value, (list, tuple)) or len(value) != 2:
                raise ValueError("gps must render [latitude, longitude]")
            latitude, longitude = self._validated_coordinates(*value)
            coords = {
                ATTR_LATITUDE: latitude,
                ATTR_LONGITUDE: longitude,
                ATTR_RADIUS: 0,
            }
            changed = self._coords != coords or self._location is not None
            self._coords = coords
            self._location = None
            return changed
        if name in {ATTR_LATITUDE, ATTR_LONGITUDE}:
            latitude = value if name == ATTR_LATITUDE else self.latitude
            longitude = value if name == ATTR_LONGITUDE else self.longitude
            if latitude is None or longitude is None:
                if isinstance(value, bool):
                    raise ValueError(f"{name} must be numeric")
                try:
                    parsed = float(value)
                except (TypeError, ValueError, OverflowError) as err:
                    raise ValueError(f"{name} must be numeric") from err
                limit = 90 if name == ATTR_LATITUDE else 180
                if not isfinite(parsed) or not -limit <= parsed <= limit:
                    raise ValueError(f"Invalid {name}: {value}")
                changed = self._coords.get(name) != parsed
                self._coords[name] = parsed
                self._location = None
                return changed
            latitude, longitude = self._validated_coordinates(latitude, longitude)
            coords = {
                ATTR_LATITUDE: latitude,
                ATTR_LONGITUDE: longitude,
                ATTR_RADIUS: 0,
            }
            changed = self._coords != coords or self._location is not None
            self._coords = coords
            self._location = None
            return changed
        if name in {CONF_GPS_ACCURACY, "location_accuracy"}:
            if isinstance(value, bool):
                raise ValueError("location_accuracy must be a non-negative integer")
            try:
                value = int(value)
            except (TypeError, ValueError, OverflowError) as err:
                raise ValueError(
                    "location_accuracy must be a non-negative integer"
                ) from err
            if value < 0:
                raise ValueError("location_accuracy must be a non-negative integer")
            changed = self._gps_accuracy != value
            self._gps_accuracy = value
            return changed
        return super()._apply_native_template_value(name, value)

    @staticmethod
    def _validated_coordinates(latitude, longitude) -> tuple[float, float]:
        if isinstance(latitude, bool) or isinstance(longitude, bool):
            raise ValueError("GPS coordinates must be numeric")
        try:
            latitude = float(latitude)
            longitude = float(longitude)
        except (TypeError, ValueError, OverflowError) as err:
            raise ValueError("GPS coordinates must be numeric") from err
        if (
            not isfinite(latitude)
            or not isfinite(longitude)
            or not -90 <= latitude <= 90
            or not -180 <= longitude <= 180
        ):
            raise ValueError(
                "GPS coordinates are outside valid latitude/longitude ranges"
            )
        return latitude, longitude


async def async_virtual_move_service(hass, call):
    entity_ids = call.data["entity_id"]
    _assert_managed_virtual_entities(hass, entity_ids)
    for entity_id in entity_ids:
        _LOGGER.debug("Moving virtual device tracker %s", entity_id)

        entity = get_entity_from_domain(hass, PLATFORM_DOMAIN, entity_id)

        location = call.data.get(CONF_LOCATION, None)
        coords = call.data.get(CONF_GPS, None)
        if location is not None:
            entity.move_to_location(location)
        elif coords is not None:
            accuracy = call.data.get(CONF_GPS_ACCURACY, 0)
            entity.move_to_coords(coords, accuracy)
        else:
            _LOGGER.debug(f"not moving {entity_id}")
