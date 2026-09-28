"""Naver Map raster backgrounds remain embedded beneath polygon SVG layers."""

import base64
import io

import pytest
from PIL import Image

from custom_components.virtual_layer import osm_tiles
from custom_components.virtual_layer.polygon import (
    parse_geojson_zones,
    render_polygon_map_svg,
)

pytestmark = pytest.mark.unit


def _zones():
    return parse_geojson_zones(
        {
            "type": "Feature",
            "properties": {"name": "Home"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [[126.99, 37.54], [127.00, 37.54], [127.00, 37.55], [126.99, 37.54]]
                ],
            },
        }
    )


def _png():
    output = io.BytesIO()
    Image.new("RGB", (osm_tiles.TILE_SIZE, osm_tiles.TILE_SIZE), "#3b82f6").save(
        output, "PNG"
    )
    return output.getvalue()


async def test_naver_background_embeds_cached_raster_and_svg_keeps_layer_order(
    hass, monkeypatch
):
    calls = []

    async def tile(_hass, _version, zoom, x, y):
        calls.append((zoom, x, y))
        return _png()

    async def version(_hass):
        return "test-version"

    monkeypatch.setattr(osm_tiles, "_fetch_tile", tile)
    monkeypatch.setattr(osm_tiles, "_naver_version", version)
    background = await osm_tiles.async_map_background(hass, _zones(), 320, 180)
    assert background and background.startswith("data:image/png;base64,")
    assert base64.b64decode(background.partition(",")[2]).startswith(b"\x89PNG")
    assert 1 <= len(calls) <= osm_tiles.MAX_TILES
    svg = render_polygon_map_svg(_zones(), 320, 180, background_image=background)
    assert svg.index("data:image/png;base64,") < svg.index("<path d=")
    assert "© Naver Corp." in svg


async def test_naver_background_degrades_to_plain_geojson_when_all_tiles_fail(
    hass, monkeypatch
):
    async def unavailable(*_args):
        return None

    monkeypatch.setattr(osm_tiles, "_fetch_tile", unavailable)

    async def version(_hass):
        return "test-version"

    monkeypatch.setattr(osm_tiles, "_naver_version", version)
    assert await osm_tiles.async_map_background(hass, _zones()) is None


async def test_naver_background_ignores_a_truncated_cached_tile(hass, monkeypatch):
    async def tiles(_hass, _version, _zoom, x, _y):
        return b"\x89PNG\r\n\x1a\n" if x % 2 else _png()

    async def version(_hass):
        return "test-version"

    monkeypatch.setattr(osm_tiles, "_fetch_tile", tiles)
    monkeypatch.setattr(osm_tiles, "_naver_version", version)
    background = await osm_tiles.async_map_background(hass, _zones(), 320, 180)
    assert background is None


async def test_missing_tile_retries_and_preserves_complete_background(hass, monkeypatch):
    from unittest.mock import AsyncMock

    tile = AsyncMock(return_value=_png())
    monkeypatch.setattr(osm_tiles, "_fetch_tile", tile)
    monkeypatch.setattr(osm_tiles, "_naver_version", AsyncMock(return_value="123"))
    first = await osm_tiles.async_map_background(hass, _zones(), 320, 180)
    assert first
    tile.return_value = None
    assert await osm_tiles.async_map_background(hass, _zones(), 320, 180) == first
    # A different geographic viewport must never reuse the previous image.
    assert await osm_tiles.async_map_background(hass, _zones(), 400, 400) is None


async def test_fetch_tile_reads_all_network_chunks(hass, monkeypatch, tmp_path):
    from unittest.mock import AsyncMock, Mock

    data = _png()

    class Response:
        status = 200
        content_length = None
        content = None

        async def __aenter__(self):
            self.content = self
            return self

        async def __aexit__(self, *_args):
            pass

        async def iter_chunked(self, _size):
            for offset in range(0, len(data), 17):
                yield data[offset:offset + 17]

    monkeypatch.setattr(osm_tiles, "_read_fresh", AsyncMock(return_value=None))
    monkeypatch.setattr(osm_tiles, "_tile_path", lambda *_args: tmp_path / "tile.png")
    monkeypatch.setattr(osm_tiles, "async_get_clientsession", lambda _: Mock(get=Mock(return_value=Response())))
    assert await osm_tiles._fetch_tile(hass, "123", 15, 27947, 12710) == data


async def test_naver_map_version_is_cached_then_refreshed(hass, monkeypatch):
    responses = ["100", "200"]

    class Response:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def raise_for_status(self):
            return None

        async def json(self, **_kwargs):
            return {"version": responses.pop(0)}

    class Session:
        def get(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(osm_tiles, "async_get_clientsession", lambda _hass: Session())
    monkeypatch.setattr(osm_tiles.time, "monotonic", lambda: 100.0)
    assert await osm_tiles._naver_version(hass) == "100"
    assert await osm_tiles._naver_version(hass) == "100"
    monkeypatch.setattr(
        osm_tiles.time,
        "monotonic",
        lambda: 100.0 + osm_tiles.NAVER_VERSION_REFRESH_SECONDS,
    )
    assert await osm_tiles._naver_version(hass) == "200"
