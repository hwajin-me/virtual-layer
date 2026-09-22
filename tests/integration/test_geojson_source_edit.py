"""GeoJSON editor clearing uses actual HA form validation and persistence."""

import json
from contextlib import asynccontextmanager

import pytest
from aiohttp import web

from custom_components.virtual_layer.geojson_catalog import (
    GeoJSONCatalog,
    async_get_catalog,
)
from custom_components.virtual_layer.polygon import find_polygon_zone
from tests.integration.test_geojson_catalog import document
from tests.integration.test_geojson_group import create_group

pytestmark = pytest.mark.integration


@asynccontextmanager
async def source(hass, tmp_path, kind):
    payload = document()
    payload["properties"] = {"Name": "Home"}
    if kind == "file":
        path = tmp_path / "areas.json"
        hass.config.allowlist_external_dirs.add(str(tmp_path))
        await hass.async_add_executor_job(path.write_text, json.dumps(payload))
        yield str(path)
    else:

        async def get_document(request):
            return web.json_response(payload)

        app = web.Application()
        app.router.add_get("/areas.json", get_document)
        runner = web.AppRunner(app)
        await runner.setup()
        try:
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            yield f"http://127.0.0.1:{runner.addresses[0][1]}/areas.json"
        finally:
            await runner.cleanup()


async def edit(hass, group, key):
    manager = hass.config_entries.options
    result = await manager.async_init(group.entry_id)
    for data in ({"action": "edit"}, {"record": key}):
        result = await manager.async_configure(result["flow_id"], data)
    assert result["step_id"] == "geojson_record"
    return result


@pytest.mark.parametrize("kind", ["file", "url"])
@pytest.mark.parametrize("replacement", ["omitted", "empty", "inline"])
async def test_clear_source_survives_reopen_refresh_and_storage(
    hass, tmp_path, kind, replacement, socket_enabled
):
    group = await create_group(hass)
    catalog = await async_get_catalog(hass)
    async with source(hass, tmp_path, kind) as address:
        await catalog.save(
            "area",
            {
                "name": "Area",
                "enabled": True,
                "priority": 0,
                "source": address,
            },
            catalog.revision,
        )
        form = await edit(hass, group, "area")
        assert "source" not in form["data_schema"]({"name": "Area"})
        marker = next(k for k in form["data_schema"].schema if k.schema == "source")
        assert marker.description["suggested_value"] == address
        data = {"name": "Area", "enabled": True, "priority": 0}
        if replacement == "empty":
            data.update(source="", geojson="")
        elif replacement == "inline":
            data["geojson"] = json.dumps(document("Replacement"))
        result = await hass.config_entries.options.async_configure(
            form["flow_id"], data
        )
        assert result["step_id"] == "geojson" and not result.get("errors"), result
        hass.config_entries.options.async_abort(result["flow_id"])
    # No file/HTTP access is needed after detaching the source.
    await catalog.refresh()
    assert catalog.records["area"]["source"] == ""
    expected = "Replacement" if replacement == "inline" else "Home"
    assert find_polygon_zone(0, 0, 0, catalog.selected(["area"]))["name"] == expected
    restored = GeoJSONCatalog(hass)
    await restored.load()
    assert restored.records["area"]["source"] == ""
    assert restored.selected(["area"]) == catalog.selected(["area"])
    form = await edit(hass, group, "area")
    suggestions = {k.schema: k.description for k in form["data_schema"].schema}
    assert suggestions["source"]["suggested_value"] == ""
    assert (
        json.loads(suggestions["geojson"]["suggested_value"])["features"][0][
            "properties"
        ]["name"]
        == expected
    )
    hass.config_entries.options.async_abort(form["flow_id"])
    assert await hass.config_entries.async_unload(group.entry_id)


async def test_inline_to_file_and_validation_error_keep_cleared_fields(hass, tmp_path):
    group = await create_group(hass)
    manager = hass.config_entries.options
    catalog = await async_get_catalog(hass)
    await catalog.save(
        "area",
        {
            "name": "Area",
            "enabled": True,
            "priority": 0,
            "geojson": document(),
        },
        catalog.revision,
    )
    form = await edit(hass, group, "area")
    async with source(hass, tmp_path, "file") as address:
        # The cleared inline field is omitted by the frontend.
        result = await manager.async_configure(
            form["flow_id"],
            {
                "name": "Area",
                "enabled": True,
                "priority": 0,
                "source": address,
            },
        )
        assert not result.get("errors"), result
        assert "geojson" not in catalog.records["area"]
        manager.async_abort(result["flow_id"])
        form = await edit(hass, group, "area")
        result = await manager.async_configure(
            form["flow_id"],
            {
                "name": "Area",
                "enabled": True,
                "priority": 0,
                "geojson": "bad json",
            },
        )
        assert result["errors"] == {"geojson": "geojson_invalid"}
        suggestions = {k.schema: k.description for k in result["data_schema"].schema}
        assert suggestions["source"]["suggested_value"] == ""
        assert suggestions["geojson"]["suggested_value"] == "bad json"
        assert catalog.records["area"]["source"] == address
        manager.async_abort(result["flow_id"])
    assert await hass.config_entries.async_unload(group.entry_id)


@pytest.mark.parametrize(
    "properties,expected",
    [
        ({"Name": "Home"}, "Home"),
        ({"name": "Canonical", "Name": "Alternative"}, "Canonical"),
    ],
)
async def test_name_alias_through_create_flow(hass, properties, expected):
    group = await create_group(hass)
    manager = hass.config_entries.options
    result = await manager.async_init(group.entry_id)
    result = await manager.async_configure(result["flow_id"], {"action": "add"})
    payload = document()
    payload["properties"] = properties
    result = await manager.async_configure(
        result["flow_id"],
        {
            "name": "Area",
            "enabled": True,
            "priority": 0,
            "geojson": json.dumps({"type": "FeatureCollection", "features": [payload]}),
        },
    )
    assert not result.get("errors"), result
    catalog = await async_get_catalog(hass)
    assert (
        find_polygon_zone(0, 0, 0, catalog.selected(list(catalog.records)))["name"]
        == expected
    )
    manager.async_abort(result["flow_id"])
    assert await hass.config_entries.async_unload(group.entry_id)
