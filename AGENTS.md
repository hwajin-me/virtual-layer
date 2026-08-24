# AGENTS.md

## Mission

Virtual Layer is a Home Assistant custom integration for creating virtual
Devices and virtual entities entirely from the Home Assistant UI. It extends
the ideas behind Home Assistant template entities with multi-source helpers,
native domain properties, actions, diagnostics, and resilient device/entity
management.

This file is the implementation handoff for coding agents. Read it together
with `README.md`, but treat the code and tests as the source of truth when the
two disagree.

## Non-Negotiable Product Contract

- Configuration is UI-only through config entries and options flows. Do not
  add YAML entity loading, import, backup, restore, or export flows.
- The user-facing grouping concept is `Device`, even though persisted legacy
  keys such as `group_name` remain for compatibility.
- One Device may contain many virtual entities. All primary, information,
  source-debug, battery, polygon-zone, and polygon-map entities must attach to
  the same Home Assistant device registry entry.
- Device metadata is editable: stable device ID, name, manufacturer, model,
  software/hardware version, serial number, configuration URL, area, and parent
  device. Preserve stable IDs when names change.
- Entity name, entity ID, icon, templates, sources, native values, and actions
  are editable. Explicit entity IDs are authoritative and are restored if a
  registry rename attempts to change them.
- Config entry titles use only the configured Device/group name. Do not append
  `- virtual_layer`. Legacy exact-match titles are migrated on setup.
- Initial setup must show a blank Device name. Do not restore an `imported`
  default.
- Existing entries, including older or partly malformed storage, must remain
  loadable, editable, and deletable. Recovery must isolate bad records instead
  of making the whole integration unmanageable.
- English and Korean config-flow/service translations must have identical key
  topology. Every visible dynamic input requires a useful description.

## Current Handoff Snapshot

As of 2026-08-13, the working tree intentionally contains uncommitted changes.
Do not reset, discard, or broadly rewrite them. The current changes include:

- removal and migration of the legacy config-entry title suffix;
- a blank initial Device-name input;
- dedicated native-value Jinja inputs and helper generation across managed
  entity domains;
- source-aware fallback helpers for native properties, including properties
  missing from the source's current attributes;
- safer optional datetime, image, sensor, and vacuum values;
- synchronized English/Korean descriptions and expanded coverage tests.

The verified baseline immediately before this file was added was:

- `645 passed` from the complete pytest suite;
- Ruff syntax checks passed;
- Docker all-domain smoke passed on Home Assistant 2026.7.4 with 46 domains and
  73 entities, with no Virtual Layer warnings or errors.

Always run `git status --short` before editing. Work with existing changes and
never assume the checkout is clean just because the last test result was green.

## Architecture Map

- `custom_components/virtual_layer/const.py`
  Defines storage keys, supported domains, state-only domains, reserved names,
  generated-diagnostic markers, and the public command contract per domain.
- `custom_components/virtual_layer/config_flow.py`
  The UI control plane. It owns selectors, sectioned forms, defaults, source
  copying, helper generation, native-template property catalogs, JSON/Jinja
  validation, helper-policy detection, device management, and damaged-data
  recovery. Changes here usually require translation and test changes.
- `custom_components/virtual_layer/cfg.py`
  Converts config-entry options into validated runtime entities and devices,
  migrates old data, reserves IDs, detects orphans, and generates runtime-only
  information/debug/battery/polygon companion entities.
- `custom_components/virtual_layer/__init__.py`
  Owns setup, reload, unload/removal, state-only entities, services, registry
  synchronization, fixed entity IDs, and fixed device metadata.
- `custom_components/virtual_layer/entity.py`
  Base `VirtualEntity`: restore state, source subscriptions, pull intervals,
  Jinja rendering, availability/icon/attribute/native templates, event hooks,
  command actions, and shared state mutation.
- `custom_components/virtual_layer/generic.py`
  Native generic implementations for select, text, date/time/datetime, button,
  siren, lawn mower, remote, media player, water heater, update, and related
  domains.
- Domain modules such as `climate.py`, `fan.py`, `humidifier.py`, `camera.py`,
  `image.py`, `device_tracker.py`, `vacuum.py`, and `sensor.py`
  Implement domain schemas, native properties, services, feature flags, and
  runtime template application.
- `custom_components/virtual_layer/polygon.py`
  Loads and validates Polygon/MultiPolygon GeoJSON and renders map SVG output.
- `custom_components/virtual_layer/translations/{en,ko}.json`
  Config flow, options flow, selectors, errors, and service text.
- `tests/unit`, `tests/integration`, and `tests/docker`
  Helper/schema tests, real Home Assistant config-entry tests, and an official
  Home Assistant Container smoke matrix respectively.

## Persisted Model And Compatibility

- `ConfigEntry.data` contains the integration-level group/device identity.
- `ConfigEntry.options[ATTR_DEVICES]` contains a mapping of Device names to
  entity lists. Shared metadata is stored separately under
  `ConfigEntry.options[ATTR_DEVICE_ATTRIBUTES]`. Treat all nested values as
  untrusted input.
