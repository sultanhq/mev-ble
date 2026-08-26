"""Fan preset control for Vent-Axia Multihome."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import VentaxiaMultihomeConfigEntry
from .const import MAX_OVERRIDE_DURATION, MIN_OVERRIDE_DURATION, PRESET_NAMES
from .entity import VentaxiaMultihomeEntity
from .protocol import AirflowPreset, VentilationMode

SERVICE_SET_TIMED_OVERRIDE = "set_timed_override"
SERVICE_STOP_VENTILATION = "stop_ventilation"
ATTR_DURATION = "duration"
ATTR_PRESET = "preset"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VentaxiaMultihomeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Multihome fan entity."""

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_SET_TIMED_OVERRIDE,
        {
            vol.Required(ATTR_PRESET): vol.In(PRESET_NAMES),
            vol.Required(ATTR_DURATION): vol.All(
                vol.Coerce(int),
                vol.Range(min=MIN_OVERRIDE_DURATION, max=MAX_OVERRIDE_DURATION),
            ),
        },
        "async_set_timed_override",
    )
    platform.async_register_entity_service(
        SERVICE_STOP_VENTILATION,
        {},
        "async_stop_ventilation",
    )
    async_add_entities([MultihomeFan(entry.runtime_data, entry)])


class MultihomeFan(VentaxiaMultihomeEntity, FanEntity):
    """Ventilation fan with model-gated power and preset controls."""

    _attr_translation_key = "ventilation"
    _attr_supported_features = FanEntityFeature.PRESET_MODE
    _attr_preset_modes = list(PRESET_NAMES)

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry, "fan")
        if coordinator.device.supports_ventilation_mode_control:
            self._attr_supported_features |= (
                FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF
            )

    @property
    def is_on(self) -> bool | None:
        """Return the last telemetry-confirmed running state."""

        if (data := self.coordinator.data) is None:
            return None
        return bool(data.system.fan_speed or data.zone.fan_rpm)

    @property
    def preset_mode(self) -> str | None:
        """Return the current documented speed level as a preset."""

        if (data := self.coordinator.data) is None:
            return None
        try:
            return AirflowPreset(data.zone.fan_level).name.lower()
        except ValueError:
            return None

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set a preset for the configured default duration."""

        await self.coordinator.async_set_override(AirflowPreset[preset_mode.upper()])

    async def async_set_timed_override(self, preset: str, duration: int) -> None:
        """Set a preset for an explicit number of seconds."""

        await self.coordinator.async_set_override(
            AirflowPreset[preset.upper()], duration
        )

    async def async_turn_off(self) -> None:
        """Set the documented off mode without cancelling an override."""

        self._require_ventilation_mode_control()
        await self.coordinator.async_set_ventilation_mode(VentilationMode.OFF)

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Restore the model's normal mode or apply a requested preset."""

        if percentage is not None:
            raise HomeAssistantError(
                "Percentage control is not supported; select an airflow preset"
            )
        if preset_mode is not None:
            await self.async_set_preset_mode(preset_mode)
            return
        self._require_ventilation_mode_control()
        mode = self.coordinator.device.normal_ventilation_mode
        if mode is None:  # Guard retained for type narrowing and direct calls.
            raise HomeAssistantError("This Multihome model cannot be turned on")
        await self.coordinator.async_set_ventilation_mode(mode)

    async def async_stop_ventilation(self) -> None:
        """Send the protocol stop mode separately from normal power-off."""

        self._require_ventilation_mode_control()
        await self.coordinator.async_set_ventilation_mode(VentilationMode.STOP)

    def _require_ventilation_mode_control(self) -> None:
        """Reject direct power calls for unknown or unsupported models."""

        if not self.coordinator.device.supports_ventilation_mode_control:
            model = self.coordinator.device.device_info.model or "unknown"
            raise HomeAssistantError(
                "Power and stop controls are unavailable for reported "
                f"Multihome model {model}"
            )
