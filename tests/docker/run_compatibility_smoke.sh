#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.yml"

docker compose -f "$COMPOSE_FILE" pull homeassistant
docker compose -f "$COMPOSE_FILE" run --rm --no-deps -T \
  -e PYTHONPATH=/config \
  --entrypoint python \
  homeassistant - <<'PY'
import asyncio
import os
import tempfile
from threading import get_ident
from unittest.mock import AsyncMock, patch
from pathlib import Path

import yaml
from homeassistant import bootstrap, loader
from homeassistant.components.camera import CameraEntityFeature
from homeassistant.components.light import ColorMode, LightEntityFeature
from homeassistant.components.sensor import DEVICE_CLASS_UNITS
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant, HassJob
from homeassistant.util import dt as dt_util
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.virtual_layer import get_entity_from_domain, number, sensor
from custom_components.virtual_layer.camera import CAMERA_SCHEMA, VirtualCamera
from custom_components.virtual_layer.climate import CLIMATE_SCHEMA, VirtualClimate
from custom_components.virtual_layer.config_flow import (
    ACTION_EDIT_ENTITY,
    CONF_ACTION,
    CONF_ADD_FIRST_ENTITY,
    CONF_DOMAIN_OPTIONS_JSON,
    CONF_ENTITY_KEY,
    CONF_ENTITY_NAME,
    CONF_HELPER_UPDATE_MODE,
    CONF_NATIVE_VALUE_TEMPLATES,
    CONF_REFERENCE_ENTITY_ID,
    CONF_SENSOR_AGGREGATION,
    CONF_SENSOR_CONVERSION,
    CONF_USE_TEMPLATE_HELPER,
    HELPER_UPDATE_FORCE,
    _apply_sensor_conversion_defaults,
    _flatten_entity_form_sections,
    _sensor_conversion_choices,
)
from custom_components.virtual_layer.const import (
    ATTR_GROUP_NAME,
    COMPONENT_DOMAIN,
    CONF_INITIAL_VALUE,
    CONF_NATIVE_TEMPLATES,
    CONF_VALUE_TEMPLATE,
)
from custom_components.virtual_layer.light import LIGHT_SCHEMA, VirtualLight
from custom_components.virtual_layer.sensor import SENSOR_SCHEMA, VirtualSensor
from custom_components.virtual_layer.device_tracker import VirtualDeviceTracker
from custom_components.virtual_layer.entity import VirtualEntity
from custom_components.virtual_layer.vacuum import VACUUM_SCHEMA, VirtualVacuum


climate_config = CLIMATE_SCHEMA({
    "name": "Docker Climate",
    "entity_id": "climate.docker_climate",
    "initial_value": "heat",
    "hvac_modes": ["off", "heat"],
    "native_templates": {
        "hvac_action": "{{ <HVACAction.HEATING: 'heating'> }}",
    },
})
# Home Assistant's template validator requires its event loop. Add these after
# the synchronous schema smoke so this container test can cover runtime repair.
climate_config.update({
    "value_template": "{{ <HVACMode.HEAT: 'heat'> }}",
    "availability_template": "{{ <LegacyFlag.YES: True> }}",
    "icon_template": "{{ <LegacyIcon.FIRE: 'mdi:fire'> }}",
    "attribute_templates": {
        "source_type": "{{ <LegacySource.VIRTUAL: 'virtual'> }}",
    },
    "event_hooks": [{
        "trigger": "event",
        "event_type": "docker_test",
        "value_template": "{{ <HVACMode.HEAT: 'heat'> }}",
    }],
    "command_actions": {
        "set_temperature": [{
            "variables": {
                "legacy_limit": "{{ <LegacyLimit.MAX: 100> }}",
            },
        }],
    },
})
climate = VirtualClimate(climate_config, False)
vacuum = VirtualVacuum(
    VACUUM_SCHEMA({
        "name": "Docker Vacuum",
        "entity_id": "vacuum.docker_vacuum",
        "initial_value": "docked",
        "fan_speed_list": ["quiet", "turbo"],
    }),
    False,
)
camera = VirtualCamera(
    CAMERA_SCHEMA({
        "name": "Docker Camera",
        "entity_id": "camera.docker_camera",
        "initial_value": "on",
        "stream_source": "rtsp://camera/live",
    }),
    False,
)
light = VirtualLight(
    LIGHT_SCHEMA({
        "name": "Docker Matter Light",
        "entity_id": "light.docker_matter_light",
        "initial_value": "off",
        "matter_light_type": "extended_color",
        "support_effect": True,
    }),
    False,
)

