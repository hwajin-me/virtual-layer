"""Schema coverage for every advertised virtual entity domain."""

from importlib import import_module

import pytest
import voluptuous as vol
from homeassistant.const import ATTR_ENTITY_ID

from custom_components.virtual_layer.const import (
    CONF_ATTRIBUTE,
    CONF_INITIAL_VALUE,
    CONF_NAME,
    CONF_NATIVE_TEMPLATES,
    CONF_TEMPLATE_SOURCES,
    VIRTUAL_ENTITY_DOMAINS,
)

GENERIC_DIRECT_OPTION_DOMAINS = (
    "ai_task",
    "air_quality",
    "alarm_control_panel",
    "assist_satellite",
    "button",
    "calendar",
    "conversation",
    "date",
    "datetime",
    "event",
    "geolocation",
    "image",
    "image_processing",
    "infrared",
    "lawn_mower",
    "media_player",
    "notify",
    "radio_frequency",
    "remote",
    "scene",
    "select",
    "siren",
    "stt",
    "tag",
    "text",
    "time",
    "todo",
    "tts",
    "update",
    "vacuum",
    "wake_word",
    "water_heater",
    "weather",
)


pytestmark = pytest.mark.unit


def test_every_domain_schema_accepts_composite_template_sources():
    for domain in VIRTUAL_ENTITY_DOMAINS:
        module = import_module(f"custom_components.virtual_layer.{domain}")
        schema = (
            getattr(module, f"{domain.upper()}_SCHEMA", None)
            or module.ENTITY_SCHEMA
        )
        config = {
            CONF_NAME: "Schema validation entity",
            CONF_TEMPLATE_SOURCES: {
                "source": {
                    ATTR_ENTITY_ID: "sensor.source",
                    CONF_ATTRIBUTE: "state",
                },
            },
        }
        if domain == "climate":
            config[CONF_INITIAL_VALUE] = "off"
        if domain == "number":
            config.update({"min": 0, "max": 100})

        validated = schema(config)

        assert (
            validated[CONF_TEMPLATE_SOURCES]["source"][ATTR_ENTITY_ID]
            == "sensor.source"
        ), domain


def test_generic_domain_schemas_accept_direct_ui_options():
    direct_option = {"enabled": True, "values": [1, 2]}

    for domain in GENERIC_DIRECT_OPTION_DOMAINS:
        module = import_module(f"custom_components.virtual_layer.{domain}")
        validated = module.ENTITY_SCHEMA({
            CONF_NAME: "Direct configuration entity",
            "yaml_only_option": direct_option,
        })

        assert validated["yaml_only_option"] == direct_option, domain


def test_climate_native_templates_override_stale_static_fallbacks():
    """Jinja native values must be able to replace legacy copied options."""
    from custom_components.virtual_layer.climate import validate_domain_options

    validate_domain_options({
        CONF_INITIAL_VALUE: "heat",
        "hvac_modes": ["off", "cool"],
        "fan_modes": ["auto"],
        "fan_mode": "auto",
        "target_temperature": 40,
        "min_temp": 7,
        "max_temp": 35,
        "temperature_unit": "°C",
        "min_humidity": 0,
        "max_humidity": 100,
        CONF_NATIVE_TEMPLATES: {
            "hvac_modes": "{{ ['off', 'heat'] }}",
            "fan_modes": "{{ ['auto', 'quiet'] }}",
            "fan_mode": "{{ 'quiet' }}",
            "target_temperature": "{{ 21 }}",
            "min_temp": "{{ 18 }}",
            "max_temp": "{{ 25 }}",
        },
    })


@pytest.mark.parametrize("supported_features", [-1, ["start", -1]])
def test_vacuum_schema_rejects_negative_feature_bitmasks(supported_features):
    from custom_components.virtual_layer.vacuum import VACUUM_SCHEMA

    with pytest.raises(vol.Invalid):
        VACUUM_SCHEMA({
            CONF_NAME: "Invalid feature vacuum",
            "supported_features": supported_features,
        })
