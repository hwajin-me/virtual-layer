"""Automatically generated, bounded on-demand diagnostics and one-day cleanup."""

from collections.abc import Mapping
from datetime import timedelta
from types import SimpleNamespace

from homeassistant.components.button import ButtonEntity
from homeassistant.core import Context, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.restore_state import async_get as async_get_restore_state
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_CONFIG_ENTRY_ID, ATTR_DEVICE_ID, ATTR_ENTITIES, ATTR_ENTITY_ID, ATTR_UNIQUE_ID, COMPONENT_DOMAIN,
    CONF_ATTRIBUTES, CONF_DEBUG_TARGET, CONF_NAME, CONF_SOURCE_DIAGNOSTICS,
    CONF_DIAGNOSTIC_DETAILS, CONF_DIAGNOSTIC_DETAILS_UNTIL,
    DIAGNOSTIC_UNIQUE_ID_MARKER, EXCLUDED_VIRTUAL_ATTRIBUTE_NAMES,
    STATE_ONLY_ENTITY_DOMAINS,
)
from .source_diagnostics import _KEYS, _parse_deadline, _preview, _size, attributes

DEBUG_SUFFIX = f"{DIAGNOSTIC_UNIQUE_ID_MARKER}debug_button"
DEBUG_AGE = timedelta(days=1)


def _target_config(hass, entry, unique_id):
    for group in hass.data.get(COMPONENT_DOMAIN, {}).values():
        if not isinstance(group, Mapping) or group.get(ATTR_CONFIG_ENTRY_ID) != entry.entry_id:
            continue
        for records in group.get(ATTR_ENTITIES, {}).values():
            for config in records:
                if config.get(ATTR_UNIQUE_ID) == unique_id:
                    return config
    return None


def cleanse(hass, entry, target, *, max_age=None):
    """Clean runtime and restore snapshots without creating a diagnostic report."""
    from . import _state_only_managed_attribute_names
    from .attribute_retention import prune

    target_id = target.entity_id
    domain = target_id.split(".")[0]
    config = _target_config(hass, entry, target.unique_id)
    cleansed = 0
    if config is not None:
        component = hass.data.get(domain)
        entity = component.get_entity(target_id) if hasattr(component, "get_entity") else None
        if callable(getattr(entity, "debug_cleanup", None)):
            cleansed = entity.debug_cleanup(max_age=max_age)
        elif domain in STATE_ONLY_ENTITY_DOMAINS:
            state = hass.states.get(target_id)
            if state is not None:
                current = dict(state.attributes)
                before = set(current)
                prune(config, current, _state_only_managed_attribute_names(config), max_age=max_age)
                cleansed = len(before - set(current) - EXCLUDED_VIRTUAL_ATTRIBUTE_NAMES)
                for key in _KEYS:
                    current.pop(key, None)
                current.update(attributes(hass, config, current))
                if current != state.attributes:
                    hass.states.async_set(target_id, state.state, current)
        # Disabled/unloaded entities can still have an old restore snapshot.
        # Clean it too, so enabling them later cannot resurrect expired traces.
        stored = async_get_restore_state(hass).last_states.get(target_id)
        if stored is not None:
            restored = dict(stored.state.attributes)
            before = set(restored)
            prune(config, restored, _state_only_managed_attribute_names(config), max_age=max_age)
            cleansed = max(cleansed, len(before - set(restored) - EXCLUDED_VIRTUAL_ATTRIBUTE_NAMES))
            for key in _KEYS:
                restored.pop(key, None)
            restored.update(attributes(hass, config, restored))
            previous = stored.state
            stored.state = State(
                previous.entity_id, previous.state, restored,
                last_changed=previous.last_changed, last_updated=previous.last_updated,
                context=previous.context,
            )
    elif entry.data.get("presence_fusion"):
        coordinator = entry.runtime_data.coordinator
        if max_age is not None:
            # Legacy reason strings are undated. Manual cleanup keeps only now.
            cleansed = len(coordinator.engine.history)
            coordinator.engine.history.clear()
            coordinator.engine.history.append(coordinator.engine.reason)
    else:
        raise HomeAssistantError("The virtual target is no longer configured")
    return cleansed


