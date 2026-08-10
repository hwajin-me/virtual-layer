"""
This component provides support for a virtual sensor.

"""

import logging
from collections.abc import Callable
from datetime import date

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components.sensor import (
    DOMAIN as PLATFORM_DOMAIN,
)
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_ENTITY_ID,
    ATTR_UNIT_OF_MEASUREMENT,
    CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
    CONCENTRATION_PARTS_PER_MILLION,
    CONF_ICON,
    CONF_UNIT_OF_MEASUREMENT,
    LIGHT_LUX,
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS,
    UnitOfApparentPower,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfPressure,
    UnitOfReactivePower,
    UnitOfTemperature,
    UnitOfVolume,
    UnitOfVolumeFlowRate,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.config_validation import PLATFORM_SCHEMA
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.util import dt as dt_util

from . import (
    _assert_managed_virtual_entity,
    _async_verify_target_entity_control,
    get_entity_configs,
    get_entity_from_domain,
)
from .const import *
from .const import generic_entity_options
from .entity import VirtualEntity, virtual_schema

_LOGGER = logging.getLogger(__name__)

DEPENDENCIES = [COMPONENT_DOMAIN]

DEFAULT_SENSOR_VALUE = "0"
CONF_STATE_CLASS = "state_class"


def _as_state_class(value) -> SensorStateClass | None:
    if value in (None, ""):
        return None
    if isinstance(value, SensorStateClass):
        return value
    return SensorStateClass(str(value).lower())


def _as_device_class(value):
    if value in (None, ""):
        return None
    try:
        return SensorDeviceClass(value)
    except ValueError:
        return value

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(virtual_schema(DEFAULT_SENSOR_VALUE, {
    vol.Optional(CONF_CLASS): cv.string,
    vol.Optional(CONF_DIAGNOSTIC_SOURCE_ENTITY): cv.entity_id,
    vol.Optional(CONF_ICON): cv.string,
    vol.Optional(CONF_STATE_CLASS): _as_state_class,
    vol.Optional(CONF_UNIT_OF_MEASUREMENT, default=""): cv.string,
}))
SENSOR_SCHEMA = vol.Schema(virtual_schema(DEFAULT_SENSOR_VALUE, {
    vol.Optional(CONF_CLASS): cv.string,
    vol.Optional(CONF_DIAGNOSTIC_SOURCE_ENTITY): cv.entity_id,
    vol.Optional(CONF_ICON): cv.string,
    vol.Optional(CONF_STATE_CLASS): _as_state_class,
    vol.Optional(CONF_UNIT_OF_MEASUREMENT, default=""): cv.string,
}), extra=vol.ALLOW_EXTRA)

SERVICE_SET = "set"
SERVICE_SCHEMA = vol.Schema({
    vol.Required(ATTR_ENTITY_ID): cv.comp_entity_ids,
    vol.Required(ATTR_VALUE): cv.string,
})

UNITS_OF_MEASUREMENT = {
    SensorDeviceClass.APPARENT_POWER: UnitOfApparentPower.VOLT_AMPERE,  # apparent power (VA)
    SensorDeviceClass.BATTERY: PERCENTAGE,  # % of battery that is left
    SensorDeviceClass.CO: CONCENTRATION_PARTS_PER_MILLION,  # ppm of CO concentration
    SensorDeviceClass.CO2: CONCENTRATION_PARTS_PER_MILLION,  # ppm of CO2 concentration
    SensorDeviceClass.HUMIDITY: PERCENTAGE,  # % of humidity in the air
    SensorDeviceClass.ILLUMINANCE: LIGHT_LUX,  # current light level (lx/lm)
    SensorDeviceClass.NITROGEN_DIOXIDE: CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,  # µg/m³ of nitrogen dioxide
    SensorDeviceClass.NITROGEN_MONOXIDE: CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,  # µg/m³ of nitrogen monoxide
    SensorDeviceClass.NITROUS_OXIDE: CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,  # µg/m³ of nitrogen oxide
    SensorDeviceClass.OZONE: CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,  # µg/m³ of ozone
    SensorDeviceClass.PM1: CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,  # µg/m³ of PM1
    SensorDeviceClass.PM10: CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,  # µg/m³ of PM10
    SensorDeviceClass.PM25: CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,  # µg/m³ of PM2.5
    SensorDeviceClass.SIGNAL_STRENGTH: SIGNAL_STRENGTH_DECIBELS,  # signal strength (dB/dBm)
    SensorDeviceClass.SULPHUR_DIOXIDE: CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,  # µg/m³ of sulphur dioxide
    SensorDeviceClass.TEMPERATURE: UnitOfTemperature.CELSIUS,
    SensorDeviceClass.PRESSURE: UnitOfPressure.HPA,  # pressure (hPa/mbar)
    SensorDeviceClass.POWER: UnitOfPower.KILO_WATT,  # power (W/kW)
    SensorDeviceClass.CURRENT: UnitOfElectricCurrent.AMPERE,  # current (A)
    SensorDeviceClass.ENERGY: UnitOfEnergy.KILO_WATT_HOUR,  # energy (Wh/kWh/MWh)
    SensorDeviceClass.FREQUENCY: UnitOfFrequency.HERTZ,
    SensorDeviceClass.POWER_FACTOR: PERCENTAGE,  # power factor (no unit, min: -1.0, max: 1.0)
    SensorDeviceClass.REACTIVE_POWER: UnitOfReactivePower.VOLT_AMPERE_REACTIVE,  # reactive power (var)
    SensorDeviceClass.VOLATILE_ORGANIC_COMPOUNDS: CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,  # µg/m³ of vocs
    SensorDeviceClass.VOLTAGE: UnitOfElectricPotential.VOLT,  # voltage (V)
    SensorDeviceClass.GAS: UnitOfVolume.CUBIC_METERS,  # gas (m³)
    SensorDeviceClass.MOISTURE: PERCENTAGE,  # moisture percentage
    SensorDeviceClass.VOLUME: UnitOfVolume.CUBIC_METERS,  # volume (m³)
    SensorDeviceClass.VOLUME_FLOW_RATE: UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR,
    SensorDeviceClass.VOLUME_STORAGE: UnitOfVolume.CUBIC_METERS,
    SensorDeviceClass.WATER: UnitOfVolume.LITERS,  # water consumption (L)
}


