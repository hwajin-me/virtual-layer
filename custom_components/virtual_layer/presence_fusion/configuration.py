"""Validate persisted records without relying on UI selectors or HA state."""

import re
from math import isfinite


def device_error(device):
    """Return a non-identifying error code; never include stored user values."""
    if not isinstance(device, dict):
        return "invalid_device"
    if (
        any(
            not isinstance(device.get(k), str) or not device[k].strip()
            for k in ("id", "name")
        )
        or not isinstance(device.get("candidate"), bool)
        or type(device.get("priority")) is not int
        or not 0 <= device["priority"] <= 9999
    ):
        return "invalid_device"
    sources = device.get("sources")
    if not isinstance(sources, list) or len(sources) > 64:
        return "invalid_sources"
    kinds = []
    for source in sources:
        if not isinstance(source, dict) or any(
            not isinstance(source.get(k), str) or not source[k]
            for k in ("id", "entity_id", "kind")
        ):
            return "invalid_source"
        kind = source["kind"]
        entity = source["entity_id"]
        domains = (
            {"device_tracker"}
            if kind == "gps"
            else {
                "device_tracker",
                "sensor",
                "binary_sensor",
                "input_boolean",
                "input_text",
                "input_select",
            }
        )
        if (
            kind not in {"gps", "wifi", "ble", "room"}
            or not re.fullmatch(r"[a-z_]+\.[a-z0-9_]+", entity)
            or entity.split(".")[0] not in domains
        ):
            return "invalid_source"
        for key in ("registry_id", "attribute", "timestamp_attribute"):
            if source.get(key) is not None and not isinstance(source[key], str):
                return "invalid_mapping"
        if source.get("timestamp_format", "iso") not in (
            "iso",
            "seconds",
            "milliseconds",
        ):
            return "invalid_mapping"
        if source.get("freshness", "source_managed") not in (
            "source_managed",
            "timestamp_ttl",
        ):
            return "invalid_mapping"
        if source.get("freshness") == "timestamp_ttl" and not source.get(
            "timestamp_attribute"
        ):
            return "invalid_mapping"
        ttl = source.get("ttl", 300)
        if type(ttl) not in (int, float) or not isfinite(ttl) or not 0 < ttl <= 86400:
            return "invalid_mapping"
        for key in ("positive", "negative"):
            values = source.get(key, [])
            if not isinstance(values, list) or any(
                not isinstance(v, str) or v in {"unknown", "unavailable", "None", ""}
                for v in values
            ):
                return "invalid_mapping"
        if set(source.get("positive", ["on", "home"])) & set(
            source.get("negative", ["off", "not_home"])
        ):
            return "invalid_mapping"
        mapping = source.get("room_mapping", {})
        if not isinstance(mapping, dict) or any(
            not isinstance(k, str)
            or not isinstance(v, str)
            or not k.strip()
            or not v.strip()
            for k, v in mapping.items()
        ):
            return "invalid_mapping"
        kinds.append(kind)
    if (
        kinds.count("gps") > 1
        or kinds.count("room") > 1
        or (device["candidate"] and "gps" not in kinds)
    ):
        return "invalid_source_count"
    return None


def isolate_devices(raw):
    """Keep valid independent records; UI retains originals for repair/removal."""
    if not isinstance(raw, list) or len(raw) > 16:
        return [], ["invalid_device_count"]
    devices, errors = [], []
    ids, priorities, sources, source_ids = set(), set(), set(), set()
    for device in raw:
        error = device_error(device)
        if error:
            errors.append(error)
            continue
        refs = [s.get("registry_id") or s["entity_id"] for s in device["sources"]]
        keys = [s["id"] for s in device["sources"]]
        if (
            device["id"] in ids
            or (device["candidate"] and device["priority"] in priorities)
            or len(set(refs)) != len(refs)
            or set(refs) & sources
            or len(set(keys)) != len(keys)
            or set(keys) & source_ids
        ):
            errors.append("duplicate_mapping")
            continue
        devices.append(device)
        ids.add(device["id"])
        if device["candidate"]:
            priorities.add(device["priority"])
        sources.update(refs)
        source_ids.update(keys)
    return devices, errors
