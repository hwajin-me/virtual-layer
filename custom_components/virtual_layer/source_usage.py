"""Runtime-only reverse references on the devices providing source entities."""

from collections.abc import Mapping

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import ATTR_ENTITY_ID, CONF_ICON
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory

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
    """Group explicit source references without persisting companion records."""
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
                    usages.setdefault(source, set()).add(virtual_entity_id)

    registry = er.async_get(hass)
    for source, virtual_entities in sorted(usages.items()):
        source_entry = registry.async_get(source)
        unique_id = (
            f"{entry.entry_id}{DIAGNOSTIC_UNIQUE_ID_MARKER}source_usage:{source}"
        )
        registered = registry.async_get_or_create(
            "sensor",
            COMPONENT_DOMAIN,
            unique_id,
            config_entry=entry,
            device_id=source_entry.device_id if source_entry else None,
            suggested_object_id=f"{source.replace('.', '_')}_virtual_layer_usage",
        )
        entities.setdefault("sensor", []).append({
            ATTR_ENTITY_ID: registered.entity_id,
            ATTR_UNIQUE_ID: unique_id,
            CONF_ICON: "mdi:link-variant",
            SOURCE_USAGE: {
                "source_entity_id": source,
                "virtual_entities": sorted(virtual_entities),
                ATTR_CONFIG_ENTRY_ID: entry.entry_id,
            },
        })


class SourceUsageSensor(SensorEntity):
    """Show configured usage even when the source is offline or unregistered."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:link-variant"
    _attr_translation_key = "source_usage"

    def __init__(self, config):
        self.entity_id = config[ATTR_ENTITY_ID]
        self._attr_unique_id = config[ATTR_UNIQUE_ID]
        self._attr_extra_state_attributes = config[SOURCE_USAGE]
        self._source_entity_id = config[SOURCE_USAGE]["source_entity_id"]
        self._attr_translation_placeholders = {"source": self._source_entity_id}
        self._attr_native_value = len(config[SOURCE_USAGE]["virtual_entities"])

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

        @callback
        def source_registry_changed(event):
            if self._source_entity_id in (
                event.data.get(ATTR_ENTITY_ID), event.data.get("old_entity_id"),
            ):
                self._async_sync_source_device()

        self.async_on_remove(self.hass.bus.async_listen(
            er.EVENT_ENTITY_REGISTRY_UPDATED, source_registry_changed,
        ))
