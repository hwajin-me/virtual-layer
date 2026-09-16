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
import copy
import math
import os
import tempfile
from datetime import timedelta
from threading import get_ident
from unittest.mock import AsyncMock, Mock, patch
from pathlib import Path
from io import BytesIO

import yaml
from PIL import Image
from homeassistant import bootstrap, loader
from homeassistant.components.climate import HVACMode
from homeassistant.helpers.data_entry_flow import FlowManagerResourceView
from homeassistant.components.camera import CameraEntityFeature
from homeassistant.components.light import ColorMode, LightEntityFeature
from homeassistant.components.sensor import DEVICE_CLASS_UNITS
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant, HassJob, State
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
    _motion_hold_schema,
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
for property_name, value in {
    "brightness": 128, "color_mode": "hs", "color_temp_kelvin": 4000,
    "hs_color": [120, 50], "xy_color": [0.25, 0.5],
    "rgb_color": [10, 20, 30], "rgbw_color": [10, 20, 30, 40],
    "rgbww_color": [10, 20, 30, 40, 50],
}.items():
    light._apply_native_template_value(property_name, value)
    previous = getattr(light, property_name)
    for missing in (None, "None", "", "unknown", "unavailable"):
        assert not light._apply_native_template_value(property_name, missing)
        assert getattr(light, property_name) == previous
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

        # UI response serialization happens after the flow manager advances.
        # Custom validators here strand the browser on the previous form.
        FlowManagerResourceView(None)._prepare_result_json({
            "type": FlowResultType.FORM,
            "data_schema": _motion_hold_schema({}),
        })
        hass.states.async_set("climate.enum_source", "cool", {
            "hvac_modes": [HVACMode.OFF, HVACMode.COOL],
        })
        enum_climate = VirtualClimate(CLIMATE_SCHEMA({
            "name": "Enum Climate", "entity_id": "climate.enum_virtual",
            "initial_value": "off", "hvac_modes": ["off", "heat"],
            "native_templates": {
                "hvac_modes": "{{ state_attr('climate.enum_source', 'hvac_modes') }}",
            },
        }), False)
        enum_climate.hass = hass
        enum_climate.async_schedule_update_ha_state = lambda *args, **kwargs: None
        enum_climate._create_state(enum_climate._config)
        assert enum_climate._apply_restore_prerequisite_templates()
        assert enum_climate.hvac_modes == [HVACMode.OFF, HVACMode.COOL]
        hass.states.async_set("climate.enum_source", "heat", {
            "hvac_modes": [HVACMode.OFF, HVACMode.HEAT],
        })
        enum_climate._apply_templates()
        assert enum_climate.hvac_modes == [HVACMode.OFF, HVACMode.HEAT]

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
            ("volatile_organic_compounds", "ppb", 100, "mg/m³", 0.45, "mg/m³", 0.45),
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
            rendered = float(converted._render_template(defaults[CONF_VALUE_TEMPLATE]))
            assert math.isclose(rendered, expected_value), (device_class, rendered, expected_value)
            assert options["unit_of_measurement"] == expected_unit
            assert options.get("class") == device_class
            if device_class is not None:
                assert expected_unit in DEVICE_CLASS_UNITS[device_class]
    finally:
        await hass.async_stop()


asyncio.run(test_sensor_conversion_runtime())


def suggested_form_values(schema):
    """Submit the ID displayed by the frontend unless the test clears it."""
    values = schema({})
    for marker in schema.schema:
        if marker == "entity_id" and marker.description:
            values["entity_id"] = marker.description["suggested_value"]
    return values


async def configure_flow(manager, result, user_input):
    """Submit one real Home Assistant config/options flow step."""
    result = await manager.async_configure(result["flow_id"], user_input)
    assert not result.get("errors"), result.get("errors")
    if result.get("data_schema") is not None:
        FlowManagerResourceView(manager)._prepare_result_json(result)
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


async def test_dawarich_http_tracking(hass):
    """Exercise real HTTP, UI validation and polygon selection in official HA."""
    from aiohttp import web
    from custom_components.virtual_layer.config_flow import _entity_schema, _async_build_entity_config

    timestamp = dt_util.utcnow().timestamp()
    requests = []

    async def points(request):
        assert request.headers.get("Authorization") == "Bearer docker-only-key"
        requests.append(request.path)
        return web.json_response([
            {"latitude": "37.5", "longitude": "127.0", "timestamp": timestamp, "accuracy": 12},
            {"latitude": 999, "longitude": 0, "timestamp": timestamp},
        ])

    async def visits(request):
        assert "start_at" in request.query and "end_at" in request.query
        return web.json_response([{"name": "Office", "started_at": timestamp - 60}])

    app = web.Application()
    app.router.add_get("/api/v1/points", points)
    app.router.add_get("/api/v1/visits", visits)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    try:
        for polygon in (False, True):
            form = _entity_schema({"platform": "device_tracker", "entity_name": "Dawarich Docker"})({})
            form.update({"device_name": "Dawarich Docker", "entity_id": "device_tracker.dawarich_docker"})
            form["dawarich_settings"].update({
                "dawarich_enabled": True, "dawarich_url": f"http://127.0.0.1:{port}",
                "dawarich_api_key": "docker-only-key", "dawarich_test_connection": True,
            })
            if polygon:
                form["domain_settings"]["polygon_geojson_json"] = {
                    "type": "Feature", "properties": {"name": "Office"},
                    "geometry": {"type": "Polygon", "coordinates": [[[126.9,37.4],[127.1,37.4],[127.1,37.6],[126.9,37.6],[126.9,37.4]]]},
                }
            _, config = await _async_build_entity_config(hass, form)
            tracker = VirtualDeviceTracker(config)
            tracker.hass = hass
            tracker.async_schedule_update_ha_state = Mock()
            tracker._create_state(config)
            if polygon:
                await tracker._async_reload_polygon_zones()
            await tracker._async_refresh_dawarich()
            assert tracker.latitude == 37.5, tracker.extra_state_attributes
            assert tracker.extra_state_attributes["dawarich_visit"]["name"] == "Office"
            assert tracker.extra_state_attributes["dawarich_stale"] is False
            assert "docker-only-key" not in str(tracker.extra_state_attributes)
            if polygon:
                assert tracker.extra_state_attributes["polygon_zone"] == "Office"
            await tracker.async_will_remove_from_hass()
        assert len(requests) == 4
        print("Dawarich HTTP smoke passed: UI connection test, actual HTTP, visits, standalone/polygon GPS, credential isolation")
    finally:
        await runner.cleanup()


