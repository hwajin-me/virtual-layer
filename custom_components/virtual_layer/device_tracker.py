"""
This component provides support for a virtual device tracker.

"""

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from math import asin, cos, isfinite, radians, sin, sqrt

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
from homeassistant.core import HomeAssistant, callback
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
LOCATION_CHANGE_DISTANCE_METERS = 1
ATTR_LOCATION_MEDIAN_LATITUDE = "location_median_latitude"
ATTR_LOCATION_MEDIAN_LONGITUDE = "location_median_longitude"
ATTR_LOCATION_PRIORITY_SOURCE = "location_priority_source"
ATTR_LOCATION_SOURCE_LAST_MOVED = "location_source_last_moved"
ATTR_LOCATION_SOURCE_POSITIONS = "location_source_positions"
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
ATTR_LOCATION_PRESENCE_SOURCES = "location_presence_sources"
ATTR_LOCATION_CLASSIFICATION = "location_classification"
ATTR_LOCATION_HOME_DISTANCE = "location_home_distance"
ATTR_LOCATION_BLE_DISTANCE = "location_ble_distance"
DEFAULT_DAWARICH_POLL_INTERVAL = 60
DEFAULT_DAWARICH_HISTORY_LIMIT = 10
POLYGON_STRATEGIES = {"majority", "priority", "latest", "median"}
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
        },
    )
)


