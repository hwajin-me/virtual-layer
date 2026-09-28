"""Humidifier power and activity must not contradict each other in HA."""

import pytest
from homeassistant.core import State

from custom_components.virtual_layer.humidifier import HUMIDIFIER_SCHEMA, VirtualHumidifier


@pytest.mark.parametrize("device_class", ["humidifier", "dehumidifier"])
@pytest.mark.parametrize("power", ["on", "off"])
@pytest.mark.parametrize("activity", [None, "off", "idle", "humidifying", "drying"])
def test_published_activity_matches_power(device_class, power, activity):
    config = HUMIDIFIER_SCHEMA({
        "name": "Humidity", "initial_value": power, "class": device_class,
    })
    entity = VirtualHumidifier(config, False)
    entity._create_state(config)
    entity._update_attributes()
    expected = "off" if power == "off" else None if activity == "off" else activity

    # Cover restored snapshots as well as source/native-template updates.
    entity._restore_state(State("humidifier.test", power, {"action": activity}), config)
    assert entity.action == expected
    entity._apply_native_template_value("action", activity)
    entity._native_templates_applied()
    assert entity.state == power
    assert entity.action == expected
    assert entity.state_attributes.get("action") == expected

    # The same retained helper value must remain consistent across power changes.
    entity.set_state("off")
    entity._apply_native_template_value("action", activity)
    assert entity.action == "off"
