"""Power templates must remain authoritative over secondary telemetry."""

from unittest.mock import Mock

import pytest

from custom_components.virtual_layer.fan import FAN_SCHEMA, VirtualFan
from custom_components.virtual_layer.light import LIGHT_SCHEMA, VirtualLight


@pytest.mark.parametrize("power", ["on", "off"])
@pytest.mark.parametrize("speed", [None, 0, 75])
@pytest.mark.parametrize("preset", [None, "Auto"])
def test_fan_value_template_power_overrides_speed(hass, power, speed, preset):
    entity = VirtualFan(FAN_SCHEMA({
        "name": "Fan", "speed_count": 100, "modes": ["Auto"],
        "value_template": "{{ '" + power + "' }}",
        "native_templates": {
            "percentage": "{{ " + (repr(speed) if speed is not None else "none") + " }}",
            "preset_mode": "{{ " + (repr(preset) if preset is not None else "none") + " }}",
        },
    }), False)
    entity.hass = hass
    entity._create_state(entity._config)
    entity._schedule_state_update = Mock()
    for _ in range(2):
        entity._apply_templates()
        assert entity.state == power
        assert entity.percentage == (speed if power == "on" else 0)
        assert entity.preset_mode == (preset if power == "on" else None)


@pytest.mark.parametrize("power", ["on", "off"])
@pytest.mark.parametrize("brightness", [0, 125, 255])
@pytest.mark.parametrize("reverse", [False, True])
def test_light_power_independent_of_brightness(hass, power, brightness, reverse):
    values = [("is_on", "{{ " + repr(power == "on") + " }}"),
              ("brightness", "{{ " + str(brightness) + " }}")]
    entity = VirtualLight(LIGHT_SCHEMA({
        "name": "Light", "support_brightness": True,
        "native_templates": dict(reversed(values) if reverse else values),
    }), False)
    entity.hass = hass
    entity._create_state(entity._config)
    entity._schedule_state_update = Mock()
    for _ in range(2):
        entity._apply_templates()
        assert entity.state == power
        assert entity.brightness == (brightness if power == "on" else None)
        if power == "off":
            assert entity.color_mode is None
            assert entity.extra_state_attributes.get("brightness") is None
