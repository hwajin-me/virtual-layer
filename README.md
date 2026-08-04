# Virtual Layer for Home Assistant

Virtual Layer is a Home Assistant custom integration for creating virtual
devices and entities from the Home Assistant UI.

![Virtual Layer icon](images/virtual-icon.png)

## Breaking Changes

Virtual Layer is UI-only. Entity definitions are stored in the integration
config entry options and file-based entity loading is no longer supported.

Do not add Virtual Layer entities to `configuration.yaml`. Create, edit, delete,
back up, and restore them from `Settings > Devices & services > Virtual Layer`.

## Contents

- [Features](#features)
- [Installation](#installation)
- [UI Configuration](#ui-configuration)
- [Devices](#devices)
- [Entities](#entities)
- [Composite Entities](#composite-entities)
- [Supported Domains](#supported-domains)
- [Services](#services)
- [Backup and Restore](#backup-and-restore)
- [Translations and Icons](#translations-and-icons)
- [Testing](#testing)

## Features

- UI-only config flow and options flow
- Create and edit virtual devices
- Set device metadata such as device ID, manufacturer, model, software version,
  hardware version, and serial number
- Create, edit, and delete virtual entities
- Delete multiple entities in one operation
- Set entity name and entity ID from the UI
- Create a virtual entity from one or more existing Home Assistant entities
- Auto-generate useful helper templates when multiple source entities are
  selected
- Optional Home Assistant Jinja templates for custom state, availability, and
  attributes
- Periodic pull refresh for composite entities
- Backup and restore UI-managed device definitions
- Korean and English UI translations
- Integration icons and brand assets

## Installation

### HACS

[![hacs_badge](https://img.shields.io/badge/HACS-Default-orange.svg?style=for-the-badge)](https://github.com/hacs/integration)

Install Virtual Layer from HACS, then restart Home Assistant.

### Manual

Copy `custom_components/virtual_layer` into your Home Assistant
`/config/custom_components/virtual_layer` directory, then restart Home
Assistant.

## UI Configuration

Create the integration from:

`Settings > Devices & services > Add integration > Virtual Layer`

During setup you can:

- enter the initial device name
- optionally add the first entity immediately
- select existing source entities to prefill a new virtual entity
- customize the generated entity name, entity ID, domain, initial state, device
  metadata, and templates

After setup, use `Configure` on the Virtual Layer integration entry to:

- add a virtual entity
- edit an existing virtual entity
- delete one or more virtual entities
- back up devices
- restore devices
- finish without changes

Use `Reconfigure` to update the integration entry's main device name.

## Devices

Virtual Layer uses `Device` in the UI. Older internal/service fields may still
use `group_name` for backward compatibility, but the user-facing concept is a
device.

Entities with the same device name are attached to the same Home Assistant
device. When creating or editing an entity, the UI also lets you set device
registry metadata:

- device ID
- manufacturer
- model
- software version
- hardware version
- serial number

If no device ID is provided, the device name is used.

## Entities

Every entity supports:

- domain
- name
- optional explicit entity ID
- initial value
- initial availability
- persistence
- source entities
- template source variables
- value template
- availability template
- static attributes
- attribute sources
- attribute templates
- pull interval

The UI accepts JSON objects for static attributes, template sources, attribute
sources, and attribute templates.

Example template source JSON:

```json
{
  "power": "sensor.washer_power",
  "door": "binary_sensor.washer_door.state",
  "room_humidity": {
    "entity_id": "sensor.laundry_room",
    "attribute": "humidity"
  }
}
```

Example attribute source JSON:

```json
{
  "copied_power": "sensor.washer_power.state",
  "power_unit": "sensor.washer_power.unit_of_measurement"
}
```

Example attribute template JSON:

```json
{
  "summary": "{{ power }}W / {{ room_humidity }}%",
  "load_score": "{{ (power|float(0) * room_humidity|float(0) / 100)|round(1) }}"
}
```

Set `pull_interval` to a positive number of seconds to periodically refresh
source values and templates. Leave it empty or set it to `0` to update from
source entity state changes only.

## Composite Entities

When adding or editing an entity, select one or more existing Home Assistant
entities first. Virtual Layer prefills the new virtual entity from those
sources, and you can then customize only the fields you care about.

For multiple source entities, Virtual Layer generates a helper template based
on the source type:

- Boolean-like sources use `AND`
- Number-like sources use `average`
- String-like sources use concatenation
- Date, time, and datetime sources use the latest known value
- Select/input-select sources use the first available value
- Multiple location sources create a GPS median helper. A source more than 300 m
  from that median is followed for 30 minutes after its latest GPS update, so a
  travelling device remains selected after it arrives near the other devices.

The generated Home Assistant Jinja template is optional. You can keep it, edit
it, or replace it entirely.

Example washer-style virtual sensor:

```jinja
{% if is_state('binary_sensor.washer_door', 'on') %}
  paused
{% elif states('sensor.washer_power')|float(0) > 10 %}
  washing
{% else %}
  idle
{% endif %}
```

Home Assistant does not provide dedicated `washer`, `dryer`, or `pump` entity
domains. Model these appliances by creating multiple virtual entities under
one Virtual Layer device, for example a state sensor, a door binary sensor,
and a power switch. Washer and dryer status metadata such as `program`,
`remaining_time`, and `door_locked` can be supplied through the UI's Domain
options JSON and is exposed as sensor attributes. A pump can use a virtual
`switch` or `valve` depending on whether it needs position/open-close control.

Humidifiers support both `humidifier` and `dehumidifier` device classes,
humidity targets, modes, and native turn-on/turn-off commands. Electrical
sensor and number values support power (`kW`), energy (`kWh`), current (`A`),
voltage (`V`), apparent power (`VA`), reactive power (`var`), and power factor
(`%`) with automatic units unless a custom unit is supplied.

Image entities support a single source-image alias, a local file, or an image
URL and expose the native image bytes/content type. Camera and image entities
cannot be combined into a multi-source helper because binary media cannot be
meaningfully concatenated or averaged.

## Cameras

Create a camera alias by selecting one camera as the original entity. The UI
automatically selects the `camera` domain, copies its state through a template,
and sets the camera-specific `source_entity` option. The virtual camera proxies
the source image and stream while keeping its own entity name, id, device, and
other virtual-layer settings.

Camera creation also supports direct UI configuration through **Domain options
JSON**, which is the UI-only equivalent of camera YAML options. A camera can
use a local image, a stream URL, or both without an original entity:

```json
{
  "image_path": "/config/www/virtual-camera.jpg",
  "stream_source": "rtsp://camera.example.local/live",
  "is_recording": false,
  "motion_detection": true
}
```

For an alias, use the source option alone (or add direct options to override
the proxied image or stream):

```json
{
  "source_entity": "camera.front_door"
}
```

## Direct Domain Settings

Every virtual entity is created and edited from the UI. **Domain options JSON**
is the UI-only equivalent of domain YAML options: it is validated against the
native virtual implementation for rich domains such as climate, cover, light,
humidifier, camera, and lock.

For the remaining state-backed domains, use the same field for arbitrary
JSON-compatible domain data. These settings are preserved on edits and backup
restore, and appear as state attributes. This makes YAML-only style metadata
available without enabling YAML loading. For example, a virtual weather entity
can be created with:

```json
{
  "temperature": 21.5,
  "humidity": 48,
  "forecast_provider": "virtual"
}
```

## Supported Domains

Virtual Layer supports every Home Assistant building-block entity domain listed
in the official entities and domains documentation at the time this integration
was updated.

Domain-specific virtual behavior is implemented for:

`binary_sensor`, `camera`, `climate`, `cover`, `device_tracker`, `fan`,
`humidifier`, `image`, `light`, `lock`, `number`, `sensor`, `switch`, `vacuum`,
and `valve`.

Generic state-backed virtual entities are available for:

`ai_task`, `air_quality`, `alarm_control_panel`, `assist_satellite`, `button`,
`calendar`, `conversation`, `date`, `datetime`, `event`, `geolocation`,
`image_processing`, `infrared`, `lawn_mower`, `media_player`,
`notify`, `radio_frequency`, `remote`, `scene`, `select`, `siren`, `stt`,
`tag`, `text`, `time`, `todo`, `tts`, `update`, `wake_word`, `water_heater`,
and `weather`.

Generic state-backed virtual entities support state, availability, persistence,
device attachment, attributes, source entities, templates, and pull refresh.
The virtual vacuum additionally exposes native HA activity states and start,
pause, stop, return-to-base, spot-clean, locate, fan-speed, and send-command
services.

## Services

Virtual Layer provides these services:

- `virtual_layer.set_available`: set availability for any virtual entity
- `virtual_layer.turn_on`: turn on a virtual binary sensor
- `virtual_layer.turn_off`: turn off a virtual binary sensor
- `virtual_layer.toggle`: toggle a virtual binary sensor
- `virtual_layer.set`: set a virtual sensor value
- `virtual_layer.set_state`: set state on a virtual entity using native domain
  behavior where possible
- `virtual_layer.set_attributes`: add or update extra state attributes
- `virtual_layer.clear_attributes`: clear selected attributes, or all extra
  attributes when no names are supplied
- `virtual_layer.backup_devices`: write a JSON backup file
- `virtual_layer.restore_devices`: restore a JSON backup file in `merge` or
  `replace` mode
- `virtual_layer.move`: move a virtual device tracker

## Backup and Restore

Use the integration `Configure` menu or the services to back up and restore
Virtual Layer devices.

Default backup path:

```text
/config/virtual_layer_backup.json
```

Restore modes:

- `merge`: keep existing UI-managed devices and append restored entities with
  regenerated entity keys
- `replace`: replace existing UI-managed devices with the restored backup

Backups include the UI-managed device/entity definitions and device metadata.
The loader is defensive about older or malformed saved data so that invalid
entries can still be skipped, replaced, or removed from the UI.

## Translations and Icons

Virtual Layer includes integration icons, brand assets, and Home Assistant UI
translations.

Current translation files:

- English: `custom_components/virtual_layer/translations/en.json`
- Korean: `custom_components/virtual_layer/translations/ko.json`
- Czech: `custom_components/virtual_layer/translations/cz.json`
- Slovak: `custom_components/virtual_layer/translations/sk.json`

## Testing

Run unit and integration tests:

```sh
PYTHONPATH=. .venv/bin/pytest tests/unit tests/integration -q
```

Run the syntax and lightweight lint checks used during development:

```sh
.venv/bin/python -m compileall custom_components/virtual_layer tests -q
ruff check custom_components/virtual_layer tests --select E9,F63,F7,F82
git diff --check
```

Install the local git pre-commit lint hook:

```sh
git config core.hooksPath .githooks
```

After installing it, every commit runs the same lightweight compile, Ruff, and
whitespace checks.

Run a real Home Assistant container with Docker Compose:

```sh
docker compose -f tests/docker/docker-compose.yml pull
docker compose -f tests/docker/docker-compose.yml up -d
docker compose -f tests/docker/docker-compose.yml logs -f homeassistant
```

Open `http://localhost:8123`, finish Home Assistant onboarding if needed, then
add `Virtual Layer` from `Settings > Devices & services > Add integration`.

Stop the container:

```sh
docker compose -f tests/docker/docker-compose.yml down
```

The Docker environment intentionally does not include any Virtual Layer YAML.
It mounts the local custom integration into Home Assistant and verifies the same
UI-only path users will use.
