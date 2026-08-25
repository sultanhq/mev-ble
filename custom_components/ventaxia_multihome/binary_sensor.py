"""Diagnostic fault binary sensors for Vent-Axia Multihome."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import VentaxiaMultihomeConfigEntry
from .entity import VentaxiaMultihomeEntity
from .protocol import DIAGNOSTIC_FAULTS, FaultFlag


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VentaxiaMultihomeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one diagnostic entity per documented fault flag."""

    async_add_entities(
        MultihomeFaultBinarySensor(entry.runtime_data, entry, fault)
        for fault in DIAGNOSTIC_FAULTS
    )


class MultihomeFaultBinarySensor(VentaxiaMultihomeEntity, BinarySensorEntity):
    """Represent one documented fault flag."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry, fault: FaultFlag) -> None:
        key = fault.name.lower()
        super().__init__(coordinator, entry, key)
        self._fault = fault
        self._attr_translation_key = key

    @property
    def is_on(self) -> bool | None:
        """Return whether the fault bit is active."""

        if (data := self.coordinator.data) is None:
            return None
        return bool(data.fault_mask & self._fault)