def diagnose(hass, entry, target):
    """Refresh HA snapshots without issuing commands to physical devices."""
    from .cfg import _diagnostic_configuration

    cleansed = cleanse(hass, entry, target, max_age=DEBUG_AGE)
    target_id = target.entity_id
    domain = target_id.split(".")[0]
    config = _target_config(hass, entry, target.unique_id)
    if config is not None:
        snapshot_config = {
            **config, CONF_DIAGNOSTIC_DETAILS: True,
            CONF_DIAGNOSTIC_DETAILS_UNTIL: (dt_util.utcnow() + DEBUG_AGE).isoformat(),
            CONF_SOURCE_DIAGNOSTICS: _diagnostic_configuration(config, domain),
        }
        report = attributes(hass, snapshot_config, {})
    elif entry.data.get("presence_fusion"):
        coordinator = entry.runtime_data.coordinator
        profile = {}
        for key, value in _preview(coordinator.diagnostics()).items():
            if isinstance(value, list):
                while value and _size({**profile, key: value}) > 12000:
                    value.pop()
            if _size({**profile, key: value}) <= 12000:
                profile[key] = value
        report = {"profile_diagnostics": profile}
    else:
        raise HomeAssistantError("The virtual target is no longer configured")
    state = hass.states.get(target_id)
    report.update({
        "virtual_entity_id": target_id,
        "target_state": state.state if state else None,
        "target_loaded": state is not None,
        "cleansed_attributes": cleansed,
    })
    return report


