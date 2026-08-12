"""Regression tests for non-mergeable media and safety sensor helpers."""

from unittest.mock import AsyncMock, Mock

import pytest
from aiohttp import ClientConnectionError
from homeassistant.components.image import ImageEntity
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_FRIENDLY_NAME,
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    CONF_NAME,
)
from homeassistant.helpers.template import Template

from custom_components.virtual_layer import image as image_platform
from custom_components.virtual_layer.camera import CAMERA_SCHEMA, VirtualCamera
from custom_components.virtual_layer.config_flow import (
    InvalidEntityReference,
    _reference_entity_defaults,
)
from custom_components.virtual_layer.const import (
    ATTR_UNIQUE_ID,
    CONF_INITIAL_VALUE,
    CONF_POLYGON_GEOJSON,
    CONF_POLYGONAL_ZONE,
    CONF_SOURCE_ENTITIES,
)
from custom_components.virtual_layer.image import IMAGE_SCHEMA, VirtualImage

pytestmark = pytest.mark.unit


async def test_virtual_image_alias_returns_source_image(hass):
    source = Mock()
    source.async_image = AsyncMock(return_value=b"image-bytes")
    source.content_type = "image/png"
    image_component = Mock()
    image_component.get_entity.return_value = source
    hass.data["image"] = image_component

    entity = VirtualImage(IMAGE_SCHEMA({
        CONF_NAME: "Front Door Image",
        ATTR_ENTITY_ID: "image.front_door_alias",
        ATTR_UNIQUE_ID: "front_door_alias",
        CONF_INITIAL_VALUE: "unknown",
        "source_entity": "image.front_door",
    }), hass, False)
    entity.hass = hass
    entity._create_state(entity._config)
    entity.async_write_ha_state = Mock()

    assert isinstance(entity, ImageEntity)
    assert await entity.async_image() == b"image-bytes"
    assert entity.image_last_updated is not None
    assert entity.state_attributes["content_type"] == "image/png"
    source.async_image.assert_awaited_once_with()
    entity.async_write_ha_state.assert_called_once()


async def test_virtual_image_reads_configured_file(hass, tmp_path):
    image_path = tmp_path / "snapshot.png"
    image_path.write_bytes(b"jpeg-bytes")
    entity = VirtualImage(IMAGE_SCHEMA({
        CONF_NAME: "Snapshot Image",
        ATTR_ENTITY_ID: "image.snapshot",
        ATTR_UNIQUE_ID: "snapshot",
        CONF_INITIAL_VALUE: "unknown",
        "image_path": str(image_path),
    }), hass, False)
    entity.hass = hass
    entity._create_state(entity._config)
    entity.async_write_ha_state = Mock()

    assert await entity.async_image() == b"jpeg-bytes"
    assert entity.image_last_updated is not None
    assert entity.state_attributes["content_type"] == "image/png"


async def test_virtual_image_renders_polygon_map_svg(hass):
    hass.states.async_set(
        "device_tracker.family_polygon",
        "Home",
        {
            ATTR_FRIENDLY_NAME: "Family <Phone>",
            ATTR_LATITUDE: 37.5,
            ATTR_LONGITUDE: 127.0,
        },
    )
    entity = VirtualImage(IMAGE_SCHEMA({
        CONF_NAME: "Family Polygon Map",
        ATTR_ENTITY_ID: "image.family_polygon_map",
        ATTR_UNIQUE_ID: "family_polygon_map",
        CONF_INITIAL_VALUE: "unknown",
        CONF_SOURCE_ENTITIES: ["device_tracker.family_polygon"],
        CONF_POLYGONAL_ZONE: {
            CONF_POLYGON_GEOJSON: {
                "type": "Feature",
                "properties": {"name": "Home"},
                "geometry": {
                    "type": "MultiPolygon",
                    "coordinates": [[[
                        [126.9, 37.4],
                        [127.1, 37.4],
                        [127.1, 37.6],
                        [126.9, 37.6],
                        [126.9, 37.4],
                    ]]],
                },
            },
        },
    }), hass, False)
    entity.hass = hass
    entity._create_state(entity._config)
    entity.async_write_ha_state = Mock()

    image = await entity.async_image()

    assert image is not None
    assert image.startswith(b"<svg ")
    assert b"Home" in image
    assert b'data-entity-id="device_tracker.family_polygon"' in image
    assert b"Family &lt;Phone&gt;" in image
    assert entity.image_last_updated is not None
    assert entity.state_attributes["content_type"] == "image/svg+xml"
    assert entity.state_attributes["image_type"] == "polygon_map"

    first_updated = entity.image_last_updated
    assert await entity.async_image() == image
    assert entity.image_last_updated == first_updated
    entity.async_write_ha_state.assert_called_once()


