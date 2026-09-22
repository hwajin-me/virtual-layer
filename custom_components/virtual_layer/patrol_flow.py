"""Teach a route by operating the configured ONVIF camera in an options step."""
import math
from copy import deepcopy

import voluptuous as vol
from homeassistant.helpers import selector

from . import patrol_positions as positions
from .const import ATTR_ENTITY_ID, CONF_ONVIF_PATROL_TARGET


def camera_choices(options):
    from . import config_flow as cf

    result = {}
    for key, label in cf._entity_choices(options).items():
        device, index = cf._find_entity_by_selection_key(options, key)
        if cf._get_ui_entity(options, device, index).get("platform") == "camera":
            result[key] = label
    return result


class PatrolFlow:
    """Only save the route after explicit confirmation; never rewrite media options."""

    async def async_step_patrol_camera(self, user_input=None):
        from . import config_flow as cf

        choices = camera_choices(self.config_entry.options)
        errors = {}
        if user_input is not None:
            key = user_input.get("entity_key")
            if key not in choices:
                errors["base"] = "entity_not_found"
            else:
                device, index = cf._find_entity_by_selection_key(self.config_entry.options, key)
                self._patrol_key = key
                self._patrol_snapshot = deepcopy(cf._get_ui_entity(self.config_entry.options, device, index))
                self._patrol_settings = {
                    CONF_ONVIF_PATROL_TARGET: self._patrol_snapshot.get(CONF_ONVIF_PATROL_TARGET, ""),
                    "onvif_patrol_interval": self._patrol_snapshot.get("onvif_patrol_interval", 30),
                    "onvif_patrol_speed": self._patrol_snapshot.get("onvif_patrol_speed", 0.5),
                }
                try:
                    route = positions.validate_route(self._patrol_snapshot.get(positions.CONF_PATROL_ROUTE))
                    self._patrol_points = route["points"] if route["target"] == self._patrol_settings[CONF_ONVIF_PATROL_TARGET] else []
                except vol.Invalid:
                    self._patrol_points = []
                return await self.async_step_patrol_settings()
        return self.async_show_form(
            step_id="patrol_camera", errors=errors,
            data_schema=vol.Schema({vol.Required("entity_key"): selector.SelectSelector(
                selector.SelectSelectorConfig(options=[{"value": key, "label": label} for key, label in choices.items()]),
            )}),
        )

    async def _async_pause_patrol_editor(self):
        """Do not fight this camera's running patrol/automatic scheduler."""
        component = self.hass.data.get("camera")
        get_entity = getattr(component, "get_entity", None)
        parent = get_entity(self._patrol_snapshot.get(ATTR_ENTITY_ID)) if callable(get_entity) else None
        if parent is not None and callable(getattr(parent, "async_stop_patrol", None)):
            await parent.async_stop_patrol()

    async def async_step_patrol_settings(self, user_input=None):
        if not hasattr(self, "_patrol_snapshot"):
            return await self.async_step_patrol_camera()
        errors = {}
        if user_input is not None:
            previous = self._patrol_settings[CONF_ONVIF_PATROL_TARGET]
            try:
                schema = self._patrol_settings_schema()
                settings = schema(user_input)
                if not all(math.isfinite(settings[field]) for field in ("onvif_patrol_interval", "onvif_patrol_speed")):
                    raise vol.Invalid("Patrol values must be finite")
                target = settings[CONF_ONVIF_PATROL_TARGET]
                if target == self._patrol_snapshot.get(ATTR_ENTITY_ID):
                    raise ValueError("The patrol target must be a separate ONVIF camera")
                await positions.client(self.hass, target)
                await self._async_pause_patrol_editor()
                self._patrol_settings = settings
                if previous != target:
                    self._patrol_points = []
                self._patrol_current = None
                return await self.async_step_patrol_positions()
            except Exception:
                errors["base"] = "patrol_connection_failed"
        return self.async_show_form(
            step_id="patrol_settings", data_schema=self._patrol_settings_schema(), errors=errors,
        )

    def _patrol_settings_schema(self):
        defaults = self._patrol_settings
        target = defaults.get(CONF_ONVIF_PATROL_TARGET)
        return vol.Schema({
            vol.Required(CONF_ONVIF_PATROL_TARGET, **({"default": target} if target else {})): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="camera", integration="onvif"),
            ),
            vol.Required("onvif_patrol_interval", default=defaults["onvif_patrol_interval"]): selector.NumberSelector(
                selector.NumberSelectorConfig(min=5, max=3600, step=1, mode=selector.NumberSelectorMode.BOX),
            ),
            vol.Required("onvif_patrol_speed", default=defaults["onvif_patrol_speed"]): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0.01, max=1, step=0.01, mode=selector.NumberSelectorMode.BOX),
            ),
        })

    async def async_step_patrol_positions(self, user_input=None):
        from . import config_flow as cf

        if not hasattr(self, "_patrol_snapshot"):
            return await self.async_step_patrol_camera()
        errors = {}
        target = self._patrol_settings[CONF_ONVIF_PATROL_TARGET]
        if user_input is not None:
            action = user_input.get("action")
            try:
                if action == "cancel":
                    return await self.async_step_init()
                device, index = cf._find_entity_by_selection_key(self.config_entry.options, self._patrol_key)
                current = cf._get_ui_entity(self.config_entry.options, device, index)
                if current != self._patrol_snapshot:
                    raise cf.InvalidEntitySelection
                if action in {"save", "legacy"}:
                    updated = deepcopy(current)
                    updated.update(self._patrol_settings)
                    if action == "legacy":
                        updated.pop(positions.CONF_PATROL_ROUTE, None)
                    else:
                        updated[positions.CONF_PATROL_ROUTE] = positions.validate_route({
                            "target": target, "points": self._patrol_points,
                        })
                    # A focused update preserves identity, device metadata, helpers,
                    # Frigate policies and concurrent edits to other entities.
                    options = cf._plain_options(self.config_entry.options)
                    options[cf.ATTR_DEVICES][device][index] = updated
                    return self.async_create_entry(data=options)
                await self._async_pause_patrol_editor()
                if action in {"capture", "replace"}:
                    if action == "capture" and len(self._patrol_points) >= positions.MAX_POINTS:
                        raise vol.Invalid("Too many points")
                    name = user_input.get("point_name", "").strip()
                    if not 1 <= len(name) <= 64:
                        raise vol.Invalid("Name the position")
                    coordinates = await positions.capture(self.hass, target)
                    point = {"name": name, "position": coordinates}
                    if action == "capture":
                        self._patrol_points.append(point)
                    else:
                        self._patrol_points[self._patrol_point_index(user_input)] = point
                    self._patrol_current = coordinates
                elif action in {"visit", "remove", "earlier", "later"}:
                    index = self._patrol_point_index(user_input)
                    if action == "visit":
                        await positions.move_to(self.hass, target, self._patrol_points[index]["position"], self._patrol_settings["onvif_patrol_speed"])
                    elif action == "remove":
                        self._patrol_points.pop(index)
                    else:
                        other = index + (-1 if action == "earlier" else 1)
                        if 0 <= other < len(self._patrol_points):
                            self._patrol_points[index], self._patrol_points[other] = self._patrol_points[other], self._patrol_points[index]
                elif action in {"left", "right", "up", "down", "stop"}:
                    await self._async_patrol_jog(action, user_input.get("jog_distance", 0.05))
                    self._patrol_current = None
                else:
                    raise vol.Invalid("Choose an action")
            except cf.InvalidEntitySelection:
                errors["base"] = "entity_not_found"
            except (vol.Invalid, ValueError, IndexError, TypeError):
                errors["base"] = "patrol_invalid_points"
            except Exception:
                errors["base"] = "patrol_command_failed"
        points = self._patrol_points
        choices = [{"value": str(index), "label": f"{index + 1}. {point['name']} (x={point['position']['x']:.4f}, y={point['position']['y']:.4f})"} for index, point in enumerate(points)]
        schema = {
            vol.Required("action", default="capture"): selector.SelectSelector(selector.SelectSelectorConfig(
                options=["left", "right", "up", "down", "stop", "capture", "replace", "visit", "remove", "earlier", "later", "save", "legacy", "cancel"],
                translation_key="patrol_position_action",
            )),
            vol.Optional("point_name", default=f"{len(points) + 1}"): selector.TextSelector(),
            vol.Optional("jog_distance", default=0.05): selector.NumberSelector(selector.NumberSelectorConfig(min=0.01, max=0.2, step=0.01)),
        }
        if choices:
            schema[vol.Optional("point", default=choices[-1]["value"])] = selector.SelectSelector(selector.SelectSelectorConfig(options=choices))
        return self.async_show_form(
            step_id="patrol_positions", data_schema=vol.Schema(schema), errors=errors,
            description_placeholders={"camera": self._patrol_snapshot.get(ATTR_ENTITY_ID, ""), "count": str(len(points)),
                                      "positions": "\n".join(choice["label"] for choice in choices) or "—"},
        )

    def _patrol_point_index(self, user_input):
        index = int(user_input.get("point", -1))
        if not 0 <= index < len(self._patrol_points):
            raise vol.Invalid("Select a position")
        return index

    async def _async_patrol_jog(self, action, distance):
        """Short finite jogs; always request Stop, including on cancellation."""
        target = self._patrol_settings[CONF_ONVIF_PATROL_TARGET]
        distance = float(distance)
        if not math.isfinite(distance) or not 0.01 <= distance <= 0.2:
            raise vol.Invalid("Invalid jog distance")
        await positions.jog(self.hass, target, action, distance, self._patrol_settings["onvif_patrol_speed"])
