"""Unit tests for Virtual Layer config flow helpers."""

import json
from datetime import timedelta
from types import MappingProxyType, SimpleNamespace

import pytest
from homeassistant.const import ATTR_ENTITY_ID, CONF_ICON, CONF_NAME, CONF_PLATFORM
from homeassistant.helpers.template import Template
from homeassistant.util import dt as dt_util

from custom_components.virtual_layer import (
    _merge_device_sets as _service_merge_device_sets,
)
from custom_components.virtual_layer.config_flow import (
    CONF_ATTRIBUTE_SOURCES_JSON,
    CONF_ATTRIBUTE_TEMPLATES_JSON,
    CONF_ATTRIBUTES_JSON,
    CONF_DEVICE_HW_VERSION,
    CONF_DEVICE_ID,
    CONF_DEVICE_MANUFACTURER,
    CONF_DEVICE_MODEL,
    CONF_DEVICE_NAME,
    CONF_DEVICE_SERIAL_NUMBER,
    CONF_DEVICE_SW_VERSION,
    CONF_DOMAIN_OPTIONS_JSON,
    CONF_ENTITY_NAME,
    CONF_SOURCE_ENTITIES_TEXT,
    CONF_TEMPLATE_SOURCES_JSON,
    InvalidDomainOptions,
    InvalidEntityId,
    InvalidEntityReference,
    InvalidJson,
    _append_ui_entity,
    _auto_helper_profile,
    _build_device_config,
    _build_entity_config,
    _delete_ui_entities,
    _entity_choices,
    _entity_form_defaults,
    _entity_key,
    _entity_key_from_stable_key,
    _managed_device_choices,
    _entity_schema,
    _find_backup_group_for_entry,
    _find_entity_by_selection_key,
    _merge_device_attributes,
    _merge_device_sets,
    _options_schema,
    _reference_edit_defaults,
    _reference_entity_defaults,
    _replace_ui_entity,
    _replace_ui_device,
    _set_auto_helper_profile,
)
from custom_components.virtual_layer.const import (
    ATTR_DEVICE_ATTRIBUTES,
    ATTR_DEVICE_ID,
    ATTR_DEVICES,
    ATTR_ENTITY_KEY,
    ATTR_GROUP_NAME,
    CONF_ATTRIBUTE,
    CONF_ATTRIBUTE_SOURCES,
    CONF_ATTRIBUTE_TEMPLATES,
    CONF_ATTRIBUTES,
    CONF_AVAILABILITY_TEMPLATE,
    CONF_CLASS,
    CONF_HW_VERSION,
    CONF_INITIAL_AVAILABILITY,
    CONF_INITIAL_VALUE,
    CONF_LOCATION_HELPER,
    CONF_MANUFACTURER,
    CONF_MAX,
    CONF_MIN,
    CONF_MODEL,
    CONF_PERSISTENT,
    CONF_PULL_INTERVAL,
    CONF_SERIAL_NUMBER,
    CONF_SOURCE_ENTITIES,
    CONF_SW_VERSION,
    CONF_TEMPLATE_SOURCES,
    CONF_VALUE_TEMPLATE,
    VIRTUAL_ENTITY_DOMAINS,
)

pytestmark = pytest.mark.unit


def _entity_input(overrides=None):
    data = {
        CONF_DEVICE_NAME: "Laundry",
        CONF_DEVICE_ID: "",
        CONF_DEVICE_MANUFACTURER: "",
        CONF_DEVICE_MODEL: "",
        CONF_DEVICE_SW_VERSION: "",
        CONF_DEVICE_HW_VERSION: "",
        CONF_DEVICE_SERIAL_NUMBER: "",
        CONF_ENTITY_NAME: "Washer Phase",
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
        CONF_ATTRIBUTES_JSON: "",
        CONF_ATTRIBUTE_SOURCES_JSON: "",
        CONF_ATTRIBUTE_TEMPLATES_JSON: "",
        CONF_DOMAIN_OPTIONS_JSON: "",
    }
    data.update(overrides or {})
    return data


