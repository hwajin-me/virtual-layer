"""Preset speed telemetry and stepped humidifier command regressions."""

from unittest.mock import Mock

import pytest
from homeassistant.helpers.template import Template
from homeassistant.core import State

from custom_components.virtual_layer.config_flow import (
    _entity_schema, _flatten_entity_form_sections, _parse_command_actions,
    _build_entity_config, _entity_form_defaults,
    _reference_entity_defaults, CONF_COMMAND_ACTIONS_JSON, CONF_NATIVE_VALUE_TEMPLATES,
    _fan_manual_preset_prefix, _xiaomi_fan_percentage_template,
    _xiaomi_fan_number_value_template, _apply_fan_source_roles,
    _apply_matter_fan_percentage_helper, _apply_matter_fan_level_helper,
    _fan_reported_percentage_prefix, _fan_source_role_defaults,
    _xiaomi_fan_availability_template,
    _native_reference_templates,
)
from custom_components.virtual_layer.fan import FAN_SCHEMA, VirtualFan
from custom_components.virtual_layer.fan_options import manual_preset_mode
from custom_components.virtual_layer.fan_options import (
    migrate_legacy_fan_attributes,
    normalize_preset_modes,
)
from custom_components.virtual_layer.humidifier import HUMIDIFIER_SCHEMA, VirtualHumidifier


@pytest.mark.parametrize("paired", [False, True])
@pytest.mark.parametrize("mode", ["Auto", "Sleep", "Manual"])
async def test_speed_command_selects_advertised_manual_before_write(hass, paired, mode):
    source = "fan.purifier"
    number = "number.favorite_speed"
    hass.states.async_set(source, "on", {
        "preset_modes": ["Auto", "Sleep", "Manual"], "preset_mode": mode,
        "percentage": 43, "percentage_step": 1, "supported_features": 57,
    })
    hass.states.async_set(number, "1500", {"min": 300, "max": 2200, "step": 1})
    defaults = _reference_entity_defaults(hass, [source, number] if paired else [source])
    entity = VirtualFan(FAN_SCHEMA({
        "name": "Purifier", "initial_value": "on", "speed_count": 100,
        "modes": ["Auto", "Sleep", "Manual"],
        "command_actions": _parse_command_actions(defaults[CONF_COMMAND_ACTIONS_JSON], "fan"),
    }), False)
    entity.hass = hass
    entity._create_state(entity._config)
    entity._schedule_state_update = Mock()
    calls = []

    async def capture(call):
        calls.append((call.domain, call.service, dict(call.data)))

    for domain, service in [("fan", "set_preset_mode"), ("fan", "set_percentage"),
                            ("fan", "turn_off"), ("number", "set_value")]:
        hass.services.async_register(domain, service, capture)
    await entity.async_set_percentage(61)
    if mode != "Manual":
        assert calls.pop(0) == ("fan", "set_preset_mode", {
            "entity_id": [source], "preset_mode": "Manual",
        })
    assert [(domain, service) for domain, service, _ in calls] == [
        ("number", "set_value") if paired else ("fan", "set_percentage")
    ]
    assert entity.preset_mode == "Manual"
    calls.clear()
    await entity.async_set_percentage(0)
    assert all(service != "set_preset_mode" for _, service, _ in calls)


@pytest.mark.parametrize("number_domain", ["number", "input_number"])
@pytest.mark.parametrize("power", ["on", "off"])
async def test_number_speed_write_without_advertised_manual_mode(hass, number_domain, power):
    source = "fan.purifier"
    number = f"{number_domain}.speed"
    hass.states.async_set(source, power, {
        "preset_modes": ["Auto", "Sleep"], "preset_mode": "Auto", "supported_features": 56,
    })
    hass.states.async_set(number, "40", {"min": 0, "max": 100, "step": 1, "unit_of_measurement": "%"})
    defaults = _reference_entity_defaults(hass, [source, number])
    entity = VirtualFan(FAN_SCHEMA({
        "name": "Purifier", "initial_value": power, "speed_count": 100,
        "modes": ["Auto", "Sleep"],
        "command_actions": _parse_command_actions(defaults[CONF_COMMAND_ACTIONS_JSON], "fan"),
    }), False)
    entity.hass = hass
    entity._create_state(entity._config)
    entity._schedule_state_update = Mock()
    calls = []

    async def capture(call):
        calls.append((call.domain, call.service, dict(call.data)))

    for domain, service in [("fan", "turn_on"), ("fan", "set_preset_mode"), (number_domain, "set_value")]:
        hass.services.async_register(domain, service, capture)
    await entity.async_set_percentage(61)
    expected = [] if power == "on" else [("fan", "turn_on", {"entity_id": [source]})]
    expected.append((number_domain, "set_value", {"entity_id": [number], "value": 61}))
    assert calls == expected


