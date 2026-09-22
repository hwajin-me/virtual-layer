"""One shared entry and real native HA Devices/entities for GeoJSON records."""

import asyncio
import json

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.virtual_layer.geojson_catalog import async_get_catalog
from custom_components.virtual_layer.geojson_group import (
    GROUP_KEY,
    GROUP_TITLE,
    GROUP_UNIQUE_ID,
    group_entry,
)
from custom_components.virtual_layer.geojson_metrics import summarize
from tests.integration.test_geojson_catalog import document, record


async def create_group(hass):
    manager = hass.config_entries.flow
    result = await manager.async_init(
        "virtual_layer",
        context={"source": SOURCE_USER},
        data={"device_group_type": GROUP_KEY},
    )
    assert result["step_id"] == "geojson_group", result
    result = await manager.async_configure(result["flow_id"], {})
    assert result["type"] == "create_entry", result
    await hass.async_block_till_done()
    return result["result"]


def rows(hass, entry):
    return er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)


def state(hass, entry, key, field):
    row = next(
        row for row in rows(hass, entry) if row.unique_id == f"geojson:{key}:{field}"
    )
    return hass.states.get(row.entity_id)


async def test_group_singleton_including_parallel_setup(hass):
    manager = hass.config_entries.flow
    results = await asyncio.gather(
        *[
            manager.async_init(
                "virtual_layer",
                context={"source": SOURCE_USER},
                data={"device_group_type": GROUP_KEY},
            )
            for _ in range(2)
        ]
    )
    forms = [r for r in results if r["type"] == "form"]
    assert len(forms) == 1
    assert [r["reason"] for r in results if r["type"] == "abort"] == [
        "already_in_progress"
    ]
    result = await manager.async_configure(forms[0]["flow_id"], {})
    await hass.async_block_till_done()
    entry = result["result"]
    assert entry.title == GROUP_TITLE and entry.unique_id == GROUP_UNIQUE_ID
    assert entry.data[GROUP_KEY] and not rows(hass, entry)
    duplicate = await manager.async_init(
        "virtual_layer",
        context={"source": SOURCE_USER},
        data={"device_group_type": GROUP_KEY},
    )
    assert duplicate["reason"] == "geojson_group_exists"
    assert (
        len(
            [
                e
                for e in hass.config_entries.async_entries("virtual_layer")
                if e.data.get(GROUP_KEY)
            ]
        )
        == 1
    )
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_each_geojson_has_own_device_and_live_information(hass):
    entry = await create_group(hass)
    manager = hass.config_entries.options
    result = await manager.async_init(entry.entry_id)
    assert result["step_id"] == "geojson"
    for name in ("Home", "Work"):
        result = await manager.async_configure(result["flow_id"], {"action": "add"})
        result = await manager.async_configure(
            result["flow_id"],
            {
                "name": name,
                "enabled": True,
                "priority": 0,
                "source": "",
                "geojson": json.dumps(document(name)),
            },
        )
        assert not result.get("errors"), result
    await hass.async_block_till_done()
    catalog = await async_get_catalog(hass)
    keys = list(catalog.records)
    assert len(keys) == 2
    assert len(rows(hass, entry)) == 14
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert len(devices) == 2 and {d.name for d in devices} == {"Home", "Work"}
    for key, name in zip(keys, ("Home", "Work"), strict=True):
        assert (
            len(
                {
                    r.device_id
                    for r in rows(hass, entry)
                    if r.unique_id.startswith(f"geojson:{key}:")
                }
            )
            == 1
        )
        assert state(hass, entry, key, "zone_names").state == name
        assert state(hass, entry, key, "zone_count").state == "1"
        assert state(hass, entry, key, "information").state == "ok"
        assert float(state(hass, entry, key, "area").state) == pytest.approx(
            197829.53, rel=0.001
        )
        assert state(hass, entry, key, "area").attributes["unit_of_measurement"] == "m²"
        assert float(state(hass, entry, key, "size").state) > 100
        bounds = state(hass, entry, key, "bounds").attributes
        assert bounds["west"] == pytest.approx(-0.002) and bounds["north"] == 0.002
        assert bounds["crosses_antimeridian"] is False
        map_row = next(
            r for r in rows(hass, entry) if r.unique_id == f"geojson:{key}:map"
        )
        image = hass.data["image"].get_entity(map_row.entity_id)
        assert name.encode() in await image.async_image()
    before = {r.unique_id: (r.entity_id, r.device_id) for r in rows(hass, entry)}
    await catalog.save(keys[0], record("Garden"), catalog.revision)
    await hass.async_block_till_done()
    assert state(hass, entry, keys[0], "zone_names").state == "Garden"
    assert {
        r.unique_id: (r.entity_id, r.device_id) for r in rows(hass, entry)
    } == before
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert {
        r.unique_id: (r.entity_id, r.device_id) for r in rows(hass, entry)
    } == before
    await catalog.save(keys[0], None, catalog.revision)
    await hass.async_block_till_done()
    assert len(rows(hass, entry)) == 7
    assert (
        len(dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)) == 1
    )
    assert all(
        hass.states.get(entity_id) is None
        for unique_id, (entity_id, _) in before.items()
        if unique_id.startswith(f"geojson:{keys[0]}:")
    )
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert not catalog.listeners and catalog.timer is None


