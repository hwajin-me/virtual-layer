"""Translation catalog tests for Virtual Layer."""

import json
import re
from pathlib import Path

import pytest
import yaml
from homeassistant.const import CONF_PLATFORM

from custom_components.virtual_layer.config_flow import (
    CONF_DEVICE_NAME,
    CONF_NATIVE_VALUE_TEMPLATES,
    DOMAIN_NATIVE_TEMPLATE_PROPERTIES,
    _delete_entities_schema,
    _device_schema,
    _entity_schema,
    _helper_update_schema,
    _helper_usage_schema,
    _options_schema,
    _reference_entity_schema,
    _select_device_schema,
    _select_entity_schema,
    _setup_schema,
)
from custom_components.virtual_layer.const import (
    ATTR_DEVICE_ATTRIBUTES,
    ATTR_DEVICE_ID,
    ATTR_DEVICES,
    ATTR_GROUP_NAME,
    CONF_INITIAL_VALUE,
    CONF_NAME,
    VIRTUAL_ENTITY_DOMAINS,
)

pytestmark = pytest.mark.unit

TRANSLATIONS = (
    Path(__file__).parents[2]
    / "custom_components"
    / "virtual_layer"
    / "translations"
)

VALID_PLACEHOLDER = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _leaf_paths(value, prefix="") -> set[str]:
    if not isinstance(value, dict):
        return {prefix}

    paths = set()
    for key, child in value.items():
        child_prefix = f"{prefix}.{key}" if prefix else key
        paths.update(_leaf_paths(child, child_prefix))
    return paths


def _leaf_values(value):
    if isinstance(value, dict):
        for child in value.values():
            yield from _leaf_values(child)
        return

    yield value


def _schema_key_names(schema) -> set[str]:
    return {
        str(getattr(key, "schema", key))
        for key in schema.schema
    }


def _english_catalog():
    return json.loads((TRANSLATIONS / "en.json").read_text(encoding="utf-8"))


def _assert_form_translation_fields(catalog, section, step_id, schema):
    step = catalog[section]["step"][step_id]
    fields = _schema_key_names(schema)

    assert step["title"]
    assert step["description"]
    section_fields = set(step.get("sections", {}))
    assert fields <= set(step.get("data", {})) | section_fields, step_id
    assert fields - section_fields <= set(step.get("data_description", {})), step_id
    for section_name in fields & section_fields:
        translated_section = step["sections"][section_name]
        section_validator = next(
            validator
            for marker, validator in schema.schema.items()
            if str(getattr(marker, "schema", marker)) == section_name
        )
        section_fields = _schema_key_names(section_validator.schema)
        assert translated_section["name"]
        assert translated_section["description"]
        assert section_fields <= set(translated_section.get("data", {})), (
            step_id,
            section_name,
        )
        assert section_fields <= set(
            translated_section.get("data_description", {})
        ), (
            step_id,
            section_name,
        )


def _selector_options(schema, translation_key: str) -> set[str]:
    for validator in schema.schema.values():
        if getattr(validator, "config", {}).get("translation_key") == translation_key:
            return set(validator.config["options"])
    raise AssertionError(f"Missing selector {translation_key}")


def test_all_translation_files_match_english_key_topology():
    english = _english_catalog()
    english_paths = _leaf_paths(english)

    for translation_file in TRANSLATIONS.glob("*.json"):
        translated = json.loads(translation_file.read_text(encoding="utf-8"))

        assert _leaf_paths(translated) == english_paths, translation_file.name


def test_korean_translation_covers_config_options_selectors_and_services():
    korean = json.loads((TRANSLATIONS / "ko.json").read_text(encoding="utf-8"))

    assert korean["config"]["step"]["entity"]["title"] == "가상 엔티티 추가"
    assert korean["options"]["step"]["delete_entities"]["title"] == "가상 엔티티 삭제"
    assert "backup_devices" not in korean["selector"]["options_action"]["options"]
    assert "restore_devices" not in korean["selector"]["options_action"]["options"]
    assert korean["services"]["set_attributes"]["name"] == "속성 설정"


def test_native_template_sections_are_translated_for_add_and_edit():
    for translation_file in (TRANSLATIONS / "en.json", TRANSLATIONS / "ko.json"):
        catalog = json.loads(translation_file.read_text(encoding="utf-8"))
        for platform, properties in DOMAIN_NATIVE_TEMPLATE_PROPERTIES.items():
            for root, step_id in (
                ("config", "entity"),
                ("options", "entity"),
                ("options", "edit_entity"),
            ):
                translated_section = catalog[root]["step"][step_id]["sections"][
                    CONF_NATIVE_VALUE_TEMPLATES
                ]
                assert translated_section["name"], platform
                assert translated_section["description"], platform
                assert set(properties) <= set(translated_section["data"]), platform
                assert set(properties) <= set(
                    translated_section["data_description"]
                ), platform
                assert all(
                    translated_section["data_description"][property_name].strip()
                    for property_name in properties
                ), platform


