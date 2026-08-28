"""
This component provides support for a virtual sensor.

This class adds persistence to an entity.
"""

import inspect
import logging
import math
import re
from asyncio import get_running_loop
from contextvars import ContextVar
from datetime import timedelta
from enum import Enum
from functools import wraps
from math import isfinite
from pathlib import Path

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components.cover import ATTR_CURRENT_POSITION
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_ENTITY_ID,
    CONF_ICON,
    EVENT_HOMEASSISTANT_STARTED,
    STATE_CLOSED,
    STATE_UNAVAILABLE,
)
from homeassistant.core import Context, CoreState, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.event import (
    TrackTemplate,
    async_call_later,
    async_track_state_change_event,
    async_track_template_result,
    async_track_time_interval,
)
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.script import Script
from homeassistant.helpers.script_variables import ScriptRunVariables
from homeassistant.helpers.template import Template, TemplateError
from homeassistant.util import slugify

from .const import *

_LOGGER = logging.getLogger(__name__)
_VIRTUAL_ENTITY_COMMAND_NAMES = frozenset().union(
    *VIRTUAL_ENTITY_COMMANDS.values(),
)
_MISSING = object()
MAX_LOCAL_MEDIA_BYTES = 25 * 1024 * 1024
_STARTUP_AVAILABILITY_RETRY_DELAYS = (5, 10, 15, 30)
_LEGACY_ENUM_TEMPLATE_RE = re.compile(
    r"<[A-Za-z_][A-Za-z0-9_]*"
    r"(?:\.[A-Za-z_][A-Za-z0-9_]*(?:\|[A-Za-z_][A-Za-z0-9_]*)*)+:\s*"
    r"((?:'(?:\\.|[^'\\])*')|(?:\"(?:\\.|[^\"\\])*\")|"
    r"-?\d+(?:\.\d+)?|True|False|None)>"
)
_COMMAND_ACTION_CHAIN: ContextVar[frozenset[tuple[int, str]]] = ContextVar(
    "virtual_layer_command_action_chain",
    default=frozenset(),
)


def nearest_step_value(
    value: float,
    minimum: float,
    maximum: float,
    step: float | None,
) -> float:
    """Clamp a finite numeric value and snap it to the nearest advertised step."""
    bounded = max(minimum, min(maximum, value))
    if step is None or not math.isfinite(step) or step <= 0:
        return bounded
    step_index = math.floor(((bounded - minimum) / step) + 0.5)
    snapped = max(minimum, min(maximum, minimum + (step_index * step)))
    # Avoid exposing floating-point artifacts such as 0.30000000000000004.
    return round(snapped, 12)


def repair_legacy_enum_template(template: str) -> str:
    """Repair enum repr literals emitted by older helper generation."""
    if not isinstance(template, str) or "<" not in template:
        return template

    def _replace(match: re.Match[str]) -> str:
        prefix = template[: match.start()]
        expression_start = max(prefix.rfind("{{"), prefix.rfind("{%"))
        expression_end = max(prefix.rfind("}}"), prefix.rfind("%}"))
        if expression_start <= expression_end:
            return match.group(0)

        quote = None
        escaped = False
        for character in template[expression_start + 2 : match.start()]:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif quote is None and character in {"'", '"'}:
                quote = character
            elif character == quote:
                quote = None
        return match.group(0) if quote is not None else match.group(1)

    return _LEGACY_ENUM_TEMPLATE_RE.sub(_replace, template)


def repair_legacy_template_data(value, _seen=None, _depth=0):
    """Repair legacy enum reprs in a JSON-like template container."""
    if isinstance(value, str):
        return repair_legacy_enum_template(value)
    if _depth > 100:
        return None
    if not isinstance(value, (dict, list, tuple)):
        return value
    if _seen is None:
        _seen = set()
    identity = id(value)
    if identity in _seen:
        return None
    _seen.add(identity)
    try:
        if isinstance(value, dict):
            return {
                key: repair_legacy_template_data(item, _seen, _depth + 1)
                for key, item in value.items()
            }
        items = [
            repair_legacy_template_data(item, _seen, _depth + 1)
            for item in value
        ]
        return tuple(items) if isinstance(value, tuple) else items
    finally:
        _seen.remove(identity)

def nonnegative_int(value) -> int:
    """Coerce a non-negative integer without accepting booleans as numbers."""
    if isinstance(value, bool):
        raise vol.Invalid("value must be a non-negative integer")
    return cv.positive_int(value)


def number_float(value) -> float:
    """Coerce a number without accepting booleans as 0/1."""
    if isinstance(value, bool):
        raise vol.Invalid("value must be a number")
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError) as err:
        raise vol.Invalid("value must be a number") from err


def finite_float(value) -> float:
    """Coerce a finite number without accepting booleans as 0/1."""
    value = number_float(value)
    if not math.isfinite(value):
        raise vol.Invalid("value must be a finite number")
    return value


def positive_tick(value) -> float:
    """Coerce a positive timer interval without accepting booleans."""
    return vol.All(finite_float, vol.Range(min=0, min_included=False))(value)


def allowed_local_path(hass, file_name: str) -> str | None:
    """Resolve a media path inside HA's config or external allowlist."""
    if not isinstance(file_name, str) or not file_name.strip():
        return None
    try:
        candidate = Path(file_name)
        if not candidate.is_absolute():
            candidate = Path(hass.config.config_dir) / candidate
        candidate = candidate.resolve()
        config_root = Path(hass.config.config_dir).resolve()
    except (OSError, RuntimeError):
        return None
    if candidate.is_relative_to(config_root) or hass.config.is_allowed_path(
        str(candidate)
    ):
        return str(candidate)
    return None

