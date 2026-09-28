"""Climate operating mode and published action remain consistent."""

import pytest
from homeassistant.components.climate import HVACAction, HVACMode
from homeassistant.core import State

from custom_components.virtual_layer.climate import CLIMATE_SCHEMA, VirtualClimate


@pytest.mark.parametrize("mode", list(HVACMode))
@pytest.mark.parametrize("action", [None, *HVACAction])
def test_climate_activity_after_restore_and_template_update(hass, mode, action):
    config = CLIMATE_SCHEMA({
        "name": "Climate", "initial_value": mode,
        "hvac_modes": list(HVACMode),
    })
    entity = VirtualClimate(config, False)
    entity.hass = hass
    entity._create_state(config)
    entity._update_attributes()
    expected = HVACAction.OFF if mode == HVACMode.OFF else (
        None if action == HVACAction.OFF else action
    )
    entity._restore_state(State("climate.test", mode, {"hvac_action": action}), config)
    assert entity.hvac_action == expected
    # A source action may be evaluated after the mode template.
    entity.set_state(mode)
    entity._apply_native_template_value("hvac_action", action)
    entity._native_templates_applied()
    assert entity.state == mode
    assert entity.hvac_action == expected
    assert entity.state_attributes.get("hvac_action") == expected
