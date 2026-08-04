# Tech Stack
- Python Home Assistant custom component using async config-flow, entity/device registries, templates, and config-entry options.
- Integration dependency: `aiofiles` from `manifest.json`.
- Test dependencies are pinned/listed in `requirements_test.txt`; tests run from the repository virtual environment `.venv`.
- Docker smoke testing uses the official `ghcr.io/home-assistant/home-assistant:stable` image via `tests/docker/docker-compose.yml`; integration source is bind-mounted read-only into HA config.
- Target host is macOS/Darwin with zsh.