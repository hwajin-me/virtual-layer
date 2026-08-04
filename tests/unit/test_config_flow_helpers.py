"""Unit tests for Virtual Layer config flow helpers."""

from types import MappingProxyType

import pytest

from homeassistant.const import ATTR_ENTITY_ID, CONF_NAME, CONF_PLATFORM

from custom_components.virtual_layer import _merge_device_sets as _service_merge_device_sets
from custom_components.virtual_layer.config_flow import (
    CONF_ATTRIBUTE_SOURCES_JSON,
    CONF_ATTRIBUTES_JSON,
    CONF_ATTRIBUTE_TEMPLATES_JSON,
    CONF_DEVICE_HW_VERSION,
    CONF_DEVICE_ID,
    CONF_DEVICE_MANUFACTURER,
    CONF_DEVICE_MODEL,
    CONF_DEVICE_NAME,
    CONF_DEVICE_SERIAL_NUMBER,
    CONF_DEVICE_SW_VERSION,
    CONF_ENTITY_NAME,
    CONF_SOURCE_ENTITIES_TEXT,
    CONF_TEMPLATE_SOURCES_JSON,
    InvalidEntityReference,
    InvalidEntityId,
    InvalidJson,
    _append_ui_entity,
    _build_device_config,
    _build_entity_config,
    _delete_ui_entities,
    _entity_choices,
    _entity_form_defaults,
    _entity_key,
    _entity_key_from_stable_key,
    _find_entity_by_selection_key,
    _find_backup_group_for_entry,
    _merge_device_attributes,
    _merge_device_sets,
    _options_schema,
    _reference_entity_defaults,
    _replace_ui_entity,
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
    CONF_INITIAL_AVAILABILITY,
    CONF_INITIAL_VALUE,
    CONF_HW_VERSION,
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
    }
    data.update(overrides or {})
    return data


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


def test_reference_entity_defaults_combines_location_sources_with_all_home_template(hass):
    hass.states.async_set("device_tracker.phone", "home")
    hass.states.async_set("person.owner", "not_home")

    defaults = _reference_entity_defaults(
        hass,
        ["device_tracker.phone", "person.owner"],
    )

    assert defaults[CONF_PLATFORM] == "device_tracker"
    assert defaults[CONF_INITIAL_VALUE] == "not_home"
    assert defaults[CONF_VALUE_TEMPLATE] == (
        "{{ 'home' if (phone == 'home') and (owner == 'home') else 'not_home' }}"
    )


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
        "backup_devices",
        "restore_devices",
        "finish",
    ]


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
    class Entry:
        data = {ATTR_GROUP_NAME: "second"}

    backup_group = _find_backup_group_for_entry([
        {ATTR_GROUP_NAME: "first", ATTR_DEVICES: {}},
        {ATTR_GROUP_NAME: "second", ATTR_DEVICES: {"Device": []}},
    ], Entry())

    assert backup_group == {ATTR_GROUP_NAME: "second", ATTR_DEVICES: {"Device": []}}