assert climate._native_templates["hvac_action"] == "{{ 'heating' }}"
assert climate._value_template == "{{ 'heat' }}"
assert climate._availability_template == "{{ True }}"
assert climate._icon_template == "{{ 'mdi:fire' }}"
assert climate._attribute_templates["source_type"] == "{{ 'virtual' }}"
assert climate._event_hooks[0]["value_template"] == "{{ 'heat' }}"
assert climate._command_actions["set_temperature"][0]["variables"] == {
    "legacy_limit": "{{ 100 }}",
}
assert int(vacuum.supported_features) >= 0
# A copied, non-streaming source can contribute an ON_OFF-only mask before the
# user adds a direct H.264 URL. The URL must remain authoritative for STREAM.
camera._apply_native_template_value("supported_features", 1)
camera._sync_stream_capabilities()
assert CameraEntityFeature.STREAM in camera.supported_features
assert camera.use_stream_for_stills
assert light.supported_color_modes == {
    ColorMode.HS,
    ColorMode.XY,
    ColorMode.COLOR_TEMP,
}
assert LightEntityFeature.EFFECT not in light.supported_features
assert LightEntityFeature.FLASH not in light.supported_features
assert sensor.CONCENTRATION_PARTS_PER_MILLION == (
    number.CONCENTRATION_PARTS_PER_MILLION
)
assert sensor.CONCENTRATION_MICROGRAMS_PER_CUBIC_METER == (
    number.CONCENTRATION_MICROGRAMS_PER_CUBIC_METER
)


