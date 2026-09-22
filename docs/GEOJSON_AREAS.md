# GeoJson Device Group

Virtual Layer provides **one GeoJson Device Group per Home Assistant**. This is
a dedicated Virtual Layer config entry with a fixed name, not a separate
integration. Every registered GeoJSON document becomes one Device within this
group. Ordinary polygon trackers and Presence Fusion share its catalog.
No YAML configuration or account connection is required.

## Setup

1. Open **Settings → Devices & services → Add integration → Virtual Layer**.
2. In **What would you like to create?**, choose **GeoJson Device Group**.
   Leave the regular Device name empty; this group uses its fixed name. Confirm
   creation. A second group is rejected, including simultaneous setup flows.
3. Open **Configure** on **GeoJson Device Group**, then **Add an area set**.
   Enter a GeoJSON Device name, priority and either inline GeoJSON or a local file /
   HTTP(S) URL. Relative file paths resolve below the HA configuration directory;
   other paths must be explicitly allowlisted by HA. Save the document. Repeat
   for additional documents. Each registration creates a separate Device.
   Edit, disable, delete and reload sources from this same group.
   Feature labels may use `properties.name` or `properties.Name`; `name` takes
   precedence if both are present. Clearing a saved file/URL stops source
   refresh and keeps the last valid boundaries as inline GeoJSON. You can also
   paste new GeoJSON when clearing the source to replace those boundaries.
4. For an ordinary `device_tracker`, select multiple **Shared area sets**
   in its domain settings alongside the usual source entities. Existing inline
   GeoJSON and file settings continue to work.
5. For Presence Fusion, choose **Choose location areas and Home**, select area sets and
   optionally choose one as Home. Save the Presence Fusion configuration.

The management menu is no longer part of ordinary tracker or Presence Fusion
configuration. Those profiles select shared areas; registration and editing
belong exclusively to GeoJson Device Group.

Saving a catalog document applies immediately to every consumer. Closing the
group's options dialog does not undo a document already saved. Simultaneous stale edits
are rejected; reopen the document from the catalog to retry. Document UUIDs
remain stable when names change. Deleting or disabling documents preserves
consumer selections so they remain visible and removable in their forms.

## Devices and entities

### Checking a tracker's GPS against shared areas

Select the physical GPS `device_tracker` as the source of a virtual tracker,
then choose the shared area sets in the virtual tracker's domain settings.
Registering a GeoJSON document alone does not subscribe to every HA tracker.

The virtual tracker publishes the matched Zone name and retains latitude,
longitude and GPS accuracy for the HA map. With multiple sources it checks the
position selected by the configured aggregation strategy. Its companion Zone
sensor mirrors that state. Source events, periodic expiry and catalog edits
trigger reevaluation.

- `polygon_zone`: selected Zone after accounting for the GPS accuracy circle.
  A coordinate just outside an edge can match when its accuracy circle reaches
  the polygon. Existing priority rules resolve overlapping matches.
- `polygon_inside`: whether the coordinate itself is inside any selected polygon,
  without expanding it by GPS accuracy (`true` / `false` / `null` if unknown).
- `polygon_containing_zone`: highest-priority polygon containing the coordinate
  itself, or `null`. Exterior boundaries count as inside; hole boundaries and
  hole interiors are excluded from this strict check.

Valid coordinates outside a complete set produce `not_home` (or the configured
away state). Missing, unavailable, invalid or expired coordinates produce
`unknown` and clear GPS attributes. If no geometry is available, the result is
`unknown` while valid GPS coordinates remain visible. On definition load errors,
cached matching geometry remains usable, but an unmatched point is `unknown`
because a missing area could contain it. Reenabling/reloading definitions or
receiving fresh coordinates automatically recovers the result.

For a polygon tracker using Wi-Fi/BLE-inferred Home coordinates, disconnecting
also removes those coordinates and yields `unknown`: a disconnect alone does
not establish that the device is outside an arbitrary polygon. Ordinary local
presence tracking without GeoJSON retains its existing Home/away behavior.

