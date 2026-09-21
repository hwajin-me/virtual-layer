"""
This component provides support for a virtual sensor.

"""

import logging
import math
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal, DecimalException

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components.sensor import (
    DEVICE_CLASS_STATE_CLASSES,
    DEVICE_CLASS_UNITS,
    NON_NUMERIC_DEVICE_CLASSES,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.components.sensor import (
    DOMAIN as PLATFORM_DOMAIN,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_ENTITY_ID,
    ATTR_UNIT_OF_MEASUREMENT,
    CONF_ICON,
    CONF_UNIT_OF_MEASUREMENT,
    EVENT_CORE_CONFIG_UPDATE,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.config_validation import PLATFORM_SCHEMA
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event, async_track_point_in_time
from homeassistant.core import callback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.util import dt as dt_util

from . import (
    _assert_managed_virtual_entities,
    _async_verify_target_entity_control,
    get_entity_configs,
    get_entity_from_domain,
)
from .const import *
from .const import generic_entity_options
from .entity import VirtualEntity, virtual_schema
from .source_usage import SOURCE_USAGE, SourceUsageSensor
from .air_quality_options import default_air_quality_icon, normalize_unit
from . import unit_history
from . import meter
from .sensor_units import UNITS_OF_MEASUREMENT

_LOGGER = logging.getLogger(__name__)

DEPENDENCIES = [COMPONENT_DOMAIN]

DEFAULT_SENSOR_VALUE = "0"
CONF_STATE_CLASS = "state_class"


def validate_domain_options(config):
    """Isolate invalid stored meter records and reject invalid UI saves."""
    if config.get("utility_meter_enabled"):
        settings = meter.options(config)
        sources = config.get(CONF_SOURCE_ENTITIES, [])
        if not isinstance(sources, list) or len(sources) != 1 or not isinstance(sources[0], str) or not sources[0].startswith("sensor."):
            raise vol.Invalid("A utility meter requires exactly one sensor source")
        cv.entity_id(sources[0])
        initial = meter.number(config.get(CONF_INITIAL_VALUE, 0))
        if initial < 0 and not settings["net_consumption"]:
            raise vol.Invalid("Negative initial total requires net consumption")
        if settings["tariff_entity"]:
            cv.entity_id(settings["tariff_entity"])
            if not settings["tariff"]:
                raise vol.Invalid("Tariff name is required")
        if config.get("utility_meter_correction_id"):
            correction = meter.number(config.get("utility_meter_correction"))
            if correction < 0 and not settings["net_consumption"]:
                raise vol.Invalid("Negative correction requires net consumption")


def _as_state_class(value) -> SensorStateClass | None:
    if value in (None, ""):
        return None
    if isinstance(value, SensorStateClass):
        return value
    return SensorStateClass(str(value).lower())


def _as_device_class(value):
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError("Sensor device class must be a string")
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
SERVICE_ADJUST_UTILITY_METER = "adjust_utility_meter"
SERVICE_CALIBRATE_UTILITY_METER = "calibrate_utility_meter"
SERVICE_RESET_UTILITY_METER = "reset_utility_meter"
SERVICE_SCHEMA = vol.Schema({
    vol.Required(ATTR_ENTITY_ID): cv.comp_entity_ids,
    vol.Required(ATTR_VALUE): cv.string,
})
UTILITY_METER_ADJUST_SCHEMA = vol.Schema({
    vol.Required(ATTR_ENTITY_ID): cv.comp_entity_ids,
    vol.Required("amount"): meter.number,
})
UTILITY_METER_CALIBRATE_SCHEMA = vol.Schema({
    vol.Required(ATTR_ENTITY_ID): cv.comp_entity_ids,
    vol.Required("value"): meter.number,
})
UTILITY_METER_RESET_SCHEMA = vol.Schema({
    vol.Required(ATTR_ENTITY_ID): cv.comp_entity_ids,
})

def setup_services(hass: HomeAssistant) -> None:

    async def meter_targets(call):
        await _async_verify_target_entity_control(hass, call)
        _assert_managed_virtual_entities(hass, call.data[ATTR_ENTITY_ID])
        targets = []
        for entity_id in dict.fromkeys(call.data[ATTR_ENTITY_ID]):
            entity = get_entity_from_domain(hass, PLATFORM_DOMAIN, entity_id)
            if not isinstance(entity, VirtualSensor) or not entity.is_utility_meter:
                raise vol.Invalid(f"{entity_id} is not a virtual utility meter")
            if call.service == SERVICE_ADJUST_UTILITY_METER:
                amount = call.data["amount"]
                if amount < 0:
                    raise vol.Invalid("Adjustment must be non-negative")
                meter.number(Decimal(str(entity.native_value or 0)) + amount)
            elif call.service == SERVICE_CALIBRATE_UTILITY_METER:
                if call.data["value"] < 0 and not entity._meter_settings["net_consumption"]:
                    raise vol.Invalid("Negative total requires net consumption")
            targets.append(entity)
        return targets

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
        async def async_adjust_utility_meter(call):
            """Add unmeasured consumption to a virtual utility meter."""
            for entity in await meter_targets(call):
                entity.adjust_utility_meter(call.data["amount"])

        async def async_calibrate_utility_meter(call):
            """Set the current-period total to an audited reading."""
            for entity in await meter_targets(call):
                entity.calibrate_utility_meter(call.data["value"])

        async def async_reset_utility_meter(call):
            """Manually close the current period and begin a new one."""
            for entity in await meter_targets(call):
                entity._async_utility_meter_reset(None)

        hass.services.async_register(
            COMPONENT_DOMAIN,
            SERVICE_ADJUST_UTILITY_METER,
            async_adjust_utility_meter,
            schema=UTILITY_METER_ADJUST_SCHEMA,
        )
        hass.services.async_register(
            COMPONENT_DOMAIN,
            SERVICE_CALIBRATE_UTILITY_METER,
            async_calibrate_utility_meter,
            schema=UTILITY_METER_CALIBRATE_SCHEMA,
        )
        hass.services.async_register(
            COMPONENT_DOMAIN,
            SERVICE_RESET_UTILITY_METER,
            async_reset_utility_meter,
            schema=UTILITY_METER_RESET_SCHEMA,
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
        if SOURCE_USAGE in entity and entity.get(ATTR_UNIQUE_ID, "").startswith(
            f"{entry.entry_id}{DIAGNOSTIC_UNIQUE_ID_MARKER}source_usage:"
        ):
            entities.append(SourceUsageSensor(entity))
            continue
        entity = SENSOR_SCHEMA(entity)
        if entity.get(CONF_DIAGNOSTIC_SOURCE_ENTITY):
            entities.append(VirtualDiagnosticSensor(entity, False))
        else:
            entities.append(VirtualSensor(entity, False))
    async_add_entities(entities)
    setup_services(hass)


class VirtualSensor(VirtualEntity, SensorEntity):
    """An implementation of a Virtual Sensor."""

    @property
    def icon(self):
        """Give existing pollutant sensors the same fallback as new UI entries."""
        return super().icon or default_air_quality_icon(
            PLATFORM_DOMAIN, self.device_class, self.name, self.entity_id,
        ) or None

    def __init__(self, config, old_style: bool):
        """Initialize an Virtual Sensor."""
        super().__init__(config, PLATFORM_DOMAIN, old_style)

        legacy_attributes = config.get(CONF_ATTRIBUTES, {})
        try:
            self._attr_device_class = _as_device_class(
                config.get(CONF_CLASS, legacy_attributes.get(ATTR_DEVICE_CLASS))
            )
        except (TypeError, ValueError):
            self._attr_device_class = None
        try:
            self._attr_state_class = _as_state_class(
                config.get(CONF_STATE_CLASS, legacy_attributes.get(CONF_STATE_CLASS))
            )
        except (TypeError, ValueError):
            self._attr_state_class = None
        self._attr_icon = config.get(CONF_ICON)
        self._domain_options = generic_entity_options(config)
        self._attr_options = config.get("options")
        self._utility_meter_enabled = bool(config.get("utility_meter_enabled", False))
        self._utility_meter_cycle = config.get("utility_meter_cycle", "monthly")
        self._utility_meter_last_source: Decimal | None = None
        self._utility_meter_last_period = Decimal("0")
        self._utility_meter_last_reset: datetime | None = None
        self._meter_settings = meter.options(config) if self._utility_meter_enabled else None
        self._meter_cancel_reset = None
        self._meter_next_reset = None
        self._meter_restored_source = None
        self._meter_correction_id = None
        self._meter_schedule_profile = None
        self._meter_schedule_since = None
        if self._utility_meter_enabled:
            self._attr_state_class = SensorStateClass.TOTAL_INCREASING

        # Set unit of measurement
        self._attr_native_unit_of_measurement = (
            config.get(CONF_UNIT_OF_MEASUREMENT)
            or legacy_attributes.get(ATTR_UNIT_OF_MEASUREMENT)
            or None
        )
        if not isinstance(self._attr_native_unit_of_measurement, (str, type(None))):
            self._attr_native_unit_of_measurement = None
        if self._attr_device_class == SensorDeviceClass.CONDUCTIVITY:
            self._attr_native_unit_of_measurement = normalize_unit(self._attr_native_unit_of_measurement) or None
        self._last_valid_unit = normalize_unit(self._attr_native_unit_of_measurement) or None
        if (
            not self._attr_native_unit_of_measurement
            and self._attr_device_class in UNITS_OF_MEASUREMENT
        ):
            self._attr_native_unit_of_measurement = UNITS_OF_MEASUREMENT[
                self._attr_device_class
            ]
        # These numeric sensors retain their default on the first missing
        # source-unit render, before any valid source metadata has arrived.
        if self._attr_device_class in {
            SensorDeviceClass.PM1, SensorDeviceClass.PM25, SensorDeviceClass.PM10, "pm4",
            SensorDeviceClass.CONDUCTIVITY,
        }:
            self._last_valid_unit = normalize_unit(self._attr_native_unit_of_measurement) or None
        # Keep this alias for old callers while SensorEntity uses the native unit.
        self._attr_unit_of_measurement = self._attr_native_unit_of_measurement

        _LOGGER.debug(f"VirtualSensor: {self.name} created")

    @property
    def is_utility_meter(self) -> bool:
        """Whether this sensor accumulates source readings like Utility Meter."""
        return self._utility_meter_enabled

    async def async_added_to_hass(self):
        """Recover a missing unit before publishing the first sensor state."""
        if self._attr_device_class not in NON_NUMERIC_DEVICE_CLASSES:
            _, unit = unit_history.configured_unit(self.hass, self._config)
            if not unit:
                metadata = await unit_history.statistics_snapshot(self.hass, self.entity_id)
                if metadata and metadata.get("source") == "recorder":
                    recorded = normalize_unit(metadata.get("unit_of_measurement"))
                    if recorded:
                        self._attr_native_unit_of_measurement = recorded
                        self._attr_unit_of_measurement = recorded
                        self._last_valid_unit = recorded
        await super().async_added_to_hass()
        if self._utility_meter_enabled:
            if self._utility_meter_last_reset is None:
                self._utility_meter_last_reset = dt_util.utcnow()
            profile = meter.schedule_profile(self._meter_settings)
            if self._meter_schedule_profile is not None and self._meter_schedule_profile != profile:
                # Editing a schedule must not invent historical resets.
                self._meter_schedule_since = dt_util.utcnow()
            self._meter_schedule_profile = profile
            boundary = meter.next_reset(self._meter_settings, self._meter_schedule_since or self._utility_meter_last_reset)
            if boundary is not None and dt_util.as_utc(boundary) <= dt_util.utcnow():
                self._async_utility_meter_reset(None)
                self._utility_meter_last_source = None
            self._initialize_utility_meter_source()
            correction_id = self._config.get("utility_meter_correction_id")
            if correction_id and correction_id != self._meter_correction_id:
                self.calibrate_utility_meter(meter.number(self._config["utility_meter_correction"]))
                self._meter_correction_id = correction_id
            self._setup_utility_meter_reset()
            @callback
            def timezone_changed(_event):
                profile = meter.schedule_profile(self._meter_settings)
                if profile != self._meter_schedule_profile:
                    self._meter_schedule_profile = profile
                    self._meter_schedule_since = dt_util.utcnow()
                self._setup_utility_meter_reset()
                self._update_attributes()
                self._schedule_state_update()
            self._refresh_remove_listeners.append(self.hass.bus.async_listen(EVENT_CORE_CONFIG_UPDATE, timezone_changed))
            self._update_attributes()

    def _setup_templates(self):
        """Utility meters track their source deltas instead of rendering its state."""
        if not self._utility_meter_enabled:
            return super()._setup_templates()

    def _apply_templates(self):
        if not self._utility_meter_enabled:
            return super()._apply_templates()
        # Accumulation owns state, availability and statistics metadata.
        self._attr_state_class = SensorStateClass.TOTAL if self._meter_settings["net_consumption"] else SensorStateClass.TOTAL_INCREASING
        self._update_attributes()

    def _initialize_utility_meter_source(self) -> None:
        sources = [source for source in self._source_entities if isinstance(source, str)]
        if len(sources) != 1:
            self._attr_available = False
            self._update_attributes()
            self._schedule_state_update()
            return
        source_id = sources[0]
        if self._meter_restored_source != source_id:
            self._utility_meter_last_source = None
        source = self.hass.states.get(source_id)
        self._attr_available = self._meter_settings["always_available"]
        if source is not None:
            source_value = self._utility_decimal(source.state)
            if (
                source_value is not None
                and self._utility_meter_last_source is not None
                and (source_value >= self._utility_meter_last_source or self._meter_settings["net_consumption"])
                and not self._meter_settings["delta_values"]
                and not self._meter_settings["tariff_entity"]
                and meter.collection_started(self._meter_settings, dt_util.utcnow())
            ):
                self._attr_native_value = (
                    Decimal(str(self._attr_native_value or 0))
                    + source_value - self._utility_meter_last_source
                )
                self._attr_state = self._attr_native_value
            if source_value is not None or self._meter_settings["periodically_resetting"]:
                self._utility_meter_last_source = source_value
            self._attr_available = source_value is not None or self._meter_settings["always_available"]
            source_class = source.attributes.get(ATTR_DEVICE_CLASS)
            if isinstance(source_class, str):
                self._attr_device_class = _as_device_class(source_class)
            if self._attr_native_unit_of_measurement is None:
                self._attr_native_unit_of_measurement = source.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
                self._attr_unit_of_measurement = self._attr_native_unit_of_measurement

        @callback
        def _source_changed(event):
            new_state = event.data.get("new_state")
            self._async_utility_meter_reading(new_state)

        self._refresh_remove_listeners.append(
            async_track_state_change_event(self.hass, [source_id], _source_changed)
        )
        tariff_id = self._meter_settings["tariff_entity"]
        if tariff_id:
            @callback
            def tariff_changed(_event):
                source = self.hass.states.get(source_id)
                self._utility_meter_last_source = self._utility_decimal(source.state) if source else None
            self._refresh_remove_listeners.append(async_track_state_change_event(self.hass, [tariff_id], tariff_changed))
        self._update_attributes()
        self._schedule_state_update()

    @staticmethod
    def _utility_decimal(value) -> Decimal | None:
        try:
            result = meter.number(value)
        except (DecimalException, TypeError, ValueError, vol.Invalid):
            return None
        return result

    @callback
    def _async_utility_meter_reading(self, state) -> None:
        value = self._utility_decimal(state.state) if state else None
        if value is None:
            self._attr_available = self._meter_settings["always_available"]
            if self._meter_settings["periodically_resetting"]:
                self._utility_meter_last_source = None
            self._update_attributes()
            self._schedule_state_update()
            return
        previous = self._utility_meter_last_source
        self._utility_meter_last_source = value
        self._attr_available = True
        unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
        if isinstance(unit, str) and unit:
            self._attr_native_unit_of_measurement = unit
            self._attr_unit_of_measurement = unit
        tariff_id = self._meter_settings["tariff_entity"]
        tariff = self.hass.states.get(tariff_id) if tariff_id else None
        collecting = not tariff_id or (tariff and tariff.state == self._meter_settings["tariff"])
        collecting = collecting and meter.collection_started(self._meter_settings, dt_util.utcnow())
        delta = value if self._meter_settings["delta_values"] else value - previous if previous is not None else Decimal(0)
        if collecting and (delta >= 0 or self._meter_settings["net_consumption"]):
            self._attr_native_value = meter.number(Decimal(str(self._attr_native_value or 0)) + delta)
            self._attr_state = self._attr_native_value
        self._update_attributes()
        self._schedule_state_update()

    def _setup_utility_meter_reset(self) -> None:
        if self._meter_cancel_reset:
            self._meter_cancel_reset()
            self._meter_cancel_reset = None
        self._meter_next_reset = meter.next_reset(self._meter_settings, dt_util.utcnow())
        if self._meter_next_reset is not None:
            @callback
            def reset(now):
                self._meter_cancel_reset = None
                self._async_utility_meter_reset(now)
                self._setup_utility_meter_reset()
                self._update_attributes()
                self._schedule_state_update()
            self._meter_cancel_reset = async_track_point_in_time(self.hass, reset, self._meter_next_reset)

    async def async_will_remove_from_hass(self):
        if self._meter_cancel_reset:
            self._meter_cancel_reset()
            self._meter_cancel_reset = None
        await super().async_will_remove_from_hass()

    @callback
    def _async_utility_meter_reset(self, _now) -> None:
        self._utility_meter_last_period = Decimal(str(self._attr_native_value or 0))
        self._attr_native_value = Decimal("0")
        self._attr_state = self._attr_native_value
        self._utility_meter_last_reset = dt_util.utcnow()
        self._meter_schedule_since = self._utility_meter_last_reset
        self._update_attributes()
        self._schedule_state_update()

    def adjust_utility_meter(self, amount: Decimal) -> None:
        """Add a positive correction for consumption missed by the source meter."""
        amount = meter.number(amount)
        if amount < 0:
            raise ValueError("Utility meter adjustment must be a finite positive number")
        self._attr_native_value = meter.number(Decimal(str(self._attr_native_value or 0)) + amount)
        self._attr_state = self._attr_native_value
        self._update_attributes()
        self._schedule_state_update()

    def calibrate_utility_meter(self, value: Decimal) -> None:
        """Set an audited current-period total without changing the source."""
        value = meter.number(value)
        if value < 0 and not self._meter_settings["net_consumption"]:
            raise ValueError("Utility meter calibration must be a finite non-negative number")
        self._attr_native_value = value
        self._attr_state = value
        # An audited total already includes all usage up to this point. Do not
        # add a pre-correction outage gap when the physical source recovers.
        if self.hass and self._source_entities:
            source = self.hass.states.get(self._source_entities[0])
            self._utility_meter_last_source = self._utility_decimal(source.state) if source else None
        self._update_attributes()
        self._schedule_state_update()

    def _create_state(self, config):
        super()._create_state(config)

        self._attr_native_value = self._safe_native_value(
            config.get(CONF_INITIAL_VALUE),
        )
        self._attr_state = self._attr_native_value

    def _restore_state(self, state, config):
        super()._restore_state(state, config)

        self._attr_native_value = self._safe_native_value(
            self._restored_state_value(state, config),
            config.get(CONF_INITIAL_VALUE),
        )
        self._attr_state = self._attr_native_value
        if self._utility_meter_enabled:
            self._meter_restored_source = state.attributes.get("meter_source")
            self._meter_correction_id = state.attributes.get("meter_correction_id")
            total = self._utility_decimal(state.attributes.get("meter_total"))
            if total is not None:
                self._attr_native_value = total
                self._attr_state = total
            self._utility_meter_last_source = self._utility_decimal(
                state.attributes.get("last_valid_state")
            )
            self._utility_meter_last_period = (
                self._utility_decimal(state.attributes.get("last_period"))
                or Decimal("0")
            )
            raw_reset = state.attributes.get("last_reset")
            self._utility_meter_last_reset = dt_util.parse_datetime(raw_reset) if isinstance(raw_reset, str) else None
            profile = state.attributes.get("meter_schedule")
            self._meter_schedule_profile = dict(profile) if isinstance(profile, dict) else None
            raw_since = state.attributes.get("meter_schedule_since")
            self._meter_schedule_since = dt_util.parse_datetime(raw_since) if isinstance(raw_since, str) else None

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
        if self._attr_device_class in {SensorDeviceClass.PH, SensorDeviceClass.CONDUCTIVITY}:
            if self._attr_native_unit_of_measurement not in DEVICE_CLASS_UNITS[self._attr_device_class]:
                raise ValueError("Water measurement has an incompatible unit")
            # Conductivity cannot be negative. Do not clamp pH to 0..14:
            # concentrated solutions can legitimately fall outside that range.
            if self._attr_device_class == SensorDeviceClass.CONDUCTIVITY and float(value) < 0:
                raise ValueError("Conductivity must be non-negative")
        if isinstance(value, bool):
            raise ValueError("Sensor value must not be a boolean")
        if str(value).lower() in {"unknown", "unavailable", "none"}:
            if (
                self._attr_device_class is not None
                or self._attr_state_class is not None
                or self._attr_native_unit_of_measurement is not None
                or self._attr_options is not None
            ):
                return None
            return value
        if self._attr_device_class in (SensorDeviceClass.TIMESTAMP, "uptime"):
            if hasattr(value, "tzinfo"):
                parsed = value
            else:
                parsed = dt_util.parse_datetime(str(value))
            if parsed is None:
                raise ValueError(f"Invalid timestamp sensor value: {value}")
            return dt_util.as_utc(parsed)
        if self._attr_device_class is SensorDeviceClass.DATE:
            if isinstance(value, datetime):
                return value.date()
            if isinstance(value, date):
                return value
            try:
                return date.fromisoformat(str(value))
            except ValueError as err:
                raise ValueError(f"Invalid date sensor value: {value}") from err
        if self._attr_device_class not in NON_NUMERIC_DEVICE_CLASSES and (
                self._attr_state_class is not None
                or self._attr_native_unit_of_measurement is not None
                or (isinstance(self._attr_device_class, SensorDeviceClass)
                    and self._attr_device_class not in NON_NUMERIC_DEVICE_CLASSES)):
            if not math.isfinite(float(value)):
                raise ValueError("Numeric sensor value must be finite")
        return value

    def _update_attributes(self):
        super()._update_attributes()
        for name, value in (
            (ATTR_DEVICE_CLASS, self._attr_device_class),
            (ATTR_UNIT_OF_MEASUREMENT, self._attr_native_unit_of_measurement),
            (CONF_STATE_CLASS, self._attr_state_class),
        ):
            if value is None:
                self._attr_extra_state_attributes.pop(name, None)
        self._attr_extra_state_attributes.update({
            name: value for name, value in (
                (ATTR_DEVICE_CLASS, self._attr_device_class),
                (ATTR_UNIT_OF_MEASUREMENT, self._attr_native_unit_of_measurement),
            ) if value is not None
        })
        self._attr_extra_state_attributes.update(self._domain_options)
        if self._utility_meter_enabled:
            self._attr_extra_state_attributes.update({
                "meter_source": self._source_entities[0] if self._source_entities else None,
                "meter_correction_id": self._meter_correction_id,
                "meter_schedule": self._meter_schedule_profile,
                "meter_schedule_since": self._meter_schedule_since.isoformat() if self._meter_schedule_since else None,
                "meter_total": str(self._attr_native_value or 0),
                "next_reset": self._meter_next_reset.isoformat() if self._meter_next_reset else None,
                "cost": str(meter.cost(self._meter_settings, self._attr_native_value or 0)),
                "currency": self._meter_settings["currency"].upper(),
                "utility_meter": True,
                "utility_meter_cycle": self._utility_meter_cycle,
                "last_period": str(self._utility_meter_last_period),
                "last_reset": self._utility_meter_last_reset.isoformat() if self._utility_meter_last_reset else None,
                "last_valid_state": str(self._utility_meter_last_source) if self._utility_meter_last_source is not None else None,
            })

    def set(self, value) -> None:
        _LOGGER.debug("Setting state for %s", self.entity_id)
        if self._utility_meter_enabled:
            self.calibrate_utility_meter(meter.number(value))
            return
        self._attr_native_value = self._coerce_native_value(value)
        self._attr_state = self._attr_native_value
        self._schedule_state_update()

    def set_state(self, value) -> None:
        # Invalid template readings must clear the previous measurement.
        # Explicit service writes use set(), which rejects invalid values.
        if not self._utility_meter_enabled:
            self.set(self._safe_native_value(value))

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
            if value is None:
                pass
            elif not isinstance(value, (list, tuple, set)):
                raise ValueError("options must render a list")
            else:
                value = [str(item).strip() for item in value if str(item).strip()]
                if len(set(value)) != len(value):
                    raise ValueError("options contains duplicate values")
        elif name == "native_unit_of_measurement":
            value = normalize_unit(value)
            if not value or value.lower() in {"none", "unknown", "unavailable"}:
                # Missing source metadata must not invalidate recorded statistics.
                value = self._last_valid_unit
            else:
                self._last_valid_unit = value
        elif name == "suggested_display_precision":
            if value is None or value == "":
                value = None
            elif isinstance(value, bool):
                raise ValueError(
                    "suggested_display_precision must be a non-negative integer"
                )
            else:
                try:
                    number = float(value)
                    if not math.isfinite(number) or not number.is_integer():
                        raise ValueError("Precision must be an integer")
                    value = int(number)
                except (TypeError, ValueError, OverflowError) as err:
                    raise ValueError(
                        "suggested_display_precision must be a non-negative integer"
                    ) from err
                if value < 0:
                    raise ValueError(
                        "suggested_display_precision must be a non-negative integer"
                    )
        elif name == "suggested_unit_of_measurement":
            value = None if value is None or value == "" else str(value)
        elif name == "last_reset":
            if value is None or value == "":
                return super()._apply_native_template_value(name, None)
            if isinstance(value, datetime):
                parsed = value
            else:
                parsed = dt_util.parse_datetime(str(value))
            if parsed is None:
                raise ValueError("last_reset must be a datetime")
            value = parsed if parsed.tzinfo else dt_util.as_local(parsed)
        return super()._apply_native_template_value(name, value)

    def _native_templates_applied(self) -> None:
        if self._attr_device_class != SensorDeviceClass.ENUM:
            self._attr_options = None
        if self._attr_device_class in NON_NUMERIC_DEVICE_CLASSES:
            self._attr_native_unit_of_measurement = None
            self._attr_suggested_unit_of_measurement = None
            self._last_valid_unit = None
        elif self._attr_native_unit_of_measurement:
            self._last_valid_unit = self._attr_native_unit_of_measurement
        valid_state_classes = DEVICE_CLASS_STATE_CLASSES.get(self._attr_device_class)
        if (
            valid_state_classes is not None
            and self._attr_state_class not in valid_state_classes
        ):
            self._attr_state_class = None
        if self._attr_state_class is not SensorStateClass.TOTAL:
            self._attr_last_reset = None
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
            "source_attributes": {
                name: value
                for name, value in source_state.attributes.items()
                if name not in TRANSIENT_SOURCE_ATTRIBUTE_NAMES
            },
            "source_last_updated": source_state.last_updated.isoformat(),
            "source_last_changed": source_state.last_changed.isoformat(),
        })


async def async_virtual_set_service(hass, call):
    entity_ids = call.data[ATTR_ENTITY_ID]
    _assert_managed_virtual_entities(hass, entity_ids)
    value = call.data[ATTR_VALUE]
    for entity_id in entity_ids:
        _LOGGER.debug("Setting state for %s", entity_id)
        get_entity_from_domain(hass, PLATFORM_DOMAIN, entity_id).set(value)
