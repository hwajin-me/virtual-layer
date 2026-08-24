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
from homeassistant.const import __version__ as HA_VERSION

from custom_components.virtual_layer import number, sensor
from custom_components.virtual_layer.camera import CAMERA_SCHEMA, VirtualCamera
from custom_components.virtual_layer.climate import CLIMATE_SCHEMA, VirtualClimate
from custom_components.virtual_layer.vacuum import VACUUM_SCHEMA, VirtualVacuum


climate = VirtualClimate(
    CLIMATE_SCHEMA({
        "name": "Docker Climate",
        "entity_id": "climate.docker_climate",
        "initial_value": "heat",
        "hvac_modes": ["off", "heat"],
        "native_templates": {
            "hvac_action": "{{ <HVACAction.HEATING: 'heating'> }}",
        },
    }),
    False,
)
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

assert climate._native_templates["hvac_action"] == "{{ 'heating' }}"
assert int(vacuum.supported_features) >= 0
assert CameraEntityFeature.STREAM in camera.supported_features
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
