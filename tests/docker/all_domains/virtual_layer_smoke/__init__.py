"""Run an all-domain Virtual Layer smoke test inside Home Assistant Container."""

from __future__ import annotations

import asyncio
import json
import logging
import traceback
from pathlib import Path

import homeassistant.helpers.device_registry as dr
import homeassistant.helpers.entity_registry as er
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, STATE_UNAVAILABLE
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResultType

from custom_components.virtual_layer.const import (
    ATTR_DEVICE_ATTRIBUTES,
    ATTR_DEVICE_ID,
    ATTR_DEVICES,
    ATTR_GROUP_NAME,
    COMPONENT_DOMAIN,
    CONF_INITIAL_AVAILABILITY,
    CONF_INITIAL_VALUE,
    CONF_NAME,
    CONF_PERSISTENT,
    STATE_ONLY_ENTITY_DOMAINS,
    VIRTUAL_ENTITY_DOMAINS,
)

DOMAIN = "virtual_layer_smoke"
GROUP_NAME = "docker_all_domains"
DEVICE_NAME = "Docker All Domains"
RESULT_FILE = "all-domains-result.json"


class _VirtualLayerErrorHandler(logging.Handler):
    """Collect Virtual Layer errors without changing normal HA logging."""

    def __init__(self) -> None:
        super().__init__(logging.ERROR)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith("custom_components.virtual_layer"):
            self.messages.append(self.format(record))


class _VirtualLayerDeprecationHandler(logging.Handler):
    """Collect HA deprecation warnings caused by Virtual Layer entities."""

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        message = self.format(record)
        if "virtual_layer" in message and "deprecated" in message.lower():
            self.messages.append(message)


def _entity_config(domain: str) -> dict:
    """Return valid, feature-rich UI options for a supported domain."""
    initial_values = {
        "binary_sensor": "off",
        "camera": "on",
        "climate": "off",
        "cover": "closed",
        "date": "2026-08-08",
        "datetime": "2026-08-08T12:34:56+09:00",
        "device_tracker": "not_home",
        "fan": "off",
        "humidifier": "off",
        "lawn_mower": "docked",
        "light": "off",
        "lock": "locked",
        "media_player": "idle",
        "number": "0",
        "remote": "off",
        "select": "eco",
        "siren": "off",
        "switch": "off",
        "text": "hello",
        "time": "12:34:56",
        "update": "1.0.0",
        "vacuum": "docked",
        "valve": "closed",
        "water_heater": "off",
    }
    entity = {
        "platform": domain,
        CONF_NAME: f"Docker {domain}",
        "entity_id": f"{domain}.docker_{domain}",
        CONF_INITIAL_VALUE: initial_values.get(
            domain,
            "docker_smoke" if domain in STATE_ONLY_ENTITY_DOMAINS else "unknown",
        ),
        CONF_INITIAL_AVAILABILITY: True,
        CONF_PERSISTENT: True,
    }
    domain_options = {
        "climate": {
            "hvac_modes": ["off", "heat", "cool"],
            "fan_modes": ["auto", "turbo"],
            "fan_mode": "auto",
            "preset_modes": ["none", "eco"],
            "preset_mode": "none",
            "swing_modes": ["off", "vertical"],
            "swing_mode": "off",
            "swing_horizontal_modes": ["left", "right"],
            "swing_horizontal_mode": "left",
            "target_temperature": 22,
            "target_humidity": 45,
        },
        "cover": {"open_close_duration": 0},
        "fan": {
            "speed_count": 3,
            "oscillate": True,
            "direction": True,
            "modes": ["eco", "boost"],
        },
        "humidifier": {
            "min_humidity": 30,
            "max_humidity": 70,
            "target_humidity": 50,
            "modes": ["normal", "eco"],
            "mode": "normal",
        },
        "light": {
            "support_brightness": True,
            "support_color": True,
            "initial_color": [0, 100],
            "support_color_temp": True,
            "initial_color_temp": 4000,
            "support_effect": True,
            "initial_effect_list": ["none", "rainbow"],
            "initial_effect": "none",
        },
        "lock": {"support_open": True},
        "media_player": {
            "source_list": ["TV", "Radio"],
            "source": "TV",
        },
        "number": {"min": 0, "max": 100, "step": 0.5, "mode": "slider"},
        "remote": {"activity_list": ["TV", "Music"]},
        "select": {"options": ["eco", "boost"]},
        "siren": {"available_tones": ["alarm"]},
        "text": {"min": 1, "max": 32},
        "update": {
            "installed_version": "1.0.0",
            "latest_version": "1.1.0",
            "versions": ["1.0.0", "1.1.0"],
        },
        "vacuum": {
            "battery_level": 80,
            "fan_speed": "normal",
            "fan_speed_list": ["normal", "turbo"],
        },
        "valve": {"open_close_duration": 0},
        "water_heater": {
            "operation_list": ["off", "eco", "heat"],
            "target_temperature": 50,
        },
    }
    entity.update(domain_options.get(domain, {}))
    return entity


def _variant_config(
    domain: str,
    slug: str,
    initial_value: str,
    **options,
) -> dict:
    """Return a persistent entity used by the real-HA feature matrix."""
    entity = {
        "platform": domain,
        CONF_NAME: f"Docker {slug.replace('_', ' ').title()}",
        "entity_id": f"{domain}.docker_{slug}",
        CONF_INITIAL_VALUE: initial_value,
        CONF_INITIAL_AVAILABILITY: True,
        CONF_PERSISTENT: True,
    }
    entity.update(options)
    return entity


