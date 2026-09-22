"""Shared geometry through real storage, flows and HA platforms."""

import json
import base64
import gzip

import pytest
from homeassistant.helpers import entity_registry as er

from custom_components.virtual_layer.geojson_catalog import (
    GeoJSONCatalog,
    async_get_catalog,
)
from custom_components.virtual_layer.polygon import find_polygon_zone, polygon_clearance
from tests.integration.test_presence_fusion_ha import configuration, gps, output, setup


def document(name="Home", size=0.002):
    return {
        "type": "Feature",
        "properties": {"name": name},
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [-size, -size],
                    [size, -size],
                    [size, size],
                    [-size, size],
                    [-size, -size],
                ]
            ],
        },
    }


def record(name="Home", size=0.002, priority=0):
    return {
        "name": name,
        "enabled": True,
        "priority": priority,
        "geojson": document(name, size),
    }


async def test_catalog_persistence_priority_disable_delete_conflict(hass):
    catalog = await async_get_catalog(hass)
    await catalog.save("a", record(), catalog.revision)
    await catalog.save("b", record("Work", priority=-1), catalog.revision)
    assert find_polygon_zone(0, 0, 0, catalog.selected(["a", "b"]))["name"] == "Work"
    restored = GeoJSONCatalog(hass)
    await restored.load()
    assert restored.selected(["a", "b"]) == catalog.selected(["a", "b"])
    with pytest.raises(ValueError, match="geojson_conflict"):
        await catalog.save("a", None, 0)
    await catalog.save("b", {**record("Work"), "enabled": False}, catalog.revision)
    assert find_polygon_zone(0, 0, 0, catalog.selected(["a", "b"]))["name"] == "Home"
    await catalog.save("a", None, catalog.revision)
    assert not catalog.selected(["a", "b"])
    assert catalog.errors(["a"])


async def test_catalog_file_last_good_and_recovery(hass, tmp_path):
    hass.config.allowlist_external_dirs.add(str(tmp_path))
    path = tmp_path / "areas.json"
    await hass.async_add_executor_job(path.write_text, json.dumps(document()))
    catalog = await async_get_catalog(hass)
    await catalog.save("file", {**record(), "source": str(path)}, catalog.revision)
    await hass.async_add_executor_job(path.write_text, "broken")
    await catalog.refresh()
    assert catalog.errors(["file"])
    assert catalog.selected(["file"])[0]["name"] == "Home"
    await hass.async_add_executor_job(path.write_text, json.dumps(document("Changed")))
    await catalog.refresh()
    assert not catalog.errors(["file"])
    assert catalog.selected(["file"])[0]["name"] == "Changed"


async def test_catalog_accepts_geojson_io_compressed_share_link(hass):
    payload = base64.urlsafe_b64encode(gzip.compress(json.dumps(document()).encode())).decode()
    catalog = await async_get_catalog(hass)
    await catalog.save(
        "shared",
        {**record(), "source": f"https://geojson.io/?data=gz:{payload}"},
        catalog.revision,
    )
    assert catalog.selected(["shared"])[0]["name"] == "Home"


@pytest.mark.parametrize(
    "bad",
    [
        None,
        {},
        {"type": "Point", "coordinates": [0, 0]},
        {"type": "FeatureCollection", "features": []},
    ],
)
async def test_invalid_geometry_is_not_saved(hass, bad):
    catalog = await async_get_catalog(hass)
    with pytest.raises((ValueError, TypeError)):
        await catalog.save("bad", {**record(), "geojson": bad}, catalog.revision)
    assert not catalog.records


async def test_fusion_shared_live_geometry_map_and_unload(hass):
    catalog = await async_get_catalog(hass)
    await catalog.save("home", record(), catalog.revision)
    config = configuration()
    config["zones"] = {"catalog_ids": ["home"], "home": "home"}
    entry, coordinator = await setup(hass, config=config)
    await gps(hass, coordinator, x=150)
    coordinator.clock.advance(60)
    await gps(hass, coordinator, x=150)
    assert output(hass, entry, "zone").state == "Home"
    assert output(hass, entry, "presence").state == "home"
    assert output(hass, entry, "gps").attributes["longitude"] > 0
    rows = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    assert len(rows) == 11
    assert len({row.device_id for row in rows}) == 1
    from custom_components.virtual_layer.presence_fusion.entities import FusionMap

    image = FusionMap(coordinator)
    image.hass = hass
    assert b"GPS" in await image.async_image()
    await catalog.save("home", record("Renamed"), catalog.revision)
    await hass.async_block_till_done()
    assert output(hass, entry, "zone").state == "Renamed"
    await catalog.save("home", None, catalog.revision)
    await hass.async_block_till_done()
    assert not coordinator.engine.home_shape_valid
    assert output(hass, entry, "zone").state == "unknown"
    assert output(hass, entry, "map").state == "unavailable"
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    from custom_components.virtual_layer.geojson_group import group_entry

    assert catalog.listeners  # The shared group still displays its documents.
    assert await hass.config_entries.async_unload(group_entry(hass).entry_id)
    await hass.async_block_till_done()
    assert not catalog.listeners and catalog.timer is None


