#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.yml"

docker compose -f "$COMPOSE_FILE" pull homeassistant
docker compose -f "$COMPOSE_FILE" run --rm --no-deps -T \
  -e PYTHONPATH=/config \
  --entrypoint python \
  homeassistant - <<'PY'
from homeassistant.components.camera import CameraEntityFeature
from homeassistant.components.light import ColorMode, LightEntityFeature
from homeassistant.const import __version__ as HA_VERSION

from custom_components.virtual_layer import number, sensor
from custom_components.virtual_layer.camera import CAMERA_SCHEMA, VirtualCamera
from custom_components.virtual_layer.climate import CLIMATE_SCHEMA, VirtualClimate
from custom_components.virtual_layer.light import LIGHT_SCHEMA, VirtualLight
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
assert CameraEntityFeature.STREAM in camera.supported_features
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
print(
    "Virtual Layer Docker compatibility smoke passed "
    f"on Home Assistant {HA_VERSION}"
)
PY
