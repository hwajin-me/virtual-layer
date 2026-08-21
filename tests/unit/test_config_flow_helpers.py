"""Unit tests for Virtual Layer config flow helpers."""

import json
import logging
from datetime import timedelta
from types import MappingProxyType

import pytest
import voluptuous as vol
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_FRIENDLY_NAME,
    CONF_ICON,
    CONF_NAME,
    CONF_PLATFORM,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import selector
from homeassistant.helpers.template import Template
from homeassistant.util import dt as dt_util
from voluptuous_serialize import convert

from custom_components.virtual_layer.config_flow import (
    CLIMATE_NATIVE_TEMPLATE_PROPERTIES,
    CONF_ADVANCED_SETTINGS,
    CONF_ATTRIBUTE_SOURCES_JSON,
    CONF_ATTRIBUTE_TEMPLATES_JSON,
    CONF_ATTRIBUTES_JSON,
    CONF_COMMAND_ACTIONS_JSON,
    CONF_DEVICE_CONFIGURATION_URL,
    CONF_DEVICE_DETAILS,
    CONF_DEVICE_HW_VERSION,
    CONF_DEVICE_ID,
    CONF_DEVICE_MANUFACTURER,
    CONF_DEVICE_MODEL,
    CONF_DEVICE_NAME,
    CONF_DEVICE_SERIAL_NUMBER,
    CONF_DEVICE_SUGGESTED_AREA,
    CONF_DEVICE_SW_VERSION,
    CONF_DEVICE_VIA_DEVICE_ID,
    CONF_DOMAIN_OPTIONS_JSON,
    CONF_DOMAIN_SETTINGS,
    CONF_ENTITY_NAME,
    CONF_EVENT_HOOKS_JSON,
    CONF_NATIVE_TEMPLATES_JSON,
    CONF_NATIVE_VALUE_TEMPLATES,
    CONF_REFERENCE_ENTITY_ID,
    CONF_SOURCE_ENTITIES_TEXT,
    CONF_TEMPLATE_SOURCES_JSON,
    DOMAIN_NATIVE_TEMPLATE_PROPERTIES,
    FAN_NATIVE_TEMPLATE_PROPERTIES,
    HUMIDIFIER_NATIVE_TEMPLATE_PROPERTIES,
    NATIVE_TEMPLATE_BITMASK_PROPERTIES,
    NATIVE_TEMPLATE_BOOLEAN_PROPERTIES,
    NATIVE_TEMPLATE_DATETIME_PROPERTIES,
    NATIVE_TEMPLATE_LIST_PROPERTIES,
    NATIVE_TEMPLATE_MAPPING_PROPERTIES,
    NATIVE_TEMPLATE_MAXIMUM_PROPERTIES,
    NATIVE_TEMPLATE_MINIMUM_PROPERTIES,
    NATIVE_TEMPLATE_NUMERIC_PROPERTIES,
    NATIVE_TEMPLATE_ATTRIBUTE_ALIASES,
    DeviceNameAlreadyUsed,
    InvalidDomainOptions,
    InvalidEntityId,
    InvalidEntityReference,
    InvalidEntitySelection,
    InvalidJson,
    _append_ui_entity,
    _auto_helper_profile,
    _build_device_config,
    _build_entity_config,
    _default_virtual_entity_id,
    _delete_entities_schema,
    _delete_ui_device,
    _delete_ui_entities,
    _device_schema,
    _domain_options_error_field,
    _entity_choices,
    _entity_form_defaults,
    _entity_key,
    _entity_key_from_stable_key,
    _entity_schema,
    _find_entity_by_selection_key,
    _flatten_entity_form_sections,
    _flow_errors,
    _helper_update_schema,
    _helper_usage_schema,
    _json_default,
    _log_unhandled_flow_errors,
    _managed_device_choices,
    _merged_native_template,
    _native_reference_templates,
    _native_source_template,
    _needs_domain_specific_form,
    _normalize_reference_entity_ids,
    _options_schema,
    _parse_command_actions,
    _parse_entity_key,
    _parse_json_object,
    _parse_json_value,
    _parse_native_templates,
    _parse_source_entities,
    _plain_options,
    _reference_edit_defaults,
    _reference_entity_defaults,
    _reference_entity_schema,
    _replace_ui_device,
    _replace_ui_entity,
    _select_device_schema,
    _select_entity_schema,
    _set_auto_helper_profile,
    _setup_schema,
    _stored_entity_ids,
    _validate_platform_entity,
    _without_template_helpers,
)
from custom_components.virtual_layer.const import (
    ATTR_DEVICE_ATTRIBUTES,
    ATTR_DEVICE_ID,
    ATTR_DEVICES,
    ATTR_ENTITY_KEY,
    CONF_ATTRIBUTE,
    CONF_ATTRIBUTE_SOURCES,
    CONF_ATTRIBUTE_TEMPLATES,
    CONF_ATTRIBUTES,
    CONF_AVAILABILITY_TEMPLATE,
    CONF_CLASS,
    CONF_COMMAND_ACTIONS,
    CONF_CONFIGURATION_URL,
    CONF_EVENT_HOOKS,
    CONF_HW_VERSION,
    CONF_ICON_TEMPLATE,
    CONF_INITIAL_AVAILABILITY,
    CONF_INITIAL_VALUE,
    CONF_LOCATION_HELPER,
    CONF_MANUFACTURER,
    CONF_MAX,
    CONF_MIN,
    CONF_MODEL,
    CONF_NATIVE_TEMPLATES,
    CONF_PERSISTENT,
    CONF_POLYGON_AWAY_STATE,
    CONF_POLYGON_DISTANCE_METERS,
    CONF_POLYGON_FILES,
    CONF_POLYGON_STRATEGY,
    CONF_POLYGONAL_ZONE,
    CONF_PULL_INTERVAL,
    CONF_SERIAL_NUMBER,
    CONF_SOURCE_ENTITIES,
    CONF_SUGGESTED_AREA,
    CONF_SW_VERSION,
    CONF_TEMPLATE_SOURCES,
    CONF_VALUE_TEMPLATE,
    CONF_VIA_DEVICE_ID,
    VIRTUAL_ENTITY_DOMAINS,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "progress",
    [101, pytest.param(10**10000, id="huge-integer")],
)
def test_update_domain_options_reject_out_of_range_progress(progress):
    from custom_components.virtual_layer.update import (
        ENTITY_SCHEMA,
        validate_domain_options,
    )

    with pytest.raises(InvalidDomainOptions):
        _validate_platform_entity(
            {
                CONF_PLATFORM: "update",
                CONF_NAME: "Invalid Update",
                CONF_INITIAL_VALUE: "1.0.0",
                "update_percentage": progress,
            },
            ENTITY_SCHEMA,
            validate_domain_options,
        )


@pytest.mark.parametrize("platform", ["climate", "fan", "humidifier"])
def test_native_domain_validation_errors_are_shown_at_form_level(platform):
    assert _domain_options_error_field({CONF_PLATFORM: platform}) == "base"


def test_advanced_domain_validation_errors_remain_attached_to_options():
    assert _domain_options_error_field({CONF_PLATFORM: "vacuum"}) == (
        CONF_DOMAIN_OPTIONS_JSON
    )


def _entity_input(overrides=None):
    data = {
        CONF_DEVICE_NAME: "Laundry",
        CONF_DEVICE_ID: "",
        CONF_DEVICE_MANUFACTURER: "",
        CONF_DEVICE_MODEL: "",
        CONF_DEVICE_SW_VERSION: "",
        CONF_DEVICE_HW_VERSION: "",
        CONF_DEVICE_SERIAL_NUMBER: "",
        CONF_DEVICE_CONFIGURATION_URL: "",
        CONF_DEVICE_SUGGESTED_AREA: "",
        CONF_DEVICE_VIA_DEVICE_ID: "",
        CONF_ENTITY_NAME: "Washer Phase",
        CONF_ICON: "",
        CONF_ICON_TEMPLATE: "",
        ATTR_ENTITY_ID: "",
        CONF_PLATFORM: "sensor",
        CONF_INITIAL_VALUE: "idle",
        CONF_INITIAL_AVAILABILITY: True,
        CONF_PERSISTENT: True,
        CONF_SOURCE_ENTITIES_TEXT: "",
        CONF_TEMPLATE_SOURCES_JSON: "",
        CONF_PULL_INTERVAL: 0,
        CONF_VALUE_TEMPLATE: "",
        CONF_AVAILABILITY_TEMPLATE: "",
        CONF_EVENT_HOOKS_JSON: "",
        CONF_ATTRIBUTES_JSON: "",
        CONF_ATTRIBUTE_SOURCES_JSON: "",
        CONF_ATTRIBUTE_TEMPLATES_JSON: "",
        CONF_DOMAIN_OPTIONS_JSON: "",
    }
    data.update(overrides or {})
    return data


def _section_validators(schema, section_name):
    outer = {
        marker.schema: validator for marker, validator in schema.schema.items()
    }
    section_schema = outer[section_name].schema
    return {
        marker.schema: validator
        for marker, validator in section_schema.schema.items()
    }


@pytest.mark.parametrize(
    "value",
    [123, {}, ["sensor.valid", 42], ("sensor.valid", None)],
)
def test_source_entity_parser_reports_malformed_payloads_as_field_errors(value):
    with pytest.raises(InvalidEntityReference) as err:
        _parse_source_entities(value)

    assert err.value.field_name == CONF_SOURCE_ENTITIES_TEXT


def test_source_entity_parser_accepts_legacy_sequence_payloads():
    assert _parse_source_entities(
        [" sensor.first ", "sensor.second", "sensor.first"]
    ) == ["sensor.first", "sensor.second"]


@pytest.mark.parametrize("value", [123, {}, ["sensor.valid", 42]])
def test_reference_entity_parser_reports_malformed_payloads_as_field_errors(value):
    with pytest.raises(InvalidEntityReference) as err:
        _normalize_reference_entity_ids(value)

    assert err.value.field_name == CONF_REFERENCE_ENTITY_ID


def test_entity_form_collapses_secondary_fields_and_flattens_submissions():
    schema = _entity_schema({CONF_PLATFORM: "climate"})
    outer = {
        marker.schema: validator for marker, validator in schema.schema.items()
    }

    for section_name in (
        CONF_DEVICE_DETAILS,
        CONF_ADVANCED_SETTINGS,
        CONF_NATIVE_VALUE_TEMPLATES,
    ):
        assert outer[section_name].options["collapsed"] is True
    assert CONF_DOMAIN_SETTINGS not in outer

    flattened = _flatten_entity_form_sections(
        {
            CONF_DEVICE_DETAILS: {CONF_DEVICE_ID: "nested-device"},
            CONF_ADVANCED_SETTINGS: {CONF_ATTRIBUTES_JSON: '{"source": true}'},
            CONF_DOMAIN_SETTINGS: {"fan_mode": "auto"},
            CONF_DEVICE_ID: "flat-device",
        }
    )
    assert flattened[CONF_DEVICE_ID] == "flat-device"
    assert flattened[CONF_ATTRIBUTES_JSON] == '{"source": true}'
    assert flattened["fan_mode"] == "auto"
    assert CONF_DEVICE_DETAILS not in flattened


def test_entity_form_defaults_to_the_selected_domain_prefix():
    defaults = _entity_schema(
        {
            CONF_PLATFORM: "sensor",
            CONF_ENTITY_NAME: "Washer Phase",
        }
    )({})

    assert defaults[ATTR_ENTITY_ID] == "sensor.washer_phase"


def test_entity_form_preserves_an_existing_entity_id():
    defaults = _entity_schema(
        {
            CONF_PLATFORM: "sensor",
            CONF_ENTITY_NAME: "Washer Phase",
            ATTR_ENTITY_ID: "sensor.custom_washer_phase",
        }
    )({})

    assert defaults[ATTR_ENTITY_ID] == "sensor.custom_washer_phase"


def test_entity_form_uses_icon_and_template_selectors():
    schema = _entity_schema({CONF_PLATFORM: "sensor"})
    validators = {
        marker.schema: validator for marker, validator in schema.schema.items()
    }

    assert isinstance(validators[CONF_ICON], selector.IconSelector)
    assert isinstance(validators[CONF_ICON_TEMPLATE], selector.TemplateSelector)


def test_all_config_flow_forms_are_frontend_serializable():
    options = {
        ATTR_DEVICES: {
            "Laundry": [{
                CONF_PLATFORM: "sensor",
                CONF_NAME: "Washer Phase",
                CONF_INITIAL_VALUE: "idle",
            }],
        },
    }
    schemas = [
        *(
            (f"entity:{platform}", _entity_schema({CONF_PLATFORM: platform}))
            for platform in VIRTUAL_ENTITY_DOMAINS
        ),
        ("setup", _setup_schema({})),
        ("options", _options_schema(options)),
        ("reference", _reference_entity_schema(
            device_options=[{"value": "Laundry", "label": "Laundry"}],
        )),
        ("helper_update", _helper_update_schema()),
        ("helper_usage", _helper_usage_schema()),
        ("device", _device_schema()),
        ("select_device", _select_device_schema(options)),
        ("select_entity", _select_entity_schema(options)),
        ("delete_entities", _delete_entities_schema(options)),
    ]

    for name, schema in schemas:
        assert convert(schema, custom_serializer=cv.custom_serializer), name


def test_native_value_template_sections_match_domain_properties():
    common_template_only_domains = {
        "infrared",
        "radio_frequency",
        "scene",
        "tag",
        "wake_word",
    }
    assert set(DOMAIN_NATIVE_TEMPLATE_PROPERTIES).isdisjoint(
        common_template_only_domains
    )
    assert set(DOMAIN_NATIVE_TEMPLATE_PROPERTIES) | common_template_only_domains == set(
        VIRTUAL_ENTITY_DOMAINS
    )

    for platform, expected_properties in sorted(
        DOMAIN_NATIVE_TEMPLATE_PROPERTIES.items()
    ):
        schema = _entity_schema({CONF_PLATFORM: platform})
        validators = {
            marker.schema: validator
            for marker, validator in schema.schema.items()
        }
        native_section = validators[CONF_NATIVE_VALUE_TEMPLATES]
        template_validators = {
            marker.schema: validator
            for marker, validator in native_section.schema.schema.items()
        }

        assert set(template_validators) == set(expected_properties), platform
        assert CONF_NATIVE_TEMPLATES_JSON not in validators, platform
        assert all(
            isinstance(validator, selector.TemplateSelector)
            for validator in template_validators.values()
        ), platform


def _native_helper_sample(platform: str, property_name: str, index: int):
    if property_name in NATIVE_TEMPLATE_DATETIME_PROPERTIES:
        return f"2026-08-{10 + index:02d}T12:00:00+00:00"
    if property_name == "native_value":
        if platform == "number":
            return 10 + index
        if platform == "date":
            return f"2026-08-{10 + index:02d}"
        if platform == "datetime":
            return f"2026-08-{10 + index:02d}T12:00:00+00:00"
        if platform == "time":
            return f"{10 + index:02d}:00:00"
        return f"value-{index}"
    if property_name in NATIVE_TEMPLATE_BOOLEAN_PROPERTIES:
        return index == 0
    if property_name in NATIVE_TEMPLATE_BITMASK_PROPERTIES:
        return 1 << index
    if property_name == "gps":
        return [37.5 + index, 127.0 + index]
    if property_name in {"hs_color", "xy_color"}:
        return [10 + index, 20 + index]
    if property_name == "rgb_color":
        return [10 + index, 20 + index, 30 + index]
    if property_name == "rgbw_color":
        return [10 + index, 20 + index, 30 + index, 40 + index]
    if property_name == "rgbww_color":
        return [10 + index, 20 + index, 30 + index, 40 + index, 50 + index]
    if property_name == "todo_items":
        return [{"summary": f"item-{index}"}]
    if property_name in NATIVE_TEMPLATE_LIST_PROPERTIES:
        return [f"{property_name}-{index}"]
    if property_name in NATIVE_TEMPLATE_MAPPING_PROPERTIES:
        return {f"key_{index}": f"value-{index}"}
    if (
        property_name in NATIVE_TEMPLATE_NUMERIC_PROPERTIES
        or property_name in NATIVE_TEMPLATE_MINIMUM_PROPERTIES
        or property_name in NATIVE_TEMPLATE_MAXIMUM_PROPERTIES
    ):
        return 10 + index
    return f"value-{index}"


def test_all_native_jinja_fields_have_renderable_source_helpers(hass):
    for platform, properties in DOMAIN_NATIVE_TEMPLATE_PROPERTIES.items():
        source = f"{platform}.helper_source"
        hass.states.async_set(
            source,
            "active",
            {property_name: property_name for property_name in properties},
        )
        defaults = _reference_entity_defaults(hass, [source])

        if platform == "device_tracker":
            assert CONF_NATIVE_VALUE_TEMPLATES not in defaults, platform
        else:
            assert set(defaults[CONF_NATIVE_VALUE_TEMPLATES]) == set(properties), platform
            for property_name in properties:
                expected = _native_source_template(
                    source,
                    hass.states.get(source),
                    property_name,
                )
                assert defaults[CONF_NATIVE_VALUE_TEMPLATES][property_name] == expected, (
                    f"{platform}.{property_name}"
                )

        sources = [f"{platform}.helper_first", f"{platform}.helper_second"]
        for index, entity_id in enumerate(sources):
            hass.states.async_set(
                entity_id,
                "active",
                {
                    property_name: _native_helper_sample(
                        platform, property_name, index
                    )
                    for property_name in properties
                },
            )
        templates = _native_reference_templates(
            platform,
            sources,
            [hass.states.get(entity_id) for entity_id in sources],
        )
        assert set(templates) == set(properties), platform
        for property_name, template in templates.items():
            rendered = Template(template, hass).async_render(parse_result=True)
            assert rendered is not None, f"{platform}.{property_name}: {template}"

        missing_source = f"{platform}.minimal_source"
        hass.states.async_set(missing_source, "active")
        missing_templates = _native_reference_templates(
            platform,
            [missing_source],
            [hass.states.get(missing_source)],
        )
        assert set(missing_templates) == set(properties), platform
        assert all(missing_templates.values()), platform
        for property_name, template in missing_templates.items():
            Template(template, hass).async_render(parse_result=True)

        missing_sources = [
            f"{platform}.minimal_first",
            f"{platform}.minimal_second",
        ]
        for entity_id in missing_sources:
            hass.states.async_set(entity_id, "active")
        missing_merged_templates = _native_reference_templates(
            platform,
            missing_sources,
            [hass.states.get(entity_id) for entity_id in missing_sources],
        )
        assert set(missing_merged_templates) == set(properties), platform
        for property_name, template in missing_merged_templates.items():
            Template(template, hass).async_render(parse_result=True)

        for property_name in properties:
            alias = NATIVE_TEMPLATE_ATTRIBUTE_ALIASES.get(property_name)
            if not alias or alias == property_name:
                continue
            alias_sources = [
                f"{platform}.alias_first_{property_name}",
                f"{platform}.alias_second_{property_name}",
            ]
            for index, entity_id in enumerate(alias_sources):
                hass.states.async_set(
                    entity_id,
                    "active",
                    {alias: _native_helper_sample(platform, property_name, index)},
                )
            alias_template = _native_reference_templates(
                platform,
                alias_sources,
                [hass.states.get(entity_id) for entity_id in alias_sources],
            )[property_name]
            Template(alias_template, hass).async_render(parse_result=True)


