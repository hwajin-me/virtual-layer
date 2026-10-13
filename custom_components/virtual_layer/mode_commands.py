"""Source-specific routing for native choice commands and legacy helpers."""

from collections.abc import Mapping
from functools import lru_cache

from jinja2 import Environment, TemplateSyntaxError

from homeassistant.components.climate import ClimateEntityFeature
from homeassistant.components.fan import FanEntityFeature
from homeassistant.components.humidifier import HumidifierEntityFeature
from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.components.vacuum import VacuumEntityFeature
from homeassistant.components.water_heater import WaterHeaterEntityFeature

from .fan_options import resolve_preset_mode, source_preset_action


MODE_COMMANDS = {
    ("climate", "set_hvac_mode"): ("hvac_mode", ("hvac_modes",)),
    ("climate", "set_fan_mode"): ("fan_mode", ("fan_modes",)),
    ("climate", "set_preset_mode"): ("preset_mode", ("preset_modes",)),
    ("climate", "set_swing_mode"): ("swing_mode", ("swing_modes",)),
    ("climate", "set_swing_horizontal_mode"): ("swing_horizontal_mode", ("swing_horizontal_modes",)),
    ("fan", "set_preset_mode"): ("preset_mode", ("preset_modes",)),
    ("fan", "turn_on"): ("preset_mode", ("preset_modes",)),
    ("humidifier", "set_mode"): ("mode", ("available_modes", "modes")),
    ("media_player", "select_source"): ("source", ("source_list",)),
    ("media_player", "select_sound_mode"): ("sound_mode", ("sound_mode_list",)),
    ("vacuum", "set_fan_speed"): ("fan_speed", ("fan_speed_list",)),
    ("water_heater", "set_operation_mode"): ("operation_mode", ("operation_list",)),
    ("select", "select_option"): ("option", ("options",)),
}
OPTIONAL_MODE_COMMANDS = {
    ("climate", "set_temperature"): ("hvac_mode", ("hvac_modes",)),
    ("water_heater", "set_temperature"): ("operation_mode", ("operation_list",)),
}
SOURCE_MODE_COMMANDS = {**MODE_COMMANDS, **OPTIONAL_MODE_COMMANDS}

MODE_FEATURES = {
    ("climate", "set_fan_mode"): ClimateEntityFeature.FAN_MODE,
    ("climate", "set_preset_mode"): ClimateEntityFeature.PRESET_MODE,
    ("climate", "set_swing_mode"): ClimateEntityFeature.SWING_MODE,
    ("climate", "set_swing_horizontal_mode"): ClimateEntityFeature.SWING_HORIZONTAL_MODE,
    ("fan", "set_preset_mode"): FanEntityFeature.PRESET_MODE,
    ("fan", "turn_on"): FanEntityFeature.PRESET_MODE,
    ("humidifier", "set_mode"): HumidifierEntityFeature.MODES,
    ("media_player", "select_source"): MediaPlayerEntityFeature.SELECT_SOURCE,
    ("media_player", "select_sound_mode"): MediaPlayerEntityFeature.SELECT_SOUND_MODE,
    ("vacuum", "set_fan_speed"): VacuumEntityFeature.FAN_SPEED,
    ("water_heater", "set_operation_mode"): WaterHeaterEntityFeature.OPERATION_MODE,
}

MODE_VALUE_PROPERTIES = frozenset({
    "preset_mode", "fan_mode", "swing_mode", "swing_horizontal_mode", "mode",
    "fan_speed", "source", "sound_mode", "current_option", "current_operation",
})


@lru_cache(maxsize=256)
def wrap_mode_template(source):
    """Preserve opaque scalar strings while keeping actual Jinja None typed."""
    try:
        tokens = list(Environment().lex(source))
    except TemplateSyntaxError:
        return source
    result = []
    protected = 0
    statement = None
    assignment = False
    wrapped_output = False
    for _line, kind, value in tokens:
        if kind == "block_begin":
            statement = ""
            assignment = False
        elif kind == "name" and statement == "":
            statement = value
        elif kind == "operator" and value == "=":
            assignment = True
        elif kind == "block_end":
            if statement in {"macro", "call", "filter"} or (statement == "set" and not assignment):
                protected += 1
            elif statement in {"endmacro", "endcall", "endfilter", "endset"}:
                protected = max(0, protected - 1)
            statement = None
        if kind == "variable_begin":
            wrapped_output = protected == 0
            if wrapped_output:
                value += " {'virtual_layer_mode_value': ("
        elif kind == "variable_end" and wrapped_output:
            value = ")} " + value
        result.append(value)
    return "".join(result)


