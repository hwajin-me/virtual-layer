"""Dedicated physical-device/source wizard shared by Config and Options flows."""

from copy import deepcopy
from dataclasses import asdict
from uuid import uuid4

import voluptuous as vol
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from ..device_metadata import (
    async_get_virtual_device,
    configuration_url_or_none,
    valid_parent_device,
)
from ..geojson_catalog import async_get_catalog
from ..geojson_flow import GeoJSONFlow, choice
from ..polygon import polygon_clearance
from .configuration import isolate_devices
from .models import Settings
from .movement import distance

KEY = "presence_fusion"


def select(options):
    return selector.SelectSelector(
        selector.SelectSelectorConfig(options=options, mode="dropdown")
    )


def form_schema(values, fields):
    return vol.Schema(
        {
            vol.Optional(key, default=values.get(key, default)): validator
            for key, default, validator in fields
        }
    )


class FusionFlow(GeoJSONFlow):
    async def _fusion_home_radius(self):
        home = self.hass.states.get("zone.home")
        radius = home.attributes.get("radius", 100) if home else 100
        config = self._fusion.get("zones", {})
        if not isinstance(config, dict) or not config.get("home"):
            return radius
        catalog = await async_get_catalog(self.hass)
        zones = catalog.selected([config["home"]])
        if not home or not zones or config["home"] not in config.get("catalog_ids", []):
            raise ValueError("geojson_home")
        center = (
            float(home.attributes["latitude"]),
            float(home.attributes["longitude"]),
        )
        if polygon_clearance(*center, zones) < 0:
            raise ValueError("geojson_home")
        return max(
            distance(center, (y, x))
            for z in zones
            for p in z["polygons"]
            for x, y in p["outer"]
        )

    def _fusion_init(self):
        if not hasattr(self, "_fusion"):
            entry = getattr(self, "config_entry", None)
            self._fusion = (
                deepcopy(dict(entry.options).get(KEY, {"devices": [], "settings": {}}))
                if entry
                else {"devices": [], "settings": {}}
            )
            if not isinstance(self._fusion, dict):
                self._fusion = {"devices": [], "settings": {}}
            devices = self._fusion.get("devices")
            if not isinstance(devices, list):
                devices = []
            repaired = []
            seen = set()
            for index, raw in enumerate(devices):
                d = deepcopy(raw) if isinstance(raw, dict) else {}
                d.setdefault("id", str(uuid4()))
                d.setdefault("name", f"Device {index + 1}")
                d.setdefault("priority", index)
                d.setdefault("candidate", False)
                if not isinstance(d["id"], str) or not d["id"] or d["id"] in seen:
                    d["id"] = str(uuid4())
                seen.add(d["id"])
                if not isinstance(d["name"], str):
                    d["name"] = f"Device {index + 1}"
                if type(d["priority"]) is not int:
                    d["priority"] = index
                if not isinstance(d["candidate"], bool):
                    d["candidate"] = False
                d["sources"] = (
                    [
                        s
                        for s in d.get("sources", [])
                        if isinstance(s, dict)
                        and all(
                            isinstance(s.get(k), str)
                            for k in ("id", "kind", "entity_id")
                        )
                    ]
                    if isinstance(d.get("sources"), list)
                    else []
                )
                repaired.append(d)
                registry = er.async_get(self.hass)
                for source in d["sources"]:
                    if (
                        isinstance(source.get("registry_id"), str)
                        and source["registry_id"]
                    ):
                        row = registry.entities.get_entry(source["registry_id"])
                        if row:
                            source["entity_id"] = row.entity_id
            self._fusion["devices"] = repaired
            if not isinstance(self._fusion.get("settings"), dict):
                self._fusion["settings"] = {}
            if not isinstance(self._fusion.get("metadata", {}), dict):
                self._fusion["metadata"] = {}
            self._fusion_device = None
            self._fusion_source = None

    async def async_step_fusion(self, user_input=None):
        self._fusion_init()
        errors = {}
        if user_input:
            action = user_input["action"]
            if action == "add":
                if len(self._fusion["devices"]) >= 16:
                    errors["action"] = "fusion_limit"
                else:
                    self._fusion_device = {"id": str(uuid4()), "sources": []}
                    return await self.async_step_fusion_device()
            elif action == "settings":
                return await self.async_step_fusion_settings()
            elif action == "metadata":
                return await self.async_step_fusion_metadata()
            elif action == "manage_geojson":
                self._geo_return = "fusion"
                return await self.async_step_geojson()
            elif action == "zones":
                return await self.async_step_fusion_zones()
            elif action in {"edit", "delete"}:
                self._fusion_action = action
                return await self.async_step_fusion_choose_device()
            elif action == "save":
                _, invalid = isolate_devices(self._fusion["devices"])
                try:
                    radius = await self._fusion_home_radius()
                    if Settings(**self._fusion.get("settings", {})).errors(radius):
                        invalid.append("invalid_settings")
                except (KeyError, TypeError, ValueError):
                    invalid.append("invalid_settings")
                if len(self._fusion["devices"]) > 16:
                    errors["action"] = "fusion_limit"
                elif invalid:
                    errors["action"] = "fusion_invalid_config"
                elif not any(
                    d.get("candidate") and any(s["kind"] == "gps" for s in d["sources"])
                    for d in self._fusion["devices"]
                ):
                    errors["action"] = "fusion_gps_required"
                else:
                    entry = getattr(self, "config_entry", None)
                    if entry:
                        options = deepcopy(dict(entry.options))
                        options[KEY] = self._fusion
                        return self.async_create_entry(title="", data=options)
                    return self.async_create_entry(
                        title=self._pending_title,
                        data={**self._pending_data, KEY: True},
                        options={KEY: self._fusion},
                    )
        return self.async_show_form(
            step_id="fusion",
            data_schema=vol.Schema(
                {
                    vol.Required("action", default="add"): choice(
                        [
                            "add",
                            "edit",
                            "delete",
                            "metadata",
                            "settings",
                            "manage_geojson",
                            "zones",
                            "save",
                        ],
                        "fusion_action",
                    )
                }
            ),
            errors=errors,
            description_placeholders={"count": str(len(self._fusion["devices"]))},
        )

    async def async_step_fusion_zones(self, user_input=None):
        catalog = await async_get_catalog(self.hass)
        values = user_input or self._fusion.get("zones", {})
        if not isinstance(values, dict):
            values = {}
        errors = {}
        if user_input:
            ids = user_input.get("catalog_ids", [])
            home = user_input.get("home", "")
            if home and (home not in ids or not catalog.selected([home])):
                errors["home"] = "geojson_home"
            elif home:
                state = self.hass.states.get("zone.home")
                try:
                    if (
                        not state
                        or polygon_clearance(
                            float(state.attributes["latitude"]),
                            float(state.attributes["longitude"]),
                            catalog.selected([home]),
                        )
                        < 0
                    ):
                        raise ValueError
                except (KeyError, TypeError, ValueError):
                    errors["home"] = "geojson_home"
            if not errors:
                self._fusion["zones"] = dict(user_input)
                return await self.async_step_fusion()
        return self.async_show_form(
            step_id="fusion_zones",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        "catalog_ids", default=values.get("catalog_ids", [])
                    ): choice(
                        catalog.choices(values.get("catalog_ids", [])), multiple=True
                    ),
                    vol.Optional("home", default=values.get("home", "")): choice(
                        [
                            {"value": "", "label": "zone.home"},
                            *catalog.choices(
                                [values["home"]] if values.get("home") else []
                            ),
                        ]
                    ),
                }
            ),
        )

    async def async_step_fusion_metadata(self, user_input=None):
        errors = {}
        if user_input:
            url = user_input.get("configuration_url", "")
            if url and configuration_url_or_none(url) is None:
                errors["configuration_url"] = "fusion_source"
            device_id = user_input.get("device_id", "").strip()
            entry = getattr(self, "config_entry", None)
            registry = dr.async_get(self.hass)
            existing = (
                async_get_virtual_device(registry, device_id) if device_id else None
            )
            if existing and (
                entry is None or entry.entry_id not in existing.config_entries
            ):
                errors["device_id"] = "fusion_duplicate"
            parent = user_input.get("parent_device")
            previous_id = (
                entry.options.get(KEY, {}).get("metadata", {}).get("device_id")
                if entry
                else None
            )
            if parent and (
                not valid_parent_device(
                    self.hass,
                    parent,
                    previous_id or (entry.entry_id if entry else device_id),
                )
                or (
                    entry
                    and entry.entry_id in registry.async_get(parent).config_entries
                )
            ):
                errors["parent_device"] = "fusion_source"
            if not errors:
                self._fusion["metadata"] = dict(user_input)
                return await self.async_step_fusion()
        fields = [
            (k, "", str)
            for k in (
                "device_id",
                "manufacturer",
                "model",
                "sw_version",
                "hw_version",
                "serial_number",
                "configuration_url",
            )
        ]
        values = user_input or self._fusion.get("metadata", {})
        schema = form_schema(values, fields).schema
        for key, validator in [
            ("area_id", selector.AreaSelector()),
            ("parent_device", selector.DeviceSelector()),
        ]:
            marker = (
                vol.Optional(key, description={"suggested_value": values[key]})
                if values.get(key)
                else vol.Optional(key)
            )
            schema[marker] = validator
        return self.async_show_form(
            step_id="fusion_metadata",
            errors=errors,
            data_schema=vol.Schema(schema),
        )

    async def async_step_fusion_choose_device(self, user_input=None):
        if user_input:
            device = next(
                (d for d in self._fusion["devices"] if d["id"] == user_input["device"]),
                None,
            )
            if device:
                if self._fusion_action == "delete":
                    self._fusion["devices"].remove(device)
                    return await self.async_step_fusion()
                self._fusion_device = deepcopy(device)
                return await self.async_step_fusion_device()
        return self.async_show_form(
            step_id="fusion_choose_device",
            data_schema=vol.Schema(
                {
                    vol.Required("device"): select(
                        [
                            {
                                "value": d["id"],
                                "label": f"{d.get('name', 'Device')} ({d['id']})",
                            }
                            for d in self._fusion["devices"]
                        ]
                    )
                }
            ),
        )

    async def async_step_fusion_device(self, user_input=None):
        d = self._fusion_device
        errors = {}
        if user_input:
            try:
                priority = user_input.get("priority", 1)
                if (
                    (
                        isinstance(priority, bool)
                        or not float(priority).is_integer()
                        or not 0 <= priority <= 9999
                    )
                    or user_input.get("candidate", True)
                    and any(
                        other["id"] != d["id"]
                        and other.get("candidate")
                        and other["priority"] == priority
                        for other in self._fusion["devices"]
                    )
                ):
                    errors["priority"] = "fusion_priority"
                if not user_input.get("name", "").strip():
                    errors["name"] = "required"
                if not errors:
                    d.update(
                        name=user_input["name"].strip(),
                        priority=int(priority),
                        candidate=user_input.get("candidate", True),
                    )
                    return await self.async_step_fusion_sources()
            except (TypeError, ValueError, OverflowError):
                errors["priority"] = "fusion_priority"
        values = {**d, **(user_input or {})}
        return self.async_show_form(
            step_id="fusion_device",
            errors=errors,
            data_schema=form_schema(
                values,
                [
                    ("name", "", str),
                    ("priority", 1, vol.Coerce(float)),
                    ("candidate", True, bool),
                ],
            ),
        )

    async def async_step_fusion_sources(self, user_input=None):
        errors = {}
        if user_input:
            action = user_input["action"]
            if action == "add":
                self._fusion_source = {"id": str(uuid4())}
                return await self.async_step_fusion_source()
            if action in {"edit", "delete"}:
                self._fusion_source_action = action
                return await self.async_step_fusion_choose_source()
            if action == "done":
                d = self._fusion_device
                if d["candidate"] and not any(s["kind"] == "gps" for s in d["sources"]):
                    errors["action"] = "fusion_gps_required"
                else:
                    self._fusion["devices"] = [
                        o for o in self._fusion["devices"] if o["id"] != d["id"]
                    ] + [deepcopy(d)]
                    return await self.async_step_fusion()
        return self.async_show_form(
            step_id="fusion_sources",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Required("action", default="add"): select(
                        ["add", "edit", "delete", "done"]
                    )
                }
            ),
            description_placeholders={
                "count": str(len(self._fusion_device["sources"]))
            },
        )

    async def async_step_fusion_choose_source(self, user_input=None):
        sources = self._fusion_device["sources"]
        if user_input:
            source = next((s for s in sources if s["id"] == user_input["source"]), None)
            if source:
                if self._fusion_source_action == "delete":
                    sources.remove(source)
                    return await self.async_step_fusion_sources()
                self._fusion_source = deepcopy(source)
                return await self.async_step_fusion_source()
        return self.async_show_form(
            step_id="fusion_choose_source",
            data_schema=vol.Schema(
                {
                    vol.Required("source"): select(
                        [
                            {
                                "value": s["id"],
                                "label": s["entity_id"] + " (" + s["kind"] + ")",
                            }
                            for s in sources
                        ]
                    )
                }
            ),
        )

    async def async_step_fusion_source(self, user_input=None):
        errors = {}
        source = self._fusion_source
        if user_input:
            registry = er.async_get(self.hass)
            entity_id = user_input.get("entity_id", "")
            record = registry.async_get(entity_id)
            domain = entity_id.split(".")[0]
            kind = user_input.get("kind", "gps")
            if (
                domain
                not in (
                    {"device_tracker"}
                    if kind == "gps"
                    else {
                        "device_tracker",
                        "sensor",
                        "binary_sensor",
                        "input_boolean",
                        "input_text",
                        "input_select",
                    }
                )
                or (record and record.platform in {"virtual_layer", "presence_fusion"})
                or record is None
                and self.hass.states.get(entity_id) is None
            ):
                errors["entity_id"] = "fusion_source"
            all_sources = [
                s
                for d in self._fusion["devices"]
                if d["id"] != self._fusion_device["id"]
                for s in d["sources"]
            ] + self._fusion_device["sources"]
            if any(
                s["id"] != source["id"] and s["entity_id"] == entity_id
                for s in all_sources
            ):
                errors["entity_id"] = "fusion_duplicate"
            if kind in {"gps", "room"} and any(
                s["id"] != source["id"] and s["kind"] == kind
                for s in self._fusion_device["sources"]
            ):
                errors["kind"] = "fusion_duplicate"
            if len(self._fusion_device["sources"]) >= 64 and not any(
                s["id"] == source["id"] for s in self._fusion_device["sources"]
            ):
                errors["entity_id"] = "fusion_limit"
            if set(user_input.get("positive", "on\nhome").splitlines()) & set(
                user_input.get("negative", "off\nnot_home").splitlines()
            ):
                errors["negative"] = "fusion_mapping"
            for key in ("positive", "negative"):
                if set(user_input.get(key, "").splitlines()) & {
                    "unknown",
                    "unavailable",
                    "None",
                    "",
                }:
                    errors[key] = "fusion_mapping"
            if (
                user_input.get("freshness") == "timestamp_ttl"
                and not user_input.get("timestamp_attribute", "").strip()
            ):
                errors["timestamp_attribute"] = "required"
            try:
                ttl = float(user_input.get("ttl", 300))
                if not 0 < ttl <= 86400:
                    errors["ttl"] = "fusion_number"
            except (TypeError, ValueError):
                errors["ttl"] = "fusion_number"
            mapping = {}
            for line in user_input.get("room_mapping", "").splitlines():
                if "=" not in line:
                    errors["room_mapping"] = "fusion_mapping"
                else:
                    key, value = line.split("=", 1)
                    if not key.strip() or not value.strip():
                        errors["room_mapping"] = "fusion_mapping"
                    mapping[key.strip()] = value.strip()
            if not errors:
                source.update(user_input)
                source["registry_id"] = record.id if record else None
                source["positive"] = user_input.get("positive", "on\nhome").splitlines()
                source["negative"] = user_input.get(
                    "negative", "off\nnot_home"
                ).splitlines()
                source["room_mapping"] = mapping
                self._fusion_device["sources"] = [
                    s for s in self._fusion_device["sources"] if s["id"] != source["id"]
                ] + [deepcopy(source)]
                return await self.async_step_fusion_sources()
        defaults = {**source}
        for key in ("positive", "negative"):
            if isinstance(defaults.get(key), list):
                defaults[key] = "\n".join(str(value) for value in defaults[key])
        if isinstance(defaults.get("room_mapping"), dict):
            defaults["room_mapping"] = "\n".join(
                f"{k}={v}" for k, v in defaults["room_mapping"].items()
            )
        defaults.update(user_input or {})
        text = selector.TextSelector(selector.TextSelectorConfig(multiline=True))
        fields = [
            ("kind", "gps", select(["gps", "wifi", "ble", "room"])),
            ("entity_id", "", selector.EntitySelector()),
            ("attribute", "", str),
            ("positive", "on\nhome", text),
            ("negative", "off\nnot_home", text),
            (
                "freshness",
                "source_managed",
                select(["source_managed", "timestamp_ttl"]),
            ),
            ("timestamp_attribute", "", str),
            ("timestamp_format", "iso", select(["iso", "seconds", "milliseconds"])),
            ("ttl", 300, vol.Coerce(float)),
            ("room_mapping", "", text),
        ]
        return self.async_show_form(
            step_id="fusion_source",
            errors=errors,
            data_schema=form_schema(defaults, fields),
        )

    async def async_step_fusion_settings(self, user_input=None):
        values = {
            **asdict(Settings()),
            **{
                k: v
                for k, v in self._fusion.get("settings", {}).items()
                if k in asdict(Settings())
            },
            **(user_input or {}),
        }
        errors = {}
        if user_input:
            try:
                radius = await self._fusion_home_radius()
                errors = Settings(**values).errors(radius)
            except (KeyError, TypeError, ValueError):
                errors = {"base": "geojson_home"}
            if not errors:
                self._fusion["settings"] = values
                return await self.async_step_fusion()
            errors = {
                key: value if key == "base" else "fusion_number"
                for key, value in errors.items()
            }
        return self.async_show_form(
            step_id="fusion_settings",
            errors=errors,
            data_schema=form_schema(
                values,
                [(k, v, vol.Coerce(float)) for k, v in asdict(Settings()).items()],
            ),
        )
