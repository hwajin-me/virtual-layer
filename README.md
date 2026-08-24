# Virtual Layer for Home Assistant

Virtual Layer is a Home Assistant custom integration for creating virtual
devices and entities from the Home Assistant UI.

![Virtual Layer icon](images/virtual-icon.png)

## Breaking Changes

Virtual Layer is UI-only. Entity definitions are stored in the integration
config entry options and file-based entity loading is no longer supported.

Do not add Virtual Layer entities to `configuration.yaml`. Create, edit, delete,
and manage them from `Settings > Devices & services > Virtual Layer`.

## Contents

- [Features](#features)
- [Installation](#installation)
- [UI Configuration](#ui-configuration)
- [Polygon Zones](#polygon-zones)
- [Devices](#devices)
- [Entities](#entities)
- [Composite Entities](#composite-entities)
- [Supported Domains](#supported-domains)
- [Services](#services)
- [Translations and Icons](#translations-and-icons)
- [Testing](#testing)

## Features

- UI-only config flow and options flow
- Create and edit virtual devices
- Set device metadata such as device ID, manufacturer, model, software version,
  hardware version, and serial number
- Create, edit, and delete virtual entities
- Delete multiple entities in one operation
- Delete a complete virtual Device, including malformed legacy groups
- Set entity name and entity ID from the UI
- Create a virtual entity from one or more existing Home Assistant entities
- Auto-generate useful helper templates when multiple source entities are
  selected
- Optional Home Assistant Jinja templates for custom state, availability, and
  attributes
- Periodic pull refresh for composite entities
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
- manage virtual device metadata
- delete a virtual device and all of its entities
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
- native property templates
- command actions
- pull interval

The UI accepts JSON objects for static attributes, template sources, attribute
sources, attribute templates, native property templates, and command actions.

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

Native property templates update real Home Assistant entity properties instead
of adding extra attributes. They work for every domain and may return strings,
numbers, booleans, dictionaries, or lists. Native validation and dependent
feature updates are included for climate, fan, humidifier, light, number,
select, text, date/time, siren, lawn mower, remote, media player, water heater,
update, vacuum, camera, image, device tracker, cover, valve, lock, sensor,
binary sensor, and switch entities. For example, changing `source_list`,
`effect_list`, `operation_list`, `fan_speed_list`, or `options` also updates the
corresponding Home Assistant controls and clears a stale selected value. Range
templates revalidate the current value, and GPS templates accept either a
`gps` pair or separate `latitude` and `longitude` values:

```json
{
  "fan_modes": "{{ state_attr('climate.bedroom', 'fan_modes') or [] }}",
  "fan_mode": "{{ state_attr('climate.bedroom', 'fan_mode') }}",
  "target_temperature": "{{ states('sensor.preferred_temperature') | float }}"
}
```

Common aliases use the names shown by Home Assistant: `state`/`is_on`,
`temperature`, `humidity`, `position`, `source`, `effect`, `activity`, and
`location_accuracy`. Domain-specific list and range names can be copied from
Developer Tools > States, such as `preset_modes`, `available_tones`,
`supported_color_modes`, `min_temp`, or `native_step`.

Command actions connect virtual controls to real entities. Keys are native
method names without the `async_` prefix, such as `turn_on`, `set_temperature`,
`set_fan_mode`, `set_percentage`, or `set_humidity`. All command arguments are
available to action templates. A value can be one action, an action list, or an
object with `sequence` and `optimistic`. Set `optimistic` to `false` when native
property templates should exclusively reflect the real device state:

```json
{
  "set_temperature": {
    "optimistic": false,
    "sequence": [
      {
        "action": "climate.set_temperature",
        "target": {"entity_id": "climate.bedroom"},
        "data": {"temperature": "{{ temperature }}"}
      }
    ]
  }
}
```

Command actions use Home Assistant's action engine, so conditions, `choose`,
delays, and templated action data are supported.

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

Source attributes receive helpers too. A single source uses a dynamic
`state_attr()` template. Attributes shared by multiple sources use `AND` for
booleans, `average` for numbers, an order-preserving unique union for lists,
concatenation for strings, and an ordered merge for mapping objects. Native
domain properties and metadata such as device class or unit are kept in their
dedicated fields instead of duplicated.

The generated Home Assistant Jinja template is optional. You can keep it, edit
it, or replace it entirely. Editing an entity with selected sources shows a
template policy step even when the source IDs are unchanged, so updated source
capabilities can be read again. Changing sources in the Modify form shows the
policy step again. Automatic mode regenerates untouched helpers while
preserving each customized field, Keep Current leaves every template unchanged,
and Force Helper replaces generated and custom templates from the current
sources.

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

## Polygon Zones

A virtual `device_tracker` can combine multiple source trackers and resolve its
GPS position against named GeoJSON polygons. Configure it entirely in the
Add/Edit Virtual Entity form:

- **Source entities**: one or more `device_tracker` entities
- **Polygon GeoJSON**: an inline Feature or FeatureCollection
- **Polygon files or URLs**: one local path or HTTP(S) URL per line
- **Person**: the optional `person` represented by the combined tracker
- **Tracker selection strategy**: `majority`, `priority`, `latest`, or `median`
- **Tracker grouping distance**: distance used to form majority groups
- **Tracker rules JSON**: optional per-source filtering and selection rules
- **Outside-zone state**: defaults to `not_home`

GeoJSON supports `Polygon`, `MultiPolygon`, interior holes, overlapping-zone
priority, GPS accuracy at boundaries, and polygons crossing the international
date line. Coordinates use GeoJSON order: `[longitude, latitude]`. Each feature
must have a `properties.name`; a lower numeric `properties.priority` wins when
zones overlap.

```json
{
  "type": "FeatureCollection",
  "features": [{
    "type": "Feature",
    "properties": {"name": "Office", "priority": 1},
    "geometry": {
      "type": "Polygon",
      "coordinates": [[[126.9, 37.4], [127.1, 37.4], [127.1, 37.6], [126.9, 37.4]]]
    }
  }]
}
```

Tracker rules are keyed by source entity ID. `dominant` always selects that
valid source; `weight` affects majority voting; lower `priority` wins the
priority strategy; `max_age_seconds` and `max_gps_accuracy` reject stale or
imprecise reports; `enabled` disables a source; and `condition_template`
provides a Home Assistant Jinja condition with `source`, `source_entity_id`,
`person`, and `this` variables.

```json
{
  "device_tracker.primary_phone": {
    "dominant": true,
    "priority": 1,
    "max_age_seconds": 1800,
    "max_gps_accuracy": 100,
    "condition_template": "{{ source.state != 'unavailable' }}"
  },
  "device_tracker.watch": {"weight": 2}
}
```

The virtual tracker keeps both the selected zone state and GPS coordinates, so
it appears on Home Assistant maps. Virtual Layer also creates
`sensor.<tracker_id>_zone` and `image.<tracker_id>_map` on the same device. The
SVG image draws every Polygon/MultiPolygon and marks the combined tracker's
current GPS position; it can be used in an image or picture card. File- and
URL-backed GeoJSON is reloaded every five minutes. If one source fails, valid
files and the last working polygon set remain active, and the error is reported
in the tracker's `polygon_load_error` attribute. Editing or deleting the
virtual tracker updates or cleans up both generated entities normally.

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

Climate/HVAC entities expose dedicated add/edit controls for HVAC, fan, preset,
vertical swing, and horizontal swing modes; current HVAC action; current and
target temperatures; temperature ranges and steps; current and target humidity;
and the temperature unit. Fan entities expose speed count, initial percentage,
preset modes, oscillation, and direction controls. Humidifier entities expose
humidifier/dehumidifier type, current action, humidity limits and target, modes,
and adjustment step. Copying an existing entity prefills these native controls,
including older Virtual Layer configurations that stored them as attributes.
Custom mode and preset values can be added when the source integration does not
publish a list.

Climate, fan, and humidifier forms also provide a collapsed **Native value
Jinja templates** section. Every native value supported by those virtual
entities has its own Home Assistant Template editor. This includes mode and
mode-list fields, fan/preset/swing and horizontal-swing values, current and
target temperature ranges, current and target humidity ranges, fan percentage,
oscillation, direction, humidifier action, and on/off state where applicable.
A non-empty template takes precedence over the corresponding static control;
leaving it empty keeps the static value as the fallback. Existing managed
entries from Native property templates JSON are moved into these dedicated
editors when an entity is edited. The raw Native property templates JSON input
is not shown for these domains; unknown vendor-specific keys from older entries
are preserved transparently when the entity is saved.

The same dedicated Jinja section covers the standard native properties of 41
Home Assistant entity domains. In addition to the domains above, this includes
air quality, alarms, Assist satellites, calendars, conversations, events,
geolocation, image processing, media metadata, notifications, STT/TTS, to-do
lists, updates, and weather. Lights include HS, XY, RGB, RGBW, and RGBWW colors;
media players include playback metadata, sound modes, grouping, and progress;
and covers include tilt position and tilt actions. For example, a vacuum can
template its activity, battery level, fan speed list, current fan speed, and
supported feature set without editing JSON.

The five domains without additional synchronous native properties (`infrared`,
`radio_frequency`, `scene`, `tag`, and `wake_word`) continue to use the common
value, availability, icon, and attribute templates. Their advanced JSON input
remains available for integration-specific extensions that have no standard
Home Assistant property contract.

For the remaining state-backed domains, use the same field for arbitrary
JSON-compatible domain data. These settings are preserved on edits and appear
as state attributes. This makes YAML-only style metadata
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
- `virtual_layer.move`: move a virtual device tracker

## Translations and Icons

Virtual Layer includes integration icons, brand assets, and Home Assistant UI
translations.

Current translation files:

- English: `custom_components/virtual_layer/translations/en.json`
- Korean: `custom_components/virtual_layer/translations/ko.json`

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

Run a compatibility smoke test against the official stable Home Assistant
container. It checks climate, robot vacuum, and camera imports, schemas,
features, and legacy native-template recovery without a custom Docker image.
The complete config-entry, registry, service, reload, and all-domain behavior
matrix runs under `tests/integration`:

```sh
tests/docker/run_compatibility_smoke.sh
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