async def test_sensor_conversion_runtime():
    hass = HomeAssistant(tempfile.mkdtemp())
    try:
        from homeassistant.helpers.template import Template
        from custom_components.virtual_layer.air_quality_options import LEVELS, generate

        hass.states.async_set("sensor.aq_a", "0.005", {"unit_of_measurement": "mg/m³"})
        hass.states.async_set("sensor.aq_b", "25", {"unit_of_measurement": "μg/m³"})
        recipe = {
            "mode": "measurement", "sources": ["sensor.aq_a", "sensor.aq_b"],
            "unit": "μg/m³", "thresholds": [10, 20, 30, 40, 50],
            "levels": list(LEVELS), "multiplier": 2,
            "boundary_rule": "lower_inclusive",
        }
        for reducer, expected in (("per_source", "fair"), ("mean", "poor"),
                                  ("median", "poor"), ("minimum", "fair"),
                                  ("maximum", "extremely_poor")):
            helper = Template(generate({**recipe, "reducer": reducer}), hass)
            assert helper.async_render() == expected, (reducer, helper.async_render())
        hass.states.async_set(
            "climate.docker_source",
            "heat",
            {"current_temperature": 21.5, "temperature_unit": "°C"},
        )
        defaults = _apply_sensor_conversion_defaults(
            hass,
            {CONF_ENTITY_NAME: "Docker Converted Sensor"},
            _sensor_conversion_choices(["climate.docker_source"], hass)[
                "climate.docker_source:current_temperature"
            ],
        )
        config = {
            "name": "Docker Converted Sensor",
            "entity_id": "sensor.docker_converted_sensor",
            "initial_value": defaults[CONF_INITIAL_VALUE],
            CONF_VALUE_TEMPLATE: defaults[CONF_VALUE_TEMPLATE],
            CONF_NATIVE_TEMPLATES: defaults[CONF_NATIVE_VALUE_TEMPLATES],
            **yaml.safe_load(defaults[CONF_DOMAIN_OPTIONS_JSON]),
        }
        converted = VirtualSensor(SENSOR_SCHEMA(config), False)
        converted.hass = hass
        converted._create_state(converted._config)
        converted.async_schedule_update_ha_state = lambda *args, **kwargs: None
        converted._apply_templates()
        assert converted.native_value == "21.5"
        assert str(converted.device_class) == "temperature"
        assert str(converted.state_class) == "measurement"
        assert converted.native_unit_of_measurement == "°C"

        hass.states.async_set("light.docker_first", "on", {"brightness": 255})
        hass.states.async_set("light.docker_second", "on", {"brightness": 0})
        choices = _sensor_conversion_choices(
            ["light.docker_first", "light.docker_second"], hass
        )
        defaults = _apply_sensor_conversion_defaults(
            hass,
            {CONF_ENTITY_NAME: "Docker Brightness Sensor"},
            choices["brightness"],
        )
        rendered = converted._render_template(defaults[CONF_VALUE_TEMPLATE])
        assert float(rendered) == 50.0
        hass.states.async_set("light.docker_second", "on", {})
        rendered = converted._render_template(defaults[CONF_VALUE_TEMPLATE])
        assert float(rendered) == 100.0

        hass.states.async_set(
            "sensor.docker_pm25_micrograms",
            "10",
            {"device_class": "pm25", "unit_of_measurement": "µg/m³"},
        )
        hass.states.async_set(
            "sensor.docker_pm25_milligrams",
            "0.02",
            {"device_class": "pm25", "unit_of_measurement": "mg/m³"},
        )
        choices = _sensor_conversion_choices(
            [
                "sensor.docker_pm25_micrograms",
                "sensor.docker_pm25_milligrams",
            ],
            hass,
        )
        defaults = _apply_sensor_conversion_defaults(
            hass,
            {CONF_ENTITY_NAME: "Docker PM2.5 Sensor"},
            choices["state"],
        )
        pm25_config = {
            "name": "Docker PM2.5 Sensor",
            "entity_id": "sensor.docker_pm25_sensor",
            "initial_value": defaults[CONF_INITIAL_VALUE],
            CONF_VALUE_TEMPLATE: defaults[CONF_VALUE_TEMPLATE],
            CONF_NATIVE_TEMPLATES: defaults[CONF_NATIVE_VALUE_TEMPLATES],
            **yaml.safe_load(defaults[CONF_DOMAIN_OPTIONS_JSON]),
        }
        pm25 = VirtualSensor(SENSOR_SCHEMA(pm25_config), False)
        pm25.hass = hass
        pm25._create_state(pm25._config)
        pm25.async_schedule_update_ha_state = lambda *args, **kwargs: None
        pm25._apply_templates()
        assert float(pm25.native_value) == 15.0
        assert str(pm25.device_class) == "pm25"
        assert str(pm25.state_class) == "measurement"
        assert pm25.native_unit_of_measurement == "μg/m³"
        assert pm25.native_unit_of_measurement in DEVICE_CLASS_UNITS[
            str(pm25.device_class)
        ]

        pollution_cases = (
            ("pm10", "μg/m³", 30, "ug/m3", 10, "μg/m³", 20),
            ("carbon_dioxide", "ppm", 800, "ppb", 600000, "ppm", 700),
            ("carbon_monoxide", "ppb", 1000, "ppm", 3, "ppm", 2),
            (None, "Bq/m³", 37, "pCi/L", 1, "Bq/m³", 37),
        )
        for index, (
            device_class,
            first_unit,
            first_value,
            second_unit,
            second_value,
            expected_unit,
            expected_value,
        ) in enumerate(pollution_cases):
            source_ids = (
                f"sensor.docker_pollution_{index}_first",
                f"sensor.docker_pollution_{index}_second",
            )
            for entity_id, value, unit in (
                (source_ids[0], first_value, first_unit),
                (source_ids[1], second_value, second_unit),
            ):
                attributes = {"unit_of_measurement": unit}
                if device_class is not None:
                    attributes["device_class"] = device_class
                hass.states.async_set(entity_id, str(value), attributes)
            defaults = _apply_sensor_conversion_defaults(
                hass,
                {CONF_ENTITY_NAME: f"Docker Pollution Sensor {index}"},
                _sensor_conversion_choices(source_ids, hass)["state"],
            )
            options = yaml.safe_load(defaults[CONF_DOMAIN_OPTIONS_JSON])
            assert float(converted._render_template(
                defaults[CONF_VALUE_TEMPLATE]
            )) == expected_value
            assert options["unit_of_measurement"] == expected_unit
            assert options.get("class") == device_class
            if device_class is not None:
                assert expected_unit in DEVICE_CLASS_UNITS[device_class]
    finally:
        await hass.async_stop()


asyncio.run(test_sensor_conversion_runtime())


async def configure_flow(manager, result, user_input):
    """Submit one real Home Assistant config/options flow step."""
    result = await manager.async_configure(result["flow_id"], user_input)
    assert not result.get("errors"), result.get("errors")
    return result


