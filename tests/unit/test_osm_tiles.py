"""OpenStreetMap raster backgrounds remain embedded beneath polygon SVG layers."""

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
    Image.new("RGB", (256, 256), "#3b82f6").save(output, "PNG")
    return output.getvalue()


async def test_osm_background_embeds_cached_raster_and_svg_keeps_layer_order(
    hass, monkeypatch
):
    calls = []

    async def tile(_hass, zoom, x, y):
        calls.append((zoom, x, y))
        return _png()

    monkeypatch.setattr(osm_tiles, "_fetch_tile", tile)
    background = await osm_tiles.async_osm_background(hass, _zones(), 320, 180)
    assert background and background.startswith("data:image/png;base64,")
    assert base64.b64decode(background.partition(",")[2]).startswith(b"\x89PNG")
    assert 1 <= len(calls) <= osm_tiles.MAX_TILES
    svg = render_polygon_map_svg(_zones(), 320, 180, background_image=background)
    assert svg.index("data:image/png;base64,") < svg.index("<path d=")
    assert "© OpenStreetMap contributors" in svg


async def test_osm_background_degrades_to_plain_geojson_when_all_tiles_fail(
    hass, monkeypatch
):
    async def unavailable(*_args):
        return None

    monkeypatch.setattr(osm_tiles, "_fetch_tile", unavailable)
    assert await osm_tiles.async_osm_background(hass, _zones()) is None