@pytest.mark.parametrize("mode", ["Auto", "Sleep"])
def test_automatic_rpm_telemetry_prefers_selected_number(hass, mode):
    hass.states.async_set("fan.purifier", "on", {
        "preset_modes": ["Auto", "Sleep", "Favorite"], "preset_mode": mode,
        "motor_speed": 2200, "supported_features": 56,
    })
    hass.states.async_set("number.favorite_speed", "1460", {"min": 300, "max": 2200, "step": 1})
    defaults = _reference_entity_defaults(hass, ["fan.purifier", "number.favorite_speed"])
    template = Template(defaults[CONF_NATIVE_VALUE_TEMPLATES]["percentage"], hass)
    assert template.async_render(parse_result=True) == 61
    hass.states.async_set("fan.purifier", "off")
    assert template.async_render(parse_result=True) == 0


@pytest.mark.parametrize("mode", ["Auto", "Manual"])
def test_missing_number_bounds_fall_back_to_fan_rpm_bounds(hass, mode):
    hass.states.async_set("fan.purifier", "on", {
        "preset_mode": mode, "motor_speed": 500, "max_rpm": 1000,
    })
    hass.states.async_set("number.rpm", "unavailable")
    template = Template(_xiaomi_fan_percentage_template("fan.purifier", "number.rpm", "rpm"), hass)
    assert template.async_render() == 50  # 50.5 rounds to the nearest even integer.
    hass.states.async_set("fan.purifier", "on", {"preset_mode": mode, "motor_speed": 1000, "max_rpm": 1000})
    assert template.async_render() == 100


@pytest.mark.parametrize("step, expected", [(None, 55), (10, 50)])
async def test_humidity_step_controls_ui_and_outgoing_command(hass, step, expected):
    config = {"name": "Humidifier", "initial_value": "on", "min_humidity": 0, "max_humidity": 100, "command_actions": {
        "set_humidity": [{"action": "humidifier.set_humidity", "data": {"humidity": "{{ humidity }}"}}],
    }}
    if step is not None:
        config["target_humidity_step"] = step
    entity = VirtualHumidifier(HUMIDIFIER_SCHEMA(config), False)
    entity.hass = hass
    entity._create_state(entity._config)
    entity.async_write_ha_state = Mock()
    calls = []

    async def capture(call):
        calls.append(call.data["humidity"])

    hass.services.async_register("humidifier", "set_humidity", capture)
    await entity.async_set_humidity(53)
    assert entity.target_humidity_step == (step or 5)
    assert entity.target_humidity == expected
    assert calls == [expected]
    values = _flatten_entity_form_sections(_entity_schema({"platform": "humidifier"})({}))
    assert Template(values[CONF_NATIVE_VALUE_TEMPLATES]["target_humidity_step"], hass).async_render() == 5


def test_humidity_step_survives_form_save_and_reopen():
    values = _flatten_entity_form_sections(_entity_schema({"platform": "humidifier"})({}))
    values.update({"platform": "humidifier", "device_name": "Bedroom", "entity_name": "Humidity"})
    values[CONF_NATIVE_VALUE_TEMPLATES]["target_humidity_step"] = "{{ 10 }}"
    device, config = _build_entity_config(values)
    reopened = _flatten_entity_form_sections(_entity_schema(_entity_form_defaults(device, config))({}))
    assert reopened[CONF_NATIVE_VALUE_TEMPLATES]["target_humidity_step"] == "{{ 10 }}"


