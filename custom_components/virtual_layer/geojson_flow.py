"""Shared named GeoJSON library, usable from both Virtual Layer profiles."""

import json
from copy import deepcopy
from uuid import uuid4

import voluptuous as vol
from homeassistant.helpers import selector

from .geojson_catalog import MAX_RECORDS, async_get_catalog


def choice(options, key=None, multiple=False):
    config = {"options": options, "mode": "dropdown", "multiple": multiple}
    if key:
        config["translation_key"] = key
    return selector.SelectSelector(selector.SelectSelectorConfig(**config))


class GeoJSONFlow:
    async def async_step_geojson(self, user_input=None):
        self._geo_catalog = await async_get_catalog(self.hass)
        errors = {}
        if user_input:
            action = user_input["action"]
            if action == "add":
                if len(self._geo_catalog.records) >= MAX_RECORDS:
                    errors["action"] = "geojson_limit"
                else:
                    self._geo_key = str(uuid4())
                    self._geo_record = {}
                    self._geo_revision = self._geo_catalog.revision
                    return await self.async_step_geojson_record()
            elif action in {"edit", "delete"}:
                self._geo_action = action
                return await self.async_step_geojson_choose()
            elif action == "refresh":
                await self._geo_catalog.refresh()
            elif action == "done":
                if getattr(self, "_geo_return", None) == "fusion":
                    return await self.async_step_fusion()
                return await self.async_step_init()
        return self.async_show_form(
            step_id="geojson",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Required("action", default="add"): choice(
                        ["add", "edit", "delete", "refresh", "done"], "geojson_action"
                    )
                }
            ),
            description_placeholders={"count": str(len(self._geo_catalog.records))},
        )

    async def async_step_geojson_choose(self, user_input=None):
        if user_input and user_input["record"] in self._geo_catalog.records:
            self._geo_key = user_input["record"]
            self._geo_revision = self._geo_catalog.revision
            if self._geo_action == "delete":
                return await self.async_step_geojson_delete()
            raw = self._geo_catalog.records[self._geo_key]
            self._geo_record = deepcopy(raw) if isinstance(raw, dict) else {}
            return await self.async_step_geojson_record()
        return self.async_show_form(
            step_id="geojson_choose",
            data_schema=vol.Schema(
                {
                    vol.Required("record"): choice(self._geo_catalog.choices()),
                }
            ),
        )

    async def async_step_geojson_delete(self, user_input=None):
        errors = {}
        if user_input:
            if user_input.get("confirm"):
                try:
                    await self._geo_catalog.save(
                        self._geo_key, None, self._geo_revision
                    )
                    return await self.async_step_geojson()
                except ValueError:
                    errors["base"] = "geojson_conflict"
            else:
                return await self.async_step_geojson()
        return self.async_show_form(
            step_id="geojson_delete",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Required("confirm", default=False): bool,
                }
            ),
        )

    async def async_step_geojson_record(self, user_input=None):
        record = self._geo_record
        defaults = {
            "name": record.get("name", ""),
            "enabled": record.get("enabled", True),
            "priority": record.get("priority", 0),
            "source": record.get("source", ""),
            "geojson": json.dumps(
                record.get("geojson", {}), ensure_ascii=False, indent=2
            )
            if record.get("geojson")
            else "",
        }
        defaults.update(user_input or {})
        errors = {}
        if user_input:
            try:
                priority = user_input.get("priority", 0)
                if isinstance(priority, bool) or not float(priority).is_integer():
                    raise ValueError("geojson_priority")
                if (
                    user_input.get("source", "").strip()
                    and user_input.get("geojson", "").strip()
                ):
                    raise ValueError("geojson_one_source")
                new = {**record, **user_input, "priority": int(priority)}
                await self._geo_catalog.save(self._geo_key, new, self._geo_revision)
                return await self.async_step_geojson()
            except (ValueError, TypeError, RecursionError, OverflowError) as err:
                code = str(err)
                field = {
                    "geojson_name": "name",
                    "geojson_priority": "priority",
                    "geojson_source": "source",
                }.get(code, "geojson")
                errors["base" if code == "geojson_conflict" else field] = (
                    code
                    if code
                    in {
                        "geojson_name",
                        "geojson_priority",
                        "geojson_source",
                        "geojson_conflict",
                        "geojson_limit",
                        "geojson_one_source",
                    }
                    else "geojson_invalid"
                )
        return self.async_show_form(
            step_id="geojson_record",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Required("name", default=defaults["name"]): str,
                    vol.Required("enabled", default=defaults["enabled"]): bool,
                    vol.Required("priority", default=defaults["priority"]): vol.Coerce(
                        float
                    ),
                    vol.Optional("source", default=defaults["source"]): str,
                    vol.Optional(
                        "geojson", default=defaults["geojson"]
                    ): selector.TextSelector(
                        selector.TextSelectorConfig(multiline=True)
                    ),
                }
            ),
        )
