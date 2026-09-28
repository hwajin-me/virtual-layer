"""Bounded, read-only refresh of referenced Zigbee2MQTT devices.

Never manufacture source states or availability. MQTT discovery supplies the
bridge topic; its device inventory supplies identity, power and GET capability.
"""

import asyncio
import json
import logging
import math
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
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)
_DATA = "virtual_layer_zigbee_refresh"
_IDENTIFIER = re.compile(r"zigbee2mqtt_(0x[0-9a-fA-F]{16})\Z")
_READ_PROPERTIES = (
    "state", "brightness", "position", "current_heating_setpoint",
    "local_temperature", "temperature", "humidity", "power", "energy",
    "voltage", "current", "illuminance", "co2",
)
_MISSING = {"unknown", "unavailable"}
DIAGNOSTICS_UPDATED = "virtual_layer_zigbee_diagnostics_updated"


def _availability(payload):
    """Accept both legacy plain and current JSON availability messages."""
    try:
        value = json.loads(payload)
    except (ValueError, TypeError, RecursionError):
        value = payload
    if isinstance(value, Mapping):
        value = value.get("state")
    return value if value in ("online", "offline") else "unknown"


def _measurement(value, maximum):
    if type(value) in (int, float) and 0 <= value <= maximum and math.isfinite(value):
        return value
    return None


def _last_seen(value):
    try:
        if type(value) in (int, float):
            # Zigbee2MQTT's epoch format is milliseconds.
            parsed = dt_util.utc_from_timestamp(value / 1000)
        elif isinstance(value, str) and len(value) <= 64:
            parsed = dt_util.parse_datetime(value)
        else:
            return None
        if parsed is not None and parsed.tzinfo is not None:
            return dt_util.as_utc(parsed).isoformat()
    except (ValueError, TypeError, OverflowError, OSError):
        pass
    return None


def _address(value):
    return value.lower() if isinstance(value, str) and re.fullmatch(r"0x[0-9a-fA-F]{16}", value) else None


def _text(value):
    return value[:256] if isinstance(value, str) else None


def _networkmap(data):
    """Parse only identity/link fields; source is the neighbor, target its reporter."""
    if not isinstance(data, Mapping) or not isinstance(data.get("nodes"), list) or not isinstance(data.get("links"), list):
        return None
    if len(data["nodes"]) > 4096 or len(data["links"]) > 32768:
        return None
    nodes = {}
    for node in data["nodes"]:
        if isinstance(node, Mapping) and (address := _address(node.get("ieeeAddr"))):
            nodes[address] = {"ieee_address": address, "name": _text(node.get("friendlyName")),
                              "type": _text(node.get("type"))}
    links = []
    for link in data["links"]:
        if not isinstance(link, Mapping):
            continue
        source, target = link.get("source"), link.get("target")
        source = _address(source.get("ieeeAddr")) if isinstance(source, Mapping) else None
        target = _address(target.get("ieeeAddr")) if isinstance(target, Mapping) else None
        relation = link.get("relationship")
        if source in nodes and target in nodes and source != target and type(relation) is int and 0 <= relation <= 3:
            links.append((source, target, relation, _measurement(link.get("lqi", link.get("linkquality")), 255)))
    return nodes, links


def _routing_attributes(bridge, address):
    nodes, links = bridge.get("networkmap", ({}, []))
    parents, neighbors = {}, []
    for source, target, relation, quality in links:
        if address not in (source, target):
            continue
        peer = target if source == address else source
        neighbor = {**nodes[peer], "linkquality": quality, "reporter": target,
                    "neighbor_relationship": ("parent", "child", "sibling", "none")[relation]}
        if len(neighbors) < 16:
            neighbors.append(neighbor)
        # LQI tables describe the neighbor's relationship to their reporter.
        if (source == address and relation == 1) or (target == address and relation == 0):
            if nodes[peer]["type"] in ("Router", "Coordinator"):
                parents[peer] = nodes[peer]
    return {
        "zigbee_parent": next(iter(parents.values())) if len(parents) == 1 else None,
        "zigbee_parent_candidates": list(parents.values())[:16],
        "zigbee_neighbors": neighbors,
        "zigbee_networkmap_updated": bridge.get("networkmap_updated"),
        "zigbee_networkmap_status": bridge.get("networkmap_status", "unknown"),
    }


async def async_request_networkmap(hass, source_entities):
    """Explicit user request only: topology scans can disrupt Zigbee traffic."""
    manager = hass.data.get(_DATA)
    bases = {target[0] for source in source_entities if (target := _source_target(hass, source))}
    if manager is None or not bases or not mqtt.is_connected(hass):
        raise HomeAssistantError("No connected Zigbee2MQTT source is available")
    now = hass.loop.time()
    for base in bases:
        bridge = manager.bridges.get(base, {})
        if not bridge.get("online"):
            raise HomeAssistantError("The Zigbee2MQTT bridge is not online")
        if now < bridge.get("next_networkmap", 0):
            raise HomeAssistantError("Wait two minutes before requesting another network map")
    for base in sorted(bases):
        bridge = manager.bridges[base]
        bridge["next_networkmap"] = now + 120
        bridge["networkmap_status"] = "pending"
        try:
            await mqtt.async_publish(hass, f"{base}/bridge/request/networkmap",
                                     '{"type":"raw","routes":false}', qos=0, retain=False)
        except HomeAssistantError:
            bridge["networkmap_status"] = "error"
            raise
        finally:
            async_dispatcher_send(hass, DIAGNOSTICS_UPDATED)


