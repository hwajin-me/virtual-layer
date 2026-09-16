"""Pollutant aliases share icons without inventing concentration semantics."""

import pytest
from homeassistant.core import State
from homeassistant.helpers.template import Template

from custom_components.virtual_layer import air_quality_options as aq
from custom_components.virtual_layer.config_flow import (
    _air_quality_default_icon,
    _reference_entity_defaults,
)


@pytest.mark.parametrize("canonical,names", [
    ("toluene", ["Toluene", "methyl-benzene", "톨루엔"]),
    ("xylene", ["o-Xylene", "dimethyl benzene", "자일렌", "크실렌"]),
    ("ethylbenzene", ["Ethyl Benzene", "ethyl_benzene", "에틸벤젠"]),
    ("styrene", ["Styrene", "스티렌", "스타이렌"]),
    ("acetone", ["Acetone", "Propanone", "아세톤"]),
    ("acetaldehyde", ["Acetaldehyde", "ethanal", "아세트 알데히드"]),
    ("acrolein", ["Acrolein", "아크롤레인"]),
    ("methanol", ["Methanol", "methyl alcohol", "메탄올", "메틸 알코올"]),
    ("methane", ["CH₄", "ＣＨ４", "Methane", "메탄"]),
    ("propane", ["C₃H₈", "propane", "프로판"]),
    ("butane", ["C₄H₁₀", "butane", "부탄"]),
    ("chlorine", ["Cl₂", "chlorine", "염소"]),
    ("hydrogen_chloride", ["HCl", "hydrogen–chloride", "염화 수소"]),
    ("hydrogen_cyanide", ["HCN", "hydrogen cyanide", "시안화수소", "청산 가스"]),
    ("hydrogen_fluoride", ["hydrogen fluoride", "불화수소", "플루오린화수소"]),
    ("sulfur_trioxide", ["SO₃", "sulphur trioxide", "삼산화황"]),
    ("nitrogen_oxides", ["NOx", "nitrogen oxides", "질소산화물"]),
    ("sulfur_oxides", ["SOx", "sulfur oxides", "황산화물"]),
    ("btex", ["BTEX"]),
])
def test_extended_pollutants_have_canonical_names_and_icons(canonical, names):
    for name in names:
        assert aq.pollutant_name_matches(name, include_icon_only=True) == {canonical}
        assert _air_quality_default_icon("sensor", name) == "mdi:air-filter"
        # Recognizing a name must not invent automatic thresholds or units.
        state = State("sensor.sample", "1", {"friendly_name": name})
        assert aq.infer_quantity(state) is None


@pytest.mark.parametrize("name,quantity", [
    ("ＣＯ₂", "carbon_dioxide"),
    ("carbon—dioxide", "carbon_dioxide"),
    ("ＰＭ２．５", "pm25"),
    ("PM₂₅", "pm25"),
    ("Ｃ₆Ｈ₆", "benzene"),
    ("NH₃", "ammonia"),
    ("H₂S", "hydrogen_sulfide"),
    ("N₂O", "nitrous_oxide"),
    ("Sulfur–Dioxide", "sulphur_dioxide"),
    ("Total Volatile Organic Compounds", "volatile_organic_compounds"),
    ("총 휘발성 유기 화합물", "volatile_organic_compounds"),
    ("포름 알데히드", "formaldehyde"),
    ("Methanal", "formaldehyde"),
    ("이산화 탄소", "carbon_dioxide"),
    ("일산화_탄소", "carbon_monoxide"),
    ("이산화-질소", "nitrogen_dioxide"),
    ("일산화 질소", "nitrogen_monoxide"),
    ("아산화 질소", "nitrous_oxide"),
    ("이산화 황", "sulphur_dioxide"),
    ("황화 수소", "hydrogen_sulfide"),
    ("초 미세 먼지", "pm25"),
])
def test_supported_measurements_normalize_aliases(name, quantity):
    state = State("sensor.sample", "1", {"friendly_name": name})
    assert aq.infer_quantity(state) == quantity
    assert _air_quality_default_icon("sensor", name) == "mdi:air-filter"


@pytest.mark.parametrize("name", [
    "Company", "Score", "ChlorinePump", "NO", "C8H10", "PM1_5", "PM1_01",
    "PM100", "PM10.5", "PM10_01", "PM4_5", "chlorinated", "methanolic", "nozzle", "toluene2",
])
def test_ambiguous_formulas_and_non_pollutant_tokens_are_not_guessed(name):
    assert not aq.pollutant_name_matches(name, include_icon_only=True)
    assert _air_quality_default_icon("sensor", name) == ""


def test_mixed_pollutants_and_metadata_keep_measurement_inference_conservative():
    assert aq.pollutant_name_matches("CO₂ + Toluene", include_icon_only=True) == {
        "carbon_dioxide", "toluene",
    }
    assert aq.infer_quantity(State("sensor.co2", "1", {"friendly_name": "Toluene"})) is None
    assert aq.infer_quantity(State("sensor.co2", "1", {"device_class": "temperature"})) is None
    assert aq.infer_quantity(State("sensor.toluene", "1", {"device_class": "carbon_dioxide"})) == "carbon_dioxide"
    assert not aq.pollutant_name_matches("carbon", "dioxide", include_icon_only=True)
    assert _air_quality_default_icon("switch", "Toluene") == ""
    assert aq.normalize_pollutant_name(None) == ""


@pytest.mark.parametrize("name", ["에틸벤젠", "ＣＨ₄", "hydrogen–chloride", "PM 10.0", "VOC", "h2ho"])
def test_source_prefill_uses_normalized_pollutant_icon(hass, name):
    hass.states.async_set("sensor.sample", "1", {"friendly_name": name, "icon": "mdi:cloud"})
    defaults = _reference_entity_defaults(hass, ["sensor.sample"])
    assert defaults["icon"] == "mdi:air-filter"
    # The helper defers to the editable static icon instead of a vendor icon.
    assert Template(defaults["icon_template"], hass).async_render() == ""


@pytest.mark.parametrize("device_class", ["pm10", "volatile_organic_compounds", "volatile_organic_compounds_parts"])
def test_source_device_class_supplies_icon_without_descriptive_name(hass, device_class):
    hass.states.async_set("sensor.sample", "1", {"device_class": device_class})
    assert _reference_entity_defaults(hass, ["sensor.sample"])["icon"] == "mdi:air-filter"


@pytest.mark.parametrize("config", [
    {"name": "PM 10.0"}, {"name": "VOC"}, {"name": "h2ho"},
    {"name": "Reading", "class": "pm10"},
    {"name": "Reading", "class": "volatile_organic_compounds_parts"},
    {"name": "Toluene"},
])
def test_existing_sensor_runtime_icon_and_explicit_override(config):
    from custom_components.virtual_layer.sensor import VirtualSensor

    sensor = VirtualSensor(config, False)
    assert sensor.icon == "mdi:air-filter"
    customized = VirtualSensor({**config, "icon": "mdi:flask"}, False)
    assert customized.icon == "mdi:flask"
    sensor._apply_native_template_value("icon", "mdi:cloud")
    assert sensor.icon == "mdi:cloud"
    sensor._apply_native_template_value("icon", "")
    assert sensor.icon == "mdi:air-filter"
