"""Integration tests for UI-only Virtual Layer setup behavior."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import homeassistant.helpers.device_registry as dr
import homeassistant.helpers.entity_registry as er
import pytest
from homeassistant.config_entries import SOURCE_IMPORT
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    CONF_NAME,
    CONF_PLATFORM,
)
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.translation import async_get_translations
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.virtual_layer import (
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
from custom_components.virtual_layer.climate import CLIMATE_SCHEMA
from custom_components.virtual_layer.config_flow import (
    ACTION_ADD_ENTITY,
    ACTION_DELETE_ENTITY,
    ACTION_EDIT_ENTITY,
    ACTION_FINISH,
    CONF_ACTION,
    CONF_DEVICE_ID,
    CONF_DEVICE_MANUFACTURER,
    CONF_DEVICE_MODEL,
    CONF_DEVICE_NAME,
    CONF_ENTITY_KEY,
    CONF_ENTITY_KEYS,
    CONF_ENTITY_NAME,
    CONF_REFERENCE_ENTITY_ID,
    CONF_SOURCE_ENTITIES_TEXT,
    CONF_TEMPLATE_SOURCES_JSON,
    _entity_key,
    _reference_entity_defaults,
)
from custom_components.virtual_layer.const import (
    ATTR_ATTRIBUTES,
    ATTR_AVAILABLE,
    ATTR_DEVICE_ATTRIBUTES,
    ATTR_DEVICE_ID,
    ATTR_DEVICES,
    ATTR_ENTITIES,
    ATTR_ENTITY_KEY,
    ATTR_FILE_NAME,
    ATTR_GROUP_NAME,
    ATTR_UNIQUE_ID,
    ATTR_VALUE,
    COMPONENT_DOMAIN,
    COMPONENT_SERVICES,
    CONF_ATTRIBUTE,
    CONF_ATTRIBUTE_SOURCES,
    CONF_ATTRIBUTE_TEMPLATES,
    CONF_ATTRIBUTES,
    CONF_AVAILABILITY_TEMPLATE,
    CONF_CLASS,
    CONF_INITIAL_AVAILABILITY,
    CONF_INITIAL_VALUE,
    CONF_LOCATION_HELPER,
    CONF_MANUFACTURER,
    CONF_MODEL,
    CONF_PERSISTENT,
    CONF_PULL_INTERVAL,
    CONF_SOURCE_ENTITIES,
    CONF_TEMPLATE_SOURCES,
    CONF_VALUE_TEMPLATE,
)
from custom_components.virtual_layer.sensor import VirtualSensor

pytestmark = pytest.mark.integration


async def test_home_assistant_loads_korean_config_translations(hass):
    config_translations = await async_get_translations(
        hass,
        "ko",
        "config",
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
    assert selector_translations[
        "component.virtual_layer.selector.options_action.options.add_entity"
    ] == "가상 엔티티 추가"
    assert selector_translations[
        "component.virtual_layer.selector.restore_mode.options.replace"
    ] == "교체"


async def test_config_import_is_rejected(hass):
    result = await hass.config_entries.flow.async_init(
        COMPONENT_DOMAIN,
        context={"source": SOURCE_IMPORT},
        data={ATTR_GROUP_NAME: "imported"},
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "import_not_supported"


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
    assert ATTR_FILE_NAME not in group_data
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


async def test_backup_service_writes_ui_options_payload(hass, tmp_path):
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
                    },
                ],
            },
            ATTR_DEVICE_ATTRIBUTES: {
                "Laundry": {
                    ATTR_DEVICE_ID: "laundry-device-1",
                    CONF_NAME: "Laundry Device",
                    CONF_MODEL: "Washer 9000",
                },
            },
        },
    )
    entry.add_to_hass(hass)
    assert await async_setup(hass, {}) is True

    file_name = tmp_path / "virtual_layer_backup.json"
    await hass.services.async_call(
        COMPONENT_DOMAIN,
        "backup_devices",
        {ATTR_FILE_NAME: str(file_name)},
        blocking=True,
    )

    backup = json.loads(file_name.read_text())
    assert backup["groups"] == [
        {
            ATTR_GROUP_NAME: "ui",
            ATTR_DEVICES: {
                "Laundry": [
                    {
                        CONF_PLATFORM: "sensor",
                        CONF_NAME: "Washer Phase",
                    },
                ],
            },
            ATTR_DEVICE_ATTRIBUTES: {
                "Laundry": {
                    ATTR_DEVICE_ID: "laundry-device-1",
                    CONF_NAME: "Laundry Device",
                    CONF_MODEL: "Washer 9000",
                },
            },
        },
    ]


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
    source_defaults = result["data_schema"]({})
    assert source_defaults[CONF_REFERENCE_ENTITY_ID] == [
        "sensor.washer_power",
        "binary_sensor.washer_door",
    ]

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {},
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "edit_entity"

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

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][ATTR_DEVICES]["Laundry"][0].pop(ATTR_ENTITY_KEY)
    assert result["data"][ATTR_DEVICES]["Laundry"][0].pop("auto_helper") is False
    assert result["data"][ATTR_DEVICES]["Laundry"] == [
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
    assert result["data"][ATTR_DEVICE_ATTRIBUTES]["Laundry"] == {
        ATTR_DEVICE_ID: "laundry-updated",
        CONF_NAME: "Laundry",
        CONF_MANUFACTURER: "Acme",
        CONF_MODEL: "Washer 9000",
    }


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

    defaults = result["data_schema"]({})
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

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "entity"

    defaults = result["data_schema"]({})
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
    assert result["data"][ATTR_DEVICES]["Kitchen"][0].pop(ATTR_ENTITY_KEY)
    assert result["data"][ATTR_DEVICES]["Kitchen"][0].pop("auto_helper")
    assert result["data"][ATTR_DEVICES]["Kitchen"] == [
        {
            CONF_PLATFORM: "light",
            CONF_NAME: "Kitchen Lamp",
            ATTR_ENTITY_ID: "light.virtual_kitchen_lamp",
            CONF_INITIAL_VALUE: "on",
            CONF_INITIAL_AVAILABILITY: True,
            CONF_PERSISTENT: True,
            CONF_SOURCE_ENTITIES: ["light.kitchen_lamp"],
            CONF_TEMPLATE_SOURCES: {
                "kitchen_lamp": {
                    ATTR_ENTITY_ID: "light.kitchen_lamp",
                    CONF_ATTRIBUTE: "state",
                },
            },
            CONF_VALUE_TEMPLATE: "{{ kitchen_lamp }}",
            CONF_ATTRIBUTES: {"brightness": 128},
        },
    ]


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

    defaults = result["data_schema"]({})
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
    entity = result["data"][ATTR_DEVICES]["Security"][0]
    entity.pop(ATTR_ENTITY_KEY)
    entity.pop("auto_helper")
    assert entity == {
        CONF_PLATFORM: "binary_sensor",
        CONF_NAME: "All Doors Ready",
        ATTR_ENTITY_ID: "binary_sensor.all_doors_ready",
        CONF_INITIAL_VALUE: "on",
        CONF_INITIAL_AVAILABILITY: True,
        CONF_PERSISTENT: True,
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
    }


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

    defaults = result["data_schema"]({})
    assert defaults[CONF_PLATFORM] == "sensor"
    assert defaults[CONF_INITIAL_VALUE] == "23.0"
    assert "reject('in'" in defaults[CONF_VALUE_TEMPLATE]
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
    hass.states.async_set("sensor.washer_power", "150", {"unit": "W"})
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
                        ATTR_ENTITY_ID: "sensor.virtual_washer",
                        CONF_INITIAL_VALUE: "idle",
                        CONF_INITIAL_AVAILABILITY: True,
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
    assert info.attributes["virtual_entity_id"] == "sensor.virtual_washer"
    assert info.attributes["configured_source_entities"] == [
        "sensor.washer_power",
        "binary_sensor.washer_door",
    ]
    assert info.attributes["configuration"]["platform"] == "sensor"
    assert debug_power.state == "150"
    assert debug_power.attributes["source_attributes"] == {"unit": "W"}
    assert debug_door.state == "on"
    assert debug_door.attributes["source_attributes"] == {"battery": 95}

    entity_registry = er.async_get(hass)
    primary_entry = entity_registry.async_get("sensor.virtual_washer")
    assert entity_registry.async_get("sensor.virtual_washer_info").device_id == primary_entry.device_id
    assert entity_registry.async_get("sensor.virtual_washer_debug1").device_id == primary_entry.device_id


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

    with patch.object(
        hass.config_entries,
        "async_forward_entry_setups",
        AsyncMock(return_value=True),
    ):
        assert await async_setup_entry(hass, entry) is True

    assert entity_registry.async_get("sensor.orphan_sensor") is None
    assert device_registry.async_get_device(
        identifiers={(COMPONENT_DOMAIN, "orphan-device")},
    ) is None


async def test_unload_entry_is_idempotent_when_group_data_is_missing(hass):
    entry = MockConfigEntry(
        domain=COMPONENT_DOMAIN,
        data={ATTR_GROUP_NAME: "missing"},
    )
    entry.add_to_hass(hass)
    hass.data[COMPONENT_DOMAIN] = {}

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
            CONF_ATTRIBUTE_TEMPLATES: {
                "copied": "{{ source }}",
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
    assert hass.states.get("tag.composite_tag").attributes["copied"] == "first"
    assert hass.states.get("tag.composite_tag").attributes["direct_setting"] == {
        "mode": "nfc",
        "priority": 1,
    }

    hass.states.async_set("sensor.tag_source", "second")
    await hass.async_block_till_done()
    assert hass.states.get("tag.composite_tag").state == "second"

    assert await async_unload_entry(hass, entry) is True
    hass.states.async_set("sensor.tag_source", "after_unload")
    await hass.async_block_till_done()
    assert hass.states.get("tag.composite_tag") is None


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

    def render_template(template):
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