@pytest.mark.parametrize("manual", [
    "Manual", " MANUAL ", "manual_mode", "Manual-Mode", "manual\tmode",
    "Normal", "Normal mode", "수동", "수동 모드", "일반", "일반 모드",
    "Favorite", "FAVOURITE", "즐겨찾기",
])
def test_manual_alias_catalog_matches_runtime_and_generated_helpers(hass, manual):
    modes = ["Auto", "Sleep", "Smart", "Nature", "Eco", "Pet", "Turbo", manual]
    hass.states.async_set("fan.aliases", "on", {"preset_modes": modes, "preset_mode": "Auto"})
    assert manual_preset_mode(modes, "Auto") == manual
    assert Template(_fan_manual_preset_prefix("fan.aliases") + "{{ [manual] }}", hass).async_render() == [manual]


@pytest.mark.parametrize("modes", [None, 42, "Manual", {"Manual": 1}, [None, {}, True, 2],
                                      ["Auto", "Sleep", "Eco", "Turbo", "Nature", "Smart", "Pet", "Custom", "Low"]])
def test_unrecognized_or_malformed_modes_never_invent_manual(hass, modes):
    hass.states.async_set("fan.aliases", "on", {"preset_modes": modes, "preset_mode": "Manual"})
    assert manual_preset_mode(modes, "Manual") is None
    assert Template(_fan_manual_preset_prefix("fan.aliases") + "{{ manual }}", hass).async_render() is None


@pytest.mark.parametrize("current, expected", [("Auto", "Manual"), ("Normal", "Normal"), ("Favourite", "Favourite")])
def test_manual_selection_preserves_current_then_uses_stable_priority(hass, current, expected):
    modes = ["Favourite", "Normal", "Manual", "Auto"]
    hass.states.async_set("fan.aliases", "on", {"preset_modes": modes, "preset_mode": current})
    assert manual_preset_mode(modes, current) == expected
    assert Template(_fan_manual_preset_prefix("fan.aliases") + "{{ manual }}", hass).async_render() == expected


def test_preset_mode_normalization_repairs_legacy_presentation_duplicates(hass):
    """Keep source command spellings while removing duplicate UI choices."""
    modes = ["직풍", " 자연풍 ", "스마트", "직풍", "자연풍", "수면"]
    expected = ["직풍", "자연풍", "스마트", "수면"]

    assert normalize_preset_modes(modes) == expected
    assert migrate_legacy_fan_attributes({"modes": modes})["modes"] == expected
    sources = ["fan.first", "fan.second"]
    hass.states.async_set(sources[0], "on", {"preset_modes": modes[:3]})
    hass.states.async_set(sources[1], "on", {"preset_modes": modes[3:]})
    templates = _native_reference_templates(
        "fan", sources, [hass.states.get(source) for source in sources]
    )
    assert Template(templates["preset_modes"], hass).async_render(
        parse_result=True
    ) == expected


@pytest.mark.parametrize("step", [0, -1, "nan", "inf"])
def test_bad_number_step_does_not_divide_by_zero_or_display_infinite_speed(hass, step):
    hass.states.async_set("fan.purifier", "on", {"preset_mode": "Normal", "preset_modes": ["Normal"], "percentage": 42})
    hass.states.async_set("number.level", "3", {"min": 1, "max": 5, "step": step})
    assert Template(_xiaomi_fan_percentage_template("fan.purifier", "number.level", "level"), hass).async_render() == 42
    assert Template(_xiaomi_fan_number_value_template("number.level", "level"), hass).async_render({"percentage": 60}) is None


@pytest.mark.parametrize("percentage, expected", [(1, 1), (20, 1), (60, 3), (100, 5)])
def test_level_write_is_bounded_to_source_range(hass, percentage, expected):
    hass.states.async_set("number.level", "3", {"min": 1, "max": 5, "step": 1})
    template = Template(_xiaomi_fan_number_value_template("number.level", "level"), hass)
    assert template.async_render({"percentage": percentage}) == expected


@pytest.mark.parametrize("value", [None, True, -1, 101, "nan", "inf", [], {}])
async def test_invalid_speed_never_runs_source_action(hass, value):
    entity = VirtualFan(FAN_SCHEMA({"name": "Validated fan", "speed_count": 100,
        "command_actions": {"set_percentage": [{"action": "fan.set_percentage", "data": {"percentage": "{{ percentage }}"}}]},
    }), False)
    entity.hass = hass
    entity._create_state(entity._config)
    capture = Mock()
    hass.services.async_register("fan", "set_percentage", capture)
    with pytest.raises(ValueError):
        await entity.async_set_percentage(value)
    capture.assert_not_called()


