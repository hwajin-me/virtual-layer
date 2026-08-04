# Conventions
- Preserve UI-only setup; do not reintroduce YAML configuration paths.
- Treat config entry options, backups, and metadata as untrusted/versioned input. Normalize mapping/list/string shapes and skip malformed entities without making removal impossible.
- Keep all supported HA domains in the domain constants and use generic or direct-state fallback where a domain cannot be forwarded as a normal platform.
- Composite entities accept multiple sources, Home Assistant Jinja templates, source-trigger updates, and optional pull intervals.
- Config flow helpers should tolerate malformed legacy options during list/edit/delete so users can recover.
- Translation source of truth is `translations/en.json`; locale files should preserve identical key topology.
- Tests should cover helper behavior in unit tests and actual config entry/service/registry lifecycle in integration tests.