# Project Core
- Home Assistant custom integration domain: `virtual_layer`; source under `custom_components/virtual_layer/`.
- UI-only configuration invariant: config entries/options and integration storage back virtual devices/entities; YAML entity loading is intentionally unsupported.
- Config flow and entity CRUD/backup/restore live mainly in `config_flow.py`; persisted config normalization in `cfg.py`; setup, services, state-only domains, and removal cleanup in `__init__.py`.
- Domain-specific platform modules coexist with a generic fallback. State-only domains are registered directly instead of forwarded as HA entity platforms.
- Tests split into `tests/unit/`, `tests/integration/`, and a real Home Assistant Docker Compose smoke environment in `tests/docker/`.
- Read `mem:tech_stack` for runtime pins/tooling, `mem:conventions` for implementation rules, `mem:suggested_commands` for common commands, and `mem:task_completion` before handing off changes.