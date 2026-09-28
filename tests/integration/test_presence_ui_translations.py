"""Verify the selectors actually served by HA have localized choices and help."""

import pytest
from homeassistant.helpers.translation import async_get_translations

from tests.integration.test_presence_fusion_ha import setup


async def test_motion_mode_options_save_and_reopen(hass):
    entry, _ = await setup(hass)
    manager = hass.config_entries.options
    result = await manager.async_init(entry.entry_id)
    result = await manager.async_configure(result["flow_id"], {"action": "settings"})
    result = await manager.async_configure(result["flow_id"], {
        "boundary_motion_mode": "presence", "entering_hold_s": 30,
        "leaving_hold_s": 60, "gps_max_accuracy_m": 40,
    })
    assert not result.get("errors")
    result = await manager.async_configure(result["flow_id"], {"action": "save"})
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    result = await manager.async_init(entry.entry_id)
    result = await manager.async_configure(result["flow_id"], {"action": "settings"})
    marker = next(k for k in result["data_schema"].schema if k.schema == "boundary_motion_mode")
    assert marker.default() == "presence"
    for key, expected in {"entering_hold_s": 30, "leaving_hold_s": 60, "gps_max_accuracy_m": 40}.items():
        marker = next(k for k in result["data_schema"].schema if k.schema == key)
        assert marker.default() == expected
    manager.async_abort(result["flow_id"])
    await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize("language", ["en", "ko"])
async def test_presence_and_area_forms_are_localized(hass, language):
    hass.config.language = language
    entry, _ = await setup(hass)
    translated = {}
    for category in ("options", "selector"):
        translated.update(
            await async_get_translations(hass, language, category, {"virtual_layer"})
        )

    def check(result):
        step = result["step_id"]
        prefix = f"component.virtual_layer.options.step.{step}."
        assert translated[prefix + "title"]
        assert translated[prefix + "description"]
        for marker, validator in result["data_schema"].schema.items():
            field = marker.schema
            assert translated[prefix + "data." + field]
            assert translated[prefix + "data_description." + field]
            config = getattr(validator, "config", {})
            options = config.get("options", [])
            static = [value for value in options if isinstance(value, str)]
            if static:
                key = config["translation_key"]
                for value in static:
                    label = translated[
                        f"component.virtual_layer.selector.{key}.options.{value}"
                    ]
                    assert label and label != value

    manager = hass.config_entries.options
    result = await manager.async_init(entry.entry_id)
    check(result)
    for data in [
        {"action": "add"},
        {"name": "Phone", "priority": 10, "candidate": True},
        {"action": "add"},
    ]:
        result = await manager.async_configure(result["flow_id"], data)
        check(result)
    assert result["step_id"] == "fusion_source"
    manager.async_abort(result["flow_id"])

    for action in ("settings", "metadata", "zones"):
        result = await manager.async_init(entry.entry_id)
        result = await manager.async_configure(result["flow_id"], {"action": action})
        check(result)
        if action == "zones":
            home = next(
                v for k, v in result["data_schema"].schema.items() if k.schema == "home"
            )
            assert home.config["options"][0] == {
                "value": "",
                "label": translated[
                    "component.virtual_layer.selector.fusion_home.options.default"
                ],
            }
        manager.async_abort(result["flow_id"])
    from tests.integration.test_geojson_group import create_group

    group = await create_group(hass)
    result = await manager.async_init(group.entry_id)
    check(result)
    result = await manager.async_configure(result["flow_id"], {"action": "add"})
    check(result)
    manager.async_abort(result["flow_id"])
    assert await hass.config_entries.async_unload(group.entry_id)
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize(
    "values,field,code",
    [
        (
            {"configuration_url": "ftp://example.test"},
            "configuration_url",
            "fusion_url",
        ),
        ({"parent_device": "missing"}, "parent_device", "fusion_parent"),
    ],
)
async def test_device_details_errors_describe_the_actual_field(
    hass, values, field, code
):
    entry, _ = await setup(hass)
    manager = hass.config_entries.options
    result = await manager.async_init(entry.entry_id)
    result = await manager.async_configure(result["flow_id"], {"action": "metadata"})
    result = await manager.async_configure(result["flow_id"], values)
    assert result["errors"][field] == code
    assert await hass.config_entries.async_unload(entry.entry_id)
