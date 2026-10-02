"""Cached Naver Map raster backgrounds for locally rendered SVG maps."""

from __future__ import annotations

import asyncio
import base64
import io
import math
import time
from pathlib import Path

import aiofiles
import aiofiles.os
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
_NAVER_RETRY_DATA = "virtual_layer_naver_map_retry"
BACKGROUND_REFRESH_SECONDS = 60 * 60
BACKGROUND_RETRY_SECONDS = 60
BACKGROUND_TIMEOUT = 6


def _valid_png(data: bytes) -> bool:
    """Fully load a tile: a PNG signature alone does not reject truncation."""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            if image.format != "PNG" or image.size != (TILE_SIZE, TILE_SIZE):
                return False
            image.load()
        return True
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError):
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
        if time.time() - (await aiofiles.os.stat(path)).st_mtime > MIN_CACHE_SECONDS:
            return None
        async with aiofiles.open(path, "rb") as file:
            data = await file.read(MAX_TILE_BYTES + 1)
            return data if len(data) <= MAX_TILE_BYTES else None
    except OSError:
        return None


async def _naver_version(hass):
    """Fetch and cache Naver's active raster style version."""
    lock = hass.data.setdefault(_NAVER_VERSION_DATA + "_lock", asyncio.Lock())
    async with lock:
        return await _fetch_naver_version(hass)


async def _fetch_naver_version(hass):
    now = time.monotonic()
    cached = hass.data.get(_NAVER_VERSION_DATA)
    fallback = cached[0] if isinstance(cached, tuple) and len(cached) == 2 else None
    if now < hass.data.get(_NAVER_RETRY_DATA, 0):
        return fallback
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
            raise ValueError("Invalid map style version")
    except (asyncio.TimeoutError, ClientError, TypeError, ValueError):
        hass.data[_NAVER_RETRY_DATA] = time.monotonic() + BACKGROUND_RETRY_SECONDS
        return fallback
    hass.data[_NAVER_VERSION_DATA] = (version, now)
    return version


async def _fetch_tile(hass, version, zoom, x, y):
    scale = 2**zoom
    x %= scale
    path = _tile_path(hass, version, zoom, x, y)
    if cached := await _read_fresh(path):
        if await hass.async_add_executor_job(_valid_png, cached):
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
            # StreamReader.read(n) may return only the first network chunk.
            # Drain the bounded response before decoding the PNG.
            chunks = []
            size = 0
            async for chunk in response.content.iter_chunked(64 * 1024):
                size += len(chunk)
                if size > MAX_TILE_BYTES:
                    return None
                chunks.append(chunk)
            data = b"".join(chunks)
            if len(data) > MAX_TILE_BYTES or not data.startswith(b"\x89PNG\r\n\x1a\n"):
                return None
        if not await hass.async_add_executor_job(_valid_png, data):
            return None
        try:
            await hass.async_add_executor_job(path.parent.mkdir, 0o755, True, True)
            async with aiofiles.open(path, "wb") as file:
                await file.write(data)
        except OSError:
            # A read-only/full cache must not discard successfully fetched media.
            pass
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
                        tile.convert("RGB").resize(
                            (TILE_SIZE, TILE_SIZE), Image.Resampling.LANCZOS
                        ),
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
    crop = canvas.resize(
        (width, height),
        Image.Resampling.LANCZOS,
        box=(
            x0 - left * TILE_SIZE,
            y0 - top * TILE_SIZE,
            x1 - left * TILE_SIZE,
            y1 - top * TILE_SIZE,
        ),
    )
    output = io.BytesIO()
    crop.save(output, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode()


async def async_map_background(hass, zones, width=720, height=480):
    """Return a cache-backed embedded Naver Map image, or None when unavailable."""
    try:
        plan = _tile_plan(list(zones), width, height)
    except (IndexError, KeyError, TypeError, ValueError):
        return None
    previous = hass.data.setdefault("virtual_layer_map_backgrounds", {})
    cache_key = (plan, width, height)
    deadlines = hass.data.setdefault("virtual_layer_map_background_deadlines", {})
    if time.monotonic() < deadlines.get(cache_key, 0):
        return previous.get(cache_key)
    pending = hass.data.setdefault("virtual_layer_map_background_pending", {})

    async def refresh():
        try:
            try:
                async with asyncio.timeout(BACKGROUND_TIMEOUT):
                    result = await _build_background(hass, plan, width, height)
            except TimeoutError:
                result = None
            delay = BACKGROUND_REFRESH_SECONDS if result else BACKGROUND_RETRY_SECONDS
            if cache_key not in deadlines and len(deadlines) >= 8:
                deadlines.pop(next(iter(deadlines)))
            deadlines[cache_key] = time.monotonic() + delay
            return result or previous.get(cache_key)
        finally:
            pending.pop(cache_key, None)

    if cache_key not in pending:
        pending[cache_key] = hass.async_create_background_task(
            refresh(), "Virtual Layer map background", eager_start=False
        )
    return await asyncio.shield(pending[cache_key])


async def _build_background(hass, plan, width, height):
    previous = hass.data.setdefault("virtual_layer_map_backgrounds", {})
    cache_key = (plan, width, height)
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
    # Retry missing/corrupt tiles once; never publish a mosaic with blank holes.
    valid = await hass.async_add_executor_job(
        lambda: {key: data for key, data in tiles.items() if data and _valid_png(data)}
    )
    missing = [key for key in tiles if key not in valid]
    if missing:
        retried = await asyncio.gather(
            *(_fetch_tile(hass, version, zoom, x, y) for x, y in missing),
            return_exceptions=True,
        )
        valid.update(await hass.async_add_executor_job(
            lambda: {
                key: data for key, data in zip(missing, retried, strict=True)
                if isinstance(data, bytes) and _valid_png(data)
            }
        ))
    if len(valid) != len(tiles):
        return None
    background = await hass.async_add_executor_job(_compose, valid, plan, width, height)
    if cache_key not in previous and len(previous) >= 8:
        evicted = next(iter(previous))
        previous.pop(evicted)
        hass.data.get("virtual_layer_map_background_deadlines", {}).pop(evicted, None)
    previous[cache_key] = background
    return background


# Compatibility alias for callers from releases which exposed this helper under
# its former OSM-specific name. New callers should use async_map_background.
async_osm_background = async_map_background
