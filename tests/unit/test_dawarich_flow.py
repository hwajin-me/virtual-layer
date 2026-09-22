"""UI-only Dawarich configuration and edit round trips."""

import json
from unittest.mock import AsyncMock

import pytest
from homeassistant.const import CONF_PLATFORM

from custom_components.virtual_layer import config_flow as flow
from custom_components.virtual_layer.cfg import _diagnostic_configuration
from custom_components.virtual_layer.const import CONF_DAWARICH
from custom_components.virtual_layer.dawarich import DawarichError
from custom_components.virtual_layer.device_tracker import (
    DEVICE_TRACKER_SCHEMA,
    validate_domain_options,
)

pytestmark = pytest.mark.unit


def form():
    result = flow._entity_schema(
        {CONF_PLATFORM: "device_tracker", flow.CONF_ENTITY_NAME: "Dawarich"}
    )({})
    result.update(
        {flow.CONF_DEVICE_NAME: "Family", "entity_id": "device_tracker.dawarich"}
    )
    result[flow.CONF_DAWARICH_SETTINGS].update(
        {
            "dawarich_enabled": True,
            "dawarich_url": "https://example.test",
            "dawarich_api_key": "private-test-key",
            "dawarich_member": "alex@example.test",
            "dawarich_person": ["person.alex"],
            "dawarich_poll_interval": 120,
            "dawarich_verify_ssl": True,
            "dawarich_request_timeout": 30,
            "dawarich_include_visits": False,
            "dawarich_visit_lookback_days": 90,
        }
    )
    return result


def build(value):
    return flow._build_entity_config(
        value, DEVICE_TRACKER_SCHEMA, validate_domain_options
    )[1]


def test_create_edit_clear_and_disable():
    entity = build(form())
    assert entity[CONF_DAWARICH]["member"] == "alex@example.test"
    defaults = flow._entity_form_defaults("Family", entity)
    reopened = flow._entity_schema(defaults)({})
    section = reopened[flow.CONF_DAWARICH_SETTINGS]
    assert section["dawarich_person"] == ["person.alex"]
    assert section["dawarich_api_key"] == "private-test-key"
    assert section["dawarich_poll_interval"] == 120
    assert section["dawarich_verify_ssl"] is True
    assert section["dawarich_request_timeout"] == 30
    assert section["dawarich_include_visits"] is False
    assert section["dawarich_visit_lookback_days"] == 90
    assert build(reopened)[CONF_DAWARICH] == entity[CONF_DAWARICH]
    section.update({"dawarich_member": "", "dawarich_person": []})
    assert build(reopened)[CONF_DAWARICH]["person_entity_id"] == ""
    section["dawarich_enabled"] = False
    assert CONF_DAWARICH not in build(reopened)


@pytest.mark.parametrize(
    "field,value",
    [
        ("dawarich_poll_interval", 15.5),
        ("dawarich_api_key", ""),
        ("dawarich_url", "https://example.test?api_key=secret"),
        ("dawarich_person", ["person.alex", "person.other"]),
        ("dawarich_request_timeout", 61),
        ("dawarich_visit_lookback_days", 366),
    ],
)
def test_invalid_fields_return_visible_flow_error(field, value):
    data = form()
    data[flow.CONF_DAWARICH_SETTINGS][field] = value
    with pytest.raises(flow.InvalidFieldValue) as error:
        build(data)
    assert error.value.error_code == "invalid_dawarich_config"
    assert error.value.field_name == field


async def test_optional_connection_test_works_with_sectioned_form(hass, monkeypatch):
    fetch = AsyncMock()
    monkeypatch.setattr(flow.DawarichClient, "async_fetch", fetch)
    data = form()
    await flow._async_build_entity_config(hass, data)
    fetch.assert_not_awaited()
    data[flow.CONF_DAWARICH_SETTINGS]["dawarich_test_connection"] = True
    await flow._async_build_entity_config(hass, data)
    fetch.assert_awaited_once_with("", include_visit=False)
    fetch.side_effect = DawarichError("invalid_auth")
    with pytest.raises(flow.InvalidFieldValue) as error:
        await flow._async_build_entity_config(hass, data)
    assert error.value.field_name == "dawarich_api_key"
    assert error.value.error_code == "dawarich_invalid_auth"


def test_diagnostic_configuration_does_not_publish_key_url_or_member():
    output = _diagnostic_configuration(build(form()), "device_tracker")
    assert output[CONF_DAWARICH]["configured"] is True
    for private in ("private-test-key", "example.test", "person.alex"):
        assert private not in str(output)


def test_diagnostic_configuration_is_bounded_for_recorder():
    entity = build(form())
    entity["command_actions"] = {"turn_on": "x" * 20_000}
    output = _diagnostic_configuration(entity, "device_tracker")
    assert output["configuration_truncated"] is True
    assert len(json.dumps(output).encode()) < 10 * 1024


def test_polygon_can_use_dawarich_without_local_trackers():
    from test_polygon_config_flow import GEOJSON

    data = form()
    data[flow.CONF_DOMAIN_SETTINGS][flow.CONF_POLYGON_GEOJSON_JSON] = GEOJSON
    entity = build(data)
    assert entity[CONF_DAWARICH]
    assert entity["polygonal_zone"]
