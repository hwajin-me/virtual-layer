# Presence Fusion in Virtual Layer

Presence Fusion estimates one person's presence from the existing HA entities
of their physical devices. It selects **one actual GPS source**, rather than
averaging devices or replacing GPS with Home coordinates. Devices left at home
cannot hold a carried device at home after a confirmed departure.

## Install and configure

1. Install/update the repository's `custom_components/virtual_layer` directory
   using the normal Virtual Layer installation process. No separate
   `presence_fusion` directory or external service is required. Restart HA when
   installing Python integration updates; this task does not perform that step
   on an operational server.
2. Add **Virtual Layer** in Settings → Devices & services. Enter a blank-form
   Device name identifying the person and enable **Create a Presence Fusion
   Device**. Do not choose the unrelated source-Device-copy option.
3. Add each **physical device**, such as a phone or watch. Set a distinct integer
   GPS priority (lower wins at confirmed reunion). A device with no GPS may be
   a local accessory; disable GPS candidate for it.
4. Add source mappings inside that physical device: at most one GPS tracker,
   any configured home Wi-Fi/BLE presence entities, and at most one room entity.
   A watch is a GPS candidate only if it actually has an independently updating
   GPS entity. Maximum: 16 physical devices, 64 mappings per device.
5. For GPS, optionally specify a reliable measurement timestamp attribute and
   choose timezone-aware ISO 8601, epoch seconds or epoch milliseconds. No unit
   guessing occurs. GPS input attributes are `latitude`, `longitude` and
   `gps_accuracy`.
6. For Wi-Fi/BLE, map exact positive and negative values. An SSID sensor's
   positive values must be your exact home SSIDs, one per line. Unmapped values,
   unknown, unavailable and removed entities are **unknown**, not absent.
   Choose state or an explicitly named attribute.
7. Use `source_managed` when the upstream integration manages timeout. A
   long-lived `on` does not expire merely because last_changed is old.
   `timestamp_ttl` requires a trustworthy heartbeat/measurement attribute and a
   TTL. HA state timestamps do not prove an MQTT retained message is recent.
8. Room mappings optionally rename values using `source=display` lines.
   `not_home` is absent; unknown/unavailable do not become room names. The room
   provider must manage its own signal timeout, or use timestamp TTL.
9. Finish the source/device screens and choose **Save**. Advanced heuristic
   settings are on a separate screen. Their defaults are tunable starting
   points, not accuracy guarantees. Home geometry defaults to `zone.home`; an
   explicitly selected [shared GeoJSON boundary](GEOJSON_AREAS.md) overrides its shape.

Each person uses a separate Virtual Layer entry and shares one output Device.
Do not assign another person's devices as this person's evidence. The Device
metadata screen edits its stable identifier, manufacturer, model, versions,
serial number, URL, area and parent. Use Reconfigure for the entry/Device name;
renaming never derives new tracked-device UUIDs or entity unique IDs.

Configure reopens the saved devices and mappings, with add/edit/delete actions.
Registry-backed source renames follow the registry ID. Unavailable sources are
not deleted from configuration. Save reloads this entry and applies new options.
The HA entry menu also provides Reload. To remove everything, delete the entry:
entities, listeners, maintenance and entry-specific runtime storage are removed.
Broken saved device rows remain accessible to the options delete workflow.

## Outputs and automation

Nine native entities are created: GPS tracker; presence; home binary sensor;
room; primary GPS; GPS mode; tracking health; distance to Home; direction. Entity
IDs and registry display names can be adjusted in HA. Output unique IDs depend
only on the config entry ID and fixed output key. All outputs share the Device.

Use **presence/home** for presence automations. Presence is home, nearby,
arriving, away or unknown; room is separate. Home binary unknown is not off.
Use the **GPS tracker** for maps/person: its normal home/not_home/zone state is
calculated by HA. It never emits nearby/arriving/room as its tracker state.
GPS stale makes that tracker unavailable, independently of presence's bounded
hold. Active local evidence may legitimately keep presence=home while GPS is
temporarily outside or unavailable; coordinates are never snapped to Home.

To use HA Person, manually select the fusion GPS tracker. Linking original
trackers as well invokes HA's separate person arbitration; the integration does
not alter Person configuration. Direct `person` or Virtual Layer output inputs
are rejected. Indirect feedback through templates is unsupported.

A low-risk notification automation can trigger when presence changes from
`away`/`nearby`/`arriving` to `home`. Do not treat `unknown` or `unavailable` as
`away`, and do not use this estimate as an access-control decision.

## Primary selection and manual override