async def test_local_presence(hass):
    """Exercise the real HA scanner registry without requiring host BLE hardware."""
    from homeassistant.components import bluetooth
    from habluetooth import BaseHaRemoteScanner
    from types import SimpleNamespace

    adapters = SimpleNamespace(refresh=AsyncMock(), adapters={}, history={}, default_adapter=None)
    with patch("homeassistant.components.bluetooth.get_adapters", return_value=adapters), patch(
        "homeassistant.components.bluetooth.manager.async_load_history_from_system", return_value=({}, {})
    ), patch("homeassistant.components.bluetooth.BleakSlotManager.async_setup", new=AsyncMock()):
        assert await async_setup_component(hass, "bluetooth", {})
        await hass.async_block_till_done()
    scanner = BaseHaRemoteScanner("ab_gateway", "AB Gateway", connectable=False)
    stop = scanner.async_setup()
    unregister = bluetooth.async_register_scanner(hass, scanner)
    mac = "AA:BB:CC:DD:EE:FF"
    config = {"name": "Local presence", "entity_id": "device_tracker.local_smoke",
              "initial_value": "not_home", "initial_availability": True,
              "local_presence": {"wifi_entities": ["binary_sensor.smoke_wifi"], "ble_addresses": [mac]}}
    tracker = VirtualDeviceTracker(config)
    tracker.hass = hass
    tracker.async_schedule_update_ha_state = Mock()
    tracker._create_state(config)
    try:
        hass.states.async_set("binary_sensor.smoke_wifi", "off")
        tracker._update_location_from_sources()
        assert tracker.state == "not_home"
        scanner._async_on_advertisement(mac.lower(), -60, "Phone", [], {}, {}, None, {}, bluetooth.MONOTONIC_TIME())
        tracker._update_location_from_sources()
        assert tracker.state == "home"
        assert tracker.latitude == hass.config.latitude
        assert tracker.extra_state_attributes["location_presence_sources"] == ["ble:" + mac]
        with patch("homeassistant.components.bluetooth.MONOTONIC_TIME", return_value=bluetooth.MONOTONIC_TIME() + 121):
            tracker._update_location_from_sources()
            assert tracker.state == "not_home"
            hass.states.async_set("binary_sensor.smoke_wifi", "on")
            tracker._update_location_from_sources()
            assert tracker.state == "home"
        print("Wi-Fi / AB Gateway presence smoke passed: HA scanner registry, advertisements, timeout, Wi-Fi home GPS")
    finally:
        unregister()
        stop()


