# GeoJson Device Group implementation and verification

Verified on 2026-09-22. Setup and entity details are in [GEOJSON_AREAS.md](GEOJSON_AREAS.md).

## Follow-up: exported names and clearing a source

`polygon.py` now accepts both `properties.name` and the common editor export
`properties.Name`, preferring `name` when both are present. The supplied
FeatureCollection was checked unchanged, including one interior and one exterior
sample. Saved catalog snapshots normalize the label to `name`.

`geojson_flow.py` previously used stored source values as optional schema
defaults, then merged omitted input with the old record. HA can omit optional
text fields after they are cleared, so both paths restored the deleted source.
The form now uses suggested values for display and treats omitted source/text
fields as empty. Removing a file/URL preserves its last valid snapshot as inline
GeoJSON, or uses replacement GeoJSON supplied in the same submission. Validation
errors retain the entered/cleared fields without changing saved data. Both
English and Korean descriptions explain this behavior.

`tests/integration/test_geojson_source_edit.py` adds nine regression cases:
file and actual loopback HTTP sources, omitted/empty clear submissions, replacement
inline data, switching inline data to a file, validation-error redisplay, storage
reload, and name aliases/precedence. No geometry or save function is mocked.

```sh
PYTHONPATH=. .venv/bin/pytest tests/integration/test_geojson_source_edit.py tests/integration/test_geojson_catalog.py tests/integration/test_geojson_group.py tests/integration/test_presence_ui_translations.py tests/unit/test_polygon_zones.py tests/unit/test_translations.py -q
```

Result: **95 passed** on local HA 2026.2.3 / Python 3.14.7.
`sh tests/docker/run_presence_fusion_smoke.sh` passed on HA 2026.9.3 /
Python 3.14.6, now including `Name` input and clearing the source through actual
HA options-flow schema validation. Compileall, Ruff and `git diff --check` passed.
`PYTHONPATH=. .venv/bin/pytest tests/unit tests/integration -q` reported
**3670 passed, 3 failed, 1 warning in 182.25s**. Failures remain the same existing
water-unit and PM2.5 companion cases listed below. No new failures were found.

## Follow-up: map background

`osm_tiles.py` downloads only the bounded set of OpenStreetMap raster tiles
needed by a GeoJSON Device map (at most 12), identifies this integration in the
HTTP User-Agent, stores them in HA's `.storage/virtual_layer_osm_tiles` for at
least seven days, and composites the cropped result into a data URI. The SVG
uses the same fitted viewport for that image and draws polygons, labels and GPS
markers over it. Attribution is rendered in the image. Failures or unavailable
tiles retain the plain SVG map instead of failing the Image entity.

`tests/unit/test_osm_tiles.py` verifies the embedded PNG, bounded tile set,
correct SVG layer ordering/attribution and no-tile fallback without accessing
the network. Google Maps is intentionally excluded because a static-map API key
and the user's billing/terms setup are required.

## Follow-up: GPS membership audit

The existing ordinary-tracker implementation already selects GPS coordinates and
calls `find_polygon_zone`. This audit fixed these concrete issues:

- Missing/invalid/expired GPS previously claimed the configured away state.
  It now publishes `unknown` and removes coordinates.
- Missing/deleted/all-disabled GeoJSON previously claimed outside. It now
  publishes `unknown`, retaining valid GPS attributes. Load failures with no
  matching cached polygon are also treated as unknown.
- Negative source GPS accuracy was clamped to zero and accepted as exact GPS.
  It is now rejected as an invalid observation.
- Oversized numeric coordinates could raise an uncaught overflow; names longer
  than HA's state limit could fail during publication. Both are rejected while
  validating the document.

Added `polygon_inside` and `polygon_containing_zone` attributes distinguish
coordinate containment from the existing accuracy-circle intersection behavior.
The Zone companion follows the tracker. The apparent companion lag during the
audit was a test synchronization issue: its assertion must wait for the tracker
write and the subsequent HA state callback, not only the original source event.

`tests/integration/test_geojson_tracker_membership.py` uses real HA entities and
state events, without replacing the point-in-polygon engine:

| Test | Coverage |
|---|---|
| `test_actual_tracker_membership` (11 cases) | Inside, outside, hole, hole edge, outer edge, accuracy-only match, MultiPolygon, ±180° and both sides of the date line; GPS attributes and Zone companion |
| `test_invalid_gps_does_not_claim_outside` (7 cases) | Unknown/unavailable states, absent/out-of-range/boolean/NaN coordinates, negative accuracy |
| `test_expiry_recovery_catalog_changes_and_reload` | Silent GPS expiry, fresh recovery, movement, disable/reenable/delete, reload |
| `tests/unit/test_polygon_zones.py` additional validation cases | Oversized/non-numeric coordinates and 255/256-character Zone names |

The focused command below passed **137 tests**:

```sh
PYTHONPATH=. .venv/bin/pytest tests/integration/test_geojson_tracker_membership.py tests/integration/test_geojson_catalog.py tests/integration/test_geojson_group.py tests/integration/test_polygon_zone_setup.py tests/unit/test_polygon_zones.py tests/unit/test_polygon_config_flow.py tests/unit/test_device_tracker_location_helper.py -q
```

