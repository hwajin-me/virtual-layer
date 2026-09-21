"""Frigate MQTT recording state must be captured before changing it."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
import voluptuous as vol

from custom_components.virtual_layer.patrol_recording import PatrolRecording
from custom_components.virtual_layer.camera import validate_domain_options


@pytest.mark.parametrize("previous", ["ON", "OFF"])
async def test_mqtt_recording_apply_and_restore(hass, previous):
    handler = None
    unsubscribe = Mock()
    async def subscribe(hass, topic, callback):
        nonlocal handler
        handler = callback
        callback(SimpleNamespace(payload=previous))
        return unsubscribe
    async def publish(hass, topic, payload, **kwargs):
        handler(SimpleNamespace(payload=payload))
    mqtt = SimpleNamespace(async_subscribe=AsyncMock(side_effect=subscribe), async_publish=AsyncMock(side_effect=publish))
    with patch("custom_components.virtual_layer.patrol_recording.async_import_module", AsyncMock(return_value=mqtt)):
        controller = PatrolRecording(hass, "frigate/front/recordings")
        await controller.apply("off")
        assert controller.previous == previous
        unsubscribe.assert_called_once()
        await controller.restore()
        mqtt.async_publish.assert_awaited_with(hass, "frigate/front/recordings/set", previous, qos=1, retain=False)
        assert controller.previous is None


@pytest.mark.parametrize("topic", ["frigate/+/recordings", "frigate/#/recordings", "frigate/front"])
def test_invalid_recording_topics(topic):
    with pytest.raises(vol.Invalid):
        validate_domain_options({"frigate_mqtt_recordings_topic": topic})


def test_recording_transport_must_be_unambiguous():
    with pytest.raises(vol.Invalid):
        validate_domain_options({"frigate_mqtt_recordings_topic": "frigate/front/recordings", "frigate_recording_switch": "switch.recordings"})


def test_edit_preserves_patrol_and_recording_controls():
    from custom_components.virtual_layer.config_flow import _entity_form_defaults, _build_entity_config
    configured = {
        "platform": "camera", "name": "Front", "entity_id": "camera.front",
        "onvif_patrol_target": "camera.ptz", "onvif_patrol_interval": 120,
        "frigate_mqtt_recordings_topic": "frigate/front/recordings",
        "frigate_recording_during_patrol": "off",
    }
    defaults = _entity_form_defaults("Front", configured)
    for field in ("onvif_patrol_target", "onvif_patrol_interval", "frigate_mqtt_recordings_topic", "frigate_recording_during_patrol"):
        assert defaults[field] == configured[field]
    _, rebuilt = _build_entity_config(defaults)
    assert rebuilt["frigate_mqtt_recordings_topic"] == configured["frigate_mqtt_recordings_topic"]
    assert rebuilt["onvif_patrol_interval"] == 120