async def test_tracker_creation_flows(hass):
    """Create, edit, reload and delete all tracker presets through real HA flows."""
    from aiohttp import web
    from habluetooth import BaseHaRemoteScanner
    from homeassistant.components import bluetooth
    from custom_components.virtual_layer import config_flow as vf

    requests = []

    async def points(request):
        assert request.headers.get("Authorization") == "Bearer flow-test-key"
        requests.append(request.path)
        return web.json_response([{"latitude": 37.5, "longitude": 127,
                                  "timestamp": dt_util.utcnow().timestamp(), "accuracy": 8}])

    app = web.Application()
    app.router.add_get("/api/v1/points", points)
    app.router.add_get("/api/v1/visits", lambda request: web.json_response([]))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    url = f"http://127.0.0.1:{runner.addresses[0][1]}"
    scanner = BaseHaRemoteScanner("ab_gateway", "AB Gateway Flow", connectable=False)
    stop = scanner.async_setup()
    unregister = bluetooth.async_register_scanner(hass, scanner)
    mac = "AA:BB:CC:DD:EE:01"
    scanner._async_on_advertisement(mac.lower(), -55, "Phone", [], {}, {}, None, {}, bluetooth.MONOTONIC_TIME())
    entries = []
    registry = er.async_get(hass)
    try:
        for kind in ("dawarich", "wifi", "ble"):
            entry = None
            device_key = None
            created_ids = []
            for initial in (True, False):
                entity_id = f"device_tracker.flow_{kind}_{int(initial)}"
                created_ids.append(entity_id)
                wifi_id = f"binary_sensor.flow_wifi_{int(initial)}"
                hass.states.async_set(wifi_id, "on")
                if initial:
                    manager = hass.config_entries.flow
                    result = await manager.async_init(COMPONENT_DOMAIN, context={"source": SOURCE_USER})
                    result = await configure_flow(manager, result, {
                        ATTR_GROUP_NAME: f"Flow {kind}", CONF_ADD_FIRST_ENTITY: True,
                    })
                else:
                    manager = hass.config_entries.options
                    result = await manager.async_init(entry.entry_id, data={CONF_ACTION: vf.ACTION_ADD_ENTITY})
                source_input = {"tracker_creation": kind, CONF_REFERENCE_ENTITY_ID: [wifi_id] if kind == "wifi" else []}
                if not initial:
                    source_input[vf.CONF_TARGET_DEVICE_NAME] = device_key
                result = await configure_flow(manager, result, source_input)
                assert result["step_id"] == "tracker_settings", result
                values = suggested_form_values(result["data_schema"])
                if kind == "dawarich":
                    assert values["dawarich_settings"]["dawarich_enabled"]
                    values["dawarich_settings"].update({"dawarich_url": url, "dawarich_api_key": "flow-test-key", "dawarich_test_connection": True})
                else:
                    assert values["local_presence_settings"]["presence_enabled"]
                    if kind == "ble":
                        values["local_presence_settings"]["presence_ble_addresses"] = mac
                result = await configure_flow(manager, result, values)
                assert result["step_id"] == "entity", result
                values = _flatten_entity_form_sections(suggested_form_values(result["data_schema"]))
                assert "dawarich_settings" not in result["data_schema"]({})
                assert "local_presence_settings" not in result["data_schema"]({})
                values.update({"entity_id": entity_id, CONF_ENTITY_NAME: f"Flow {kind} {int(initial)}"})
                if initial:
                    values["device_name"] = f"Flow {kind}"
                result = await configure_flow(manager, result, values)
                assert result["type"] == FlowResultType.CREATE_ENTRY, result
                if initial:
                    entry = result["result"]
                    entries.append(entry)
                await hass.async_block_till_done()
                device_key, entities = next(iter(entry.options["devices"].items()))
                assert len(entities) == (1 if initial else 2)
                saved = next(item for item in entities if item["entity_id"] == entity_id)
                assert ("dawarich" if kind == "dawarich" else "local_presence") in saved
                state = hass.states.get(entity_id)
                assert state is not None, entity_id
                assert state.attributes["latitude"] == (37.5 if kind == "dawarich" else hass.config.latitude), state
                assert "flow-test-key" not in str(state.attributes)
                primary = registry.async_get(entity_id)
                assert registry.async_get(f"sensor.{entity_id.split('.')[1]}_info").device_id == primary.device_id
                if not initial:
                    assert primary.device_id == registry.async_get(created_ids[0]).device_id
                if kind == "wifi":
                    assert state.state == "home"
                    hass.states.async_set(wifi_id, "off")
                    await hass.async_block_till_done()
                    assert hass.states.get(entity_id).state == "not_home"
                    hass.states.async_set(wifi_id, "on")
                    await hass.async_block_till_done()
                if kind == "ble":
                    assert state.attributes["location_presence_sources"] == ["ble:" + mac]
                # Edit the just-created tracker using its stable selection key.
                manager = hass.config_entries.options
                selection = vf._selection_key_for_entity(device_key, entities.index(saved), saved)
                result = await manager.async_init(entry.entry_id, data={CONF_ACTION: vf.ACTION_EDIT_ENTITY})
                result = await configure_flow(manager, result, {CONF_ENTITY_KEY: selection})
                result = await configure_flow(manager, result, {CONF_REFERENCE_ENTITY_ID: []})
                assert result["step_id"] == "tracker_settings", result
                values = suggested_form_values(result["data_schema"])
                field = "dawarich_poll_interval" if kind == "dawarich" else "presence_ble_timeout"
                values["dawarich_settings" if kind == "dawarich" else "local_presence_settings"][field] = 180
                result = await configure_flow(manager, result, values)
                assert result["step_id"] == "edit_entity", result
                result = await configure_flow(manager, result, suggested_form_values(result["data_schema"]))
                assert result["type"] == FlowResultType.CREATE_ENTRY
                await hass.async_block_till_done()
                assert await hass.config_entries.async_reload(entry.entry_id)
                await hass.async_block_till_done()
                assert hass.states.get(entity_id).attributes.get("latitude") is not None
                updated = next(item for item in entry.options["devices"][device_key] if item["entity_id"] == entity_id)
                assert updated["dawarich" if kind == "dawarich" else "local_presence"]["poll_interval" if kind == "dawarich" else "ble_timeout"] == 180
            keys = [vf._selection_key_for_entity(device_key, i, entity) for i, entity in enumerate(entry.options["devices"][device_key])]
            result = await manager.async_init(entry.entry_id, data={CONF_ACTION: vf.ACTION_DELETE_ENTITY})
            result = await configure_flow(manager, result, {vf.CONF_ENTITY_KEYS: keys})
            assert result["type"] == FlowResultType.CREATE_ENTRY
            await hass.async_block_till_done()
            for entity_id in created_ids:
                assert hass.states.get(entity_id) is None
                assert registry.async_get(entity_id) is None
                assert registry.async_get(f"sensor.{entity_id.split('.')[1]}_info") is None
            print(f"Tracker config-flow Docker passed: {kind}, initial + options creation, runtime, Device grouping, edit, reload, delete")
        assert requests, "Dawarich config-flow trackers did not call the HTTP server"
    finally:
        for entry in entries:
            await hass.config_entries.async_remove(entry.entry_id)
        unregister()
        stop()
        await runner.cleanup()


