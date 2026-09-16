"""Runtime-only reverse references on the devices providing source entities."""

from collections.abc import Mapping

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import (
    ATTR_ENTITY_ID,
    CONF_ICON,
    CONF_NAME,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.event import async_track_state_change_event

from .cfg import _diagnostic_source_entities
from .const import (
    ATTR_CONFIG_ENTRY_ID,
    ATTR_UNIQUE_ID,
    COMPONENT_DOMAIN,
    DIAGNOSTIC_UNIQUE_ID_MARKER,
)

SOURCE_USAGE = "_source_usage"


@callback
def async_append_source_usage_sensors(hass, entry, entities):
    """Build one live reverse reference per source/target without saving options."""
    usages = {}
    for records in entities.values():
        for entity in records:
            if not isinstance(entity, Mapping):
                continue
            if DIAGNOSTIC_UNIQUE_ID_MARKER in entity.get(ATTR_UNIQUE_ID, ""):
                continue
            virtual_entity_id = entity.get(ATTR_ENTITY_ID)
            if not isinstance(virtual_entity_id, str):
                continue
            for source in _diagnostic_source_entities(entity):
                if source != virtual_entity_id:
                    usages.setdefault(source, {})[virtual_entity_id] = entity

    registry = er.async_get(hass)
    for source, targets in sorted(usages.items()):
        source_entry = registry.async_get(source)
        legacy_unique_id = (
            f"{entry.entry_id}{DIAGNOSTIC_UNIQUE_ID_MARKER}source_usage:{source}"
        )
        for index, (target_id, target) in enumerate(sorted(targets.items())):
            unique_id = f"{legacy_unique_id}:{target[ATTR_UNIQUE_ID]}"
            # Reuse the old count sensor for the first target, preserving its
            # entity ID and user registry customizations. Subsequent targets
            # receive separate sensors so no current state is ambiguous.
            legacy_id = registry.async_get_entity_id(
                "sensor", COMPONENT_DOMAIN, legacy_unique_id,
            )
            if index == 0 and legacy_id is not None and registry.async_get_entity_id(
                "sensor", COMPONENT_DOMAIN, unique_id,
            ) is None:
                registry.async_update_entity(legacy_id, new_unique_id=unique_id)
            target_entry = registry.async_get(target_id)
            target_name = (
                (target_entry.name if target_entry else None)
                or target.get(CONF_NAME)
                or target_id
            )
            registered = registry.async_get_or_create(
                "sensor",
                COMPONENT_DOMAIN,
                unique_id,
                config_entry=entry,
                device_id=source_entry.device_id if source_entry else None,
                suggested_object_id=(
                    f"{source.replace('.', '_')}_{target_id.replace('.', '_')}_virtual"
                ),
            )
            entities.setdefault("sensor", []).append({
                ATTR_ENTITY_ID: registered.entity_id,
                ATTR_UNIQUE_ID: unique_id,
                CONF_NAME: f"{target_name} ({target_id})",
                CONF_ICON: "mdi:link-variant",
                SOURCE_USAGE: {
                    "source_entity_id": source,
                    "virtual_entity_id": target_id,
                    "virtual_entities": [target_id],
                    ATTR_CONFIG_ENTRY_ID: entry.entry_id,
                },
            })


class SourceUsageSensor(SensorEntity):
    """Mirror a virtual target's live state on its source's existing device."""

    _attr_should_poll = False
    _attr_has_entity_name = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:link-variant"

    def __init__(self, config):
        self.entity_id = config[ATTR_ENTITY_ID]
        self._attr_unique_id = config[ATTR_UNIQUE_ID]
        self._attr_name = config[CONF_NAME]
        self._attr_extra_state_attributes = config[SOURCE_USAGE]
        self._source_entity_id = config[SOURCE_USAGE]["source_entity_id"]
        self._target_entity_id = config[SOURCE_USAGE]["virtual_entity_id"]

    @callback
    def _async_refresh_target(self):
        target = self.hass.states.get(self._target_entity_id)
        self._attr_available = target is not None and target.state != STATE_UNAVAILABLE
        self._attr_native_value = (
            target.state
            if target is not None and target.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE)
            else None
        )
        registry = er.async_get(self.hass)
        target_entry = registry.async_get(self._target_entity_id)
        target_name = (target_entry.name if target_entry else None) or (
            target.name if target else None
        )
        if target_name:
            self._attr_name = f"{target_name} ({self._target_entity_id})"
            own_entry = registry.async_get(self.entity_id)
            if own_entry is not None and own_entry.original_name != self._attr_name:
                registry.async_update_entity(
                    self.entity_id, original_name=self._attr_name,
                )

    @callback
    def _async_sync_source_device(self):
        """Attach to an existing device without adopting or modifying it."""
        registry = er.async_get(self.hass)
        source = registry.async_get(self._source_entity_id)
        own_entry = registry.async_get(self.entity_id)
        device_id = source.device_id if source else None
        if own_entry is not None and own_entry.device_id != device_id:
            registry.async_update_entity(self.entity_id, device_id=device_id)

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self._async_sync_source_device()
        self._async_refresh_target()

        @callback
        def target_state_changed(event):
            self._async_refresh_target()
            self.async_write_ha_state()

        self.async_on_remove(async_track_state_change_event(
            self.hass, [self._target_entity_id], target_state_changed,
        ))

        @callback
        def source_registry_changed(event):
            if self._source_entity_id in (
                event.data.get(ATTR_ENTITY_ID), event.data.get("old_entity_id"),
            ):
                self._async_sync_source_device()
            if event.data.get(ATTR_ENTITY_ID) == self._target_entity_id:
                target_state_changed(event)

        self.async_on_remove(self.hass.bus.async_listen(
            er.EVENT_ENTITY_REGISTRY_UPDATED, source_registry_changed,
        ))
