"""Real HA lifecycle and platform outputs. Only the clock is injected."""

import pytest
from homeassistant.core import State
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.virtual_layer.presence_fusion.adapters import gps_observation
from custom_components.virtual_layer.presence_fusion.models import Settings


class FakeClock:
    def __init__(self, utc):
        self.now = utc
        self.mono = 0

    def utc(self):
        return self.now

    def monotonic(self):
        return self.mono

    def advance(self, seconds):
        self.now += seconds
        self.mono += seconds


def configuration(prefix="test", count=1):
    return {
        "devices": [
            {
                "id": f"{prefix}_{i}",
                "name": f"Synthetic {i}",
                "priority": i,
                "candidate": True,
                "sources": [
                    {
                        "id": f"{prefix}_gps_{i}",
                        "kind": "gps",
                        "entity_id": f"device_tracker.{prefix}_{i}",
                        "timestamp_attribute": "measured",
                        "timestamp_format": "seconds",
                    }
                ],
            }
            for i in range(count)
        ],
        "settings": {},
    }


async def setup(hass, prefix="test", count=1, config=None):
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "zone", {})
    hass.states.async_set(
        "zone.home", "0", {"latitude": 0, "longitude": 0, "radius": 100}
    )
    entry = MockConfigEntry(
        domain="virtual_layer",
        title=prefix,
        data={"group_name": prefix, "presence_fusion": True},
        options={"presence_fusion": config or configuration(prefix, count)},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    c = entry.runtime_data.coordinator
    c.clock = FakeClock(c.clock.utc())
    return entry, c


async def gps(hass, c, index=0, x=0, prefix="test"):
    hass.states.async_set(
        f"device_tracker.{prefix}_{index}",
        "not_home",
        {
            "latitude": 0,
            "longitude": x / 111195.08,
            "gps_accuracy": 10,
            "measured": c.clock.utc(),
        },
    )
    await hass.async_block_till_done()


def output(hass, entry, key):
    registry = er.async_get(hass)
    row = next(
        row
        for row in er.async_entries_for_config_entry(registry, entry.entry_id)
        if row.unique_id == f"{entry.entry_id}:presence_fusion:{key}"
    )
    return hass.states.get(row.entity_id)


async def test_T28_T30_T46_native_outputs_timer(hass):
    entry, c = await setup(hass)
    await gps(hass, c)
    c.clock.advance(60)
    await gps(hass, c)
    assert output(hass, entry, "presence").state == "home"
    assert output(hass, entry, "home").state == "on"
    assert output(hass, entry, "gps").attributes["latitude"] == 0
    c.clock.advance(300)
    c._tick(None)
    await hass.async_block_till_done()
    assert output(hass, entry, "gps").state == "unavailable"
    c.clock.advance(300)
    c._tick(None)
    await hass.async_block_till_done()
    assert output(hass, entry, "presence").state == "unknown"
    assert output(hass, entry, "home").state == "unknown"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_T40_T41_entry_isolation_reload(hass):
    a, ca = await setup(hass, "one")
    b, cb = await setup(hass, "two")
    await gps(hass, ca, prefix="one")
    await gps(hass, cb, prefix="two", x=2000)
    registry = er.async_get(hass)
    ids = {r.unique_id for r in er.async_entries_for_config_entry(registry, a.entry_id)}
    assert await hass.config_entries.async_reload(a.entry_id)
    await hass.async_block_till_done()
    assert ca.stopped and not ca.unsubs and ca.state_unsub is None
    assert ids == {
        r.unique_id for r in er.async_entries_for_config_entry(registry, a.entry_id)
    }
    assert await hass.config_entries.async_unload(a.entry_id)
    cb.clock.advance(60)
    await gps(hass, cb, prefix="two", x=2100)
    assert output(hass, b, "gps").state != "unavailable"
    assert not cb.stopped
    assert await hass.config_entries.async_unload(b.entry_id)


async def test_T44_service_validation(hass):
    entry, c = await setup(hass)
    await gps(hass, c)
    from homeassistant.helpers.service import async_get_all_descriptions

    descriptions = await async_get_all_descriptions(hass)
    assert descriptions["presence_fusion"]["set_primary"]["fields"][
        "tracked_device_id"
    ]["required"]
    from homeassistant.exceptions import ServiceValidationError

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "presence_fusion",
            "set_primary",
            {"config_entry_id": entry.entry_id, "tracked_device_id": "other"},
            blocking=True,
        )
    await hass.services.async_call(
        "presence_fusion",
        "set_primary",
        {
            "config_entry_id": entry.entry_id,
            "tracked_device_id": "test_0",
            "duration": 60,
        },
        blocking=True,
    )
    assert c.data.mode == "manual"
    c.clock.advance(60)
    c._tick(None)
    assert c.data.mode == "dynamic"
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize(
    "duration", [True, False, 0, 86401, float("nan"), float("inf")]
)
async def test_T44_invalid_duration_has_no_side_effect(hass, duration):
    import voluptuous as vol

    entry, c = await setup(hass)
    await gps(hass, c, x=2000)
    before = c.engine.metadata()
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            "presence_fusion",
            "set_primary",
            {
                "config_entry_id": entry.entry_id,
                "tracked_device_id": "test_0",
                "duration": duration,
            },
            blocking=True,
        )
    assert c.engine.metadata() == before
    assert await hass.config_entries.async_unload(entry.entry_id)