- Every virtual entity needs a stable internal entity key and unique ID. Do not
  identify an editable entity only by list index, display name, or mutable
  entity ID.
- Keep persisted configuration JSON-serializable. Reject NaN, infinity,
  oversized integers, recursive structures, excessive depth, invalid entity
  IDs, and malformed source references before saving.
- Migrations must be idempotent. Preserve unknown vendor-specific fields when
  they are valid, especially when editing legacy entities.
- Deletion paths intentionally accept records that normal edit validation would
  reject. Users must be able to remove broken entities and empty devices.
- Generated companion entities are runtime-derived and marked with
  `DIAGNOSTIC_UNIQUE_ID_MARKER`; they are not independent user configuration.
  Their registry entries must be migrated or removed when the parent changes.

## Config Flow Rules

The normal create/edit path is:

1. Select zero, one, or multiple existing source entities and a target Device.
2. Prefill the entity form from those sources.
3. Edit common, native-domain, and advanced values.
4. When editing an entity with sources, choose a helper policy.
5. Persist options and reload the config entry.

Helper policies are field-aware:

- `automatic`: regenerate only fields that still match a previously generated
  helper; preserve every independently customized field.
- `keep_current`: keep all current templates even if sources changed.
- `force_helper`: replace generated and customized helper-capable fields using
  the current sources.

Do not infer customization merely from source IDs. Compare a canonical helper
profile per field. Source additions, removals, reordering, changed source
capabilities, and old profiles all need to work. Stale template-source entries,
attribute helpers, and native helpers must disappear when no longer applicable.

Native Home Assistant properties should use dedicated Jinja template inputs
whenever the domain has a standard property contract. Keep static persisted
values only as compatibility/fallback data. Advanced JSON remains appropriate
for structured attributes, event hooks, command action sequences, polygon
GeoJSON/rules, and integration-specific extensions; do not expose a duplicate
raw JSON editor for properties already represented by dedicated Jinja inputs.

All Jinja editors must have a usable default/helper. A missing source attribute
must still produce a renderable dynamic helper with a type-safe fallback rather
than a blank field. Templates must be compiled and validated before persistence.

## Composite Helper Semantics

Keep these defaults stable unless a product requirement explicitly changes:

- Boolean-like values: `AND`.
- Leak, smoke, gas, carbon-monoxide, moisture, problem, and safety binary
  sensors: `OR`, because any alarm is significant.
- Motion/presence binary sensors: majority detection; once active, remain active
  until all sources have been off for five minutes.
- Numeric values: average of finite, available values.
- Strings: ordered concatenation.
- Date/time/datetime: latest valid value.
- Select/enum and other stateful native domains: first known/available value.
- Lists: ordered unique union.
- Mappings: deterministic ordered merge.
- Shared source attributes: use type-aware merging; source-only attributes must
  not be silently omitted.
- Locations: median GPS, with a source more than 300 m from the median preferred
  for 30 minutes after movement. Preserve deterministic priority/tie behavior.
- Camera and image media: alias one source only; never merge binary media.

For each property listed in `DOMAIN_NATIVE_TEMPLATE_PROPERTIES`, check its
classification in the state, boolean, list, mapping, atomic-list, bitmask,
minimum, maximum, numeric, or datetime sets. Also check domain fallback maps,
attribute aliases, supported-feature derivation, and runtime coercion. Adding a
property to the catalog without adding correct helper and runtime semantics is
an incomplete change.

## Native Domains And Actions

`VIRTUAL_ENTITY_DOMAINS` is the advertised contract. Every listed domain must
have an importable platform module and a successful `async_setup_entry` path,
except domains deliberately handled as state-only entities.

Native property templates must update actual Home Assistant entity properties,
not merely extra state attributes. Recompute dependent feature flags and clear
stale selections when an option list changes. Validate ranges, enum membership,
finite numbers, date/time values, GPS pairs, color tuples, supported-feature
bitmasks, and mutable defaults.

Command actions use Home Assistant's script/action engine. Method names omit
the `async_` prefix and must exist in `VIRTUAL_ENTITY_COMMANDS`. Action payloads
may be a single action, a list, or `{sequence, optimistic}`. Preserve command
arguments as template variables and avoid optimistic mutation when explicitly
disabled.

Home Assistant has no dedicated washer, dryer, or pump domain. Represent these
as several entities on one virtual Device. Electrical, gas, and water usage are
sensor/number device classes and units, not new domains.

## Generated Information And Debug Entities

Each configured virtual entity produces:

- `<configured_object_id>_info`: configuration summary;
- `<configured_object_id>_debug1`, `_debug2`, ...: one per source, including
  source state, attributes, `last_updated`, and `last_changed`.

Additional generated entities include vacuum battery sensors and polygon zone
and map entities. Generated IDs, names, icons, device IDs, and unique IDs must
follow the configured parent entity, avoid registry collisions, and be cleaned
up when sources or parents are edited/deleted. Preserve user-customized registry
display names while updating integration-owned original metadata.

