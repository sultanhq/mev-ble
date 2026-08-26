"""Device abstraction for a Vent-Axia Multihome BLE unit."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

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
    Operation,
    PacketType,
    ProtocolError,
    ProtocolPacket,
    SystemStatus,
    ZoneTelemetry,
    decode_packet,
    decode_system_status,
    decode_zone_telemetry,
    encode_cancel_override,
    encode_packet,
    encode_setup_code,
    encode_user_override,
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
        self._connection_lock = asyncio.Lock()
        self._transaction_lock = asyncio.Lock()
        self.device_info = MultihomeDeviceInfo()

    @property
    def connected(self) -> bool:
        """Return whether the current client is connected."""

        return bool(self._client and self._client.is_connected)

    @property
    def transport_name(self) -> str | None:
        """Return the selected transport name."""

        return self._transport.name if self._transport else None

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
                "setup code rejected; confirm pairing/setup mode and the displayed code"
            )
        self._authenticated = True

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
            decoded = raw.rstrip(b"\x00").decode("utf-8", errors="replace").strip()
            values[field] = decoded or None
        return MultihomeDeviceInfo(**values)

    async def update(self, ble_device: BLEDevice) -> MultihomeData:
        """Read zone telemetry and system status."""

        await self.connect(ble_device)
        zone_packet = await self._request(
            PacketType.ZONE_VIEW_ROW, Operation.DATA_REQUEST, b"\x00"
        )
        system_packet = await self._request(
            PacketType.SYSTEM_STATUS, Operation.DATA_REQUEST
        )
        return MultihomeData(
            zone=decode_zone_telemetry(zone_packet.payload),
            system=decode_system_status(system_packet.payload),
            last_successful_update=datetime.now(UTC),
        )

    async def set_override(
        self,
        ble_device: BLEDevice,
        preset: AirflowPreset,
        duration_seconds: int,
    ) -> None:
        """Set one documented timed airflow override."""

        await self.connect(ble_device)
        await self._request(
            PacketType.USER_OVERRIDE,
            Operation.DATA_REQUEST,
            encode_user_override(preset, duration_seconds),
        )

    async def cancel_override(self, ble_device: BLEDevice) -> None:
        """Send the documented cancel-override command."""

        await self.connect(ble_device)
        await self._request(
            PacketType.USER_OVERRIDE,
            Operation.DATA_REQUEST,
            encode_cancel_override(),
        )

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
