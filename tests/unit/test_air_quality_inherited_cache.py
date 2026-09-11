"""Bridge creation must preserve the age of an upstream cached grade."""

import pytest

from custom_components.virtual_layer.sensor import VirtualSensor
from custom_components.virtual_layer.const import DIAGNOSTIC_UNIQUE_ID_MARKER

pytestmark = pytest.mark.unit


def test_new_bridge_inherits_stale_grade_then_survives_source_removal(hass):
    timestamp = "2026-09-01T10:00:00+00:00"
    hass.states.async_set("air_quality.cached", "poor", {
        "air_quality_stale": True, "air_quality_last_valid_at": timestamp,
    })
    config = {"name": "Bridge", "entity_id": "sensor.bridge",
              "unique_id": "parent" + DIAGNOSTIC_UNIQUE_ID_MARKER + "air_quality",
              "attributes": {"sensor_type": "matter_air_quality"},
              "initial_value": "unknown", "initial_availability": True,
              "source_entities": ["air_quality.cached"],
              "value_template": "{{ states('air_quality.cached') }}"}
    entity = VirtualSensor(config, False)
    entity.hass = hass
    entity._schedule_state_update = lambda **kwargs: None
    entity._create_state(config)
    entity._apply_templates()
    assert entity.native_value == "poor"
    assert entity.extra_state_attributes["air_quality_stale"] is True
    assert entity.extra_state_attributes["air_quality_last_valid_at"] == timestamp
    hass.states.async_remove("air_quality.cached")
    entity._apply_templates()
    assert entity.native_value == "poor"
    assert entity.extra_state_attributes["air_quality_last_valid_at"] == timestamp
    assert entity.extra_state_attributes["air_quality_fallback_reason"] == "sources_unavailable"