@pytest.mark.parametrize("profile", ["percentage", "levels"])
async def test_matter_speed_switches_selected_preset_source(hass, profile):
    main, speed, preset = "fan.main", "fan.speed", "fan.preset"
    hass.states.async_set(main, "on", {"supported_features": 48})
    hass.states.async_set(speed, "on", {"percentage": 40, "percentage_step": 20, "supported_features": 1})
    hass.states.async_set(preset, "on", {"preset_modes": ["Normal", "Auto"], "preset_mode": "Auto", "supported_features": 8})
    defaults = _apply_fan_source_roles(_reference_entity_defaults(hass, [main, speed, preset]),
        {"main": main, "speed": speed, "preset": preset})
    defaults = (_apply_matter_fan_percentage_helper(defaults, speed) if profile == "percentage"
                else _apply_matter_fan_level_helper(defaults, speed, (20, 60, 100)))
    actions = _parse_command_actions(defaults[CONF_COMMAND_ACTIONS_JSON], "fan")
    entity = VirtualFan(FAN_SCHEMA({"name": "Combined", "speed_count": 100,
        "modes": ["Normal", "Auto"] if profile == "percentage" else ["low", "medium", "high"],
        "command_actions": actions,
    }), False)
    entity.hass = hass
    entity._create_state(entity._config)
    entity._schedule_state_update = Mock()
    calls = []

    async def capture(call):
        calls.append((call.service, dict(call.data)))

    for service in ("turn_on", "set_percentage", "set_preset_mode"):
        hass.services.async_register("fan", service, capture)
    await entity.async_set_percentage(60)
    assert calls[0] == ("set_preset_mode", {"entity_id": [preset], "preset_mode": "Normal"})
    assert calls[-1] == ("set_percentage", {"entity_id": [speed], "percentage": 60})
    calls.clear()
    await entity.async_turn_on(percentage=60)
    assert [service for service, _ in calls] == ["turn_on", "set_preset_mode", "set_percentage"]
    if profile == "levels":
        calls.clear()
        await entity.async_set_preset_mode("medium")
        assert calls[0] == ("set_preset_mode", {"entity_id": [preset], "preset_mode": "Normal"})
        assert calls[-1] == ("set_percentage", {"entity_id": [speed], "percentage": 60})
        calls.clear()
        await entity.async_turn_on(preset_mode="medium", percentage=99)
        assert calls == [
            ("turn_on", {"entity_id": [main]}),
            ("set_preset_mode", {"entity_id": [preset], "preset_mode": "Normal"}),
            ("set_percentage", {"entity_id": [speed], "percentage": 60}),
        ]


@pytest.mark.parametrize("number_state, step", [("unavailable", 1), ("3", 0), ("3", "nan")])
async def test_invalid_paired_number_fails_before_power_or_preset_changes(hass, number_state, step):
    hass.states.async_set("fan.purifier", "off", {"preset_mode": "Auto", "preset_modes": ["Auto", "Normal"], "supported_features": 56})
    hass.states.async_set("number.level", number_state, {"min": 1, "max": 5, "step": step})
    defaults = _reference_entity_defaults(hass, ["fan.purifier", "number.level"])
    entity = VirtualFan(FAN_SCHEMA({"name": "Guarded", "speed_count": 5,
        "modes": ["Auto", "Normal"], "command_actions": _parse_command_actions(defaults[CONF_COMMAND_ACTIONS_JSON], "fan")}), False)
    entity.hass = hass
    entity._create_state(entity._config)
    entity._schedule_state_update = Mock()
    calls = []

    async def capture(call):
        calls.append(call)

    for domain, service in [("fan", "turn_on"), ("fan", "set_preset_mode"), ("number", "set_value")]:
        hass.services.async_register(domain, service, capture)
    with pytest.raises(Exception, match="Fan speed number is unavailable or has an invalid range"):
        await entity.async_set_percentage(60)
    assert calls == []
    assert entity.percentage == 0