def _variant_entity_configs() -> list[dict]:
    """Cover important device classes and appliance-shaped configurations."""
    return [
        *[
            _variant_config(
                "binary_sensor",
                slug,
                "off",
                **{"class": device_class},
            )
            for slug, device_class in (
                ("motion", "motion"),
                ("presence", "presence"),
                ("leak", "moisture"),
                ("smoke", "smoke"),
                ("gas_alarm", "gas"),
            )
        ],
        *[
            _variant_config(
                "sensor",
                slug,
                "1",
                **{
                    "class": device_class,
                    "state_class": state_class,
                },
            )
            for slug, device_class, state_class in (
                ("power", "power", "measurement"),
                ("energy", "energy", "total_increasing"),
                ("current", "current", "measurement"),
                ("voltage", "voltage", "measurement"),
                ("gas_usage", "gas", "total_increasing"),
                ("water_usage", "water", "total_increasing"),
            )
        ],
        _variant_config(
            "sensor",
            "washer",
            "idle",
            appliance_type="washer",
            program="cotton",
            remaining_time=1800,
            door_locked=True,
        ),
        _variant_config(
            "sensor",
            "dryer",
            "idle",
            appliance_type="dryer",
            program="normal",
            remaining_time=2400,
            door_locked=True,
        ),
        _variant_config(
            "number",
            "power_limit",
            "1",
            **{
                "class": "power",
                "min": 0,
                "max": 10,
                "step": 0.1,
                "mode": "box",
            },
        ),
        _variant_config(
            "number",
            "temperature_setpoint",
            "20",
            **{
                "class": "temperature",
                "min": -20,
                "max": 50,
                "step": 0.5,
                "mode": "box",
            },
        ),
        _variant_config(
            "number",
            "frequency_limit",
            "50",
            **{
                "class": "frequency",
                "min": 0,
                "max": 100,
                "step": 1,
                "mode": "slider",
            },
        ),
        _variant_config(
            "humidifier",
            "dehumidifier",
            "off",
            **{
                "class": "dehumidifier",
                "current_humidity": 65,
                "target_humidity": 50,
                "modes": ["auto", "sleep"],
                "mode": "auto",
            },
        ),
        _variant_config(
            "climate",
            "climate_range",
            "heat_cool",
            hvac_modes=["off", "heat_cool"],
            min_temp=10,
            max_temp=32,
            target_temperature_low=17,
            target_temperature_high=26,
        ),
        _variant_config(
            "cover",
            "garage_cover",
            "closed",
            **{"class": "garage", "open_close_duration": 0},
        ),
        _variant_config(
            "valve",
            "water_valve",
            "closed",
            **{"class": "water", "open_close_duration": 0},
        ),
    ]


async def _async_call(
    hass: HomeAssistant,
    service_errors: list[str],
    domain: str,
    service: str,
    data: dict,
) -> None:
    try:
        await hass.services.async_call(domain, service, data, blocking=True)
    except Exception as err:  # noqa: BLE001 - report every HA service failure
        service_errors.append(f"{domain}.{service}: {type(err).__name__}: {err}")


