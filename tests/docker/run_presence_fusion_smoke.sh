#!/bin/sh
set -eu
TASK_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
docker compose -f "$TASK_DIR/docker-compose.yml" run --rm --no-deps -T \
  -e PYTHONPATH=/config \
  -v "$TASK_DIR/presence_fusion_smoke.py:/tmp/presence_fusion_smoke.py:ro" \
  --entrypoint python homeassistant /tmp/presence_fusion_smoke.py