@pytest.mark.parametrize(
    ("platform", "property_name", "features", "expected"),
    [
        ("lock", "support_open", 1, True),
        ("siren", "support_volume", 8, True),
        ("siren", "support_duration", 8, False),
        ("update", "support_backup", 8, True),
    ],
)
def test_capability_helpers_derive_values_from_supported_features(
    hass,
    platform,
    property_name,
    features,
    expected,
):
    entity_id = f"{platform}.source"
    hass.states.async_set(entity_id, "active", {"supported_features": features})

    template = _native_source_template(
        entity_id,
        hass.states.get(entity_id),
        property_name,
    )

    assert Template(template, hass).async_render(parse_result=True) is expected


def test_missing_climate_mode_attribute_remains_dynamic(hass):
    entity_id = "climate.source"
    hass.states.async_set(entity_id, "cool", {"hvac_modes": ["off", "cool"]})
    template = _native_source_template(
        entity_id,
        hass.states.get(entity_id),
        "fan_mode",
    )

    assert Template(template, hass).async_render(parse_result=True) is None

    hass.states.async_set(
        entity_id,
        "cool",
        {"hvac_modes": ["off", "cool"], "fan_mode": "auto"},
    )
    assert Template(template, hass).async_render(parse_result=True) == "auto"


def test_reference_weather_uses_standard_native_attribute_aliases(hass):
    entity_id = "weather.home"
    hass.states.async_set(
        entity_id,
        "partlycloudy",
        {
            "temperature": 23,
            "apparent_temperature": 24,
            "dew_point": 12,
            "temperature_unit": "°C",
            "pressure": 1012,
            "pressure_unit": "hPa",
            "visibility": 10,
            "visibility_unit": "km",
            "wind_speed": 5,
            "wind_speed_unit": "km/h",
            "wind_gust_speed": 8,
            "wind_bearing": 180,
        },
    )

    templates = _reference_entity_defaults(hass, [entity_id])[
        CONF_NATIVE_VALUE_TEMPLATES
    ]

    assert Template(templates["native_temperature"], hass).async_render(
        parse_result=True
    ) == 23
    assert Template(templates["native_temperature_unit"], hass).async_render(
        parse_result=True
    ) == "°C"
    assert Template(templates["native_pressure"], hass).async_render(
        parse_result=True
    ) == 1012
    assert Template(templates["native_wind_speed"], hass).async_render(
        parse_result=True
    ) == 5


def test_reference_water_heater_maps_standard_away_mode_attribute(hass):
    entity_id = "water_heater.home"
    hass.states.async_set(
        entity_id,
        "eco",
        {
            "away_mode": "on",
            "operation_list": ["off", "eco"],
            "temperature": 45,
        },
    )

    template = _reference_entity_defaults(hass, [entity_id])[
        CONF_NATIVE_VALUE_TEMPLATES
    ]["is_away_mode_on"]

    assert Template(template, hass).async_render(parse_result=True) == "on"


def test_reference_fan_derives_speed_count_from_percentage_step(hass):
    entity_id = "fan.home"
    hass.states.async_set(
        entity_id,
        "on",
        {"percentage": 50, "percentage_step": 25},
    )

    defaults = _reference_entity_defaults(hass, [entity_id])
    template = defaults[CONF_NATIVE_VALUE_TEMPLATES]["speed_count"]

    assert Template(template, hass).async_render(parse_result=True) == 4
    assert defaults.get(CONF_ATTRIBUTE_TEMPLATES_JSON, "") == ""


def test_multiple_fan_speed_count_helpers_remain_valid_jinja(hass):
    hass.states.async_set("fan.first", "on", {"percentage_step": 25})
    hass.states.async_set("fan.second", "on", {"percentage_step": 20})

    defaults = _reference_entity_defaults(hass, ["fan.first", "fan.second"])
    template = defaults[CONF_NATIVE_VALUE_TEMPLATES]["speed_count"]

    assert Template(template, hass).async_render(parse_result=True) == 4.5
    assert defaults.get(CONF_ATTRIBUTE_TEMPLATES_JSON, "") == ""


def test_reference_calendar_builds_event_from_standard_source_attributes(hass):
    entity_id = "calendar.home"
    hass.states.async_set(
        entity_id,
        "on",
        {
            "message": "Dentist",
            "start_time": "2026-08-14 10:00:00",
            "end_time": "2026-08-14 11:00:00",
            "all_day": False,
            "location": "Clinic",
            "description": "Bring documents",
        },
    )

    defaults = _reference_entity_defaults(hass, [entity_id])
    template = defaults[CONF_NATIVE_VALUE_TEMPLATES]["event"]
    event = Template(template, hass).async_render(parse_result=True)

    assert event == {
        "summary": "Dentist",
        "start": "2026-08-14 10:00:00",
        "end": "2026-08-14 11:00:00",
        "all_day": False,
        "location": "Clinic",
        "description": "Bring documents",
    }
    assert defaults.get(CONF_ATTRIBUTE_TEMPLATES_JSON, "") == ""


def test_reference_event_copies_event_attributes_mapping(hass):
    entity_id = "event.button"
    hass.states.async_set(
        entity_id,
        "pressed",
        {"event_type": "pressed", "button": 1},
    )

    defaults = _reference_entity_defaults(hass, [entity_id])
    template = defaults[CONF_NATIVE_VALUE_TEMPLATES]["event_attributes"]
    attributes = Template(template, hass).async_render(parse_result=True)

    assert attributes["event_type"] == "pressed"
    assert attributes["button"] == 1
    assert defaults.get(CONF_ATTRIBUTE_TEMPLATES_JSON, "") == ""


def test_reference_calendar_keeps_non_event_vendor_attributes(hass):
    hass.states.async_set(
        "calendar.work",
        "on",
        {"message": "Meeting", "vendor_color": "blue"},
    )

    defaults = _reference_entity_defaults(hass, ["calendar.work"])

    assert json.loads(defaults[CONF_ATTRIBUTE_TEMPLATES_JSON]) == {
        "vendor_color": "{{ state_attr('calendar.work', 'vendor_color') }}",
    }


def test_reference_image_uses_source_state_for_last_updated(hass):
    entity_id = "image.camera_snapshot"
    timestamp = "2026-08-14T10:15:00+00:00"
    hass.states.async_set(entity_id, timestamp, {"content_type": "image/jpeg"})

    template = _reference_entity_defaults(hass, [entity_id])[
        CONF_NATIVE_VALUE_TEMPLATES
    ]["image_last_updated"]

    assert Template(template, hass).async_render(parse_result=True) == timestamp


@pytest.mark.parametrize(
    ("platform", "property_name", "expected"),
    [
        ("climate", "target_temperature", None),
        ("fan", "speed_count", 0),
        ("cover", "current_cover_tilt_position", None),
        ("media_player", "shuffle", None),
        ("update", "update_percentage", None),
        ("water_heater", "is_away_mode_on", None),
        ("todo", "todo_items", []),
        ("event", "event_attributes", {}),
    ],
)
def test_multi_source_missing_native_values_use_safe_defaults(
    hass,
    platform,
    property_name,
    expected,
):
    entity_ids = [f"{platform}.first", f"{platform}.second"]
    for entity_id in entity_ids:
        hass.states.async_set(entity_id, "active")

    templates = _native_reference_templates(
        platform,
        entity_ids,
        [hass.states.get(entity_id) for entity_id in entity_ids],
    )

    assert Template(templates[property_name], hass).async_render(
        parse_result=True
    ) == expected


def test_sparse_native_attribute_tracks_all_selected_sources(hass):
    first = "light.first"
    second = "light.second"
    hass.states.async_set(first, "on", {"brightness": 100})
    hass.states.async_set(second, "on")

    templates = _native_reference_templates(
        "light",
        [first, second],
        [hass.states.get(first), hass.states.get(second)],
    )

    assert Template(templates["brightness"], hass).async_render(parse_result=True) == 100
    hass.states.async_set(second, "on", {"brightness": 200})
    assert Template(templates["brightness"], hass).async_render(parse_result=True) == 150


def test_native_multi_source_helpers_use_property_semantics(hass):
    def render(property_name, values, platform="sensor"):
        templates = [f"{{{{ {value!r} }}}}" for value in values]
        template = _merged_native_template(platform, property_name, templates, values)
        return Template(template, hass).async_render(parse_result=True)

    assert render("is_closed", [True, False]) is False
    assert render("supported_features", [1, 4]) == 5
    assert render("support_open", [False, True]) is True
    assert render("native_min_value", [10, 20]) == 10
    assert render("native_max_value", [10, 20]) == 20
    assert render("native_value", [10, 20], "number") == 15
    assert render("native_value", ["2026-08-10", "2026-08-11"], "date") == (
        "2026-08-11"
    )
    assert render("native_value", ["10:00:00", "11:00:00"], "time") == "11:00:00"
    assert render(
        "native_value",
        ["2026-08-10T12:00:00+00:00", "2026-08-11T12:00:00+00:00"],
        "datetime",
    ) == "2026-08-11T12:00:00+00:00"
    assert render("native_value", ["hello", " world"], "text") == "hello world"
    assert render("rgb_color", [[1, 2, 3], [4, 5, 6]]) == [1, 2, 3]
    assert render("options", [["eco", "boost"], ["boost", "turbo"]]) == [
        "eco",
        "boost",
        "turbo",
    ]


def test_same_domain_state_helpers_select_first_known_value(hass):
    excluded_domains = {
        "binary_sensor",
        "camera",
        "date",
        "datetime",
        "device_tracker",
        "fan",
        "geolocation",
        "humidifier",
        "image",
        "light",
        "lock",
        "number",
        "remote",
        "select",
        "sensor",
        "siren",
        "switch",
        "text",
        "time",
    }
    for platform in sorted(set(DOMAIN_NATIVE_TEMPLATE_PROPERTIES) - excluded_domains):
        first = f"{platform}.first"
        second = f"{platform}.second"
        hass.states.async_set(first, "unavailable")
        hass.states.async_set(second, "active")

        defaults = _reference_entity_defaults(hass, [first, second])
        variables = {
            name: hass.states.get(entity_id).state
            for name, entity_id in json.loads(
                defaults[CONF_TEMPLATE_SOURCES_JSON]
            ).items()
        }

        assert defaults[CONF_PLATFORM] == platform
        assert Template(defaults[CONF_VALUE_TEMPLATE], hass).async_render(
            variables=variables,
            parse_result=False,
        ) == "active", platform


def test_attribute_helpers_include_attributes_present_on_only_one_source(hass):
    hass.states.async_set("sensor.first", "ready", {"power": 10})
    hass.states.async_set("sensor.second", "ready", {"battery": 90})

    defaults = _reference_entity_defaults(
        hass,
        ["sensor.first", "sensor.second"],
    )
    templates = json.loads(defaults[CONF_ATTRIBUTE_TEMPLATES_JSON])

    assert "state_attr('sensor.first', 'power')" in templates["power"]
    assert "state_attr('sensor.second', 'power')" in templates["power"]
    assert "state_attr('sensor.first', 'battery')" in templates["battery"]
    assert "state_attr('sensor.second', 'battery')" in templates["battery"]

    hass.states.async_set("sensor.second", "ready", {"battery": 90, "power": 30})
    assert Template(templates["power"], hass).async_render(parse_result=True) == 20


def test_numeric_helper_rejects_non_finite_source_states(hass):
    hass.states.async_set("sensor.nan", "nan")
    hass.states.async_set("sensor.infinity", "inf")

    defaults = _reference_entity_defaults(
        hass,
        ["sensor.nan", "sensor.infinity"],
    )

    assert defaults[CONF_INITIAL_VALUE] == "naninf"
    assert "average" not in defaults[CONF_VALUE_TEMPLATE]


def test_multiple_climate_sources_keep_domain_and_generate_type_aware_helpers(hass):
    attributes = {
        "hvac_modes": ["off", "cool"],
        "fan_modes": ["auto", "high"],
        "current_temperature": 20,
        "temperature": 22,
        "fan_mode": "auto",
    }
    hass.states.async_set("climate.first", "cool", attributes)
    hass.states.async_set(
        "climate.second",
        "heat",
        {
            **attributes,
            "hvac_modes": ["off", "heat"],
            "current_temperature": 24,
            "temperature": 24,
        },
    )

    defaults = _reference_entity_defaults(
        hass,
        ["climate.first", "climate.second"],
    )

    assert defaults[CONF_PLATFORM] == "climate"
    templates = defaults[CONF_NATIVE_VALUE_TEMPLATES]
    assert set(templates) == set(CLIMATE_NATIVE_TEMPLATE_PROPERTIES)
    assert "namespace(values=[])" in templates["hvac_modes"]
    assert "average" in templates["current_temperature"]
    assert "values[0]" in templates["hvac_mode"]
    assert all(templates.values())


def test_build_entity_config_supports_composite_templates_and_attributes():
    device_name, entity = _build_entity_config(
        _entity_input(
            {
                ATTR_ENTITY_ID: "sensor.washer_phase",
                CONF_SOURCE_ENTITIES_TEXT: "sensor.washer_power, binary_sensor.washer_door",
                CONF_VALUE_TEMPLATE: "{{ states('sensor.washer_power') }}",
                CONF_AVAILABILITY_TEMPLATE: "{{ is_state('sensor.washer_power', 'on') }}",
                CONF_ATTRIBUTES_JSON: '{"source": "simulation"}',
                CONF_ATTRIBUTE_TEMPLATES_JSON: '{"door": "{{ states(\\"binary_sensor.washer_door\\") }}"}',
            }
        )
    )

    assert device_name == "Laundry"
    assert entity == {
        CONF_PLATFORM: "sensor",
        CONF_NAME: "Washer Phase",
        ATTR_ENTITY_ID: "sensor.washer_phase",
        CONF_INITIAL_VALUE: "idle",
        CONF_INITIAL_AVAILABILITY: True,
        CONF_PERSISTENT: True,
        CONF_SOURCE_ENTITIES: [
            "sensor.washer_power",
            "binary_sensor.washer_door",
        ],
        CONF_VALUE_TEMPLATE: "{{ states('sensor.washer_power') }}",
        CONF_AVAILABILITY_TEMPLATE: "{{ is_state('sensor.washer_power', 'on') }}",
        CONF_ATTRIBUTES: {"source": "simulation"},
        CONF_ATTRIBUTE_TEMPLATES: {
            "door": '{{ states("binary_sensor.washer_door") }}',
        },
    }


def test_build_entity_config_supports_custom_event_hooks():
    _, entity = _build_entity_config(
        _entity_input(
            {
                ATTR_ENTITY_ID: "sensor.washer_phase",
                CONF_EVENT_HOOKS_JSON: json.dumps(
                    {
                        "door_hook": {
                            "trigger": "state",
                            "entity_ids": ["binary_sensor.washer_door"],
                            "attribute": "battery",
                            "value_template": "{{ trigger.to }}",
                            "attribute_templates": {
                                "source_battery": "{{ trigger.to_state.attributes.battery }}",
                            },
                            "debounce": 0.5,
                            "enabled": "false",
                        },
                        "manual_event": {
                            "trigger": "event",
                            "event_type": "virtual_layer_manual_update",
                            "event_data": {"target": "washer"},
                            "attributes": {"hooked": True},
                            "refresh": "no",
                        },
                    }
                ),
            }
        )
    )

    assert entity[CONF_EVENT_HOOKS] == [
        {
            "trigger": "state",
            ATTR_ENTITY_ID: ["binary_sensor.washer_door"],
            CONF_ATTRIBUTE: ["battery"],
            CONF_VALUE_TEMPLATE: "{{ trigger.to }}",
            CONF_ATTRIBUTE_TEMPLATES: {
                "source_battery": "{{ trigger.to_state.attributes.battery }}",
            },
            "debounce": 0.5,
            "enabled": False,
            CONF_NAME: "door_hook",
        },
        {
            "trigger": "event",
            "event_type": "virtual_layer_manual_update",
            "event_data": {"target": "washer"},
            CONF_ATTRIBUTES: {"hooked": True},
            "refresh": False,
            CONF_NAME: "manual_event",
        },
    ]


def test_build_entity_config_rejects_invalid_custom_event_hooks():
    with pytest.raises(InvalidEntityReference) as err:
        _build_entity_config(
            _entity_input(
                {
                    CONF_EVENT_HOOKS_JSON: '[{"trigger": "state", "entity_id": ["not-an-entity"]}]',
                }
            )
        )

    assert err.value.field_name == CONF_EVENT_HOOKS_JSON

    with pytest.raises(InvalidJson) as err:
        _build_entity_config(
            _entity_input(
                {
                    CONF_EVENT_HOOKS_JSON: json.dumps(
                        [
                            {
                                "trigger": "event",
                                "event_type": "virtual_layer_manual_update",
                                "enabled": "sometimes",
                            }
                        ]
                    ),
                }
            )
        )

    assert err.value.field_name == CONF_EVENT_HOOKS_JSON

    with pytest.raises(InvalidJson) as err:
        _build_entity_config(
            _entity_input(
                {
                    CONF_EVENT_HOOKS_JSON: '[{"trigger": "event"}]',
                }
            )
        )

    assert err.value.field_name == CONF_EVENT_HOOKS_JSON

    with pytest.raises(InvalidEntityReference) as err:
        _build_entity_config(
            _entity_input(
                {
                    ATTR_ENTITY_ID: "sensor.washer_phase",
                    CONF_EVENT_HOOKS_JSON: '[{"trigger": "state", "entity_id": ["sensor.washer_phase"]}]',
                }
            )
        )

    assert err.value.field_name == CONF_EVENT_HOOKS_JSON


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_build_entity_config_rejects_non_standard_json_numbers(constant):
    with pytest.raises(InvalidJson) as err:
        _build_entity_config(
            _entity_input(
                {
                    CONF_ATTRIBUTES_JSON: f'{{"bad": {constant}}}',
                }
            )
        )

    assert err.value.field_name == CONF_ATTRIBUTES_JSON


def test_build_entity_config_rejects_json_integer_outside_ha_range():
    with pytest.raises(InvalidJson) as err:
        _build_entity_config(
            _entity_input(
                {
                    CONF_ATTRIBUTES_JSON: '{"too_large": 18446744073709551616}',
                }
            )
        )

    assert err.value.field_name == CONF_ATTRIBUTES_JSON


def test_plain_options_isolates_recursive_and_excessively_deep_values():
    recursive = {}
    recursive["self"] = recursive
    deeply_nested = "leaf"
    for _ in range(150):
        deeply_nested = [deeply_nested]

    result = _plain_options({
        "recursive": recursive,
        "deep": deeply_nested,
        "healthy": {"nested": True},
    })

    assert result["recursive"] == {"self": None}
    assert result["healthy"] == {"nested": True}
    value = result["deep"]
    depth = 0
    while isinstance(value, list):
        value = value[0]
        depth += 1
    assert value is None
    assert 0 < depth <= 101


def test_json_default_sanitizes_values_that_cannot_be_saved_by_home_assistant():
    result = json.loads(
        _json_default(
            {
                "object": object(),
                "set": {"one", "two"},
                "too_large": 10**10000,
                "not_finite": float("nan"),
            }
        )
    )

    assert result["object"].startswith("<object object at ")
    assert sorted(result["set"]) == ["one", "two"]
    assert result["too_large"] is None
    assert result["not_finite"] == "nan"


