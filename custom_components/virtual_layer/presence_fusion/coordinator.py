"""Serial push adapter, bounded maintenance, registry identity and private Store."""

import logging
import time
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from ..geojson_catalog import async_get_catalog
from ..polygon import find_polygon_zone, polygon_clearance
from .adapters import gps_observation, local_observation, number
from .configuration import isolate_devices
from .engine import Engine
from .models import Device, Settings, Snapshot
from .movement import distance

KEY = "presence_fusion"
LOGGER = logging.getLogger(__name__)


class Clock:
    def utc(self):
        return dt_util.utcnow().timestamp()

    def monotonic(self):
        return time.monotonic()


@dataclass
class FusionRuntime:
    coordinator: "FusionCoordinator"


class FusionCoordinator(DataUpdateCoordinator[Snapshot]):
    def __init__(self, hass, entry: ConfigEntry[FusionRuntime], clock=None):
        super().__init__(
            hass, LOGGER, config_entry=entry, name=KEY, always_update=False
        )
        self.entry = entry
        self.clock = clock or Clock()
        config = deepcopy(dict(entry.options[KEY]))
        raw_metadata = config.get("metadata", {})
        self.metadata = (
            {k: v for k, v in raw_metadata.items() if isinstance(v, str)}
            if isinstance(raw_metadata, dict)
            else {}
        )
        devices, self.config_errors = isolate_devices(config.get("devices"))
        settings = Settings(**config.get("settings", {}))
        if settings.errors(0):
            raise ValueError("invalid_settings")
        self.engine = Engine(
            [
                Device(d["id"], d["name"], d["priority"], d["candidate"])
                for d in devices
            ],
            settings,
            (0, 0, 100),
        )
        if len(self.engine.devices) != len(devices):
            raise ValueError("duplicate_device")
        self.sources = [(d["id"], s) for d in devices for s in d["sources"]]
        self.source_status = {}
        self.store = Store(hass, 1, f"virtual_layer.presence_fusion.{entry.entry_id}")
        self.stopped = False
        self.unsubs = []
        self.state_unsub = None
        self.last_metadata = None
        self._published_primary_source = None
        zone_config = config.get("zones", {})
        if not isinstance(zone_config, dict):
            zone_config = {}
        raw_ids = zone_config.get("catalog_ids", [])
        self.zone_ids = (
            list(dict.fromkeys(raw_ids))
            if isinstance(raw_ids, list)
            and all(isinstance(key, str) for key in raw_ids)
            and len(raw_ids) <= 32
            else []
        )
        self.home_zone_id = (
            zone_config.get("home")
            if isinstance(zone_config.get("home"), str)
            else None
        )
        self.catalog = None
        self.zone_geometry = []
        self._home()

    def _home(self):
        state = self.hass.states.get("zone.home")
        try:
            attrs = state.attributes if state else {}
            lat, lon, radius = (
                number(attrs[k]) for k in ("latitude", "longitude", "radius")
            )
            if not (-90 <= lat <= 90 and -180 <= lon <= 180 and radius > 0):
                raise ValueError
            self.engine.home = (lat, lon, radius)
            self.engine.home_boundary = None
            self.engine.home_shape_valid = True
            if self.home_zone_id:
                zones = (
                    self.catalog.selected([self.home_zone_id])
                    if self.catalog and self.home_zone_id in self.zone_ids
                    else []
                )
                self.engine.home_shape_valid = (
                    bool(zones) and polygon_clearance(lat, lon, zones) >= 0
                )
                self.engine.home_boundary = lambda latitude, longitude: (
                    polygon_clearance(latitude, longitude, zones)
                )
                if zones:
                    extent = max(
                        distance((lat, lon), (y, x))
                        for z in zones
                        for p in z["polygons"]
                        for x, y in p["outer"]
                    )
                    self.engine.home = (lat, lon, extent)
        except (KeyError, TypeError, ValueError):
            # No invented home coordinates: invalidate geometry until zone loads.
            self.engine.home = (0, 0, self.engine.s.nearby_enter_m)
            self.engine.home_boundary = None
            self.engine.home_shape_valid = False

    async def start(self):
        try:
            if self.zone_ids or self.home_zone_id:
                self.catalog = await async_get_catalog(self.hass)
                self.zone_geometry = self.catalog.selected(self.zone_ids)
                self.unsubs.append(self.catalog.subscribe(self._catalog_changed))
                self._home()
            stored = await self.store.async_load()
            self.engine.restore(stored, self.clock.utc(), self.clock.monotonic())
            self.unsubs.append(
                self.hass.bus.async_listen(
                    er.EVENT_ENTITY_REGISTRY_UPDATED, self._registry_event
                )
            )
            self._subscribe()
            # Subscription and snapshot are synchronous on HA's event loop.
            for device, source in self.sources:
                self._read(
                    device,
                    source,
                    self.hass.states.get(source["entity_id"]),
                    None,
                    True,
                )
            self.unsubs.append(
                async_track_time_interval(self.hass, self._tick, timedelta(seconds=5))
            )
            self.publish()
        except BaseException:
            await self.stop()
            raise

    @callback
    def _catalog_changed(self):
        if not self.stopped:
            self.zone_geometry = self.catalog.selected(self.zone_ids)
            self._home()
            self.publish()

    def _snapshot(self):
        snapshot = self.engine.evaluate(self.clock.utc(), self.clock.monotonic())
        if self.catalog:
            match = (
                find_polygon_zone(*snapshot.gps, self.zone_geometry)
                if snapshot.gps
                else None
            )
            error = self.catalog.errors(self.zone_ids)
            snapshot = replace(
                snapshot,
                zone=match["name"]
                if match
                else "not_home"
                if snapshot.gps and self.zone_geometry and not error
                else None,
                zone_id=match["catalog_id"] if match else None,
                zone_error=error,
                map_revision=self.catalog.revision,
            )
        return snapshot

    def _subscribe(self):
        if self.state_unsub:
            self.state_unsub()
        registry = er.async_get(self.hass)
        for _, source in self.sources:
            if source.get("registry_id"):
                record = registry.entities.get_entry(source["registry_id"])
                if record:
                    source["entity_id"] = record.entity_id
        ids = {s["entity_id"] for _, s in self.sources} | {"zone.home"}
        self.state_unsub = async_track_state_change_event(
            self.hass, ids, self._state_event
        )

    @callback
    def _registry_event(self, event):
        if self.stopped:
            return
        if any(
            event.data.get("entity_id") == s["entity_id"]
            or event.data.get("changes", {}).get("entity_id") == s["entity_id"]
            for _, s in self.sources
        ):
            self._subscribe()
            for device, source in self.sources:
                self._read(
                    device,
                    source,
                    self.hass.states.get(source["entity_id"]),
                    None,
                    True,
                )
            self.publish()

    def _read(self, device, source, state, old, initial=False):
        now, mono = self.clock.utc(), self.clock.monotonic()
        registry = er.async_get(self.hass)
        record = (
            registry.entities.get_entry(source["registry_id"])
            if source.get("registry_id")
            else registry.async_get(source["entity_id"])
        )
        unavailable_reason = None
        if source.get("registry_id") and record is None:
            state, unavailable_reason = None, "removed"
        elif record and record.disabled_by:
            state, unavailable_reason = None, "disabled"
        elif record and record.platform in {"virtual_layer", "presence_fusion"}:
            state, unavailable_reason = None, "feedback"
        if source["entity_id"].startswith("person."):
            state, unavailable_reason = None, "feedback"
        if source["kind"] == "gps":
            if device not in self.engine.paths:
                return
            p, reason = gps_observation(source, state, old, now, self.engine.s, initial)
            path = self.engine.paths[device]
            if p:
                p = replace(p, tracked_device_id=device)
                self.engine.observe(device, p, now, mono)
                reason = path.rejection or "available"
            elif reason not in {"not_location_event", "restored_without_timestamp"}:
                path.available = False
            self.source_status[source["id"]] = unavailable_reason or reason
        else:
            value, expires, status, room = local_observation(source, state, now)
            self.engine.local(
                device,
                source["id"],
                value,
                expires,
                room if room is not None else "" if source["kind"] == "room" else None,
            )
            self.source_status[source["id"]] = unavailable_reason or status

    @callback
    def _state_event(self, event):
        if self.stopped:
            return
        if event.data["entity_id"] == "zone.home":
            self._home()
        else:
            for device, source in self.sources:
                if source["entity_id"] == event.data["entity_id"]:
                    self._read(
                        device,
                        source,
                        event.data.get("new_state"),
                        event.data.get("old_state"),
                    )
        self.publish()

    @callback
    def _tick(self, _now):
        if not self.stopped:
            self.publish()

    async def _async_update_data(self) -> Snapshot:
        """Explicit HA refresh re-evaluates cached evidence; it is not a poll."""
        if self.stopped:
            return self.data
        return self._snapshot()

    @callback
    def publish(self):
        if self.stopped:
            return
        snapshot = self._snapshot()
        primary_source = next(
            (
                s["entity_id"]
                for d, s in self.sources
                if d == snapshot.primary and s["kind"] == "gps"
            ),
            None,
        )
        # HA's push method always notifies even with always_update=False.
        # Compare explicitly before calling it (trackers force state writes).
        if (
            snapshot != self.data
            or not self.last_update_success
            or primary_source != self._published_primary_source
        ):
            self._published_primary_source = primary_source
            self.async_set_updated_data(snapshot)
        data = self.engine.metadata()
        # Presence evidence timestamps change on observation but must not cause
        # disk writes per GPS event. Flush the latest metadata on transitions.
        signature = {k: v for k, v in data.items() if k != "presence_at"}
        if signature != self.last_metadata:
            self.last_metadata = signature
            self.store.async_delay_save(self.engine.metadata, 5)

    async def stop(self):
        if self.stopped:
            return
        self.stopped = True
        await self.async_shutdown()
        if self.state_unsub:
            self.state_unsub()
            self.state_unsub = None
        for unsub in self.unsubs:
            unsub()
        self.unsubs.clear()
        # async_save cancels any delayed save, leaving no pending task/listener.
        await self.store.async_save(self.engine.metadata())

    def diagnostics(self):
        # Allowlist only. Never serialize config, observations, exception text,
        # room strings, names, entity IDs, coordinates or movement patterns.
        return {
            "profile": KEY,
            "mode": self.engine.mode,
            "health": self.engine.health,
            "reason_history": list(self.engine.history),
            "configuration_errors": self.config_errors,
            "sources": [
                {
                    "source": f"source_{index + 1}",
                    "kind": s["kind"],
                    "status": self._source_status(d, s),
                    "configured": True,
                    "gps_eligible": self.engine.paths[d].fresh(self.clock.utc())
                    if s["kind"] == "gps" and d in self.engine.paths
                    else None,
                    "gps_rejection": self.engine.paths[d].rejection
                    if s["kind"] == "gps" and d in self.engine.paths
                    else None,
                    "score": "redacted",
                    "accuracy_assumed": bool(
                        self.engine.paths.get(d)
                        and self.engine.paths[d].latest
                        and self.engine.paths[d].latest.assumed
                    ),
                }
                for index, (d, s) in enumerate(self.sources)
            ],
            "limitations": [
                "device_not_person",
                "heuristic_not_probability",
                "indirect_cycles_unsupported",
            ],
        }

    def _source_status(self, device, source):
        status = self.source_status.get(source["id"], "unknown")
        if source["kind"] == "gps":
            path = self.engine.paths.get(device)
            if (
                path
                and path.latest
                and not path.fresh(self.clock.utc())
                and status in {"available", "duplicate", "not_location_event"}
            ):
                return "stale"
        else:
            _value, expires = self.engine.locals[device].get(
                source["id"], ("unknown", None)
            )
            if expires is not None and self.clock.utc() >= expires:
                return "stale"
        return status