def test_T24_T25_measurement_adapter():
    config = {"entity_id": "device_tracker.synthetic"}
    state = State(
        config["entity_id"], "home", {"latitude": 0, "longitude": 0, "gps_accuracy": 10}
    )
    p, reason = gps_observation(config, state, None, 0, Settings(), initial=True)
    assert p is None and reason == "restored_without_timestamp"
    battery = State(config["entity_id"], "home", {**state.attributes, "battery": 90})
    p, reason = gps_observation(config, battery, state, 100, Settings())
    assert p is None and reason == "not_location_event"
    measured = State(config["entity_id"], "home", {**state.attributes, "measured": 100})
    p, reason = gps_observation(
        {**config, "timestamp_attribute": "measured", "timestamp_format": "seconds"},
        measured,
        state,
        100,
        Settings(),
    )
    assert p.observed == 100 and reason is None


async def test_T02_T08_T09_T42_events_to_entities(hass, monkeypatch):
    cfg = configuration(count=3)
    for i in (0, 2):
        cfg["devices"][i]["sources"].append(
            {"id": f"local_{i}", "entity_id": f"binary_sensor.home_{i}", "kind": "wifi"}
        )
        hass.states.async_set(f"binary_sensor.home_{i}", "on")
    entry, c = await setup(hass, count=3, config=cfg)
    for step in range(14):
        if step:
            c.clock.advance(30)
        for i in range(3):
            await gps(hass, c, i, max(0, step - 2) * 120 if i == 1 else 0)
    assert c.data.primary == "test_1" and c.data.mode == "dynamic"
    assert output(hass, entry, "presence").state == "away"
    now = c.clock.utc()
    # The restart must use the simulated UTC already reached by observations,
    # including during Store restoration, rather than jump back six minutes.
    monkeypatch.setattr(
        "custom_components.virtual_layer.presence_fusion.coordinator.Clock",
        lambda: FakeClock(now + 1),
    )
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    c = entry.runtime_data.coordinator
    c.clock = FakeClock(now + 1)
    for i in range(3):
        await gps(hass, c, i, 1320 if i == 1 else 0)
    assert c.data.primary == "test_1" and c.data.mode == "dynamic"
    assert output(hass, entry, "presence").state == "away"
    assert not c.engine.paths["test_1"].segments
    # Primary stops reporting; fresh home devices cannot become fallback.
    for _ in range(21):
        c.clock.advance(30)
        for i in (0, 2):
            await gps(hass, c, i)
    assert output(hass, entry, "gps").state == "unavailable"
    assert output(hass, entry, "home").state == "unknown"
    # Reacquire before reunion. The first isolated point is not accepted.
    await gps(hass, c, 1)
    c.clock.advance(30)
    await gps(hass, c, 1)
    await gps(hass, c, 0)
    c.clock.advance(60)
    await gps(hass, c, 0)
    await gps(hass, c, 1)
    c.clock.advance(59)
    c._tick(None)
    assert c.data.mode == "dynamic"
    c.clock.advance(1)
    await gps(hass, c, 0)
    await gps(hass, c, 1)
    assert c.data.mode == "priority" and c.data.primary == "test_0"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_T24_event_battery_does_not_refresh(hass):
    cfg = configuration()
    cfg["devices"][0]["sources"][0].pop("timestamp_attribute")
    entry, c = await setup(hass, config=cfg)
    await gps(hass, c)
    observed = c.engine.paths["test_0"].latest.observed
    for i in range(1, 32):
        c.clock.advance(10)
        original = hass.states.get("device_tracker.test_0")
        hass.states.async_set(
            original.entity_id, original.state, {**original.attributes, "battery": i}
        )
        await hass.async_block_till_done()
    assert c.engine.paths["test_0"].latest.observed == observed
    assert output(hass, entry, "gps").state == "unavailable"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_T31_local_only_home(hass):
    cfg = configuration()
    cfg["devices"][0]["sources"].append(
        {"id": "wifi", "entity_id": "binary_sensor.wifi", "kind": "wifi"}
    )
    entry, c = await setup(hass, config=cfg)
    await gps(hass, c)
    hass.states.async_set("binary_sensor.wifi", "on")
    await hass.async_block_till_done()
    c.clock.advance(5)
    c._tick(None)
    c.clock.advance(1000)
    c._tick(None)
    await hass.async_block_till_done()
    assert output(hass, entry, "presence").state == "home"
    assert output(hass, entry, "gps").state == "unavailable"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_T12_local_reunion_then_preferred_gps_recovers(hass):
    cfg = configuration(count=2)
    for i in range(2):
        cfg["devices"][i]["sources"].append(
            {"id": f"wifi_{i}", "kind": "wifi", "entity_id": f"binary_sensor.wifi_{i}"}
        )
        hass.states.async_set(f"binary_sensor.wifi_{i}", "on" if i == 0 else "off")
    entry, c = await setup(hass, config=cfg)
    for step in range(14):
        if step:
            c.clock.advance(30)
        await gps(hass, c, 0, 0)
        await gps(hass, c, 1, max(0, step - 2) * 120)
    assert c.data.primary == "test_1" and c.data.mode == "dynamic"
    c.clock.advance(310)
    c._tick(None)
    hass.states.async_set("binary_sensor.wifi_1", "on")
    await hass.async_block_till_done()
    for _ in range(5):
        await gps(hass, c, 1, 0)
        c.clock.advance(30)
    c._tick(None)
    assert c.data.mode == "priority" and c.data.primary == "test_1"
    await gps(hass, c, 0, 0)
    c.clock.advance(30)
    await gps(hass, c, 0, 0)
    assert output(hass, entry, "primary_gps").state == "device_tracker.test_0"
    assert output(hass, entry, "home").state == "on"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_T38_malformed_record_isolated_and_removable(hass):
    cfg = configuration()
    cfg["devices"].append({"id": [], "name": {}, "priority": "bad", "sources": None})
    entry, c = await setup(hass, config=cfg)
    await gps(hass, c, x=2000)
    assert output(hass, entry, "gps").state != "unavailable"
    assert c.diagnostics()["configuration_errors"] == ["invalid_device"]
    mgr = hass.config_entries.options
    r = await mgr.async_init(entry.entry_id)
    r = await mgr.async_configure(r["flow_id"], {"action": "delete"})
    options = next(iter(r["data_schema"].schema.values())).config["options"]
    bad_id = next(o["value"] for o in options if o["value"] != "test_0")
    r = await mgr.async_configure(r["flow_id"], {"device": bad_id})
    await mgr.async_configure(r["flow_id"], {"action": "save"})
    await hass.async_block_till_done()
    assert len(entry.options["presence_fusion"]["devices"]) == 1
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_T38_runtime_feedback_rejected_without_saved_registry_id(hass):
    a, ca = await setup(hass)
    await gps(hass, ca, x=2000)
    cfg = configuration("other")
    cfg["devices"][0]["sources"][0]["entity_id"] = output(hass, a, "gps").entity_id
    b, cb = await setup(hass, prefix="other", config=cfg)
    assert cb.source_status["other_gps_0"] == "feedback"
    assert cb.data.gps is None
    assert await hass.config_entries.async_unload(b.entry_id)
    assert await hass.config_entries.async_unload(a.entry_id)


