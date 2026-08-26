"""Bluetooth transports and Home Assistant connection helper."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from time import monotonic
from typing import TYPE_CHECKING, Protocol

from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from .const import (
    FRAGMENT_CHARACTERISTIC_UUID,
    WHOLE_PACKET_CHARACTERISTIC_UUID,
)
from .protocol import (
    FRAGMENT_CANCEL,
    FRAGMENT_SIZE,
    WHOLE_PACKET_ACK,
    WHOLE_PACKET_CANCEL,
    ProtocolError,
    decode_packet,
    fragment_ack,
    fragment_packet,
    reassemble_fragments,
    validate_fragment,
)

if TYPE_CHECKING:
    from bleak.backends.characteristic import BleakGATTCharacteristic
    from bleak.backends.device import BLEDevice

_LOGGER = logging.getLogger(__name__)

TRANSACTION_TIMEOUT = 5.0
WHOLE_PACKET_POLL_INTERVAL = 0.05
FRAGMENT_POLL_INTERVAL = 0.05
FRAGMENT_WRITE_DELAY = 0.005


class BluetoothClient(Protocol):
    """Small subset of the Bleak client API used by this integration."""

    @property
    def is_connected(self) -> bool:
        """Return whether the client is connected."""

    @property
    def services(self) -> object:
        """Return the discovered GATT services."""

    async def read_gatt_char(
        self, char_specifier: str | BleakGATTCharacteristic
    ) -> bytearray:
        """Read a GATT characteristic."""

    async def write_gatt_char(
        self,
        char_specifier: str | BleakGATTCharacteristic,
        data: bytes | bytearray,
        response: bool | None = None,
    ) -> None:
        """Write a GATT characteristic."""

    async def disconnect(self) -> bool:
        """Disconnect the client."""


class TransportError(Exception):
    """Base exception raised by a BLE protocol transport."""


class TransactionTimeoutError(TransportError):
    """Raised when a protocol transaction exceeds its deadline."""


class ProtocolTransport:
    """Abstract protocol transport."""

    name = "unknown"

    def __init__(
        self,
        client: BluetoothClient,
        *,
        timeout: float = TRANSACTION_TIMEOUT,
        poll_interval: float = WHOLE_PACKET_POLL_INTERVAL,
    ) -> None:
        self.client = client
        self.timeout = timeout
        self.poll_interval = poll_interval

    async def request(self, packet: bytes) -> bytes:
        """Send one packet and return one valid response packet."""

        raise NotImplementedError

    async def send(self, packet: bytes) -> None:
        """Send one packet without waiting for a protocol response."""

        raise NotImplementedError


class WholePacketTransport(ProtocolTransport):
    """Preferred whole-packet request/read/acknowledge transport."""

    name = "whole_packet"

    async def request(self, packet: bytes) -> bytes:
        """Perform one whole-packet transaction."""

        deadline = monotonic() + self.timeout
        await self.send(packet)
        while monotonic() < deadline:
            value = bytes(
                await self.client.read_gatt_char(WHOLE_PACKET_CHARACTERISTIC_UUID)
            )
            if not value or not any(value):
                await asyncio.sleep(self.poll_interval)
                continue

            await self.client.write_gatt_char(
                WHOLE_PACKET_CHARACTERISTIC_UUID,
                WHOLE_PACKET_ACK,
                response=False,
            )
            try:
                decode_packet(value)
            except ProtocolError as err:
                _LOGGER.debug("Ignoring malformed whole-packet response: %s", err)
                await asyncio.sleep(self.poll_interval)
                continue
            return value

        await self._cancel()
        raise TransactionTimeoutError("whole-packet transaction timed out")

    async def send(self, packet: bytes) -> None:
        """Write one complete packet without requiring a protocol response."""

        await self.client.write_gatt_char(
            WHOLE_PACKET_CHARACTERISTIC_UUID, packet, response=False
        )

    async def _cancel(self) -> None:
        """Cancel an in-progress whole-packet transaction."""

        with contextlib.suppress(Exception):
            await self.client.write_gatt_char(
                WHOLE_PACKET_CHARACTERISTIC_UUID,
                WHOLE_PACKET_CANCEL,
                response=False,
            )


class FragmentedTransport(ProtocolTransport):
    """Legacy 20-byte fragmented request/response transport."""

    name = "fragmented"

    def __init__(
        self,
        client: BluetoothClient,
        *,
        timeout: float = TRANSACTION_TIMEOUT,
        poll_interval: float = FRAGMENT_POLL_INTERVAL,
        write_delay: float = FRAGMENT_WRITE_DELAY,
    ) -> None:
        super().__init__(client, timeout=timeout, poll_interval=poll_interval)
        self.write_delay = write_delay

    async def request(self, packet: bytes) -> bytes:
        """Perform one fragmented transaction with per-frame acknowledgements."""

        deadline = monotonic() + self.timeout
        response_frames: dict[int, bytes] = {}
        response_total = await self._send_frames(
            packet,
            deadline,
            response_frames=response_frames,
        )

        while monotonic() < deadline:
            if response_total is not None and len(response_frames) == response_total:
                ordered = [
                    response_frames[index] for index in range(1, response_total + 1)
                ]
                try:
                    response = reassemble_fragments(ordered)
                    decode_packet(response)
                except ProtocolError as err:
                    _LOGGER.debug("Ignoring malformed fragmented response: %s", err)
                    response_frames.clear()
                    response_total = None
                else:
                    return response

            value = bytes(
                await self.client.read_gatt_char(FRAGMENT_CHARACTERISTIC_UUID)
            )
            if not value or not any(value):
                await asyncio.sleep(self.poll_interval)
                continue
            consumed = await self._consume_response_frame(
                value, response_frames, response_total
            )
            if consumed is not None:
                response_total = consumed
            else:
                await asyncio.sleep(self.poll_interval)

        await self._cancel()
        raise TransactionTimeoutError("fragmented transaction timed out")

    async def send(self, packet: bytes) -> None:
        """Send all fragments and require only their per-frame acknowledgements."""

        await self._send_frames(packet, monotonic() + self.timeout)

    async def _send_frames(
        self,
        packet: bytes,
        deadline: float,
        *,
        response_frames: dict[int, bytes] | None = None,
    ) -> int | None:
        """Write request frames and wait for each documented acknowledgement."""

        response_total: int | None = None
        for sequence, frame in enumerate(fragment_packet(packet), 1):
            await self.client.write_gatt_char(
                FRAGMENT_CHARACTERISTIC_UUID, frame, response=False
            )
            if self.write_delay:
                await asyncio.sleep(self.write_delay)
            while monotonic() < deadline:
                value = bytes(
                    await self.client.read_gatt_char(FRAGMENT_CHARACTERISTIC_UUID)
                )
                if not value or not any(value):
                    await asyncio.sleep(self.poll_interval)
                    continue
                if self._is_ack(value, sequence):
                    break
                if response_frames is not None:
                    consumed = await self._consume_response_frame(
                        value, response_frames, response_total
                    )
                    if consumed is not None:
                        response_total = consumed
                        # A response frame proves the request has been accepted.
                        break
                await asyncio.sleep(self.poll_interval)
            else:
                await self._cancel()
                raise TransactionTimeoutError(
                    f"fragment {sequence} acknowledgement timed out"
                )
        return response_total

    @staticmethod
    def _is_ack(value: bytes, expected_sequence: int) -> bool:
        """Return whether a frame is the expected zero-filled acknowledgement."""

        return (
            len(value) == FRAGMENT_SIZE
            and value[0] == 0
            and value[1] == 0
            and value[2] == expected_sequence
            and not any(value[3:])
        )

    async def _consume_response_frame(
        self,
        value: bytes,
        frames: dict[int, bytes],
        expected_total: int | None,
    ) -> int | None:
        """Validate, acknowledge, and retain one response frame."""

        try:
            sequence, total = validate_fragment(value)
        except ProtocolError as err:
            _LOGGER.debug("Ignoring malformed fragmented frame: %s", err)
            return None
        if expected_total is not None and total != expected_total:
            _LOGGER.debug(
                "Ignoring fragment with changed total: got %s, expected %s",
                total,
                expected_total,
            )
            return None
        await self.client.write_gatt_char(
            FRAGMENT_CHARACTERISTIC_UUID,
            fragment_ack(sequence),
            response=False,
        )
        frames.setdefault(sequence, value)
        return total

    async def _cancel(self) -> None:
        """Cancel an in-progress fragmented transaction."""

        with contextlib.suppress(Exception):
            await self.client.write_gatt_char(
                FRAGMENT_CHARACTERISTIC_UUID,
                FRAGMENT_CANCEL,
                response=False,
            )


async def async_establish_connection(
    ble_device: BLEDevice,
    name: str,
    disconnected_callback: Callable[[BluetoothClient], None],
) -> BluetoothClient:
    """Connect through the HA-provided BLEDevice and its selected adapter.

    The BLEDevice may be backed by a local adapter, ESPHome Bluetooth Proxy, or
    any other connectable controller managed by Home Assistant.
    """

    return await establish_connection(
        BleakClientWithServiceCache,
        ble_device,
        name,
        disconnected_callback=disconnected_callback,
        max_attempts=3,
    )
