"""Invalid native commands must not execute configured external actions."""

from importlib import import_module
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.virtual_layer.config_flow import _platform_schema


CASES = [
    ("select", "select_option", {"option": "bad"}, {"options": ["eco"]}),
    ("text", "set_value", {"value": "a"}, {"min": 2}),
    ("text", "set_value", {"value": "bad"}, {"pattern": "[0-9]+"}),
    ("date", "set_value", {"value": "not-a-date"}, {}),
    ("time", "set_value", {"value": "25:90"}, {}),
    ("datetime", "set_value", {"value": "not-a-date"}, {}),
    ("number", "set_native_value", {"value": float("nan")}, {"min": 0, "max": 100}),
    ("fan", "set_direction", {"direction": "sideways"}, {}),
    ("fan", "oscillate", {"oscillating": "false"}, {}),
    ("water_heater", "set_temperature", {"temperature": 20}, {"min_temp": 35}),
    ("water_heater", "set_operation_mode", {"operation_mode": "bad"}, {"operation_list": ["off", "eco"]}),
    ("media_player", "set_volume_level", {"volume": 2}, {}),
    ("media_player", "select_source", {"source": "bad"}, {"source_list": ["TV"]}),
    ("media_player", "select_sound_mode", {"sound_mode": "bad"}, {"sound_mode_list": ["music"]}),
    ("media_player", "set_shuffle", {"shuffle": "false"}, {}),
    ("media_player", "mute_volume", {"mute": "false"}, {}),
    ("media_player", "set_repeat", {"repeat": "forever"}, {}),
    ("remote", "turn_on", {"activity": "bad"}, {"activity_list": ["TV"]}),
    ("siren", "turn_on", {"tone": "bad"}, {"available_tones": ["alarm"]}),
    ("vacuum", "set_fan_speed", {"fan_speed": "bad"}, {"fan_speed_list": ["quiet"]}),
    ("cover", "set_cover_position", {"position": True}, {}),
    ("cover", "set_cover_tilt_position", {"tilt_position": True}, {}),
    ("valve", "set_valve_position", {"position": True}, {}),
    ("update", "install", {"version": "bad", "backup": False}, {"versions": ["1.1"]}),
]

VALID_ARGUMENTS = {
    ("select", "select_option"): {"option": "eco"},
    ("text", "set_value"): {"value": "123"},
    ("date", "set_value"): {"value": "2026-09-26"},
    ("time", "set_value"): {"value": "12:30:00"},
    ("datetime", "set_value"): {"value": "2026-09-26T12:30:00+09:00"},
    ("number", "set_native_value"): {"value": 50},
    ("fan", "set_direction"): {"direction": "forward"},
    ("fan", "oscillate"): {"oscillating": False},
    ("water_heater", "set_temperature"): {"temperature": 40},
    ("water_heater", "set_operation_mode"): {"operation_mode": "eco"},
    ("media_player", "set_volume_level"): {"volume": 0.4},
    ("media_player", "select_source"): {"source": "TV"},
    ("media_player", "select_sound_mode"): {"sound_mode": "music"},
    ("media_player", "set_shuffle"): {"shuffle": False},
    ("media_player", "mute_volume"): {"mute": False},
    ("media_player", "set_repeat"): {"repeat": "off"},
    ("remote", "turn_on"): {"activity": "TV"},
    ("siren", "turn_on"): {"tone": "alarm"},
    ("vacuum", "set_fan_speed"): {"fan_speed": "quiet"},
    ("cover", "set_cover_position"): {"position": 50},
    ("cover", "set_cover_tilt_position"): {"tilt_position": 50},
    ("valve", "set_valve_position"): {"position": 50},
    ("update", "install"): {"version": "1.1", "backup": False},
}


@pytest.mark.parametrize("optimistic", [False, True])
@pytest.mark.parametrize("domain,command,arguments,config", CASES)
async def test_invalid_command_is_rejected_before_actions(hass, domain, command, arguments, config, optimistic):
    module = import_module(f"custom_components.virtual_layer.{domain}")
    cls = getattr(module, "ENTITY_CLASS", None)
    if cls is None:
        cls = getattr(module, "Virtual" + "".join(part.title() for part in domain.split("_")))
    entity = cls(_platform_schema(domain)({
        "name": "Preflight", "entity_id": f"{domain}.preflight",
        "initial_value": "0" if domain == "number" else "off",
        "command_actions": {command: {
            "sequence": [{"action": "audit.capture"}], "optimistic": optimistic,
        }}, **config,
    }), False)
    entity.hass = hass
    entity._create_state(entity._config)
    entity.async_write_ha_state = Mock()
    entity._schedule_state_update = Mock()
    capture = AsyncMock()
    hass.services.async_register("audit", "capture", capture)
    before = entity.state
    with pytest.raises((ValueError, TypeError)):
        await getattr(entity, f"async_{command}")(**arguments)
    capture.assert_not_awaited()
    assert entity.state == before
    # The same action remains usable after a rejected command, including when
    # optimistic local mutation is disabled.
    valid_arguments = VALID_ARGUMENTS[domain, command]
    await getattr(entity, f"async_{command}")(**valid_arguments)
    capture.assert_awaited_once()
    if not optimistic:
        assert entity.state == before
    await entity.async_will_remove_from_hass()
