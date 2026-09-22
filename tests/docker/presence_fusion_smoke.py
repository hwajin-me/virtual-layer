"""Run with official HA image, synthetic states and an explicit fake clock."""

import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
from homeassistant import bootstrap, loader
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er


async def main():
    root = Path(tempfile.mkdtemp())
    (root / "custom_components").mkdir()
    os.symlink(
        "/config/custom_components/virtual_layer",
        root / "custom_components/virtual_layer",
    )
    hass = HomeAssistant(str(root))
    loader.async_setup(hass)
    try:
        await bootstrap.async_from_config_dict({}, hass)
        await hass.async_start()
        hass.states.async_set(
            "zone.home", "0", {"latitude": 0, "longitude": 0, "radius": 100}
        )
        hass.states.async_set("device_tracker.fusion_fixture", "unavailable")
        manager = hass.config_entries.flow
        result = await manager.async_init(
            "virtual_layer",
            context={"source": SOURCE_USER},
            data={"device_group_type": "geojson_group"},
        )
        assert result["step_id"] == "geojson_group"
        result = await manager.async_configure(result["flow_id"], {})
        group = result["result"]
        await hass.async_block_till_done()
        duplicate = await manager.async_init(
            "virtual_layer",
            context={"source": SOURCE_USER},
            data={"device_group_type": "geojson_group"},
        )
        assert duplicate["reason"] == "geojson_group_exists"
        manager = hass.config_entries.options
        result = await manager.async_init(group.entry_id)
        geometry = {
            "type": "Feature",
            "properties": {"name": "Polygon Home"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [-0.002, -0.002],
                        [0.002, -0.002],
                        [0.002, 0.002],
                        [-0.002, 0.002],
                        [-0.002, -0.002],
                    ]
                ],
            },
        }
        for data in [
            {"action": "add"},
            {
                "name": "Home boundary",
                "enabled": True,
                "priority": 0,
                "source": "",
                "geojson": json.dumps(geometry),
            },
        ]:
            result = await manager.async_configure(result["flow_id"], data)
            assert not result.get("errors"), result
        result = await manager.async_configure(result["flow_id"], {"action": "add"})
        result = await manager.async_configure(
            result["flow_id"],
            {
                "name": "Second area",
                "enabled": True,
                "priority": 1,
                "source": "",
                "geojson": json.dumps(geometry),
            },
        )
        assert not result.get("errors"), result
        await manager.async_configure(result["flow_id"], {"action": "done"})
        await hass.async_block_till_done()
        group_rows = er.async_entries_for_config_entry(
            er.async_get(hass), group.entry_id
        )
        assert len(group_rows) == 14 and len({r.device_id for r in group_rows}) == 2
        assert {r.unique_id.rsplit(":", 1)[-1] for r in group_rows} == {
            "information",
            "zone_names",
            "bounds",
            "area",
            "size",
            "zone_count",
            "map",
        }
        from custom_components.virtual_layer.geojson_catalog import async_get_catalog

        catalog = await async_get_catalog(hass)
        key = next(iter(catalog.records))
        manager = hass.config_entries.flow
        result = await manager.async_init(
            "virtual_layer",
            context={"source": SOURCE_USER},
            data={"group_name": "Fusion smoke", "device_group_type": "presence_fusion"},
        )
        for data in [{"action": "zones"}, {"catalog_ids": [key], "home": key}]:
            result = await manager.async_configure(result["flow_id"], data)
            assert not result.get("errors"), result
        for data in [
            {"action": "add"},
            {"name": "Synthetic", "priority": 1, "candidate": True},
            {"action": "add"},
            {
                "kind": "gps",
                "entity_id": "device_tracker.fusion_fixture",
                "timestamp_attribute": "measured",
                "timestamp_format": "seconds",
            },
            {"action": "done"},
            {"action": "save"},
        ]:
            result = await manager.async_configure(result["flow_id"], data)
            assert not result.get("errors"), result
        entry = result["result"]
        await hass.async_block_till_done()
        coordinator = entry.runtime_data.coordinator

        class Clock:
            now = coordinator.clock.utc()

            def utc(self):
                return self.now

            def monotonic(self):
                return self.now

        coordinator.clock = clock = Clock()
        for elapsed in (0, 60):
            clock.now += elapsed
            hass.states.async_set(
                "device_tracker.fusion_fixture",
                "home",
                {
                    "latitude": 0,
                    "longitude": 0,
                    "gps_accuracy": 10,
                    "measured": clock.now,
                },
            )
            await hass.async_block_till_done()
        registry = er.async_get(hass)
        rows = er.async_entries_for_config_entry(registry, entry.entry_id)
        assert len(rows) == 11
        assert len({r.device_id for r in rows}) == 1

        def state(key):
            row = next(r for r in rows if r.unique_id.endswith(":" + key))
            return hass.states.get(row.entity_id).state

        assert state("presence") == "home" and state("home") == "on"
        assert state("zone") == "Polygon Home"
        map_row = next(r for r in rows if r.unique_id.endswith(":map"))
        image = hass.data["image"].get_entity(map_row.entity_id)
        assert b"Polygon Home" in await image.async_image()
        clock.now += 300
        coordinator._tick(None)
        await hass.async_block_till_done()
        assert state("gps") == "unavailable" and state("presence") == "home"
        clock.now += 300
        coordinator._tick(None)
        await hass.async_block_till_done()
        assert state("home") == "unknown" and state("presence") == "unknown"
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert len(er.async_entries_for_config_entry(registry, entry.entry_id)) == 11
        assert coordinator.stopped and not coordinator.unsubs
        assert await hass.config_entries.async_unload(entry.entry_id)
        assert catalog.listeners
        # Exercise ordinary trackers against the same shared catalog in real HA.
        result = await hass.config_entries.flow.async_init(
            "virtual_layer", context={"source": SOURCE_USER},
            data={"group_name": "GPS membership", "add_first_entity": False},
        )
        tracker_entry = result["result"]
        await hass.async_block_till_done()
        hass.config_entries.async_update_entry(tracker_entry, options={"devices": {
            "GPS membership": [{
                "platform": "device_tracker", "name": "GPS membership",
                "entity_id": "device_tracker.geojson_smoke",
                "initial_value": "unknown", "initial_availability": True,
                "persistent": False,
                "source_entities": ["device_tracker.geojson_input"],
                "polygonal_zone": {"catalog_ids": [key]},
            }],
        }})
        await hass.async_block_till_done()
        assert await hass.config_entries.async_reload(tracker_entry.entry_id)
        await hass.async_block_till_done()
        for latitude, accuracy, source_state, expected, inside in [
            (0, 0, "not_home", "Polygon Home", True),
            (1, 0, "not_home", "not_home", False),
            (0.0021, 20, "not_home", "Polygon Home", False),
            (0, 0, "unavailable", "unknown", None),
            (0, 0, "not_home", "Polygon Home", True),
        ]:
            hass.states.async_set("device_tracker.geojson_input", source_state, {
                "latitude": latitude, "longitude": 0, "gps_accuracy": accuracy,
            })
            await hass.async_block_till_done()
            await hass.async_block_till_done()
            tracker = hass.states.get("device_tracker.geojson_smoke")
            assert tracker.state == expected, tracker
            assert tracker.attributes["polygon_inside"] is inside
            assert hass.states.get("sensor.geojson_smoke_zone").state == expected
        assert await hass.config_entries.async_unload(tracker_entry.entry_id)
        assert await hass.config_entries.async_unload(group.entry_id)
        assert not catalog.listeners and catalog.timer is None
        print(
            json.dumps(
                {
                    "presence_fusion": "passed",
                    "homeassistant": HA_VERSION,
                    "python": sys.version.split()[0],
                    "entities": 11,
                    "geojson_devices": 2,
                    "geojson_entities": 14,
                    "checks": [
                        "config_flow",
                        "shared_geojson_flow",
                        "singleton_geojson_group",
                        "geojson_device_information",
                        "gps_inside_outside_accuracy_and_recovery",
                        "polygon_home",
                        "zone_sensor_and_svg",
                        "native_entities",
                        "device_grouping",
                        "timer_expiry",
                        "unknown",
                        "reload",
                        "unload",
                    ],
                }
            )
        )
    finally:
        await hass.async_stop()


asyncio.run(main())
