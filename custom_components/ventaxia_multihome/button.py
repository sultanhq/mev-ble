"""Buttons for Vent-Axia Multihome."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import VentaxiaMultihomeConfigEntry
from .entity import VentaxiaMultihomeEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VentaxiaMultihomeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the cancel-override button."""

    async_add_entities([CancelOverrideButton(entry.runtime_data, entry)])


class CancelOverrideButton(VentaxiaMultihomeEntity, ButtonEntity):
    """Cancel an active timed override without implying fan power-off."""

    _attr_translation_key = "cancel_override"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry, "cancel_override")

    async def async_press(self) -> None:
        """Send the documented cancel command."""

        await self.coordinator.async_cancel_override()
