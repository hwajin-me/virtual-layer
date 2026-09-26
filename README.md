# Virtual Layer for Home Assistant

Virtual Layer is a Home Assistant custom integration for creating virtual
devices and entities from the Home Assistant UI.

**Presence Fusion** is an optional person-specific Virtual Layer Device profile.
It maps GPS/Wi-Fi/BLE/room sources per physical device, excludes devices left
behind, retains tracking at stops, and restores priority only after observed
reunion. Choose **Presence Fusion — track a person** in the initial setup.
See [installation, source mapping, outputs, manual override and privacy](docs/PRESENCE_FUSION.md).

![Virtual Layer icon](images/virtual-icon.png)

## Breaking Changes

Virtual Layer is UI-only. Entity definitions are stored in the integration
config entry options and file-based entity loading is no longer supported.

Do not add Virtual Layer entities to `configuration.yaml`. Create, edit, delete,
and manage them from `Settings > Devices & services > Virtual Layer`.

## Contents

### Teach camera patrol positions

After creating a virtual camera, open Virtual Layer **Configure → Configure
camera patrol positions**. Select the virtual camera and its existing ONVIF
camera, then set the speed and interval. This pauses its running/automatic patrol.
Watch the camera in a separate dashboard tab: jog left/right/up/down, wait for
motion to stop, name the view, and choose **Capture current position**. Capture
at least two different positions. You can visit, replace, delete, and reorder
the points before choosing **Save route**. Nothing is persisted until save.

Patrol visits the actual saved pan/tilt coordinates in order and repeats from
the last point to the first; zoom stays unchanged. This route replaces relative
range settings but is not a hard boundary on the path between points. It requires
ONVIF GetStatus and AbsoluteMove support and reuses the existing integration’s
authenticated client. Changing the ONVIF target clears the draft route. Choose
**Use legacy relative patrol** to remove the route and reuse the old ranges.
Normal entity edits preserve routes, media sources, and Frigate settings.
Cancel discards draft edits, but does not reverse preview movement or restart
patrol. Teaching movements use normal recording settings; patrol itself still
uses the configured Frigate policies and return-to-start behavior.

Cameras with an ONVIF patrol target automatically create
`switch.<camera_object_id>_patrol` on the same Device. Turn this switch on or off
from dashboards or automations to start or stop patrol. Its state follows the
camera's running patrol, including service calls and failures; it does not restore
a stale on state after restart. The camera's configured automatic-start setting
still applies. Removing the camera or its ONVIF patrol target removes the generated
control when the entry reloads.

Patrol captures the initial ONVIF pan/tilt position when the camera supports
`GetStatus` and requests an `AbsoluteMove` back on stop (including patrol errors).
Zoom is unchanged. Unsupported position queries or return moves are logged;
relative patrol remains available on those cameras. Return commands do not
guarantee physical arrival before recording resumes.

Patrol now defaults to Off for Frigate controls. Existing explicit Keep/On
policies are preserved: set both Recording during patrol and Detection during
patrol to Off and choose Entire patrol to suppress
recording and detection for the whole session. With no explicit target, controls
are discovered only on the selected source camera's Frigate device. Detection
is disabled and confirmed before motion is disabled. All switch commands must
be confirmed before movement; stop restores each captured state in reverse order.
The virtual camera's recording state follows the linked recording switch.

In camera Domain settings, optionally select a Frigate recording switch **or** enter an
MQTT base topic such as `frigate/front/recordings`, then set Recording during
patrol to Off. This also controls detect and motion (including the sibling MQTT
topics); explicit legacy motion policies remain supported. MQTT uses Home Assistant's configured broker, subscribes to
`<base>/state`, and publishes non-retained commands to `<base>/set`. It requires
a known ON/OFF state and confirmation before starting movement. Stop restores
the previous recording state after requesting return to the initial position.
Keep leaves recording untouched; On temporarily enables it. Switch and MQTT
controls are mutually exclusive. A failed restore is logged and retained for
another stop attempt; abrupt power loss cannot restore the previous state.

Enable Automatic patrol cycle to repeat a patrol session followed by a quiet
period (defaults: 300 seconds on, 1800 seconds off). Manual switch or patrol
service control suspends this cycle until the integration reloads. Automatic
cycles take priority over the existing start-on-load option and are cancelled
when the camera unloads.

Recording control period can be Entire patrol (the existing behavior) or During
movement only. With recording policy Off, movement-only control disables
recording before each move, waits the configurable settling time (default five
seconds), sends ONVIF Stop, and restores the previous state during the interval
between movements. This also applies to the return move. Settling is a timed
estimate, not physical position feedback; increase it for slower movements.

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

## Dynamic boiler feedback (opt-in)

The boiler helper step now shows the **base room-to-water formula** separately
from **dynamic water-target formula**, including during creation and editing.
Enable dynamic boiler feedback there or in the climate domain settings, then
select room-temperature sensors. The base linear conversion remains unchanged;
room warming and residual heat belong in the dynamic formula, which is retained
through the next form and save. Use one boiler climate source, Celsius virtual
temperature units and the existing room-to-water calibration formula. The
boiler's `current_temperature` must measure heating water. Source temperatures
in Fahrenheit are converted to Celsius.
Room sensors may mix Celsius, Fahrenheit and Kelvin; unitless room readings
are treated as Celsius. The generated dynamic room-display helper uses the
same conversion as the controller.

Set a room target after enabling. This target is kept separate from the water
target and restored when persistence is enabled. This mode owns temperature
writes instead of the configured `set_temperature` action. Power/mode actions
still apply. It never starts heating by itself: both climates must be in heat
mode. It evaluates every 60 seconds, attempts writes at least 120 seconds
apart, ignores differences below 0.5°C and limits each change to 2°C while
respecting the boiler's limits and step.

The editable formula returns Celsius and receives `temperature` (saved room
target), `room_temperature` (valid sensor mean), `boiler_water_temperature`,
`room_temperature_rate` (°C/min over up to ten minutes),
`heating_elapsed_minutes`, `heat_accumulation`, and `base_water_temperature`
(the existing calibration result). The other variables are also available to
the base calibration in dynamic mode.

The default adds room-error recovery and reduces output as the room warms and
heat accumulates. Accumulation is a proxy in °C·minutes, not measured energy:
positive water-minus-room temperature integrated during heating with a
30-minute exponential decay time constant. It decays across restarts; heating
duration and trends reset after observation gaps. Missing room or water
readings reset continuous heating duration; room sensor membership
changes reset the temperature trend. Accumulation is capped at 3000 °C·minutes.
Missing input or invalid formulas pause writes. Tune the default gains to the
heating system. This mode does not implement automatic thermostat cut-off or burner/pump control; those
remain the physical boiler's responsibility. `boiler_control_status` and its
history attributes expose the controller state.

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
- Inspect each virtual entity's `source_entities` state attribute for its
  configured source entity IDs in order (an empty list when no sources are set).
- Each referenced source gets a diagnostic sensor per virtual target, attached to
  the source's existing device when available (otherwise a standalone sensor).
  Its name shows the virtual target's display name and exact entity ID. Its
  value follows that target's current state, including unknown/unavailable.
  `virtual_entity_id` identifies the target, `virtual_entities` retains a
  one-item list for compatibility, and `source_entity_id` identifies the source.
  References are tracked per Virtual Layer config entry, including explicit
  attribute/template sources. Sensors update or disappear as references are edited or removed;
  source outages do not erase configured usage. Original devices and entities
  keep their metadata and ownership.
  Home Assistant startup and Virtual Layer reload automatically rebuild these
  links for all saved virtual entities: missing usage sensors are recreated,
  outdated reference lists and device links are refreshed, and repeated reloads
  do not create duplicates. Existing entities need no edit or manual save.
  Old count sensors migrate automatically on reload: the first target reuses
  the existing sensor and its user customizations; other targets get separate
  sensors. Renaming a target refreshes the default sensor name while keeping
  a user-customized usage-sensor name.
- Convert one supported single source to a different virtual entity type, such
  as a real switch exposed as a virtual fan
- Auto-generate useful helper templates when multiple source entities are
  selected
- Optional Home Assistant Jinja templates for custom state, availability, and
  attributes
- Fan, climate and humidifier off commands reapply source updates received
  during their actions, so fast replies are not lost. Without a new response,
  they retain their immediate local off state until the source updates.
