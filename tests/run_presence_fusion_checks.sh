#!/bin/sh
# Run from the repository root; Docker uses disposable official HA containers.
set -eu
PYTHONPATH=. .venv/bin/pytest tests/unit tests/integration -q
COVERAGE_FILE=.coverage.presence_fusion PYTHONPATH=. .venv/bin/pytest tests/unit/test_presence_fusion.py tests/integration/test_presence_fusion_ha.py \
  --cov=custom_components.virtual_layer.presence_fusion --cov-branch --cov-report=term-missing -q
.venv/bin/python -m compileall custom_components/virtual_layer tests -q
.venv/bin/ruff check custom_components/virtual_layer tests --select E9,F63,F7,F82
.venv/bin/ruff check custom_components/virtual_layer/presence_fusion \
  custom_components/virtual_layer/diagnostics.py tests/unit/test_presence_fusion.py \
  tests/integration/test_presence_fusion_ha.py tests/conftest.py
.venv/bin/ruff format --check custom_components/virtual_layer/presence_fusion \
  custom_components/virtual_layer/diagnostics.py tests/unit/test_presence_fusion.py \
  tests/integration/test_presence_fusion_ha.py tests/docker/presence_fusion_smoke.py
git diff --check
sh tests/docker/run_compatibility_smoke.sh
sh tests/docker/run_presence_fusion_smoke.sh
