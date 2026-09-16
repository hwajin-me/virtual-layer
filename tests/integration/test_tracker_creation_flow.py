"""Dedicated tracker creation paths use the normal managed entity lifecycle."""

import pytest
from unittest.mock import AsyncMock
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.virtual_layer import config_flow as flow
from custom_components.virtual_layer.const import (
    ATTR_DEVICES,
    ATTR_GROUP_NAME,
    COMPONENT_DOMAIN,
)

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("kind", ["dawarich", "wifi", "ble"])
@pytest.mark.parametrize("initial", [True, False])
async def test_tracker_creation_presets(hass, aioclient_mock, kind, initial):
    aioclient_mock.get("https://example.test/api/v1/points", json=[])
    aioclient_mock.get("https://example.test/api/v1/visits", json=[])
    if initial:
        manager = hass.config_entries.flow
        result = await manager.async_init(COMPONENT_DOMAIN, context={"source": "user"})
        result = await manager.async_configure(
            result["flow_id"],
            {
                ATTR_GROUP_NAME: "Tracking",
                flow.CONF_ADD_FIRST_ENTITY: True,
            },
        )
    else:
        entry = MockConfigEntry(
            domain=COMPONENT_DOMAIN,
            data={ATTR_GROUP_NAME: "Tracking"},
            options={ATTR_DEVICES: {}},
        )
        entry.add_to_hass(hass)
        manager = hass.config_entries.options
        result = await manager.async_init(
            entry.entry_id, data={flow.CONF_ACTION: flow.ACTION_ADD_ENTITY}
        )
    assert "tracker_creation" in result["data_schema"]({})
    result = await manager.async_configure(
        result["flow_id"],
        {
            "tracker_creation": kind,
            flow.CONF_REFERENCE_ENTITY_ID: ["binary_sensor.phone_wifi"]
            if kind == "wifi"
            else [],
        },
    )
    assert result["step_id"] == "tracker_settings"
    form = result["data_schema"]({})
    if kind == "dawarich":
        settings = form["dawarich_settings"]
        assert settings["dawarich_enabled"] is True
        settings.update(
            {"dawarich_url": "https://example.test", "dawarich_api_key": "test-key"}
        )
    else:
        settings = form["local_presence_settings"]
        assert settings["presence_enabled"] is True
        if kind == "wifi":
            assert settings["presence_wifi_entities"] == ["binary_sensor.phone_wifi"]
        else:
            settings["presence_ble_addresses"] = "AA:BB:CC:DD:EE:FF"
    result = await manager.async_configure(result["flow_id"], form)
    assert result["step_id"] == "entity", result
    form = flow._flatten_entity_form_sections(result["data_schema"]({}))
    assert "dawarich_settings" not in result["data_schema"]({})
    assert "local_presence_settings" not in result["data_schema"]({})
    form["entity_id"] = "device_tracker.test_tracker"
    form["device_name"] = "Tracking"
    result = await manager.async_configure(result["flow_id"], form)
    assert result["type"] == "create_entry", result
    options = result["options"] if initial else result["data"]
    device_key, entities = next(iter(options[ATTR_DEVICES].items()))
    entity = entities[0]
    assert entity["platform"] == "device_tracker"
    assert ("dawarich" if kind == "dawarich" else "local_presence") in entity
    reopened = flow._entity_schema(flow._entity_form_defaults(device_key, entity))({})
    assert flow._build_entity_config(reopened)[1].get("local_presence") == entity.get(
        "local_presence"
    )
    if initial:
        entry = result["result"]
    else:
        hass.config_entries.async_update_entry(entry, options=options)
    await hass.async_block_till_done()
    manager = hass.config_entries.options
    selection = flow._selection_key_for_entity(device_key, 0, entity)
    result = await manager.async_init(
        entry.entry_id, data={flow.CONF_ACTION: flow.ACTION_EDIT_ENTITY}
    )
    result = await manager.async_configure(
        result["flow_id"], {flow.CONF_ENTITY_KEY: selection}
    )
    result = await manager.async_configure(
        result["flow_id"], {flow.CONF_REFERENCE_ENTITY_ID: []}
    )
    assert result["step_id"] == "tracker_settings"
    form = result["data_schema"]({})
    if kind == "dawarich":
        assert form["dawarich_settings"]["dawarich_api_key"] == "test-key"
        form["dawarich_settings"]["dawarich_poll_interval"] = 180
    else:
        assert form["local_presence_settings"]["presence_enabled"] is True
        form["local_presence_settings"]["presence_ble_timeout"] = 180
    result = await manager.async_configure(result["flow_id"], form)
    assert result["step_id"] == "edit_entity", result
    result = await manager.async_configure(result["flow_id"], result["data_schema"]({}))
    assert result["type"] == "create_entry", result
    hass.config_entries.async_update_entry(entry, options=result["data"])
    saved = entry.options[ATTR_DEVICES][device_key][0]
    assert (
        saved.get("dawarich", saved.get("local_presence"))[
            "poll_interval" if kind == "dawarich" else "ble_timeout"
        ]
        == 180
    )
    result = await manager.async_init(
        entry.entry_id, data={flow.CONF_ACTION: flow.ACTION_DELETE_ENTITY}
    )
    result = await manager.async_configure(
        result["flow_id"], {flow.CONF_ENTITY_KEYS: [selection]}
    )
    assert result["type"] == "create_entry", result
    assert not any(result["data"][ATTR_DEVICES].values())
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()