def test_virtual_polygon_map_ignores_overflowing_source_coordinates(hass):
    hass.states.async_set(
        "device_tracker.damaged",
        "not_home",
        {
            ATTR_LATITUDE: 10**10000,
            ATTR_LONGITUDE: 127.0,
        },
    )
    entity = VirtualImage(IMAGE_SCHEMA({
        CONF_NAME: "Damaged Polygon Map",
        ATTR_ENTITY_ID: "image.damaged_polygon_map",
        ATTR_UNIQUE_ID: "damaged_polygon_map",
        CONF_INITIAL_VALUE: "unknown",
        CONF_SOURCE_ENTITIES: ["device_tracker.damaged"],
        CONF_POLYGONAL_ZONE: {CONF_POLYGON_GEOJSON: {}},
    }), hass, False)
    entity.hass = hass

    assert entity._polygon_markers() == []


async def test_virtual_polygon_map_keeps_last_complete_zones_after_partial_reload(
    hass,
    monkeypatch,
):
    def zone(name):
        return {
            "name": name,
            "priority": 0,
            "polygons": [{
                "outer": [
                    (126.9, 37.4),
                    (127.1, 37.4),
                    (127.1, 37.6),
                    (126.9, 37.6),
                    (126.9, 37.4),
                ],
                "holes": [],
            }],
            "area": 0.04,
            "properties": {},
        }

    initial_zone = zone("Initial zone")
    updated_zone = zone("Updated zone")
    monkeypatch.setattr(
        image_platform,
        "load_polygon_zones",
        AsyncMock(side_effect=[
            ([initial_zone], []),
            ([updated_zone], ["secondary.geojson: offline"]),
            ([updated_zone], []),
            ([updated_zone], []),
        ]),
    )
    entity = VirtualImage(IMAGE_SCHEMA({
        CONF_NAME: "Cached Polygon Map",
        ATTR_ENTITY_ID: "image.cached_polygon_map",
        ATTR_UNIQUE_ID: "cached_polygon_map",
        CONF_INITIAL_VALUE: "unknown",
        CONF_POLYGONAL_ZONE: {CONF_POLYGON_GEOJSON: {}},
    }), hass, False)
    entity.hass = hass
    entity._create_state(entity._config)
    entity.async_write_ha_state = Mock()

    assert b"Initial zone" in await entity.async_image()
    retained = await entity.async_image()
    assert b"Initial zone" in retained
    assert b"Updated zone" not in retained
    assert entity.state_attributes["polygon_map_error"] == "secondary.geojson: offline"
    assert entity.state_attributes["polygon_zones"] == ["Initial zone"]

    refreshed = await entity.async_image()
    assert b"Updated zone" in refreshed
    assert entity.state_attributes["polygon_map_error"] is None
    assert entity.state_attributes["polygon_zones"] == ["Updated zone"]

    monkeypatch.setattr(
        image_platform,
        "render_polygon_map_svg",
        Mock(side_effect=ValueError("invalid map projection")),
    )
    assert await entity.async_image() is None
    assert entity.state_attributes["polygon_map_error"] == "invalid map projection"
    assert entity.state_attributes["polygon_zones"] == ["Updated zone"]


