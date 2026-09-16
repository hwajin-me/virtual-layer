"""Frigate-only RTSP defaults, including renamed and unloaded cameras."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.template import Template
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.virtual_layer.frigate_source import frigate_camera_stream_url
from custom_components.virtual_layer.config_flow import (
    CONF_NATIVE_VALUE_TEMPLATES, _entity_schema, _flatten_entity_form_sections,
    _prefill_frigate_camera_stream, _reference_entity_defaults,
    _reference_edit_defaults, _auto_helper_profile,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def frigate_source(hass):
    entry = MockConfigEntry(domain="frigate", data={"url": "https://frigate.example.test:8971"})
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("frigate", f"{entry.entry_id}:camera1")},
        name="User-renamed Bedroom", configuration_url="https://frigate.example.test/#camera1",
    )
    entity = er.async_get(hass).async_get_or_create(
        "camera", "frigate", f"{entry.entry_id}:camera:camera1",
        config_entry=entry, device_id=device.id, suggested_object_id="renamed_bedroom",
    )
    hass.states.async_set(entity.entity_id, "idle", {"camera_name": "camera1"})
    return entry, entity, device


def render(hass, defaults):
    return Template(defaults[CONF_NATIVE_VALUE_TEMPLATES]["stream_source"], hass).async_render()


async def test_frigate_registry_name_and_server_default(hass, frigate_source):
    _, entity, _ = frigate_source
    url = "rtsp://frigate.example.test:8554/camera1?video=h264&audio=all"
    assert frigate_camera_stream_url(hass, entity.entity_id) == url
    defaults = _reference_entity_defaults(hass, [entity.entity_id])
    assert render(hass, defaults) == url
    hass.states.async_remove(entity.entity_id)
    assert frigate_camera_stream_url(hass, entity.entity_id) == url


@pytest.mark.parametrize("source_url,expected", [
    ("rtsp://go2rtc.myong.us:8554/camera1?video=all&audio=aac", "rtsp://go2rtc.myong.us:8554/camera1?video=h264&audio=all"),
    ("rtsps://user:secret@[::1]:9554/live?token=abc&video=h265", "rtsps://user:secret@[::1]:9554/live?token=abc&video=h264&audio=all"),
])
async def test_frigate_uses_loaded_stream_override(hass, frigate_source, source_url, expected):
    _, entity, _ = frigate_source
    hass.data["camera"] = Mock(get_entity=Mock(return_value=SimpleNamespace(_stream_source=source_url)))
    assert frigate_camera_stream_url(hass, entity.entity_id) == expected


async def test_frigate_stored_override_and_live_stream_mapping(hass, frigate_source):
    entry, entity, _ = frigate_source
    hass.config_entries.async_update_entry(entry, options={
        "rtsp_url_template": "rtsp://go2rtc.myong.us:8554/{{ name }}?video=all",
    })
    assert frigate_camera_stream_url(hass, entity.entity_id) == "rtsp://go2rtc.myong.us:8554/camera1?video=h264&audio=all"
    hass.config_entries.async_update_entry(entry, options={})
    hass.data["frigate"] = {entry.entry_id: {"config": {
        "cameras": {"camera1": {"live": {"streams": {"High": "front_main"}}}},
        "go2rtc": {"streams": {"front_main": []}},
    }}}
    assert frigate_camera_stream_url(hass, entity.entity_id).endswith("/front_main?video=h264&audio=all")


async def test_frigate_device_identifiers_recover_missing_entity_metadata(hass, frigate_source):
    _, entity, _ = frigate_source
    er.async_get(hass).async_update_entity(entity.entity_id, new_unique_id="legacy-id")
    hass.states.async_remove(entity.entity_id)
    entry = hass.config_entries.async_get_entry(entity.config_entry_id)
    hass.data["frigate"] = {entry.entry_id: {"config": {"cameras": {"camera1": {}}}}}
    assert frigate_camera_stream_url(hass, entity.entity_id).endswith("/camera1?video=h264&audio=all")


@pytest.mark.parametrize("value", [None, [], "bad", {"config": []}, {"config": {"cameras": [], "go2rtc": []}}])
async def test_malformed_frigate_runtime_is_isolated(hass, frigate_source, value):
    entry, entity, _ = frigate_source
    hass.data["frigate"] = {entry.entry_id: value}
    assert frigate_camera_stream_url(hass, entity.entity_id).endswith("/camera1?video=h264&audio=all")


@pytest.mark.parametrize("override", ["{{ undefined_value.missing }}", "http://not-rtsp/live", "rtsp://host:invalid/live"])
async def test_bad_explicit_override_does_not_invent_another_server(hass, frigate_source, override):
    entry, entity, _ = frigate_source
    hass.config_entries.async_update_entry(entry, options={"rtsp_url_template": override})
    assert frigate_camera_stream_url(hass, entity.entity_id) is None


@pytest.mark.parametrize("value", ["", "   ", None])
async def test_blank_stream_editor_gets_frigate_default(hass, frigate_source, value):
    _, entity, _ = frigate_source
    defaults = {"platform": "camera", "source_entities_text": entity.entity_id,
                CONF_NATIVE_VALUE_TEMPLATES: {"stream_source": value}}
    filled = _prefill_frigate_camera_stream(hass, defaults)
    assert "video=h264&audio=all" in render(hass, filled)
    schema_defaults = _flatten_entity_form_sections(_entity_schema(defaults, hass=hass)({}))
    assert render(hass, schema_defaults) == render(hass, filled)
    assert defaults[CONF_NATIVE_VALUE_TEMPLATES]["stream_source"] == value


@pytest.mark.parametrize("value", ["{{ 'rtsp://custom/live' }}", "{{ none }}", "{{ states('input_text.stream') }}"])
async def test_explicit_templates_are_preserved(hass, frigate_source, value):
    _, entity, _ = frigate_source
    defaults = {"platform": "camera", "source_entities_text": [entity.entity_id],
                CONF_NATIVE_VALUE_TEMPLATES: {"stream_source": value}}
    assert _prefill_frigate_camera_stream(hass, defaults) == defaults


async def test_legacy_url_and_non_frigate_camera_are_unchanged(hass, frigate_source):
    _, entity, _ = frigate_source
    defaults = {"platform": "camera", "source_entities_text": [entity.entity_id],
                "domain_options_json": json.dumps({"stream_source": "rtsp://custom/live"})}
    assert _prefill_frigate_camera_stream(hass, defaults) == defaults
    fake = er.async_get(hass).async_get_or_create("camera", "generic", "fake", suggested_object_id="frigate_fake")
    hass.states.async_set(fake.entity_id, "idle", {"camera_name": "camera1", "manufacturer": "Frigate"})
    assert frigate_camera_stream_url(hass, fake.entity_id) is None
    defaults = {"platform": "camera", "source_entities_text": [fake.entity_id]}
    assert _prefill_frigate_camera_stream(hass, defaults) == defaults


async def test_frigate_helpers_respect_edit_policies(hass, frigate_source):
    entry, entity, _ = frigate_source
    old = _reference_entity_defaults(hass, [entity.entity_id])
    custom = {**old, CONF_NATIVE_VALUE_TEMPLATES: {**old[CONF_NATIVE_VALUE_TEMPLATES], "stream_source": "{{ 'rtsp://custom/live' }}"}}
    hass.config_entries.async_update_entry(entry, options={"rtsp_url_template": "rtsp://new-host/{{ name }}"})
    new = _reference_entity_defaults(hass, [entity.entity_id])
    assert render(hass, _reference_edit_defaults(old, new, _auto_helper_profile(old))) == render(hass, new)
    assert render(hass, _reference_edit_defaults(custom, new, _auto_helper_profile(old))) == "rtsp://custom/live"
    assert render(hass, _reference_edit_defaults(old, new)) == render(hass, old)
    assert render(hass, _reference_edit_defaults(custom, new, force_template_helper=True)) == render(hass, new)