async def test_T38_config_options_wizard(hass):
    from homeassistant.config_entries import SOURCE_USER
    from homeassistant.data_entry_flow import FlowResultType

    hass.states.async_set("device_tracker.wizard", "unavailable")
    mgr = hass.config_entries.flow
    r = await mgr.async_init(
        "virtual_layer",
        context={"source": SOURCE_USER},
        data={"group_name": "Wizard", "presence_fusion": True},
    )
    assert r["step_id"] == "fusion"

    async def step(data):
        nonlocal r
        r = await mgr.async_configure(r["flow_id"], data)
        return r

    await step({"action": "add"})
    await step({"name": "Phone", "priority": 1, "candidate": True})
    await step({"action": "add"})
    await step({"kind": "gps", "entity_id": "person.someone"})
    assert r["errors"]["entity_id"] == "fusion_source"
    await step({"kind": "gps", "entity_id": "device_tracker.wizard"})
    await step({"action": "done"})
    await step({"action": "settings"})
    await step({"gps_stale_after_s": 20})
    assert r["errors"]["gps_stale_after_s"] == "fusion_number"
    await step({"gps_stale_after_s": 300})
    await step({"action": "save"})
    assert r["type"] is FlowResultType.CREATE_ENTRY
    entry = r["result"]
    await hass.async_block_till_done()
    uid = entry.options["presence_fusion"]["devices"][0]["id"]
    mgr = hass.config_entries.options
    r = await mgr.async_init(entry.entry_id)
    await step({"action": "edit"})
    await step({"device": uid})
    await step({"name": "Renamed", "priority": 2, "candidate": True})
    await step({"action": "done"})
    await step({"action": "save"})
    await hass.async_block_till_done()
    assert entry.options["presence_fusion"]["devices"][0]["id"] == uid
    assert entry.options["presence_fusion"]["devices"][0]["name"] == "Renamed"
    assert (
        len(er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)) == 9
    )
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_T39_registry_rename_disable_remove(hass):
    registry = er.async_get(hass)
    record = registry.async_get_or_create(
        "device_tracker", "synthetic", "synthetic", suggested_object_id="test_0"
    )
    cfg = configuration()
    cfg["devices"][0]["sources"][0]["registry_id"] = record.id
    entry, c = await setup(hass, config=cfg)
    await gps(hass, c)
    registry.async_update_entity(
        record.entity_id, new_entity_id="device_tracker.renamed"
    )
    await hass.async_block_till_done()
    assert c.sources[0][1]["entity_id"] == "device_tracker.renamed"
    c.clock.advance(30)
    hass.states.async_set(
        "device_tracker.renamed",
        "not_home",
        {
            "latitude": 0,
            "longitude": 0.001,
            "gps_accuracy": 10,
            "measured": c.clock.utc(),
        },
    )
    await hass.async_block_till_done()
    assert c.engine.paths["test_0"].latest.longitude == 0.001
    registry.async_update_entity(
        "device_tracker.renamed", disabled_by=er.RegistryEntryDisabler.USER
    )
    await hass.async_block_till_done()
    assert c.data.gps is None
    assert c.source_status["test_gps_0"] == "disabled"
    registry.async_remove("device_tracker.renamed")
    await hass.async_block_till_done()
    assert c.source_status["test_gps_0"] == "removed"
    assert entry.options["presence_fusion"]["devices"][0]["sources"]
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_T39_rename_with_identical_fix_updates_selected_source(hass):
    registry = er.async_get(hass)
    row = registry.async_get_or_create(
        "device_tracker", "synthetic", "rename", suggested_object_id="test_0"
    )
    cfg = configuration()
    cfg["devices"][0]["sources"][0]["registry_id"] = row.id
    entry, c = await setup(hass, config=cfg)
    await gps(hass, c, x=2000)
    original = c.data
    old = hass.states.get(row.entity_id)
    registry.async_update_entity(row.entity_id, new_entity_id="device_tracker.renamed")
    hass.states.async_set("device_tracker.renamed", old.state, dict(old.attributes))
    await hass.async_block_till_done()
    assert c.data.gps == original.gps and c.data.health == "ok"
    assert c.engine.paths["test_0"].latest.observed == old.attributes["measured"]
    assert output(hass, entry, "primary_gps").state == "device_tracker.renamed"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_T42_T43_store_restore_original_expiry(hass):
    entry, c = await setup(hass)
    await gps(hass, c, x=2000)
    await hass.services.async_call(
        "presence_fusion",
        "set_primary",
        {
            "config_entry_id": entry.entry_id,
            "tracked_device_id": "test_0",
            "duration": 60,
        },
        blocking=True,
    )
    until = c.engine.override_until
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    restored = entry.runtime_data.coordinator
    assert restored.engine.override_until == until
    assert restored.engine.primary == "test_0"
    assert not restored.engine.paths["test_0"].segments
    assert restored.engine.candidate is None
    restored.clock = FakeClock(until)
    restored._tick(None)
    assert restored.data.mode == "dynamic"
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize("timezone", ["Asia/Seoul", "America/New_York", "UTC"])
async def test_T45_zone_updates(hass, timezone):
    await hass.config.async_set_time_zone(timezone)
    entry, c = await setup(hass)
    await gps(hass, c, x=300)
    previous = c.data.distance
    hass.states.async_set(
        "zone.home", "0", {"latitude": 0, "longitude": 0.002, "radius": 100}
    )
    await hass.async_block_till_done()
    assert c.data.distance < previous
    hass.states.async_set(
        "zone.home", "0", {"latitude": 0, "longitude": 0.002, "radius": 2000}
    )
    await hass.async_block_till_done()
    assert c.data.health == "ambiguous"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_T47_private_diagnostics(hass, caplog):
    import json

    from custom_components.virtual_layer.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    cfg = configuration()
    cfg["devices"][0]["name"] = "PRIVATE_REAL_NAME"
    cfg["devices"][0]["sources"].append(
        {
            "id": "room",
            "kind": "room",
            "entity_id": "sensor.private_room",
            "room_mapping": {"SECRET_ROOM": "PRIVATE_ROOM"},
        }
    )
    cfg["private_nested"] = {
        "ssid": "SECRET_SSID",
        "mac": "AA:BB:CC:DD:EE:FF",
        "irk": "SECRET_IRK",
    }
    entry, c = await setup(hass, config=cfg)
    await gps(hass, c, x=321)
    hass.states.async_set("sensor.private_room", "SECRET_ROOM")
    await hass.async_block_till_done()
    diagnostic = json.dumps(await async_get_config_entry_diagnostics(hass, entry))
    for secret in [
        "PRIVATE_REAL_NAME",
        "SECRET_ROOM",
        "PRIVATE_ROOM",
        "SECRET_SSID",
        "AA:BB:CC:DD:EE:FF",
        "SECRET_IRK",
        "sensor.private_room",
        "device_tracker.test_0",
        "latitude",
        "longitude",
    ]:
        assert secret not in diagnostic
    own_logs = "\n".join(
        record.message for record in caplog.records if "presence_fusion" in record.name
    )
    assert "SECRET" not in own_logs and "PRIVATE" not in own_logs
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize(
    "accuracy,assumed", [(None, True), (0, True), (-1, True), (10, False), (200, False)]
)
def test_T22_accuracy_adapter(accuracy, assumed):
    state = State(
        "device_tracker.test",
        "not_home",
        {"latitude": 0, "longitude": 0, "gps_accuracy": accuracy},
    )
    p, error = gps_observation(
        {"entity_id": state.entity_id}, state, None, 0, Settings()
    )
    assert error is None and p.assumed is assumed
    assert p.accuracy == (100 if assumed else accuracy)


