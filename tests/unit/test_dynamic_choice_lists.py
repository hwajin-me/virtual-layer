"""Removed source capabilities must not leave stale selected values."""

import pytest
from unittest.mock import Mock
from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.components.vacuum import VacuumEntityFeature

from custom_components.virtual_layer.generic import VirtualMediaPlayer
from custom_components.virtual_layer.media_player import ENTITY_SCHEMA as GENERIC_SCHEMA
from custom_components.virtual_layer.vacuum import VirtualVacuum, VACUUM_SCHEMA


@pytest.mark.parametrize("choices,selected,value,feature", [
    ("source_list", "source", "TV", MediaPlayerEntityFeature.SELECT_SOURCE),
    ("sound_mode_list", "sound_mode", "music", MediaPlayerEntityFeature.SELECT_SOUND_MODE),
])
def test_media_player_clears_selection_when_choices_become_empty(choices, selected, value, feature):
    entity = VirtualMediaPlayer(GENERIC_SCHEMA({"name": "Player", "initial_value": "off"}), False)
    entity._create_state(entity._config)
    entity._apply_native_template_value(choices, [value])
    entity._apply_native_template_value(selected, value)
    entity._native_templates_applied()
    assert getattr(entity, selected) == value
    assert entity.supported_features & feature
    entity._apply_native_template_value(choices, [])
    entity._apply_native_template_value(selected, value)  # Delayed stale source attribute.
    entity._native_templates_applied()
    assert getattr(entity, selected) is None
    assert not entity.supported_features & feature


def test_vacuum_clears_speed_after_source_removes_all_speed_options():
    entity = VirtualVacuum(VACUUM_SCHEMA({"name": "Vacuum", "fan_speed_list": ["quiet"]}), False)
    entity._apply_native_template_value("fan_speed", "quiet")
    entity._native_templates_applied()
    assert entity.fan_speed == "quiet"
    entity._apply_native_template_value("fan_speed_list", [])
    entity._apply_native_template_value("fan_speed", "quiet")
    entity._native_templates_applied()
    assert entity.fan_speed is None
    assert not entity.supported_features & VacuumEntityFeature.FAN_SPEED


def test_scalar_source_metadata_without_a_declared_list_is_preserved():
    media = VirtualMediaPlayer(GENERIC_SCHEMA({"name": "Player"}), False)
    media._apply_native_template_value("source", "HDMI")
    media._native_templates_applied()
    assert media.source == "HDMI"
    vacuum = VirtualVacuum(VACUUM_SCHEMA({"name": "Vacuum"}), False)
    vacuum._apply_native_template_value("fan_speed", "custom")
    vacuum._native_templates_applied()
    assert vacuum.fan_speed == "custom"


async def test_legacy_media_sound_modes_are_native_and_remain_editable(hass):
    entity = VirtualMediaPlayer(GENERIC_SCHEMA({
        "name": "Legacy sound", "entity_id": "media_player.legacy_sound",
        "sound_mode_list": ["movie", "music"], "sound_mode": "movie",
    }), False)
    entity.hass = hass
    entity.async_write_ha_state = Mock()
    entity._create_state(entity._config)
    assert entity.sound_mode_list == ["movie", "music"]
    assert entity.sound_mode == "movie"
    assert entity.supported_features & MediaPlayerEntityFeature.SELECT_SOUND_MODE
    await entity.async_select_sound_mode("music")
    entity._update_attributes()
    assert entity.sound_mode == "music"
    assert "sound_mode" not in entity.extra_state_attributes
    assert "sound_mode_list" not in entity.extra_state_attributes