@pytest.mark.parametrize("debounce", ["Infinity", True])
def test_build_entity_config_rejects_invalid_event_hook_debounce(debounce):
    with pytest.raises(InvalidJson) as err:
        _build_entity_config(
            _entity_input(
                {
                    CONF_EVENT_HOOKS_JSON: json.dumps(
                        [
                            {
                                "trigger": "event",
                                "event_type": "virtual_layer_update",
                                "debounce": debounce,
                            }
                        ]
                    ),
                }
            )
        )

    assert err.value.field_name == CONF_EVENT_HOOKS_JSON

    with pytest.raises(InvalidDomainOptions):
        _build_entity_config(
            _entity_input(
                {
                    CONF_PLATFORM: "number",
                    CONF_INITIAL_VALUE: "1",
                    CONF_DOMAIN_OPTIONS_JSON: '{"min": "NaN", "max": 100}',
                }
            )
        )


def test_climate_schema_rejects_unknown_hvac_modes():
    from custom_components.virtual_layer.climate import CLIMATE_SCHEMA

    with pytest.raises(vol.Invalid):
        CLIMATE_SCHEMA(
            {
                CONF_NAME: "Invalid climate",
                "hvac_modes": ["off", "removed_mode"],
            }
        )


@pytest.mark.parametrize(
    "domain_options",
    [
        {"support_color": True, "initial_color": [120]},
        {
            "support_effect": True,
            "initial_effect": "removed",
            "initial_effect_list": ["none", "rainbow"],
        },
    ],
)
def test_build_entity_config_rejects_malformed_light_options(domain_options):
    with pytest.raises(InvalidDomainOptions):
        _build_entity_config(
            _entity_input(
                {
                    CONF_PLATFORM: "light",
                    CONF_DOMAIN_OPTIONS_JSON: json.dumps(domain_options),
                }
            )
        )


@pytest.mark.parametrize(
    "domain_options",
    [
        {"battery_level": 101},
        {"activity": "teleporting"},
        {"fan_speed": "turbo", "fan_speed_list": ["normal"]},
    ],
)
def test_build_entity_config_rejects_malformed_vacuum_options(domain_options):
    with pytest.raises(InvalidDomainOptions):
        _build_entity_config(
            _entity_input(
                {
                    CONF_PLATFORM: "vacuum",
                    CONF_DOMAIN_OPTIONS_JSON: json.dumps(domain_options),
                }
            )
        )


def test_build_entity_config_deduplicates_sources_and_rejects_invalid_template_variables():
    _, entity = _build_entity_config(
        _entity_input(
            {
                CONF_SOURCE_ENTITIES_TEXT: "sensor.power, sensor.power\nsensor.door",
                CONF_TEMPLATE_SOURCES_JSON: '{"power": "sensor.power"}',
            }
        )
    )

    assert entity[CONF_SOURCE_ENTITIES] == ["sensor.power", "sensor.door"]

    with pytest.raises(InvalidJson) as err:
        _build_entity_config(
            _entity_input(
                {
                    CONF_TEMPLATE_SOURCES_JSON: '{"not-valid": "sensor.power"}',
                }
            )
        )

    assert err.value.field_name == CONF_TEMPLATE_SOURCES_JSON


def test_build_entity_config_normalizes_attribute_names_and_rejects_bad_templates():
    _, entity = _build_entity_config(
        _entity_input(
            {
                CONF_ATTRIBUTES_JSON: '{" summary ": "ready"}',
                CONF_ATTRIBUTE_TEMPLATES_JSON: (
                    '{" detail ": "{{ states(\\"sensor.detail\\") }}"}'
                ),
            }
        )
    )

    assert entity[CONF_ATTRIBUTES] == {"summary": "ready"}
    assert entity[CONF_ATTRIBUTE_TEMPLATES] == {
        "detail": '{{ states("sensor.detail") }}',
    }

    for field_name, payload in (
        (CONF_ATTRIBUTE_TEMPLATES_JSON, '{"detail": 42}'),
        (CONF_ATTRIBUTES_JSON, '{"name": 1, " name ": 2}'),
        (CONF_TEMPLATE_SOURCES_JSON, '{"power": "sensor.a", " power ": "sensor.b"}'),
        (
            CONF_ATTRIBUTE_SOURCES_JSON,
            '{"power": "sensor.a.state", " power ": "sensor.b.state"}',
        ),
    ):
        with pytest.raises(InvalidJson) as err:
            _build_entity_config(_entity_input({field_name: payload}))
        assert err.value.field_name == field_name


def test_build_entity_config_rejects_non_string_event_attribute_templates():
    with pytest.raises(InvalidJson) as err:
        _build_entity_config(
            _entity_input(
                {
                    CONF_EVENT_HOOKS_JSON: json.dumps(
                        [{
                            "trigger": "event",
                            "event_type": "virtual_layer_update",
                            "attribute_templates": {"value": {"not": "jinja"}},
                        }]
                    ),
                }
            )
        )

    assert err.value.field_name == CONF_EVENT_HOOKS_JSON

    with pytest.raises(InvalidJson) as err:
        _build_entity_config(
            _entity_input(
                {
                    CONF_ATTRIBUTES_JSON: '{"available": false}',
                }
            )
        )

    assert err.value.field_name == CONF_ATTRIBUTES_JSON

    with pytest.raises(InvalidJson) as err:
        _build_entity_config(
            _entity_input(
                {
                    CONF_TEMPLATE_SOURCES_JSON: '{"none": "sensor.power"}',
                }
            )
        )

    assert err.value.field_name == CONF_TEMPLATE_SOURCES_JSON


def test_reference_entity_defaults_avoids_jinja_reserved_source_variable_names(hass):
    entity_ids = [
        "sensor.none",
        "sensor.true",
        "sensor.states",
        "sensor.state_attr",
        "sensor.this",
        "sensor.trigger",
        "sensor.namespace",
    ]
    for index, entity_id in enumerate(entity_ids):
        hass.states.async_set(entity_id, f"value-{index}")

    defaults = _reference_entity_defaults(hass, entity_ids)

    template_sources = json.loads(defaults[CONF_TEMPLATE_SOURCES_JSON])
    assert template_sources == {
        "source_none": "sensor.none",
        "source_true": "sensor.true",
        "source_states": "sensor.states",
        "source_state_attr": "sensor.state_attr",
        "source_this": "sensor.this",
        "source_trigger": "sensor.trigger",
        "source_namespace": "sensor.namespace",
    }
    assert "values | join('')" in defaults[CONF_VALUE_TEMPLATE]
    assert "source_none, source_true, source_states" in defaults[CONF_VALUE_TEMPLATE]
    variables = {
        name: hass.states.get(entity_id).state
        for name, entity_id in template_sources.items()
    }
    assert Template(defaults[CONF_VALUE_TEMPLATE], hass).async_render(
        variables=variables,
        parse_result=False,
    ) == "".join(f"value-{index}" for index in range(len(entity_ids)))
    assert Template(defaults[CONF_AVAILABILITY_TEMPLATE], hass).async_render(
        variables=variables,
        parse_result=True,
    ) is True


def test_build_entity_config_preserves_domain_options_and_normalizes_climate_default():
    _, entity = _build_entity_config(
        _entity_input(
            {
                CONF_PLATFORM: "climate",
                CONF_INITIAL_VALUE: "unknown",
                CONF_DOMAIN_OPTIONS_JSON: '{"hvac_modes": ["off", "heat"], "target_temperature": 21}',
            }
        )
    )

    assert entity[CONF_INITIAL_VALUE] == "off"
    assert entity["hvac_modes"] == ["off", "heat"]
    assert entity["target_temperature"] == 21


def test_climate_mode_fields_override_or_clear_advanced_json_values():
    _, overridden = _build_entity_config(
        _entity_input(
            {
                CONF_PLATFORM: "climate",
                CONF_INITIAL_VALUE: "cool",
                CONF_DOMAIN_OPTIONS_JSON: json.dumps(
                    {
                        "hvac_modes": ["off", "cool"],
                        "fan_modes": ["old"],
                        "fan_mode": "old",
                    }
                ),
                "fan_modes": ["auto", "turbo"],
                "fan_mode": "auto",
            }
        )
    )
    assert overridden["fan_modes"] == ["auto", "turbo"]
    assert overridden["fan_mode"] == "auto"

    _, cleared = _build_entity_config(
        _entity_input(
            {
                CONF_PLATFORM: "climate",
                CONF_INITIAL_VALUE: "cool",
                CONF_DOMAIN_OPTIONS_JSON: json.dumps(
                    {
                        "hvac_modes": ["off", "cool"],
                        "fan_modes": ["old"],
                        "fan_mode": "old",
                    }
                ),
                "fan_modes": [],
                "fan_mode": "",
            }
        )
    )
    assert cleared["fan_modes"] == []
    assert "fan_mode" not in cleared


@pytest.mark.parametrize(
    "overrides",
    [
        {
            CONF_INITIAL_VALUE: "cool",
            "hvac_modes": [],
        },
        {
            CONF_INITIAL_VALUE: "cool",
            "hvac_modes": ["off", "cool"],
            "fan_modes": ["auto"],
            "fan_mode": "turbo",
        },
        {
            CONF_INITIAL_VALUE: "cool",
            "hvac_modes": ["off", "cool", "cool"],
        },
        {
            CONF_INITIAL_VALUE: "cool",
            "hvac_modes": ["off", "cool"],
            "preset_modes": ["none", ""],
        },
    ],
)
def test_build_entity_config_rejects_invalid_climate_mode_relationships(overrides):
    with pytest.raises(InvalidDomainOptions):
        _build_entity_config(
            _entity_input(
                {
                    CONF_PLATFORM: "climate",
                    **overrides,
                }
            )
        )


def test_reference_climate_promotes_native_modes_and_temperature_options(hass):
    hass.states.async_set(
        "climate.living_room",
        "cool",
        {
            ATTR_FRIENDLY_NAME: "Living Room AC",
            "current_temperature": 24.0,
            "fan_mode": "auto",
            "fan_modes": ["medium", "high", "turbo", "auto"],
            "hvac_modes": ["off", "cool", "dry", "fan_only"],
            "max_temp": 30.0,
            "min_temp": 18.0,
            "preset_mode": "none",
            "preset_modes": ["none", "sleep", "quiet", "speed", "ai_comfort"],
            "supported_features": 441,
            "swing_mode": None,
            "swing_modes": [],
            "target_temp_step": 1.0,
            "temperature": 23.0,
            "vendor_attribute": "preserved",
        },
    )

    defaults = _reference_entity_defaults(hass, ["climate.living_room"])

    assert defaults[CONF_PLATFORM] == "climate"
    assert defaults[CONF_INITIAL_VALUE] == "cool"
    assert defaults["current_temperature"] == 24.0
    assert defaults["max_temp"] == 30.0
    assert defaults["min_temp"] == 18.0
    assert defaults["target_temperature"] == 23.0
    assert defaults["target_temperature_step"] == 1.0
    assert CONF_DOMAIN_OPTIONS_JSON not in defaults
    assert defaults["hvac_modes"] == ["off", "cool", "dry", "fan_only"]
    assert defaults["fan_mode"] == "auto"
    assert defaults["fan_modes"] == ["medium", "high", "turbo", "auto"]
    assert defaults["preset_mode"] == "none"
    assert defaults["preset_modes"] == [
        "none",
        "sleep",
        "quiet",
        "speed",
        "ai_comfort",
    ]
    assert defaults["swing_modes"] == []
    native_templates = defaults[CONF_NATIVE_VALUE_TEMPLATES]
    assert native_templates["hvac_mode"] == "{{ states('climate.living_room') }}"
    assert native_templates["hvac_modes"] == (
        "{{ state_attr('climate.living_room', 'hvac_modes') }}"
    )
    assert native_templates["target_temperature"] == (
        "{{ state_attr('climate.living_room', 'temperature') }}"
    )
    assert json.loads(defaults[CONF_ATTRIBUTES_JSON]) == {
        "vendor_attribute": "preserved",
    }

    _, entity = _build_entity_config(_entity_input(defaults))
    assert entity["fan_modes"] == ["medium", "high", "turbo", "auto"]
    assert entity["preset_modes"] == [
        "none",
        "sleep",
        "quiet",
        "speed",
        "ai_comfort",
    ]
    assert entity["target_temperature"] == 23.0
    assert entity["target_temperature_step"] == 1.0
    assert "supported_features" not in entity[CONF_ATTRIBUTES]


def test_reference_heating_only_climate_builds_heat_off_boiler_helper(hass):
    hass.states.async_set(
        "climate.boiler",
        "heat",
        {
            ATTR_FRIENDLY_NAME: "Boiler",
            "current_temperature": 29.0,
            "temperature": 26.0,
            "hvac_modes": ["auto", "heat", "fan_only", "off"],
            "min_temp": 10.0,
            "max_temp": 35.0,
        },
    )

    defaults = _reference_entity_defaults(hass, ["climate.boiler"])

    assert defaults[CONF_PLATFORM] == "climate"
    assert defaults[CONF_INITIAL_VALUE] == "heat"
    assert defaults["hvac_modes"] == ["off", "heat"]
    assert defaults[CONF_VALUE_TEMPLATE] == (
        "{{ 'heat' if states('climate.boiler') == 'heat' else 'off' }}"
    )
    assert defaults[CONF_NATIVE_VALUE_TEMPLATES]["hvac_modes"] == (
        "{{ ['off', 'heat'] }}"
    )
    assert defaults[CONF_NATIVE_VALUE_TEMPLATES]["hvac_mode"] == (
        defaults[CONF_VALUE_TEMPLATE]
    )
    actions = json.loads(defaults[CONF_COMMAND_ACTIONS_JSON])
    assert set(actions) == {
        "set_hvac_mode",
        "set_temperature",
        "turn_off",
        "turn_on",
    }
    assert actions["turn_off"] == [
        {
            "action": "climate.set_hvac_mode",
            "data": {"hvac_mode": "off"},
            "target": {ATTR_ENTITY_ID: "climate.boiler"},
        }
    ]
    assert all(
        action.get("target", {}).get(ATTR_ENTITY_ID) != "switch.hot_water"
        for sequence in (actions["turn_on"], actions["turn_off"])
        for action in sequence
    )
    assert CONF_COMMAND_ACTIONS_JSON not in _without_template_helpers(defaults)


def test_climate_entity_form_uses_only_jinja_native_controls():
    schema = _entity_schema(
        {
            CONF_PLATFORM: "climate",
            "hvac_modes": ["off", "cool"],
            "fan_modes": ["auto", "turbo"],
            "fan_mode": "auto",
            "preset_modes": ["none", "sleep"],
            "preset_mode": "none",
            "swing_modes": ["off", "vertical"],
            "swing_mode": "off",
            "swing_horizontal_modes": ["off", "horizontal"],
            "swing_horizontal_mode": "off",
        }
    )
    outer = {marker.schema: validator for marker, validator in schema.schema.items()}
    assert CONF_DOMAIN_SETTINGS not in outer
    validators = _section_validators(schema, CONF_NATIVE_VALUE_TEMPLATES)
    assert set(validators) == set(CLIMATE_NATIVE_TEMPLATE_PROPERTIES)
    assert all(isinstance(value, selector.TemplateSelector) for value in validators.values())

    submitted = schema({})[CONF_NATIVE_VALUE_TEMPLATES]
    assert submitted["hvac_modes"] == "{{ ['off', 'cool'] }}"
    assert submitted["fan_modes"] == "{{ ['auto', 'turbo'] }}"
    assert submitted["fan_mode"] == "{{ 'auto' }}"
    assert all(submitted[property_name] for property_name in CLIMATE_NATIVE_TEMPLATE_PROPERTIES)


def test_build_climate_config_uses_all_native_hvac_fields():
    _, entity = _build_entity_config(
        _entity_input(
            {
                CONF_PLATFORM: "climate",
                CONF_INITIAL_VALUE: "cool",
                "hvac_modes": ["off", "cool", "dry", "fan_only"],
                "fan_modes": ["auto", "turbo"],
                "fan_mode": "auto",
                "preset_modes": ["none", "sleep"],
                "preset_mode": "none",
                "swing_modes": ["off", "vertical"],
                "swing_mode": "off",
                "swing_horizontal_modes": ["left", "right"],
                "swing_horizontal_mode": "left",
                "current_temperature": 24,
                "target_temperature": 23,
                "min_temp": 18,
                "max_temp": 30,
                "target_temperature_step": 1,
                "current_humidity": 55,
                "target_humidity": 50,
                "min_humidity": 30,
                "max_humidity": 80,
                "target_humidity_step": 1,
                "hvac_action": "cooling",
                "temperature_unit": "°C",
            }
        )
    )

    assert entity["hvac_modes"] == ["off", "cool", "dry", "fan_only"]
    assert entity["fan_modes"] == ["auto", "turbo"]
    assert entity["preset_modes"] == ["none", "sleep"]
    assert entity["swing_modes"] == ["off", "vertical"]
    assert entity["swing_horizontal_modes"] == ["left", "right"]
    assert entity["current_temperature"] == 24
    assert entity["target_temperature"] == 23
    assert entity["target_humidity"] == 50
    assert entity["hvac_action"] == "cooling"
    assert entity["temperature_unit"] == "°C"


@pytest.mark.parametrize(
    "options",
    [
        {"min_temp": 30, "max_temp": 20},
        {"min_humidity": 70, "max_humidity": 30},
        {"min_temp": 10, "max_temp": 30, "target_temperature": 31},
        {
            "min_temp": 10,
            "max_temp": 30,
            "target_temperature_low": 25,
            "target_temperature_high": 20,
        },
        {"target_temperature_step": 0},
        {"temperature_unit": "rankine"},
        {"hvac_action": "teleporting"},
    ],
)
def test_build_entity_config_rejects_invalid_climate_scalar_options(options):
    with pytest.raises(InvalidDomainOptions):
        _build_entity_config(
            _entity_input(
                {
                    CONF_PLATFORM: "climate",
                    CONF_INITIAL_VALUE: "off",
                    "hvac_modes": ["off", "cool"],
                    **options,
                }
            )
        )


def test_humidifier_entity_form_migrates_static_values_to_jinja_controls():
    form = _entity_schema(
        {
            CONF_PLATFORM: "humidifier",
            "class": "dehumidifier",
            "action": "drying",
            "current_humidity": 65,
            "min_humidity": 30,
            "max_humidity": 80,
            "target_humidity": 50,
            "target_humidity_step": 1,
            "modes": ["auto", "sleep"],
            "mode": "auto",
        }
    )
    outer = {marker.schema: validator for marker, validator in form.schema.items()}
    assert CONF_DOMAIN_SETTINGS not in outer
    templates = form({})[CONF_NATIVE_VALUE_TEMPLATES]
    assert templates["device_class"] == "{{ 'dehumidifier' }}"
    assert templates["available_modes"] == "{{ ['auto', 'sleep'] }}"
    assert templates["mode"] == "{{ 'auto' }}"
    assert all(templates[name] for name in HUMIDIFIER_NATIVE_TEMPLATE_PROPERTIES)

    _, entity = _build_entity_config(
        _entity_input(
            {
                CONF_PLATFORM: "humidifier",
                CONF_INITIAL_VALUE: "on",
                "class": "dehumidifier",
                "action": "drying",
                "current_humidity": 65,
                "min_humidity": 30,
                "max_humidity": 80,
                "target_humidity": 50,
                "target_humidity_step": 1,
                "modes": ["auto", "sleep"],
                "mode": "auto",
            }
        )
    )
    assert entity["class"] == "dehumidifier"
    assert entity["action"] == "drying"
    assert entity["target_humidity"] == 50
    assert entity["modes"] == ["auto", "sleep"]
    assert entity["mode"] == "auto"


