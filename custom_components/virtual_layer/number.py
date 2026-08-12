"""
This component provides support for a virtual number.

"""

import logging
import math
from collections.abc import Callable

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components.number import (
    ATTR_MAX,
    ATTR_MIN,
    ATTR_STEP,
    DEFAULT_STEP,
    NumberDeviceClass,
    NumberEntity,
    NumberMode,
)
from homeassistant.components.number import (
    DOMAIN as PLATFORM_DOMAIN,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_UNIT_OF_MEASUREMENT,
    CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
    CONCENTRATION_PARTS_PER_MILLION,
    CONF_MODE,
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

from . import get_entity_configs
from .const import *
from .entity import VirtualEntity, virtual_schema

_LOGGER = logging.getLogger(__name__)

DEPENDENCIES = [COMPONENT_DOMAIN]

DEFAULT_NUMBER_VALUE = "0"


def _as_number_mode(value) -> NumberMode:
    """Return a valid number input mode while tolerating old stored data."""
    try:
        return NumberMode(value)
    except (TypeError, ValueError):
        return NumberMode.AUTO


def validate_domain_options(config) -> None:
    """Validate number bounds and controls entered through the UI."""
    minimum = VirtualNumber._finite_bound(config.get(CONF_MIN), 0.0)
    maximum = VirtualNumber._finite_bound(config.get(CONF_MAX), 100.0)
    if minimum > maximum:
        raise vol.Invalid("min must not be greater than max")

    step = VirtualNumber._finite_bound(config.get(ATTR_STEP), DEFAULT_STEP)
    if step <= 0:
        raise vol.Invalid("step must be a positive finite number")

    if CONF_MODE in config:
        try:
            NumberMode(config[CONF_MODE])
        except (TypeError, ValueError) as err:
            raise vol.Invalid("mode must be auto, box, or slider") from err

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(virtual_schema(DEFAULT_NUMBER_VALUE, {
    vol.Optional(CONF_CLASS): cv.string,
    vol.Required(CONF_MIN): vol.Coerce(float),
    vol.Required(CONF_MAX): vol.Coerce(float),
    vol.Optional(ATTR_STEP, default=DEFAULT_STEP): vol.Coerce(float),
    vol.Optional(CONF_MODE, default=NumberMode.AUTO): _as_number_mode,
    vol.Optional(CONF_UNIT_OF_MEASUREMENT, default=""): cv.string,
}))
NUMBER_SCHEMA = vol.Schema(virtual_schema(DEFAULT_NUMBER_VALUE, {
    vol.Optional(CONF_CLASS): cv.string,
    vol.Required(CONF_MIN): vol.Coerce(float),
    vol.Required(CONF_MAX): vol.Coerce(float),
    vol.Optional(ATTR_STEP, default=DEFAULT_STEP): vol.Coerce(float),
    vol.Optional(CONF_MODE, default=NumberMode.AUTO): _as_number_mode,
    vol.Optional(CONF_UNIT_OF_MEASUREMENT, default=""): cv.string,
}))

UNITS_OF_MEASUREMENT = {
    NumberDeviceClass.APPARENT_POWER: UnitOfApparentPower.VOLT_AMPERE,  # apparent power (VA)
    NumberDeviceClass.BATTERY: PERCENTAGE,  # % of battery that is left
    NumberDeviceClass.CO: CONCENTRATION_PARTS_PER_MILLION,  # ppm of CO concentration
    NumberDeviceClass.CO2: CONCENTRATION_PARTS_PER_MILLION,  # ppm of CO2 concentration
    NumberDeviceClass.HUMIDITY: PERCENTAGE,  # % of humidity in the air
    NumberDeviceClass.ILLUMINANCE: LIGHT_LUX,  # current light level (lx/lm)
    NumberDeviceClass.NITROGEN_DIOXIDE: CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,  # µg/m³ of nitrogen dioxide
    NumberDeviceClass.NITROGEN_MONOXIDE: CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,  # µg/m³ of nitrogen monoxide
    NumberDeviceClass.NITROUS_OXIDE: CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,  # µg/m³ of nitrogen oxide
    NumberDeviceClass.OZONE: CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,  # µg/m³ of ozone
    NumberDeviceClass.PM1: CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,  # µg/m³ of PM1
    NumberDeviceClass.PM10: CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,  # µg/m³ of PM10
    NumberDeviceClass.PM25: CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,  # µg/m³ of PM2.5
    NumberDeviceClass.SIGNAL_STRENGTH: SIGNAL_STRENGTH_DECIBELS,  # signal strength (dB/dBm)
    NumberDeviceClass.SULPHUR_DIOXIDE: CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,  # µg/m³ of sulphur dioxide
    NumberDeviceClass.TEMPERATURE: UnitOfTemperature.CELSIUS,
    NumberDeviceClass.PRESSURE: UnitOfPressure.HPA,  # pressure (hPa/mbar)
    NumberDeviceClass.POWER: UnitOfPower.KILO_WATT,  # power (W/kW)
    NumberDeviceClass.CURRENT: UnitOfElectricCurrent.AMPERE,  # current (A)
    NumberDeviceClass.ENERGY: UnitOfEnergy.KILO_WATT_HOUR,  # energy (Wh/kWh/MWh)
    NumberDeviceClass.FREQUENCY: UnitOfFrequency.HERTZ,
    NumberDeviceClass.POWER_FACTOR: PERCENTAGE,  # power factor (no unit, min: -1.0, max: 1.0)
    NumberDeviceClass.REACTIVE_POWER: UnitOfReactivePower.VOLT_AMPERE_REACTIVE,  # reactive power (var)
    NumberDeviceClass.VOLATILE_ORGANIC_COMPOUNDS: CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,  # µg/m³ of vocs
    NumberDeviceClass.VOLTAGE: UnitOfElectricPotential.VOLT,  # voltage (V)
    NumberDeviceClass.GAS: UnitOfVolume.CUBIC_METERS,  # gas (m³)
    NumberDeviceClass.MOISTURE: PERCENTAGE,  # moisture percentage
    NumberDeviceClass.VOLUME: UnitOfVolume.CUBIC_METERS,  # volume (m³)
    NumberDeviceClass.VOLUME_FLOW_RATE: UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR,
    NumberDeviceClass.VOLUME_STORAGE: UnitOfVolume.CUBIC_METERS,
    NumberDeviceClass.WATER: UnitOfVolume.LITERS,  # water consumption (L)
}


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
        entity = NUMBER_SCHEMA(entity)
        entities.append(VirtualNumber(entity, False))
    async_add_entities(entities)


class VirtualNumber(VirtualEntity, NumberEntity):
    """An implementation of a Virtual Number."""

    def __init__(self, config, old_style: bool):
        """Initialize an Virtual Number."""
        super().__init__(config, PLATFORM_DOMAIN, old_style)

        self._attr_device_class = config.get(CONF_CLASS)

        self._attr_native_min_value = self._finite_bound(
            config.get(CONF_MIN),
            0.0,
        )
        self._attr_native_max_value = self._finite_bound(
            config.get(CONF_MAX),
            100.0,
        )
        if self._attr_native_min_value > self._attr_native_max_value:
            self._attr_native_min_value, self._attr_native_max_value = (
                self._attr_native_max_value,
                self._attr_native_min_value,
            )
        self._attr_native_step = self._finite_bound(
            config.get(ATTR_STEP),
            DEFAULT_STEP,
        )
        if self._attr_native_step <= 0:
            self._attr_native_step = DEFAULT_STEP
        self._attr_mode = _as_number_mode(config.get(CONF_MODE))

        # Set unit of measurement
        self._attr_unit_of_measurement = config.get(CONF_UNIT_OF_MEASUREMENT)
        if not self._attr_unit_of_measurement and self._attr_device_class in UNITS_OF_MEASUREMENT:
            self._attr_unit_of_measurement = UNITS_OF_MEASUREMENT[self._attr_device_class]
        self._attr_native_unit_of_measurement = self._attr_unit_of_measurement

        _LOGGER.debug(f"VirtualNumber: {self.name} created")

    def convert_to_native_value(self, value: float) -> float:
        return value

    @staticmethod
    def _finite_bound(value, fallback: float) -> float:
        try:
            value = float(value)
        except (TypeError, ValueError, OverflowError):
            return fallback
        return value if math.isfinite(value) else fallback

    def _create_state(self, config):
        super()._create_state(config)
        self._attr_native_value = self._normalize_value(
            config.get(CONF_INITIAL_VALUE),
            self.native_min_value,
        )

    def _restore_state(self, state, config):
        super()._restore_state(state, config)

        self._attr_native_value = self._normalize_value(
            state.state,
            config.get(CONF_INITIAL_VALUE, self.native_min_value),
        )

    def _normalize_value(self, value, fallback) -> float:
        try:
            native_value = float(value)
        except (TypeError, ValueError, OverflowError):
            try:
                native_value = float(fallback)
            except (TypeError, ValueError, OverflowError):
                native_value = self.native_min_value
        if not math.isfinite(native_value):
            native_value = self.native_min_value
        return max(
            self.native_min_value,
            min(self.native_max_value, native_value),
        )

    def _update_attributes(self):
        super()._update_attributes()
        self._attr_extra_state_attributes.update({
            name: value for name, value in (
                (ATTR_DEVICE_CLASS, self._attr_device_class),
                (ATTR_UNIT_OF_MEASUREMENT, self._attr_unit_of_measurement),
                (ATTR_MIN, self.native_min_value),
                (ATTR_MAX, self.native_max_value),
                (ATTR_STEP, self.native_step),
            ) if value is not None
        })

    async def async_set_native_value(self, value: float) -> None:
        """Set new value."""
        self.set(value)

    def set(self, value) -> None:
        _LOGGER.debug("set %s to %s", self.name, value)
        self._attr_native_value = self._normalize_value(value, self.native_value)
        self.async_schedule_update_ha_state()

    def set_state(self, value) -> None:
        self.set(value)

    def _apply_native_template_value(self, name: str, value) -> bool:
        aliases = {
            "min": "native_min_value",
            "max": "native_max_value",
            "step": "native_step",
            "value": "native_value",
            "state": "native_value",
            "unit": "native_unit_of_measurement",
            "unit_of_measurement": "native_unit_of_measurement",
        }
        name = aliases.get(name, name)
        if name in {"native_min_value", "native_max_value", "native_value"}:
            value = self._finite_bound(value, float("nan"))
            if not math.isfinite(value):
                raise ValueError(f"{name} must be a finite number")
        elif name == "native_step":
            value = self._finite_bound(value, float("nan"))
            if not math.isfinite(value) or value <= 0:
                raise ValueError("native_step must be a positive finite number")
        elif name == "mode":
            try:
                value = NumberMode(value)
            except (TypeError, ValueError) as err:
                raise ValueError("mode must be auto, box, or slider") from err
        elif name == "device_class":
            try:
                value = None if value in {None, ""} else NumberDeviceClass(value)
            except (TypeError, ValueError) as err:
                raise ValueError(f"Invalid number device class: {value}") from err
        elif name == "native_unit_of_measurement":
            value = None if value in {None, ""} else str(value)
        return super()._apply_native_template_value(name, value)

    def _native_templates_applied(self) -> None:
        if self._attr_native_min_value > self._attr_native_max_value:
            self._attr_native_min_value, self._attr_native_max_value = (
                self._attr_native_max_value,
                self._attr_native_min_value,
            )
        self._attr_native_value = self._normalize_value(
            self._attr_native_value,
            self._attr_native_min_value,
        )
        self._attr_unit_of_measurement = self._attr_native_unit_of_measurement
