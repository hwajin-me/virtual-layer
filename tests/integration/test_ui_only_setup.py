"""Integration tests for UI-only Virtual Layer setup behavior."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import homeassistant.helpers.area_registry as ar
import homeassistant.helpers.device_registry as dr
import homeassistant.helpers.entity_registry as er
import pytest
import voluptuous as vol
from homeassistant.components.camera import Camera, CameraEntityFeature, CameraState
from homeassistant.components.camera.const import StreamType
from homeassistant.components.camera.webrtc import WebRTCAnswer
from homeassistant.components.climate import ClimateEntityFeature, HVACMode
from homeassistant.components.fan import FanEntityFeature
from homeassistant.config_entries import SOURCE_IMPORT, SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    ATTR_RESTORED,
    CONF_ICON,
    CONF_NAME,
    CONF_PLATFORM,
    EVENT_HOMEASSISTANT_STARTED,
)
from homeassistant.core import Context, CoreState
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError, Unauthorized
from homeassistant.helpers.template import Template
from homeassistant.helpers.translation import async_get_translations
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.virtual_layer import (
    CONFIG_SCHEMA,
    SERVICE_SET_ATTRIBUTES_SCHEMA,
    SERVICE_SET_STATE_SCHEMA,
    _async_apply_state_only_event_hook,
    _async_apply_state_only_templates,
    _async_delete_virtual_device_from_registry,
    _async_delete_virtual_entity_from_registry,
    _async_remove_device_metadata_guard,
    _async_remove_entity_id_guard,
    _async_remove_orphaned_diagnostic_registry_entries,
    _async_setup_device_metadata_guard,
    _async_setup_entity_id_guard,
    _async_setup_state_only_templates,
    _async_verify_admin,
    _async_verify_target_entity_control,
    async_remove_entry,
    async_setup,
    async_setup_entry,
    async_unload_entry,
    async_virtual_clear_attributes_service,
    async_virtual_set_attributes_service,
    async_virtual_set_availability_service,
    async_virtual_set_state_service,
)
from custom_components.virtual_layer import binary_sensor as virtual_binary_sensor
from custom_components.virtual_layer import device_tracker as virtual_device_tracker
from custom_components.virtual_layer import sensor as virtual_sensor
from custom_components.virtual_layer.binary_sensor import (
    async_setup_platform as async_setup_binary_sensor_platform,
)
from custom_components.virtual_layer.camera import (
    CAMERA_SCHEMA,
    VirtualCamera,
)
from custom_components.virtual_layer.camera import (
    CONF_SOURCE_ENTITY as CAMERA_SOURCE_ENTITY,
)
from custom_components.virtual_layer.climate import CLIMATE_SCHEMA, VirtualClimate
from custom_components.virtual_layer.fan import FAN_SCHEMA, VirtualFan
from custom_components.virtual_layer.config_flow import (
    ACTION_ADD_ENTITY,
    ACTION_DELETE_DEVICE,
    ACTION_DELETE_ENTITY,
    ACTION_EDIT_ENTITY,
    ACTION_FINISH,
    ACTION_MANAGE_DEVICES,
    CLIMATE_NATIVE_TEMPLATE_PROPERTIES,
    CONF_ACTION,
    CONF_ADD_FIRST_ENTITY,
    CONF_ADVANCED_SETTINGS,
    CONF_ATTRIBUTE_TEMPLATES_JSON,
    CONF_ATTRIBUTES_JSON,
    CONF_COMMAND_ACTIONS_JSON,
    CONF_DEVICE_ID,
    CONF_DEVICE_MANUFACTURER,
    CONF_DEVICE_MODEL,
    CONF_DEVICE_NAME,
    CONF_DOMAIN_OPTIONS_JSON,
    CONF_ENTITY_KEY,
    CONF_ENTITY_KEYS,
    CONF_ENTITY_NAME,
    CONF_HELPER_UPDATE_MODE,
    CONF_MANAGED_DEVICE_NAME,
    CONF_NATIVE_TEMPLATES_JSON,
    CONF_NATIVE_VALUE_TEMPLATES,
    CONF_REFERENCE_ENTITY_ID,
    CONF_SOURCE_ENTITIES_TEXT,
    CONF_TARGET_DEVICE_NAME,
    CONF_TARGET_ENTITY_TYPE,
    CONF_TEMPLATE_SOURCES_JSON,
    CONF_USE_TEMPLATE_HELPER,
    DOMAIN_NATIVE_TEMPLATE_PROPERTIES,
    HELPER_UPDATE_AUTO,
    HELPER_UPDATE_FORCE,
    HELPER_UPDATE_KEEP,
    VirtualOptionsFlowHandler,
    _auto_helper_profile,
    _entity_key,
    _flatten_entity_form_sections,
    _reference_entity_defaults,
)
from custom_components.virtual_layer.const import (
    ATTR_ATTRIBUTES,
    ATTR_AVAILABLE,
    ATTR_CONFIG_ENTRY_ID,
    ATTR_DEVICE_ATTRIBUTES,
    ATTR_DEVICE_ID,
    ATTR_DEVICES,
    ATTR_ENTITIES,
    ATTR_ENTITY_KEY,
    ATTR_GROUP_NAME,
    ATTR_UNIQUE_ID,
    ATTR_VALUE,
    ATTR_VIRTUAL_ATTRIBUTES,
    COMPONENT_DOMAIN,
    COMPONENT_SERVICES,
    CONF_ATTRIBUTE,
    CONF_ATTRIBUTE_SOURCES,
    CONF_ATTRIBUTE_TEMPLATES,
    CONF_ATTRIBUTES,
    CONF_AUTO_HELPER,
    CONF_AVAILABILITY_TEMPLATE,
    CONF_CLASS,
    CONF_COMMAND_ACTIONS,
    CONF_CONFIGURATION_URL,
    CONF_EVENT_HOOKS,
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
    CONF_PULL_INTERVAL,
    CONF_SOURCE_ENTITIES,
    CONF_SUGGESTED_AREA,
    CONF_SW_VERSION,
    CONF_TEMPLATE_SOURCES,
    CONF_VALUE_TEMPLATE,
    CONF_VIA_DEVICE_ID,
    TRANSIENT_SOURCE_ATTRIBUTE_NAMES,
)
from custom_components.virtual_layer.sensor import VirtualSensor

pytestmark = pytest.mark.integration


async def _choose_add_template_helper(
    hass,
    result,
    *,
    enabled=True,
    target_entity_type=None,
):
    if result["step_id"] == "entity_type":
        defaults = result["data_schema"]({})
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                CONF_TARGET_ENTITY_TYPE: target_entity_type
                or defaults[CONF_TARGET_ENTITY_TYPE],
            },
        )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "entity_helper"
    return await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_USE_TEMPLATE_HELPER: enabled},
    )


def _first_stored_entity(result):
    """Return the sole Device's first entity without relying on its display name."""
    devices = result["data"][ATTR_DEVICES]
    assert len(devices) == 1
    return next(iter(devices.values()))[0]


async def test_options_flow_persists_native_templates_and_command_actions(hass):
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={ATTR_DEVICES: {}},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(
        entry.entry_id,
        data={CONF_ACTION: ACTION_ADD_ENTITY},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_REFERENCE_ENTITY_ID: []},
    )
    defaults = _flatten_entity_form_sections(result["data_schema"]({}))
    native_templates = {
        "fan_modes": "{{ state_attr('climate.real', 'fan_modes') }}",
        "fan_mode": "{{ state_attr('climate.real', 'fan_mode') }}",
        "preset_modes": "{{ state_attr('climate.real', 'preset_modes') }}",
        "preset_mode": "{{ state_attr('climate.real', 'preset_mode') }}",
        "swing_mode": "{{ state_attr('climate.real', 'swing_mode') }}",
        "swing_horizontal_mode": "{{ state_attr('climate.real', 'swing_horizontal_mode') }}",
        "current_temperature": "{{ state_attr('climate.real', 'current_temperature') }}",
        "target_temperature": "{{ state_attr('climate.real', 'temperature') }}",
        "current_humidity": "{{ state_attr('climate.real', 'current_humidity') }}",
        "target_humidity": "{{ state_attr('climate.real', 'humidity') }}",
    }
    command_actions = {
        "set_fan_mode": [
            {
                "action": "climate.set_fan_mode",
                "target": {"entity_id": "climate.real"},
                "data": {"fan_mode": "{{ fan_mode }}"},
            }
        ]
    }

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            **defaults,
            CONF_DEVICE_NAME: "Linked HVAC",
            CONF_ENTITY_NAME: "Linked HVAC",
            ATTR_ENTITY_ID: "climate.linked_hvac",
            CONF_PLATFORM: "climate",
            CONF_INITIAL_VALUE: "off",
            CONF_COMMAND_ACTIONS_JSON: json.dumps(command_actions),
        },
    )
    if result["type"] == FlowResultType.FORM:
        validators = {
            marker.schema: validator
            for marker, validator in result["data_schema"].schema.items()
        }
        assert CONF_NATIVE_TEMPLATES_JSON not in validators
        defaults = _flatten_entity_form_sections(result["data_schema"]({}))
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                **defaults,
                CONF_NATIVE_VALUE_TEMPLATES: native_templates,
                CONF_COMMAND_ACTIONS_JSON: json.dumps(command_actions),
            },
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    entity = _first_stored_entity(result)
    assert set(entity[CONF_NATIVE_TEMPLATES]) == set(CLIMATE_NATIVE_TEMPLATE_PROPERTIES)
    assert {
        name: entity[CONF_NATIVE_TEMPLATES][name]
        for name in native_templates
    } == native_templates
    assert all(entity[CONF_NATIVE_TEMPLATES].values())
    assert entity[CONF_COMMAND_ACTIONS] == command_actions


async def test_options_flow_can_copy_standard_energy_sensor(hass):
    hass.states.async_set(
        "sensor.energy_monitor",
        "12.5",
        {
            "friendly_name": "Energy",
            "device_class": "energy",
            "state_class": "total_increasing",
            "unit_of_measurement": "kWh",
            CONF_ICON: "mdi:flash",
        },
    )
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={ATTR_DEVICES: {}},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(
        entry.entry_id,
        data={CONF_ACTION: ACTION_ADD_ENTITY},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_REFERENCE_ENTITY_ID: ["sensor.energy_monitor"]},
    )
    result = await _choose_add_template_helper(hass, result)

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "entity"
    assert result["errors"] == {}
    defaults = _flatten_entity_form_sections(result["data_schema"]({}))
    assert defaults[CONF_PLATFORM] == "sensor"
    assert defaults[CONF_INITIAL_VALUE] == "12.5"
    assert json.loads(defaults[CONF_DOMAIN_OPTIONS_JSON]) == {
        "class": "energy",
        "unit_of_measurement": "kWh",
    }
    native_templates = defaults[CONF_NATIVE_VALUE_TEMPLATES]
    assert native_templates["options"] == (
        "{{ state_attr('sensor.energy_monitor', 'options') }}"
    )
    assert native_templates["suggested_display_precision"] == (
        "{{ state_attr('sensor.energy_monitor', 'suggested_display_precision') }}"
    )

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            **defaults,
            CONF_DEVICE_NAME: "Energy Monitor",
            ATTR_ENTITY_ID: "sensor.virtual_energy_monitor",
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    entity = _first_stored_entity(result)
    runtime_config = {
        key: value
        for key, value in entity.items()
        if key not in {CONF_PLATFORM, ATTR_ENTITY_KEY, CONF_AUTO_HELPER}
    }
    sensor = VirtualSensor(virtual_sensor.SENSOR_SCHEMA(runtime_config), False)
    sensor.hass = hass
    sensor._create_state(sensor._config)
    sensor.async_schedule_update_ha_state = Mock()
    sensor._apply_templates()

    assert sensor.native_value == "12.5"
    assert sensor.options is None
    assert sensor.device_class == "energy"
    assert sensor.state_class == "total_increasing"
    assert sensor.native_unit_of_measurement == "kWh"

    hass.states.async_set(
        "sensor.energy_monitor",
        "13.0",
        {
            "device_class": "energy",
            "state_class": "total_increasing",
            "unit_of_measurement": "kWh",
            "options": ["13.0"],
            "suggested_display_precision": 2,
        },
    )
    sensor._apply_templates()
    assert sensor.native_value == "13.0"
    assert sensor.options == ["13.0"]
    assert sensor.suggested_display_precision == 2

    hass.states.async_set(
        "sensor.energy_monitor",
        "14.0",
        {
            "device_class": "energy",
            "state_class": "total_increasing",
            "unit_of_measurement": "kWh",
        },
    )
    sensor._apply_templates()
    assert sensor.native_value == "14.0"
    assert sensor.options is None
    assert sensor.suggested_display_precision is None


async def test_options_flow_converts_single_switch_source_to_fan(hass):
    hass.states.async_set(
        "switch.desk_fan_power",
        "on",
        {"friendly_name": "Desk Fan Power"},
    )
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={ATTR_DEVICES: {}},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(
        entry.entry_id,
        data={CONF_ACTION: ACTION_ADD_ENTITY},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_REFERENCE_ENTITY_ID: ["switch.desk_fan_power"]},
    )

    assert result["step_id"] == "entity_type"
    type_options = result["data_schema"].schema
    target_selector = next(
        validator
        for marker, validator in type_options.items()
        if marker.schema == CONF_TARGET_ENTITY_TYPE
    )
    assert {
        option["value"] for option in target_selector.config["options"]
    } == {"switch", "fan", "light"}

    result = await _choose_add_template_helper(
        hass,
        result,
        target_entity_type="fan",
    )
    defaults = _flatten_entity_form_sections(result["data_schema"]({}))

    assert defaults[CONF_PLATFORM] == "fan"
    assert defaults[CONF_NATIVE_VALUE_TEMPLATES]["is_on"] == (
        "{{ states('switch.desk_fan_power') not in "
        "['off', 'unknown', 'unavailable'] }}"
    )
    assert json.loads(defaults[CONF_COMMAND_ACTIONS_JSON]) == {
        "turn_off": [{
            "action": "switch.turn_off",
            "target": {ATTR_ENTITY_ID: "switch.desk_fan_power"},
        }],
        "turn_on": [{
            "action": "switch.turn_on",
            "target": {ATTR_ENTITY_ID: "switch.desk_fan_power"},
        }],
    }

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        defaults,
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    stored = _first_stored_entity(result)
    runtime_config = {
        key: value
        for key, value in stored.items()
        if key not in {CONF_PLATFORM, ATTR_ENTITY_KEY, CONF_AUTO_HELPER}
    }
    calls = []

    async def _capture_turn_on(call):
        calls.append(dict(call.data))

    hass.services.async_register("switch", "turn_on", _capture_turn_on)
    fan = VirtualFan(FAN_SCHEMA(runtime_config), False)
    fan.hass = hass
    fan._create_state(fan._config)
    fan.async_write_ha_state = Mock()

    await fan.async_turn_on()

    assert calls == [{ATTR_ENTITY_ID: ["switch.desk_fan_power"]}]


async def test_entity_type_step_recovers_when_source_disappears(hass):
    hass.states.async_set("switch.temporary_source", "on")
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={ATTR_DEVICES: {}},
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(
        entry.entry_id,
        data={CONF_ACTION: ACTION_ADD_ENTITY},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_REFERENCE_ENTITY_ID: ["switch.temporary_source"]},
    )
    assert result["step_id"] == "entity_type"

    hass.states.async_remove("switch.temporary_source")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_TARGET_ENTITY_TYPE: "fan"},
    )

    assert result["step_id"] == "entity_type"
    assert result["errors"] == {"base": "source_unavailable"}


async def test_options_flow_edit_changes_single_switch_backed_entity_to_fan(hass):
    hass.states.async_set("switch.desk_power", "off")
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={
            ATTR_DEVICES: {
                "Desk": [{
                    CONF_PLATFORM: "switch",
                    CONF_NAME: "Desk Power",
                    ATTR_ENTITY_ID: "switch.desk_power_virtual",
                    CONF_INITIAL_VALUE: "off",
                    CONF_SOURCE_ENTITIES: ["switch.desk_power"],
                }],
            },
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(
        entry.entry_id,
        data={CONF_ACTION: ACTION_EDIT_ENTITY},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_ENTITY_KEY: _entity_key("Desk", 0)},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_REFERENCE_ENTITY_ID: ["switch.desk_power"]},
    )

    assert result["step_id"] == "edit_entity_type"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_TARGET_ENTITY_TYPE: "fan"},
    )
    assert result["step_id"] == "edit_entity_helper"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_HELPER_UPDATE_MODE: HELPER_UPDATE_AUTO},
    )

    assert result["step_id"] == "edit_entity"
    defaults = _flatten_entity_form_sections(result["data_schema"]({}))
    assert defaults[CONF_PLATFORM] == "fan"
    assert defaults[ATTR_ENTITY_ID] == "fan.desk_power_virtual"
    assert json.loads(defaults[CONF_COMMAND_ACTIONS_JSON])["turn_on"] == [{
        "action": "switch.turn_on",
        "target": {ATTR_ENTITY_ID: "switch.desk_power"},
    }]

    defaults[CONF_PLATFORM] = "light"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        defaults,
    )
    assert result["step_id"] == "edit_entity"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        _flatten_entity_form_sections(result["data_schema"]({})),
    )
    assert result["step_id"] == "edit_entity_helper"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_HELPER_UPDATE_MODE: HELPER_UPDATE_AUTO},
    )
    changed_defaults = _flatten_entity_form_sections(result["data_schema"]({}))
    assert changed_defaults[CONF_PLATFORM] == "light"
    assert changed_defaults[ATTR_ENTITY_ID] == "light.desk_power_virtual"
    assert set(changed_defaults[CONF_NATIVE_VALUE_TEMPLATES]) == set(
        DOMAIN_NATIVE_TEMPLATE_PROPERTIES["light"]
    )


async def test_edit_type_step_preserves_legacy_custom_target_domain(hass):
    hass.states.async_set("switch.legacy_source", "on")
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={
            ATTR_DEVICES: {
                "Legacy": [{
                    CONF_PLATFORM: "sensor",
                    CONF_NAME: "Legacy State",
                    ATTR_ENTITY_ID: "sensor.legacy_state",
                    CONF_INITIAL_VALUE: "on",
                    CONF_SOURCE_ENTITIES: ["switch.legacy_source"],
                    CONF_VALUE_TEMPLATE: "{{ states('switch.legacy_source') }}",
                }],
            },
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(
        entry.entry_id,
        data={CONF_ACTION: ACTION_EDIT_ENTITY},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_ENTITY_KEY: _entity_key("Legacy", 0)},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_REFERENCE_ENTITY_ID: ["switch.legacy_source"]},
    )

    assert result["step_id"] == "edit_entity_type"
    assert result["data_schema"]({})[CONF_TARGET_ENTITY_TYPE] == "sensor"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_TARGET_ENTITY_TYPE: "sensor"},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_HELPER_UPDATE_MODE: HELPER_UPDATE_KEEP},
    )

    defaults = _flatten_entity_form_sections(result["data_schema"]({}))
    assert defaults[CONF_PLATFORM] == "sensor"
    assert defaults[ATTR_ENTITY_ID] == "sensor.legacy_state"
    assert defaults[CONF_VALUE_TEMPLATE] == (
        "{{ states('switch.legacy_source') }}"
    )


async def test_edit_automatic_repairs_legacy_switch_backed_fan_actions(hass):
    hass.states.async_set("switch.legacy_fan_power", "off")
    runtime_calls = []

    async def _capture_legacy_turn_on(call):
        runtime_calls.append(dict(call.data))

    hass.services.async_register("switch", "turn_on", _capture_legacy_turn_on)
    legacy_fan = VirtualFan(
        FAN_SCHEMA({
            CONF_NAME: "Legacy Fan",
            ATTR_ENTITY_ID: "fan.legacy_fan_runtime",
            ATTR_UNIQUE_ID: "legacy-fan-runtime",
            CONF_INITIAL_VALUE: "off",
            CONF_SOURCE_ENTITIES: ["switch.legacy_fan_power"],
        }),
        False,
    )
    legacy_fan.hass = hass
    legacy_fan._create_state(legacy_fan._config)
    legacy_fan.async_write_ha_state = Mock()

    await legacy_fan.async_turn_on(percentage=75)

    assert runtime_calls == [{
        ATTR_ENTITY_ID: ["switch.legacy_fan_power"],
    }]

    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={
            ATTR_DEVICES: {
                "Legacy Fan": [{
                    CONF_PLATFORM: "fan",
                    CONF_NAME: "Legacy Fan",
                    ATTR_ENTITY_ID: "fan.legacy_fan",
                    CONF_INITIAL_VALUE: "off",
                    CONF_SOURCE_ENTITIES: ["switch.legacy_fan_power"],
                }],
            },
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(
        entry.entry_id,
        data={CONF_ACTION: ACTION_EDIT_ENTITY},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_ENTITY_KEY: _entity_key("Legacy Fan", 0)},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_REFERENCE_ENTITY_ID: ["switch.legacy_fan_power"]},
    )
    assert result["step_id"] == "edit_entity_type"
    assert result["data_schema"]({})[CONF_TARGET_ENTITY_TYPE] == "fan"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_TARGET_ENTITY_TYPE: "fan"},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_HELPER_UPDATE_MODE: HELPER_UPDATE_AUTO},
    )

    defaults = _flatten_entity_form_sections(result["data_schema"]({}))
    actions = json.loads(defaults[CONF_COMMAND_ACTIONS_JSON])
    assert actions["turn_on"] == [{
        "action": "switch.turn_on",
        "target": {ATTR_ENTITY_ID: "switch.legacy_fan_power"},
    }]
    assert actions["turn_off"] == [{
        "action": "switch.turn_off",
        "target": {ATTR_ENTITY_ID: "switch.legacy_fan_power"},
    }]
    assert defaults[CONF_NATIVE_VALUE_TEMPLATES]["is_on"] == (
        "{{ states('switch.legacy_fan_power') not in "
        "['off', 'unknown', 'unavailable'] }}"
    )


async def test_options_flow_ignores_restored_source_metadata(hass):
    hass.states.async_set(
        "sensor.radon_sensor",
        "unknown",
        {
            ATTR_RESTORED: "{{ <RestoredState> }}",
            "vendor_status": "warming_up",
        },
    )
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={ATTR_DEVICES: {}},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(
        entry.entry_id,
        data={CONF_ACTION: ACTION_ADD_ENTITY},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_REFERENCE_ENTITY_ID: ["sensor.radon_sensor"]},
    )
    result = await _choose_add_template_helper(hass, result)
    defaults = _flatten_entity_form_sections(result["data_schema"]({}))

    assert ATTR_RESTORED not in json.loads(defaults[CONF_ATTRIBUTES_JSON])
    assert ATTR_RESTORED not in json.loads(
        defaults[CONF_ATTRIBUTE_TEMPLATES_JSON]
    )

    # A flow opened before the fix can still submit the old generated fields.
    # They are Home Assistant-owned metadata and should be repaired, not block
    # the complete entity form with invalid_template.
    stale_templates = json.loads(defaults[CONF_ATTRIBUTE_TEMPLATES_JSON])
    stale_templates.update({
        ATTR_RESTORED: "{{ <RestoredState> }}",
        "access_token": "{{ <rotating-token> }}",
        "entity_picture": "{{ <tokenized-picture> }}",
    })
    defaults[CONF_ATTRIBUTE_TEMPLATES_JSON] = json.dumps(stale_templates)

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            **defaults,
            CONF_DEVICE_NAME: "Radon Sensor",
            ATTR_ENTITY_ID: "sensor.virtual_radon_sensor",
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    entity = _first_stored_entity(result)
    assert ATTR_RESTORED not in entity.get(CONF_ATTRIBUTES, {})
    assert ATTR_RESTORED not in entity.get(CONF_ATTRIBUTE_TEMPLATES, {})
    assert entity[CONF_ATTRIBUTE_TEMPLATES]["vendor_status"] == (
        "{{ state_attr('sensor.radon_sensor', 'vendor_status') }}"
    )


async def test_options_flow_camera_alias_tracks_native_camera_states(hass):
    source_entity_id = "camera.camera1"
    hass.states.async_set(
        source_entity_id,
        CameraState.RECORDING,
        {
            "access_token": "rotating-secret",
            "entity_picture": "/api/camera_proxy/camera.camera1?token=secret",
        },
    )
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={ATTR_DEVICES: {}},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(
        entry.entry_id,
        data={CONF_ACTION: ACTION_ADD_ENTITY},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_REFERENCE_ENTITY_ID: [source_entity_id]},
    )
    result = await _choose_add_template_helper(hass, result)
    defaults = _flatten_entity_form_sections(result["data_schema"]({}))

    assert defaults[CONF_PLATFORM] == "camera"
    assert defaults[CONF_INITIAL_VALUE] == CameraState.RECORDING
    assert "access_token" not in json.loads(defaults[CONF_ATTRIBUTES_JSON] or "{}")
    assert "access_token" not in json.loads(
        defaults[CONF_ATTRIBUTE_TEMPLATES_JSON] or "{}"
    )

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            **defaults,
            CONF_DEVICE_NAME: "Camera",
            ATTR_ENTITY_ID: "camera.camera1_copy",
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY

    stored = _first_stored_entity(result)
    runtime_config = {
        key: value
        for key, value in stored.items()
        if key not in {CONF_PLATFORM, ATTR_ENTITY_KEY, CONF_AUTO_HELPER}
    }
    # Old releases copied HA-owned camera metadata into generic attribute
    # templates. Those records must load without compiling the malformed token.
    runtime_config.setdefault(CONF_ATTRIBUTES, {}).update({
        ATTR_RESTORED: True,
        "access_token": "stale-token",
    })
    runtime_config.setdefault(CONF_ATTRIBUTE_TEMPLATES, {}).update({
        "access_token": "{{ <rotating-token> }}",
        "entity_picture": "{{ <tokenized-picture> }}",
    })
    camera = VirtualCamera(CAMERA_SCHEMA(runtime_config), False)
    camera.hass = hass
    camera.async_schedule_update_ha_state = Mock()
    camera._create_state(camera._config)
    camera._apply_templates()

    assert set(camera._virtual_attributes).isdisjoint(
        TRANSIENT_SOURCE_ATTRIBUTE_NAMES
    )
    assert set(camera._attribute_templates).isdisjoint(
        TRANSIENT_SOURCE_ATTRIBUTE_NAMES
    )
    assert camera.available is True
    assert camera.is_on is True
    assert camera.is_recording is True
    assert camera.is_streaming is False
    assert camera.state is CameraState.RECORDING

    for source_state, expected_state in (
        (CameraState.STREAMING, CameraState.STREAMING),
        (CameraState.IDLE, CameraState.IDLE),
        ("unavailable", CameraState.IDLE),
        (CameraState.RECORDING, CameraState.RECORDING),
    ):
        hass.states.async_set(source_entity_id, source_state)
        camera._apply_templates()
        assert camera.available is (source_state != "unavailable")
        assert camera.state is expected_state


