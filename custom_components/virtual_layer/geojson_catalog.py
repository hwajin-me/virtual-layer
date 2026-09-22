"""UI-managed shared GeoJSON definitions; never stores tracker observations."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from datetime import timedelta
from urllib.parse import urlsplit

from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store

from .polygon import load_polygon_zones, parse_geojson_zones

CATALOG_KEY = "virtual_layer_geojson_catalog"
CATALOG_IDS = "catalog_ids"
MAX_RECORDS = 32
MAX_POINTS = 20000
MAX_BYTES = 2 * 1024 * 1024


def document_zones(document):
    """Validate bounded configuration before accepting or loading geometry."""
    if isinstance(document, str):
        if len(document.encode()) > MAX_BYTES:
            raise ValueError("geojson_limit")
        document = json.loads(document)
    if len(json.dumps(document, allow_nan=False).encode()) > MAX_BYTES:
        raise ValueError("geojson_limit")
    zones = parse_geojson_zones(document)
    if (
        sum(
            len(ring)
            for z in zones
            for p in z["polygons"]
            for ring in [p["outer"], *p["holes"]]
        )
        > MAX_POINTS
    ):
        raise ValueError("geojson_limit")
    for zone in zones:
        raw = zone["properties"].get("priority", 0)
        if type(raw) is not int or not -100000 <= raw <= 100000:
            raise ValueError("geojson_invalid")
    return zones


def snapshot_document(zones):
    """Only configured boundaries, names and priority; no remote extra properties."""
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"name": z["name"], "priority": z["priority"]},
                "geometry": {
                    "type": "MultiPolygon",
                    "coordinates": [[p["outer"], *p["holes"]] for p in z["polygons"]],
                },
            }
            for z in zones
        ],
    }


def validate_catalog(records, geometries):
    points = sum(
        len(ring)
        for zones in geometries.values()
        for z in zones
        for p in z["polygons"]
        for ring in [p["outer"], *p["holes"]]
    )
    if (
        len(records) > MAX_RECORDS
        or points > MAX_POINTS
        or len(json.dumps(records, allow_nan=False).encode()) > MAX_BYTES * 2
    ):
        raise ValueError("geojson_limit")


class GeoJSONCatalog:
    def __init__(self, hass):
        self.hass = hass
        self.store = Store(hass, 1, "virtual_layer.geojson_catalog")
        self.records = {}
        self.zones = {}
        self.status = {}
        self.revision = 0
        self.listeners = set()
        self.lock = asyncio.Lock()
        self.timer = None
        self.task = None

    async def load(self):
        data = await self.store.async_load()
        if isinstance(data, dict) and isinstance(data.get("records"), dict):
            self.records = {
                k: v for k, v in data["records"].items() if isinstance(k, str)
            }
        for key, record in self.records.items():
            try:
                if type(record.get("priority", 0)) is not int or not isinstance(
                    record.get("enabled", True), bool
                ):
                    raise ValueError("geojson_invalid")
                self.zones[key] = await self.hass.async_add_executor_job(
                    document_zones, record.get("snapshot") or record.get("geojson")
                )
                self.status[key] = "cached" if record.get("source") else "ok"
            except (ValueError, TypeError, AttributeError, RecursionError):
                self.status[key] = "invalid"

    def choices(self, selected=()):
        choices = [
            {
                "value": key,
                "label": str(record.get("name") or key)
                if isinstance(record, dict)
                else key,
            }
            for key, record in self.records.items()
        ]
        choices.extend(
            {"value": key, "label": key} for key in selected if key not in self.records
        )
        return choices

    def selected(self, ids):
        result = []
        for key in ids:
            record = self.records.get(key)
            if not isinstance(record, dict) or not record.get("enabled", True):
                continue
            for zone in self.zones.get(key, []):
                result.append(
                    {
                        **zone,
                        "catalog_id": key,
                        "catalog_priority": record.get("priority", 0),
                    }
                )
        return result

    def errors(self, ids):
        return any(
            key not in self.records
            or self.status.get(key) in {"invalid", "source_unavailable"}
            for key in ids
        )

    def subscribe(self, listener):
        self.listeners.add(listener)
        if self.timer is None:
            self.timer = async_track_time_interval(
                self.hass, self._schedule_refresh, timedelta(minutes=5)
            )
            self._schedule_refresh(None)

        def remove():
            self.listeners.discard(listener)
            if not self.listeners:
                if self.timer:
                    self.timer()
                    self.timer = None
                if self.task:
                    self.task.cancel()

        return remove

    def _schedule_refresh(self, _now):
        if self.task is None or self.task.done():
            self.task = self.hass.async_create_background_task(
                self.refresh(), "Virtual Layer GeoJSON refresh"
            )

    def _notify(self):
        self.revision += 1
        for listener in tuple(self.listeners):
            listener()

    async def prepare(self, record):
        record = deepcopy(record)
        if not isinstance(record.get("name"), str) or not record["name"].strip():
            raise ValueError("geojson_name")
        if (
            type(record.get("priority")) is not int
            or not -100000 <= record["priority"] <= 100000
        ):
            raise ValueError("geojson_priority")
        if not isinstance(record.get("enabled"), bool):
            raise TypeError("geojson_invalid")
        if not isinstance(record.get("source", ""), str):
            raise TypeError("geojson_source")
        source = record.get("source", "").strip()
        if source:
            parts = urlsplit(source)
            if (
                parts.scheme
                and parts.scheme not in {"http", "https"}
                or parts.username
                or parts.password
            ):
                raise ValueError("geojson_source")
            zones, errors = await load_polygon_zones(
                self.hass, files=[source], return_errors=True
            )
            if errors:
                raise ValueError("geojson_source")
            document = snapshot_document(zones)
        else:
            document = record.get("geojson")
        zones = await self.hass.async_add_executor_job(document_zones, document)
        record.update(
            name=record["name"].strip(),
            source=source,
            snapshot=snapshot_document(zones),
        )
        zones = await self.hass.async_add_executor_job(
            document_zones, record["snapshot"]
        )
        if not source:
            record["geojson"] = record["snapshot"]
        else:
            record.pop("geojson", None)
        return record, zones

    async def save(self, key, record, revision):
        prepared, zones = (
            await self.prepare(record) if record is not None else (None, None)
        )
        async with self.lock:
            if revision != self.revision:
                raise ValueError("geojson_conflict")
            records = deepcopy(self.records)
            if prepared is None:
                records.pop(key, None)
            else:
                records[key] = prepared
            geometries = dict(self.zones)
            if prepared is None:
                geometries.pop(key, None)
            else:
                geometries[key] = zones
            await self.hass.async_add_executor_job(
                validate_catalog, records, geometries
            )
            await self.store.async_save({"records": records})
            self.records = records
            if prepared is None:
                self.zones.pop(key, None)
                self.status.pop(key, None)
            else:
                self.zones[key] = zones
                self.status[key] = "ok"
            self._notify()

    async def refresh(self):
        async with self.lock:
            changed = False
            records = dict(self.records)
            geometries = dict(self.zones)
            for key, record in self.records.items():
                if (
                    not isinstance(record, dict)
                    or not record.get("source")
                    or not record.get("enabled", True)
                ):
                    continue
                try:
                    prepared, zones = await self.prepare(record)
                    candidate_records = {**records, key: prepared}
                    candidate_zones = {**geometries, key: zones}
                    await self.hass.async_add_executor_job(
                        validate_catalog, candidate_records, candidate_zones
                    )
                    changed |= prepared != record or self.status.get(key) != "ok"
                    records = candidate_records
                    geometries = candidate_zones
                    self.status[key] = "ok"
                except (ValueError, TypeError, OSError, RecursionError):
                    changed |= self.status.get(key) != "source_unavailable"
                    self.status[key] = "source_unavailable"
            if changed:
                try:
                    await self.store.async_save({"records": records})
                except OSError:
                    for key, record in records.items():
                        if isinstance(record, dict) and record.get("source"):
                            self.status[key] = "source_unavailable"
                else:
                    self.records = records
                    self.zones = geometries
                self._notify()


async def async_get_catalog(hass):
    """One shared load per HA instance, including concurrent config flows."""
    if CATALOG_KEY not in hass.data:
        catalog = GeoJSONCatalog(hass)
        hass.data[CATALOG_KEY] = (catalog, hass.async_create_task(catalog.load()))
    catalog, ready = hass.data[CATALOG_KEY]
    await asyncio.shield(ready)
    return catalog


def catalog_choices(hass, selected=()):
    if hass and CATALOG_KEY in hass.data:
        return hass.data[CATALOG_KEY][0].choices(selected)
    return [{"value": key, "label": key} for key in selected]