def test_entity_form_defaults_to_the_selected_domain_prefix():
    defaults = _entity_schema({
        CONF_PLATFORM: "sensor",
        CONF_ENTITY_NAME: "Washer Phase",
    })({})

    assert defaults[ATTR_ENTITY_ID] == "sensor.washer_phase"


def test_entity_form_preserves_an_existing_entity_id():
    defaults = _entity_schema({
        CONF_PLATFORM: "sensor",
        CONF_ENTITY_NAME: "Washer Phase",
        ATTR_ENTITY_ID: "sensor.custom_washer_phase",
    })({})

    assert defaults[ATTR_ENTITY_ID] == "sensor.custom_washer_phase"


def test_build_entity_config_supports_composite_templates_and_attributes():
    device_name, entity = _build_entity_config(_entity_input({
        ATTR_ENTITY_ID: "sensor.washer_phase",
        CONF_SOURCE_ENTITIES_TEXT: "sensor.washer_power, binary_sensor.washer_door",
        CONF_VALUE_TEMPLATE: "{{ states('sensor.washer_power') }}",
        CONF_AVAILABILITY_TEMPLATE: "{{ is_state('sensor.washer_power', 'on') }}",
        CONF_ATTRIBUTES_JSON: '{"source": "simulation"}',
        CONF_ATTRIBUTE_TEMPLATES_JSON: '{"door": "{{ states(\\\"binary_sensor.washer_door\\\") }}"}',
    }))

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
            "door": "{{ states(\"binary_sensor.washer_door\") }}",
        },
    }


def test_build_entity_config_deduplicates_sources_and_rejects_invalid_template_variables():
    _, entity = _build_entity_config(_entity_input({
        CONF_SOURCE_ENTITIES_TEXT: "sensor.power, sensor.power\nsensor.door",
        CONF_TEMPLATE_SOURCES_JSON: '{"power": "sensor.power"}',
    }))

    assert entity[CONF_SOURCE_ENTITIES] == ["sensor.power", "sensor.door"]

    with pytest.raises(InvalidJson) as err:
        _build_entity_config(_entity_input({
            CONF_TEMPLATE_SOURCES_JSON: '{"not-valid": "sensor.power"}',
        }))

    assert err.value.field_name == CONF_TEMPLATE_SOURCES_JSON

    with pytest.raises(InvalidJson) as err:
        _build_entity_config(_entity_input({
            CONF_ATTRIBUTES_JSON: '{"available": false}',
        }))

    assert err.value.field_name == CONF_ATTRIBUTES_JSON

    with pytest.raises(InvalidJson) as err:
        _build_entity_config(_entity_input({
            CONF_TEMPLATE_SOURCES_JSON: '{"none": "sensor.power"}',
        }))

    assert err.value.field_name == CONF_TEMPLATE_SOURCES_JSON


def test_reference_entity_defaults_avoids_jinja_reserved_source_variable_names(hass):
    hass.states.async_set("sensor.none", "first")
    hass.states.async_set("sensor.true", "second")

    defaults = _reference_entity_defaults(hass, ["sensor.none", "sensor.true"])

    assert json.loads(defaults[CONF_TEMPLATE_SOURCES_JSON]) == {
        "source_none": "sensor.none",
        "source_true": "sensor.true",
    }
    assert defaults[CONF_VALUE_TEMPLATE] == "{{ source_none ~ source_true }}"


def test_build_entity_config_preserves_domain_options_and_normalizes_climate_default():
    _, entity = _build_entity_config(_entity_input({
        CONF_PLATFORM: "climate",
        CONF_INITIAL_VALUE: "unknown",
        CONF_DOMAIN_OPTIONS_JSON: '{"hvac_modes": ["off", "heat"], "target_temperature": 21}',
    }))

    assert entity[CONF_INITIAL_VALUE] == "off"
    assert entity["hvac_modes"] == ["off", "heat"]
    assert entity["target_temperature"] == 21