async def test_invalid_tracker_source_can_be_corrected(hass):
    handler = flow.VirtualFlowHandler()
    handler.hass = hass
    result = await handler.async_step_entity_source(
        {
            "tracker_creation": "ble",
            flow.CONF_REFERENCE_ENTITY_ID: ["sensor.temperature"],
        }
    )
    assert result["errors"]
    corrected = result["data_schema"]({})
    assert corrected["tracker_creation"] == "ble"
    assert corrected[flow.CONF_REFERENCE_ENTITY_ID] == ["sensor.temperature"]
    corrected[flow.CONF_REFERENCE_ENTITY_ID] = []
    result = await handler.async_step_entity_source(corrected)
    assert result["step_id"] == "tracker_settings"


@pytest.mark.parametrize(
    "kind,changes,field",
    [
        (
            "wifi",
            {"presence_wifi_entities": ["sensor.phone_ssid"]},
            "presence_wifi_ssids",
        ),
        ("ble", {"presence_ble_addresses": "invalid"}, "presence_ble_addresses"),
        (
            "ble",
            {"presence_ble_addresses": "AA:BB:CC:DD:EE:FF", "presence_ble_sources": ""},
            "presence_ble_sources",
        ),
    ],
)
async def test_presence_errors_target_the_invalid_field(hass, kind, changes, field):
    handler = flow.VirtualFlowHandler()
    handler.hass = hass
    result = await handler.async_step_entity_source({"tracker_creation": kind})
    form = result["data_schema"]({})
    form["local_presence_settings"].update(changes)
    form = flow._flatten_entity_form_sections(form)
    form["device_name"] = "Phone"
    result = await handler.async_step_tracker_settings(form)
    assert result["errors"] == {field: "invalid_local_presence"}
    reopened = result["data_schema"]({})["local_presence_settings"]
    for name, value in changes.items():
        assert reopened[name] == value


async def test_retry_keeps_existing_target_device(hass):
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "Family"},
        options={ATTR_DEVICES: {"Phone": []}},
    )
    entry.add_to_hass(hass)
    manager = hass.config_entries.options
    result = await manager.async_init(
        entry.entry_id, data={flow.CONF_ACTION: flow.ACTION_ADD_ENTITY}
    )
    result = await manager.async_configure(
        result["flow_id"],
        {
            "tracker_creation": "ble",
            flow.CONF_REFERENCE_ENTITY_ID: ["sensor.invalid"],
            flow.CONF_TARGET_DEVICE_NAME: "Phone",
        },
    )
    assert result["errors"]
    defaults = result["data_schema"]({})
    assert defaults["tracker_creation"] == "ble"
    assert defaults[flow.CONF_TARGET_DEVICE_NAME] == "Phone"


@pytest.mark.parametrize("field,value", [
    ("dawarich_url", "https://example.test?api_key=private"),
    ("dawarich_api_key", ""),
    ("dawarich_poll_interval", 15.5),
    ("dawarich_poll_interval", 0),
    ("dawarich_history_limit", 101),
    ("dawarich_person", ["person.one", "person.two"]),
])
async def test_dawarich_invalid_input_stays_editable(hass, monkeypatch, field, value):
    handler = flow.VirtualFlowHandler()
    handler.hass = hass
    result = await handler.async_step_entity_source({"tracker_creation": "dawarich"})
    form = result["data_schema"]({})
    settings = form["dawarich_settings"]
    settings.update({"dawarich_url": "https://example.test", "dawarich_api_key": "test-key",
                     "dawarich_test_connection": True})
    valid = dict(settings)
    settings[field] = value
    fetch = AsyncMock()
    monkeypatch.setattr(flow.DawarichClient, "async_fetch", fetch)
    result = await handler.async_step_tracker_settings(form)
    assert result["step_id"] == "tracker_settings"
    assert result["errors"] == {field: "invalid_dawarich_config"}
    fetch.assert_not_awaited()
    section_schema = next(validator.schema for marker, validator in result["data_schema"].schema.items()
                          if marker.schema == "dawarich_settings")
    # A numeric value outside the selector range cannot validate through ({}),
    # but its suggestion must still be returned so the user can correct it.
    suggestions = {marker.schema: marker.description["suggested_value"]
                   for marker in section_schema.schema}
    assert suggestions[field] == value
    assert suggestions["dawarich_url"] == settings["dawarich_url"]
    assert suggestions["dawarich_api_key"] == settings["dawarich_api_key"]
    suggestions[field] = valid[field]
    result = await handler.async_step_tracker_settings({"dawarich_settings": suggestions})
    assert result["step_id"] == "entity", result
    assert result["errors"] == {}
    fetch.assert_awaited_once_with("", include_visit=False)


async def test_frontend_language_catalogs_load_for_selectors(hass):
    from homeassistant.helpers.translation import async_get_translations

    hass.config.language = "en"
    for language, expected in (("en", "Humidifier"), ("ko", "가습·제습기")):
        translated = await async_get_translations(hass, language, "selector", {COMPONENT_DOMAIN})
        prefix = f"component.{COMPONENT_DOMAIN}.selector."
        assert translated[prefix + "entity_type.options.humidifier"] == expected
        assert translated[prefix + "target_device.options.__new_device__"]
        assert translated[prefix + "dawarich_connection.options.manual"]