@callback
def diagnostic_attributes(hass, entity_id):
    """Return only allowlisted data for a positively identified source device."""
    target = _source_target(hass, entity_id)
    if target is None:
        return {}
    base, address = target
    manager = hass.data.get(_DATA)
    bridge = manager.bridges.get(base, {}) if manager else {}
    device = bridge.get("devices", {}).get(address, {})
    sample = manager.diagnostics.get(target, {}) if manager else {}
    # A renamed/removed inventory entry must never expose its old topic's data.
    if sample.get("name") != device.get("friendly_name"):
        sample = {}
    connected = mqtt.is_connected(hass) if mqtt.DATA_MQTT in hass.data else False
    seen = sample.get("last_seen")
    definition = device.get("definition")
    definition = definition if isinstance(definition, Mapping) else {}

    def text(value):
        return value[:256] if isinstance(value, str) else None

    return {
        "source_protocol": "zigbee",
        "source_integration": "zigbee2mqtt",
        "zigbee_ieee_address": address,
        "zigbee_friendly_name": text(device.get("friendly_name")),
        "zigbee_device_type": text(device.get("type")),
        "zigbee_power_source": text(device.get("power_source")),
        "zigbee_model": text(definition.get("model")),
        "zigbee_vendor": text(definition.get("vendor")),
        "zigbee_linkquality": sample.get("linkquality"),
        "zigbee_battery": sample.get("battery"),
        "zigbee_last_seen": seen,
        "zigbee_last_seen_seconds_ago": max(0, int(
            (dt_util.utcnow() - dt_util.parse_datetime(seen)).total_seconds()
        )) if seen else None,
        "zigbee_device_availability": sample.get("availability", "unknown") if connected else "unknown",
        "zigbee_bridge_availability": bridge.get("availability", "unknown") if connected else "unknown",
        "zigbee_mqtt_connected": connected,
        "zigbee_rssi": sample.get("rssi"),
        "zigbee_hub": bridge.get("hub"),
        "zigbee_channel": bridge.get("channel"),
        **_routing_attributes(bridge, address),
    }


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
    """Shared passive diagnostics and bounded refresh for source references."""

    def __init__(self, hass):
        self.hass = hass
        self.sources = Counter()
        self.bridges = {}
        self.diagnostics = {}
        self.retry = {}
        self.offline = {}
        self.task = None
        self.refresh_lock = asyncio.Lock()
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
                for sample in self.diagnostics.values():
                    while sample["unsubscribe"]:
                        sample["unsubscribe"].pop()()
                self.diagnostics.clear()
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
                        current["availability"] = "unknown"
                    for sample in self.diagnostics.values():
                        sample["availability"] = "unknown"
                async_dispatcher_send(self.hass, DIAGNOSTICS_UPDATED)
                self._tick(None)

            self.remove_connection_listener = mqtt.async_subscribe_connection_status(
                self.hass, connection_changed,
            )
        bridge = {"online": False, "devices": {}, "unsubscribe": []}
        self.bridges[base] = bridge

        @callback
        def receive(message):
            if message.topic == f"{base}/bridge/state":
                bridge["availability"] = _availability(message.payload)
                bridge["online"] = bridge["availability"] == "online"
                async_dispatcher_send(self.hass, DIAGNOSTICS_UPDATED)
                return
            if message.topic in (f"{base}/bridge/info", f"{base}/bridge/response/networkmap"):
                try:
                    if len(message.payload) > 2 * 1024 * 1024:
                        raise ValueError
                    data = json.loads(message.payload)
                    if not isinstance(data, Mapping):
                        raise ValueError
                    if message.topic.endswith("/info"):
                        coordinator = data.get("coordinator", {})
                        network = data.get("network", {})
                        bridge["hub"] = {
                            "ieee_address": _address(coordinator.get("ieee_address")),
                            "role": "Coordinator",
                            "type": _text(coordinator.get("type")),
                        } if isinstance(coordinator, Mapping) else None
                        channel = network.get("channel") if isinstance(network, Mapping) else None
                        bridge["channel"] = channel if type(channel) is int and 11 <= channel <= 26 else None
                    else:
                        response = data.get("data")
                        if data.get("status") != "ok" or not isinstance(response, Mapping):
                            raise ValueError
                        if response.get("type") != "raw":
                            return
                        topology = _networkmap(response.get("value"))
                        if topology is None:
                            raise ValueError
                        bridge["networkmap"] = topology
                        bridge["networkmap_updated"] = dt_util.utcnow().isoformat()
                        bridge["networkmap_status"] = "ok"
                except (ValueError, TypeError, RecursionError):
                    if message.topic.endswith("/networkmap"):
                        bridge["networkmap_status"] = "error"
                    else:
                        bridge["hub"], bridge["channel"] = None, None
                async_dispatcher_send(self.hass, DIAGNOSTICS_UPDATED)
                return
            try:
                data = json.loads(message.payload)
            except (ValueError, TypeError, RecursionError):
                bridge["devices"] = {}
                self._tick(None)
                async_dispatcher_send(self.hass, DIAGNOSTICS_UPDATED)
                return
            if isinstance(data, list):
                bridge["devices"] = {
                    item["ieee_address"].lower(): item for item in data
                    if isinstance(item, Mapping) and isinstance(item.get("ieee_address"), str)
                }
            else:
                bridge["devices"] = {}
            self._tick(None)
            async_dispatcher_send(self.hass, DIAGNOSTICS_UPDATED)

        try:
            for suffix in ("state", "devices", "info", "response/networkmap"):
                unsubscribe = await mqtt.async_subscribe(
                    self.hass, f"{base}/bridge/{suffix}", receive, 0,
                )
                bridge["unsubscribe"].append(unsubscribe)
        except BaseException:
            while bridge["unsubscribe"]:
                bridge["unsubscribe"].pop()()
            self.bridges.pop(base, None)
            raise

    async def _sync_diagnostics(self, targets):
        """Share exact-topic passive subscriptions by bridge and IEEE address."""
        desired = {}
        for target in targets:
            base, address = target
            device = self.bridges[base]["devices"].get(address, {})
            name = device.get("friendly_name")
            if _topic(name) and len(name) <= 512 and _topic(f"{base}/{name}/availability"):
                desired[target] = name
        for target, sample in list(self.diagnostics.items()):
            if desired.get(target) != sample["name"]:
                for unsubscribe in sample["unsubscribe"]:
                    unsubscribe()
                del self.diagnostics[target]
        for target, name in desired.items():
            if target in self.diagnostics:
                continue
            base, _address = target
            topic = f"{base}/{name}"
            sample = {"name": name, "unsubscribe": [], "availability": "unknown"}
            self.diagnostics[target] = sample

            @callback
            def receive(message, sample=sample, topic=topic):
                if message.topic == f"{topic}/availability":
                    sample["availability"] = _availability(message.payload)
                else:
                    if len(message.payload) > 65536:
                        return
                    try:
                        data = json.loads(message.payload)
                    except (ValueError, TypeError, RecursionError):
                        return
                    if not isinstance(data, Mapping):
                        return
                    # Partial device messages do not erase the last reported values.
                    for key, maximum in (("linkquality", 255), ("battery", 100)):
                        if key in data:
                            sample[key] = _measurement(data[key], maximum)
                    if "last_seen" in data:
                        sample["last_seen"] = _last_seen(data["last_seen"])
                    if "rssi" in data:
                        rssi = data["rssi"]
                        sample["rssi"] = rssi if type(rssi) in (int, float) and -200 <= rssi <= 0 else None
                async_dispatcher_send(self.hass, DIAGNOSTICS_UPDATED)

            try:
                for subscribed_topic in (topic, f"{topic}/availability"):
                    sample["unsubscribe"].append(await mqtt.async_subscribe(
                        self.hass, subscribed_topic, receive, 0,
                    ))
            except BaseException:
                while sample["unsubscribe"]:
                    sample["unsubscribe"].pop()()
                self.diagnostics.pop(target, None)
                async_dispatcher_send(self.hass, DIAGNOSTICS_UPDATED)
                raise
        async_dispatcher_send(self.hass, DIAGNOSTICS_UPDATED)

    async def _refresh(self):
        async with self.refresh_lock:
            if not self.closed:
                await self._refresh_locked()

    async def _refresh_locked(self):
        try:
            if mqtt.DATA_MQTT not in self.hass.data or not mqtt.is_connected(self.hass):
                for bridge in self.bridges.values():
                    bridge["online"] = False
                    bridge["availability"] = "unknown"
                for sample in self.diagnostics.values():
                    sample["availability"] = "unknown"
                async_dispatcher_send(self.hass, DIAGNOSTICS_UPDATED)
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
            try:
                await self._sync_diagnostics(targets)
            except (HomeAssistantError, ValueError, TypeError, KeyError, AttributeError, OSError):
                # Optional diagnostics must not block the existing refresh queue.
                _LOGGER.debug("Zigbee2MQTT diagnostic subscription deferred")
            now = self.hass.loop.time()
            for bridge in self.bridges.values():
                if bridge.get("networkmap_status") == "pending" and now >= bridge.get("next_networkmap", 0):
                    bridge["networkmap_status"] = "timeout"
                    async_dispatcher_send(self.hass, DIAGNOSTICS_UPDATED)
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