def test_entity_forms_have_descriptions_for_every_dynamic_field_in_both_languages():
    for translation_file in (TRANSLATIONS / "en.json", TRANSLATIONS / "ko.json"):
        catalog = json.loads(translation_file.read_text(encoding="utf-8"))
        expected_fields = set()
        expected_sections = set()
        expected_section_fields = {}
        for platform in VIRTUAL_ENTITY_DOMAINS:
            schema = _entity_schema({CONF_PLATFORM: platform})
            for marker, validator in schema.schema.items():
                field = str(getattr(marker, "schema", marker))
                nested = getattr(validator, "schema", None)
                if hasattr(nested, "schema"):
                    expected_sections.add(field)
                    expected_section_fields.setdefault(field, set()).update(
                        _schema_key_names(nested)
                    )
                else:
                    expected_fields.add(field)
            _assert_form_translation_fields(catalog, "config", "entity", schema)
            _assert_form_translation_fields(catalog, "options", "entity", schema)
            _assert_form_translation_fields(catalog, "options", "edit_entity", schema)

        for root, step_id in (
            ("config", "entity"),
            ("options", "entity"),
            ("options", "edit_entity"),
        ):
            translated_step = catalog[root]["step"][step_id]
            assert set(translated_step["data"]) == expected_fields
            assert set(translated_step["data_description"]) == expected_fields
            assert set(translated_step["sections"]) == expected_sections
            for section_name, fields in expected_section_fields.items():
                translated_section = translated_step["sections"][section_name]
                assert set(translated_section["data"]) == fields
                assert set(translated_section["data_description"]) == fields


def test_english_translation_covers_config_flow_forms_and_errors():
    english = _english_catalog()
    entity_options = {
        ATTR_DEVICES: {
            "Laundry": [{
                CONF_PLATFORM: "sensor",
                CONF_NAME: "Washer Phase",
                CONF_INITIAL_VALUE: "idle",
            }],
        },
        ATTR_DEVICE_ATTRIBUTES: {
            "Laundry": {
                ATTR_DEVICE_ID: "laundry-1",
                CONF_NAME: "Laundry",
            },
        },
    }

    config_steps = {
        "user": _setup_schema({ATTR_GROUP_NAME: "Virtual Device"}),
        "reconfigure": _setup_schema(
            {ATTR_GROUP_NAME: "Virtual Device"},
            include_entity_toggle=False,
        ),
        "entity_source": _reference_entity_schema(),
        "entity_helper": _helper_usage_schema(),
        "entity": _entity_schema(),
    }
    option_steps = {
        "init": _options_schema(entity_options),
        "select_entity": _select_entity_schema(entity_options),
        "select_device": _select_device_schema(entity_options),
        "edit_device": _device_schema({CONF_DEVICE_NAME: "Laundry"}),
        "delete_entities": _delete_entities_schema(entity_options),
        "delete_device": _select_device_schema(entity_options),
        "edit_entity": _entity_schema(),
        "edit_entity_helper": _helper_update_schema(),
        "entity_helper": _helper_usage_schema(),
        "edit_entity_source": _reference_entity_schema(
            ["sensor.washer_phase"],
            [{"value": "Laundry", "label": "Laundry"}],
            "Laundry",
        ),
        "entity": _entity_schema(),
        "entity_source": _reference_entity_schema(
            device_options=[{"value": "Laundry", "label": "Laundry"}],
        ),
    }

    for step_id, schema in config_steps.items():
        _assert_form_translation_fields(english, "config", step_id, schema)
    for step_id, schema in option_steps.items():
        _assert_form_translation_fields(english, "options", step_id, schema)

    assert {
        "group_name_used",
        "invalid_json",
        "invalid_entity_id",
        "entity_id_used",
        "invalid_domain_options",
        "required",
    } <= set(english["config"]["error"])
    assert {
        "invalid_json",
        "invalid_entity_id",
        "entity_id_used",
        "invalid_domain_options",
        "device_not_found",
        "entity_not_found",
        "no_devices",
        "no_entities",
        "required",
    } <= set(english["options"]["error"])

    assert _selector_options(
        _options_schema(entity_options),
        "options_action",
    ) <= set(english["selector"]["options_action"]["options"])
    assert _selector_options(
        _helper_update_schema(),
        "helper_update_mode",
    ) <= set(english["selector"]["helper_update_mode"]["options"])
def test_english_service_translations_match_services_yaml():
    english = _english_catalog()
    services = yaml.safe_load(
        (TRANSLATIONS.parent / "services.yaml").read_text(encoding="utf-8"),
    )

    assert set(services) <= set(english["services"])
    for service_name, service in services.items():
        translated_service = english["services"][service_name]
        assert translated_service["name"] == service["name"]
        assert translated_service["description"] == service["description"]

        for field_name, field in service.get("fields", {}).items():
            translated_field = translated_service["fields"][field_name]
            assert translated_field["name"] == field["name"]
            assert translated_field["description"] == field["description"]


@pytest.mark.parametrize("translation_file", sorted(TRANSLATIONS.glob("*.json")))
def test_translation_placeholders_are_home_assistant_identifiers(translation_file):
    catalog = json.loads(translation_file.read_text(encoding="utf-8"))

    for value in _leaf_values(catalog):
        if not isinstance(value, str):
            continue
        for placeholder in re.findall(r"\{([^{}]+)\}", value):
            assert VALID_PLACEHOLDER.match(placeholder), (
                f"{translation_file.name} has invalid placeholder "
                f"{placeholder!r} in {value!r}"
            )
