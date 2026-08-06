"""GeoJSON polygon and multi-tracker location helpers."""

from __future__ import annotations

import asyncio
import html
import json
import math
from collections.abc import Mapping
from datetime import datetime
from itertools import pairwise
from pathlib import Path
from statistics import median
from typing import Any

import aiofiles
from aiohttp import ClientError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

EARTH_RADIUS_METERS = 6_371_000
SUPPORTED_GEOMETRY_TYPES = {"Polygon", "MultiPolygon"}
SVG_COLORS = (
    "#2563eb",
    "#16a34a",
    "#dc2626",
    "#9333ea",
    "#ea580c",
    "#0891b2",
)


class InvalidGeoJson(ValueError):
    """Raised when a GeoJSON document cannot describe usable polygon zones."""


def _coordinate(value) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        raise ValueError("GeoJSON coordinates must contain longitude and latitude")
    longitude = float(value[0])
    latitude = float(value[1])
    if (
        not math.isfinite(longitude)
        or not math.isfinite(latitude)
        or not -180 <= longitude <= 180
        or not -90 <= latitude <= 90
    ):
        raise ValueError("GeoJSON coordinate is outside the valid GPS range")
    return longitude, latitude


def _ring(value) -> list[tuple[float, float]]:
    if not isinstance(value, list):
        raise InvalidGeoJson("GeoJSON polygon ring must be a list")
    ring = [_coordinate(item) for item in value]
    if len(ring) < 3:
        raise ValueError("GeoJSON polygon ring needs at least three points")
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    if len(ring) < 4:
        raise ValueError("GeoJSON polygon ring is invalid")
    return ring


def _polygon(value) -> dict[str, Any]:
    if not isinstance(value, list) or not value:
        raise ValueError("GeoJSON polygon must contain an exterior ring")
    rings = [_ring(item) for item in value]
    if _ring_area(rings[0]) == 0:
        raise ValueError("GeoJSON polygon exterior ring has no area")
    if any(_ring_area(hole) == 0 for hole in rings[1:]):
        raise ValueError("GeoJSON polygon hole has no area")
    if any(
        not all(_point_in_ring(longitude, latitude, rings[0]) for longitude, latitude in hole[:-1])
        for hole in rings[1:]
    ):
        raise ValueError("GeoJSON polygon hole must be inside its exterior ring")
    return {"outer": rings[0], "holes": rings[1:]}


def _ring_area(ring: list[tuple[float, float]]) -> float:
    unwrapped = _unwrap_ring(ring)
    return abs(sum(
        first[0] * second[1] - second[0] * first[1]
        for first, second in pairwise(unwrapped)
    )) / 2


def _longitude_delta(longitude: float, anchor: float) -> float:
    """Return the shortest signed longitude delta from an anchor."""
    return (longitude - anchor + 180) % 360 - 180


def _normalize_longitude(longitude: float) -> float:
    normalized = (longitude + 180) % 360 - 180
    return 180.0 if normalized == -180 and longitude > 0 else normalized


def median_longitude(values, anchor: float | None = None) -> float:
    """Return a median longitude without averaging across the date line."""
    values = list(values)
    if not values:
        raise ValueError("At least one longitude is required")
    if max(values) - min(values) <= 180:
        return median(values)
    anchor = values[0] if anchor is None else anchor
    return _normalize_longitude(median(
        anchor + _longitude_delta(longitude, anchor)
        for longitude in values
    ))


def _unwrap_ring(ring) -> list[tuple[float, float]]:
    """Make consecutive ring longitudes continuous across the date line."""
    unwrapped = [ring[0]]
    for point in ring[1:]:
        previous_original = ring[len(unwrapped) - 1]
        previous_longitude = unwrapped[-1][0]
        unwrapped.append((
            previous_longitude + _longitude_delta(point[0], previous_original[0]),
            point[1],
        ))
    return unwrapped


