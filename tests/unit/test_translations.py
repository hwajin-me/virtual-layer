"""Translation catalog tests for Virtual Layer."""

import json
from pathlib import Path
import re

import pytest


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


def test_korean_translation_matches_english_key_topology():
    english = json.loads((TRANSLATIONS / "en.json").read_text(encoding="utf-8"))
    korean = json.loads((TRANSLATIONS / "ko.json").read_text(encoding="utf-8"))

    assert _leaf_paths(korean) == _leaf_paths(english)


def test_korean_translation_covers_config_options_selectors_and_services():
    korean = json.loads((TRANSLATIONS / "ko.json").read_text(encoding="utf-8"))

    assert korean["config"]["step"]["entity"]["title"] == "가상 엔티티 추가"
    assert korean["options"]["step"]["delete_entities"]["title"] == "가상 엔티티 삭제"
    assert korean["selector"]["options_action"]["options"]["restore_devices"] == "장치 복원"
    assert korean["selector"]["restore_mode"]["options"] == {
        "merge": "병합",
        "replace": "교체",
    }
    assert korean["services"]["set_attributes"]["name"] == "속성 설정"


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
