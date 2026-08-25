"""Shared entity base for Vent-Axia Multihome."""

from __future__ import annotations

from homeassistant.const import CONF_ADDRESS
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import VentaxiaMultihomeConfigEntry
from .const import DOMAIN, MANUFACTURER
from .coordinator import VentaxiaMultihomeCoordinator


def format_identifier(address: str) -> str:
    """Normalize a Bluetooth address for unique IDs."""

    return address.replace(":", "").replace("-", "").lower()


class VentaxiaMultihomeEntity(CoordinatorEntity[VentaxiaMultihomeCoordinator]):
    """Base entity attached to one Multihome device."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: VentaxiaMultihomeCoordinator,
        entry: VentaxiaMultihomeConfigEntry,
        key: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{format_identifier(entry.data[CONF_ADDRESS])}_{key}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return Home Assistant device registry information."""

        info = self.coordinator.device.device_info
        address = self._entry.data[CONF_ADDRESS]
        return DeviceInfo(
            identifiers={(DOMAIN, format_identifier(address))},
            connections={(CONNECTION_BLUETOOTH, address)},
            manufacturer=MANUFACTURER,
            model=info.model,
            serial_number=info.serial,
            sw_version=info.firmware,
            hw_version=info.hardware,
        )
