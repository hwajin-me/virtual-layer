"""Cached native HA output entities, all on one Virtual Layer Device."""

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.components.image import ImageEntity
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from ..polygon import render_polygon_map_svg


class FusionEntity(CoordinatorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, key):
        super().__init__(coordinator)
        self.key = key
        self._attr_unique_id = f"{coordinator.entry.entry_id}:presence_fusion:{key}"
        self._attr_name = key.replace("_", " ").title()
        metadata = coordinator.metadata
        info = {"manufacturer": "Virtual Layer", "model": "Presence Fusion"}
        info.update(
            {
                k: v
                for k, v in metadata.items()
                if k
                in {
                    "manufacturer",
                    "model",
                    "sw_version",
                    "hw_version",
                    "serial_number",
                    "configuration_url",
                }
                and isinstance(v, str)
                and v
            }
        )
        self._attr_device_info = DeviceInfo(
            identifiers={
                (
                    "virtual_layer",
                    metadata.get("device_id") or coordinator.entry.entry_id,
                )
            },
            name=coordinator.entry.title,
            **info,
        )
        parent = dr.async_get(coordinator.hass).async_get(
            metadata.get("parent_device", "")
        )
        if parent and parent.identifiers:
            self._attr_device_info["via_device"] = min(parent.identifiers)


class FusionSensor(FusionEntity, SensorEntity):
    def __init__(self, coordinator, key):
        super().__init__(coordinator, key)
        if key == "distance_home":
            self._attr_native_unit_of_measurement = "m"
            self._attr_device_class = "distance"

    @property
    def native_value(self):
        data = self.coordinator.data
        if self.key == "primary_gps":
            return next(
                (
                    s["entity_id"]
                    for d, s in self.coordinator.sources
                    if d == data.primary and s["kind"] == "gps"
                ),
                None,
            )
        return getattr(
            data,
            {
                "gps_mode": "mode",
                "tracking_health": "health",
                "distance_home": "distance",
            }.get(self.key, self.key),
        )

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data
        if self.key == "primary_gps":
            return {"tracked_device_id": data.primary, "reason": data.reason}
        if self.key == "presence":
            return {"held": data.held, "missing_since": data.missing_since}
        if self.key == "room":
            return {"room_source": data.room_source, "conflict": data.room_conflict}
        if self.key == "zone":
            return {
                "catalog_id": data.zone_id,
                "load_error": data.zone_error,
                "home_boundary_valid": self.coordinator.engine.home_shape_valid,
            }
        return None


class FusionHome(FusionEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.PRESENCE

    @property
    def is_on(self):
        presence = self.coordinator.data.presence
        return None if presence is None else presence == "home"


class FusionTracker(FusionEntity, TrackerEntity):
    _attr_source_type = SourceType.GPS

    @property
    def available(self):
        return super().available and self.coordinator.data.gps is not None

    @property
    def latitude(self):
        return self.coordinator.data.gps[0] if self.coordinator.data.gps else None

    @property
    def longitude(self):
        return self.coordinator.data.gps[1] if self.coordinator.data.gps else None

    @property
    def location_accuracy(self):
        return self.coordinator.data.gps[2] if self.coordinator.data.gps else 0


class FusionMap(FusionEntity, ImageEntity):
    _attr_content_type = "image/svg+xml"

    def __init__(self, coordinator):
        ImageEntity.__init__(self, coordinator.hass)
        FusionEntity.__init__(self, coordinator, "map")
        self._attr_image_last_updated = dt_util.utcnow()
        self._map_signature = (coordinator.data.gps, coordinator.data.map_revision)

    def _handle_coordinator_update(self):
        signature = (self.coordinator.data.gps, self.coordinator.data.map_revision)
        if signature != self._map_signature:
            self._map_signature = signature
            self._attr_image_last_updated = dt_util.utcnow()
        super()._handle_coordinator_update()

    @property
    def available(self):
        return super().available and bool(self.coordinator.zone_geometry)

    async def async_image(self):
        if not self.coordinator.zone_geometry:
            return None
        gps = self.coordinator.data.gps
        markers = (
            [{"latitude": gps[0], "longitude": gps[1], "label": "GPS"}] if gps else []
        )
        svg = await self.hass.async_add_executor_job(
            render_polygon_map_svg, self.coordinator.zone_geometry, 720, 480, markers
        )
        return svg.encode()


def entities(entry, platform):
    coordinator = entry.runtime_data.coordinator
    if platform == "device_tracker":
        return [FusionTracker(coordinator, "gps")]
    if platform == "binary_sensor":
        return [FusionHome(coordinator, "home")]
    if platform == "image":
        return [FusionMap(coordinator)] if coordinator.zone_ids else []
    return [
        FusionSensor(coordinator, k)
        for k in (
            "presence",
            "room",
            "primary_gps",
            "gps_mode",
            "tracking_health",
            "distance_home",
            "direction",
        )
        + (("zone",) if coordinator.zone_ids else ())
    ]
