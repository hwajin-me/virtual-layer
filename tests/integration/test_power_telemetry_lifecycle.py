"""Fan and every light profile keep power consistent through live updates."""

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.virtual_layer.const import COMPONENT_DOMAIN


@pytest.mark.parametrize("domain,profile", [
    ("fan", None), ("light", "on_off"), ("light", "dimmable"),
    ("light", "color_temperature"), ("light", "extended_color"),
])
async def test_power_telemetry_source_updates_and_reload(hass, tmp_path, monkeypatch, domain, profile):
    monkeypatch.setattr(
        "custom_components.virtual_layer.cfg.default_meta_file",
        lambda _hass: str(tmp_path / "virtual_layer.meta.json"),
    )
    source, target = f"{domain}.physical", f"{domain}.power_audit"
    telemetry = {"percentage": 75, "preset_mode": "Auto"} if domain == "fan" else {
        "brightness": 150, "color_temp_kelvin": 4000, "hs_color": [30, 50],
    }
    entity = {
        "platform": domain, "name": "Power audit", "entity_id": target,
        "initial_value": "off", "source_entities": [source],
        "value_template": "{{ states('" + source + "') }}",
        "availability_template": "{{ has_value('" + source + "') }}",
        "native_templates": {
            key: "{{ state_attr('" + source + "', '" + key + "') }}"
            for key in telemetry
        },
    }
    if domain == "fan":
        entity.update({"speed_count": 100, "modes": ["Auto"]})
    else:
        entity["matter_light_type"] = profile
    hass.states.async_set(source, "off", telemetry)
    entry = MockConfigEntry(domain=COMPONENT_DOMAIN, title="Power audit",
        data={"group_name": "Power audit"}, options={"devices": {"Power audit": [entity]}})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    def check(power):
        state = hass.states.get(target)
        assert state is not None and state.state == power
        if domain == "fan":
            assert state.attributes.get("percentage") == (75 if power == "on" else 0)
            assert state.attributes.get("preset_mode") == ("Auto" if power == "on" else None)
        elif power == "off" or profile == "on_off":
            for key in telemetry:
                assert state.attributes.get(key) is None
        else:
            assert state.attributes["brightness"] == 150

    check("off")
    for power in ("on", "off", "on"):
        hass.states.async_set(source, power, telemetry)
        await hass.async_block_till_done()
        check(power)
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        check(power)
    hass.states.async_set(source, "unavailable")
    await hass.async_block_till_done()
    assert hass.states.get(target).state == "unavailable"
    hass.states.async_set(source, "off", telemetry)
    await hass.async_block_till_done()
    check("off")
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(target) is None
