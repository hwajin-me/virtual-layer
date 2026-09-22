# Shared GeoJSON areas

Virtual Layer manages multiple polygon documents from the Home Assistant UI.
The same catalog is available to ordinary polygon trackers and Presence Fusion.
No YAML configuration, account connection or new integration is required.

## Setup

1. Open **Settings → Devices & services → Virtual Layer → Configure**.
2. Choose **Manage shared GeoJSON areas**, then **Add**. In a Presence Fusion
   setup wizard the same menu is available before saving the entry.
3. Enter a management name, priority and either inline GeoJSON or a local file /
   HTTP(S) URL. Relative file paths resolve below the HA configuration directory;
   other paths must be explicitly allowlisted by HA. Save the document. Repeat
   for additional documents. Edit, disable, delete and refresh use the same menu.
4. For an ordinary `device_tracker`, select multiple **Shared GeoJSON documents**
   in its domain settings alongside the usual source entities. Existing inline
   GeoJSON and file settings continue to work.
5. For Presence Fusion, choose **Areas and Home boundary**, select documents and
   optionally choose one as Home. Save the Presence Fusion configuration.

Saving a catalog document applies immediately to every consumer, including
entries other than the one whose options dialog is open. Cancelling the parent
options dialog does not undo a document already saved. Simultaneous stale edits
are rejected; reopen the document from the catalog to retry. Document UUIDs
remain stable when names change. Deleting or disabling documents preserves
consumer selections so they remain visible and removable in their forms.

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

External sources refresh every five minutes while there are active consumers;
**Refresh** also works from the catalog. A failed document keeps its entire last
successful geometry, including after HA restart. Source errors are exposed as
fixed status flags without including URLs or response bodies. Each document is
independent; another document's failure does not remove its valid neighbors.
Unloading the last consumer removes the timer and cancels an active refresh.

The shared HA Store contains configured boundaries and cached normalized
geometry, never tracker observations or movement history. URLs contact only the
explicitly configured server; tracker GPS positions are not sent to it. Avoid
putting credentials in source URLs; user/password URL authority is rejected.
HA Recorder may independently record entity state as usual. Catalog documents
remain until explicitly deleted, even if the last consuming entry is removed.

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
