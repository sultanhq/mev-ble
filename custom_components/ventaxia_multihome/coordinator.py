"""Polling coordinator for Vent-Axia Multihome."""

from __future__ import annotations

import logging
from dataclasses import replace
from math import ceil, isfinite
from time import time
from typing import TYPE_CHECKING

from bleak.exc import BleakError
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import (
    BluetoothReachabilityIntent,
    BluetoothScanningMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .bluetooth import TransportError
from .const import (
    CO2_CALIBRATION_COOLDOWN,
    CONF_LAST_CO2_CALIBRATION_ATTEMPT,
    CONF_OVERRIDE_DURATION,
    DEFAULT_OVERRIDE_DURATION,
    MAX_OVERRIDE_DURATION,
    MIN_OVERRIDE_DURATION,
    STARTUP_ADVERTISEMENT_TIMEOUT,
    UPDATE_INTERVAL,
)
from .device import (
    CalibrationTargetDiscoveryError,
    CalibrationWriteUncertainError,
    DeviceError,
    GlobalSettingsUnavailableError,
    MultihomeData,
    MultihomeDevice,
    SetupCodeRejectedError,
    SilentHoursUnavailableError,
)
from .protocol import (
    MAX_CO2_CALIBRATION_REFERENCE,
    MIN_CO2_CALIBRATION_REFERENCE,
    AirflowPreset,
    ProtocolError,
    SilentHour,
)

if TYPE_CHECKING:
    from bleak.backends.device import BLEDevice

_LOGGER = logging.getLogger(__name__)


class CalibrationNotSupportedError(HomeAssistantError):
    """Raised when the internal calibration target is not validated."""


class CalibrationRateLimitedError(HomeAssistantError):
    """Raised when calibration is attempted again too quickly."""


class CalibrationCommandNotSentError(HomeAssistantError):
    """Raised when calibration definitely failed before its write."""


class CalibrationDeliveryUncertainError(HomeAssistantError):
    """Raised when the calibration write may have reached the unit."""


class AirflowConfigurationNotSupportedError(HomeAssistantError):
    """Raised when a model is not validated for airflow configuration."""


class AirflowConfigurationUnavailableError(HomeAssistantError):
    """Raised when no current settings record permits an airflow update."""


class SilentHoursNotSupportedError(HomeAssistantError):
    """Raised when schedule management is not validated for the model."""


class SilentHoursConfigurationUnavailableError(HomeAssistantError):
    """Raised when no complete current table permits a schedule mutation."""


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
        self._last_calibration_attempt = self._stored_calibration_attempt(entry)
        self.last_calibration_outcome: str | None = None
        self.last_calibration_error: str | None = None

    @staticmethod
    def _stored_calibration_attempt(entry: ConfigEntry) -> float | None:
        """Restore a valid persisted calibration-attempt timestamp."""

        raw_value = entry.data.get(CONF_LAST_CO2_CALIBRATION_ATTEMPT)
        if (
            isinstance(raw_value, (int, float))
            and not isinstance(raw_value, bool)
            and isfinite(float(raw_value))
            and float(raw_value) > 0
        ):
            return float(raw_value)
        return None

    def _record_calibration_attempt(self, attempted_at: float) -> None:
        """Persist the cooldown before dispatching an uncertain BLE write."""

        self._last_calibration_attempt = attempted_at
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            data={
                **self.config_entry.data,
                CONF_LAST_CO2_CALIBRATION_ATTEMPT: attempted_at,
            },
        )

    @property
    def override_duration(self) -> int:
        """Return the configured default override duration."""

        return int(
            self.config_entry.options.get(
                CONF_OVERRIDE_DURATION, DEFAULT_OVERRIDE_DURATION
            )
        )

    async def async_wait_for_initial_bluetooth(self) -> None:
        """Wait briefly for HA to learn a connectable route during startup."""

        address = self.config_entry.data[CONF_ADDRESS]
        if ble_device := bluetooth.async_ble_device_from_address(
            self.hass, address, connectable=True
        ):
            self._last_ble_device = ble_device
            return

        if bluetooth.async_scanner_count(self.hass, connectable=True) == 0:
            raise ConfigEntryNotReady(self._bluetooth_unreachable_message(address))

        try:
            await bluetooth.async_process_advertisements(
                self.hass,
                lambda _service_info: True,
                {"address": address, "connectable": True},
                BluetoothScanningMode.ACTIVE,
                STARTUP_ADVERTISEMENT_TIMEOUT,
            )
        except TimeoutError as err:
            raise ConfigEntryNotReady(
                self._bluetooth_unreachable_message(address)
            ) from err

        if ble_device := bluetooth.async_ble_device_from_address(
            self.hass, address, connectable=True
        ):
            self._last_ble_device = ble_device
            return

        raise ConfigEntryNotReady(self._bluetooth_unreachable_message(address))

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

    async def async_set_airflow_profile(
        self,
        *,
        low: int,
        normal: int,
        boost: int,
        purge: int,
    ) -> None:
        """Apply and publish one confirmed four-level airflow profile."""

        if not self.device.supports_global_airflow_configuration:
            raise AirflowConfigurationNotSupportedError(
                "Global airflow configuration is not validated for this model"
            )
        if (
            self.data is None
            or not self.last_update_success
            or not self.device.global_settings_write_ready
        ):
            raise AirflowConfigurationUnavailableError(
                "Current global settings are unavailable; wait for a successful poll"
            )
        try:
            settings = await self.device.set_airflow_profile(
                self._ble_device(),
                low=low,
                normal=normal,
                boost=boost,
                purge=purge,
            )
        except GlobalSettingsUnavailableError as err:
            raise AirflowConfigurationUnavailableError(str(err)) from err
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
                f"Unable to update Multihome airflow profile: {err}"
            ) from err
        self.async_set_updated_data(replace(self.data, global_settings=settings))

    async def async_set_silent_hour(self, index: int, record: SilentHour) -> None:
        """Create/update one slot and publish only confirmed full-table readback."""

        await self._async_mutate_silent_hour(index, record)

    async def async_delete_silent_hour(self, index: int) -> None:
        """Delete one slot and publish only confirmed full-table readback."""

        await self._async_mutate_silent_hour(index, None)

    async def _async_mutate_silent_hour(
        self, index: int, record: SilentHour | None
    ) -> None:
        """Run one guarded schedule mutation through the shared device lock."""

        if not self.device.supports_silent_hours_management:
            raise SilentHoursNotSupportedError(
                "Silent-hours management is not validated for this model"
            )
        if (
            self.data is None
            or not self.last_update_success
            or not self.device.silent_hours_write_ready
            or len(self.data.silent_hours) != 6
        ):
            raise SilentHoursConfigurationUnavailableError(
                "Current silent-hours table is unavailable; wait for a successful poll"
            )
        try:
            if record is None:
                slots = await self.device.delete_silent_hour(self._ble_device(), index)
            else:
                slots = await self.device.set_silent_hour(
                    self._ble_device(), index, record
                )
        except SilentHoursUnavailableError as err:
            raise SilentHoursConfigurationUnavailableError(str(err)) from err
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
                f"Unable to update Multihome silent hours: {err}"
            ) from err
        self.async_set_updated_data(replace(self.data, silent_hours=slots))

    async def async_calibrate_internal_co2(self, reference_ppm: int) -> None:
        """Start one guarded internal-sensor calibration command."""

        if (
            not MIN_CO2_CALIBRATION_REFERENCE
            <= reference_ppm
            <= (MAX_CO2_CALIBRATION_REFERENCE)
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

        now = time()
        if self._last_calibration_attempt is not None:
            elapsed = max(0.0, now - self._last_calibration_attempt)
            remaining = ceil(CO2_CALIBRATION_COOLDOWN - elapsed)
            if remaining > 0:
                raise CalibrationRateLimitedError(
                    f"Wait {remaining} seconds before another calibration attempt"
                )

        try:
            await self.device.calibrate_internal_co2(self._ble_device(), reference_ppm)
        except CalibrationTargetDiscoveryError as err:
            await self.device.disconnect()
            self.last_calibration_outcome = "not_sent"
            self.last_calibration_error = str(err)
            raise CalibrationCommandNotSentError(
                f"Calibration was not sent: {err}"
            ) from err
        except CalibrationWriteUncertainError as err:
            self._record_calibration_attempt(now)
            await self.device.disconnect()
            self.last_calibration_outcome = "delivery_uncertain"
            self.last_calibration_error = str(err)
            raise CalibrationDeliveryUncertainError(
                "Calibration delivery could not be confirmed; the command may "
                f"have reached the unit: {err}"
            ) from err
        except (
            BleakError,
            TransportError,
            DeviceError,
            ProtocolError,
            TimeoutError,
        ) as err:
            await self.device.disconnect()
            self.last_calibration_outcome = "not_sent"
            self.last_calibration_error = str(err)
            raise CalibrationCommandNotSentError(
                f"Calibration was not sent: {err}"
            ) from err
        self._record_calibration_attempt(now)
        self.last_calibration_outcome = "sent"
        self.last_calibration_error = None

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
        raise UpdateFailed(self._bluetooth_unreachable_message(address))

    def _bluetooth_unreachable_message(self, address: str) -> str:
        """Return Home Assistant's current Bluetooth reachability diagnosis."""

        reason = bluetooth.async_address_reachability_diagnostics(
            self.hass, address, BluetoothReachabilityIntent.CONNECTION
        )
        return f"Bluetooth device is currently unreachable: {reason}"
