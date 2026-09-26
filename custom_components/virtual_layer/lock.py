"""
This component provides support for a virtual lock.

"""

import logging
import random
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components.lock import (
    DOMAIN as PLATFORM_DOMAIN,
)
from homeassistant.components.lock import (
    LockEntity,
    LockEntityFeature,
    LockState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.config_validation import PLATFORM_SCHEMA
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import get_entity_configs
from .const import *
from .entity import VirtualEntity, nonnegative_int, virtual_schema

_LOGGER = logging.getLogger(__name__)

DEPENDENCIES = [COMPONENT_DOMAIN]

CONF_CHANGE_TIME = "locking_time"
CONF_SUPPORT_OPEN = "support_open"
CONF_TEST_JAMMING = "jamming_test"

DEFAULT_LOCK_VALUE = "locked"
DEFAULT_CHANGE_TIME = timedelta(seconds=0)
DEFAULT_SUPPORT_OPEN = False
DEFAULT_TEST_JAMMING = 0

_STATE_FLAG_PRIORITY = (
    "is_locked", "is_unlocking", "is_locking", "is_opening", "is_open", "is_jammed",
)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(virtual_schema(DEFAULT_LOCK_VALUE, {
    vol.Optional(CONF_CHANGE_TIME, default=DEFAULT_CHANGE_TIME): vol.All(cv.time_period, cv.positive_timedelta),
    vol.Optional(CONF_SUPPORT_OPEN, default=DEFAULT_SUPPORT_OPEN): cv.boolean,
    vol.Optional(CONF_TEST_JAMMING, default=DEFAULT_TEST_JAMMING): nonnegative_int,
}))
LOCK_SCHEMA = vol.Schema(virtual_schema(DEFAULT_LOCK_VALUE, {
    vol.Optional(CONF_CHANGE_TIME, default=DEFAULT_CHANGE_TIME): vol.All(cv.time_period, cv.positive_timedelta),
    vol.Optional(CONF_SUPPORT_OPEN, default=DEFAULT_SUPPORT_OPEN): cv.boolean,
    vol.Optional(CONF_TEST_JAMMING, default=DEFAULT_TEST_JAMMING): nonnegative_int,
}))


async def async_setup_platform(
        hass: HomeAssistant,
        config: ConfigType,
        async_add_entities: AddEntitiesCallback,
        _discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Ignore platform setup; Virtual Layer entities are config-entry only."""
    _LOGGER.debug("ignoring platform setup")


async def async_setup_entry(
        hass: HomeAssistant,
        entry: ConfigEntry,
        async_add_entities: Callable[[list], None],
) -> None:
    _LOGGER.debug("setting up the entries...")

    entities = []
    for entity in get_entity_configs(hass, entry.data[ATTR_GROUP_NAME], PLATFORM_DOMAIN):
        entity = LOCK_SCHEMA(entity)
        entities.append(VirtualLock(hass, entity, False))
    async_add_entities(entities)


class VirtualLock(VirtualEntity, LockEntity):
    """Representation of a Virtual lock."""

    def __init__(self, hass, config, old_style: bool):
        """Initialize the Virtual lock device."""
        super().__init__(config, PLATFORM_DOMAIN, old_style)

        self._hass = hass
        self._change_time = config.get(CONF_CHANGE_TIME)
        self._support_open = config.get(CONF_SUPPORT_OPEN)
        self._test_jamming = config.get(CONF_TEST_JAMMING)
        self._timer_handle = None
        self._attr_supported_features = LockEntityFeature(0)
        if self._support_open:
            self._attr_supported_features |= LockEntityFeature.OPEN
        
        _LOGGER.debug(f'VirtualLock: {self.name} created')

    def _create_state(self, config):
        super()._create_state(config)
        initial_state = str(config.get(CONF_INITIAL_VALUE, DEFAULT_LOCK_VALUE)).lower()
        if not self._set_lock_state_flags(initial_state):
            self._set_lock_state_flags(DEFAULT_LOCK_VALUE)

    def _restore_state(self, state, config):
        super()._restore_state(state, config)
        if self._set_lock_state_flags(state.state):
            return
        initial_state = str(config.get(CONF_INITIAL_VALUE, DEFAULT_LOCK_VALUE)).lower()
        if not self._set_lock_state_flags(initial_state):
            self._set_lock_state_flags(DEFAULT_LOCK_VALUE)

    def _set_lock_state_flags(self, state: str) -> bool:
        """Apply a native lock state without triggering lock side effects."""
        state = str(state).lower()
        state_flags = {
            LockState.JAMMED: "is_jammed",
            LockState.OPEN: "is_open",
            LockState.OPENING: "is_opening",
            LockState.LOCKING: "is_locking",
            LockState.UNLOCKING: "is_unlocking",
            LockState.LOCKED: "is_locked",
            LockState.UNLOCKED: None,
        }
        if state not in state_flags:
            return False
        active_flag = state_flags[state]
        for flag in _STATE_FLAG_PRIORITY:
            setattr(self, f"_attr_{flag}", flag == active_flag)
        return True

    def _lock(self) -> None:
        if self._test_jamming == 0 or random.randint(0, self._test_jamming) > 0:
            _LOGGER.debug(f"locked {self.name}")
            self._attr_is_open = False
            self._attr_is_locked = True
            self._attr_is_locking = False
            self._attr_is_unlocking = False
            self._attr_is_opening = False
            self._attr_is_jammed = False
        else:
            self._jam()

    def _locking(self) -> None:
        _LOGGER.debug(f"locking {self.name}")
        self._attr_is_open = False
        self._attr_is_locked = False
        self._attr_is_locking = True
        self._attr_is_unlocking = False
        self._attr_is_opening = False
        self._attr_is_jammed = False

    def _unlock(self) -> None:
        _LOGGER.debug(f"unlocked {self.name}")
        self._attr_is_open = False
        self._attr_is_locked = False
        self._attr_is_locking = False
        self._attr_is_unlocking = False
        self._attr_is_opening = False
        self._attr_is_jammed = False

    def _open(self) -> None:
        _LOGGER.debug(f"opened {self.name}")
        self._attr_is_open = True
        self._attr_is_locked = False
        self._attr_is_locking = False
        self._attr_is_unlocking = False
        self._attr_is_opening = False
        self._attr_is_jammed = False

    def _unlocking(self) -> None:
        _LOGGER.debug(f"unlocking {self.name}")
        self._attr_is_open = False
        self._attr_is_locked = False
        self._attr_is_locking = False
        self._attr_is_unlocking = True
        self._attr_is_opening = False
        self._attr_is_jammed = False

    def _jam(self) -> None:
        _LOGGER.debug(f"jamming {self.name}")
        self._attr_is_open = False
        self._attr_is_locked = False
        self._attr_is_locking = False
        self._attr_is_unlocking = False
        self._attr_is_opening = False
        self._attr_is_jammed = True

    @callback
    async def _finish_operation(self, _point_in_time) -> None:
        self._timer_handle = None
        if self.is_locking:
            self._lock()
        if self.is_unlocking:
            self._unlock()
        self.async_schedule_update_ha_state()

    def _start_operation(self):
        self._cancel_timer()
        self._timer_handle = async_call_later(
            self.hass,
            self._change_time,
            self._finish_operation,
        )

    def _cancel_timer(self) -> None:
        if self._timer_handle is not None:
            self._timer_handle()
            self._timer_handle = None

    async def async_will_remove_from_hass(self) -> None:
        """Cancel an in-flight lock operation before unload."""
        self._cancel_timer()
        await super().async_will_remove_from_hass()

    async def async_lock(self, **kwargs: Any) -> None:
        if self._change_time == DEFAULT_CHANGE_TIME:
            self._lock()
        else:
            self._locking()
            self._start_operation()
        self.async_write_ha_state()

    async def async_unlock(self, **kwargs: Any) -> None:
        if self._change_time == DEFAULT_CHANGE_TIME:
            self._unlock()
        else:
            self._unlocking()
            self._start_operation()
        self.async_write_ha_state()

    async def async_open(self, **kwargs: Any) -> None:
        _LOGGER.debug(f"opening {self.name}")
        self._cancel_timer()
        if self._support_open:
            self._open()
        else:
            self._unlock()
        self.async_write_ha_state()

    def set_state(self, value) -> None:
        value = str(value).lower()
        value = {
            "lock": "locked", "on": "locked", "true": "locked", "1": "locked",
            "opened": "open", "unlock": "unlocked", "off": "unlocked",
            "false": "unlocked", "0": "unlocked",
        }.get(value, value)
        if not self._set_lock_state_flags(value):
            raise ValueError(f"Invalid lock state: {value}")
        # A reported state is authoritative; it must not run the simulated
        # jamming logic or inherit a previous command's completion timer.
        self._cancel_timer()

    def _apply_native_template_value(self, name: str, value) -> bool:
        if name == CONF_SUPPORT_OPEN:
            value = value if isinstance(value, bool) else self._template_to_bool(value)
            changed = self._support_open != value
            self._support_open = value
            return changed
        if name in _STATE_FLAG_PRIORITY:
            if not isinstance(value, bool):
                value = self._template_to_bool(value)
            if value or name == "is_locked":
                previous = tuple(
                    getattr(self, f"_attr_{flag}", False) for flag in _STATE_FLAG_PRIORITY
                )
                self._cancel_timer()
                self._set_lock_state_flags(name.removeprefix("is_") if value else "unlocked")
                return previous != tuple(
                    getattr(self, f"_attr_{flag}", False) for flag in _STATE_FLAG_PRIORITY
                )
        return super()._apply_native_template_value(name, value)

    def _native_template_priority(self, name: str) -> int:
        # Explicit source flags replace older local states. Apply the existing
        # jam/open/moving/locked precedence independently of JSON field order.
        if name in _STATE_FLAG_PRIORITY:
            return 10 + _STATE_FLAG_PRIORITY.index(name)
        return super()._native_template_priority(name)

    def _native_templates_applied(self) -> None:
        active = next(
            (
                name
                for name in reversed(_STATE_FLAG_PRIORITY)
                if getattr(self, f"_attr_{name}", False)
            ),
            None,
        )
        for name in _STATE_FLAG_PRIORITY:
            setattr(self, f"_attr_{name}", name == active)
        self._attr_supported_features = LockEntityFeature(0)
        if self._support_open or "open" in self._command_actions:
            self._attr_supported_features |= LockEntityFeature.OPEN