## Polygon And Location Requirements

- Accept inline GeoJSON and local/HTTP(S) sources managed from the UI.
- Support Polygon, MultiPolygon, holes, overlap priority, boundary accuracy, and
  international-date-line geometry. GeoJSON coordinate order is longitude,
  latitude; Home Assistant entity properties are latitude, longitude.
- Tracker strategies include majority, priority, latest, and median, with
  per-source enabled/dominant/weight/priority/age/accuracy/Jinja conditions.
- A combined tracker must publish both zone state and valid GPS attributes so it
  appears on a Home Assistant map.
- A polygon tracker automatically receives a zone sensor and SVG map image on
  the same Device.
- A transient bad file/URL must not discard the last complete working polygon
  set. Expose load errors without taking unrelated entities down.

## Translation And UX Checklist

When adding or renaming a form field:

1. Add matching labels and descriptions to both `en.json` and `ko.json`.
2. Update every create/edit step that exposes the field.
3. Keep translation placeholders valid identifiers and keep both files at the
   exact same key topology.
4. Use Home Assistant selectors: entity selector for sources, icon selector for
   icons, template selector for Jinja, and list/dropdown selectors for modes.
5. Keep large or advanced groups collapsed in sections, but never hide required
   values or make existing values disappear on edit.
6. Reopen forms with all stored values, selected sources, target Device, and
   custom templates intact.

Only English and Korean translation files currently exist in this checkout.
Do not claim additional locales without adding and testing the actual files.

## Change Checklist For A Domain Or Property

Before considering a domain/property change complete, inspect and update all
applicable layers:

1. Domain membership and command declarations in `const.py`.
2. Domain schema, normalization, validation, entity properties, template
   application, services, and feature flags in the platform module.
3. Native property catalogs, aliases, type sets, defaults, source fallbacks,
   helper generation, and dedicated form controls in `config_flow.py`.
4. Legacy migration and runtime config generation in `cfg.py`.
5. English and Korean labels/descriptions/errors.
6. Focused unit tests for valid, invalid, missing, restored, and changing values.
7. Config-flow integration tests for add, edit, source change, all helper
   policies, reload, registry grouping, and deletion/recovery.
8. All-domain coverage samples and Docker smoke fixtures.

Prefer table-driven/parametrized tests over dozens of nearly identical cases,
but keep distinct behavioral edge cases readable. Test behavior, not private
implementation trivia.

## Verification Commands

Install the local test environment when needed:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements_test.txt ruff
```

Run focused tests first, then the complete local suite:

```sh
PYTHONPATH=. .venv/bin/pytest tests/unit/test_config_flow_helpers.py -q
PYTHONPATH=. .venv/bin/pytest tests/unit tests/integration -q
```

Run the lightweight repository checks:

```sh
.venv/bin/python -m compileall custom_components/virtual_layer tests -q
.venv/bin/ruff check custom_components/virtual_layer tests --select E9,F63,F7,F82
git diff --check
```

Run the official Home Assistant Container compatibility smoke for platform API
changes. Config-entry, registry, service, reload, and all-domain behavior remain
covered by `tests/integration`:

```sh
tests/docker/run_compatibility_smoke.sh
```

The Docker test intentionally uses the official Home Assistant image through
`tests/docker/docker-compose.yml`; do not introduce a custom Dockerfile. Do not
leave Compose sessions running after verification. Inspect the generated result
and logs, not only the shell exit code.

Enable the repository's local pre-commit hook with:

```sh
git config core.hooksPath .githooks
```

CI additionally runs hassfest and HACS validation. Keep `manifest.json`,
`services.yaml`, icons, translations, and workflow action versions compatible
with those validators.

## Agent Working Rules

- Read the relevant implementation and tests before changing behavior.
- Preserve unrelated user changes in a dirty worktree. Never use destructive
  reset/checkout commands unless explicitly requested.
- Use `rg`/`rg --files` for discovery and `apply_patch` for manual edits.
- Keep changes scoped, but follow a cross-layer contract wherever required.
- Reuse Home Assistant APIs, selectors, templates, script execution, and entity
  validation instead of hand-rolling substitutes.
- Treat config-entry options, restored states, source attributes, Jinja output,
  GeoJSON, and service payloads as hostile or stale input.
- Do not block Home Assistant's event loop with file/network I/O.
- Do not expose secrets, credentials, or private source attributes in logs or
  diagnostics beyond what the user explicitly configured.
- Report exactly which test layers ran. Never describe Docker coverage as
  passed when only mocked pytest integration tests ran.
- When a Home Assistant API may have changed, verify against the installed test
  dependency and, when necessary, current official Home Assistant docs/source.

## Definition Of Done

A change is done only when the UI can create and edit it without losing prior
configuration, runtime behavior matches Home Assistant's domain contract,
malformed legacy data remains removable, generated entities stay on the right
Device, both translations are complete, focused tests pass, the full local
suite passes, and Docker smoke passes when the blast radius reaches live Home
Assistant platform behavior.
