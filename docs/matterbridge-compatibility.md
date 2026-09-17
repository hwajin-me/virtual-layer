# Matterbridge compatibility audit

Audited on 2026-09-09 against Matterbridge **3.10.8** and the user's
**matterbridge-hass 1.5.0**. Plugin source snapshot:
`4117b96b8a0a073d6756ad2df2386401a9cea4aa`.

The bridge core implements Matter clusters; the Home Assistant plugin decides
which HA entities and attributes become endpoints. Core support alone does not
establish plugin or controller support.

## Mapping requirements checked

| HA entity | Relevant plugin requirements |
| --- | --- |
| Electrical sensors | Power: W; current: A; voltage: V, with measurement state class. Cumulative energy: kWh and total_increasing. |
| Environmental sensors | Matching device_class and measurement state_class; valid units and finite values. |
| Binary sensors | Explicit supported class. Smoke/CO, moisture, occupancy/presence/motion, contact and cold have mappings; rain has no direct mapping. |
| Light | supported_color_modes, color_mode, brightness, color_temp_kelvin and min/max Kelvin, HS/XY values. |
| Climate | hvac_modes, current/target temperatures, min/max temperatures and range setpoints where applicable. |
| Fan | percentage, supported_features, preset_modes, direction and oscillating. |
| Cover / valve | Current position and supported commands. |
| Battery | measurement, battery, percent; associated with a Device. |

These requirements come from the plugin's
[converters](https://github.com/Luligu/matterbridge-hass/blob/4117b96b8a0a073d6756ad2df2386401a9cea4aa/src/converters.ts)
and [entity construction](https://github.com/Luligu/matterbridge-hass/blob/4117b96b8a0a073d6756ad2df2386401a9cea4aa/src/control.entity.ts).
In particular, the README's energy measurement row differs from the converter;
the executable converter requires total_increasing.

## Virtual Layer changes

- Generated vacuum battery sensors now expose measurement metadata and follow
  the parent's live battery_level instead of retaining the configuration value.
- Direct cumulative-meter conversions preserve total/total_increasing metadata.
  Summing compatible cumulative meters preserves their shared state class.
- Cumulative sums require all source measurements to be available. Publishing
  a partial sum could resemble a meter reset and corrupt consumption statistics.
- Other aggregation policies retain their existing measurement behavior; an
  average is not automatically asserted to be a cumulative meter.

## Configuration guidance and boundaries

### Media players in Apple Home

`matterbridge-hass` maps every Home Assistant `media_player` to Matter's
Basic Video Player and Keypad Input clusters. Apple Home does not currently
accept that device type, so it displays the direct virtual media-player
endpoint as **Unsupported**. This is a controller/device-type limitation in
Matterbridge, not a malformed Virtual Layer state or a setting that can be
corrected by changing media metadata.

For Apple Home, use Matterbridge's **Virtual Control Label** fallback. Assign
the configured label to the virtual media-player entity in Home Assistant. The
plugin then exposes Apple Home-compatible momentary switches for supported
commands (power, play/pause/stop, previous/next, mute, and volume up/down).
Virtual Layer advertises and implements the previous/next and volume-step
features required by that fallback, and proxies those commands to compatible
media-player sources. Keep the player eligible while Matterbridge creates the
labelled controls; then ignore the unsupported direct endpoint in Apple Home.

This fallback supplies command switches, not a Now Playing tile, media
metadata, queue browsing, or AirPlay routing. Re-pair/reload Matterbridge after
changing its entity filters or virtual-control label, because endpoint
composition is cached by Matter controllers.

### Battery readings in Apple Home

A battery percentage is a Power Source cluster, not a standalone Apple Home
accessory. Matterbridge only attaches it correctly when the `sensor` belongs to
the same Home Assistant Device as a supported primary entity. Virtual Layer's
generated vacuum battery sensor has the required `battery` device class,
`measurement` state class, `%` unit, and parent Device association.

Keep that generated `sensor.<object_id>_battery` with its parent Device in
Matterbridge. Do **not** select it as an individual entity or put it in
`splitEntities`: doing so creates a Power Source-only Matter device, which
Apple Home can report as **Unsupported**. Also keep direct media players out of
the same Apple Home bridge Device; an unsupported Basic Video Player endpoint
can make the combined Device unusable even though its battery cluster is valid.

If the battery is still absent after keeping it attached, verify the live Home
Assistant state has all four values exactly: numeric `state` in 0–100,
`device_class: battery`, `state_class: measurement`, and
`unit_of_measurement: %`. Reload/re-pair Matterbridge after correcting metadata.

For a stepped fan, select the Matter three-level profile in the Virtual Layer
fan flow when Low/Medium/High is the desired controller experience. It exposes
the canonical `low`, `medium`, and `high` HA preset modes, reflects the active
physical speed as one of those modes, and maps each Matter preset request back
to the three source percentages selected in the UI. The normal percentage
profile instead keeps Matter's 1–100% setting. Retain a source-specific preset
only when it is required in Home Assistant; it is not a Matter-standard fan
mode.
Direction and oscillation are included only when the virtual fan declares the
matching native feature and current value; Matterbridge uses those attributes
when it constructs the FanControl cluster.

Use the sensor's dedicated native metadata inputs and generated conversion
helpers to publish the required units. Never relabel kW as W or Wh as kWh without
converting the numerical value. Existing configurations are not silently
rescaled by this audit.

Keep each binary sensor's class truthful: mapping rain to smoke merely to obtain
a Matter endpoint would create a false alarm meaning. A different class should
be selected only when it describes the actual combined sensor.

Matterbridge 3.10.8 adds closure, tariff, temperature-alarm and water-tank APIs,
but these do not automatically become HA plugin mappings. See the
[3.10.8 release notes](https://github.com/Luligu/matterbridge/releases/tag/3.10.8).
Adding arbitrary attributes to Virtual Layer cannot enable unimplemented
plugin clusters. Camera transport and controller-specific support also remain
separate concerns.

Validation covers generated helpers and real Home Assistant entity setup.
It does not constitute Matter certification or a live commissioning test with
the user's Matterbridge instance and controllers.
