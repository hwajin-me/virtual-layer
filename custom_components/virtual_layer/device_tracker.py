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
from homeassistant.helpers.template import Template, TemplateError
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify

from . import (
    _assert_managed_virtual_entity,
    _async_verify_target_entity_control,
    get_entity_configs,
    get_entity_from_domain,
)
from .const import *
from .entity import VirtualEntity, virtual_schema
from .polygon import (
    find_polygon_zone,
    load_polygon_zones,
    median_longitude,
    parse_geojson_zones,
    select_tracker_position,
)

_LOGGER = logging.getLogger(__name__)

DEPENDENCIES = [COMPONENT_DOMAIN]

CONF_LOCATION = 'location'
CONF_GPS = 'gps'
CONF_GPS_ACCURACY = 'gps_accuracy'
CONF_LOCATION_HELPER_DISTANCE_METERS = "distance_threshold_meters"
CONF_LOCATION_HELPER_PRIORITY_WINDOW_SECONDS = "priority_window_seconds"
DEFAULT_DEVICE_TRACKER_VALUE = 'home'
DEFAULT_LOCATION = 'home'
DEFAULT_LOCATION_HELPER_DISTANCE_METERS = 300
DEFAULT_LOCATION_HELPER_PRIORITY_WINDOW_SECONDS = 30 * 60
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

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_DEVICES, default=[]): cv.ensure_list
})

DEVICE_TRACKER_SCHEMA = vol.Schema(virtual_schema(DEFAULT_DEVICE_TRACKER_VALUE, {
    # Keep the helper flexible so older stored data with an invalid setting can
    # still load and be edited or removed through the UI.
    vol.Optional(CONF_LOCATION_HELPER): object,
    vol.Optional(CONF_POLYGONAL_ZONE): object,
}))


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

        try:
            distance_threshold = float(value.get(
                CONF_LOCATION_HELPER_DISTANCE_METERS,
                DEFAULT_LOCATION_HELPER_DISTANCE_METERS,
            ))
            priority_window = int(value.get(
                CONF_LOCATION_HELPER_PRIORITY_WINDOW_SECONDS,
                DEFAULT_LOCATION_HELPER_PRIORITY_WINDOW_SECONDS,
            ))
        except (TypeError, ValueError) as err:
            raise vol.Invalid("invalid location_helper option") from err

        if not isfinite(distance_threshold) or distance_threshold <= 0 or priority_window <= 0:
            raise vol.Invalid("location_helper options must be positive")

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
    if not isinstance(files, list) or any(not isinstance(item, str) or not item.strip() for item in files):
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
    try:
        distance = float(polygon.get(
            CONF_POLYGON_DISTANCE_METERS,
            DEFAULT_LOCATION_HELPER_DISTANCE_METERS,
        ))
    except (TypeError, ValueError) as err:
        raise vol.Invalid("invalid polygon distance threshold") from err
    if not isfinite(distance) or distance <= 0:
        raise vol.Invalid("polygon distance threshold must be positive")
    rules = polygon.get(CONF_POLYGON_TRACKER_RULES, {})
    _validate_polygon_rules(rules)
    if CONF_SOURCE_ENTITIES in config:
        source_entities = config.get(CONF_SOURCE_ENTITIES, [])
        if not isinstance(source_entities, list):
            raise vol.Invalid("polygon sources must be a list")
        if any(
            not isinstance(entity_id, str)
            or not entity_id.startswith("device_tracker.")
            for entity_id in source_entities
        ):
            raise vol.Invalid("polygon sources must be device_tracker entities")
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
                try:
                    value = float(rule[key])
                except (TypeError, ValueError) as err:
                    raise vol.Invalid(f"invalid polygon tracker {key}") from err
                if not isfinite(value) or value <= 0:
                    raise vol.Invalid(f"polygon tracker {key} must be positive")
        if "priority" in rule:
            try:
                if not isfinite(float(rule["priority"])):
                    raise ValueError
            except (TypeError, ValueError) as err:
                raise vol.Invalid("invalid polygon tracker priority") from err
        if "condition_template" in rule and not isinstance(rule["condition_template"], str):
            raise vol.Invalid("polygon tracker condition_template must be a string")
        for key in ("dominant", "enabled"):
            if key in rule and not isinstance(rule[key], bool):
                raise vol.Invalid(f"polygon tracker {key} must be a boolean")