def test_build_entity_config_validates_location_helper_options():
    _, entity = _build_entity_config(_entity_input({
        CONF_PLATFORM: "device_tracker",
        CONF_INITIAL_VALUE: "not_home",
        CONF_SOURCE_ENTITIES_TEXT: "device_tracker.first, person.second",
        CONF_DOMAIN_OPTIONS_JSON: (
            '{"location_helper": {'
            '"distance_threshold_meters": 300, '
            '"priority_window_seconds": 1800}}'
        ),
    }))

    assert entity[CONF_LOCATION_HELPER]["distance_threshold_meters"] == 300

    with pytest.raises(InvalidDomainOptions):
        _build_entity_config(_entity_input({
            CONF_PLATFORM: "device_tracker",
            CONF_DOMAIN_OPTIONS_JSON: '{"location_helper": {"distance_threshold_meters": 0}}',
        }))


def test_build_entity_config_rejects_reserved_domain_options():
    with pytest.raises(InvalidJson) as err:
        _build_entity_config(_entity_input({
            CONF_DOMAIN_OPTIONS_JSON: '{"name": "must not override"}',
        }))

    assert err.value.field_name == CONF_DOMAIN_OPTIONS_JSON

    with pytest.raises(InvalidJson) as err:
        _build_entity_config(_entity_input({
            CONF_DOMAIN_OPTIONS_JSON: '{"friendly_name": "must not override"}',
        }))

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
    assert defaults[CONF_SOURCE_ENTITIES_TEXT] == "binary_sensor.front_door\nswitch.alarm_ready"
    assert defaults[CONF_TEMPLATE_SOURCES_JSON] == (
        '{"alarm_ready": "switch.alarm_ready", "front_door": "binary_sensor.front_door"}'
    )
    assert " and " in defaults[CONF_VALUE_TEMPLATE]
    assert "front_door | lower" in defaults[CONF_VALUE_TEMPLATE]
    assert "alarm_ready | lower" in defaults[CONF_VALUE_TEMPLATE]


def test_presence_motion_helper_uses_majority_and_delayed_all_off_clear(hass, monkeypatch):
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
    hass.states.async_set("light.invalid_motion_source", "on", {"device_class": "motion"})
    hass.states.async_set("switch.invalid_motion_source", "on", {"device_class": "motion"})

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
    assert "reject('in'" in defaults[CONF_VALUE_TEMPLATE]
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

    defaults = _reference_entity_defaults(hass, [
        "sensor.water_meter_one",
        "sensor.water_meter_two",
    ])

    assert json.loads(defaults[CONF_DOMAIN_OPTIONS_JSON]) == {
        "class": "water",
        "unit_of_measurement": "L",
    }


def test_reference_entity_defaults_combines_string_sources_with_concat_template(hass):
    hass.states.async_set("sensor.washer_phase", "wash")
    hass.states.async_set("sensor.washer_mode", "eco")

    defaults = _reference_entity_defaults(
        hass,
        ["sensor.washer_phase", "sensor.washer_mode"],
    )

    assert defaults[CONF_PLATFORM] == "sensor"
    assert defaults[CONF_INITIAL_VALUE] == "washeco"
    assert defaults[CONF_VALUE_TEMPLATE] == "{{ washer_phase ~ washer_mode }}"
    assert defaults[CONF_ATTRIBUTE_TEMPLATES_JSON] == (
        '{"washer_mode": "{{ washer_mode }}", "washer_phase": "{{ washer_phase }}"}'
    )


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
    hass.states.async_set("datetime.first_seen", "2026-08-03T10:00:00+09:00")
    hass.states.async_set("datetime.last_seen", "2026-08-04T11:00:00+09:00")

    defaults = _reference_entity_defaults(
        hass,
        ["datetime.first_seen", "datetime.last_seen"],
    )

    assert defaults[CONF_PLATFORM] == "datetime"
    assert defaults[CONF_INITIAL_VALUE] == "2026-08-04T11:00:00+09:00"
    assert "sort | last" in defaults[CONF_VALUE_TEMPLATE]


