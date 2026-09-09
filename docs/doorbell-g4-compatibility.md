# Doorbell / Aqara G4 compatibility audit

Checked 2026-09-09 against this checkout and installed Home Assistant 2026.2.3.
Virtual Layer can compose a doorbell Device, but does **not** reproduce every
Aqara G4 feature. There is no independent `doorbell` entity domain or G4 preset.

| G4 capability | Virtual Layer coverage |
| --- | --- |
| Live video / snapshot | A virtual `camera` aliases a source camera or uses configured media. Resolution, viewing angle and infrared night vision depend on hardware/source. |
| Ring notification | Use an `event` with `class: doorbell` and a changing timestamp state, or an off/on `binary_sensor` pulse. HomeKit camera accessory linking is separate. |
| Motion, tamper, battery | Represent exposed readings with separate binary sensors and sensors on the same Device. No detection or battery hardware is created. |
| Chime, custom ringtone, siren, prerecorded message | Virtual buttons, sirens and media players can call existing HA actions; a working physical endpoint/service is required. No G4 ringtone upload API is implemented. |
| Two-way intercom / voice changing | Not implemented. Forwarding a video stream does not add microphone talkback. |
| Local face recognition, loitering detection | No built-in analytics. Existing source results can be represented and used by HA automations. |
| HomeKit Secure Video / iCloud history | Not supported by HA HomeKit Bridge. Aliasing a G4 camera does not preserve its native HKSV pairing capabilities. |
| Cloud clips, microSD / continuous recording | No storage, retention or recording playback backend. External camera/recorder services are needed; a recording status attribute is not a recorder. |
| Privacy masking | No pixel masking engine. Configure this on the source camera/recorder. |
| Apple Home, Alexa, Google display integration | Requires separate HA integrations/bridges and compatible source media; Virtual Layer provides no G4 cloud integration. |
| Battery/wired power, Wi-Fi security, weather resistance, physical chime | Hardware/firmware capabilities, outside a virtual entity's scope. |

## UI-only composition

Create a Device in Virtual Layer and add camera, ring, motion and battery entities
to it. Select an existing camera as the video source. Do not assume G4 publishes
an RTSP URL: availability of media and controls depends on its source integration.

For a source `event` ring entity, preserve its changing timestamp in the value
template (`{{ states('event.actual_ring') }}`), set device class to `doorbell`,
and mirror event metadata if needed. Repeated presses often have the same
`event_type`; templating only the constant string `pressed` cannot encode each
press. The current generic event implementation timestamps **type changes**,
so it is not a complete native EventEntity producer. A timestamp source/value
template or explicit `virtual_layer.set_state` timestamp updates are required.
For a binary sensor, reset it to off between rings. A button entity is an action
control, not automatically an incoming doorbell notification.

Expose the camera using a separate HomeKit accessory-mode entry. Installed HA
can discover a same-Device doorbell-class event and link it to the camera;
`linked_doorbell_sensor` also supports binary sensors. This is bridge configuration,
not a camera native template or a Virtual Layer YAML setting. No bridge is
created or paired by Virtual Layer.

## Verification boundaries

`tests/integration/test_doorbell.py` checks both timestamp events (same repeated
press type) and binary pulses, calls HA's actual HomeKit doorbell state handler
with mocked HAP characteristics, checks a camera stream URL, shared Device
registration, reload and removal. Existing camera tests cover media behavior.
These tests do not verify physical G4 connectivity, Apple pairing, HomePod sound,
notifications, live RTP delivery, or G4 feature parity. No runtime code was
changed by this audit.

## Sources

- [Aqara G4 official features and specifications](https://us.aqara.com/products/video-doorbell-g4)
- [Home Assistant HomeKit Bridge: camera, linked sensors and HKSV limitation](https://www.home-assistant.io/integrations/homekit/)
- Local implementation: `camera.py`, `generic.py`, `event.py`, `binary_sensor.py`,
  and installed HA `components/homekit/doorbell.py` and `components/homekit/__init__.py`.
