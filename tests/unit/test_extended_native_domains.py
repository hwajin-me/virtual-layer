"""Regression coverage for domain-specific Home Assistant entity contracts."""

from datetime import date, datetime, time, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from homeassistant.components.climate import ClimateEntityFeature
from homeassistant.components.date import DateEntity
from homeassistant.components.datetime import DateTimeEntity
from homeassistant.components.fan import FanEntityFeature
from homeassistant.components.lawn_mower import LawnMowerActivity, LawnMowerEntity
from homeassistant.components.media_player import MediaPlayerEntity, MediaPlayerState
from homeassistant.components.remote import RemoteEntity, RemoteEntityFeature
from homeassistant.components.select import SelectEntity
from homeassistant.components.sensor import (
    DEVICE_CLASS_UNITS,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.components.siren import SirenEntity
from homeassistant.components.text import TextEntity
from homeassistant.components.time import TimeEntity
from homeassistant.components.update import UpdateEntity
from homeassistant.components.water_heater import WaterHeaterEntity
from homeassistant.const import (
    ATTR_ENTITY_ID,
    UnitOfEnergy,
)

from custom_components.virtual_layer.climate import (
    ATTR_TARGET_TEMPERATURE_HIGH,
    ATTR_TARGET_TEMPERATURE_LOW,
    CLIMATE_SCHEMA,
    VirtualClimate,
)
from custom_components.virtual_layer.climate_options import (
    migrate_legacy_climate_attributes,
)
from custom_components.virtual_layer.const import (
    ATTR_UNIQUE_ID,
    CONF_ATTRIBUTES,
    CONF_INITIAL_VALUE,
    CONF_NAME,
)
from custom_components.virtual_layer.fan import FAN_SCHEMA, VirtualFan
from custom_components.virtual_layer.humidifier import (
    HUMIDIFIER_SCHEMA,
    VirtualHumidifier,
)
from custom_components.virtual_layer.light import (
    CONF_INITIAL_COLOR_TEMP,
    CONF_SUPPORT_COLOR_TEMP,
    LIGHT_SCHEMA,
    VirtualLight,
)
from custom_components.virtual_layer.number import NUMBER_SCHEMA, VirtualNumber
from custom_components.virtual_layer.sensor import (
    SENSOR_SCHEMA,
    UNITS_OF_MEASUREMENT,
    VirtualSensor,
)
from custom_components.virtual_layer.vacuum import VACUUM_SCHEMA, VirtualVacuum

pytestmark = pytest.mark.unit


def _config(schema, domain: str, initial_value: str = "unknown", **options):
    return schema({
        CONF_NAME: f"Native {domain}",
        ATTR_ENTITY_ID: f"{domain}.native",
        ATTR_UNIQUE_ID: f"{domain}.native.unique",
        CONF_INITIAL_VALUE: initial_value,
        **options,
    })


async def test_climate_migrates_native_modes_from_legacy_virtual_attributes():
    migrated = migrate_legacy_climate_attributes({
        CONF_NAME: "Legacy Climate",
        ATTR_ENTITY_ID: "climate.legacy",
        ATTR_UNIQUE_ID: "climate.legacy.unique",
        CONF_INITIAL_VALUE: "cool",
        CONF_ATTRIBUTES: {
            "fan_mode": "auto",
            "fan_modes": ["medium", "high", "turbo", "auto"],
            "hvac_modes": ["off", "cool", "dry", "fan_only"],
            "preset_mode": "none",
            "preset_modes": ["none", "sleep", "quiet"],
            "supported_features": 441,
            "swing_mode": "vertical",
            "swing_modes": ["off", "vertical"],
            "temperature": 23,
            "vendor_attribute": "kept",
        },
    })
    climate = VirtualClimate(CLIMATE_SCHEMA(migrated), False)
    climate._create_state(climate._config)
    climate.async_write_ha_state = Mock()

    assert climate.fan_modes == ["medium", "high", "turbo", "auto"]
    assert climate.fan_mode == "auto"
    assert climate.preset_modes == ["none", "sleep", "quiet"]
    assert climate.preset_mode == "none"
    assert climate.swing_modes == ["off", "vertical"]
    assert climate.swing_mode == "vertical"
    assert climate.target_temperature == 23
    assert ClimateEntityFeature.FAN_MODE in climate.supported_features
    assert ClimateEntityFeature.PRESET_MODE in climate.supported_features
    assert ClimateEntityFeature.SWING_MODE in climate.supported_features
    assert ClimateEntityFeature.TARGET_HUMIDITY not in climate.supported_features
    assert migrated[CONF_ATTRIBUTES] == {"vendor_attribute": "kept"}

    await climate.async_set_fan_mode("turbo")
    await climate.async_set_preset_mode("sleep")
    await climate.async_set_swing_mode("off")
    assert climate.fan_mode == "turbo"
    assert climate.preset_mode == "sleep"
    assert climate.swing_mode == "off"


def test_climate_exposes_target_humidity_only_when_configured():
    without_humidity = VirtualClimate(
        _config(CLIMATE_SCHEMA, "climate", "off"),
        False,
    )
    with_humidity = VirtualClimate(
        _config(CLIMATE_SCHEMA, "climate", "off", target_humidity=50),
        False,
    )

    assert ClimateEntityFeature.TARGET_HUMIDITY not in without_humidity.supported_features
    assert ClimateEntityFeature.TARGET_HUMIDITY in with_humidity.supported_features


def test_fan_advertises_preset_mode_only_when_modes_are_configured():
    without_modes = VirtualFan(
        _config(FAN_SCHEMA, "fan", "off", speed_count=3),
        False,
    )
    with_modes = VirtualFan(
        _config(
            FAN_SCHEMA,
            "fan",
            "off",
            speed_count=3,
            modes=["eco", "boost"],
        ),
        False,
    )

    assert FanEntityFeature.PRESET_MODE not in without_modes.supported_features
    assert FanEntityFeature.PRESET_MODE in with_modes.supported_features


@pytest.mark.parametrize(
    ("domain", "base_class"),
    [
        ("select", SelectEntity),
        ("text", TextEntity),
        ("date", DateEntity),
        ("time", TimeEntity),
        ("datetime", DateTimeEntity),
        ("siren", SirenEntity),
        ("lawn_mower", LawnMowerEntity),
        ("remote", RemoteEntity),
        ("media_player", MediaPlayerEntity),
        ("water_heater", WaterHeaterEntity),
        ("update", UpdateEntity),
    ],
)
def test_service_domains_use_native_home_assistant_entities(domain, base_class):
    module = __import__(
        f"custom_components.virtual_layer.{domain}",
        fromlist=["ENTITY_SCHEMA", "ENTITY_CLASS"],
    )
    config = module.ENTITY_SCHEMA({
        CONF_NAME: f"Native {domain}",
        ATTR_ENTITY_ID: f"{domain}.native",
        ATTR_UNIQUE_ID: f"{domain}.native.unique",
        CONF_INITIAL_VALUE: "unknown",
    })

    entity = module.ENTITY_CLASS(config, False)

    assert isinstance(entity, base_class)


async def test_select_text_and_temporal_entities_apply_native_values():
    from custom_components.virtual_layer.date import ENTITY_CLASS as VirtualDate
    from custom_components.virtual_layer.date import ENTITY_SCHEMA as DATE_SCHEMA
    from custom_components.virtual_layer.datetime import ENTITY_CLASS as VirtualDateTime
    from custom_components.virtual_layer.datetime import (
        ENTITY_SCHEMA as DATETIME_SCHEMA,
    )
    from custom_components.virtual_layer.select import ENTITY_CLASS as VirtualSelect
    from custom_components.virtual_layer.select import ENTITY_SCHEMA as SELECT_SCHEMA
    from custom_components.virtual_layer.text import ENTITY_CLASS as VirtualText
    from custom_components.virtual_layer.text import ENTITY_SCHEMA as TEXT_SCHEMA
    from custom_components.virtual_layer.time import ENTITY_CLASS as VirtualTime
    from custom_components.virtual_layer.time import ENTITY_SCHEMA as TIME_SCHEMA

    select = VirtualSelect(
        _config(SELECT_SCHEMA, "select", "eco", options=["eco", "boost"]),
        False,
    )
    text = VirtualText(
        _config(TEXT_SCHEMA, "text", "hello", min=2, max=8),
        False,
    )
    date_entity = VirtualDate(_config(DATE_SCHEMA, "date", "2026-08-04"), False)
    time_entity = VirtualTime(_config(TIME_SCHEMA, "time", "12:34:56"), False)
    datetime_entity = VirtualDateTime(
        _config(DATETIME_SCHEMA, "datetime", "2026-08-04T12:34:56+09:00"),
        False,
    )
    entities = [select, text, date_entity, time_entity, datetime_entity]
    for entity in entities:
        entity._create_state(entity._config)
        entity.async_write_ha_state = Mock()

    await select.async_select_option("boost")
    await text.async_set_value("updated")
    await date_entity.async_set_value(date(2026, 8, 5))
    await time_entity.async_set_value(time(1, 2, 3))
    await datetime_entity.async_set_value(
        datetime(2026, 8, 5, 1, 2, 3, tzinfo=timezone.utc)
    )

    assert select.current_option == "boost"
    assert text.native_value == "updated"
    assert date_entity.native_value == date(2026, 8, 5)
    assert time_entity.native_value == time(1, 2, 3)
    assert datetime_entity.native_value == datetime(
        2026, 8, 5, 1, 2, 3, tzinfo=timezone.utc
    )
    assert all(entity.async_write_ha_state.called for entity in entities)


async def test_generic_entities_repair_malformed_saved_options_and_publish_actions():
    from custom_components.virtual_layer.button import ENTITY_CLASS as VirtualButton
    from custom_components.virtual_layer.button import ENTITY_SCHEMA as BUTTON_SCHEMA
    from custom_components.virtual_layer.media_player import (
        ENTITY_CLASS as VirtualMediaPlayer,
    )
    from custom_components.virtual_layer.media_player import (
        ENTITY_SCHEMA as MEDIA_PLAYER_SCHEMA,
    )
    from custom_components.virtual_layer.text import ENTITY_CLASS as VirtualText
    from custom_components.virtual_layer.text import ENTITY_SCHEMA as TEXT_SCHEMA
    from custom_components.virtual_layer.water_heater import (
        ENTITY_CLASS as VirtualWaterHeater,
    )
    from custom_components.virtual_layer.water_heater import (
        ENTITY_SCHEMA as WATER_HEATER_SCHEMA,
    )

    text = VirtualText(_config(
        TEXT_SCHEMA,
        "text",
        "saved",
        min="not-a-number",
        max=[],
        mode=object(),
        pattern="[A-Z]{2}\\d{2}",
    ), False)
    button = VirtualButton(_config(
        BUTTON_SCHEMA,
        "button",
        attributes={"press_count": "corrupt"},
    ), False)
    media_player = VirtualMediaPlayer(_config(
        MEDIA_PLAYER_SCHEMA,
        "media_player",
        source_list="not-a-list",
        volume_level="not-a-number",
        is_volume_muted="not-a-bool",
    ), False)
    water_heater = VirtualWaterHeater(_config(
        WATER_HEATER_SCHEMA,
        "water_heater",
        min_temp="not-a-number",
        max_temp=float("nan"),
        target_temperature="not-a-number",
        target_temperature_step=0,
        operation_list="not-a-list",
    ), False)
    for entity in (text, button, media_player, water_heater):
        entity._create_state(entity._config)
        entity.async_write_ha_state = Mock()

    assert text.native_min == 0
    assert text.native_max == 255
    with pytest.raises(ValueError, match="pattern"):
        await text.async_set_value("not-valid")
    await text.async_set_value("AB12")
    await button.async_press()

    assert button.extra_state_attributes["press_count"] == 1
    button.async_write_ha_state.assert_called_once()
    assert media_player.source_list == []
    assert media_player.volume_level == 0.5
    assert media_player.is_volume_muted is False
    assert water_heater.min_temp == 35
    assert water_heater.max_temp == 85
    assert water_heater.target_temperature is None
    assert water_heater.target_temperature_step == 1
    assert water_heater.operation_list == ["off", "heat"]


async def test_siren_and_lawn_mower_native_commands_publish_state():
    from custom_components.virtual_layer.lawn_mower import (
        ENTITY_CLASS as VirtualLawnMower,
    )
    from custom_components.virtual_layer.lawn_mower import ENTITY_SCHEMA as MOWER_SCHEMA
    from custom_components.virtual_layer.siren import ENTITY_CLASS as VirtualSiren
    from custom_components.virtual_layer.siren import ENTITY_SCHEMA as SIREN_SCHEMA

    siren = VirtualSiren(
        _config(SIREN_SCHEMA, "siren", "off", available_tones=["alarm"]),
        False,
    )
    mower = VirtualLawnMower(
        _config(MOWER_SCHEMA, "lawn_mower", "docked"),
        False,
    )
    for entity in (siren, mower):
        entity._create_state(entity._config)
        entity.async_write_ha_state = Mock()

    await siren.async_turn_on(tone="alarm", volume_level=0.7, duration=30)
    await mower.async_start_mowing()
    assert siren.is_on is True
    assert siren.extra_state_attributes["tone"] == "alarm"
    assert mower.activity is LawnMowerActivity.MOWING

    await siren.async_turn_off()
    await mower.async_dock()
    assert siren.is_on is False
    assert mower.activity is LawnMowerActivity.RETURNING


async def test_remote_media_water_heater_and_update_native_commands():
    from custom_components.virtual_layer.media_player import (
        ENTITY_CLASS as VirtualMediaPlayer,
    )
    from custom_components.virtual_layer.media_player import (
        ENTITY_SCHEMA as MEDIA_PLAYER_SCHEMA,
    )
    from custom_components.virtual_layer.remote import (
        ENTITY_CLASS as VirtualRemote,
    )
    from custom_components.virtual_layer.remote import (
        ENTITY_SCHEMA as REMOTE_SCHEMA,
    )
    from custom_components.virtual_layer.update import (
        ENTITY_CLASS as VirtualUpdate,
    )
    from custom_components.virtual_layer.update import (
        ENTITY_SCHEMA as UPDATE_SCHEMA,
    )
    from custom_components.virtual_layer.water_heater import (
        ENTITY_CLASS as VirtualWaterHeater,
    )
    from custom_components.virtual_layer.water_heater import (
        ENTITY_SCHEMA as WATER_HEATER_SCHEMA,
    )

    remote = VirtualRemote(
        _config(
            REMOTE_SCHEMA,
            "remote",
            "off",
            activity_list=["TV", "Music"],
            current_activity="TV",
        ),
        False,
    )
    media_player = VirtualMediaPlayer(
        _config(
            MEDIA_PLAYER_SCHEMA,
            "media_player",
            "idle",
            source_list=["TV", "Radio"],
            source="TV",
            volume_level=0.5,
        ),
        False,
    )
    water_heater = VirtualWaterHeater(
        _config(
            WATER_HEATER_SCHEMA,
            "water_heater",
            "eco",
            operation_list=["off", "eco", "heat"],
            min_temp=35,
            max_temp=70,
            target_temperature=50,
        ),
        False,
    )
    update = VirtualUpdate(
        _config(
            UPDATE_SCHEMA,
            "update",
            "1.0.0",
            installed_version="1.0.0",
            latest_version="1.1.0",
            versions=["1.0.0", "1.1.0"],
        ),
        False,
    )
    entities = (remote, media_player, water_heater, update)
    for entity in entities:
        entity._create_state(entity._config)
        entity._update_attributes()
        entity.async_write_ha_state = Mock()

    await remote.async_turn_on(activity="Music")
    await remote.async_send_command(["POWER", "INPUT"])
    await media_player.async_media_play()
    await media_player.async_set_volume_level(0.7)
    await media_player.async_select_source("Radio")
    await water_heater.async_set_temperature(temperature=55)
    await water_heater.async_turn_off()
    await update.async_install(None, backup=True)

    assert remote.is_on is True
    assert remote.current_activity == "Music"
    assert RemoteEntityFeature.ACTIVITY in remote.supported_features
    assert remote.extra_state_attributes["last_command"] == ["POWER", "INPUT"]
    assert media_player.state is MediaPlayerState.PLAYING
    assert media_player.volume_level == 0.7
    assert media_player.source == "Radio"
    assert water_heater.target_temperature == 55
    assert water_heater.current_operation == "off"
    assert update.installed_version == "1.1.0"
    assert update.extra_state_attributes["last_install_backup"] is True
    assert "current_activity" not in remote.extra_state_attributes
    assert "source" not in media_player.extra_state_attributes
    assert "volume_level" not in media_player.extra_state_attributes
    assert "target_temperature" not in water_heater.extra_state_attributes
    assert "installed_version" not in update.extra_state_attributes
    assert "latest_version" not in update.extra_state_attributes

    with pytest.raises(ValueError):
        await media_player.async_set_volume_level(1.1)
    with pytest.raises(ValueError):
        await media_player.async_select_source("Invalid")
    with pytest.raises(ValueError):
        await water_heater.async_set_temperature(temperature=80)
    with pytest.raises(ValueError):
        await update.async_install("2.0.0", backup=False)


def test_energy_sensor_uses_native_sensor_contract_and_state_class():
    entity = VirtualSensor(
        _config(
            SENSOR_SCHEMA,
            "sensor",
            "12.5",
            **{
                "class": "energy",
                "unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR,
                "state_class": SensorStateClass.TOTAL_INCREASING,
            },
        ),
        False,
    )
    entity._create_state(entity._config)

    assert isinstance(entity, SensorEntity)
    assert entity.native_value == "12.5"
    assert entity.native_unit_of_measurement == UnitOfEnergy.KILO_WATT_HOUR
    assert entity.state_class is SensorStateClass.TOTAL_INCREASING
    assert entity.state == "12.5"


def test_all_default_sensor_units_are_valid_for_their_device_class():
    for device_class, unit in UNITS_OF_MEASUREMENT.items():
        assert unit in DEVICE_CLASS_UNITS[device_class]


def test_sensor_migrates_legacy_native_metadata_and_timestamp_value():
    entity = VirtualSensor(
        _config(
            SENSOR_SCHEMA,
            "sensor",
            "2026-08-04T12:34:56+09:00",
            attributes={
                "device_class": "timestamp",
                "state_class": None,
            },
        ),
        False,
    )
    entity._create_state(entity._config)

    assert entity.device_class == "timestamp"
    assert entity.native_value == datetime(
        2026, 8, 4, 3, 34, 56, tzinfo=timezone.utc
    )
    assert entity.state == "2026-08-04T03:34:56+00:00"


def test_numeric_sensor_unknown_value_is_exposed_as_unknown_not_an_error():
    entity = VirtualSensor(
        _config(
            SENSOR_SCHEMA,
            "sensor",
            "unknown",
            **{"class": "power", "unit_of_measurement": "W"},
        ),
        False,
    )
    entity._create_state(entity._config)

    assert entity.native_value is None
    assert entity.state is None


@pytest.mark.parametrize(
    ("device_class", "initial_value", "restored_value", "expected"),
    [
        ("date", "2026-08-06", "not-a-date", date(2026, 8, 6)),
        (
            "timestamp",
            "2026-08-06T12:00:00+09:00",
            "not-a-timestamp",
            datetime(2026, 8, 6, 3, 0, tzinfo=timezone.utc),
        ),
    ],
)
def test_temporal_sensor_recovers_invalid_restored_state(
    device_class,
    initial_value,
    restored_value,
    expected,
):
    entity = VirtualSensor(
        _config(
            SENSOR_SCHEMA,
            "sensor",
            initial_value,
            **{"class": device_class},
        ),
        False,
    )

    entity._restore_state(
        SimpleNamespace(state=restored_value, attributes={}),
        entity._config,
    )

    assert entity.native_value == expected


def test_light_migrates_legacy_mired_color_temperature_to_kelvin():
    legacy = VirtualLight(
        _config(
            LIGHT_SCHEMA,
            "light",
            "on",
            **{CONF_SUPPORT_COLOR_TEMP: True, CONF_INITIAL_COLOR_TEMP: 240},
        ),
        False,
    )
    kelvin = VirtualLight(
        _config(
            LIGHT_SCHEMA,
            "light",
            "on",
            **{CONF_SUPPORT_COLOR_TEMP: True, CONF_INITIAL_COLOR_TEMP: 4000},
        ),
        False,
    )
    legacy._create_state(legacy._config)
    kelvin._create_state(kelvin._config)

    assert legacy.color_temp_kelvin == 4167
    assert kelvin.color_temp_kelvin == 4000


async def test_native_range_and_mode_validation_prevents_invalid_states():
    fan = VirtualFan(_config(FAN_SCHEMA, "fan", "off", speed_count=3), False)
    humidifier = VirtualHumidifier(
        _config(
            HUMIDIFIER_SCHEMA,
            "humidifier",
            "off",
            min_humidity=30,
            max_humidity=70,
            modes=["normal", "eco"],
        ),
        False,
    )
    climate = VirtualClimate(
        _config(
            CLIMATE_SCHEMA,
            "climate",
            "off",
            hvac_modes=["off", "heat"],
            fan_modes=["low", "high"],
            min_temp=10,
            max_temp=30,
        ),
        False,
    )
    for entity in (fan, humidifier, climate):
        entity._create_state(entity._config)
        entity.async_write_ha_state = Mock()

    with pytest.raises(ValueError):
        await fan.async_set_percentage(101)
    with pytest.raises(ValueError):
        await humidifier.async_set_humidity(10)
    with pytest.raises(ValueError):
        await humidifier.async_set_mode("invalid")
    with pytest.raises(ValueError):
        await climate.async_set_hvac_mode("cool")
    with pytest.raises(ValueError):
        await climate.async_set_fan_mode("turbo")
    with pytest.raises(ValueError):
        await climate.async_set_temperature(temperature=31)


async def test_native_restore_and_range_updates_are_defensive():
    climate = VirtualClimate(
        _config(
            CLIMATE_SCHEMA,
            "climate",
            "off",
            hvac_modes=["off", "heat"],
            target_temperature=20,
            target_temperature_high=25,
            target_temperature_low=15,
            min_temp=10,
            max_temp=30,
        ),
        False,
    )
    climate._create_state(climate._config)
    climate.async_write_ha_state = Mock()

    with pytest.raises(ValueError):
        await climate.async_set_temperature(
            **{
                ATTR_TARGET_TEMPERATURE_LOW: 28,
                ATTR_TARGET_TEMPERATURE_HIGH: 22,
            },
        )
    assert climate.target_temperature_low == 15
    assert climate.target_temperature_high == 25

    climate._restore_state(SimpleNamespace(
        state="removed_hvac_mode",
        attributes={
            "hvac_action": "removed_action",
            "current_temperature": "nan",
            "target_temperature": "not-a-number",
        },
    ), climate._config)
    assert climate.hvac_mode == "off"
    assert climate.hvac_action is None
    assert climate.current_temperature is None
    assert climate.target_temperature is None

    from custom_components.virtual_layer.humidifier import _as_action
    from custom_components.virtual_layer.light import _as_color_temp_kelvin

    assert _as_action("removed_action") is None
    assert _as_color_temp_kelvin(0) == 4000

    number = VirtualNumber(_config(
        NUMBER_SCHEMA,
        "number",
        "nan",
        min=float("nan"),
        max=float("inf"),
    ), False)
    number._create_state(number._config)
    assert number.native_min_value == 0
    assert number.native_max_value == 100
    assert number.native_value == 0


def test_climate_and_humidifier_restore_home_assistant_native_target_keys():
    climate = VirtualClimate(
        _config(
            CLIMATE_SCHEMA,
            "climate",
            "off",
            hvac_modes=["off", "heat"],
            min_temp=10,
            max_temp=30,
            min_humidity=20,
            max_humidity=80,
            target_temperature=20,
            target_temperature_high=25,
            target_temperature_low=15,
            target_humidity=45,
        ),
        False,
    )
    humidifier = VirtualHumidifier(
        _config(
            HUMIDIFIER_SCHEMA,
            "humidifier",
            "on",
            min_humidity=20,
            max_humidity=80,
            target_humidity=45,
        ),
        False,
    )

    climate._restore_state(
        SimpleNamespace(
            state="heat",
            attributes={
                "temperature": 24,
                "target_temp_high": 27,
                "target_temp_low": 17,
                "humidity": 48,
            },
        ),
        climate._config,
    )
    humidifier._restore_state(
        SimpleNamespace(state="on", attributes={"humidity": 55}),
        humidifier._config,
    )

    assert climate.target_temperature == 24
    assert climate.target_temperature_high == 27
    assert climate.target_temperature_low == 17
    assert climate.target_humidity == 48
    assert humidifier.target_humidity == 55


def test_native_generic_entities_restore_runtime_service_attributes():
    from custom_components.virtual_layer.media_player import (
        ENTITY_CLASS as VirtualMediaPlayer,
    )
    from custom_components.virtual_layer.media_player import (
        ENTITY_SCHEMA as MEDIA_PLAYER_SCHEMA,
    )
    from custom_components.virtual_layer.remote import ENTITY_CLASS as VirtualRemote
    from custom_components.virtual_layer.remote import ENTITY_SCHEMA as REMOTE_SCHEMA
    from custom_components.virtual_layer.water_heater import (
        ENTITY_CLASS as VirtualWaterHeater,
    )
    from custom_components.virtual_layer.water_heater import (
        ENTITY_SCHEMA as WATER_HEATER_SCHEMA,
    )

    remote = VirtualRemote(
        _config(
            REMOTE_SCHEMA,
            "remote",
            "off",
            activity_list=["TV", "Music"],
            current_activity="TV",
        ),
        False,
    )
    media_player = VirtualMediaPlayer(
        _config(
            MEDIA_PLAYER_SCHEMA,
            "media_player",
            "idle",
            source_list=["TV", "Radio"],
            source="TV",
            volume_level=0.2,
            is_volume_muted=False,
        ),
        False,
    )
    water_heater = VirtualWaterHeater(
        _config(
            WATER_HEATER_SCHEMA,
            "water_heater",
            "off",
            operation_list=["off", "heat"],
            min_temp=35,
            max_temp=70,
            current_temperature=40,
            target_temperature=45,
        ),
        False,
    )

    remote._restore_state(
        SimpleNamespace(
            state="on",
            attributes={"current_activity": "Music"},
        ),
        remote._config,
    )
    media_player._restore_state(
        SimpleNamespace(
            state="playing",
            attributes={
                "source": "Radio",
                "volume_level": 0.8,
                "is_volume_muted": True,
            },
        ),
        media_player._config,
    )
    water_heater._restore_state(
        SimpleNamespace(
            state="heat",
            attributes={
                "current_temperature": 48,
                "temperature": 55,
            },
        ),
        water_heater._config,
    )

    assert remote.current_activity == "Music"
    assert media_player.source == "Radio"
    assert media_player.volume_level == 0.8
    assert media_player.is_volume_muted is True
    assert water_heater.current_temperature == 48
    assert water_heater.target_temperature == 55


def test_native_generic_restore_rejects_removed_options_and_bad_values():
    from custom_components.virtual_layer.media_player import (
        ENTITY_CLASS as VirtualMediaPlayer,
    )
    from custom_components.virtual_layer.media_player import (
        ENTITY_SCHEMA as MEDIA_PLAYER_SCHEMA,
    )
    from custom_components.virtual_layer.remote import ENTITY_CLASS as VirtualRemote
    from custom_components.virtual_layer.remote import ENTITY_SCHEMA as REMOTE_SCHEMA

    remote = VirtualRemote(
        _config(
            REMOTE_SCHEMA,
            "remote",
            "off",
            activity_list=["TV"],
            current_activity="TV",
        ),
        False,
    )
    media_player = VirtualMediaPlayer(
        _config(
            MEDIA_PLAYER_SCHEMA,
            "media_player",
            "idle",
            source_list=["TV"],
            source="TV",
            volume_level=0.2,
            is_volume_muted=False,
        ),
        False,
    )

    remote._restore_state(
        SimpleNamespace(
            state="on",
            attributes={"current_activity": "Removed"},
        ),
        remote._config,
    )
    media_player._restore_state(
        SimpleNamespace(
            state="playing",
            attributes={
                "source": "Removed",
                "volume_level": "invalid",
                "is_volume_muted": "invalid",
            },
        ),
        media_player._config,
    )

    assert remote.current_activity == "TV"
    assert media_player.source == "TV"
    assert media_player.volume_level == 0.2
    assert media_player.is_volume_muted is False


def test_climate_and_humidifier_recover_non_finite_configured_ranges():
    climate = VirtualClimate(
        _config(
            CLIMATE_SCHEMA,
            "climate",
            "off",
            hvac_modes=["off", "heat"],
            min_temp=float("nan"),
            max_temp=float("inf"),
            min_humidity=float("-inf"),
            max_humidity=float("nan"),
            target_temperature_step=float("nan"),
            target_humidity_step=float("inf"),
        ),
        False,
    )
    humidifier = VirtualHumidifier(
        _config(
            HUMIDIFIER_SCHEMA,
            "humidifier",
            "off",
            min_humidity=float("nan"),
            max_humidity=float("inf"),
            target_humidity_step=float("nan"),
        ),
        False,
    )

    climate._create_state(climate._config)
    humidifier._create_state(humidifier._config)

    assert climate.min_temp == 7
    assert climate.max_temp == 35
    assert climate.target_temperature_step == 0.1
    assert climate._attr_target_humidity_step is None
    assert humidifier.min_humidity == 0
    assert humidifier.max_humidity == 100
    assert humidifier._attr_target_humidity_step is None


def test_fan_light_and_vacuum_reject_malformed_restored_attributes():
    fan = VirtualFan(
        _config(
            FAN_SCHEMA,
            "fan",
            "off",
            speed_count=3,
            oscillate=True,
            direction=True,
            modes=["eco", "boost"],
        ),
        False,
    )
    light = VirtualLight(
        _config(
            LIGHT_SCHEMA,
            "light",
            "on",
            support_color=True,
            initial_color=[120, 50],
            support_effect=True,
            initial_effect="none",
            initial_effect_list=["none", "rainbow"],
        ),
        False,
    )
    vacuum = VirtualVacuum(
        _config(
            VACUUM_SCHEMA,
            "vacuum",
            "docked",
            battery_level=50,
            fan_speed="normal",
            fan_speed_list=["normal", "turbo"],
        ),
        False,
    )

    fan._restore_state(
        SimpleNamespace(
            state="on",
            attributes={
                "direction": "sideways",
                "oscillating": "yes",
                "percentage": float("inf"),
                "preset_mode": "removed",
            },
        ),
        fan._config,
    )
    light._restore_state(
        SimpleNamespace(
            state="on",
            attributes={
                "color_mode": "hs",
                "hs_color": {"bad": "shape"},
                "brightness": float("nan"),
                "effect": "removed",
            },
        ),
        light._config,
    )
    vacuum._restore_state(
        SimpleNamespace(
            state="cleaning",
            attributes={"battery_level": 101, "fan_speed": "removed"},
        ),
        vacuum._config,
    )

    assert fan.current_direction == "forward"
    assert fan.oscillating is False
    assert fan.percentage is None
    assert fan.preset_mode is None
    assert light.hs_color == (120, 50)
    assert light.brightness == 255
    assert light.effect == "none"
    assert vacuum._battery_level == 50
    assert vacuum.fan_speed == "normal"


def test_fan_and_light_without_optional_features_publish_initial_attributes():
    fan = VirtualFan(_config(
        FAN_SCHEMA,
        "fan",
        "off",
        speed_count=0,
        oscillate=False,
        direction=False,
    ), False)
    light = VirtualLight(_config(
        LIGHT_SCHEMA,
        "light",
        "on",
        support_brightness=False,
        support_color=False,
        support_color_temp=False,
        support_effect=False,
    ), False)

    fan._create_state(fan._config)
    light._create_state(light._config)
    fan._update_attributes()
    light._update_attributes()

    assert "percentage" not in fan.extra_state_attributes
    assert light.brightness is None
    assert light.color_mode == "onoff"
    assert "effect" not in light.extra_state_attributes