@pytest.mark.parametrize("paired", [False, True])
async def test_explicit_preset_wins_over_turn_on_speed(hass, paired):
    hass.states.async_set("fan.purifier", "on", {"preset_mode": "Normal", "preset_modes": ["Auto", "Normal"], "percentage": 40, "supported_features": 57})
    hass.states.async_set("number.level", "3", {"min": 1, "max": 5, "step": 1})
    defaults = _reference_entity_defaults(hass, ["fan.purifier", "number.level"] if paired else ["fan.purifier"])
    entity = VirtualFan(FAN_SCHEMA({"name": "Priority", "speed_count": 100, "modes": ["Auto", "Normal"],
        "command_actions": _parse_command_actions(defaults[CONF_COMMAND_ACTIONS_JSON], "fan")}), False)
    entity.hass = hass
    entity._create_state(entity._config)
    entity._schedule_state_update = Mock()
    calls = []

    async def capture(call):
        calls.append((call.service, dict(call.data)))

    hass.services.async_register("fan", "turn_on", capture)
    await entity.async_turn_on(percentage=60, preset_mode="Auto")
    assert calls == [("turn_on", {"entity_id": ["fan.purifier"], "preset_mode": "Auto"})]
    assert entity.preset_mode == "Auto"


async def test_manual_spelling_is_preserved_in_real_action_data(hass):
    from custom_components.virtual_layer.config_flow import _fan_manual_preset_actions

    hass.states.async_set("fan.alias", "on", {"preset_mode": "Auto", "preset_modes": ["Auto", " NORMAL "]})
    entity = VirtualFan(FAN_SCHEMA({"name": "Exact alias", "speed_count": 100,
        "command_actions": {"set_percentage": _fan_manual_preset_actions("fan.alias")}}), False)
    entity.hass = hass
    entity._create_state(entity._config)
    entity._schedule_state_update = Mock()
    calls = []

    async def capture(call):
        calls.append(call.data["preset_mode"])

    hass.services.async_register("fan", "set_preset_mode", capture)
    await entity.async_set_percentage(40)
    assert calls == [" NORMAL "]


@pytest.mark.parametrize("attributes, expected", [
    ({"percentage": True}, None), ({"percentage": "nan"}, None),
    ({"percentage": 37, "motor_speed": 9999}, 37),
    ({"rpm": 0, "max_rpm": 2000}, 0),
    ({"rpm": True, "max_rpm": 2000}, None),
    ({"rpm": 1000}, None), ({"rpm": "inf", "max_rpm": 2000}, None),
    ({"rpm": 1000, "max_rpm": True}, None),
])
def test_telemetry_rejects_invalid_values_without_fabricating_speed(hass, attributes, expected):
    hass.states.async_set("fan.telemetry", "on", attributes)
    assert Template(_fan_reported_percentage_prefix("fan.telemetry") + "{{ reported }}", hass).async_render() == expected


def test_restored_automatic_speed_does_not_snap_to_manual_speed_steps():
    entity = VirtualFan(FAN_SCHEMA({"name": "Restored", "speed_count": 5, "modes": ["Auto", "Manual"]}), False)
    entity._restore_state(State("fan.restored", "on", {"percentage": 43, "preset_mode": "Auto"}), entity._config)
    assert entity.percentage == 43
    assert entity.preset_mode == "Auto"


def test_role_reopen_does_not_mistake_manual_switch_target_for_speed(hass):
    sources = ["fan.control", "fan.speed"]
    for source in sources:
        hass.states.async_set(source, "on", {"percentage": 40, "preset_mode": "Auto", "preset_modes": ["Auto", "Normal"], "supported_features": 57})
    defaults = _apply_fan_source_roles(_reference_entity_defaults(hass, sources),
        {"main": sources[0], "preset": sources[0], "speed": sources[1]})
    roles = _fan_source_role_defaults(defaults, {"main": sources, "speed": sources, "preset": sources})
    assert roles == {"main": sources[0], "preset": sources[0], "speed": sources[1]}
    reduced = _apply_matter_fan_level_helper(defaults, sources[1], (20, 60, 100))
    assert _fan_source_role_defaults(reduced, {"main": sources, "speed": sources, "preset": sources}) == roles


