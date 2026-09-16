"""Explicit Wi-Fi and remote BLE presence inputs for composite trackers."""

import re
from datetime import timedelta
from math import isfinite

import voluptuous as vol
from homeassistant.core import State
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

BLE_PREFIX = "ble:"
DEFAULTS = {
    "wifi_entities": [],
    "wifi_ssids": [],
    "ble_addresses": [],
    "ble_sources": ["ab_gateway"],
    "ble_timeout": 120,
    "ble_min_rssi": -90,
}


def normalize(value):
    if not isinstance(value, dict) or set(value) - set(DEFAULTS):
        raise vol.Invalid("Invalid local presence configuration")
    result = {}
    for key in ("wifi_entities", "wifi_ssids", "ble_addresses", "ble_sources"):
        raw = value.get(key, DEFAULTS[key])
        if not isinstance(raw, list) or len(raw) > 64:
            raise vol.Invalid("Invalid local presence list")
        cleaned = []
        for item in raw:
            if (
                not isinstance(item, str)
                or not item.strip()
                or len(item) > 256
                or any(ord(c) < 32 for c in item)
            ):
                raise vol.Invalid("Invalid local presence value")
            item = item if key == "wifi_ssids" else item.strip()
            if key == "wifi_entities":
                item = cv.entity_id(item)
                if item.split(".")[0] not in {
                    "device_tracker",
                    "binary_sensor",
                    "sensor",
                }:
                    raise vol.Invalid("Invalid Wi-Fi source domain")
            if key == "ble_addresses":
                raw_mac = item.replace(":", "").replace("-", "")
                if not re.fullmatch(r"[a-fA-F0-9]{12}", raw_mac):
                    raise vol.Invalid("Invalid BLE address")
                item = ":".join(raw_mac[i : i + 2] for i in range(0, 12, 2)).upper()
            if item not in cleaned:
                cleaned.append(item)
        result[key] = cleaned
    for key, minimum, maximum in (("ble_timeout", 10, 3600), ("ble_min_rssi", -127, 0)):
        raw = value.get(key, DEFAULTS[key])
        try:
            number = float(raw)
            if (
                isinstance(raw, bool)
                or not isfinite(number)
                or not number.is_integer()
                or not minimum <= number <= maximum
            ):
                raise ValueError
        except (ValueError, TypeError, OverflowError) as err:
            raise vol.Invalid("Invalid BLE threshold") from err
        result[key] = int(number)
    if result["ble_addresses"] and not result["ble_sources"]:
        raise vol.Invalid("Select at least one Bluetooth scanner source")
    if (
        any(e.startswith("sensor.") for e in result["wifi_entities"])
        and not result["wifi_ssids"]
    ):
        raise vol.Invalid("SSID sensors require an explicit SSID list")
    if not result["wifi_entities"] and not result["ble_addresses"]:
        raise vol.Invalid("Select a Wi-Fi source or BLE address")
    return result


class LocalPresence:
    """Read existing HA sources; never scan the network or consume MQTT twice."""

    def __init__(self, hass, config):
        self.hass = hass
        self.config = config
        self._strong = {}
        self.error = None

    def wifi_state(self, entity_id):
        state = self.hass.states.get(entity_id)
        if state is None:
            return None
        value = state.state
        if value in {"unknown", "unavailable"}:
            presence = value
        elif entity_id.startswith("sensor."):
            presence = "home" if value in self.config["wifi_ssids"] else "not_home"
        else:
            presence = (
                "home"
                if value.lower() in {"home", "on", "connected", "true"}
                else "not_home"
            )
        # Wi-Fi connection state is authoritative until the router changes it;
        # unlike GPS, a stationary connection need not emit periodic updates.
        return State(
            entity_id,
            presence,
            {"source_type": "router", "local_connection": True},
            last_changed=state.last_changed,
            last_updated=dt_util.utcnow(),
            last_reported=dt_util.utcnow(),
        )

    def ble_states(self):
        addresses = self.config["ble_addresses"]
        if not addresses:
            return {}
        from homeassistant.components import bluetooth

        now = dt_util.utcnow()
        monotonic = bluetooth.MONOTONIC_TIME()
        available = set()
        self.error = None
        if "bluetooth" in self.hass.config.components:
            available = {
                scanner.source
                for scanner in bluetooth.async_current_scanners(self.hass)
                if getattr(scanner, "scanning", True)
            }
        wanted = set(self.config["ble_sources"])
        if wanted - available:
            self.error = "scanner_unavailable"
        states = {}
        for index, address in enumerate(addresses):
            if available & wanted:
                # AB Gateway historically supplies lower-case addresses.
                devices = bluetooth.async_scanner_devices_by_address(
                    self.hass, address, connectable=False
                )
                devices += bluetooth.async_scanner_devices_by_address(
                    self.hass, address.lower(), connectable=False
                )
                for device in devices:
                    scanner = device.scanner
                    if scanner.source not in wanted:
                        continue
                    times = getattr(scanner, "discovered_device_timestamps", {})
                    timestamp = times.get(address, times.get(address.lower()))
                    rssi = device.advertisement.rssi
                    if isinstance(timestamp, bool) or not isinstance(
                        timestamp, (int, float)
                    ):
                        continue
                    age = monotonic - timestamp
                    if not 0 <= age <= self.config["ble_timeout"]:
                        continue
                    if (
                        isinstance(rssi, bool)
                        or not isinstance(rssi, (int, float))
                        or not isfinite(rssi)
                        or not -127 <= rssi <= 0
                    ):
                        continue
                    if rssi >= self.config["ble_min_rssi"]:
                        self._strong[address] = max(
                            timestamp, self._strong.get(address, timestamp)
                        )
            seen = self._strong.get(address)
            recent = (
                seen is not None and 0 <= monotonic - seen <= self.config["ble_timeout"]
            )
            presence = "home" if recent else ("unknown" if self.error else "not_home")
            measured = now - timedelta(seconds=monotonic - seen) if recent else now
            states[BLE_PREFIX + address] = State(
                f"device_tracker.virtual_layer_ble_{index}",
                presence,
                {"source_type": "bluetooth_le"},
                last_updated=measured,
                last_reported=now,
            )
        return states