async def test_options_flow_builds_and_runs_climate_hot_water_boiler_helper(hass):
    hass.states.async_set(
        "climate.boiler",
        "heat",
        {
            "friendly_name": "Boiler",
            "current_temperature": 29.0,
            "temperature": 26.0,
            "hvac_modes": ["auto", "heat", "fan_only", "off"],
            "min_temp": 10.0,
            "max_temp": 35.0,
        },
    )
    hass.states.async_set(
        "switch.hot_water",
        "on",
        {"friendly_name": "Hot water mode"},
    )
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={ATTR_DEVICES: {}},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(
        entry.entry_id,
        data={CONF_ACTION: ACTION_ADD_ENTITY},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_REFERENCE_ENTITY_ID: [
                "switch.hot_water",
                "climate.boiler",
            ]
        },
    )
    result = await _choose_add_template_helper(hass, result)

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "entity"
    defaults = _flatten_entity_form_sections(result["data_schema"]({}))
    assert defaults[CONF_PLATFORM] == "climate"
    assert defaults[CONF_ENTITY_NAME] == "Boiler"
    assert defaults[CONF_INITIAL_VALUE] == "heat"
    assert defaults[CONF_NATIVE_VALUE_TEMPLATES]["hvac_modes"] == (
        "{{ ['off', 'heat'] }}"
    )
    assert "switch.hot_water" not in defaults[CONF_NATIVE_VALUE_TEMPLATES][
        "current_temperature"
    ]
    generated_actions = json.loads(defaults[CONF_COMMAND_ACTIONS_JSON])
    assert generated_actions["turn_off"] == [
        {
            "action": "switch.turn_on",
            "target": {ATTR_ENTITY_ID: "switch.hot_water"},
        },
        {
            "action": "climate.set_hvac_mode",
            "data": {"hvac_mode": "fan_only"},
            "target": {ATTR_ENTITY_ID: "climate.boiler"},
        },
    ]
    assert generated_actions["turn_on"] == [
        {
            "action": "switch.turn_on",
            "target": {ATTR_ENTITY_ID: "switch.hot_water"},
        },
        {
            "action": "climate.set_hvac_mode",
            "data": {"hvac_mode": "heat"},
            "target": {ATTR_ENTITY_ID: "climate.boiler"},
        },
    ]

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            **defaults,
            CONF_DEVICE_NAME: "Boiler",
            ATTR_ENTITY_ID: "climate.virtual_boiler",
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    stored = _first_stored_entity(result)
    runtime_config = {
        key: value
        for key, value in stored.items()
        if key not in {CONF_PLATFORM, ATTR_ENTITY_KEY, CONF_AUTO_HELPER}
    }
    boiler = VirtualClimate(CLIMATE_SCHEMA(runtime_config), False)
    boiler.hass = hass
    boiler._create_state(boiler._config)
    boiler.async_write_ha_state = Mock()
    boiler.async_schedule_update_ha_state = Mock()
    boiler._apply_templates()
    assert boiler.hvac_modes == [HVACMode.OFF, HVACMode.HEAT]
    assert boiler.hvac_mode is HVACMode.HEAT
    assert boiler.current_temperature == 29.0
    assert boiler.target_temperature == 26.0

    calls = []

    async def _capture(call):
        calls.append((call.domain, call.service, dict(call.data)))

    for domain, service in (
        ("climate", "set_hvac_mode"),
        ("climate", "set_temperature"),
        ("switch", "turn_on"),
        ("switch", "turn_off"),
    ):
        hass.services.async_register(domain, service, _capture)

    await boiler.async_set_hvac_mode(HVACMode.OFF)
    assert [(domain, service) for domain, service, _data in calls] == [
        ("switch", "turn_on"),
        ("climate", "set_hvac_mode"),
    ]
    assert calls[1][2]["hvac_mode"] == "fan_only"

    calls.clear()
    await boiler.async_set_hvac_mode(HVACMode.HEAT)
    assert [(domain, service) for domain, service, _data in calls] == [
        ("switch", "turn_on"),
        ("climate", "set_hvac_mode"),
    ]
    assert calls[1][2]["hvac_mode"] == "heat"

    calls.clear()
    await boiler.async_set_temperature(temperature=27)
    assert [(domain, service) for domain, service, _data in calls] == [
        ("climate", "set_temperature"),
    ]
    assert calls[0][2]["temperature"] == 27

    calls.clear()
    with pytest.raises(ValueError, match="Unsupported HVAC mode"):
        await boiler.async_set_hvac_mode(HVACMode.COOL)
    assert calls == []

    with pytest.raises(ValueError, match="configured minimum and maximum"):
        await boiler.async_set_temperature(temperature=100)
    assert calls == []


async def test_options_flow_rejects_invalid_jinja_before_saving(hass, caplog):
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={ATTR_DEVICES: {}},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(
        entry.entry_id,
        data={CONF_ACTION: ACTION_ADD_ENTITY},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_REFERENCE_ENTITY_ID: []},
    )
    defaults = result["data_schema"]({})
    defaults[CONF_ADVANCED_SETTINGS][CONF_ATTRIBUTE_TEMPLATES_JSON] = (
        '{"broken": "{{ broken + }}"}'
    )
    with caplog.at_level(
        "ERROR",
        logger="custom_components.virtual_layer.config_flow",
    ):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            defaults,
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {
        CONF_ATTRIBUTE_TEMPLATES_JSON: "invalid_template"
    }
    assert "field=attribute_templates_json" in caplog.text
    assert "template=broken" in caplog.text
    assert "unexpected 'end of print statement'" in caplog.text
    assert entry.options[ATTR_DEVICES] == {}


async def test_options_flow_rejects_an_entity_id_owned_by_another_entity(hass):
    hass.states.async_set("sensor.existing_meter", "42")
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={ATTR_DEVICES: {}},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(
        entry.entry_id,
        data={CONF_ACTION: ACTION_ADD_ENTITY},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_REFERENCE_ENTITY_ID: []},
    )
    defaults = _flatten_entity_form_sections(result["data_schema"]({}))
    defaults.update({
        CONF_DEVICE_NAME: "Meters",
        CONF_ENTITY_NAME: "Virtual Meter",
        ATTR_ENTITY_ID: "sensor.existing_meter",
    })

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        defaults,
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "entity"
    assert result["errors"] == {ATTR_ENTITY_ID: "entity_id_used"}
    assert entry.options[ATTR_DEVICES] == {}


async def test_vacuum_edit_hides_json_and_preserves_native_templates(hass):
    device_name = "Robot Vacuum"
    managed_property = "battery_level"
    native_templates = {
        managed_property: "{{ states('sensor.managed_value') }}",
        "vendor_property": "{{ states('sensor.vendor_value') }}",
    }
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={
            ATTR_DEVICES: {
                device_name: [{
                    CONF_PLATFORM: "vacuum",
                    CONF_NAME: f"Linked {device_name}",
                    CONF_INITIAL_VALUE: "docked",
                    CONF_INITIAL_AVAILABILITY: True,
                    CONF_PERSISTENT: True,
                    CONF_NATIVE_TEMPLATES: native_templates,
                }],
            },
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(
        entry.entry_id,
        data={CONF_ACTION: ACTION_EDIT_ENTITY},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_ENTITY_KEY: _entity_key(device_name, 0)},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {},
    )

    validators = {
        marker.schema: validator
        for marker, validator in result["data_schema"].schema.items()
    }
    assert CONF_NATIVE_TEMPLATES_JSON not in validators
    defaults = _flatten_entity_form_sections(result["data_schema"]({}))
    assert defaults[CONF_NATIVE_VALUE_TEMPLATES][managed_property] == (
        native_templates[managed_property]
    )

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        defaults,
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    saved = result["data"][ATTR_DEVICES][device_name][0]
    assert saved[CONF_NATIVE_TEMPLATES] == native_templates


async def test_climate_edit_repairs_legacy_enum_repr_native_template(hass):
    device_name = "Legacy Climate"
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={
            ATTR_DEVICES: {
                device_name: [{
                    CONF_PLATFORM: "climate",
                    CONF_NAME: "Boiler",
                    CONF_INITIAL_VALUE: "heat",
                    CONF_INITIAL_AVAILABILITY: True,
                    CONF_PERSISTENT: True,
                    CONF_NATIVE_TEMPLATES: {
                        "hvac_action": (
                            "{{ <HVACAction.HEATING: 'heating'> }}"
                        ),
                    },
                }],
            },
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(
        entry.entry_id,
        data={CONF_ACTION: ACTION_EDIT_ENTITY},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_ENTITY_KEY: _entity_key(device_name, 0)},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {},
    )

    defaults = _flatten_entity_form_sections(result["data_schema"]({}))
    assert defaults[CONF_NATIVE_VALUE_TEMPLATES]["hvac_action"] == (
        "{{ 'heating' }}"
    )

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        defaults,
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    saved = result["data"][ATTR_DEVICES][device_name][0]
    assert saved[CONF_NATIVE_TEMPLATES]["hvac_action"] == "{{ 'heating' }}"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_common_service_schemas_reject_non_finite_values(value):
    with pytest.raises(vol.Invalid):
        SERVICE_SET_STATE_SCHEMA({
            ATTR_ENTITY_ID: ["sensor.virtual"],
            ATTR_VALUE: value,
        })

    with pytest.raises(vol.Invalid):
        SERVICE_SET_ATTRIBUTES_SCHEMA({
            ATTR_ENTITY_ID: ["sensor.virtual"],
            ATTR_ATTRIBUTES: {"nested": [value]},
        })


@pytest.mark.parametrize(
    "attributes",
    [
        {1: "numeric key"},
        {"": "empty key"},
        {"unsupported": object()},
        {"too_large": 1 << 128},
    ],
)
def test_set_attributes_schema_rejects_non_json_compatible_values(attributes):
    with pytest.raises(vol.Invalid):
        SERVICE_SET_ATTRIBUTES_SCHEMA({
            ATTR_ENTITY_ID: ["sensor.virtual"],
            ATTR_ATTRIBUTES: attributes,
        })


def test_set_state_schema_rejects_integer_outside_home_assistant_json_range():
    with pytest.raises(vol.Invalid):
        SERVICE_SET_STATE_SCHEMA({
            ATTR_ENTITY_ID: ["sensor.virtual"],
            ATTR_VALUE: 1 << 128,
        })


def test_set_attributes_schema_uses_home_assistant_serialization_extensions():
    payload = {"nested": {2: "numeric key"}, "values": {"one", "two"}}
    validated = SERVICE_SET_ATTRIBUTES_SCHEMA({
        ATTR_ENTITY_ID: ["sensor.virtual"],
        ATTR_ATTRIBUTES: payload,
    })
    assert validated[ATTR_ATTRIBUTES] == payload


def test_set_attributes_schema_rejects_recursive_and_excessively_deep_values():
    recursive = {}
    recursive["self"] = recursive

    nested = {}
    cursor = nested
    for _ in range(102):
        cursor["next"] = {}
        cursor = cursor["next"]

    for attributes in (recursive, nested):
        with pytest.raises(vol.Invalid):
            SERVICE_SET_ATTRIBUTES_SCHEMA({
                ATTR_ENTITY_ID: ["sensor.virtual"],
                ATTR_ATTRIBUTES: attributes,
            })


async def test_home_assistant_loads_korean_config_translations(hass):
    config_translations = await async_get_translations(
        hass,
        "ko",
        "config",
        integrations={COMPONENT_DOMAIN},
    )
    options_translations = await async_get_translations(
        hass,
        "ko",
        "options",
        integrations={COMPONENT_DOMAIN},
    )
    selector_translations = await async_get_translations(
        hass,
        "ko",
        "selector",
        integrations={COMPONENT_DOMAIN},
    )

    assert config_translations[
        "component.virtual_layer.config.step.entity.title"
    ] == "가상 엔티티 추가"
    assert config_translations[
        "component.virtual_layer.config.step.entity.sections."
        "native_value_templates.data.supported_languages"
    ] == "지원 언어"
    assert config_translations[
        "component.virtual_layer.config.step.entity.sections."
        "device_details.data.device_model"
    ] == "장치 모델"
    assert "장치 레지스트리" in config_translations[
        "component.virtual_layer.config.step.entity.sections."
        "device_details.data_description.device_model"
    ]
    assert config_translations[
        "component.virtual_layer.config.step.entity.sections."
        "advanced_settings.data.event_hooks_json"
    ] == "이벤트 훅 JSON"
    assert "상태 변경" in config_translations[
        "component.virtual_layer.config.step.entity.sections."
        "advanced_settings.data_description.event_hooks_json"
    ]
    assert config_translations[
        "component.virtual_layer.config.step.entity.sections."
        "domain_settings.data.polygon_person"
    ] == "Person"
    assert "지도 위치" in config_translations[
        "component.virtual_layer.config.step.entity.sections."
        "domain_settings.data_description.polygon_person"
    ]
    assert "직접 수정한 값은 보존" in config_translations[
        "component.virtual_layer.config.step.entity.sections."
        "native_value_templates.description"
    ]
    assert "Jinja 템플릿" in config_translations[
        "component.virtual_layer.config.step.entity."
        "data_description.value_template"
    ]
    assert config_translations[
        "component.virtual_layer.config.step.entity_helper.title"
    ] == "템플릿 자동 생성 선택"
    assert "원본 엔티티" in config_translations[
        "component.virtual_layer.config.step.entity_helper.description"
    ]
    assert config_translations[
        "component.virtual_layer.config.step.entity_helper."
        "data.use_template_helper"
    ] == "원본 기반 템플릿 자동 생성"
    assert "템플릿은 생성하지 않습니다" in config_translations[
        "component.virtual_layer.config.step.entity_helper."
        "data_description.use_template_helper"
    ]
    assert options_translations[
        "component.virtual_layer.options.step.entity_helper.title"
    ] == "템플릿 자동 생성 선택"
    assert "원본 엔티티" in options_translations[
        "component.virtual_layer.options.step.entity_helper.description"
    ]
    assert selector_translations[
        "component.virtual_layer.selector.options_action.options.add_entity"
    ] == "가상 엔티티 추가"


async def test_config_import_is_rejected(hass):
    result = await hass.config_entries.flow.async_init(
        COMPONENT_DOMAIN,
        context={"source": SOURCE_IMPORT},
        data={ATTR_GROUP_NAME: "imported"},
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "import_not_supported"


async def test_initial_config_flow_device_name_is_blank(hass):
    result = await hass.config_entries.flow.async_init(
        COMPONENT_DOMAIN,
        context={"source": SOURCE_USER},
    )

    assert result["type"] == FlowResultType.FORM
    assert result["data_schema"]({})[ATTR_GROUP_NAME] == ""


async def test_config_flow_normalizes_and_validates_device_group_names(hass):
    result = await hass.config_entries.flow.async_init(
        COMPONENT_DOMAIN,
        context={"source": SOURCE_USER},
        data={ATTR_GROUP_NAME: "  Upstairs  "},
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][ATTR_GROUP_NAME] == "Upstairs"
    assert result["title"] == "Upstairs"

    result = await hass.config_entries.flow.async_init(
        COMPONENT_DOMAIN,
        context={"source": SOURCE_USER},
        data={ATTR_GROUP_NAME: "   "},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {ATTR_GROUP_NAME: "required"}

    result = await hass.config_entries.flow.async_init(
        COMPONENT_DOMAIN,
        context={"source": SOURCE_USER},
        data={ATTR_GROUP_NAME: " Upstairs "},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "group_name_used"}


def test_yaml_configuration_is_rejected_by_config_entry_only_schema(caplog):
    CONFIG_SCHEMA({COMPONENT_DOMAIN: {}})

    assert "does not support YAML setup" in caplog.text


async def test_setup_repairs_partially_initialized_runtime_data(hass):
    hass.data[COMPONENT_DOMAIN] = {}
    hass.data.pop("virtual_layer-services", None)

    assert await async_setup(hass, {}) is True

    assert hass.services.has_service(COMPONENT_DOMAIN, "set_state")
    assert hass.data["virtual_layer-services"][COMPONENT_DOMAIN] == "installed"


async def test_setup_does_not_overwrite_runtime_group_owned_by_another_entry(hass):
    owner = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "duplicate"},
    )
    duplicate = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "duplicate"},
    )
    owner.add_to_hass(hass)
    duplicate.add_to_hass(hass)
    owner_runtime = {
        ATTR_CONFIG_ENTRY_ID: owner.entry_id,
        ATTR_ENTITIES: {"sensor": []},
        ATTR_DEVICES: [],
    }
    hass.data[COMPONENT_DOMAIN] = {"duplicate": owner_runtime}

    assert await async_setup_entry(hass, duplicate) is False
    assert hass.data[COMPONENT_DOMAIN]["duplicate"] is owner_runtime
    assert await async_unload_entry(hass, duplicate) is True
    assert hass.data[COMPONENT_DOMAIN]["duplicate"] is owner_runtime


async def test_platform_setup_from_file_configuration_is_ignored(hass):
    async_add_entities = Mock()

    await async_setup_binary_sensor_platform(
        hass,
        {CONF_NAME: "From File", CONF_PLATFORM: "binary_sensor"},
        async_add_entities,
    )

    async_add_entities.assert_not_called()


async def test_config_entry_setup_loads_entities_from_options_only(hass):
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        title="ui - virtual_layer",
        data={ATTR_GROUP_NAME: "ui"},
        options={
            ATTR_DEVICES: {
                "Laundry": [
                    {
                        CONF_PLATFORM: "sensor",
                        CONF_NAME: "Washer Phase",
                        "initial_value": "idle",
                    },
                ],
            },
            ATTR_DEVICE_ATTRIBUTES: {
                "Laundry": {
                    ATTR_DEVICE_ID: "laundry-device-1",
                    CONF_NAME: "Laundry Device",
                    CONF_MANUFACTURER: "Acme",
                    CONF_MODEL: "Washer 9000",
                },
            },
        },
    )
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.virtual_layer._async_get_or_create_virtual_device_in_registry",
            AsyncMock(),
        ),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            AsyncMock(return_value=True),
        ) as forward_setups,
    ):
        assert await async_setup_entry(hass, entry) is True

    group_data = hass.data[COMPONENT_DOMAIN]["ui"]
    assert group_data[ATTR_CONFIG_ENTRY_ID] == entry.entry_id
    assert group_data[ATTR_DEVICES] == [
        {
            "device_id": "laundry-device-1",
            CONF_NAME: "Laundry Device",
            CONF_MANUFACTURER: "Acme",
            CONF_MODEL: "Washer 9000",
        },
    ]
    assert group_data[ATTR_ENTITIES]["sensor"][0][CONF_NAME] == "Washer Phase"
    assert group_data[ATTR_ENTITIES]["sensor"][0][ATTR_DEVICE_ID] == "laundry-device-1"
    assert group_data[ATTR_ENTITIES]["sensor"][0][CONF_MANUFACTURER] == "Acme"
    assert group_data[ATTR_ENTITIES]["sensor"][0]["initial_value"] == "idle"
    forward_setups.assert_awaited_once_with(entry, ["sensor"])


async def test_config_entry_setup_skips_forwarding_empty_platforms(hass):
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        title="empty - virtual_layer",
        data={ATTR_GROUP_NAME: "empty"},
        options={ATTR_DEVICES: {}},
    )
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.virtual_layer._async_get_or_create_virtual_device_in_registry",
            AsyncMock(),
        ),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            AsyncMock(return_value=True),
        ) as forward_setups,
    ):
        assert await async_setup_entry(hass, entry) is True

    forward_setups.assert_not_awaited()


@pytest.mark.parametrize(
    ("title", "data", "expected_group_name"),
    [
        ("Damaged legacy entry", {}, None),
        (
            " Spaced Device ",
            {ATTR_GROUP_NAME: " Spaced Device "},
            "Spaced Device",
        ),
    ],
)
async def test_config_entry_setup_recovers_and_normalizes_device_name(
    hass,
    title,
    data,
    expected_group_name,
):
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        title=title,
        data=dict(data),
        options={ATTR_DEVICES: {}},
    )
    entry.add_to_hass(hass)

    with patch.object(
        hass.config_entries,
        "async_forward_entry_setups",
        AsyncMock(return_value=True),
    ) as forward_setups:
        assert await async_setup_entry(hass, entry) is True

    group_name = expected_group_name or f"recovered_{entry.entry_id}"
    assert entry.data[ATTR_GROUP_NAME] == group_name
    assert entry.title == group_name
    assert hass.data[COMPONENT_DOMAIN][group_name][
        ATTR_CONFIG_ENTRY_ID
    ] == entry.entry_id
    forward_setups.assert_not_awaited()


async def test_setup_failure_cleans_runtime_state_and_listeners(hass):
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "failed"},
        options={
            ATTR_DEVICES: {
                "Failure Device": [{
                    CONF_PLATFORM: "tag",
                    CONF_NAME: "Failure Tag",
                    ATTR_ENTITY_ID: "tag.failure_tag",
                    CONF_VALUE_TEMPLATE: "{{ source }}",
                    CONF_TEMPLATE_SOURCES: {
                        "source": {
                            ATTR_ENTITY_ID: "sensor.failure_source",
                            CONF_ATTRIBUTE: "state",
                        },
                    },
                }],
            },
        },
    )
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.failure_source", "ready")

    with patch.object(
        hass.config_entries,
        "async_forward_entry_setups",
        AsyncMock(side_effect=RuntimeError("platform failed")),
    ), pytest.raises(RuntimeError, match="platform failed"):
        await async_setup_entry(hass, entry)

    assert "failed" not in hass.data[COMPONENT_DOMAIN]
    assert hass.states.get("tag.failure_tag") is None
    assert entry.entry_id not in hass.data.get(
        "virtual_layer_state_only_template_listeners",
        {},
    )
    assert entry.entry_id not in hass.data.get(
        "virtual_layer_entity_id_guard_listeners",
        {},
    )
    assert entry.entry_id not in hass.data.get(
        "virtual_layer_device_metadata_guard_listeners",
        {},
    )


async def test_number_entity_supports_native_service_and_virtual_state_clamping(hass):
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "numbers"},
        options={
            ATTR_DEVICES: {
                "Electrical Controls": [{
                    CONF_PLATFORM: "number",
                    CONF_NAME: "Current Limit",
                    ATTR_ENTITY_ID: "number.current_limit",
                    CONF_INITIAL_VALUE: "10",
                    CONF_MIN: 0,
                    CONF_MAX: 20,
                    CONF_PERSISTENT: False,
                }],
            },
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id) is True
    await hass.async_block_till_done()

    await hass.services.async_call(
        "number",
        "set_value",
        {ATTR_ENTITY_ID: "number.current_limit", "value": 15},
        blocking=True,
    )
    assert hass.states.get("number.current_limit").state == "15.0"

    await async_virtual_set_state_service(
        hass,
        SimpleNamespace(data={
            ATTR_ENTITY_ID: ["number.current_limit"],
            ATTR_VALUE: 99,
        }),
    )
    await hass.async_block_till_done()
    assert hass.states.get("number.current_limit").state == "20.0"

    with pytest.raises(ValueError, match="Number value"):
        await async_virtual_set_state_service(
            hass,
            SimpleNamespace(data={
                ATTR_ENTITY_ID: ["number.current_limit"],
                ATTR_VALUE: "not-a-number",
            }),
        )
    await hass.async_block_till_done()
    assert hass.states.get("number.current_limit").state == "20.0"


async def test_virtual_service_permissions_check_every_target_and_admin_operations(
    hass,
    monkeypatch,
):
    user = SimpleNamespace(
        id="limited-user",
        is_admin=False,
        permissions=SimpleNamespace(
            check_entity=lambda entity_id, _permission: entity_id == "sensor.allowed",
        ),
    )
    monkeypatch.setattr(hass.auth, "async_get_user", AsyncMock(return_value=user))
    allowed_call = SimpleNamespace(
        context=Context(user_id=user.id),
        data={ATTR_ENTITY_ID: ["sensor.allowed"]},
    )
    await _async_verify_target_entity_control(hass, allowed_call)

    denied_call = SimpleNamespace(
        context=Context(user_id=user.id),
        data={ATTR_ENTITY_ID: ["sensor.allowed", "sensor.denied"]},
    )
    with pytest.raises(Unauthorized):
        await _async_verify_target_entity_control(hass, denied_call)
    with pytest.raises(Unauthorized):
        await _async_verify_admin(hass, allowed_call)

    user.is_admin = True
    await _async_verify_admin(hass, allowed_call)

    system_call = SimpleNamespace(
        context=Context(),
        data={ATTR_ENTITY_ID: ["sensor.denied"]},
    )
    await _async_verify_target_entity_control(hass, system_call)
    await _async_verify_admin(hass, system_call)


async def test_backup_and_restore_services_are_not_registered(hass):
    hass.services.async_register(COMPONENT_DOMAIN, "backup_devices", Mock())
    hass.services.async_register(COMPONENT_DOMAIN, "restore_devices", Mock())

    assert await async_setup(hass, {}) is True

    assert not hass.services.has_service(COMPONENT_DOMAIN, "backup_devices")
    assert not hass.services.has_service(COMPONENT_DOMAIN, "restore_devices")


async def test_options_flow_finish_preserves_existing_options(hass):
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={ATTR_DEVICES: {"Laundry": []}},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(
        entry.entry_id,
        data={CONF_ACTION: ACTION_FINISH},
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"] == {ATTR_DEVICES: {"Laundry": []}}


async def test_options_flow_manages_shared_device_metadata_without_editing_entities(hass):
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={
            ATTR_DEVICES: {
                "Laundry": [
                    {CONF_PLATFORM: "sensor", CONF_NAME: "Washer Phase"},
                    {CONF_PLATFORM: "binary_sensor", CONF_NAME: "Washer Door"},
                ],
            },
            ATTR_DEVICE_ATTRIBUTES: {
                "Laundry": {
                    ATTR_DEVICE_ID: "laundry-old",
                    CONF_NAME: "Laundry",
                },
            },
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(
        entry.entry_id,
        data={CONF_ACTION: ACTION_MANAGE_DEVICES},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "select_device"
    assert result["data_schema"]({CONF_MANAGED_DEVICE_NAME: "Laundry"}) == {
        CONF_MANAGED_DEVICE_NAME: "Laundry",
    }

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_MANAGED_DEVICE_NAME: "Laundry"},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "edit_device"
    defaults = _flatten_entity_form_sections(result["data_schema"]({}))
    assert defaults[CONF_DEVICE_ID] == "laundry-old"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            **defaults,
            CONF_DEVICE_NAME: "Laundry Room",
            CONF_DEVICE_ID: "laundry-new",
            CONF_DEVICE_MANUFACTURER: "Acme",
            CONF_DEVICE_MODEL: "Washer 9000",
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert "Laundry" not in result["data"][ATTR_DEVICES]
    assert len(result["data"][ATTR_DEVICES]["laundry-new"]) == 2
    assert result["data"][ATTR_DEVICE_ATTRIBUTES]["laundry-new"] == {
        ATTR_DEVICE_ID: "laundry-new",
        CONF_NAME: "Laundry Room",
        CONF_MANUFACTURER: "Acme",
        CONF_MODEL: "Washer 9000",
    }


async def test_options_flow_allows_device_name_collision_with_different_id(hass):
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={
            ATTR_DEVICES: {
                "Laundry": [{CONF_PLATFORM: "sensor", CONF_NAME: "Washer"}],
                "HVAC": [{CONF_PLATFORM: "climate", CONF_NAME: "Thermostat"}],
            },
            ATTR_DEVICE_ATTRIBUTES: {
                "Laundry": {ATTR_DEVICE_ID: "laundry-1", CONF_NAME: "Laundry"},
                "HVAC": {ATTR_DEVICE_ID: "hvac-1", CONF_NAME: "HVAC"},
            },
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(
        entry.entry_id,
        data={CONF_ACTION: ACTION_MANAGE_DEVICES},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_MANAGED_DEVICE_NAME: "Laundry"},
    )
    defaults = _flatten_entity_form_sections(result["data_schema"]({}))
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            **defaults,
            CONF_DEVICE_NAME: "HVAC",
            CONF_DEVICE_ID: "different-id",
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert set(result["data"][ATTR_DEVICES]) == {"HVAC", "different-id"}
    assert result["data"][ATTR_DEVICE_ATTRIBUTES]["HVAC"][ATTR_DEVICE_ID] == "hvac-1"
    assert result["data"][ATTR_DEVICE_ATTRIBUTES]["different-id"] == {
        ATTR_DEVICE_ID: "different-id",
        CONF_NAME: "HVAC",
    }


async def test_options_flow_can_delete_multiple_entities(hass):
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={
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
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(
        entry.entry_id,
        data={CONF_ACTION: ACTION_DELETE_ENTITY},
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "delete_entities"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_ENTITY_KEYS: [
                _entity_key("Laundry", 0),
                _entity_key("HVAC", 0),
            ],
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][ATTR_DEVICES] == {
        "Laundry": [
            {CONF_PLATFORM: "binary_sensor", CONF_NAME: "Washer Door"},
        ],
    }
    assert result["data"][ATTR_DEVICE_ATTRIBUTES] == {
        "Laundry": {
            ATTR_DEVICE_ID: "laundry-1",
            CONF_NAME: "Laundry",
        },
    }


async def test_options_flow_can_delete_a_malformed_device_group(hass):
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={
            ATTR_DEVICES: {
                "Healthy": [{CONF_PLATFORM: "sensor", CONF_NAME: "Power"}],
                "Broken": "not-an-entity-list",
            },
            ATTR_DEVICE_ATTRIBUTES: {
                "Healthy": {ATTR_DEVICE_ID: "healthy-1"},
                "Broken": {ATTR_DEVICE_ID: "broken-1"},
            },
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(
        entry.entry_id,
        data={CONF_ACTION: ACTION_DELETE_DEVICE},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "delete_device"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_MANAGED_DEVICE_NAME: "Broken"},
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][ATTR_DEVICES] == {
        "Healthy": [{CONF_PLATFORM: "sensor", CONF_NAME: "Power"}],
    }
    assert result["data"][ATTR_DEVICE_ATTRIBUTES] == {
        "Healthy": {ATTR_DEVICE_ID: "healthy-1"},
    }


async def test_options_flow_can_delete_the_only_malformed_entity(hass):
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={
            ATTR_DEVICES: {"Broken": ["not-an-entity"]},
            ATTR_DEVICE_ATTRIBUTES: {
                "Broken": {ATTR_DEVICE_ID: "broken-1", CONF_NAME: "Broken"},
            },
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(
        entry.entry_id,
        data={CONF_ACTION: ACTION_DELETE_ENTITY},
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "delete_entities"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_ENTITY_KEYS: [_entity_key("Broken", 0)]},
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][ATTR_DEVICES] == {}
    assert result["data"][ATTR_DEVICE_ATTRIBUTES] == {}


async def test_options_flow_can_edit_existing_entity(hass):
    hass.states.async_set("sensor.washer_power", "idle")
    hass.states.async_set("binary_sensor.washer_door", "off")
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={
            ATTR_DEVICES: {
                "Laundry": [
                    {
                        CONF_PLATFORM: "sensor",
                        CONF_NAME: "Washer Phase",
                        CONF_INITIAL_VALUE: "idle",
                        CONF_SOURCE_ENTITIES: [
                            "sensor.washer_power",
                            "binary_sensor.washer_door",
                        ],
                    },
                ],
            },
            ATTR_DEVICE_ATTRIBUTES: {
                "Laundry": {
                    ATTR_DEVICE_ID: "laundry-old",
                    CONF_NAME: "Laundry",
                },
            },
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(
        entry.entry_id,
        data={CONF_ACTION: ACTION_EDIT_ENTITY},
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "select_entity"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_ENTITY_KEY: _entity_key("Laundry", 0)},
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "edit_entity_source"
    source_defaults = _flatten_entity_form_sections(result["data_schema"]({}))
    assert source_defaults[CONF_REFERENCE_ENTITY_ID] == [
        "sensor.washer_power",
        "binary_sensor.washer_door",
    ]

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {},
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "edit_entity_helper"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_HELPER_UPDATE_MODE: HELPER_UPDATE_AUTO},
    )
    assert result["step_id"] == "edit_entity"
    edit_defaults = _flatten_entity_form_sections(result["data_schema"]({}))
    assert edit_defaults[CONF_ENTITY_NAME] == "Washer Phase"
    assert edit_defaults[ATTR_ENTITY_ID] == "sensor.washer_phase"
    assert edit_defaults[CONF_PLATFORM] == "sensor"
    assert edit_defaults[CONF_SOURCE_ENTITIES_TEXT] == (
        "sensor.washer_power\nbinary_sensor.washer_door"
    )
    assert CONF_VALUE_TEMPLATE in edit_defaults

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_DEVICE_NAME: "Laundry",
            CONF_DEVICE_ID: "laundry-updated",
            CONF_DEVICE_MANUFACTURER: "Acme",
            CONF_DEVICE_MODEL: "Washer 9000",
            CONF_ENTITY_NAME: "Washer Status",
            ATTR_ENTITY_ID: "sensor.washer_status",
            CONF_PLATFORM: "sensor",
            CONF_INITIAL_VALUE: "running",
            CONF_INITIAL_AVAILABILITY: True,
            CONF_PERSISTENT: False,
            CONF_SOURCE_ENTITIES_TEXT: "sensor.washer_power",
            CONF_TEMPLATE_SOURCES_JSON: "",
            CONF_PULL_INTERVAL: 10,
            CONF_VALUE_TEMPLATE: "{{ states('sensor.washer_power') }}",
            CONF_AVAILABILITY_TEMPLATE: "",
            "attributes_json": "",
            "attribute_sources_json": "",
            "attribute_templates_json": "",
        },
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "edit_entity_type"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_TARGET_ENTITY_TYPE: "sensor"},
    )
    assert result["step_id"] == "edit_entity_helper"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_HELPER_UPDATE_MODE: HELPER_UPDATE_AUTO},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        _flatten_entity_form_sections(result["data_schema"]({})),
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    stored_entities = result["data"][ATTR_DEVICES]["laundry-updated"]
    assert stored_entities[0].pop(ATTR_ENTITY_KEY)
    assert isinstance(
        stored_entities[0].pop("auto_helper"),
        dict,
    )
    assert stored_entities == [
        {
            CONF_PLATFORM: "sensor",
            CONF_NAME: "Washer Status",
            ATTR_ENTITY_ID: "sensor.washer_status",
            CONF_INITIAL_VALUE: "running",
            CONF_INITIAL_AVAILABILITY: True,
            CONF_PERSISTENT: False,
            CONF_SOURCE_ENTITIES: ["sensor.washer_power"],
            CONF_PULL_INTERVAL: 10,
            CONF_VALUE_TEMPLATE: "{{ states('sensor.washer_power') }}",
        },
    ]
    assert result["data"][ATTR_DEVICE_ATTRIBUTES]["laundry-updated"] == {
        ATTR_DEVICE_ID: "laundry-updated",
        CONF_NAME: "Laundry",
        CONF_MANUFACTURER: "Acme",
        CONF_MODEL: "Washer 9000",
    }