@pytest.mark.parametrize(
    "options",
    [
        {CONF_INITIAL_VALUE: "idle"},
        {"min_humidity": 80, "max_humidity": 30},
        {"min_humidity": 30, "max_humidity": 80, "target_humidity": 90},
        {"target_humidity_step": 0},
        {"modes": ["auto", "auto"]},
        {"modes": ["auto"], "mode": "sleep"},
        {"action": "cooling"},
    ],
)
def test_build_entity_config_rejects_invalid_humidifier_options(options):
    with pytest.raises(InvalidDomainOptions):
        _build_entity_config(
            _entity_input(
                {
                    CONF_PLATFORM: "humidifier",
                    CONF_INITIAL_VALUE: "off",
                    **options,
                }
            )
        )


def test_fan_entity_form_migrates_static_values_to_jinja_controls():
    schema = _entity_schema(
        {
            CONF_PLATFORM: "fan",
            "speed_count": 4,
            "oscillate": True,
            "direction": True,
            "modes": ["eco", "boost"],
            "percentage": 25,
            "preset_mode": "eco",
            "oscillating": True,
            "current_direction": "reverse",
        }
    )
    outer = {marker.schema: validator for marker, validator in schema.schema.items()}
    assert CONF_DOMAIN_SETTINGS not in outer
    templates = schema({})[CONF_NATIVE_VALUE_TEMPLATES]
    assert templates["speed_count"] == "{{ 4 }}"
    assert templates["preset_modes"] == "{{ ['eco', 'boost'] }}"
    assert templates["preset_mode"] == "{{ 'eco' }}"
    assert templates["current_direction"] == "{{ 'reverse' }}"
    assert all(templates[name] for name in FAN_NATIVE_TEMPLATE_PROPERTIES)


def test_build_fan_config_uses_native_fields_and_normalizes_default_state():
    _, entity = _build_entity_config(
        _entity_input(
            {
                CONF_PLATFORM: "fan",
                CONF_INITIAL_VALUE: "unknown",
                CONF_DOMAIN_OPTIONS_JSON: json.dumps(
                    {"speed_count": 2, "modes": ["old"]}
                ),
                "speed_count": 4,
                "oscillate": True,
                "direction": True,
                "modes": ["eco", "boost"],
                "percentage": 25,
                "preset_mode": "eco",
                "oscillating": True,
                "current_direction": "reverse",
            }
        )
    )

    assert entity[CONF_INITIAL_VALUE] == "off"
    assert entity["speed_count"] == 4
    assert entity["modes"] == ["eco", "boost"]
    assert entity["percentage"] == 25
    assert entity["preset_mode"] == "eco"
    assert entity["oscillate"] is True
    assert entity["oscillating"] is True
    assert entity["direction"] is True
    assert entity["current_direction"] == "reverse"


def test_build_default_fan_form_accepts_disabled_optional_features():
    _, entity = _build_entity_config(
        _entity_input(
            {
                CONF_PLATFORM: "fan",
                CONF_INITIAL_VALUE: "unknown",
                "speed_count": 0,
                "oscillate": False,
                "direction": False,
                "modes": [],
                "oscillating": False,
            }
        )
    )

    assert entity[CONF_INITIAL_VALUE] == "off"
    assert entity["oscillate"] is False
    assert entity["oscillating"] is False


@pytest.mark.parametrize(
    "fan_options",
    [
        {CONF_INITIAL_VALUE: "idle"},
        {"modes": ["eco", "eco"]},
        {"modes": ["eco", ""]},
        {"modes": ["eco"], "preset_mode": "boost"},
        {"direction": False, "current_direction": "reverse"},
        {"oscillate": False, "oscillating": True},
    ],
)
def test_build_entity_config_rejects_invalid_fan_feature_relationships(fan_options):
    with pytest.raises(InvalidDomainOptions):
        _build_entity_config(
            _entity_input(
                {
                    CONF_PLATFORM: "fan",
                    CONF_INITIAL_VALUE: "off",
                    **fan_options,
                }
            )
        )


def test_reference_fan_promotes_native_speed_preset_and_motion_options(hass):
    hass.states.async_set(
        "fan.bedroom",
        "on",
        {
            ATTR_FRIENDLY_NAME: "Bedroom Fan",
            "percentage": 50,
            "percentage_step": 25.0,
            "preset_mode": "eco",
            "preset_modes": ["eco", "boost"],
            "oscillating": True,
            "direction": "reverse",
            "supported_features": 15,
            "vendor": "kept",
        },
    )

    defaults = _reference_entity_defaults(hass, ["fan.bedroom"])
    assert defaults[CONF_PLATFORM] == "fan"
    assert defaults["speed_count"] == 4
    assert defaults["modes"] == ["eco", "boost"]
    assert defaults["percentage"] == 50
    assert defaults["preset_mode"] == "eco"
    assert defaults["oscillate"] is True
    assert defaults["oscillating"] is True
    assert defaults["direction"] is True
    assert defaults["current_direction"] == "reverse"
    assert json.loads(defaults[CONF_ATTRIBUTES_JSON]) == {"vendor": "kept"}


def test_fan_edit_form_migrates_legacy_native_attributes():
    defaults = _entity_form_defaults(
        "Bedroom",
        {
            CONF_PLATFORM: "fan",
            CONF_NAME: "Legacy Fan",
            CONF_INITIAL_VALUE: "on",
            "speed_count": 0,
            "modes": [],
            CONF_ATTRIBUTES: {
                "percentage": 50,
                "percentage_step": 25.0,
                "preset_modes": ["eco", "boost"],
                "preset_mode": "eco",
                "oscillating": True,
                "direction": "reverse",
                "vendor": "preserved",
            },
        },
    )

    assert defaults["speed_count"] == 4
    assert defaults["modes"] == ["eco", "boost"]
    assert defaults["percentage"] == 50
    assert defaults["preset_mode"] == "eco"
    assert defaults["oscillate"] is True
    assert defaults["oscillating"] is True
    assert defaults["direction"] is True
    assert defaults["current_direction"] == "reverse"
    assert json.loads(defaults[CONF_ATTRIBUTES_JSON]) == {
        "vendor": "preserved"
    }


def test_climate_edit_form_migrates_legacy_fan_attributes_over_empty_fields():
    defaults = _entity_form_defaults(
        "Bedroom",
        {
            CONF_PLATFORM: "climate",
            CONF_NAME: "Legacy AC",
            CONF_INITIAL_VALUE: "cool",
            "hvac_modes": ["off", "cool"],
            "fan_modes": [],
            "fan_mode": "",
            CONF_ATTRIBUTES: {
                "fan_modes": ["auto", "turbo"],
                "fan_mode": "auto",
                "supported_features": 441,
                "vendor_attribute": "preserved",
            },
        },
    )

    assert defaults["fan_modes"] == ["auto", "turbo"]
    assert defaults["fan_mode"] == "auto"
    assert json.loads(defaults[CONF_ATTRIBUTES_JSON]) == {
        "vendor_attribute": "preserved"
    }


def test_reference_humidifier_promotes_native_options(hass):
    hass.states.async_set(
        "humidifier.basement",
        "on",
        {
            ATTR_FRIENDLY_NAME: "Basement Dehumidifier",
            "action": "drying",
            "available_modes": ["auto", "sleep"],
            "current_humidity": 65,
            "device_class": "dehumidifier",
            "humidity": 50,
            "max_humidity": 80,
            "min_humidity": 30,
            "mode": "auto",
            "target_humidity_step": 1,
            "supported_features": 1,
            "vendor": "preserved",
        },
    )

    defaults = _reference_entity_defaults(hass, ["humidifier.basement"])
    assert defaults[CONF_PLATFORM] == "humidifier"
    assert defaults[CONF_INITIAL_VALUE] == "on"
    assert defaults["class"] == "dehumidifier"
    assert defaults["action"] == "drying"
    assert defaults["current_humidity"] == 65
    assert defaults["target_humidity"] == 50
    assert defaults["modes"] == ["auto", "sleep"]
    assert defaults["mode"] == "auto"
    assert json.loads(defaults[CONF_ATTRIBUTES_JSON]) == {"vendor": "preserved"}


def test_humidifier_edit_form_migrates_legacy_native_attributes():
    defaults = _entity_form_defaults(
        "Basement",
        {
            CONF_PLATFORM: "humidifier",
            CONF_NAME: "Legacy Dehumidifier",
            CONF_INITIAL_VALUE: "on",
            "modes": [],
            "mode": "",
            CONF_ATTRIBUTES: {
                "action": "drying",
                "available_modes": ["auto", "sleep"],
                "current_humidity": 65,
                "device_class": "dehumidifier",
                "humidity": 50,
                "mode": "auto",
                "vendor": "preserved",
            },
        },
    )

    assert defaults["class"] == "dehumidifier"
    assert defaults["action"] == "drying"
    assert defaults["current_humidity"] == 65
    assert defaults["target_humidity"] == 50
    assert defaults["modes"] == ["auto", "sleep"]
    assert defaults["mode"] == "auto"
    assert json.loads(defaults[CONF_ATTRIBUTES_JSON]) == {"vendor": "preserved"}


def test_build_entity_config_validates_location_helper_options():
    _, entity = _build_entity_config(
        _entity_input(
            {
                CONF_PLATFORM: "device_tracker",
                CONF_INITIAL_VALUE: "not_home",
                CONF_SOURCE_ENTITIES_TEXT: "device_tracker.first, person.second",
                CONF_DOMAIN_OPTIONS_JSON: (
                    '{"location_helper": {'
                    '"distance_threshold_meters": 300, '
                    '"priority_window_seconds": 1800}}'
                ),
            }
        )
    )

    assert entity[CONF_LOCATION_HELPER]["distance_threshold_meters"] == 300

    with pytest.raises(InvalidDomainOptions):
        _build_entity_config(
            _entity_input(
                {
                    CONF_PLATFORM: "device_tracker",
                    CONF_DOMAIN_OPTIONS_JSON: '{"location_helper": {"distance_threshold_meters": 0}}',
                }
            )
        )


def test_build_entity_config_rejects_reserved_domain_options():
    with pytest.raises(InvalidJson) as err:
        _build_entity_config(
            _entity_input(
                {
                    CONF_DOMAIN_OPTIONS_JSON: '{"name": "must not override"}',
                }
            )
        )

    assert err.value.field_name == CONF_DOMAIN_OPTIONS_JSON

    with pytest.raises(InvalidJson) as err:
        _build_entity_config(
            _entity_input(
                {
                    CONF_DOMAIN_OPTIONS_JSON: '{"friendly_name": "must not override"}',
                }
            )
        )

    assert err.value.field_name == CONF_DOMAIN_OPTIONS_JSON


def test_reference_entity_defaults_combines_boolean_sources_with_and_template(hass):
    hass.states.async_set("binary_sensor.front_door", "on")
    hass.states.async_set("switch.alarm_ready", "on")

    defaults = _reference_entity_defaults(
        hass,
        ["binary_sensor.front_door", "switch.alarm_ready"],
    )

    assert defaults[CONF_PLATFORM] == "binary_sensor"
    assert defaults[CONF_INITIAL_VALUE] == "on"
    assert (
        defaults[CONF_SOURCE_ENTITIES_TEXT]
        == "binary_sensor.front_door\nswitch.alarm_ready"
    )
    assert defaults[CONF_TEMPLATE_SOURCES_JSON] == (
        '{"alarm_ready": "switch.alarm_ready", "front_door": "binary_sensor.front_door"}'
    )
    assert " and " in defaults[CONF_VALUE_TEMPLATE]
    assert "front_door | lower" in defaults[CONF_VALUE_TEMPLATE]
    assert "alarm_ready | lower" in defaults[CONF_VALUE_TEMPLATE]


def test_reference_entity_defaults_avoids_source_id_for_a_single_entity_copy(hass):
    hass.states.async_set(
        "sensor.kitchen_lamp",
        "on",
        {ATTR_FRIENDLY_NAME: "Kitchen Lamp"},
    )

    defaults = _reference_entity_defaults(hass, ["sensor.kitchen_lamp"])

    assert defaults[ATTR_ENTITY_ID] == "sensor.kitchen_lamp_copy"


def test_reference_entity_defaults_copy_id_keeps_suffix_after_slug_limit(hass):
    source_id = "sensor." + "a" * 80
    hass.states.async_set(source_id, "on")

    defaults = _reference_entity_defaults(hass, [source_id])

    assert defaults[ATTR_ENTITY_ID].endswith("_copy")
    assert defaults[ATTR_ENTITY_ID] != source_id


def test_presence_motion_helper_uses_majority_and_delayed_all_off_clear(
    hass, monkeypatch
):
    source_ids = [
        "binary_sensor.hall_motion",
        "binary_sensor.kitchen_motion",
        "binary_sensor.garage_motion",
    ]
    for entity_id, state in zip(source_ids, ("on", "on", "off"), strict=True):
        hass.states.async_set(entity_id, state, {"device_class": "motion"})

    defaults = _reference_entity_defaults(hass, source_ids)

    assert defaults[CONF_PLATFORM] == "binary_sensor"
    assert defaults[CONF_INITIAL_VALUE] == "on"
    assert json.loads(defaults[CONF_DOMAIN_OPTIONS_JSON]) == {CONF_CLASS: "motion"}
    assert "(active | count) > 3 / 2" in defaults[CONF_VALUE_TEMPLATE]
    assert "all_off" in defaults[CONF_VALUE_TEMPLATE]
    assert "this.state == 'on'" in defaults[CONF_VALUE_TEMPLATE]
    assert "< 300" in defaults[CONF_VALUE_TEMPLATE]

    single_source_defaults = _reference_entity_defaults(hass, source_ids[:1])
    assert json.loads(single_source_defaults[CONF_DOMAIN_OPTIONS_JSON]) == {
        CONF_CLASS: "motion",
    }
    assert "this.state == 'on'" in single_source_defaults[CONF_VALUE_TEMPLATE]

    hass.states.async_set("binary_sensor.combined_motion", "on")
    template = Template(defaults[CONF_VALUE_TEMPLATE], hass)
    template_sources = json.loads(defaults[CONF_TEMPLATE_SOURCES_JSON])

    def render_template():
        variables = {
            "this": hass.states.get("binary_sensor.combined_motion"),
            **{
                name: hass.states.get(entity_id).state
                for name, entity_id in template_sources.items()
            },
        }
        return template.async_render(variables=variables, parse_result=False).strip()

    # A minority is still enough to keep a detected aggregate on until every
    # source has reported off.
    hass.states.async_set(source_ids[0], "off", {"device_class": "motion"})
    future = dt_util.now() + timedelta(minutes=10)
    monkeypatch.setattr(
        "homeassistant.helpers.template.extensions.datetime.dt_util.now",
        lambda: future,
    )
    assert render_template() == "true"

    hass.states.async_set(source_ids[1], "off", {"device_class": "motion"})
    cleared_at = hass.states.get(source_ids[1]).last_changed
    monkeypatch.setattr(
        "homeassistant.helpers.template.extensions.datetime.dt_util.now",
        lambda: dt_util.as_local(cleared_at + timedelta(seconds=299)),
    )
    assert render_template() == "true"

    monkeypatch.setattr(
        "homeassistant.helpers.template.extensions.datetime.dt_util.now",
        lambda: dt_util.as_local(cleared_at + timedelta(seconds=301)),
    )
    assert render_template() == "false"


def test_presence_motion_helper_requires_binary_sensor_sources(hass):
    hass.states.async_set(
        "light.invalid_motion_source", "on", {"device_class": "motion"}
    )
    hass.states.async_set(
        "switch.invalid_motion_source", "on", {"device_class": "motion"}
    )

    defaults = _reference_entity_defaults(
        hass,
        ["light.invalid_motion_source", "switch.invalid_motion_source"],
    )

    assert defaults[CONF_PLATFORM] == "binary_sensor"
    assert "this.state == 'on'" not in defaults[CONF_VALUE_TEMPLATE]
    assert CONF_DOMAIN_OPTIONS_JSON not in defaults


def test_reference_entity_defaults_combines_number_sources_with_average_template(hass):
    hass.states.async_set("sensor.living_room_temp", "20")
    hass.states.async_set("number.target_temp", "24")

    defaults = _reference_entity_defaults(
        hass,
        ["sensor.living_room_temp", "number.target_temp"],
    )

    assert defaults[CONF_PLATFORM] == "sensor"
    assert defaults[CONF_INITIAL_VALUE] == "22.0"
    assert "select('is_number')" in defaults[CONF_VALUE_TEMPLATE]
    assert "values | average" in defaults[CONF_VALUE_TEMPLATE]


def test_reference_entity_defaults_treats_zero_one_sensors_as_numbers(hass):
    hass.states.async_set("sensor.failed_jobs", "0")
    hass.states.async_set("sensor.pending_jobs", "1")

    defaults = _reference_entity_defaults(
        hass,
        ["sensor.failed_jobs", "sensor.pending_jobs"],
    )

    assert defaults[CONF_PLATFORM] == "sensor"
    assert defaults[CONF_INITIAL_VALUE] == "0.5"
    assert "values | average" in defaults[CONF_VALUE_TEMPLATE]


def test_reference_entity_defaults_ignores_unknown_number_in_initial_average(hass):
    hass.states.async_set("number.first_reading", "unknown")
    hass.states.async_set("sensor.second_reading", "30")

    defaults = _reference_entity_defaults(
        hass,
        ["number.first_reading", "sensor.second_reading"],
    )

    assert defaults[CONF_PLATFORM] == "sensor"
    assert defaults[CONF_INITIAL_VALUE] == "30.0"


def test_number_helper_ignores_invalid_runtime_values(hass):
    hass.states.async_set("sensor.first", "10")
    hass.states.async_set("sensor.second", "20")

    defaults = _reference_entity_defaults(hass, ["sensor.first", "sensor.second"])
    template = Template(defaults[CONF_VALUE_TEMPLATE], hass)

    assert template.async_render(
        variables={"first": "bad", "second": "20"},
        parse_result=True,
    ) == 20.0
    assert (
        template.async_render(
            variables={"first": "unknown", "second": "unavailable"},
            parse_result=False,
        ).strip()
        == "unknown"
    )


def test_reference_entity_defaults_preserves_water_usage_class_and_unit(hass):
    hass.states.async_set(
        "sensor.water_meter_one",
        "12",
        {"device_class": "water", "unit_of_measurement": "L"},
    )
    hass.states.async_set(
        "sensor.water_meter_two",
        "8",
        {"device_class": "water", "unit_of_measurement": "L"},
    )

    defaults = _reference_entity_defaults(
        hass,
        [
            "sensor.water_meter_one",
            "sensor.water_meter_two",
        ],
    )

    assert json.loads(defaults[CONF_DOMAIN_OPTIONS_JSON]) == {
        "class": "water",
        "unit_of_measurement": "L",
    }


def test_reference_entity_defaults_accepts_standard_energy_sensor(hass):
    """Copying a normal Energy dashboard sensor must not fail the source step."""
    hass.states.async_set(
        "sensor.energy_monitor",
        "12.5",
        {
            ATTR_FRIENDLY_NAME: "Energy",
            "device_class": "energy",
            "state_class": "total_increasing",
            "unit_of_measurement": "kWh",
            CONF_ICON: "mdi:flash",
        },
    )

    defaults = _reference_entity_defaults(hass, ["sensor.energy_monitor"])

    assert defaults[CONF_PLATFORM] == "sensor"
    assert defaults[CONF_INITIAL_VALUE] == "12.5"
    assert defaults[CONF_DOMAIN_OPTIONS_JSON]