GeoJSON coordinates use **longitude, latitude**; tracker attributes use their
explicit `latitude` and `longitude` keys. Polygon/MultiPolygon, holes and areas
crossing ±180° longitude are supported. Zone names must fit HA's 255-character
state limit. These checks do not change Presence Fusion's stricter Home
confirmation rules described below.

```text
Virtual Layer
└── GeoJson Device Group (one config entry)
    ├── Home boundaries (one registered GeoJSON Device)
    │   ├── GeoJSON information
    │   ├── Zone names
    │   ├── Bounds
    │   ├── Estimated area
    │   ├── GeoJSON data size
    │   ├── Zone count
    │   └── GeoJSON map
    └── Work boundaries (another Device, with its own seven entities)
```

| Entity | Value and information |
|---|---|
| GeoJSON information | Ready / saved data / disabled / invalid / source unavailable; attributes include document UUID, enabled flag, priority, source type, normalized format, polygon/coordinate counts and load-error flag |
| Zone names | Feature names joined for display; full list in `zone_names` attributes even when the state must be shortened to HA's limit |
| Bounds | West, south, east, north in degrees; individual coordinates and `crosses_antimeridian` in attributes |
| Estimated area | Square metres on a mean-radius sphere; holes subtracted; areas of overlapping polygons are added rather than dissolved into a union |
| GeoJSON data size | UTF-8 bytes of the normalized cached GeoJSON, not the original remote file size |
| Zone count | Number of named Features; MultiPolygon parts are counted separately only in the information attributes |
| GeoJSON map | OpenStreetMap raster background with the document's SVG geometry layered above it, including when matching is disabled |

Several Features in one FeatureCollection stay on **one Device**. Device and
entity identities use the existing document UUID, so renaming/reload preserves
them. User-assigned Device names and entity names/disable settings are preserved.

### Map background

The GeoJSON map downloads the OpenStreetMap raster tiles needed for its current
area, caches them under HA's `.storage` directory for at least seven days, and
embeds the composed image in the SVG before drawing zones and labels. Attribution
is shown in the image. This means the browser does not fetch map tiles itself.
If HA cannot reach the tile service, the map remains available with the existing
plain SVG background and geometry. The tile request reveals the tile area that
contains the configured boundary to OpenStreetMap.

Google Maps is not requested automatically: its static-map service requires a
user-owned API key and a billing/terms configuration. It is deliberately not
treated as a drop-in public tile endpoint.
Deleting a document removes its Device and generated entities immediately.
These are informational entities; they do not create circular `zone.*` entities.

## Existing installations

On integration setup, an existing shared catalog automatically gets its single
GeoJson Device Group. Documents, UUIDs, last-good boundaries and selections in
trackers/Fusion are retained; they are not copied into per-tracker catalogs.
The group may initially contain no Devices until the first GeoJSON is registered.
The group name is fixed; rename individual GeoJSON Devices through their records
or HA's Device display-name setting instead.

Unloading/reloading the group preserves the catalog. **Removing the group from
Home Assistant deletes its entire catalog and generated Devices/entities**.
Consumers immediately lose those boundaries and retain missing references for
correction. Creating the group again starts empty. Removing a consuming tracker
or Presence Fusion entry does not delete the shared catalog.

## Geometry and overlap

Supply a GeoJSON Feature or FeatureCollection with `Polygon` or `MultiPolygon`
geometries. Every feature needs `properties.name`. Holes and international date
line crossings are supported. Coordinates are **longitude, latitude**:

```json
{"type":"Feature","properties":{"name":"Garden"},"geometry":{"type":"Polygon","coordinates":[[[127.0000,37.5000],[127.0020,37.5000],[127.0020,37.5010],[127.0000,37.5010],[127.0000,37.5000]]]}}
```

Lower document priorities win overlaps. Ties use feature `properties.priority`
(default zero), smaller area, name and document UUID. Management names label the
catalog; feature names label the resulting location. Limits: 32 documents,
20,000 coordinate positions across the catalog, 2 MiB per document and 4 MiB
serialized catalog including cached boundaries. Large datasets should be
simplified before adding them. These limits bound work on HA's event loop.

## Presence Fusion Home