async def test_options_flow_aligns_entity_id_when_domain_is_edited(hass):
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={
            ATTR_DEVICES: {
                "Virtual": [{
                    CONF_PLATFORM: "sensor",
                    CONF_NAME: "Mode",
                    ATTR_ENTITY_ID: "sensor.virtual_mode",
                    CONF_INITIAL_VALUE: "idle",
                    CONF_INITIAL_AVAILABILITY: True,
                    CONF_PERSISTENT: True,
                }],
            },
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(
        entry.entry_id,
        data={CONF_ACTION: ACTION_EDIT_ENTITY},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_ENTITY_KEY: _entity_key("Virtual", 0)},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {},
    )
    defaults = _flatten_entity_form_sections(result["data_schema"]({}))
    defaults[CONF_PLATFORM] = "binary_sensor"
    defaults[CONF_INITIAL_VALUE] = "off"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        defaults,
    )
    if result["type"] == FlowResultType.FORM:
        assert result["step_id"] == "edit_entity"
        defaults = _flatten_entity_form_sections(result["data_schema"]({}))
        assert "device_class" in defaults[CONF_NATIVE_VALUE_TEMPLATES]
        defaults[CONF_VALUE_TEMPLATE] = "{{ 'on' }}"
        defaults[CONF_NATIVE_VALUE_TEMPLATES]["device_class"] = "{{ 'door' }}"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            defaults,
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    entity = _first_stored_entity(result)
    assert entity[CONF_PLATFORM] == "binary_sensor"
    assert entity[ATTR_ENTITY_ID] == "binary_sensor.virtual_mode"
    assert entity[CONF_VALUE_TEMPLATE] == "{{ 'on' }}"
    assert entity[CONF_NATIVE_TEMPLATES]["device_class"] == "{{ 'door' }}"
    assert CONF_AUTO_HELPER not in entity


@pytest.mark.parametrize(
    ("auto_helper_marker", "old_sources", "new_sources"),
    [
        (
            "legacy_profile",
            ["binary_sensor.door_6"],
            ["binary_sensor.door_7", "binary_sensor.door_6"],
        ),
        (
            False,
            ["binary_sensor.door_6", "binary_sensor.door_5"],
            ["binary_sensor.door_7", "binary_sensor.door_6"],
        ),
        (False, ["binary_sensor.door_6"], []),
    ],
)
async def test_options_flow_refreshes_generated_helper_when_sources_change(
    hass,
    auto_helper_marker,
    old_sources,
    new_sources,
):
    for entity_id in {*old_sources, *new_sources}:
        hass.states.async_set(entity_id, "off", {"device_class": "door"})

    old_defaults = _reference_entity_defaults(hass, old_sources)
    old_template_sources = json.loads(old_defaults[CONF_TEMPLATE_SOURCES_JSON])
    legacy_profile = _auto_helper_profile(old_defaults)
    legacy_profile[CONF_TEMPLATE_SOURCES_JSON] = old_defaults[
        CONF_TEMPLATE_SOURCES_JSON
    ]
    stored_auto_helper = (
        legacy_profile
        if auto_helper_marker == "legacy_profile"
        else auto_helper_marker
    )
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={
            ATTR_DEVICES: {
                "Doors": [{
                    CONF_PLATFORM: "binary_sensor",
                    CONF_NAME: "Combined Doors",
                    CONF_INITIAL_VALUE: old_defaults[CONF_INITIAL_VALUE],
                    CONF_INITIAL_AVAILABILITY: True,
                    CONF_PERSISTENT: True,
                    CONF_SOURCE_ENTITIES: old_sources,
                    CONF_TEMPLATE_SOURCES: {
                        variable_name: {
                            ATTR_ENTITY_ID: source_entity_id,
                            CONF_ATTRIBUTE: "state",
                        }
                        for variable_name, source_entity_id
                        in old_template_sources.items()
                    },
                    CONF_VALUE_TEMPLATE: old_defaults[CONF_VALUE_TEMPLATE],
                    CONF_AUTO_HELPER: stored_auto_helper,
                }],
            },
        },
    )
    entry.add_to_hass(hass)
    if auto_helper_marker is False:
        # A changed source state makes the generated initial value differ from
        # the stored value. That must not turn an untouched template custom.
        for entity_id in old_sources:
            hass.states.async_set(entity_id, "on", {"device_class": "door"})

    result = await hass.config_entries.options.async_init(
        entry.entry_id,
        data={CONF_ACTION: ACTION_EDIT_ENTITY},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_ENTITY_KEY: _entity_key("Doors", 0)},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_REFERENCE_ENTITY_ID: new_sources},
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "edit_entity_helper"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_HELPER_UPDATE_MODE: HELPER_UPDATE_AUTO},
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "edit_entity"
    defaults = _flatten_entity_form_sections(result["data_schema"]({}))
    expected_helper = _reference_entity_defaults(hass, new_sources)
    assert defaults[CONF_SOURCE_ENTITIES_TEXT] == "\n".join(new_sources)
    assert defaults[CONF_TEMPLATE_SOURCES_JSON] == expected_helper.get(
        CONF_TEMPLATE_SOURCES_JSON,
        "",
    )
    assert defaults[CONF_VALUE_TEMPLATE] == expected_helper.get(CONF_VALUE_TEMPLATE, "")

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        defaults,
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    entity = _first_stored_entity(result)
    assert entity.get(CONF_SOURCE_ENTITIES, []) == new_sources
    assert entity.get(CONF_VALUE_TEMPLATE, "") == expected_helper.get(
        CONF_VALUE_TEMPLATE,
        "",
    )
    assert entity[CONF_AUTO_HELPER] is not False
    if not new_sources:
        assert entity[CONF_AUTO_HELPER][CONF_VALUE_TEMPLATE] == ""
        assert entity[CONF_AUTO_HELPER][CONF_TEMPLATE_SOURCES_JSON] == ""


@pytest.mark.parametrize("auto_helper_marker", ["new_profile", False])
async def test_options_flow_recovers_stale_helper_after_partial_source_update(
    hass,
    auto_helper_marker,
):
    """Recover an old generated template after sources were already replaced."""
    old_sources = [
        "binary_sensor.door_sensor_5_contact",
        "binary_sensor.door_sensor_6_contact",
    ]
    new_sources = [
        "binary_sensor.door_sensor_7_contact",
        "binary_sensor.door_sensor_6_contact",
    ]
    for entity_id in {*old_sources, *new_sources}:
        hass.states.async_set(entity_id, "off", {"device_class": "door"})

    old_defaults = _reference_entity_defaults(hass, old_sources)
    new_defaults = _reference_entity_defaults(hass, new_sources)
    old_template_sources = json.loads(old_defaults[CONF_TEMPLATE_SOURCES_JSON])
    stored_auto_helper = (
        _auto_helper_profile(new_defaults)
        if auto_helper_marker == "new_profile"
        else auto_helper_marker
    )
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={
            ATTR_DEVICES: {
                "Doors": [{
                    CONF_PLATFORM: "binary_sensor",
                    CONF_NAME: "Combined Doors",
                    CONF_INITIAL_VALUE: old_defaults[CONF_INITIAL_VALUE],
                    CONF_INITIAL_AVAILABILITY: True,
                    CONF_PERSISTENT: True,
                    # Simulate an older failed edit: the selection and marker
                    # are new, while the generated templates are still old.
                    CONF_SOURCE_ENTITIES: new_sources,
                    CONF_TEMPLATE_SOURCES: {
                        variable_name: {
                            ATTR_ENTITY_ID: source_entity_id,
                            CONF_ATTRIBUTE: "state",
                        }
                        for variable_name, source_entity_id
                        in old_template_sources.items()
                    },
                    CONF_VALUE_TEMPLATE: old_defaults[CONF_VALUE_TEMPLATE],
                    CONF_AUTO_HELPER: stored_auto_helper,
                }],
            },
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(
        entry.entry_id,
        data={CONF_ACTION: ACTION_EDIT_ENTITY},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_ENTITY_KEY: _entity_key("Doors", 0)},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_REFERENCE_ENTITY_ID: new_sources},
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "edit_entity_helper"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_HELPER_UPDATE_MODE: HELPER_UPDATE_AUTO},
    )
    assert result["step_id"] == "edit_entity"
    defaults = _flatten_entity_form_sections(result["data_schema"]({}))
    assert defaults[CONF_SOURCE_ENTITIES_TEXT] == "\n".join(new_sources)
    assert defaults[CONF_TEMPLATE_SOURCES_JSON] == new_defaults[
        CONF_TEMPLATE_SOURCES_JSON
    ]
    assert defaults[CONF_VALUE_TEMPLATE] == new_defaults[CONF_VALUE_TEMPLATE]
    assert "door_sensor_5_contact" not in defaults[CONF_TEMPLATE_SOURCES_JSON]
    assert "door_sensor_7_contact" in defaults[CONF_TEMPLATE_SOURCES_JSON]

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        defaults,
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    entity = _first_stored_entity(result)
    assert entity[CONF_SOURCE_ENTITIES] == new_sources
    assert {
        source[ATTR_ENTITY_ID]
        for source in entity[CONF_TEMPLATE_SOURCES].values()
    } == set(new_sources)
    assert entity[CONF_VALUE_TEMPLATE] == new_defaults[CONF_VALUE_TEMPLATE]


async def test_options_flow_recovers_sorted_legacy_boolean_or_helper(hass):
    """Recognize the old OR helper even when JSON sorting changed source order."""
    old_sources = [
        "binary_sensor.door_sensor_6_contact",
        "binary_sensor.door_sensor_5_contact",
    ]
    new_sources = [
        "binary_sensor.door_sensor_7_contact",
        "binary_sensor.door_sensor_6_contact",
    ]
    for entity_id in {*old_sources, *new_sources}:
        hass.states.async_set(entity_id, "off", {"device_class": "door"})

    old_defaults = _reference_entity_defaults(hass, old_sources)
    new_defaults = _reference_entity_defaults(hass, new_sources)
    old_template_sources = json.loads(old_defaults[CONF_TEMPLATE_SOURCES_JSON])
    old_or_template = (
        "{{ ((door_sensor_6_contact | lower) in "
        "['1', 'on', 'open', 'true', 'unlocked', 'yes']) or "
        "((door_sensor_5_contact | lower) in "
        "['1', 'on', 'open', 'true', 'unlocked', 'yes']) }}"
    )
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={
            ATTR_DEVICES: {
                "Doors": [{
                    CONF_PLATFORM: "binary_sensor",
                    CONF_NAME: "Combined Doors",
                    CONF_INITIAL_VALUE: "off",
                    CONF_INITIAL_AVAILABILITY: True,
                    CONF_PERSISTENT: True,
                    CONF_SOURCE_ENTITIES: new_sources,
                    # JSON round-tripping sorts these as Door 5, then Door 6.
                    CONF_TEMPLATE_SOURCES: {
                        variable_name: {
                            ATTR_ENTITY_ID: source_entity_id,
                            CONF_ATTRIBUTE: "state",
                        }
                        for variable_name, source_entity_id
                        in old_template_sources.items()
                    },
                    # The generated expression retained Door 6, then Door 5.
                    CONF_VALUE_TEMPLATE: old_or_template,
                    CONF_AUTO_HELPER: _auto_helper_profile(new_defaults),
                }],
            },
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(
        entry.entry_id,
        data={CONF_ACTION: ACTION_EDIT_ENTITY},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_ENTITY_KEY: _entity_key("Doors", 0)},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_REFERENCE_ENTITY_ID: new_sources},
    )

    assert result["step_id"] == "edit_entity_helper"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_HELPER_UPDATE_MODE: HELPER_UPDATE_AUTO},
    )
    assert result["step_id"] == "edit_entity"
    defaults = _flatten_entity_form_sections(result["data_schema"]({}))
    assert defaults[CONF_TEMPLATE_SOURCES_JSON] == new_defaults[
        CONF_TEMPLATE_SOURCES_JSON
    ]
    assert defaults[CONF_VALUE_TEMPLATE] == new_defaults[CONF_VALUE_TEMPLATE]
    assert "door_sensor_5_contact" not in defaults[CONF_VALUE_TEMPLATE]
    assert "door_sensor_7_contact" in defaults[CONF_VALUE_TEMPLATE]

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        defaults,
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    entity = _first_stored_entity(result)
    assert entity[CONF_SOURCE_ENTITIES] == new_sources
    assert entity[CONF_VALUE_TEMPLATE] == new_defaults[CONF_VALUE_TEMPLATE]
    assert {
        source[ATTR_ENTITY_ID]
        for source in entity[CONF_TEMPLATE_SOURCES].values()
    } == set(new_sources)


async def test_edit_same_sources_can_force_helpers_from_current_source_attributes(hass):
    sources = ["sensor.washer_one", "sensor.washer_two"]
    for entity_id, power in zip(sources, (10, 20), strict=True):
        hass.states.async_set(entity_id, "idle", {"power": power})
    generated = _reference_entity_defaults(hass, sources)

    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={
            ATTR_DEVICES: {
                "Laundry": [{
                    CONF_PLATFORM: "sensor",
                    CONF_NAME: "Combined Washers",
                    CONF_INITIAL_VALUE: generated[CONF_INITIAL_VALUE],
                    CONF_INITIAL_AVAILABILITY: True,
                    CONF_PERSISTENT: True,
                    CONF_SOURCE_ENTITIES: sources,
                    CONF_TEMPLATE_SOURCES: {
                        variable_name: {
                            ATTR_ENTITY_ID: source_entity_id,
                            CONF_ATTRIBUTE: "state",
                        }
                        for variable_name, source_entity_id in json.loads(
                            generated[CONF_TEMPLATE_SOURCES_JSON]
                        ).items()
                    },
                    CONF_VALUE_TEMPLATE: generated[CONF_VALUE_TEMPLATE],
                    CONF_ATTRIBUTE_TEMPLATES: json.loads(
                        generated[CONF_ATTRIBUTE_TEMPLATES_JSON]
                    ),
                    CONF_AUTO_HELPER: _auto_helper_profile(generated),
                }],
            },
        },
    )
    entry.add_to_hass(hass)

    for entity_id, power in zip(sources, (10, 20), strict=True):
        hass.states.async_set(
            entity_id,
            "idle",
            {"power": power, "energy": power * 5},
        )

    result = await hass.config_entries.options.async_init(
        entry.entry_id,
        data={CONF_ACTION: ACTION_EDIT_ENTITY},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_ENTITY_KEY: _entity_key("Laundry", 0)},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_REFERENCE_ENTITY_ID: sources},
    )

    assert result["step_id"] == "edit_entity_helper"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_HELPER_UPDATE_MODE: HELPER_UPDATE_FORCE},
    )
    defaults = _flatten_entity_form_sections(result["data_schema"]({}))
    templates = json.loads(defaults[CONF_ATTRIBUTE_TEMPLATES_JSON])
    assert "energy" in templates


@pytest.mark.parametrize(("helper_mode", "preserves_custom"), [
    (HELPER_UPDATE_AUTO, True),
    (HELPER_UPDATE_FORCE, False),
])
async def test_options_flow_handles_custom_template_when_sources_change(
    hass,
    helper_mode,
    preserves_custom,
):
    old_sources = ["binary_sensor.door_6", "binary_sensor.door_5"]
    new_sources = ["binary_sensor.door_7", "binary_sensor.door_6"]
    for entity_id in {*old_sources, *new_sources}:
        hass.states.async_set(entity_id, "off", {"device_class": "door"})

    generated = _reference_entity_defaults(hass, old_sources)
    template_sources = json.loads(generated[CONF_TEMPLATE_SOURCES_JSON])
    custom_template = (
        generated[CONF_VALUE_TEMPLATE][:-3]
        + " and (door_5 != 'unavailable') }}"
    )
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={
            ATTR_DEVICES: {
                "Doors": [{
                    CONF_PLATFORM: "binary_sensor",
                    CONF_NAME: "Custom Combined Doors",
                    CONF_INITIAL_VALUE: generated[CONF_INITIAL_VALUE],
                    CONF_INITIAL_AVAILABILITY: True,
                    CONF_PERSISTENT: True,
                    CONF_SOURCE_ENTITIES: old_sources,
                    CONF_TEMPLATE_SOURCES: {
                        variable_name: {
                            ATTR_ENTITY_ID: source_entity_id,
                            CONF_ATTRIBUTE: "state",
                        }
                        for variable_name, source_entity_id
                        in template_sources.items()
                    },
                    CONF_VALUE_TEMPLATE: custom_template,
                    CONF_AUTO_HELPER: _auto_helper_profile(generated),
                }],
            },
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(
        entry.entry_id,
        data={CONF_ACTION: ACTION_EDIT_ENTITY},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_ENTITY_KEY: _entity_key("Doors", 0)},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_REFERENCE_ENTITY_ID: new_sources},
    )

    assert result["step_id"] == "edit_entity_helper"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_HELPER_UPDATE_MODE: helper_mode},
    )

    defaults = _flatten_entity_form_sections(result["data_schema"]({}))
    assert defaults[CONF_SOURCE_ENTITIES_TEXT] == "\n".join(new_sources)
    expected_template = (
        custom_template
        if preserves_custom
        else _reference_entity_defaults(hass, new_sources)[CONF_VALUE_TEMPLATE]
    )
    assert defaults[CONF_VALUE_TEMPLATE] == expected_template
    assert ("door_5" in defaults[CONF_TEMPLATE_SOURCES_JSON]) is preserves_custom
    assert ("door_7" in defaults[CONF_TEMPLATE_SOURCES_JSON]) is not preserves_custom

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        defaults,
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    entity = _first_stored_entity(result)
    assert entity[CONF_SOURCE_ENTITIES] == new_sources
    assert entity[CONF_VALUE_TEMPLATE] == expected_template
    assert ("door_5" in entity[CONF_TEMPLATE_SOURCES]) is preserves_custom


async def test_edit_form_source_change_requires_helper_policy(hass):
    old_sources = ["binary_sensor.door_5", "binary_sensor.door_6"]
    new_sources = ["binary_sensor.door_6", "binary_sensor.door_7"]
    for entity_id in {*old_sources, *new_sources}:
        hass.states.async_set(entity_id, "off", {"device_class": "door"})

    generated = _reference_entity_defaults(hass, old_sources)
    template_sources = json.loads(generated[CONF_TEMPLATE_SOURCES_JSON])
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={
            ATTR_DEVICES: {
                "Doors": [{
                    CONF_PLATFORM: "binary_sensor",
                    CONF_NAME: "Combined Doors",
                    CONF_INITIAL_VALUE: generated[CONF_INITIAL_VALUE],
                    CONF_INITIAL_AVAILABILITY: True,
                    CONF_PERSISTENT: True,
                    CONF_SOURCE_ENTITIES: old_sources,
                    CONF_TEMPLATE_SOURCES: {
                        variable_name: {
                            ATTR_ENTITY_ID: source_entity_id,
                            CONF_ATTRIBUTE: "state",
                        }
                        for variable_name, source_entity_id
                        in template_sources.items()
                    },
                    CONF_VALUE_TEMPLATE: generated[CONF_VALUE_TEMPLATE],
                    CONF_AUTO_HELPER: _auto_helper_profile(generated),
                }],
            },
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(
        entry.entry_id,
        data={CONF_ACTION: ACTION_EDIT_ENTITY},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_ENTITY_KEY: _entity_key("Doors", 0)},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {},
    )
    assert result["step_id"] == "edit_entity_helper"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_HELPER_UPDATE_MODE: HELPER_UPDATE_AUTO},
    )
    defaults = _flatten_entity_form_sections(result["data_schema"]({}))
    defaults[CONF_SOURCE_ENTITIES_TEXT] = "\n".join(new_sources)
    defaults[CONF_ENTITY_NAME] = "Renamed Combined Doors"
    defaults[CONF_DEVICE_MODEL] = "Updated Model"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        defaults,
    )
    assert result["step_id"] == "edit_entity_helper"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_HELPER_UPDATE_MODE: HELPER_UPDATE_KEEP},
    )
    edited = _flatten_entity_form_sections(result["data_schema"]({}))
    assert edited[CONF_SOURCE_ENTITIES_TEXT] == "\n".join(new_sources)
    assert edited[CONF_ENTITY_NAME] == "Renamed Combined Doors"
    assert edited[CONF_DEVICE_MODEL] == "Updated Model"
    assert edited[CONF_VALUE_TEMPLATE] == generated[CONF_VALUE_TEMPLATE]
    assert edited[CONF_TEMPLATE_SOURCES_JSON] == generated[
        CONF_TEMPLATE_SOURCES_JSON
    ]

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        edited,
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    entity = _first_stored_entity(result)
    assert entity[CONF_SOURCE_ENTITIES] == new_sources
    assert entity[CONF_VALUE_TEMPLATE] == generated[CONF_VALUE_TEMPLATE]
    assert entity[CONF_AUTO_HELPER][CONF_VALUE_TEMPLATE] == generated[
        CONF_VALUE_TEMPLATE
    ]


async def test_repeated_source_changes_regenerate_from_latest_helper_baseline(hass):
    first_sources = ["binary_sensor.door_5", "binary_sensor.door_6"]
    second_sources = ["binary_sensor.door_6", "binary_sensor.door_7"]
    final_sources = ["binary_sensor.door_7", "binary_sensor.door_8"]
    for entity_id in {*first_sources, *second_sources, *final_sources}:
        hass.states.async_set(entity_id, "off", {"device_class": "door"})

    generated = _reference_entity_defaults(hass, first_sources)
    template_sources = json.loads(generated[CONF_TEMPLATE_SOURCES_JSON])
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={
            ATTR_DEVICES: {
                "Doors": [{
                    CONF_PLATFORM: "binary_sensor",
                    CONF_NAME: "Combined Doors",
                    CONF_INITIAL_VALUE: "off",
                    CONF_SOURCE_ENTITIES: first_sources,
                    CONF_TEMPLATE_SOURCES: {
                        name: {
                            ATTR_ENTITY_ID: entity_id,
                            CONF_ATTRIBUTE: "state",
                        }
                        for name, entity_id in template_sources.items()
                    },
                    CONF_VALUE_TEMPLATE: generated[CONF_VALUE_TEMPLATE],
                    CONF_AUTO_HELPER: _auto_helper_profile(generated),
                }],
            },
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(
        entry.entry_id,
        data={CONF_ACTION: ACTION_EDIT_ENTITY},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_ENTITY_KEY: _entity_key("Doors", 0)},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_REFERENCE_ENTITY_ID: second_sources},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_HELPER_UPDATE_MODE: HELPER_UPDATE_AUTO},
    )
    defaults = _flatten_entity_form_sections(result["data_schema"]({}))
    assert "door_7" in defaults[CONF_VALUE_TEMPLATE]

    defaults[CONF_SOURCE_ENTITIES_TEXT] = "\n".join(final_sources)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        defaults,
    )
    assert result["step_id"] == "edit_entity_helper"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_HELPER_UPDATE_MODE: HELPER_UPDATE_AUTO},
    )
    final_defaults = _flatten_entity_form_sections(result["data_schema"]({}))
    expected = _reference_entity_defaults(hass, final_sources)
    assert final_defaults[CONF_VALUE_TEMPLATE] == expected[CONF_VALUE_TEMPLATE]
    assert final_defaults[CONF_TEMPLATE_SOURCES_JSON] == expected[
        CONF_TEMPLATE_SOURCES_JSON
    ]
    assert "door_6" not in final_defaults[CONF_VALUE_TEMPLATE]


async def test_edit_form_rejects_multiple_nonmergeable_media_sources(hass):
    hass.states.async_set("camera.front", "idle")
    hass.states.async_set("camera.back", "idle")
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={
            ATTR_DEVICES: {
                "Media": [{
                    CONF_PLATFORM: "sensor",
                    CONF_NAME: "Media State",
                    CONF_INITIAL_VALUE: "idle",
                }],
            },
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(
        entry.entry_id,
        data={CONF_ACTION: ACTION_EDIT_ENTITY},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_ENTITY_KEY: _entity_key("Media", 0)},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {},
    )
    defaults = _flatten_entity_form_sections(result["data_schema"]({}))
    defaults[CONF_SOURCE_ENTITIES_TEXT] = "camera.front\ncamera.back"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        defaults,
    )

    assert result["step_id"] == "edit_entity"
    assert result["errors"] == {
        CONF_SOURCE_ENTITIES_TEXT: "invalid_entity_id",
    }


