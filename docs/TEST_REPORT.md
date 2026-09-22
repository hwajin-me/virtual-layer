# Presence Fusion verification report

## Shared GeoJSON extension verification (2026-09-22)

This section records the later user-requested extension for both Virtual Layer
profiles. Earlier results below describe the preceding Presence Fusion audit.
The original specification and AGENTS.md remain unchanged.

Modified implementation: new `geojson_catalog.py` and `geojson_flow.py`; shared
selectors in `config_flow.py`; geometry matching in `polygon.py`; catalog
subscriptions in `device_tracker.py` and `image.py`; Fusion `flow.py`,
`coordinator.py`, `engine.py`, `models.py`, `entities.py`, and `lifecycle.py`;
matching English/Korean translations. Documentation: README, GEOJSON_AREAS,
PRESENCE_FUSION, DECISIONS and TEST_MATRIX. Existing unrelated working-tree
changes were preserved.

Tests: new `tests/integration/test_geojson_catalog.py`, updated menu assertions
in `tests/unit/test_config_flow_helpers.py`, and expanded official-container
`tests/docker/presence_fusion_smoke.py`.

| Executed command | Actual result |
|---|---|
| `PYTHONPATH=. .venv/bin/pytest tests/unit tests/integration -q` | **3626 passed**, 1 platform warning, 184.93 s |
| `PYTHONPATH=. .venv/bin/pytest tests/integration/test_geojson_catalog.py -q` | **17 passed**, 0.45 s |
| Focused Fusion + GeoJSON pytest with branch coverage, command below | **122 passed**, 2.43 s; pure engine **234/250 branches (93.6%)** |
| `.venv/bin/python -m compileall custom_components/virtual_layer tests -q` | Exit 0 |
| `.venv/bin/ruff check custom_components/virtual_layer tests --select E9,F63,F7,F82` | All checks passed |
| Ruff check on new catalog/flow, Fusion package and new tests | All configured checks passed |
| Ruff format check on those files and Fusion Docker smoke | 15 files already formatted |
| `git diff --check` | Exit 0 |
| `sh tests/docker/run_compatibility_smoke.sh` | Passed on **HA 2026.9.3** |
| `sh tests/docker/run_presence_fusion_smoke.sh` | Passed on **HA 2026.9.3 / Python 3.14.6**, **11 entities**; shared GeoJSON config flow, polygon Home, SVG, expiry, unknown, reload and unload |

```sh
COVERAGE_FILE=/tmp/geojson-fusion.coverage PYTHONPATH=. .venv/bin/pytest \
  tests/unit/test_presence_fusion.py tests/integration/test_presence_fusion_ha.py \
  tests/integration/test_geojson_catalog.py \
  --cov=custom_components.virtual_layer.presence_fusion --cov-branch \
  --cov-report=json:/tmp/geojson-fusion-coverage.json -q
```

Local versions remain **HA 2026.2.3 / Python 3.14.7**, pytest 9.0.0,
pytest-asyncio 1.3.0, pytest-homeassistant-custom-component 0.13.316 and Ruff
0.16.1. Docker logged the standard custom-integration warning and `rich`
dependency SyntaxWarning; neither final Docker log contained an ERROR or
traceback. Disposable containers were removed after verification. No operational
HA server was deployed/restarted and no git push or account connection occurred.

The initial regression run found four expected-shape failures: the newly added
management menu action and an unnecessary empty `catalog_ids` field. Empty
references are now omitted for legacy configurations; menu tests explicitly
include the new action. The final full run above passed. Its one warning is the
existing `asyncinotify` platform limitation on macOS.

Installation and initial configuration, limits, privacy and extension test
matrix: [GEOJSON_AREAS.md](GEOJSON_AREAS.md). Install/update the existing
`custom_components/virtual_layer` directory using the normal Virtual Layer
installation procedure. Catalog changes apply live; installing changed Python
code requires the user's normal HA restart. HA's native map does not draw these
polygons; the generated SVG image provides the preview. No drawing editor,
universal version compatibility or real-device accuracy guarantee is claimed.

