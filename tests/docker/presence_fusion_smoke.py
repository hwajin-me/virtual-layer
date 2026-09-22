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
            data={"group_name": "Fusion smoke", "presence_fusion": True},
        )
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
            {"action": "manage_geojson"},
            {"action": "add"},
            {
                "name": "Home boundary",
                "enabled": True,
                "priority": 0,
                "source": "",
                "geojson": json.dumps(geometry),
            },
            {"action": "done"},
        ]:
            result = await manager.async_configure(result["flow_id"], data)
            assert not result.get("errors"), result
        from custom_components.virtual_layer.geojson_catalog import async_get_catalog

        catalog = await async_get_catalog(hass)
        key = next(iter(catalog.records))
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
        assert not catalog.listeners and catalog.timer is None
        print(
            json.dumps(
                {
                    "presence_fusion": "passed",
                    "homeassistant": HA_VERSION,
                    "python": sys.version.split()[0],
                    "entities": 11,
                    "checks": [
                        "config_flow",
                        "shared_geojson_flow",
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