async def test_options_flow_refreshes_untouched_native_jinja_and_keeps_custom_field(
    hass,
):
    hass.states.async_set(
        "climate.old_unit",
        "cool",
        {"hvac_modes": ["off", "cool"], "fan_mode": "auto"},
    )
    hass.states.async_set(
        "climate.new_unit",
        "heat",
        {"hvac_modes": ["off", "heat"], "fan_mode": "high"},
    )
    generated = _reference_entity_defaults(hass, ["climate.old_unit"])
    generated_native = generated[CONF_NATIVE_VALUE_TEMPLATES]
    stored_native = {**generated_native, "fan_mode": "{{ 'quiet' }}"}
    template_sources = json.loads(generated[CONF_TEMPLATE_SOURCES_JSON])
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={
            ATTR_DEVICES: {
                "HVAC": [{
                    CONF_PLATFORM: "climate",
                    CONF_NAME: "Combined HVAC",
                    CONF_INITIAL_VALUE: "cool",
                    CONF_INITIAL_AVAILABILITY: True,
                    CONF_PERSISTENT: True,
                    CONF_SOURCE_ENTITIES: ["climate.old_unit"],
                    CONF_TEMPLATE_SOURCES: {
                        variable_name: {
                            ATTR_ENTITY_ID: source_entity_id,
                            CONF_ATTRIBUTE: "state",
                        }
                        for variable_name, source_entity_id
                        in template_sources.items()
                    },
                    CONF_VALUE_TEMPLATE: generated[CONF_VALUE_TEMPLATE],
                    CONF_NATIVE_TEMPLATES: stored_native,
                    CONF_AUTO_HELPER: _auto_helper_profile(generated),
                }],
            },
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(
        entry.entry_id,
        data={CONF_ACTION: ACTION_EDIT_ENTITY},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_ENTITY_KEY: _entity_key("HVAC", 0)},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_REFERENCE_ENTITY_ID: ["climate.new_unit"]},
    )
    assert result["step_id"] == "edit_entity_type"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_TARGET_ENTITY_TYPE: "climate"},
    )
    assert result["step_id"] == "edit_entity_helper"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_HELPER_UPDATE_MODE: HELPER_UPDATE_AUTO},
    )

    defaults = _flatten_entity_form_sections(result["data_schema"]({}))
    native_templates = defaults[CONF_NATIVE_VALUE_TEMPLATES]
    expected = _reference_entity_defaults(hass, ["climate.new_unit"])
    assert native_templates["hvac_mode"] == expected[
        CONF_NATIVE_VALUE_TEMPLATES
    ]["hvac_mode"]
    assert native_templates["hvac_modes"] == expected[
        CONF_NATIVE_VALUE_TEMPLATES
    ]["hvac_modes"]
    assert native_templates["fan_mode"] == "{{ 'quiet' }}"


async def test_options_flow_refreshes_entity_id_when_source_domain_changes(hass):
    hass.states.async_set("sensor.old_value", "20")
    hass.states.async_set("binary_sensor.new_door", "off")
    old_defaults = _reference_entity_defaults(hass, ["sensor.old_value"])
    old_template_sources = json.loads(old_defaults[CONF_TEMPLATE_SOURCES_JSON])
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={
            ATTR_DEVICES: {
                "Combined": [{
                    CONF_PLATFORM: "sensor",
                    CONF_NAME: "Combined State",
                    ATTR_ENTITY_ID: "sensor.combined_state",
                    CONF_INITIAL_VALUE: old_defaults[CONF_INITIAL_VALUE],
                    CONF_INITIAL_AVAILABILITY: True,
                    CONF_PERSISTENT: True,
                    CONF_SOURCE_ENTITIES: ["sensor.old_value"],
                    CONF_TEMPLATE_SOURCES: {
                        variable_name: {
                            ATTR_ENTITY_ID: source_entity_id,
                            CONF_ATTRIBUTE: "state",
                        }
                        for variable_name, source_entity_id
                        in old_template_sources.items()
                    },
                    CONF_VALUE_TEMPLATE: old_defaults[CONF_VALUE_TEMPLATE],
                    CONF_AUTO_HELPER: _auto_helper_profile(old_defaults),
                }],
            },
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(
        entry.entry_id,
        data={CONF_ACTION: ACTION_EDIT_ENTITY},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_ENTITY_KEY: _entity_key("Combined", 0)},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_REFERENCE_ENTITY_ID: ["binary_sensor.new_door"]},
    )
    assert result["step_id"] == "edit_entity_type"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_TARGET_ENTITY_TYPE: "binary_sensor"},
    )
    assert result["step_id"] == "edit_entity_helper"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_HELPER_UPDATE_MODE: HELPER_UPDATE_AUTO},
    )

    defaults = _flatten_entity_form_sections(result["data_schema"]({}))
    assert defaults[CONF_PLATFORM] == "binary_sensor"
    assert defaults[ATTR_ENTITY_ID] == "binary_sensor.combined_state"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        defaults,
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    entity = _first_stored_entity(result)
    assert entity[CONF_PLATFORM] == "binary_sensor"
    assert entity[ATTR_ENTITY_ID] == "binary_sensor.combined_state"
    assert entity[CONF_SOURCE_ENTITIES] == ["binary_sensor.new_door"]


async def test_options_flow_rejects_indirect_virtual_entity_dependency_cycle(hass):
    """Editing an entity must not turn an existing dependency into a cycle."""
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={
            ATTR_DEVICES: {
                "Virtual": [
                    {
                        CONF_PLATFORM: "sensor",
                        CONF_NAME: "First",
                        ATTR_ENTITY_ID: "sensor.first",
                        CONF_SOURCE_ENTITIES: ["sensor.second"],
                    },
                    {
                        CONF_PLATFORM: "sensor",
                        CONF_NAME: "Second",
                        ATTR_ENTITY_ID: "sensor.second",
                    },
                ],
            },
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(
        entry.entry_id,
        data={CONF_ACTION: ACTION_EDIT_ENTITY},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_ENTITY_KEY: _entity_key("Virtual", 1)},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {},
    )

    defaults = _flatten_entity_form_sections(result["data_schema"]({}))
    defaults[CONF_SOURCE_ENTITIES_TEXT] = "sensor.first"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        defaults,
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "edit_entity"
    assert result["errors"] == {CONF_SOURCE_ENTITIES_TEXT: "invalid_entity_id"}


async def test_options_flow_can_prefill_new_entity_from_existing_entity(hass):
    hass.states.async_set(
        "light.kitchen_lamp",
        "on",
        {
            "friendly_name": "Kitchen Lamp",
            "brightness": 128,
        },
    )
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={ATTR_DEVICES: {}},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(
        entry.entry_id,
        data={CONF_ACTION: ACTION_ADD_ENTITY},
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "entity_source"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_REFERENCE_ENTITY_ID: ["light.kitchen_lamp"]},
    )
    result = await _choose_add_template_helper(hass, result)

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "entity"

    defaults = _flatten_entity_form_sections(result["data_schema"]({}))
    assert defaults[CONF_ENTITY_NAME] == "Kitchen Lamp"
    assert defaults[CONF_PLATFORM] == "light"
    assert defaults[CONF_INITIAL_VALUE] == "on"
    assert defaults["attributes_json"] == '{"brightness": 128}'

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            **defaults,
            CONF_DEVICE_NAME: "Kitchen",
            ATTR_ENTITY_ID: "light.virtual_kitchen_lamp",
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    saved = _first_stored_entity(result)
    assert saved.pop(ATTR_ENTITY_KEY)
    assert saved.pop("auto_helper")
    native_templates = saved.pop(CONF_NATIVE_TEMPLATES)
    command_actions = saved.pop(CONF_COMMAND_ACTIONS)
    assert command_actions == {
        "turn_off": [{
            "action": "light.turn_off",
            "data": "{{ command_data }}",
            "target": {ATTR_ENTITY_ID: "light.kitchen_lamp"},
        }],
        "turn_on": [{
            "action": "light.turn_on",
            "data": "{{ command_data }}",
            "target": {ATTR_ENTITY_ID: "light.kitchen_lamp"},
        }],
    }
    assert next(iter(result["data"][ATTR_DEVICES].values())) == [{
            CONF_PLATFORM: "light",
            CONF_NAME: "Kitchen Lamp",
            ATTR_ENTITY_ID: "light.virtual_kitchen_lamp",
            CONF_INITIAL_VALUE: "on",
            CONF_INITIAL_AVAILABILITY: True,
            CONF_PERSISTENT: True,
            CONF_ICON_TEMPLATE: (
                "{{ state_attr('light.kitchen_lamp', 'icon') "
                "| default('', true) }}"
            ),
            CONF_SOURCE_ENTITIES: ["light.kitchen_lamp"],
            CONF_TEMPLATE_SOURCES: {
                "kitchen_lamp": {
                    ATTR_ENTITY_ID: "light.kitchen_lamp",
                    CONF_ATTRIBUTE: "state",
                },
                },
                CONF_VALUE_TEMPLATE: "{{ kitchen_lamp }}",
                CONF_AVAILABILITY_TEMPLATE: (
                    "{{ states('light.kitchen_lamp') not in "
                    "['unknown', 'unavailable'] }}"
                ),
                CONF_ATTRIBUTES: {"brightness": 128},
            },
        ]
    assert set(native_templates) == set(
        DOMAIN_NATIVE_TEMPLATE_PROPERTIES["light"]
    )
    assert native_templates["is_on"]
    assert native_templates["brightness"]
    assert native_templates["is_on"] == (
        "{{ states('light.kitchen_lamp') not in "
        "['off', 'unknown', 'unavailable'] }}"
    )
    assert Template(native_templates["brightness"], hass).async_render(
        parse_result=True
    ) == 128


async def test_creation_flows_apply_the_selected_template_helper_policy(hass):
    hass.states.async_set("sensor.source_temperature", "22")
    result = await hass.config_entries.flow.async_init(
        COMPONENT_DOMAIN,
        context={"source": SOURCE_USER},
        data={
            ATTR_GROUP_NAME: "First Device",
            CONF_ADD_FIRST_ENTITY: True,
        },
    )
    assert result["step_id"] == "entity_source"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_REFERENCE_ENTITY_ID: ["sensor.source_temperature"]},
    )
    assert result["step_id"] == "entity_type"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_TARGET_ENTITY_TYPE: "sensor"},
    )
    assert result["step_id"] == "entity_helper"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_USE_TEMPLATE_HELPER: True},
    )
    assert result["step_id"] == "entity"
    config_defaults = _flatten_entity_form_sections(result["data_schema"]({}))
    assert config_defaults[CONF_SOURCE_ENTITIES_TEXT] == "sensor.source_temperature"
    assert "source_temperature" in config_defaults[CONF_VALUE_TEMPLATE]
    hass.config_entries.flow.async_abort(result["flow_id"])

    hass.states.async_set(
        "sensor.room_temperature",
        "21.5",
        {
            "friendly_name": "Room Temperature",
            "quality": "good",
        },
    )
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={ATTR_DEVICES: {}},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(
        entry.entry_id,
        data={CONF_ACTION: ACTION_ADD_ENTITY},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_REFERENCE_ENTITY_ID: ["sensor.room_temperature"]},
    )

    assert result["step_id"] == "entity_type"
    result = await _choose_add_template_helper(hass, result, enabled=False)

    assert result["step_id"] == "entity"
    defaults = _flatten_entity_form_sections(result["data_schema"]({}))
    assert defaults[CONF_ENTITY_NAME] == "Room Temperature"
    assert defaults[CONF_PLATFORM] == "sensor"
    assert defaults[CONF_INITIAL_VALUE] == "21.5"
    assert defaults[CONF_SOURCE_ENTITIES_TEXT] == "sensor.room_temperature"
    assert defaults[CONF_ATTRIBUTES_JSON] == '{"quality": "good"}'
    assert defaults[CONF_VALUE_TEMPLATE] == ""
    assert defaults[CONF_AVAILABILITY_TEMPLATE] == ""
    assert defaults[CONF_ICON_TEMPLATE] == ""
    assert defaults[CONF_TEMPLATE_SOURCES_JSON] == ""
    assert defaults[CONF_ATTRIBUTE_TEMPLATES_JSON] == ""
    assert all(
        "sensor.room_temperature" not in template
        for template in defaults[CONF_NATIVE_VALUE_TEMPLATES].values()
    )

    defaults[CONF_DEVICE_NAME] = "Room"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        defaults,
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    saved = _first_stored_entity(result)
    assert saved[CONF_SOURCE_ENTITIES] == ["sensor.room_temperature"]
    assert CONF_VALUE_TEMPLATE not in saved
    assert CONF_AVAILABILITY_TEMPLATE not in saved
    assert CONF_ICON_TEMPLATE not in saved
    assert CONF_TEMPLATE_SOURCES not in saved
    assert CONF_AUTO_HELPER not in saved


async def test_options_flow_copy_existing_entity_avoids_source_entity_id(hass):
    hass.states.async_set(
        "sensor.kitchen_lamp",
        "on",
        {"friendly_name": "Kitchen Lamp"},
    )
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={ATTR_DEVICES: {}},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(
        entry.entry_id,
        data={CONF_ACTION: ACTION_ADD_ENTITY},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_REFERENCE_ENTITY_ID: ["sensor.kitchen_lamp"]},
    )
    result = await _choose_add_template_helper(hass, result)
    defaults = _flatten_entity_form_sections(result["data_schema"]({}))

    assert defaults[ATTR_ENTITY_ID] == "sensor.kitchen_lamp_copy"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {**defaults, CONF_DEVICE_NAME: "Kitchen"},
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert _first_stored_entity(result)[ATTR_ENTITY_ID] == (
        "sensor.kitchen_lamp_copy"
    )


async def test_options_flow_refreshes_untouched_helpers_when_add_sources_change(hass):
    old_sources = [
        "binary_sensor.door_one",
        "binary_sensor.door_two",
    ]
    new_sources = [
        "binary_sensor.door_one",
        "binary_sensor.door_three",
    ]
    for entity_id, state in zip(
        [*old_sources, new_sources[-1]],
        ["on", "off", "on"],
        strict=True,
    ):
        hass.states.async_set(entity_id, state, {"device_class": "door"})

    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={ATTR_DEVICES: {}},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(
        entry.entry_id,
        data={CONF_ACTION: ACTION_ADD_ENTITY},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_REFERENCE_ENTITY_ID: old_sources},
    )
    result = await _choose_add_template_helper(hass, result)
    defaults = _flatten_entity_form_sections(result["data_schema"]({}))
    assert "door_two" in defaults[CONF_VALUE_TEMPLATE]

    defaults[CONF_DEVICE_NAME] = "Doors"
    defaults[CONF_SOURCE_ENTITIES_TEXT] = "\n".join(new_sources)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        defaults,
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY

    saved = _first_stored_entity(result)
    assert saved[CONF_SOURCE_ENTITIES] == new_sources
    assert {
        source[ATTR_ENTITY_ID]
        for source in saved[CONF_TEMPLATE_SOURCES].values()
    } == set(new_sources)
    assert "door_three" in saved[CONF_VALUE_TEMPLATE]
    assert "door_two" not in saved[CONF_VALUE_TEMPLATE]
    assert saved[CONF_AUTO_HELPER][CONF_SOURCE_ENTITIES_TEXT] == "\n".join(
        new_sources
    )


async def test_helper_update_failure_keeps_edit_form_available(hass, monkeypatch, caplog):
    old_source = "binary_sensor.door_old"
    new_source = "binary_sensor.door_new"
    hass.states.async_set(old_source, "off", {"device_class": "door"})
    hass.states.async_set(new_source, "off", {"device_class": "door"})

    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={
            ATTR_DEVICES: {
                "Doors": [{
                    CONF_PLATFORM: "binary_sensor",
                    CONF_NAME: "Combined Door",
                    CONF_INITIAL_VALUE: "off",
                    CONF_INITIAL_AVAILABILITY: True,
                    CONF_PERSISTENT: True,
                    CONF_SOURCE_ENTITIES: [old_source],
                    CONF_VALUE_TEMPLATE: "{{ door_old }}",
                }],
            },
        },
    )
    entry.add_to_hass(hass)

    def fail_helper_update(self, *, helper_update_mode):
        raise RuntimeError("helper generation failed")

    monkeypatch.setattr(
        VirtualOptionsFlowHandler,
        "_prepare_edit_entity_defaults",
        fail_helper_update,
    )
    result = await hass.config_entries.options.async_init(
        entry.entry_id,
        data={CONF_ACTION: ACTION_EDIT_ENTITY},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_ENTITY_KEY: _entity_key("Doors", 0)},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_REFERENCE_ENTITY_ID: [new_source]},
    )
    assert result["step_id"] == "edit_entity_type"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_TARGET_ENTITY_TYPE: "binary_sensor"},
    )

    with caplog.at_level("ERROR"):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {CONF_HELPER_UPDATE_MODE: HELPER_UPDATE_AUTO},
        )

    assert result["step_id"] == "edit_entity"
    assert result["errors"] == {"base": "helper_update_failed"}
    defaults = _flatten_entity_form_sections(result["data_schema"]({}))
    assert defaults[CONF_SOURCE_ENTITIES_TEXT] == new_source
    assert defaults[CONF_VALUE_TEMPLATE] == "{{ door_old }}"
    assert "Unable to apply Virtual Layer helper update" in caplog.text

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        defaults,
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    saved = _first_stored_entity(result)
    assert saved[CONF_SOURCE_ENTITIES] == [new_source]
    assert saved[CONF_VALUE_TEMPLATE] == "{{ door_old }}"


async def test_options_flow_prefills_climate_native_mode_options(hass):
    hass.states.async_set(
        "climate.bedroom",
        "cool",
        {
            "friendly_name": "Bedroom AC",
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
        },
    )
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={ATTR_DEVICES: {}},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(
        entry.entry_id,
        data={CONF_ACTION: ACTION_ADD_ENTITY},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_REFERENCE_ENTITY_ID: ["climate.bedroom"]},
    )
    result = await _choose_add_template_helper(hass, result)

    defaults = _flatten_entity_form_sections(result["data_schema"]({}))
    assert defaults[CONF_PLATFORM] == "climate"
    native_templates = defaults[CONF_NATIVE_VALUE_TEMPLATES]
    assert set(native_templates) == set(CLIMATE_NATIVE_TEMPLATE_PROPERTIES)
    assert Template(native_templates["hvac_modes"], hass).async_render(
        parse_result=True
    ) == ["off", "cool", "dry", "fan_only"]
    assert Template(native_templates["fan_mode"], hass).async_render(
        parse_result=True
    ) == "auto"
    assert Template(native_templates["target_temperature"], hass).async_render(
        parse_result=True
    ) == 23.0
    assert all(native_templates.values())
    assert defaults[CONF_DOMAIN_OPTIONS_JSON] == ""
    assert defaults["attributes_json"] == ""

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            **defaults,
            CONF_DEVICE_NAME: "Bedroom",
            ATTR_ENTITY_ID: "climate.virtual_bedroom",
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    entity = _first_stored_entity(result)
    assert set(entity[CONF_NATIVE_TEMPLATES]) == set(
        CLIMATE_NATIVE_TEMPLATE_PROPERTIES
    )
    assert entity[CONF_NATIVE_TEMPLATES] == native_templates
    assert "supported_features" not in entity.get(CONF_ATTRIBUTES, {})

    runtime_config = {
        key: value
        for key, value in entity.items()
        if key not in {CONF_PLATFORM, ATTR_ENTITY_KEY, CONF_AUTO_HELPER}
    }
    climate = VirtualClimate(CLIMATE_SCHEMA(runtime_config), False)
    climate.hass = hass
    climate._create_state(climate._config)
    climate.async_schedule_update_ha_state = Mock()
    climate._apply_templates()

    assert climate.hvac_mode == HVACMode.COOL
    assert climate.current_temperature == 24
    assert climate.target_temperature == 23
    assert climate.fan_modes == ["medium", "high", "turbo", "auto"]
    assert climate.fan_mode == "auto"
    assert climate.preset_mode == "none"
    assert climate.swing_modes == []
    assert climate.swing_mode is None
    assert climate.target_humidity is None
    assert ClimateEntityFeature.TARGET_TEMPERATURE in climate.supported_features
    assert ClimateEntityFeature.FAN_MODE in climate.supported_features
    assert ClimateEntityFeature.PRESET_MODE in climate.supported_features
    assert (
        ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
        not in climate.supported_features
    )
    assert ClimateEntityFeature.TARGET_HUMIDITY not in climate.supported_features
    assert ClimateEntityFeature.SWING_MODE not in climate.supported_features
    assert int(climate.supported_features) == 409
    state_attributes = climate.state_attributes
    assert state_attributes["current_temperature"] == 24
    assert state_attributes["temperature"] == 23
    assert "humidity" not in state_attributes
    assert "target_temp_high" not in state_attributes
    assert "target_temp_low" not in state_attributes


async def test_options_flow_copies_fan_without_duplicate_attribute_templates(hass):
    hass.states.async_set(
        "fan.air_ventilator",
        "on",
        {
            "friendly_name": "Air Ventilator",
            "percentage": 40,
            "percentage_step": 20,
            "preset_mode": "Manual",
            "preset_modes": ["Manual", "Auto", "Sleep 1", "Sleep 2", "Sleep 3"],
            "supported_features": 57,
        },
    )
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={ATTR_DEVICES: {}},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(
        entry.entry_id,
        data={CONF_ACTION: ACTION_ADD_ENTITY},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_REFERENCE_ENTITY_ID: ["fan.air_ventilator"]},
    )
    result = await _choose_add_template_helper(hass, result)
    defaults = _flatten_entity_form_sections(result["data_schema"]({}))

    assert defaults[CONF_PLATFORM] == "fan"
    assert defaults[CONF_ATTRIBUTE_TEMPLATES_JSON] == ""
    assert "percentage_step" in defaults[CONF_NATIVE_VALUE_TEMPLATES]["speed_count"]

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            **defaults,
            CONF_DEVICE_NAME: "Air Ventilator",
            ATTR_ENTITY_ID: "fan.air_ventilator_copy",
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    entity = _first_stored_entity(result)
    assert CONF_ATTRIBUTE_TEMPLATES not in entity
    assert entity["speed_count"] == 5

    runtime_config = {
        key: value
        for key, value in entity.items()
        if key not in {CONF_PLATFORM, ATTR_ENTITY_KEY, CONF_AUTO_HELPER}
    }
    fan = VirtualFan(FAN_SCHEMA(runtime_config), False)
    fan.hass = hass
    fan._create_state(fan._config)
    fan.async_schedule_update_ha_state = Mock()
    fan._apply_templates()

    assert fan.percentage == 40
    assert fan.preset_modes == ["Manual", "Auto", "Sleep 1", "Sleep 2", "Sleep 3"]
    assert fan.preset_mode == "Manual"
    assert fan.oscillating is None
    assert fan.current_direction is None
    assert fan.supported_features == FanEntityFeature(57)


async def test_options_flow_combines_xiaomi_fan_and_speed_number(hass):
    fan_entity_id = "fan.air_purifier_purifier_1"
    number_entity_id = "number.air_purifier_favorite_level"
    hass.states.async_set(
        fan_entity_id,
        "on",
        {
            "friendly_name": "Air Purifier",
            "percentage": 35,
            "percentage_step": 1,
            "preset_mode": "Manual",
            "preset_modes": ["Favorite", "Manual", "Auto", "Silent"],
            "supported_features": 57,
        },
    )
    hass.states.async_set(
        number_entity_id,
        "72",
        {"min": 0, "max": 100, "step": 1, "mode": "slider"},
    )
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={ATTR_DEVICES: {}},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(
        entry.entry_id,
        data={CONF_ACTION: ACTION_ADD_ENTITY},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_REFERENCE_ENTITY_ID: [fan_entity_id, number_entity_id]},
    )
    result = await _choose_add_template_helper(hass, result)
    defaults = _flatten_entity_form_sections(result["data_schema"]({}))

    assert defaults[CONF_PLATFORM] == "fan"
    assert defaults[CONF_ATTRIBUTE_TEMPLATES_JSON] == ""
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            **defaults,
            CONF_DEVICE_NAME: "Air Purifier",
            ATTR_ENTITY_ID: "fan.air_purifier_virtual",
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    entity = _first_stored_entity(result)
    assert CONF_ATTRIBUTE_TEMPLATES not in entity
    percentage_template = entity[CONF_NATIVE_TEMPLATES]["percentage"]
    assert Template(percentage_template, hass).async_render(parse_result=True) == 72

    hass.states.async_set(
        fan_entity_id,
        "on",
        {
            "percentage": 35,
            "percentage_step": 1,
            "preset_mode": "Silent",
            "preset_modes": ["Favorite", "Manual", "Auto", "Silent"],
            "supported_features": 57,
        },
    )
    hass.states.async_set(number_entity_id, "unavailable")
    assert Template(percentage_template, hass).async_render(parse_result=True) == 35
    assert Template(
        entity[CONF_AVAILABILITY_TEMPLATE], hass
    ).async_render(parse_result=True) is True


async def test_options_flow_prefills_and_creates_native_dehumidifier(hass):
    hass.states.async_set(
        "humidifier.basement",
        "on",
        {
            "friendly_name": "Basement Dehumidifier",
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
        },
    )
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={ATTR_DEVICES: {}},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(
        entry.entry_id,
        data={CONF_ACTION: ACTION_ADD_ENTITY},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_REFERENCE_ENTITY_ID: ["humidifier.basement"]},
    )
    result = await _choose_add_template_helper(hass, result)

    defaults = _flatten_entity_form_sections(result["data_schema"]({}))
    assert defaults[CONF_PLATFORM] == "humidifier"
    native_templates = defaults[CONF_NATIVE_VALUE_TEMPLATES]
    assert Template(native_templates["device_class"], hass).async_render(
        parse_result=True
    ) == "dehumidifier"
    assert Template(native_templates["available_modes"], hass).async_render(
        parse_result=True
    ) == ["auto", "sleep"]
    assert Template(native_templates["target_humidity"], hass).async_render(
        parse_result=True
    ) == 50
    assert all(native_templates.values())
    assert defaults[CONF_DOMAIN_OPTIONS_JSON] == ""

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            **defaults,
            CONF_DEVICE_NAME: "Basement",
            ATTR_ENTITY_ID: "humidifier.virtual_basement",
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    entity = _first_stored_entity(result)
    assert entity[CONF_NATIVE_TEMPLATES] == native_templates
    assert "supported_features" not in entity.get(CONF_ATTRIBUTES, {})


async def test_options_flow_composes_humidifier_from_mixed_source_domains(hass):
    entity_ids = [
        "number.dressing_room_dehumidifier_target_humidity",
        "switch.dressing_room_dehumidifier_power",
        "select.dressing_room_dehumidifier_operating_mode",
        "sensor.dressing_room_dehumidifier_humidity",
    ]
    hass.states.async_set(
        entity_ids[0],
        "50",
        {"min": 30, "max": 70, "step": 5, "mode": "slider"},
    )
    hass.states.async_set(entity_ids[1], "on")
    hass.states.async_set(
        entity_ids[2],
        "Medium",
        {"options": ["Low", "Medium", "High"]},
    )
    hass.states.async_set(
        entity_ids[3],
        "52",
        {"device_class": "humidity", "unit_of_measurement": "%"},
    )
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={ATTR_DEVICES: {}},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(
        entry.entry_id,
        data={CONF_ACTION: ACTION_ADD_ENTITY},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_REFERENCE_ENTITY_ID: entity_ids},
    )

    assert result["step_id"] == "entity_type"
    result = await _choose_add_template_helper(
        hass,
        result,
        target_entity_type="humidifier",
    )
    defaults = _flatten_entity_form_sections(result["data_schema"]({}))
    assert defaults[CONF_PLATFORM] == "humidifier"
    assert defaults[CONF_NATIVE_VALUE_TEMPLATES]["target_humidity"] == (
        "{{ states('number.dressing_room_dehumidifier_target_humidity') "
        "| float(none) }}"
    )
    assert "max" not in json.loads(
        defaults.get(CONF_ATTRIBUTE_TEMPLATES_JSON, "{}") or "{}"
    )

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            **defaults,
            CONF_DEVICE_NAME: "Dressing Room Dehumidifier",
            ATTR_ENTITY_ID: "humidifier.dressing_room_dehumidifier",
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    entity = _first_stored_entity(result)
    assert entity[CONF_PLATFORM] == "humidifier"
    assert set(entity[CONF_COMMAND_ACTIONS]) == {
        "set_humidity",
        "set_mode",
        "turn_off",
        "turn_on",
    }


