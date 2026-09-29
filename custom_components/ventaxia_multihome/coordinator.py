"""Polling coordinator for Vent-Axia Multihome."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
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
from .capabilities import (
    AIRFLOW_FIELDS,
    ANALOGUE_INPUT_1_VALIDATION_FIELDS,
    ANALOGUE_INPUT_2_VALIDATION_FIELDS,
    BOOST_MINIMUM_FIELDS,
    COMFORT_MODE_FIELDS,
    DELAY_OVERRUN_FIELDS,
    DIGITAL_INPUT_VALIDATION_FIELDS,
    HUMIDITY_RESPONSE_FIELDS,
    LOW_TEMPERATURE_PROTECTION_FIELDS,
    LS_ACTION_VALIDATION_FIELDS,
    SENSOR_THRESHOLD_FIELDS,
    TEMPERATURE_VALIDATION_FIELDS,
)
from .const import (
    CO2_CALIBRATION_COOLDOWN,
    CONF_CONFIGURATION_BACKUP,
    CONF_LAST_CO2_CALIBRATION_ATTEMPT,
    CONF_OVERRIDE_DURATION,
    DEFAULT_OVERRIDE_DURATION,
    HARD_RESET_RECOVERY_TIMEOUT,
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
    HardResetDispatchResult,
    HardResetDispatchUncertainError,
    MultihomeData,
    MultihomeDevice,
    SetupCodeRejectedError,
    SilentHoursUnavailableError,
)
from .protocol import (
    GLOBAL_SETTING_FIELD_SPECS,
    MAX_CO2_CALIBRATION_REFERENCE,
    MIN_CO2_CALIBRATION_REFERENCE,
    AirflowPreset,
    GlobalSettingField,
    GlobalSettings,
    ProtocolError,
    SilentHour,
    decode_global_settings,
    decode_silent_hour,
    validate_analogue_input_1_profile,
    validate_analogue_input_2_profile,
    validate_temperature_threshold_profile,
)
from .schedule_time import (
    current_utc_offset_seconds,
    device_slots_to_local,
    local_record_to_device,
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


class HardResetNotSupportedError(HomeAssistantError):
    """Raised when hard reset is not enabled for the exact device identity."""


class HardResetUnavailableError(HomeAssistantError):
    """Raised when current device state is too stale to begin a hard reset."""


class HardResetDeliveryUncertainError(HomeAssistantError):
    """Raised when packet 61 may have reached a unit that disconnected."""


@dataclass(frozen=True, slots=True)
class HardResetRecoveryResult:
    """Describe one bounded post-reset recovery attempt."""

    outcome: str
    detail: str
    configuration_changed: bool = False
    delivery_uncertain: bool = False


class ConfigurationBackupUnavailableError(HomeAssistantError):
    """Raised when a complete restorable configuration snapshot cannot be saved."""


class ConfigurationRestoreError(HomeAssistantError):
    """Raised when a saved configuration cannot be safely restored."""


@dataclass(frozen=True, slots=True)
class ConfigurationRestoreResult:
    """Describe one confirmed restore from the persistent configuration backup."""

    global_fields_restored: int
    silent_hours_restored: int
    raw_record_matches: bool


class AirflowConfigurationNotSupportedError(HomeAssistantError):
    """Raised when a model is not validated for airflow configuration."""


class AirflowConfigurationUnavailableError(HomeAssistantError):
    """Raised when no current settings record permits an airflow update."""


class BoostMinimumConfigurationNotSupportedError(HomeAssistantError):
    """Raised when restricted Boost minimum validation is not enabled."""


class BoostMinimumConfigurationUnavailableError(HomeAssistantError):
    """Raised when no current record permits Boost minimum validation."""


class SensorThresholdConfigurationNotSupportedError(HomeAssistantError):
    """Raised when CO2/humidity threshold writes are not enabled."""


class SensorThresholdConfigurationUnavailableError(HomeAssistantError):
    """Raised when no current record permits a sensor-threshold update."""


class HumidityResponseConfigurationNotSupportedError(HomeAssistantError):
    """Raised when humidity-response writes are not enabled."""


class HumidityResponseConfigurationUnavailableError(HomeAssistantError):
    """Raised when no current record permits a humidity-response update."""


class ComfortModeConfigurationNotSupportedError(HomeAssistantError):
    """Raised when Comfort mode writes are not enabled."""


class ComfortModeConfigurationUnavailableError(HomeAssistantError):
    """Raised when no current record permits a Comfort mode update."""


class DelayOverrunConfigurationNotSupportedError(HomeAssistantError):
    """Raised when delay/overrun writes are not enabled."""


class DelayOverrunConfigurationUnavailableError(HomeAssistantError):
    """Raised when no current record permits a delay/overrun update."""


class LsActionValidationNotSupportedError(HomeAssistantError):
    """Raised when guarded switched-live action validation is not enabled."""


class LsActionValidationUnavailableError(HomeAssistantError):
    """Raised when no current record permits switched-live action validation."""


class AnalogueInput1ValidationNotSupportedError(HomeAssistantError):
    """Raised when guarded analogue-input 1 validation is not enabled."""


class AnalogueInput1ValidationUnavailableError(HomeAssistantError):
    """Raised when no current record permits analogue-input 1 validation."""


class AnalogueInput2ValidationNotSupportedError(HomeAssistantError):
    """Raised when guarded analogue-input 2 validation is not enabled."""


class AnalogueInput2ValidationUnavailableError(HomeAssistantError):
    """Raised when no current record permits analogue-input 2 validation."""


class DigitalInputValidationNotSupportedError(HomeAssistantError):
    """Raised when guarded digital-input validation is not enabled."""


class DigitalInputValidationUnavailableError(HomeAssistantError):
    """Raised when no current record permits digital-input validation."""


class TemperatureValidationNotSupportedError(HomeAssistantError):
    """Raised when guarded temperature validation is not enabled."""


class TemperatureValidationUnavailableError(HomeAssistantError):
    """Raised when no current record permits temperature validation."""


class LowTemperatureProtectionValidationNotSupportedError(HomeAssistantError):
    """Raised when guarded field-16 validation is not enabled."""


class LowTemperatureProtectionValidationUnavailableError(HomeAssistantError):
    """Raised when no current record permits field-16 validation."""


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
        self._hard_reset_dispatch_claimed = False
        self._hard_reset_recovery_mode = False
        self._hard_reset_recovery_task: (
            asyncio.Task[HardResetRecoveryResult] | None
        ) = None
        self._hard_reset_baseline_global_settings: bytes | None = None
        self._hard_reset_baseline_silent_hours: tuple[object, ...] | None = None
        self._hard_reset_baseline_advertisement_time: float | None = None
        self.last_hard_reset_recovery_result: HardResetRecoveryResult | None = None

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

    def _silent_hours_utc_offset_seconds(self) -> int:
        """Return the current HA-local offset used by UTC-scheduled MEV firmware."""

        hass = getattr(self, "hass", None)
        config = getattr(hass, "config", None)
        time_zone = getattr(config, "time_zone", "UTC")
        return current_utc_offset_seconds(time_zone)

    def _localize_data(self, data: MultihomeData) -> MultihomeData:
        """Convert raw UTC schedule records to the current HA local wall clock."""

        if not isinstance(data, MultihomeData):
            return data
        offset = VentaxiaMultihomeCoordinator._silent_hours_utc_offset_seconds(self)
        slots = device_slots_to_local(data.silent_hours, offset)
        if slots is data.silent_hours:
            return data
        return replace(data, silent_hours=slots)

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

        if self._hard_reset_recovery_mode:
            raise UpdateFailed(
                "Hard reset recovery owns the Bluetooth route; waiting for a fresh "
                "device advertisement before reconnecting"
            )

        ble_device = self._ble_device()
        try:
            data = await self.device.update(ble_device)
            return VentaxiaMultihomeCoordinator._localize_data(self, data)
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
        self.async_set_updated_data(
            VentaxiaMultihomeCoordinator._localize_data(self, data)
        )

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
        self.async_set_updated_data(
            VentaxiaMultihomeCoordinator._localize_data(self, data)
        )

    @property
    def configuration_backup(self) -> dict[str, object] | None:
        """Return the persisted configuration backup, if one is available."""

        backup = self.config_entry.options.get(CONF_CONFIGURATION_BACKUP)
        return dict(backup) if isinstance(backup, dict) else None

    def save_configuration_backup(self, *, reason: str) -> dict[str, object]:
        """Persist a complete, currently confirmed configuration snapshot."""

        if (
            self.data is None
            or not self.last_update_success
            or not self.device.global_settings_write_ready
        ):
            raise ConfigurationBackupUnavailableError(
                "Current global settings are unavailable; wait for a successful poll"
            )
        settings = self.data.global_settings
        if settings.invalid_boolean_fields:
            raise ConfigurationBackupUnavailableError(
                "Current global settings contain unsupported boolean values and "
                "cannot be guaranteed restorable"
            )

        silent_hours: list[str | None] = []
        if self.device.supports_silent_hours_management:
            if (
                not self.device.silent_hours_write_ready
                or len(self.data.silent_hours) != 6
                or not all(slot.is_known for slot in self.data.silent_hours)
            ):
                raise ConfigurationBackupUnavailableError(
                    "The complete six-slot silent-hours table is unavailable"
                )
            silent_hours = [
                slot.record.raw_record.hex() if slot.record is not None else None
                for slot in self.data.silent_hours
            ]

        backup: dict[str, object] = {
            "version": 1,
            "reason": reason,
            "captured_at": self.data.last_successful_update.isoformat(),
            "time_zone": self.hass.config.time_zone,
            "identity": {
                "model_number": self.device.model_number,
                "serial": self.device.device_info.serial,
                "firmware": self.device.device_info.firmware,
                "hardware": self.device.device_info.hardware,
            },
            "global_settings": settings.raw_record.hex(),
            "silent_hours": silent_hours,
        }
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            options={
                **self.config_entry.options,
                CONF_CONFIGURATION_BACKUP: backup,
            },
        )
        return backup

    def _load_configuration_backup(
        self,
    ) -> tuple[dict[str, object], GlobalSettings, tuple[SilentHour | None, ...]]:
        """Validate and decode the persisted backup without writing the unit."""

        backup = self.configuration_backup
        if backup is None:
            raise ConfigurationRestoreError("No configuration backup is stored")
        if backup.get("version") != 1:
            raise ConfigurationRestoreError(
                "The stored configuration backup is unsupported"
            )

        identity = backup.get("identity")
        if not isinstance(identity, dict):
            raise ConfigurationRestoreError("The stored backup has no device identity")
        expected_identity = (
            identity.get("model_number"),
            identity.get("firmware"),
            identity.get("hardware"),
        )
        current_identity = (
            self.device.model_number,
            self.device.device_info.firmware,
            self.device.device_info.hardware,
        )
        if expected_identity != current_identity:
            raise ConfigurationRestoreError(
                "The stored backup belongs to a different model, firmware, or hardware"
            )
        saved_serial = identity.get("serial")
        if (
            isinstance(saved_serial, str)
            and self.device.device_info.serial is not None
            and saved_serial != self.device.device_info.serial
        ):
            raise ConfigurationRestoreError(
                "The stored backup belongs to a different serial number"
            )

        raw_settings = backup.get("global_settings")
        if not isinstance(raw_settings, str):
            raise ConfigurationRestoreError(
                "The stored backup has no global-settings record"
            )
        try:
            target_settings = decode_global_settings(bytes.fromhex(raw_settings))
        except (ValueError, ProtocolError) as err:
            raise ConfigurationRestoreError(
                "The stored global-settings record is malformed"
            ) from err
        if target_settings.invalid_boolean_fields:
            raise ConfigurationRestoreError(
                "The stored global-settings record contains unsupported boolean values"
            )

        raw_silent_hours = backup.get("silent_hours")
        if not isinstance(raw_silent_hours, list):
            raise ConfigurationRestoreError(
                "The stored backup has no silent-hours table"
            )
        if self.device.supports_silent_hours_management and len(raw_silent_hours) != 6:
            raise ConfigurationRestoreError(
                "The stored silent-hours table does not contain six slots"
            )
        decoded_silent_hours: list[SilentHour | None] = []
        try:
            for raw_record in raw_silent_hours:
                if raw_record is None:
                    decoded_silent_hours.append(None)
                elif isinstance(raw_record, str):
                    decoded_silent_hours.append(
                        decode_silent_hour(bytes.fromhex(raw_record))
                    )
                else:
                    raise ValueError("invalid silent-hours slot")
        except (ValueError, ProtocolError) as err:
            raise ConfigurationRestoreError(
                "The stored silent-hours table is malformed"
            ) from err

        return backup, target_settings, tuple(decoded_silent_hours)

    @staticmethod
    def _next_valid_profile_step(
        current: tuple[int, ...],
        target: tuple[int, ...],
        validator: Callable[..., None],
        *,
        name: str,
    ) -> tuple[int, ...]:
        """Return one target-directed field change that keeps a profile valid."""

        for index, (current_value, target_value) in enumerate(
            zip(current, target, strict=True)
        ):
            if current_value == target_value:
                continue
            candidate = list(current)
            candidate[index] = target_value
            try:
                validator(*candidate)
            except ProtocolError:
                continue
            return tuple(candidate)
        raise ConfigurationRestoreError(
            f"No safe one-field path could restore the saved {name} profile"
        )

    async def async_restore_configuration_backup(self) -> ConfigurationRestoreResult:
        """Restore the saved configuration through validated, readback-checked paths."""

        _backup, target, target_silent_hours = self._load_configuration_backup()
        if (
            self.data is None
            or not self.last_update_success
            or not self.device.global_settings_write_ready
        ):
            raise ConfigurationRestoreError(
                "Current global settings are unavailable; wait for a successful poll"
            )
        if (
            self.device.supports_silent_hours_management
            and (
                not self.device.silent_hours_write_ready
                or len(self.data.silent_hours) != 6
            )
        ):
            raise ConfigurationRestoreError(
                "Current silent-hours state is unavailable; wait for a successful poll"
            )

        restorable_fields = (
            AIRFLOW_FIELDS
            | BOOST_MINIMUM_FIELDS
            | SENSOR_THRESHOLD_FIELDS
            | HUMIDITY_RESPONSE_FIELDS
            | COMFORT_MODE_FIELDS
            | DELAY_OVERRUN_FIELDS
            | LS_ACTION_VALIDATION_FIELDS
            | TEMPERATURE_VALIDATION_FIELDS
            | LOW_TEMPERATURE_PROTECTION_FIELDS
            | ANALOGUE_INPUT_1_VALIDATION_FIELDS
            | ANALOGUE_INPUT_2_VALIDATION_FIELDS
            | DIGITAL_INPUT_VALIDATION_FIELDS
        )
        unsupported = self.device.writable_installer_fields - restorable_fields
        if unsupported:
            ids = ", ".join(str(int(field)) for field in sorted(unsupported))
            raise ConfigurationRestoreError(
                f"Validated writable fields are missing from restore coverage: {ids}"
            )

        initial = self.data.global_settings
        global_fields_restored = sum(
            getattr(initial, GLOBAL_SETTING_FIELD_SPECS[field].attribute)
            != getattr(target, GLOBAL_SETTING_FIELD_SPECS[field].attribute)
            for field in self.device.writable_installer_fields
        )

        if AIRFLOW_FIELDS <= self.device.writable_installer_fields and (
            initial.speed_low,
            initial.speed_medium,
            initial.speed_boost,
            initial.speed_purge,
        ) != (
            target.speed_low,
            target.speed_medium,
            target.speed_boost,
            target.speed_purge,
        ):
            await self.async_set_airflow_profile(
                low=target.speed_low,
                normal=target.speed_medium,
                boost=target.speed_boost,
                purge=target.speed_purge,
            )

        if (
            BOOST_MINIMUM_FIELDS <= self.device.writable_installer_fields
            and self.data.global_settings.boost_minimum != target.boost_minimum
        ):
            await self.async_set_boost_minimum(value=target.boost_minimum)

        current = self.data.global_settings
        if SENSOR_THRESHOLD_FIELDS <= self.device.writable_installer_fields and (
            current.humidity_threshold,
            current.co2_boost_threshold,
            current.co2_purge_threshold,
        ) != (
            target.humidity_threshold,
            target.co2_boost_threshold,
            target.co2_purge_threshold,
        ):
            await self.async_set_sensor_thresholds(
                humidity=target.humidity_threshold,
                co2_boost=target.co2_boost_threshold,
                co2_purge=target.co2_purge_threshold,
            )

        current = self.data.global_settings
        if HUMIDITY_RESPONSE_FIELDS <= self.device.writable_installer_fields and (
            current.rapid_response_enabled,
            current.ambient_response_enabled,
        ) != (
            target.rapid_response_enabled,
            target.ambient_response_enabled,
        ):
            await self.async_set_humidity_response(
                rapid=bool(target.rapid_response_enabled),
                ambient=bool(target.ambient_response_enabled),
            )

        if (
            COMFORT_MODE_FIELDS <= self.device.writable_installer_fields
            and self.data.global_settings.comfort_enabled != target.comfort_enabled
        ):
            await self.async_set_comfort_mode(enabled=bool(target.comfort_enabled))

        current = self.data.global_settings
        if DELAY_OVERRUN_FIELDS <= self.device.writable_installer_fields and (
            current.delay_timeout_minutes,
            current.overrun_enabled,
            current.overrun_timeout_minutes,
        ) != (
            target.delay_timeout_minutes,
            target.overrun_enabled,
            target.overrun_timeout_minutes,
        ):
            # Field 7 (Delay enabled) remains intentionally unvalidated on this
            # firmware. Preserve its current value while restoring only fields
            # 8..10 through the already validated grouped setter.
            await self.async_set_delay_overrun(
                delay_enabled=bool(current.delay_enabled),
                delay_minutes=target.delay_timeout_minutes,
                overrun_enabled=bool(target.overrun_enabled),
                overrun_minutes=target.overrun_timeout_minutes,
            )

        current = self.data.global_settings
        if LS_ACTION_VALIDATION_FIELDS <= self.device.writable_installer_fields and (
            current.ls1_action,
            current.ls2_action,
            current.ls3_action,
        ) != (
            target.ls1_action,
            target.ls2_action,
            target.ls3_action,
        ):
            await self.async_set_ls_action_validation(
                ls1_action=target.ls1_action,
                ls2_action=target.ls2_action,
                ls3_action=target.ls3_action,
            )

        target_temperature = (
            target.low_threshold_action,
            target.high_threshold_action,
            target.low_temperature_threshold,
            target.high_temperature_threshold,
        )
        current = self.data.global_settings
        current_temperature = (
            current.low_threshold_action,
            current.high_threshold_action,
            current.low_temperature_threshold,
            current.high_temperature_threshold,
        )
        if (
            TEMPERATURE_VALIDATION_FIELDS <= self.device.writable_installer_fields
            and current_temperature != target_temperature
        ):
            if current.low_temperature_enabled is not False:
                if not (
                    LOW_TEMPERATURE_PROTECTION_FIELDS
                    <= self.device.writable_installer_fields
                ):
                    raise ConfigurationRestoreError(
                        "Temperature settings require disabling low-temperature "
                        "protection, but that field is not validated writable"
                    )
                await self.async_set_low_temperature_protection_validation(
                    enabled=False
                )
            for _attempt in range(4):
                current = self.data.global_settings
                current_temperature = (
                    current.low_threshold_action,
                    current.high_threshold_action,
                    current.low_temperature_threshold,
                    current.high_temperature_threshold,
                )
                if current_temperature == target_temperature:
                    break
                step = self._next_valid_profile_step(
                    current_temperature,
                    target_temperature,
                    validate_temperature_threshold_profile,
                    name="temperature",
                )
                await self.async_set_temperature_threshold_validation(
                    low_action=step[0],
                    high_action=step[1],
                    low_threshold=step[2],
                    high_threshold=step[3],
                )
            else:
                raise ConfigurationRestoreError(
                    "Temperature settings did not converge to the saved profile"
                )

        target_analogue_1 = (
            target.analogue_input_1_low_action,
            target.analogue_input_1_high_action,
            target.analogue_input_1_low_value,
            target.analogue_input_1_high_value,
        )
        for _attempt in (
            range(4)
            if ANALOGUE_INPUT_1_VALIDATION_FIELDS
            <= self.device.writable_installer_fields
            else range(0)
        ):
            current = self.data.global_settings
            current_analogue_1 = (
                current.analogue_input_1_low_action,
                current.analogue_input_1_high_action,
                current.analogue_input_1_low_value,
                current.analogue_input_1_high_value,
            )
            if current_analogue_1 == target_analogue_1:
                break
            step = self._next_valid_profile_step(
                current_analogue_1,
                target_analogue_1,
                validate_analogue_input_1_profile,
                name="analogue input 1",
            )
            await self.async_set_analogue_input_1_validation(
                low_action=step[0],
                high_action=step[1],
                low_threshold=step[2],
                high_threshold=step[3],
            )
        else:
            if ANALOGUE_INPUT_1_VALIDATION_FIELDS <= self.device.writable_installer_fields:
                raise ConfigurationRestoreError(
                    "Analogue input 1 settings did not converge to the saved profile"
                )

        target_analogue_2 = (
            target.analogue_input_2_low_action,
            target.analogue_input_2_high_action,
            target.analogue_input_2_low_value,
            target.analogue_input_2_high_value,
        )
        for _attempt in (
            range(4)
            if ANALOGUE_INPUT_2_VALIDATION_FIELDS
            <= self.device.writable_installer_fields
            else range(0)
        ):
            current = self.data.global_settings
            current_analogue_2 = (
                current.analogue_input_2_low_action,
                current.analogue_input_2_high_action,
                current.analogue_input_2_low_value,
                current.analogue_input_2_high_value,
            )
            if current_analogue_2 == target_analogue_2:
                break
            step = self._next_valid_profile_step(
                current_analogue_2,
                target_analogue_2,
                validate_analogue_input_2_profile,
                name="analogue input 2",
            )
            await self.async_set_analogue_input_2_validation(
                low_action=step[0],
                high_action=step[1],
                low_threshold=step[2],
                high_threshold=step[3],
            )
        else:
            if ANALOGUE_INPUT_2_VALIDATION_FIELDS <= self.device.writable_installer_fields:
                raise ConfigurationRestoreError(
                    "Analogue input 2 settings did not converge to the saved profile"
                )

        target_digital = (
            target.digital_input_1_action,
            target.digital_input_2_action,
        )
        for _attempt in (
            range(2)
            if DIGITAL_INPUT_VALIDATION_FIELDS <= self.device.writable_installer_fields
            else range(0)
        ):
            current = self.data.global_settings
            current_digital = (
                current.digital_input_1_action,
                current.digital_input_2_action,
            )
            if current_digital == target_digital:
                break
            step = list(current_digital)
            index = next(
                index
                for index, values in enumerate(
                    zip(current_digital, target_digital, strict=True)
                )
                if values[0] != values[1]
            )
            step[index] = target_digital[index]
            await self.async_set_digital_input_validation(
                digital_input_1_action=step[0],
                digital_input_2_action=step[1],
            )
        else:
            if DIGITAL_INPUT_VALIDATION_FIELDS <= self.device.writable_installer_fields:
                raise ConfigurationRestoreError(
                    "Digital input settings did not converge to the saved profile"
                )

        if (
            LOW_TEMPERATURE_PROTECTION_FIELDS <= self.device.writable_installer_fields
            and self.data.global_settings.low_temperature_enabled
            != target.low_temperature_enabled
        ):
            await self.async_set_low_temperature_protection_validation(
                enabled=bool(target.low_temperature_enabled)
            )

        silent_hours_restored = 0
        if self.device.supports_silent_hours_management:
            for index, target_record in enumerate(target_silent_hours):
                current_record = self.data.silent_hours[index].record
                current_raw = (
                    current_record.raw_record if current_record is not None else None
                )
                target_raw = (
                    target_record.raw_record if target_record is not None else None
                )
                if current_raw == target_raw:
                    continue
                if target_record is None:
                    await self.async_delete_silent_hour(index)
                else:
                    await self.async_set_silent_hour(index, target_record)
                silent_hours_restored += 1

        remaining = tuple(
            GLOBAL_SETTING_FIELD_SPECS[field].attribute
            for field in self.device.writable_installer_fields
            if getattr(
                self.data.global_settings,
                GLOBAL_SETTING_FIELD_SPECS[field].attribute,
            )
            != getattr(target, GLOBAL_SETTING_FIELD_SPECS[field].attribute)
        )
        if remaining:
            raise ConfigurationRestoreError(
                "Restore completed writes but confirmed values still differ for: "
                + ", ".join(remaining)
            )

        if self.device.supports_silent_hours_management:
            confirmed_silent_hours = tuple(
                slot.record.raw_record if slot.record is not None else None
                for slot in self.data.silent_hours
            )
            expected_silent_hours = tuple(
                record.raw_record if record is not None else None
                for record in target_silent_hours
            )
            if confirmed_silent_hours != expected_silent_hours:
                raise ConfigurationRestoreError(
                    "Restore completed writes but silent-hours readback still differs"
                )

        return ConfigurationRestoreResult(
            global_fields_restored=global_fields_restored,
            silent_hours_restored=silent_hours_restored,
            raw_record_matches=(
                self.data.global_settings.raw_record == target.raw_record
            ),
        )

    async def async_dispatch_hard_reset_from_options(
        self,
    ) -> HardResetDispatchResult:
        """Dispatch one guarded reset and start bounded recovery ownership."""

        if not self.device.supports_guarded_hard_reset:
            raise HardResetNotSupportedError(
                "Hard reset is not enabled for this model, firmware, and hardware"
            )
        if self.data is None or not self.last_update_success:
            raise HardResetUnavailableError(
                "Current device state is unavailable; wait for a successful poll"
            )
        if self._hard_reset_dispatch_claimed:
            raise HardResetUnavailableError(
                "A hard reset has already been claimed for this device session; "
                "wait for recovery before starting another Configure flow"
            )

        try:
            ble_device = self._ble_device()
        except UpdateFailed as err:
            raise HardResetUnavailableError(str(err)) from err

        try:
            self.save_configuration_backup(reason="hard_reset")
        except ConfigurationBackupUnavailableError as err:
            raise HardResetUnavailableError(
                f"Hard reset requires a fresh restorable configuration backup: {err}"
            ) from err

        self._hard_reset_baseline_global_settings = self.data.global_settings.raw_record
        self._hard_reset_baseline_silent_hours = tuple(self.data.silent_hours)
        baseline_advertisement = bluetooth.async_last_service_info(
            self.hass,
            self.config_entry.data[CONF_ADDRESS],
            connectable=True,
        )
        self._hard_reset_baseline_advertisement_time = (
            baseline_advertisement.time if baseline_advertisement else None
        )

        # Claim synchronously before the first await. Separate options-flow instances
        # share this coordinator, so a sibling flow cannot queue a second packet 61.
        self._hard_reset_dispatch_claimed = True

        try:
            result = await self.device._dispatch_hard_reset(ble_device)
        except asyncio.CancelledError:
            if self.device.hard_reset_recovery_pending:
                self._begin_hard_reset_recovery(delivery_uncertain=True)
            else:
                self._hard_reset_dispatch_claimed = False
            raise
        except HardResetDispatchUncertainError as err:
            self._begin_hard_reset_recovery(delivery_uncertain=True)
            raise HardResetDeliveryUncertainError(str(err)) from err
        except (
            BleakError,
            TransportError,
            DeviceError,
            ProtocolError,
            TimeoutError,
        ) as err:
            # These errors escape the device primitive only before packet 61 reaches
            # its uncertain send phase. Release the coordinator claim so a later
            # fresh Configure flow can retry after connectivity/authentication recovers.
            self._hard_reset_dispatch_claimed = False
            await self.device.disconnect()
            raise HardResetUnavailableError(
                f"Hard reset was not dispatched: {err}"
            ) from err

        self._begin_hard_reset_recovery(delivery_uncertain=False)
        return result

    def _begin_hard_reset_recovery(self, *, delivery_uncertain: bool) -> None:
        """Synchronously transfer post-reset ownership to a coordinator task."""

        if self._hard_reset_recovery_task and not self._hard_reset_recovery_task.done():
            return

        self._hard_reset_recovery_mode = True
        self._last_ble_device = None
        self.async_set_update_error(
            UpdateFailed(
                "Hard reset dispatched; waiting for a fresh Bluetooth advertisement"
            )
        )
        self._hard_reset_recovery_task = self.hass.async_create_task(
            self._async_recover_after_hard_reset(
                delivery_uncertain=delivery_uncertain
            ),
            f"Recover Vent-Axia Multihome {self.config_entry.data[CONF_ADDRESS]} "
            "after hard reset",
        )

    async def async_wait_for_hard_reset_recovery(self) -> HardResetRecoveryResult:
        """Wait for coordinator-owned reset recovery without transferring ownership."""

        task = self._hard_reset_recovery_task
        if task is None:
            raise HardResetUnavailableError("Hard reset recovery has not started")
        return await asyncio.shield(task)

    async def _async_recover_after_hard_reset(
        self, *, delivery_uncertain: bool
    ) -> HardResetRecoveryResult:
        """Wait for one fresh advertisement and make one controlled reconnect."""

        address = self.config_entry.data[CONF_ADDRESS]
        await self.device.disconnect()

        if bluetooth.async_scanner_count(self.hass, connectable=True) == 0:
            return self._finish_hard_reset_recovery_failure(
                outcome="bluetooth_unavailable",
                detail=(
                    "No connectable Home Assistant Bluetooth scanner is available. "
                    "Check Bluetooth/proxy availability, then reload the integration; "
                    "do not resend the reset."
                ),
                delivery_uncertain=delivery_uncertain,
            )

        try:
            await bluetooth.async_process_advertisements(
                self.hass,
                lambda service_info: (
                    self._hard_reset_baseline_advertisement_time is None
                    or service_info.time
                    > self._hard_reset_baseline_advertisement_time
                ),
                {"address": address, "connectable": True},
                BluetoothScanningMode.ACTIVE,
                HARD_RESET_RECOVERY_TIMEOUT,
            )
        except TimeoutError:
            return self._finish_hard_reset_recovery_failure(
                outcome="timed_out",
                detail=(
                    "The unit did not produce a fresh connectable advertisement within "
                    f"{HARD_RESET_RECOVERY_TIMEOUT} seconds. Check power, Bluetooth "
                    "range, and the unit state; reload the integration after it is "
                    "advertising again. Do not resend the reset."
                ),
                delivery_uncertain=delivery_uncertain,
            )

        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, address, connectable=True
        )
        if ble_device is None:
            return self._finish_hard_reset_recovery_failure(
                outcome="route_unavailable",
                detail=(
                    "A fresh advertisement was observed but Home Assistant could not "
                    "resolve a connectable route. Check the Bluetooth proxy/adapter "
                    "and reload the integration; do not resend the reset."
                ),
                delivery_uncertain=delivery_uncertain,
            )

        self._last_ble_device = ble_device
        try:
            data = await self.device.recover_after_hard_reset(ble_device)
        except SetupCodeRejectedError:
            await self.device.disconnect()
            return self._finish_hard_reset_recovery_failure(
                outcome="pairing_required",
                detail=(
                    "The reset unit is advertising again but rejected the stored "
                    "application setup code. Put the unit into physical pairing mode "
                    "and reload/re-authenticate the integration. Do not resend reset."
                ),
                delivery_uncertain=delivery_uncertain,
            )
        except (
            BleakError,
            TransportError,
            DeviceError,
            ProtocolError,
            TimeoutError,
        ) as err:
            await self.device.disconnect()
            return self._finish_hard_reset_recovery_failure(
                outcome="reconnect_failed",
                detail=(
                    "The unit advertised again but the single controlled reconnect "
                    f"failed: {err}. Wait for the unit to settle, then reload the "
                    "integration. Do not resend reset."
                ),
                delivery_uncertain=delivery_uncertain,
            )

        localized = VentaxiaMultihomeCoordinator._localize_data(self, data)
        configuration_changed = bool(
            (
                self._hard_reset_baseline_global_settings is not None
                and data.global_settings.raw_record
                != self._hard_reset_baseline_global_settings
            )
            or (
                self._hard_reset_baseline_silent_hours is not None
                and tuple(localized.silent_hours)
                != self._hard_reset_baseline_silent_hours
            )
        )
        outcome = (
            "recovered_configuration_changed"
            if configuration_changed
            else "recovered"
        )
        detail = (
            "The unit returned on a fresh Bluetooth advertisement and Home Assistant "
            "completed one authenticated telemetry/settings read."
        )
        if configuration_changed:
            detail += (
                " One or more global settings or silent-hours records differ from the "
                "pre-reset snapshot; review and restore commissioning settings before "
                "normal use."
            )

        self._hard_reset_recovery_mode = False
        result = HardResetRecoveryResult(
            outcome=outcome,
            detail=detail,
            configuration_changed=configuration_changed,
            delivery_uncertain=delivery_uncertain,
        )
        self.last_hard_reset_recovery_result = result
        self.async_set_updated_data(localized)
        return result

    def _finish_hard_reset_recovery_failure(
        self,
        *,
        outcome: str,
        detail: str,
        delivery_uncertain: bool,
    ) -> HardResetRecoveryResult:
        """Keep normal polling suppressed after an actionable recovery failure."""

        result = HardResetRecoveryResult(
            outcome=outcome,
            detail=detail,
            delivery_uncertain=delivery_uncertain,
        )
        self.last_hard_reset_recovery_result = result
        self.async_set_update_error(UpdateFailed(detail))
        return result

    async def async_shutdown(self) -> None:
        """Cancel any reset-recovery task and disconnect during entry unload."""

        task = self._hard_reset_recovery_task
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self.device.disconnect()

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
                "Global airflow configuration is not validated for this model, "
                "firmware, and hardware"
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

    async def async_set_sensor_thresholds(
        self,
        *,
        humidity: int,
        co2_boost: int,
        co2_purge: int,
    ) -> None:
        """Apply and publish confirmed CO2 and humidity thresholds."""

        if not self.device.supports_sensor_threshold_configuration:
            raise SensorThresholdConfigurationNotSupportedError(
                "Sensor-threshold configuration is not enabled for this model, "
                "firmware, and hardware"
            )
        if (
            self.data is None
            or not self.last_update_success
            or not self.device.global_settings_write_ready
        ):
            raise SensorThresholdConfigurationUnavailableError(
                "Current global settings are unavailable; wait for a successful poll"
            )
        try:
            settings = await self.device.set_sensor_thresholds(
                self._ble_device(),
                humidity=humidity,
                co2_boost=co2_boost,
                co2_purge=co2_purge,
            )
        except GlobalSettingsUnavailableError as err:
            raise SensorThresholdConfigurationUnavailableError(str(err)) from err
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
                f"Unable to update Multihome sensor thresholds: {err}"
            ) from err
        self.async_set_updated_data(replace(self.data, global_settings=settings))

    async def async_set_humidity_response(
        self,
        *,
        rapid: bool,
        ambient: bool,
    ) -> None:
        """Apply and publish confirmed humidity-response settings."""

        if not self.device.supports_humidity_response_configuration:
            raise HumidityResponseConfigurationNotSupportedError(
                "Humidity-response configuration is not enabled for this model, "
                "firmware, and hardware"
            )
        if (
            self.data is None
            or not self.last_update_success
            or not self.device.global_settings_write_ready
        ):
            raise HumidityResponseConfigurationUnavailableError(
                "Current global settings are unavailable; wait for a successful poll"
            )
        try:
            settings = await self.device.set_humidity_response(
                self._ble_device(),
                rapid=rapid,
                ambient=ambient,
            )
        except GlobalSettingsUnavailableError as err:
            raise HumidityResponseConfigurationUnavailableError(str(err)) from err
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
                f"Unable to update Multihome humidity response: {err}"
            ) from err
        self.async_set_updated_data(replace(self.data, global_settings=settings))

    async def async_set_boost_minimum(self, *, value: int) -> None:
        """Apply and publish a confirmed Boost minimum value."""

        if not self.device.supports_boost_minimum_configuration:
            raise BoostMinimumConfigurationNotSupportedError(
                "Boost minimum configuration is not enabled for this model, "
                "firmware, and hardware"
            )
        if (
            self.data is None
            or not self.last_update_success
            or not self.device.global_settings_write_ready
        ):
            raise BoostMinimumConfigurationUnavailableError(
                "Current global settings are unavailable; wait for a successful poll"
            )
        try:
            settings = await self.device.set_boost_minimum(
                self._ble_device(), value=value
            )
        except GlobalSettingsUnavailableError as err:
            raise BoostMinimumConfigurationUnavailableError(str(err)) from err
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
                f"Unable to update Multihome Boost minimum: {err}"
            ) from err
        self.async_set_updated_data(replace(self.data, global_settings=settings))

    async def async_set_comfort_mode(self, *, enabled: bool) -> None:
        """Apply and publish confirmed Comfort mode state."""

        if not self.device.supports_comfort_mode_configuration:
            raise ComfortModeConfigurationNotSupportedError(
                "Comfort-mode configuration is not enabled for this model, "
                "firmware, and hardware"
            )
        if (
            self.data is None
            or not self.last_update_success
            or not self.device.global_settings_write_ready
        ):
            raise ComfortModeConfigurationUnavailableError(
                "Current global settings are unavailable; wait for a successful poll"
            )
        try:
            settings = await self.device.set_comfort_mode(
                self._ble_device(), enabled=enabled
            )
        except GlobalSettingsUnavailableError as err:
            raise ComfortModeConfigurationUnavailableError(str(err)) from err
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
                f"Unable to update Multihome Comfort mode: {err}"
            ) from err
        self.async_set_updated_data(replace(self.data, global_settings=settings))

    async def async_set_delay_overrun(
        self,
        *,
        delay_enabled: bool,
        delay_minutes: int,
        overrun_enabled: bool,
        overrun_minutes: int,
    ) -> None:
        """Apply and publish confirmed LS delay/overrun timer state."""

        if not self.device.supports_delay_overrun_configuration:
            raise DelayOverrunConfigurationNotSupportedError(
                "Delay/overrun configuration is not enabled for this model, "
                "firmware, and hardware"
            )
        if (
            self.data is None
            or not self.last_update_success
            or not self.device.global_settings_write_ready
        ):
            raise DelayOverrunConfigurationUnavailableError(
                "Current global settings are unavailable; wait for a successful poll"
            )
        try:
            settings = await self.device.set_delay_overrun(
                self._ble_device(),
                delay_enabled=delay_enabled,
                delay_minutes=delay_minutes,
                overrun_enabled=overrun_enabled,
                overrun_minutes=overrun_minutes,
            )
        except GlobalSettingsUnavailableError as err:
            raise DelayOverrunConfigurationUnavailableError(str(err)) from err
        except (
            BleakError,
            TransportError,
            DeviceError,
            ProtocolError,
            TimeoutError,
        ) as err:
            await self.device.disconnect()
            # A rejected installer write invalidates further writes until the next
            # poll, but it is not a failed telemetry poll. Keep the last confirmed
            # entity snapshot available while diagnostics retain the write evidence.
            raise HomeAssistantError(
                f"Unable to update Multihome delay/overrun timers: {err}"
            ) from err
        self.async_set_updated_data(replace(self.data, global_settings=settings))

    async def async_set_temperature_threshold_validation(
        self,
        *,
        low_action: int,
        high_action: int,
        low_threshold: int,
        high_threshold: int,
    ) -> None:
        """Apply and publish one confirmed temperature validation change."""

        if not self.device.supports_temperature_threshold_validation:
            raise TemperatureValidationNotSupportedError(
                "temperature-threshold validation is not enabled for this model, "
                "firmware, and hardware"
            )
        if (
            self.data is None
            or not self.last_update_success
            or not self.device.global_settings_write_ready
        ):
            raise TemperatureValidationUnavailableError(
                "Current global settings are unavailable; wait for a successful poll"
            )
        try:
            settings = await self.device.set_temperature_threshold_validation(
                self._ble_device(),
                low_action=low_action,
                high_action=high_action,
                low_threshold=low_threshold,
                high_threshold=high_threshold,
            )
        except GlobalSettingsUnavailableError as err:
            raise TemperatureValidationUnavailableError(str(err)) from err
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
                f"Unable to update Multihome temperature validation field: {err}"
            ) from err
        self.async_set_updated_data(replace(self.data, global_settings=settings))

    async def async_set_ls_action_validation(
        self,
        *,
        ls1_action: int,
        ls2_action: int,
        ls3_action: int,
    ) -> None:
        """Apply and publish one confirmed switched-live action change."""

        if not self.device.supports_ls_action_validation:
            raise LsActionValidationNotSupportedError(
                "LS action validation is not enabled for this model, firmware, "
                "and hardware"
            )
        if (
            self.data is None
            or not self.last_update_success
            or not self.device.global_settings_write_ready
        ):
            raise LsActionValidationUnavailableError(
                "Current global settings are unavailable; wait for a successful poll"
            )
        try:
            settings = await self.device.set_ls_action_validation(
                self._ble_device(),
                ls1_action=ls1_action,
                ls2_action=ls2_action,
                ls3_action=ls3_action,
            )
        except GlobalSettingsUnavailableError as err:
            raise LsActionValidationUnavailableError(str(err)) from err
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
                f"Unable to update Multihome LS action validation field: {err}"
            ) from err
        self.async_set_updated_data(replace(self.data, global_settings=settings))

    async def async_set_analogue_input_1_validation(
        self,
        *,
        low_action: int,
        high_action: int,
        low_threshold: int,
        high_threshold: int,
    ) -> None:
        """Apply and publish one confirmed analogue-input 1 field change."""

        if not self.device.supports_analogue_input_1_validation:
            raise AnalogueInput1ValidationNotSupportedError(
                "analogue input 1 validation is not enabled for this model, "
                "firmware, and hardware"
            )
        if (
            self.data is None
            or not self.last_update_success
            or not self.device.global_settings_write_ready
        ):
            raise AnalogueInput1ValidationUnavailableError(
                "Current global settings are unavailable; wait for a successful poll"
            )
        try:
            settings = await self.device.set_analogue_input_1_validation(
                self._ble_device(),
                low_action=low_action,
                high_action=high_action,
                low_threshold=low_threshold,
                high_threshold=high_threshold,
            )
        except GlobalSettingsUnavailableError as err:
            raise AnalogueInput1ValidationUnavailableError(str(err)) from err
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
                f"Unable to update Multihome analogue input 1 validation field: {err}"
            ) from err
        self.async_set_updated_data(replace(self.data, global_settings=settings))


    async def async_set_analogue_input_2_validation(
        self,
        *,
        low_action: int,
        high_action: int,
        low_threshold: int,
        high_threshold: int,
    ) -> None:
        """Apply and publish one confirmed analogue-input 2 field change."""

        if not self.device.supports_analogue_input_2_validation:
            raise AnalogueInput2ValidationNotSupportedError(
                "analogue input 2 validation is not enabled for this model, "
                "firmware, and hardware"
            )
        if (
            self.data is None
            or not self.last_update_success
            or not self.device.global_settings_write_ready
        ):
            raise AnalogueInput2ValidationUnavailableError(
                "Current global settings are unavailable; wait for a successful poll"
            )
        try:
            settings = await self.device.set_analogue_input_2_validation(
                self._ble_device(),
                low_action=low_action,
                high_action=high_action,
                low_threshold=low_threshold,
                high_threshold=high_threshold,
            )
        except GlobalSettingsUnavailableError as err:
            raise AnalogueInput2ValidationUnavailableError(str(err)) from err
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
                f"Unable to update Multihome analogue input 2 validation field: {err}"
            ) from err
        self.async_set_updated_data(replace(self.data, global_settings=settings))

    async def async_set_digital_input_validation(
        self,
        *,
        digital_input_1_action: int,
        digital_input_2_action: int,
    ) -> None:
        """Apply and publish one confirmed digital-input action change."""

        if not self.device.supports_digital_input_validation:
            raise DigitalInputValidationNotSupportedError(
                "digital input validation is not enabled for this model, firmware, "
                "and hardware"
            )
        if (
            self.data is None
            or not self.last_update_success
            or not self.device.global_settings_write_ready
        ):
            raise DigitalInputValidationUnavailableError(
                "Current global settings are unavailable; wait for a successful poll"
            )
        try:
            settings = await self.device.set_digital_input_validation(
                self._ble_device(),
                digital_input_1_action=digital_input_1_action,
                digital_input_2_action=digital_input_2_action,
            )
        except GlobalSettingsUnavailableError as err:
            raise DigitalInputValidationUnavailableError(str(err)) from err
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
                f"Unable to update Multihome digital input validation field: {err}"
            ) from err
        self.async_set_updated_data(replace(self.data, global_settings=settings))

    async def async_set_low_temperature_protection_validation(
        self,
        *,
        enabled: bool,
    ) -> None:
        """Apply and publish one confirmed field-16 validation change."""

        if not self.device.supports_low_temperature_protection_validation:
            raise LowTemperatureProtectionValidationNotSupportedError(
                "low-temperature protection validation is not enabled for this "
                "model, firmware, and hardware"
            )
        if (
            self.data is None
            or not self.last_update_success
            or not self.device.global_settings_write_ready
        ):
            raise LowTemperatureProtectionValidationUnavailableError(
                "Current global settings are unavailable; wait for a successful poll"
            )
        try:
            settings = (
                await self.device.set_low_temperature_protection_validation(
                    self._ble_device(), enabled=enabled
                )
            )
        except GlobalSettingsUnavailableError as err:
            raise LowTemperatureProtectionValidationUnavailableError(str(err)) from err
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
                "Unable to update Multihome low-temperature protection: "
                f"{err}"
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
        offset = VentaxiaMultihomeCoordinator._silent_hours_utc_offset_seconds(self)
        try:
            if record is None:
                slots = await self.device.delete_silent_hour(self._ble_device(), index)
            else:
                device_record = local_record_to_device(record, offset)
                slots = await self.device.set_silent_hour(
                    self._ble_device(), index, device_record
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
        localized_slots = device_slots_to_local(slots, offset)
        self.async_set_updated_data(replace(self.data, silent_hours=localized_slots))

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