def test_config_flow_validation_errors_are_logged_at_error(caplog):
    errors = _flow_errors(object(), "entity")

    with caplog.at_level(
        logging.ERROR,
        logger="custom_components.virtual_layer.config_flow",
    ):
        errors["value_template"] = "invalid_template"

    assert "config-flow validation error" in caplog.text
    assert "step=entity" in caplog.text
    assert "field=value_template" in caplog.text
    assert "error=invalid_template" in caplog.text


async def test_unhandled_config_flow_errors_include_traceback(caplog):
    @_log_unhandled_flow_errors
    class BrokenFlow:
        async def async_step_entity(self, user_input=None):
            raise RuntimeError("source defaults exploded")

    with caplog.at_level(
        logging.ERROR,
        logger="custom_components.virtual_layer.config_flow",
    ), pytest.raises(RuntimeError, match="source defaults exploded"):
        await BrokenFlow().async_step_entity({"source_entities": []})

    record = next(
        record
        for record in caplog.records
        if record.name == "custom_components.virtual_layer.config_flow"
    )
    assert record.levelno == logging.ERROR
    assert record.exc_info is not None
    assert "Unhandled Virtual Layer config-flow error" in record.message
    assert "input_keys=['source_entities']" in record.getMessage()


def test_reference_entity_defaults_combines_string_sources_with_concat_template(hass):
    hass.states.async_set("sensor.washer_phase", "wash")
    hass.states.async_set("sensor.washer_mode", "eco")

    defaults = _reference_entity_defaults(
        hass,
        ["sensor.washer_phase", "sensor.washer_mode"],
    )

    assert defaults[CONF_PLATFORM] == "sensor"
    assert defaults[CONF_INITIAL_VALUE] == "washeco"
    assert "values | join('')" in defaults[CONF_VALUE_TEMPLATE]
    assert "washer_phase, washer_mode" in defaults[CONF_VALUE_TEMPLATE]
    assert defaults[CONF_ATTRIBUTE_TEMPLATES_JSON] == (
        '{"washer_mode": "{{ washer_mode }}", "washer_phase": "{{ washer_phase }}"}'
    )


def test_reference_entity_defaults_generates_dynamic_single_source_attributes(hass):
    hass.states.async_set(
        "climate.office",
        "cool",
        {
            "hvac_modes": ["off", "cool"],
            "temperature": 23,
            "vendor_status": "healthy",
        },
    )

    defaults = _reference_entity_defaults(hass, ["climate.office"])
    templates = json.loads(defaults[CONF_ATTRIBUTE_TEMPLATES_JSON])

    assert templates == {
        "vendor_status": "{{ state_attr('climate.office', 'vendor_status') }}",
    }
    assert "temperature" not in templates
    assert Template(templates["vendor_status"], hass).async_render() == "healthy"


def test_reference_entity_defaults_merges_common_attributes_by_type(hass):
    hass.states.async_set(
        "sensor.first_process",
        "wash",
        {
            "active": True,
            "label": "washer",
            "load": 10,
            "metadata": {1: "first", "shared": "first"},
            "steps": [{"name": "wash"}],
            "tags": ["laundry", "wet"],
        },
    )
    hass.states.async_set(
        "sensor.second_process",
        "dry",
        {
            "active": False,
            "label": "dryer",
            "load": 20,
            "metadata": {2: "second", "shared": "second"},
            "steps": [{"name": "wash"}, {"name": "dry"}],
            "tags": ["laundry", "dry"],
        },
    )

    defaults = _reference_entity_defaults(
        hass,
        ["sensor.first_process", "sensor.second_process"],
    )
    templates = json.loads(defaults[CONF_ATTRIBUTE_TEMPLATES_JSON])

    assert Template(templates["active"], hass).async_render() is False
    assert Template(templates["load"], hass).async_render() == 15.0
    assert Template(templates["label"], hass).async_render() == "washerdryer"
    assert Template(templates["tags"], hass).async_render() == [
        "laundry",
        "wet",
        "dry",
    ]
    assert Template(templates["metadata"], hass).async_render() == {
        1: "first",
        2: "second",
        "shared": "second",
    }
    assert Template(templates["steps"], hass).async_render() == [
        {"name": "wash"},
        {"name": "dry"},
    ]
    assert "first_process" in templates
    assert "second_process" in templates


def test_reference_camera_defaults_create_image_and_stream_alias(hass):
    hass.states.async_set("camera.front_door", "on", {"friendly_name": "Front Door"})

    defaults = _reference_entity_defaults(hass, ["camera.front_door"])

    assert defaults[CONF_PLATFORM] == "camera"
    assert defaults[CONF_INITIAL_VALUE] == "on"
    assert defaults[CONF_SOURCE_ENTITIES_TEXT] == "camera.front_door"
    assert defaults[CONF_VALUE_TEMPLATE] == "{{ front_door }}"
    assert json.loads(defaults[CONF_DOMAIN_OPTIONS_JSON]) == {
        "source_entity": "camera.front_door",
    }
    assert defaults[CONF_NATIVE_VALUE_TEMPLATES]["source_entity"] == (
        "{{ 'camera.front_door' }}"
    )


def test_reference_light_generates_boolean_and_brightness_templates(hass):
    hass.states.async_set(
        "light.desk",
        "on",
        {"brightness": 128, CONF_ICON: "mdi:desk-lamp"},
    )

    defaults = _reference_entity_defaults(hass, ["light.desk"])

    assert defaults[CONF_NATIVE_VALUE_TEMPLATES]["is_on"] == (
        "{{ states('light.desk') not in ['off', 'unknown', 'unavailable'] }}"
    )
    assert defaults[CONF_NATIVE_VALUE_TEMPLATES]["brightness"] == (
        "{{ state_attr('light.desk', 'brightness') }}"
    )
    assert defaults[CONF_AVAILABILITY_TEMPLATE] == (
        "{{ states('light.desk') not in ['unknown', 'unavailable'] }}"
    )
    assert defaults[CONF_ICON_TEMPLATE] == (
        "{{ state_attr('light.desk', 'icon') | default('', true) }}"
    )
    _, entity = _build_entity_config(_entity_input(defaults))
    assert entity[CONF_ICON_TEMPLATE] == (
        "{{ state_attr('light.desk', 'icon') | default('', true) }}"
    )
    assert CONF_ICON not in entity.get(CONF_ATTRIBUTES, {})


def test_reference_tts_generates_language_and_option_templates(hass):
    hass.states.async_set(
        "tts.house_voice",
        "ready",
        {
            "friendly_name": "House Voice",
            "supported_languages": ["en", "ko"],
            "default_language": "ko",
            "supported_options": ["voice"],
            "default_options": {"voice": "female"},
        },
    )

    defaults = _reference_entity_defaults(hass, ["tts.house_voice"])
    native_templates = defaults[CONF_NATIVE_VALUE_TEMPLATES]

    assert defaults[CONF_PLATFORM] == "tts"
    assert native_templates == {
        "supported_languages": (
            "{{ state_attr('tts.house_voice', 'supported_languages') }}"
        ),
        "default_language": (
            "{{ state_attr('tts.house_voice', 'default_language') }}"
        ),
        "supported_options": (
            "{{ state_attr('tts.house_voice', 'supported_options') }}"
        ),
        "default_options": (
            "{{ state_attr('tts.house_voice', 'default_options') }}"
        ),
    }


def test_reference_entity_defaults_combines_date_sources_with_latest_template(hass):
    hass.states.async_set("date.started", "2026-08-01")
    hass.states.async_set("date.finished", "2026-08-04")

    defaults = _reference_entity_defaults(
        hass,
        ["date.started", "date.finished"],
    )

    assert defaults[CONF_PLATFORM] == "date"
    assert defaults[CONF_INITIAL_VALUE] == "2026-08-04"
    assert "sort | last" in defaults[CONF_VALUE_TEMPLATE]


def test_reference_entity_defaults_combines_time_sources_with_latest_template(hass):
    hass.states.async_set("time.morning", "08:30:00")
    hass.states.async_set("time.evening", "21:15:00")

    defaults = _reference_entity_defaults(
        hass,
        ["time.morning", "time.evening"],
    )

    assert defaults[CONF_PLATFORM] == "time"
    assert defaults[CONF_INITIAL_VALUE] == "21:15:00"
    assert "sort | last" in defaults[CONF_VALUE_TEMPLATE]


def test_reference_entity_defaults_combines_datetime_sources_with_latest_template(hass):
    # Lexicographic order picks first_seen, but last_seen is the later instant.
    hass.states.async_set("datetime.first_seen", "2026-08-04T00:30:00+09:00")
    hass.states.async_set("datetime.last_seen", "2026-08-03T20:00:00+00:00")

    defaults = _reference_entity_defaults(
        hass,
        ["datetime.first_seen", "datetime.last_seen"],
    )

    assert defaults[CONF_PLATFORM] == "datetime"
    assert defaults[CONF_INITIAL_VALUE] == "2026-08-03T20:00:00+00:00"
    assert "as_timestamp" in defaults[CONF_VALUE_TEMPLATE]
    assert (
        Template(defaults[CONF_VALUE_TEMPLATE], hass).async_render(
            variables={
                "first_seen": "2026-08-04T00:30:00+09:00",
                "last_seen": "2026-08-03T20:00:00+00:00",
            },
            parse_result=False,
        )
        == "2026-08-03T20:00:00+00:00"
    )


def test_reference_entity_defaults_combines_enum_sources_with_first_available_template(
    hass,
):
    hass.states.async_set("select.primary_mode", "unknown")
    hass.states.async_set("input_select.fallback_mode", "eco")

    defaults = _reference_entity_defaults(
        hass,
        ["select.primary_mode", "input_select.fallback_mode"],
    )

    assert defaults[CONF_PLATFORM] == "select"
    assert defaults[CONF_INITIAL_VALUE] == "eco"
    assert "values[0] if values else 'unknown'" in defaults[CONF_VALUE_TEMPLATE]


def test_reference_entity_defaults_creates_location_median_helper(hass):
    hass.states.async_set(
        "device_tracker.phone",
        "home",
        {"latitude": 37.5, "longitude": 127.0},
    )
    hass.states.async_set(
        "person.owner",
        "not_home",
        {"latitude": 37.6, "longitude": 127.1},
    )

    defaults = _reference_entity_defaults(
        hass,
        ["device_tracker.phone", "person.owner"],
    )

    assert defaults[CONF_PLATFORM] == "device_tracker"
    assert defaults[CONF_INITIAL_VALUE] == "not_home"
    assert defaults[CONF_VALUE_TEMPLATE] == ""
    assert json.loads(defaults[CONF_DOMAIN_OPTIONS_JSON]) == {
        CONF_LOCATION_HELPER: {
            "distance_threshold_meters": 300,
            "priority_window_seconds": 1800,
        },
    }
    assert defaults.get(CONF_ATTRIBUTES_JSON, "") == ""
    assert CONF_NATIVE_VALUE_TEMPLATES not in defaults


def test_multiple_geolocation_sources_use_device_tracker_location_helper(hass):
    hass.states.async_set(
        "geolocation.first",
        "Near home",
        {"latitude": 37.5, "longitude": 127.0},
    )
    hass.states.async_set(
        "geolocation.second",
        "Near work",
        {"latitude": 37.6, "longitude": 127.1},
    )

    defaults = _reference_entity_defaults(
        hass,
        ["geolocation.first", "geolocation.second"],
    )

    assert defaults[CONF_PLATFORM] == "device_tracker"
    assert defaults[CONF_VALUE_TEMPLATE] == ""
    assert CONF_LOCATION_HELPER in json.loads(defaults[CONF_DOMAIN_OPTIONS_JSON])


def test_reference_entity_defaults_uses_location_helper_for_single_location_source(
    hass,
):
    hass.states.async_set(
        "device_tracker.phone",
        "home",
        {
            ATTR_FRIENDLY_NAME: "Hwajin's iPhone 14 Pro (iCloud)",
            "latitude": 37.5,
            "longitude": 127.0,
            "battery_level": 55,
        },
    )

    defaults = _reference_entity_defaults(hass, ["device_tracker.phone"])

    assert defaults[CONF_PLATFORM] == "device_tracker"
    assert defaults[CONF_VALUE_TEMPLATE] == ""
    assert json.loads(defaults[CONF_DOMAIN_OPTIONS_JSON]) == {
        CONF_LOCATION_HELPER: {
            "distance_threshold_meters": 300,
            "priority_window_seconds": 1800,
        },
    }
    assert defaults.get(CONF_ATTRIBUTES_JSON, "") == ""
    assert CONF_NATIVE_VALUE_TEMPLATES not in defaults


def test_reference_entity_defaults_shortens_generated_combined_location_names(hass):
    long_name = (
        "Hwajin's iPhone 14 Pro (iCloud) "
        "(hwajin_s_iphone_14_pro_icloud) "
        "Hwajin's Apple Watch Ultra 2 (iCloud)"
    )
    hass.states.async_set(
        "device_tracker.phone",
        "home",
        {
            ATTR_FRIENDLY_NAME: long_name,
            "latitude": 37.5,
            "longitude": 127.0,
            "device_configuration": "large blob",
        },
    )
    hass.states.async_set(
        "person.owner",
        "not_home",
        {
            ATTR_FRIENDLY_NAME: "Hwajin Lee",
            "latitude": 37.6,
            "longitude": 127.1,
        },
    )

    defaults = _reference_entity_defaults(
        hass, ["device_tracker.phone", "person.owner"]
    )

    assert defaults[CONF_ENTITY_NAME].startswith(
        "Combined Hwajin's iPhone 14 Pro (iCloud)",
    )
    assert defaults[CONF_ENTITY_NAME].endswith("...")
    assert len(defaults[CONF_ENTITY_NAME]) <= 80
    assert "Apple Watch" not in defaults[CONF_ENTITY_NAME]
    assert defaults.get(CONF_ATTRIBUTES_JSON, "") == ""


def test_default_virtual_entity_id_shortens_generated_slug():
    entity_id = _default_virtual_entity_id(
        "device_tracker",
        "Combined " + ("very long source name " * 10),
    )

    assert entity_id.startswith("device_tracker.combined_very_long_source_name")
    assert len(entity_id.split(".", 1)[1]) <= 80


def test_build_device_config_supports_device_registry_metadata():
    device = _build_device_config(
        _entity_input(
            {
                CONF_DEVICE_ID: "laundry-appliance-1",
                CONF_DEVICE_MANUFACTURER: "Acme",
                CONF_DEVICE_MODEL: "Washer 9000",
                CONF_DEVICE_SW_VERSION: "2026.8",
                CONF_DEVICE_HW_VERSION: "rev-a",
                CONF_DEVICE_SERIAL_NUMBER: "SN-123",
                CONF_DEVICE_CONFIGURATION_URL: "https://example.test/laundry",
                CONF_DEVICE_SUGGESTED_AREA: "Laundry Room",
                CONF_DEVICE_VIA_DEVICE_ID: "parent-device-id",
            }
        ),
        "Laundry",
    )

    assert device == {
        ATTR_DEVICE_ID: "laundry-appliance-1",
        CONF_NAME: "Laundry",
        CONF_MANUFACTURER: "Acme",
        CONF_MODEL: "Washer 9000",
        CONF_SW_VERSION: "2026.8",
        CONF_HW_VERSION: "rev-a",
        CONF_SERIAL_NUMBER: "SN-123",
        CONF_CONFIGURATION_URL: "https://example.test/laundry",
        CONF_SUGGESTED_AREA: "Laundry Room",
        CONF_VIA_DEVICE_ID: "parent-device-id",
    }


def test_build_device_config_defaults_device_id_to_device_name():
    assert _build_device_config(_entity_input(), "Laundry") == {
        ATTR_DEVICE_ID: "Laundry",
        CONF_NAME: "Laundry",
    }


def test_build_entity_config_requires_entity_id_domain_to_match_platform():
    with pytest.raises(InvalidEntityId):
        _build_entity_config(
            _entity_input(
                {
                    ATTR_ENTITY_ID: "switch.washer_phase",
                    CONF_PLATFORM: "sensor",
                }
            )
        )


def test_build_entity_config_rejects_non_object_json_fields():
    with pytest.raises(InvalidJson) as err:
        _build_entity_config(
            _entity_input(
                {
                    CONF_ATTRIBUTES_JSON: '["not", "object"]',
                }
            )
        )

    assert err.value.field_name == CONF_ATTRIBUTES_JSON


def test_build_entity_config_adds_number_defaults():
    _, entity = _build_entity_config(
        _entity_input(
            {
                CONF_PLATFORM: "number",
                CONF_INITIAL_VALUE: "10",
            }
        )
    )

    assert entity[CONF_MIN] == 0
    assert entity[CONF_MAX] == 100


def test_build_entity_config_supports_attribute_sources_and_pull_interval():
    _, entity = _build_entity_config(
        _entity_input(
            {
                CONF_PULL_INTERVAL: 30,
                CONF_ATTRIBUTE_SOURCES_JSON: (
                    '{"battery": "sensor.remote.battery_level", '
                    '"phase": {"entity_id": "sensor.washer", "attribute": "state"}}'
                ),
            }
        )
    )

    assert entity[CONF_PULL_INTERVAL] == 30
    assert entity[CONF_ATTRIBUTE_SOURCES] == {
        "battery": {
            ATTR_ENTITY_ID: "sensor.remote",
            CONF_ATTRIBUTE: "battery_level",
        },
        "phase": {
            ATTR_ENTITY_ID: "sensor.washer",
            CONF_ATTRIBUTE: "state",
        },
    }


def test_build_entity_config_supports_template_sources_for_composite_values():
    _, entity = _build_entity_config(
        _entity_input(
            {
                CONF_TEMPLATE_SOURCES_JSON: (
                    '{"power": "sensor.washer_power", '
                    '"door": "binary_sensor.washer_door.state", '
                    '"humidity": {"entity_id": "sensor.laundry", "attribute": "humidity"}}'
                ),
                CONF_VALUE_TEMPLATE: (
                    "{% if power|float(0) > 10 and door == 'off' %}"
                    "running"
                    "{% else %}"
                    "idle"
                    "{% endif %}"
                ),
            }
        )
    )

    assert entity[CONF_TEMPLATE_SOURCES] == {
        "power": {
            ATTR_ENTITY_ID: "sensor.washer_power",
            CONF_ATTRIBUTE: "state",
        },
        "door": {
            ATTR_ENTITY_ID: "binary_sensor.washer_door",
            CONF_ATTRIBUTE: "state",
        },
        "humidity": {
            ATTR_ENTITY_ID: "sensor.laundry",
            CONF_ATTRIBUTE: "humidity",
        },
    }
    assert "power|float" in entity[CONF_VALUE_TEMPLATE]


def test_build_camera_alias_adds_source_subscription_and_state_template():
    _, entity = _build_entity_config(
        _entity_input(
            {
                CONF_PLATFORM: "camera",
                CONF_INITIAL_VALUE: "off",
                CONF_DOMAIN_OPTIONS_JSON: '{"source_entity": "camera.front_door"}',
            }
        )
    )

    assert entity["source_entity"] == "camera.front_door"
    assert entity[CONF_SOURCE_ENTITIES] == ["camera.front_door"]
    assert entity[CONF_VALUE_TEMPLATE] == "{{ states('camera.front_door') }}"