async def test_options_flow_can_edit_all_climate_modes(hass):
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={
            ATTR_DEVICES: {
                "Bedroom": [{
                    CONF_PLATFORM: "climate",
                    CONF_NAME: "Bedroom AC",
                    ATTR_ENTITY_ID: "climate.virtual_bedroom",
                    CONF_INITIAL_VALUE: "cool",
                    CONF_INITIAL_AVAILABILITY: True,
                    CONF_PERSISTENT: True,
                    "hvac_modes": ["off", "cool", "dry"],
                    "fan_modes": ["auto", "turbo"],
                    "fan_mode": "auto",
                    "preset_modes": ["none", "sleep"],
                    "preset_mode": "none",
                    "swing_modes": ["off", "vertical"],
                    "swing_mode": "off",
                    "swing_horizontal_modes": ["left", "right"],
                    "swing_horizontal_mode": "left",
                }],
            },
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(
        entry.entry_id,
        data={CONF_ACTION: ACTION_EDIT_ENTITY},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_ENTITY_KEY: _entity_key("Bedroom", 0)},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {},
    )

    assert result["step_id"] == "edit_entity"
    defaults = _flatten_entity_form_sections(result["data_schema"]({}))
    native_templates = defaults[CONF_NATIVE_VALUE_TEMPLATES]
    assert native_templates["fan_modes"] == "{{ ['auto', 'turbo'] }}"
    assert native_templates["preset_mode"] == "{{ 'none' }}"
    assert native_templates["swing_modes"] == "{{ ['off', 'vertical'] }}"
    assert native_templates["swing_horizontal_mode"] == "{{ 'left' }}"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            **defaults,
            CONF_INITIAL_VALUE: "heat",
            CONF_NATIVE_VALUE_TEMPLATES: {
                **native_templates,
                "hvac_modes": "{{ ['off', 'heat', 'cool'] }}",
                "fan_modes": "{{ ['auto', 'quiet'] }}",
                "fan_mode": "{{ 'quiet' }}",
                "preset_modes": "{{ ['none', 'eco'] }}",
                "preset_mode": "{{ 'eco' }}",
                "swing_modes": "{{ ['auto', 'vertical'] }}",
                "swing_mode": "{{ 'auto' }}",
                "swing_horizontal_modes": "{{ ['left', 'right'] }}",
                "swing_horizontal_mode": "{{ 'right' }}",
            },
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    entity = _first_stored_entity(result)
    assert entity[CONF_INITIAL_VALUE] == "heat"
    saved_templates = entity[CONF_NATIVE_TEMPLATES]
    assert saved_templates["hvac_modes"] == "{{ ['off', 'heat', 'cool'] }}"
    assert saved_templates["fan_modes"] == "{{ ['auto', 'quiet'] }}"
    assert saved_templates["fan_mode"] == "{{ 'quiet' }}"
    assert saved_templates["preset_modes"] == "{{ ['none', 'eco'] }}"
    assert saved_templates["swing_mode"] == "{{ 'auto' }}"
    assert saved_templates["swing_horizontal_mode"] == "{{ 'right' }}"


async def test_options_flow_can_prefill_composite_binary_sensor_from_multiple_entities(hass):
    hass.states.async_set("binary_sensor.front_door", "on")
    hass.states.async_set("binary_sensor.back_door", "on")
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={ATTR_DEVICES: {}},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(
        entry.entry_id,
        data={CONF_ACTION: ACTION_ADD_ENTITY},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_REFERENCE_ENTITY_ID: [
                "binary_sensor.front_door",
                "binary_sensor.back_door",
            ],
        },
    )
    result = await _choose_add_template_helper(hass, result)

    defaults = _flatten_entity_form_sections(result["data_schema"]({}))
    assert defaults[CONF_PLATFORM] == "binary_sensor"
    assert defaults[CONF_INITIAL_VALUE] == "on"
    assert defaults[CONF_SOURCE_ENTITIES_TEXT] == (
        "binary_sensor.front_door\nbinary_sensor.back_door"
    )
    assert " and " in defaults[CONF_VALUE_TEMPLATE]

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            **defaults,
            CONF_DEVICE_NAME: "Security",
            CONF_ENTITY_NAME: "All Doors Ready",
            ATTR_ENTITY_ID: "binary_sensor.all_doors_ready",
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    entity = _first_stored_entity(result)
    entity.pop(ATTR_ENTITY_KEY)
    entity.pop("auto_helper")
    assert entity == {
        CONF_PLATFORM: "binary_sensor",
        CONF_NAME: "All Doors Ready",
        ATTR_ENTITY_ID: "binary_sensor.all_doors_ready",
        CONF_INITIAL_VALUE: "on",
        CONF_INITIAL_AVAILABILITY: True,
        CONF_PERSISTENT: True,
        CONF_ICON_TEMPLATE: defaults[CONF_ICON_TEMPLATE],
        CONF_SOURCE_ENTITIES: [
            "binary_sensor.front_door",
            "binary_sensor.back_door",
        ],
        CONF_TEMPLATE_SOURCES: {
            "front_door": {
                ATTR_ENTITY_ID: "binary_sensor.front_door",
                CONF_ATTRIBUTE: "state",
            },
            "back_door": {
                ATTR_ENTITY_ID: "binary_sensor.back_door",
                CONF_ATTRIBUTE: "state",
            },
            },
            CONF_VALUE_TEMPLATE: defaults[CONF_VALUE_TEMPLATE],
        CONF_AVAILABILITY_TEMPLATE: defaults[CONF_AVAILABILITY_TEMPLATE],
        CONF_NATIVE_TEMPLATES: {"device_class": "{{ None }}"},
    }


async def test_options_flow_adds_entity_to_selected_existing_device(hass):
    hass.states.async_set("sensor.refrigerator_temperature", "4")
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={
            ATTR_DEVICES: {
                "Refrigerator Door": [
                    {
                        CONF_PLATFORM: "binary_sensor",
                        CONF_NAME: "Refrigerator Door",
                        CONF_INITIAL_VALUE: "off",
                    },
                ],
            },
            ATTR_DEVICE_ATTRIBUTES: {
                "Refrigerator Door": {
                    ATTR_DEVICE_ID: "refrigerator-door-1",
                    CONF_NAME: "Refrigerator Door",
                    CONF_MANUFACTURER: "TCL",
                },
            },
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(
        entry.entry_id,
        data={CONF_ACTION: ACTION_ADD_ENTITY},
    )
    source_defaults = _flatten_entity_form_sections(result["data_schema"]({}))
    assert source_defaults[CONF_TARGET_DEVICE_NAME] == "__new_device__"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_REFERENCE_ENTITY_ID: ["sensor.refrigerator_temperature"],
            CONF_TARGET_DEVICE_NAME: "Refrigerator Door",
        },
    )
    result = await _choose_add_template_helper(hass, result)
    defaults = _flatten_entity_form_sections(result["data_schema"]({}))
    assert defaults[CONF_DEVICE_NAME] == "Refrigerator Door"
    assert defaults[CONF_DEVICE_ID] == "refrigerator-door-1"
    assert defaults[CONF_DEVICE_MANUFACTURER] == "TCL"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            **defaults,
            CONF_ENTITY_NAME: "Refrigerator Temperature",
            ATTR_ENTITY_ID: "sensor.refrigerator_temperature_virtual",
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    devices = result["data"][ATTR_DEVICES]
    assert list(devices) == ["Refrigerator Door"]
    assert len(devices["Refrigerator Door"]) == 2
    assert result["data"][ATTR_DEVICE_ATTRIBUTES]["Refrigerator Door"][ATTR_DEVICE_ID] == (
        "refrigerator-door-1"
    )


async def test_setup_entry_groups_multiple_virtual_entities_on_one_device(hass, tmp_path, monkeypatch):
    meta_file = tmp_path / "virtual_layer.meta.json"
    monkeypatch.setattr(
        "custom_components.virtual_layer.cfg.default_meta_file",
        lambda _hass: str(meta_file),
    )
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "refrigerator"},
        options={
            ATTR_DEVICES: {
                "Refrigerator Door": [
                    {
                        CONF_PLATFORM: "binary_sensor",
                        CONF_NAME: "Door Open",
                        CONF_ICON: "mdi:door-open",
                        ATTR_ENTITY_ID: "binary_sensor.refrigerator_door_open",
                        CONF_INITIAL_VALUE: "off",
                        CONF_INITIAL_AVAILABILITY: True,
                        CONF_PERSISTENT: False,
                    },
                    {
                        CONF_PLATFORM: "sensor",
                        CONF_NAME: "Door Temperature",
                        ATTR_ENTITY_ID: "sensor.refrigerator_door_temperature",
                        CONF_INITIAL_VALUE: "4",
                        CONF_INITIAL_AVAILABILITY: True,
                        CONF_PERSISTENT: False,
                    },
                ],
            },
            ATTR_DEVICE_ATTRIBUTES: {
                "Refrigerator Door": {
                    ATTR_DEVICE_ID: "refrigerator-door-1",
                    CONF_NAME: "Refrigerator Door",
                    CONF_MANUFACTURER: "TCL",
                },
            },
        },
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id) is True
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)
    device_ids = {
        entity_registry.async_get(entity_id).device_id
        for entity_id in (
            "binary_sensor.refrigerator_door_open",
            "sensor.refrigerator_door_open_info",
            "sensor.refrigerator_door_temperature",
            "sensor.refrigerator_door_temperature_info",
        )
    }
    assert len(device_ids) == 1
    device_id = device_ids.pop()
    assert entity_registry.async_get(
        "binary_sensor.refrigerator_door_open",
    ).original_icon == "mdi:door-open"
    device_entry = dr.async_get(hass).async_get(device_id)
    assert device_entry.name == "Refrigerator Door"
    assert (COMPONENT_DOMAIN, "refrigerator-door-1") in device_entry.identifiers


async def test_new_entities_default_to_an_explicit_device_area(
    hass, tmp_path, monkeypatch
):
    """New entities disable Device-area inheritance without changing old entries."""
    meta_file = tmp_path / "virtual_layer.meta.json"
    monkeypatch.setattr(
        "custom_components.virtual_layer.cfg.default_meta_file",
        lambda _hass: str(meta_file),
    )
    kitchen = ar.async_get(hass).async_create("Kitchen")
    office = ar.async_get(hass).async_create("Office")
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "areas"},
        options={
            ATTR_DEVICES: {
                "Washer": [
                    {
                        CONF_PLATFORM: "sensor",
                        CONF_NAME: "Washer Phase",
                        ATTR_ENTITY_ID: "sensor.washer_phase",
                        CONF_INITIAL_VALUE: "washing",
                        CONF_INITIAL_AVAILABILITY: True,
                        CONF_PERSISTENT: False,
                    },
                ],
            },
            ATTR_DEVICE_ATTRIBUTES: {
                "Washer": {
                    ATTR_DEVICE_ID: "washer-1",
                    CONF_NAME: "Washer",
                    CONF_SUGGESTED_AREA: "Kitchen",
                },
            },
        },
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id) is True
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)
    primary = entity_registry.async_get("sensor.washer_phase")
    info = entity_registry.async_get("sensor.washer_phase_info")
    assert primary.area_id == kitchen.id
    assert info.area_id == kitchen.id

    entity_registry.async_update_entity(primary.entity_id, area_id=office.id)
    entity_registry.async_update_entity(info.entity_id, area_id=None)
    assert await async_unload_entry(hass, entry) is True
    assert await async_setup_entry(hass, entry) is True
    await hass.async_block_till_done()

    assert entity_registry.async_get("sensor.washer_phase").area_id == office.id
    assert entity_registry.async_get("sensor.washer_phase_info").area_id is None


async def test_setup_entry_restores_stale_virtual_entity_registry_metadata(
    hass,
    tmp_path,
    monkeypatch,
):
    """Stale default registry rows are moved back to the configured entity/device."""
    meta_file = tmp_path / "virtual_layer.meta.json"
    meta_file.write_text(json.dumps({
        "version": 1,
        ATTR_DEVICES: {
            "fridge": {
                "door-key": {
                    ATTR_UNIQUE_ID: "door-unique",
                    ATTR_ENTITY_ID: "binary_sensor.virtual_entity",
                    ATTR_DEVICE_ID: "old-device",
                    CONF_NAME: "Virtual Entity",
                    CONF_PLATFORM: "binary_sensor",
                },
            },
        },
    }))
    monkeypatch.setattr(
        "custom_components.virtual_layer.cfg.default_meta_file",
        lambda _hass: str(meta_file),
    )
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "fridge"},
        options={
            ATTR_DEVICES: {
                "Refrigerator Door": [
                    {
                        CONF_PLATFORM: "binary_sensor",
                        CONF_NAME: "Refrigerator Door",
                        CONF_ICON: "mdi:door-open",
                        ATTR_ENTITY_KEY: "door-key",
                        ATTR_ENTITY_ID: "binary_sensor.refrigerator_door",
                        CONF_INITIAL_VALUE: "off",
                        CONF_INITIAL_AVAILABILITY: True,
                        CONF_PERSISTENT: False,
                        CONF_SOURCE_ENTITIES: [
                            "binary_sensor.door_sensor_6",
                            "binary_sensor.door_sensor_5",
                        ],
                    },
                ],
            },
            ATTR_DEVICE_ATTRIBUTES: {
                "Refrigerator Door": {
                    ATTR_DEVICE_ID: "refrigerator-door-1",
                    CONF_NAME: "Refrigerator Door",
                    CONF_MANUFACTURER: "TCL",
                },
            },
        },
    )
    entry.add_to_hass(hass)
    hass.states.async_set("binary_sensor.door_sensor_6", "off")
    hass.states.async_set("binary_sensor.door_sensor_5", "on")

    device_registry = dr.async_get(hass)
    old_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(COMPONENT_DOMAIN, "old-device")},
        name="Virtual Device",
    )
    entity_registry = er.async_get(hass)
    entity_registry.async_get_or_create(
        "binary_sensor",
        COMPONENT_DOMAIN,
        "door-unique",
        suggested_object_id="virtual_entity",
        config_entry=entry,
        device_id=old_device.id,
        original_name="Virtual Entity",
        original_icon="mdi:eye-outline",
    )
    entity_registry.async_get_or_create(
        "sensor",
        COMPONENT_DOMAIN,
        "door-unique.virtual_layer_diagnostic.info",
        suggested_object_id="virtual_entity_info",
        config_entry=entry,
        device_id=old_device.id,
        original_name="Virtual Entity - Configuration",
        original_icon="mdi:eye-outline",
    )

    assert await hass.config_entries.async_setup(entry.entry_id) is True
    await hass.async_block_till_done()

    primary = entity_registry.async_get("binary_sensor.refrigerator_door")
    info = entity_registry.async_get("sensor.refrigerator_door_info")
    debug1 = entity_registry.async_get("sensor.refrigerator_door_debug1")

    assert entity_registry.async_get("binary_sensor.virtual_entity") is None
    assert entity_registry.async_get("sensor.virtual_entity_info") is None
    assert primary is not None
    assert info is not None
    assert debug1 is not None
    assert {primary.device_id, info.device_id, debug1.device_id} == {primary.device_id}
    assert primary.original_name == "Refrigerator Door"
    assert primary.original_icon == "mdi:door-open"
    assert info.original_name == "Refrigerator Door - Configuration"
    assert info.original_icon == "mdi:information-outline"
    assert device_registry.async_get(old_device.id) is None


async def test_presence_motion_helper_retains_detected_state_until_all_sources_clear(hass):
    source_ids = [
        "binary_sensor.entry_motion",
        "binary_sensor.hall_motion",
        "binary_sensor.kitchen_motion",
    ]
    for entity_id, state in zip(source_ids, ("on", "on", "off"), strict=True):
        hass.states.async_set(entity_id, state, {"device_class": "motion"})
    defaults = _reference_entity_defaults(hass, source_ids)
    template_sources = json.loads(defaults[CONF_TEMPLATE_SOURCES_JSON])
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "motion-helper"},
        options={
            ATTR_DEVICES: {
                "Security": [
                    {
                        CONF_PLATFORM: "binary_sensor",
                        CONF_NAME: "Combined Motion",
                        ATTR_ENTITY_ID: "binary_sensor.combined_motion",
                        CONF_INITIAL_VALUE: defaults[CONF_INITIAL_VALUE],
                        CONF_INITIAL_AVAILABILITY: True,
                        CONF_PERSISTENT: False,
                        CONF_CLASS: "motion",
                        CONF_SOURCE_ENTITIES: source_ids,
                        CONF_TEMPLATE_SOURCES: {
                            name: {
                                ATTR_ENTITY_ID: entity_id,
                                CONF_ATTRIBUTE: "state",
                            }
                            for name, entity_id in template_sources.items()
                        },
                        CONF_VALUE_TEMPLATE: defaults[CONF_VALUE_TEMPLATE],
                    },
                ],
            },
        },
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id) is True
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.combined_motion").state == "on"
    assert hass.states.get("binary_sensor.combined_motion").attributes["device_class"] == "motion"

    # One active source is not a majority, but a previous detection remains on
    # until all sources have been off for the configured five-minute window.
    hass.states.async_set(source_ids[0], "off", {"device_class": "motion"})
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.combined_motion").state == "on"

    hass.states.async_set(source_ids[1], "off", {"device_class": "motion"})
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.combined_motion").state == "on"


async def test_options_flow_can_prefill_composite_sensor_with_average_template(hass):
    hass.states.async_set("sensor.indoor_temperature", "20")
    hass.states.async_set("sensor.outdoor_temperature", "26")
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={ATTR_DEVICES: {}},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(
        entry.entry_id,
        data={CONF_ACTION: ACTION_ADD_ENTITY},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_REFERENCE_ENTITY_ID: [
                "sensor.indoor_temperature",
                "sensor.outdoor_temperature",
            ],
        },
    )
    result = await _choose_add_template_helper(hass, result)

    defaults = _flatten_entity_form_sections(result["data_schema"]({}))
    assert defaults[CONF_PLATFORM] == "sensor"
    assert defaults[CONF_INITIAL_VALUE] == "23.0"
    assert "select('is_number')" in defaults[CONF_VALUE_TEMPLATE]
    assert "values | average" in defaults[CONF_VALUE_TEMPLATE]


async def test_setup_entries_reserve_unique_ids_for_matching_generated_entities(hass):
    """Separate config entries must not claim the same generated entity id."""
    first_entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "first"},
        options={
            ATTR_DEVICES: {
                "First Device": [
                    {
                        CONF_PLATFORM: "sensor",
                        CONF_NAME: "Shared Name",
                        CONF_INITIAL_VALUE: "unknown",
                        CONF_INITIAL_AVAILABILITY: True,
                        CONF_PERSISTENT: False,
                    },
                ],
            },
        },
    )
    second_entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "second"},
        options={
            ATTR_DEVICES: {
                "Second Device": [
                    {
                        CONF_PLATFORM: "sensor",
                        CONF_NAME: "Shared Name",
                        CONF_INITIAL_VALUE: "unknown",
                        CONF_INITIAL_AVAILABILITY: True,
                        CONF_PERSISTENT: False,
                    },
                ],
            },
        },
    )
    first_entry.add_to_hass(hass)
    second_entry.add_to_hass(hass)

    with patch.object(
        hass.config_entries,
        "async_forward_entry_setups",
        AsyncMock(return_value=True),
    ):
        assert await async_setup_entry(hass, first_entry) is True
        assert await async_setup_entry(hass, second_entry) is True

    first_entity = hass.data[COMPONENT_DOMAIN]["first"][ATTR_ENTITIES]["sensor"][0]
    second_entity = hass.data[COMPONENT_DOMAIN]["second"][ATTR_ENTITIES]["sensor"][0]
    assert first_entity[ATTR_ENTITY_ID] == "sensor.shared_name"
    assert second_entity[ATTR_ENTITY_ID] != first_entity[ATTR_ENTITY_ID]

    entity_registry = er.async_get(hass)
    assert entity_registry.async_get(first_entity[ATTR_ENTITY_ID]).config_entry_id == first_entry.entry_id
    assert entity_registry.async_get(second_entity[ATTR_ENTITY_ID]).config_entry_id == second_entry.entry_id


async def test_setup_entry_repairs_unique_id_owned_by_another_entry(
    hass,
    tmp_path,
    monkeypatch,
):
    meta_file = tmp_path / "virtual_layer.meta.json"
    shared_meta = {
        "entity-key": {
            ATTR_UNIQUE_ID: "shared-corrupt-unique",
            ATTR_ENTITY_ID: "sensor.shared_identity",
            ATTR_DEVICE_ID: "shared-device",
            CONF_NAME: "Shared Identity",
            CONF_PLATFORM: "sensor",
        },
    }
    meta_file.write_text(json.dumps({
        "version": 1,
        ATTR_DEVICES: {"first": shared_meta, "second": shared_meta},
    }))
    monkeypatch.setattr(
        "custom_components.virtual_layer.cfg.default_meta_file",
        lambda _hass: str(meta_file),
    )
    options = {
        ATTR_DEVICES: {
            "Device": [{
                CONF_PLATFORM: "sensor",
                CONF_NAME: "Shared Identity",
                ATTR_ENTITY_KEY: "entity-key",
            }],
        },
    }
    first_entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "first"},
        options=options,
    )
    second_entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "second"},
        options=options,
    )
    first_entry.add_to_hass(hass)
    second_entry.add_to_hass(hass)

    with patch.object(
        hass.config_entries,
        "async_forward_entry_setups",
        AsyncMock(return_value=True),
    ):
        assert await async_setup_entry(hass, first_entry) is True
        assert await async_setup_entry(hass, second_entry) is True

    first_entity = hass.data[COMPONENT_DOMAIN]["first"][ATTR_ENTITIES]["sensor"][0]
    second_entity = hass.data[COMPONENT_DOMAIN]["second"][ATTR_ENTITIES]["sensor"][0]
    assert first_entity[ATTR_UNIQUE_ID] == "shared-corrupt-unique"
    assert second_entity[ATTR_UNIQUE_ID] != "shared-corrupt-unique"
    assert first_entity[ATTR_ENTITY_ID] == "sensor.shared_identity"
    assert second_entity[ATTR_ENTITY_ID] != first_entity[ATTR_ENTITY_ID]

    registry = er.async_get(hass)
    assert registry.async_get(first_entity[ATTR_ENTITY_ID]).config_entry_id == first_entry.entry_id
    assert registry.async_get(second_entity[ATTR_ENTITY_ID]).config_entry_id == second_entry.entry_id


async def test_setup_entry_creates_information_and_source_debug_sensors(
    hass,
    tmp_path,
    monkeypatch,
):
    meta_file = tmp_path / "virtual_layer.meta.json"
    monkeypatch.setattr(
        "custom_components.virtual_layer.cfg.default_meta_file",
        lambda _hass: str(meta_file),
    )
    hass.states.async_set(
        "sensor.washer_power",
        "150",
        {
            "unit": "W",
            ATTR_RESTORED: True,
            "access_token": "rotating-secret",
            "entity_picture": "/api/media?token=secret",
        },
    )
    hass.states.async_set("binary_sensor.washer_door", "on", {"battery": 95})
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "laundry"},
        options={
            ATTR_DEVICES: {
                "Laundry": [
                    {
                        CONF_PLATFORM: "sensor",
                        CONF_NAME: "Washer Summary",
                        ATTR_ENTITY_KEY: "washer-summary",
                        ATTR_ENTITY_ID: "sensor.virtual_washer",
                        CONF_INITIAL_VALUE: "idle",
                        CONF_INITIAL_AVAILABILITY: True,
                        CONF_AVAILABILITY_TEMPLATE: "{{ false }}",
                        CONF_PERSISTENT: False,
                        CONF_SOURCE_ENTITIES: [
                            "sensor.washer_power",
                            "binary_sensor.washer_door",
                        ],
                    },
                ],
            },
        },
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id) is True
    await hass.async_block_till_done()

    info = hass.states.get("sensor.virtual_washer_info")
    debug_power = hass.states.get("sensor.virtual_washer_debug1")
    debug_door = hass.states.get("sensor.virtual_washer_debug2")
    assert info is not None
    assert debug_power is not None
    assert debug_door is not None
    assert hass.states.get("sensor.virtual_washer").state == "unavailable"
    assert info.state == "configured"
    assert info.attributes["available"] is True
    assert "source_state" not in info.attributes
    assert info.attributes["virtual_entity_id"] == "sensor.virtual_washer"
    assert info.attributes["configured_source_entities"] == [
        "sensor.washer_power",
        "binary_sensor.washer_door",
    ]
    assert info.attributes["configuration"]["platform"] == "sensor"
    assert info.attributes["diagnostic_type"] == "configuration"
    assert info.attributes["friendly_name"].endswith("Washer Summary - Configuration")
    assert info.attributes["icon"] == "mdi:information-outline"
    assert debug_power.state == "150"
    assert debug_power.attributes["diagnostic_type"] == "source_state"
    assert debug_power.attributes["source_entity_name"] == "Washer Power"
    assert debug_power.attributes["friendly_name"].endswith(
        "Washer Summary - Source 1: Washer Power"
    )
    assert debug_power.attributes["icon"] == "mdi:bug-outline"
    assert debug_power.attributes["source_attributes"] == {"unit": "W"}
    assert debug_power.attributes["source_last_updated"] == hass.states.get(
        "sensor.washer_power"
    ).last_updated.isoformat()
    assert debug_power.attributes["source_last_changed"] == hass.states.get(
        "sensor.washer_power"
    ).last_changed.isoformat()
    assert debug_door.state == "on"
    assert debug_door.attributes["source_attributes"] == {"battery": 95}

    hass.states.async_set("sensor.washer_power", "160", {"unit": "W"})
    await hass.async_block_till_done()
    debug_power = hass.states.get("sensor.virtual_washer_debug1")
    source_power = hass.states.get("sensor.washer_power")
    assert debug_power.state == "160"
    assert debug_power.attributes["source_last_updated"] == source_power.last_updated.isoformat()
    assert debug_power.attributes["source_last_changed"] == source_power.last_changed.isoformat()

    entity_registry = er.async_get(hass)
    primary_entry = entity_registry.async_get("sensor.virtual_washer")
    assert entity_registry.async_get("sensor.virtual_washer_info").device_id == primary_entry.device_id
    assert entity_registry.async_get("sensor.virtual_washer_debug1").device_id == primary_entry.device_id

    entity_registry.async_update_entity(
        "sensor.virtual_washer_debug2",
        name="My Door Diagnostics",
    )
    entity_registry.async_update_entity(
        "sensor.virtual_washer",
        name="Laundry Status",
    )
    await hass.async_block_till_done()

    assert entity_registry.async_get(
        "sensor.virtual_washer_info"
    ).original_name == "Laundry Status - Configuration"
    assert entity_registry.async_get(
        "sensor.virtual_washer_debug1"
    ).original_name == "Laundry Status - Source 1: Washer Power"
    customized_debug = entity_registry.async_get("sensor.virtual_washer_debug2")
    assert customized_debug.original_name == "Laundry Status - Source 2: Washer Door"
    assert customized_debug.name == "My Door Diagnostics"

    assert await hass.config_entries.async_unload(entry.entry_id) is True
    assert await hass.config_entries.async_setup(entry.entry_id) is True
    await hass.async_block_till_done()

    assert entity_registry.async_get(
        "sensor.virtual_washer_info"
    ).original_name == "Laundry Status - Configuration"
    assert entity_registry.async_get(
        "sensor.virtual_washer_debug1"
    ).original_name == "Laundry Status - Source 1: Washer Power"

    entity_registry.async_update_entity("sensor.virtual_washer", name=None)
    await hass.async_block_till_done()
    assert entity_registry.async_get(
        "sensor.virtual_washer_info"
    ).original_name == "Washer Summary - Configuration"
    assert entity_registry.async_get(
        "sensor.virtual_washer_debug1"
    ).original_name == "Washer Summary - Source 1: Washer Power"

    result = await hass.config_entries.options.async_init(
        entry.entry_id,
        data={CONF_ACTION: ACTION_EDIT_ENTITY},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_ENTITY_KEY: json.dumps(
                ["key", "washer-summary"],
                separators=(",", ":"),
            ),
        },
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_REFERENCE_ENTITY_ID: [
                "sensor.washer_power",
                "binary_sensor.washer_door",
            ],
        },
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_HELPER_UPDATE_MODE: HELPER_UPDATE_KEEP},
    )
    reconfigured = _flatten_entity_form_sections(result["data_schema"]({}))
    reconfigured[CONF_ENTITY_NAME] = "Reconfigured Washer"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        reconfigured,
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    assert entity_registry.async_get(
        "sensor.virtual_washer"
    ).original_name == "Reconfigured Washer"
    assert entity_registry.async_get(
        "sensor.virtual_washer_info"
    ).original_name == "Reconfigured Washer - Configuration"
    assert entity_registry.async_get(
        "sensor.virtual_washer_debug1"
    ).original_name == "Reconfigured Washer - Source 1: Washer Power"
    customized_debug = entity_registry.async_get("sensor.virtual_washer_debug2")
    assert customized_debug.original_name == (
        "Reconfigured Washer - Source 2: Washer Door"
    )
    assert customized_debug.name == "My Door Diagnostics"