Both `sh tests/docker/run_compatibility_smoke.sh` and
`sh tests/docker/run_presence_fusion_smoke.sh` passed again on HA 2026.9.3 /
Python 3.14.6. The latter now also exercises an ordinary tracker consuming shared
GeoJSON: inside → outside → accuracy-only match → unavailable → recovery, with
the actual Zone companion. Compileall, Ruff's E9/F63/F7/F82 checks and
`git diff --check` passed. No production server was changed.

`PYTHONPATH=. .venv/bin/pytest tests/unit/test_local_presence.py tests/integration/test_geojson_tracker_membership.py -q`
also passed **39 tests**, including the distinction between ordinary Wi-Fi
presence and polygon membership when inferred coordinates disappear.

Final full rerun (`PYTHONPATH=. .venv/bin/pytest tests/unit tests/integration -q`):
**3661 passed, 3 failed, 1 warning in 177.57s**. The three failures are the same
pre-existing water-unit and PM2.5 companion cases listed below; no GeoJSON or
tracker regression remains in the executed tests. Earlier full-suite figures
below describe the initial group implementation, before this follow-up audit.

## Implementation

- `geojson_group.py`: singleton config-entry ownership, legacy catalog migration,
  per-document Devices, six information sensors and an SVG image, registry
  reconciliation, unload and removal.
- `geojson_metrics.py`: bounds including the date line, spherical area with holes,
  normalized data size and geometry counts.
- `geojson_flow.py`, `config_flow.py`, `presence_fusion/flow.py`: a dedicated
  GeoJson Device Group setup choice and management flow. Consumers select shared
  documents; only the group manages them.
- `__init__.py`, `sensor.py`, `image.py`: lifecycle and platform integration.
- English/Korean translations, README and usage documentation cover the new flow.
- Existing catalog UUIDs and consumer references are retained. Group unload keeps
  data; removing the group deletes its catalog. Removing consumers does not.

## Requirement-to-test matrix

Functions below are in `tests/integration/test_geojson_group.py`.

| Requirement | Test function |
|---|---|
| One group per HA, including concurrent setup | `test_group_singleton_including_parallel_setup` |
| Each document has a Device, information and map entities; live edits and cleanup | `test_each_geojson_has_own_device_and_live_information` |
| Existing shared records and consumer references survive migration | `test_legacy_catalog_migrates_once_without_changing_references` |
| Bounds and area support holes and the date line | `test_geometry_summary_accounts_for_holes_and_date_line` |
| Multiple zones belong to one document Device; registry preferences survive | `test_many_zones_stay_on_one_device_and_registry_preferences_survive` |
| Failed sources retain last complete geometry and expose status | `test_source_failure_status_keeps_metrics_and_map_across_reload` |
| Invalid legacy documents remain removable | `test_invalid_legacy_document_has_removable_device` |
| Stored duplicate groups cannot own or erase the shared catalog | `test_duplicate_stored_group_cannot_own_or_delete_catalog` |

Shared tracker/Fusion consumption is covered by `test_geojson_catalog.py`;
UI descriptions and translation topology by `test_presence_ui_translations.py`
and `tests/unit/test_translations.py`. The Docker Presence Fusion smoke creates
two GeoJSON Devices with 14 entities through the actual config flow.

## Executed checks

Local environment: Home Assistant 2026.2.3, Python 3.14.7, pytest 9.0.0,
pytest-asyncio 1.3.0, pytest-homeassistant-custom-component 0.13.316,
Ruff 0.16.1. Official HA Container: Home Assistant 2026.9.3, Python 3.14.6.

| Command | Actual result |
|---|---|
| `PYTHONPATH=. .venv/bin/pytest tests/integration/test_geojson_group.py tests/integration/test_geojson_catalog.py tests/integration/test_presence_ui_translations.py tests/integration/test_presence_fusion_ha.py tests/unit/test_translations.py -q` | 86 passed |
| `PYTHONPATH=. .venv/bin/pytest tests/unit tests/integration -q` | 3638 passed, 3 failed, 1 platform warning |
| `.venv/bin/python -m compileall custom_components/virtual_layer tests -q` | Passed |
| `.venv/bin/ruff check custom_components/virtual_layer tests --select E9,F63,F7,F82` | Passed |
| `git diff --check` | Passed |
| `sh tests/docker/run_compatibility_smoke.sh` | Passed on HA 2026.9.3 |
| `sh tests/docker/run_presence_fusion_smoke.sh` | Passed; 2 GeoJSON Devices / 14 entities and 11 Fusion entities |

The full suite is **not green**. These previously reproduced failures concern
existing sensor-unit changes in the working tree, which this change preserves:

- `test_water_measurement_runtime[ph-pH-7-None]`
- `test_water_measurement_runtime[conductivity-ppm-500-None]`
- `test_existing_measurement_gets_live_companion_and_cleanup[pm25-ug/m^3-5-good]`

No production HA deployment/restart, git push or account connection was performed.
Docker verification containers were stopped after testing.

## Limits

Area is a spherical estimate, not a survey measurement. Overlapping polygons
are summed; they are not merged before measuring. Data size describes the
normalized cached document, not the original file. Polygon areas are shared
Virtual Layer geometry, not native circular `zone.*` entities.
