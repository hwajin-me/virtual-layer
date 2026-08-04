"""
This component provides support for a virtual device tracker.

"""

import json
import logging
from datetime import datetime, timedelta, timezone
from math import asin, cos, radians, sin, sqrt
from collections.abc import Callable

import aiofiles
import voluptuous as vol

import homeassistant.helpers.config_validation as cv
from homeassistant.components.device_tracker import (
    DOMAIN as PLATFORM_DOMAIN,
    SourceType,
    TrackerEntity,
)
from homeassistant.components.zone import ATTR_RADIUS
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    CONF_DEVICES
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.config_validation import PLATFORM_SCHEMA
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util

from . import _assert_managed_virtual_entity, get_entity_from_domain, get_entity_configs
from .const import *
from .entity import VirtualEntity, virtual_schema


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
LOCATION_CHANGE_DISTANCE_METERS = 1
ATTR_LOCATION_MEDIAN_LATITUDE = "location_median_latitude"
ATTR_LOCATION_MEDIAN_LONGITUDE = "location_median_longitude"
ATTR_LOCATION_PRIORITY_SOURCE = "location_priority_source"
ATTR_LOCATION_SOURCE_LAST_MOVED = "location_source_last_moved"
ATTR_LOCATION_SOURCE_POSITIONS = "location_source_positions"

STATE_FILE = "/config/.storage/virtual.restore_state"

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_DEVICES, default=[]): cv.ensure_list
})

DEVICE_TRACKER_SCHEMA = vol.Schema(virtual_schema(DEFAULT_DEVICE_TRACKER_VALUE, {
    # Keep the helper flexible so older backup data with an invalid setting can
    # still load and be edited or removed through the UI.
    vol.Optional(CONF_LOCATION_HELPER): object,
}))


def validate_domain_options(config) -> None:
    """Validate UI-supplied location helper settings without rejecting backups."""
    value = config.get(CONF_LOCATION_HELPER)
    if value is None:
        return
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

    if distance_threshold <= 0 or priority_window <= 0:
        raise vol.Invalid("location_helper options must be positive")

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

tracker_states = {}

async def _async_load_json(file_name):
    try:
        async with aiofiles.open(file_name, 'r') as state_file:
            contents = await state_file.read()
            return json.loads(contents)
    except Exception as e:
        return {}


def _write_state():
    global tracker_states
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(tracker_states, f)
    except:
        pass

def _state_changed(event):
    entity_id = event.data.get('entity_id', None)
    new_state = event.data.get('new_state', None)
    if entity_id is None or new_state is None:
        _LOGGER.info(f'state changed error')
        return

    # update database
    _LOGGER.info(f"moving {entity_id} to {new_state.state}")
    global tracker_states
    tracker_states[entity_id] = new_state.state
    _write_state()


def _shutting_down(event):
    _LOGGER.info(f'shutting down {event}')
    _write_state()


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
        if ATTR_LONGITUDE in state.attributes and ATTR_LATITUDE in state.attributes:
            self._location = None
            self._coords = {
                ATTR_LONGITUDE: state.attributes[ATTR_LONGITUDE],
                ATTR_LATITUDE: state.attributes[ATTR_LATITUDE],
                ATTR_RADIUS: 0
            }
        else:
            self._location = state.state
            self._coords = {}
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

        if distance_threshold <= 0 or priority_window <= 0:
            _LOGGER.warning("Ignoring non-positive location helper configuration")
            return None

        return {
            CONF_LOCATION_HELPER_DISTANCE_METERS: distance_threshold,
            CONF_LOCATION_HELPER_PRIORITY_WINDOW_SECONDS: priority_window,
        }

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

        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            return None
        return latitude, longitude

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
        return 6_371_000 * 2 * asin(sqrt(value))

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
            if (coordinates := self._coordinates_from_state(state)) is not None
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
            self._median([position[1] for position in positions.values()]),
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
    def location_name(self) -> str | None:
        """Return a location name for the current location of the device."""
        return self._location

    @property
    def source_type(self) -> SourceType | str:
        if self._coords:
            return "gps"
        return "virtual"

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
        self.async_schedule_update_ha_state()

    def move_to_coords(self, new_coords, accuracy):
        _LOGGER.debug(f"{self._attr_name} moving via GPS to {new_coords} ({accuracy}m)")
        self._location = None
        self._coords = new_coords
        self._gps_accuracy = accuracy
        self.async_schedule_update_ha_state()

    def set_state(self, value) -> None:
        if self._location_helper:
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
