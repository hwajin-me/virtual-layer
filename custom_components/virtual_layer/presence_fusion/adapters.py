"""Explicit source mappings and timestamp parsing, independent of HA state clocks."""

from datetime import datetime
from math import isfinite

from .models import GPS


def number(value):
    try:
        if isinstance(value, bool):
            raise TypeError("invalid_number")
        result = float(value)
    except (TypeError, ValueError) as err:
        # Voluptuous callable validators translate ValueError into field errors.
        raise ValueError("invalid_number") from err
    if not isfinite(result):
        raise ValueError("invalid_number")
    return result


def timestamp(value, fmt):
    if fmt == "iso":
        result = datetime.fromisoformat(str(value))
        if result.tzinfo is None:
            raise ValueError("timezone_required")
        return result.timestamp()
    if fmt not in {"seconds", "milliseconds"}:
        raise ValueError("timestamp_format")
    return number(value) / (1000 if fmt == "milliseconds" else 1)


def gps_observation(config, state, old, now, settings, initial=False):
    if state is None or state.state in {"unknown", "unavailable"}:
        return None, "unavailable" if state else "removed"
    attrs = state.attributes
    try:
        lat, lon = number(attrs.get("latitude")), number(attrs.get("longitude"))
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError("coordinates")
        accuracy = attrs.get("gps_accuracy")
        try:
            accuracy = number(accuracy)
        except (TypeError, ValueError):
            accuracy = 0
        assumed = accuracy <= 0
        if assumed:
            accuracy = settings.gps_missing_accuracy_m
        attribute = config.get("timestamp_attribute")
        if attribute:
            observed = timestamp(
                attrs.get(attribute), config.get("timestamp_format", "iso")
            )
            basis = "measurement"
        else:
            if initial:
                return None, "restored_without_timestamp"
            keys = ("latitude", "longitude", "gps_accuracy")
            if old and all(old.attributes.get(k) == attrs.get(k) for k in keys):
                return None, "not_location_event"
            observed, basis = now, "received"
        return GPS(
            lat, lon, accuracy, observed, now, assumed, basis, config["entity_id"]
        ), None
    except (TypeError, ValueError, OverflowError):
        return None, "invalid_observation"


def local_observation(config, state, now):
    for key in ("positive", "negative"):
        if key in config and (
            not isinstance(config[key], list)
            or any(not isinstance(v, str) for v in config[key])
        ):
            return "unknown", None, "invalid_mapping", None
    if config.get("freshness", "source_managed") not in {
        "source_managed",
        "timestamp_ttl",
    }:
        return "unknown", None, "invalid_mapping", None
    if state is None:
        return "unknown", None, "removed", None
    if state.state in {"unknown", "unavailable"}:
        return "unknown", None, state.state, None
    expires = None
    if config.get("freshness", "source_managed") == "timestamp_ttl":
        try:
            observed = timestamp(
                state.attributes.get(config.get("timestamp_attribute")),
                config.get("timestamp_format", "iso"),
            )
            ttl = number(config["ttl"])
            if not 0 < ttl <= 86400:
                raise ValueError("ttl")
            expires = observed + ttl
            if observed > now + 30 or now >= expires:
                return "unknown", expires, "stale", None
        except (TypeError, ValueError, OverflowError, KeyError):
            return "unknown", None, "invalid_timestamp", None
    value = (
        state.attributes.get(config["attribute"])
        if config.get("attribute")
        else state.state
    )
    value = str(value)
    if value in {"unknown", "unavailable", "None", ""}:
        return "unknown", expires, "unknown", None
    if value in config.get("negative", ["off", "not_home"]):
        return "absent", expires, "available", None
    if config["kind"] == "room":
        mapping = config.get("room_mapping", {})
        if not isinstance(mapping, dict) or not isinstance(
            mapping.get(value, value), str
        ):
            return "unknown", expires, "invalid_mapping", None
        room = mapping.get(value, value).strip()[:255]
        return "present", expires, "available", room or None
    if value in config.get("positive", ["on", "home"]):
        return "present", expires, "available", None
    return "unknown", expires, "unmapped", None