async def test_tracker_timer_dispatch(hass):
    """Exercise HA's real timer-job dispatch for both tracker helper modes."""
    periodic_sensor = VirtualSensor({
        "name": "Docker Periodic", "entity_id": "sensor.docker_periodic",
        "pull_interval": 60, "persistent": False,
    }, False)
    periodic_sensor.hass = hass
    callbacks = []
    threads = []
    with (
        patch.object(periodic_sensor, "_apply_templates", side_effect=lambda: threads.append(get_ident())),
        patch("custom_components.virtual_layer.entity.async_track_time_interval",
              side_effect=lambda _hass, action, interval: callbacks.append(action) or (lambda: None)),
    ):
        periodic_sensor._setup_templates()
        assert len(callbacks) == 1
        hass.async_run_hass_job(HassJob(callbacks[0]), dt_util.utcnow())
        await hass.async_block_till_done()
        assert threads == [hass.loop_thread_id], threads
    for polygon in (False, True):
        tracker = VirtualDeviceTracker({
            "name": "Docker Timer", "entity_id": "device_tracker.docker_timer",
            "initial_value": "not_home", "persistent": False,
            "location_helper": {"distance_threshold_meters": 300},
        })
        tracker.hass = hass
        if polygon:
            tracker._polygon_config = {"geojson": None}
        callbacks = []
        threads = []
        method = "_update_polygon_from_sources" if polygon else "_update_location_from_sources"
        with (
            patch.object(VirtualEntity, "async_added_to_hass", new=AsyncMock()),
            patch.object(tracker, "_async_reload_polygon_zones", new=AsyncMock()),
            patch.object(tracker, method, side_effect=lambda: threads.append(get_ident())),
            patch("custom_components.virtual_layer.device_tracker.async_track_time_interval",
                  side_effect=lambda _hass, action, interval: callbacks.append(action) or (lambda: None)),
        ):
            await tracker.async_added_to_hass()
            threads.clear()
            assert len(callbacks) == 1
            hass.async_run_hass_job(HassJob(callbacks[0]), dt_util.utcnow())
            await hass.async_block_till_done()
            assert threads == [hass.loop_thread_id], (polygon, threads)


