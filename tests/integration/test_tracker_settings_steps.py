"""Independent tracker details and importing existing Dawarich connections."""

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.virtual_layer import config_flow as flow

pytestmark = pytest.mark.integration


def connection(hass, name="Existing", **data):
    entry = MockConfigEntry(
        domain="dawarich",
        title=name,
        data={
            "host": "dawarich.example:3000",
            "ssl": True,
            "api_key": "existing-key",
            **data,
        },
    )
    entry.add_to_hass(hass)
    return entry


@pytest.mark.parametrize(
    "ssl,expected",
    [(True, "https://dawarich.example:3000"), (False, "http://dawarich.example:3000")],
)
async def test_single_connection_prefills_separate_step(hass, ssl, expected):
    entry = connection(hass, ssl=ssl)
    handler = flow.VirtualFlowHandler()
    handler.hass = hass
    result = await handler.async_step_entity_source({"tracker_creation": "dawarich"})
    assert result["step_id"] == "tracker_settings"
    form = result["data_schema"]({})
    assert set(form) == {"dawarich_settings", "local_presence_settings"}
    assert form["dawarich_settings"]["dawarich_url"] == expected
    assert form["dawarich_settings"]["dawarich_api_key"] == "existing-key"
    form["dawarich_settings"]["dawarich_api_key"] = "edited-key"
    result = await handler.async_step_tracker_settings(form)
    assert result["step_id"] == "entity"
    assert "dawarich_settings" not in result["data_schema"]({})
    assert "local_presence_settings" not in result["data_schema"]({})
    assert entry.data["api_key"] == "existing-key"
    assert handler._entity_defaults["dawarich_api_key"] == "edited-key"


async def test_multiple_connections_require_selection_and_handle_removed_entry(hass):
    first = connection(hass, "First")
    second = connection(hass, "Second", host="other.example:80", api_key="other-key")
    handler = flow.VirtualFlowHandler()
    handler.hass = hass
    result = await handler.async_step_entity_source({"tracker_creation": "dawarich"})
    assert result["step_id"] == "dawarich_connection"
    assert "existing-key" not in str(result["data_schema"].schema)
    await hass.config_entries.async_remove(first.entry_id)
    result = await handler.async_step_dawarich_connection(
        {"dawarich_connection": first.entry_id}
    )
    assert result["errors"]
    result = await handler.async_step_dawarich_connection(
        {"dawarich_connection": second.entry_id}
    )
    assert result["step_id"] == "tracker_settings"
    assert (
        result["data_schema"]({})["dawarich_settings"]["dawarich_api_key"]
        == "other-key"
    )


async def test_existing_tracker_connection_is_not_overwritten(hass):
    connection(hass)
    handler = flow.VirtualFlowHandler()
    handler.hass = hass
    handler._entity_defaults = flow._tracker_creation_defaults(
        {"tracker_creation": "dawarich"}
    )
    handler._entity_defaults.update(
        {"dawarich_url": "http://saved.example", "dawarich_api_key": "saved-key"}
    )
    result = await handler._async_tracker_start(edit=True)
    assert (
        result["data_schema"]({})["dawarich_settings"]["dawarich_api_key"]
        == "saved-key"
    )


async def test_imported_connection_auth_error_stays_on_details_step(
    hass, aioclient_mock
):
    connection(hass)
    aioclient_mock.get("https://dawarich.example:3000/api/v1/points", status=401)
    handler = flow.VirtualFlowHandler()
    handler.hass = hass
    result = await handler.async_step_entity_source({"tracker_creation": "dawarich"})
    form = result["data_schema"]({})
    form["dawarich_settings"]["dawarich_test_connection"] = True
    result = await handler.async_step_tracker_settings(form)
    assert result["step_id"] == "tracker_settings"
    assert result["errors"] == {"dawarich_api_key": "dawarich_invalid_auth"}
    assert (
        result["data_schema"]({})["dawarich_settings"]["dawarich_url"]
        == "https://dawarich.example:3000"
    )