async def _async_test_services(hass: HomeAssistant) -> list[str]:
    """Exercise native commands for every domain with a command surface."""
    errors: list[str] = []
    calls = [
        (
            COMPONENT_DOMAIN,
            "turn_on",
            {"entity_id": "binary_sensor.docker_binary_sensor"},
        ),
        (
            COMPONENT_DOMAIN,
            "set",
            {"entity_id": "sensor.docker_sensor", "value": "123.5"},
        ),
        (
            COMPONENT_DOMAIN,
            "move",
            {
                "entity_id": "device_tracker.docker_device_tracker",
                "gps": {"latitude": 37.5, "longitude": 127.0},
                "gps_accuracy": 8,
            },
        ),
        ("button", "press", {"entity_id": "button.docker_button"}),
        ("camera", "turn_off", {"entity_id": "camera.docker_camera"}),
        (
            "climate",
            "set_hvac_mode",
            {"entity_id": "climate.docker_climate", "hvac_mode": "heat"},
        ),
        (
            "climate",
            "set_fan_mode",
            {"entity_id": "climate.docker_climate", "fan_mode": "turbo"},
        ),
        (
            "climate",
            "set_preset_mode",
            {"entity_id": "climate.docker_climate", "preset_mode": "eco"},
        ),
        (
            "climate",
            "set_swing_mode",
            {"entity_id": "climate.docker_climate", "swing_mode": "vertical"},
        ),
        (
            "climate",
            "set_swing_horizontal_mode",
            {
                "entity_id": "climate.docker_climate",
                "swing_horizontal_mode": "right",
            },
        ),
        (
            "climate",
            "set_temperature",
            {"entity_id": "climate.docker_climate", "temperature": 24},
        ),
        (
            "climate",
            "set_humidity",
            {"entity_id": "climate.docker_climate", "humidity": 48},
        ),
        (
            "cover",
            "set_cover_position",
            {"entity_id": "cover.docker_cover", "position": 35},
        ),
        (
            "date",
            "set_value",
            {"entity_id": "date.docker_date", "date": "2026-08-09"},
        ),
        (
            "datetime",
            "set_value",
            {
                "entity_id": "datetime.docker_datetime",
                "datetime": "2026-08-09T01:02:03+09:00",
            },
        ),
        (
            "fan",
            "set_percentage",
            {"entity_id": "fan.docker_fan", "percentage": 67},
        ),
        (
            "fan",
            "set_direction",
            {"entity_id": "fan.docker_fan", "direction": "reverse"},
        ),
        (
            "fan",
            "oscillate",
            {"entity_id": "fan.docker_fan", "oscillating": True},
        ),
        (
            "fan",
            "set_preset_mode",
            {"entity_id": "fan.docker_fan", "preset_mode": "boost"},
        ),
        (
            "humidifier",
            "turn_on",
            {"entity_id": "humidifier.docker_humidifier"},
        ),
        (
            "humidifier",
            "set_humidity",
            {"entity_id": "humidifier.docker_humidifier", "humidity": 55},
        ),
        (
            "humidifier",
            "set_mode",
            {"entity_id": "humidifier.docker_humidifier", "mode": "eco"},
        ),
        (
            "lawn_mower",
            "start_mowing",
            {"entity_id": "lawn_mower.docker_lawn_mower"},
        ),
        (
            "light",
            "turn_on",
            {
                "entity_id": "light.docker_light",
                "brightness": 128,
                "effect": "rainbow",
            },
        ),
        ("lock", "open", {"entity_id": "lock.docker_lock"}),
        (
            "media_player",
            "media_play",
            {"entity_id": "media_player.docker_media_player"},
        ),
        (
            "media_player",
            "volume_set",
            {"entity_id": "media_player.docker_media_player", "volume_level": 0.7},
        ),
        (
            "media_player",
            "volume_mute",
            {"entity_id": "media_player.docker_media_player", "is_volume_muted": True},
        ),
        (
            "media_player",
            "select_source",
            {"entity_id": "media_player.docker_media_player", "source": "Radio"},
        ),
        (
            "number",
            "set_value",
            {"entity_id": "number.docker_number", "value": 42},
        ),
        (
            "remote",
            "turn_on",
            {"entity_id": "remote.docker_remote", "activity": "Music"},
        ),
        (
            "remote",
            "send_command",
            {"entity_id": "remote.docker_remote", "command": ["POWER"]},
        ),
        (
            "select",
            "select_option",
            {"entity_id": "select.docker_select", "option": "boost"},
        ),
        (
            "siren",
            "turn_on",
            {
                "entity_id": "siren.docker_siren",
                "tone": "alarm",
                "volume_level": 0.6,
                "duration": 10,
            },
        ),
        ("switch", "turn_on", {"entity_id": "switch.docker_switch"}),
        (
            "text",
            "set_value",
            {"entity_id": "text.docker_text", "value": "updated"},
        ),
        (
            "time",
            "set_value",
            {"entity_id": "time.docker_time", "time": "01:02:03"},
        ),
        (
            "update",
            "install",
            {
                "entity_id": "update.docker_update",
                "version": "1.1.0",
                "backup": False,
            },
        ),
        ("vacuum", "start", {"entity_id": "vacuum.docker_vacuum"}),
        (
            "vacuum",
            "set_fan_speed",
            {"entity_id": "vacuum.docker_vacuum", "fan_speed": "turbo"},
        ),
        ("vacuum", "pause", {"entity_id": "vacuum.docker_vacuum"}),
        (
            "valve",
            "set_valve_position",
            {"entity_id": "valve.docker_valve", "position": 35},
        ),
        (
            "water_heater",
            "set_temperature",
            {"entity_id": "water_heater.docker_water_heater", "temperature": 55},
        ),
        (
            "water_heater",
            "set_operation_mode",
            {"entity_id": "water_heater.docker_water_heater", "operation_mode": "heat"},
        ),
    ]
    for domain, service, data in calls:
        await _async_call(hass, errors, domain, service, data)

    domains_with_dedicated_commands = {
        "binary_sensor",
        "button",
        "camera",
        "climate",
        "cover",
        "date",
        "datetime",
        "device_tracker",
        "fan",
        "humidifier",
        "image",
        "lawn_mower",
        "light",
        "lock",
        "media_player",
        "number",
        "remote",
        "select",
        "sensor",
        "siren",
        "switch",
        "text",
        "time",
        "update",
        "vacuum",
        "valve",
        "water_heater",
    }
    for domain in set(VIRTUAL_ENTITY_DOMAINS) - domains_with_dedicated_commands:
        await _async_call(
            hass,
            errors,
            COMPONENT_DOMAIN,
            "set_state",
            {
                "entity_id": f"{domain}.docker_{domain}",
                "value": "docker_smoke",
            },
        )
    return errors


