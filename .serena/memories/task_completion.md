# Task Completion
- Run `PYTHONPATH=. .venv/bin/pytest tests/unit tests/integration -q`.
- Run `.venv/bin/python -m compileall custom_components/virtual_layer tests -q`.
- For setup/platform/config changes, restart the official HA container and scan recent logs for setup errors.
- Confirm translation JSON parses and locale key topology matches English.
- Inspect `git diff --check` and `git status --short`; preserve unrelated dirty-worktree changes.