"""Cached Naver Map raster backgrounds for locally rendered SVG maps."""

from __future__ import annotations

import asyncio
import base64
import io
import math
import time
from pathlib import Path

import aiofiles
from aiohttp import ClientError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .polygon import map_viewport

# Naver's @2x endpoint returns 512 px raster tiles.
TILE_SIZE = 512
MAX_TILES = 12
MIN_CACHE_SECONDS = 7 * 24 * 60 * 60
MAX_TILE_BYTES = 1024 * 1024
NAVER_METADATA_URL = (
    "https://map.pstatic.net/nrb/styles/basic@2x.json?fmt=png&mt=bg.ol.ts.ar.lko"
)
NAVER_TILE_URL = (
    "https://map.pstatic.net/nrb/styles/basic/{version}/{zoom}/{x}/{y}@2x.png"
    "?mt=bg.ol.ts.ar.lko"
)
NAVER_VERSION_REFRESH_SECONDS = 60 * 60
_NAVER_VERSION_DATA = "virtual_layer_naver_map_version"


def _valid_png(data: bytes) -> bool:
    """Fully load a tile: a PNG signature alone does not reject truncation."""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            image.load()
        return True
    except (OSError, SyntaxError, ValueError):
        return False


def _mercator_y(latitude: float) -> float:
    latitude = max(-85.05112878, min(85.05112878, latitude))
    return (1 - math.asinh(math.tan(math.radians(latitude))) / math.pi) / 2


def _viewport(zones, width, height):
    return map_viewport(zones, width, height)


def _tile_plan(zones, width, height):
    west, south, east, north = _viewport(zones, width, height)
    for zoom in range(18, 0, -1):
        scale = 2**zoom
        left, right = (
            math.floor((west + 180) / 360 * scale),
            math.floor((east + 180) / 360 * scale),
        )
        top, bottom = (
            math.floor(_mercator_y(north) * scale),
            math.floor(_mercator_y(south) * scale),
        )
        if (right - left + 1) * (bottom - top + 1) <= MAX_TILES:
            return west, south, east, north, zoom, left, right, top, bottom
    return west, south, east, north, 0, 0, 0, 0, 0


def _tile_path(hass, version, zoom, x, y):
    return Path(
        hass.config.path(
            ".storage",
            "virtual_layer_naver_tiles",
            version,
            str(zoom),
            str(x),
            f"{y}.png",
        )
    )


async def _read_fresh(path):
    try:
        if time.time() - path.stat().st_mtime > MIN_CACHE_SECONDS:
            return None
        async with aiofiles.open(path, "rb") as file:
            return await file.read()
    except OSError:
        return None


async def _naver_version(hass):
    """Fetch and cache Naver's active raster style version."""
    now = time.monotonic()
    cached = hass.data.get(_NAVER_VERSION_DATA)
    if (
        isinstance(cached, tuple)
        and len(cached) == 2
        and isinstance(cached[0], str)
        and now - cached[1] < NAVER_VERSION_REFRESH_SECONDS
    ):
        return cached[0]
    try:
        session = async_get_clientsession(hass)
        async with session.get(NAVER_METADATA_URL, timeout=10) as response:
            response.raise_for_status()
            metadata = await response.json(content_type=None)
        version = metadata.get("version") if isinstance(metadata, dict) else None
        if not isinstance(version, str) or not version.isdecimal():
            return None
    except (asyncio.TimeoutError, ClientError, TypeError, ValueError):
        return (
            cached[0]
            if isinstance(cached, tuple) and isinstance(cached[0], str)
            else None
        )
    hass.data[_NAVER_VERSION_DATA] = (version, now)
    return version


async def _fetch_tile(hass, version, zoom, x, y):
    scale = 2**zoom
    x %= scale
    path = _tile_path(hass, version, zoom, x, y)
    if cached := await _read_fresh(path):
        return cached
    try:
        session = async_get_clientsession(hass)
        async with session.get(
            NAVER_TILE_URL.format(version=version, zoom=zoom, x=x, y=y),
            headers={
                "User-Agent": "Home-Assistant-Virtual-Layer/1.0 (+https://github.com/hwajin-me/virtual-layer)",
                "Referer": "https://map.naver.com/",
            },
            timeout=15,
        ) as response:
            if (
                response.status != 200
                or response.content_length
                and response.content_length > MAX_TILE_BYTES
            ):
                return None
            data = await response.content.read(MAX_TILE_BYTES + 1)
            if len(data) > MAX_TILE_BYTES or not data.startswith(b"\x89PNG\r\n\x1a\n"):
                return None
        if not await hass.async_add_executor_job(_valid_png, data):
            return None
        await hass.async_add_executor_job(path.parent.mkdir, 0o755, True, True)
        async with aiofiles.open(path, "wb") as file:
            await file.write(data)
        return data
    except (asyncio.TimeoutError, ClientError, OSError):
        return None


def _compose(tiles, plan, width, height):
    from PIL import Image

    west, south, east, north, zoom, left, right, top, bottom = plan
    canvas = Image.new(
        "RGB",
        ((right - left + 1) * TILE_SIZE, (bottom - top + 1) * TILE_SIZE),
        "#f8fafc",
    )
    for (x, y), data in tiles.items():
        if data:
            try:
                with Image.open(io.BytesIO(data)) as tile:
                    canvas.paste(
                        tile.convert("RGB"),
                        ((x - left) * TILE_SIZE, (y - top) * TILE_SIZE),
                    )
            except (OSError, SyntaxError, ValueError):
                # A stale/corrupt cache entry must not make the Image endpoint 500.
                continue
    scale = 2**zoom
    x0, x1 = (
        (west + 180) / 360 * scale * TILE_SIZE,
        (east + 180) / 360 * scale * TILE_SIZE,
    )
    y0, y1 = (
        _mercator_y(north) * scale * TILE_SIZE,
        _mercator_y(south) * scale * TILE_SIZE,
    )
    crop = canvas.crop(
        (
            round(x0 - left * TILE_SIZE),
            round(y0 - top * TILE_SIZE),
            round(x1 - left * TILE_SIZE),
            round(y1 - top * TILE_SIZE),
        )
    ).resize((width, height), Image.Resampling.LANCZOS)
    output = io.BytesIO()
    crop.save(output, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode()


async def async_map_background(hass, zones, width=720, height=480):
    """Return a cache-backed embedded Naver Map image, or None when unavailable."""
    try:
        plan = _tile_plan(list(zones), width, height)
    except (IndexError, KeyError, TypeError, ValueError):
        return None
    if not (version := await _naver_version(hass)):
        return None
    _, _, _, _, zoom, left, right, top, bottom = plan
    jobs = {
        (x, y): _fetch_tile(hass, version, zoom, x, y)
        for x in range(left, right + 1)
        for y in range(top, bottom + 1)
    }
    results = await asyncio.gather(*jobs.values(), return_exceptions=True)
    tiles = {
        key: value if isinstance(value, bytes) else None
        for key, value in zip(jobs, results, strict=True)
    }
    if not any(tiles.values()):
        return None
    return await hass.async_add_executor_job(_compose, tiles, plan, width, height)


# Compatibility alias for callers from releases which exposed this helper under
# its former OSM-specific name. New callers should use async_map_background.
async_osm_background = async_map_background