def _state_contract_errors(hass: HomeAssistant) -> list[str]:
    """Check that successful service calls actually changed native state."""
    expected = {
        "binary_sensor": ("on", {}),
        "camera": ("idle", {}),
        "climate": ("heat", {
            "temperature": 24.0,
            "humidity": 48,
            "fan_mode": "turbo",
            "preset_mode": "eco",
            "swing_mode": "vertical",
            "swing_horizontal_mode": "right",
        }),
        "cover": ("open", {"current_position": 35}),
        "date": ("2026-08-09", {}),
        "device_tracker": ("not_home", {
            "latitude": 37.5,
            "longitude": 127.0,
            "gps_accuracy": 8,
            "source_type": "gps",
        }),
        "fan": ("on", {
            "direction": "reverse",
            "oscillating": True,
            "preset_mode": "boost",
            "supported_features": 63,
        }),
        "humidifier": ("on", {"humidity": 55, "mode": "eco"}),
        "lawn_mower": ("mowing", {}),
        "light": ("on", {"brightness": 128, "effect": "rainbow"}),
        "lock": ("open", {}),
        "media_player": ("playing", {
            "volume_level": 0.7,
            "is_volume_muted": True,
            "source": "Radio",
        }),
        "number": ("42.0", {"step": 0.5, "mode": "slider"}),
        "remote": ("on", {"current_activity": "Music", "last_command": ["POWER"]}),
        "select": ("boost", {"options": ["eco", "boost"]}),
        "sensor": ("123.5", {}),
        "siren": ("on", {"tone": "alarm", "volume_level": 0.6, "duration": 10}),
        "switch": ("on", {}),
        "text": ("updated", {}),
        "time": ("01:02:03", {}),
        "update": ("off", {
            "installed_version": "1.1.0",
            "latest_version": "1.1.0",
        }),
        "vacuum": ("paused", {"fan_speed": "turbo", "battery_level": 80}),
        "valve": ("open", {"current_position": 35}),
        "water_heater": ("heat", {"temperature": 55.0}),
    }
    errors = []
    for domain, (expected_state, expected_attributes) in expected.items():
        entity_id = f"{domain}.docker_{domain}"
        state = hass.states.get(entity_id)
        if state is None:
            errors.append(f"{entity_id}: state is missing")
            continue
        if state.state != expected_state:
            errors.append(
                f"{entity_id}: expected state {expected_state!r}, got {state.state!r}"
            )
        for name, expected_value in expected_attributes.items():
            actual_value = state.attributes.get(name)
            if actual_value != expected_value:
                errors.append(
                    f"{entity_id}.{name}: expected {expected_value!r}, "
                    f"got {actual_value!r}"
                )

    for domain in set(VIRTUAL_ENTITY_DOMAINS) - set(expected) - {
        "button",
        "datetime",
        "image",
    }:
        state = hass.states.get(f"{domain}.docker_{domain}")
        if state is None or state.state != "docker_smoke":
            errors.append(
                f"{domain}.docker_{domain}: generic state update was not applied"
            )

    button = hass.states.get("button.docker_button")
    if button is None or button.attributes.get("press_count") != 1:
        errors.append("button.docker_button: press_count was not updated")
    datetime_state = hass.states.get("datetime.docker_datetime")
    if datetime_state is None or datetime_state.state not in {
        "2026-08-09T01:02:03+09:00",
        "2026-08-08T16:02:03+00:00",
    }:
        errors.append(
            "datetime.docker_datetime: expected the configured instant, got "
            f"{None if datetime_state is None else datetime_state.state!r}"
        )
    camera_component = hass.data.get("camera")
    camera_entity = (
        camera_component.get_entity("camera.docker_camera")
        if camera_component is not None
        else None
    )
    if camera_entity is None or camera_entity.is_on:
        errors.append("camera.docker_camera: turn_off did not update is_on")
    return errors


async def _async_test_variant_services(hass: HomeAssistant) -> list[str]:
    """Exercise appliance and device-class variants in the live container."""
    errors: list[str] = []
    await _async_call(
        hass,
        errors,
        COMPONENT_DOMAIN,
        "turn_on",
        {
            "entity_id": [
                "binary_sensor.docker_motion",
                "binary_sensor.docker_presence",
                "binary_sensor.docker_leak",
                "binary_sensor.docker_smoke",
                "binary_sensor.docker_gas_alarm",
            ],
        },
    )
    await _async_call(
        hass,
        errors,
        COMPONENT_DOMAIN,
        "set",
        {
            "entity_id": [
                "sensor.docker_power",
                "sensor.docker_energy",
                "sensor.docker_current",
                "sensor.docker_voltage",
                "sensor.docker_gas_usage",
                "sensor.docker_water_usage",
            ],
            "value": "42",
        },
    )
    for entity_id, value in (
        ("sensor.docker_washer", "running"),
        ("sensor.docker_dryer", "drying"),
    ):
        await _async_call(
            hass,
            errors,
            COMPONENT_DOMAIN,
            "set",
            {"entity_id": entity_id, "value": value},
        )
    for domain, service, data in (
        (
            "number",
            "set_value",
            {"entity_id": "number.docker_power_limit", "value": 7.5},
        ),
        (
            "number",
            "set_value",
            {"entity_id": "number.docker_temperature_setpoint", "value": 21.5},
        ),
        (
            "number",
            "set_value",
            {"entity_id": "number.docker_frequency_limit", "value": 60},
        ),
        (
            "humidifier",
            "turn_on",
            {"entity_id": "humidifier.docker_dehumidifier"},
        ),
        (
            "humidifier",
            "set_humidity",
            {"entity_id": "humidifier.docker_dehumidifier", "humidity": 40},
        ),
        (
            "humidifier",
            "set_mode",
            {"entity_id": "humidifier.docker_dehumidifier", "mode": "sleep"},
        ),
        (
            "climate",
            "set_hvac_mode",
            {
                "entity_id": "climate.docker_climate_range",
                "hvac_mode": "heat_cool",
            },
        ),
        (
            "climate",
            "set_temperature",
            {
                "entity_id": "climate.docker_climate_range",
                "target_temp_low": 18,
                "target_temp_high": 27,
            },
        ),
        (
            "cover",
            "open_cover",
            {"entity_id": "cover.docker_garage_cover"},
        ),
        (
            "valve",
            "open_valve",
            {"entity_id": "valve.docker_water_valve"},
        ),
    ):
        await _async_call(hass, errors, domain, service, data)
    return errors