def test_tracker_measurement_clock(hass):
    """Delayed delivery and attribute refreshes must not replace fix time."""
    config = {
        "name": "Clock Smoke", "entity_id": "device_tracker.clock_smoke",
        "initial_value": "not_home", "initial_availability": True,
        "source_entities": ["device_tracker.clock_smoke_source"],
        "location_helper": {"distance_threshold_meters": 300},
    }
    tracker = VirtualDeviceTracker(config)
    tracker.hass = hass
    tracker.async_schedule_update_ha_state = Mock()
    tracker._create_state(config)
    measured = dt_util.utcnow()
    source = config["source_entities"][0]
    hass.states.async_set(source, "not_home", {
        "lat": 37.5, "lon": 127, "acc": 5, "last_seen": (measured - timedelta(seconds=120)).isoformat(),
    })
    tracker._update_location_from_sources()
    hass.states.async_set(source, "not_home", {
        "lat": 37.51, "lon": 127, "acc": 5, "last_seen": measured.isoformat(),
    })
    tracker._update_location_from_sources()
    assert abs(tracker.extra_state_attributes["location_speed_m_s"] - 9.27) < 0.05
    assert tracker.extra_state_attributes["location_bearing"] == 0
    hass.states.async_set(source, "not_home", {
        "lat": 38, "lon": 127, "acc": 5, "last_seen": (measured - timedelta(seconds=60)).isoformat(),
    })
    tracker._update_location_from_sources()
    assert tracker.latitude == 37.51
    assert tracker.extra_state_attributes["location_rejected_sources"][source] == "out_of_order"
    assert tracker.extra_state_attributes["location_speed_m_s"] is None
    print("Tracker measurement-clock smoke passed: aliases, delayed fixes, speed, bearing, out-of-order rejection")


def test_tracker_adaptive_travel(hass):
    """Check partial-device travel against the official HA State API."""
    for polygon in (False, True):
        sources = [f"device_tracker.docker_trip_{int(polygon)}_{i}" for i in range(3)]
        config = {
            "name": "Docker Trip", "entity_id": "device_tracker.docker_trip",
            "initial_value": "not_home", "persistent": False,
            "source_entities": sources,
            "location_helper": {"distance_threshold_meters": 300},
        }
        if polygon:
            config["polygonal_zone"] = {
                "strategy": "adaptive",
                "geojson": {
                    "type": "Feature", "properties": {"name": "Home"},
                    "geometry": {"type": "Polygon", "coordinates": [[
                        [126.99, 37.49], [127.01, 37.49], [127.01, 37.505],
                        [126.99, 37.505], [126.99, 37.49],
                    ]]},
                },
            }
        tracker = VirtualDeviceTracker(config)
        tracker.hass = hass
        tracker.async_schedule_update_ha_state = Mock()
        tracker._create_state(config)
        update = tracker._update_polygon_from_sources if polygon else tracker._update_location_from_sources
        now = dt_util.utcnow() - timedelta(hours=1)
        with (
            patch("homeassistant.core.time.time", side_effect=lambda: now.timestamp()),
            patch("homeassistant.util.dt.utcnow", side_effect=lambda: now),
        ):
            for source in sources:
                hass.states.async_set(source, "not_home", {
                    "latitude": 37.5, "longitude": 127, "gps_accuracy": 12,
                })
            update()
            now += timedelta(seconds=60)
            hass.states.async_set(sources[0], "not_home", {
                "latitude": 37.51, "longitude": 127, "gps_accuracy": 12,
            })
            update()
            assert tracker.latitude == 37.51, (polygon, tracker.extra_state_attributes)
            assert tracker.location_accuracy == 12
            now += timedelta(minutes=31)
            for source in sources:
                state = hass.states.get(source)
                hass.states.async_set(source, state.state, dict(state.attributes))
            update()
            assert tracker.latitude == 37.51
            assert tracker.extra_state_attributes["location_stale"] is False
            now += timedelta(seconds=1)
            hass.states.async_set(sources[0], "not_home", {
                "latitude": 35, "longitude": 129, "gps_accuracy": 12,
            })
            update()
            assert tracker.latitude == 37.51
            assert tracker.extra_state_attributes["location_rejected_sources"][sources[0]] == "implausible_speed"
    print("Adaptive tracker smoke passed: departure, stationary reports, accuracy, GPS jump; helper and polygon")


async def test_source_startup_grace(hass):
    """Validate the startup grace callback against the real HA entity API."""
    hass.states.async_set("sensor.grace_source", "unknown")
    entity = VirtualSensor(SENSOR_SCHEMA({
        "name": "Grace", "entity_id": "sensor.grace_virtual", "persistent": True,
        "source_entities": ["sensor.grace_source"],
        "value_template": "{{ states('sensor.grace_source') }}",
        "availability_template": "{{ has_value('sensor.grace_source') }}",
    }), False)
    entity.hass = hass
    callbacks = {}
    def schedule(_hass, delay, action):
        callbacks[delay] = action
        return lambda: None
    with (
        patch.object(entity, "async_get_last_state", AsyncMock(return_value=State(entity.entity_id, "42"))),
        patch.object(entity, "async_write_ha_state"),
        patch.object(entity, "async_schedule_update_ha_state"),
        patch("custom_components.virtual_layer.entity.async_call_later", side_effect=schedule),
    ):
        await entity.async_added_to_hass()
        assert entity.native_value == "42"
        assert entity.available
        assert 180 in callbacks
        callbacks[180](None)
        assert not entity.available
        hass.states.async_set("sensor.grace_source", "45")
        await hass.async_block_till_done()
        assert entity.available
        assert str(entity.native_value) == "45"
        await entity.async_will_remove_from_hass()