def parse_geojson_zones(data, default_priority: int = 0) -> list[dict[str, Any]]:
    """Parse supported GeoJSON features into a compact runtime representation."""
    if isinstance(data, str):
        data = json.loads(data)
    if not isinstance(data, Mapping):
        raise InvalidGeoJson("GeoJSON root must be an object")

    data_type = data.get("type")
    if data_type == "FeatureCollection":
        features = data.get("features")
    elif data_type == "Feature":
        features = [data]
    else:
        raise ValueError("GeoJSON root must be a Feature or FeatureCollection")
    if not isinstance(features, list):
        raise InvalidGeoJson("GeoJSON features must be a list")

    zones = []
    for feature_index, feature in enumerate(features):
        if not isinstance(feature, Mapping):
            raise InvalidGeoJson("GeoJSON feature must be an object")
        geometry = feature.get("geometry")
        properties = feature.get("properties", {})
        if not isinstance(geometry, Mapping) or not isinstance(properties, Mapping):
            raise InvalidGeoJson(
                "GeoJSON feature geometry and properties must be objects"
            )
        geometry_type = geometry.get("type")
        if geometry_type not in SUPPORTED_GEOMETRY_TYPES:
            raise ValueError(f"Unsupported GeoJSON geometry: {geometry_type}")
        name = properties.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Every polygon zone needs a name property")
        try:
            priority = int(properties.get("priority", default_priority))
        except (TypeError, ValueError) as err:
            raise ValueError(f"Invalid priority for polygon zone {name}") from err

        coordinates = geometry.get("coordinates")
        if geometry_type == "Polygon":
            polygons = [_polygon(coordinates)]
        else:
            if not isinstance(coordinates, list) or not coordinates:
                raise ValueError("GeoJSON MultiPolygon must contain polygons")
            polygons = [_polygon(item) for item in coordinates]
        area = sum(
            _ring_area(item["outer"])
            - sum(_ring_area(hole) for hole in item["holes"])
            for item in polygons
        )
        zones.append({
            "name": name.strip(),
            "priority": priority,
            "polygons": polygons,
            "area": max(area, 0),
            "properties": dict(properties),
            "feature_index": feature_index,
        })
    if not zones:
        raise ValueError("GeoJSON must contain at least one polygon zone")
    return zones


def _point_in_ring(longitude: float, latitude: float, ring) -> bool:
    inside = False
    unwrapped = _unwrap_ring(ring)
    longitude = unwrapped[0][0] + _longitude_delta(longitude, ring[0][0])
    for first, second in pairwise(unwrapped):
        x1 = first[0]
        x2 = second[0]
        y1 = first[1]
        y2 = second[1]
        cross = (longitude - x1) * (y2 - y1) - (latitude - y1) * (x2 - x1)
        if abs(cross) < 1e-12 and min(x1, x2) <= longitude <= max(x1, x2) and min(y1, y2) <= latitude <= max(y1, y2):
            return True
        if (y1 > latitude) != (y2 > latitude):
            intersection = (x2 - x1) * (latitude - y1) / (y2 - y1) + x1
            if longitude < intersection:
                inside = not inside
    return inside


def _distance_to_segment_meters(latitude, longitude, first, second) -> float:
    latitude_radians = math.radians(latitude)
    first = (
        longitude + _longitude_delta(first[0], longitude),
        first[1],
    )
    second = (
        first[0] + _longitude_delta(second[0], first[0]),
        second[1],
    )

    def local(point):
        return (
            EARTH_RADIUS_METERS
            * math.radians(point[0] - longitude)
            * math.cos(latitude_radians),
            EARTH_RADIUS_METERS * math.radians(point[1] - latitude),
        )

    x1, y1 = local(first)
    x2, y2 = local(second)
    dx = x2 - x1
    dy = y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(x1, y1)
    projection = max(0.0, min(1.0, -(x1 * dx + y1 * dy) / (dx * dx + dy * dy)))
    return math.hypot(x1 + projection * dx, y1 + projection * dy)


def _polygon_contains(latitude, longitude, accuracy, polygon) -> bool:
    outer = polygon["outer"]
    holes = polygon["holes"]
    if _point_in_ring(longitude, latitude, outer) and not any(
        _point_in_ring(longitude, latitude, hole) for hole in holes
    ):
        return True
    if accuracy <= 0:
        return False
    return any(
        _distance_to_segment_meters(latitude, longitude, first, second) <= accuracy
        for ring in (outer, *holes)
        for first, second in pairwise(ring)
    )


