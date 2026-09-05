"""Device abstraction for a Vent-Axia Multihome BLE unit."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from math import ceil
from time import monotonic
from typing import TYPE_CHECKING

from .bluetooth import (
    BluetoothClient,
    FragmentedTransport,
    ProtocolTransport,
    WholePacketTransport,
    async_establish_connection,
)
from .capabilities import (
    AIRFLOW_FIELDS,
    BOOST_MINIMUM_FIELDS,
    COMFORT_MODE_FIELDS,
    DELAY_OVERRUN_FIELDS,
    HUMIDITY_RESPONSE_FIELDS,
    LOW_TEMPERATURE_PROTECTION_FIELDS,
    SENSOR_THRESHOLD_FIELDS,
    TEMPERATURE_VALIDATION_FIELDS,
    installer_configurable_fields,
    installer_validation_candidate_fields,
    installer_writable_fields,
    model_capability,
)
from .const import (
    DEVICE_INFO_CHARACTERISTICS,
    FRAGMENT_CHARACTERISTIC_UUID,
    PIN_CHARACTERISTIC_UUID,
    PIN_CONFIRM_CHARACTERISTIC_UUID,
    WHOLE_PACKET_CHARACTERISTIC_UUID,
)
from .protocol import (
    MAX_BOOST_MINIMUM,
    MIN_BOOST_MINIMUM,
    SILENT_HOUR_SLOT_COUNT,
    AirflowPreset,
    DeviceType,
    FanState,
    GlobalSettingField,
    GlobalSettings,
    Operation,
    PacketType,
    ProtocolError,
    ProtocolPacket,
    SilentHour,
    SilentHourSlot,
    SystemStatus,
    ZoneTelemetry,
    decode_device_view_header,
    decode_device_view_row,
    decode_global_settings,
    decode_packet,
    decode_silent_hour_slot,
    decode_system_status,
    decode_zone_telemetry,
    encode_cancel_override,
    encode_co2_calibration,
    encode_global_setting_update,
    encode_packet,
    encode_setup_code,
    encode_silent_hour_delete,
    encode_silent_hour_request,
    encode_silent_hour_update,
    encode_user_override,
    global_settings_after_update,
    plan_airflow_profile_updates,
    plan_comfort_mode_update,
    plan_delay_overrun_updates,
    plan_humidity_response_updates,
    plan_low_temperature_protection_validation_update,
    plan_sensor_threshold_updates,
    plan_temperature_validation_update,
    preserve_unknown_silent_hour_slot,
)

if TYPE_CHECKING:
    from bleak.backends.device import BLEDevice

_LOGGER = logging.getLogger(__name__)

ClientFactory = Callable[
    ["BLEDevice", str, Callable[[BluetoothClient], None]],
    Awaitable[BluetoothClient],
]


class DeviceError(Exception):
    """Base device error."""


class SetupCodeRejectedError(DeviceError):
    """Raised when the unit rejects the application setup code."""


class MissingCharacteristicError(DeviceError):
    """Raised when required protocol characteristics are unavailable."""


class CalibrationTargetDiscoveryError(DeviceError):
    """Raised when calibration routing fails before its write is attempted."""


class CalibrationWriteUncertainError(DeviceError):
    """Raised when a calibration write may have reached the unit."""


class GlobalSettingsUnavailableError(DeviceError):
    """Raised when no current settings record is available for a safe write."""


class GlobalSettingUpdateError(DeviceError):
    """Raised when a settings write cannot be confirmed by exact readback."""


class SilentHoursUnavailableError(DeviceError):
    """Raised when no complete schedule table is available for a safe write."""


class SilentHourUpdateError(DeviceError):
    """Raised when a schedule mutation cannot be confirmed by exact readback."""


@dataclass(frozen=True, slots=True)
class MultihomeDeviceInfo:
    """Standard BLE Device Information values."""

    model: str | None = None
    serial: str | None = None
    firmware: str | None = None
    hardware: str | None = None
    software: str | None = None
    manufacturer: str | None = None


@dataclass(frozen=True, slots=True)
class MultihomeData:
    """One complete coordinator update."""

    zone: ZoneTelemetry
    system: SystemStatus
    global_settings: GlobalSettings
    last_successful_update: datetime
    silent_hours: tuple[SilentHourSlot, ...] = ()

    @property
    def fault_mask(self) -> int:
        """Return the combined zone/system fault flags."""

        return self.zone.fault_mask | self.system.fault_mask


class MultihomeDevice:
    """Connected device facade with serialized protocol transactions."""

    def __init__(
        self,
        address: str,
        name: str,
        setup_code: int,
        *,
        client_factory: ClientFactory = async_establish_connection,
    ) -> None:
        self.address = address
        self.name = name
        self._setup_code = setup_code
        self._client_factory = client_factory
        self._client: BluetoothClient | None = None
        self._transport: ProtocolTransport | None = None
        self._authenticated = False
        self._operation_lock = asyncio.Lock()
        self._connection_lock = asyncio.Lock()
        self._transaction_lock = asyncio.Lock()
        self._override_deadline: float | None = None
        self._override_preset: AirflowPreset | None = None
        self.last_calibration_target: int | None = None
        self.last_calibration_target_scan: list[tuple[int, int, int]] = []
        self.last_calibration_device_table_version: int | None = None
        self._confirmed_global_settings: GlobalSettings | None = None
        self._global_settings_write_ready = False
        self._confirmed_silent_hours: tuple[SilentHourSlot, ...] | None = None
        self._silent_hours_write_ready = False
        self.device_info = MultihomeDeviceInfo()

    @property
    def connected(self) -> bool:
        """Return whether the current client is connected."""

        return bool(self._client and self._client.is_connected)

    @property
    def transport_name(self) -> str | None:
        """Return the selected transport name."""

        return self._transport.name if self._transport else None

    @property
    def model_number(self) -> int | None:
        """Return the official MEV model number when it is recognised."""

        if not (model := self.device_info.model):
            return None
        try:
            return int(model)
        except ValueError:
            return None

    @property
    def supports_internal_co2_calibration(self) -> bool:
        """Return whether the recovered model map identifies an internal CO2 sensor."""

        capability = model_capability(self.model_number)
        return bool(capability and capability.internal_co2)

    @property
    def writable_installer_fields(self) -> frozenset[GlobalSettingField]:
        """Return fields validated for this exact device identity."""

        return installer_writable_fields(
            self.model_number,
            self.device_info.firmware,
            self.device_info.hardware,
        )

    @property
    def validation_candidate_installer_fields(self) -> frozenset[GlobalSettingField]:
        """Return prerelease fields gated to the exact validation identity."""

        return installer_validation_candidate_fields(
            self.model_number,
            self.device_info.firmware,
            self.device_info.hardware,
        )

    @property
    def configurable_installer_fields(self) -> frozenset[GlobalSettingField]:
        """Return stable plus guarded-prerelease fields for this identity."""

        return installer_configurable_fields(
            self.model_number,
            self.device_info.firmware,
            self.device_info.hardware,
        )

    @property
    def supports_global_airflow_configuration(self) -> bool:
        """Return whether all four airflow writes are validated for this identity."""

        return AIRFLOW_FIELDS <= self.writable_installer_fields

    @property
    def supports_sensor_threshold_configuration(self) -> bool:
        """Return whether CO2/humidity writes are validated for this identity."""

        return SENSOR_THRESHOLD_FIELDS <= self.writable_installer_fields

    @property
    def supports_humidity_response_configuration(self) -> bool:
        """Return whether humidity-response writes are physically validated."""

        return HUMIDITY_RESPONSE_FIELDS <= self.writable_installer_fields

    @property
    def supports_boost_minimum_configuration(self) -> bool:
        """Return whether guarded Boost minimum writes are validated."""

        return BOOST_MINIMUM_FIELDS <= self.writable_installer_fields

    @property
    def supports_comfort_mode_configuration(self) -> bool:
        """Return whether Comfort mode writes are physically validated."""

        return COMFORT_MODE_FIELDS <= self.writable_installer_fields

    @property
    def supports_delay_overrun_configuration(self) -> bool:
        """Return whether the physically validated LS timers are writable."""

        return DELAY_OVERRUN_FIELDS <= self.writable_installer_fields

    @property
    def supports_temperature_threshold_validation(self) -> bool:
        """Return whether the storage-validated temperature fields are writable."""

        return TEMPERATURE_VALIDATION_FIELDS <= self.writable_installer_fields

    @property
    def supports_low_temperature_protection_validation(self) -> bool:
        """Return whether guarded field-16 configuration is validated."""

        return LOW_TEMPERATURE_PROTECTION_FIELDS <= self.writable_installer_fields

    @property
    def supports_silent_hours_management(self) -> bool:
        """Return whether silent-hours writes are enabled for this stable model."""

        return self.model_number == 10

    @property
    def confirmed_global_settings(self) -> GlobalSettings | None:
        """Return the last complete settings record confirmed by a read."""

        return self._confirmed_global_settings

    @property
    def global_settings_write_ready(self) -> bool:
        """Return whether a current record permits a guarded field write."""

        return self._global_settings_write_ready

    @property
    def confirmed_silent_hours(self) -> tuple[SilentHourSlot, ...] | None:
        """Return the last complete six-slot table confirmed by reads."""

        return self._confirmed_silent_hours

    @property
    def silent_hours_write_ready(self) -> bool:
        """Return whether a complete current table permits a guarded write."""

        return self._silent_hours_write_ready

    async def connect(self, ble_device: BLEDevice) -> None:
        """Connect, authenticate, select a transport, and read device info."""

        async with self._connection_lock:
            if self.connected and self._authenticated and self._transport:
                return
            await self._disconnect_unlocked()
            _LOGGER.debug("Connecting to discovered Multihome device %s", self.address)
            client = await self._client_factory(
                ble_device, self.name, self._handle_disconnect
            )
            self._client = client
            try:
                self._transport = self._select_transport(client)
                _LOGGER.debug(
                    "Selected %s transport for %s",
                    self._transport.name,
                    self.address,
                )
                await self.authenticate()
                self.device_info = await self.read_device_information()
            except BaseException:
                await self._disconnect_unlocked()
                raise

    async def authenticate(self) -> None:
        """Perform the documented application-level setup-code exchange."""

        client = self._require_client()
        # Do not log the encoded setup-code payload.
        await client.write_gatt_char(
            PIN_CHARACTERISTIC_UUID,
            encode_setup_code(self._setup_code),
            response=True,
        )
        confirmation = bytes(
            await client.read_gatt_char(PIN_CONFIRM_CHARACTERISTIC_UUID)
        )
        if not confirmation or confirmation[0] == 0:
            raise SetupCodeRejectedError(
                "stored setup code was rejected; pair the unit again"
            )
        self._authenticated = True

    async def pair(self, ble_device: BLEDevice) -> int:
        """Read and confirm the code exposed during physical pairing mode."""

        async with self._connection_lock:
            await self._disconnect_unlocked()
            _LOGGER.debug("Pairing with discovered Multihome device %s", self.address)
            client = await self._client_factory(
                ble_device, self.name, self._handle_disconnect
            )
            self._client = client
            try:
                self._transport = self._select_transport(client)
                raw_code = bytes(await client.read_gatt_char(PIN_CHARACTERISTIC_UUID))
                if len(raw_code) != 4:
                    raise DeviceError(
                        "pairing returned an invalid application-code payload"
                    )
                setup_code = int.from_bytes(raw_code, "little")
                if not setup_code:
                    raise SetupCodeRejectedError(
                        "the unit did not expose an application code; "
                        "confirm physical pairing mode"
                    )
                # Confirm the device-provided value; never log this payload.
                await client.write_gatt_char(
                    PIN_CHARACTERISTIC_UUID,
                    raw_code,
                    response=True,
                )
                confirmation = bytes(
                    await client.read_gatt_char(PIN_CONFIRM_CHARACTERISTIC_UUID)
                )
                if not confirmation or confirmation[0] == 0:
                    raise SetupCodeRejectedError(
                        "the unit rejected its application code"
                    )
                self._setup_code = setup_code
                self._authenticated = True
                self.device_info = await self.read_device_information()
                return setup_code
            except BaseException:
                await self._disconnect_unlocked()
                raise

    async def read_device_information(self) -> MultihomeDeviceInfo:
        """Read all available standard Device Information characteristics."""

        client = self._require_client()
        values: dict[str, str | None] = {
            field: None for field in DEVICE_INFO_CHARACTERISTICS
        }
        for field, characteristic_uuid in DEVICE_INFO_CHARACTERISTICS.items():
            if not self._has_characteristic(client, characteristic_uuid):
                continue
            try:
                raw = bytes(await client.read_gatt_char(characteristic_uuid))
            except Exception as err:  # Device information is optional.
                _LOGGER.debug(
                    "Could not read device information field %s: %s", field, err
                )
                continue
            if field == "model" and len(raw) == 1 and raw[0]:
                decoded = str(raw[0])
            else:
                decoded = raw.rstrip(b"\x00").decode("utf-8", errors="replace").strip()
            values[field] = decoded or None
        return MultihomeDeviceInfo(**values)

    async def update(self, ble_device: BLEDevice) -> MultihomeData:
        """Read zone telemetry and system status."""

        async with self._operation_lock:
            await self.connect(ble_device)
            return self._reconcile_override_remaining(await self._read_data())

    async def set_override(
        self,
        ble_device: BLEDevice,
        preset: AirflowPreset,
        duration_seconds: int,
    ) -> MultihomeData:
        """Set one documented timed override and read back fresh telemetry."""

        async with self._operation_lock:
            await self.connect(ble_device)
            command_started = monotonic()
            await self._send(
                PacketType.USER_OVERRIDE,
                Operation.DATA_REQUEST,
                encode_user_override(preset, duration_seconds),
            )
            self._override_deadline = command_started + duration_seconds
            self._override_preset = preset
            return self._reconcile_override_remaining(await self._read_data())

    async def cancel_override(self, ble_device: BLEDevice) -> MultihomeData:
        """Cancel the active override and read back fresh telemetry."""

        async with self._operation_lock:
            await self.connect(ble_device)
            await self._send(
                PacketType.USER_OVERRIDE,
                Operation.DATA_REQUEST,
                encode_cancel_override(),
            )
            self._clear_local_override()
            return self._reconcile_override_remaining(await self._read_data())

    async def calibrate_internal_co2(
        self,
        ble_device: BLEDevice,
        reference_ppm: int,
    ) -> None:
        """Start calibration of the validated internal MEV CO2 target."""

        if not self.supports_internal_co2_calibration:
            raise DeviceError(
                "internal CO2 calibration is not validated for this model"
            )
        async with self._operation_lock:
            self.last_calibration_target = None
            self.last_calibration_target_scan = []
            self.last_calibration_device_table_version = None
            try:
                await self.connect(ble_device)
                target = await self._find_internal_co2_target()
                payload = encode_co2_calibration(
                    reference_ppm,
                    automatic_enabled=False,
                    start_forced_calibration=True,
                )
            except Exception as err:
                raise CalibrationTargetDiscoveryError(str(err)) from err

            try:
                await self._send(
                    PacketType.CO2_CALIBRATION,
                    Operation.UPDATE,
                    payload,
                    target=target,
                )
            except Exception as err:
                raise CalibrationWriteUncertainError(str(err)) from err

    async def set_global_setting(
        self,
        ble_device: BLEDevice,
        field: GlobalSettingField | int,
        value: int | bool,
    ) -> GlobalSettings:
        """Update one validated field and accept only an exact fresh readback."""

        if (
            isinstance(field, bool)
            or not isinstance(field, int)
            or field not in self.configurable_installer_fields
        ):
            raise DeviceError(
                "global setting is not validated for this model, firmware, and hardware"
            )
        async with self._operation_lock:
            await self.connect(ble_device)
            return await self._set_global_setting_locked(field, value)

    async def set_sensor_thresholds(
        self,
        ble_device: BLEDevice,
        *,
        humidity: int,
        co2_boost: int,
        co2_purge: int,
    ) -> GlobalSettings:
        """Apply guarded sensor thresholds with exact readback after each field."""

        if not self.supports_sensor_threshold_configuration:
            raise DeviceError(
                "sensor-threshold configuration is not enabled for this model, "
                "firmware, and hardware"
            )
        async with self._operation_lock:
            await self.connect(ble_device)
            confirmed = self._confirmed_global_settings
            if confirmed is None or not self._global_settings_write_ready:
                raise GlobalSettingsUnavailableError(
                    "global settings must be read successfully before an update"
                )
            plan = plan_sensor_threshold_updates(
                confirmed,
                humidity=humidity,
                co2_boost=co2_boost,
                co2_purge=co2_purge,
            )
            result = confirmed
            for field, value in plan:
                result = await self._set_global_setting_locked(field, value)
            return result

    async def set_humidity_response(
        self,
        ble_device: BLEDevice,
        *,
        rapid: bool,
        ambient: bool,
    ) -> GlobalSettings:
        """Apply guarded humidity-response flags with exact per-field readback."""

        if not self.supports_humidity_response_configuration:
            raise DeviceError(
                "humidity-response configuration is not enabled for this model, "
                "firmware, and hardware"
            )
        async with self._operation_lock:
            await self.connect(ble_device)
            confirmed = self._confirmed_global_settings
            if confirmed is None or not self._global_settings_write_ready:
                raise GlobalSettingsUnavailableError(
                    "global settings must be read successfully before an update"
                )
            plan = plan_humidity_response_updates(
                confirmed,
                rapid=rapid,
                ambient=ambient,
            )
            result = confirmed
            for field, value in plan:
                result = await self._set_global_setting_locked(field, value)
            return result

    async def set_boost_minimum(
        self,
        ble_device: BLEDevice,
        *,
        value: int,
    ) -> GlobalSettings:
        """Apply guarded Boost minimum with exact packet-137 readback."""

        if not self.supports_boost_minimum_configuration:
            raise DeviceError(
                "Boost minimum configuration is not enabled for this model, "
                "firmware, and hardware"
            )
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not MIN_BOOST_MINIMUM <= value <= MAX_BOOST_MINIMUM
        ):
            raise ProtocolError(
                "Boost minimum must be an integer between "
                f"{MIN_BOOST_MINIMUM}% and {MAX_BOOST_MINIMUM}%"
            )
        async with self._operation_lock:
            await self.connect(ble_device)
            if (
                self._confirmed_global_settings is None
                or not self._global_settings_write_ready
            ):
                raise GlobalSettingsUnavailableError(
                    "global settings must be read successfully before an update"
                )
            return await self._set_global_setting_locked(
                GlobalSettingField.BOOST_MINIMUM, value
            )

    async def set_comfort_mode(
        self,
        ble_device: BLEDevice,
        *,
        enabled: bool,
    ) -> GlobalSettings:
        """Apply guarded Comfort mode with exact packet-137 readback."""

        if not self.supports_comfort_mode_configuration:
            raise DeviceError(
                "comfort-mode configuration is not enabled for this model, "
                "firmware, and hardware"
            )
        async with self._operation_lock:
            await self.connect(ble_device)
            confirmed = self._confirmed_global_settings
            if confirmed is None or not self._global_settings_write_ready:
                raise GlobalSettingsUnavailableError(
                    "global settings must be read successfully before an update"
                )
            plan = plan_comfort_mode_update(confirmed, enabled=enabled)
            result = confirmed
            for field, value in plan:
                result = await self._set_global_setting_locked(field, value)
            return result

    async def set_delay_overrun(
        self,
        ble_device: BLEDevice,
        *,
        delay_enabled: bool,
        delay_minutes: int,
        overrun_enabled: bool,
        overrun_minutes: int,
    ) -> GlobalSettings:
        """Apply guarded paired LS timers with exact per-field readback."""

        if not self.supports_delay_overrun_configuration:
            raise DeviceError(
                "delay/overrun configuration is not enabled for this model, "
                "firmware, and hardware"
            )
        async with self._operation_lock:
            await self.connect(ble_device)
            confirmed = self._confirmed_global_settings
            if confirmed is None or not self._global_settings_write_ready:
                raise GlobalSettingsUnavailableError(
                    "global settings must be read successfully before an update"
                )
            if (
                confirmed.delay_enabled is None
                or delay_enabled != confirmed.delay_enabled
            ):
                raise DeviceError(
                    "Delay On enable is read-only after field 7 failed physical "
                    "readback validation"
                )
            plan = plan_delay_overrun_updates(
                confirmed,
                delay_enabled=delay_enabled,
                delay_minutes=delay_minutes,
                overrun_enabled=overrun_enabled,
                overrun_minutes=overrun_minutes,
            )
            result = confirmed
            for field, value in plan:
                result = await self._set_global_setting_locked(field, value)
            return result

    async def set_temperature_threshold_validation(
        self,
        ble_device: BLEDevice,
        *,
        low_action: int,
        high_action: int,
        low_threshold: int,
        high_threshold: int,
    ) -> GlobalSettings:
        """Apply one guarded temperature-field validation write."""

        if not self.supports_temperature_threshold_validation:
            raise DeviceError(
                "temperature-threshold validation is not enabled for this model, "
                "firmware, and hardware"
            )
        confirmed = self._confirmed_global_settings
        if confirmed is None or not self._global_settings_write_ready:
            raise GlobalSettingsUnavailableError(
                "global settings must be read successfully before an update"
            )
        plan_temperature_validation_update(
            confirmed,
            low_action=low_action,
            high_action=high_action,
            low_threshold=low_threshold,
            high_threshold=high_threshold,
        )
        async with self._operation_lock:
            await self.connect(ble_device)
            confirmed = self._confirmed_global_settings
            if confirmed is None or not self._global_settings_write_ready:
                raise GlobalSettingsUnavailableError(
                    "global settings must be read successfully before an update"
                )
            fresh = decode_global_settings(
                (
                    await self._request(
                        PacketType.GLOBAL_DATA,
                        Operation.DATA_REQUEST,
                    )
                ).payload
            )
            if fresh.raw_record != confirmed.raw_record:
                self._global_settings_write_ready = False
                raise GlobalSettingUpdateError(
                    "global settings changed before the temperature validation "
                    "write; no update was sent and the last confirmed snapshot "
                    "was retained"
                )
            field, value = plan_temperature_validation_update(
                fresh,
                low_action=low_action,
                high_action=high_action,
                low_threshold=low_threshold,
                high_threshold=high_threshold,
            )
            return await self._set_global_setting_locked(field, value)

    async def set_low_temperature_protection_validation(
        self,
        ble_device: BLEDevice,
        *,
        enabled: bool,
    ) -> GlobalSettings:
        """Apply one guarded field-16 validation write."""

        if not self.supports_low_temperature_protection_validation:
            raise DeviceError(
                "low-temperature protection validation is not enabled for this "
                "model, firmware, and hardware"
            )
        confirmed = self._confirmed_global_settings
        if confirmed is None or not self._global_settings_write_ready:
            raise GlobalSettingsUnavailableError(
                "global settings must be read successfully before an update"
            )
        plan_low_temperature_protection_validation_update(
            confirmed, enabled=enabled
        )
        async with self._operation_lock:
            await self.connect(ble_device)
            confirmed = self._confirmed_global_settings
            if confirmed is None or not self._global_settings_write_ready:
                raise GlobalSettingsUnavailableError(
                    "global settings must be read successfully before an update"
                )
            fresh = decode_global_settings(
                (
                    await self._request(
                        PacketType.GLOBAL_DATA,
                        Operation.DATA_REQUEST,
                    )
                ).payload
            )
            if fresh.raw_record != confirmed.raw_record:
                self._global_settings_write_ready = False
                raise GlobalSettingUpdateError(
                    "global settings changed before the low-temperature protection "
                    "validation write; no update was sent and the last confirmed "
                    "snapshot was retained"
                )
            field, value = plan_low_temperature_protection_validation_update(
                fresh, enabled=enabled
            )
            return await self._set_global_setting_locked(field, value)

    async def set_airflow_profile(
        self,
        ble_device: BLEDevice,
        *,
        low: int,
        normal: int,
        boost: int,
        purge: int,
    ) -> GlobalSettings:
        """Apply a validated four-speed profile without invalid intermediates."""

        if not self.supports_global_airflow_configuration:
            raise DeviceError(
                "global airflow configuration is not validated for this model, "
                "firmware, and hardware"
            )
        async with self._operation_lock:
            await self.connect(ble_device)
            confirmed = self._confirmed_global_settings
            if confirmed is None or not self._global_settings_write_ready:
                raise GlobalSettingsUnavailableError(
                    "global settings must be read successfully before an update"
                )
            plan = plan_airflow_profile_updates(
                confirmed,
                low=low,
                normal=normal,
                boost=boost,
                purge=purge,
            )
            result = confirmed
            for field, value in plan:
                result = await self._set_global_setting_locked(field, value)
            return result

    async def _set_global_setting_locked(
        self,
        field: GlobalSettingField | int,
        value: int | bool,
    ) -> GlobalSettings:
        """Send and confirm one field while the operation lock is held."""

        confirmed = self._confirmed_global_settings
        if confirmed is None or not self._global_settings_write_ready:
            raise GlobalSettingsUnavailableError(
                "global settings must be read successfully before an update"
            )
        expected = global_settings_after_update(confirmed, field, value)
        payload = encode_global_setting_update(field, value)
        try:
            await self._send(
                PacketType.GLOBAL_DATA_FIELD,
                Operation.UPDATE,
                payload,
            )
            response = await self._request(
                PacketType.GLOBAL_DATA,
                Operation.DATA_REQUEST,
            )
            received = decode_global_settings(response.payload)
        except Exception as err:
            self._global_settings_write_ready = False
            raise GlobalSettingUpdateError(
                "global setting update was not confirmed; "
                "the last confirmed snapshot was retained"
            ) from err
        if received.raw_record != expected.raw_record:
            self._global_settings_write_ready = False
            differences = ", ".join(
                f"{offset}:{wanted:02x}->{actual:02x}"
                for offset, (wanted, actual) in enumerate(
                    zip(expected.raw_record, received.raw_record, strict=True)
                )
                if wanted != actual
            )
            raise GlobalSettingUpdateError(
                "global setting readback did not match the requested update "
                f"for field {int(field)}; differing bytes [{differences}]; "
                f"expected={expected.raw_record.hex()}; "
                f"received={received.raw_record.hex()}; "
                "the last confirmed snapshot was retained"
            )
        self._confirmed_global_settings = received
        self._global_settings_write_ready = True
        return received

    async def set_silent_hour(
        self,
        ble_device: BLEDevice,
        index: int,
        record: SilentHour,
    ) -> tuple[SilentHourSlot, ...]:
        """Create or replace one slot and accept only exact full-table readback."""

        if not self.supports_silent_hours_management:
            raise DeviceError("silent-hours management is not validated for this model")
        async with self._operation_lock:
            await self.connect(ble_device)
            return await self._mutate_silent_hour_locked(index, record)

    async def delete_silent_hour(
        self,
        ble_device: BLEDevice,
        index: int,
    ) -> tuple[SilentHourSlot, ...]:
        """Delete one populated slot and confirm the complete table afterward."""

        if not self.supports_silent_hours_management:
            raise DeviceError("silent-hours management is not validated for this model")
        async with self._operation_lock:
            await self.connect(ble_device)
            return await self._mutate_silent_hour_locked(index, None)

    async def _mutate_silent_hour_locked(
        self,
        index: int,
        record: SilentHour | None,
    ) -> tuple[SilentHourSlot, ...]:
        """Mutate and confirm one slot while the operation lock is held."""

        confirmed = self._confirmed_silent_hours
        if confirmed is None or not self._silent_hours_write_ready:
            raise SilentHoursUnavailableError(
                "all six silent-hours slots must be read before an update"
            )
        if not 0 <= index < SILENT_HOUR_SLOT_COUNT:
            raise ProtocolError("silent-hours slot must be 0..5")

        payload = (
            encode_silent_hour_delete(index)
            if record is None
            else encode_silent_hour_update(index, record)
        )
        try:
            await self._send(PacketType.SILENT_HOURS, Operation.UPDATE, payload)
            received = await self._read_silent_hours()
        except Exception as err:
            self._silent_hours_write_ready = False
            raise SilentHourUpdateError(
                "silent-hours change was not confirmed; "
                "the last confirmed table was retained"
            ) from err

        if not all(slot.is_known for slot in received):
            self._silent_hours_write_ready = False
            raise SilentHourUpdateError(
                "silent-hours readback format is unsupported; "
                "the last confirmed table was retained"
            )
        received_record = received[index].record
        matches = (
            received_record is None
            if record is None
            else received_record is not None
            and received_record.raw_record == record.raw_record
        )
        if not matches:
            self._silent_hours_write_ready = False
            raise SilentHourUpdateError(
                "silent-hours readback did not match the requested change; "
                "the last confirmed table was retained"
            )
        self._confirmed_silent_hours = received
        self._silent_hours_write_ready = True
        return received

    async def _read_silent_hours(self) -> tuple[SilentHourSlot, ...]:
        """Read all six indexed slots in deterministic order."""

        slots: list[SilentHourSlot] = []
        for index in range(SILENT_HOUR_SLOT_COUNT):
            response = await self._request(
                PacketType.SILENT_HOURS,
                Operation.DATA_REQUEST,
                encode_silent_hour_request(index),
            )
            try:
                slot = decode_silent_hour_slot(response.payload, expected_index=index)
            except ProtocolError:
                slot = preserve_unknown_silent_hour_slot(index, response.payload)
                _LOGGER.warning(
                    "Preserving unsupported silent-hours response for slot %s: %s",
                    index,
                    response.payload.hex(),
                )
            if slot.index != index:
                raise ProtocolError(
                    f"silent-hours response index {slot.index} did not match {index}"
                )
            slots.append(slot)
        return tuple(slots)

    async def _find_internal_co2_target(self) -> int:
        """Return the internal sensor address used by the official app."""

        header = decode_device_view_header(
            (
                await self._request(
                    PacketType.DEVICE_VIEW_HEADER,
                    Operation.DATA_REQUEST,
                )
            ).payload
        )
        self.last_calibration_device_table_version = header.version
        mev_control_target: int | None = None
        for index in range(header.row_count):
            row = decode_device_view_row(
                (
                    await self._request(
                        PacketType.DEVICE_VIEW_ROW,
                        Operation.DATA_REQUEST,
                        bytes([index]),
                    )
                ).payload
            )
            self.last_calibration_target_scan.append(
                (row.address, row.device_type, row.hardware_type)
            )
            if row.device_type == DeviceType.INTERNAL_CO2_SENSOR:
                self.last_calibration_target = row.address
                return row.address
            if row.device_type == DeviceType.MEV_CONTROL_UNIT and row.address == 0:
                mev_control_target = row.address
        if mev_control_target is not None:
            self.last_calibration_target = mev_control_target
            return mev_control_target
        discovered_types = ", ".join(
            str(device_type) for _, device_type, _ in self.last_calibration_target_scan
        )
        raise DeviceError(
            "the MEV device table has no internal CO2 sensor target "
            f"(version {header.version}, device types: "
            f"{discovered_types or 'none'})"
        )

    async def _read_data(self) -> MultihomeData:
        """Read one coherent telemetry and global-settings snapshot."""

        zone_packet = await self._request(
            PacketType.ZONE_VIEW_ROW, Operation.DATA_REQUEST, b"\x00"
        )
        system_packet = await self._request(
            PacketType.SYSTEM_STATUS, Operation.DATA_REQUEST
        )
        global_settings_packet = await self._request(
            PacketType.GLOBAL_DATA, Operation.DATA_REQUEST
        )
        global_settings = decode_global_settings(global_settings_packet.payload)
        silent_hours = (
            await self._read_silent_hours()
            if self.supports_silent_hours_management
            else ()
        )
        data = MultihomeData(
            zone=decode_zone_telemetry(zone_packet.payload),
            system=decode_system_status(system_packet.payload),
            global_settings=global_settings,
            last_successful_update=datetime.now(UTC),
            silent_hours=silent_hours,
        )
        self._confirmed_global_settings = global_settings
        self._global_settings_write_ready = True
        self._confirmed_silent_hours = (
            silent_hours if self.supports_silent_hours_management else None
        )
        self._silent_hours_write_ready = (
            self.supports_silent_hours_management
            and len(silent_hours) == SILENT_HOUR_SLOT_COUNT
            and all(slot.is_known for slot in silent_hours)
        )
        return data

    def _reconcile_override_remaining(self, data: MultihomeData) -> MultihomeData:
        """Use a local deadline only when active firmware reports a false zero."""

        if data.zone.fan_state != FanState.USER_OVERRIDE:
            self._clear_local_override()
            return data

        reported = data.system.override_remaining
        if reported is not None and reported > 0:
            return data

        if (
            self._override_deadline is None
            or self._override_preset is None
            or data.zone.fan_level != self._override_preset
        ):
            self._clear_local_override()
            system = replace(
                data.system,
                override_remaining=None,
                override_remaining_source="unavailable",
            )
            return replace(data, system=system)

        remaining = max(0, ceil(self._override_deadline - monotonic()))
        system = replace(
            data.system,
            override_remaining=remaining,
            override_remaining_source="estimated",
        )
        return replace(data, system=system)

    def _clear_local_override(self) -> None:
        """Forget the locally commanded deadline and preset."""

        self._override_deadline = None
        self._override_preset = None

    async def disconnect(self) -> None:
        """Disconnect and clear transport/authentication state."""

        async with self._connection_lock:
            await self._disconnect_unlocked()

    async def _request(
        self, packet_type: PacketType, operation: Operation, payload: bytes = b""
    ) -> ProtocolPacket:
        """Serialize a request and validate the response packet type."""

        async with self._transaction_lock:
            if not self._transport or not self.connected or not self._authenticated:
                raise DeviceError("device is not ready for a protocol transaction")
            _LOGGER.debug("Requesting packet type %s", int(packet_type))
            response_bytes = await self._transport.request(
                encode_packet(packet_type, operation, payload)
            )
            response = decode_packet(response_bytes)
            _LOGGER.debug(
                "Received packet type %s with operation 0x%02x",
                response.packet_type,
                response.operation,
            )
            if response.packet_type != packet_type:
                raise ProtocolError(
                    f"unexpected response packet type {response.packet_type}; "
                    f"expected {int(packet_type)}"
                )
            return response

    async def _send(
        self,
        packet_type: PacketType,
        operation: Operation,
        payload: bytes = b"",
        *,
        target: int = 0,
    ) -> None:
        """Serialize a command whose transport acknowledgements indicate acceptance."""

        async with self._transaction_lock:
            if not self._transport or not self.connected or not self._authenticated:
                raise DeviceError("device is not ready for a protocol transaction")
            _LOGGER.debug("Sending packet type %s", int(packet_type))
            await self._transport.send(
                encode_packet(packet_type, operation, payload, target=target)
            )

    def _select_transport(self, client: BluetoothClient) -> ProtocolTransport:
        """Prefer the readable whole-packet characteristic, then legacy framing."""

        whole = self._get_characteristic(client, WHOLE_PACKET_CHARACTERISTIC_UUID)
        if whole is not None and "read" in getattr(whole, "properties", []):
            return WholePacketTransport(client)
        if self._has_characteristic(client, FRAGMENT_CHARACTERISTIC_UUID):
            return FragmentedTransport(client)
        raise MissingCharacteristicError(
            "device exposes neither documented protocol characteristic"
        )

    def _handle_disconnect(self, client: BluetoothClient) -> None:
        """Clear stale state when Bleak reports a disconnect."""

        if client is not self._client:
            return
        _LOGGER.debug("Multihome device %s disconnected", self.address)
        self._client = None
        self._transport = None
        self._authenticated = False
        self._global_settings_write_ready = False
        self._silent_hours_write_ready = False

    async def _disconnect_unlocked(self) -> None:
        client = self._client
        self._client = None
        self._transport = None
        self._authenticated = False
        self._global_settings_write_ready = False
        self._silent_hours_write_ready = False
        if client and client.is_connected:
            with contextlib.suppress(Exception):
                await client.disconnect()

    def _require_client(self) -> BluetoothClient:
        if not self._client or not self._client.is_connected:
            raise DeviceError("device is not connected")
        return self._client

    @staticmethod
    def _get_characteristic(client: BluetoothClient, uuid: str) -> object | None:
        services = client.services
        getter = getattr(services, "get_characteristic", None)
        return getter(uuid) if getter else None

    @classmethod
    def _has_characteristic(cls, client: BluetoothClient, uuid: str) -> bool:
        return cls._get_characteristic(client, uuid) is not None