Date: 2026-09-22. This is a Virtual Layer feature, not a separate integration
installation. The supplied 595-line specification was read in full and copied
byte-for-byte to the root `PRESENCE_FUSION_SPEC.md`. `AGENTS.md` was not changed.

## Verified versions

| Environment | Versions | Executed scope |
|---|---|---|
| Local `.venv`, macOS | Home Assistant Core **2026.2.3**, Python **3.14.7**, pytest **9.0.0**, pytest-asyncio **1.3.0**, pytest-homeassistant-custom-component **0.13.316** | Complete unit/integration suite and feature coverage |
| Official `ghcr.io/home-assistant/home-assistant:stable` container | Home Assistant Core **2026.9.3**, Python **3.14.6** | Existing compatibility smoke and dedicated Presence Fusion smoke |
| Static tools | Ruff **0.16.1**, coverage **7.10.6**, pytest-cov **7.0.0** | Syntax, formatting, whitespace and branch measurement |

Minimum targeted/tested Core: 2026.2.3 with Python 3.14. Only the versions above
were actually verified. This does not assert support for all future releases or
full pytest coverage on the container's newer Core. Reproduction dependencies
are in `requirements_presence_fusion_test.txt`.

## Final commands and actual results

The final complete command was:

```sh
sh tests/run_presence_fusion_checks.sh
```

It exited **0**. Its commands and results were:

| Command | Actual result |
|---|---|
| `PYTHONPATH=. .venv/bin/pytest tests/unit tests/integration -q` | **3592 passed**, 1 third-party warning, 189.29 s |
| `COVERAGE_FILE=.coverage.presence_fusion PYTHONPATH=. .venv/bin/pytest tests/unit/test_presence_fusion.py tests/integration/test_presence_fusion_ha.py --cov=custom_components.virtual_layer.presence_fusion --cov-branch --cov-report=term-missing -q` | **105 passed**, 2.48 s; feature statement/branch combined coverage **90%** |
| `.venv/bin/python -m compileall custom_components/virtual_layer tests -q` | Passed |
| `.venv/bin/ruff check custom_components/virtual_layer tests --select E9,F63,F7,F82` | All checks passed |
| `.venv/bin/ruff check custom_components/virtual_layer/presence_fusion custom_components/virtual_layer/diagnostics.py tests/unit/test_presence_fusion.py tests/integration/test_presence_fusion_ha.py tests/conftest.py` | All configured rules passed, including imports and unused variables |
| `.venv/bin/ruff format --check custom_components/virtual_layer/presence_fusion custom_components/virtual_layer/diagnostics.py tests/unit/test_presence_fusion.py tests/integration/test_presence_fusion_ha.py tests/docker/presence_fusion_smoke.py` | 15 files already formatted |
| `git diff --check` | Passed |
| `sh tests/docker/run_compatibility_smoke.sh` | Passed on **HA 2026.9.3**; existing Virtual Layer native/runtime/config-flow regression smoke |
| `sh tests/docker/run_presence_fusion_smoke.sh` | Passed on **HA 2026.9.3 / Python 3.14.6**; real initial flow, 9 native entities on one Device, expiry/unknown, reload, unload |

Additional executed checks:

- English/Korean translation key topology and every Presence Fusion form field's
  description matched. `tests/unit/test_translations.py`: **18 passed** after
  correcting exact English service YAML/catalog wording.
- Specification copy byte identity passed.
- `git diff --exit-code -- AGENTS.md` passed.
- `docker ps` was empty after verification; disposable Compose runs used `--rm`
  and did not leave a running verification session.
- HA service descriptions were read through `async_get_all_descriptions` in the
  integration tests. Dynamic compatibility aliases have explicit HA service
  schemas and do not require a nonexistent standalone manifest.

