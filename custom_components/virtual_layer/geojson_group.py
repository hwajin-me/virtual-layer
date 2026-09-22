"""One shared GeoJSON config entry, with one native HA Device per document."""

import asyncio

from homeassistant.components.image import ImageEntity
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import SOURCE_INTEGRATION_DISCOVERY
from homeassistant.const import UnitOfArea, UnitOfInformation
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import DeviceInfo, Entity
from homeassistant.util import dt as dt_util

from .geojson_catalog import async_get_catalog, snapshot_document
from .geojson_metrics import summarize
from .osm_tiles import async_osm_background
from .polygon import render_polygon_map_svg

DOMAIN = "virtual_layer"
GROUP_KEY = "geojson_group"
GROUP_TITLE = "GeoJson Device Group"
GROUP_UNIQUE_ID = "virtual_layer_geojson_group"
PLATFORMS = ["sensor", "image"]
SENSOR_KEYS = ("information", "zone_names", "bounds", "area", "size", "zone_count")


def group_entry(hass):
    return next(
        (
            e
            for e in hass.config_entries.async_entries(DOMAIN)
            if e.data.get(GROUP_KEY) or e.unique_id == GROUP_UNIQUE_ID
        ),
        None,
    )


async def migrate_legacy_catalog(hass):
    """Expose the existing Store without copying or changing document UUIDs."""
    catalog = await async_get_catalog(hass)
    if catalog.records and group_entry(hass) is None:
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_INTEGRATION_DISCOVERY},
            data={GROUP_KEY: True},
        )


class GeoJSONRuntime:
    def __init__(self, hass, entry, catalog):
        self.hass, self.entry, self.catalog = hass, entry, catalog
        self.platforms = {}
        self.entities = {}
        self.metrics = {}
        self.task = None
        self.unsubscribe = None
        self.lock = asyncio.Lock()
        self.stopped = False

    @callback
    def changed(self):
        if not self.stopped and (self.task is None or self.task.done()):
            self.task = self.hass.async_create_task(
                self.sync(), "GeoJSON Device registry update"
            )

    async def register(self, platform, async_add_entities):
        self.platforms[platform] = async_add_entities
        await self.sync()

    async def sync(self):
        async with self.lock:
            while not self.stopped:
                revision = self.catalog.revision
                records = dict(self.catalog.records)
                geometries = dict(self.catalog.zones)
                self.metrics = await self.hass.async_add_executor_job(
                    lambda geometries=geometries, records=records: {
                        key: summarize(
                            geometries.get(key, []),
                            snapshot_document(geometries.get(key, [])),
                        )
                        for key in records
                    }
                )
                if self.stopped:
                    return
                if revision != self.catalog.revision:
                    continue
                registry, devices = er.async_get(self.hass), dr.async_get(self.hass)
                expected = {
                    f"geojson:{key}:{field}"
                    for key in records
                    for field in (*SENSOR_KEYS, "map")
                }
                for row in er.async_entries_for_config_entry(
                    registry, self.entry.entry_id
                ):
                    if (
                        row.unique_id.startswith("geojson:")
                        and row.unique_id not in expected
                    ):
                        registry.async_remove(row.entity_id)
                for device in dr.async_entries_for_config_entry(
                    devices, self.entry.entry_id
                ):
                    owned = {
                        identifier
                        for domain, identifier in device.identifiers
                        if domain == DOMAIN and identifier.startswith("geojson:")
                    }
                    if owned and not any(f"geojson:{key}" in owned for key in records):
                        devices.async_update_device(
                            device.id, remove_config_entry_id=self.entry.entry_id
                        )
                for identity in list(self.entities):
                    if identity[0] not in records:
                        self.entities.pop(identity)
                for key, record in records.items():
                    name = record.get("name") if isinstance(record, dict) else None
                    device = devices.async_get_or_create(
                        config_entry_id=self.entry.entry_id,
                        identifiers={(DOMAIN, f"geojson:{key}")},
                        name=name if isinstance(name, str) and name else key,
                        manufacturer="Virtual Layer",
                        model="GeoJSON",
                    )
                    # HA preserves name_by_user, area and other registry choices.
                    devices.async_update_device(
                        device.id, name=name if isinstance(name, str) and name else key
                    )
                    for platform, add in self.platforms.items():
                        keys = SENSOR_KEYS if platform == "sensor" else ("map",)
                        new = []
                        for field in keys:
                            identity = (key, field)
                            entity = self.entities.get(identity)
                            if entity is None:
                                entity = (
                                    GeoJSONMap(self, key)
                                    if field == "map"
                                    else GeoJSONSensor(self, key, field)
                                )
                                self.entities[identity] = entity
                                new.append(entity)
                            elif (
                                entity.hass is not None
                                and entity.entity_id is not None
                                and entity.platform is not None
                                and entity.entity_id in entity.platform.entities
                            ):
                                if field == "map":
                                    entity._attr_image_last_updated = dt_util.utcnow()
                                entity.async_write_ha_state()
                        if new:
                            add(new)
                if revision == self.catalog.revision:
                    break

    async def stop(self):
        self.stopped = True
        if self.unsubscribe:
            self.unsubscribe()
            self.unsubscribe = None
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)


class GeoJSONEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, runtime, key, field):
        self.runtime, self.record_key, self.field = runtime, key, field
        self._attr_unique_id = f"geojson:{key}:{field}"
        self._attr_translation_key = f"geojson_{field}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, f"geojson:{key}")})

    @property
    def available(self):
        return (
            self.record_key in self.runtime.catalog.records and not self.runtime.stopped
        )

    @property
    def summary(self):
        return self.runtime.metrics.get(self.record_key, {})


class GeoJSONSensor(GeoJSONEntity, SensorEntity):
    def __init__(self, runtime, key, field):
        super().__init__(runtime, key, field)
        if field == "area":
            self._attr_device_class = SensorDeviceClass.AREA
            self._attr_native_unit_of_measurement = UnitOfArea.SQUARE_METERS
            self._attr_suggested_display_precision = 1
        elif field == "size":
            self._attr_device_class = SensorDeviceClass.DATA_SIZE
            self._attr_native_unit_of_measurement = UnitOfInformation.BYTES
        elif field == "information":
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = [
                "ok",
                "cached",
                "disabled",
                "invalid",
                "source_unavailable",
            ]

    @property
    def native_value(self):
        if self.field == "information":
            record = self.runtime.catalog.records.get(self.record_key)
            if isinstance(record, dict) and record.get("enabled") is False:
                return "disabled"
            return self.runtime.catalog.status.get(self.record_key, "invalid")
        if self.field == "zone_names":
            names = self.summary.get("zone_names", [])
            return ", ".join(names)[:255] if names else None
        if self.field == "bounds":
            bounds = self.summary.get("bounds")
            return (
                ", ".join(
                    f"{bounds[key]:.6f}" for key in ("west", "south", "east", "north")
                )
                if bounds
                else None
            )
        return self.summary.get(self.field)

    @property
    def extra_state_attributes(self):
        if self.field == "bounds":
            return self.summary.get("bounds")
        if self.field == "zone_names":
            return {"zone_names": self.summary.get("zone_names", [])}
        if self.field == "information":
            raw = self.runtime.catalog.records.get(self.record_key)
            record = raw if isinstance(raw, dict) else {}
            source = record.get("source", "")
            return {
                "catalog_id": self.record_key,
                "enabled": record.get("enabled", True),
                "priority": record.get("priority", 0),
                "source_type": "url"
                if isinstance(source, str)
                and source.startswith(("http://", "https://"))
                else "file"
                if source
                else "inline",
                "geojson_type": "FeatureCollection",
                "zone_count": self.summary.get("zone_count", 0),
                "polygon_count": self.summary.get("polygon_count", 0),
                "coordinate_count": self.summary.get("coordinate_count", 0),
                "load_error": self.runtime.catalog.errors([self.record_key]),
            }
        return None


class GeoJSONMap(GeoJSONEntity, ImageEntity):
    _attr_content_type = "image/svg+xml"

    def __init__(self, runtime, key):
        ImageEntity.__init__(self, runtime.hass)
        GeoJSONEntity.__init__(self, runtime, key, "map")
        self._attr_image_last_updated = dt_util.utcnow()

    @property
    def available(self):
        return super().available and bool(
            self.runtime.catalog.zones.get(self.record_key)
        )

    async def async_image(self):
        zones = self.runtime.catalog.zones.get(self.record_key)
        if not zones:
            return None
        # The base ImageEntity view allows ten seconds to produce the image.
        # OSM is a visual enhancement only: a slow or malformed tile must not
        # turn an otherwise valid GeoJSON SVG into a 500 response.
        try:
            async with asyncio.timeout(7):
                background = await async_osm_background(self.hass, zones)
        except Exception:  # OSM is optional; always preserve the SVG map.
            background = None
        svg = await self.hass.async_add_executor_job(
            render_polygon_map_svg, zones, 720, 480, None, background
        )
        return svg.encode()


async def setup(hass, entry):
    owner = group_entry(hass)
    if owner and owner.entry_id != entry.entry_id:
        return False
    if entry.title != GROUP_TITLE or entry.unique_id != GROUP_UNIQUE_ID:
        hass.config_entries.async_update_entry(
            entry, title=GROUP_TITLE, unique_id=GROUP_UNIQUE_ID
        )
    runtime = GeoJSONRuntime(hass, entry, await async_get_catalog(hass))
    entry.runtime_data = runtime
    runtime.unsubscribe = runtime.catalog.subscribe(runtime.changed)
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        await runtime.stop()
        await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
        raise
    entry.async_on_unload(entry.add_update_listener(reload))
    return True


async def reload(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)


async def unload(hass, entry):
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.stop()
        return True
    return False


async def remove(hass, entry):
    owner = group_entry(hass)
    if owner and owner.entry_id != entry.entry_id:
        return
    catalog = await async_get_catalog(hass)
    async with catalog.lock:
        await catalog.store.async_remove()
        catalog.records.clear()
        catalog.zones.clear()
        catalog.status.clear()
        catalog._notify()
