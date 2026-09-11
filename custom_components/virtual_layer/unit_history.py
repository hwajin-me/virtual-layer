"""Explicit, scoped handling of recorded sensor statistics on UI unit edits."""

import asyncio

from homeassistant.const import CONF_UNIT_OF_MEASUREMENT
from homeassistant.helpers.template import Template, TemplateError

from .const import CONF_ATTRIBUTES, CONF_NATIVE_TEMPLATES


def unit_template(entity):
    templates = entity.get(CONF_NATIVE_TEMPLATES, {})
    return next((value for key, value in reversed(list(templates.items()))
                 if key in ("unit", "unit_of_measurement", "native_unit_of_measurement") and value), None)


def unit_definition(entity):
    """Compare persisted definitions, not changing source states."""
    return (entity.get(CONF_UNIT_OF_MEASUREMENT) or None,
            entity.get(CONF_ATTRIBUTES, {}).get(CONF_UNIT_OF_MEASUREMENT) or None,
            unit_template(entity))


def configured_unit(hass, entity):
    """Resolve a unit for preview; unresolved Jinja must never rewrite history."""
    template = unit_template(entity)
    if template:
        try:
            value = Template(template, hass).async_render(strict=True)
        except (TemplateError, ValueError, TypeError):
            return False, None
    else:
        value = entity.get(CONF_UNIT_OF_MEASUREMENT) or entity.get(CONF_ATTRIBUTES, {}).get(CONF_UNIT_OF_MEASUREMENT)
    if value is None or value == "":
        return True, None
    if not isinstance(value, str) or value.lower() in ("unknown", "unavailable", "none"):
        return False, None
    return True, value


async def statistics_snapshot(hass, entity_id):
    """Read only the selected entity's metadata on Recorder's executor."""
    if "recorder" not in hass.config.components:
        return None
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.statistics import get_metadata
    from functools import partial

    metadata = await get_instance(hass).async_add_executor_job(
        partial(get_metadata, hass, statistic_ids={entity_id}))
    record = metadata.get(entity_id)
    return dict(record[1]) if record else None


async def apply_statistics_policy(hass, entity_id, policy, unit, expected):
    """Use HA APIs, guard stale metadata, and never modify raw state history."""
    if policy == "keep":
        return
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.statistics import (
        STATISTIC_UNIT_TO_UNIT_CONVERTER, async_change_statistics_unit,
        async_update_statistics_metadata,
    )
    current = await statistics_snapshot(hass, entity_id)
    if current is None or current != expected or current.get("source") != "recorder":
        raise ValueError("Statistics changed; reopen the unit handling step")
    if policy == "convert":
        await async_change_statistics_unit(hass, entity_id,
            new_unit_of_measurement=unit,
            old_unit_of_measurement=current["unit_of_measurement"])
    elif policy == "relabel":
        converter = STATISTIC_UNIT_TO_UNIT_CONVERTER.get(unit)
        async_update_statistics_metadata(hass, entity_id,
            new_unit_class=converter.UNIT_CLASS if converter else None,
            new_unit_of_measurement=unit)
    else:
        raise ValueError("Invalid statistics policy")
    # An empty queue does not mean the currently executing statistics job has
    # committed. Enqueue an unconditional barrier before checking its result.
    from homeassistant.components.recorder.tasks import SynchronizeTask
    committed = hass.loop.create_future()
    get_instance(hass).queue_task(SynchronizeTask(committed))
    await asyncio.wait_for(committed, timeout=30)
    updated = await statistics_snapshot(hass, entity_id)
    if updated is None or updated["unit_of_measurement"] != unit:
        raise ValueError("Statistics update was not applied")