def test_build_entity_config_accepts_common_icon_and_template_for_native_domains():
    _, entity = _build_entity_config(
        _entity_input(
            {
                CONF_PLATFORM: "binary_sensor",
                CONF_ICON: "mdi:door-open",
                CONF_ICON_TEMPLATE: (
                    "{{ 'mdi:door-open' if this.state == 'on' else 'mdi:door-closed' }}"
                ),
            }
        )
    )

    assert entity[CONF_ICON] == "mdi:door-open"
    assert entity[CONF_ICON_TEMPLATE] == (
        "{{ 'mdi:door-open' if this.state == 'on' else 'mdi:door-closed' }}"
    )


def test_build_camera_alias_rejects_non_camera_source():
    with pytest.raises(InvalidDomainOptions):
        _build_entity_config(
            _entity_input(
                {
                    CONF_PLATFORM: "camera",
                    CONF_DOMAIN_OPTIONS_JSON: '{"source_entity": "sensor.front_door"}',
                }
            )
        )


def test_build_entity_config_rejects_explicit_self_references():
    with pytest.raises(InvalidEntityReference) as err:
        _build_entity_config(
            _entity_input(
                {
                    ATTR_ENTITY_ID: "sensor.self_referencing",
                    CONF_SOURCE_ENTITIES_TEXT: "sensor.self_referencing",
                }
            )
        )

    assert err.value.field_name == CONF_SOURCE_ENTITIES_TEXT


def test_build_camera_alias_rejects_itself_as_source():
    with pytest.raises(InvalidEntityReference) as err:
        _build_entity_config(
            _entity_input(
                {
                    ATTR_ENTITY_ID: "camera.self_alias",
                    CONF_PLATFORM: "camera",
                    CONF_DOMAIN_OPTIONS_JSON: '{"source_entity": "camera.self_alias"}',
                }
            )
        )

    assert err.value.field_name == CONF_DOMAIN_OPTIONS_JSON


def test_build_generic_entity_keeps_direct_domain_options():
    _, entity = _build_entity_config(
        _entity_input(
            {
                CONF_PLATFORM: "weather",
                CONF_DOMAIN_OPTIONS_JSON: (
                    '{"temperature": 21.5, "humidity": 48, '
                    '"forecast_provider": "virtual"}'
                ),
            }
        )
    )

    assert entity["temperature"] == 21.5
    assert entity["humidity"] == 48
    assert entity["forecast_provider"] == "virtual"


def test_entity_form_preserves_generic_direct_domain_options_for_editing():
    defaults = _entity_form_defaults(
        "Weather",
        {
            CONF_PLATFORM: "weather",
            CONF_NAME: "Virtual Forecast",
            "temperature": 21.5,
            "humidity": 48,
        },
    )

    assert json.loads(defaults[CONF_DOMAIN_OPTIONS_JSON]) == {
        "humidity": 48,
        "temperature": 21.5,
    }


def test_auto_helper_refresh_replaces_only_generated_helper_fields():
    current = {
        CONF_DEVICE_NAME: "Custom Device",
        CONF_ENTITY_NAME: "Custom Name",
        CONF_PLATFORM: "sensor",
        CONF_INITIAL_VALUE: "10",
        CONF_SOURCE_ENTITIES_TEXT: "sensor.old",
        CONF_TEMPLATE_SOURCES_JSON: '{"old": "sensor.old"}',
        CONF_VALUE_TEMPLATE: "{{ old }}",
        CONF_ATTRIBUTES_JSON: '{"battery": 50}',
        CONF_ATTRIBUTE_SOURCES_JSON: "",
        CONF_ATTRIBUTE_TEMPLATES_JSON: '{"old": "{{ old }}"}',
        CONF_DOMAIN_OPTIONS_JSON: '{"old_option": true}',
    }
    reference = {
        CONF_PLATFORM: "sensor",
        CONF_INITIAL_VALUE: "20",
        CONF_SOURCE_ENTITIES_TEXT: "sensor.new",
        CONF_TEMPLATE_SOURCES_JSON: '{"new": "sensor.new"}',
        CONF_VALUE_TEMPLATE: "{{ new }}",
        CONF_ATTRIBUTES_JSON: '{"battery": 90}',
    }

    refreshed = _reference_edit_defaults(current, reference, auto_helper=True)

    assert refreshed[CONF_DEVICE_NAME] == "Custom Device"
    assert refreshed[CONF_ENTITY_NAME] == "Custom Name"
    assert refreshed[CONF_SOURCE_ENTITIES_TEXT] == "sensor.new"
    assert refreshed[CONF_VALUE_TEMPLATE] == "{{ new }}"
    assert refreshed[CONF_ATTRIBUTES_JSON] == '{"battery": 90}'
    assert refreshed[CONF_ATTRIBUTE_TEMPLATES_JSON] == ""
    assert CONF_DOMAIN_OPTIONS_JSON not in refreshed


def test_custom_template_fields_are_preserved_while_generated_fields_refresh():
    generated = {
        CONF_PLATFORM: "sensor",
        CONF_SOURCE_ENTITIES_TEXT: "sensor.old",
        CONF_TEMPLATE_SOURCES_JSON: '{"old": "sensor.old"}',
        CONF_VALUE_TEMPLATE: "{{ old }}",
        CONF_ATTRIBUTES_JSON: "",
        CONF_ATTRIBUTE_SOURCES_JSON: "",
        CONF_ATTRIBUTE_TEMPLATES_JSON: "",
        CONF_DOMAIN_OPTIONS_JSON: "",
        CONF_INITIAL_VALUE: "10",
    }
    current = {
        **generated,
        CONF_VALUE_TEMPLATE: "{{ old | float(0) * 2 }}",
    }
    reference = {
        CONF_PLATFORM: "sensor",
        CONF_SOURCE_ENTITIES_TEXT: "sensor.new",
        CONF_TEMPLATE_SOURCES_JSON: '{"new": "sensor.new"}',
        CONF_VALUE_TEMPLATE: "{{ new }}",
        CONF_ATTRIBUTES_JSON: "",
        CONF_INITIAL_VALUE: "20",
    }
    entity = {}
    generated_profile = _auto_helper_profile(generated)

    refreshed = _reference_edit_defaults(current, reference, generated_profile)

    assert refreshed[CONF_SOURCE_ENTITIES_TEXT] == "sensor.new"
    assert refreshed[CONF_TEMPLATE_SOURCES_JSON] == current[CONF_TEMPLATE_SOURCES_JSON]
    assert refreshed[CONF_VALUE_TEMPLATE] == current[CONF_VALUE_TEMPLATE]
    assert refreshed[CONF_INITIAL_VALUE] == reference[CONF_INITIAL_VALUE]
    _set_auto_helper_profile(entity, current, reference, generated_profile)

    assert entity["auto_helper"] == _auto_helper_profile(reference)


def test_unrelated_custom_field_does_not_block_generated_template_refresh():
    generated = {
        CONF_PLATFORM: "sensor",
        CONF_INITIAL_VALUE: "10",
        CONF_SOURCE_ENTITIES_TEXT: "sensor.old",
        CONF_TEMPLATE_SOURCES_JSON: '{"old": "sensor.old"}',
        CONF_VALUE_TEMPLATE: "{{ old }}",
        CONF_ATTRIBUTES_JSON: '{"battery": 50}',
    }
    current = {
        **generated,
        CONF_INITIAL_VALUE: "manually-set",
        CONF_ATTRIBUTES_JSON: '{"battery": 75}',
    }
    reference = {
        CONF_PLATFORM: "sensor",
        CONF_INITIAL_VALUE: "20",
        CONF_SOURCE_ENTITIES_TEXT: "sensor.new",
        CONF_TEMPLATE_SOURCES_JSON: '{"new": "sensor.new"}',
        CONF_VALUE_TEMPLATE: "{{ new }}",
        CONF_ATTRIBUTES_JSON: '{"battery": 90}',
    }

    refreshed = _reference_edit_defaults(
        current,
        reference,
        _auto_helper_profile(generated),
    )

    assert refreshed[CONF_SOURCE_ENTITIES_TEXT] == "sensor.new"
    assert refreshed[CONF_TEMPLATE_SOURCES_JSON] == '{"new": "sensor.new"}'
    assert refreshed[CONF_VALUE_TEMPLATE] == "{{ new }}"
    assert refreshed[CONF_INITIAL_VALUE] == "manually-set"
    assert refreshed[CONF_ATTRIBUTES_JSON] == '{"battery": 75}'


def test_auto_helper_refreshes_generated_climate_modes_but_preserves_custom_modes():
    generated = {
        CONF_PLATFORM: "climate",
        CONF_INITIAL_VALUE: "cool",
        CONF_SOURCE_ENTITIES_TEXT: "climate.old",
        CONF_TEMPLATE_SOURCES_JSON: '{"old": "climate.old"}',
        CONF_VALUE_TEMPLATE: "{{ old }}",
        "hvac_modes": ["off", "cool"],
        "fan_modes": ["auto", "turbo"],
        "fan_mode": "auto",
        "preset_modes": ["none", "sleep"],
        "preset_mode": "none",
        "swing_modes": [],
    }
    reference = {
        CONF_PLATFORM: "climate",
        CONF_INITIAL_VALUE: "heat",
        CONF_SOURCE_ENTITIES_TEXT: "climate.new",
        CONF_TEMPLATE_SOURCES_JSON: '{"new": "climate.new"}',
        CONF_VALUE_TEMPLATE: "{{ new }}",
        "hvac_modes": ["off", "heat"],
        "fan_modes": ["low", "high"],
        "fan_mode": "low",
        "preset_modes": ["none", "eco"],
        "preset_mode": "eco",
    }

    refreshed = _reference_edit_defaults(
        generated,
        reference,
        _auto_helper_profile(generated),
    )
    assert refreshed["hvac_modes"] == ["off", "heat"]
    assert refreshed["fan_modes"] == ["low", "high"]
    assert refreshed["fan_mode"] == "low"
    assert refreshed["preset_mode"] == "eco"
    assert "swing_modes" not in refreshed

    customized = {
        **generated,
        "fan_modes": ["auto", "quiet"],
        "fan_mode": "quiet",
    }
    refreshed_custom = _reference_edit_defaults(
        customized,
        reference,
        _auto_helper_profile(generated),
    )
    assert refreshed_custom["fan_modes"] == ["auto", "quiet"]
    assert refreshed_custom["fan_mode"] == "quiet"
    assert refreshed_custom["preset_modes"] == ["none", "eco"]


def test_auto_helper_refreshes_generated_boiler_actions_but_preserves_custom_actions():
    generated = {
        CONF_PLATFORM: "climate",
        CONF_SOURCE_ENTITIES_TEXT: "climate.boiler",
        CONF_COMMAND_ACTIONS_JSON: json.dumps({
            "turn_off": [{"action": "climate.turn_off"}],
        }),
    }
    reference = {
        CONF_PLATFORM: "climate",
        CONF_SOURCE_ENTITIES_TEXT: "climate.boiler\nswitch.hot_water",
        CONF_COMMAND_ACTIONS_JSON: json.dumps({
            "turn_off": [
                {"action": "climate.turn_off"},
                {"action": "switch.turn_on"},
            ],
        }),
    }

    refreshed = _reference_edit_defaults(
        generated,
        reference,
        _auto_helper_profile(generated),
    )
    assert refreshed[CONF_COMMAND_ACTIONS_JSON] == reference[
        CONF_COMMAND_ACTIONS_JSON
    ]

    customized = {
        **generated,
        CONF_COMMAND_ACTIONS_JSON: json.dumps({
            "turn_off": [{"action": "script.custom_boiler_off"}],
        }),
    }
    refreshed_custom = _reference_edit_defaults(
        customized,
        reference,
        _auto_helper_profile(generated),
    )
    assert refreshed_custom[CONF_COMMAND_ACTIONS_JSON] == customized[
        CONF_COMMAND_ACTIONS_JSON
    ]

    forced = _reference_edit_defaults(
        customized,
        reference,
        _auto_helper_profile(generated),
        force_template_helper=True,
    )
    assert forced[CONF_COMMAND_ACTIONS_JSON] == reference[
        CONF_COMMAND_ACTIONS_JSON
    ]


def test_auto_helper_refreshes_native_jinja_per_field_and_preserves_custom_values():
    generated = {
        CONF_PLATFORM: "climate",
        CONF_SOURCE_ENTITIES_TEXT: "climate.old",
        CONF_AVAILABILITY_TEMPLATE: "{{ states('climate.old') != 'unavailable' }}",
        CONF_ICON_TEMPLATE: "{{ state_attr('climate.old', 'icon') }}",
        CONF_NATIVE_VALUE_TEMPLATES: {
            "hvac_mode": "{{ states('climate.old') }}",
            "fan_mode": "{{ state_attr('climate.old', 'fan_mode') }}",
        },
    }
    current = {
        **generated,
        CONF_AVAILABILITY_TEMPLATE: "{{ true }}",
        CONF_NATIVE_VALUE_TEMPLATES: {
            **generated[CONF_NATIVE_VALUE_TEMPLATES],
            "fan_mode": "{{ 'quiet' }}",
        },
    }
    reference = {
        CONF_PLATFORM: "climate",
        CONF_SOURCE_ENTITIES_TEXT: "climate.new",
        CONF_AVAILABILITY_TEMPLATE: "{{ states('climate.new') != 'unavailable' }}",
        CONF_ICON_TEMPLATE: "{{ state_attr('climate.new', 'icon') }}",
        CONF_NATIVE_VALUE_TEMPLATES: {
            "hvac_mode": "{{ states('climate.new') }}",
            "fan_mode": "{{ state_attr('climate.new', 'fan_mode') }}",
            "preset_mode": "{{ state_attr('climate.new', 'preset_mode') }}",
        },
    }

    refreshed = _reference_edit_defaults(
        current,
        reference,
        _auto_helper_profile(generated),
    )

    assert refreshed[CONF_NATIVE_VALUE_TEMPLATES] == {
        "hvac_mode": "{{ states('climate.new') }}",
        "fan_mode": "{{ 'quiet' }}",
        "preset_mode": "{{ state_attr('climate.new', 'preset_mode') }}",
    }
    assert refreshed[CONF_AVAILABILITY_TEMPLATE] == "{{ true }}"
    assert refreshed[CONF_ICON_TEMPLATE] == "{{ state_attr('climate.new', 'icon') }}"

    forced = _reference_edit_defaults(
        current,
        reference,
        _auto_helper_profile(generated),
        force_template_helper=True,
    )
    assert forced[CONF_NATIVE_VALUE_TEMPLATES] == reference[
        CONF_NATIVE_VALUE_TEMPLATES
    ]


def test_auto_helper_refreshes_icon_and_availability_per_field():
    generated = {
        CONF_PLATFORM: "sensor",
        CONF_SOURCE_ENTITIES_TEXT: "sensor.old",
        CONF_AVAILABILITY_TEMPLATE: "{{ states('sensor.old') != 'unavailable' }}",
        CONF_ICON_TEMPLATE: "{{ state_attr('sensor.old', 'icon') }}",
    }
    reference = {
        CONF_PLATFORM: "sensor",
        CONF_SOURCE_ENTITIES_TEXT: "sensor.new",
        CONF_AVAILABILITY_TEMPLATE: "{{ states('sensor.new') != 'unavailable' }}",
        CONF_ICON_TEMPLATE: "{{ state_attr('sensor.new', 'icon') }}",
    }
    customized = {
        **generated,
        CONF_AVAILABILITY_TEMPLATE: "{{ true }}",
    }

    refreshed = _reference_edit_defaults(
        customized,
        reference,
        _auto_helper_profile(generated),
    )
    assert refreshed[CONF_AVAILABILITY_TEMPLATE] == "{{ true }}"
    assert refreshed[CONF_ICON_TEMPLATE] == reference[CONF_ICON_TEMPLATE]

    forced = _reference_edit_defaults(
        customized,
        reference,
        _auto_helper_profile(generated),
        force_template_helper=True,
    )
    assert forced[CONF_AVAILABILITY_TEMPLATE] == reference[
        CONF_AVAILABILITY_TEMPLATE
    ]
    assert forced[CONF_ICON_TEMPLATE] == reference[CONF_ICON_TEMPLATE]


def test_auto_helper_refreshes_attribute_jinja_per_key_and_removes_stale_helpers():
    generated = {
        CONF_PLATFORM: "sensor",
        CONF_SOURCE_ENTITIES_TEXT: "sensor.old",
        CONF_ATTRIBUTE_TEMPLATES_JSON: json.dumps(
            {
                "generated": "{{ state_attr('sensor.old', 'generated') }}",
                "removed": "{{ state_attr('sensor.old', 'removed') }}",
                "customized": "{{ state_attr('sensor.old', 'customized') }}",
            }
        ),
    }
    current = {
        **generated,
        CONF_ATTRIBUTE_TEMPLATES_JSON: json.dumps(
            {
                "generated": "{{ state_attr('sensor.old', 'generated') }}",
                "removed": "{{ state_attr('sensor.old', 'removed') }}",
                "customized": "{{ 42 }}",
            }
        ),
    }
    reference = {
        CONF_PLATFORM: "sensor",
        CONF_SOURCE_ENTITIES_TEXT: "sensor.new",
        CONF_ATTRIBUTE_TEMPLATES_JSON: json.dumps(
            {
                "generated": "{{ state_attr('sensor.new', 'generated') }}",
                "added": "{{ state_attr('sensor.new', 'added') }}",
                "customized": "{{ state_attr('sensor.new', 'customized') }}",
            }
        ),
    }

    refreshed = _reference_edit_defaults(
        current,
        reference,
        _auto_helper_profile(generated),
    )

    assert json.loads(refreshed[CONF_ATTRIBUTE_TEMPLATES_JSON]) == {
        "added": "{{ state_attr('sensor.new', 'added') }}",
        "customized": "{{ 42 }}",
        "generated": "{{ state_attr('sensor.new', 'generated') }}",
    }

    forced = _reference_edit_defaults(
        current,
        reference,
        _auto_helper_profile(generated),
        force_template_helper=True,
    )
    assert json.loads(forced[CONF_ATTRIBUTE_TEMPLATES_JSON]) == json.loads(
        reference[CONF_ATTRIBUTE_TEMPLATES_JSON]
    )


def test_auto_helper_profile_normalizes_stored_template_source_references():
    generated = {
        CONF_PLATFORM: "binary_sensor",
        CONF_INITIAL_VALUE: "off",
        CONF_SOURCE_ENTITIES_TEXT: "binary_sensor.door_5\nbinary_sensor.door_6",
        CONF_TEMPLATE_SOURCES_JSON: (
            '{"door_5": "binary_sensor.door_5", "door_6": "binary_sensor.door_6"}'
        ),
        CONF_VALUE_TEMPLATE: "{{ door_5 and door_6 }}",
    }
    stored = {
        **generated,
        CONF_TEMPLATE_SOURCES_JSON: (
            '{"door_5": {"attribute": "state", '
            '"entity_id": "binary_sensor.door_5"}, '
            '"door_6": {"attribute": "state", '
            '"entity_id": "binary_sensor.door_6"}}'
        ),
    }

    assert _auto_helper_profile(generated) == _auto_helper_profile(stored)


