"""Schema coverage for every advertised virtual entity domain."""

from importlib import import_module

import pytest
from homeassistant.const import ATTR_ENTITY_ID

from custom_components.virtual_layer.const import (
    CONF_ATTRIBUTE,
    CONF_INITIAL_VALUE,
    CONF_NAME,
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