def _variant_contract_errors(hass: HomeAssistant) -> list[str]:
    """Validate device classes, units, appliance data, and native values."""
    expected = {
        "binary_sensor.docker_motion": ("on", {"device_class": "motion"}),
        "binary_sensor.docker_presence": ("on", {"device_class": "presence"}),
        "binary_sensor.docker_leak": ("on", {"device_class": "moisture"}),
        "binary_sensor.docker_smoke": ("on", {"device_class": "smoke"}),
        "binary_sensor.docker_gas_alarm": ("on", {"device_class": "gas"}),
        "sensor.docker_power": (
            "42",
            {
                "device_class": "power",
                "state_class": "measurement",
                "unit_of_measurement": "kW",
            },
        ),
        "sensor.docker_energy": (
            "42",
            {
                "device_class": "energy",
                "state_class": "total_increasing",
                "unit_of_measurement": "kWh",
            },
        ),
        "sensor.docker_current": (
            "42",
            {
                "device_class": "current",
                "state_class": "measurement",
                "unit_of_measurement": "A",
            },
        ),
        "sensor.docker_voltage": (
            "42",
            {
                "device_class": "voltage",
                "state_class": "measurement",
                "unit_of_measurement": "V",
            },
        ),
        "sensor.docker_gas_usage": (
            "42",
            {
                "device_class": "gas",
                "state_class": "total_increasing",
                "unit_of_measurement": "m³",
            },
        ),
        "sensor.docker_water_usage": (
            "42",
            {
                "device_class": "water",
                "state_class": "total_increasing",
                "unit_of_measurement": "L",
            },
        ),
        "sensor.docker_washer": (
            "running",
            {
                "appliance_type": "washer",
                "program": "cotton",
                "remaining_time": 1800,
                "door_locked": True,
            },
        ),
        "sensor.docker_dryer": (
            "drying",
            {
                "appliance_type": "dryer",
                "program": "normal",
                "remaining_time": 2400,
                "door_locked": True,
            },
        ),
        "number.docker_power_limit": (
            "7.5",
            {
                "device_class": "power",
                "unit_of_measurement": "kW",
                "mode": "box",
                "step": 0.1,
            },
        ),
        "number.docker_temperature_setpoint": (
            "21.5",
            {
                "device_class": "temperature",
                "unit_of_measurement": "°C",
                "mode": "box",
                "step": 0.5,
            },
        ),
        "number.docker_frequency_limit": (
            "60.0",
            {
                "device_class": "frequency",
                "unit_of_measurement": "Hz",
                "mode": "slider",
                "step": 1.0,
            },
        ),
        "humidifier.docker_dehumidifier": (
            "on",
            {
                "device_class": "dehumidifier",
                "humidity": 40.0,
                "mode": "sleep",
            },
        ),
        "climate.docker_climate_range": (
            "heat_cool",
            {"target_temp_low": 18.0, "target_temp_high": 27.0},
        ),
        "cover.docker_garage_cover": (
            "open",
            {"device_class": "garage", "current_position": 100},
        ),
        "valve.docker_water_valve": (
            "open",
            {"device_class": "water", "current_position": 100},
        ),
    }
    errors: list[str] = []
    for entity_id, (expected_state, expected_attributes) in expected.items():
        state = hass.states.get(entity_id)
        if state is None:
            errors.append(f"{entity_id}: variant state is missing")
            continue
        if state.state != expected_state:
            errors.append(
                f"{entity_id}: expected state {expected_state!r}, "
                f"got {state.state!r}"
            )
        for name, expected_value in expected_attributes.items():
            actual_value = state.attributes.get(name)
            if actual_value != expected_value:
                errors.append(
                    f"{entity_id}.{name}: expected {expected_value!r}, "
                    f"got {actual_value!r}"
                )
    return errors


