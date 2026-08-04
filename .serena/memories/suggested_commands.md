# Suggested Commands
- Full local suite: `PYTHONPATH=. .venv/bin/pytest tests/unit tests/integration -q`
- Compile validation: `.venv/bin/python -m compileall custom_components/virtual_layer tests -q`
- Start Docker HA: `docker compose -f tests/docker/docker-compose.yml up -d`
- Restart Docker HA after code changes: `docker compose -f tests/docker/docker-compose.yml restart homeassistant`
- Inspect recent HA errors: `docker compose -f tests/docker/docker-compose.yml logs --since=1m homeassistant | rg -n "ERROR|Traceback|Failed|Exception|Platform error|Setup failed|Unable to set up"`
- Fast file/text discovery: `rg --files`, `rg PATTERN PATH`.