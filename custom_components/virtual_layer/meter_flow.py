"""Shared, draft-only utility-meter step for initial setup and options flows."""
from copy import deepcopy
from functools import wraps

import voluptuous as vol
from homeassistant.helpers import selector

from . import meter


def meter_schema(defaults):
    fields = {}
    for key, default in meter.DEFAULTS.items():
        field = meter.PREFIX + key
        value = defaults.get(field, default)
        marker = vol.Optional(field, default=value)
        if isinstance(default, bool):
            control = selector.BooleanSelector()
        elif key == "cycle":
            control = selector.SelectSelector(selector.SelectSelectorConfig(
                options=list(meter.CYCLES), translation_key="utility_meter_cycle",
                mode=selector.SelectSelectorMode.DROPDOWN))
        elif key == "tiers":
            control = selector.ObjectSelector()
        elif isinstance(default, int):
            control = selector.NumberSelector(selector.NumberSelectorConfig(
                min=1 if key == "days" else 0,
                max=36600 if key == "days" else 40319 if key == "offset" else 1e12,
                mode=selector.NumberSelectorMode.BOX,
                step=1 if key in {"days", "offset"} else "any"))
        elif key in {"start", "tariff_entity"}:
            # Suggestions, not defaults: omission must clear a stored value.
            marker = vol.Optional(field, description={"suggested_value": value})
            control = selector.DateTimeSelector() if key == "start" else selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["select", "input_select"]))
        else:
            control = selector.TextSelector()
        fields[marker] = control
    fields[vol.Optional("utility_meter_current_value", default=defaults.get("utility_meter_current_value", ""))] = selector.TextSelector()
    fields[vol.Optional("back", default=False)] = selector.BooleanSelector()
    return vol.Schema(fields)


def meter_form(function):
    """Route enabled sensors through one shared step before persistence."""
    @wraps(function)
    async def wrapped(self, user_input=None):
        from . import config_flow as cf

        if user_input is not None and not getattr(self, "_meter_resuming", False):
            values = cf._merge_entity_form_defaults(cf._flatten_entity_form_sections(user_input), self._entity_defaults)
            if values.get("platform") == "sensor" and values.get("utility_meter_enabled"):
                fingerprint = {key: value for key, value in values.items()
                               if key.startswith(meter.PREFIX) or key in ("entity_id", "entity_name", "device_name", "source_entities_text")}
                if fingerprint != getattr(self, "_meter_reviewed", None):
                    self._meter_pending = deepcopy(values)
                    self._meter_return = function.__name__
                    return await self._async_meter_step()
        return await function(self, user_input)
    return wrapped


class MeterFlow:
    async def async_step_utility_meter(self, user_input=None):
        return await self._async_meter_step(user_input)

    async def async_step_edit_utility_meter(self, user_input=None):
        return await self._async_meter_step(user_input)

    async def _async_meter_step(self, user_input=None):
        pending = getattr(self, "_meter_pending", None)
        if pending is None:
            return self.async_abort(reason="entity_not_found")
        step = "edit_utility_meter" if self._meter_return == "async_step_edit_entity" else "utility_meter"
        errors = {}
        if user_input is not None:
            draft = {**pending, **user_input}
            draft.pop("back", None)
            for key in ("start", "tariff_entity"):
                draft[meter.PREFIX + key] = user_input.get(meter.PREFIX + key, "")
            if user_input.get("back"):
                self._meter_pending = draft
                self._entity_defaults = deepcopy(draft)
                self._meter_reviewed = None
                return await getattr(self, self._meter_return)()
            try:
                if draft.get("utility_meter_enabled"):
                    meter.options(draft)
                    correction = draft.get("utility_meter_current_value", "")
                    if correction != "":
                        try:
                            value = meter.number(correction)
                            if value < 0 and not draft.get("utility_meter_net_consumption"):
                                raise vol.Invalid("Negative total requires net mode")
                        except vol.Invalid as err:
                            raise vol.Invalid(str(err), path=["utility_meter_current_value"]) from err
                self._meter_pending = draft
                self._entity_defaults = deepcopy(draft)
                self._meter_reviewed = {key: value for key, value in draft.items()
                                       if key.startswith(meter.PREFIX) or key in ("entity_id", "entity_name", "device_name", "source_entities_text")}
                self._meter_resuming = True
                try:
                    return await getattr(self, self._meter_return)(draft)
                finally:
                    self._meter_resuming = False
            except vol.Invalid as err:
                errors[str(err.path[0]) if err.path else "base"] = "invalid_domain_options"
            self._meter_pending = draft
            pending = draft
        return self.async_show_form(step_id=step, data_schema=meter_schema(pending), errors=errors)