async def test_nonoptimistic_speed_action_preserves_native_mode(hass):
    entity = VirtualFan(FAN_SCHEMA({"name": "Authoritative", "initial_value": "on", "speed_count": 100,
        "modes": ["Auto", "Normal"], "preset_mode": "Auto", "percentage": 43,
        "command_actions": {"set_percentage": {"sequence": [{"action": "fan.set_percentage", "data": {"percentage": "{{ percentage }}"}}], "optimistic": False}},
    }), False)
    entity.hass = hass
    entity._create_state(entity._config)
    calls = []

    async def capture(call):
        calls.append(call.data["percentage"])

    hass.services.async_register("fan", "set_percentage", capture)
    await entity.async_set_percentage(60)
    assert calls == [60]
    assert (entity.preset_mode, entity.percentage) == ("Auto", 43)


@pytest.mark.parametrize("power, percentage, preset, expected_speed", [
    (True, None, None, None), (True, 0, "Auto", 0),
    (False, 75, "Auto", 0),
])
def test_explicit_source_power_wins_over_missing_or_stale_telemetry(hass, power, percentage, preset, expected_speed):
    entity = VirtualFan(FAN_SCHEMA({"name": "Power", "speed_count": 100, "modes": ["Auto", "Normal"],
        "native_templates": {"is_on": "{{ " + repr(power) + " }}",
                             "percentage": "{{ " + (repr(percentage) if percentage is not None else "none") + " }}",
                             "preset_mode": "{{ " + (repr(preset) if preset else "none") + " }}"},
    }), False)
    entity.hass = hass
    entity._create_state(entity._config)
    entity._schedule_state_update = Mock()
    entity._apply_templates()
    assert entity.is_on is power
    assert entity.percentage == expected_speed
    if not power:
        assert entity.preset_mode is None


def test_off_source_remains_available_when_manual_speed_number_is_offline(hass):
    hass.states.async_set("fan.power", "off", {"preset_modes": ["Auto", "Normal"], "preset_mode": "Normal"})
    hass.states.async_set("number.speed", "unavailable")
    template = Template(_xiaomi_fan_availability_template("fan.power", "number.speed"), hass)
    assert template.async_render() is True
    hass.states.async_set("fan.power", "on", {"preset_modes": ["Auto", "Normal"], "preset_mode": "Normal"})
    assert template.async_render() is True
    hass.states.async_set("fan.power", "on", {"preset_modes": ["Auto", "Normal"], "preset_mode": "Auto"})
    assert template.async_render() is True


@pytest.mark.parametrize("command", ["async_set_preset_mode", "async_turn_on"])
async def test_invalid_preset_is_rejected_before_any_actions(hass, command):
    name = command.removeprefix("async_")
    entity = VirtualFan(FAN_SCHEMA({"name": "Preset validation", "modes": ["Auto", "Normal"],
        "command_actions": {name: [{"action": "fan.turn_on"}]}}), False)
    entity.hass = hass
    entity._create_state(entity._config)
    with pytest.raises(ValueError, match="Invalid preset mode"):
        await getattr(entity, command)(preset_mode="Nonexistent")


def test_generated_preset_detection_tracks_changed_capabilities(hass):
    template = Template(_fan_manual_preset_prefix("fan.dynamic") + "{{ manual }}", hass)
    for modes, expected in [(["Auto", "Manual"], "Manual"), (["Auto", "NORMAL"], "NORMAL"), (["Auto", "Sleep"], None)]:
        hass.states.async_set("fan.dynamic", "on", {"preset_modes": modes, "preset_mode": "Auto"})
        assert template.async_render() == expected


