"""Sensor device-class default units shared during configuration and runtime."""

from homeassistant.components.sensor import DEVICE_CLASS_UNITS, SensorDeviceClass
from homeassistant.const import (
    LIGHT_LUX,
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS,
    UnitOfApparentPower,
    UnitOfConductivity,
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

try:
    from homeassistant.const import UnitOfDensity, UnitOfRatio
except ImportError:  # Home Assistant before 2026.8
    from homeassistant.const import (
        CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        CONCENTRATION_PARTS_PER_MILLION,
    )
else:
    CONCENTRATION_MICROGRAMS_PER_CUBIC_METER = (
        UnitOfDensity.MICROGRAMS_PER_CUBIC_METER
    )
    CONCENTRATION_PARTS_PER_MILLION = UnitOfRatio.PARTS_PER_MILLION


UNITS_OF_MEASUREMENT = {
    SensorDeviceClass.APPARENT_POWER: UnitOfApparentPower.VOLT_AMPERE,
    SensorDeviceClass.BATTERY: PERCENTAGE,
    SensorDeviceClass.CO: CONCENTRATION_PARTS_PER_MILLION,
    SensorDeviceClass.CO2: CONCENTRATION_PARTS_PER_MILLION,
    SensorDeviceClass.CONDUCTIVITY: UnitOfConductivity.MICROSIEMENS_PER_CM,
    SensorDeviceClass.HUMIDITY: PERCENTAGE,
    SensorDeviceClass.ILLUMINANCE: LIGHT_LUX,
    SensorDeviceClass.NITROGEN_DIOXIDE: CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
    SensorDeviceClass.NITROGEN_MONOXIDE: CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
    SensorDeviceClass.NITROUS_OXIDE: CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
    SensorDeviceClass.OZONE: CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
    SensorDeviceClass.PM1: CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
    SensorDeviceClass.PM10: CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
    SensorDeviceClass.PM25: CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
    "pm4": CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
    SensorDeviceClass.SIGNAL_STRENGTH: SIGNAL_STRENGTH_DECIBELS,
    SensorDeviceClass.SULPHUR_DIOXIDE: CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
    SensorDeviceClass.TEMPERATURE: UnitOfTemperature.CELSIUS,
    SensorDeviceClass.PRESSURE: UnitOfPressure.HPA,
    SensorDeviceClass.POWER: UnitOfPower.KILO_WATT,
    SensorDeviceClass.CURRENT: UnitOfElectricCurrent.AMPERE,
    SensorDeviceClass.ENERGY: UnitOfEnergy.KILO_WATT_HOUR,
    SensorDeviceClass.FREQUENCY: UnitOfFrequency.HERTZ,
    SensorDeviceClass.POWER_FACTOR: PERCENTAGE,
    SensorDeviceClass.REACTIVE_POWER: UnitOfReactivePower.VOLT_AMPERE_REACTIVE,
    SensorDeviceClass.VOLATILE_ORGANIC_COMPOUNDS: CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
    SensorDeviceClass.VOLTAGE: UnitOfElectricPotential.VOLT,
    SensorDeviceClass.GAS: UnitOfVolume.CUBIC_METERS,
    SensorDeviceClass.MOISTURE: PERCENTAGE,
    SensorDeviceClass.VOLUME: UnitOfVolume.CUBIC_METERS,
    SensorDeviceClass.VOLUME_FLOW_RATE: UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR,
    SensorDeviceClass.VOLUME_STORAGE: UnitOfVolume.CUBIC_METERS,
    SensorDeviceClass.WATER: UnitOfVolume.LITERS,
}


def is_compatible_device_class_unit(device_class, unit) -> bool:
    """Return whether a known sensor device class accepts ``unit``.

    Unknown (including integration-specific) classes deliberately remain
    permissive; Home Assistant only defines a unit contract for its own sensor
    device classes.  Empty units are also valid for classes which support a
    unitless reading.
    """
    if device_class in (None, "") or unit in (None, ""):
        return True
    try:
        device_class = SensorDeviceClass(str(device_class))
    except ValueError:
        return True
    allowed_units = DEVICE_CLASS_UNITS.get(device_class)
    return allowed_units is None or unit in allowed_units