def find_polygon_zone(latitude, longitude, accuracy, zones):
    """Return the highest-priority polygon intersecting a GPS accuracy circle."""
    matches = [
        zone
        for zone in zones
        if any(
            _polygon_contains(latitude, longitude, max(0.0, accuracy), polygon)
            for polygon in zone["polygons"]
        )
    ]
    if not matches:
        return None
    return min(matches, key=lambda zone: (zone["priority"], zone["area"], zone["name"]))


def _svg_path(rings, project) -> str:
    commands = []
    for ring in rings:
        points = [project(longitude, latitude) for longitude, latitude in ring]
        if not points:
            continue
        first, *rest = points
        commands.append(f"M {first[0]:.2f} {first[1]:.2f}")
        commands.extend(f"L {point[0]:.2f} {point[1]:.2f}" for point in rest)
        commands.append("Z")
    return " ".join(commands)


def _align_unwrapped_ring(ring, anchor: float) -> list[tuple[float, float]]:
    """Shift a continuous ring to the world copy nearest an anchor."""
    unwrapped = _unwrap_ring(ring)
    shift = round((anchor - unwrapped[0][0]) / 360) * 360
    return [(longitude + shift, latitude) for longitude, latitude in unwrapped]


def render_polygon_map_svg(
    zones,
    width: int = 720,
    height: int = 480,
    markers=None,
) -> str:
    """Render polygon zones as a compact SVG image for Home Assistant image entities."""
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        raise ValueError("SVG width and height must be positive integers")

    zones = list(zones)
    try:
        anchor = zones[0]["polygons"][0]["outer"][0][0]
    except (IndexError, KeyError, TypeError):
        raise ValueError("No polygon rings to render") from None

    render_zones = []
    rings = []
    for zone in zones:
        render_polygons = []
        for polygon in zone["polygons"]:
            outer = _align_unwrapped_ring(polygon["outer"], anchor)
            holes = [
                _align_unwrapped_ring(hole, outer[0][0])
                for hole in polygon["holes"]
            ]
            render_polygons.append({"outer": outer, "holes": holes})
            rings.extend((outer, *holes))
        render_zones.append((zone, render_polygons))
    if not rings:
        raise ValueError("No polygon rings to render")

    render_markers = []
    for marker in markers or []:
        try:
            latitude = float(marker["latitude"])
            longitude = float(marker["longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        if (
            not math.isfinite(latitude)
            or not math.isfinite(longitude)
            or not -90 <= latitude <= 90
            or not -180 <= longitude <= 180
        ):
            continue
        render_markers.append({
            **marker,
            "latitude": latitude,
            "longitude": anchor + _longitude_delta(longitude, anchor),
        })

    longitudes = [longitude for ring in rings for longitude, _latitude in ring]
    longitudes.extend(marker["longitude"] for marker in render_markers)
    latitudes = [latitude for ring in rings for _longitude, latitude in ring]
    latitudes.extend(marker["latitude"] for marker in render_markers)
    min_longitude = min(longitudes)
    max_longitude = max(longitudes)
    min_latitude = min(latitudes)
    max_latitude = max(latitudes)
    longitude_span = max(max_longitude - min_longitude, 0.0001)
    latitude_span = max(max_latitude - min_latitude, 0.0001)
    padding = 32
    draw_width = max(width - padding * 2, 1)
    draw_height = max(height - padding * 2, 1)
    scale = min(draw_width / longitude_span, draw_height / latitude_span)
    map_width = longitude_span * scale
    map_height = latitude_span * scale
    offset_x = (width - map_width) / 2
    offset_y = (height - map_height) / 2

    def project(longitude, latitude):
        return (
            offset_x + (longitude - min_longitude) * scale,
            offset_y + (max_latitude - latitude) * scale,
        )

    paths = []
    labels = []
    for zone_index, (zone, polygons) in enumerate(render_zones):
        color = SVG_COLORS[zone_index % len(SVG_COLORS)]
        for polygon in polygons:
            outer = polygon["outer"]
            holes = polygon["holes"]
            path = _svg_path((outer, *holes), project)
            if path:
                paths.append(
                    f'<path d="{path}" fill="{color}" fill-opacity="0.22" '
                    f'stroke="{color}" stroke-width="3" stroke-linejoin="round" '
                    'fill-rule="evenodd"/>'
                )
            label_x = sum(point[0] for point in outer[:-1]) / max(len(outer) - 1, 1)
            label_y = sum(point[1] for point in outer[:-1]) / max(len(outer) - 1, 1)
            x, y = project(label_x, label_y)
            labels.append(
                f'<text x="{x:.2f}" y="{y:.2f}" text-anchor="middle" '
                'font-family="Arial, sans-serif" font-size="16" '
                'font-weight="700" fill="#111827" paint-order="stroke" '
                'stroke="#ffffff" stroke-width="4" stroke-linejoin="round">'
                f'{html.escape(str(zone["name"]))}</text>'
            )

    marker_elements = []
    for marker in render_markers:
        x, y = project(marker["longitude"], marker["latitude"])
        entity_id = html.escape(str(marker.get("entity_id", "")), quote=True)
        label = html.escape(str(marker.get("label") or entity_id or "Current location"))
        marker_elements.append(
            f'<g data-entity-id="{entity_id}">'
            f'<circle cx="{x:.2f}" cy="{y:.2f}" r="9" fill="#e11d48" '
            'stroke="#ffffff" stroke-width="4"/>'
            f'<text x="{x:.2f}" y="{y - 15:.2f}" text-anchor="middle" '
            'font-family="Arial, sans-serif" font-size="14" font-weight="700" '
            'fill="#881337" paint-order="stroke" stroke="#ffffff" '
            f'stroke-width="4" stroke-linejoin="round">{label}</text></g>'
        )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="Polygon map">'
        '<rect width="100%" height="100%" fill="#f8fafc"/>'
        '<g opacity="0.35" stroke="#cbd5e1" stroke-width="1">'
        f'<path d="M {padding} {height / 2:.2f} H {width - padding}"/>'
        f'<path d="M {width / 2:.2f} {padding} V {height - padding}"/>'
        '</g>'
        f'<g>{"".join(paths)}</g>'
        f'<g>{"".join(labels)}</g>'
        f'<g>{"".join(marker_elements)}</g>'
        '</svg>'
    )


