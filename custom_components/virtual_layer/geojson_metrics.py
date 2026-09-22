"""Bounded geometry summaries; coordinates are longitude, latitude."""

import json
from itertools import pairwise
from math import pi, radians, sin

EARTH_RADIUS = 6371008.8


def ring_area(ring):
    """Spherical area estimate, using the short arc across the date line."""
    value = sum(
        radians((b[0] - a[0] + 180) % 360 - 180)
        * (sin(radians(a[1])) + sin(radians(b[1])))
        for a, b in pairwise(ring)
    )
    area = abs(value) * EARTH_RADIUS**2 / 2
    return min(area, 4 * pi * EARTH_RADIUS**2 - area)


def summarize(zones, document):
    """Summarize complete geometry, subtracting holes (overlaps not dissolved)."""
    polygons = [p for zone in zones for p in zone["polygons"]]
    points = [
        point for p in polygons for ring in [p["outer"], *p["holes"]] for point in ring
    ]
    bounds = None
    if points:
        longitudes = sorted({(p[0] + 180) % 360 for p in points})
        gaps = [
            ((longitudes[(i + 1) % len(longitudes)] - lon) % 360, i)
            for i, lon in enumerate(longitudes)
        ]
        _, index = max(gaps)
        west = longitudes[(index + 1) % len(longitudes)] - 180
        east = longitudes[index] - 180
        bounds = {
            "west": west,
            "south": min(p[1] for p in points),
            "east": east,
            "north": max(p[1] for p in points),
            "crosses_antimeridian": west > east,
        }
    return {
        "zone_names": [z["name"] for z in zones],
        "zone_count": len(zones),
        "polygon_count": len(polygons),
        "coordinate_count": len(points),
        "bounds": bounds,
        "area": sum(
            max(0, ring_area(p["outer"]) - sum(ring_area(h) for h in p["holes"]))
            for p in polygons
        )
        if polygons
        else None,
        "size": len(
            json.dumps(
                document, ensure_ascii=False, separators=(",", ":"), allow_nan=False
            ).encode()
        )
        if zones
        else None,
    }