@pytest.mark.parametrize(
    "fmt,value,expected",
    [
        ("iso", "2026-01-01T09:00:00+09:00", 1767225600),
        ("seconds", "1234", 1234),
        ("milliseconds", 1234000, 1234),
    ],
)
def test_T24_timestamp_formats(fmt, value, expected):
    from custom_components.virtual_layer.presence_fusion.adapters import timestamp

    assert timestamp(value, fmt) == expected
    for invalid in ("2026-01-01T00:00:00", "invalid"):
        with pytest.raises(ValueError):
            timestamp(invalid, "iso")


def test_T29_T30_local_unknown_and_ttl():
    from custom_components.virtual_layer.presence_fusion.adapters import (
        local_observation,
    )

    config = {
        "kind": "wifi",
        "entity_id": "sensor.ssid",
        "positive": ["SyntheticSSID"],
        "negative": ["disconnected"],
    }
    assert (
        local_observation(config, State("sensor.ssid", "elsewhere"), 100)[0]
        == "unknown"
    )
    assert (
        local_observation(config, State("sensor.ssid", "unavailable"), 100)[0]
        == "unknown"
    )
    assert local_observation(config, None, 100)[2] == "removed"
    state = State("sensor.ssid", "SyntheticSSID", {"last_seen": 10})
    assert local_observation(config, state, 1000)[0] == "present"
    config.update(
        freshness="timestamp_ttl",
        timestamp_attribute="last_seen",
        timestamp_format="seconds",
        ttl=30,
    )
    assert local_observation(config, state, 39)[0] == "present"
    assert local_observation(config, state, 40)[0] == "unknown"
    assert (
        local_observation(config, State("sensor.ssid", "SyntheticSSID"), 40)[2]
        == "invalid_timestamp"
    )


