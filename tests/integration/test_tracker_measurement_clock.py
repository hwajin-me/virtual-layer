"""Measurement clocks survive real source events, config-entry reload and removal."""

from datetime import timedelta

import pytest
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.virtual_layer.const import (
    ATTR_DEVICES,
    ATTR_GROUP_NAME,
    COMPONENT_DOMAIN,
)

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("polygon", [False, True])
async def test_delayed_measurement_does_not_steal_carried_device(
    hass, freezer, polygon
):
    sources = [
        "device_tracker.clock_phone",
        "device_tracker.clock_tablet",
        "device_tracker.clock_watch",
    ]
    initial = dt_util.utcnow()
    for source in sources:
        hass.states.async_set(
            source,
            "not_home",
            {
                "lat": 37.5,
                "lon": 127,
                "acc": 5,
                "last_timestamp": initial.timestamp(),
            },
        )
    config = {
        "platform": "device_tracker",
        "name": "Clock Trip",
        "entity_id": "device_tracker.clock_trip",
        "persistent": True,
        "initial_value": "not_home",
        "initial_availability": True,
        "source_entities": sources,
        "location_helper": {"distance_threshold_meters": 300},
    }
    if polygon:
        config["polygonal_zone"] = {
            "strategy": "adaptive",
            "geojson": {
                "type": "Feature",
                "properties": {"name": "Home"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [126.99, 37.49],
                            [127.01, 37.49],
                            [127.01, 37.501],
                            [126.99, 37.501],
                            [126.99, 37.49],
                        ]
                    ],
                },
            },
        }
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "Clock Trip"},
        options={ATTR_DEVICES: {"Clock Trip": [config]}},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=120))
    hass.states.async_set(
        sources[0],
        "not_home",
        {
            "lat": 37.51,
            "lon": 127,
            "acc": 5,
            "last_timestamp": dt_util.utcnow().timestamp(),
        },
    )
    await hass.async_block_till_done()
    state = hass.states.get(config["entity_id"])
    assert state.attributes["latitude"] == 37.51
    assert state.attributes["location_speed_m_s"] == pytest.approx(9.27, abs=0.05)
    seen = state.attributes["location_last_seen"]
    freezer.tick(timedelta(seconds=60))
    hass.states.async_set(
        sources[1],
        "not_home",
        {
            "lat": 35,
            "lon": 128,
            "acc": 5,
            "last_timestamp": initial.timestamp() - 60,
        },
    )
    await hass.async_block_till_done()
    state = hass.states.get(config["entity_id"])
    assert state.attributes["location_priority_source"] == sources[0]
    assert state.attributes["location_last_seen"] == seen
    assert state.attributes["location_rejected_sources"][sources[1]] == "out_of_order"
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    state = hass.states.get(config["entity_id"])
    assert state.attributes["latitude"] == 37.51
    assert state.attributes["location_speed_m_s"] is None
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