The full-suite warning is `asyncinotify` explaining that its OS API does not run
on macOS. Containers print HA's normal unverified-custom-integration warning and
a Python 3.14 `rich` dependency SyntaxWarning. No `ERROR` lines occurred in the
final combined Docker/check log. These tests do not imply official HA approval.

## Pure engine branch coverage

Final branch counts were also captured in a separate temporary artifact using:

```sh
COVERAGE_FILE=/tmp/presence-fusion-final.coverage PYTHONPATH=. .venv/bin/pytest \
  tests/unit/test_presence_fusion.py tests/integration/test_presence_fusion_ha.py \
  --cov=custom_components.virtual_layer.presence_fusion --cov-branch \
  --cov-report=json:/tmp/presence-fusion-final-coverage.json -q
```

This capture passed **105 tests in 2.60 s**. The extra capture was necessary
because the shared workspace coverage file was no longer available for the final
JSON export; it does not replace the complete suite or Docker results above.

| Module | Covered / total branches |
|---|---:|
| `engine.py` | 149 / 160 |
| `movement.py` | 46 / 48 |
| `grouping.py` | 26 / 26 |
| `models.py` (settings validation included) | 11 / 14 |
| **Pure engine total** | **232 / 248 = 93.55%** |

The 90% target is met for the pure engine, including settings validation.
Coverage is not 100%; it does not prove every possible input ordering or real
sensor behavior. INV-01–INV-12 and every T01–T48 scenario have actual module and
test-function mappings in `docs/TEST_MATRIX.md`.

## Re-audit on the user's renewed request

The original prompt, repository instructions and all 595 specification lines
were read again. There is no separate PROMPT file in this checkout; the user
instructions and execution contract at the beginning of the supplied spec are
the prompt. Existing passing tests were not treated as proof of completeness.
Ten newly added regression cases failed before the corrections: cross-source
false return, deferred priority, reuse of a segment across the reunion epoch,
left-behind room retention, five malformed Store variants, and sequential GPS
packet arrival breaking a valid close run. All were corrected.

Further tests cover GPS-confirmed local return, local reunion with every GPS
stale, deferred UUID restoration, duplicate/reordered reacquisition packets,
challenger identity changes, repeated-conflict observation counts, continuous
exit hold across nearby boundaries, damaged-record isolation/removal and runtime
feedback without a stored registry ID. The HA Home-zone test now runs in Seoul,
New York and UTC. No arbitration function is mocked.

An additional HA rename regression reproduced loss of GPS availability when
the source resumed under its new entity ID with the same measured fix. Identical
valid measurements now restore availability while retaining their original
timestamp/history; primary source ID changes also trigger publication. Manual
action and default-duration tests reject booleans, non-finite values and values
outside 1–86400 seconds without mutating tracking state.

The first complete re-audit run reported **2 failed, 3555 passed** (173.80 s).
Both failures were in existing general sensor tests after concurrent work changed
display precision to a 0–5 range/default 5. The all-domain fixture's generic 10
was replaced with a valid explicit precision 5. The energy-copy test now checks
the new fallback helper and the actual fallback value 5, retaining its assertion
that an explicit source precision 2 propagates. The user's implementation and
invalid-precision rejection tests were preserved. These were test-contract
updates, not changes to Presence Fusion's expected behavior.

The restart event test now injects its clock before reload begins, so the Store
is read at the simulated restart time, rather than moving UTC backwards from
the already supplied future observations. The real Store and lifecycle still run.

Concurrent independent pytest processes were observed during the audit. The HA
fixture package uses one shared `testing_config` directory, and generic Virtual
Layer metadata previously performed real I/O there. A shared autouse fixture now
routes that metadata to each test's `tmp_path`; it preserves real reads/writes
and reload persistence inside a test. This prevents cross-process entity-ID
collisions without mocking configuration or entity behavior. An intermediate
run under the old setup reported 13 failures / 3558 passes, including those
collisions and concurrently revised boiler-flow expectations. A partial rerun
was interrupted after 436 passes while updating the precision test contract;
it is not counted as a successful complete run.
An intermediate isolated run also encountered three tests for concurrently added
utility-meter previous-month functionality (3586 passed). The final rerun of the
current working tree passed all 3592 collected tests, including that work.