async def test_diagnostic_registry_defaults_are_migrated_without_overwriting_user_customization(hass):
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "diagnostics"},
    )
    entry.add_to_hass(hass)
    entity_registry = er.async_get(hass)
    diagnostic = entity_registry.async_get_or_create(
        "sensor",
        COMPONENT_DOMAIN,
        "virtual-unique.virtual_layer_diagnostic.info",
        suggested_object_id="virtual_entity_info",
        config_entry=entry,
        original_name="Info",
        original_icon="mdi:eye-outline",
    )
    entity_registry.async_update_entity(
        diagnostic.entity_id,
        name="My custom diagnostic name",
        icon="mdi:star",
    )

    _async_remove_orphaned_diagnostic_registry_entries(
        hass,
        entry,
        {
            "sensor": [{
                ATTR_UNIQUE_ID: diagnostic.unique_id,
                CONF_NAME: "Virtual Entity - Configuration",
                CONF_ICON: "mdi:information-outline",
            }],
        },
    )

    migrated = entity_registry.async_get(diagnostic.entity_id)
    assert migrated.original_name == "Virtual Entity - Configuration"
    assert migrated.original_icon == "mdi:information-outline"
    assert migrated.name == "My custom diagnostic name"
    assert migrated.icon == "mdi:star"


async def test_entity_registry_rename_is_restored_to_configured_id(
    hass,
    tmp_path,
    monkeypatch,
):
    meta_file = tmp_path / "virtual_layer.meta.json"
    monkeypatch.setattr(
        "custom_components.virtual_layer.cfg.default_meta_file",
        lambda _hass: str(meta_file),
    )
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "fixed-id"},
        options={
            ATTR_DEVICES: {
                "Fixed ID Device": [
                    {
                        CONF_PLATFORM: "sensor",
                        CONF_NAME: "Fixed ID Sensor",
                        ATTR_ENTITY_ID: "sensor.fixed_id_sensor",
                        CONF_INITIAL_VALUE: "ready",
                        CONF_INITIAL_AVAILABILITY: True,
                        CONF_PERSISTENT: False,
                    },
                ],
            },
        },
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id) is True
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)
    assert entity_registry.async_get("sensor.fixed_id_sensor") is not None
    entity_registry.async_update_entity(
        "sensor.fixed_id_sensor",
        new_entity_id="sensor.renamed_by_user",
    )
    await hass.async_block_till_done()

    restored = entity_registry.async_get("sensor.fixed_id_sensor")
    assert restored is not None
    assert restored.config_entry_id == entry.entry_id
    assert entity_registry.async_get("sensor.renamed_by_user") is None


async def test_stale_entity_id_guard_task_cannot_override_reconfigured_id(hass):
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "guard-race"},
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "sensor",
        COMPONENT_DOMAIN,
        "guard-race-unique",
        suggested_object_id="old_guard_id",
        config_entry=entry,
    )
    old_entities = {
        "sensor": [{
            ATTR_UNIQUE_ID: "guard-race-unique",
            ATTR_ENTITY_ID: "sensor.old_guard_id",
        }],
    }
    new_entities = {
        "sensor": [{
            ATTR_UNIQUE_ID: "guard-race-unique",
            ATTR_ENTITY_ID: "sensor.new_guard_id",
        }],
    }
    _async_setup_entity_id_guard(hass, entry, old_entities)
    pending = []
    with patch.object(
        hass,
        "async_create_task",
        side_effect=lambda coro: pending.append(coro),
    ):
        registry.async_update_entity(
            "sensor.old_guard_id",
            new_entity_id="sensor.new_guard_id",
        )

    assert len(pending) == 1
    _async_remove_entity_id_guard(hass, entry.entry_id)
    _async_setup_entity_id_guard(hass, entry, new_entities)
    await pending[0]

    assert registry.async_get("sensor.new_guard_id") is not None
    assert registry.async_get("sensor.old_guard_id") is None


async def test_stale_device_metadata_guard_task_cannot_override_new_config(hass):
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "device-guard-race"},
    )
    entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    device = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(COMPONENT_DOMAIN, "guard-device")},
        name="Guard Device",
        manufacturer="Old Manufacturer",
    )
    old_devices = [{
        ATTR_DEVICE_ID: "guard-device",
        CONF_NAME: "Guard Device",
        CONF_MANUFACTURER: "Old Manufacturer",
    }]
    new_devices = [{
        ATTR_DEVICE_ID: "guard-device",
        CONF_NAME: "Guard Device",
        CONF_MANUFACTURER: "New Manufacturer",
    }]
    _async_setup_device_metadata_guard(hass, entry, old_devices)
    pending = []
    with patch.object(
        hass,
        "async_create_task",
        side_effect=lambda coro: pending.append(coro),
    ):
        registry.async_update_device(device.id, manufacturer="External Edit")

    assert len(pending) == 1
    _async_remove_device_metadata_guard(hass, entry.entry_id)
    registry.async_update_device(device.id, manufacturer="New Manufacturer")
    _async_setup_device_metadata_guard(hass, entry, new_devices)
    await pending[0]

    assert registry.async_get(device.id).manufacturer == "New Manufacturer"


async def test_shared_device_metadata_guards_converge_without_update_loop(hass):
    entries = [
        MockConfigEntry(
            domain=COMPONENT_DOMAIN,
            data={ATTR_GROUP_NAME: f"shared-{index}"},
        )
        for index in range(2)
    ]
    for entry in entries:
        entry.add_to_hass(hass)

    registry = dr.async_get(hass)
    device = registry.async_get_or_create(
        config_entry_id=entries[0].entry_id,
        identifiers={(COMPONENT_DOMAIN, "shared-guard-device")},
        name="Shared Guard Device",
    )
    registry.async_update_device(
        device.id,
        add_config_entry_id=entries[1].entry_id,
    )
    configured_manufacturers = {
        entries[0].entry_id: "Manufacturer A",
        entries[1].entry_id: "Manufacturer B",
    }
    for entry in entries:
        _async_setup_device_metadata_guard(hass, entry, [{
            ATTR_DEVICE_ID: "shared-guard-device",
            CONF_NAME: "Shared Guard Device",
            CONF_MANUFACTURER: configured_manufacturers[entry.entry_id],
        }])

    pending = []
    with patch.object(
        hass,
        "async_create_task",
        side_effect=lambda coro: pending.append(coro),
    ):
        registry.async_update_device(device.id, manufacturer="External Edit")
        processed = 0
        while pending and processed < 20:
            await pending.pop(0)
            processed += 1

    for coroutine in pending:
        coroutine.close()
    for entry in entries:
        _async_remove_device_metadata_guard(hass, entry.entry_id)

    assert not pending
    owner_entry_id = min(entry.entry_id for entry in entries)
    assert registry.async_get(device.id).manufacturer == configured_manufacturers[
        owner_entry_id
    ]


async def test_setup_entry_removes_orphaned_entity_and_device_registry_entries(hass, tmp_path, monkeypatch):
    meta_file = tmp_path / "virtual_layer.meta.json"
    meta_file.write_text(json.dumps({
        "version": 1,
        ATTR_DEVICES: {
            "ui": {
                "orphan-key": {
                    ATTR_UNIQUE_ID: "orphan-unique",
                    ATTR_ENTITY_ID: "sensor.orphan_sensor",
                    ATTR_DEVICE_ID: "orphan-device",
                    CONF_NAME: "Orphan Sensor",
                    CONF_PLATFORM: "sensor",
                },
            },
        },
    }))
    monkeypatch.setattr(
        "custom_components.virtual_layer.cfg.default_meta_file",
        lambda _hass: str(meta_file),
    )

    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={ATTR_DEVICES: {}},
    )
    entry.add_to_hass(hass)
    device_registry = dr.async_get(hass)
    device_entry = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(COMPONENT_DOMAIN, "orphan-device")},
        name="Orphan Device",
    )
    entity_registry = er.async_get(hass)
    entity_registry.async_get_or_create(
        "sensor",
        COMPONENT_DOMAIN,
        "orphan-unique",
        suggested_object_id="orphan_sensor",
        config_entry=entry,
        device_id=device_entry.id,
    )

    assert await hass.config_entries.async_setup(entry.entry_id) is True
    await hass.async_block_till_done()

    assert entity_registry.async_get("sensor.orphan_sensor") is None
    assert device_registry.async_get_device(
        identifiers={(COMPONENT_DOMAIN, "orphan-device")},
    ) is None


async def test_orphan_cleanup_preserves_device_shared_with_another_entry(hass):
    entry = MockConfigEntry(domain=COMPONENT_DOMAIN, data={ATTR_GROUP_NAME: "ui"})
    shared_entry = MockConfigEntry(domain="test", data={})
    entry.add_to_hass(hass)
    shared_entry.add_to_hass(hass)
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(COMPONENT_DOMAIN, "shared-device")},
        name="Shared Device",
    )
    device_registry.async_update_device(
        device.id,
        add_config_entry_id=shared_entry.entry_id,
    )

    await _async_delete_virtual_device_from_registry(
        hass,
        entry,
        {ATTR_DEVICE_ID: "shared-device"},
    )

    remaining = device_registry.async_get(device.id)
    assert remaining is not None
    assert entry.entry_id not in remaining.config_entries
    assert shared_entry.entry_id in remaining.config_entries


async def test_orphan_cleanup_ignores_registry_data_owned_by_another_entry(hass):
    entry = MockConfigEntry(domain=COMPONENT_DOMAIN, data={ATTR_GROUP_NAME: "ui"})
    owner = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "owner"},
    )
    entry.add_to_hass(hass)
    owner.add_to_hass(hass)

    device_registry = dr.async_get(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=owner.entry_id,
        identifiers={(COMPONENT_DOMAIN, "owned-device")},
        name="Owned Device",
    )
    entity_registry = er.async_get(hass)
    owned_entity = entity_registry.async_get_or_create(
        "sensor",
        COMPONENT_DOMAIN,
        "owned-unique-id",
        suggested_object_id="owned_sensor",
        config_entry=owner,
        device_id=device.id,
    )

    orphan_metadata = {
        ATTR_UNIQUE_ID: "owned-unique-id",
        ATTR_ENTITY_ID: owned_entity.entity_id,
        ATTR_DEVICE_ID: "owned-device",
        CONF_PLATFORM: "sensor",
    }
    await _async_delete_virtual_entity_from_registry(
        hass,
        entry,
        orphan_metadata,
        active_device_ids=set(),
        active_entity_unique_ids=set(),
    )

    assert entity_registry.async_get(owned_entity.entity_id) is not None
    assert device_registry.async_get(device.id) is not None


async def test_orphan_cleanup_does_not_remove_active_duplicate_identity(hass):
    entry = MockConfigEntry(domain=COMPONENT_DOMAIN, data={ATTR_GROUP_NAME: "ui"})
    entry.add_to_hass(hass)
    entity_registry = er.async_get(hass)
    active = entity_registry.async_get_or_create(
        "sensor",
        COMPONENT_DOMAIN,
        "active-unique-id",
        suggested_object_id="active_sensor",
        config_entry=entry,
    )

    await _async_delete_virtual_entity_from_registry(
        hass,
        entry,
        {
            ATTR_UNIQUE_ID: "active-unique-id",
            ATTR_ENTITY_ID: active.entity_id,
            CONF_PLATFORM: "sensor",
        },
        active_device_ids=set(),
        active_entity_unique_ids={"active-unique-id"},
    )

    assert entity_registry.async_get(active.entity_id) is not None


async def test_setup_entry_removes_stale_device_after_device_id_change(hass, tmp_path, monkeypatch):
    meta_file = tmp_path / "virtual_layer.meta.json"
    monkeypatch.setattr(
        "custom_components.virtual_layer.cfg.default_meta_file",
        lambda _hass: str(meta_file),
    )
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={
            ATTR_DEVICES: {"Laundry": []},
            ATTR_DEVICE_ATTRIBUTES: {
                "Laundry": {ATTR_DEVICE_ID: "laundry-new", CONF_NAME: "Laundry"},
            },
        },
    )
    entry.add_to_hass(hass)
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(COMPONENT_DOMAIN, "laundry-old")},
        name="Laundry",
    )

    assert await hass.config_entries.async_setup(entry.entry_id) is True

    assert device_registry.async_get_device(
        identifiers={(COMPONENT_DOMAIN, "laundry-old")},
    ) is None
    assert device_registry.async_get_device(
        identifiers={(COMPONENT_DOMAIN, "laundry-new")},
    ) is not None


async def test_setup_entry_registers_same_name_devices_separately_by_id(
    hass, tmp_path, monkeypatch
):
    meta_file = tmp_path / "virtual_layer.meta.json"
    monkeypatch.setattr(
        "custom_components.virtual_layer.cfg.default_meta_file",
        lambda _hass: str(meta_file),
    )
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={
            ATTR_DEVICES: {"washer-1": [], "washer-2": []},
            ATTR_DEVICE_ATTRIBUTES: {
                "washer-1": {ATTR_DEVICE_ID: "washer-1", CONF_NAME: "Washer"},
                "washer-2": {ATTR_DEVICE_ID: "washer-2", CONF_NAME: "Washer"},
            },
        },
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id) is True

    device_registry = dr.async_get(hass)
    first = device_registry.async_get_device(
        identifiers={(COMPONENT_DOMAIN, "washer-1")},
    )
    second = device_registry.async_get_device(
        identifiers={(COMPONENT_DOMAIN, "washer-2")},
    )
    assert first is not None
    assert second is not None
    assert first.id != second.id
    assert first.name == second.name == "Washer"


async def test_setup_entry_syncs_and_restores_device_registry_metadata(hass, tmp_path, monkeypatch):
    meta_file = tmp_path / "virtual_layer.meta.json"
    monkeypatch.setattr(
        "custom_components.virtual_layer.cfg.default_meta_file",
        lambda _hass: str(meta_file),
    )
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={
            ATTR_DEVICES: {"Child": []},
            ATTR_DEVICE_ATTRIBUTES: {
                "Child": {
                    ATTR_DEVICE_ID: "child-device",
                    CONF_NAME: "Child",
                    CONF_MANUFACTURER: "Acme",
                    CONF_MODEL: "Bridge Sensor",
                    CONF_SW_VERSION: "2026.8",
                    CONF_CONFIGURATION_URL: "https://example.test/device",
                    CONF_SUGGESTED_AREA: "Kitchen",
                },
            },
        },
    )
    parent_entry = MockConfigEntry(domain="test_parent", data={})
    entry.add_to_hass(hass)
    parent_entry.add_to_hass(hass)
    device_registry = dr.async_get(hass)
    parent_device = device_registry.async_get_or_create(
        config_entry_id=parent_entry.entry_id,
        identifiers={("test_parent", "hub")},
        name="Hub",
    )
    kitchen = ar.async_get(hass).async_create("Kitchen")
    entry.options[ATTR_DEVICE_ATTRIBUTES]["Child"][CONF_VIA_DEVICE_ID] = parent_device.id

    assert await hass.config_entries.async_setup(entry.entry_id) is True
    await hass.async_block_till_done()

    child_device = device_registry.async_get_device(
        identifiers={(COMPONENT_DOMAIN, "child-device")},
    )
    assert child_device is not None
    assert child_device.manufacturer == "Acme"
    assert child_device.model == "Bridge Sensor"
    assert child_device.sw_version == "2026.8"
    assert str(child_device.configuration_url) == "https://example.test/device"
    assert child_device.area_id == kitchen.id
    assert child_device.via_device_id == parent_device.id

    device_registry.async_update_device(
        child_device.id,
        manufacturer="Wrong",
        area_id=None,
    )
    await hass.async_block_till_done()
    await hass.async_block_till_done()

    child_device = device_registry.async_get(child_device.id)
    assert child_device.manufacturer == "Acme"
    assert child_device.area_id == kitchen.id

    assert await async_unload_entry(hass, entry) is True
    hass.config_entries.async_update_entry(
        entry,
        options={
            ATTR_DEVICES: {"Child": []},
            ATTR_DEVICE_ATTRIBUTES: {
                "Child": {
                    ATTR_DEVICE_ID: "child-device",
                    CONF_NAME: "Child",
                    CONF_MANUFACTURER: "Acme",
                    CONF_MODEL: "Bridge Sensor",
                    CONF_SW_VERSION: "2026.8",
                },
            },
        },
    )
    assert await async_setup_entry(hass, entry) is True
    await hass.async_block_till_done()

    child_device = device_registry.async_get_device(
        identifiers={(COMPONENT_DOMAIN, "child-device")},
    )
    assert child_device is not None
    assert child_device.configuration_url is None
    assert child_device.area_id is None
    assert child_device.via_device_id is None


async def test_setup_entry_clears_removed_entity_default_icon(hass, tmp_path, monkeypatch):
    meta_file = tmp_path / "virtual_layer.meta.json"
    monkeypatch.setattr(
        "custom_components.virtual_layer.cfg.default_meta_file",
        lambda _hass: str(meta_file),
    )
    entity = {
        CONF_PLATFORM: "sensor",
        CONF_NAME: "Virtual Meter",
        CONF_ICON: "mdi:flash",
        ATTR_ENTITY_KEY: "virtual-meter",
        ATTR_ENTITY_ID: "sensor.virtual_meter",
        CONF_INITIAL_VALUE: "0",
        CONF_INITIAL_AVAILABILITY: True,
        CONF_PERSISTENT: False,
    }
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={
            ATTR_DEVICES: {"Meter": [entity]},
            ATTR_DEVICE_ATTRIBUTES: {
                "Meter": {ATTR_DEVICE_ID: "meter-device", CONF_NAME: "Meter"},
            },
        },
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id) is True
    await hass.async_block_till_done()
    assert er.async_get(hass).async_get("sensor.virtual_meter").original_icon == "mdi:flash"

    assert await hass.config_entries.async_unload(entry.entry_id) is True
    entity.pop(CONF_ICON)
    assert await hass.config_entries.async_setup(entry.entry_id) is True
    await hass.async_block_till_done()

    assert er.async_get(hass).async_get("sensor.virtual_meter").original_icon is None


async def test_device_id_change_moves_existing_entity_to_new_device(hass, tmp_path, monkeypatch):
    meta_file = tmp_path / "virtual_layer.meta.json"
    monkeypatch.setattr(
        "custom_components.virtual_layer.cfg.default_meta_file",
        lambda _hass: str(meta_file),
    )
    entity = {
        CONF_PLATFORM: "sensor",
        CONF_NAME: "Washer Phase",
        ATTR_ENTITY_ID: "sensor.washer_phase",
        ATTR_ENTITY_KEY: "washer-phase",
        CONF_INITIAL_VALUE: "idle",
        CONF_INITIAL_AVAILABILITY: True,
        CONF_PERSISTENT: False,
    }
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={
            ATTR_DEVICES: {"Laundry": [entity]},
            ATTR_DEVICE_ATTRIBUTES: {
                "Laundry": {ATTR_DEVICE_ID: "laundry-old", CONF_NAME: "Laundry"},
            },
        },
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id) is True
    await hass.async_block_till_done()
    entity_registry = er.async_get(hass)
    old_device_id = entity_registry.async_get("sensor.washer_phase").device_id

    assert await hass.config_entries.async_unload(entry.entry_id) is True
    hass.config_entries.async_update_entry(
        entry,
        options={
            ATTR_DEVICES: {"Laundry": [entity]},
            ATTR_DEVICE_ATTRIBUTES: {
                "Laundry": {ATTR_DEVICE_ID: "laundry-new", CONF_NAME: "Laundry"},
            },
        },
    )
    assert await hass.config_entries.async_setup(entry.entry_id) is True
    await hass.async_block_till_done()

    new_device_id = entity_registry.async_get("sensor.washer_phase").device_id
    assert new_device_id != old_device_id
    assert (
        er.async_get(hass).async_get("sensor.washer_phase_info").device_id
        == new_device_id
    )
    assert dr.async_get(hass).async_get(old_device_id) is None
    assert dr.async_get(hass).async_get(new_device_id) is not None


async def test_reconfigure_group_name_preserves_identity_and_cleans_runtime_cache(
    hass,
    tmp_path,
    monkeypatch,
):
    meta_file = tmp_path / "virtual_layer.meta.json"
    monkeypatch.setattr(
        "custom_components.virtual_layer.cfg.default_meta_file",
        lambda _hass: str(meta_file),
    )
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        title="old - virtual_layer",
        data={ATTR_GROUP_NAME: "old"},
        options={
            ATTR_DEVICES: {
                "Renamed Device": [{
                    CONF_PLATFORM: "sensor",
                    CONF_NAME: "Stable Sensor",
                    ATTR_ENTITY_KEY: "stable-key",
                    ATTR_ENTITY_ID: "sensor.stable_sensor",
                    "initial_value": "ready",
                    "persistent": False,
                }],
            },
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id) is True
    await hass.async_block_till_done()
    assert entry.title == "old"

    original_registry_entry = er.async_get(hass).async_get("sensor.stable_sensor")
    original_unique_id = original_registry_entry.unique_id
    assert "old" in hass.data[COMPONENT_DOMAIN]

    result = await hass.config_entries.flow.async_init(
        COMPONENT_DOMAIN,
        context={
            "source": SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
        data={ATTR_GROUP_NAME: "new"},
    )
    assert result["type"] == FlowResultType.ABORT
    await hass.async_block_till_done()

    assert entry.data[ATTR_GROUP_NAME] == "new"
    assert entry.title == "new"
    assert "old" not in hass.data[COMPONENT_DOMAIN]
    assert hass.data[COMPONENT_DOMAIN]["new"][ATTR_CONFIG_ENTRY_ID] == entry.entry_id
    renamed_registry_entry = er.async_get(hass).async_get("sensor.stable_sensor")
    assert renamed_registry_entry.unique_id == original_unique_id
    saved_groups = json.loads(meta_file.read_text())[ATTR_DEVICES]
    assert "old" not in saved_groups
    assert saved_groups["new"]["stable-key"][ATTR_UNIQUE_ID] == original_unique_id


async def test_unload_entry_is_idempotent_when_group_data_is_missing(hass):
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "missing"},
    )
    entry.add_to_hass(hass)
    hass.data[COMPONENT_DOMAIN] = {}

    assert await async_unload_entry(hass, entry) is True
    assert hass.data[COMPONENT_DOMAIN] == {}


async def test_unload_entry_removes_empty_legacy_runtime_group(hass):
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "legacy-empty"},
    )
    entry.add_to_hass(hass)
    hass.data[COMPONENT_DOMAIN] = {"legacy-empty": {}}

    assert await async_unload_entry(hass, entry) is True
    assert hass.data[COMPONENT_DOMAIN] == {}


async def test_remove_entry_cleans_metadata_registries_and_state_even_after_failed_setup(
    hass,
    tmp_path,
    monkeypatch,
):
    meta_file = tmp_path / "virtual_layer.meta.json"
    meta_file.write_text(json.dumps({
        "version": 99,
        ATTR_DEVICES: {
            "ui": {
                "stale-key": {
                    ATTR_UNIQUE_ID: "stale-unique",
                    ATTR_ENTITY_ID: "sensor.stale_sensor",
                    ATTR_DEVICE_ID: "stale-device",
                },
            },
            "other": {},
        },
    }))
    monkeypatch.setattr(
        "custom_components.virtual_layer.cfg.default_meta_file",
        lambda _hass: str(meta_file),
    )
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={ATTR_DEVICES: "bad-shape"},
    )
    entry.add_to_hass(hass)

    device_registry = dr.async_get(hass)
    device_entry = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(COMPONENT_DOMAIN, "stale-device")},
        name="Stale Device",
    )
    entity_registry = er.async_get(hass)
    entity_registry.async_get_or_create(
        "sensor",
        COMPONENT_DOMAIN,
        "stale-unique",
        suggested_object_id="stale_sensor",
        config_entry=entry,
        device_id=device_entry.id,
    )
    hass.states.async_set("tag.stale_tag", "old")
    hass.data[COMPONENT_DOMAIN] = {
        "ui": {
            ATTR_ENTITIES: {
                "tag": [
                    {
                        ATTR_ENTITY_ID: "tag.stale_tag",
                        ATTR_UNIQUE_ID: "tag-stale",
                        ATTR_DEVICE_ID: "stale-device",
                    },
                ],
            },
        },
    }

    await async_remove_entry(hass, entry)

    assert json.loads(meta_file.read_text())[ATTR_DEVICES] == {"other": {}}
    assert entity_registry.async_get("sensor.stale_sensor") is None
    assert device_registry.async_get_device(
        identifiers={(COMPONENT_DOMAIN, "stale-device")},
    ) is None
    assert hass.states.get("tag.stale_tag") is None
    assert "ui" not in hass.data[COMPONENT_DOMAIN]


async def test_state_only_virtual_entities_can_be_updated_with_services(hass):
    hass.data[COMPONENT_DOMAIN] = {
        "ui": {
            ATTR_ENTITIES: {
                "tag": [
                    {
                        ATTR_ENTITY_ID: "tag.virtual_tag",
                        ATTR_UNIQUE_ID: "tag-virtual-tag",
                        ATTR_DEVICE_ID: "tag-device",
                        "configured": "fixed",
                    },
                ],
            },
        },
    }
    hass.states.async_set(
        "tag.virtual_tag",
        "old",
        {
            ATTR_AVAILABLE: True,
            "source": "initial",
            "configured": "fixed",
        },
    )

    await async_virtual_set_state_service(
        hass,
        SimpleNamespace(data={ATTR_ENTITY_ID: ["tag.virtual_tag"], ATTR_VALUE: "new"}),
    )
    await async_virtual_set_attributes_service(
        hass,
        SimpleNamespace(data={
            ATTR_ENTITY_ID: ["tag.virtual_tag"],
            "attributes": {
                "source": "service",
                "extra": 1,
                "configured": "overridden",
            },
        }),
    )
    await async_virtual_clear_attributes_service(
        hass,
        SimpleNamespace(data={
            ATTR_ENTITY_ID: ["tag.virtual_tag"],
            "attributes": ["source", "configured"],
        }),
    )
    await async_virtual_set_availability_service(
        hass,
        SimpleNamespace(data={"entity_id": ["tag.virtual_tag"], "value": False}),
    )

    state = hass.states.get("tag.virtual_tag")
    assert state.state == "new"
    assert state.attributes[ATTR_AVAILABLE] is False
    assert state.attributes["extra"] == 1
    assert state.attributes["configured"] == "fixed"
    assert "source" not in state.attributes


def test_state_only_invalid_templates_keep_current_values(hass):
    entity = {
        ATTR_ENTITY_ID: "tag.template_availability",
        CONF_AVAILABILITY_TEMPLATE: "{{ 'not-a-boolean' }}",
        CONF_NATIVE_TEMPLATES: {
            "supported_features": "{{ true }}",
            "latitude": "{{ true }}",
            "longitude": "{{ false }}",
            "confidence": "{{ true }}",
        },
    }
    hass.states.async_set(
        entity[ATTR_ENTITY_ID],
        "ready",
        {
            ATTR_AVAILABLE: True,
            "supported_features": 8,
            "latitude": 37.5,
            "longitude": 127.0,
            "confidence": 75,
        },
    )

    _async_apply_state_only_templates(hass, entity)

    state = hass.states.get(entity[ATTR_ENTITY_ID])
    assert state.attributes[ATTR_AVAILABLE] is True
    assert state.attributes["supported_features"] == 8
    assert state.attributes["latitude"] == 37.5
    assert state.attributes["longitude"] == 127.0
    assert state.attributes["confidence"] == 75

    _async_apply_state_only_event_hook(
        hass,
        entity,
        {CONF_AVAILABILITY_TEMPLATE: "{{ 'not-a-boolean' }}"},
        SimpleNamespace(data={}),
    )

    assert hass.states.get(entity[ATTR_ENTITY_ID]).attributes[ATTR_AVAILABLE] is True


def test_state_only_unavailable_source_preserves_strict_native_values(
    hass,
    caplog,
):
    entity_id = "geolocation.strict_copy"
    source_state_id = "sensor.location_state"
    source_latitude_id = "sensor.location_latitude"
    entity = {
        ATTR_ENTITY_ID: entity_id,
        CONF_VALUE_TEMPLATE: f"{{{{ states({source_state_id!r}) }}}}",
        CONF_AVAILABILITY_TEMPLATE: (
            f"{{{{ states({source_state_id!r}) not in "
            f"['unknown', 'unavailable'] and states({source_latitude_id!r}) "
            f"not in ['unknown', 'unavailable'] }}}}"
        ),
        CONF_NATIVE_TEMPLATES: {
            "latitude": f"{{{{ states({source_latitude_id!r}) }}}}",
        },
    }
    hass.states.async_set(source_state_id, "home")
    hass.states.async_set(source_latitude_id, "37.5")
    hass.states.async_set(
        entity_id,
        "home",
        {ATTR_AVAILABLE: True, ATTR_LATITUDE: 37.5},
    )

    hass.states.async_set(source_state_id, "unavailable")
    hass.states.async_set(source_latitude_id, "unavailable")
    _async_apply_state_only_templates(hass, entity)

    state = hass.states.get(entity_id)
    assert state.state == "home"
    assert state.attributes[ATTR_AVAILABLE] is False
    assert state.attributes[ATTR_LATITUDE] == 37.5
    assert "Unable to render" not in caplog.text

    hass.states.async_set(source_state_id, "not_home")
    hass.states.async_set(source_latitude_id, "38.25")
    _async_apply_state_only_templates(hass, entity)

    state = hass.states.get(entity_id)
    assert state.state == "not_home"
    assert state.attributes[ATTR_AVAILABLE] is True
    assert state.attributes[ATTR_LATITUDE] == 38.25


