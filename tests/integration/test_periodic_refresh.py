"""Real Home Assistant timer coverage for template refreshes."""

from datetime import timedelta
from threading import get_ident

import pytest
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.virtual_layer.entity import VirtualEntity

pytestmark = pytest.mark.integration


async def test_pull_interval_renders_on_event_loop(hass, monkeypatch):
    """Periodic Jinja rendering and native state writes require the HA loop."""
    threads = []
    original = VirtualEntity._apply_templates

    def record_refresh(self):
        if self.entity_id == "sensor.periodic_template":
            threads.append(get_ident())
        return original(self)

    monkeypatch.setattr(VirtualEntity, "_apply_templates", record_refresh)
    entry = MockConfigEntry(
        domain="virtual_layer",
        data={"group_name": "Periodic Device"},
        options={"devices": {"Periodic Device": [{
            "platform": "sensor",
            "name": "Periodic Template",
            "entity_id": "sensor.periodic_template",
            "value_template": "{{ 21 + 1 }}",
            "pull_interval": 60,
            "persistent": False,
        }]}},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    threads.clear()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=2))
    await hass.async_block_till_done()
    assert threads
    assert set(threads) == {hass.loop_thread_id}
    assert hass.states.get("sensor.periodic_template").state == "22"
