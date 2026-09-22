# Presence Fusion decisions

## Packaging and compatibility

The user's final instruction takes precedence over the specification's suggested
standalone integration directory. Presence Fusion is a **Virtual Layer Device
profile**, selected in its existing initial config flow. Each person gets one
`virtual_layer` config entry, a typed `FusionRuntime`, a single shared HA Device,
and nine native platform entities. Existing generic Virtual Layer entries and
their tracker semantics are unchanged. The profile lives in
`custom_components/virtual_layer/presence_fusion/`; there is no second integration
to install. Physical-device UUIDs are generated, never derived from names.

The minimum targeted/tested Core is **2026.2.3**, with Python **3.14**. The existing
`pytest-homeassistant-custom-component==0.13.316` dependency installs that Core.
Local execution uses Python **3.14.7**, pytest **9.0.0**, pytest-asyncio **1.3.0**.
The official container smoke uses Core **2026.9.3**, Python **3.14.6**. These are
two tested versions, not a claim about every intervening or future release.
The historical AGENTS.md 2026.7.4 baseline was not the locally installed version.
The current cached/pulled `stable` Docker image was measured before reporting it.

Installed HA source was checked for ConfigEntry runtime_data, TrackerEntity,
DataUpdateCoordinator, registry UUID lookup (`entities.get_entry`), event helpers,
and Store save/remove behavior. `async_set_updated_data` always notifies listeners
in the tested Core, even when `always_update=False`; explicit snapshot equality
therefore suppresses identical push results.

## UI and identity

Initial setup retains the blank Device name. Enable **Create a Presence Fusion
Device**, then add physical devices and individual source mappings. A separate
advanced-settings screen exposes the heuristic parameters. Device names and GPS
priorities can be edited independently of UUIDs; source mapping records also
have stable UUIDs. The physical-device selector shows UUIDs for manual actions.
Metadata has its own screen for the stable Device identifier, manufacturer,
model, software/hardware versions, serial number, configuration URL, area and
parent Device. The default identifier is the immutable config entry ID. An
explicit identifier edit migrates the same registry Device; names do not change
identity. HA's normal Device UI also manages the user's registry display name.

Only existing GPS device_tracker entities are accepted as coordinate sources.
Other signals accept explicitly mapped tracker, sensor, binary_sensor and input
helper states/attributes. A single device has at most one GPS and one room
source. A source cannot be assigned to two physical devices within an entry.
The configuration wizard allows absent/unavailable registered sources. Duplicate
candidate priorities, direct Virtual Layer/person inputs, invalid numbers and
invalid threshold order are rejected. No Jinja is evaluated by this feature.

For compatibility with the specification, actions exist under
`presence_fusion.set_primary` / `clear_primary_override`. The discoverable,
translated Virtual Layer aliases are `virtual_layer.presence_fusion_set_primary`
and `virtual_layer.presence_fusion_clear_primary_override`. Both call the same
validated handler. Actions select sources, never force presence.

## Evidence, buckets, sessions

The engine has no HA object, network access or implicit clock. UTC epoch seconds
measure observation age and UTC bucket boundaries. An injected monotonic clock
measures debounce/holds. Metadata preserves original UTC expiry across restarts;
pending observations and monotonic holds are never restored.

Buckets are half-open UTC intervals `[k*b, (k+1)*b)`, finalized at the end. The
best accuracy wins, then the latest timestamp; finalized buckets are immutable.
Output position is the latest accepted individual source observation and does
not wait for a bucket. Movement uses anchor deadbands, adjacent observation gap
checks and fractional overlap at the window boundary (constant-speed estimate
within a segment). A round-trip test uses a 900-second window so finalizing the
last bucket does not expire the beginning of the 600-second fixture.

Path buffers retain at most 4096 segments / 4097 bucket samples, and parameters
that would need more than 4096 buckets are rejected. Age pruning usually retains
far less (roughly one window plus a boundary sample). Pair state is bounded by
the 16-device limit, reason history by 32, and source mappings by 64 per device.
Single quarantined jump fixes never become movement anchors. Reacquisition
requires at least two consistent new observations spanning 30 seconds.