async def test_geojson_flow_add_edit_delete_and_select_home(hass):
    from tests.integration.test_geojson_group import create_group

    group = await create_group(hass)
    entry, _ = await setup(hass)
    manager = hass.config_entries.options
    result = await manager.async_init(group.entry_id)
    for data in [
        {"action": "add"},
        {
            "name": "Garden",
            "enabled": True,
            "priority": 0,
            "source": "",
            "geojson": json.dumps(document()),
        },
    ]:
        result = await manager.async_configure(result["flow_id"], data)
        assert not result.get("errors"), result
    catalog = await async_get_catalog(hass)
    key = next(iter(catalog.records))
    await manager.async_configure(result["flow_id"], {"action": "done"})
    result = await manager.async_init(entry.entry_id)
    for data in [
        {"action": "zones"},
        {"catalog_ids": [key], "home": key},
        {"action": "save"},
    ]:
        result = await manager.async_configure(result["flow_id"], data)
        assert not result.get("errors"), result
    await hass.async_block_till_done()
    assert entry.options["presence_fusion"]["zones"]["home"] == key
    assert (
        len(er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)) == 11
    )
    result = await manager.async_init(entry.entry_id)
    for data in [
        {"action": "zones"},
        {"catalog_ids": [], "home": ""},
        {"action": "save"},
    ]:
        result = await manager.async_configure(result["flow_id"], data)
    await hass.async_block_till_done()
    assert (
        len(er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)) == 9
    )
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert await hass.config_entries.async_unload(group.entry_id)


async def test_polygon_clearance_holes_and_dateline(hass):
    catalog = await async_get_catalog(hass)
    geometry = document(size=0.01)
    geometry["geometry"]["coordinates"].append(
        document(size=0.001)["geometry"]["coordinates"][0]
    )
    await catalog.save("hole", {**record(), "geojson": geometry}, catalog.revision)
    zones = catalog.selected(["hole"])
    assert polygon_clearance(0, 0, zones) < -100
    assert polygon_clearance(0, 0.005, zones) > 400
    assert polygon_clearance(0, 0.02, zones) < 0
    geometry["geometry"]["coordinates"] = [
        [[179, -1], [-179, -1], [-179, 1], [179, 1], [179, -1]]
    ]
    await catalog.save("date", {**record(), "geojson": geometry}, catalog.revision)
    assert polygon_clearance(0, 180, catalog.selected(["date"])) > 100000
    assert polygon_clearance(0, 0, catalog.selected(["date"])) < 0


