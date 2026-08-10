# Docker Home Assistant Integration Environment

This directory contains a Docker Compose setup for checking Virtual Layer in a
real Home Assistant Container.

The compose file follows the Home Assistant Linux Docker Compose installation
shape: official `ghcr.io/home-assistant/home-assistant:stable` image,
`/config` volume, local timezone mount, published `8123` port, and automatic
restart.
See the official Home Assistant Linux Docker Compose guide:
https://www.home-assistant.io/installation/linux#docker-compose

## Start Home Assistant

```sh
docker compose -f tests/docker/docker-compose.yml pull
docker compose -f tests/docker/docker-compose.yml up -d
```

Then open `http://localhost:8123` or `http://<host>:8123` and add
`Virtual Layer` from `Settings > Devices & services > Add integration`.

The official Linux guide targets Docker Engine on Linux. This test compose file
uses bridge networking with an explicit `8123:8123` port mapping so it also
works cleanly with Docker Desktop.

## Check Logs

```sh
docker compose -f tests/docker/docker-compose.yml logs -f homeassistant
```

## Automated All-Domain Test

Run an isolated official Home Assistant stable container that creates every
Virtual Layer domain on one device and exercises native services, common
attribute and availability controls, and persistent entity reloads. The current
matrix creates 66 entities across all 46 supported domains, including safety,
appliance, electrical, utility, and HVAC variants:

```sh
tests/docker/run_all_domains.sh
```

The script fails when an entity is missing, a service does not produce the
expected state or attributes, persistence changes values during reload, a
registry entry is detached from the shared device, Virtual Layer logs an error,
or Home Assistant reports a Virtual Layer deprecation warning. Each run starts
with fresh generated Home Assistant storage, does not require onboarding, and
does not reuse the interactive container configuration.

## Stop

```sh
docker compose -f tests/docker/docker-compose.yml down
```

The Home Assistant config intentionally does not include `virtual_layer` YAML.
Virtual Layer entities must be created and edited from the Home Assistant UI.

If Bluetooth/D-Bus behavior needs to be tested on a Linux host, add this volume
to the `homeassistant` service:

```yaml
- /run/dbus:/run/dbus:ro
```
