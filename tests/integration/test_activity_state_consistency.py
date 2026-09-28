"""HA publishes consistent power/activity through source updates and reload."""

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.virtual_layer.const import COMPONENT_DOMAIN


@pytest.mark.parametrize("domain,power,activity_key,active", [
    ("climate", "heat", "hvac_action", "heating"),
    ("humidifier", "on", "action", "humidifying"),
])
async def test_source_activity_consistency(hass, tmp_path, monkeypatch, domain, power, activity_key, active):
    monkeypatch.setattr(
        "custom_components.virtual_layer.cfg.default_meta_file",
        lambda _hass: str(tmp_path / "virtual_layer.meta.json"),
    )
    source = f"{domain}.activity_source"
    target = f"{domain}.activity_virtual"
    hass.states.async_set(source, power, {activity_key: "off"})
    entity = {
        "platform": domain, "name": "Activity", "entity_id": target,
        "initial_value": "off", "source_entities": [source],
        "value_template": "{{ states('" + source + "') }}",
        "native_templates": {
            activity_key: "{{ state_attr('" + source + "', '" + activity_key + "') }}",
        },
    }
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN, title="Activity",
        data={"group_name": "Activity"},
        options={"devices": {"Activity": [entity]}},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    state = hass.states.get(target)
    assert state.state == power
    assert state.attributes.get(activity_key) is None
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(target).attributes.get(activity_key) is None
    for source_power, action, expected in [
        (power, active, active), ("off", active, "off"), (power, "idle", "idle"),
    ]:
        hass.states.async_set(source, source_power, {activity_key: action})
        await hass.async_block_till_done()
        state = hass.states.get(target)
        assert state.state == source_power
        assert state.attributes.get(activity_key) == expected
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(target) is None
