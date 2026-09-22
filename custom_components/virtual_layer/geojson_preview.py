"""Authenticated, short-lived SVG previews for the GeoJSON options flow."""

from __future__ import annotations

import secrets
import time

from aiohttp import web
from homeassistant.components.http import HomeAssistantView

_DATA = "virtual_layer_geojson_previews"
_VIEW = "virtual_layer_geojson_preview_view"
_MAX_AGE_SECONDS = 15 * 60


class GeoJSONPreviewView(HomeAssistantView):
    """Serve a preview only to an authenticated Home Assistant client."""

    name = "api:virtual_layer:geojson_preview"
    url = "/api/virtual_layer/geojson_preview/{token}.svg"
    requires_auth = True

    def __init__(self, hass):
        self.hass = hass

    async def get(self, _request, token):
        previews = self.hass.data.get(_DATA, {})
        expires_at, svg = previews.pop(token, (0, None))
        if not isinstance(svg, bytes) or expires_at < time.monotonic():
            raise web.HTTPNotFound
        previews[token] = (expires_at, svg)
        return web.Response(
            body=svg,
            content_type="image/svg+xml",
            headers={"Cache-Control": "no-store"},
        )


def async_store_preview(hass, svg: str) -> str:
    """Store one rendered SVG and return its authenticated, ephemeral URL."""
    if not hass.data.get(_VIEW):
        hass.http.register_view(GeoJSONPreviewView(hass))
        hass.data[_VIEW] = True
    now = time.monotonic()
    previews = hass.data.setdefault(_DATA, {})
    fresh = {
        token: value
        for token, value in previews.items()
        if isinstance(value, tuple) and value[0] >= now
    }
    previews.clear()
    previews.update(fresh)
    token = secrets.token_urlsafe(24)
    previews[token] = (now + _MAX_AGE_SECONDS, svg.encode())
    return f"/api/virtual_layer/geojson_preview/{token}.svg"