def test_reference_entity_defaults_combines_enum_sources_with_first_available_template(hass):
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


def test_build_device_config_supports_device_registry_metadata():
    device = _build_device_config(_entity_input({
        CONF_DEVICE_ID: "laundry-appliance-1",
        CONF_DEVICE_MANUFACTURER: "Acme",
        CONF_DEVICE_MODEL: "Washer 9000",
        CONF_DEVICE_SW_VERSION: "2026.8",
        CONF_DEVICE_HW_VERSION: "rev-a",
        CONF_DEVICE_SERIAL_NUMBER: "SN-123",
    }), "Laundry")

    assert device == {
        ATTR_DEVICE_ID: "laundry-appliance-1",
        CONF_NAME: "Laundry",
        CONF_MANUFACTURER: "Acme",
        CONF_MODEL: "Washer 9000",
        CONF_SW_VERSION: "2026.8",
        CONF_HW_VERSION: "rev-a",
        CONF_SERIAL_NUMBER: "SN-123",
    }


def test_build_device_config_defaults_device_id_to_device_name():
    assert _build_device_config(_entity_input(), "Laundry") == {
        ATTR_DEVICE_ID: "Laundry",
        CONF_NAME: "Laundry",
    }


def test_build_entity_config_requires_entity_id_domain_to_match_platform():
    with pytest.raises(InvalidEntityId):
        _build_entity_config(_entity_input({
            ATTR_ENTITY_ID: "switch.washer_phase",
            CONF_PLATFORM: "sensor",
        }))


def test_build_entity_config_rejects_non_object_json_fields():
    with pytest.raises(InvalidJson) as err:
        _build_entity_config(_entity_input({
            CONF_ATTRIBUTES_JSON: '["not", "object"]',
        }))

    assert err.value.field_name == CONF_ATTRIBUTES_JSON


def test_build_entity_config_adds_number_defaults():
    _, entity = _build_entity_config(_entity_input({
        CONF_PLATFORM: "number",
        CONF_INITIAL_VALUE: "10",
    }))

    assert entity[CONF_MIN] == 0
    assert entity[CONF_MAX] == 100


def test_build_entity_config_supports_attribute_sources_and_pull_interval():
    _, entity = _build_entity_config(_entity_input({
        CONF_PULL_INTERVAL: 30,
        CONF_ATTRIBUTE_SOURCES_JSON: (
            '{"battery": "sensor.remote.battery_level", '
            '"phase": {"entity_id": "sensor.washer", "attribute": "state"}}'
        ),
    }))

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
    _, entity = _build_entity_config(_entity_input({
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
    }))

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
    _, entity = _build_entity_config(_entity_input({
        CONF_PLATFORM: "camera",
        CONF_INITIAL_VALUE: "off",
        CONF_DOMAIN_OPTIONS_JSON: '{"source_entity": "camera.front_door"}',
    }))

    assert entity["source_entity"] == "camera.front_door"
    assert entity[CONF_SOURCE_ENTITIES] == ["camera.front_door"]
    assert entity[CONF_VALUE_TEMPLATE] == "{{ states('camera.front_door') }}"


def test_build_entity_config_accepts_common_icon_for_native_domains():
    _, entity = _build_entity_config(_entity_input({
        CONF_PLATFORM: "binary_sensor",
        CONF_ICON: "mdi:door-open",
    }))

    assert entity[CONF_ICON] == "mdi:door-open"


def test_build_camera_alias_rejects_non_camera_source():
    with pytest.raises(InvalidDomainOptions):
        _build_entity_config(_entity_input({
            CONF_PLATFORM: "camera",
            CONF_DOMAIN_OPTIONS_JSON: '{"source_entity": "sensor.front_door"}',
        }))


def test_build_entity_config_rejects_explicit_self_references():
    with pytest.raises(InvalidEntityReference) as err:
        _build_entity_config(_entity_input({
            ATTR_ENTITY_ID: "sensor.self_referencing",
            CONF_SOURCE_ENTITIES_TEXT: "sensor.self_referencing",
        }))

    assert err.value.field_name == CONF_SOURCE_ENTITIES_TEXT


