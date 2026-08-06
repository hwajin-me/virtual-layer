"""Unit tests for Virtual Layer config and metadata helpers."""

import json

import pytest
from homeassistant.const import ATTR_ENTITY_ID, CONF_PLATFORM

from custom_components.virtual_layer.cfg import (
    BlendedCfg,
    _async_load_json,
    _async_save_json,
    _delete_meta_data,
    _make_entity_id,
    _normalize_common_entity_config,
    _rename_meta_data,
)
from custom_components.virtual_layer.const import (
    ATTR_DEVICE_ATTRIBUTES,
    ATTR_DEVICE_ID,
    ATTR_DEVICES,
    ATTR_ENTITY_KEY,
    ATTR_GROUP_NAME,
    ATTR_UNIQUE_ID,
    ATTR_VERSION,
    CONF_ATTRIBUTE,
    CONF_ATTRIBUTE_SOURCES,
    CONF_ATTRIBUTE_TEMPLATES,
    CONF_ATTRIBUTES,
    CONF_AVAILABILITY_TEMPLATE,
    CONF_EVENT_HOOKS,
    CONF_INITIAL_AVAILABILITY,
    CONF_INITIAL_VALUE,
    CONF_MAX,
    CONF_MIN,
    CONF_NAME,
    CONF_PERSISTENT,
    CONF_PULL_INTERVAL,
    CONF_SOURCE_ENTITIES,
    CONF_TEMPLATE_SOURCES,
    CONF_VALUE_TEMPLATE,
)

pytestmark = pytest.mark.unit


def test_make_entity_id_uses_the_domain_prefix_for_prefixed_names():
    assert _make_entity_id("sensor", "+Kitchen Temperature") == (
        "sensor.kitchen_temperature"
    )


def test_make_entity_id_uses_the_domain_prefix_for_ui_names():
    assert _make_entity_id("sensor", "Kitchen Temperature") == (
        "sensor.kitchen_temperature"
    )


def test_make_entity_id_repairs_an_empty_object_id():
    assert _make_entity_id("switch", "+") == "switch.virtual_entity"


@pytest.mark.asyncio
async def test_json_storage_recovers_legacy_non_finite_numbers(tmp_path):
    storage_file = tmp_path / "legacy.json"
    storage_file.write_text(
        '{"valid": 1, "nan": NaN, "positive": Infinity, "negative": -Infinity}',
        encoding="utf-8",
    )

    assert await _async_load_json(str(storage_file)) == {
        "valid": 1,
        "nan": None,
        "positive": None,
        "negative": None,
    }

    with pytest.raises(ValueError):
        await _async_save_json(str(storage_file), {"bad": float("nan")})
    assert not list(tmp_path.glob("*.tmp"))


def test_stored_entity_normalization_sanitizes_non_finite_values():
    normalized = _normalize_common_entity_config(
        {
            CONF_PLATFORM: "number",
            CONF_NAME: "Damaged number",
            CONF_INITIAL_VALUE: float("nan"),
            CONF_MIN: float("nan"),
            CONF_MAX: float("inf"),
            CONF_ATTRIBUTES: {
                "nested": [1, float("-inf")],
                10: "invalid key",
            },
            CONF_EVENT_HOOKS: [{
                "trigger": "event",
                "event_type": "virtual_layer_update",
                "debounce": float("inf"),
            }],
        },
        "Damaged Device",
        0,
    )

    assert normalized[CONF_INITIAL_VALUE] == "unknown"
    assert normalized[CONF_MIN] == 0
    assert normalized[CONF_MAX] == 100
    assert normalized[CONF_ATTRIBUTES] == {"nested": [1, None]}
    assert normalized[CONF_EVENT_HOOKS] == [{
        "trigger": "event",
        "event_type": "virtual_layer_update",
    }]