def test_auto_helper_refresh_updates_entity_id_domain_with_platform():
    current = {
        ATTR_ENTITY_ID: "sensor.combined_doors",
        CONF_PLATFORM: "sensor",
        CONF_SOURCE_ENTITIES_TEXT: "sensor.old",
    }
    reference = {
        CONF_PLATFORM: "binary_sensor",
        CONF_SOURCE_ENTITIES_TEXT: "binary_sensor.new",
    }

    refreshed = _reference_edit_defaults(current, reference, auto_helper=True)

    assert refreshed[CONF_PLATFORM] == "binary_sensor"
    assert refreshed[ATTR_ENTITY_ID] == "binary_sensor.combined_doors"


def test_entity_edit_defaults_recover_legacy_scalar_and_null_values():
    defaults = _entity_form_defaults(
        "Legacy",
        {
            CONF_PLATFORM: "device_tracker",
            CONF_NAME: None,
            ATTR_ENTITY_ID: None,
            CONF_INITIAL_VALUE: None,
            CONF_INITIAL_AVAILABILITY: "not-a-boolean",
            CONF_PERSISTENT: None,
            CONF_SOURCE_ENTITIES: "device_tracker.phone",
            CONF_PULL_INTERVAL: -10,
            CONF_VALUE_TEMPLATE: None,
            CONF_POLYGONAL_ZONE: {
                CONF_POLYGON_FILES: "/config/zones/home.geojson",
                CONF_POLYGON_STRATEGY: "broken",
                CONF_POLYGON_DISTANCE_METERS: "not-a-number",
                CONF_POLYGON_AWAY_STATE: None,
            },
        },
        {
            ATTR_DEVICE_ATTRIBUTES: {
                "Legacy": {
                    ATTR_DEVICE_ID: None,
                    CONF_MANUFACTURER: None,
                    CONF_VIA_DEVICE_ID: None,
                },
            },
        },
    )

    assert defaults[CONF_ENTITY_NAME] == "Virtual Entity"
    assert defaults[CONF_DEVICE_ID] == "Legacy"
    assert defaults[CONF_DEVICE_MANUFACTURER] == ""
    assert defaults[CONF_INITIAL_VALUE] == "unknown"
    assert defaults[CONF_INITIAL_AVAILABILITY] is True
    assert defaults[CONF_PERSISTENT] is True
    assert defaults[CONF_SOURCE_ENTITIES_TEXT] == "device_tracker.phone"
    assert defaults[CONF_PULL_INTERVAL] == 0
    assert defaults["polygon_files_text"] == "/config/zones/home.geojson"
    assert defaults["polygon_strategy"] == "majority"
    assert defaults["polygon_distance_meters"] == 300
    assert defaults["polygon_away_state"] == "not_home"
    assert _entity_schema(defaults)({})[CONF_PLATFORM] == "device_tracker"


def test_stored_entity_ids_keeps_valid_legacy_sources_and_drops_bad_values():
    assert _stored_entity_ids(
        "sensor.first, not-an-entity\nbinary_sensor.second",
    ) == ["sensor.first", "binary_sensor.second"]


def test_build_entity_config_rejects_invalid_template_source_entity_id():
    with pytest.raises(InvalidEntityReference) as err:
        _build_entity_config(
            _entity_input(
                {
                    CONF_TEMPLATE_SOURCES_JSON: '{"power": "not_an_entity"}',
                }
            )
        )

    assert err.value.field_name == CONF_TEMPLATE_SOURCES_JSON


def test_build_entity_config_rejects_invalid_attribute_source_shape():
    with pytest.raises(InvalidJson) as err:
        _build_entity_config(
            _entity_input(
                {
                    CONF_ATTRIBUTE_SOURCES_JSON: '{"battery": {"entity_id": "sensor.remote"}}',
                }
            )
        )

    assert err.value.field_name == CONF_ATTRIBUTE_SOURCES_JSON


def test_build_entity_config_rejects_invalid_attribute_source_entity_id():
    with pytest.raises(InvalidEntityReference) as err:
        _build_entity_config(
            _entity_input(
                {
                    CONF_ATTRIBUTE_SOURCES_JSON: '{"battery": "not_an_entity.battery_level"}',
                }
            )
        )

    assert err.value.field_name == CONF_ATTRIBUTE_SOURCES_JSON


def test_append_ui_entity_keeps_existing_options_immutable():
    original = {
        ATTR_DEVICES: {
            "Existing": [
                {CONF_PLATFORM: "sensor", CONF_NAME: "Existing Sensor"},
            ],
        },
        "other": "kept",
    }
    next_options = _append_ui_entity(
        original,
        "Laundry",
        {CONF_PLATFORM: "sensor", CONF_NAME: "Washer Phase"},
    )

    assert original[ATTR_DEVICES].keys() == {"Existing"}
    assert next_options["other"] == "kept"
    assert next_options[ATTR_DEVICES]["Laundry"][0].pop(ATTR_ENTITY_KEY)
    assert next_options[ATTR_DEVICES]["Laundry"] == [
        {CONF_PLATFORM: "sensor", CONF_NAME: "Washer Phase"},
    ]


def test_append_ui_entity_stores_device_attributes():
    next_options = _append_ui_entity(
        {ATTR_DEVICES: {}},
        "Laundry",
        {CONF_PLATFORM: "sensor", CONF_NAME: "Washer Phase"},
        {
            ATTR_DEVICE_ID: "laundry-1",
            CONF_NAME: "Laundry Device",
            CONF_MANUFACTURER: "Acme",
        },
    )

    assert next_options[ATTR_DEVICE_ATTRIBUTES] == {
        "Laundry": {
            ATTR_DEVICE_ID: "laundry-1",
            CONF_NAME: "Laundry Device",
            CONF_MANUFACTURER: "Acme",
        },
    }


def test_append_ui_entity_reuses_existing_device_with_matching_device_id():
    options = {
        ATTR_DEVICES: {
            "Refrigerator Door": [{CONF_PLATFORM: "binary_sensor"}],
        },
        ATTR_DEVICE_ATTRIBUTES: {
            "Refrigerator Door": {
                ATTR_DEVICE_ID: "refrigerator-door-1",
                CONF_NAME: "Refrigerator Door",
            },
        },
    }

    next_options = _append_ui_entity(
        options,
        "A Different Display Name",
        {CONF_PLATFORM: "sensor", CONF_NAME: "Temperature"},
        {
            ATTR_DEVICE_ID: "refrigerator-door-1",
            CONF_NAME: "A Different Display Name",
        },
    )

    assert list(next_options[ATTR_DEVICES]) == ["Refrigerator Door"]
    assert len(next_options[ATTR_DEVICES]["Refrigerator Door"]) == 2
    assert next_options[ATTR_DEVICE_ATTRIBUTES]["Refrigerator Door"][CONF_NAME] == (
        "Refrigerator Door"
    )


def test_append_ui_entity_accepts_home_assistant_read_only_options():
    original = MappingProxyType(
        {
            ATTR_DEVICES: MappingProxyType(
                {
                    "Existing": [
                        MappingProxyType(
                            {
                                CONF_PLATFORM: "sensor",
                                CONF_NAME: "Existing Sensor",
                            }
                        ),
                    ],
                }
            ),
        }
    )

    next_options = _append_ui_entity(
        original,
        "Laundry",
        MappingProxyType(
            {
                CONF_PLATFORM: "sensor",
                CONF_NAME: "Washer Phase",
            }
        ),
    )

    assert next_options[ATTR_DEVICES]["Laundry"][0].pop(ATTR_ENTITY_KEY)
    assert next_options == {
        ATTR_DEVICES: {
            "Existing": [
                {CONF_PLATFORM: "sensor", CONF_NAME: "Existing Sensor"},
            ],
            "Laundry": [
                {CONF_PLATFORM: "sensor", CONF_NAME: "Washer Phase"},
            ],
        },
    }


def test_append_ui_entity_recovers_from_invalid_devices_options():
    next_options = _append_ui_entity(
        {ATTR_DEVICES: "bad-shape"},
        "Laundry",
        {CONF_PLATFORM: "sensor", CONF_NAME: "Washer Phase"},
    )

    assert next_options[ATTR_DEVICES]["Laundry"][0].pop(ATTR_ENTITY_KEY)
    assert next_options[ATTR_DEVICES] == {
        "Laundry": [
            {CONF_PLATFORM: "sensor", CONF_NAME: "Washer Phase"},
        ],
    }


def test_append_ui_entity_recovers_from_invalid_target_list_and_device_attributes():
    next_options = _append_ui_entity(
        {
            ATTR_DEVICES: {"Laundry": "bad-entity-list"},
            ATTR_DEVICE_ATTRIBUTES: "bad-device-attributes",
        },
        "Laundry",
        {CONF_PLATFORM: "sensor", CONF_NAME: "Washer Phase"},
        {ATTR_DEVICE_ID: "laundry-1", CONF_NAME: "Laundry"},
    )

    assert next_options[ATTR_DEVICES]["Laundry"][0].pop(ATTR_ENTITY_KEY)
    assert next_options[ATTR_DEVICES]["Laundry"] == [
        {CONF_PLATFORM: "sensor", CONF_NAME: "Washer Phase"},
    ]
    assert next_options[ATTR_DEVICE_ATTRIBUTES] == {
        "Laundry": {ATTR_DEVICE_ID: "laundry-1", CONF_NAME: "Laundry"},
    }


def test_entity_choices_ignores_invalid_stored_options():
    assert _entity_choices({ATTR_DEVICES: "bad-shape"}) == {}
    assert _entity_choices(
        {
            ATTR_DEVICES: {
                "Laundry": [
                    "bad-entity",
                    {CONF_PLATFORM: "sensor", CONF_NAME: "Washer Phase"},
                ],
                "Broken": "not-a-list",
            },
        }
    ) == {
        _entity_key("Laundry", 1): "Laundry / Washer Phase (sensor)",
    }


def test_options_schema_allows_deleting_but_not_editing_invalid_stored_entity():
    schema = _options_schema(
        {
            ATTR_DEVICES: {"Broken": ["bad-entity"]},
        }
    )
    action_selector = next(iter(schema.schema.values()))

    assert action_selector.config["translation_key"] == "options_action"
    assert action_selector.config["options"] == [
        "add_entity",
        "delete_entity",
        "manage_devices",
        "delete_device",
        "finish",
    ]


def test_managed_device_choices_show_stable_id_and_entity_count():
    choices = _managed_device_choices(
        {
            ATTR_DEVICES: {
                "Laundry": [
                    {CONF_PLATFORM: "sensor"},
                    {CONF_PLATFORM: "binary_sensor"},
                ],
            },
            ATTR_DEVICE_ATTRIBUTES: {
                "Laundry": {ATTR_DEVICE_ID: "laundry-1"},
            },
        }
    )

    assert choices == {"Laundry": "Laundry (laundry-1, 2 entities)"}


def test_replace_ui_device_renames_group_and_updates_shared_metadata():
    original = {
        ATTR_DEVICES: {
            "Laundry": [
                {CONF_PLATFORM: "sensor", CONF_NAME: "Washer Phase"},
                {CONF_PLATFORM: "binary_sensor", CONF_NAME: "Washer Door"},
            ],
        },
        ATTR_DEVICE_ATTRIBUTES: {
            "Laundry": {ATTR_DEVICE_ID: "laundry-old", CONF_NAME: "Laundry"},
        },
    }

    next_options = _replace_ui_device(
        original,
        "Laundry",
        "Laundry Room",
        {
            ATTR_DEVICE_ID: "laundry-new",
            CONF_NAME: "Laundry Room",
            CONF_MANUFACTURER: "Acme",
        },
    )

    assert "Laundry" not in next_options[ATTR_DEVICES]
    assert len(next_options[ATTR_DEVICES]["Laundry Room"]) == 2
    assert next_options[ATTR_DEVICE_ATTRIBUTES]["Laundry Room"] == {
        ATTR_DEVICE_ID: "laundry-new",
        CONF_NAME: "Laundry Room",
        CONF_MANUFACTURER: "Acme",
    }
    assert original[ATTR_DEVICE_ATTRIBUTES]["Laundry"][ATTR_DEVICE_ID] == "laundry-old"


def test_replace_ui_device_merges_matching_stable_device_id_without_overwrite():
    original = {
        ATTR_DEVICES: {
            "Washer": [{CONF_PLATFORM: "sensor", CONF_NAME: "Phase"}],
            "Laundry": [{CONF_PLATFORM: "binary_sensor", CONF_NAME: "Door"}],
        },
        ATTR_DEVICE_ATTRIBUTES: {
            "Washer": {ATTR_DEVICE_ID: "washer-1", CONF_NAME: "Washer"},
            "Laundry": {
                ATTR_DEVICE_ID: "laundry-1",
                CONF_NAME: "Laundry",
                CONF_MANUFACTURER: "TCL",
            },
        },
    }

    next_options = _replace_ui_device(
        original,
        "Washer",
        "Washer Renamed",
        {ATTR_DEVICE_ID: "laundry-1", CONF_NAME: "Washer Renamed"},
    )

    assert "Washer" not in next_options[ATTR_DEVICES]
    assert [entity[CONF_NAME] for entity in next_options[ATTR_DEVICES]["Laundry"]] == [
        "Door",
        "Phase",
    ]
    assert next_options[ATTR_DEVICE_ATTRIBUTES]["Laundry"][CONF_MANUFACTURER] == "TCL"


def test_replace_ui_device_merges_matching_id_when_device_name_is_unchanged():
    original = {
        ATTR_DEVICES: {
            "Washer": [{CONF_PLATFORM: "sensor", CONF_NAME: "Phase"}],
            "Laundry": [{CONF_PLATFORM: "binary_sensor", CONF_NAME: "Door"}],
        },
        ATTR_DEVICE_ATTRIBUTES: {
            "Washer": {ATTR_DEVICE_ID: "washer-1", CONF_NAME: "Washer"},
            "Laundry": {ATTR_DEVICE_ID: "laundry-1", CONF_NAME: "Laundry"},
        },
    }

    next_options = _replace_ui_device(
        original,
        "Washer",
        "Washer",
        {ATTR_DEVICE_ID: "laundry-1", CONF_NAME: "Washer"},
    )

    assert "Washer" not in next_options[ATTR_DEVICES]
    assert len(next_options[ATTR_DEVICES]["Laundry"]) == 2


def test_replace_ui_entity_updates_existing_entity_without_mutating_options():
    original = {
        ATTR_DEVICES: {
            "Laundry": [
                {CONF_PLATFORM: "sensor", CONF_NAME: "Washer Phase"},
                {CONF_PLATFORM: "binary_sensor", CONF_NAME: "Washer Door"},
            ],
        },
    }

    next_options = _replace_ui_entity(
        original,
        "Laundry",
        0,
        "Laundry",
        {
            CONF_PLATFORM: "sensor",
            CONF_NAME: "Washer Status",
            CONF_INITIAL_VALUE: "running",
        },
    )

    assert original[ATTR_DEVICES]["Laundry"][0][CONF_NAME] == "Washer Phase"
    assert next_options[ATTR_DEVICES]["Laundry"][0].pop(ATTR_ENTITY_KEY)
    assert next_options[ATTR_DEVICES]["Laundry"] == [
        {
            CONF_PLATFORM: "sensor",
            CONF_NAME: "Washer Status",
            CONF_INITIAL_VALUE: "running",
        },
        {CONF_PLATFORM: "binary_sensor", CONF_NAME: "Washer Door"},
    ]


def test_replace_ui_entity_updates_device_attributes():
    original = {
        ATTR_DEVICES: {
            "Laundry": [
                {CONF_PLATFORM: "sensor", CONF_NAME: "Washer Phase"},
            ],
        },
        ATTR_DEVICE_ATTRIBUTES: {
            "Laundry": {
                ATTR_DEVICE_ID: "old-laundry",
                CONF_NAME: "Laundry",
            },
        },
    }

    next_options = _replace_ui_entity(
        original,
        "Laundry",
        0,
        "Laundry",
        {CONF_PLATFORM: "sensor", CONF_NAME: "Washer Status"},
        {
            ATTR_DEVICE_ID: "new-laundry",
            CONF_NAME: "Laundry",
            CONF_MODEL: "Washer 9000",
        },
    )

    assert original[ATTR_DEVICE_ATTRIBUTES]["Laundry"][ATTR_DEVICE_ID] == "old-laundry"
    assert next_options[ATTR_DEVICE_ATTRIBUTES]["Laundry"] == {
        ATTR_DEVICE_ID: "new-laundry",
        CONF_NAME: "Laundry",
        CONF_MODEL: "Washer 9000",
    }


def test_replace_ui_entity_can_move_entity_to_another_device():
    original = {
        ATTR_DEVICES: {
            "Laundry": [
                {CONF_PLATFORM: "sensor", CONF_NAME: "Washer Phase"},
            ],
            "HVAC": [],
        },
    }

    next_options = _replace_ui_entity(
        original,
        "Laundry",
        0,
        "HVAC",
        {CONF_PLATFORM: "climate", CONF_NAME: "Thermostat"},
    )

    assert "Laundry" not in next_options[ATTR_DEVICES]
    assert next_options[ATTR_DEVICES]["HVAC"][0].pop(ATTR_ENTITY_KEY)
    assert next_options[ATTR_DEVICES]["HVAC"] == [
        {CONF_PLATFORM: "climate", CONF_NAME: "Thermostat"},
    ]


def test_replace_ui_entity_preserves_existing_internal_entity_key():
    original = {
        ATTR_DEVICES: {
            "Laundry": [
                {
                    CONF_PLATFORM: "sensor",
                    CONF_NAME: "Washer Phase",
                    ATTR_ENTITY_KEY: "stable-key",
                },
            ],
        },
    }

    next_options = _replace_ui_entity(
        original,
        "Laundry",
        0,
        "Laundry",
        {CONF_PLATFORM: "sensor", CONF_NAME: "Washer Status"},
    )

    assert next_options[ATTR_DEVICES]["Laundry"][0][ATTR_ENTITY_KEY] == "stable-key"


def test_replace_ui_entity_moves_device_attributes():
    original = {
        ATTR_DEVICES: {
            "Laundry": [
                {CONF_PLATFORM: "sensor", CONF_NAME: "Washer Phase"},
            ],
        },
        ATTR_DEVICE_ATTRIBUTES: {
            "Laundry": {
                ATTR_DEVICE_ID: "laundry-1",
                CONF_NAME: "Laundry",
            },
        },
    }

    next_options = _replace_ui_entity(
        original,
        "Laundry",
        0,
        "HVAC",
        {CONF_PLATFORM: "climate", CONF_NAME: "Thermostat"},
        {
            ATTR_DEVICE_ID: "hvac-1",
            CONF_NAME: "HVAC",
            CONF_MODEL: "Thermostat",
        },
    )

    assert "Laundry" not in next_options[ATTR_DEVICE_ATTRIBUTES]
    assert next_options[ATTR_DEVICE_ATTRIBUTES]["HVAC"] == {
        ATTR_DEVICE_ID: "hvac-1",
        CONF_NAME: "HVAC",
        CONF_MODEL: "Thermostat",
    }