async def test_geojson_map_falls_back_to_plain_svg_when_osm_background_fails(
    hass, monkeypatch
):
    entry = await create_group(hass)
    catalog = await async_get_catalog(hass)
    await catalog.save("home", record(), catalog.revision)
    await hass.async_block_till_done()

    async def unavailable_background(*_args):
        raise OSError("OSM tile cache unavailable")

    monkeypatch.setattr(
        "custom_components.virtual_layer.geojson_group.async_map_background",
        unavailable_background,
    )
    map_row = next(
        row for row in rows(hass, entry) if row.unique_id == "geojson:home:map"
    )
    image = hass.data["image"].get_entity(map_row.entity_id)
    rendered = await image.async_image()
    assert rendered.startswith(b'<svg xmlns="http://www.w3.org/2000/svg"')
    assert b"Home" in rendered


async def test_geojson_map_refreshes_its_image_url_hourly(hass):
    entry = await create_group(hass)
    catalog = await async_get_catalog(hass)
    await catalog.save("home", record(), catalog.revision)
    await hass.async_block_till_done()

    map_row = next(
        row for row in rows(hass, entry) if row.unique_id == "geojson:home:map"
    )
    image = hass.data["image"].get_entity(map_row.entity_id)
    before = image._attr_image_last_updated
    image._async_refresh_map(None)
    assert image._attr_image_last_updated >= before


async def test_legacy_catalog_migrates_once_without_changing_references(hass):
    from tests.integration.test_presence_fusion_ha import (
        configuration,
        gps,
        output,
        setup,
    )

    catalog = await async_get_catalog(hass)
    await catalog.save("existing", record(), catalog.revision)
    config = configuration()
    config["zones"] = {"catalog_ids": ["existing"], "home": "existing"}
    consumer, coordinator = await setup(hass, config=config)
    group = group_entry(hass)
    assert group is not None and group.unique_id == GROUP_UNIQUE_ID
    assert list(catalog.records) == ["existing"]
    assert len(rows(hass, group)) == 7
    await gps(hass, coordinator)
    assert output(hass, consumer, "zone").state == "Home"
    assert await hass.config_entries.async_reload(consumer.entry_id)
    await hass.async_block_till_done()
    assert group_entry(hass).entry_id == group.entry_id
    assert await hass.config_entries.async_remove(group.entry_id)
    await hass.async_block_till_done()
    assert not catalog.records and group_entry(hass) is None
    assert output(hass, consumer, "zone").state == "unknown"
    replacement = await create_group(hass)
    assert not rows(hass, replacement)
    assert await hass.config_entries.async_unload(consumer.entry_id)
    assert await hass.config_entries.async_unload(replacement.entry_id)


async def test_geometry_summary_accounts_for_holes_and_date_line(hass):
    catalog = await async_get_catalog(hass)
    shape = document(size=0.01)
    shape["geometry"]["coordinates"].append(
        document(size=0.005)["geometry"]["coordinates"][0]
    )
    await catalog.save("hole", {**record(), "geojson": shape}, catalog.revision)
    hole = summarize(catalog.zones["hole"], catalog.records["hole"]["snapshot"])
    assert hole["area"] == pytest.approx(3709303.7, rel=0.001)
    shape["geometry"]["coordinates"] = [
        [[179, -1], [-179, -1], [-179, 1], [179, 1], [179, -1]]
    ]
    await catalog.save("date", {**record(), "geojson": shape}, catalog.revision)
    date = summarize(catalog.zones["date"], catalog.records["date"]["snapshot"])
    assert date["bounds"] == {
        "west": 179,
        "east": -179,
        "north": 1,
        "south": -1,
        "crosses_antimeridian": True,
    }
    assert date["area"] == pytest.approx(49454872580, rel=0.001)


