"""Media metadata must appear even when helpers were created while idle."""

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.virtual_layer.config_flow import _native_reference_templates

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("source_count", [1, 2])
async def test_idle_created_player_tracks_later_playback(hass, source_count, caplog):
    sources = ["media_player.tv", "media_player.apple_tv"][-source_count:]
    for source in sources:
        hass.states.async_set(source, "off")
    templates = _native_reference_templates(
        "media_player", sources, [hass.states.get(source) for source in sources],
    )
    entry = MockConfigEntry(
        domain="virtual_layer", title="Media", data={"group_name": "Media"},
        options={"devices": {"Media": [{
            "platform": "media_player", "name": "Virtual TV",
            "entity_id": "media_player.virtual_tv", "unique_id": "live_media",
            "initial_value": "off", "source_entities": sources,
            "native_templates": templates,
        }]}},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    metadata = {
        "media_title": "Apple TV video", "app_name": "YouTube",
        "media_content_type": "video", "media_duration": 484,
        "media_position": 433, "media_position_updated_at": "2026-09-21T10:00:00+00:00",
        "volume_level": 0.37, "is_volume_muted": False,
    }
    if source_count == 2:
        hass.states.async_set(sources[0], "on", {
            "media_title": "HDMI", "media_position": 10, "volume_level": 0.9,
        })
    hass.states.async_set(sources[-1], "playing", metadata)
    await hass.async_block_till_done()
    state = hass.states.get("media_player.virtual_tv")
    assert state is not None
    assert state.state == "playing"
    for name, value in metadata.items():
        actual = state.attributes[name]
        if name == "media_position_updated_at":
            actual = actual.isoformat()
        assert actual == value

    hass.states.async_set(sources[-1], "paused", {
        **metadata, "media_position": 450, "volume_level": 0.0, "is_volume_muted": True,
    })
    await hass.async_block_till_done()
    state = hass.states.get("media_player.virtual_tv")
    assert state.state == "paused"
    assert state.attributes["media_position"] == 450
    assert state.attributes["volume_level"] == 0
    assert state.attributes["is_volume_muted"] is True
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    state = hass.states.get("media_player.virtual_tv")
    assert state.state == "paused"
    assert state.attributes["media_position"] == 450
    assert state.attributes["volume_level"] == 0
    for source in sources:
        hass.states.async_set(source, "off")
    await hass.async_block_till_done()
    state = hass.states.get("media_player.virtual_tv")
    assert state.state == "off"
    assert "media_title" not in state.attributes
    hass.states.async_set(sources[-1], "playing")
    await hass.async_block_till_done()
    state = hass.states.get("media_player.virtual_tv")
    assert state.state == "playing"
    for name in ("media_title", "media_position", "media_duration", "volume_level"):
        assert name not in state.attributes
    assert "Unable to render native template" not in caplog.text
    assert await hass.config_entries.async_unload(entry.entry_id)
