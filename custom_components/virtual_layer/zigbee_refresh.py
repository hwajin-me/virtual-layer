"""Bounded, read-only refresh of referenced Zigbee2MQTT devices.

Never manufacture source states or availability. MQTT discovery supplies the
bridge topic; its device inventory supplies identity, power and GET capability.
"""

import asyncio
import json
import logging
import re
from collections import Counter
from collections.abc import Mapping
from datetime import timedelta

from homeassistant.components import mqtt
from homeassistant.components.mqtt.const import ATTR_DISCOVERY_PAYLOAD
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_time_interval

_LOGGER = logging.getLogger(__name__)
_DATA = "virtual_layer_zigbee_refresh"
_IDENTIFIER = re.compile(r"zigbee2mqtt_(0x[0-9a-fA-F]{16})\Z")
_READ_PROPERTIES = (
    "state", "brightness", "position", "current_heating_setpoint",
    "local_temperature", "temperature", "humidity", "power", "energy",
    "voltage", "current", "illuminance", "co2",
)
_MISSING = {"unknown", "unavailable"}


def _topic(value):
    if not isinstance(value, str) or not value or any(
        char in value for char in ("+", "#", "\x00")
    ):
        return False
    try:
        return len(value.encode("utf-8")) <= 65535
    except UnicodeError:
        return False


def _source_target(hass, entity_id):
    """Keep HA's discovery-cache access isolated; absent data disables requests."""
    entry = er.async_get(hass).async_get(entity_id)
    if not entry or entry.platform != "mqtt" or entry.disabled_by or not entry.device_id:
        return None
    device = dr.async_get(hass).async_get(entry.device_id)
    if not device:
        return None
    addresses = [match[1].lower() for domain, identifier in device.identifiers
                 if domain == "mqtt" and (match := _IDENTIFIER.fullmatch(identifier))]
    if len(addresses) != 1:
        return None
    data = getattr(hass.data.get(mqtt.DATA_MQTT), "debug_info_entities", {})
    info = data.get(entity_id, {}) if isinstance(data, Mapping) else {}
    discovery = info.get("discovery_data", {}) if isinstance(info, Mapping) else {}
    payload = discovery.get(ATTR_DISCOVERY_PAYLOAD, {}) if isinstance(discovery, Mapping) else {}
    if not isinstance(payload, Mapping):
        return None
    availability = payload.get("availability", payload.get("avty", []))
    if not isinstance(availability, list):
        return None
    bases = {item["topic"][:-len("/bridge/state")] for item in availability
             if isinstance(item, Mapping) and _topic(item.get("topic"))
             and item["topic"].endswith("/bridge/state")}
    if len(bases) != 1 or not _topic(base := next(iter(bases))):
        return None
    return base, addresses[0]


def _read_payload(device):
    """Only simple, explicitly readable live properties of powered devices."""
    if (device.get("disabled") or device.get("supported") is not True
            or device.get("type") == "Coordinator"
            or device.get("power_source") not in (
                "Mains (single phase)", "Mains (3 phase)", "DC Source",
            )):
        return {}
    definition = device.get("definition")
    if not isinstance(definition, Mapping):
        return {}
    readable = {}

    def visit(items, depth=0):
        if not isinstance(items, list) or depth > 5:
            return
        for item in items[:256]:
            if not isinstance(item, Mapping):
                continue
            prop, access = item.get("property"), item.get("access")
            if (isinstance(prop, str) and type(access) is int and access & 4
                    and item.get("type") in ("binary", "numeric", "enum")
                    and item.get("category") not in ("config", "diagnostic")):
                name, endpoint = item.get("name"), item.get("endpoint")
                if prop in _READ_PROPERTIES or (
                    name in _READ_PROPERTIES and isinstance(endpoint, str)
                    and prop == f"{name}_{endpoint}"
                ):
                    readable[prop] = ""
            # Domain containers can have features; composite GETs need nested
            # payloads and are intentionally not flattened into invalid keys.
            if not prop:
                visit(item.get("features"), depth + 1)

    visit(definition.get("exposes"))
    return dict(list(readable.items())[:8])


@callback
def async_watch_sources(hass, sources):
    """Share requests across entities/config entries, and release on unload."""
    manager = hass.data.get(_DATA)
    if manager is None:
        manager = hass.data[_DATA] = ZigbeeRefresh(hass)
    return manager.watch(sources)