async def test_T30_registered_maintenance_timer(hass):
    from datetime import timedelta

    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    entry, c = await setup(hass)
    await gps(hass, c)
    c.clock.advance(60)
    await gps(hass, c)
    c.clock.advance(300)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=10))
    await hass.async_block_till_done()
    assert output(hass, entry, "gps").state == "unavailable"
    c.clock.advance(300)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=20))
    await hass.async_block_till_done()
    assert output(hass, entry, "presence").state == "unknown"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_T41_setup_exception_cleanup(hass):
    from unittest.mock import patch

    from homeassistant.config_entries import ConfigEntries

    from custom_components.virtual_layer.presence_fusion.lifecycle import (
        setup as fusion_setup,
    )

    entry = MockConfigEntry(
        domain="virtual_layer",
        title="Failure",
        data={"presence_fusion": True},
        options={"presence_fusion": configuration()},
    )
    entry.add_to_hass(hass)
    # Fault injection at the HA platform boundary, not the decision engine.
    with (
        patch.object(
            ConfigEntries,
            "async_forward_entry_setups",
            side_effect=RuntimeError("synthetic setup failure"),
        ),
        pytest.raises(RuntimeError, match="synthetic setup failure"),
    ):
        await fusion_setup(hass, entry)
    c = entry.runtime_data.coordinator
    assert c.stopped and not c.unsubs and c.state_unsub is None