Complete-link grouping uses stable UUID order and at least three observations.
Pair evidence counts only when both source measurement clocks have advanced.
Timers do not fabricate samples. Cold starts with several GPS candidates wait
for a confirmed group, compatible home evidence, a dominant moving group or a
manual choice. A solitary first-arriving event does not resolve a multi-device
cold start, avoiding event-order-dependent remote selection.

The active group is retained at stops. A departed moving group can succeed its
previous group, including when the original Primary itself leads the departure.
A remote pre-existing group cannot take over automatically. Reunion uses only
previously separated devices with fresh sustained GPS proximity, or actual
active-device local return. It starts a new movement epoch without erasing
recent movement diagnostics. Missing GPS never licenses arbitrary fallback.

### Re-audit corrections

Local return edges belong to the same source on the active physical device;
an absent Wi-Fi report followed by a different BLE source appearing is not a
return edge. Departure and manual selection clear old return evidence. A fresh
GPS path that actually leaves and returns inside Home can also corroborate the
local Home anchor. Local reunion can complete even with all GPS unavailable;
it preserves the primary identity without publishing stale coordinates. Deferred
priority UUIDs survive reload as hints and need a new valid fix plus current
same-place evidence before becoming the GPS supplier.

Segments crossing a reunion epoch are excluded from new-session arbitration;
fractional contributions still apply at the ordinary rolling-window boundary.
Out-of-order/duplicate quarantined fixes cannot restart a reacquisition window.
Sequential packets can temporarily exceed pair skew: they suspend eligibility,
but do not erase a close run if the completing packet arrives within the
evidence horizon. Outages, ambiguous geometry and expired evidence still
interrupt confirmation. A strong local/GPS conflict requires two distinct GPS
observations; maintenance ticks cannot fabricate repeated conflict evidence.

Outside-Home debounce spans nearby/away boundary changes. A room belonging to an
ineligible old group is cleared immediately, even while presence is in its exit
hold. Store restoration validates the entire hint before committing any state;
inconsistent groups and future historical timestamps cold-start safely.
Malformed device records are isolated at runtime and remain repairable/removable
in Options. Final save validates all device/source records, including unchanged
records, so editing one item cannot silently save another invalid item.

A source rename can remove its old HA state before its identical measured fix
appears under the new ID. That duplicate restores availability without changing
the observation timestamp or adding history. Primary source identity is included
in coordinator publication comparisons even when coordinates remain identical.
Manual duration validation rejects booleans/non-finite values before coercion,
and configured default durations use the same 1–86400 second range.

## Persistence and privacy

HA Store contains only versioned UUID/session/mode/presence/expiry metadata.
Coordinates, movement paths, SSIDs, room names and raw states are not stored
there. Metadata is saved with a five-second debounce on semantic transitions,
and flushed on unload; a crash can lose the most recent unsaved transition and
therefore restore conservatively. Config Entries necessarily store source
references and explicitly entered SSID comparison values.

Download diagnostics use an allowlist of fixed reason/status codes and numbered
source aliases, never generic redaction of arbitrary nested raw configuration.
They exclude coordinates, distances, movement patterns, source names and actual
rooms. UI entity attributes intentionally expose current position, selected
source and UUID. HA Recorder can independently record these input/output states.
No existing Recorder configuration is changed.

## Validation boundaries

The subsequent user request adds a shared GeoJSON catalog to both ordinary
Virtual Layer trackers and Presence Fusion. This explicitly extends the original
specification's circular `zone.home` geometry when the user selects polygon
Home. The original specification remains unchanged; see GEOJSON_AREAS.md for
the extension contract, UI, storage and test matrix. Fusion observation metadata
remains coordinate-free; the separate catalog necessarily stores configured
boundary coordinates and last-good external geometry.

Synthetic fixtures and HA platform tests exercise the real engine. The only
setup fault-injection test replaces HA platform forwarding to verify cleanup;
it does not replace any arbitration function. Tests use injected time, never
sleep or real GPS/cloud/radio sources. Docker operates disposable containers
with temporary HA configuration; no operational HA instance is deployed or
restarted. See TEST_MATRIX.md and TEST_REPORT.md for exact evidence.