async def _async_test_feature_sequences(hass: HomeAssistant) -> list[str]:
    """Check intermediate state after each remaining native command."""
    errors: list[str] = []

    async def call_and_expect(
        domain: str,
        service: str,
        data: dict,
        expected_state: str | None = None,
        expected_attributes: dict | None = None,
    ) -> None:
        previous_error_count = len(errors)
        await _async_call(hass, errors, domain, service, data)
        if len(errors) != previous_error_count:
            return
        await asyncio.sleep(0)
        entity_id = data["entity_id"]
        state = hass.states.get(entity_id)
        if state is None:
            errors.append(f"{domain}.{service}: {entity_id} disappeared")
            return
        if expected_state is not None and state.state != expected_state:
            errors.append(
                f"{domain}.{service}: expected {entity_id} state "
                f"{expected_state!r}, got {state.state!r}"
            )
        for name, expected_value in (expected_attributes or {}).items():
            actual_value = state.attributes.get(name)
            if actual_value != expected_value:
                errors.append(
                    f"{domain}.{service}: expected {entity_id}.{name} "
                    f"{expected_value!r}, got {actual_value!r}"
                )

    cases = [
        (COMPONENT_DOMAIN, "turn_off", "binary_sensor.docker_binary_sensor", "off", {}),
        (COMPONENT_DOMAIN, "toggle", "binary_sensor.docker_binary_sensor", "on", {}),
        ("climate", "turn_off", "climate.docker_climate", "off", {}),
        ("climate", "turn_on", "climate.docker_climate", "heat", {}),
        ("cover", "close_cover", "cover.docker_cover", "closed", {"current_position": 0}),
        ("cover", "open_cover", "cover.docker_cover", "open", {"current_position": 100}),
        ("fan", "turn_off", "fan.docker_fan", "off", {"percentage": 0}),
        (
            "fan",
            "turn_on",
            "fan.docker_fan",
            "on",
            {"preset_mode": "boost"},
            {"preset_mode": "boost"},
        ),
        (
            "fan",
            "turn_on",
            "fan.docker_fan",
            "on",
            {"percentage": 33, "preset_mode": None},
            {"percentage": 33},
        ),
        ("humidifier", "turn_off", "humidifier.docker_humidifier", "off", {}),
        ("humidifier", "turn_on", "humidifier.docker_humidifier", "on", {}),
        ("lawn_mower", "start_mowing", "lawn_mower.docker_lawn_mower", "mowing", {}),
        ("lawn_mower", "pause", "lawn_mower.docker_lawn_mower", "paused", {}),
        ("lawn_mower", "dock", "lawn_mower.docker_lawn_mower", "returning", {}),
        ("light", "turn_off", "light.docker_light", "off", {}),
        (
            "light",
            "turn_on",
            "light.docker_light",
            "on",
            {"hs_color": (120.0, 50.0)},
            {"hs_color": [120, 50]},
        ),
        (
            "light",
            "turn_on",
            "light.docker_light",
            "on",
            {"color_temp_kelvin": 3500},
            {"color_temp_kelvin": 3500},
        ),
        ("lock", "lock", "lock.docker_lock", "locked", {}),
        ("lock", "unlock", "lock.docker_lock", "unlocked", {}),
        ("lock", "open", "lock.docker_lock", "open", {}),
        ("media_player", "media_pause", "media_player.docker_media_player", "paused", {}),
        ("media_player", "media_stop", "media_player.docker_media_player", "idle", {}),
        ("media_player", "turn_off", "media_player.docker_media_player", "off", {}),
        ("media_player", "turn_on", "media_player.docker_media_player", "on", {}),
        (
            "remote",
            "turn_off",
            "remote.docker_remote",
            "off",
            {},
        ),
        (
            "remote",
            "turn_on",
            "remote.docker_remote",
            "on",
            {"current_activity": "TV"},
            {"activity": "TV"},
        ),
        ("siren", "turn_off", "siren.docker_siren", "off", {}),
        ("vacuum", "stop", "vacuum.docker_vacuum", "idle", {}),
        ("vacuum", "clean_spot", "vacuum.docker_vacuum", "cleaning", {}),
        (
            "vacuum",
            "locate",
            "vacuum.docker_vacuum",
            "cleaning",
            {"last_command": {"command": "locate"}},
        ),
        ("vacuum", "return_to_base", "vacuum.docker_vacuum", "returning", {}),
        ("valve", "close_valve", "valve.docker_valve", "closed", {"current_position": 0}),
        ("valve", "open_valve", "valve.docker_valve", "open", {"current_position": 100}),
        ("water_heater", "turn_off", "water_heater.docker_water_heater", "off", {}),
        ("water_heater", "turn_on", "water_heater.docker_water_heater", "eco", {}),
    ]
    for case in cases:
        domain, service, entity_id, state, attributes, *service_data = case
        data = {"entity_id": entity_id}
        if service_data:
            data.update(service_data[0])
        await call_and_expect(domain, service, data, state, attributes)

    await call_and_expect(
        "camera",
        "turn_on",
        {"entity_id": "camera.docker_camera"},
        "idle",
    )
    camera_component = hass.data.get("camera")
    camera_entity = (
        camera_component.get_entity("camera.docker_camera")
        if camera_component is not None
        else None
    )
    if camera_entity is None or not camera_entity.is_on:
        errors.append("camera.turn_on: runtime camera did not turn on")
    await call_and_expect(
        "camera",
        "enable_motion_detection",
        {"entity_id": "camera.docker_camera"},
    )
    if camera_entity is None or not camera_entity.motion_detection_enabled:
        errors.append("camera.enable_motion_detection: flag was not enabled")
    await call_and_expect(
        "camera",
        "disable_motion_detection",
        {"entity_id": "camera.docker_camera"},
    )
    if camera_entity is None or camera_entity.motion_detection_enabled:
        errors.append("camera.disable_motion_detection: flag was not disabled")
    return errors


def _feature_sequence_restore_errors(hass: HomeAssistant) -> list[str]:
    """Check that the final command in each feature sequence survives reload."""
    expected = {
        "binary_sensor.docker_binary_sensor": ("on", {}),
        "climate.docker_climate": ("heat", {}),
        "cover.docker_cover": ("open", {"current_position": 100}),
        "fan.docker_fan": ("on", {"percentage": 33, "preset_mode": None}),
        "humidifier.docker_humidifier": ("on", {}),
        "lawn_mower.docker_lawn_mower": ("returning", {}),
        "light.docker_light": (
            "on",
            {"color_mode": "color_temp", "color_temp_kelvin": 3500},
        ),
        "lock.docker_lock": ("open", {}),
        "media_player.docker_media_player": ("on", {}),
        "remote.docker_remote": ("on", {"current_activity": "TV"}),
        "siren.docker_siren": ("off", {}),
        "vacuum.docker_vacuum": ("returning", {"fan_speed": "turbo"}),
        "valve.docker_valve": ("open", {"current_position": 100}),
        "water_heater.docker_water_heater": ("eco", {"temperature": 55.0}),
    }
    errors: list[str] = []
    for entity_id, (expected_state, expected_attributes) in expected.items():
        state = hass.states.get(entity_id)
        if state is None:
            errors.append(f"{entity_id}: feature reload state is missing")
            continue
        if state.state != expected_state:
            errors.append(
                f"{entity_id}: feature reload expected {expected_state!r}, "
                f"got {state.state!r}"
            )
        for name, expected_value in expected_attributes.items():
            actual_value = state.attributes.get(name)
            if actual_value != expected_value:
                errors.append(
                    f"{entity_id}.{name}: feature reload expected "
                    f"{expected_value!r}, got {actual_value!r}"
                )

    camera_component = hass.data.get("camera")
    camera_entity = (
        camera_component.get_entity("camera.docker_camera")
        if camera_component is not None
        else None
    )
    if camera_entity is None or not camera_entity.is_on:
        errors.append("camera.docker_camera: power was not restored after feature test")
    if camera_entity is None or camera_entity.motion_detection_enabled:
        errors.append("camera.docker_camera: motion flag was not restored after feature test")
    return errors