async def test_generic_tracker_uses_same_catalog_and_live_map(hass):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    catalog = await async_get_catalog(hass)
    await catalog.save("shared", record(), catalog.revision)
    fusion_config = configuration("other")
    fusion_config["zones"] = {"catalog_ids": ["shared"], "home": ""}
    fusion_entry, fusion = await setup(hass, prefix="other", config=fusion_config)
    await gps(hass, fusion, prefix="other")
    hass.states.async_set(
        "device_tracker.input",
        "not_home",
        {"latitude": 0, "longitude": 0, "gps_accuracy": 5},
    )
    entry = MockConfigEntry(
        domain="virtual_layer",
        title="Areas",
        data={"group_name": "Areas"},
        options={
            "devices": {
                "Areas": [
                    {
                        "platform": "device_tracker",
                        "name": "Areas",
                        "entity_id": "device_tracker.areas",
                        "initial_value": "not_home",
                        "initial_availability": True,
                        "persistent": False,
                        "source_entities": ["device_tracker.input"],
                        "polygonal_zone": {"catalog_ids": ["shared"]},
                    }
                ]
            }
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("device_tracker.areas").state == "Home"
    assert hass.states.get("sensor.areas_zone").state == "Home"
    image = hass.data["image"].get_entity("image.areas_map")
    assert b"Home" in await image.async_image()
    manager = hass.config_entries.options
    result = await manager.async_init(entry.entry_id)
    action = next(
        v for k, v in result["data_schema"].schema.items() if k.schema == "action"
    )
    assert "manage_geojson" not in action.config["options"]
    manager.async_abort(result["flow_id"])
    from custom_components.virtual_layer.geojson_group import group_entry

    group = group_entry(hass)
    result = await manager.async_init(group.entry_id)
    assert result["step_id"] == "geojson"
    for data in [
        {"action": "edit"},
        {"record": "shared"},
        {
            "name": "New",
            "enabled": True,
            "priority": 0,
            "source": "",
            "geojson": json.dumps(document("New")),
        },
    ]:
        result = await manager.async_configure(result["flow_id"], data)
        assert not result.get("errors"), result
    await hass.async_block_till_done()
    assert hass.states.get("device_tracker.areas").state == "New"
    assert output(hass, fusion_entry, "zone").state == "New"
    assert b"New" in await image.async_image()
    for data in [{"action": "delete"}, {"record": "shared"}, {"confirm": True}]:
        result = await manager.async_configure(result["flow_id"], data)
        assert not result.get("errors"), result
    await hass.async_block_till_done()
    assert hass.states.get("device_tracker.areas").state == "unknown"
    assert b"New" not in (await image.async_image() or b"")
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert catalog.listeners and catalog.timer is not None
    assert await hass.config_entries.async_unload(fusion_entry.entry_id)
    await hass.async_block_till_done()
    assert catalog.listeners and catalog.timer is not None
    assert await hass.config_entries.async_unload(group.entry_id)
    await hass.async_block_till_done()
    assert not catalog.listeners and catalog.timer is None


async def test_catalog_corrupt_record_isolated_and_removable(hass):
    catalog = GeoJSONCatalog(hass)
    await catalog.store.async_save({"records": {"bad": "broken", "valid": record()}})
    await catalog.load()
    assert catalog.errors(["bad"])
    assert catalog.selected(["valid"])
    await catalog.save("bad", None, catalog.revision)
    assert list(catalog.records) == ["valid"]


@pytest.mark.parametrize(
    "changes",
    [
        {"priority": float("nan")},
        {"priority": True},
        {"enabled": "yes"},
        {"source": "https://user:pass@example.com/map.json"},
    ],
)
async def test_catalog_rejects_invalid_configuration(hass, changes):
    catalog = await async_get_catalog(hass)
    with pytest.raises((ValueError, TypeError)):
        await catalog.save("invalid", {**record(), **changes}, catalog.revision)
    assert not catalog.records


async def test_catalog_limits_and_diagnostics(hass, monkeypatch):
    from custom_components.virtual_layer import geojson_catalog

    monkeypatch.setattr(geojson_catalog, "MAX_POINTS", 9)
    catalog = await async_get_catalog(hass)
    await catalog.save("a", record("PRIVATE GARDEN"), catalog.revision)
    with pytest.raises(ValueError, match="geojson_limit"):
        await catalog.save("b", record(), catalog.revision)
    assert list(catalog.records) == ["a"]
    config = configuration()
    config["zones"] = {"catalog_ids": ["a"], "home": "a"}
    entry, coordinator = await setup(hass, config=config)
    await gps(hass, coordinator)
    diagnostics = json.dumps(coordinator.diagnostics())
    assert "PRIVATE GARDEN" not in diagnostics
    assert "coordinates" not in diagnostics and "latitude" not in diagnostics
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_generic_catalog_selection_form_round_trip(hass):
    from custom_components.virtual_layer.config_flow import (
        _build_entity_config,
        _entity_form_defaults,
        _entity_schema,
    )
    from custom_components.virtual_layer.device_tracker import (
        DEVICE_TRACKER_SCHEMA,
        validate_domain_options,
    )

    catalog = await async_get_catalog(hass)
    await catalog.save("a", record(), catalog.revision)
    await catalog.save("b", record("Office"), catalog.revision)
    form = _entity_schema(
        {"platform": "device_tracker", "entity_name": "Areas"}, hass=hass
    )({})
    form.update(
        {
            "device_name": "Family",
            "entity_id": "device_tracker.family_areas",
            "source_entities_text": "device_tracker.input",
            "polygon_catalog_ids": ["b", "a"],
        }
    )
    _, entity = _build_entity_config(
        form, DEVICE_TRACKER_SCHEMA, validate_domain_options
    )
    assert entity["polygonal_zone"]["catalog_ids"] == ["b", "a"]
    defaults = _entity_form_defaults(
        "Family",
        {**entity, "device_id": "family", "name": "Areas", "initial_value": "not_home"},
    )
    assert defaults["polygon_catalog_ids"] == ["b", "a"]