async def _local_geojson_path(hass, file_name: str) -> str:
    def resolve() -> str:
        candidate = Path(file_name)
        if not candidate.is_absolute():
            candidate = Path(hass.config.config_dir) / candidate
        candidate = candidate.resolve()
        config_root = Path(hass.config.config_dir).resolve()
        if candidate.is_relative_to(config_root) or hass.config.is_allowed_path(str(candidate)):
            return str(candidate)
        raise ValueError(f"GeoJSON path is outside Home Assistant's allowed directories: {file_name}")

    return await hass.async_add_executor_job(resolve)


async def load_polygon_zones(
    hass,
    inline_geojson=None,
    files=None,
    *,
    return_errors=False,
):
    """Load and combine inline, local, and remote GeoJSON zone definitions."""
    zones = []
    errors = []
    if inline_geojson:
        try:
            zones.extend(parse_geojson_zones(inline_geojson, 0))
        except (json.JSONDecodeError, TypeError, ValueError) as err:
            if not return_errors:
                raise
            errors.append(f"inline GeoJSON: {err}")
    session = None
    for file_index, file_name in enumerate(files or [], start=1):
        if not isinstance(file_name, str) or not file_name.strip():
            continue
        file_name = file_name.strip()
        try:
            if file_name.startswith(("http://", "https://")):
                session = session or async_get_clientsession(hass)
                async with session.get(file_name, timeout=20) as response:
                    response.raise_for_status()
                    payload = await response.json(content_type=None)
            else:
                path = await _local_geojson_path(hass, file_name)
                async with aiofiles.open(path, encoding="utf-8") as geojson_file:
                    payload = json.loads(await geojson_file.read())
            zones.extend(parse_geojson_zones(payload, file_index))
        except (
            asyncio.TimeoutError,
            ClientError,
            json.JSONDecodeError,
            OSError,
            TypeError,
            ValueError,
        ) as err:
            if not return_errors:
                raise
            errors.append(f"{file_name}: {err}")
    return (zones, errors) if return_errors else zones


