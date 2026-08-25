"""Transport-level request/response tests."""

from __future__ import annotations

from collections import deque

import pytest

from custom_components.ventaxia_multihome.bluetooth import (
    FragmentedTransport,
    TransactionTimeoutError,
    WholePacketTransport,
)
from custom_components.ventaxia_multihome.const import (
    FRAGMENT_CHARACTERISTIC_UUID,
    WHOLE_PACKET_CHARACTERISTIC_UUID,
)
from custom_components.ventaxia_multihome.protocol import (
    FRAGMENT_CANCEL,
    WHOLE_PACKET_ACK,
    WHOLE_PACKET_CANCEL,
    Operation,
    PacketType,
    encode_packet,
    fragment_ack,
    fragment_packet,
)


class FakeClient:
    """Queue-backed Bleak client subset."""

    def __init__(self, reads: list[bytes]) -> None:
        self.reads = deque(reads)
        self.writes: list[tuple[str, bytes, bool | None]] = []
        self.is_connected = True
        self.services = None

    async def read_gatt_char(self, uuid: str) -> bytearray:
        return bytearray(self.reads.popleft() if self.reads else bytes(20))

    async def write_gatt_char(
        self, uuid: str, data: bytes, response: bool | None = None
    ) -> None:
        self.writes.append((uuid, bytes(data), response))

    async def disconnect(self) -> bool:
        self.is_connected = False
        return True


@pytest.mark.asyncio
async def test_whole_packet_zero_then_response() -> None:
    """All-zero reads are not ready and a valid response is acknowledged."""

    # Arrange - queue a not-ready value and then a valid response.
    request = encode_packet(
        PacketType.SYSTEM_STATUS, Operation.DATA_REQUEST, timestamp=1
    )
    response = encode_packet(PacketType.SYSTEM_STATUS, Operation.RESPONSE, timestamp=2)
    client = FakeClient([bytes(20), response])
    transport = WholePacketTransport(client, timeout=0.1, poll_interval=0)

    # Act - perform one request.
    result = await transport.request(request)

    # Assert - request and documented acknowledgement were written without response.
    assert result == response
    assert client.writes == [
        (WHOLE_PACKET_CHARACTERISTIC_UUID, request, False),
        (WHOLE_PACKET_CHARACTERISTIC_UUID, WHOLE_PACKET_ACK, False),
    ]


@pytest.mark.asyncio
async def test_whole_packet_malformed_then_valid() -> None:
    """A malformed nonzero whole packet is acknowledged and polling continues."""

    # Arrange - queue a bad checksum followed by a good response.
    request = encode_packet(
        PacketType.SYSTEM_STATUS, Operation.DATA_REQUEST, timestamp=1
    )
    response = encode_packet(PacketType.SYSTEM_STATUS, Operation.RESPONSE, timestamp=2)
    malformed = bytes([response[0] ^ 1]) + response[1:]
    client = FakeClient([malformed, response])

    # Act - perform one request.
    result = await WholePacketTransport(client, timeout=0.1, poll_interval=0).request(
        request
    )

    # Assert - both nonzero reads were acknowledged and the valid one was returned.
    assert result == response
    assert [write[1] for write in client.writes].count(WHOLE_PACKET_ACK) == 2


@pytest.mark.asyncio
async def test_whole_packet_timeout_cancels() -> None:
    """A whole-packet timeout sends the documented cancellation."""

    # Arrange - create a client that only returns not-ready zeros.
    request = encode_packet(
        PacketType.SYSTEM_STATUS, Operation.DATA_REQUEST, timestamp=1
    )
    client = FakeClient([])

    # Act / Assert - timeout is typed and cancellation follows.
    with pytest.raises(TransactionTimeoutError):
        await WholePacketTransport(client, timeout=0.005, poll_interval=0.001).request(
            request
        )
    assert client.writes[-1] == (
        WHOLE_PACKET_CHARACTERISTIC_UUID,
        WHOLE_PACKET_CANCEL,
        False,
    )


@pytest.mark.asyncio
async def test_fragmented_transport_round_trip() -> None:
    """Legacy transport validates per-frame ACKs and reassembles a response."""

    # Arrange - a one-frame request and two-frame response.
    request = encode_packet(
        PacketType.SYSTEM_STATUS, Operation.DATA_REQUEST, timestamp=1
    )
    response = encode_packet(
        PacketType.SYSTEM_STATUS, Operation.RESPONSE, bytes(range(18)), timestamp=2
    )
    response_frames = fragment_packet(response)
    client = FakeClient([fragment_ack(1), *response_frames])
    transport = FragmentedTransport(client, timeout=0.1, poll_interval=0, write_delay=0)

    # Act - transact through fragmented framing.
    result = await transport.request(request)

    # Assert - response is reassembled and every received fragment acknowledged.
    assert result == response
    assert client.writes[0] == (
        FRAGMENT_CHARACTERISTIC_UUID,
        fragment_packet(request)[0],
        False,
    )
    assert [write[1] for write in client.writes[1:]] == [
        fragment_ack(1),
        fragment_ack(2),
    ]


@pytest.mark.asyncio
async def test_fragmented_timeout_cancels() -> None:
    """A missing fragment acknowledgement cancels the legacy transaction."""

    # Arrange - queue no acknowledgements.
    request = encode_packet(
        PacketType.SYSTEM_STATUS, Operation.DATA_REQUEST, timestamp=1
    )
    client = FakeClient([])

    # Act / Assert - timeout sends a 20-byte channel-FF cancellation frame.
    with pytest.raises(TransactionTimeoutError):
        await FragmentedTransport(
            client, timeout=0.005, poll_interval=0.001, write_delay=0
        ).request(request)
    assert client.writes[-1] == (
        FRAGMENT_CHARACTERISTIC_UUID,
        FRAGMENT_CANCEL,
        False,
    )