async def test_config_flow_create_modify_runtime():
    """Create, load, edit, reload, and live-update through real HA flows."""
    config_dir = Path(tempfile.mkdtemp())
    custom_components_dir = config_dir / "custom_components"
    custom_components_dir.mkdir()
    os.symlink(
        "/config/custom_components/virtual_layer",
        custom_components_dir / "virtual_layer",
    )
    hass = HomeAssistant(str(config_dir))
    loader.async_setup(hass)
    try:
        assert await bootstrap.async_from_config_dict({}, hass) is hass
        assert await async_setup_component(hass, COMPONENT_DOMAIN, {})
        await hass.async_start()
        await test_tracker_timer_dispatch(hass)

        source_ids = ["sensor.docker_flow_pm25_a", "sensor.docker_flow_pm25_b"]
        hass.states.async_set(
            source_ids[0],
            "10",
            {"device_class": "pm25", "unit_of_measurement": "µg/m³"},
        )
        hass.states.async_set(
            source_ids[1],
            "0.02",
            {"device_class": "pm25", "unit_of_measurement": "mg/m³"},
        )

        flow = hass.config_entries.flow
        result = await flow.async_init(
            COMPONENT_DOMAIN,
            context={"source": SOURCE_USER},
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"
        result = await configure_flow(
            flow,
            result,
            {ATTR_GROUP_NAME: "Docker Air", CONF_ADD_FIRST_ENTITY: True},
        )
        assert result["step_id"] == "entity_source"
        result = await configure_flow(
            flow,
            result,
            {CONF_REFERENCE_ENTITY_ID: source_ids},
        )
        assert result["step_id"] == "sensor_conversion"
        result = await configure_flow(
            flow,
            result,
            {
                CONF_SENSOR_CONVERSION: "state",
                CONF_SENSOR_AGGREGATION: "average",
            },
        )
        assert result["step_id"] == "entity_helper"
        result = await configure_flow(
            flow,
            result,
            {CONF_USE_TEMPLATE_HELPER: True},
        )
        assert result["step_id"] == "entity"
        create_defaults = _flatten_entity_form_sections(result["data_schema"]({}))
        create_defaults[CONF_ENTITY_NAME] = "Docker PM2.5"
        create_defaults["device_name"] = "Docker Air"
        create_defaults["entity_id"] = "sensor.docker_flow_pm25"
        result = await configure_flow(flow, result, create_defaults)
        assert result["type"] == FlowResultType.CREATE_ENTRY
        entry = result["result"]
        await hass.async_block_till_done()
        assert entry.state.value == "loaded"

        created_state = hass.states.get("sensor.docker_flow_pm25")
        assert created_state is not None
        assert float(created_state.state) == 15.0
        assert created_state.attributes["device_class"] == "pm25"
        assert created_state.attributes["state_class"] == "measurement"
        assert created_state.attributes["unit_of_measurement"] == "μg/m³"

        registry = er.async_get(hass)
        created_registry_entry = registry.async_get("sensor.docker_flow_pm25")
        assert created_registry_entry is not None
        assert created_registry_entry.device_id is not None
        original_unique_id = created_registry_entry.unique_id
        device_id = created_registry_entry.device_id
        for suffix in ("info", "debug1", "debug2"):
            companion_id = f"sensor.docker_flow_pm25_{suffix}"
            assert hass.states.get(companion_id) is not None
            companion_entry = registry.async_get(companion_id)
            assert companion_entry is not None
            assert companion_entry.device_id == device_id

        options_flow = hass.config_entries.options
        result = await options_flow.async_init(
            entry.entry_id,
            data={CONF_ACTION: ACTION_EDIT_ENTITY},
        )
        assert result["step_id"] == "select_entity"
        entity_key = result["data_schema"]({})[CONF_ENTITY_KEY]
        result = await configure_flow(
            options_flow,
            result,
            {CONF_ENTITY_KEY: entity_key},
        )
        assert result["step_id"] == "edit_entity_source"
        result = await configure_flow(
            options_flow,
            result,
            {CONF_REFERENCE_ENTITY_ID: source_ids},
        )
        assert result["step_id"] == "edit_sensor_conversion"
        result = await configure_flow(
            options_flow,
            result,
            {
                CONF_SENSOR_CONVERSION: "state",
                CONF_SENSOR_AGGREGATION: "maximum",
            },
        )
        assert result["step_id"] == "edit_entity_helper"
        result = await configure_flow(
            options_flow,
            result,
            {CONF_HELPER_UPDATE_MODE: HELPER_UPDATE_FORCE},
        )
        assert result["step_id"] == "edit_entity"
        edit_defaults = _flatten_entity_form_sections(result["data_schema"]({}))
        edit_defaults[CONF_ENTITY_NAME] = "Docker PM2.5 Maximum"
        edit_defaults["entity_id"] = "sensor.docker_flow_pm25_max"

        invalid_defaults = dict(edit_defaults)
        invalid_defaults[CONF_VALUE_TEMPLATE] = "{{ invalid template"
        invalid_result = await options_flow.async_configure(
            result["flow_id"], invalid_defaults
        )
        assert invalid_result["type"] == FlowResultType.FORM
        assert invalid_result["step_id"] == "edit_entity"
        assert "invalid_template" in invalid_result["errors"].values(), (
            invalid_result["errors"]
        )

        result = await configure_flow(options_flow, invalid_result, edit_defaults)
        assert result["type"] == FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()

        assert hass.states.get("sensor.docker_flow_pm25") is None
        modified_id = "sensor.docker_air_docker_pm2_5_maximum"
        modified_state = hass.states.get(modified_id)
        assert modified_state is not None
        assert float(modified_state.state) == 20.0
        assert modified_state.attributes["unit_of_measurement"] == "μg/m³"
        modified_registry_entry = registry.async_get(modified_id)
        assert modified_registry_entry is not None
        assert modified_registry_entry.unique_id == original_unique_id
        assert modified_registry_entry.device_id == device_id
        assert registry.async_get("sensor.docker_flow_pm25") is None
        for suffix in ("info", "debug1", "debug2"):
            assert registry.async_get(f"sensor.docker_flow_pm25_{suffix}") is None
            companion_id = f"{modified_id}_{suffix}"
            companion_entry = registry.async_get(companion_id)
            assert companion_entry is not None
            assert companion_entry.device_id == device_id

        hass.states.async_set(
            source_ids[1],
            "0.03",
            {"device_class": "pm25", "unit_of_measurement": "mg/m³"},
        )
        await hass.async_block_till_done()
        assert float(hass.states.get(modified_id).state) == 30.0

        for source_id in source_ids:
            hass.states.async_set(
                source_id,
                "unavailable",
                {"device_class": "pm25", "unit_of_measurement": "μg/m³"},
            )
        await hass.async_block_till_done()
        assert hass.states.get(modified_id).state == "unavailable"

        hass.states.async_set(
            source_ids[0],
            "25",
            {"device_class": "pm25", "unit_of_measurement": "μg/m³"},
        )
        await asyncio.sleep(0.1)
        await hass.async_block_till_done()
        recovered_state = hass.states.get(modified_id)
        runtime_entity = get_entity_from_domain(
            hass, "sensor", modified_id
        )
        assert float(recovered_state.state) == 25.0, (
            recovered_state,
            runtime_entity._restore_waiting_for_sources,
            runtime_entity._render_template(runtime_entity._value_template),
            runtime_entity._value_template,
        )
        assert recovered_state.attributes["available"] is True
    finally:
        await hass.async_stop()


asyncio.run(test_config_flow_create_modify_runtime())
print(
    "Virtual Layer Docker compatibility smoke passed "
    f"on Home Assistant {HA_VERSION}"
)
PY