def distance_meters(first, second) -> float:
    """Return haversine distance between (latitude, longitude) pairs."""
    latitude1, longitude1 = map(math.radians, first)
    latitude2, longitude2 = map(math.radians, second)
    delta_latitude = latitude2 - latitude1
    delta_longitude = longitude2 - longitude1
    value = (
        math.sin(delta_latitude / 2) ** 2
        + math.cos(latitude1) * math.cos(latitude2) * math.sin(delta_longitude / 2) ** 2
    )
    value = max(0.0, min(1.0, value))
    return EARTH_RADIUS_METERS * 2 * math.atan2(
        math.sqrt(value),
        math.sqrt(1 - value),
    )


def _latest_timestamp(sample) -> float:
    updated = sample.get("last_updated")
    return updated.timestamp() if isinstance(updated, datetime) else 0.0


def _best_source(samples):
    return min(
        samples,
        key=lambda sample: (
            float(sample.get("priority", 100)),
            -_latest_timestamp(sample),
            sample["entity_id"],
        ),
    )


def _connected_clusters(samples, distance_threshold):
    remaining = set(range(len(samples)))
    clusters = []
    while remaining:
        pending = [remaining.pop()]
        cluster_indexes = set(pending)
        while pending:
            current = pending.pop()
            neighbours = {
                candidate
                for candidate in remaining
                if distance_meters(
                    (samples[current]["latitude"], samples[current]["longitude"]),
                    (samples[candidate]["latitude"], samples[candidate]["longitude"]),
                ) <= distance_threshold
            }
            remaining.difference_update(neighbours)
            cluster_indexes.update(neighbours)
            pending.extend(neighbours)
        clusters.append(sorted(
            (samples[index] for index in cluster_indexes),
            key=lambda sample: sample["entity_id"],
        ))
    return clusters


def select_tracker_position(samples, strategy="majority", distance_threshold=300):
    """Select a stable combined GPS position from normalized tracker samples."""
    samples = list(samples)
    if not samples:
        return None

    dominant = [sample for sample in samples if sample.get("dominant")]
    if dominant:
        selected = _best_source(dominant)
        selected_samples = [selected]
        reason = "dominant"
    elif strategy == "priority":
        selected = _best_source(samples)
        selected_samples = [selected]
        reason = "priority"
    elif strategy == "latest":
        selected = max(samples, key=lambda sample: (_latest_timestamp(sample), sample["entity_id"]))
        selected_samples = [selected]
        reason = "latest"
    elif strategy == "median":
        selected = max(samples, key=lambda sample: (_latest_timestamp(sample), sample["entity_id"]))
        selected_samples = samples
        reason = "median"
    else:
        clusters = _connected_clusters(samples, distance_threshold)
        selected_samples = max(
            clusters,
            key=lambda cluster: (
                sum(float(sample.get("weight", 1)) for sample in cluster),
                len(cluster),
                max(
                    (_latest_timestamp(sample), sample["entity_id"])
                    for sample in cluster
                ),
            ),
        )
        selected = max(
            selected_samples,
            key=lambda sample: (_latest_timestamp(sample), sample["entity_id"]),
        )
        reason = "majority"

    anchor_longitude = selected["longitude"]
    longitude = median_longitude(
        (sample["longitude"] for sample in selected_samples),
        anchor_longitude,
    )
    return {
        "latitude": median(sample["latitude"] for sample in selected_samples),
        "longitude": longitude,
        "gps_accuracy": max(float(sample.get("gps_accuracy", 0)) for sample in selected_samples),
        "selected_source": selected["entity_id"],
        "members": [sample["entity_id"] for sample in selected_samples],
        "reason": reason,
    }