class ZigbeeRefresh:
    """One serialized refresh queue for all Virtual Layer source references."""

    def __init__(self, hass):
        self.hass = hass
        self.sources = Counter()
        self.bridges = {}
        self.retry = {}
        self.offline = {}
        self.task = None
        self.closed = False
        self.next_publish = 0
        self.remove_connection_listener = None
        self.remove_timer = async_track_time_interval(
            hass, self._tick, timedelta(seconds=30),
        )

    @callback
    def watch(self, sources):
        sources = set(sources)
        self.sources.update(sources)
        self._tick(None)
        removed = False

        @callback
        def remove():
            nonlocal removed
            if removed:
                return
            removed = True
            self.sources.subtract(sources)
            self.sources = +self.sources
            if not self.sources:
                self.closed = True
                self.remove_timer()
                if self.remove_connection_listener:
                    self.remove_connection_listener()
                if self.task:
                    self.task.cancel()
                for bridge in self.bridges.values():
                    while bridge["unsubscribe"]:
                        bridge["unsubscribe"].pop()()
                self.bridges.clear()
                self.hass.data.pop(_DATA, None)

        return remove

    @callback
    def _tick(self, _now):
        if not self.closed and (self.task is None or self.task.done()):
            self.task = self.hass.async_create_task(self._run())

    async def _run(self):
        try:
            async with asyncio.timeout(25):
                await self._refresh()
        except TimeoutError:
            _LOGGER.debug("Zigbee2MQTT source refresh timed out")

    async def _subscribe(self, base):
        if self.remove_connection_listener is None:
            @callback
            def connection_changed(connected):
                if not connected:
                    for current in self.bridges.values():
                        current["online"] = False
                self._tick(None)

            self.remove_connection_listener = mqtt.async_subscribe_connection_status(
                self.hass, connection_changed,
            )
        bridge = {"online": False, "devices": {}, "unsubscribe": []}
        self.bridges[base] = bridge

        @callback
        def receive(message):
            try:
                data = json.loads(message.payload)
            except (ValueError, TypeError, RecursionError):
                if message.topic.endswith("/state"):
                    bridge["online"] = message.payload == "online"
                else:
                    bridge["devices"] = {}
                return
            if message.topic == f"{base}/bridge/state":
                bridge["online"] = isinstance(data, Mapping) and data.get("state") == "online"
            elif isinstance(data, list):
                bridge["devices"] = {
                    item["ieee_address"].lower(): item for item in data
                    if isinstance(item, Mapping) and isinstance(item.get("ieee_address"), str)
                }
            else:
                bridge["devices"] = {}

        try:
            for suffix in ("state", "devices"):
                unsubscribe = await mqtt.async_subscribe(
                    self.hass, f"{base}/bridge/{suffix}", receive, 0,
                )
                bridge["unsubscribe"].append(unsubscribe)
        except BaseException:
            while bridge["unsubscribe"]:
                bridge["unsubscribe"].pop()()
            self.bridges.pop(base, None)
            raise

    async def _refresh(self):
        try:
            if mqtt.DATA_MQTT not in self.hass.data or not mqtt.is_connected(self.hass):
                for bridge in self.bridges.values():
                    bridge["online"] = False
                return
            targets = {}
            for entity_id in self.sources:
                if target := _source_target(self.hass, entity_id):
                    targets.setdefault(target, []).append(entity_id)
            bases = {base for base, _address in targets}
            for base in set(self.bridges) - bases:
                for unsubscribe in self.bridges.pop(base)["unsubscribe"]:
                    unsubscribe()
            self.retry = {key: value for key, value in self.retry.items() if key in targets}
            for base in bases - self.bridges.keys():
                await self._subscribe(base)
            now = self.hass.loop.time()
            offline = {
                target: any(
                    (state := self.hass.states.get(entity_id)) is None or state.state in _MISSING
                    for entity_id in entity_ids
                ) for target, entity_ids in targets.items()
            }
            for target, missing in offline.items():
                if target not in self.retry:
                    continue
                due, failures = self.retry[target]
                if missing and self.offline.get(target) is False:
                    # A new outage need not wait for the healthy 15-minute poll.
                    self.retry[target] = (now, 0)
                elif not missing:
                    self.retry[target] = (due, 0)
            self.offline = offline
            if now < self.next_publish:
                return
            # Oldest due request wins; at most one device every 30 seconds,
            # regardless of how many virtual entities reference that device.
            for target in sorted(targets, key=lambda key: self.retry.get(key, (0, 0))[0]):
                base, address = target
                bridge = self.bridges[base]
                device = bridge["devices"].get(address, {})
                payload = _read_payload(device)
                due, failures = self.retry.get(target, (0, 0))
                if not bridge["online"] or not payload or now < due:
                    continue
                # Use IEEE identity, never a mutable friendly name or guessed
                # topic derived from an HA entity ID.
                delay = min(1800, 60 * 2 ** min(failures, 5)) if offline[target] else 900
                self.retry[target] = (now + delay, failures + 1 if offline[target] else 0)
                self.next_publish = now + 30
                await mqtt.async_publish(
                    self.hass, f"{base}/{address}/get", json.dumps(payload), qos=0, retain=False,
                )
                break
        except (HomeAssistantError, ValueError, TypeError, KeyError, AttributeError, OSError):
            # Do not expose source attributes/discovery payloads in logs.
            _LOGGER.debug("Zigbee2MQTT source refresh deferred")
