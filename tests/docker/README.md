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