@pytest.mark.asyncio
async def test_atomic_json_save_keeps_previous_file_when_replace_fails(tmp_path, monkeypatch):
    file_name = tmp_path / "metadata.json"
    file_name.write_text('{"previous": true}')

    async def fail_replace(_source, _target):
        raise OSError("replace failed")

    monkeypatch.setattr("custom_components.virtual_layer.cfg.aiofiles.os.replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        await _async_save_json(str(file_name), {"next": True})

    assert json.loads(file_name.read_text()) == {"previous": True}
    assert not list(tmp_path.glob("metadata.json.*.tmp"))


@pytest.mark.asyncio
async def test_rename_meta_data_preserves_identity_and_unrelated_groups(
    hass,
    tmp_path,
    monkeypatch,
):
    meta_file = tmp_path / "virtual_layer.meta.json"
    meta_file.write_text(json.dumps({
        ATTR_VERSION: 99,
        ATTR_DEVICES: {
            "old": {"entity-key": {ATTR_UNIQUE_ID: "stable-unique"}},
            "other": {"other-key": {ATTR_UNIQUE_ID: "other-unique"}},
        },
        "future_field": {"keep": True},
    }))
    monkeypatch.setattr(
        "custom_components.virtual_layer.cfg.default_meta_file",
        lambda _hass: str(meta_file),
    )

    await _rename_meta_data(hass, "old", "new")

    saved = json.loads(meta_file.read_text())
    assert saved[ATTR_VERSION] == 1
    assert "old" not in saved[ATTR_DEVICES]
    assert saved[ATTR_DEVICES]["new"]["entity-key"][ATTR_UNIQUE_ID] == "stable-unique"
    assert saved[ATTR_DEVICES]["other"]["other-key"][ATTR_UNIQUE_ID] == "other-unique"
    assert saved["future_field"] == {"keep": True}


@pytest.mark.asyncio
async def test_blended_cfg_does_not_reuse_legacy_meta_with_wrong_domain(hass, tmp_path, monkeypatch):
    meta_file = tmp_path / "virtual_layer.meta.json"
    meta_file.write_text(json.dumps({
        ATTR_VERSION: 1,
        ATTR_DEVICES: {
            "ui": {
                "Virtual Entity": {
                    ATTR_UNIQUE_ID: "lock-unique",
                    ATTR_ENTITY_ID: "lock.ee12",
                    ATTR_DEVICE_ID: "Virtual Device",
                },
            },
        },
    }))
    monkeypatch.setattr(
        "custom_components.virtual_layer.cfg.default_meta_file",
        lambda _hass: str(meta_file),
    )

    cfg = BlendedCfg(
        hass,
        {ATTR_GROUP_NAME: "ui"},
        {
            ATTR_DEVICES: {
                "Virtual Device": [
                    {
                        CONF_PLATFORM: "sensor",
                        CONF_NAME: "Virtual Entity",
                        ATTR_ENTITY_KEY: "sensor-key",
                        CONF_INITIAL_VALUE: "unknown",
                        CONF_INITIAL_AVAILABILITY: True,
                        CONF_PERSISTENT: True,
                    },
                    {
                        CONF_PLATFORM: "lock",
                        CONF_NAME: "Virtual Entity",
                        ATTR_ENTITY_KEY: "lock-key",
                        CONF_INITIAL_VALUE: "unknown",
                        CONF_INITIAL_AVAILABILITY: True,
                        CONF_PERSISTENT: True,
                    },
                ],
            },
        },
    )

    await cfg.async_load()

    assert cfg.entities["sensor"][0][ATTR_ENTITY_ID] == "sensor.virtual_entity"
    assert cfg.entities["lock"][0][ATTR_ENTITY_ID] == "lock.ee12"
    assert cfg.entities["lock"][0][ATTR_UNIQUE_ID] == "lock-unique"


@pytest.mark.asyncio
async def test_blended_cfg_preserves_identity_when_entity_is_renamed(hass, tmp_path, monkeypatch):
    meta_file = tmp_path / "virtual_layer.meta.json"
    meta_file.write_text(json.dumps({
        ATTR_VERSION: 1,
        ATTR_DEVICES: {
            "ui": {
                "stable-key": {
                    ATTR_UNIQUE_ID: "stable-unique",
                    ATTR_ENTITY_ID: "sensor.washer_phase",
                    ATTR_DEVICE_ID: "Laundry",
                    CONF_NAME: "Washer Phase",
                    CONF_PLATFORM: "sensor",
                },
            },
        },
    }))
    monkeypatch.setattr(
        "custom_components.virtual_layer.cfg.default_meta_file",
        lambda _hass: str(meta_file),
    )

    cfg = BlendedCfg(
        hass,
        {ATTR_GROUP_NAME: "ui"},
        {
            ATTR_DEVICES: {
                "Laundry": [
                    {
                        CONF_PLATFORM: "sensor",
                        CONF_NAME: "Washer Status",
                        ATTR_ENTITY_KEY: "stable-key",
                        CONF_INITIAL_VALUE: "running",
                        CONF_INITIAL_AVAILABILITY: True,
                        CONF_PERSISTENT: True,
                    },
                ],
            },
        },
    )

    await cfg.async_load()

    entity = cfg.entities["sensor"][0]
    assert entity[ATTR_UNIQUE_ID] == "stable-unique"
    assert entity[ATTR_ENTITY_ID] == "sensor.washer_phase"
    assert entity[CONF_NAME] == "Washer Status"


@pytest.mark.asyncio
async def test_delete_meta_data_is_idempotent(hass, tmp_path, monkeypatch):
    meta_file = tmp_path / "virtual_layer.meta.json"
    meta_file.write_text(json.dumps({ATTR_VERSION: 1, ATTR_DEVICES: {}}))
    monkeypatch.setattr(
        "custom_components.virtual_layer.cfg.default_meta_file",
        lambda _hass: str(meta_file),
    )

    await _delete_meta_data(hass, "missing")

    assert json.loads(meta_file.read_text()) == {ATTR_VERSION: 1, ATTR_DEVICES: {}}


@pytest.mark.asyncio
async def test_delete_meta_data_recovers_from_invalid_storage_payload(hass, tmp_path, monkeypatch):
    meta_file = tmp_path / "virtual_layer.meta.json"
    meta_file.write_text(json.dumps(["old", "bad", "shape"]))
    monkeypatch.setattr(
        "custom_components.virtual_layer.cfg.default_meta_file",
        lambda _hass: str(meta_file),
    )

    await _delete_meta_data(hass, "ui")

    assert json.loads(meta_file.read_text()) == {ATTR_VERSION: 1, ATTR_DEVICES: {}}


@pytest.mark.asyncio
async def test_blended_cfg_skips_invalid_stored_options_so_entry_can_still_load(
    hass,
    tmp_path,
    monkeypatch,
):
    meta_file = tmp_path / "virtual_layer.meta.json"
    meta_file.write_text(json.dumps({
        ATTR_VERSION: 99,
        ATTR_DEVICES: {
            "ui": {
                "bad-meta": "not a dict",
                "orphan-key": {
                    ATTR_ENTITY_ID: "sensor.orphan",
                    ATTR_DEVICE_ID: "orphan-device",
                },
            },
        },
        "future_field": {"kept": True},
    }))
    monkeypatch.setattr(
        "custom_components.virtual_layer.cfg.default_meta_file",
        lambda _hass: str(meta_file),
    )

    cfg = BlendedCfg(
        hass,
        {ATTR_GROUP_NAME: "ui"},
        {
            ATTR_DEVICES: {
                "Bad Device": "not a list",
                "Mixed Device": [
                    "not a dict",
                    {CONF_NAME: "No Platform"},
                    {CONF_PLATFORM: "unsupported_domain", CONF_NAME: "Unsupported"},
                    {
                        CONF_PLATFORM: "sensor",
                        CONF_NAME: "Good Sensor",
                        CONF_INITIAL_VALUE: "ok",
                        CONF_INITIAL_AVAILABILITY: True,
                        CONF_PERSISTENT: True,
                    },
                ],
            },
            ATTR_DEVICE_ATTRIBUTES: "not a dict",
        },
    )

    await cfg.async_load()

    assert cfg.entities["sensor"][0][CONF_NAME] == "Good Sensor"
    assert cfg.entities["sensor"][0][ATTR_ENTITY_ID] == "sensor.good_sensor"
    assert "orphan-key" in cfg.orphaned_entities
    saved = json.loads(meta_file.read_text())
    assert saved["future_field"] == {"kept": True}
    assert saved[ATTR_VERSION] == 1


@pytest.mark.asyncio
async def test_blended_cfg_normalizes_malformed_common_entity_fields(
    hass,
    tmp_path,
    monkeypatch,
):
    meta_file = tmp_path / "virtual_layer.meta.json"
    monkeypatch.setattr(
        "custom_components.virtual_layer.cfg.default_meta_file",
        lambda _hass: str(meta_file),
    )
    cfg = BlendedCfg(
        hass,
        {ATTR_GROUP_NAME: "ui"},
        {
            ATTR_DEVICES: {
                "Damaged Device": [{
                    CONF_PLATFORM: "sensor",
                    CONF_NAME: "Damaged Sensor",
                    CONF_INITIAL_VALUE: {"bad": "shape"},
                    CONF_INITIAL_AVAILABILITY: "not-a-boolean",
                    CONF_PERSISTENT: "not-a-boolean",
                    CONF_ATTRIBUTES: ["bad"],
                    CONF_ATTRIBUTE_SOURCES: "bad",
                    CONF_ATTRIBUTE_TEMPLATES: 3,
                    CONF_TEMPLATE_SOURCES: None,
                    CONF_SOURCE_ENTITIES: ["sensor.valid", "invalid"],
                    CONF_PULL_INTERVAL: -30,
                    CONF_VALUE_TEMPLATE: {"bad": "template"},
                    CONF_AVAILABILITY_TEMPLATE: ["bad"],
                    CONF_EVENT_HOOKS: {
                        "valid event": {
                            "trigger": "event",
                            "event_type": "virtual_layer_recovered",
                            "event_data": "bad",
                            "debounce": "2.5",
                            "refresh": "yes",
                        },
                        "valid state": {
                            "trigger": "state",
                            "entity_ids": ["invalid", "sensor.valid", "sensor.valid"],
                            "attributes_changed": ["mode", ""],
                            CONF_ATTRIBUTE_TEMPLATES: {
                                "copied": "{{ trigger.to }}",
                                ATTR_ENTITY_ID: "blocked",
                            },
                        },
                        "missing source": {"trigger": "state"},
                        "bad item": "invalid",
                    },
                }],
            },
            ATTR_DEVICE_ATTRIBUTES: {
                "Damaged Device": {
                    ATTR_DEVICE_ID: ["bad"],
                    CONF_NAME: {"bad": "name"},
                },
            },
        },
    )

    await cfg.async_load()

    entity = cfg.entities["sensor"][0]
    assert entity[CONF_INITIAL_VALUE] == "unknown"
    assert entity[CONF_INITIAL_AVAILABILITY] is True
    assert entity[CONF_PERSISTENT] is True
    assert entity[CONF_ATTRIBUTES] == {}
    assert entity[CONF_ATTRIBUTE_SOURCES] == {}
    assert entity[CONF_ATTRIBUTE_TEMPLATES] == {}
    assert entity[CONF_TEMPLATE_SOURCES] == {}
    assert entity[CONF_SOURCE_ENTITIES] == ["sensor.valid"]
    assert entity[CONF_PULL_INTERVAL] == 0
    assert CONF_VALUE_TEMPLATE not in entity
    assert CONF_AVAILABILITY_TEMPLATE not in entity
    assert entity[CONF_EVENT_HOOKS] == [
        {
            "trigger": "event",
            "event_type": "virtual_layer_recovered",
            "name": "valid event",
            "debounce": 2.5,
            "refresh": True,
        },
        {
            "trigger": "state",
            ATTR_ENTITY_ID: ["sensor.valid"],
            CONF_ATTRIBUTE: ["mode"],
            CONF_ATTRIBUTE_TEMPLATES: {"copied": "{{ trigger.to }}"},
            "name": "valid state",
        },
    ]
    assert entity[ATTR_DEVICE_ID] == "Damaged Device"


@pytest.mark.asyncio
async def test_blended_cfg_drops_malformed_nested_source_references(
    hass,
    tmp_path,
    monkeypatch,
):
    meta_file = tmp_path / "virtual_layer.meta.json"
    monkeypatch.setattr(
        "custom_components.virtual_layer.cfg.default_meta_file",
        lambda _hass: str(meta_file),
    )
    cfg = BlendedCfg(
        hass,
        {ATTR_GROUP_NAME: "ui"},
        {ATTR_DEVICES: {"Device": [{
            CONF_PLATFORM: "sensor",
            CONF_NAME: "Recovered Sensor",
            CONF_ATTRIBUTE_SOURCES: {
                "battery": "sensor.remote.battery_level",
                "broken": {ATTR_ENTITY_ID: ["sensor.remote"], CONF_ATTRIBUTE: "state"},
            },
            CONF_TEMPLATE_SOURCES: {
                "source": "sensor.remote",
                "broken": {ATTR_ENTITY_ID: 3, CONF_ATTRIBUTE: "state"},
                "missing_attribute": {ATTR_ENTITY_ID: "sensor.remote", CONF_ATTRIBUTE: None},
            },
        }]}},
    )

    await cfg.async_load()

    entity = cfg.entities["sensor"][0]
    assert entity[CONF_ATTRIBUTE_SOURCES] == {
        "battery": {ATTR_ENTITY_ID: "sensor.remote", CONF_ATTRIBUTE: "battery_level"},
    }
    assert entity[CONF_TEMPLATE_SOURCES] == {
        "source": {ATTR_ENTITY_ID: "sensor.remote", CONF_ATTRIBUTE: "state"},
    }


@pytest.mark.asyncio
async def test_blended_cfg_repairs_legacy_number_range(hass, tmp_path, monkeypatch):
    meta_file = tmp_path / "virtual_layer.meta.json"
    monkeypatch.setattr(
        "custom_components.virtual_layer.cfg.default_meta_file",
        lambda _hass: str(meta_file),
    )
    cfg = BlendedCfg(
        hass,
        {ATTR_GROUP_NAME: "ui"},
        {ATTR_DEVICES: {"Meter": [{
            CONF_PLATFORM: "number",
            CONF_NAME: "Legacy Meter",
            CONF_MIN: "not-a-number",
            CONF_MAX: -1,
        }]}},
    )

    await cfg.async_load()

    entity = cfg.entities["number"][0]
    assert entity[CONF_MIN] == 0.0
    assert entity[CONF_MAX] == 100.0


@pytest.mark.asyncio
async def test_blended_cfg_skips_invalid_domain_entity_but_keeps_repair_metadata(
    hass,
    tmp_path,
    monkeypatch,
):
    meta_file = tmp_path / "virtual_layer.meta.json"
    monkeypatch.setattr(
        "custom_components.virtual_layer.cfg.default_meta_file",
        lambda _hass: str(meta_file),
    )
    cfg = BlendedCfg(
        hass,
        {ATTR_GROUP_NAME: "ui"},
        {ATTR_DEVICES: {"Mixed Device": [
            {
                CONF_PLATFORM: "sensor",
                CONF_NAME: "Healthy Sensor",
                ATTR_ENTITY_KEY: "healthy",
                CONF_INITIAL_VALUE: "12",
            },
            {
                CONF_PLATFORM: "camera",
                CONF_NAME: "Broken Camera",
                ATTR_ENTITY_KEY: "broken",
                # Camera requires a string stream source; this simulates a
                # stale stored data after a schema change.
                "stream_source": {"unexpected": "shape"},
            },
        ]}},
    )

    await cfg.async_load()

    assert [entity[CONF_NAME] for entity in cfg.entities["sensor"]] == [
        "Healthy Sensor",
    ]
    assert "camera" not in cfg.entities
    saved_metadata = json.loads(meta_file.read_text())[ATTR_DEVICES]["ui"]
    assert "broken" in saved_metadata


@pytest.mark.asyncio
async def test_blended_cfg_repairs_corrupt_identity_fields(
    hass,
    tmp_path,
    monkeypatch,
):
    meta_file = tmp_path / "virtual_layer.meta.json"
    meta_file.write_text(json.dumps({
        ATTR_VERSION: 1,
        ATTR_DEVICES: {
            "ui": {
                "stable-key": {
                    ATTR_UNIQUE_ID: ["not", "hashable"],
                    ATTR_ENTITY_ID: "sensor",
                    ATTR_DEVICE_ID: "old-device",
                },
            },
        },
    }))
    monkeypatch.setattr(
        "custom_components.virtual_layer.cfg.default_meta_file",
        lambda _hass: str(meta_file),
    )
    cfg = BlendedCfg(
        hass,
        {ATTR_GROUP_NAME: "ui"},
        {ATTR_DEVICES: {"Device": [{
            CONF_PLATFORM: "sensor",
            CONF_NAME: "Healthy Identity",
            ATTR_ENTITY_KEY: "stable-key",
            ATTR_ENTITY_ID: "sensor",
        }]}},
    )

    await cfg.async_load()

    entity = cfg.entities["sensor"][0]
    assert entity[ATTR_ENTITY_ID] == "sensor.healthy_identity"
    assert isinstance(entity[ATTR_UNIQUE_ID], str)
    assert entity[ATTR_UNIQUE_ID]


@pytest.mark.asyncio
async def test_blended_cfg_repairs_duplicate_and_non_string_entity_keys(
    hass,
    tmp_path,
    monkeypatch,
):
    meta_file = tmp_path / "virtual_layer.meta.json"
    monkeypatch.setattr(
        "custom_components.virtual_layer.cfg.default_meta_file",
        lambda _hass: str(meta_file),
    )
    cfg = BlendedCfg(
        hass,
        {ATTR_GROUP_NAME: "ui"},
        {ATTR_DEVICES: {"Device": [
            {CONF_PLATFORM: "sensor", CONF_NAME: "One", ATTR_ENTITY_KEY: "duplicate"},
            {CONF_PLATFORM: "sensor", CONF_NAME: "Two", ATTR_ENTITY_KEY: "duplicate"},
            {CONF_PLATFORM: "sensor", CONF_NAME: "Three", ATTR_ENTITY_KEY: ["bad"]},
        ]}},
    )

    await cfg.async_load()

    primary_entities = [
        entity
        for entity in cfg.entities["sensor"]
        if ".virtual_layer_diagnostic." not in entity[ATTR_UNIQUE_ID]
    ]
    assert len({entity[ATTR_UNIQUE_ID] for entity in primary_entities}) == 3
    saved_metadata = json.loads(meta_file.read_text())[ATTR_DEVICES]["ui"]
    assert len(saved_metadata) == 3
