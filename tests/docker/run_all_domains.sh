#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.yml"
CONFIG_DIR="$SCRIPT_DIR/all_domains/config"
RESULT_FILE="$SCRIPT_DIR/all_domains/config/all-domains-result.json"

docker compose -f "$COMPOSE_FILE" --profile integration rm -sf homeassistant-all-domains
rm -rf "$CONFIG_DIR/.storage"
rm -f \
  "$CONFIG_DIR/.HA_VERSION" \
  "$CONFIG_DIR/.ha_run.lock" \
  "$CONFIG_DIR"/*.db \
  "$CONFIG_DIR"/*.db-* \
  "$CONFIG_DIR"/*.log \
  "$CONFIG_DIR"/*.log.* \
  "$RESULT_FILE"
docker compose -f "$COMPOSE_FILE" --profile integration up \
  --force-recreate \
  --exit-code-from homeassistant-all-domains \
  homeassistant-all-domains

python3 - "$RESULT_FILE" <<'PY'
import json
import sys
from pathlib import Path

result_path = Path(sys.argv[1])
if not result_path.is_file():
    raise SystemExit("Docker smoke result was not created")

result = json.loads(result_path.read_text())
print(json.dumps(result, indent=2, sort_keys=True))
if not result.get("success"):
    raise SystemExit("Docker all-domain smoke test failed")
PY