def test_build_camera_alias_rejects_itself_as_source():
    with pytest.raises(InvalidEntityReference) as err:
        _build_entity_config(_entity_input({
            ATTR_ENTITY_ID: "camera.self_alias",
            CONF_PLATFORM: "camera",
            CONF_DOMAIN_OPTIONS_JSON: '{"source_entity": "camera.self_alias"}',
        }))

    assert err.value.field_name == CONF_DOMAIN_OPTIONS_JSON


def test_build_generic_entity_keeps_direct_domain_options():
    _, entity = _build_entity_config(_entity_input({
        CONF_PLATFORM: "weather",
        CONF_DOMAIN_OPTIONS_JSON: (
            '{"temperature": 21.5, "humidity": 48, '
            '"forecast_provider": "virtual"}'
        ),
    }))

    assert entity["temperature"] == 21.5
    assert entity["humidity"] == 48
    assert entity["forecast_provider"] == "virtual"


def test_entity_form_preserves_generic_direct_domain_options_for_editing():
    defaults = _entity_form_defaults("Weather", {
        CONF_PLATFORM: "weather",
        CONF_NAME: "Virtual Forecast",
        "temperature": 21.5,
        "humidity": 48,
    })

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
    assert refreshed[CONF_DOMAIN_OPTIONS_JSON] == ""