SERVICE_MOVE = "move"
SERVICE_SCHEMA = vol.Schema({
    vol.Required(ATTR_ENTITY_ID): cv.comp_entity_ids,
    vol.Optional(CONF_LOCATION): cv.string,
    vol.Optional(CONF_GPS): {
        vol.Required(ATTR_LATITUDE): cv.latitude,
        vol.Required(ATTR_LONGITUDE): cv.longitude,
        vol.Optional(ATTR_RADIUS): cv.string,
    },
    vol.Optional(CONF_GPS_ACCURACY): cv.positive_int,
})

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
    for entity in get_entity_configs(hass, entry.data[ATTR_GROUP_NAME], PLATFORM_DOMAIN):
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
        hass.data[COMPONENT_SERVICES][PLATFORM_DOMAIN] = 'installed'
        hass.services.async_register(
            COMPONENT_DOMAIN, SERVICE_MOVE, async_virtual_service, schema=SERVICE_SCHEMA,
        )


class VirtualDeviceTracker(TrackerEntity, VirtualEntity):
    """Represent a tracked device."""

    def __init__(self, config):
        """Initialize a Virtual Device Tracker."""

        # Handle deprecated option.
        if config.get(CONF_LOCATION, None) is not None:
            _LOGGER.info("'location' option is deprecated for virtual device trackers, please use 'initial_value'")
            config[CONF_INITIAL_VALUE] = config.pop(CONF_LOCATION)

        super().__init__(config, PLATFORM_DOMAIN)

        self._location = None
        self._coords = {}
        self._gps_accuracy = 0
        self._location_helper = self._normalize_location_helper(
            config.get(CONF_LOCATION_HELPER),
        )
        self._polygon_config = self._normalize_polygon_config(
            config.get(CONF_POLYGONAL_ZONE),
            config.get(CONF_SOURCE_ENTITIES),
        )
        self._polygon_zones = []
        self._priority_source = None
        self._source_positions = {}
        self._source_last_moved = {}

        _LOGGER.debug(f"{self._attr_name}, available={self._attr_available}")
        _LOGGER.debug(f"{self._attr_name}, entity={self.entity_id}")

    def _create_state(self, config):
        _LOGGER.debug(f"device_tracker-create=config={config}")
        super()._create_state(config)
        self._location = config.get(CONF_INITIAL_VALUE)
        self._restore_location_helper_attributes()

    def _restore_state(self, state, config):
        _LOGGER.debug(f"device_tracker-restore=state={state.state}")
        _LOGGER.debug(f"device_tracker-restore=attrs={state.attributes}")

        if ATTR_AVAILABLE not in state.attributes:
            _LOGGER.debug("looks wrong, from upgrade? creating instead...")
            self._create_state(config)
            return

        super()._restore_state(state, config)
        position = self._coordinates_from_state(state)
        if position is not None:
            latitude, longitude = position
            self._location = None
            self._coords = {
                ATTR_LONGITUDE: longitude,
                ATTR_LATITUDE: latitude,
                ATTR_RADIUS: 0
            }
            try:
                accuracy = float(state.attributes.get(CONF_GPS_ACCURACY, 0) or 0)
            except (TypeError, ValueError):
                accuracy = 0
            self._gps_accuracy = max(0, accuracy) if isfinite(accuracy) else 0
        else:
            self._location = state.state
            self._coords = {}
            self._gps_accuracy = 0
        self._restore_location_helper_attributes()

    @staticmethod
    def _normalize_location_helper(value):
        """Return a safe policy for a stored location helper configuration."""
        if not isinstance(value, dict):
            return None

        try:
            distance_threshold = float(value.get(
                CONF_LOCATION_HELPER_DISTANCE_METERS,
                DEFAULT_LOCATION_HELPER_DISTANCE_METERS,
            ))
            priority_window = int(value.get(
                CONF_LOCATION_HELPER_PRIORITY_WINDOW_SECONDS,
                DEFAULT_LOCATION_HELPER_PRIORITY_WINDOW_SECONDS,
            ))
        except (TypeError, ValueError):
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
                    latitude, longitude = map(float, position)
                except (TypeError, ValueError):
                    continue
                if -90 <= latitude <= 90 and -180 <= longitude <= 180:
                    self._source_positions[entity_id] = (latitude, longitude)

        source_last_moved = self._virtual_attributes.get(ATTR_LOCATION_SOURCE_LAST_MOVED)
        if isinstance(source_last_moved, dict):
            for entity_id, timestamp in source_last_moved.items():
                if entity_id not in source_entities:
                    continue
                try:
                    self._source_last_moved[entity_id] = datetime.fromtimestamp(
                        float(timestamp),
                        tz=timezone.utc,
                    )
                except (TypeError, ValueError, OSError, OverflowError):
                    continue

    async def async_added_to_hass(self) -> None:
        """Start aggregate GPS tracking after normal virtual setup."""
        await super().async_added_to_hass()
        if self._polygon_config:
            await self._async_setup_polygon_tracking()
            return
        if not self._location_helper:
            return

        source_entities = set(self._location_source_entities())
        if source_entities:
            self._refresh_remove_listeners.append(async_track_state_change_event(
                self.hass,
                source_entities,
                self._async_location_source_changed,
            ))
        # Re-evaluate the 30 minute priority window even when no source emits
        # a new state event.
        self._refresh_remove_listeners.append(async_track_time_interval(
            self.hass,
            lambda _now: self._update_location_from_sources(),
            timedelta(minutes=1),
        ))
        self._update_location_from_sources()

    async def _async_setup_polygon_tracking(self) -> None:
        """Load polygon definitions and start source aggregation."""
        await self._async_reload_polygon_zones(keep_existing=False)

        source_entities = set(self._polygon_source_entities())
        if source_entities:
            self._refresh_remove_listeners.append(async_track_state_change_event(
                self.hass,
                source_entities,
                self._async_polygon_source_changed,
            ))
        self._refresh_remove_listeners.append(async_track_time_interval(
            self.hass,
            lambda _now: self._update_polygon_from_sources(),
            timedelta(minutes=1),
        ))
        if self._polygon_config.get(CONF_POLYGON_FILES):
            self._refresh_remove_listeners.append(async_track_time_interval(
                self.hass,
                self._async_reload_polygon_zones,
                POLYGON_FILE_RELOAD_INTERVAL,
            ))
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
        except (asyncio.TimeoutError, ClientError, OSError, TypeError, ValueError) as err:
            _LOGGER.error("Unable to load polygon zones for %s: %s", self.entity_id, err)
            if not keep_existing:
                self._polygon_zones = []
            self._virtual_attributes[ATTR_POLYGON_LOAD_ERROR] = str(err)
        if _now is not None:
            self._update_polygon_from_sources()

    def _polygon_source_entities(self) -> list[str]:
        """Return explicit trackers, falling back to the configured person."""
        source_entities = self._location_source_entities()
        if source_entities:
            return source_entities
        person = self._polygon_config.get(CONF_POLYGON_PERSON_ENTITY)
        return [person] if person and person != self.entity_id else []

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
        try:
            accuracy = float(state.attributes.get(CONF_GPS_ACCURACY, 0) or 0)
        except (TypeError, ValueError):
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
                accuracy = float(state.attributes.get(CONF_GPS_ACCURACY, 0) or 0)
                if not isfinite(accuracy):
                    continue
                accuracy = max(0.0, accuracy)
                samples.append({
                    "entity_id": entity_id,
                    "latitude": position[0],
                    "longitude": position[1],
                    "gps_accuracy": accuracy,
                    "last_updated": state.last_updated,
                    "dominant": bool(rule.get("dominant", False)),
                    "priority": float(rule.get("priority", 100)),
                    "weight": float(rule.get("weight", 1)),
                })
            except (TypeError, ValueError):
                continue

        selected = select_tracker_position(
            samples,
            self._polygon_config[CONF_POLYGON_STRATEGY],
            float(self._polygon_config[CONF_POLYGON_DISTANCE_METERS]),
        )
        self._virtual_attributes.update({
            ATTR_POLYGON_PERSON: self._polygon_config.get(CONF_POLYGON_PERSON_ENTITY),
            ATTR_POLYGON_STRATEGY: self._polygon_config[CONF_POLYGON_STRATEGY],
            ATTR_POLYGON_ZONES: [zone["name"] for zone in self._polygon_zones],
            ATTR_POLYGON_SELECTION_REASON: selected["reason"] if selected else None,
            ATTR_POLYGON_SELECTED_SOURCE: selected["selected_source"] if selected else None,
            ATTR_POLYGON_SELECTED_MEMBERS: selected["members"] if selected else [],
        })
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
        location = zone["name"] if zone else self._polygon_config[CONF_POLYGON_AWAY_STATE]
        self._virtual_attributes[ATTR_POLYGON_ZONE] = zone["name"] if zone else None
        self._update_attributes()
        self._location = location
        self._coords = {
            ATTR_LATITUDE: selected["latitude"],
            ATTR_LONGITUDE: selected["longitude"],
            ATTR_RADIUS: 0,
        }
        self._gps_accuracy = selected["gps_accuracy"]
        self.async_schedule_update_ha_state()

    @callback
    def _async_location_source_changed(self, _event) -> None:
        self._update_location_from_sources()

    @staticmethod
    def _coordinates_from_state(state):
        """Read usable GPS coordinates from a Home Assistant state."""
        try:
            latitude = float(state.attributes[ATTR_LATITUDE])
            longitude = float(state.attributes[ATTR_LONGITUDE])
        except (KeyError, TypeError, ValueError):
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
            slugify(str(candidate)) == wanted
            for candidate in candidates
            if candidate
        )

    @classmethod
    def _coordinates_from_location_state(cls, hass, location_name):
        """Resolve a named Home Assistant zone to GPS coordinates."""
        if str(location_name).lower() in {"", "none", "not_home", "unknown", "unavailable"}:
            return None

        for zone_state in hass.states.async_all("zone"):
            if cls._zone_matches(zone_state, location_name):
                return cls._coordinates_from_state(zone_state)
        return None

    @classmethod
    def _position_from_state(cls, hass, state):
        """Read source coordinates directly or from its named zone state."""
        return (
            cls._coordinates_from_state(state)
            or cls._coordinates_from_location_state(hass, state.state)
        )

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

    @callback
    def _update_location_from_sources(self) -> None:
        """Track a recent outlier, otherwise use the sources' median position."""
        if not self._location_helper:
            return

        now = dt_util.utcnow()
        source_entities = self._location_source_entities()
        source_states = {
            entity_id: self.hass.states.get(entity_id)
            for entity_id in source_entities
        }
        positions = {
            entity_id: coordinates
            for entity_id, state in source_states.items()
            if state is not None
            if (coordinates := self._position_from_state(self.hass, state)) is not None
        }
        self._record_source_movements(positions, source_states)
        self._virtual_attributes.update({
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
        })
        if not positions:
            known_states = [
                state.state for state in source_states.values() if state is not None
            ]
            self._priority_source = None
            self._virtual_attributes.update({
                ATTR_LOCATION_MEDIAN_LATITUDE: None,
                ATTR_LOCATION_MEDIAN_LONGITUDE: None,
                ATTR_LOCATION_PRIORITY_SOURCE: None,
            })
            self._update_attributes()
            self.move_to_location(
                "home" if known_states and all(value == "home" for value in known_states) else "not_home",
            )
            return

        median = (
            self._median([position[0] for position in positions.values()]),
            median_longitude(position[1] for position in positions.values()),
        )
        self._virtual_attributes.update({
            ATTR_LOCATION_MEDIAN_LATITUDE: median[0],
            ATTR_LOCATION_MEDIAN_LONGITUDE: median[1],
        })

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
        self._update_attributes()
        latitude, longitude = positions[selected] if selected else median
        self.move_to_coords({
            ATTR_LATITUDE: latitude,
            ATTR_LONGITUDE: longitude,
            ATTR_RADIUS: 0,
        }, 0)

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
        _LOGGER.debug(f"{self._attr_name} moving to {new_location}")
        self._location = new_location
        self._coords = {}
        self._gps_accuracy = 0
        self.async_schedule_update_ha_state()

    def move_to_coords(self, new_coords, accuracy):
        _LOGGER.debug(f"{self._attr_name} moving via GPS to {new_coords} ({accuracy}m)")
        self._location = None
        self._coords = new_coords
        self._gps_accuracy = accuracy
        self.async_schedule_update_ha_state()

    def set_state(self, value) -> None:
        if self._location_helper or self._polygon_config:
            return
        self.move_to_location(value)


async def async_virtual_move_service(hass, call):
    for entity_id in call.data['entity_id']:
        _LOGGER.debug(f"moving {entity_id} --> {call.data}")

        _assert_managed_virtual_entity(hass, entity_id)
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