def setup_services(hass: HomeAssistant) -> None:

    async def async_virtual_service(call):
        """Call virtual service handler."""
        await _async_verify_target_entity_control(hass, call)
        _LOGGER.debug(f"{call.service} service called")
        await async_virtual_set_service(hass, call)

    # Build up services...
    if PLATFORM_DOMAIN not in hass.data[COMPONENT_SERVICES]:
        _LOGGER.debug("installing handlers")
        hass.data[COMPONENT_SERVICES][PLATFORM_DOMAIN] = "installed"
        hass.services.async_register(
            COMPONENT_DOMAIN, SERVICE_SET, async_virtual_service, schema=SERVICE_SCHEMA,
        )


async def async_setup_platform(
        hass: HomeAssistant,
        config: ConfigType,
        async_add_entities: AddEntitiesCallback,
        _discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Ignore platform setup; Virtual Layer entities are config-entry only."""
    _LOGGER.debug("ignoring platform setup")


async def async_setup_entry(
        hass: HomeAssistant,
        entry: ConfigEntry,
        async_add_entities: Callable[[list], None],
) -> None:
    _LOGGER.debug("setting up the entries...")

    entities = []
    for entity in get_entity_configs(hass, entry.data[ATTR_GROUP_NAME], PLATFORM_DOMAIN):
        entity = SENSOR_SCHEMA(entity)
        if entity.get(CONF_DIAGNOSTIC_SOURCE_ENTITY):
            entities.append(VirtualDiagnosticSensor(entity, False))
        else:
            entities.append(VirtualSensor(entity, False))
    async_add_entities(entities)
    setup_services(hass)


class VirtualSensor(VirtualEntity, SensorEntity):
    """An implementation of a Virtual Sensor."""

    def __init__(self, config, old_style: bool):
        """Initialize an Virtual Sensor."""
        super().__init__(config, PLATFORM_DOMAIN, old_style)

        legacy_attributes = config.get(CONF_ATTRIBUTES, {})
        self._attr_device_class = _as_device_class(
            config.get(CONF_CLASS, legacy_attributes.get(ATTR_DEVICE_CLASS))
        )
        self._attr_state_class = _as_state_class(
            config.get(CONF_STATE_CLASS, legacy_attributes.get(CONF_STATE_CLASS))
        )
        self._attr_icon = config.get(CONF_ICON)
        self._domain_options = generic_entity_options(config)
        self._attr_options = config.get("options")

        # Set unit of measurement
        self._attr_native_unit_of_measurement = (
            config.get(CONF_UNIT_OF_MEASUREMENT)
            or legacy_attributes.get(ATTR_UNIT_OF_MEASUREMENT)
            or None
        )
        if (
            not self._attr_native_unit_of_measurement
            and self._attr_device_class in UNITS_OF_MEASUREMENT
        ):
            self._attr_native_unit_of_measurement = UNITS_OF_MEASUREMENT[
                self._attr_device_class
            ]
        # Keep this alias for old callers while SensorEntity uses the native unit.
        self._attr_unit_of_measurement = self._attr_native_unit_of_measurement

        _LOGGER.debug(f"VirtualSensor: {self.name} created")

    def _create_state(self, config):
        super()._create_state(config)

        self._attr_native_value = self._safe_native_value(
            config.get(CONF_INITIAL_VALUE),
        )
        self._attr_state = self._attr_native_value

    def _restore_state(self, state, config):
        super()._restore_state(state, config)

        self._attr_native_value = self._safe_native_value(
            state.state,
            config.get(CONF_INITIAL_VALUE),
        )
        self._attr_state = self._attr_native_value

    def _safe_native_value(self, value, fallback=None):
        """Recover from stale date/timestamp states without unloading the entity."""
        try:
            return self._coerce_native_value(value)
        except (OverflowError, TypeError, ValueError):
            if fallback is not None and fallback != value:
                try:
                    return self._coerce_native_value(fallback)
                except (OverflowError, TypeError, ValueError):
                    pass
            return None

    def _coerce_native_value(self, value):
        if value is None:
            return None
        if str(value).lower() in {"unknown", "unavailable", "none"}:
            if (
                self._attr_device_class is not None
                or self._attr_state_class is not None
                or self._attr_native_unit_of_measurement is not None
                or self._attr_options is not None
            ):
                return None
            return value
        if self._attr_device_class is SensorDeviceClass.TIMESTAMP:
            if hasattr(value, "tzinfo"):
                parsed = value
            else:
                parsed = dt_util.parse_datetime(str(value))
            if parsed is None:
                raise ValueError(f"Invalid timestamp sensor value: {value}")
            return dt_util.as_utc(parsed)
        if self._attr_device_class is SensorDeviceClass.DATE:
            if isinstance(value, date):
                return value
            try:
                return date.fromisoformat(str(value))
            except ValueError as err:
                raise ValueError(f"Invalid date sensor value: {value}") from err
        return value

    def _update_attributes(self):
        super()._update_attributes()
        self._attr_extra_state_attributes.update({
            name: value for name, value in (
                (ATTR_DEVICE_CLASS, self._attr_device_class),
                (ATTR_UNIT_OF_MEASUREMENT, self._attr_native_unit_of_measurement),
            ) if value is not None
        })
        self._attr_extra_state_attributes.update(self._domain_options)

    def set(self, value) -> None:
        _LOGGER.debug(f"set {self.name} to {value}")
        self._attr_native_value = self._coerce_native_value(value)
        self._attr_state = self._attr_native_value
        self.async_schedule_update_ha_state()

    def set_state(self, value) -> None:
        self.set(value)

    def _apply_native_template_value(self, name: str, value) -> bool:
        aliases = {
            "unit": "native_unit_of_measurement",
            "unit_of_measurement": "native_unit_of_measurement",
            "value": "state",
            "native_value": "state",
        }
        name = aliases.get(name, name)
        if name == "device_class":
            value = _as_device_class(value)
        elif name == "state_class":
            try:
                value = _as_state_class(value)
            except ValueError as err:
                raise ValueError(f"Invalid sensor state class: {value}") from err
        elif name == "options":
            if not isinstance(value, (list, tuple, set)):
                raise ValueError("options must render a list")
            value = [str(item).strip() for item in value if str(item).strip()]
            if len(set(value)) != len(value):
                raise ValueError("options contains duplicate values")
        elif name == "native_unit_of_measurement":
            value = None if value is None or value == "" else str(value)
        return super()._apply_native_template_value(name, value)

    def _native_templates_applied(self) -> None:
        self._attr_unit_of_measurement = self._attr_native_unit_of_measurement
        self._attr_native_value = self._safe_native_value(self._attr_native_value)
        self._attr_state = self._attr_native_value
        if self._attr_options is not None and str(self._attr_native_value) not in self._attr_options:
            self._attr_native_value = None
            self._attr_state = None


class VirtualDiagnosticSensor(VirtualSensor):
    """Expose a source entity's current state and attributes for diagnostics."""

    def __init__(self, config, old_style: bool):
        self._diagnostic_source_entity = config[CONF_DIAGNOSTIC_SOURCE_ENTITY]
        super().__init__(config, old_style)

    def _update_attributes(self):
        super()._update_attributes()
        source_state = self.hass.states.get(self._diagnostic_source_entity)
        if source_state is None:
            self._attr_extra_state_attributes.update({
                "source_state": None,
                "source_attributes": {},
                "source_last_updated": None,
                "source_last_changed": None,
            })
            return
        self._attr_extra_state_attributes.update({
            "source_state": source_state.state,
            "source_attributes": dict(source_state.attributes),
            "source_last_updated": source_state.last_updated.isoformat(),
            "source_last_changed": source_state.last_changed.isoformat(),
        })


async def async_virtual_set_service(hass, call):
    for entity_id in call.data[ATTR_ENTITY_ID]:
        value = call.data[ATTR_VALUE]
        _LOGGER.debug(f"setting {entity_id} to {value})")
        _assert_managed_virtual_entity(hass, entity_id)
        get_entity_from_domain(hass, PLATFORM_DOMAIN, entity_id).set(value)