def source_mode_data(domain, command, state, data):
    """Return a source-safe payload, or None when the choice is unsupported."""
    spec = SOURCE_MODE_COMMANDS.get((domain, command))
    if spec is None:
        return data
    field, attributes = spec
    requested = data.get(field)
    if requested is None and ((domain, command) == ("fan", "turn_on") or (domain, command) in OPTIONAL_MODE_COMMANDS):
        return data
    if state is None or state.state in {"unknown", "unavailable"}:
        return None
    required = MODE_FEATURES.get((domain, command), 0)
    features = state.attributes.get("supported_features")
    if required and features is not None:
        try:
            if isinstance(features, bool) or not int(features) & int(required):
                return None
        except (TypeError, ValueError, OverflowError):
            return None
    modes = next((state.attributes[name] for name in attributes
                  if isinstance(state.attributes.get(name), (list, tuple))), [])
    if domain == "fan":
        resolved = resolve_preset_mode(modes, requested)
    else:
        resolved = requested if isinstance(requested, str) and requested in modes else None
    if resolved is None:
        return None
    return {**data, field: resolved}


def source_mode_action(domain, command, action, source):
    """Guard a generated action against the source's live advertised catalog."""
    if domain == "fan":
        return source_preset_action(action, source)
    field, attributes = SOURCE_MODE_COMMANDS[(domain, command)]
    prefix = (
        "{% set catalog = namespace(value=none) %}"
        f"{{% for name in {attributes!r} %}}"
        f"{{% set choices = state_attr({source!r}, name) %}}"
        "{% if catalog.value is none and choices is sequence and choices is not string "
        "and choices is not mapping %}{% set catalog.value = choices %}{% endif %}"
        "{% endfor %}"
    )
    required = int(MODE_FEATURES.get((domain, command), 0))
    prefix += (
        f"{{% set features = state_attr({source!r}, 'supported_features') %}}"
        f"{{% if {required} and features is not none and (features is boolean "
        f"or features | int(0) | bitwise_and({required}) == 0) %}}"
        "{% set catalog.value = [] %}{% endif %}"
    )
    payload = dict(action)
    if payload.get("data") == "{{ command_data }}" or payload.get("data") == {field: "{{ " + field + " }}"}:
        # Keep evaluation inside the script, including nested choose/variables.
        payload["data"] = "{{ dict(command_data) }}"
    choice = (
        f"and command_data.get({field!r}) is string "
        f"and command_data.get({field!r}) in (catalog.value or [])"
    ).removeprefix("and ")
    if (domain, command) in OPTIONAL_MODE_COMMANDS:
        choice = f"command_data.get({field!r}) is none or ({choice})"
    return {"choose": [{
        "conditions": prefix + f"{{{{ states({source!r}) not in ['unknown', 'unavailable'] and ({choice}) }}}}",
        "sequence": [payload],
    }]}


def repair_source_mode_actions(value, domain, sources, *, allow_mapping=False):
    """Upgrade only simple pass-through helpers; preserve custom action logic."""
    if isinstance(value, list):
        return [repair_source_mode_actions(item, domain, sources, allow_mapping=allow_mapping) for item in value]
    if not isinstance(value, dict):
        return value
    service = value.get("action", value.get("service"))
    command = service.removeprefix(domain + ".") if isinstance(service, str) else ""
    spec = SOURCE_MODE_COMMANDS.get((domain, command)) if service == f"{domain}.{command}" else None
    target = value.get("target")
    ids = target.get("entity_id") if isinstance(target, Mapping) else None
    ids = [ids] if isinstance(ids, str) else ids
    data = value.get("data")
    generated_payload = data == "{{ command_data }}"
    if spec is not None:
        generated_payload |= (
            (allow_mapping or (domain, command) == ("climate", "set_hvac_mode"))
            and data == {spec[0]: "{{ " + spec[0] + " }}"}
        )
        if isinstance(data, str) and isinstance(ids, list) and len(ids) == 1 and isinstance(ids[0], str):
            from .config_flow import _source_command_data_template
            generated_payload |= data == _source_command_data_template(domain, command, ids[0])
    if (
        spec is not None and isinstance(ids, list) and ids
        and all(isinstance(source, str) and source in sources
                and source.startswith(domain + ".") for source in ids)
        and set(value) <= {"action", "service", "target", "data"}
        and set(target) == {"entity_id"}
        and generated_payload
    ):
        return {"sequence": [source_mode_action(domain, command, {
            **value, "target": {"entity_id": source},
            "data": "{{ command_data }}" if domain == "fan" and isinstance(data, Mapping) else data,
        }, source) for source in ids]}
    return {key: repair_source_mode_actions(item, domain, sources, allow_mapping=allow_mapping)
            for key, item in value.items()}


def upgrade_mode_helpers(actions, domain, sources, profile):
    """Use the saved helper baseline to distinguish field maps from edits."""
    baseline = profile.get("command_actions_json") if isinstance(profile, Mapping) else None
    if isinstance(baseline, str):
        from .config_flow import _command_action_mapping
        baseline = _command_action_mapping(baseline)
    baseline = baseline if isinstance(baseline, Mapping) else {}
    return {command: repair_source_mode_actions(spec, domain, sources,
        allow_mapping=profile is True or spec == baseline.get(command))
        for command, spec in actions.items()}