async def test_state_only_reload_falls_back_from_unavailable_state(
    hass,
    tmp_path,
    monkeypatch,
):
    meta_file = tmp_path / "virtual_layer.meta.json"
    monkeypatch.setattr(
        "custom_components.virtual_layer.cfg.default_meta_file",
        lambda _hass: str(meta_file),
    )
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={ATTR_DEVICES: {"Tags": [{
            "platform": "tag",
            "name": "Persistent Tag",
            ATTR_ENTITY_ID: "tag.persistent_tag",
            CONF_INITIAL_VALUE: "configured",
            CONF_PERSISTENT: True,
        }]}},
    )
    entry.add_to_hass(hass)

    with patch.object(
        hass.config_entries,
        "async_forward_entry_setups",
        AsyncMock(return_value=True),
    ):
        assert await async_setup_entry(hass, entry) is True

    state = hass.states.get("tag.persistent_tag")
    hass.states.async_set(state.entity_id, "unavailable", state.attributes)
    assert await async_unload_entry(hass, entry) is True

    with patch.object(
        hass.config_entries,
        "async_forward_entry_setups",
        AsyncMock(return_value=True),
    ):
        assert await async_setup_entry(hass, entry) is True

    assert hass.states.get("tag.persistent_tag").state == "configured"


async def test_state_only_entities_honor_persistent_setting_across_reload(
    hass,
    tmp_path,
    monkeypatch,
):
    meta_file = tmp_path / "virtual_layer.meta.json"
    monkeypatch.setattr(
        "custom_components.virtual_layer.cfg.default_meta_file",
        lambda _hass: str(meta_file),
    )
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={ATTR_DEVICES: {"Tags": [
            {
                "platform": "tag",
                "name": "Persistent Tag",
                ATTR_ENTITY_ID: "tag.persistent_tag",
                CONF_INITIAL_VALUE: "initial",
                CONF_PERSISTENT: True,
            },
            {
                "platform": "tag",
                "name": "Transient Tag",
                ATTR_ENTITY_ID: "tag.transient_tag",
                CONF_INITIAL_VALUE: "initial",
                CONF_PERSISTENT: False,
            },
        ]}},
    )
    entry.add_to_hass(hass)

    with patch.object(
        hass.config_entries,
        "async_forward_entry_setups",
        AsyncMock(return_value=True),
    ):
        assert await async_setup_entry(hass, entry) is True

    persistent = hass.states.get("tag.persistent_tag")
    transient = hass.states.get("tag.transient_tag")
    hass.states.async_set(
        persistent.entity_id,
        "changed",
        {**persistent.attributes, "runtime_value": {"nested": [1, 2]}},
    )
    hass.states.async_set(transient.entity_id, "changed", transient.attributes)

    assert await async_unload_entry(hass, entry) is True
    with patch.object(
        hass.config_entries,
        "async_forward_entry_setups",
        AsyncMock(return_value=True),
    ):
        assert await async_setup_entry(hass, entry) is True

    persistent = hass.states.get("tag.persistent_tag")
    transient = hass.states.get("tag.transient_tag")
    assert persistent.state == "changed"
    assert persistent.attributes["runtime_value"] == {"nested": [1, 2]}
    assert transient.state == "initial"


async def test_state_only_reload_removes_attributes_deleted_from_configuration(
    hass,
    tmp_path,
    monkeypatch,
):
    meta_file = tmp_path / "virtual_layer.meta.json"
    monkeypatch.setattr(
        "custom_components.virtual_layer.cfg.default_meta_file",
        lambda _hass: str(meta_file),
    )
    entity = {
        "platform": "tag",
        "name": "Managed Tag",
        ATTR_ENTITY_ID: "tag.managed_tag",
        CONF_INITIAL_VALUE: "initial",
        CONF_PERSISTENT: True,
        CONF_ATTRIBUTES: {"removed_attribute": "configured"},
        CONF_NATIVE_TEMPLATES: {"removed_native": "{{ 'rendered' }}"},
    }
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={ATTR_DEVICES: {"Tags": [entity]}},
    )
    entry.add_to_hass(hass)

    with patch.object(
        hass.config_entries,
        "async_forward_entry_setups",
        AsyncMock(return_value=True),
    ):
        assert await async_setup_entry(hass, entry) is True

    state = hass.states.get("tag.managed_tag")
    assert state.attributes["removed_attribute"] == "configured"
    assert state.attributes["removed_native"] == "rendered"
    hass.states.async_set(
        state.entity_id,
        "changed",
        {
            **state.attributes,
            "runtime_attribute": "preserved",
            ATTR_RESTORED: True,
            "access_token": "stale-token",
            "entity_picture": "/api/media?token=stale",
        },
    )

    next_entity = dict(entity)
    next_entity.pop(CONF_ATTRIBUTES)
    next_entity.pop(CONF_NATIVE_TEMPLATES)
    hass.config_entries.async_update_entry(
        entry,
        options={ATTR_DEVICES: {"Tags": [next_entity]}},
    )
    assert await async_unload_entry(hass, entry) is True
    with patch.object(
        hass.config_entries,
        "async_forward_entry_setups",
        AsyncMock(return_value=True),
    ):
        assert await async_setup_entry(hass, entry) is True

    restored = hass.states.get("tag.managed_tag")
    assert restored.state == "changed"
    assert "removed_attribute" not in restored.attributes
    assert "removed_native" not in restored.attributes
    assert restored.attributes["runtime_attribute"] == "preserved"
    assert set(restored.attributes).isdisjoint(
        TRANSIENT_SOURCE_ATTRIBUTE_NAMES
    )
    assert restored.attributes[ATTR_VIRTUAL_ATTRIBUTES] == []


async def test_state_only_services_do_not_update_unmanaged_entities(hass):
    hass.data[COMPONENT_DOMAIN] = {
        "ui": {
            ATTR_ENTITIES: {
                "tag": [
                    {
                        ATTR_ENTITY_ID: "tag.virtual_tag",
                        ATTR_UNIQUE_ID: "tag-virtual-tag",
                        ATTR_DEVICE_ID: "tag-device",
                    },
                ],
            },
        },
    }
    hass.states.async_set(
        "tag.external_tag",
        "external",
        {"source": "outside"},
    )

    with pytest.raises(HomeAssistantError, match="not managed by virtual_layer"):
        await async_virtual_set_state_service(
            hass,
            SimpleNamespace(data={
                ATTR_ENTITY_ID: ["tag.external_tag"],
                ATTR_VALUE: "changed",
            }),
        )

    state = hass.states.get("tag.external_tag")
    assert state.state == "external"
    assert state.attributes["source"] == "outside"

    hass.states.async_set("tag.virtual_tag", "unchanged")
    with pytest.raises(HomeAssistantError, match="not managed by virtual_layer"):
        await async_virtual_set_state_service(
            hass,
            SimpleNamespace(data={
                ATTR_ENTITY_ID: ["tag.virtual_tag", "tag.external_tag"],
                ATTR_VALUE: "partially_changed",
            }),
        )
    assert hass.states.get("tag.virtual_tag").state == "unchanged"


async def test_virtual_services_reject_non_virtual_platform_entities(hass):
    entity_registry = er.async_get(hass)
    external_entry = entity_registry.async_get_or_create(
        "sensor",
        "external_integration",
        "external-sensor",
        suggested_object_id="external_sensor",
    )
    external_entity = Mock()
    hass.data["sensor"] = Mock(get_entity=Mock(return_value=external_entity))

    with pytest.raises(HomeAssistantError, match="not managed by virtual_layer"):
        await async_virtual_set_state_service(
            hass,
            SimpleNamespace(data={
                ATTR_ENTITY_ID: [external_entry.entity_id],
                ATTR_VALUE: "42",
            }),
        )

    external_entity.set_state.assert_not_called()


async def test_virtual_attributes_cannot_override_reserved_entity_metadata(hass):
    config = {
        CONF_NAME: "Protected Attributes",
        ATTR_ENTITY_ID: "sensor.protected_attributes",
        ATTR_UNIQUE_ID: "protected_attributes",
        ATTR_DEVICE_ID: "Protected",
        CONF_INITIAL_VALUE: "ready",
        CONF_INITIAL_AVAILABILITY: True,
        CONF_PERSISTENT: True,
        CONF_ATTRIBUTES: {"available": False, "custom": "value"},
    }
    entity = VirtualSensor(config, False)
    entity.hass = hass
    entity.async_schedule_update_ha_state = Mock()
    entity._create_state(config)
    entity.set_attributes({"persistent": False, "custom": "updated"})

    assert entity.extra_state_attributes[ATTR_AVAILABLE] is True
    assert entity.extra_state_attributes[CONF_PERSISTENT] is True
    assert entity.extra_state_attributes["custom"] == "updated"


async def test_state_only_attributes_cannot_override_reserved_entity_metadata(hass):
    hass.data[COMPONENT_DOMAIN] = {
        "ui": {
            ATTR_ENTITIES: {
                "tag": [{ATTR_ENTITY_ID: "tag.protected", ATTR_UNIQUE_ID: "protected"}],
            },
        },
    }
    hass.states.async_set(
        "tag.protected",
        "ready",
        {ATTR_AVAILABLE: True, CONF_PERSISTENT: True, "custom": "original"},
    )

    await async_virtual_set_attributes_service(
        hass,
        SimpleNamespace(data={
            ATTR_ENTITY_ID: ["tag.protected"],
            ATTR_ATTRIBUTES: {
                ATTR_AVAILABLE: False,
                CONF_PERSISTENT: False,
                "custom": "updated",
            },
        }),
    )

    state = hass.states.get("tag.protected")
    assert state.attributes[ATTR_AVAILABLE] is True
    assert state.attributes[CONF_PERSISTENT] is True
    assert state.attributes["custom"] == "updated"


@pytest.mark.parametrize(
    ("domain", "service", "service_data", "method"),
    [
        (
            "binary_sensor",
            virtual_binary_sensor.async_virtual_on_service,
            {},
            "turn_on",
        ),
        (
            "sensor",
            virtual_sensor.async_virtual_set_service,
            {ATTR_VALUE: "42"},
            "set",
        ),
        (
            "device_tracker",
            virtual_device_tracker.async_virtual_move_service,
            {"location": "home"},
            "move_to_location",
        ),
    ],
)
async def test_platform_services_reject_non_virtual_entities(
    hass,
    domain,
    service,
    service_data,
    method,
):
    entity_registry = er.async_get(hass)
    external_entry = entity_registry.async_get_or_create(
        domain,
        "external_integration",
        f"external-{domain}",
        suggested_object_id=f"external_{domain}",
    )
    external_entity = Mock()
    hass.data[domain] = Mock(get_entity=Mock(return_value=external_entity))

    with pytest.raises(HomeAssistantError, match="not managed by virtual_layer"):
        await service(
            hass,
            SimpleNamespace(data={
                ATTR_ENTITY_ID: [external_entry.entity_id],
                **service_data,
            }),
        )

    getattr(external_entity, method).assert_not_called()


def test_climate_schema_accepts_template_sources_and_default_state():
    config = CLIMATE_SCHEMA({
        CONF_NAME: "Virtual Thermostat",
        CONF_INITIAL_VALUE: "off",
        CONF_TEMPLATE_SOURCES: {
            "outdoor_temperature": {
                ATTR_ENTITY_ID: "sensor.outdoor_temperature",
                CONF_ATTRIBUTE: "state",
            },
        },
    })

    assert config[CONF_TEMPLATE_SOURCES]["outdoor_temperature"][ATTR_ENTITY_ID] == "sensor.outdoor_temperature"


async def test_direct_jinja_template_reacts_without_explicit_source_list(hass):
    config = {
        CONF_NAME: "Template Sensor",
        ATTR_ENTITY_ID: "sensor.template_sensor",
        ATTR_UNIQUE_ID: "template-sensor",
        ATTR_DEVICE_ID: "template-device",
        CONF_INITIAL_VALUE: "0",
        CONF_INITIAL_AVAILABILITY: True,
        CONF_PERSISTENT: False,
        CONF_VALUE_TEMPLATE: "{{ states('sensor.template_source') }}",
    }
    entity = VirtualSensor(config, False)
    entity.hass = hass
    entity.async_schedule_update_ha_state = Mock()
    entity._create_state(config)
    entity._setup_templates()

    hass.states.async_set("sensor.template_source", "17")
    await hass.async_block_till_done()

    assert entity._attr_state == "17"
    await entity.async_will_remove_from_hass()


async def test_icon_template_reacts_to_template_source_changes(hass):
    hass.states.async_set("binary_sensor.front_door", "off")
    config = {
        CONF_NAME: "Template Icon Sensor",
        ATTR_ENTITY_ID: "sensor.template_icon_sensor",
        ATTR_UNIQUE_ID: "template-icon-sensor",
        ATTR_DEVICE_ID: "template-icon-device",
        CONF_INITIAL_VALUE: "ready",
        CONF_INITIAL_AVAILABILITY: True,
        CONF_PERSISTENT: False,
        CONF_ICON: "mdi:door",
        CONF_ICON_TEMPLATE: (
            "{{ 'mdi:door-open' if door == 'on' else 'mdi:door-closed' }}"
        ),
        CONF_TEMPLATE_SOURCES: {
            "door": {
                ATTR_ENTITY_ID: "binary_sensor.front_door",
                CONF_ATTRIBUTE: "state",
            },
        },
    }
    entity = VirtualSensor(config, False)
    entity.hass = hass
    entity.async_schedule_update_ha_state = Mock()
    entity._create_state(config)
    entity._setup_templates()
    entity._apply_templates()

    assert entity.icon == "mdi:door-closed"

    hass.states.async_set("binary_sensor.front_door", "on")
    await hass.async_block_till_done()

    assert entity.icon == "mdi:door-open"
    assert entity.async_schedule_update_ha_state.called
    await entity.async_will_remove_from_hass()


async def test_custom_state_hook_updates_virtual_entity_from_configured_event(hass):
    config = {
        CONF_NAME: "Hook Sensor",
        ATTR_ENTITY_ID: "sensor.hook_sensor",
        ATTR_UNIQUE_ID: "hook-sensor",
        ATTR_DEVICE_ID: "hook-device",
        CONF_INITIAL_VALUE: "idle",
        CONF_INITIAL_AVAILABILITY: True,
        CONF_PERSISTENT: False,
        CONF_EVENT_HOOKS: [{
            "trigger": "state",
            ATTR_ENTITY_ID: ["sensor.hook_source"],
            CONF_ATTRIBUTE: ["mode"],
            CONF_VALUE_TEMPLATE: "{{ trigger.to_state.attributes.mode }}",
            CONF_ATTRIBUTE_TEMPLATES: {
                "hook_entity": "{{ trigger.entity_id }}",
                "previous_state": "{{ trigger.from }}",
            },
        }],
    }
    entity = VirtualSensor(config, False)
    entity.hass = hass
    entity.async_schedule_update_ha_state = Mock()
    entity._create_state(config)
    entity._setup_templates()

    hass.states.async_set("sensor.hook_source", "off", {"mode": "wash"})
    await hass.async_block_till_done()

    assert entity._attr_state == "wash"
    assert entity.extra_state_attributes["hook_entity"] == "sensor.hook_source"
    assert entity.extra_state_attributes["previous_state"] is None
    await entity.async_will_remove_from_hass()


async def test_custom_event_bus_hook_filters_and_updates_virtual_entity(hass):
    config = {
        CONF_NAME: "Manual Hook Sensor",
        ATTR_ENTITY_ID: "sensor.manual_hook_sensor",
        ATTR_UNIQUE_ID: "manual-hook-sensor",
        ATTR_DEVICE_ID: "hook-device",
        CONF_INITIAL_VALUE: "idle",
        CONF_INITIAL_AVAILABILITY: True,
        CONF_PERSISTENT: False,
        CONF_EVENT_HOOKS: [{
            "trigger": "event",
            "event_type": "virtual_layer_manual_update",
            "event_data": {"target": "washer"},
            CONF_VALUE_TEMPLATE: "{{ trigger.data.value }}",
            CONF_ATTRIBUTE_TEMPLATES: {
                "source_event": "{{ trigger.event_type }}",
            },
        }],
    }
    entity = VirtualSensor(config, False)
    entity.hass = hass
    entity.async_schedule_update_ha_state = Mock()
    entity._create_state(config)
    entity._setup_templates()

    hass.bus.async_fire("virtual_layer_manual_update", {"target": "dryer", "value": "drying"})
    await hass.async_block_till_done()
    assert entity._attr_state == "idle"

    hass.bus.async_fire("virtual_layer_manual_update", {"target": "washer", "value": "rinse"})
    await hass.async_block_till_done()

    assert entity._attr_state == "rinse"
    assert entity.extra_state_attributes["source_event"] == "virtual_layer_manual_update"
    await entity.async_will_remove_from_hass()


@pytest.mark.parametrize("invalid_debounce", ["Infinity", True])
async def test_custom_hooks_ignore_damaged_sources_and_invalid_debounce(
    hass,
    invalid_debounce,
):
    config = {
        CONF_NAME: "Recovered Hook Sensor",
        ATTR_ENTITY_ID: "sensor.recovered_hook_sensor",
        ATTR_UNIQUE_ID: "recovered-hook-sensor",
        ATTR_DEVICE_ID: "hook-device",
        CONF_INITIAL_VALUE: "idle",
        CONF_INITIAL_AVAILABILITY: True,
        CONF_PERSISTENT: False,
        CONF_EVENT_HOOKS: [
            {
                "trigger": "state",
                ATTR_ENTITY_ID: 42,
                CONF_ATTRIBUTE: {"bad": "shape"},
            },
            {
                "trigger": "event",
                "event_type": "virtual_layer_recovered_update",
                "debounce": invalid_debounce,
                CONF_VALUE_TEMPLATE: "{{ trigger.data.value }}",
            },
        ],
    }
    entity = VirtualSensor(config, False)
    entity.hass = hass
    entity.async_schedule_update_ha_state = Mock()
    entity._create_state(config)
    entity._setup_templates()

    hass.bus.async_fire(
        "virtual_layer_recovered_update",
        {"value": "recovered"},
    )
    await hass.async_block_till_done()

    assert entity._attr_state == "recovered"
    await entity.async_will_remove_from_hass()


async def test_state_only_entity_registry_preserves_configured_icon(hass, tmp_path, monkeypatch):
    meta_file = tmp_path / "virtual_layer.meta.json"
    monkeypatch.setattr(
        "custom_components.virtual_layer.cfg.default_meta_file",
        lambda _hass: str(meta_file),
    )
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={
            ATTR_DEVICES: {
                "Virtual Tag": [{
                    CONF_PLATFORM: "tag",
                    CONF_NAME: "Protected Tag",
                    ATTR_ENTITY_ID: "tag.protected_tag",
                    CONF_ICON: "mdi:tag-heart",
                    CONF_INITIAL_VALUE: "ready",
                    CONF_INITIAL_AVAILABILITY: True,
                    CONF_PERSISTENT: False,
                }],
            },
        },
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id) is True
    registry_entry = er.async_get(hass).async_get("tag.protected_tag")
    assert registry_entry is not None
    assert registry_entry.original_icon == "mdi:tag-heart"


async def test_state_only_entity_updates_composite_templates(hass, tmp_path, monkeypatch):
    meta_file = tmp_path / "virtual_layer.meta.json"
    monkeypatch.setattr(
        "custom_components.virtual_layer.cfg.default_meta_file",
        lambda _hass: str(meta_file),
    )
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={ATTR_DEVICES: {"Virtual Tag": [{
            CONF_PLATFORM: "tag",
            CONF_NAME: "Composite Tag",
            ATTR_ENTITY_ID: "tag.composite_tag",
            CONF_INITIAL_VALUE: "waiting",
            CONF_INITIAL_AVAILABILITY: True,
            CONF_PERSISTENT: False,
            CONF_SOURCE_ENTITIES: ["sensor.tag_source"],
            CONF_TEMPLATE_SOURCES: {
                "source": {
                    ATTR_ENTITY_ID: "sensor.tag_source",
                    CONF_ATTRIBUTE: "state",
                },
            },
            CONF_VALUE_TEMPLATE: "{{ source }}",
            CONF_ICON: "mdi:tag-outline",
            CONF_ICON_TEMPLATE: (
                "{{ 'mdi:tag' if source == 'first' else 'mdi:tag-off' }}"
            ),
            CONF_ATTRIBUTE_TEMPLATES: {
                "copied": "{{ source }}",
                "structured": "{{ {'values': [source], 'count': 1} }}",
                "broken": "{{ 1 / 0 }}",
                "after_broken": "{{ [source, 'ok'] }}",
                "direct_setting": "{{ 'overridden' }}",
            },
            "direct_setting": {"mode": "nfc", "priority": 1},
        }]}},
    )
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.tag_source", "first")

    with patch.object(
        hass.config_entries,
        "async_forward_entry_setups",
        AsyncMock(return_value=True),
    ):
        assert await async_setup_entry(hass, entry) is True
    await hass.async_block_till_done()
    assert hass.states.get("tag.composite_tag").state == "first"
    assert hass.states.get("tag.composite_tag").attributes[CONF_ICON] == "mdi:tag"
    assert hass.states.get("tag.composite_tag").attributes["copied"] == "first"
    assert hass.states.get("tag.composite_tag").attributes["structured"] == {
        "values": ["first"],
        "count": 1,
    }
    assert "broken" not in hass.states.get("tag.composite_tag").attributes
    assert hass.states.get("tag.composite_tag").attributes["after_broken"] == [
        "first",
        "ok",
    ]
    assert hass.states.get("tag.composite_tag").attributes["direct_setting"] == {
        "mode": "nfc",
        "priority": 1,
    }

    hass.states.async_set("sensor.tag_source", "second")
    await hass.async_block_till_done()
    assert hass.states.get("tag.composite_tag").state == "second"
    assert hass.states.get("tag.composite_tag").attributes[CONF_ICON] == "mdi:tag-off"

    assert await async_unload_entry(hass, entry) is True
    hass.states.async_set("sensor.tag_source", "after_unload")
    await hass.async_block_till_done()
    assert hass.states.get("tag.composite_tag") is None


async def test_state_only_entity_renders_and_tracks_native_templates(
    hass,
    tmp_path,
    monkeypatch,
):
    meta_file = tmp_path / "virtual_layer.meta.json"
    monkeypatch.setattr(
        "custom_components.virtual_layer.cfg.default_meta_file",
        lambda _hass: str(meta_file),
    )
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={ATTR_DEVICES: {"Location": [{
            CONF_PLATFORM: "geolocation",
            CONF_NAME: "Templated location",
            ATTR_ENTITY_ID: "geolocation.templated_location",
            CONF_INITIAL_VALUE: "home",
            CONF_INITIAL_AVAILABILITY: True,
            CONF_PERSISTENT: False,
            CONF_SOURCE_ENTITIES: ["sensor.latitude", "sensor.longitude"],
            CONF_TEMPLATE_SOURCES: {
                "latitude_source": {
                    ATTR_ENTITY_ID: "sensor.latitude",
                    CONF_ATTRIBUTE: "state",
                },
                "longitude_source": {
                    ATTR_ENTITY_ID: "sensor.longitude",
                    CONF_ATTRIBUTE: "state",
                },
            },
            CONF_NATIVE_TEMPLATES: {
                "latitude": "{{ latitude_source | float }}",
                "longitude": "{{ longitude_source | float }}",
                "source": "{{ 'virtual_layer' }}",
            },
        }]}},
    )
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.latitude", "37.5")
    hass.states.async_set("sensor.longitude", "127.0")

    with patch.object(
        hass.config_entries,
        "async_forward_entry_setups",
        AsyncMock(return_value=True),
    ):
        assert await async_setup_entry(hass, entry) is True
    await hass.async_block_till_done()
    state = hass.states.get("geolocation.templated_location")
    assert state is not None
    assert state.attributes[ATTR_LATITUDE] == 37.5
    assert state.attributes[ATTR_LONGITUDE] == 127.0
    assert state.attributes["source"] == "virtual_layer"

    hass.states.async_set("sensor.latitude", "38.25")
    await hass.async_block_till_done()
    state = hass.states.get("geolocation.templated_location")
    assert state is not None
    assert state.attributes[ATTR_LATITUDE] == 38.25


async def test_state_only_entity_supports_state_and_event_hooks(
    hass,
    tmp_path,
    monkeypatch,
):
    meta_file = tmp_path / "virtual_layer.meta.json"
    monkeypatch.setattr(
        "custom_components.virtual_layer.cfg.default_meta_file",
        lambda _hass: str(meta_file),
    )
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={ATTR_DEVICES: {"Virtual Tag": [{
            CONF_PLATFORM: "tag",
            CONF_NAME: "Hook Tag",
            ATTR_ENTITY_ID: "tag.hook_tag",
            CONF_INITIAL_VALUE: "waiting",
            CONF_INITIAL_AVAILABILITY: True,
            CONF_PERSISTENT: False,
            CONF_EVENT_HOOKS: [
                {
                    "trigger": "state",
                    ATTR_ENTITY_ID: ["sensor.tag_hook_source"],
                    CONF_ATTRIBUTE: ["mode"],
                    CONF_VALUE_TEMPLATE: "{{ trigger.to_state.attributes.mode }}",
                    CONF_ATTRIBUTE_TEMPLATES: {
                        "state_source": "{{ trigger.entity_id }}",
                    },
                },
                {
                    "trigger": "event",
                    "event_type": "virtual_layer_tag_update",
                    "event_data": {"target": "tag"},
                    "refresh": True,
                    CONF_VALUE_TEMPLATE: "{{ trigger.data.value }}",
                    CONF_ATTRIBUTE_TEMPLATES: {
                        "event_source": "{{ trigger.event_type }}",
                        "previous_value": "{{ this.state }}",
                        "broken": "{{ 1 / 0 }}",
                        "structured": "{{ {'values': [trigger.data.value]} }}",
                    },
                },
            ],
        }]}},
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id) is True
    await hass.async_block_till_done()

    hass.states.async_set("sensor.tag_hook_source", "on", {"mode": "scanned"})
    await hass.async_block_till_done()
    state = hass.states.get("tag.hook_tag")
    assert state.state == "scanned"
    assert state.attributes["state_source"] == "sensor.tag_hook_source"

    hass.bus.async_fire(
        "virtual_layer_tag_update",
        {"target": "other", "value": "ignored"},
    )
    await hass.async_block_till_done()
    assert hass.states.get("tag.hook_tag").state == "scanned"

    hass.bus.async_fire(
        "virtual_layer_tag_update",
        {"target": "tag", "value": "received"},
    )
    await hass.async_block_till_done()
    state = hass.states.get("tag.hook_tag")
    assert state.state == "received"
    assert state.attributes["event_source"] == "virtual_layer_tag_update"
    assert state.attributes["previous_value"] == "scanned"
    assert "broken" not in state.attributes
    assert state.attributes["structured"] == {"values": ["received"]}

    info = hass.states.get("sensor.hook_tag_info")
    debug = hass.states.get("sensor.hook_tag_debug1")
    assert info.attributes["configured_source_entities"] == [
        "sensor.tag_hook_source",
    ]
    assert info.attributes["configuration"]["event_hooks"] == (
        entry.options[ATTR_DEVICES]["Virtual Tag"][0][CONF_EVENT_HOOKS]
    )
    assert debug.attributes["source_entity_id"] == "sensor.tag_hook_source"
    assert debug.state == "on"

    assert await async_unload_entry(hass, entry) is True
    hass.bus.async_fire(
        "virtual_layer_tag_update",
        {"target": "tag", "value": "after_unload"},
    )
    await hass.async_block_till_done()
    assert hass.states.get("tag.hook_tag") is None


async def test_state_only_event_hook_cancels_pending_debounce_on_unload(
    hass,
    tmp_path,
    monkeypatch,
):
    meta_file = tmp_path / "virtual_layer.meta.json"
    monkeypatch.setattr(
        "custom_components.virtual_layer.cfg.default_meta_file",
        lambda _hass: str(meta_file),
    )
    cancel_debounce = Mock()
    scheduled = {}

    def _fake_call_later(_hass, _delay, callback):
        scheduled["callback"] = callback
        return cancel_debounce

    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
        options={ATTR_DEVICES: {"Virtual Tag": [{
            CONF_PLATFORM: "tag",
            CONF_NAME: "Debounced Tag",
            ATTR_ENTITY_ID: "tag.debounced_tag",
            CONF_INITIAL_VALUE: "waiting",
            CONF_EVENT_HOOKS: [{
                "trigger": "event",
                "event_type": "virtual_layer_delayed_tag_update",
                "debounce": 30,
                CONF_VALUE_TEMPLATE: "{{ trigger.data.value }}",
            }],
        }]}},
    )
    entry.add_to_hass(hass)

    with (
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            AsyncMock(return_value=True),
        ),
        patch(
            "custom_components.virtual_layer.async_call_later",
            side_effect=_fake_call_later,
        ),
    ):
        assert await async_setup_entry(hass, entry) is True
        hass.bus.async_fire(
            "virtual_layer_delayed_tag_update",
            {"value": "delayed"},
        )
        await hass.async_block_till_done()
        assert "callback" in scheduled
        assert hass.states.get("tag.debounced_tag").state == "waiting"

        assert await async_unload_entry(hass, entry) is True

    cancel_debounce.assert_called_once_with()


