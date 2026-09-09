"""Doorbell composition coverage; no physical G4 or Apple Home is simulated."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from homeassistant.components.homekit.doorbell import HomeDoorbellAccessory
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.virtual_layer.const import (
    ATTR_DEVICES, ATTR_GROUP_NAME, COMPONENT_DOMAIN,
)

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("source_domain", ["event", "binary_sensor"])
async def test_doorbell_repeated_rings_and_device_lifecycle(hass, source_domain):
    """Timestamp events and off/on pulses both reach HA's HomeKit handler."""
    source_id = f"{source_domain}.g4_ring"
    initial = "2026-09-09T00:00:00+00:00" if source_domain == "event" else "off"
    hass.states.async_set(source_id, initial, {"event_type": "pressed"})
    ring_id = f"{source_domain}.front_ring"
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        title="Entrance",
        data={ATTR_GROUP_NAME: "Entrance"},
        options={ATTR_DEVICES: {"Entrance": [
            {"platform": "camera", "name": "Front Video",
             "stream_source": "rtsp://example.test/front"},
            {"platform": source_domain, "name": "Front Ring",
             "class": "doorbell" if source_domain == "event" else None,
             "source_entities": [source_id],
             "value_template": "{{ states('" + source_id + "') }}"},
            {"platform": "binary_sensor", "name": "Front Motion", "class": "motion"},
            {"platform": "sensor", "name": "Front Battery", "class": "battery",
             "initial_value": "75", "unit_of_measurement": "%"},
        ]}},
    )
    # Omit an optional class instead of persisting a null string.
    if source_domain == "binary_sensor":
        entry.options[ATTR_DEVICES]["Entrance"][1].pop("class")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    ids = ["camera.front_video", ring_id, "binary_sensor.front_motion", "sensor.front_battery"]
    device_id = registry.async_get(ids[0]).device_id
    assert device_id
    assert all(registry.async_get(entity_id).device_id == device_id for entity_id in ids)
    assert all(
        entity.device_id == device_id
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id)
    )
    assert hass.states.get(ring_id).state == initial
    if source_domain == "event":
        assert hass.states.get(ring_id).attributes["device_class"] == "doorbell"
    camera = hass.data["camera"].get_entity(ids[0])
    assert await camera.stream_source() == "rtsp://example.test/front"
    receiver = SimpleNamespace(
        doorbell_is_event=source_domain == "event",
        _char_doorbell_detected=Mock(),
        _char_doorbell_detected_switch=Mock(),
        entity_id=ids[0], linked_doorbell_sensor=ring_id,
    )
    for second in (1, 2):
        old = hass.states.get(ring_id)
        value = f"2026-09-09T00:00:0{second}+00:00" if source_domain == "event" else "on"
        hass.states.async_set(source_id, value, {"event_type": "pressed"})
        await hass.async_block_till_done()
        new = hass.states.get(ring_id)
        assert new.state == value
        HomeDoorbellAccessory.async_update_doorbell_state(receiver, old, new)
        if source_domain == "binary_sensor":
            hass.states.async_set(source_id, "off")
            await hass.async_block_till_done()
            HomeDoorbellAccessory.async_update_doorbell_state(receiver, new, hass.states.get(ring_id))
    assert receiver._char_doorbell_detected.set_value.call_count == 2
    assert receiver._char_doorbell_detected_switch.set_value.call_count == 2
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert all(registry.async_get(entity_id).device_id == device_id for entity_id in ids)
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert all(hass.states.get(entity_id) is None for entity_id in ids)
