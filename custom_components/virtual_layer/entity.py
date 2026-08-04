"""
This component provides support for a virtual sensor.

This class adds persistence to an entity.
"""

import logging
import pprint
from datetime import timedelta

import voluptuous as vol

import homeassistant.helpers.config_validation as cv
from homeassistant.components.cover import ATTR_CURRENT_POSITION
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_ENTITY_ID,
    STATE_CLOSED,
)
from homeassistant.core import callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.event import (
    TrackTemplate,
    async_track_template_result,
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.template import Template
from homeassistant.util import slugify

from .const import *


_LOGGER = logging.getLogger(__name__)

positive_tick = vol.All(vol.Coerce(float), vol.Range(min=0, min_included=False))

def virtual_schema(default_initial_value: str, extra_attrs):
    schema = {
        vol.Required(CONF_NAME): cv.string,
        vol.Optional(CONF_INITIAL_VALUE, default=default_initial_value): cv.string,
        vol.Optional(CONF_INITIAL_AVAILABILITY, default=DEFAULT_AVAILABILITY): cv.boolean,
        vol.Optional(CONF_ATTRIBUTES, default=dict): dict,
        vol.Optional(CONF_AUTO_HELPER): object,
        vol.Optional(CONF_ATTRIBUTE_SOURCES, default=dict): dict,
        vol.Optional(CONF_ATTRIBUTE_TEMPLATES, default=dict): dict,
        vol.Optional(CONF_AVAILABILITY_TEMPLATE): cv.template,
        vol.Optional(CONF_PERSISTENT, default=DEFAULT_PERSISTENT): cv.boolean,
        vol.Optional(CONF_PULL_INTERVAL, default=0): vol.All(vol.Coerce(int), vol.Range(min=0)),
        vol.Optional(CONF_SOURCE_ENTITIES, default=list): vol.All(cv.ensure_list, [cv.entity_id]),
        vol.Optional(CONF_TEMPLATE_SOURCES, default=dict): dict,
        vol.Optional(CONF_VALUE_TEMPLATE): cv.template,
        vol.Optional(ATTR_DEVICE_ID, default="NOTYET"): cv.string,
        vol.Optional(CONF_MANUFACTURER): cv.string,
        vol.Optional(CONF_MODEL): cv.string,
        vol.Optional(CONF_SW_VERSION): cv.string,
        vol.Optional(CONF_HW_VERSION): cv.string,
        vol.Optional(CONF_SERIAL_NUMBER): cv.string,
        vol.Optional(ATTR_ENTITY_ID, default="NOTYET"): cv.string,
        vol.Optional(ATTR_UNIQUE_ID, default="NOTYET"): cv.string,
    }
    schema.update(extra_attrs)
    return schema


class VirtualEntity(RestoreEntity):
    """A base class to add state restoring.
    """

    # Are we saving/restoring this entity
    _persistent: bool = True

    def __init__(self, config, domain, old_style : bool = False):
        """Initialize an Virtual Sensor."""
        _LOGGER.debug(f"creating-virtual-{domain}={config}")
        self._config = config
        self._persistent = config.get(CONF_PERSISTENT)
        self._virtual_attributes = {
            name: value
            for name, value in dict(config.get(CONF_ATTRIBUTES, {})).items()
            if name not in RESERVED_VIRTUAL_ATTRIBUTE_NAMES
        }
        self._attribute_sources = {
            name: self._normalize_attribute_source(source)
            for name, source in dict(config.get(CONF_ATTRIBUTE_SOURCES, {})).items()
            if name not in RESERVED_VIRTUAL_ATTRIBUTE_NAMES
        }
        self._attribute_templates = {
            name: template
            for name, template in dict(config.get(CONF_ATTRIBUTE_TEMPLATES, {})).items()
            if name not in RESERVED_VIRTUAL_ATTRIBUTE_NAMES
        }
        self._pull_interval = config.get(CONF_PULL_INTERVAL, 0)
        self._source_entities = config.get(CONF_SOURCE_ENTITIES, [])
        self._template_sources = {
            name: self._normalize_template_source(source)
            for name, source in dict(config.get(CONF_TEMPLATE_SOURCES, {})).items()
        }
        self._value_template = config.get(CONF_VALUE_TEMPLATE)
        self._availability_template = config.get(CONF_AVAILABILITY_TEMPLATE)
        self._refresh_remove_listeners = []

        if old_style:
            # Build name, entity id and unique id. We do this because historically
            # the non-domain piece of the entity_id was prefixed with virtual_ so
            # we build the pieces manually to make sure.
            self._attr_name = config.get(CONF_NAME)
            if self._attr_name.startswith("!"):
                self._attr_name = self._attr_name[1:]
                self.entity_id = f'{domain}.{slugify(self._attr_name)}'
            else:
                self.entity_id = f'{domain}.{COMPONENT_DOMAIN}_{slugify(self._attr_name)}'
            self._attr_unique_id = slugify(self._attr_name)

        else:
            # Build name, entity id and unique id. We do this because historically
            # the non-domain piece of the entity_id was prefixed with virtual_ so
            # we build the pieces manually to make sure.
            self._attr_name = config.get(CONF_NAME)

            self.entity_id = config.get(ATTR_ENTITY_ID)
            if self.entity_id == "NOTYET":
                if self._attr_name.startswith("+"):
                    self._attr_name = self._attr_name[1:]
                    self.entity_id = f'{domain}.{COMPONENT_DOMAIN}_{slugify(self._attr_name)}'
                else:
                    self.entity_id = f'{domain}.{slugify(self._attr_name)}'

            self._attr_unique_id = config.get(ATTR_UNIQUE_ID, None)
            if self._attr_unique_id == "NOTYET":
                self._attr_unique_id = slugify(self._attr_name)

            if config.get(ATTR_DEVICE_ID) != "NOTYET":
                _LOGGER.debug("setting up device info")
                device_info = {
                    "identifiers": {(COMPONENT_DOMAIN, config.get(ATTR_DEVICE_ID))},
                    "manufacturer": config.get(CONF_MANUFACTURER) or COMPONENT_MANUFACTURER,
                    "model": config.get(CONF_MODEL) or COMPONENT_MODEL,
                }
                for config_key, info_key in (
                    (CONF_SW_VERSION, "sw_version"),
                    (CONF_HW_VERSION, "hw_version"),
                    (CONF_SERIAL_NUMBER, "serial_number"),
                ):
                    if config.get(config_key):
                        device_info[info_key] = config[config_key]
                self._attr_device_info = DeviceInfo(**device_info)

        _LOGGER.info(f'VirtualEntity {self._attr_name} created')

    def _create_state(self, config):
        _LOGGER.info(f'VirtualEntity {self.unique_id}: creating initial state')
        self._attr_available = config.get(CONF_INITIAL_AVAILABILITY)

    def _restore_state(self, state, config):
        _LOGGER.info(f'VirtualEntity {self.unique_id}: restoring state')
        _LOGGER.debug(f'VirtualEntity:: state={pprint.pformat(state.state)}')
        _LOGGER.debug(f'VirtualEntity:: attr={pprint.pformat(state.attributes)}')
        self._attr_available = state.attributes.get(
            ATTR_AVAILABLE,
            config.get(CONF_INITIAL_AVAILABILITY, DEFAULT_AVAILABILITY),
        )
        attribute_names = state.attributes.get(
            ATTR_VIRTUAL_ATTRIBUTES,
            list(self._virtual_attributes.keys()),
        )
        self._virtual_attributes = {
            name: state.attributes.get(name)
            for name in attribute_names
            if name in state.attributes
            if name not in RESERVED_VIRTUAL_ATTRIBUTE_NAMES
        }

    def _update_attributes(self):
        self._attr_extra_state_attributes = {
            ATTR_PERSISTENT: self._persistent,
            ATTR_AVAILABLE: self._attr_available,
        }
        self._attr_extra_state_attributes.update(self._virtual_attributes)
        if self._virtual_attributes:
            self._attr_extra_state_attributes[ATTR_VIRTUAL_ATTRIBUTES] = list(self._virtual_attributes.keys())
        if _LOGGER.isEnabledFor(logging.DEBUG):
            self._attr_extra_state_attributes.update({
                ATTR_ENTITY_ID: self.entity_id,
                ATTR_UNIQUE_ID: self.unique_id,
            })

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        state = await self.async_get_last_state()
        if not self._persistent or not state:
            self._create_state(self._config)
        else:
            self._restore_state(state, self._config)
        self._update_attributes()
        self._setup_templates()
        self._apply_templates()

    async def async_will_remove_from_hass(self) -> None:
        """Call when entity is being removed from hass."""
        for remove_listener in self._refresh_remove_listeners:
            remove_listener()
        self._refresh_remove_listeners = []
        await super().async_will_remove_from_hass()

    def set_available(self, value):
        self._attr_available = value
        self._update_attributes()
        self.async_schedule_update_ha_state()

    def set_attributes(self, attributes):
        self._virtual_attributes.update({
            name: value
            for name, value in attributes.items()
            if name not in RESERVED_VIRTUAL_ATTRIBUTE_NAMES
        })
        self._update_attributes()
        self.async_schedule_update_ha_state()

    def clear_attributes(self, attributes):
        if attributes:
            for attribute in attributes:
                if attribute in RESERVED_VIRTUAL_ATTRIBUTE_NAMES:
                    continue
                self._virtual_attributes.pop(attribute, None)
        else:
            self._virtual_attributes.clear()
        self._update_attributes()
        self.async_schedule_update_ha_state()

    def set_state(self, value) -> None:
        raise NotImplementedError

    @callback
    def _setup_templates(self):
        source_entities = set(self._source_entities)
        source_entities.update(
            source[ATTR_ENTITY_ID]
            for source in self._attribute_sources.values()
            if source.get(ATTR_ENTITY_ID)
        )
        source_entities.update(
            source[ATTR_ENTITY_ID]
            for source in self._template_sources.values()
            if source.get(ATTR_ENTITY_ID)
        )
        source_entities.discard(self.entity_id)

        @callback
        def _async_source_entity_changed(_event):
            self._apply_templates()

        if source_entities:
            self._refresh_remove_listeners.append(async_track_state_change_event(
                self.hass,
                source_entities,
                _async_source_entity_changed,
            ))

        templates = [
            template
            for template in (
                self._value_template,
                self._availability_template,
                *self._attribute_templates.values(),
            )
            if template
        ]
        for template in templates:
            tracked_template = template if isinstance(template, Template) else Template(
                str(template), self.hass
            )

            @callback
            def _async_template_changed(_event, _updates):
                self._apply_templates()

            self._refresh_remove_listeners.append(
                async_track_template_result(
                    self.hass,
                    [TrackTemplate(tracked_template, self._template_variables())],
                    _async_template_changed,
                ).async_remove
            )

        if self._pull_interval:
            self._refresh_remove_listeners.append(async_track_time_interval(
                self.hass,
                lambda _now: self._apply_templates(),
                timedelta(seconds=self._pull_interval),
            ))

    def _normalize_attribute_source(self, source):
        if isinstance(source, str):
            entity_id, _, attribute = source.rpartition(".")
            return {
                ATTR_ENTITY_ID: entity_id,
                CONF_ATTRIBUTE: attribute,
            }
        if not isinstance(source, dict):
            return {}
        return {
            ATTR_ENTITY_ID: source.get(ATTR_ENTITY_ID),
            CONF_ATTRIBUTE: source.get(CONF_ATTRIBUTE),
        }

    def _normalize_template_source(self, source):
        if isinstance(source, str):
            if "." in source:
                domain, _, object_id = source.partition(".")
                if "." not in object_id:
                    return {
                        ATTR_ENTITY_ID: source,
                        CONF_ATTRIBUTE: "state",
                    }
            entity_id, _, attribute = source.rpartition(".")
            return {
                ATTR_ENTITY_ID: entity_id,
                CONF_ATTRIBUTE: attribute,
            }
        if not isinstance(source, dict):
            return {}
        return {
            ATTR_ENTITY_ID: source.get(ATTR_ENTITY_ID),
            CONF_ATTRIBUTE: source.get(CONF_ATTRIBUTE, "state"),
        }

    def _render_template(self, template):
        variables = self._template_variables()
        if isinstance(template, Template):
            template.hass = self.hass
            return template.async_render(variables=variables, parse_result=False)
        return Template(str(template), self.hass).async_render(variables=variables, parse_result=False)

    def _template_variables(self):
        variables = {}
        for name, source in self._template_sources.items():
            entity_id = source.get(ATTR_ENTITY_ID)
            attribute = source.get(CONF_ATTRIBUTE)
            if not entity_id or not attribute:
                continue

            state = self.hass.states.get(entity_id)
            if state is None:
                variables[name] = None
                continue

            if attribute == "state":
                variables[name] = state.state
            else:
                variables[name] = state.attributes.get(attribute)
        return variables

    def _template_to_bool(self, value) -> bool:
        value = str(value).lower()
        return value in ["y", "yes", "t", "true", "on", "1"]

    @callback
    def _apply_templates(self):
        changed = False

        if self._availability_template:
            try:
                self._attr_available = self._template_to_bool(self._render_template(self._availability_template))
                changed = True
            except Exception as e:
                _LOGGER.warning(f"Unable to render availability template for {self.entity_id}: {e}")

        if self._value_template:
            try:
                self.set_state(self._render_template(self._value_template))
                changed = True
            except Exception as e:
                _LOGGER.warning(f"Unable to render value template for {self.entity_id}: {e}")

        for name, template in self._attribute_templates.items():
            try:
                self._virtual_attributes[name] = self._render_template(template)
                changed = True
            except Exception as e:
                _LOGGER.warning(f"Unable to render attribute template {name} for {self.entity_id}: {e}")

        if self._attribute_sources:
            try:
                changed = self._apply_attribute_sources() or changed
            except Exception as e:
                _LOGGER.warning(f"Unable to apply attribute sources for {self.entity_id}: {e}")

        if changed:
            self._update_attributes()
            self.async_schedule_update_ha_state()

    def _apply_attribute_sources(self):
        changed = False
        for name, source in self._attribute_sources.items():
            entity_id = source.get(ATTR_ENTITY_ID)
            attribute = source.get(CONF_ATTRIBUTE)
            if not entity_id or not attribute:
                continue

            state = self.hass.states.get(entity_id)
            if state is None:
                value = None
            elif attribute == "state":
                value = state.state
            else:
                value = state.attributes.get(attribute)

            if self._virtual_attributes.get(name) != value:
                self._virtual_attributes[name] = value
                changed = True
        return changed


class VirtualOpenableEntity(VirtualEntity):
    """Representation of a Virtual openable.

    This can handle cover and valve devices. If they diverge too much in the
    future we will need to rethink this.
    """

    _current_position: float
    _target_position: float | None
    _positions_per_tick: float | None
    _open_close_duration: int
    _open_close_tick: float
    _open_close_operation_started: bool | None
    _attr_is_closed: bool

    def __init__(self, config, domain, old_style: bool):
        """Initialize the Virtual openable device."""
        _LOGGER.debug(f"creating-virtual-openable-{domain}={config}")
        super().__init__(config, domain, old_style)

        self._attr_device_class = config.get(CONF_CLASS)
        self._open_close_duration = config.get(CONF_OPEN_CLOSE_DURATION)
        self._open_close_tick = config.get(CONF_OPEN_CLOSE_TICK)

        self._open_close_operation_started = None
        self._current_position = 0
        self._target_position = None
        self._positions_per_tick = None

        _LOGGER.info(f"VirtualOpenable: {self.name} created")

    def _create_state(self, config):
        super()._create_state(config)

        self._attr_is_closed = config.get(CONF_INITIAL_VALUE).lower() == STATE_CLOSED
        if self._attr_is_closed:
            self._current_position = 0
        else:
            self._current_position = 100

    def _restore_state(self, state, config):
        super()._restore_state(state, config)

        # Cover and valve use the same position state. If this changes we will
        # need to add this into the derived class.
        if ATTR_CURRENT_POSITION in state.attributes:
            self._current_position = state.attributes[ATTR_CURRENT_POSITION]
        self._attr_is_closed = state.state.lower() == STATE_CLOSED

    def _update_attributes(self):
        super()._update_attributes()
        self._attr_extra_state_attributes.update({
            name: value for name, value in (
                (ATTR_DEVICE_CLASS, self._attr_device_class),
            ) if value is not None
        })

    def _cancel_timer(self) -> None:
        """Cancel the current movement timer if active."""
        if hasattr(self, '_timer_handle') and self._timer_handle:
            self._timer_handle()
            self._timer_handle = None

    def _stop(self) -> None:
        _LOGGER.info(f"stopping {self.name} at position {self._current_position}")

        self._cancel_timer()

        self._target_position = None
        self._positions_per_tick = None
        self._attr_is_opening = False
        self._attr_is_closing = False

        self._attr_is_closed = (self._current_position == 0)

        self.async_write_ha_state()

    def _set_direction_flags(self, target_position: float) -> None:
        """Set opening/closing flags based on target position."""
        if target_position < self._current_position:
            self._attr_is_closing = True
            self._attr_is_opening = False
        else:
            self._attr_is_opening = True
            self._attr_is_closing = False

        self.async_write_ha_state()

    def _set_position(self, position: int) -> None:
        _LOGGER.info(f"setting {self.name} position {position}")

        self._cancel_timer()

        position = max(0, min(100, int(position)))

        self._target_position = position

        if self._target_position == self._current_position:
            return

        if self._open_close_tick > self._open_close_duration:
            _LOGGER.warning(f"Tick duration {self._open_close_tick} > total duration {self._open_close_duration}, capping to {self._open_close_duration}")
            self._open_close_tick = self._open_close_duration

        if self._open_close_duration == 0:
            # Transition through opening/closing state for automations
            self._set_direction_flags(self._target_position)

            # Immediately set final state
            self._current_position = self._target_position
            self._attr_is_opening = False
            self._attr_is_closing = False
            self._attr_is_closed = (self._current_position == 0)
            self._target_position = None

            self.async_schedule_update_ha_state(force_refresh=True)
            return

        distance = abs(self._target_position - self._current_position)
        movement_duration = (distance / 100.0) * self._open_close_duration
        total_ticks = max(1, int(movement_duration / self._open_close_tick))
        self._positions_per_tick = distance / total_ticks

        self._set_direction_flags(self._target_position)
        self._timer_handle = async_call_later(self.hass, self._open_close_tick, self._update_position)

    def set_state(self, value) -> None:
        value = str(value).lower()
        if value in ["open", "opened", "on", "true", "1"]:
            self._set_position(100)
        elif value in ["closed", "close", "off", "false", "0"]:
            self._set_position(0)
        else:
            self._set_position(int(value))

    @callback
    def _update_position(self, _now) -> None:
        if self._target_position is None:
            return

        if self._attr_is_closing:
            next_pos = max(self._target_position, self._current_position - self._positions_per_tick)
        else:
            next_pos = min(self._target_position, self._current_position + self._positions_per_tick)

        self._current_position = next_pos

        if self._current_position == self._target_position:
            self._stop()
        else:
            self.async_write_ha_state()
            self._timer_handle = async_call_later(self.hass, self._open_close_tick, self._update_position)