- Periodic pull refresh for composite entities
- Light-to-percentage sensor helpers show 0% while a source light is off and
  its reported brightness percentage when on. Unknown/unavailable sources are
  excluded. For existing sensors, regenerate the brightness conversion helper
  in the edit flow to apply this behavior to the saved template.
- Light groups with two or more light sources dispatch default commands to
  each bulb in parallel. After a group command, the virtual light retains its
  requested power, brightness and color; delayed member reports update
  diagnostics and availability without replacing that target. Consecutive
  group commands are dispatched in order. Custom action sequences remain
  supported, including `optimistic: false` for source-authoritative behavior.
  After default group actions finish, response-delay/retry settings trigger
  device updates and bounded command retries for members that still differ
  from the requested power, brightness or color. New commands cancel old
  retries; successful members are not resent commands. When all members
  report the requested state, the pending check is cancelled immediately,
  including replies received before the service returns. A single-source light
  then immediately resumes following physical state changes. Transitions receive
  their requested duration before polling or retrying. The ignore-unresponsive option
  skips command retries to unknown/unavailable members. A device update request
  depends on the physical integration's polling support and does not guarantee
  that an offline bulb can be reached.
  Mixed RGB/colour-temperature groups expose the colour-temperature profile.
  HA converts Kelvin requests to HS/RGB/XY or calibrated RGBWW for each bulb;
  reconciliation checks the resulting native channels instead of requiring
  RGB-only bulbs to report Kelvin. Re-forwarded requests contain only the
  modern Kelvin descriptor, avoiding duplicate legacy mired fields rejected
  by HA. RGB input to a CT-only group is a white-temperature approximation,
  not full RGB colour reproduction. Matter cluster/unit conversion remains
  the responsibility of the installed bridge plugin.
- Korean and English UI translations
- Single-source lights with the default forwarding action also use the configured
  response delay and bounded retries. The requested state appears immediately
  while a slow bulb responds; transition duration is included before checking.
  Only mismatched bulbs receive retries, and a new command cancels older retries.
  After acknowledgement or retry exhaustion, the virtual light follows its
  source again. A zero response delay disables retries. Single-source custom
  actions share command ordering with default actions and are never retried;
  `optimistic: false` still follows source reports. Cancelled custom actions
  release the temporary state hold. Transitions are exposed when a light source
  advertises support, forwarded through HA services, and included in the initial
  reconciliation delay. On/off-only members are checked for power alone;
  hue comparisons account for the 0°/360° boundary to avoid needless retries.
- Humidifier target humidity defaults to 5% steps. Edit the humidity adjustment
  step Jinja input in the entity's native settings (for example `{{ 10 }}`).
  An advertised source step or an existing custom value remains authoritative.
- Climate and humidifier measured humidity is independent of target limits.
  Generated native-source humidity helpers ignore unavailable and invalid
  readings, average valid readings, and return unknown when none remain.
  Missing activity attributes also stay unknown instead of reusing a snapshot.
  Saved templates are retained until helper regeneration in the edit flow;
  review custom templates before choosing Force helper. Humidity commands send
  the same rounded target to actions that the virtual entity displays.