async def test_state_only_setup_does_not_overwrite_existing_state(hass, tmp_path, monkeypatch):
    meta_file = tmp_path / "virtual_layer.meta.json"
    monkeypatch.setattr(
        "custom_components.virtual_layer.cfg.default_meta_file",
        lambda _hass: str(meta_file),
    )
    hass.states.async_set(
        "tag.existing_tag",
        "external",
        {"source": "outside"},
    )
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        title="ui - virtual_layer",
        data={ATTR_GROUP_NAME: "ui"},
        options={
            ATTR_DEVICES: {
                "Tags": [
                    {
                        CONF_PLATFORM: "tag",
                        CONF_NAME: "Existing Tag",
                        ATTR_ENTITY_ID: "tag.existing_tag",
                        CONF_INITIAL_VALUE: "virtual",
                    },
                ],
            },
        },
    )
    entry.add_to_hass(hass)

    with patch.object(
        hass.config_entries,
        "async_forward_entry_setups",
        AsyncMock(return_value=True),
    ):
        assert await async_setup_entry(hass, entry) is True

    external_state = hass.states.get("tag.existing_tag")
    assert external_state.state == "external"
    assert external_state.attributes["source"] == "outside"

    virtual_state = hass.states.get("tag.existing_tag_2")
    assert virtual_state is not None
    assert virtual_state.state == "virtual"
    assert er.async_get(hass).async_get("tag.existing_tag_2") is not None
    assert (
        hass.data[COMPONENT_DOMAIN]["ui"][ATTR_ENTITIES]["tag"][0][ATTR_ENTITY_ID]
        == "tag.existing_tag_2"
    )


async def test_state_only_setup_respects_registry_disabled_entities(
    hass,
    tmp_path,
    monkeypatch,
):
    meta_file = tmp_path / "virtual_layer.meta.json"
    monkeypatch.setattr(
        "custom_components.virtual_layer.cfg.default_meta_file",
        lambda _hass: str(meta_file),
    )
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        title="ui - virtual_layer",
        data={ATTR_GROUP_NAME: "ui"},
        options={
            ATTR_DEVICES: {
                "Tags": [{
                    CONF_PLATFORM: "tag",
                    CONF_NAME: "Disabled Tag",
                    ATTR_ENTITY_ID: "tag.disabled_tag",
                    CONF_INITIAL_VALUE: "virtual",
                }],
            },
        },
    )
    entry.add_to_hass(hass)

    with patch.object(
        hass.config_entries,
        "async_forward_entry_setups",
        AsyncMock(return_value=True),
    ):
        assert await async_setup_entry(hass, entry) is True
        assert hass.states.get("tag.disabled_tag") is not None
        assert er.async_get(hass).async_get("tag.disabled_tag").original_name == (
            "Disabled Tag"
        )
        assert await async_unload_entry(hass, entry) is True

        er.async_get(hass).async_update_entity(
            "tag.disabled_tag",
            disabled_by=er.RegistryEntryDisabler.USER,
        )
        assert await async_setup_entry(hass, entry) is True

    assert hass.states.get("tag.disabled_tag") is None
    assert entry.entry_id not in hass.data.get(
        "virtual_layer_state_only_template_listeners",
        {},
    )


async def test_device_tracker_service_is_registered_once(hass):
    class Services:
        def __init__(self):
            self.register_count = 0

        def async_register(self, *_args, **_kwargs):
            self.register_count += 1

    fake_hass = SimpleNamespace()
    fake_hass.data = {
        "ui": {
            ATTR_ENTITIES: {
                "device_tracker": [],
            },
        },
        COMPONENT_DOMAIN: {},
        COMPONENT_SERVICES: {},
    }
    fake_hass.data[COMPONENT_DOMAIN] = {
        "ui": {
            ATTR_ENTITIES: {
                "device_tracker": [],
            },
        },
    }
    fake_hass.services = Services()
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
    )

    await virtual_device_tracker.async_setup_entry(fake_hass, entry, lambda entities: None)
    await virtual_device_tracker.async_setup_entry(fake_hass, entry, lambda entities: None)

    assert fake_hass.services.register_count == 1


async def test_virtual_entity_copies_source_attributes_and_registers_pull(hass):
    config = {
        CONF_NAME: "Composite Washer",
        ATTR_ENTITY_ID: "sensor.composite_washer",
        ATTR_UNIQUE_ID: "composite_washer",
        ATTR_DEVICE_ID: "Laundry",
        CONF_INITIAL_VALUE: "idle",
        CONF_INITIAL_AVAILABILITY: True,
        CONF_PERSISTENT: False,
        CONF_ATTRIBUTE_SOURCES: {
            "battery": {
                ATTR_ENTITY_ID: "sensor.washer_source",
                CONF_ATTRIBUTE: "battery_level",
            },
            "phase": {
                ATTR_ENTITY_ID: "sensor.washer_source",
                CONF_ATTRIBUTE: "state",
            },
        },
        CONF_PULL_INTERVAL: 15,
    }
    entity = VirtualSensor(config, False)
    entity.hass = hass
    entity.async_schedule_update_ha_state = Mock()
    hass.states.async_set(
        "sensor.washer_source",
        "running",
        {"battery_level": 91},
    )

    entity._create_state(config)
    assert entity._apply_attribute_sources() is True
    entity._update_attributes()

    assert entity.extra_state_attributes["battery"] == 91
    assert entity.extra_state_attributes["phase"] == "running"
    assert entity.extra_state_attributes[ATTR_AVAILABLE] is True

    hass.states.async_remove("sensor.washer_source")
    assert entity._apply_attribute_sources() is True
    entity._update_attributes()
    assert entity.extra_state_attributes["battery"] is None
    assert entity.extra_state_attributes["phase"] is None

    with (
        patch(
            "custom_components.virtual_layer.entity.async_track_state_change_event",
            return_value=Mock(),
        ) as track_state_change,
        patch(
            "custom_components.virtual_layer.entity.async_track_time_interval",
            return_value=Mock(),
        ) as track_time_interval,
    ):
        entity._setup_templates()

    assert "sensor.washer_source" in track_state_change.call_args.args[1]
    assert track_time_interval.call_args.args[2].total_seconds() == 15


async def test_virtual_entity_renders_composite_templates_with_source_variables(hass):
    config = {
        CONF_NAME: "Washer Composite State",
        ATTR_ENTITY_ID: "sensor.washer_composite_state",
        ATTR_UNIQUE_ID: "washer_composite_state",
        ATTR_DEVICE_ID: "Laundry",
        CONF_INITIAL_VALUE: "idle",
        CONF_INITIAL_AVAILABILITY: True,
        CONF_PERSISTENT: False,
        CONF_TEMPLATE_SOURCES: {
            "power": {
                ATTR_ENTITY_ID: "sensor.washer_power",
                CONF_ATTRIBUTE: "state",
            },
            "door": {
                ATTR_ENTITY_ID: "binary_sensor.washer_door",
                CONF_ATTRIBUTE: "state",
            },
            "humidity": {
                ATTR_ENTITY_ID: "sensor.laundry_room",
                CONF_ATTRIBUTE: "humidity",
            },
        },
        CONF_VALUE_TEMPLATE: (
            "{% if power|float(0) > 10 and door == 'off' %}"
            "running"
            "{% else %}"
            "idle"
            "{% endif %}"
        ),
        CONF_ATTRIBUTE_TEMPLATES: {
            "summary": "{{ power }}W / {{ humidity }}%",
        },
    }
    entity = VirtualSensor(config, False)
    entity.hass = hass
    entity.async_schedule_update_ha_state = Mock()
    hass.states.async_set("sensor.washer_power", "42")
    hass.states.async_set("binary_sensor.washer_door", "off")
    hass.states.async_set("sensor.laundry_room", "ok", {"humidity": 58})

    entity._create_state(config)
    entity._apply_templates()

    assert entity.state == "running"
    assert entity.extra_state_attributes["summary"] == "42W / 58%"


async def test_template_tracking_is_batched_for_regular_and_state_only_entities(hass):
    regular_config = {
        CONF_NAME: "Batched Sensor",
        ATTR_ENTITY_ID: "sensor.batched_sensor",
        ATTR_UNIQUE_ID: "batched-sensor",
        ATTR_DEVICE_ID: "Batch",
        CONF_INITIAL_VALUE: "idle",
        CONF_INITIAL_AVAILABILITY: True,
        CONF_PERSISTENT: False,
        CONF_VALUE_TEMPLATE: "{{ states('sensor.batch_source') }}",
        CONF_AVAILABILITY_TEMPLATE: "{{ true }}",
        CONF_ICON_TEMPLATE: "{{ 'mdi:test-tube' }}",
        CONF_ATTRIBUTE_TEMPLATES: {
            "power": "{{ state_attr('sensor.batch_source', 'power') }}",
        },
    }
    regular = VirtualSensor(regular_config, False)
    regular.hass = hass
    regular.async_schedule_update_ha_state = Mock()
    regular._create_state(regular_config)

    tracked = SimpleNamespace(async_remove=Mock())
    with patch(
        "custom_components.virtual_layer.entity.async_track_template_result",
        return_value=tracked,
    ) as track_regular:
        regular._setup_templates()

    assert track_regular.call_count == 1
    assert len(track_regular.call_args.args[1]) == 4

    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
    )
    state_only = {
        CONF_PLATFORM: "tag",
        CONF_NAME: "Batched Tag",
        ATTR_ENTITY_ID: "tag.batched_tag",
        ATTR_UNIQUE_ID: "batched-tag",
        CONF_INITIAL_VALUE: "idle",
        CONF_VALUE_TEMPLATE: "{{ states('sensor.batch_source') }}",
        CONF_AVAILABILITY_TEMPLATE: "{{ true }}",
        CONF_ICON_TEMPLATE: "{{ 'mdi:tag' }}",
        CONF_ATTRIBUTE_TEMPLATES: {
            "power": "{{ state_attr('sensor.batch_source', 'power') }}",
        },
    }
    tracked_state_only = SimpleNamespace(async_remove=Mock())
    with patch(
        "custom_components.virtual_layer.async_track_template_result",
        return_value=tracked_state_only,
    ) as track_state_only:
        _async_setup_state_only_templates(hass, entry, state_only)

    assert track_state_only.call_count == 1
    assert len(track_state_only.call_args.args[1]) == 4


async def test_startup_completion_rechecks_missed_source_availability(hass):
    """Recover when source restoration finishes outside the tracked event window."""
    hass.set_state(CoreState.starting)
    availability_template = (
        "{{ states('sensor.late_startup_source') not in "
        "['unknown', 'unavailable'] }}"
    )
    regular_config = {
        CONF_NAME: "Late Startup Sensor",
        ATTR_ENTITY_ID: "sensor.late_startup_sensor",
        ATTR_UNIQUE_ID: "late-startup-sensor",
        ATTR_DEVICE_ID: "Startup",
        CONF_INITIAL_VALUE: "idle",
        CONF_INITIAL_AVAILABILITY: True,
        CONF_PERSISTENT: False,
        CONF_SOURCE_ENTITIES: ["sensor.late_startup_source"],
        CONF_AVAILABILITY_TEMPLATE: availability_template,
    }
    regular = VirtualSensor(regular_config, False)
    regular.hass = hass
    regular.async_schedule_update_ha_state = Mock()
    regular._create_state(regular_config)

    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "ui"},
    )
    state_only = {
        CONF_PLATFORM: "tag",
        CONF_NAME: "Late Startup Tag",
        ATTR_ENTITY_ID: "tag.late_startup_tag",
        ATTR_UNIQUE_ID: "late-startup-tag",
        CONF_INITIAL_VALUE: "idle",
        CONF_INITIAL_AVAILABILITY: True,
        CONF_SOURCE_ENTITIES: ["sensor.late_startup_source"],
        CONF_AVAILABILITY_TEMPLATE: availability_template,
    }
    hass.states.async_set(
        state_only[ATTR_ENTITY_ID],
        state_only[CONF_INITIAL_VALUE],
        {ATTR_AVAILABLE: True},
    )

    tracked = SimpleNamespace(async_remove=Mock())
    with (
        patch(
            "custom_components.virtual_layer.entity.async_track_state_change_event",
            return_value=Mock(),
        ),
        patch(
            "custom_components.virtual_layer.entity.async_track_template_result",
            return_value=tracked,
        ),
        patch(
            "custom_components.virtual_layer.async_track_state_change_event",
            return_value=Mock(),
        ),
        patch(
            "custom_components.virtual_layer.async_track_template_result",
            return_value=tracked,
        ),
    ):
        regular._setup_templates()
        regular._apply_templates()
        _async_setup_state_only_templates(hass, entry, state_only)

        assert regular.available is False
        assert hass.states.get(state_only[ATTR_ENTITY_ID]).attributes[ATTR_AVAILABLE] is False

        # Simulate a source which became ready before either listener observed
        # its state event. The HA-started refresh must use the final state.
        hass.states.async_set("sensor.late_startup_source", "ready")
        hass.set_state(CoreState.running)
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
        await hass.async_block_till_done()

    assert regular.available is True
    assert hass.states.get(state_only[ATTR_ENTITY_ID]).attributes[ATTR_AVAILABLE] is True


async def test_running_startup_fan_retries_missed_source_availability(hass):
    """Recover a fan loaded after HA entered running state but before its source."""
    source_entity_id = "fan.air_circulator_fan"
    config = FAN_SCHEMA({
        CONF_NAME: "Air Circulator Fan Copy",
        ATTR_ENTITY_ID: "fan.air_circulator_fan_copy",
        ATTR_UNIQUE_ID: "air-circulator-fan-copy",
        ATTR_DEVICE_ID: "Air Circulator",
        CONF_INITIAL_VALUE: "on",
        CONF_INITIAL_AVAILABILITY: True,
        CONF_PERSISTENT: True,
        CONF_SOURCE_ENTITIES: [source_entity_id],
        CONF_AVAILABILITY_TEMPLATE: (
            f"{{{{ states({source_entity_id!r}) not in "
            "['unknown', 'unavailable'] }}"
        ),
        CONF_NATIVE_TEMPLATES: {
            "is_on": (
                f"{{{{ states({source_entity_id!r}) not in "
                "['off', 'unknown', 'unavailable'] }}"
            ),
        },
    })
    fan = VirtualFan(config, False)
    fan.hass = hass
    fan.async_schedule_update_ha_state = Mock()
    fan._create_state(config)
    retry = Mock(return_value=Mock())
    tracked = SimpleNamespace(async_remove=Mock())

    with (
        patch(
            "custom_components.virtual_layer.entity.async_track_state_change_event",
            return_value=Mock(),
        ),
        patch(
            "custom_components.virtual_layer.entity.async_track_template_result",
            return_value=tracked,
        ),
        patch(
            "custom_components.virtual_layer.entity.async_call_later",
            retry,
        ),
    ):
        fan._setup_templates()
        fan._apply_templates()
        assert fan.available is False
        assert retry.call_args.args[1] == 5

        # Match the reported startup state: source/debug are on, while the
        # virtual fan missed the source event and remains unavailable.
        hass.states.async_set(source_entity_id, "on")
        retry.call_args.args[2](None)

    assert fan.available is True
    assert fan.is_on is True
    assert retry.call_count == 1


async def test_unchanged_templates_do_not_schedule_redundant_state_writes(hass):
    config = {
        CONF_NAME: "Stable Sensor",
        ATTR_ENTITY_ID: "sensor.stable_sensor",
        ATTR_UNIQUE_ID: "stable-sensor",
        ATTR_DEVICE_ID: "Stable",
        CONF_INITIAL_VALUE: "ready",
        CONF_INITIAL_AVAILABILITY: False,
        CONF_PERSISTENT: False,
        CONF_AVAILABILITY_TEMPLATE: "{{ '  true  ' }}",
        CONF_ATTRIBUTE_TEMPLATES: {"summary": "{{ {'value': [1, 2]} }}"},
    }
    entity = VirtualSensor(config, False)
    entity.hass = hass
    entity.async_schedule_update_ha_state = Mock()
    entity._create_state(config)

    entity._apply_templates()
    assert entity.available is True
    assert entity.extra_state_attributes["summary"] == {"value": [1, 2]}
    assert entity.async_schedule_update_ha_state.call_count == 1

    entity.async_schedule_update_ha_state.reset_mock()
    entity._apply_templates()
    entity.async_schedule_update_ha_state.assert_not_called()


async def test_generated_attribute_helpers_follow_multiple_source_attributes(hass):
    hass.states.async_set(
        "sensor.first_load",
        "ready",
        {"active": True, "power": 10, "programs": ["eco", "quick"]},
    )
    hass.states.async_set(
        "sensor.second_load",
        "ready",
        {"active": True, "power": 30, "programs": ["eco", "intensive"]},
    )
    defaults = _reference_entity_defaults(
        hass,
        ["sensor.first_load", "sensor.second_load"],
    )
    config = {
        CONF_NAME: "Combined Load",
        ATTR_ENTITY_ID: "sensor.combined_load",
        ATTR_UNIQUE_ID: "combined_load",
        ATTR_DEVICE_ID: "Laundry",
        CONF_INITIAL_VALUE: "ready",
        CONF_INITIAL_AVAILABILITY: True,
        CONF_PERSISTENT: False,
        CONF_ATTRIBUTE_TEMPLATES: json.loads(
            defaults[CONF_ATTRIBUTE_TEMPLATES_JSON]
        ),
    }
    entity = VirtualSensor(config, False)
    entity.hass = hass
    entity.async_schedule_update_ha_state = Mock()

    entity._create_state(config)
    entity._apply_templates()
    assert entity.extra_state_attributes["active"] is True
    assert entity.extra_state_attributes["power"] == 20.0
    assert entity.extra_state_attributes["programs"] == [
        "eco",
        "quick",
        "intensive",
    ]

    hass.states.async_set(
        "sensor.second_load",
        "running",
        {"active": False, "power": 50, "programs": ["delicate"]},
    )
    entity._apply_templates()
    assert entity.extra_state_attributes["active"] is False
    assert entity.extra_state_attributes["power"] == 30.0
    assert entity.extra_state_attributes["programs"] == [
        "eco",
        "quick",
        "delicate",
    ]


async def test_generated_numeric_helper_ignores_unavailable_sources_at_runtime(hass):
    hass.states.async_set("number.first_reading", "unavailable")
    hass.states.async_set("sensor.second_reading", "30")
    hass.states.async_set("sensor.third_reading", "42")
    defaults = _reference_entity_defaults(
        hass,
        [
            "number.first_reading",
            "sensor.second_reading",
            "sensor.third_reading",
        ],
    )
    template_sources = {
        name: {
            ATTR_ENTITY_ID: entity_id,
            CONF_ATTRIBUTE: "state",
        }
        for name, entity_id in json.loads(
            defaults[CONF_TEMPLATE_SOURCES_JSON],
        ).items()
    }
    config = {
        CONF_NAME: "Average Reading",
        ATTR_ENTITY_ID: "sensor.average_reading",
        ATTR_UNIQUE_ID: "average_reading",
        ATTR_DEVICE_ID: "Readings",
        CONF_INITIAL_VALUE: defaults[CONF_INITIAL_VALUE],
        CONF_INITIAL_AVAILABILITY: True,
        CONF_PERSISTENT: False,
        CONF_TEMPLATE_SOURCES: template_sources,
        CONF_VALUE_TEMPLATE: defaults[CONF_VALUE_TEMPLATE],
    }
    entity = VirtualSensor(config, False)
    entity.hass = hass
    entity.async_schedule_update_ha_state = Mock()

    entity._create_state(config)
    entity._apply_templates()

    assert entity.state == "36.0"

    hass.states.async_set("sensor.second_reading", "unknown")
    hass.states.async_set("sensor.third_reading", "unavailable")
    hass.states.async_remove("number.first_reading")
    entity._apply_templates()

    assert entity.state == "unknown"


async def test_location_helper_updates_from_home_assistant_state_events(hass):
    hass.states.async_set(
        "device_tracker.phone_one",
        "not_home",
        {ATTR_LATITUDE: 37.5, ATTR_LONGITUDE: 127.0},
    )
    hass.states.async_set(
        "device_tracker.phone_two",
        "not_home",
        {ATTR_LATITUDE: 37.5, ATTR_LONGITUDE: 127.001},
    )
    hass.states.async_set(
        "person.traveller",
        "not_home",
        {ATTR_LATITUDE: 37.52, ATTR_LONGITUDE: 127.02},
    )
    config = {
        CONF_NAME: "Family GPS",
        ATTR_ENTITY_ID: "device_tracker.family_gps",
        ATTR_UNIQUE_ID: "family_gps",
        ATTR_DEVICE_ID: "Family",
        CONF_INITIAL_VALUE: "not_home",
        CONF_INITIAL_AVAILABILITY: True,
        CONF_PERSISTENT: False,
        CONF_SOURCE_ENTITIES: [
            "device_tracker.phone_one",
            "device_tracker.phone_two",
            "person.traveller",
        ],
        CONF_LOCATION_HELPER: {
            "distance_threshold_meters": 300,
            "priority_window_seconds": 1800,
        },
    }
    entity = virtual_device_tracker.VirtualDeviceTracker(config)
    entity.hass = hass
    entity.async_schedule_update_ha_state = Mock()

    await entity.async_added_to_hass()

    assert entity.latitude == 37.52
    assert entity.longitude == 127.02
    assert entity.extra_state_attributes[
        virtual_device_tracker.ATTR_LOCATION_PRIORITY_SOURCE
    ] == "person.traveller"

    hass.states.async_set(
        "person.traveller",
        "not_home",
        {ATTR_LATITUDE: 37.5, ATTR_LONGITUDE: 127.0002},
    )
    await hass.async_block_till_done()

    assert entity.latitude == 37.5
    assert entity.longitude == 127.0002
    await entity.async_will_remove_from_hass()


async def test_virtual_entity_restore_defaults_availability_when_missing(hass):
    config = {
        CONF_NAME: "Restored Washer",
        ATTR_ENTITY_ID: "sensor.restored_washer",
        ATTR_UNIQUE_ID: "restored_washer",
        ATTR_DEVICE_ID: "Laundry",
        CONF_INITIAL_VALUE: "idle",
        CONF_INITIAL_AVAILABILITY: True,
        CONF_PERSISTENT: True,
    }
    entity = VirtualSensor(config, False)
    entity.hass = hass

    entity._restore_state(SimpleNamespace(state="running", attributes={}), config)
    entity._update_attributes()

    assert entity.state == "running"
    assert entity.extra_state_attributes[ATTR_AVAILABLE] is True


async def test_virtual_entity_template_failures_do_not_block_other_templates(hass):
    config = {
        CONF_NAME: "Template Isolation",
        ATTR_ENTITY_ID: "sensor.template_isolation",
        ATTR_UNIQUE_ID: "template_isolation",
        ATTR_DEVICE_ID: "Laundry",
        CONF_INITIAL_VALUE: "idle",
        CONF_INITIAL_AVAILABILITY: True,
        CONF_PERSISTENT: False,
        CONF_VALUE_TEMPLATE: "value-template",
        CONF_ATTRIBUTE_TEMPLATES: {
            "good": "good-template",
            "bad": "bad-template",
        },
    }
    entity = VirtualSensor(config, False)
    entity.hass = hass
    entity.async_schedule_update_ha_state = Mock()

    def render_template(template, _variables=None, *, parse_result=False):
        if template == "bad-template":
            raise ValueError("broken template")
        if template == "value-template":
            return "running"
        return "ok"

    entity._render_template = Mock(side_effect=render_template)
    entity._create_state(config)
    entity._apply_templates()

    assert entity.state == "running"
    assert entity.extra_state_attributes["good"] == "ok"
    assert "bad" not in entity.extra_state_attributes
    assert entity.async_schedule_update_ha_state.called


async def test_virtual_camera_alias_proxies_source_image_and_stream(hass):
    source = Mock()
    source.async_camera_image = AsyncMock(return_value=b"camera-image")
    source.stream_source = AsyncMock(return_value="rtsp://source/live")
    camera_component = Mock()
    camera_component.get_entity.return_value = source
    hass.data["camera"] = camera_component

    entity = VirtualCamera(CAMERA_SCHEMA({
        CONF_NAME: "Front Door Alias",
        ATTR_ENTITY_ID: "camera.front_door_alias",
        CONF_INITIAL_VALUE: "on",
        CAMERA_SOURCE_ENTITY: "camera.front_door",
    }), False)
    entity.hass = hass
    entity._create_state(entity._config)

    assert await entity.async_camera_image(width=640, height=360) == b"camera-image"
    assert await entity.stream_source() == "rtsp://source/live"
    source.async_camera_image.assert_awaited_once_with(width=640, height=360)
    source.stream_source.assert_awaited_once_with()


async def test_virtual_camera_alias_proxies_home_assistant_webrtc_websocket(
    hass,
    hass_ws_client,
):
    class NativeWebRTCCamera(Camera):
        _attr_name = "Native WebRTC Source"
        _attr_supported_features = CameraEntityFeature.STREAM

        def __init__(self):
            super().__init__()
            self.entity_id = "camera.native_webrtc_source"
            self.offers = []

        async def async_handle_async_webrtc_offer(
            self,
            offer_sdp,
            session_id,
            send_message,
        ):
            self.offers.append((offer_sdp, session_id))
            send_message(WebRTCAnswer("answer-sdp"))

        async def async_on_webrtc_candidate(self, session_id, candidate):
            return

    assert await async_setup_component(hass, "camera", {})
    component = hass.data["camera"]
    source = NativeWebRTCCamera()
    await component.async_add_entities([source])

    entity = VirtualCamera(CAMERA_SCHEMA({
        CONF_NAME: "WebRTC Alias",
        ATTR_ENTITY_ID: "camera.webrtc_alias",
        CONF_INITIAL_VALUE: "on",
        CAMERA_SOURCE_ENTITY: source.entity_id,
    }), False)
    await component.async_add_entities([entity])

    client = await hass_ws_client(hass)
    await client.send_json_auto_id({
        "type": "camera/capabilities",
        "entity_id": entity.entity_id,
    })
    capabilities = await client.receive_json()
    assert capabilities["success"] is True
    assert capabilities["result"]["frontend_stream_types"] == [
        StreamType.WEB_RTC,
    ]

    await client.send_json_auto_id({
        "type": "camera/webrtc/offer",
        "entity_id": entity.entity_id,
        "offer": "offer-sdp",
    })
    assert (await client.receive_json())["success"] is True
    session_event = await client.receive_json()
    answer_event = await client.receive_json()

    assert session_event["event"]["type"] == "session"
    assert answer_event["event"] == {
        "type": "answer",
        "answer": "answer-sdp",
    }
    assert source.offers == [
        ("offer-sdp", session_event["event"]["session_id"]),
    ]


async def test_virtual_camera_alias_does_not_proxy_itself(hass):
    entity = VirtualCamera(CAMERA_SCHEMA({
        CONF_NAME: "Self Alias",
        ATTR_ENTITY_ID: "camera.self_alias",
        CONF_INITIAL_VALUE: "on",
        CAMERA_SOURCE_ENTITY: "camera.self_alias",
    }), False)
    entity.hass = hass
    entity._create_state(entity._config)
    camera_component = Mock()
    camera_component.get_entity.return_value = entity
    hass.data["camera"] = camera_component

    assert await entity.async_camera_image() is None
    assert await entity.stream_source() is None


def test_virtual_entity_does_not_subscribe_to_its_own_template_source(hass):
    entity = VirtualSensor({
        CONF_NAME: "Self Referencing Source",
        ATTR_ENTITY_ID: "sensor.self_referencing_source",
        ATTR_UNIQUE_ID: "self_referencing_source",
        ATTR_DEVICE_ID: "Test Device",
        CONF_INITIAL_VALUE: "unknown",
        CONF_INITIAL_AVAILABILITY: True,
        CONF_PERSISTENT: False,
        CONF_TEMPLATE_SOURCES: {
            "self_state": {
                ATTR_ENTITY_ID: "sensor.self_referencing_source",
                CONF_ATTRIBUTE: "state",
            },
        },
    }, False)
    entity.hass = hass

    with patch(
        "custom_components.virtual_layer.entity.async_track_state_change_event",
    ) as track_state_change:
        entity._setup_templates()

    track_state_change.assert_not_called()
