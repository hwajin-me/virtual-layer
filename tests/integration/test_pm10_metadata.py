"""PM10 metadata survives source outages and initial missing attributes."""

from unittest.mock import Mock

import pytest

from custom_components.virtual_layer.sensor import VirtualSensor


@pytest.mark.parametrize("configured_icon", [None, "mdi:blur"])
async def test_pm10_metadata_recovers_from_missing_source(hass, configured_icon):
    config = {
        "name": "PM 10.0",
        "entity_id": "sensor.virtual_pm10",
        "class": "pm10",
        "initial_value": 12,
        "icon": configured_icon,
        "icon_template": "{{ state_attr('sensor.source_pm10', 'icon') }}",
        "native_templates": {
            "native_unit_of_measurement": (
                "{{ state_attr('sensor.source_pm10', 'unit_of_measurement') }}"
            ),
        },
    }
    entity = VirtualSensor(config, False)
    entity.hass = hass
    entity.async_schedule_update_ha_state = Mock()
    entity._create_state(config)
    entity._setup_templates()
    try:
        entity._apply_templates()
        assert entity.native_unit_of_measurement == "μg/m³"
        assert entity.icon == (configured_icon or "mdi:air-filter")

        hass.states.async_set("sensor.source_pm10", "12", {
            "unit_of_measurement": "mg/m³", "icon": "mdi:weather-dust",
        })
        await hass.async_block_till_done()
        entity._apply_templates()
        assert entity.native_unit_of_measurement == "mg/m³"
        assert entity.icon == "mdi:weather-dust"

        hass.states.async_set("sensor.source_pm10", "unavailable")
        await hass.async_block_till_done()
        entity._apply_templates()
        assert entity.native_unit_of_measurement == "mg/m³"
        assert entity.icon == (configured_icon or "mdi:air-filter")
    finally:
        await entity.async_will_remove_from_hass()