def test_custom_helper_fields_are_preserved_and_marked_manual():
    current = {
        CONF_PLATFORM: "sensor",
        CONF_SOURCE_ENTITIES_TEXT: "sensor.old",
        CONF_TEMPLATE_SOURCES_JSON: '{"old": "sensor.old"}',
        CONF_VALUE_TEMPLATE: "{{ old | float(0) * 2 }}",
        CONF_ATTRIBUTES_JSON: "",
        CONF_ATTRIBUTE_SOURCES_JSON: "",
        CONF_ATTRIBUTE_TEMPLATES_JSON: "",
        CONF_DOMAIN_OPTIONS_JSON: "",
        CONF_INITIAL_VALUE: "10",
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

    assert _reference_edit_defaults(current, reference, auto_helper=False) == current
    _set_auto_helper_profile(entity, current, reference, _auto_helper_profile(current))

    assert entity["auto_helper"] is False


RICH_DOMAIN_OPTIONS = {
    "binary_sensor": {"class": "door"},
    "camera": {"is_streaming": True},
    "climate": {"target_temperature": 21},
    "cover": {"open_close_duration": 5},
    "device_tracker": {"location_helper": {"distance_threshold_meters": 300}},
    "fan": {"speed_count": 3},
    "humidifier": {"target_humidity": 50},
    "light": {"support_brightness": True},
    "lock": {"support_open": True},
    "number": {"min": 1, "max": 10},
    "sensor": {"unit_of_measurement": "C"},
    "switch": {"class": "outlet"},
    "valve": {"open_close_duration": 5},
}


@pytest.mark.parametrize("domain", VIRTUAL_ENTITY_DOMAINS)
def test_every_supported_domain_accepts_direct_ui_options(domain):
    options = RICH_DOMAIN_OPTIONS.get(domain, {"yaml_only_option": True})
    overrides = {
        CONF_PLATFORM: domain,
        CONF_DOMAIN_OPTIONS_JSON: json.dumps(options),
    }
    if domain == "climate":
        overrides[CONF_INITIAL_VALUE] = "off"

    _, entity = _build_entity_config(_entity_input(overrides))

    for key, value in options.items():
        assert entity[key] == value


def test_build_entity_config_rejects_invalid_template_source_entity_id():
    with pytest.raises(InvalidEntityReference) as err:
        _build_entity_config(_entity_input({
            CONF_TEMPLATE_SOURCES_JSON: '{"power": "not_an_entity"}',
        }))

    assert err.value.field_name == CONF_TEMPLATE_SOURCES_JSON


def test_build_entity_config_rejects_invalid_attribute_source_shape():
    with pytest.raises(InvalidJson) as err:
        _build_entity_config(_entity_input({
            CONF_ATTRIBUTE_SOURCES_JSON: '{"battery": {"entity_id": "sensor.remote"}}',
        }))

    assert err.value.field_name == CONF_ATTRIBUTE_SOURCES_JSON


def test_build_entity_config_rejects_invalid_attribute_source_entity_id():
    with pytest.raises(InvalidEntityReference) as err:
        _build_entity_config(_entity_input({
            CONF_ATTRIBUTE_SOURCES_JSON: '{"battery": "not_an_entity.battery_level"}',
        }))

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
    original = MappingProxyType({
        ATTR_DEVICES: MappingProxyType({
            "Existing": [
                MappingProxyType({
                    CONF_PLATFORM: "sensor",
                    CONF_NAME: "Existing Sensor",
                }),
            ],
        }),
    })

    next_options = _append_ui_entity(
        original,
        "Laundry",
        MappingProxyType({
            CONF_PLATFORM: "sensor",
            CONF_NAME: "Washer Phase",
        }),
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
    assert _entity_choices({
        ATTR_DEVICES: {
            "Laundry": [
                "bad-entity",
                {CONF_PLATFORM: "sensor", CONF_NAME: "Washer Phase"},
            ],
            "Broken": "not-a-list",
        },
    }) == {
        _entity_key("Laundry", 1): "Laundry / Washer Phase (sensor)",
    }


def test_options_schema_allows_deleting_but_not_editing_invalid_stored_entity():
    schema = _options_schema({
        ATTR_DEVICES: {"Broken": ["bad-entity"]},
    })
    action_selector = next(iter(schema.schema.values()))

    assert action_selector.config["translation_key"] == "options_action"
    assert action_selector.config["options"] == [
        "add_entity",
        "delete_entity",
        "manage_devices",
        "backup_devices",
        "restore_devices",
        "finish",
    ]


def test_managed_device_choices_show_stable_id_and_entity_count():
    choices = _managed_device_choices({
        ATTR_DEVICES: {
            "Laundry": [{CONF_PLATFORM: "sensor"}, {CONF_PLATFORM: "binary_sensor"}],
        },
        ATTR_DEVICE_ATTRIBUTES: {
            "Laundry": {ATTR_DEVICE_ID: "laundry-1"},
        },
    })

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


def test_merge_helpers_skip_invalid_restored_payloads():
    merged = _merge_device_sets(
        {ATTR_DEVICES: "wrong"},
        {"Laundry": "not-a-list", "HVAC": [{CONF_PLATFORM: "climate"}]},
    )

    assert merged["HVAC"][0].pop(ATTR_ENTITY_KEY)
    assert merged == {"HVAC": [{CONF_PLATFORM: "climate"}]}


def test_merge_helpers_skip_invalid_entities_inside_restored_list():
    merged = _merge_device_sets(
        {},
        {
            "Laundry": [
                "bad-entity",
                {CONF_PLATFORM: "sensor", CONF_NAME: "Washer"},
                None,
            ],
        },
    )

    assert merged["Laundry"][0].pop(ATTR_ENTITY_KEY)
    assert merged == {
        "Laundry": [{CONF_PLATFORM: "sensor", CONF_NAME: "Washer"}],
    }


def test_entity_form_defaults_round_trips_stored_entity_config():
    defaults = _entity_form_defaults(
        "Laundry",
        {
            CONF_PLATFORM: "sensor",
            CONF_NAME: "Washer Phase",
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
    assert defaults[CONF_ENTITY_NAME] == "Washer Phase"
    assert defaults[ATTR_ENTITY_ID] == "sensor.washer_phase"
    assert defaults[CONF_INITIAL_AVAILABILITY] is False
    assert defaults[CONF_PERSISTENT] is False
    assert defaults[CONF_SOURCE_ENTITIES_TEXT] == "sensor.washer_power"
    assert '"power"' in defaults[CONF_TEMPLATE_SOURCES_JSON]
    assert defaults[CONF_PULL_INTERVAL] == 30
    assert defaults[CONF_VALUE_TEMPLATE] == "{{ power }}"
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


def test_merge_device_sets_appends_restored_entities_without_mutating_existing():
    existing = {
        "Laundry": [
            {
                CONF_PLATFORM: "sensor",
                CONF_NAME: "Current",
                ATTR_ENTITY_KEY: "current-key",
            },
        ],
    }
    restored = {
        "Laundry": [
            {
                CONF_PLATFORM: "binary_sensor",
                CONF_NAME: "Door",
                ATTR_ENTITY_KEY: "backup-door-key",
            },
        ],
        "HVAC": [
            {
                CONF_PLATFORM: "climate",
                CONF_NAME: "Thermostat",
                ATTR_ENTITY_KEY: "backup-hvac-key",
            },
        ],
    }

    merged = _merge_device_sets(existing, restored)

    assert existing["Laundry"] == [
        {
            CONF_PLATFORM: "sensor",
            CONF_NAME: "Current",
            ATTR_ENTITY_KEY: "current-key",
        },
    ]
    assert merged["Laundry"][0] == {
        CONF_PLATFORM: "sensor",
        CONF_NAME: "Current",
        ATTR_ENTITY_KEY: "current-key",
    }
    restored_laundry_entity = merged["Laundry"][1]
    assert restored_laundry_entity.pop(ATTR_ENTITY_KEY) != "backup-door-key"
    assert restored_laundry_entity == {
        CONF_PLATFORM: "binary_sensor",
        CONF_NAME: "Door",
    }
    restored_hvac_entity = merged["HVAC"][0]
    assert restored_hvac_entity.pop(ATTR_ENTITY_KEY) != "backup-hvac-key"
    assert restored_hvac_entity == {
        CONF_PLATFORM: "climate",
        CONF_NAME: "Thermostat",
    }


def test_service_restore_merge_regenerates_restored_entity_keys():
    merged = _service_merge_device_sets(
        {"Laundry": []},
        {
            "Laundry": [
                {
                    CONF_PLATFORM: "sensor",
                    CONF_NAME: "Restored",
                    ATTR_ENTITY_KEY: "backup-key",
                },
            ],
        },
    )

    restored_entity = merged["Laundry"][0]
    assert restored_entity.pop(ATTR_ENTITY_KEY) != "backup-key"
    assert restored_entity == {
        CONF_PLATFORM: "sensor",
        CONF_NAME: "Restored",
    }


def test_merge_device_attributes_restored_metadata_wins():
    existing = {
        "Laundry": {
            ATTR_DEVICE_ID: "old",
            CONF_NAME: "Laundry",
        },
    }
    restored = {
        "Laundry": {
            ATTR_DEVICE_ID: "new",
            CONF_NAME: "Laundry",
            CONF_MODEL: "Washer 9000",
        },
        "HVAC": {
            ATTR_DEVICE_ID: "hvac",
            CONF_NAME: "HVAC",
        },
    }

    merged = _merge_device_attributes(existing, restored)

    assert existing["Laundry"][ATTR_DEVICE_ID] == "old"
    assert merged["Laundry"][ATTR_DEVICE_ID] == "new"
    assert merged["Laundry"][CONF_MODEL] == "Washer 9000"
    assert merged["HVAC"][ATTR_DEVICE_ID] == "hvac"


def test_find_backup_group_prefers_matching_group_when_multiple_exist():
    backup_group = _find_backup_group_for_entry([
        {ATTR_GROUP_NAME: "first", ATTR_DEVICES: {}},
        {ATTR_GROUP_NAME: "second", ATTR_DEVICES: {"Device": []}},
    ], SimpleNamespace(data={ATTR_GROUP_NAME: "second"}))

    assert backup_group == {ATTR_GROUP_NAME: "second", ATTR_DEVICES: {"Device": []}}