def validate_domain_options(config) -> None:
    """Validate UI-supplied location helper settings."""
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
        if not isinstance(dawarich, dict):
            raise vol.Invalid("dawarich must be an object")
        allowed = {
            CONF_DAWARICH_URL,
            CONF_DAWARICH_API_KEY,
            CONF_DAWARICH_AUTH_MODE,
            CONF_DAWARICH_POLL_INTERVAL,
            CONF_DAWARICH_HISTORY_LIMIT,
            CONF_DAWARICH_PERSON_ENTITY,
        }
        if set(dawarich) - allowed or not isinstance(
            dawarich.get(CONF_DAWARICH_URL), str
        ):
            raise vol.Invalid("invalid Dawarich configuration")
        if (
            not dawarich[CONF_DAWARICH_URL]
            .rstrip("/")
            .startswith(("http://", "https://"))
        ):
            raise vol.Invalid("invalid Dawarich URL")
        if (
            not isinstance(dawarich.get(CONF_DAWARICH_API_KEY), str)
            or not dawarich[CONF_DAWARICH_API_KEY].strip()
        ):
            raise vol.Invalid("Dawarich API key is required")
        if dawarich.get(CONF_DAWARICH_AUTH_MODE, "bearer") not in {"bearer", "query"}:
            raise vol.Invalid("invalid Dawarich authentication mode")
        try:
            poll = int(
                dawarich.get(
                    CONF_DAWARICH_POLL_INTERVAL, DEFAULT_DAWARICH_POLL_INTERVAL
                )
            )
            limit = int(
                dawarich.get(
                    CONF_DAWARICH_HISTORY_LIMIT, DEFAULT_DAWARICH_HISTORY_LIMIT
                )
            )
        except (TypeError, ValueError, OverflowError) as err:
            raise vol.Invalid("invalid Dawarich interval") from err
        if not 15 <= poll <= 3600 or not 1 <= limit <= 100:
            raise vol.Invalid("invalid Dawarich interval")
        person = dawarich.get(CONF_DAWARICH_PERSON_ENTITY, "")
        if person:
            try:
                cv.entity_id(person)
            except vol.Invalid as err:
                raise vol.Invalid("invalid Dawarich person") from err
            if not person.startswith("person."):
                raise vol.Invalid("invalid Dawarich person")

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
        self._priority_source = None
        self._source_positions = {}
        self._source_last_moved = {}

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
        self._restore_location_helper_attributes()

    @staticmethod
    def _normalize_dawarich_config(value):
        """Return a safe Dawarich configuration from stored UI options."""
        if not isinstance(value, dict):
            return None
        try:
            validate_domain_options({CONF_DAWARICH: value})
        except vol.Invalid as err:
            _LOGGER.warning("Ignoring invalid Dawarich configuration: %s", err)
            return None
        return {
            CONF_DAWARICH_URL: value[CONF_DAWARICH_URL].rstrip("/"),
            CONF_DAWARICH_API_KEY: value[CONF_DAWARICH_API_KEY].strip(),
            CONF_DAWARICH_AUTH_MODE: value.get(CONF_DAWARICH_AUTH_MODE, "bearer"),
            CONF_DAWARICH_POLL_INTERVAL: int(
                value.get(CONF_DAWARICH_POLL_INTERVAL, DEFAULT_DAWARICH_POLL_INTERVAL)
            ),
            CONF_DAWARICH_HISTORY_LIMIT: int(
                value.get(CONF_DAWARICH_HISTORY_LIMIT, DEFAULT_DAWARICH_HISTORY_LIMIT)
            ),
            CONF_DAWARICH_PERSON_ENTITY: value.get(CONF_DAWARICH_PERSON_ENTITY, ""),
        }

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
        if not self._location_helper:
            return
        source_entities = self._location_source_entities()
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

        source_last_moved = self._virtual_attributes.get(
            ATTR_LOCATION_SOURCE_LAST_MOVED
        )
        if isinstance(source_last_moved, dict):
            for entity_id, timestamp in source_last_moved.items():
                if entity_id not in source_entities:
                    continue
                try:
                    if isinstance(timestamp, bool):
                        raise TypeError
                    self._source_last_moved[entity_id] = datetime.fromtimestamp(
                        float(timestamp),
                        tz=timezone.utc,
                    )
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
                lambda _now: self._update_location_from_sources(),
                timedelta(minutes=1),
            )
        )
        self._update_location_from_sources()

    @staticmethod
    def _dawarich_points(payload):
        """Return point records from documented and older Dawarich envelopes."""
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if not isinstance(payload, dict):
            return []
        for key in ("points", "data", "locations", "results", "visits"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return (
            [payload]
            if any(key in payload for key in ("latitude", "lat", "longitude", "lon"))
            else []
        )

    def _dawarich_person_name(self):
        """Return Home Assistant's selected Person label for family matching."""
        person_id = self._dawarich_config.get(CONF_DAWARICH_PERSON_ENTITY, "")
        person = self.hass.states.get(person_id) if person_id else None
        return str(person.name if person else "").strip().casefold()

    @staticmethod
    def _dawarich_family_point(payload, person_name):
        """Find a selected family member in version-tolerant API envelopes."""
        members = VirtualDeviceTracker._dawarich_points(payload)
        for member in members:
            user = member.get("user")
            if not isinstance(user, dict):
                user = (
                    member.get("member")
                    if isinstance(member.get("member"), dict)
                    else {}
                )
            candidate = (
                str(
                    member.get(
                        "name",
                        member.get(
                            "user_name",
                            member.get(
                                "email", user.get("name", user.get("email", ""))
                            ),
                        ),
                    )
                )
                .strip()
                .casefold()
            )
            if candidate == person_name:
                location = member.get("location")
                return location if isinstance(location, dict) else member
        return None

    @staticmethod
    def _dawarich_coordinate(point, *names):
        for name in names:
            value = point.get(name)
            if value is not None:
                return value
        return None

    @classmethod
    def _dawarich_point_timestamp(cls, point):
        """Return a sortable timestamp for ISO and Unix point payloads."""
        raw = cls._dawarich_coordinate(
            point, "timestamp", "recorded_at", "datetime", "created_at", "updated_at"
        )
        if isinstance(raw, bool) or raw is None:
            return datetime.min.replace(tzinfo=timezone.utc)
        if isinstance(raw, (int, float)):
            try:
                return datetime.fromtimestamp(raw, tz=timezone.utc)
            except (OSError, OverflowError, ValueError):
                return datetime.min.replace(tzinfo=timezone.utc)
        if isinstance(raw, str):
            parsed = dt_util.parse_datetime(raw)
            if parsed is not None:
                return dt_util.as_utc(parsed)
        return datetime.min.replace(tzinfo=timezone.utc)

    @classmethod
    def _dawarich_point_summary(cls, point):
        """Keep useful location/history metadata without exposing arbitrary API data."""
        keys = (
            "id",
            "timestamp",
            "recorded_at",
            "datetime",
            "created_at",
            "updated_at",
            "altitude",
            "speed",
            "velocity",
            "bearing",
            "course",
            "accuracy",
            "horizontal_accuracy",
            "activity",
            "address",
            "city",
            "country",
            "place_name",
            "place",
            "arrival_at",
            "departure_at",
        )
        return {
            key: point[key]
            for key in keys
            if key in point
            and isinstance(point[key], (str, int, float, bool, type(None)))
        }

    async def _async_dawarich_latest_visit(self, session, headers, params):
        """Fetch a visit opportunistically; locations remain usable without it."""
        try:
            async with asyncio.timeout(15):
                async with session.get(
                    self._dawarich_config[CONF_DAWARICH_URL] + "/api/v1/visits",
                    headers=headers,
                    params={**params, "per_page": 1},
                ) as response:
                    if response.status != 200:
                        return None
                    payload = await response.json(content_type=None)
            visits = self._dawarich_points(payload)
            return self._dawarich_point_summary(visits[0]) if visits else None
        except (asyncio.TimeoutError, ClientError, ValueError, TypeError):
            return None

    async def _async_refresh_dawarich(self, _now=None) -> None:
        """Fetch latest Dawarich points without blocking Home Assistant's loop."""
        config = self._dawarich_config
        if not config:
            return
        headers = {"Accept": "application/json"}
        params = {"per_page": config[CONF_DAWARICH_HISTORY_LIMIT]}
        if config[CONF_DAWARICH_AUTH_MODE] == "query":
            params["api_key"] = config[CONF_DAWARICH_API_KEY]
        else:
            headers["Authorization"] = "Bearer " + config[CONF_DAWARICH_API_KEY]
        try:
            session = async_get_clientsession(self.hass)
            async with asyncio.timeout(15):
                endpoint = (
                    "/api/v1/families/locations"
                    if config.get(CONF_DAWARICH_PERSON_ENTITY)
                    else "/api/v1/points"
                )
                async with session.get(
                    config[CONF_DAWARICH_URL] + endpoint, headers=headers, params=params
                ) as response:
                    response.raise_for_status()
                    payload = await response.json(content_type=None)
            family_point = (
                self._dawarich_family_point(payload, self._dawarich_person_name())
                if config.get(CONF_DAWARICH_PERSON_ENTITY)
                else None
            )
            points = [family_point] if family_point else self._dawarich_points(payload)
            if not points:
                raise ValueError("Dawarich returned no location points")
            # Point API ordering is not guaranteed across Dawarich versions.
            point = max(points, key=self._dawarich_point_timestamp)
            latitude = self._dawarich_coordinate(point, "latitude", "lat")
            longitude = self._dawarich_coordinate(point, "longitude", "lon", "lng")
            latitude, longitude = self._validated_coordinates(latitude, longitude)
            accuracy = (
                _safe_gps_accuracy(
                    self._dawarich_coordinate(
                        point, "accuracy", "horizontal_accuracy", "gps_accuracy"
                    )
                )
                or 0
            )
            visit = (
                None
                if config.get(CONF_DAWARICH_PERSON_ENTITY)
                else await self._async_dawarich_latest_visit(session, headers, params)
            )
            self._location = None
            self._coords = {
                ATTR_LATITUDE: latitude,
                ATTR_LONGITUDE: longitude,
                ATTR_RADIUS: 0,
            }
            self._gps_accuracy = accuracy
            self._virtual_attributes.update(
                {
                    ATTR_DAWARICH_LAST_UPDATED: dt_util.utcnow().isoformat(),
                    ATTR_DAWARICH_POINT: self._dawarich_point_summary(point),
                    ATTR_DAWARICH_HISTORY: [
                        self._dawarich_point_summary(item) for item in points
                    ],
                    ATTR_DAWARICH_VISIT: visit,
                    ATTR_DAWARICH_ERROR: None,
                }
            )
            self._update_attributes()
            self.hass.add_job(self.async_schedule_update_ha_state)
        except (
            asyncio.TimeoutError,
            ClientError,
            ValueError,
            TypeError,
            KeyError,
        ) as err:
            _LOGGER.warning(
                "Unable to refresh Dawarich for %s: %s", self.entity_id, err
            )
            self._virtual_attributes[ATTR_DAWARICH_ERROR] = str(err)
            self._update_attributes()
            self.hass.add_job(self.async_schedule_update_ha_state)

    async def _async_setup_polygon_tracking(self) -> None:
        """Load polygon definitions and start source aggregation."""
        await self._async_reload_polygon_zones(keep_existing=False)

        source_entities = set(self._polygon_source_entities())
        if source_entities:
            self._refresh_remove_listeners.append(
                async_track_state_change_event(
                    self.hass,
                    source_entities,
                    self._async_polygon_source_changed,
                )
            )
        self._refresh_remove_listeners.append(
            async_track_time_interval(
                self.hass,
                lambda _now: self._update_polygon_from_sources(),
                timedelta(minutes=1),
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
        source_entities = self._location_source_entities()
        if source_entities:
            return list(
                dict.fromkeys(
                    source_entities
                    + list(
                        self._polygon_config.get(CONF_POLYGON_ESPRESENSE_ANCHORS, {})
                    )
                )
            )
        person = self._polygon_config.get(CONF_POLYGON_PERSON_ENTITY)
        sources = [person] if person and person != self.entity_id else []
        return sources + list(
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
            if now - state.last_updated > timedelta(seconds=max_age):
                continue
            raw = state.attributes.get(
                "distance", state.attributes.get("distance_meters", state.state)
            )
            try:
                distance = float(raw)
                latitude, longitude = self._validated_coordinates(
                    anchor[ATTR_LATITUDE], anchor[ATTR_LONGITUDE]
                )
            except (TypeError, ValueError, OverflowError, KeyError):
                continue
            unit = str(state.attributes.get("unit_of_measurement", "m")).lower()
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
        max_age = rule.get("max_age_seconds")
        if max_age is not None and now - state.last_updated > timedelta(
            seconds=float(max_age)
        ):
            return False
        accuracy = _safe_gps_accuracy(state.attributes.get(CONF_GPS_ACCURACY, 0))
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
        rules = self._polygon_config[CONF_POLYGON_TRACKER_RULES]
        samples = []
        for entity_id in self._polygon_source_entities():
            state = self.hass.states.get(entity_id)
            if (
                state is None
                or state.state in {STATE_UNAVAILABLE, STATE_UNKNOWN}
                or (position := self._position_from_state(self.hass, state)) is None
            ):
                continue
            rule = rules.get(entity_id, {})
            if not self._polygon_rule_matches(entity_id, state, rule):
                continue
            try:
                accuracy = _safe_gps_accuracy(
                    state.attributes.get(CONF_GPS_ACCURACY, 0)
                )
                if accuracy is None:
                    continue
                samples.append(
                    {
                        "entity_id": entity_id,
                        "latitude": position[0],
                        "longitude": position[1],
                        "gps_accuracy": accuracy,
                        "last_updated": state.last_updated,
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

        selected = select_tracker_position(
            samples,
            self._polygon_config[CONF_POLYGON_STRATEGY],
            float(self._polygon_config[CONF_POLYGON_DISTANCE_METERS]),
        )
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
            self.move_to_location(self._polygon_config[CONF_POLYGON_AWAY_STATE])
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
        self.hass.add_job(self.async_schedule_update_ha_state)

    @callback
    def _async_location_source_changed(self, _event) -> None:
        self._update_location_from_sources()

    @staticmethod
    def _coordinates_from_state(state):
        """Read usable GPS coordinates from a Home Assistant state."""
        raw_latitude = state.attributes.get(ATTR_LATITUDE)
        raw_longitude = state.attributes.get(ATTR_LONGITUDE)
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
        return cls._coordinates_from_state(
            state
        ) or cls._coordinates_from_location_state(hass, state.state)

    def _location_source_entities(self) -> list[str]:
        """Return configured sources without the virtual tracker itself."""
        return [
            entity_id
            for entity_id in self._source_entities
            if entity_id != self.entity_id
        ]

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

    def _record_source_movements(self, positions, source_states) -> None:
        """Record movement only when a source coordinate actually changes."""
        for entity_id, position in positions.items():
            previous_position = self._source_positions.get(entity_id)
            self._source_positions[entity_id] = position
            if (
                previous_position is not None
                and self._distance_meters(previous_position, position)
                <= LOCATION_CHANGE_DISTANCE_METERS
            ):
                continue

            last_updated = getattr(source_states[entity_id], "last_updated", None)
            if last_updated is not None:
                self._source_last_moved[entity_id] = last_updated

    def _source_is_recent(self, entity_id, now) -> bool:
        """Return whether the source has actually moved inside the policy window."""
        last_moved = self._source_last_moved.get(entity_id)
        if last_moved is None:
            return False
        return now - last_moved <= timedelta(
            seconds=self._location_helper[CONF_LOCATION_HELPER_PRIORITY_WINDOW_SECONDS],
        )

    @classmethod
    def _is_local_presence_source(cls, entity_id, state) -> bool:
        """Recognize affirmative Wi-Fi/ESPresense-style presence states."""
        if state is None or state.state in {STATE_UNAVAILABLE, STATE_UNKNOWN}:
            return False
        domain = entity_id.split(".", 1)[0]
        if domain in {"binary_sensor", "switch", "input_boolean"}:
            return str(state.state).lower() in {"on", "home", "true", "present"}
        # Plain device_tracker state is intentionally not treated as a radio
        # proof: it may be a named-zone iCloud/GPS report. Wi-Fi and
        # ESPresense integrations conventionally expose a binary sensor or a
        # switch; those are unambiguous and therefore safe to prioritize.
        return False

    @staticmethod
    def _ble_distance_meters(source_entities, source_states) -> float | None:
        """Return the nearest finite ESPresense/BLE distance from configured sources."""
        distances = []
        for entity_id in source_entities:
            state = source_states.get(entity_id)
            if state is None or entity_id.split(".", 1)[0] != "sensor":
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
        """Track a recent outlier, otherwise use the sources' median position."""
        if not self._location_helper:
            return

        now = dt_util.utcnow()
        source_entities = self._location_source_entities()
        source_states = {
            entity_id: self.hass.states.get(entity_id) for entity_id in source_entities
        }
        local_presence = [
            entity_id
            for entity_id, state in source_states.items()
            if self._is_local_presence_source(entity_id, state)
        ]
        ble_distance = self._ble_distance_meters(source_entities, source_states)
        positions = {
            entity_id: coordinates
            for entity_id, state in source_states.items()
            if state is not None
            if (coordinates := self._position_from_state(self.hass, state)) is not None
        }
        self._record_source_movements(positions, source_states)
        self._virtual_attributes.update(
            {
                ATTR_LOCATION_SOURCE_POSITIONS: {
                    entity_id: list(position)
                    for entity_id, position in self._source_positions.items()
                    if entity_id in source_entities
                },
                ATTR_LOCATION_SOURCE_LAST_MOVED: {
                    entity_id: last_moved.timestamp()
                    for entity_id, last_moved in self._source_last_moved.items()
                    if entity_id in source_entities
                },
                ATTR_LOCATION_PRESENCE_SOURCES: local_presence,
                ATTR_LOCATION_BLE_DISTANCE: ble_distance,
            }
        )
        # Local radio/network presence is a direct proof of occupancy. It
        # outranks GPS: a phone may report an older off-site coordinate while
        # it is already associated with home Wi-Fi or detected by ESPresense.
        if local_presence:
            self._priority_source = local_presence[0]
            self._virtual_attributes.update(
                {
                    ATTR_LOCATION_MEDIAN_LATITUDE: None,
                    ATTR_LOCATION_MEDIAN_LONGITUDE: None,
                    ATTR_LOCATION_PRIORITY_SOURCE: local_presence[0],
                }
            )
            if self._presence_classification:
                self._virtual_attributes.update(
                    {
                        ATTR_LOCATION_CLASSIFICATION: "home",
                        ATTR_LOCATION_HOME_DISTANCE: 0,
                    }
                )
            self._update_attributes()
            self.move_to_location("home")
            return
        if not positions:
            known_states = [
                state.state for state in source_states.values() if state is not None
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
                "home"
                if known_states and all(value == "home" for value in known_states)
                else "not_home",
            )
            return

        median = (
            self._median([position[0] for position in positions.values()]),
            median_longitude(position[1] for position in positions.values()),
        )
        self._virtual_attributes.update(
            {
                ATTR_LOCATION_MEDIAN_LATITUDE: median[0],
                ATTR_LOCATION_MEDIAN_LONGITUDE: median[1],
            }
        )

        # Keep following the already selected device after it reaches the
        # majority location, until its own GPS updates are no longer recent.
        selected = None
        priority_state = source_states.get(self._priority_source)
        if (
            self._priority_source in positions
            and priority_state is not None
            and self._source_is_recent(self._priority_source, now)
        ):
            selected = self._priority_source
        else:
            threshold = self._location_helper[CONF_LOCATION_HELPER_DISTANCE_METERS]
            recent_outliers = [
                entity_id
                for entity_id, position in positions.items()
                if self._distance_meters(position, median) > threshold
                and self._source_is_recent(entity_id, now)
            ]
            if recent_outliers:
                selected = max(
                    recent_outliers,
                    key=lambda entity_id: source_states[entity_id].last_updated,
                )

        self._priority_source = selected
        self._virtual_attributes[ATTR_LOCATION_PRIORITY_SOURCE] = selected
        latitude, longitude = positions[selected] if selected else median
        if self._presence_classification:
            classification = self._classify_presence_location(
                (latitude, longitude), ble_distance
            )
            self._location = classification
            self._coords = {
                ATTR_LATITUDE: latitude,
                ATTR_LONGITUDE: longitude,
                ATTR_RADIUS: 0,
            }
            self._gps_accuracy = 0
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
            0,
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
        if self._location_helper or self._polygon_config:
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