async def test_image_camera_encoding(hass):
    """Verify image aliases and the container's H.264 encoder with real bytes."""
    output = BytesIO()
    Image.new("RGBA", (501, 1501), (0, 0, 255, 128)).save(output, "PNG")
    source = Mock(async_image=AsyncMock(return_value=output.getvalue()))
    entity = VirtualCamera(CAMERA_SCHEMA({
        "name": "Docker Map", "entity_id": "camera.docker_map",
        "source_entity": "image.docker_map",
    }), False)
    entity.hass = hass
    entity._create_state(entity._config)
    with patch.dict(hass.data, {"image": Mock(get_entity=Mock(return_value=source))}):
        jpeg = await entity.async_camera_image()
    assert jpeg.startswith(b"\xff\xd8")
    with Image.open(BytesIO(jpeg)) as rendered:
        assert rendered.size == (1280, 720)
    entity._sync_stream_capabilities()
    assert CameraEntityFeature.STREAM not in entity.supported_features
    process = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-f", "image2pipe", "-framerate", "25", "-vcodec", "mjpeg", "-i", "pipe:0",
        "-frames:v", "3", "-c:v", "libx264", "-profile:v", "baseline",
        "-tune", "zerolatency", "-pix_fmt", "yuv420p", "-f", "h264", "pipe:1",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        encoded, errors = await asyncio.wait_for(process.communicate(jpeg * 3), 30)
        assert process.returncode == 0, errors.decode()
        assert len(encoded) > 100 and b"\x00\x00\x00\x01" in encoded
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


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
        assert await bootstrap.async_from_config_dict({"recorder": {
            "db_url": f"sqlite:///{config_dir / 'unit-history.db'}",
        }}, hass) is hass
        assert "recorder" in hass.config.components
        assert await async_setup_component(hass, COMPONENT_DOMAIN, {})
        await hass.async_start()
        from custom_components.virtual_layer import unit_history
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.statistics import async_import_statistics, statistics_during_period
        from homeassistant.components.recorder.tasks import SynchronizeTask
        from functools import partial
        from datetime import timedelta
        from homeassistant.util import dt as dt_util
        start = dt_util.utcnow().replace(minute=0, second=0, microsecond=0) - timedelta(hours=2)
        for policy, expected in [("convert", 1), ("relabel", 1000)]:
            statistic_id = f"sensor.docker_unit_{policy}"
            async_import_statistics(hass, {"source": "recorder", "statistic_id": statistic_id,
                "name": "Unit test", "unit_class": "power", "unit_of_measurement": "W",
                "mean_type": 1, "has_sum": False},
                [{"start": start, "mean": 1000, "min": 900, "max": 1100}])
            committed = hass.loop.create_future()
            get_instance(hass).queue_task(SynchronizeTask(committed))
            await committed
            metadata = await unit_history.statistics_snapshot(hass, statistic_id)
            await unit_history.apply_statistics_policy(hass, statistic_id, policy, "kW", metadata)
            result = await get_instance(hass).async_add_executor_job(partial(statistics_during_period,
                hass, start, None, {statistic_id}, "hour", None, {"mean"}))
            assert result[statistic_id][0]["mean"] == expected
        await test_tracker_timer_dispatch(hass)
        test_tracker_adaptive_travel(hass)
        test_tracker_measurement_clock(hass)
        await test_local_presence(hass)
        await test_tracker_creation_flows(hass)
        await test_dawarich_http_tracking(hass)
        await test_source_startup_grace(hass)
        await test_image_camera_encoding(hass)

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
        create_defaults = _flatten_entity_form_sections(suggested_form_values(result["data_schema"]))
        create_defaults[CONF_ENTITY_NAME] = "Docker PM2.5"
        create_defaults["device_name"] = "Docker Air"
        create_defaults["entity_id"] = "sensor.docker_flow_pm25"
        result = await configure_flow(flow, result, create_defaults)
        assert result["type"] == FlowResultType.CREATE_ENTRY
        entry = result["result"]
        await hass.async_block_till_done()
        assert entry.state.value == "loaded"

        # Legacy placeholder URLs must survive both incremental updates and
        # a full setup without preventing primary/companion registration.
        legacy_options = copy.deepcopy(dict(entry.options))
        for metadata in legacy_options["device_attributes"].values():
            metadata["configuration_url"] = "-"
            metadata["via_device_id"] = "missing-parent"
        for records in legacy_options["devices"].values():
            records[0]["icon"] = {"bad": "icon"}
            records[0]["pull_interval"] = 2**63
            records.append({"platform": [], "name": "Broken domain"})
            records.append({
                "platform": "sensor", "name": "Malformed legacy metadata",
                "entity_id": "sensor.docker_bad_metadata", "initial_value": "5",
                "attributes": {"device_class": ["pm25"], "unit_of_measurement": ["μg/m³"], "state_class": {}},
            })
        hass.config_entries.async_update_entry(entry, options=legacy_options)
        await hass.async_block_till_done()
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state.value == "loaded"

        created_state = hass.states.get("sensor.docker_flow_pm25")
        assert created_state is not None
        assert float(created_state.state) == 15.0
        assert created_state.attributes["device_class"] == "pm25"
        assert created_state.attributes["state_class"] == "measurement"
        assert created_state.attributes["unit_of_measurement"] == "μg/m³"
        assert hass.states.get("sensor.docker_bad_metadata").state == "5"
        assert hass.states.get("air_quality.docker_bad_metadata_aqi") is None
        aqi_state = hass.states.get("air_quality.docker_flow_pm25_aqi")
        assert aqi_state.state == "fair", aqi_state
        assert "device_class" not in aqi_state.attributes
        assert "unit_of_measurement" not in aqi_state.attributes

        # Saving unrelated entity changes must preserve the running source
        # template and its state, including when a new platform is introduced.
        original_entity = hass.data["sensor"].get_entity("sensor.docker_flow_pm25")
        saved_options = copy.deepcopy(dict(entry.options))
        device_name = next(iter(saved_options["devices"]))
        added = {"platform": "switch", "name": "Incremental Switch",
                 "entity_id": "switch.incremental", "entity_key": "incremental",
                 "initial_value": "off", "persistent": False}
        for records in ([added], [{**added, "initial_value": "on"}], []):
            options = copy.deepcopy(saved_options)
            options["devices"][device_name].extend(records)
            hass.config_entries.async_update_entry(entry, options=options)
            await hass.async_block_till_done()
            assert hass.data["sensor"].get_entity("sensor.docker_flow_pm25") is original_entity
            assert hass.states.get("sensor.docker_flow_pm25") is created_state
            if records:
                assert hass.states.get("switch.incremental").state == records[0]["initial_value"]
            else:
                assert hass.states.get("switch.incremental") is None

        registry = er.async_get(hass)
        created_registry_entry = registry.async_get("sensor.docker_flow_pm25")
        usage_entries = [
            item for item in er.async_entries_for_config_entry(registry, entry.entry_id)
            if "source_usage:" in item.unique_id
        ]
        assert len(usage_entries) == 2
        for usage in usage_entries:
            usage_state = hass.states.get(usage.entity_id)
            assert usage_state.state == hass.states.get("sensor.docker_flow_pm25").state
            assert usage_state.attributes["virtual_entity_id"] == "sensor.docker_flow_pm25"
            assert usage_state.attributes["source_entity_id"] in source_ids
            assert usage_state.attributes["virtual_entities"] == ["sensor.docker_flow_pm25"]
            assert usage.device_id is None
        # Recreate a missing reverse link from saved options during reload.
        missing_usage = usage_entries[0]
        registry.async_remove(missing_usage.entity_id)
        await hass.async_block_till_done()
        assert hass.states.get(missing_usage.entity_id) is None
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        repaired_usage = registry.async_get(missing_usage.entity_id)
        assert repaired_usage.unique_id == missing_usage.unique_id
        assert hass.states.get(repaired_usage.entity_id).attributes["virtual_entities"] == ["sensor.docker_flow_pm25"]
        assert len([
            item for item in er.async_entries_for_config_entry(registry, entry.entry_id)
            if "source_usage:" in item.unique_id
        ]) == 2
        assert created_registry_entry is not None
        assert created_registry_entry.device_id is not None
        original_unique_id = created_registry_entry.unique_id
        device_id = created_registry_entry.device_id
        aqi_unique_id = registry.async_get("air_quality.docker_flow_pm25_aqi").unique_id
        for suffix in ("info", "debug1", "debug2", "aqi"):
            companion_id = f"sensor.docker_flow_pm25_{suffix}"
            if suffix == "aqi":
                companion_id = companion_id.replace("sensor.", "air_quality.", 1)
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
        entity_key = suggested_form_values(result["data_schema"])[CONF_ENTITY_KEY]
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
        edit_defaults = _flatten_entity_form_sections(suggested_form_values(result["data_schema"]))
        edit_defaults[CONF_ENTITY_NAME] = "Docker PM2.5 Maximum"
        edit_defaults["device_configuration_url"] = ""
        edit_defaults["device_via_device_id"] = ""
        edit_defaults["icon"] = "mdi:air-filter"
        edit_defaults["pull_interval"] = 0
        # The frontend omits an optional text field when the user clears it.
        edit_defaults.pop("entity_id", None)
        edit_defaults = result["data_schema"](edit_defaults)
        assert edit_defaults["entity_id"] == ""

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
        modified_id = "sensor.docker_pm2_5_maximum"
        modified_state = hass.states.get(modified_id)
        assert modified_state is not None
        for usage in usage_entries:
            assert hass.states.get(usage.entity_id).attributes["virtual_entities"] == [modified_id]
            assert hass.states.get(usage.entity_id).attributes["friendly_name"].endswith(f"({modified_id})")
        assert float(modified_state.state) == 20.0
        assert modified_state.attributes["unit_of_measurement"] == "μg/m³"
        modified_registry_entry = registry.async_get(modified_id)
        assert modified_registry_entry is not None
        assert modified_registry_entry.unique_id == original_unique_id
        assert modified_registry_entry.device_id == device_id
        assert registry.async_get("sensor.docker_flow_pm25") is None
        assert registry.async_get(f"{modified_id.replace('sensor.', 'air_quality.', 1)}_aqi").unique_id == aqi_unique_id
        for suffix in ("info", "debug1", "debug2", "aqi"):
            old_domain = "air_quality" if suffix == "aqi" else "sensor"
            assert registry.async_get(f"{old_domain}.docker_flow_pm25_{suffix}") is None
            companion_id = f"{modified_id}_{suffix}"
            if suffix == "aqi":
                companion_id = companion_id.replace("sensor.", "air_quality.", 1)
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
        hass.states.async_set(source_ids[1], "0.06", {"device_class": "pm25", "unit_of_measurement": "mg/m³"})
        await hass.async_block_till_done()
        await asyncio.sleep(0.1)
        await hass.async_block_till_done()
        assert float(hass.states.get(modified_id).state) == 60.0
        for usage in usage_entries:
            assert float(hass.states.get(usage.entity_id).state) == 60.0
        assert hass.states.get(f"{modified_id.replace('sensor.', 'air_quality.', 1)}_aqi").state == "poor"

        for source_id in source_ids:
            hass.states.async_set(
                source_id,
                "unavailable",
                {"device_class": "pm25", "unit_of_measurement": "μg/m³"},
            )
        await hass.async_block_till_done()
        assert hass.states.get(modified_id).state == "unavailable"
        await asyncio.sleep(0.1)
        await hass.async_block_till_done()
        assert hass.states.get(f"{modified_id.replace('sensor.', 'air_quality.', 1)}_aqi").state == "poor"
        assert hass.states.get(f"{modified_id.replace('sensor.', 'air_quality.', 1)}_aqi").attributes["air_quality_stale"] is True

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
        await asyncio.sleep(0.1)
        await hass.async_block_till_done()
        assert hass.states.get(f"{modified_id.replace('sensor.', 'air_quality.', 1)}_aqi").state == "fair"
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert registry.async_get(f"{modified_id.replace('sensor.', 'air_quality.', 1)}_aqi").unique_id == aqi_unique_id
        assert hass.states.get(f"{modified_id.replace('sensor.', 'air_quality.', 1)}_aqi_aqi") is None
        # Exercise the new source-specific UI using the real HA FlowManager.
        result = await options_flow.async_init(entry.entry_id, data={CONF_ACTION: ACTION_EDIT_ENTITY})
        result = await configure_flow(options_flow, result, {CONF_ENTITY_KEY: entity_key})
        result = await configure_flow(options_flow, result, {CONF_REFERENCE_ENTITY_ID: source_ids})
        result = await configure_flow(options_flow, result, {
            CONF_SENSOR_CONVERSION: "state", CONF_SENSOR_AGGREGATION: "maximum"})
        result = await configure_flow(options_flow, result, {CONF_HELPER_UPDATE_MODE: "keep_current"})
        assert result["step_id"] == "edit_entity"
        values = _flatten_entity_form_sections(suggested_form_values(result["data_schema"]))
        values["configure_air_quality_sources"] = True
        result = await configure_flow(options_flow, result, values)
        assert result["step_id"] == "air_quality_scope"
        result = await configure_flow(options_flow, result, {
            "scope": "leaves", "sources": source_ids, "missing": "skip"})
        assert result["step_id"] == "air_quality_source_rules"
        result = await configure_flow(options_flow, result, {"source": source_ids[0], "action": "edit"})
        assert result["step_id"] == "edit_air_quality_setup"
        values = suggested_form_values(result["data_schema"])
        values.update({f"boundary_{index+1}": value for index, value in enumerate([100, 200, 300, 400, 500])})
        result = await configure_flow(options_flow, result, values)
        assert result["step_id"] == "air_quality_source_rules"
        result = await configure_flow(options_flow, result, {"source": source_ids[0], "action": "continue"})
        assert result["step_id"] == "edit_entity"
        values = _flatten_entity_form_sections(suggested_form_values(result["data_schema"]))
        result = await configure_flow(options_flow, result, values)
        assert result["type"] == FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()
        assert float(hass.states.get(modified_id).state) == 25.0
        assert hass.states.get(f"{modified_id.replace('sensor.', 'air_quality.', 1)}_aqi").state == "good"
        assert hass.states.get(f"{modified_id.replace('sensor.', 'air_quality.', 1)}_aqi").attributes["air_quality_logic"]["source_roots"] == source_ids
        assert registry.async_get(f"{modified_id.replace('sensor.', 'air_quality.', 1)}_aqi").unique_id == aqi_unique_id
        reloaded = await hass.config_entries.async_reload(entry.entry_id)
        assert reloaded, (entry.state, entry.reason)
        await hass.async_block_till_done()
        assert hass.states.get(f"{modified_id.replace('sensor.', 'air_quality.', 1)}_aqi").state == "good"
        # Regression for the screenshot: a valid 0.003 mg/m3 measurement
        # must acquire a category on load, without rewriting the source unit.
        options = copy.deepcopy(dict(entry.options))
        next(iter(options["devices"].values())).append({
            "platform": "sensor", "entity_id": "sensor.docker_formaldehyde",
            "name": "Docker Formaldehyde", "class": "formaldehyde",
            "unit_of_measurement": "mg/m3", "initial_value": "0.003",
            "persistent": False,
        })
        for quantity in ("pm4", "nitrous_oxide"):
            next(iter(options["devices"].values())).append({
                "platform": "sensor", "entity_id": f"sensor.docker_{quantity}",
                "name": quantity, "class": quantity,
                "unit_of_measurement": "μg/m³", "initial_value": "3",
                "persistent": False,
            })
        hass.states.async_set("sensor.docker_co2_input", "1093")
        next(iter(options["devices"].values())).append({
            "platform": "sensor", "entity_id": "sensor.docker_carbon_dioxide",
            "name": "Docker Carbon Dioxide", "source_entities": ["sensor.docker_co2_input"],
            "value_template": "{{ states('sensor.docker_co2_input') }}",
            "native_templates": {
                "device_class": "{{ state_attr('sensor.docker_co2_input', 'device_class') }}",
                "unit_of_measurement": "{{ state_attr('sensor.docker_co2_input', 'unit_of_measurement') }}",
            },
        })
        hass.config_entries.async_update_entry(entry, options=options)
        await hass.async_block_till_done()
        assert hass.states.get("air_quality.docker_carbon_dioxide_aqi").state == "unknown"
        hass.states.async_set("sensor.docker_co2_input", "1093", {
            "device_class": "carbon_dioxide", "unit_of_measurement": "ppm",
        })
        await hass.async_block_till_done()
        await asyncio.sleep(0.1)
        await hass.async_block_till_done()
        assert hass.states.get("air_quality.docker_carbon_dioxide_aqi").state == "moderate"
        # Missing readings retain the last grade, visibly marked stale, even
        # across a reload. A new valid reading clears the stale marker.
        hass.states.async_set("sensor.docker_co2_input", "unavailable")
        await hass.async_block_till_done()
        await asyncio.sleep(0.1)
        await hass.async_block_till_done()
        assert hass.states.get("air_quality.docker_carbon_dioxide_aqi").state == "moderate"
        assert hass.states.get("air_quality.docker_carbon_dioxide_aqi").attributes["air_quality_stale"] is True
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert hass.states.get("air_quality.docker_carbon_dioxide_aqi").state == "moderate"
        hass.states.async_set("sensor.docker_co2_input", "500", {
            "device_class": "carbon_dioxide", "unit_of_measurement": "ppm",
        })
        await hass.async_block_till_done()
        await asyncio.sleep(0.1)
        await hass.async_block_till_done()
        assert hass.states.get("air_quality.docker_carbon_dioxide_aqi").state == "good"
        assert hass.states.get("air_quality.docker_carbon_dioxide_aqi").attributes["air_quality_stale"] is False
        assert hass.states.get("air_quality.docker_carbon_dioxide_aqi").attributes["air_quality_logic"]["measurements"]
        for quantity in ("pm4", "nitrous_oxide"):
            assert hass.states.get(f"air_quality.docker_{quantity}_aqi").state == "good"
        assert hass.states.get("sensor.docker_formaldehyde").state == "0.003"
        assert hass.states.get("sensor.docker_formaldehyde").attributes["unit_of_measurement"] == "mg/m3"
        assert hass.states.get("air_quality.docker_formaldehyde_aqi").state == "good"
        assert hass.states.get("sensor.docker_formaldehyde_aqim").state == "good"
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert hass.states.get("air_quality.docker_formaldehyde_aqi").state == "good"
        for quantity in ("pm4", "nitrous_oxide"):
            assert hass.states.get(f"sensor.docker_{quantity}").state == "3"
            assert hass.states.get(f"air_quality.docker_{quantity}_aqi").state == "good"
        assert hass.states.get("air_quality.docker_carbon_dioxide_aqi").state == "good"
        hass.states.async_set("sensor.docker_co_raw", "6", {
            "device_class": "carbon_monoxide", "unit_of_measurement": "ppm",
        })
        options = copy.deepcopy(dict(entry.options))
        next(iter(options["devices"].values())).append({
            "platform": "sensor", "entity_id": "sensor.docker_carbon_monoxide",
            "name": "Docker Carbon Monoxide", "class": "carbon_monoxide",
            "initial_value": "6", "source_entities": ["sensor.docker_co_raw"],
            "native_templates": {"unit_of_measurement": "{{ none }}"},
        })
        next(iter(options["devices"].values())).append({
            "platform": "sensor", "entity_id": "sensor.docker_co_detector_2_co",
            "name": "CO_DETECTOR 2 CO", "initial_value": 0,
            "attributes": {"unit_of_measurement": "ppm"},
        })
        hass.states.async_set("binary_sensor.docker_co_alarm_input", "off")
        for source, value in [("sensor.docker_composite_co2_a", "1111"), ("sensor.docker_composite_co2_b", "974")]:
            hass.states.async_set(source, value, {"device_class": "carbon_dioxide", "unit_of_measurement": "ppm"})
        next(iter(options["devices"].values())).append({
            "platform": "sensor", "entity_id": "sensor.docker_composite_co2",
            "name": "Composite CO2", "class": "carbon_dioxide", "initial_value": "1042.5",
            "source_entities": ["sensor.docker_composite_co2_a", "sensor.docker_composite_co2_b"],
            "native_templates": {"unit_of_measurement": "{{ none }}"},
        })
        next(iter(options["devices"].values())).append({
            "platform": "binary_sensor", "entity_id": "binary_sensor.docker_co_alarm",
            "name": "Docker CO Alarm", "class": "carbon_monoxide",
            "source_entities": ["binary_sensor.docker_co_alarm_input"],
            "value_template": "{{ states('binary_sensor.docker_co_alarm_input') }}",
        })
        hass.config_entries.async_update_entry(entry, options=options)
        await hass.async_block_till_done()
        assert hass.states.get("air_quality.docker_carbon_monoxide_aqi").state == "fair"
        assert hass.states.get("air_quality.docker_carbon_monoxide_aqi").attributes["air_quality_evaluation_basis"] == "combined_inherited_unit"
        assert hass.states.get("air_quality.docker_composite_co2_aqi").state == "moderate"
        assert "unit_of_measurement" not in hass.states.get("sensor.docker_carbon_monoxide").attributes
        assert hass.states.get("air_quality.docker_co_detector_2_co_aqi").state == "good"
        assert hass.states.get("sensor.docker_co_detector_2_co").attributes["unit_of_measurement"] == "ppm"
        assert hass.states.get("sensor.docker_co_detector_2_co").attributes.get("device_class") is None
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert hass.states.get("air_quality.docker_carbon_monoxide_aqi").state == "fair"
        assert hass.states.get("air_quality.docker_co_detector_2_co_aqi").state == "good"
        assert float(hass.states.get("sensor.docker_co_detector_2_co").state) == 0
        assert hass.states.get("air_quality.docker_composite_co2_aqi").state == "moderate"
        assert hass.states.get("air_quality.docker_co_alarm_aqi").state == "good"
        assert hass.states.get("sensor.docker_co_alarm_aqim").state == "good"
        for value, expected in [("on", "poor"), ("unavailable", "poor"), ("off", "good")]:
            hass.states.async_set("binary_sensor.docker_co_alarm_input", value)
            await hass.async_block_till_done()
            await asyncio.sleep(0.1)
            await hass.async_block_till_done()
            assert hass.states.get("air_quality.docker_co_alarm_aqi").state == expected
            assert hass.states.get("air_quality.docker_co_alarm_aqi").attributes["air_quality_stale"] is (value == "unavailable")
    finally:
        await hass.async_stop()


asyncio.run(test_config_flow_create_modify_runtime())
print(
    "Virtual Layer Docker compatibility smoke passed "
    f"on Home Assistant {HA_VERSION}"
)
PY
