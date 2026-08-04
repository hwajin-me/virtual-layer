"""Regression tests for non-mergeable media and safety sensor helpers."""

from unittest.mock import AsyncMock, Mock

import pytest

from homeassistant.components.image import ImageEntity
from homeassistant.const import ATTR_ENTITY_ID, CONF_NAME
from homeassistant.helpers.template import Template

from custom_components.virtual_layer.config_flow import (
    InvalidEntityReference,
    _reference_entity_defaults,
)
from custom_components.virtual_layer.const import ATTR_UNIQUE_ID, CONF_INITIAL_VALUE
from custom_components.virtual_layer.image import IMAGE_SCHEMA, VirtualImage


pytestmark = pytest.mark.unit


async def test_virtual_image_alias_returns_source_image(hass):
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

    assert isinstance(entity, ImageEntity)
    assert await entity.async_image() == b"image-bytes"
    assert entity.image_last_updated is not None
    source.async_image.assert_awaited_once_with()
    entity.async_write_ha_state.assert_called_once()


async def test_virtual_image_reads_configured_file(hass, tmp_path):
    image_path = tmp_path / "snapshot.jpg"
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
