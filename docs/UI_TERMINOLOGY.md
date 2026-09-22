# Configuration wording

Keep English and Korean step/selector/error keys identical. Stored values and
step IDs are compatibility identifiers, not labels to display to users.

| Concept | English | Korean |
|---|---|---|
| HA grouping of virtual entities | Virtual Device | 가상 Device |
| Person's phone or watch | Physical device | 실제 기기 |
| Entity supplying presence information | Input signal / source entity | 입력 신호 / 원본 엔티티 |
| Currently selected GPS device | Main GPS device | 주 GPS 기기 |
| One catalog item containing GeoJSON Features | Area set | 구역 모음 |
| Geographic region within a set | Area | 구역 |
| HA room/area assignment | Home Assistant area | Home Assistant 영역 |
| Boundary used to decide Home | Home boundary | 집 경계 |

Use verbs that explain the next action: connect, edit, disconnect, save and
apply. Distinguish disconnecting a source from deleting its original HA entity.
Document immediate shared-catalog saves separately from draft Presence Fusion
changes, which apply only after Save and apply on the main screen.

Each form needs a meaningful title, an explanation of its purpose, and field
help covering applicability, units, examples, defaults and consequences where
needed. Avoid repeated generic help unrelated to the field. Explain validity
periods instead of displaying TTL; explain measurement grouping instead of
displaying buckets. Keep technical names where users must enter them exactly,
such as properties.name, latitude or an attribute/entity ID.

All static selector options must have a translation_key and a label in both
catalogs. Dynamic options should use user-provided names or entity IDs. Localize
the default Home boundary label through HA's translation API without changing
its stored empty-string value. Never put literal JSON braces in translation
examples: HA treats them as formatting placeholders.

Verification: `tests/unit/test_translations.py` checks topology, form coverage,
placeholder validity and service consistency. The real HA flows in
`tests/integration/test_presence_ui_translations.py` check both languages,
field descriptions, selector choices, the default Home boundary label, and
field-specific metadata errors.

## Verification on 2026-09-22

```sh
PYTHONPATH=. .venv/bin/pytest tests/unit/test_translations.py \
  tests/integration/test_presence_ui_translations.py \
  tests/integration/test_presence_fusion_ha.py \
  tests/integration/test_geojson_catalog.py -q
```

Result: **78 passed in 1.26 s**, using HA 2026.2.3 / Python 3.14.7.
The additional existing HA translation-loading tests also passed as part of
a separate run: **25 passed, 287 deselected**.

`sh tests/docker/run_presence_fusion_smoke.sh` passed in the official HA
2026.9.3 / Python 3.14.6 container, including the config flow, shared areas,
11 entities, Home detection, SVG, expiry, reload and unload. This task did not
rerun the all-domain Docker smoke or deploy to an operational HA server.

Compileall, repository Ruff checks (`E9,F63,F7,F82`), configured Ruff checks
and formatting for the changed flow/new test, and `git diff --check` passed.

The pre-existing dirty sensor changes were left intact. Independently rerunning
their affected test files produced **65 passed, 3 failed**:

- `tests/unit/test_water_quality_sensor.py::test_water_measurement_runtime[ph-pH-7-None]`
- `tests/unit/test_water_quality_sensor.py::test_water_measurement_runtime[conductivity-ppm-500-None]`
- `tests/integration/test_automatic_aqi_companions.py::test_existing_measurement_gets_live_companion_and_cleanup[pm25-ug/m^3-5-good]`

Those failures concern sensor class/unit compatibility and are outside the
wording changes. The UI change makes no changes to sensor runtime behavior.

Final complete run: `PYTHONPATH=. .venv/bin/pytest tests/unit tests/integration -q`
returned **3630 passed, 3 failed, 1 warning in 177.08 s**. The three failures
are exactly the sensor cases listed above. The warning is the existing macOS
`asyncinotify` platform limitation. The complete suite is not reported as green.