## Failures found and corrected during the original implementation

Tests caught and led to fixes for repeated identical push snapshots causing
tracker writes, source registry API lookup, a config-flow import placement error,
and exact service-description translation mismatches. Additional boundary
regressions verify that poor-accuracy fixes cannot poison speed/path anchors,
late observations cannot rewrite closed buckets, bucket spacing is not mistaken
for a raw observation gap, and interrupted reunion confirmation restarts without
discarding bounded historical companionship needed for safe failover.

The 600 m round-trip test uses a 900-second movement window to retain the entire
synthetic route while its final UTC bucket closes. It still asserts an independent
600 m path length and near-zero displacement; its expected metric was not
weakened. Threshold tests use the real Path score and engine functions. Only a
platform-forwarding failure is injected in the cleanup test; no core decision
function is mocked. There are no feature `skip`/`xfail` cases.

The 8-device × 1 Hz × virtual-hour test checks bounded buckets, segments, pair
state and reason history. Another test performs 1000 identical evaluations and
an explicit HA refresh with no state_changed output writes. No absolute memory
footprint or real-world latency/accuracy guarantee is claimed.

## Changed files and integration decisions

- `custom_components/virtual_layer/presence_fusion/`: models, movement,
  grouping/arbitration/fusion engine, explicit source adapters, UI wizard,
  coordinator, native entities and lifecycle/services.
- `custom_components/virtual_layer/{__init__,config_flow,sensor,binary_sensor,device_tracker}.py`:
  scoped dispatch for the opt-in Device profile.
- `custom_components/virtual_layer/diagnostics.py`, `services.yaml` and
  `translations/{en,ko}.json`: private diagnostics, actions and bilingual UI.
- `tests/unit/test_presence_fusion.py`,
  `tests/integration/test_presence_fusion_ha.py`, and the dedicated Docker smoke:
  real engine and HA event/flow/platform coverage.
- `requirements_presence_fusion_test.txt`, `tests/run_presence_fusion_checks.sh`,
  root spec copy, README link, `docs/PRESENCE_FUSION.md`, `docs/DECISIONS.md`,
  `docs/TEST_MATRIX.md`, and this report.

Concurrent unrelated camera/patrol changes were detected in the shared worktree
and preserved. They are included in repository-wide verification but are not
claimed as Presence Fusion implementation work. No user changes or AGENTS.md
were reset, discarded or overwritten.

Concurrent utility-meter, boiler-flow and general sensor precision work was also
preserved. This audit additionally changed `tests/conftest.py` for real metadata
I/O isolation and synchronized two existing integration-test precision fixtures
with that already-changed sensor contract.

## Unverified scope and remaining limitations

- No physical GPS, Wi-Fi, BLE or ESPresense devices were tested. All observations
  are synthetic; device movement is not proof of the person's movement.
- The container smoke is not the entire pytest matrix on Core 2026.9.3.
- A dedicated mypy/pyright run, hassfest and HACS validation were not executed;
  syntax/Ruff/format/translation checks are reported separately above.
- Timestamp-less stationary GPS sources can expire; restored snapshots cannot
  supply new measurement age. Accuracy defaults and movement/group thresholds
  are tunable heuristics without an accuracy guarantee.
- Recorder may independently persist entity locations. This feature's runtime
  Store excludes coordinate history and download diagnostics omit identifying
  source/location data, but existing Recorder settings are not changed.
- No production HA deployment/restart, git push, external account connection or
  real-device accuracy evaluation was performed.

Installation, initial source mapping, manual override, update/reload/removal,
Person/map usage and troubleshooting are documented in `docs/PRESENCE_FUSION.md`.