Confirmed proximity uses fresh, time-comparable GPS with known accuracy, at
least three observations, and a complete-link group. A cold start with remote
stationary candidates is ambiguous. Wait for genuine grouping/movement or
select a known carried device manually.

Meaningful observed movement can select a group departing from its previous
companions. A remote group with no continuity cannot take over. Stopping outside
does not reset priority. If GPS fails, only a recently confirmed safe companion
can supply fallback; otherwise the selected UUID is retained for explanation,
the GPS tracker becomes unavailable, and presence becomes unknown after its
evidence hold. An actual reunion with previously separated devices restores
priority among the devices proven to be together, including away from Home.

Developer Tools → Actions:

```yaml
action: virtual_layer.presence_fusion_set_primary
data:
  config_entry_id: REPLACE_WITH_THIS_PERSONS_ENTRY_ID
  tracked_device_id: REPLACE_WITH_PHYSICAL_DEVICE_UUID
  duration: 3600
```

The physical-device edit selector displays UUIDs; primary GPS exposes the
selected `tracked_device_id`. Duration is 1–86400 seconds, defaulting to the
configured value. A stale/non-candidate/foreign device is rejected without
changing state. Override selects an actual source, not a forced home/away state.
An overridden source becoming stale never silently selects another source.
Use `virtual_layer.presence_fusion_clear_primary_override` with `config_entry_id`
to resume automatic tracking. The specification's `presence_fusion.set_primary`
and `presence_fusion.clear_primary_override` aliases are also registered.
Expiry survives reload/restart without extension.

## Data quality, privacy and troubleshooting

Without a configured measurement timestamp, only a new coordinate/accuracy
event advances GPS freshness. Battery/name/icon changes do not. After restart,
a timestamp-less HA snapshot is not a new GPS measurement. Some stationary
sources therefore become stale until they send a real location change. An
unchanged location with a new trusted timestamp remains fresh without adding
movement. Missing/zero/negative accuracy assumes 100 m by default and cannot
prove GPS reunion. A single implausible jump is quarantined; after a long gap,
two consistent fixes at least 30 seconds apart are required for reacquisition.

If presence is unknown, inspect tracking health and the primary sensor's reason,
source availability, configured timestamps and Home radius. Invalid advanced
threshold relationships are rejected. A Home-zone change that exceeds the
nearby radius is diagnosed as ambiguous until settings are corrected. Nearby
uses hysteresis and arriving requires a same-source continuous observed path;
switching Primary does not splice two devices' coordinates into a direction.

After a Home reunion, an unavailable higher-priority GPS remains excluded until
it supplies a valid fix and current same-place evidence. The indoor local reunion
itself can complete with every GPS stale; the GPS tracker remains unavailable.
Unrelated local sources cannot synthesize an absent-to-present return edge.
Diagnostics report timer-expired sources as stale and identify invalid stored
device records without revealing their contents. Use Configure to repair/delete
such records; other valid devices continue running. Invalid Home geometry clears
distance/direction rather than using an invented Home location.

Fusion does not send observations externally. Optional shared GeoJSON HTTP(S)
sources fetch configured boundaries only. Existing GPS integrations may
use cloud services independently. Runtime history is bounded, in memory only.
Its HA Store contains minimal IDs/transition/expiry metadata, not GPS history,
MACs, SSIDs, room names or credentials. Config Entries locally store entity
mapping and the SSIDs you explicitly configure. Download diagnostics exclude
precise location, names, rooms and detailed movement/score data. Current UI
position/source attributes are intentionally visible to the user.

HA Recorder may record the input and output locations independently of this
feature. Review your Recorder retention/exclusion policy; this feature does not
modify it. Reload restores metadata conservatively and does not reconstruct
travel during downtime. A crash before a debounced save can lose a recent hint.

Device movement does not prove who carries it. All devices may be left behind;
another person may carry one; different moving groups can be ambiguous. Wi-Fi
and BLE can extend outside a building, RSSI is not a calibrated range, and room
providers can disagree. The speed filter starts with a ground-travel heuristic
and is not an air-travel guarantee. No probability or accuracy percentage is
claimed. Testing uses synthetic data, not real hardware/person tracking.

## Development verification

Use Python 3.14, create `.venv`, and install
`requirements_presence_fusion_test.txt`. Run all local and official-container
checks from the repository root with:

```sh
sh tests/run_presence_fusion_checks.sh
```

Docker is needed only for container checks. The script uses disposable official
HA containers, not a custom Dockerfile. Exact measured versions, commands,
coverage and limitations are in [TEST_REPORT.md](TEST_REPORT.md), requirements
are mapped in [TEST_MATRIX.md](TEST_MATRIX.md), and implementation choices in
[DECISIONS.md](DECISIONS.md).