async def _async_test_common_controls(
    hass: HomeAssistant,
    entity_ids: list[str],
) -> list[str]:
    """Exercise shared attribute and availability controls on every domain."""
    errors: list[str] = []
    await _async_call(
        hass,
        errors,
        COMPONENT_DOMAIN,
        "set_attributes",
        {
            "entity_id": entity_ids,
            "attributes": {"docker_integration_probe": "present"},
        },
    )
    await asyncio.sleep(0.1)
    for entity_id in entity_ids:
        state = hass.states.get(entity_id)
        if state is None or state.attributes.get("docker_integration_probe") != "present":
            errors.append(f"{entity_id}: set_attributes was not applied")

    await _async_call(
        hass,
        errors,
        COMPONENT_DOMAIN,
        "clear_attributes",
        {
            "entity_id": entity_ids,
            "attributes": ["docker_integration_probe"],
        },
    )
    await asyncio.sleep(0.1)
    for entity_id in entity_ids:
        state = hass.states.get(entity_id)
        if state is None or "docker_integration_probe" in state.attributes:
            errors.append(f"{entity_id}: clear_attributes was not applied")

    await _async_call(
        hass,
        errors,
        COMPONENT_DOMAIN,
        "set_available",
        {"entity_id": entity_ids, "value": False},
    )
    await asyncio.sleep(0.1)
    for entity_id in entity_ids:
        state = hass.states.get(entity_id)
        domain = entity_id.partition(".")[0]
        unavailable = (
            state is not None
            and (
                state.attributes.get("available") is False
                if domain in STATE_ONLY_ENTITY_DOMAINS
                else state.state == STATE_UNAVAILABLE
            )
        )
        if not unavailable:
            errors.append(f"{entity_id}: unavailable state was not applied")

    await _async_call(
        hass,
        errors,
        COMPONENT_DOMAIN,
        "set_available",
        {"entity_id": entity_ids, "value": True},
    )
    await asyncio.sleep(0.1)
    for entity_id in entity_ids:
        state = hass.states.get(entity_id)
        domain = entity_id.partition(".")[0]
        available = (
            state is not None
            and (
                state.attributes.get("available") is True
                if domain in STATE_ONLY_ENTITY_DOMAINS
                else state.state != STATE_UNAVAILABLE
            )
        )
        if not available:
            errors.append(f"{entity_id}: available state was not restored")
    return errors


async def _async_wait_for_states(
    hass: HomeAssistant,
    entity_ids: list[str],
) -> list[str]:
    for _attempt in range(60):
        missing = [
            entity_id
            for entity_id in entity_ids
            if hass.states.get(entity_id) is None
        ]
        if not missing:
            return []
        await asyncio.sleep(1)
    return missing