async def test_T48_unchanged_snapshot_suppresses_writes(hass):
    from homeassistant.const import EVENT_STATE_CHANGED

    entry, c = await setup(hass)
    await gps(hass, c)
    c.clock.advance(60)
    await gps(hass, c)
    changes = []
    unsubscribe = hass.bus.async_listen(
        EVENT_STATE_CHANGED, lambda event: changes.append(event)
    )
    for _ in range(1000):
        c.publish()
    await c.async_refresh()
    await hass.async_block_till_done()
    unsubscribe()
    assert not changes
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_T38_priority_source_edit_delete_and_metadata(hass):
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import device_registry as dr

    entry, c = await setup(hass, count=2)
    for i in range(2):
        await gps(hass, c, i)
    manager = hass.config_entries.options
    result = await manager.async_init(entry.entry_id)

    async def step(data):
        nonlocal result
        result = await manager.async_configure(result["flow_id"], data)
        return result

    await step({"action": "edit"})
    await step({"device": "test_1"})
    await step({"name": "Watch", "priority": 0, "candidate": True})
    assert result["errors"]["priority"] == "fusion_priority"
    await step({"name": "Watch", "priority": 1, "candidate": True})
    await step({"action": "edit"})
    await step({"source": "test_gps_1"})
    await step({"entity_id": "device_tracker.test_0", "kind": "gps"})
    assert result["errors"]["entity_id"] == "fusion_duplicate"
    await step({"entity_id": "device_tracker.test_1", "kind": "gps"})
    await step({"action": "done"})
    await step({"action": "delete"})
    await step({"device": "test_1"})
    area = ar.async_get(hass).async_create("Synthetic area")
    parent = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("test", "parent")}
    )
    # Select an actual external parent (a different entry owns it).
    other = MockConfigEntry(domain="test", title="Parent")
    other.add_to_hass(hass)
    dr.async_get(hass).async_update_device(
        parent.id,
        add_config_entry_id=other.entry_id,
        remove_config_entry_id=entry.entry_id,
    )
    own = next(
        r.device_id
        for r in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    )
    await step({"action": "metadata"})
    await step(
        {
            "device_id": "stable_person_device",
            "manufacturer": "Synthetic",
            "model": "Fusion",
            "area_id": area.id,
            "parent_device": parent.id,
        }
    )
    await step({"action": "save"})
    await hass.async_block_till_done()
    assert len(entry.options["presence_fusion"]["devices"]) == 1
    device = dr.async_get(hass).async_get_device(
        identifiers={("virtual_layer", "stable_person_device")}
    )
    assert (
        device.id == own
        and device.area_id == area.id
        and device.via_device_id == parent.id
    )
    assert device.manufacturer == "Synthetic"
    assert (
        len(
            {
                r.device_id
                for r in er.async_entries_for_config_entry(
                    er.async_get(hass), entry.entry_id
                )
            }
        )
        == 1
    )
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_T43_corrupt_metadata_and_removal(hass):
    entry, c = await setup(hass, count=2)
    await gps(hass, c, 0)
    await gps(hass, c, 1, x=2000)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await c.store.async_save(
        {"version": 999, "primary": "test_0", "active": ["test_0"]}
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    restored = entry.runtime_data.coordinator
    assert restored.engine.primary is None and restored.data.health == "ambiguous"
    assert await hass.config_entries.async_remove(entry.entry_id)
    assert await restored.store.async_load() is None


async def test_T38_reject_own_output_and_recover_bad_row(hass):
    entry, c = await setup(hass)
    await gps(hass, c)
    tracker = output(hass, entry, "gps")
    manager = hass.config_entries.options
    r = await manager.async_init(entry.entry_id)
    for data in [
        {"action": "edit"},
        {"device": "test_0"},
        {"name": "Phone", "priority": 0, "candidate": True},
        {"action": "edit"},
        {"source": "test_gps_0"},
        {"entity_id": tracker.entity_id, "kind": "gps"},
    ]:
        r = await manager.async_configure(r["flow_id"], data)
    assert r["errors"]["entity_id"] == "fusion_source"
    manager.async_abort(r["flow_id"])
    assert await hass.config_entries.async_unload(entry.entry_id)
    # Bad rows are still deletable via UI, even if runtime validation rejects
    # their source or configuration. Existing valid mappings remain intact.
    config = configuration()
    config["devices"].append({"id": "broken", "name": "Broken", "sources": "invalid"})
    hass.config_entries.async_update_entry(entry, options={"presence_fusion": config})
    r = await manager.async_init(entry.entry_id)
    for data in [{"action": "delete"}, {"device": "broken"}, {"action": "save"}]:
        r = await manager.async_configure(r["flow_id"], data)
    await hass.async_block_till_done()
    assert len(entry.options["presence_fusion"]["devices"]) == 1
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(entry.entry_id)