- Fans and air purifiers retain reported speed percentages in automatic presets.
  When a fan and a separate `number`/`input_number` are selected as sources,
  the selected number supplies speed in **every mode**, including Auto/Sleep
  and Manual/Favorite. Its percent, level or RPM value is normalized to 0–100%.
  If that value or its required scale is invalid/unavailable, the original fan's
  percentage or known-range RPM is used instead. Missing both leaves speed
  unknown while preserving the fan's on/off state. Off always displays 0%.
  Select the number that actually reports speed; a control that retains only a
  manual setpoint cannot supply an automatic-mode measurement.
  RPM telemetry is normalized when its range is known. Positive speed commands
  select an advertised Manual/Normal/Favorite/Favourite preset before adjusting the source;
  zero remains a stop command. Regenerate existing command helpers to apply
  this behavior, preserving custom actions with the helper policy as needed.
  Case, whitespace and `manual_mode`/`manual-mode` variants are recognized,
  along with 수동, 수동 모드, 일반, 일반 모드 and 즐겨찾기. The original
  advertised spelling is used for source commands; an already selected manual
  family is retained. Otherwise priority is Manual, Normal, then Favorite.
  Auto, Smart, Nature, Sleep, Silent, Eco, Pet, Turbo, Boost and named speed
  presets are never guessed to be manual controls. Sources without an advertised
  manual mode receive their normal percentage command (or a write to the
  explicitly selected speed number). This matches the distinct
  [VeSync manual/normal](https://github.com/home-assistant/core/blob/dev/homeassistant/components/vesync/fan.py)
  and [Xiaomi favorite](https://github.com/home-assistant/core/blob/dev/homeassistant/components/xiaomi_miio/fan.py)
  control families. No reading or known RPM range means unknown speed, not a
  fabricated value inferred from a preset name. Unavailable or invalid paired
  speed numbers abort before changing source power or mode.
- Sensor unit templates retain the last valid unit when a source returns an
  empty or unavailable unit. On reload, missing units recover from the entity's
  recorded statistics. Sensor forms offer normalized Home Assistant units;
  selecting one sets a fixed unit template without converting readings. The
  unit-change step can restore the sensor's recorded unit while leaving all
  historical statistics untouched.
- Generated sensor/number metadata helpers skip unavailable sources and empty
  or unknown units, including the literal `None`. Icons use sources with usable
  units, then unit and English/Korean quantity-name hints. Explicit unit suffixes
  in source names (for example `Room temperature (°C)`) supply a unit fallback;
  quantity names alone do not guess a measurement scale. Regenerate helpers in
  the entity edit flow to apply this behavior to previously saved templates.
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
- choose a compatible target entity type when exactly one source is selected
- customize the generated entity name, entity ID, domain, initial state, device
  metadata, and templates

After setup, use `Configure` on the Virtual Layer integration entry to:

- add a virtual entity
- edit an existing virtual entity
- delete one or more virtual entities
- manage virtual device metadata
- delete a virtual device and all of its entities
- finish without changes

Choose **Create virtual Device from a device** to mirror an existing appliance
such as a Samsung oven or washer in one operation. Select the source device,
review its entities, and enter the new Device name. A final review lists every
source and proposed virtual entity ID before anything is saved, with actions
to revise the selection or choose another device. Initial setup also offers
an optional source-device selector. Each selected entity retains its domain
and receives source-linked state, native-property, and command helpers. Device
copies use direct single-source helpers: an unpressed button remains usable,
motion clears immediately with its source, and heating-only climates keep their
source temperature range without automatic boiler conversion. These semantics
are retained by the automatic, keep-current, and force-helper edit policies
while the entity remains a single-source mirror in the same domain.
Only supported, enabled entities with a current HA state are offered; Virtual
Layer entities are excluded. The new Device has its own stable ID and copies
the manufacturer/model and software/hardware versions. Entity IDs are generated without collisions. You can
edit each resulting entity afterward. New source entities are not automatically
added later. A validation failure identifies the failing source and saves none
of the selected entities. Source membership/identity and ID availability are
checked again at final creation; concurrent changes to other configuration are
preserved. Disabled, unsupported, unregistered-state, and Virtual Layer entities
are not copied. Unknown/unavailable states do not prevent preparation when the
source has a registered state. Missing/unavailable sources disable the mirror;
unknown values also disable stateful mirrors, while an unpressed button or an
event without a timestamp remains available.

Adding, editing, or deleting an entity applies only the changed configuration
and its affected companion entities. Other virtual entities retain their live
state and source subscriptions without an integration reload.

Use `Reconfigure` to update the integration entry's main device name.

Entity type, new-Device and Dawarich connection choices follow the user's
Home Assistant display language (English or Korean), independently of the server
language. Dawarich connection validation identifies the invalid input field and
keeps the submitted settings available for correction and retry.

## Devices

Virtual Layer uses `Device` in the UI. Older internal/service fields may still
use `group_name` for backward compatibility, but the user-facing concept is a
device.

Device ID is the sole identity used to group entities into a Home Assistant
device. Device names are display metadata, so different devices may use the
same name. When creating or editing an entity, the UI also lets you set device
registry metadata:

- device ID
- manufacturer
- model
- software version
- hardware version
- serial number

If no device ID is provided, Virtual Layer generates a stable ID.

## Entities

Source diagnostic sensors on the virtual Device, including the configuration
summary, use names beginning with `[Source] - ` and IDs such as
`sensor.src_<parent_object_id>_info` and `sensor.src_<parent_object_id>_debug1`.
Existing source diagnostics migrate automatically on reload, retaining their
unique IDs and customized display names.

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

Default entity IDs use `domain.entity_name`, derived only from the virtual entity
name, converted to snake case, with common room/equipment/measurement phrases abbreviated
(for example, `sensor.room_lv_temp_sensor`). Abbreviations apply to
generated IDs only, before the length limit; saved IDs are not migrated.
Spaces, underscores, and hyphens are supported, and only whole words/phrases
are replaced. The Home Assistant domain prefix remains unchanged.

| Name | ID abbreviation |
| --- | --- |
| living room / dressing room / bathroom / bedroom | room_lv / room_dr / room_bt / room_bed |
| kitchen / laundry room / entrance / server room | room_k / room_ld / room_e / room_s |
| doorstep / hallway | area_d / area_h |
| camera / cctv / robot vacuum | cctv / cctv / rvcu |
| carbon dioxide / carbon monoxide / formaldehyde | co2 / co / h2ho |
| particulate matter / radon / smoke / volatile organic compound | pm / radon / smoke / voc |
| illumination / humidity / temperature / vibration | ill / humi / temp / vib |
| air conditioner / heating and air conditioning system | airc / hvac |
| lighting controller (also lightling controller) / presence | light / pres |
| indirect / bulb / ceiling light / powder / focused light | ind / bb / clight / pd / fclight |

The dictionary also covers additional rooms, lighting fixtures, appliances,
climate equipment, measurements, access devices, and networking. For example,
`Master Bedroom Ceiling Lamp` becomes `room_mbed_clight`, and
`Kitchen Refrigerator Power Consumption` becomes `room_k_frdg_pwr`.
See the [full abbreviation dictionary](docs/entity-id-abbreviations.md).

The Device name is not prepended. An unnamed form leaves the ID blank until a
virtual entity name is submitted. During creation, changing the name updates an
untouched suggested ID. An explicitly entered ID takes precedence. Existing IDs
survive entity/Device renames; clear the ID field to regenerate it from the
current virtual entity name. Source ID collisions receive a `_copy` suffix;
other occupied IDs are rejected for correction in the form.

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
delays, and templated action data are supported. Native command arguments are
validated before source actions run, including when `optimistic` is `false`.
Invalid selections, malformed dates, and invalid numeric inputs cannot trigger
an action before being rejected.

When a source explicitly removes all media input, sound-mode, or vacuum
fan-speed options, the virtual entity clears the previous selection and updates
its supported controls. A source that only supplies a current value without an
option list can still expose that value. Legacy saved sound-mode lists also
initialize the native media player's selection controls.

Structured configuration editors reject non-finite numbers, recursive values,
duplicate keys, and non-string mapping keys before saving. Invalid input leaves
the form open for correction and preserves the existing configuration.

Set `pull_interval` to a positive number of seconds to periodically refresh
source values and templates. Leave it empty or set it to `0` to update from
source entity state changes only.

Referenced Zigbee2MQTT devices also receive automatic read-only MQTT refreshes,
independently of `pull_interval`. Virtual Layer resolves their IEEE identity and
custom bridge topic from MQTT discovery, then checks the bridge's device
inventory for mains/DC power and readable live properties. Healthy devices are
queried at most once every 15 minutes. Unavailable sources use retries starting
at one minute, backing off to 30 minutes; a successful source state resets the
backoff on the next scan. A newly detected outage bypasses the healthy polling
delay. Across all virtual entities, requests are shared by
physical device and limited to one device per 30 seconds.

Battery devices, unsupported properties, disabled devices and offline bridges
are not polled. Missing discovery or inventory data safely disables polling
until it becomes available. Queries use non-retained `/get` messages and never
change power settings, publish fake availability, restart the bridge or update
firmware. Home Assistant updates the original entities from actual MQTT replies,
and the virtual entities follow those source updates. This helps recover stale
state but cannot repair a disconnected radio or wake a sleeping battery device.
Only devices referenced by Virtual Layer are included; subscriptions and retries
are removed when those references are unloaded.

On startup, persistent entities with a valid saved state have a three-minute
source recovery window. While a declared source is missing, `unknown`, or
`unavailable`, the virtual entity preserves its last good state, availability,
and template-derived properties. Source recovery is reflected immediately.
After 180 seconds, templates are reevaluated even if no source event arrives,
and their normal unknown/unavailable handling resumes. Source retries do not
extend this deadline, and editing entities does not start a new recovery window.

## Composite Entities

### Water-quality sensors

Create one Device containing a separate `sensor` for each water measurement.
In the native Jinja fields, set `state_class` to `{{ 'measurement' }}` and use:

| Measurement | Device class template | Native unit template |
| --- | --- | --- |
| pH | `{{ 'ph' }}` | `{{ none }}` |
| Conductivity (EC) | `{{ 'conductivity' }}` | `{{ 'μS/cm' }}` (or `mS/cm`, `S/cm`) |
| Water temperature | `{{ 'temperature' }}` | `{{ '°C' }}` |
| TDS | `{{ none }}` | Source unit, commonly `{{ 'ppm' }}` |
| Turbidity | `{{ none }}` | `{{ 'NTU' }}` |
| Dissolved oxygen | `{{ none }}` | `{{ 'mg/L' }}` |
| ORP | `{{ 'voltage' }}` | `{{ 'mV' }}` |

The unit dropdown includes `mg/L` and `NTU`; custom units remain available
through the template. EC spelling aliases such as `uS/cm` and `µS/cm` normalize
to Home Assistant's `μS/cm`. A manually configured conductivity sensor defaults
to `μS/cm`; always explicitly match the instrument's unit. Selecting a unit does
not rescale readings. The multi-source conversion flow converts compatible EC
units before aggregation, for example 500 μS/cm and 1.5 mS/cm average to
1000 μS/cm when the first source uses μS/cm. EC conversion follows live source
units and excludes negative readings, missing/unsupported units, and sources
whose device class no longer matches. The existing high-outlier filter still
applies to averages. Combine only the same measurement
from comparable sources. An arithmetic pH average is a sensor-reading average,
not a prediction of the pH of mixed liquids.

Invalid numeric values become unknown. Negative conductivity and incompatible
pH/EC units also become unknown; pH is not artificially limited to 0–14.
These checks do not infer whether the water is safe. TDS/EC conversion,
temperature compensation, calibration, and application-specific limits require
the instrument's documented parameters. No automatic conversion between ppm
and mg/L, or between EC and TDS, is assumed.

For an instrument specifying TDS = EC × 0.5, with EC already in μS/cm, the
sensor value template can be configured as follows. Replace the source and
factor with the instrument's actual settings; do not apply compensation twice.

```jinja
{% set ec = states('sensor.water_ec') %}
{{ (ec | float * 0.5) if is_number(ec) and (ec | float) >= 0 else none }}
```

For a documented linear temperature coefficient, the EC-at-25°C calculation
is `EC25 = EC / (1 + alpha * (temperature - 25))`. Use the coefficient in
fraction/°C, check both inputs with `is_number`, and require a positive
denominator. Keep an unconfigured or unavailable calculation unknown instead
of substituting a zero reading.

Source-unit matching uses an explicit spelling-alias dictionary shared by
sensor conversion and air-quality helpers. For example, `µg / m^3`, `ug/m3`,
and `μg/m³` match; `℃` and the common typo `celcius` become `°C`;
`kW·h` becomes `kWh`. Whitespace is normalized, including nonbreaking spaces.
Aliases preserve values; physical unit conversions remain a separate step.
Unknown spellings are retained, and SI prefix case remains significant
(`mg` is not `Mg`). Source entities are not modified.
The dictionary also covers power/energy, electrical units, pressure, mass,
distance/area/volume, flow, speed, duration, and data sizes/rates, including
English unit names, selected Korean names, and compatibility symbols such as
`㎏` and `㎖`. Examples: `litres` → `L`, `m3 / hr` → `m³/h`,
`lbs` → `lb`, and `Mbps` → `Mbit/s`. Bytes and bits, decimal and binary
prefixes, and milli/mega prefixes remain distinct. Ambiguous spellings such as
`KB` or `mbps` are not guessed.

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
- Multiple location sources follow a device with observed, plausible GPS
  movement, including when only one of a person's devices leaves home. Movement
  must exceed 10 m and the combined accuracy of the previous motion anchor and
  current fix. GPS reports worse than the larger of 300 m and the configured
  grouping distance, or jumps exceeding 350 m/s after accuracy allowance, are
  rejected. These are heuristics, not proof that a person carries a device.
- The selected moving device remains selected during stops. A device left on
  home Wi-Fi/BLE does not override confirmed off-site movement. A different
  device can take over after moving from near the selected device, or when the
  selected device no longer has a usable report. Ordinary battery updates do
  not count as movement. When a source supplies `last_seen` or `last_timestamp`,
  its measurement clock is used for freshness, ordering, movement and polygon
  `latest`/age rules. Epoch seconds (including numeric strings), ISO timestamps
  and datetime values are accepted; naive datetimes use HA's time zone. Invalid,
  future or backwards measurements cannot become fresh merely because HA
  received an attribute update. Sources without a measurement clock retain the
  HA timestamp fallback. `lat`/`lon` and `acc` aliases are supported as well.
- With no movement evidence, the helper retains the median/outlier fallback:
  a source more than 300 m from the median may be selected for 30 minutes as an
  `unconfirmed_outlier`. The first observation is never confirmed movement.
  `location_selection_reason`, `location_stale`, and
  `location_rejected_sources` explain the decision. A confirmed device with no
  usable report in the configured window (30 minutes by default) retains its
  last known position with `location_stale: true`, rather than implying a return
  home. HA's `last_reported` is used for report freshness, so identical reports
  keep a stationary device fresh when it has no explicit measurement clock.
  Without that clock, report freshness is not a GPS measurement age.
  Selected GPS accuracy is preserved; a median includes source spread in its
  uncertainty. Only the last accepted fix and movement anchor are saved for
  restart recovery; the bounded working path is not exposed as travel history.

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

Adaptive location helpers and adaptive polygon trackers additionally expose
`location_last_seen`, `location_speed_m_s` and `location_bearing` (degrees
clockwise from north). Speed and bearing use two accepted fixes from the same
selected source, at least five seconds apart. Overlapping accuracy circles
yield zero speed and no bearing. A new stationary measurement can clear speed;
a repeated old measurement cannot refresh it. Motion estimates expire after
five minutes (or the shorter source freshness window), clear on rejected or
stale data, and are not reconstructed from a single restored fix. Median/radio
positions have no inferred speed. These are GPS estimates, not calibrated speed
measurements. The timestamp and alias handling were informed by
[Composite Tracker](https://github.com/pnbruckner/ha-composite-tracker/), while
Virtual Layer retains its UI-only configuration and carried-device selection.

## Wi-Fi and AB Gateway Presence

The entity creation source screen has an **Entity creation type** selector:
choose **Dawarich location tracker**, **Wi-Fi presence tracker**, or **BLE presence
tracker** to open the corresponding enabled tracker settings directly. This is
available during initial setup and **Add Entity** in the integration options.
Wi-Fi source selections prefill connection inputs; Dawarich/BLE selections can
add existing GPS trackers. Leave sources empty to configure a standalone tracker.
Complete **Tracker connection and presence settings** first, then set its name,
entity ID, and target Device in the common entity editor. Each tracker is a normal
managed entity: use **Edit Entity** to change connection settings or **Delete
Entities** to remove it. Multiple trackers may share the same Device.
Validation errors retain the selected creation type, sources, and target Device;
Wi-Fi/BLE errors point to the input that needs correction. Explicit GPS sources
also participate in Dawarich aggregation when they were offline during setup.

Selecting or editing a `device_tracker` opens a separate **Tracker connection and
presence settings** step before the common entity form. Enable **Wi-Fi / BLE
presence tracking** there. For a standalone presence tracker, leave the initial source
selection empty. For a composite tracker, also select your GPS source trackers.

- **Wi-Fi entities:** select an existing router `device_tracker`, connection
  `binary_sensor`, or SSID `sensor` for the specific device. For SSID sensors,
  enter the exact, case-sensitive home SSIDs, one per line. This uses your
  existing router/phone integration; no Wi-Fi password or network scan is needed.
- **BLE addresses:** enter stable beacon/device MAC addresses, one per line.
  Install and configure [AB Gateway](https://github.com/AprilBrother/component-ab-gateway)
  separately. It publishes advertisements through Home Assistant's Bluetooth
  scanner API, not device-tracker entities. Set scanner sources to `ab_gateway`
  for the upstream default; these are HA scanner IDs, not MQTT topics or gateway
  MAC addresses. Upstream advertisements from multiple physical gateways may
  share this source ID. Rotating private device addresses require another
  integration that resolves them to a stable identity.
- **BLE RSSI and timeout:** choose the minimum signal strength (default −90 dBm)
  and absence timeout (default 120 seconds). Every five seconds, Virtual Layer
  checks the selected scanners' real advertisement timestamps. Reading cached
  data or receiving weak packets does not extend the last strong observation.

Any connected/nearby source establishes home presence; all disconnected/expired
sources establish absence. Missing sources or offline scanners produce unknown
when no positive evidence remains. Home coordinates use the configured home
zone or HA home location, not a measured radio position. Confirmed movement of
a carried GPS device still wins over devices left at home. Wi-Fi connections
remain valid until their source changes state, including becoming unavailable.
The settings reopen on edit and can be disabled without retaining hidden inputs.

## Polygon Zones

The same Wi-Fi/BLE inputs can supply home coordinates to polygon tracking.
RSSI alone does not provide calibrated distance or room triangulation.
For room/entrance polygon positioning,
configure at least three non-collinear ESPresense distance anchors. Their stale
positions are reevaluated at most every five seconds, or sooner for shorter
configured anchor lifetimes (with a one-second minimum refresh interval).

A virtual `device_tracker` can combine multiple source trackers and resolve its
GPS position against named GeoJSON polygons. Multiple documents can now be
[managed in one GeoJson Device Group](docs/GEOJSON_AREAS.md), reused by ordinary
trackers and Presence Fusion, and selected as a Presence Fusion Home boundary.
Choose **GeoJson Device Group** when adding Virtual Layer. Only one such group
can exist per Home Assistant; each registered document creates its own Device
with zone names, bounds, estimated area, data size, status and an SVG map.
Configure it entirely in the
Add/Edit Virtual Entity form:

- **Source entities**: one or more `device_tracker` entities
- **Polygon GeoJSON**: an inline Feature or FeatureCollection
- **Polygon files or URLs**: one local path or HTTP(S) URL per line
- **Person**: the optional `person` represented by the combined tracker
- **Tracker selection strategy**: `adaptive`, `majority`, `priority`, `latest`, or `median`
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

Choose **Adaptive movement tracking** for multiple devices belonging to one
person. It uses the same movement and stop-retention policy as the location
helper before resolving polygon zones. Existing strategies keep their original
meaning and the default remains `majority`; a majority of devices left at home
will still win in that mode. Explicit `dominant` rules and triangulated
ESPresense positions retain their precedence. Per-source exclusion rules also
apply to adaptive tracking.

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

## Dawarich Location Source

Create or edit a `device_tracker` from the integration UI and use the **Dawarich**
section in the separate **Tracker connection and presence settings** step.
When creating a Dawarich tracker, an existing
[Dawarich custom-component](https://github.com/AlbinLind/dawarich-home-assistant)
connection supplies its server (`host` and `ssl`) and API key. If several
connections exist, choose one or enter details manually. Values are editable
copies; editing this tracker neither changes the original integration nor
overwrites the tracker's saved settings from it. Dawarich TLS certificate
verification is always disabled for connection tests and runtime reads. These
connection controls are absent from the final common add/edit form.

Enable Dawarich, enter the instance base URL (without `/api/v1`)
and API key, and select Bearer or query authentication. You can configure the
poll interval (15–3600 seconds), history limit (1–100 points), and optionally
test authentication and a usable location before saving. Disable Dawarich and
save to remove its saved configuration. The masked API key remains stored in
the config entry; it is excluded from generated information entities and
runtime error messages.

With both family fields empty, the tracker reads the API key owner's points.
For family sharing, enter the exact Dawarich member email or user ID. The
optional legacy Person selector matches its display name to a returned family
name or email; the explicit member field takes precedence. Missing or ambiguous
matches produce an error instead of selecting another person's location.
Family sharing must be enabled in Dawarich. The family API supplies current
locations, not other members' complete history or visits.

Dawarich can be the only location source, including for polygon zones, or feed
the existing location helper alongside local trackers. Source freshness uses
the measurement timestamp, so polling an old point does not simulate movement
or make it fresh. Out-of-order points cannot roll the tracker back. Invalid
points are skipped; a failed request retains the last location and marks the
Dawarich feed stale. Concurrent polls are skipped and unloading cancels an
active request. Native location templates do not overwrite the selected
Dawarich/helper/polygon position.

Attributes include `dawarich_point`, newest-first `dawarich_history`,
`dawarich_point_time` (measurement time), `dawarich_last_updated` (successful
poll time), `dawarich_stale`, and credential-safe `dawarich_error` codes. Own-account
mode also retrieves the latest visit within 30 days of the latest point;
`dawarich_visit_error` reports visit failures independently of location updates.
The history attribute is bounded diagnostic data, not a full route archive.
This integration only reads the [Dawarich API](https://dawarich.app/docs/api/dawarich-api/);
it does not upload or modify locations.

## Cameras

For a source camera registered by the Frigate custom integration, an empty
**Native values → H.264 Stream Source** template is prefilled from Frigate's
entity/device metadata and RTSP configuration, with `video=h264&audio=all`.
The camera's original Frigate name survives Home Assistant entity renames.
The loaded Frigate RTSP URL or configured RTSP template takes precedence;
otherwise the Frigate server host and port 8554 are used, with the configured
go2rtc/live stream name when available. A separate go2rtc hostname is used only
when Frigate's RTSP settings specify it. Non-Frigate sources are not changed.
Existing custom templates and legacy URLs remain authoritative; blank fields
are filled on create/edit and save. The filter selects H.264 if available and
does not create or transcode an H.264 track.

For a robot vacuum map in Apple Home, select one `image.*` source, choose
**Camera** as the target entity type, and save it on the desired Device.
The source must provide a raster image (PNG/JPEG/WebP); SVG polygon maps are
not supported by this conversion. Native values → Source Entity can also use
`{{ 'image.vacuum_map' }}` on an existing virtual camera.

The camera fits the entire image onto a fixed 1280 × 720 JPEG canvas, preserving
its aspect ratio with white padding and applying EXIF orientation. This avoids
odd-dimension H.264 failures and resolution changes during a stream. It checks
for updates once per second by default while viewed (editable with **Frame
interval**) and repeats unchanged frames as MJPEG even during slow source
requests. Concurrent requests share an in-progress image fetch. HomeKit
Bridge's default FFmpeg encoder converts this feed to H.264 on demand; keep
the default video codec, because `copy` cannot convert MJPEG to H.264. Pair
the camera as an individual HomeKit accessory using the UI steps below.
Leave this camera unchecked on HomeKit's **Cameras that support native H.264
streams** screen; that option selects `copy` and bypasses required encoding.
The map and robot position update only as often as the original image does.
The Home Assistant URL must be reachable from its own FFmpeg process.
Temporary source failures retain the last valid image. An explicit image path
or stream URL overrides the corresponding generated media.

Create a camera alias by selecting one camera as the original entity. The UI
automatically selects the `camera` domain, copies its state through a template,
and sets the camera-specific `source_entity` option. The virtual camera proxies
the source image and stream while keeping its own entity name, id, device, and
other virtual-layer settings.

Camera creation also supports dedicated **Native values** inputs. A camera can
use a local image, an H.264 stream URL, or both without an original entity.
Standard properties should use these inputs; **Domain options JSON** remains
available for integration-specific extensions:

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

Apple Home cameras must be paired separately. Home Assistant intentionally
excludes every camera from a normal HomeKit bridge, even when that bridge's
entity filter includes the camera. In **Settings > Devices & services > Add
integration > HomeKit Bridge**, select the virtual camera and create an
**Accessory mode** entry, then pair the new QR/PIN in Apple Home. Create one
HomeKit accessory entry per camera. An existing normal bridge does not begin
advertising a newly created camera automatically.

### Matter Bridge

For thermostat modes, setpoint attributes, fan controls, and sensor metadata,
see the [Matterbridge 3.10.8 / matterbridge-hass 1.5.0 compatibility audit](docs/matterbridge-compatibility.md).

Matter 1.5 defines a camera device type, but its live-video transport is a
WebRTC camera session rather than Home Assistant's H.264/RTSP
`stream_source`. Virtual Layer therefore cannot turn a camera into a Matter
camera by publishing more state attributes. The bridge must implement the
Matter camera clusters, WebRTC negotiation, and media relay itself.

As of September 2026, the widely used `matterbridge-hass` plugin does not
support Home Assistant `camera` entities. The actively maintained Home
Assistant Matter Hub fork lists its WebRTC camera feature as experimental and
SmartThings-only, not Apple Home. Use the separate HomeKit accessory-mode
entry above for Apple Home cameras. If a Matter bridge later adds verified
Apple Home camera support, it must be configured with the virtual camera's
own H.264 source; the camera already exposes the normal Home Assistant stream
capability for that purpose.

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

Water-heater measurements retain their actual temperature even outside the
target setting range; target temperatures still obey that range. Operation
values such as `Eco` and `HEAT` retain their original case through templates
and restoration. Cover and valve position templates apply before movement
flags, so field order does not change the reported direction. An authoritative
position report also publishes a stop when the position itself is unchanged.
Lock state reports cancel an older simulated completion timer and do not run
the optional jam simulation; lock commands retain that simulation behavior.

New air-quality entities default to **Automatic**, skipping the rule wizard.
The config flow uses `mdi:air-filter` as the default icon for Air Quality
entities and sensor/number/binary-sensor names containing air-quality terms
(for example AQI, PM2.5, CO₂, TVOC, 공기질, or 미세먼지). Generated source
icon helpers use the same icon. Explicit icons and custom icon templates remain
editable and are preserved under the normal helper policy.
Name matching also recognizes toluene, xylene, ethylbenzene, styrene, acetone,
acetaldehyde, acrolein, methanol, methane, propane, butane, chlorine, hydrogen
chloride/cyanide/fluoride, sulfur trioxide, NOx, SOx, and BTEX, including Korean
names and common spelling variants. Unicode subscripts, full-width characters,
and separator variants normalize consistently. These additional names share the
default icon; recognition alone does not add automatic grading thresholds or
concentration conversions. Ambiguous formulas such as C8H10 are not inferred.
Source categories (including matterbridge-hass aliases) and explicit AQI values
are converted into categorical strings for the separate bridge sensor. Multiple
known sources use the worst category. Unavailable sources are skipped; no valid
category means unknown. Recognized concentration sources now receive separate
starter measurement profiles automatically (listed below). Each pollutant is
classified in its own unit, then the worst available category is used; unlike
concentrations are never averaged. Source units must be present and compatible.
Unsupported measurements remain unknown. These are configurable display bands,
not a calculated official AQI or a health/safety certification. Generated Jinja
contains the applied thresholds and can be edited. Existing automatic entries
acquire profiles when edited and saved; custom templates are preserved.
In automatic mode, enable **Create one air-quality sensor per source** on the
entity form to additionally generate one categorical sensor per selected source.
Each evaluates only its own source, not the aggregate. IDs end in `_air_quality`
and include the parent object ID and source domain/object ID; source ordering
does not change their identity. They share the virtual Device and are removed
when the option is disabled or the parent is deleted. Original sensors remain.
These are generated companions, not separately editable configuration entries;
their thresholds come from the saved automatic profiles. For independent manual
rules, create a separate Air Quality entity with that single source instead.
The integration does not change Matterbridge endpoint grouping or regex settings.

The official [Air quality integration](https://www.home-assistant.io/integrations/air_quality/)
also provides pollutant threshold and smoke/gas/CO detection automation building
blocks. These do not imply that a cleared smoke alarm is a measured good-air
grade. Preserve native numeric measurement sensors and binary alarm sensors for
those automations; generated categorical companions serve a separate purpose.
All named quantities in the air-quality measurement selector have editable
starter profiles, including PM4 and nitrous oxide (`nitrous_oxide`, N₂O).
These are instantaneous display categories, not a health or exposure assessment:
`good` does not certify safe air. PM4 uses explicitly labelled PM2.5-shaped proxy
boundaries of 9, 35.4, 55.4, 125.4 and 225.4 μg/m³. N₂O uses local display
boundaries of 1000, 2000, 4000, 8000 and 16000 μg/m³, unrelated to occupational
exposure limits. Neither profile is an official pollutant-specific health scale.
Mass-unit equivalents are converted automatically; gas mass-to-ppm conversion is
not guessed. Unsupported units, missing readings and unidentified quantities
remain unknown. Existing custom thresholds are retained. Existing managed
companions without a custom recipe pick up these defaults on reload.
Generated `_aqi` companions also complete missing automatic measurement rules
when source units/device classes become available after startup (including
metadata supplied by native templates). This runtime repair does not rewrite
stored configuration or existing measurement overrides. Sources with genuinely
missing/incompatible units are not assumed to use ppm. Without any previously
valid grade the result remains unknown. Once a valid grade exists, air_quality
entities and all generated categorical companions retain it through source
outages, invalid results and reloads, regardless of the legacy persistence flag.
`air_quality_stale: true` marks a retained result, not a current air measurement;
`air_quality_last_valid_at` preserves its last valid classification time.
`air_quality_partial` and `air_quality_missing_sources` identify partial coverage.
Available sources continue to be classified with the recipe's missing-value
policy (normally skip); cached stale grades do not outvote live sources.
Fresh valid results replace the retained grade and clear the stale flag. A bridge
inherits upstream partial coverage and retained timestamps. Template failures
retain the previous grade with `air_quality_fallback_reason: template_error`;
other reasons include `sources_unavailable`, `availability_false`, and
`invalid_result`. Reevaluating unchanged inputs does not advance the last-valid
timestamp. Late metadata can repair owned automatic helpers on explicit
air_quality entities and per-source companions too; customized formulas are
not replaced. A bridge
can also recover an unconfigured numeric companion when its composite parent
lacks a unit: if all original sensors identify the same pollutant and declare
the same normalized unit, that unit is used to classify the **composite value**.
The parent's reducer is never replaced by worst-of-originals aggregation.
`air_quality_evaluation_basis: combined_inherited_unit` and
`air_quality_inferred_units` identify this metadata-only inheritance. Conflicting,
missing, or incompatible units do not trigger a different aggregation policy;
the last valid grade is retained as stale, or remains unknown without history.
An explicit parent unit is authoritative. Once it is supplied, `configured`
evaluation resumes. Explicit measurement
overrides are never bypassed, and the original sensors/parent metadata are not
rewritten. This includes CO concentration sensors (`carbon_monoxide`, ppm/ppb),
not binary CO alarms. A bridge
that ignores these diagnostic attributes may display an old grade without a
freshness warning; do not treat a retained good grade as proof of safe air.
The [Sensor entity contract](https://developers.home-assistant.io/docs/core/entity/sensor/)
distinguishes numeric AQI from textual categories, date from timestamp/uptime,
and numeric state classes from enum states. Virtual Layer preserves these types
and clears invalid numeric template values to unknown instead of publishing NaN
or infinity. Display precision must be a nonnegative integer.

UI-managed virtual measurement sensors also receive an automatic companion on
load: `air_quality.<measurement_object_id>_aqi`, named `<measurement name> Air Quality`.
Each automatic companion also has a `sensor.<measurement_object_id>_aqim`
Matterbridge compatibility mirror. It forwards the same textual grade and
stale/partial status, has no numeric AQI device class or concentration unit,
and is removed with its parent. Match this sensor ID in the Matterbridge
Air Quality Regex; the integration does not change bridge settings.
Formaldehyde names also recognize CH2O/CH₂O. A classless, generically named
composite can inherit an unambiguous pollutant identity from its sources;
declared parent device classes are never replaced.
Legacy automatically generated `sensor.*_aqi` registry entries migrate to this
domain on reload; original measurement sensors and independently configured
entities are preserved. Generated identity and registry name/icon overrides are
retained. Automations referencing the old sensor ID need updating; Recorder
history is not moved to the new ID automatically.

When editing a sensor's configured unit (including a native unit template), a
**Handle existing values after a unit change** step appears before saving.
Keep history is the default. With existing Recorder statistics and a resolvable
unit, you can explicitly confirm either correcting the statistics unit label
without changing numbers, or mathematically converting the statistics. These
operations affect all short- and long-term statistics for that entity only.
They do not rewrite raw state history, current/restored readings, templates, or
thresholds. If different units were mixed within the recorded period, do not
relabel the whole series; it needs a separate time-range repair. Concurrent
configuration/metadata changes are rejected. An entity ID change or an
unresolved dynamic template only permits keeping history in this step.

Automatic companions
apply to both newly created and existing Virtual Layer sensors with a
recognized air-quality device class or unambiguous ID/name. Original and unrelated
Home Assistant sensors are not modified. Companions share the parent's Device,
follow its identity/name changes and disappear when the parent is deleted or no
longer represents an air-quality measurement. They are runtime-derived, not
independent options entries, and never recursively generate more companions.
All explicitly configured IDs are reserved first; collisions receive a different
generated ID instead of overwriting a user entity. Their states are category
strings, **not numeric AQI**; no `aqi` device class or concentration unit is set.
Recognized but unsupported units produce `unknown`, not a fabricated good grade.
The applied recipe is visible in the companion's `air_quality_logic` attribute.
Virtual binary CO, smoke, and gas alarms also receive a managed `_aqi` sensor:
`off` means `good`, and `on` means `poor`. No concentration or numeric AQI is
invented. Classless alarms can be recognized by an unambiguous pollutant or
smoke/gas name; unrelated door/motion sensors are not automatically included.
Binary sources explicitly selected in an automatic Air Quality recipe use the
same two grades, with the worst valid grade winning for multiple sources.
Missing responses retain the last valid grade through the existing stale-state
fallback; they are never treated as `off`. These display grades do not replace
the original safety alarm, which remains unchanged.
Equivalent unit spellings (`mg/m3`, `mg/m^3`, `mg/m³`, `ug/m3`, `µg/m3`,
`μg/m³`, and `Bq/m3`) and surrounding whitespace are normalized consistently
in recipes, flow validation, concentration helpers, and live category templates.
The source sensor's unit is not rewritten. SI prefix case remains significant:
`Mg` is not `mg`. A bare `m³`, an area unit, or an unsupported concentration
unit remains unknown instead of being guessed into a healthy category.
Existing explicit Air Quality entities and their `_air_quality` IDs are retained.
Both sensor and Air Quality forms expose **Configure air quality per source**.
The original measurement sensor is never converted or overwritten. Choose the
combined virtual measurement, each selected source, or the deduplicated leaf
sources of configured Virtual Layer composites. Combined measurements retain
the sensor's configured reducer; independent sources use the worst valid grade.
Leaf expansion is explicit and saved as a snapshot; reopen the scope step after
changing the upstream topology. The selected composite roots are retained
separately from the resolved leaves, so submitting that step expands the current
dependencies again while retaining profiles for unchanged leaves. Older recipes
that stored only leaves keep their snapshot; select the intended composites once
to enable this refresh behavior. Cyclic or excessive graphs are rejected.

Select a source by name and entity ID to edit its unit, optional attribute, five
boundaries, six grade assignments, and calibration formula. Other source profiles
remain intact. Reset affects only the selected profile. Unsupported measurements
require explicit compatible units and boundaries rather than invented defaults.
Missing-source policy can skip invalid values or make the overall grade unknown.
Finish the source editor and save the final entity form to apply. Sensor recipes
configure the managed `_aqi` companion without changing the parent's templates;
combined-result profiles follow parent entity-ID changes. Existing custom Jinja
remains governed by the selected helper update policy.
The primary automatic companion uses the `air_quality` domain. For
matterbridge-hass 1.5.0's sensor-based mapping, use its `sensor.*_aqim` mirror.
Explicit Air Quality entities retain their `sensor.*_air_quality` companion.
The automatic AQI conversion mirrors matterbridge-hass 1.5.0's 0–500 linear
mapping (`floor(AQI / 100 + 0.5)` selects one of six categories), not a health
standard or a concentration-to-AQI formula. Values outside 0–500 are unknown.
See the [upstream converter](https://github.com/Luligu/matterbridge-hass/blob/1.5.0/src/converters.ts).
Choose **Customize air-quality rules** on the final entity form to opt into
thresholds, formulas, fixed grades or custom Jinja. Existing custom rules are
preserved. Automatic mode does not modify the original source sensor or the
external Matterbridge configuration; the Air Quality Regex setup below still
applies.

Keep existing PM2.5 and other measurement entities: selecting Air Quality while
editing a sensor/number directly prepares a separate categorical
entity on the same Device, using the original virtual measurement as its source.
The original configuration and identity always remain unchanged. There is no
confirmation step or replacement option; persistence happens only at final save.
You can also use **Add entity** directly.
Measurement setup uses one screen for sources, units and thresholds, followed
by preview and the final template editor. Quantity, category mappings, missing
value policies and calibration are grouped in a collapsed advanced section.
Saved advanced values are retained when reopening the form, submitting only
changed fields, or correcting validation errors. An omitted field keeps its
previous value; an explicitly invalid value is rejected rather than silently
replaced with a default. Preview offers
continue, refresh, or return to that same settings screen.
When optional measurement setup opens, missing thresholds and units are prefilled
for PM1.0, PM2.5, PM10, CO, NO2, AQI, radon, formaldehyde, CO2, ozone,
SO2, NO and VOC (mass or parts). PM1 is never treated as PM0.1 or PM2.5.
Additional local display bands are PM1 (μg/m³): 5/10/20/35/55;
ozone (ppb): 20/40/60/80/100; SO2 and NO (ppb): 20/40/80/160/320;
VOC defaults use mg/m³: 0.2/0.3/0.5/0.75/0.95. These are editable local conventions,
not official exposure limits. Other gas mass and ppm/ppb are not interchangeable;
use explicit manual rules for units outside a profile's supported family.
Benzene (C6H6/C₆H₆), ammonia (NH3/NH₃) and hydrogen sulfide (H2S/H₂S)
are integration-specific quantities, not new HA device classes or native Matter
concentration clusters. Their local display bands are benzene (μg/m³):
1/2/5/10/20; ammonia (ppm): 0.1/0.2/0.5/1/2; hydrogen sulfide (ppm):
0.005/0.01/0.02/0.05/0.1. These are configurable display conventions,
not safety limits or replacements for dedicated gas alarms. Their grades can
use the same automatic and per-source categorical sensor helpers.
TVOC/eTVOC default helpers convert ppb to mg/m³ using **1 ppb = 0.0045 mg/m³**
(100 ppb = 0.45 mg/m³), then aggregate compatible sources. Reverse conversion
uses ppb = mg/m³ / 0.0045; ppm and μg/m³ prefixes are also supported.
The output sensor uses the VOC mass device class. This is a mixture approximation
based on [Sensirion's TVOC guidance](https://sensirion.com/media/documents/4B4D0E67/6520038C/GAS_AN_SGP4x_BuildingStandards_D1_1.pdf),
not a universal gas constant or sensor-specific calibration. Generated Jinja
helpers remain editable; existing saved units, thresholds and custom helpers
are preserved. eTVOC remains an estimated reading, not a chemically specific measurement.
Unitless VOC indices are not treated as concentrations. O3/O₃, CO and NO2/NO₂
aliases are recognized. Atmospheric pressure and PIR stay native pressure and
motion entities; neither contributes an air-pollution grade.
Source `device_class` takes precedence over token-based entity-ID/friendly-name
hints (including Korean names). Conflicting hints, mixed pollutants, explicit
attribute inputs and unsupported profiles do not guess thresholds. Existing
values, including rejected edits, remain untouched. Names identify a proposed
profile, not trustworthy physical units: validate source metadata before saving.
Except for the explicit VOC approximation, mass and gas-ratio prefixes are converted only within compatible unit families;
radon additionally supports Bq/m³ and pCi/L (1 pCi/L = 37 Bq/m³).
PM/CO/NO2 presets borrow [EPA concentration breakpoints](https://aqs.epa.gov/aqsweb/documents/codetables/aqi_breakpoints.html)
but do not perform required time averaging or calculate official AQI. Radon
uses local display boundaries 50/75/100/125/148 Bq/m³: below 50 is `good`,
50–<75 `fair`, 75–<100 `moderate`, 100–<125 `poor`, 125–<148 `very_poor`,
and 148 or above `extremely_poor`. These are not official six-level health
categories. EPA's [radon guidance](https://www.epa.gov/radiation/radionuclide-basics-radon)
does not define these six bands. Formaldehyde, CO2 and VOC presets are explicitly
local editable display bands, not safety or exposure limits. CO₂ defaults are
below 600 ppm `good`, 600–<800 `fair`, 800–<1100 `moderate`,
1100–<1400 `poor`, 1400–<2000 `very_poor`, and 2000 or above `extremely_poor`.
Existing user thresholds and comparison rules are preserved. No source sensor
is changed by this prefill. Unrecognized sources still require user-defined
thresholds rather than fabricating a pollutant profile.
Floor area alone does not scale these concentration boundaries. Indoor display
defaults for HCHO are 0.02/0.04/0.06/0.08/0.10 mg/m³ and for TVOC mass are
200/300/500/750/950 μg/m³; equality enters the worse band. TVOC's lower bands
are more permissive than the previous profile while its upper bands, and HCHO's
upper bands, are stricter. These are local display choices, not official grades:
the [WHO HCHO reference](https://www.who.int/teams/environment-climate-change-and-health/air-quality-and-health/health-impacts/types-of-pollutants)
uses a 30-minute average, which this instantaneous helper does not calculate.
[UBA's TVOC advice](https://www.umweltbundesamt.de/en/topics/health/commissions-working-groups/german-committee-on-indoor-air-guide-values)
identifies concentrations above 950 μg/m³ as a precautionary concern and says TVOC
alone cannot assess health risk. The lower four TVOC boundaries are local choices,
not UBA categories. New TVOC/eTVOC profiles in ppb use the mass profile through
the above approximation; existing explicit profiles retain their thresholds. CO/NO₂ and other
gas profiles are not relaxed based on home size; these helpers never replace
certified smoke/CO alarms. Radon and CO₂ retain their user-requested defaults.
Generated PM2.5/PM10 helpers can read matching numeric sensor states, including
mass-unit conversion. Unmeasured concentrations are unknown (`None`), not zero.
Old customized templates are not silently rewritten; review them when upgrading.

The input step also selects the measured quantity (PM1/PM2.5/PM10, AQI,
CO₂/CO/O₃/NO₂/NO/SO₂, or VOC mass/volume ratio). Known quantities are detected
from source device classes; different pollutants cannot share one numeric
threshold rule. Classify pollutants separately and combine their categories.
PM requires mass units, AQI is unitless, and VOC supports mass and ppm/ppb with the above approximation.
Explicit quantities also guard against changed source device classes at runtime.
Attribute inputs rely on the user's declared quantity and unit.

Legacy concentration attributes use compatible mass-unit helpers, including gas
sensor states in μg/m³ or mg/m³. Gas ppm/ppb can be used directly in the rule
steps, but are not guessed into mass concentration attributes. PM0.1 is not PM1,
and nitrogen oxide (N₂O) is not NO or NO₂: these legacy fields use their exact
named source attributes. Invalid category outputs clear to unknown; invalid,
negative, boolean, or non-finite concentrations clear to unmeasured rather than
leaving a previous reading in place.

Choose source categories,
measurement thresholds, a fixed category, or custom Jinja. Measurement mode
requires five increasing upper boundaries and six category assignments; these
are user-defined rules, not a built-in health or regulatory standard. Select
the input entities, optional attribute, unit, first/worst-source aggregation,
and missing-value policy. Compatible mass units and ppm/ppb are converted;
incompatible units yield an unknown input. Attribute units are explicitly
declared because the source's primary state unit may describe another value.
The resulting `air_quality` native template remains editable. Automatic helper
updates preserve customized templates; force-helper regenerates them, while
keep-current retains them. This supplies an overall category in addition to
concentration values; configuring a Matter bridge remains a separate task.

Optional measurement configuration follows **Combined settings → Preview →
Template editor**. Source-category mode skips numeric
calculation and thresholds; fixed mode asks only for a category before preview.
Custom Jinja continues directly to the template editor. Preview uses current
Home Assistant values without saving or changing entity states. You can refresh
it or return to sources, calculation, or thresholds with your inputs intact.
It evaluates the proposed rules, not a custom template retained by helper policy
and not the actual Matter endpoint; inspect retained Jinja in the final editor.

Processing order is unit conversion, per-source
calibration `y = a*x*x + b*x + c`, numeric aggregation, then interval lookup.
Defaults `a=0, b=1, c=0` leave values unchanged. Choose per-source classification
(then first/worst category), or classify the mean, median, minimum, or maximum
of the calibrated measurements. These combine current readings, not historical
time averages; only combine the same measured quantity. Negative or non-finite
inputs/results follow the missing-value policy.

The calculation step also selects which interval includes exact boundary values:
upper-inclusive keeps equality in the lower interval; lower-inclusive moves it
to the next interval. All five thresholds and six category assignments remain
editable on reopening, including repeated or reversed categories. For example,
`a=0, b=1.1, c=-2` applies a linear correction, while `a=0.01, b=1, c=0`
applies a quadratic correction. These are mathematical examples, not recommended
air-quality calibration standards. Use custom Jinja for other formulas.

### Live media-player information

Media-player helpers read playback state, title, app, volume, duration, position,
and the position timestamp dynamically, even when the sources were off during
creation. Playing, paused, and buffering sources take priority over other
sources; positions and volumes are not averaged. Missing playback measurements
are cleared instead of retaining a previous session's values.

After updating and restarting Home Assistant, edit existing media players and
regenerate their helpers to replace previously saved empty or averaging
templates. Automatic mode preserves custom fields; force-helper mode replaces
custom templates too. Keep-current mode intentionally retains old templates.
Only information reported by the source can be displayed.

### MatterBridge 3.10.8 / matterbridge-hass 1.5.0

#### Media players in Apple Home

MatterBridge maps `media_player` entities to Matter's Basic Video Player and
Keypad Input clusters. Apple Home currently shows that direct endpoint as
**Unsupported**. Use its **Virtual Control Label** fallback: add the selected
label to the virtual media player in Home Assistant, then re-pair/reload
MatterBridge and ignore the direct unsupported endpoint in Apple Home. It exposes
Apple-Home-compatible command switches for power, playback, previous/next,
mute, and volume up/down. This is command-only; it cannot provide an Apple Home
Now Playing tile, media browsing, or AirPlay routing.

#### Battery readings in Apple Home

Keep a battery sensor attached to its parent Home Assistant Device; do not
expose or split it as an individual MatterBridge entity. A standalone Power
Source has no Apple Home accessory UI and may appear as **Unsupported**.
Virtual Layer's generated vacuum battery sensors already publish the required
numeric percentage, `battery` device class, `measurement` state class, `%` unit,
and parent Device relationship. A direct unsupported media player on the same
Device can still make the combined Apple Home Device unusable.

Each virtual air-quality entity also generates a categorical
`sensor.<parent_object_id>_air_quality` on the same Device. Its state follows the
parent's overall category; concentration readings are not converted to enum
numbers. The sensor has no numeric device class, state class, or unit.
In the plugin's **Air Quality Regex** setting, match the actual generated ID,
for example `^sensor\.living_air_quality$`. The plugin's default empty regex
does not enable this path. ID collisions may change the generated ID: check
Home Assistant before entering the regex. Keep the category and concentration
sensors on the same Device and avoid splitting them into separate endpoints.

The plugin maps category strings to Matter AirQuality, while PM2.5/PM10/CO₂
measurement sensors populate separate concentration clusters. Do not send
numeric Matter enum values 0–6 as AQI: version 1.5.0 interprets numbers as a
0–500 index. Adding arbitrary `air_quality` attributes to concentration sensors
does not activate its category conversion.

Known upstream limitation: the 1.5.0 converter returns no update for `unknown`,
so a previously valid Matter category can remain stale. Virtual Layer publishes
`unknown` honestly; it does not substitute a healthy category or mark the whole
shared Device unreachable. Correct Matter Unknown=0 propagation requires a
plugin-side fix. Actual Apple Home behavior still requires end-to-end testing.
References: [plugin sensor detection](https://github.com/Luligu/matterbridge-hass/blob/1.5.0/src/sensor.entity.ts),
[category conversion](https://github.com/Luligu/matterbridge-hass/blob/1.5.0/src/converters.ts),
[MatterBridge cluster features](https://github.com/Luligu/matterbridge/blob/3.10.8/packages/core/src/matterbridgeEndpoint.ts#L5086).

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

When a selected source has no explicit icon, light, switch, fan,
lock, cover, and media player helpers provide an editable Jinja `if/else`
icon template by default. The icon follows the source state; unknown or
unavailable sources show a question mark. Multiple sources must all match the
active branch. Source-provided icons and customized templates remain supported.
Clear the icon template to use the static icon instead.

Virtual Layer includes integration icons, brand assets, and Home Assistant UI
translations.

Climate, fan, and humidifier mode labels include common power, automatic,
manual, speed, and preset values in English and Korean. Climate swing labels
also cover power and direction values. Lowercase, title-case, and uppercase
source values (such as `off`, `Off`, and `OFF`) retain their original command
values while displaying translated labels. Unrecognized custom mode names
remain as supplied by the source.

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
sh tests/docker/run_light_interoperability.sh
```

Open `http://localhost:8123`, finish Home Assistant onboarding if needed, then
add `Virtual Layer` from `Settings > Devices & services > Add integration`.

Stop the container:

```sh
docker compose -f tests/docker/docker-compose.yml down
```

### Virtual utility meters

Create a **sensor** on your virtual Device, choose one cumulative usage sensor
(for electricity, an energy sensor in kWh, not an instantaneous W sensor), and
enable the utility-meter options. Configure the same fields when editing it.
The generated `<entity_id>_cost` monetary sensor belongs to the same Device.

The general entity form contains only the utility-meter enable switch. On
submission, enabled meters open a separate **Utility meter settings** step for
the schedule, billing, source behavior and current-value correction. This works
for initial setup, adding an entity and editing an existing entity. The Back
option retains the draft without saving; canceling the flow also leaves the
stored entity unchanged. Clearing the suggested start or tariff selector removes
that optional setting rather than silently restoring its old value.

Validation errors
identify the affected input, and blank/unknown initial readings become zero
when enabling this mode. Disabling a meter retains its schedule and billing
settings for later editing, even if they need repair before re-enabling; it
clears the old one-time correction so re-enabling cannot replay that correction.

- Cycles: every 15 minutes, hourly, daily, weekly, monthly, bimonthly, quarterly, yearly, no reset,
  anchored N-day repetition, or a five-field cron expression.
- The optional start date/time anchors calendar cycles; N-day cycles require it.
  Local calendar days follow Home Assistant's timezone, including DST. Month-end
  anchors clamp in shorter months without drifting in subsequent months.
- Configure incremental readings, signed net consumption, source reset handling,
  and availability during source outages. A decreasing cumulative source reading
  rebases the source without subtracting usage unless net consumption is enabled.
- To track tariffs, create one meter per tariff using the same source and an
  existing `select`/`input_select` entity; each meter collects only while its
  configured tariff is selected. Switching tariffs rebases the reading.
- Billing supports a base charge, a unit rate, and ascending progressive tiers
  (`up_to` and `rate`). The unit rate applies above the final tier. This is a
  configurable estimate, not a jurisdiction-specific tax or utility-bill engine.
- Enable **Create last-month comparison sensor** to add
  `<entity_id>_last_month_same_time`. It reads this meter's Recorder history at
  the same local calendar date and time one month earlier (month ends clamp to
  the last day), refreshed every minute even when the source is idle.
  Recorder must retain at least one month of state history (35 days is a safe
  retention setting; its default retention is insufficient). Missing history,
  an unavailable historical reading, or a changed unit produces `unknown`,
  never an estimate from a full-month total. No Recorder settings are changed.

**Current usage correction** replaces the current period's total when saved and
is applied once, not again on every reload. In automation actions, use
`virtual_layer.adjust_utility_meter` (`amount`, non-negative) to add missing usage,
`virtual_layer.calibrate_utility_meter` (`value`) to replace the total, or
`virtual_layer.reset_utility_meter` to finish the current period and start at zero.
All three accept `entity_id`; calibration permits negative totals only for net
consumption. Corrections do not modify the physical source reading.

Changing a cycle, start date, offset, N-day interval, cron expression, or HA
timezone keeps the running total and applies the new schedule from the change
onward. It does not retroactively reset past periods. The new schedule baseline
survives reloads; `last_reset` continues to describe the actual last reset.
Use the reset action explicitly if the change should also start a zero total.
Editing prices recalculates the current period's estimate using the new prices;
it does not split the period into historical price segments.

Absolute calibration also rebases the source. If the source is unavailable,
the first recovered reading establishes a new baseline, avoiding counting an
already-corrected outage gap twice. Consumption between calibration and that
first recovered reading cannot be reconstructed; correct it afterward if needed.
An additive adjustment does not rebase the source.
Corrections affect the current entity value and subsequent collection; they do
not rewrite previously recorded Home Assistant history or long-term statistics.

Persistent meters restore totals and source baselines. When a cycle boundary was
missed during downtime, the current period starts fresh; the integration cannot
reconstruct which period unobserved consumption belonged to. Use an audited
current-usage correction for missing Zigbee readings rather than expecting it to
recover measurements the device never reported.

The Docker environment intentionally does not include any Virtual Layer YAML.
It mounts the local custom integration into Home Assistant and verifies the same
UI-only path users will use.