async def _async_run(hass: HomeAssistant) -> None:
    result: dict = {"success": False}
    error_handler = _VirtualLayerErrorHandler()
    deprecation_handler = _VirtualLayerDeprecationHandler()
    logging.getLogger().addHandler(error_handler)
    logging.getLogger().addHandler(deprecation_handler)
    try:
        for entry in hass.config_entries.async_entries(COMPONENT_DOMAIN):
            await hass.config_entries.async_remove(entry.entry_id)

        flow_result = await hass.config_entries.flow.async_init(
            COMPONENT_DOMAIN,
            context={"source": SOURCE_USER},
            data={ATTR_GROUP_NAME: GROUP_NAME, "add_first_entity": False},
        )
        if flow_result["type"] is not FlowResultType.CREATE_ENTRY:
            raise RuntimeError(f"Config flow failed: {flow_result}")
        entry = flow_result["result"]

        entity_configs = [
            *[
                _entity_config(domain)
                for domain in VIRTUAL_ENTITY_DOMAINS
            ],
            *_variant_entity_configs(),
        ]
        configured_entity_ids = [
            entity["entity_id"]
            for entity in entity_configs
        ]
        options = {
            ATTR_DEVICES: {
                DEVICE_NAME: entity_configs,
            },
            ATTR_DEVICE_ATTRIBUTES: {
                DEVICE_NAME: {
                    ATTR_DEVICE_ID: "docker-all-domains-device",
                    CONF_NAME: DEVICE_NAME,
                },
            },
        }
        hass.config_entries.async_update_entry(entry, options=options)
        if not await hass.config_entries.async_reload(entry.entry_id):
            raise RuntimeError("Virtual Layer config entry reload failed")

        missing_entities = await _async_wait_for_states(hass, configured_entity_ids)
        missing_domains = sorted({
            entity_id.partition(".")[0]
            for entity_id in missing_entities
        })
        service_errors = await _async_test_services(hass) if not missing_entities else []
        variant_service_errors = (
            await _async_test_variant_services(hass)
            if not missing_entities
            else []
        )
        await asyncio.sleep(1)
        state_contract_errors = (
            _state_contract_errors(hass) if not missing_entities else []
        )
        variant_contract_errors = (
            _variant_contract_errors(hass) if not missing_entities else []
        )
        if not await hass.config_entries.async_reload(entry.entry_id):
            raise RuntimeError("Virtual Layer persistence reload failed")
        reload_missing_entities = await _async_wait_for_states(
            hass,
            configured_entity_ids,
        )
        reload_missing_domains = sorted({
            entity_id.partition(".")[0]
            for entity_id in reload_missing_entities
        })
        await asyncio.sleep(1)
        restore_contract_errors = (
            _state_contract_errors(hass)
            if not missing_entities and not reload_missing_entities
            else []
        )
        variant_restore_contract_errors = (
            _variant_contract_errors(hass)
            if not missing_entities and not reload_missing_entities
            else []
        )
        feature_sequence_errors = (
            await _async_test_feature_sequences(hass)
            if not missing_entities and not reload_missing_entities
            else []
        )
        if feature_sequence_errors:
            feature_reload_missing_entities = []
            feature_restore_errors = []
        else:
            if not await hass.config_entries.async_reload(entry.entry_id):
                raise RuntimeError("Virtual Layer feature persistence reload failed")
            feature_reload_missing_entities = await _async_wait_for_states(
                hass,
                configured_entity_ids,
            )
            await asyncio.sleep(1)
            feature_restore_errors = (
                _feature_sequence_restore_errors(hass)
                if not feature_reload_missing_entities
                else []
            )
        common_control_errors = (
            await _async_test_common_controls(hass, configured_entity_ids)
            if not missing_entities
            and not reload_missing_entities
            and not feature_reload_missing_entities
            else []
        )

        entity_registry = er.async_get(hass)
        device_registry = dr.async_get(hass)
        missing_registry_entries = []
        device_ids = set()
        for entity_id in configured_entity_ids:
            entity_entry = entity_registry.async_get(entity_id)
            if entity_entry is None:
                missing_registry_entries.append(entity_id)
                continue
            if entity_entry.device_id is not None:
                device_ids.add(entity_entry.device_id)

        missing_devices = [
            device_id
            for device_id in device_ids
            if device_registry.async_get(device_id) is None
        ]
        incorrect_state_only_states = [
            domain
            for domain in STATE_ONLY_ENTITY_DOMAINS
            if (
                state := hass.states.get(f"{domain}.docker_{domain}")
            ) is None or state.state != "docker_smoke"
        ]
        battery_state = hass.states.get("sensor.docker_vacuum_battery")
        battery_entry = entity_registry.async_get("sensor.docker_vacuum_battery")
        battery_sensor_valid = (
            battery_state is not None
            and battery_state.state == "80"
            and battery_state.attributes.get("device_class") == "battery"
            and battery_state.attributes.get("unit_of_measurement") == "%"
            and battery_entry is not None
            and battery_entry.device_id in device_ids
        )
        result.update({
            "home_assistant_version": HA_VERSION,
            "domain_count": len(VIRTUAL_ENTITY_DOMAINS),
            "entity_count": len(configured_entity_ids),
            "missing_domains": missing_domains,
            "missing_entities": missing_entities,
            "missing_registry_entries": missing_registry_entries,
            "device_count": len(device_ids),
            "missing_devices": missing_devices,
            "incorrect_state_only_states": incorrect_state_only_states,
            "battery_sensor_valid": battery_sensor_valid,
            "service_errors": service_errors,
            "variant_service_errors": variant_service_errors,
            "state_contract_errors": state_contract_errors,
            "variant_contract_errors": variant_contract_errors,
            "reload_missing_domains": reload_missing_domains,
            "reload_missing_entities": reload_missing_entities,
            "restore_contract_errors": restore_contract_errors,
            "variant_restore_contract_errors": variant_restore_contract_errors,
            "feature_sequence_errors": feature_sequence_errors,
            "feature_reload_missing_entities": feature_reload_missing_entities,
            "feature_restore_errors": feature_restore_errors,
            "common_control_errors": common_control_errors,
            "logged_errors": error_handler.messages,
            "deprecation_warnings": deprecation_handler.messages,
        })
        result["success"] = battery_sensor_valid and not any((
            missing_entities,
            missing_registry_entries,
            missing_devices,
            incorrect_state_only_states,
            service_errors,
            variant_service_errors,
            state_contract_errors,
            variant_contract_errors,
            reload_missing_entities,
            restore_contract_errors,
            variant_restore_contract_errors,
            feature_sequence_errors,
            feature_reload_missing_entities,
            feature_restore_errors,
            common_control_errors,
            error_handler.messages,
            deprecation_handler.messages,
        )) and len(device_ids) == 1
    except Exception:  # noqa: BLE001 - serialize failures from the live container
        result["exception"] = traceback.format_exc()
    finally:
        logging.getLogger().removeHandler(error_handler)
        logging.getLogger().removeHandler(deprecation_handler)
        result_path = Path(hass.config.path(RESULT_FILE))
        await hass.async_add_executor_job(
            result_path.write_text,
            json.dumps(result, indent=2, sort_keys=True),
        )
        _stop_task = asyncio.create_task(
            hass.async_stop(),
            name="stop Home Assistant after Virtual Layer smoke test",
        )


async def async_setup(hass: HomeAssistant, _config: dict) -> bool:
    """Schedule the smoke test after Home Assistant starts."""

    @callback
    def _async_started(_event) -> None:
        hass.async_create_task(_async_run(hass), "virtual_layer all-domain smoke")

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _async_started)
    return True