def virtual_schema(default_initial_value: str, extra_attrs):
    schema = {
        vol.Required(CONF_NAME): cv.string,
        vol.Optional(CONF_INITIAL_VALUE, default=default_initial_value): cv.string,
        vol.Optional(CONF_INITIAL_AVAILABILITY, default=DEFAULT_AVAILABILITY): cv.boolean,
        vol.Optional(CONF_ATTRIBUTES, default=dict): dict,
        vol.Optional(CONF_ICON): cv.string,
        vol.Optional(CONF_ICON_TEMPLATE): cv.template,
        vol.Optional(CONF_AUTO_HELPER): object,
        vol.Optional(CONF_ATTRIBUTE_SOURCES, default=dict): dict,
        vol.Optional(CONF_ATTRIBUTE_TEMPLATES, default=dict): dict,
        vol.Optional(CONF_NATIVE_TEMPLATES, default=dict): dict,
        vol.Optional(CONF_COMMAND_ACTIONS, default=dict): dict,
        vol.Optional(CONF_AVAILABILITY_TEMPLATE): cv.template,
        vol.Optional(CONF_EVENT_HOOKS, default=list): vol.All(cv.ensure_list, [dict]),
        vol.Optional(CONF_PERSISTENT, default=DEFAULT_PERSISTENT): cv.boolean,
        vol.Optional(CONF_PULL_INTERVAL, default=0): nonnegative_int,
        vol.Optional(CONF_SOURCE_ENTITIES, default=list): vol.All(cv.ensure_list, [cv.entity_id]),
        vol.Optional(CONF_TEMPLATE_SOURCES, default=dict): dict,
        vol.Optional(CONF_VALUE_TEMPLATE): cv.template,
        vol.Optional(ATTR_DEVICE_ID, default="NOTYET"): cv.string,
        vol.Optional(CONF_MANUFACTURER): cv.string,
        vol.Optional(CONF_MODEL): cv.string,
        vol.Optional(CONF_SW_VERSION): cv.string,
        vol.Optional(CONF_HW_VERSION): cv.string,
        vol.Optional(CONF_SERIAL_NUMBER): cv.string,
        vol.Optional(CONF_CONFIGURATION_URL): cv.string,
        vol.Optional(CONF_SUGGESTED_AREA): cv.string,
        vol.Optional(CONF_VIA_DEVICE_ID): cv.string,
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

    _COMMAND_METHOD_EXCLUSIONS = frozenset({
        "async_added_to_hass",
        "async_will_remove_from_hass",
        "async_camera_image",
        "async_image",
    })
    _NATIVE_TEMPLATE_RESERVED_NAMES = RESERVED_NATIVE_TEMPLATE_NAMES

    def __init_subclass__(cls, **kwargs):
        """Add configurable actions to native entity commands."""
        super().__init_subclass__(**kwargs)
        methods = dict(cls.__dict__)
        for base in cls.__mro__[1:]:
            if base is VirtualEntity:
                break
            for method_name, method in base.__dict__.items():
                methods.setdefault(method_name, method)
        for method_name, method in tuple(methods.items()):
            command = method_name.removeprefix("async_")
            if (
                not method_name.startswith("async_")
                or method_name.startswith("async_virtual_")
                or method_name in cls._COMMAND_METHOD_EXCLUSIONS
                or command not in _VIRTUAL_ENTITY_COMMAND_NAMES
                or not inspect.iscoroutinefunction(method)
                or getattr(method, "_virtual_action_wrapped", False)
            ):
                continue

            @wraps(method)
            async def _with_command_action(self, *args, __method=method, __command=command, **kwargs):
                action_result = await self._async_run_command_action(
                    __command,
                    __method,
                    args,
                    kwargs,
                )
                if action_result is False:
                    return None
                return await __method(self, *args, **kwargs)

            _with_command_action._virtual_action_wrapped = True
            setattr(cls, method_name, _with_command_action)

    def __init__(self, config, domain, old_style : bool = False):
        """Initialize an Virtual Sensor."""
        _LOGGER.debug(
            "Creating virtual %s entity %s",
            domain,
            config.get(ATTR_ENTITY_ID) or config.get(CONF_NAME),
        )
        self._config = config
        self._platform_domain = domain
        self._configured_icon = config.get(CONF_ICON)
        self._attr_icon = self._configured_icon
        self._icon_template = repair_legacy_enum_template(
            config.get(CONF_ICON_TEMPLATE)
        )
        self._persistent = config.get(CONF_PERSISTENT)
        self._virtual_attributes = {
            name: value
            for name, value in dict(config.get(CONF_ATTRIBUTES, {})).items()
            if name not in EXCLUDED_VIRTUAL_ATTRIBUTE_NAMES
        }
        self._attribute_sources = {
            name: self._normalize_attribute_source(source)
            for name, source in dict(config.get(CONF_ATTRIBUTE_SOURCES, {})).items()
            if name not in EXCLUDED_VIRTUAL_ATTRIBUTE_NAMES
        }
        self._attribute_templates = {
            name: repair_legacy_enum_template(template)
            for name, template in dict(config.get(CONF_ATTRIBUTE_TEMPLATES, {})).items()
            if name not in EXCLUDED_VIRTUAL_ATTRIBUTE_NAMES
            and isinstance(template, str)
        }
        self._native_templates = {
            str(name).strip(): repair_legacy_enum_template(template)
            for name, template in dict(config.get(CONF_NATIVE_TEMPLATES, {})).items()
            if self._valid_native_template_name(name) and isinstance(template, str)
        }
        self._command_actions = repair_legacy_template_data(
            dict(config.get(CONF_COMMAND_ACTIONS, {}))
        )
        self._command_scripts = {}
        self._pull_interval = config.get(CONF_PULL_INTERVAL, 0)
        self._source_entities = config.get(CONF_SOURCE_ENTITIES, [])
        self._template_sources = {
            name: self._normalize_template_source(source)
            for name, source in dict(config.get(CONF_TEMPLATE_SOURCES, {})).items()
        }
        self._value_template = repair_legacy_enum_template(
            config.get(CONF_VALUE_TEMPLATE)
        )
        self._availability_template = repair_legacy_enum_template(
            config.get(CONF_AVAILABILITY_TEMPLATE)
        )
        self._event_hooks = [
            repair_legacy_template_data(hook)
            for hook in config.get(CONF_EVENT_HOOKS, [])
            if isinstance(hook, dict) and hook.get("enabled", True)
        ]
        self._hook_debounce_cancelers = {}
        self._refresh_remove_listeners = []
        self._configured_virtual_attribute_names = {
            *self._virtual_attributes,
            *self._attribute_sources,
            *self._attribute_templates,
        }

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
                self._attr_name = self._attr_name.removeprefix("+")
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
                    (CONF_CONFIGURATION_URL, "configuration_url"),
                ):
                    if config.get(config_key):
                        device_info[info_key] = config[config_key]
                self._attr_device_info = DeviceInfo(**device_info)

        _LOGGER.debug("VirtualEntity %s created", self._attr_name)

    def _create_state(self, config):
        _LOGGER.debug("VirtualEntity %s: creating initial state", self.unique_id)
        self._attr_available = config.get(CONF_INITIAL_AVAILABILITY)

    def _restore_state(self, state, config):
        _LOGGER.debug("VirtualEntity %s: restoring state", self.unique_id)
        if self._availability_template:
            # Availability templates are authoritative. Do not briefly expose
            # a stale restored ``available=false`` before the current source
            # state is rendered during async_added_to_hass.
            self._attr_available = config.get(
                CONF_INITIAL_AVAILABILITY,
                DEFAULT_AVAILABILITY,
            )
        else:
            restored_availability = state.attributes.get(
                ATTR_AVAILABLE,
                config.get(CONF_INITIAL_AVAILABILITY, DEFAULT_AVAILABILITY),
            )
            try:
                self._attr_available = cv.boolean(restored_availability)
            except vol.Invalid:
                self._attr_available = config.get(
                    CONF_INITIAL_AVAILABILITY,
                    DEFAULT_AVAILABILITY,
                )
        attribute_names = state.attributes.get(
            ATTR_VIRTUAL_ATTRIBUTES,
            list(self._virtual_attributes.keys()),
        )
        if not isinstance(attribute_names, (list, tuple, set)):
            attribute_names = list(self._virtual_attributes)
        attribute_names = [
            name for name in attribute_names if isinstance(name, str)
        ]
        previous_configured_names = state.attributes.get(
            ATTR_CONFIGURED_VIRTUAL_ATTRIBUTES,
            [],
        )
        if not isinstance(previous_configured_names, (list, tuple, set)):
            previous_configured_names = []
        previous_configured_names = {
            name for name in previous_configured_names if isinstance(name, str)
        }
        self._virtual_attributes = {
            name: state.attributes.get(name)
            for name in attribute_names
            if name in state.attributes
            if name not in EXCLUDED_VIRTUAL_ATTRIBUTE_NAMES
            if name not in previous_configured_names
            or name in self._configured_virtual_attribute_names
        }

    def _update_attributes(self):
        self._attr_extra_state_attributes = {
            ATTR_PERSISTENT: self._persistent,
            ATTR_AVAILABLE: self._attr_available,
        }
        self._attr_extra_state_attributes.update(self._virtual_attributes)
        if self._virtual_attributes:
            self._attr_extra_state_attributes[ATTR_VIRTUAL_ATTRIBUTES] = list(self._virtual_attributes.keys())
        self._attr_extra_state_attributes[ATTR_CONFIGURED_VIRTUAL_ATTRIBUTES] = sorted(
            self._configured_virtual_attribute_names
        )
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
            prerequisites_applied = self._apply_restore_prerequisite_templates()
            self._restore_state(state, self._config)
            if prerequisites_applied:
                self._native_templates_applied()
        self._update_attributes()
        self._setup_templates()
        self._apply_templates()

    async def async_will_remove_from_hass(self) -> None:
        """Call when entity is being removed from hass."""
        for script in self._command_scripts.values():
            await script.async_stop()
        self._command_scripts = {}
        for remove_listener in self._refresh_remove_listeners:
            remove_listener()
        for remove_listener in self._hook_debounce_cancelers.values():
            remove_listener()
        self._refresh_remove_listeners = []
        self._hook_debounce_cancelers = {}
        await super().async_will_remove_from_hass()

    def set_available(self, value):
        self._attr_available = value
        self._update_attributes()
        self.async_schedule_update_ha_state()

    def _schedule_state_update(self, force_refresh: bool = False) -> None:
        """Schedule a state update safely from loop or executor contexts."""
        try:
            on_hass_loop = self.hass is None or get_running_loop() is self.hass.loop
        except RuntimeError:
            on_hass_loop = False
        if on_hass_loop:
            self.async_schedule_update_ha_state(force_refresh=force_refresh)
        else:
            self.schedule_update_ha_state(force_refresh=force_refresh)

    def set_attributes(self, attributes):
        self._virtual_attributes.update({
            name: value
            for name, value in attributes.items()
            if name not in EXCLUDED_VIRTUAL_ATTRIBUTE_NAMES
        })
        self._update_attributes()
        self.async_schedule_update_ha_state()

    def clear_attributes(self, attributes):
        if attributes:
            for attribute in attributes:
                if attribute in EXCLUDED_VIRTUAL_ATTRIBUTE_NAMES:
                    continue
                self._virtual_attributes.pop(attribute, None)
        else:
            self._virtual_attributes.clear()
        self._update_attributes()
        self.async_schedule_update_ha_state()

    def set_state(self, value) -> None:
        raise NotImplementedError

    @staticmethod
    def _restored_state_value(state, config):
        """Return the configured value when HA only restored an unavailable state."""
        value = state.state
        if str(value).strip().lower() == STATE_UNAVAILABLE:
            return config.get(CONF_INITIAL_VALUE)
        return value

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
                self._icon_template,
                *self._attribute_templates.values(),
                *self._native_templates.values(),
            )
            if template
        ]
        if templates:
            @callback
            def _async_template_changed(_event, _updates):
                self._apply_templates()

            self._refresh_remove_listeners.append(
                async_track_template_result(
                    self.hass,
                    [
                        TrackTemplate(
                            (
                                template
                                if isinstance(template, Template)
                                else Template(str(template), self.hass)
                            ),
                            self._template_variables(),
                        )
                        for template in templates
                    ],
                    _async_template_changed,
                ).async_remove
            )

            # Source restoration and config-entry setup can settle around the
            # startup boundary without a tracked callback delivering the final
            # transition. Re-render after every integration has had its startup
            # opportunity, matching the state seen by a later reload.
            if self.hass.state is not CoreState.running:
                self._refresh_remove_listeners.append(
                    self.hass.bus.async_listen_once(
                        EVENT_HOMEASSISTANT_STARTED,
                        lambda _event: self._apply_templates(),
                    )
                )
            if self._availability_template:
                self._schedule_startup_availability_retry()

        if self._pull_interval:
            self._refresh_remove_listeners.append(async_track_time_interval(
                self.hass,
                lambda _now: self._apply_templates(),
                timedelta(seconds=self._pull_interval),
            ))

        self._setup_event_hooks()

    @callback
    def _schedule_startup_availability_retry(self) -> None:
        """Retry availability while source integrations finish startup."""
        delays = iter(_STARTUP_AVAILABILITY_RETRY_DELAYS)

        @callback
        def _schedule_next() -> None:
            try:
                delay = next(delays)
            except StopIteration:
                return
            self._refresh_remove_listeners.append(
                async_call_later(self.hass, delay, _async_refresh)
            )

        @callback
        def _async_refresh(_now) -> None:
            self._apply_templates()
            if not self._attr_available:
                _schedule_next()

        _schedule_next()

    @callback
    def _setup_event_hooks(self):
        """Register user-configured state and event hooks from the UI flow."""
        for index, hook in enumerate(self._event_hooks):
            trigger = str(hook.get("trigger", "state")).lower()
            if trigger == "state":
                entity_ids = self._hook_entity_ids(hook)
                entity_ids.discard(self.entity_id)
                if not entity_ids:
                    continue

                @callback
                def _async_hook_state_changed(event, hook=hook, index=index):
                    if self._state_hook_matches(hook, event):
                        self._schedule_event_hook(index, hook, event)

                self._refresh_remove_listeners.append(async_track_state_change_event(
                    self.hass,
                    entity_ids,
                    _async_hook_state_changed,
                ))
                continue

            if trigger == "event":
                event_type = str(hook.get("event_type", "")).strip()
                if not event_type:
                    continue

                @callback
                def _async_hook_event(event, hook=hook, index=index):
                    if self._event_hook_matches(hook, event):
                        self._schedule_event_hook(index, hook, event)

                self._refresh_remove_listeners.append(
                    self.hass.bus.async_listen(event_type, _async_hook_event),
                )

    def _hook_entity_ids(self, hook):
        entity_ids = hook.get(ATTR_ENTITY_ID, hook.get("entity_ids", []))
        if isinstance(entity_ids, str):
            entity_ids = [entity_ids]
        if not isinstance(entity_ids, (list, tuple, set)):
            return set()
        return {
            entity_id
            for entity_id in entity_ids
            if isinstance(entity_id, str) and entity_id
        }

    def _hook_values_match(self, configured, actual) -> bool:
        if configured is None:
            return True
        values = configured if isinstance(configured, list) else [configured]
        return str(actual) in {str(value) for value in values}

    def _hook_attributes(self, hook):
        attributes = hook.get(CONF_ATTRIBUTE, hook.get("attributes_changed", []))
        if isinstance(attributes, str):
            attributes = [attributes]
        if not isinstance(attributes, (list, tuple, set)):
            return []
        return [
            attribute
            for attribute in attributes
            if isinstance(attribute, str) and attribute
        ]

    def _state_hook_matches(self, hook, event) -> bool:
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")
        if not self._hook_values_match(hook.get("from"), old_state.state if old_state else None):
            return False
        if not self._hook_values_match(hook.get("to"), new_state.state if new_state else None):
            return False

        attributes = self._hook_attributes(hook)
        if not attributes:
            return True
        for attribute in attributes:
            old_value = old_state.attributes.get(attribute) if old_state else None
            new_value = new_state.attributes.get(attribute) if new_state else None
            if old_value != new_value:
                return True
        return False

    def _event_hook_matches(self, hook, event) -> bool:
        event_data = hook.get("event_data")
        if not isinstance(event_data, dict):
            return True
        return all(event.data.get(key) == value for key, value in event_data.items())

    @callback
    def _schedule_event_hook(self, index: int, hook, event):
        try:
            if isinstance(hook.get("debounce", 0), bool):
                raise TypeError
            delay = float(hook.get("debounce", 0) or 0)
        except (TypeError, ValueError, OverflowError):
            delay = 0
        if not math.isfinite(delay):
            delay = 0
        if delay <= 0:
            self._apply_event_hook(hook, event)
            return

        if cancel := self._hook_debounce_cancelers.pop(index, None):
            cancel()

        @callback
        def _async_apply_later(_now):
            self._hook_debounce_cancelers.pop(index, None)
            self._apply_event_hook(hook, event)

        self._hook_debounce_cancelers[index] = async_call_later(
            self.hass,
            delay,
            _async_apply_later,
        )

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
                _, _, object_id = source.partition(".")
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

    def _render_template(self, template, extra_variables=None, *, parse_result=False):
        variables = self._template_variables()
        if extra_variables:
            variables.update(extra_variables)
        if isinstance(template, Template):
            template.hass = self.hass
            return template.async_render(variables=variables, parse_result=parse_result)
        return Template(str(template), self.hass).async_render(
            variables=variables,
            parse_result=parse_result,
        )

    @classmethod
    def _valid_native_template_name(cls, name) -> bool:
        """Return whether a native template can safely target this name."""
        return (
            isinstance(name, str)
            and name.strip().isidentifier()
            and not name.strip().startswith("_")
            and name.strip() not in cls._NATIVE_TEMPLATE_RESERVED_NAMES
        )

    @staticmethod
    def _coerce_like(current, value):
        """Preserve native property types when a template returns text."""
        if value is None:
            return None
        if current is None:
            return value
        if isinstance(current, Enum):
            return type(current)(value)
        if isinstance(current, bool):
            if isinstance(value, bool):
                return value
            normalized = str(value).strip().lower()
            if normalized in {"1", "on", "true", "yes", "y", "t"}:
                return True
            if normalized in {"0", "off", "false", "no", "n", "f"}:
                return False
            raise ValueError(f"Expected a boolean, got {value!r}")
        if isinstance(current, int) and not isinstance(current, bool):
            return int(value)
        if isinstance(current, float):
            value = float(value)
            if not math.isfinite(value):
                raise ValueError("Native template returned a non-finite number")
            return value
        if isinstance(current, (list, tuple)):
            if not isinstance(value, (list, tuple)):
                raise TypeError("Native list template must return a list")
            return list(value)
        return value

    def _apply_native_template_value(self, name: str, value) -> bool:
        """Apply a rendered value to a Home Assistant native property."""
        if name == "state":
            self.set_state(value)
            return True
        if name == "available":
            value = self._template_to_bool(value)
            if self._attr_available == value:
                return False
            self._attr_available = value
            return True
        if name == "icon":
            value = str(value).strip() or self._configured_icon
            if self._attr_icon == value:
                return False
            self._attr_icon = value
            return True

        attribute_name = f"_attr_{name}"
        current = getattr(self, attribute_name, None)
        value = self._coerce_like(current, value)
        if current == value:
            return False
        setattr(self, attribute_name, value)
        return True

    def _native_templates_applied(self) -> None:
        """Allow a platform to reconcile dependent native properties."""

    def _native_template_priority(self, name: str) -> int:
        """Apply capabilities and ranges before values which depend on them."""
        if name.endswith(("_modes", "_list")) or name in {
            "available_modes",
            "effects",
            "modes",
            "options",
            "source_list",
            "sound_mode_list",
        }:
            return 0
        if name.startswith(("min_", "max_")) or name.endswith(("_step", "_count")):
            return 1
        if name in {
            "max",
            "min",
            "native_min",
            "native_max",
            "native_min_value",
            "native_max_value",
            "native_step",
            "step",
        }:
            return 1
        if name in {"state", "is_on"}:
            return 3
        return 2

    def _apply_restore_prerequisite_templates(self) -> bool:
        """Render dynamic capabilities and ranges before restoring native values."""
        applied = False
        for name, template in sorted(
            self._native_templates.items(),
            key=lambda item: self._native_template_priority(item[0]),
        ):
            if self._native_template_priority(name) > 1:
                break
            try:
                self._apply_native_template_value(
                    name,
                    self._render_template(template, parse_result=True),
                )
                applied = True
            except (
                OverflowError,
                TemplateError,
                TypeError,
                ValueError,
                vol.Invalid,
            ) as err:
                _LOGGER.warning(
                    "Unable to render restore prerequisite template %s for %s: %s",
                    name,
                    self.entity_id,
                    err,
                )
        return applied

    def _command_action_spec(self, command: str):
        spec = self._command_actions.get(command)
        if spec is None:
            return None
        if isinstance(spec, list):
            return spec, True
        if not isinstance(spec, dict):
            return None
        if "sequence" in spec:
            return spec.get("sequence", []), bool(spec.get("optimistic", True))
        return [spec], True

    async def _async_run_command_action(self, command, method, args, kwargs):
        """Run the configured HA action sequence before a native command."""
        action_spec = self._command_action_spec(command)
        action_key = (id(self), command)
        active_actions = _COMMAND_ACTION_CHAIN.get()
        if action_key in active_actions:
            return True
        command_data = self._command_service_data(command, method, args, kwargs)
        if action_spec is None:
            token = _COMMAND_ACTION_CHAIN.set(active_actions | {action_key})
            try:
                await self._async_proxy_source_command(command, command_data)
            finally:
                _COMMAND_ACTION_CHAIN.reset(token)
            return True
        sequence, optimistic = action_spec
        if not sequence:
            return optimistic

        script = self._command_scripts.get(command)
        if script is None:
            script = Script(
                self.hass,
                cv.SCRIPT_SCHEMA(sequence),
                f"{self.entity_id} {command}",
                COMPONENT_DOMAIN,
                logger=_LOGGER,
                script_mode="parallel",
                top_level=False,
            )
            self._command_scripts[command] = script

        signature = inspect.signature(method)
        bound = signature.bind_partial(self, *args, **kwargs)
        variables = {}
        for name, value in bound.arguments.items():
            if name == "self":
                continue
            if signature.parameters[name].kind is inspect.Parameter.VAR_KEYWORD:
                variables.update(value)
            else:
                variables[name] = value
        # Domain fixers may normalize native arguments (for example a Matter
        # percentage to a fan's discrete speed steps). Keep direct argument
        # variables and command_data aligned for editable action templates.
        variables.update(command_data)
        variables.update({
            "command": command,
            "command_data": command_data,
            "entity_id": self.entity_id,
            "this": self.hass.states.get(self.entity_id),
        })
        context = self._context or Context()
        run_variables = ScriptRunVariables.create_top_level(variables)
        run_variables["context"] = context
        token = _COMMAND_ACTION_CHAIN.set(active_actions | {action_key})
        try:
            await script.async_run(run_variables, context=context)
        finally:
            _COMMAND_ACTION_CHAIN.reset(token)
        return optimistic

    def _command_service_data(self, command, method, args, kwargs) -> dict:
        """Build Home Assistant service data from native command arguments."""
        signature = inspect.signature(method)
        bound = signature.bind_partial(self, *args, **kwargs)
        data = {}
        for name, value in bound.arguments.items():
            if name == "self":
                continue
            if signature.parameters[name].kind is inspect.Parameter.VAR_KEYWORD:
                data.update(value)
            else:
                data[name] = value
        data = {name: value for name, value in data.items() if value is not None}

        key = (self._platform_domain, command)
        for old_name, new_name in VIRTUAL_ENTITY_PROXY_DATA_RENAMES.get(
            key,
            {},
        ).items():
            if old_name in data:
                data[new_name] = data.pop(old_name)
        data.update(VIRTUAL_ENTITY_PROXY_FIXED_DATA.get(key, {}))
        return data

    async def _async_proxy_source_command(
        self,
        command: str,
        command_data: dict,
    ) -> None:
        """Proxy an unconfigured native command to same-domain source entities."""
        domain = self._platform_domain
        key = (domain, command)
        if (
            command not in VIRTUAL_ENTITY_COMMANDS.get(domain, ())
            or key in VIRTUAL_ENTITY_NON_SERVICE_COMMANDS
        ):
            return

        source_entities = list(dict.fromkeys(
            entity_id
            for entity_id in self._source_entities
            if isinstance(entity_id, str)
            and entity_id.startswith(f"{domain}.")
            and entity_id != self.entity_id
        ))
        service_domain = domain
        cross_domain_power_proxy = False
        if (
            not source_entities
            and command in {"turn_off", "turn_on"}
            and domain in CROSS_DOMAIN_POWER_TARGET_DOMAINS
        ):
            cross_domain_sources = list(dict.fromkeys(
                entity_id
                for entity_id in self._source_entities
                if isinstance(entity_id, str)
                and entity_id != self.entity_id
                and entity_id.split(".", 1)[0]
                in CROSS_DOMAIN_POWER_SOURCE_DOMAINS
            ))
            if len(cross_domain_sources) == 1:
                source_entities = cross_domain_sources
                service_domain = source_entities[0].split(".", 1)[0]
                cross_domain_power_proxy = True
        if not source_entities:
            return

        service = VIRTUAL_ENTITY_PROXY_SERVICE_OVERRIDES.get(key, command)
        if not self.hass.services.has_service(service_domain, service):
            _LOGGER.error(
                "Cannot proxy %s.%s for %s: Home Assistant service is unavailable",
                service_domain,
                service,
                self.entity_id,
            )
            raise HomeAssistantError(
                f"Service {service_domain}.{service} is unavailable"
            )

        service_data = {} if cross_domain_power_proxy else dict(command_data)
        service_data[ATTR_ENTITY_ID] = source_entities
        try:
            await self.hass.services.async_call(
                service_domain,
                service,
                service_data,
                blocking=True,
                context=self._context or Context(),
            )
        except Exception:
            _LOGGER.exception(
                "Unable to proxy %s.%s from %s to source entities",
                service_domain,
                service,
                self.entity_id,
            )
            raise

    def _hook_template_variables(self, hook, event):
        trigger = str(hook.get("trigger", "state")).lower()
        if trigger == "event":
            return {
                "trigger": {
                    "platform": "event",
                    "event": event,
                    "event_type": event.event_type,
                    "data": event.data,
                },
            }

        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")
        return {
            "trigger": {
                "platform": "state",
                "event": event,
                "entity_id": event.data.get(ATTR_ENTITY_ID),
                "from_state": old_state,
                "to_state": new_state,
                "from": old_state.state if old_state else None,
                "to": new_state.state if new_state else None,
            },
        }

    def _template_variables(self):
        variables = {"this": self.hass.states.get(self.entity_id)}
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
        if isinstance(value, bool):
            return value
        value = str(value).strip().lower()
        if value in {"y", "yes", "t", "true", "on", "1"}:
            return True
        if value in {"n", "no", "f", "false", "off", "0"}:
            return False
        raise ValueError(f"Expected a boolean, got {value!r}")

    @callback
    def _apply_templates(self):
        changed = False

        availability_rendered = False
        if self._availability_template:
            try:
                available = self._template_to_bool(
                    self._render_template(self._availability_template)
                )
                availability_rendered = True
                if self._attr_available != available:
                    self._attr_available = available
                    changed = True
            except (OverflowError, TemplateError, TypeError, ValueError) as e:
                _LOGGER.warning(f"Unable to render availability template for {self.entity_id}: {e}")

        # Preserve the last valid state/native properties while a source is
        # unavailable. Generic attributes may still be useful for diagnostics.
        apply_state_templates = not (
            availability_rendered and not self._attr_available
        )

        if self._icon_template:
            try:
                rendered_icon = str(
                    self._render_template(self._icon_template),
                ).strip()
                next_icon = rendered_icon or self._configured_icon
                if self._attr_icon != next_icon:
                    self._attr_icon = next_icon
                    changed = True
            except (OverflowError, TemplateError, TypeError, ValueError) as e:
                _LOGGER.warning(f"Unable to render icon template for {self.entity_id}: {e}")

        if self._value_template and apply_state_templates:
            try:
                self.set_state(self._render_template(self._value_template))
                changed = True
            except (OverflowError, TemplateError, TypeError, ValueError) as e:
                _LOGGER.warning(f"Unable to render value template for {self.entity_id}: {e}")

        for name, template in self._attribute_templates.items():
            try:
                rendered = self._render_template(
                    template,
                    parse_result=True,
                )
                if self._virtual_attributes.get(name, _MISSING) != rendered:
                    self._virtual_attributes[name] = rendered
                    changed = True
            except (OverflowError, TemplateError, TypeError, ValueError) as e:
                _LOGGER.warning(f"Unable to render attribute template {name} for {self.entity_id}: {e}")

        native_changed = False
        if apply_state_templates:
            for name, template in sorted(
                self._native_templates.items(),
                key=lambda item: self._native_template_priority(item[0]),
            ):
                try:
                    native_changed = self._apply_native_template_value(
                        name,
                        self._render_template(template, parse_result=True),
                    ) or native_changed
                except (
                    OverflowError,
                    TemplateError,
                    TypeError,
                    ValueError,
                    vol.Invalid,
                ) as e:
                    _LOGGER.warning(
                        "Unable to render native template %s for %s: %s",
                        name,
                        self.entity_id,
                        e,
                    )
        if native_changed:
            self._native_templates_applied()
            changed = True

        if self._attribute_sources:
            try:
                changed = self._apply_attribute_sources() or changed
            except (
                KeyError,
                OverflowError,
                TemplateError,
                TypeError,
                ValueError,
            ) as e:
                _LOGGER.warning(f"Unable to apply attribute sources for {self.entity_id}: {e}")

        if changed:
            self._update_attributes()
            self._schedule_state_update()

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

            if self._virtual_attributes.get(name, _MISSING) != value:
                self._virtual_attributes[name] = value
                changed = True
        return changed

    @callback
    def _apply_event_hook(self, hook, event):
        variables = self._hook_template_variables(hook, event)
        changed = False

        if hook.get(CONF_AVAILABILITY_TEMPLATE):
            try:
                available = self._template_to_bool(
                    self._render_template(hook[CONF_AVAILABILITY_TEMPLATE], variables),
                )
                if self._attr_available != available:
                    self._attr_available = available
                    changed = True
            except (OverflowError, TemplateError, TypeError, ValueError) as e:
                _LOGGER.warning(f"Unable to render event hook availability template for {self.entity_id}: {e}")

        if hook.get(CONF_VALUE_TEMPLATE):
            try:
                self.set_state(self._render_template(hook[CONF_VALUE_TEMPLATE], variables))
                changed = True
            except (OverflowError, TemplateError, TypeError, ValueError) as e:
                _LOGGER.warning(f"Unable to render event hook value template for {self.entity_id}: {e}")

        attributes = hook.get(CONF_ATTRIBUTES)
        if isinstance(attributes, dict):
            next_attributes = {
                name: value
                for name, value in attributes.items()
                if name not in EXCLUDED_VIRTUAL_ATTRIBUTE_NAMES
            }
            if next_attributes:
                for name, value in next_attributes.items():
                    if self._virtual_attributes.get(name, _MISSING) != value:
                        self._virtual_attributes[name] = value
                        changed = True

        attribute_templates = hook.get(CONF_ATTRIBUTE_TEMPLATES)
        if isinstance(attribute_templates, dict):
            for name, template in attribute_templates.items():
                if name in EXCLUDED_VIRTUAL_ATTRIBUTE_NAMES:
                    continue
                try:
                    rendered = self._render_template(
                        template,
                        variables,
                        parse_result=True,
                    )
                    if self._virtual_attributes.get(name, _MISSING) != rendered:
                        self._virtual_attributes[name] = rendered
                        changed = True
                except (OverflowError, TemplateError, TypeError, ValueError) as e:
                    _LOGGER.warning(f"Unable to render event hook attribute template {name} for {self.entity_id}: {e}")

        should_refresh = hook.get(
            "refresh",
            not any(
                hook.get(field)
                for field in (
                    CONF_AVAILABILITY_TEMPLATE,
                    CONF_VALUE_TEMPLATE,
                    CONF_ATTRIBUTES,
                    CONF_ATTRIBUTE_TEMPLATES,
                )
            ),
        )
        if should_refresh:
            self._apply_templates()
            changed = True

        if changed:
            self._update_attributes()
            self.async_schedule_update_ha_state()


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
        super().__init__(config, domain, old_style)

        self._attr_device_class = config.get(CONF_CLASS)
        self._open_close_duration = config.get(CONF_OPEN_CLOSE_DURATION)
        self._open_close_tick = config.get(CONF_OPEN_CLOSE_TICK)

        self._open_close_operation_started = None
        self._current_position = 0
        self._target_position = None
        self._positions_per_tick = None

        _LOGGER.debug(f"VirtualOpenable: {self.name} created")

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
        restored_state = self._restored_state_value(state, config)
        self._attr_is_closed = str(restored_state).lower() == STATE_CLOSED
        fallback_position = 0.0 if self._attr_is_closed else 100.0
        restored_position = state.attributes.get(
            ATTR_CURRENT_POSITION,
            fallback_position,
        )
        try:
            if isinstance(restored_position, bool):
                raise TypeError
            restored_position = float(restored_position)
        except (TypeError, ValueError, OverflowError):
            restored_position = fallback_position
        if not isfinite(restored_position):
            restored_position = fallback_position
        self._current_position = max(0.0, min(100.0, restored_position))
        self._attr_is_closed = self._current_position == 0

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
        _LOGGER.debug(f"stopping {self.name} at position {self._current_position}")

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
        _LOGGER.debug(f"setting {self.name} position {position}")

        # Validate before changing an active transition. The generic
        # virtual-layer.set_state service accepts text values, so a bad value
        # must not inadvertently stop a cover or valve that is already moving.
        if isinstance(position, bool):
            raise ValueError("position must be an integer")
        try:
            position = int(position)
        except (TypeError, ValueError, OverflowError) as err:
            raise ValueError("position must be an integer") from err
        position = max(0, min(100, position))

        self._cancel_timer()

        self._target_position = position

        if self._target_position == self._current_position:
            self._target_position = None
            self._positions_per_tick = None
            self._attr_is_opening = False
            self._attr_is_closing = False
            self._attr_is_closed = self._current_position == 0
            self.async_schedule_update_ha_state()
            return

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

        if self._open_close_tick > self._open_close_duration:
            _LOGGER.warning(f"Tick duration {self._open_close_tick} > total duration {self._open_close_duration}, capping to {self._open_close_duration}")
            self._open_close_tick = self._open_close_duration

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

    def _apply_native_template_value(self, name: str, value) -> bool:
        if name in {
            "current_position",
            "current_cover_position",
            "current_valve_position",
            "position",
        }:
            try:
                position = float(value)
            except (TypeError, ValueError, OverflowError) as err:
                raise ValueError("position must be between 0 and 100") from err
            if not isfinite(position) or not 0 <= position <= 100:
                raise ValueError("position must be between 0 and 100")
            changed = self._current_position != position
            self._cancel_timer()
            self._current_position = position
            self._target_position = None
            self._positions_per_tick = None
            self._attr_is_closed = position == 0
            self._attr_is_opening = False
            self._attr_is_closing = False
            return changed
        if name in {"is_opening", "is_closing", "is_closed"}:
            value = value if isinstance(value, bool) else self._template_to_bool(value)
        return super()._apply_native_template_value(name, value)

    def _native_templates_applied(self) -> None:
        if self._attr_is_opening and self._attr_is_closing:
            self._attr_is_closing = False
        if self._attr_is_opening or self._attr_is_closing:
            self._attr_is_closed = False
        elif self._current_position == 0:
            self._attr_is_closed = True

    @callback
    def _update_position(self, _now) -> None:
        if self._target_position is None:
            return
        if self._positions_per_tick is None:
            _LOGGER.warning(
                "Stopping %s because its movement step is unavailable",
                self.name,
            )
            self._stop()
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

    async def async_will_remove_from_hass(self) -> None:
        """Cancel movement before the entity is detached from Home Assistant."""
        self._cancel_timer()
        await super().async_will_remove_from_hass()
