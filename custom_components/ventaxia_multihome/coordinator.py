"""Polling coordinator for Vent-Axia Multihome."""

from __future__ import annotations

import logging
from math import ceil
from time import monotonic
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
    CO2_CALIBRATION_COOLDOWN,
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
from .protocol import (
    MAX_CO2_CALIBRATION_REFERENCE,
    MIN_CO2_CALIBRATION_REFERENCE,
    AirflowPreset,
    ProtocolError,
)

if TYPE_CHECKING:
    from bleak.backends.device import BLEDevice

_LOGGER = logging.getLogger(__name__)


class CalibrationNotSupportedError(HomeAssistantError):
    """Raised when the internal calibration target is not validated."""


class CalibrationRateLimitedError(HomeAssistantError):
    """Raised when calibration is attempted again too quickly."""


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
        self._last_calibration_attempt: float | None = None

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

    async def async_calibrate_internal_co2(self, reference_ppm: int) -> None:
        """Start one guarded internal-sensor calibration command."""

        if not MIN_CO2_CALIBRATION_REFERENCE <= reference_ppm <= (
            MAX_CO2_CALIBRATION_REFERENCE
        ):
            raise HomeAssistantError(
                "CO2 calibration reference must be "
                f"{MIN_CO2_CALIBRATION_REFERENCE}.."
                f"{MAX_CO2_CALIBRATION_REFERENCE} ppm"
            )
        if not self.device.supports_internal_co2_calibration:
            raise CalibrationNotSupportedError(
                "Internal CO2 calibration is not validated for this model"
            )

        now = monotonic()
        if self._last_calibration_attempt is not None:
            remaining = ceil(
                CO2_CALIBRATION_COOLDOWN
                - (now - self._last_calibration_attempt)
            )
            if remaining > 0:
                raise CalibrationRateLimitedError(
                    f"Wait {remaining} seconds before another calibration attempt"
                )

        self._last_calibration_attempt = now
        try:
            await self.device.calibrate_internal_co2(
                self._ble_device(), reference_ppm
            )
        except (
            BleakError,
            TransportError,
            DeviceError,
            ProtocolError,
            TimeoutError,
        ) as err:
            await self.device.disconnect()
            raise HomeAssistantError(
                f"Unable to start Multihome CO2 calibration: {err}"
            ) from err

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
