"""Privacy-first diagnostics: never export source states or configuration."""

from .presence_fusion.coordinator import FusionRuntime


async def async_get_config_entry_diagnostics(hass, entry):
    runtime = getattr(entry, "runtime_data", None)
    if isinstance(runtime, FusionRuntime):
        return runtime.coordinator.diagnostics()
    return {"integration": "virtual_layer"}
