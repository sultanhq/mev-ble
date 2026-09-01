"""Diagnostic binary sensors for Vent-Axia Multihome."""

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

HUMIDITY_RESPONSE_ENTITIES = (
    ("rapid_response_enabled", "rapid_humidity_response"),
    ("ambient_response_enabled", "ambient_humidity_response"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VentaxiaMultihomeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one diagnostic entity per documented fault flag."""

    entities = [
        MultihomeFaultBinarySensor(entry.runtime_data, entry, fault)
        for fault in DIAGNOSTIC_FAULTS
    ]
    entities.extend(
        MultihomeHumidityResponseBinarySensor(
            entry.runtime_data, entry, attribute, translation_key
        )
        for attribute, translation_key in HUMIDITY_RESPONSE_ENTITIES
    )
    async_add_entities(entities)


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


class MultihomeHumidityResponseBinarySensor(
    VentaxiaMultihomeEntity, BinarySensorEntity
):
    """Represent one read-only installer humidity-response flag."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(
        self, coordinator, entry, attribute: str, translation_key: str
    ) -> None:
        super().__init__(coordinator, entry, attribute)
        self._attribute = attribute
        self._attr_translation_key = translation_key

    @property
    def is_on(self) -> bool | None:
        """Return the decoded packet-137 flag or unknown for malformed data."""

        if (data := self.coordinator.data) is None:
            return None
        return getattr(data.global_settings, self._attribute)