async def test_many_zones_stay_on_one_device_and_registry_preferences_survive(hass):
    entry = await create_group(hass)
    catalog = await async_get_catalog(hass)
    payload = {
        "type": "FeatureCollection",
        "features": [document("Garden"), document("House", 0.001)],
    }
    await catalog.save("set", {**record(), "geojson": payload}, catalog.revision)
    await hass.async_block_till_done()
    assert len(rows(hass, entry)) == 7
    assert state(hass, entry, "set", "zone_names").attributes["zone_names"] == [
        "Garden",
        "House",
    ]
    assert state(hass, entry, "set", "zone_count").state == "2"
    registry = er.async_get(hass)
    info = next(
        r for r in rows(hass, entry) if r.unique_id == "geojson:set:information"
    )
    registry.async_update_entity(
        info.entity_id, name="My GeoJSON", disabled_by=er.RegistryEntryDisabler.USER
    )
    devices = dr.async_get(hass)
    devices.async_update_device(info.device_id, name_by_user="My boundaries")
    await hass.async_block_till_done()
    await catalog.save(
        "set", {**record("Renamed"), "geojson": payload}, catalog.revision
    )
    await hass.async_block_till_done()
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await catalog.save(
        "set", {**record("Renamed again"), "geojson": payload}, catalog.revision
    )
    await hass.async_block_till_done()
    assert registry.async_get(info.entity_id).name == "My GeoJSON"
    assert (
        registry.async_get(info.entity_id).disabled_by == er.RegistryEntryDisabler.USER
    )
    assert hass.states.get(info.entity_id) is None
    device = devices.async_get(info.device_id)
    assert device.name == "Renamed again" and device.name_by_user == "My boundaries"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_source_failure_status_keeps_metrics_and_map_across_reload(
    hass, tmp_path
):
    entry = await create_group(hass)
    hass.config.allowlist_external_dirs.add(str(tmp_path))
    path = tmp_path / "boundaries.json"
    await hass.async_add_executor_job(path.write_text, json.dumps(document()))
    catalog = await async_get_catalog(hass)
    await catalog.save("file", {**record(), "source": str(path)}, catalog.revision)
    await hass.async_block_till_done()
    before = state(hass, entry, "file", "area").state
    await hass.async_add_executor_job(path.write_text, "broken")
    await catalog.refresh()
    await hass.async_block_till_done()
    assert state(hass, entry, "file", "information").state == "source_unavailable"
    assert state(hass, entry, "file", "area").state == before
    assert state(hass, entry, "file", "zone_names").state == "Home"
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert state(hass, entry, "file", "area").state == before
    assert state(hass, entry, "file", "information").attributes["load_error"]
    await hass.async_add_executor_job(
        path.write_text, json.dumps(document("Recovered"))
    )
    await catalog.refresh()
    await hass.async_block_till_done()
    assert state(hass, entry, "file", "information").state == "ok"
    assert state(hass, entry, "file", "zone_names").state == "Recovered"
    await catalog.save(
        "file", {**catalog.records["file"], "enabled": False}, catalog.revision
    )
    await hass.async_block_till_done()
    assert state(hass, entry, "file", "information").state == "disabled"
    assert not catalog.selected(["file"])
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_invalid_legacy_document_has_removable_device(hass):
    catalog = await async_get_catalog(hass)
    await catalog.store.async_save({"records": {"bad": "damaged", "good": record()}})
    await catalog.load()
    entry = await create_group(hass)
    assert len(rows(hass, entry)) == 14
    assert state(hass, entry, "bad", "information").state == "invalid"
    assert state(hass, entry, "bad", "area").state == "unknown"
    assert state(hass, entry, "good", "zone_names").state == "Home"
    manager = hass.config_entries.options
    result = await manager.async_init(entry.entry_id)
    for data in [{"action": "delete"}, {"record": "bad"}, {"confirm": True}]:
        result = await manager.async_configure(result["flow_id"], data)
        assert not result.get("errors"), result
    await hass.async_block_till_done()
    assert list(catalog.records) == ["good"] and len(rows(hass, entry)) == 7
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_duplicate_stored_group_cannot_own_or_delete_catalog(hass):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = await create_group(hass)
    catalog = await async_get_catalog(hass)
    await catalog.save("home", record(), catalog.revision)
    await hass.async_block_till_done()
    duplicate = MockConfigEntry(
        domain="virtual_layer",
        title="Duplicate",
        data={GROUP_KEY: True, "group_name": GROUP_TITLE},
    )
    duplicate.add_to_hass(hass)
    assert await hass.config_entries.async_setup(duplicate.entry_id) is False
    assert not rows(hass, duplicate)
    assert await hass.config_entries.async_remove(duplicate.entry_id)
    assert (
        list(catalog.records) == ["home"]
        and group_entry(hass).entry_id == entry.entry_id
    )
    assert await hass.config_entries.async_unload(entry.entry_id)