@pytest.mark.parametrize("domain", ["number", "input_number"])
@pytest.mark.parametrize("mode", ["Manual", "Auto", "Sleep", "Vendor Dynamic"])
@pytest.mark.parametrize("attributes, readings", [
    ({"min": 0, "max": 100, "step": 1, "unit_of_measurement": "%"}, [(12, 12), (88, 88)]),
    ({"min": 300, "max": 2200, "step": 1, "unit_of_measurement": "rpm"}, [(300, 1), (1460, 61), (0, 0)]),
    ({"min": 1, "max": 5, "step": 1}, [(1, 20), (4, 80), (0, 0)]),
])
def test_number_and_input_number_telemetry_is_independent_of_preset(hass, domain, mode, attributes, readings):
    source, speed = "fan.physical", f"{domain}.speed"
    # Deliberately no advertised manual mode: readout must still be composed.
    fan_attrs = {"percentage": 35, "preset_mode": mode, "preset_modes": ["Auto", "Sleep"], "supported_features": 56}
    hass.states.async_set(source, "on", fan_attrs)
    hass.states.async_set(speed, str(readings[0][0]), attributes)
    defaults = _reference_entity_defaults(hass, [source, speed])
    assert defaults["platform"] == "fan"
    template = Template(defaults[CONF_NATIVE_VALUE_TEMPLATES]["percentage"], hass)
    for value, expected in readings:
        hass.states.async_set(speed, str(value), attributes)
        assert template.async_render() == expected
    hass.states.async_set(speed, "unavailable", attributes)
    assert template.async_render() == 35
    hass.states.async_set(speed, str(readings[0][0]), attributes)
    hass.states.async_set(source, "off", fan_attrs)
    assert template.async_render() == 0
    hass.states.async_set(source, "unavailable", fan_attrs)
    assert template.async_render() is None


@pytest.mark.parametrize("mode", ["Auto", "Manual"])
@pytest.mark.parametrize("raw", ["unavailable", "unknown", "nan", "inf", "-1", "101", "true"])
def test_bad_percentage_number_falls_back_to_original_fan_in_all_modes(hass, mode, raw):
    hass.states.async_set("fan.physical", "on", {"percentage": 43, "preset_mode": mode})
    hass.states.async_set("number.speed", raw, {"min": 0, "max": 100, "step": 1})
    template = Template(_xiaomi_fan_percentage_template("fan.physical", "number.speed", "percentage"), hass)
    assert template.async_render() == 43
    hass.states.async_set("fan.physical", "on", {"preset_mode": mode})
    assert template.async_render() is None


def test_rpm_unit_takes_priority_over_a_zero_to_hundred_numeric_range(hass):
    from custom_components.virtual_layer.config_flow import _fan_number_speed_kind

    hass.states.async_set("number.slow_motor", "50", {"min": 0, "max": 100, "step": 1, "unit_of_measurement": "rpm"})
    assert _fan_number_speed_kind("number.slow_motor", hass.states.get("number.slow_motor")) == "rpm"


@pytest.mark.parametrize("mode", ["Auto", "Sleep", "Favorite"])
@pytest.mark.parametrize("number_id, name, attributes, raw, expected", [
    ("number.purifier_favorite_speed", "AIR_PURIFIER 2 custom-service favorite_speed",
     {"min": 300, "max": 2200, "step": 1, "mode": "auto",
      "custom_service.favorite_speed": 780}, 780, 26),
    ("number.purifier_favorite_speed", "custom-service favorite_speed",
     {"min": 300, "max": 2200, "step": 10}, 780, 26),
    ("number.purifier_favorite_fan_level", "favorite-fan-level",
     {"min": 0, "max": 14, "step": 1}, 5, 40),
    ("number.purifier_favorite_fan_level", "favorite-fan-level",
     {"min": 1, "max": 10, "step": 1}, 5, 50),
])
def test_unitless_xiaomi_speed_examples_use_declared_bounds(
    hass, mode, number_id, name, attributes, raw, expected,
):
    # Exact AIR_PURIFIER 2 attributes plus representative bounds for other variants.
    from custom_components.virtual_layer.config_flow import _fan_number_speed_kind

    hass.states.async_set("fan.purifier", "on", {
        "preset_modes": ["Auto", "Sleep", "Favorite"], "preset_mode": mode,
        "supported_features": 56,
    })
    hass.states.async_set(number_id, str(raw), {**attributes, "friendly_name": name})
    defaults = _reference_entity_defaults(hass, ["fan.purifier", number_id])
    percentage = Template(defaults[CONF_NATIVE_VALUE_TEMPLATES]["percentage"], hass)
    assert percentage.async_render() == expected
    kind = _fan_number_speed_kind(number_id, hass.states.get(number_id))
    requested = Template(_xiaomi_fan_number_value_template(number_id, kind), hass)
    assert requested.async_render({"percentage": expected}) == raw
    hass.states.async_set("fan.purifier", "off")
    assert percentage.async_render() == 0
