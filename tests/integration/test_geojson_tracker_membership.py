"""Real HA state events exercise shared GeoJSON coordinate membership."""

from datetime import timedelta

import pytest
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.virtual_layer.geojson_catalog import async_get_catalog

pytestmark = pytest.mark.integration


def rectangle(west, south, east, north):
    return [[west, south], [east, south], [east, north], [west, north], [west, south]]


def document():
    return {
        "type": "Feature",
        "properties": {"name": "Campus"},
        "geometry": {
            "type": "MultiPolygon",
            "coordinates": [
                [rectangle(126, 36, 128, 38), rectangle(126.9, 36.9, 127.1, 37.1)],
                [rectangle(179, -1, -179, 1)],
            ],
        },
    }


async def create_tracker(hass):
    catalog = await async_get_catalog(hass)
    await catalog.save(
        "campus",
        {
            "name": "Campus",
            "enabled": True,
            "priority": 0,
            "source": "",
            "geojson": document(),
        },
        catalog.revision,
    )
    entry = MockConfigEntry(
        domain="virtual_layer",
        data={"group_name": "Membership"},
        options={
            "devices": {
                "Membership": [
                    {
                        "platform": "device_tracker",
                        "name": "Membership",
                        "entity_id": "device_tracker.membership",
                        "initial_value": "not_home",
                        "initial_availability": True,
                        "persistent": False,
                        "source_entities": ["device_tracker.gps_input"],
                        "polygonal_zone": {
                            "catalog_ids": ["campus"],
                            "tracker_rules": {
                                "device_tracker.gps_input": {"max_age_seconds": 30}
                            },
                        },
                    }
                ]
            }
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return catalog, entry


async def position(hass, latitude, longitude, accuracy=0, state="not_home"):
    hass.states.async_set(
        "device_tracker.gps_input",
        state,
        {
            "latitude": latitude,
            "longitude": longitude,
            "gps_accuracy": accuracy,
        },
    )
    await hass.async_block_till_done()
    # Flush the tracker state write followed by its companion's state callback.
    await hass.async_block_till_done()
    return hass.states.get("device_tracker.membership")


@pytest.mark.parametrize(
    ("latitude", "longitude", "accuracy", "expected", "inside"),
    [
        (37.5, 127, 0, "Campus", True),
        (37, 127, 0, "not_home", False),  # hole
        (37, 126.9, 0, "not_home", False),  # hole boundary
        (36, 127, 0, "Campus", True),  # exterior boundary
        (35.9999, 127, 0, "not_home", False),
        (35.9999, 127, 20, "Campus", False),  # accuracy circle only
        (0, 179.5, 0, "Campus", True),
        (0, -179.5, 0, "Campus", True),
        (0, 180, 0, "Campus", True),
        (0, -180, 0, "Campus", True),
        (0, 0, 0, "not_home", False),
    ],
)
async def test_actual_tracker_membership(
    hass, latitude, longitude, accuracy, expected, inside
):
    _, entry = await create_tracker(hass)
    state = await position(hass, latitude, longitude, accuracy)
    assert state.state == expected
    assert state.attributes["polygon_inside"] is inside
    assert state.attributes["polygon_containing_zone"] == ("Campus" if inside else None)
    assert state.attributes["latitude"] == latitude
    assert state.attributes["longitude"] == longitude
    assert hass.states.get("sensor.membership_zone").state == expected
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize(
    "bad_state,latitude,longitude,accuracy",
    [
        ("unavailable", 37.5, 127, 0),
        ("unknown", 37.5, 127, 0),
        ("not_home", None, 127, 0),
        ("not_home", 91, 127, 0),
        ("not_home", True, 127, 0),
        ("not_home", 37.5, float("nan"), 0),
        ("not_home", 37.5, 127, -1),
    ],
)
async def test_invalid_gps_does_not_claim_outside(
    hass, bad_state, latitude, longitude, accuracy
):
    _, entry = await create_tracker(hass)
    assert (await position(hass, 37.5, 127)).state == "Campus"
    state = await position(hass, latitude, longitude, accuracy, bad_state)
    assert state.state == "unknown"
    assert state.attributes["polygon_inside"] is None
    assert "latitude" not in state.attributes
    assert hass.states.get("sensor.membership_zone").state == "unknown"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_expiry_recovery_catalog_changes_and_reload(hass, freezer):
    catalog, entry = await create_tracker(hass)
    assert (await position(hass, 37.5, 127)).state == "Campus"
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    assert hass.states.get("device_tracker.membership").state == "unknown"
    assert (await position(hass, 37.51, 127)).state == "Campus"
    assert (await position(hass, 39, 127)).state == "not_home"
    assert (await position(hass, 37.5, 127)).state == "Campus"
    original = catalog.records["campus"]
    await catalog.save("campus", {**original, "enabled": False}, catalog.revision)
    await hass.async_block_till_done()
    assert hass.states.get("device_tracker.membership").state == "unknown"
    await catalog.save("campus", original, catalog.revision)
    await hass.async_block_till_done()
    assert hass.states.get("device_tracker.membership").state == "Campus"
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("device_tracker.membership").state == "Campus"
    await catalog.save("campus", None, catalog.revision)
    await hass.async_block_till_done()
    state = hass.states.get("device_tracker.membership")
    assert state.state == "unknown"
    assert state.attributes["latitude"] == 37.5
    assert state.attributes["polygon_inside"] is None
    assert await hass.config_entries.async_unload(entry.entry_id)
