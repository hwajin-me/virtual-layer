import voluptuous as vol
from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    CodeFormat,
)
from homeassistant.helpers.config_validation import PLATFORM_SCHEMA

from .const import COMPONENT_DOMAIN
from .generic import (
    GENERIC_SCHEMA,
    GenericVirtualEntity,
    async_setup_generic_entry,
    async_setup_generic_platform,
)

PLATFORM_DOMAIN = "alarm_control_panel"
DEPENDENCIES = [COMPONENT_DOMAIN]
PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(GENERIC_SCHEMA)
ENTITY_SCHEMA = vol.Schema(GENERIC_SCHEMA, extra=vol.ALLOW_EXTRA)


class VirtualAlarmControlPanel(GenericVirtualEntity, AlarmControlPanelEntity):
    """Virtual alarm panel using Home Assistant's native service interface."""

    _COMMAND_FEATURES = {
        "alarm_arm_home": AlarmControlPanelEntityFeature.ARM_HOME,
        "alarm_arm_away": AlarmControlPanelEntityFeature.ARM_AWAY,
        "alarm_arm_night": AlarmControlPanelEntityFeature.ARM_NIGHT,
        "alarm_arm_vacation": AlarmControlPanelEntityFeature.ARM_VACATION,
        "alarm_arm_custom_bypass": AlarmControlPanelEntityFeature.ARM_CUSTOM_BYPASS,
        "alarm_trigger": AlarmControlPanelEntityFeature.TRIGGER,
    }

    def __init__(self, config, old_style: bool):
        super().__init__(config, PLATFORM_DOMAIN, old_style)
        self._attr_changed_by = config.get("changed_by")
        self._attr_code_arm_required = config.get("code_arm_required", True)
        code_format = config.get("code_format")
        try:
            self._attr_code_format = CodeFormat(code_format) if code_format else None
        except ValueError:
            self._attr_code_format = None
        self._refresh_supported_features()

    def _refresh_supported_features(self) -> None:
        """Expose only the arm and trigger commands configured for this entity."""
        try:
            features = AlarmControlPanelEntityFeature(
                int(self._domain_options.get("supported_features", 0))
            )
        except (TypeError, ValueError, OverflowError):
            features = AlarmControlPanelEntityFeature(0)
        for command, feature in self._COMMAND_FEATURES.items():
            if command in self._command_actions:
                features |= feature
        self._attr_supported_features = features

    async def _async_set_alarm_state(self, state: str) -> None:
        """Publish the local state after configurable command actions complete."""
        self._attr_state = state
        self.async_write_ha_state()

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        """Disarm the virtual panel after running its configured action."""
        await self._async_set_alarm_state("disarmed")

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        """Arm the virtual panel in home mode."""
        await self._async_set_alarm_state("armed_home")

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        """Arm the virtual panel in away mode."""
        await self._async_set_alarm_state("armed_away")

    async def async_alarm_arm_night(self, code: str | None = None) -> None:
        """Arm the virtual panel in night mode."""
        await self._async_set_alarm_state("armed_night")

    async def async_alarm_arm_vacation(self, code: str | None = None) -> None:
        """Arm the virtual panel in vacation mode."""
        await self._async_set_alarm_state("armed_vacation")

    async def async_alarm_arm_custom_bypass(self, code: str | None = None) -> None:
        """Arm the virtual panel in custom bypass mode."""
        await self._async_set_alarm_state("armed_custom_bypass")

    async def async_alarm_trigger(self, code: str | None = None) -> None:
        """Trigger the virtual alarm."""
        await self._async_set_alarm_state("triggered")

    def _native_templates_applied(self) -> None:
        """Recompute advertised features after native templates update them."""
        self._refresh_supported_features()


async def async_setup_platform(hass, config, async_add_entities, _discovery_info=None):
    await async_setup_generic_platform(hass, config, async_add_entities, PLATFORM_DOMAIN)


async def async_setup_entry(hass, entry, async_add_entities):
    await async_setup_generic_entry(
        hass,
        entry,
        async_add_entities,
        PLATFORM_DOMAIN,
        ENTITY_SCHEMA,
        VirtualAlarmControlPanel,
    )
