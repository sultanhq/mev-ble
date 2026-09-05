"""Fan preset control for Vent-Axia Multihome."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import VentaxiaMultihomeConfigEntry
from .const import MAX_OVERRIDE_DURATION, MIN_OVERRIDE_DURATION, PRESET_NAMES
from .entity import VentaxiaMultihomeEntity
from .protocol import MAX_BOOST_MINIMUM, MIN_BOOST_MINIMUM, AirflowPreset

SERVICE_SET_TIMED_OVERRIDE = "set_timed_override"
SERVICE_SET_BOOST_MINIMUM = "set_boost_minimum"
ATTR_DURATION = "duration"
ATTR_PRESET = "preset"
ATTR_VALUE = "value"
ATTR_CONFIRM = "confirm"


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
        SERVICE_SET_BOOST_MINIMUM,
        {
            vol.Required(ATTR_VALUE): vol.All(
                vol.Coerce(int),
                vol.Range(min=MIN_BOOST_MINIMUM, max=MAX_BOOST_MINIMUM),
            ),
            vol.Required(ATTR_CONFIRM): vol.All(cv.boolean, vol.In([True])),
        },
        "async_set_boost_minimum",
    )
    async_add_entities([MultihomeFan(entry.runtime_data, entry)])


class MultihomeFan(VentaxiaMultihomeEntity, FanEntity):
    """Ventilation fan exposing only timed airflow presets."""

    _attr_translation_key = "ventilation"
    _attr_supported_features = FanEntityFeature.PRESET_MODE
    _attr_preset_modes = list(PRESET_NAMES)

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry, "fan")

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

    async def async_set_boost_minimum(self, value: int, confirm: bool) -> None:
        """Set Boost minimum only after explicit action-level acknowledgement."""

        if not confirm:
            raise HomeAssistantError(
                "Boost minimum requires explicit confirmation because its runtime "
                "effect is not characterised"
            )
        await self.coordinator.async_set_boost_minimum(value=value)