Without an explicit polygon Home selection, the existing `zone.home` circle is
unchanged. A selected Home document must include the `zone.home` center. Its
features collectively define the GPS Home boundary. Confirmed Home requires
the GPS accuracy circle to fit inside a polygon; confirmed departure requires
clearance beyond GPS accuracy plus the configured exit margin. Holes exclude
their interiors. At overlaps, accuracy containment is conservative: one polygon
must contain the complete accuracy circle.

Distance/direction still use the `zone.home` center. Nearby-enter distance must
exceed the furthest Home polygon vertex from that center; increase the detection
settings for a large Home boundary before saving. Invalid or removed polygon
Home configuration suspends GPS geometry detection instead of silently falling
back to the circular boundary. Normal expiry/unknown rules still apply.
Configured local Wi-Fi/BLE/room evidence retains the original Fusion semantics.
Numerical thresholds are heuristics, not an accuracy guarantee.

Selecting documents adds a zone sensor and SVG map image on the same virtual
Device. The GPS tracker always retains actual coordinates and native HA zone
semantics. The separate zone sensor exposes polygon names. HA's built-in map
does not automatically draw these polygons; use the generated image entity for
the polygon preview. No polygon drawing editor is included.

## Loading, privacy and removal

External sources refresh every five minutes while the group or a consuming
tracker is loaded;
**Reload files and URLs** also works from the catalog. A failed document keeps its entire last
successful geometry, including after HA restart. Source errors are exposed as
fixed status flags without including URLs or response bodies. Each document is
independent; another document's failure does not remove its valid neighbors.
Unloading the group and the last consuming tracker removes the timer and
cancels an active refresh. Unloading only a tracker leaves the group active.

The shared HA Store contains configured boundaries and cached normalized
geometry, never tracker observations or movement history. URLs contact only the
explicitly configured server; tracker GPS positions are not sent to it. Avoid
putting credentials in source URLs; user/password URL authority is rejected.
HA Recorder may independently record entity state as usual. Configured boundary
coordinates, names, area and size are intentionally visible in the group's
entities. Paths, URLs and response bodies are not published in their attributes.

## Requirement / test mapping

All functions below are in `tests/integration/test_geojson_catalog.py` and use
real HA storage, config flows and runtime geometry (no mocked decision engine).

| Requirement | Test function |
|---|---|
| Multiple documents, priority, restart, stable IDs, disable/delete, stale edits | `test_catalog_persistence_priority_disable_delete_conflict` |
| File failure preserves complete geometry; recovery | `test_catalog_file_last_good_and_recovery` |
| Reject malformed/empty geometry without saving | `test_invalid_geometry_is_not_saved` |
| Polygon Home, GPS coordinates, same Device, map, live edits, unknown, unload | `test_fusion_shared_live_geometry_map_and_unload` |
| Fusion UI selection, save/reload, companion cleanup | `test_geojson_flow_add_edit_delete_and_select_home` |
| Holes, signed accuracy clearance, date line | `test_polygon_clearance_holes_and_dateline` |
| Ordinary tracker + shared UI edit/delete + live SVG + cleanup | `test_generic_tracker_uses_same_catalog_and_live_map` |
| Damaged record isolation and deletion | `test_catalog_corrupt_record_isolated_and_removable` |
| Invalid types, priority, credential-bearing URL rejection | `test_catalog_rejects_invalid_configuration` |
| Aggregate geometry limit and private diagnostics | `test_catalog_limits_and_diagnostics` |
| Ordinary tracker multi-selection save and form restoration | `test_generic_catalog_selection_form_round_trip` |

`tests/docker/presence_fusion_smoke.py` also registers a shared document through
the actual config flow in the official HA image and verifies all 11 entities,
polygon Home, SVG, expiry, reload and unload.

The Device Group tests in `tests/integration/test_geojson_group.py` cover
singleton/concurrent setup, two independently registered Devices and their 14
entities, live edits, persistent identities, deletion, automatic migration,
whole-group removal, spherical area/holes/date-line bounds, user registry
preferences, disabled entities and source failure/recovery across reload.
The Docker smoke also creates the group through its real flow, rejects a
duplicate, and checks two GeoJSON Devices / 14 information and map entities.
