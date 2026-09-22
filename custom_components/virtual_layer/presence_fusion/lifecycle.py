"""Presence profile lifecycle within the existing Virtual Layer integration."""

import voluptuous as vol
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.service import async_set_service_schema
from homeassistant.helpers.storage import Store

from ..device_metadata import async_get_virtual_device
from .adapters import number
from .coordinator import KEY, FusionCoordinator, FusionRuntime

PLATFORMS = ["sensor", "binary_sensor", "device_tracker", "image"]


async def setup(hass, entry):
    coordinator = FusionCoordinator(hass, entry)
    entry.runtime_data = FusionRuntime(coordinator)
    try:
        metadata = coordinator.metadata
        identifiers = {("virtual_layer", metadata.get("device_id") or entry.entry_id)}
        registry = dr.async_get(hass)
        previous = {
            row.device_id
            for row in er.async_entries_for_config_entry(
                er.async_get(hass), entry.entry_id
            )
            if row.unique_id.startswith(f"{entry.entry_id}:presence_fusion:")
            and row.device_id
        }
        for device_id in previous:
            target = async_get_virtual_device(
                registry, metadata.get("device_id") or entry.entry_id, entry.entry_id
            )
            if target and target.id != device_id:
                raise ValueError("device_id_collision")
            registry.async_update_device(device_id, new_identifiers=identifiers)
        await coordinator.start()
        if not coordinator.zone_ids:
            entity_registry = er.async_get(hass)
            for row in er.async_entries_for_config_entry(
                entity_registry, entry.entry_id
            ):
                if row.unique_id in {
                    f"{entry.entry_id}:presence_fusion:zone",
                    f"{entry.entry_id}:presence_fusion:map",
                }:
                    entity_registry.async_remove(row.entity_id)
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        device = async_get_virtual_device(
            registry, metadata.get("device_id") or entry.entry_id, entry.entry_id
        )
        if device:
            registry.async_update_device(
                device.id,
                area_id=metadata.get("area_id"),
                via_device_id=metadata.get("parent_device"),
            )
    except BaseException:
        await coordinator.stop()
        await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
        raise
    entry.async_on_unload(entry.add_update_listener(reload))
    register_services(hass)
    return True


async def reload(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)


async def unload(hass, entry):
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.coordinator.stop()
        return True
    return False


async def remove(hass, entry):
    await Store(
        hass, 1, f"virtual_layer.presence_fusion.{entry.entry_id}"
    ).async_remove()


def register_services(hass):
    async def handle(call):
        entry = hass.config_entries.async_get_entry(call.data["config_entry_id"])
        if (
            entry is None
            or entry.domain != "virtual_layer"
            or not entry.data.get(KEY)
            or not isinstance(getattr(entry, "runtime_data", None), FusionRuntime)
            or entry.runtime_data.coordinator.stopped
        ):
            raise ServiceValidationError(
                "Select a loaded Presence Fusion Virtual Layer entry"
            )
        coordinator = entry.runtime_data.coordinator
        try:
            if call.service in {"set_primary", "presence_fusion_set_primary"}:
                coordinator.engine.set_primary(
                    call.data["tracked_device_id"],
                    call.data.get(
                        "duration", coordinator.engine.s.manual_override_default_s
                    ),
                    coordinator.clock.utc(),
                    coordinator.clock.monotonic(),
                )
            else:
                coordinator.engine.clear_override()
        except ValueError as err:
            raise ServiceValidationError(
                "Select an available GPS candidate and a duration from 1 to 86400 seconds"
            ) from err
        coordinator.publish()

    for domain, prefix in [
        ("presence_fusion", ""),
        ("virtual_layer", "presence_fusion_"),
    ]:
        for action in ("set_primary", "clear_primary_override"):
            schema = {vol.Required("config_entry_id"): str}
            if action == "set_primary":
                schema.update(
                    {
                        vol.Required("tracked_device_id"): str,
                        vol.Optional("duration"): vol.All(
                            number, vol.Range(min=1, max=86400)
                        ),
                    }
                )
            if not hass.services.has_service(domain, prefix + action):
                hass.services.async_register(
                    domain, prefix + action, handle, schema=vol.Schema(schema)
                )
            if domain == "presence_fusion":
                # The compatibility namespace has no standalone integration.
                # Supply its schema through HA's dynamic service API so the
                # Actions UI never attempts to load a nonexistent manifest.
                fields = {
                    "config_entry_id": {
                        "name": "Config entry",
                        "description": "This person's Virtual Layer entry.",
                        "required": True,
                        "selector": {"config_entry": {"integration": "virtual_layer"}},
                    }
                }
                if action == "set_primary":
                    fields.update(
                        {
                            "tracked_device_id": {
                                "name": "Physical device UUID",
                                "description": "UUID shown in the physical-device selector.",
                                "required": True,
                                "selector": {"text": {}},
                            },
                            "duration": {
                                "name": "Duration",
                                "description": "Seconds; omit for the configured default.",
                                "selector": {
                                    "number": {"min": 1, "max": 86400, "mode": "box"}
                                },
                            },
                        }
                    )
                async_set_service_schema(
                    hass,
                    domain,
                    action,
                    {
                        "name": action.replace("_", " ").title(),
                        "description": "Presence Fusion compatibility alias for the corresponding Virtual Layer action.",
                        "fields": fields,
                    },
                )
