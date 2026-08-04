"""Unit tests for Virtual Layer config and backup helpers."""

import json

import pytest

from custom_components.virtual_layer.cfg import (
    _async_save_json,
    BlendedCfg,
    _delete_meta_data,
    _make_entity_id,
    async_build_entry_backup,
    async_load_backup,
    async_save_backup,
)
from custom_components.virtual_layer.const import (
    ATTR_BACKUP_GROUPS,
    ATTR_DEVICE_ATTRIBUTES,
    ATTR_DEVICE_ID,
    ATTR_DEVICES,
    ATTR_ENTITY_KEY,
    ATTR_GROUP_NAME,
    ATTR_UNIQUE_ID,
    ATTR_VERSION,
    COMPONENT_DOMAIN,
    CONF_INITIAL_AVAILABILITY,
    CONF_INITIAL_VALUE,
    CONF_ATTRIBUTES,
    CONF_ATTRIBUTE_SOURCES,
    CONF_ATTRIBUTE_TEMPLATES,
    CONF_AVAILABILITY_TEMPLATE,
    CONF_NAME,
    CONF_MAX,
    CONF_MIN,
    CONF_PERSISTENT,
    CONF_PULL_INTERVAL,
    CONF_SOURCE_ENTITIES,
    CONF_TEMPLATE_SOURCES,
    CONF_VALUE_TEMPLATE,
)
from homeassistant.const import ATTR_ENTITY_ID, CONF_PLATFORM


pytestmark = pytest.mark.unit


def test_make_entity_id_uses_virtual_layer_prefix_for_prefixed_names():
    assert _make_entity_id("sensor", "+Kitchen Temperature") == (
        "sensor.virtual_layer_kitchen_temperature"
    )


def test_make_entity_id_uses_plain_slug_for_ui_names():
    assert _make_entity_id("sensor", "Kitchen Temperature") == (
        "sensor.kitchen_temperature"
    )


@pytest.mark.asyncio
async def test_entry_backup_contains_only_group_and_ui_devices():
    class Entry:
        data = {ATTR_GROUP_NAME: "ui"}
        options = {
            ATTR_DEVICES: {
                "Device": [{
                    "platform": "weather",
                    "temperature": 21.5,
                    "forecast_provider": "virtual",
                }],
            },
            ATTR_DEVICE_ATTRIBUTES: {
                "Device": {
                    ATTR_DEVICE_ID: "device-1",
                    "name": "Device",
                },
            },
        }

    backup = await async_build_entry_backup(Entry())

    assert backup == {
        ATTR_GROUP_NAME: "ui",
        ATTR_DEVICES: {
            "Device": [{
                "platform": "weather",
                "temperature": 21.5,
                "forecast_provider": "virtual",
            }],
        },
        ATTR_DEVICE_ATTRIBUTES: {
            "Device": {
                ATTR_DEVICE_ID: "device-1",
                "name": "Device",
            },
        },
    }


@pytest.mark.asyncio
async def test_backup_round_trip_is_json_group_payload(tmp_path):
    file_name = tmp_path / "virtual_layer_backup.json"
    groups = [
        {
            ATTR_GROUP_NAME: "ui",
            ATTR_DEVICES: {"Device": [{"platform": "sensor"}]},
            ATTR_DEVICE_ATTRIBUTES: {},
        }
    ]

    await async_save_backup(str(file_name), groups)

    raw_backup = json.loads(file_name.read_text())
    assert raw_backup == {
        ATTR_VERSION: 1,
        "domain": COMPONENT_DOMAIN,
        ATTR_BACKUP_GROUPS: groups,
    }
    assert await async_load_backup(str(file_name)) == groups


@pytest.mark.asyncio
async def test_load_backup_returns_empty_list_for_invalid_payload(tmp_path):
    file_name = tmp_path / "invalid_backup.json"
    file_name.write_text(json.dumps({ATTR_BACKUP_GROUPS: {"not": "a list"}}))

    assert await async_load_backup(str(file_name)) == []


@pytest.mark.asyncio
async def test_load_backup_accepts_legacy_group_list_payload(tmp_path):
    file_name = tmp_path / "legacy_backup.json"
    groups = [
        {
            ATTR_GROUP_NAME: "ui",
            ATTR_DEVICES: {"Device": [{"platform": "sensor"}]},
        },
        "invalid",
    ]
    file_name.write_text(json.dumps(groups))

    assert await async_load_backup(str(file_name)) == [
        {
            ATTR_GROUP_NAME: "ui",
            ATTR_DEVICES: {"Device": [{"platform": "sensor"}]},
            ATTR_DEVICE_ATTRIBUTES: {},
        },
    ]


@pytest.mark.asyncio
async def test_load_backup_accepts_single_group_payload(tmp_path):
    file_name = tmp_path / "single_group_backup.json"
    file_name.write_text(json.dumps({
        ATTR_GROUP_NAME: "ui",
        ATTR_DEVICES: {"Device": [{"platform": "sensor"}]},
    }))

    assert await async_load_backup(str(file_name)) == [
        {
            ATTR_GROUP_NAME: "ui",
            ATTR_DEVICES: {"Device": [{"platform": "sensor"}]},
            ATTR_DEVICE_ATTRIBUTES: {},
        },
    ]


@pytest.mark.asyncio
async def test_save_backup_raises_when_file_cannot_be_written(tmp_path):
    file_name = tmp_path

    with pytest.raises(IsADirectoryError):
        await async_save_backup(str(file_name), [])


@pytest.mark.asyncio
async def test_atomic_json_save_keeps_previous_file_when_replace_fails(tmp_path, monkeypatch):
    file_name = tmp_path / "backup.json"
    file_name.write_text('{"previous": true}')

    def fail_replace(_source, _target):
        raise OSError("replace failed")

    monkeypatch.setattr("custom_components.virtual_layer.cfg.os.replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        await _async_save_json(str(file_name), {"next": True})

    assert json.loads(file_name.read_text()) == {"previous": True}
    assert not list(tmp_path.glob("backup.json.*.tmp"))


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
    assert entity[ATTR_DEVICE_ID] == "Damaged Device"


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
                # stale backup after a schema change.
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
