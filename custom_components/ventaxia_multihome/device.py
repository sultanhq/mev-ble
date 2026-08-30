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
from typing import TYPE_CHECKING, Final

from .bluetooth import (
    BluetoothClient,
    FragmentedTransport,
    ProtocolTransport,
    WholePacketTransport,
    async_establish_connection,
)
from .const import (
    DEVICE_INFO_CHARACTERISTICS,
    FRAGMENT_CHARACTERISTIC_UUID,
    PIN_CHARACTERISTIC_UUID,
    PIN_CONFIRM_CHARACTERISTIC_UUID,
    WHOLE_PACKET_CHARACTERISTIC_UUID,
)
from .protocol import (
    AirflowPreset,
    DeviceType,
    FanState,
    GlobalSettings,
    Operation,
    PacketType,
    ProtocolError,
    ProtocolPacket,
    SystemStatus,
    ZoneTelemetry,
    decode_device_view_header,
    decode_device_view_row,
    decode_global_settings,
    decode_packet,
    decode_system_status,
    decode_zone_telemetry,
    encode_cancel_override,
    encode_co2_calibration,
    encode_packet,
    encode_setup_code,
    encode_user_override,
)

if TYPE_CHECKING:
    from bleak.backends.device import BLEDevice

_LOGGER = logging.getLogger(__name__)

INTERNAL_CO2_CALIBRATION_MODELS: Final = frozenset({2, 10})

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

        return self.model_number in INTERNAL_CO2_CALIBRATION_MODELS

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
                raw_code = bytes(
                    await client.read_gatt_char(PIN_CHARACTERISTIC_UUID)
                )
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
            if (
                row.device_type == DeviceType.MEV_CONTROL_UNIT
                and row.address == 0
            ):
                mev_control_target = row.address
        if mev_control_target is not None:
            self.last_calibration_target = mev_control_target
            return mev_control_target
        discovered_types = ", ".join(
            str(device_type)
            for _, device_type, _ in self.last_calibration_target_scan
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
        return MultihomeData(
            zone=decode_zone_telemetry(zone_packet.payload),
            system=decode_system_status(system_packet.payload),
            global_settings=decode_global_settings(global_settings_packet.payload),
            last_successful_update=datetime.now(UTC),
        )

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

    async def _disconnect_unlocked(self) -> None:
        client = self._client
        self._client = None
        self._transport = None
        self._authenticated = False
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