class DebugButton(ButtonEntity):
    """One diagnostic control per virtual target; never recursively generated."""

    _attr_should_poll = False
    _attr_has_entity_name = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:bug-check"
    _attr_translation_key = "debug"

    def __init__(self, entry, config, device_info=None):
        self._entry = entry
        self._target_uid = config[CONF_DEBUG_TARGET]
        self._attr_unique_id = config[ATTR_UNIQUE_ID]
        self.entity_id = config[ATTR_ENTITY_ID]
        self._attr_name = config[CONF_NAME]
        self._attr_translation_placeholders = {"entity_name": self._attr_name.removeprefix("Debug - ")}
        self._attr_device_info = device_info if device_info is not None else DeviceInfo(
            identifiers={(COMPONENT_DOMAIN, config[ATTR_DEVICE_ID])},
        )
        self._target_id = config[CONF_ATTRIBUTES]["virtual_entity_id"]
        self._attr_extra_state_attributes = self._base_attributes()
        self._cancel_expiry = None

    def _base_attributes(self):
        return {"virtual_entity_id": self._target_id, "virtual_entity_unique_id": self._target_uid}

    async def async_get_last_state(self):
        """An entity ID reused by a new target must not restore old reports."""
        state = await super().async_get_last_state()
        if state is not None and state.attributes.get("virtual_entity_unique_id") == self._target_uid:
            return state
        return None

    def _target(self):
        return next((row for row in er.async_entries_for_config_entry(
            er.async_get(self.hass), self._entry.entry_id,
        ) if row.unique_id == self._target_uid), None)

    @callback
    def _sync_name(self, _event=None):
        target = self._target()
        if target is None:
            return
        self._target_id = target.entity_id
        state = self.hass.states.get(target.entity_id)
        name = target.name or (
            state.name if state is not None and target.has_entity_name else target.original_name
        ) or (state.name if state else None)
        if name:
            self._attr_name = f"Debug - {name}"
            self._attr_translation_placeholders["entity_name"] = name
            own = er.async_get(self.hass).async_get(self.entity_id)
            if own is not None:
                registry = er.async_get(self.hass)
                options = own.options.get(COMPONENT_DOMAIN, {})
                options = dict(options) if isinstance(options, Mapping) else {}
                managed = own.name is None or own.name == options.get("debug_managed_name")
                updates = {}
                if own.original_name != self._attr_name:
                    updates["original_name"] = self._attr_name
                # Newer HA naming also prefixes legacy entity names with the
                # Device. Use a managed registry name to keep the requested
                # exact prefix, while preserving any independent user rename.
                if managed and own.name != self._attr_name:
                    updates["name"] = self._attr_name
                if updates:
                    registry.async_update_entity(self.entity_id, **updates)
                if managed and options.get("debug_managed_name") != self._attr_name:
                    registry.async_update_entity_options(self.entity_id, COMPONENT_DOMAIN,
                        {**options, "debug_managed_name": self._attr_name})
        self._attr_extra_state_attributes["virtual_entity_id"] = self._target_id

    @callback
    def _arm_expiry(self):
        if self._cancel_expiry:
            self._cancel_expiry()
            self._cancel_expiry = None
        deadline = _parse_deadline(self._attr_extra_state_attributes.get("debug_expires_at"))
        if deadline is not None and deadline > dt_util.utcnow():
            self._cancel_expiry = async_track_point_in_utc_time(self.hass, self._expire, deadline)

    @callback
    def _expire(self, _now=None):
        self._cancel_expiry = None
        self._attr_extra_state_attributes = self._base_attributes()
        self.async_write_ha_state()

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        stored = await self.async_get_last_state()
        if stored is not None:
            deadline = _parse_deadline(stored.attributes.get("debug_expires_at"))
            result = stored.attributes.get("debug_result")
            if (deadline is not None and dt_util.utcnow() < deadline <= dt_util.utcnow() + DEBUG_AGE
                    and isinstance(result, Mapping) and _size(result) <= 12500):
                self._attr_extra_state_attributes.update({
                    "debug_expires_at": deadline.isoformat(), "debug_result": dict(result),
                })
        self._sync_name()
        self._arm_expiry()

        @callback
        def renamed(event):
            if (event.data.get("entity_id") == self._target_id
                    or event.data.get("changes", {}).get("entity_id") == self._target_id):
                self._sync_name()
                self.async_write_ha_state()

        self.async_on_remove(self.hass.bus.async_listen("entity_registry_updated", renamed))

    async def async_will_remove_from_hass(self):
        if self._cancel_expiry:
            self._cancel_expiry()
            self._cancel_expiry = None
        await super().async_will_remove_from_hass()

    async def async_press(self):
        target = self._target()
        if target is None:
            raise HomeAssistantError("The virtual target is no longer registered")
        from . import _async_verify_target_entity_control

        await _async_verify_target_entity_control(self.hass, SimpleNamespace(
            context=self._context or Context(), data={ATTR_ENTITY_ID: [target.entity_id]},
        ))
        result = diagnose(self.hass, self._entry, target)
        self._sync_name()
        self._attr_extra_state_attributes = {
            **self._base_attributes(),
            "debug_expires_at": (dt_util.utcnow() + DEBUG_AGE).isoformat(),
            "debug_result": result,
        }
        self._arm_expiry()
        self.async_write_ha_state()


def fusion_buttons(hass, entry):
    """Profile entities also receive controls on their existing Device."""
    registry = er.async_get(hass)
    result = []
    for target in er.async_entries_for_config_entry(registry, entry.entry_id):
        if target.domain == "button" or target.unique_id.endswith(DEBUG_SUFFIX):
            continue
        uid = f"{target.unique_id}{DEBUG_SUFFIX}"
        registered = registry.async_get_or_create(
            "button", COMPONENT_DOMAIN, uid, config_entry=entry,
            suggested_object_id=f"debug_{target.entity_id.replace('.', '_')}",
            device_id=target.device_id,
        )
        device = dr.async_get(hass).async_get(target.device_id)
        result.append(DebugButton(entry, {
            ATTR_ENTITY_ID: registered.entity_id, ATTR_UNIQUE_ID: uid,
            CONF_DEBUG_TARGET: target.unique_id,
            CONF_NAME: f"Debug - {target.name or target.original_name or target.entity_id}",
            CONF_ATTRIBUTES: {"virtual_entity_id": target.entity_id},
        }, DeviceInfo(identifiers=device.identifiers) if device else DeviceInfo()))
    active = {button.unique_id for button in result}
    for row in er.async_entries_for_config_entry(registry, entry.entry_id):
        if row.unique_id.endswith(DEBUG_SUFFIX) and row.unique_id not in active:
            registry.async_remove(row.entity_id)
    return result