def test_replace_ui_entity_recovers_from_invalid_device_attributes_and_target_list():
    next_options = _replace_ui_entity(
        {
            ATTR_DEVICES: {
                "Laundry": [{CONF_PLATFORM: "sensor", CONF_NAME: "Washer"}],
                "HVAC": "bad-target-list",
            },
            ATTR_DEVICE_ATTRIBUTES: "bad-device-attributes",
        },
        "Laundry",
        0,
        "HVAC",
        {CONF_PLATFORM: "climate", CONF_NAME: "Thermostat"},
        {ATTR_DEVICE_ID: "hvac-1", CONF_NAME: "HVAC"},
    )

    assert "Laundry" not in next_options[ATTR_DEVICES]
    assert next_options[ATTR_DEVICES]["HVAC"][0].pop(ATTR_ENTITY_KEY)
    assert next_options[ATTR_DEVICES]["HVAC"] == [
        {CONF_PLATFORM: "climate", CONF_NAME: "Thermostat"},
    ]
    assert next_options[ATTR_DEVICE_ATTRIBUTES] == {
        "HVAC": {ATTR_DEVICE_ID: "hvac-1", CONF_NAME: "HVAC"},
    }


def test_replace_ui_entity_rejects_existing_device_name_with_different_id():
    original = {
        ATTR_DEVICES: {
            "Laundry": [{CONF_PLATFORM: "sensor", CONF_NAME: "Washer"}],
            "HVAC": [{CONF_PLATFORM: "climate", CONF_NAME: "Thermostat"}],
        },
        ATTR_DEVICE_ATTRIBUTES: {
            "Laundry": {ATTR_DEVICE_ID: "laundry-1", CONF_NAME: "Laundry"},
            "HVAC": {ATTR_DEVICE_ID: "hvac-1", CONF_NAME: "HVAC"},
        },
    }

    with pytest.raises(DeviceNameAlreadyUsed):
        _replace_ui_entity(
            original,
            "Laundry",
            0,
            "HVAC",
            {CONF_PLATFORM: "sensor", CONF_NAME: "Washer"},
            {ATTR_DEVICE_ID: "different-id", CONF_NAME: "HVAC"},
        )

    assert original[ATTR_DEVICE_ATTRIBUTES]["HVAC"][ATTR_DEVICE_ID] == "hvac-1"


def test_replace_ui_device_rejects_name_collision_with_different_id():
    original = {
        ATTR_DEVICES: {
            "Laundry": [{CONF_PLATFORM: "sensor", CONF_NAME: "Washer"}],
            "HVAC": [{CONF_PLATFORM: "climate", CONF_NAME: "Thermostat"}],
        },
        ATTR_DEVICE_ATTRIBUTES: {
            "Laundry": {ATTR_DEVICE_ID: "laundry-1", CONF_NAME: "Laundry"},
            "HVAC": {ATTR_DEVICE_ID: "hvac-1", CONF_NAME: "HVAC"},
        },
    }

    with pytest.raises(DeviceNameAlreadyUsed):
        _replace_ui_device(
            original,
            "Laundry",
            "HVAC",
            {ATTR_DEVICE_ID: "different-id", CONF_NAME: "HVAC"},
        )


def test_delete_ui_entities_removes_multiple_entities_and_empty_device_metadata():
    original = {
        ATTR_DEVICES: {
            "Laundry": [
                {CONF_PLATFORM: "sensor", CONF_NAME: "Washer Phase"},
                {CONF_PLATFORM: "binary_sensor", CONF_NAME: "Washer Door"},
            ],
            "HVAC": [
                {CONF_PLATFORM: "climate", CONF_NAME: "Thermostat"},
            ],
        },
        ATTR_DEVICE_ATTRIBUTES: {
            "Laundry": {
                ATTR_DEVICE_ID: "laundry-1",
                CONF_NAME: "Laundry",
            },
            "HVAC": {
                ATTR_DEVICE_ID: "hvac-1",
                CONF_NAME: "HVAC",
            },
        },
    }

    next_options = _delete_ui_entities(
        original,
        [
            _entity_key("Laundry", 1),
            _entity_key("HVAC", 0),
        ],
    )

    assert original[ATTR_DEVICES]["Laundry"][1][CONF_NAME] == "Washer Door"
    assert next_options[ATTR_DEVICES] == {
        "Laundry": [
            {CONF_PLATFORM: "sensor", CONF_NAME: "Washer Phase"},
        ],
    }
    assert next_options[ATTR_DEVICE_ATTRIBUTES] == {
        "Laundry": {
            ATTR_DEVICE_ID: "laundry-1",
            CONF_NAME: "Laundry",
        },
    }


def test_delete_ui_entities_can_remove_malformed_entity_and_metadata():
    options = {
        ATTR_DEVICES: {"Broken": ["bad-entity"]},
        ATTR_DEVICE_ATTRIBUTES: "bad-device-attributes",
    }

    next_options = _delete_ui_entities(
        options,
        [_entity_key("Broken", 0)],
    )

    assert next_options[ATTR_DEVICES] == {}
    assert next_options[ATTR_DEVICE_ATTRIBUTES] == {}


def test_delete_ui_device_removes_entities_metadata_and_malformed_groups():
    options = {
        ATTR_DEVICES: {
            "Laundry": [{CONF_PLATFORM: "sensor", CONF_NAME: "Washer"}],
            "Broken": "not-an-entity-list",
        },
        ATTR_DEVICE_ATTRIBUTES: {
            "Laundry": {ATTR_DEVICE_ID: "laundry-1"},
            "Broken": {ATTR_DEVICE_ID: "broken-1"},
        },
    }

    without_broken = _delete_ui_device(options, "Broken")
    without_laundry = _delete_ui_device(without_broken, "Laundry")

    assert without_broken[ATTR_DEVICES] == {
        "Laundry": [{CONF_PLATFORM: "sensor", CONF_NAME: "Washer"}],
    }
    assert without_broken[ATTR_DEVICE_ATTRIBUTES] == {
        "Laundry": {ATTR_DEVICE_ID: "laundry-1"},
    }
    assert without_laundry[ATTR_DEVICES] == {}
    assert without_laundry[ATTR_DEVICE_ATTRIBUTES] == {}


def test_entity_form_defaults_round_trips_stored_entity_config():
    defaults = _entity_form_defaults(
        "Laundry",
        {
            CONF_PLATFORM: "sensor",
            CONF_NAME: "Washer Phase",
            CONF_ICON: "mdi:washing-machine",
            CONF_ICON_TEMPLATE: "{{ 'mdi:washing-machine-alert' if power == 'on' else 'mdi:washing-machine' }}",
            ATTR_ENTITY_ID: "sensor.washer_phase",
            CONF_INITIAL_VALUE: "idle",
            CONF_INITIAL_AVAILABILITY: False,
            CONF_PERSISTENT: False,
            CONF_SOURCE_ENTITIES: ["sensor.washer_power"],
            CONF_TEMPLATE_SOURCES: {
                "power": {
                    ATTR_ENTITY_ID: "sensor.washer_power",
                    CONF_ATTRIBUTE: "state",
                },
            },
            CONF_PULL_INTERVAL: 30,
            CONF_VALUE_TEMPLATE: "{{ power }}",
            CONF_EVENT_HOOKS: [
                {
                    "trigger": "event",
                    "event_type": "virtual_layer_manual_update",
                    CONF_VALUE_TEMPLATE: "{{ trigger.data.value }}",
                }
            ],
            CONF_ATTRIBUTES: {"source": "simulation"},
        },
        {
            ATTR_DEVICE_ATTRIBUTES: {
                "Laundry": {
                    ATTR_DEVICE_ID: "laundry-1",
                    CONF_MANUFACTURER: "Acme",
                    CONF_MODEL: "Washer 9000",
                    CONF_SW_VERSION: "2026.8",
                    CONF_HW_VERSION: "rev-a",
                    CONF_SERIAL_NUMBER: "SN-123",
                    CONF_CONFIGURATION_URL: "https://example.test/laundry",
                    CONF_SUGGESTED_AREA: "Laundry Room",
                    CONF_VIA_DEVICE_ID: "parent-device-id",
                },
            },
        },
    )

    assert defaults[CONF_DEVICE_NAME] == "Laundry"
    assert defaults[CONF_DEVICE_ID] == "laundry-1"
    assert defaults[CONF_DEVICE_MANUFACTURER] == "Acme"
    assert defaults[CONF_DEVICE_MODEL] == "Washer 9000"
    assert defaults[CONF_DEVICE_SW_VERSION] == "2026.8"
    assert defaults[CONF_DEVICE_HW_VERSION] == "rev-a"
    assert defaults[CONF_DEVICE_SERIAL_NUMBER] == "SN-123"
    assert defaults[CONF_DEVICE_CONFIGURATION_URL] == "https://example.test/laundry"
    assert defaults[CONF_DEVICE_SUGGESTED_AREA] == "Laundry Room"
    assert defaults[CONF_DEVICE_VIA_DEVICE_ID] == "parent-device-id"
    assert defaults[CONF_ENTITY_NAME] == "Washer Phase"
    assert defaults[CONF_ICON] == "mdi:washing-machine"
    assert defaults[CONF_ICON_TEMPLATE] == (
        "{{ 'mdi:washing-machine-alert' if power == 'on' else 'mdi:washing-machine' }}"
    )
    assert defaults[ATTR_ENTITY_ID] == "sensor.washer_phase"
    assert defaults[CONF_INITIAL_AVAILABILITY] is False
    assert defaults[CONF_PERSISTENT] is False
    assert defaults[CONF_SOURCE_ENTITIES_TEXT] == "sensor.washer_power"
    assert '"power"' in defaults[CONF_TEMPLATE_SOURCES_JSON]
    assert defaults[CONF_PULL_INTERVAL] == 30
    assert defaults[CONF_VALUE_TEMPLATE] == "{{ power }}"
    assert json.loads(defaults[CONF_EVENT_HOOKS_JSON]) == [
        {
            "trigger": "event",
            "event_type": "virtual_layer_manual_update",
            CONF_VALUE_TEMPLATE: "{{ trigger.data.value }}",
        }
    ]
    assert defaults[CONF_ATTRIBUTES_JSON] == '{"source": "simulation"}'


def test_entity_key_is_json_selection_key():
    assert _entity_key("Laundry", 2) == '["Laundry",2]'


def test_entity_choices_use_stable_entity_key_when_available():
    options = {
        ATTR_DEVICES: {
            "Laundry": [
                {
                    CONF_PLATFORM: "sensor",
                    CONF_NAME: "Washer Phase",
                    ATTR_ENTITY_KEY: "stable-key",
                },
            ],
        },
    }

    selection_key = _entity_key_from_stable_key("stable-key")

    assert _entity_choices(options) == {
        selection_key: "Laundry / Washer Phase (sensor)",
    }
    assert _find_entity_by_selection_key(options, selection_key) == ("Laundry", 0)


def test_entity_choices_keep_malformed_and_duplicate_stable_keys_removable():
    options = {
        ATTR_DEVICES: {
            "Laundry": [
                {
                    CONF_PLATFORM: "sensor",
                    CONF_NAME: "Malformed Key",
                    ATTR_ENTITY_KEY: ["not", "a", "string"],
                },
                {
                    CONF_PLATFORM: "sensor",
                    CONF_NAME: "Duplicate One",
                    ATTR_ENTITY_KEY: "duplicate-key",
                },
                {
                    CONF_PLATFORM: "sensor",
                    CONF_NAME: "Duplicate Two",
                    ATTR_ENTITY_KEY: "duplicate-key",
                },
            ],
        },
    }

    choices = _entity_choices(options, include_invalid=True)

    assert choices == {
        _entity_key("Laundry", 0): "Laundry / Malformed Key (sensor)",
        _entity_key("Laundry", 1): "Laundry / Duplicate One (sensor)",
        _entity_key("Laundry", 2): "Laundry / Duplicate Two (sensor)",
    }
    next_options = _delete_ui_entities(
        options,
        [_entity_key("Laundry", 2)],
    )
    assert [
        entity[CONF_NAME]
        for entity in next_options[ATTR_DEVICES]["Laundry"]
    ] == ["Malformed Key", "Duplicate One"]


def test_stable_selection_rejects_ambiguous_keys_and_boolean_indexes():
    options = {
        ATTR_DEVICES: {
            "Laundry": [
                {CONF_NAME: "One", ATTR_ENTITY_KEY: "duplicate-key"},
                {CONF_NAME: "Two", ATTR_ENTITY_KEY: "duplicate-key"},
            ],
        },
    }

    with pytest.raises(InvalidEntitySelection):
        _find_entity_by_selection_key(
            options,
            _entity_key_from_stable_key("duplicate-key"),
        )
    with pytest.raises(InvalidEntitySelection):
        _parse_entity_key('["Laundry",true]')


def test_delete_ui_entities_uses_stable_key_after_entity_order_changes():
    options = {
        ATTR_DEVICES: {
            "Laundry": [
                {
                    CONF_PLATFORM: "sensor",
                    CONF_NAME: "First",
                    ATTR_ENTITY_KEY: "first-key",
                },
                {
                    CONF_PLATFORM: "sensor",
                    CONF_NAME: "Second",
                    ATTR_ENTITY_KEY: "second-key",
                },
            ],
        },
    }

    options[ATTR_DEVICES]["Laundry"].reverse()
    next_options = _delete_ui_entities(
        options,
        [_entity_key_from_stable_key("second-key")],
    )

    assert next_options[ATTR_DEVICES]["Laundry"] == [
        {
            CONF_PLATFORM: "sensor",
            CONF_NAME: "First",
            ATTR_ENTITY_KEY: "first-key",
        },
    ]


def test_native_templates_and_command_actions_parse_complex_values():
    native_templates = _parse_native_templates(
        json.dumps(
            {
                "fan_modes": "{{ ['auto', 'turbo'] }}",
                "target_temperature": "{{ states('sensor.target') | float }}",
            }
        )
    )
    command_actions = _parse_command_actions(
        json.dumps(
            {
                "set_temperature": {
                    "optimistic": False,
                    "sequence": [
                        {
                            "action": "climate.set_temperature",
                            "target": {"entity_id": "climate.real"},
                            "data": {"temperature": "{{ temperature }}"},
                        }
                    ],
                }
            }
        )
    )

    assert native_templates["fan_modes"] == "{{ ['auto', 'turbo'] }}"
    assert command_actions["set_temperature"]["optimistic"] is False


@pytest.mark.parametrize(
    ("parser", "payload"),
    [
        (_parse_native_templates, '{"entity_id": "{{ states(\'sensor.bad\') }}"}'),
        (_parse_native_templates, '{"fan_modes": ["auto"]}'),
        (_parse_native_templates, '{"mode": "{{ 1 }}", " mode ": "{{ 2 }}"}'),
        (_parse_command_actions, '{"set-temperature": []}'),
        (_parse_command_actions, '{"turn_on": {"sequence": [], "optimistic": true}}'),
        (_parse_command_actions, '{"turn_on": {"sequence": [{"action": 1}]}}'),
    ],
)
def test_native_template_and_command_action_bad_input_is_rejected(parser, payload):
    with pytest.raises(InvalidJson):
        parser(payload)


@pytest.mark.parametrize("parser", [_parse_json_object, _parse_json_value])
def test_json_fields_reject_excessive_nesting(parser):
    payload = "[" * 1100 + "0" + "]" * 1100

    with pytest.raises(InvalidJson):
        parser(payload, "deep_json")


def test_command_actions_reject_commands_not_implemented_by_selected_domain():
    with pytest.raises(InvalidJson):
        _parse_command_actions(
            '{"set_percentage": [{"action": "fan.turn_on"}]}',
            "climate",
        )

    assert _parse_command_actions(
        '{"set_fan_mode": [{"action": "climate.set_fan_mode"}]}',
        "climate",
    )


def test_entity_form_round_trips_native_templates_and_command_actions():
    entity = {
        CONF_PLATFORM: "climate",
        CONF_NAME: "Linked HVAC",
        CONF_INITIAL_VALUE: "off",
        CONF_NATIVE_TEMPLATES: {
            "fan_modes": "{{ state_attr('climate.real', 'fan_modes') }}",
        },
        CONF_COMMAND_ACTIONS: {
            "set_fan_mode": [
                {
                    "action": "climate.set_fan_mode",
                    "target": {"entity_id": "climate.real"},
                    "data": {"fan_mode": "{{ fan_mode }}"},
                }
            ],
        },
    }

    defaults = _entity_form_defaults("HVAC", entity)
    built = _build_entity_config(
        _entity_input(
            {
                CONF_PLATFORM: "climate",
                CONF_ENTITY_NAME: "Linked HVAC",
                CONF_INITIAL_VALUE: "off",
                CONF_NATIVE_TEMPLATES_JSON: defaults[CONF_NATIVE_TEMPLATES_JSON],
                CONF_NATIVE_VALUE_TEMPLATES: defaults[
                    CONF_NATIVE_VALUE_TEMPLATES
                ],
                CONF_COMMAND_ACTIONS_JSON: defaults[CONF_COMMAND_ACTIONS_JSON],
            }
        )
    )[1]

    assert built[CONF_NATIVE_TEMPLATES] == entity[CONF_NATIVE_TEMPLATES]
    assert built[CONF_COMMAND_ACTIONS] == entity[CONF_COMMAND_ACTIONS]


def test_clearing_dedicated_native_template_removes_only_that_property():
    built = _build_entity_config(
        _entity_input(
            {
                CONF_PLATFORM: "climate",
                CONF_INITIAL_VALUE: "off",
                CONF_NATIVE_TEMPLATES_JSON: json.dumps(
                    {
                        "fan_mode": "{{ 'stale' }}",
                        "vendor_property": "{{ 'kept' }}",
                    }
                ),
                CONF_NATIVE_VALUE_TEMPLATES: {
                    "fan_mode": "",
                    "target_temperature": "{{ 23 }}",
                },
            }
        )
    )[1]

    assert built[CONF_NATIVE_TEMPLATES] == {
        "target_temperature": "{{ 23 }}",
        "vendor_property": "{{ 'kept' }}",
    }


def test_domain_form_promotes_managed_templates_from_json_defaults():
    form_data = _entity_schema(
        {
            CONF_PLATFORM: "climate",
            CONF_NATIVE_TEMPLATES_JSON: json.dumps(
                {
                    "fan_mode": "{{ 'auto' }}",
                    "vendor_property": "{{ 'kept' }}",
                }
            ),
        }
    )({})

    templates = form_data[CONF_NATIVE_VALUE_TEMPLATES]
    assert set(templates) == set(CLIMATE_NATIVE_TEMPLATE_PROPERTIES)
    assert templates["fan_mode"] == "{{ 'auto' }}"
    assert all(templates.values())
    assert CONF_NATIVE_TEMPLATES_JSON not in form_data


def test_unstructured_domain_keeps_advanced_native_template_json_input():
    schema = _entity_schema({CONF_PLATFORM: "scene"})
    validators = {
        marker.schema: validator for marker, validator in schema.schema.items()
    }

    advanced = validators[CONF_ADVANCED_SETTINGS]
    advanced_fields = {
        marker.schema for marker in advanced.schema.schema
    }
    assert CONF_NATIVE_TEMPLATES_JSON in advanced_fields


def test_vacuum_domain_selection_reopens_with_dedicated_jinja_fields():
    first_submission = _entity_input({CONF_PLATFORM: "vacuum"})

    assert _needs_domain_specific_form(first_submission)

    vacuum_defaults = _entity_schema(first_submission)({})
    assert set(vacuum_defaults[CONF_NATIVE_VALUE_TEMPLATES]) == {
        "activity",
        "battery_level",
        "fan_speed_list",
        "fan_speed",
        "supported_features",
    }
    assert not _needs_domain_specific_form(vacuum_defaults)
