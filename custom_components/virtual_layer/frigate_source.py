"""Read optional Frigate metadata without importing or contacting Frigate."""

from collections.abc import Mapping
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.template import Template, TemplateError
from homeassistant.util import slugify


def _mapping(value):
    return value if isinstance(value, Mapping) else {}


def _filtered_rtsp(value):
    if not isinstance(value, str) or any(char.isspace() for char in value):
        return None
    try:
        url = urlsplit(value)
        if url.scheme not in {"rtsp", "rtsps"} or not url.hostname or not url.path.strip("/"):
            return None
        # Validate the port even though the original authority is preserved.
        _ = url.port
        query = [(key, val) for key, val in parse_qsl(url.query, keep_blank_values=True)
                 if key not in {"video", "audio"}]
        query.extend((("video", "h264"), ("audio", "all")))
        return urlunsplit((url.scheme, url.netloc, url.path, urlencode(query), ""))
    except ValueError:
        return None


def frigate_camera_stream_url(hass, entity_id: str) -> str | None:
    """Resolve one verified Frigate camera using its registry/config metadata."""
    if not isinstance(entity_id, str) or not entity_id.startswith("camera."):
        return None
    entity = er.async_get(hass).async_get(entity_id)
    if entity is None or entity.platform != "frigate" or not entity.config_entry_id:
        return None
    entry = hass.config_entries.async_get_entry(entity.config_entry_id)
    if entry is None or entry.domain != "frigate":
        return None
    device = dr.async_get(hass).async_get(entity.device_id) if entity.device_id else None
    runtime_data = _mapping(_mapping(hass.data.get("frigate")).get(entry.entry_id))
    config = _mapping(runtime_data.get("config"))
    cameras = _mapping(config.get("cameras"))

    # Frigate IDs preserve the configured camera name even after an HA rename.
    prefix = f"{entry.entry_id}:camera:"
    name = entity.unique_id.removeprefix(prefix) if entity.unique_id.startswith(prefix) else None
    state = hass.states.get(entity_id)
    if not name and state is not None:
        name = state.attributes.get("camera_name")
    if not isinstance(name, str) or not name:
        matches = [camera_name for camera_name in cameras if isinstance(camera_name, str)
                   and device is not None
                   and ("frigate", f"{entry.entry_id}:{slugify(camera_name)}") in device.identifiers]
        name = matches[0] if len(matches) == 1 else None
    if not name:
        return None

    camera_config = _mapping(cameras.get(name))
    component = hass.data.get("camera")
    get_entity = getattr(component, "get_entity", None)
    source = get_entity(entity_id) if callable(get_entity) else None
    # This is Frigate's already-rendered RTSP override, when the platform is
    # loaded. It may use a separate go2rtc host, credentials or a custom port.
    resolved = getattr(source, "_stream_source", None)
    if isinstance(resolved, str) and resolved.strip():
        return _filtered_rtsp(resolved.strip())

    override = _mapping(entry.options).get("rtsp_url_template")
    if isinstance(override, str) and override.strip():
        try:
            values = dict(camera_config)
            values.setdefault("name", name)
            rendered = Template(override, hass).async_render(values, parse_result=False)
            return _filtered_rtsp(rendered.strip())
        except (TemplateError, TypeError, ValueError):
            return None

    stream_name = name
    streams = _mapping(_mapping(config.get("go2rtc")).get("streams"))
    live = _mapping(_mapping(camera_config.get("live")).get("streams"))
    candidates = [value for value in live.values() if isinstance(value, str) and value in streams]
    if candidates:
        stream_name = name if name in candidates else candidates[0]
    elif streams and name not in streams:
        return None

    server_url = _mapping(entry.data).get("url")
    if not server_url and device is not None:
        server_url = device.configuration_url
    if not isinstance(server_url, str):
        return None
    try:
        parsed = urlsplit(server_url)
        host = parsed.hostname
        if parsed.scheme not in {"http", "https"} or not host:
            return None
        host = f"[{host}]" if ":" in host else host
        return _filtered_rtsp(f"rtsp://{host}:8554/{quote(stream_name, safe='')}")
    except ValueError:
        return None