async def test_virtual_image_source_change_invalidates_once_before_next_fetch(hass):
    source = Mock()
    source.async_image = AsyncMock(return_value=b"image-bytes")
    image_component = Mock()
    image_component.get_entity.return_value = source
    hass.data["image"] = image_component
    entity = VirtualImage(IMAGE_SCHEMA({
        CONF_NAME: "Front Door Image",
        ATTR_ENTITY_ID: "image.front_door_alias",
        ATTR_UNIQUE_ID: "front_door_alias",
        CONF_INITIAL_VALUE: "unknown",
        "source_entity": "image.front_door",
    }), hass, False)
    entity.hass = hass
    entity._create_state(entity._config)
    entity.async_write_ha_state = Mock()
    entity.async_schedule_update_ha_state = Mock()

    await entity.async_image()
    entity._async_image_source_changed(None)
    invalidated_at = entity.image_last_updated
    await entity.async_image()

    assert entity.image_last_updated == invalidated_at
    assert entity.async_write_ha_state.call_count == 1
    entity.async_schedule_update_ha_state.assert_called_once()


async def test_virtual_image_source_transport_error_returns_no_image(hass):
    source = Mock()
    source.async_image = AsyncMock(side_effect=ClientConnectionError("offline"))
    image_component = Mock()
    image_component.get_entity.return_value = source
    hass.data["image"] = image_component
    entity = VirtualImage(IMAGE_SCHEMA({
        CONF_NAME: "Offline Image",
        ATTR_ENTITY_ID: "image.offline_alias",
        ATTR_UNIQUE_ID: "offline_alias",
        CONF_INITIAL_VALUE: "unknown",
        "source_entity": "image.offline",
    }), hass, False)
    entity.hass = hass
    entity._create_state(entity._config)

    assert await entity.async_image() is None
    assert entity.image_last_updated is None


async def test_virtual_camera_alias_transport_errors_return_no_media(hass):
    source = Mock()
    source.async_camera_image = AsyncMock(
        side_effect=ClientConnectionError("offline"),
    )
    source.stream_source = AsyncMock(side_effect=ClientConnectionError("offline"))
    camera_component = Mock()
    camera_component.get_entity.return_value = source
    hass.data["camera"] = camera_component
    entity = VirtualCamera(CAMERA_SCHEMA({
        CONF_NAME: "Offline Camera Alias",
        ATTR_ENTITY_ID: "camera.offline_alias",
        CONF_INITIAL_VALUE: "on",
        "source_entity": "camera.offline",
    }), False)
    entity.hass = hass
    entity._create_state(entity._config)

    assert await entity.async_camera_image() is None
    assert await entity.stream_source() is None


def test_media_entities_cannot_be_merged(hass):
    hass.states.async_set("image.one", "unknown")
    hass.states.async_set("image.two", "unknown")
    hass.states.async_set("camera.one", "on")

    with pytest.raises(InvalidEntityReference):
        _reference_entity_defaults(hass, ["image.one", "image.two"])
    with pytest.raises(InvalidEntityReference):
        _reference_entity_defaults(hass, ["camera.one", "image.one"])


@pytest.mark.parametrize("device_class", ["smoke", "moisture", "gas"])
async def test_alarm_sensor_helper_uses_any_active_source(hass, device_class):
    hass.states.async_set(
        f"binary_sensor.{device_class}_one",
        "on",
        {"device_class": device_class},
    )
    hass.states.async_set(
        f"binary_sensor.{device_class}_two",
        "off",
        {"device_class": device_class},
    )

    defaults = _reference_entity_defaults(hass, [
        f"binary_sensor.{device_class}_one",
        f"binary_sensor.{device_class}_two",
    ])

    assert defaults[CONF_INITIAL_VALUE] == "on"
    assert " > 0 }}" in defaults["value_template"]
    assert " and " not in defaults["value_template"]
    template = Template(defaults["value_template"], hass)
    assert template.async_render(
        variables={
            f"{device_class}_one": "on",
            f"{device_class}_two": "off",
        },
        parse_result=False,
    ) == "True"
