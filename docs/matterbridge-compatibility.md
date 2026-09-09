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
