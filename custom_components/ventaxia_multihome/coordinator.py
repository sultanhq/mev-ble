"""Polling coordinator for Vent-Axia Multihome."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from bleak.exc import BleakError
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothReachabilityIntent
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .bluetooth import TransportError
from .const import (
    CONF_OVERRIDE_DURATION,
    DEFAULT_OVERRIDE_DURATION,
    MAX_OVERRIDE_DURATION,
    MIN_OVERRIDE_DURATION,
    UPDATE_INTERVAL,
)
from .device import (
    DeviceError,
    MultihomeData,
    MultihomeDevice,
    SetupCodeRejectedError,
)
from .protocol import AirflowPreset, ProtocolError, VentilationMode

if TYPE_CHECKING:
    from bleak.backends.device import BLEDevice

_LOGGER = logging.getLogger(__name__)


class VentaxiaMultihomeCoordinator(DataUpdateCoordinator[MultihomeData]):
    """Coordinate serialized reads and controls for one ventilation unit."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        device: MultihomeDevice,
    ) -> None:
        super().__init__(
            hass,
            logger=_LOGGER,
            name=f"Vent-Axia Multihome {device.address}",
            update_interval=UPDATE_INTERVAL,
            config_entry=entry,
        )
        self.device = device
        self._last_ble_device: BLEDevice | None = None

    @property
    def override_duration(self) -> int:
        """Return the configured default override duration."""

        return int(
            self.config_entry.options.get(
                CONF_OVERRIDE_DURATION, DEFAULT_OVERRIDE_DURATION
            )
        )

    async def _async_update_data(self) -> MultihomeData:
        """Fetch zone telemetry and system status."""

        ble_device = self._ble_device()
        try:
            return await self.device.update(ble_device)
        except SetupCodeRejectedError as err:
            await self.device.disconnect()
            raise ConfigEntryAuthFailed(str(err)) from err
        except (
            BleakError,
            TransportError,
            DeviceError,
            ProtocolError,
            TimeoutError,
        ) as err:
            await self.device.disconnect()
            raise UpdateFailed(str(err)) from err

    async def async_set_override(
        self, preset: AirflowPreset, duration_seconds: int | None = None
    ) -> None:
        """Set a timed override and request fresh state."""

        duration = (
            self.override_duration if duration_seconds is None else duration_seconds
        )
        if not MIN_OVERRIDE_DURATION <= duration <= MAX_OVERRIDE_DURATION:
            raise HomeAssistantError(
                "Override duration must be "
                f"{MIN_OVERRIDE_DURATION}..{MAX_OVERRIDE_DURATION} seconds"
            )
        try:
            data = await self.device.set_override(self._ble_device(), preset, duration)
        except (
            BleakError,
            TransportError,
            DeviceError,
            ProtocolError,
            TimeoutError,
        ) as err:
            await self.device.disconnect()
            self.async_set_update_error(err)
            raise HomeAssistantError(
                f"Unable to set Multihome override: {err}"
            ) from err
        self.async_set_updated_data(data)

    async def async_cancel_override(self) -> None:
        """Cancel the active override and request fresh state."""

        try:
            data = await self.device.cancel_override(self._ble_device())
        except (
            BleakError,
            TransportError,
            DeviceError,
            ProtocolError,
            TimeoutError,
        ) as err:
            await self.device.disconnect()
            self.async_set_update_error(err)
            raise HomeAssistantError(
                f"Unable to cancel Multihome override: {err}"
            ) from err
        self.async_set_updated_data(data)

    async def async_set_ventilation_mode(self, mode: VentilationMode) -> None:
        """Set a model-supported ventilation mode and request fresh state."""

        try:
            data = await self.device.set_ventilation_mode(self._ble_device(), mode)
        except (
            BleakError,
            TransportError,
            DeviceError,
            ProtocolError,
            TimeoutError,
        ) as err:
            await self.device.disconnect()
            self.async_set_update_error(err)
            raise HomeAssistantError(
                f"Unable to set Multihome ventilation mode {mode.name.lower()}: {err}"
            ) from err
        self.async_set_updated_data(data)

    def _ble_device(self) -> BLEDevice:
        """Get the current or last known connectable HA Bluetooth device."""

        address = self.config_entry.data[CONF_ADDRESS]
        if ble_device := bluetooth.async_ble_device_from_address(
            self.hass, address, connectable=True
        ):
            self._last_ble_device = ble_device
            return ble_device
        if self._last_ble_device is not None:
            _LOGGER.debug(
                "Using the last known Bluetooth path for %s while reconnecting",
                address,
            )
            return self._last_ble_device
        reason = bluetooth.async_address_reachability_diagnostics(
            self.hass, address, BluetoothReachabilityIntent.CONNECTION
        )
        raise UpdateFailed(f"Bluetooth device is currently unreachable: {reason}")
