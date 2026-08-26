"""Device abstraction and reconnection tests."""

from __future__ import annotations

import asyncio
import struct
from collections import deque
from types import SimpleNamespace

import pytest

from custom_components.ventaxia_multihome.const import (
    PIN_CHARACTERISTIC_UUID,
    PIN_CONFIRM_CHARACTERISTIC_UUID,
    WHOLE_PACKET_CHARACTERISTIC_UUID,
)
from custom_components.ventaxia_multihome.device import (
    MultihomeDevice,
    SetupCodeRejectedError,
)
from custom_components.ventaxia_multihome.protocol import (
    AirflowPreset,
    DataObjectType,
    Operation,
    PacketType,
    decode_packet,
    encode_cancel_override,
    encode_data_object_array,
    encode_packet,
    encode_user_override,
)


class FakeServices:
    """Minimal service collection exposing the whole-packet characteristic."""

    def __init__(self) -> None:
        self._characteristics = {
            WHOLE_PACKET_CHARACTERISTIC_UUID: SimpleNamespace(properties=["read"]),
        }

    def get_characteristic(self, uuid: str):
        return self._characteristics.get(uuid)


class DeviceClient:
    """Bleak client fake with characteristic-specific responses."""

    def __init__(self, protocol_reads: list[bytes]) -> None:
        self.protocol_reads = deque(protocol_reads)
        self.services = FakeServices()
        self.is_connected = True
        self.writes: list[tuple[str, bytes, bool | None]] = []
        self.callback = None

    async def read_gatt_char(self, uuid: str) -> bytearray:
        if uuid == PIN_CONFIRM_CHARACTERISTIC_UUID:
            return bytearray(b"\x01")
        if uuid == WHOLE_PACKET_CHARACTERISTIC_UUID:
            return bytearray(self.protocol_reads.popleft())
        raise AssertionError(f"unexpected read {uuid}")

    async def write_gatt_char(
        self, uuid: str, data: bytes, response: bool | None = None
    ) -> None:
        self.writes.append((uuid, bytes(data), response))

    async def disconnect(self) -> bool:
        self.is_connected = False
        return True


def _responses(*, co2: float = 800.0) -> list[bytes]:
    zone = struct.pack("<BBBHfffI", 1, 3, 2, 1200, 22.0, 55.0, co2, 0)
    status = encode_data_object_array(DataObjectType.RAW, struct.pack("<BHI", 3, 60, 0))
    return [
        encode_packet(PacketType.ZONE_VIEW_ROW, Operation.RESPONSE, zone, timestamp=1),
        encode_packet(
            PacketType.SYSTEM_STATUS, Operation.RESPONSE, status, timestamp=2
        ),
    ]


@pytest.mark.asyncio
async def test_update_authenticates_and_decodes() -> None:
    """A device update authenticates once and reads both telemetry packets."""

    # Arrange - create a connected whole-packet fake.
    client = DeviceClient(_responses())

    async def factory(ble_device, name, callback):
        client.callback = callback
        return client

    device = MultihomeDevice(
        "AA:BB:CC:DD:EE:FF", "MEV", 0x01020304, client_factory=factory
    )

    # Act - perform one complete update.
    result = await device.update(object())

    # Assert - setup-code bytes and decoded telemetry are exact.
    assert (
        PIN_CHARACTERISTIC_UUID,
        bytes.fromhex("04030201"),
        True,
    ) in client.writes
    assert result.zone.temperature == pytest.approx(22.0)
    assert result.system.override_remaining == 60
    assert device.transport_name == "whole_packet"


@pytest.mark.asyncio
async def test_setup_code_rejected() -> None:
    """A zero confirmation is surfaced as a specific setup error."""

    # Arrange - override the confirmation read with a rejection.
    client = DeviceClient([])
    original_read = client.read_gatt_char

    async def read(uuid: str) -> bytearray:
        if uuid == PIN_CONFIRM_CHARACTERISTIC_UUID:
            return bytearray(b"\x00")
        return await original_read(uuid)

    client.read_gatt_char = read

    async def factory(ble_device, name, callback):
        return client

    device = MultihomeDevice("AA", "MEV", 1234, client_factory=factory)

    # Act / Assert - rejection is distinguishable from transport failures.
    with pytest.raises(SetupCodeRejectedError):
        await device.connect(object())
    assert client.is_connected is False


@pytest.mark.asyncio
async def test_pair_reads_and_confirms_internal_code() -> None:
    """Physical pairing reads the MEV code and writes the same bytes back."""

    # Arrange - expose a nonzero little-endian code from the pairing characteristic.
    client = DeviceClient([])
    original_read = client.read_gatt_char

    async def read(uuid: str) -> bytearray:
        if uuid == PIN_CHARACTERISTIC_UUID:
            return bytearray(bytes.fromhex("78563412"))
        return await original_read(uuid)

    client.read_gatt_char = read

    async def factory(ble_device, name, callback):
        return client

    device = MultihomeDevice("AA", "MEV", 0, client_factory=factory)

    # Act - pair while the unit is in physical pairing mode.
    setup_code = await device.pair(object())

    # Assert - the hidden value is confirmed unchanged and returned internally.
    assert (
        PIN_CHARACTERISTIC_UUID,
        bytes.fromhex("78563412"),
        True,
    ) in client.writes
    assert setup_code == 0x12345678


@pytest.mark.asyncio
async def test_pair_rejects_zero_internal_code() -> None:
    """A zero value indicates that the MEV is not in physical pairing mode."""

    # Arrange - expose the zero value returned outside pairing mode.
    client = DeviceClient([])
    original_read = client.read_gatt_char

    async def read(uuid: str) -> bytearray:
        if uuid == PIN_CHARACTERISTIC_UUID:
            return bytearray(bytes(4))
        return await original_read(uuid)

    client.read_gatt_char = read

    async def factory(ble_device, name, callback):
        return client

    device = MultihomeDevice("AA", "MEV", 0, client_factory=factory)

    # Act / Assert - no exposed code is reported as pairing-mode failure.
    with pytest.raises(SetupCodeRejectedError):
        await device.pair(object())
    assert client.is_connected is False


@pytest.mark.asyncio
async def test_reconnects_with_fresh_client_after_disconnect() -> None:
    """A proxy/device disconnect causes the next update to establish a new client."""

    # Arrange - provide one client per connection.
    clients = deque([DeviceClient(_responses()), DeviceClient(_responses())])
    created: list[DeviceClient] = []

    async def factory(ble_device, name, callback):
        client = clients.popleft()
        client.callback = callback
        created.append(client)
        return client

    device = MultihomeDevice("AA", "MEV", 1234, client_factory=factory)
    await device.update(object())
    first = created[0]

    # Act - simulate proxy loss, then allow the regular next poll to recover.
    assert first.callback is not None
    first.is_connected = False
    first.callback(first)
    await device.update(object())

    # Assert - a new client connected and repeated application authentication.
    assert len(created) == 2
    assert any(write[0] == PIN_CHARACTERISTIC_UUID for write in created[1].writes)


@pytest.mark.asyncio
async def test_reconnect_zero_co2_is_unavailable_until_valid_reading() -> None:
    """A transient zero after reconnect never becomes a CO2 measurement."""

    # Arrange - return a valid reading, then zero and recovery after reconnect.
    first = DeviceClient(_responses(co2=800.0))
    second = DeviceClient([*_responses(co2=0.0), *_responses(co2=775.0)])
    clients = deque([first, second])

    async def factory(ble_device, name, callback):
        client = clients.popleft()
        client.callback = callback
        return client

    device = MultihomeDevice("AA", "MEV", 1234, client_factory=factory)
    initial = await device.update(object())
    assert first.callback is not None
    first.is_connected = False
    first.callback(first)

    # Act - poll once with the transient zero and again after sensor recovery.
    transient = await device.update(object())
    recovered = await device.update(object())

    # Assert - zero is unavailable while both genuine readings are preserved.
    assert initial.zone.co2 == pytest.approx(800.0)
    assert transient.zone.co2 is None
    assert recovered.zone.co2 == pytest.approx(775.0)


@pytest.mark.asyncio
async def test_override_controls_use_send_only_packets() -> None:
    """Override and cancel commands do not request an application response."""

    # Arrange - install a transport that records sends and rejects requests.
    client = DeviceClient([])
    sent: list[bytes] = []

    class SendOnlyTransport:
        name = "test"

        async def send(self, packet: bytes) -> None:
            sent.append(packet)

        async def request(self, packet: bytes) -> bytes:
            raise AssertionError("control packets must not request a response")

    device = MultihomeDevice("AA", "MEV", 1234)
    device._client = client
    device._transport = SendOnlyTransport()
    device._authenticated = True

    # Act - apply one boost and then send the independent cancel command.
    await device.set_override(object(), AirflowPreset.BOOST, 90)
    await device.cancel_override(object())

    # Assert - both packet-56 commands use the exact documented payloads.
    override = decode_packet(sent[0])
    cancel = decode_packet(sent[1])
    assert override.packet_type == PacketType.USER_OVERRIDE
    assert override.operation == Operation.DATA_REQUEST
    assert override.payload == encode_user_override(AirflowPreset.BOOST, 90)
    assert cancel.packet_type == PacketType.USER_OVERRIDE
    assert cancel.operation == Operation.DATA_REQUEST
    assert cancel.payload == encode_cancel_override()


@pytest.mark.asyncio
async def test_transactions_are_serialized() -> None:
    """Concurrent controls never overlap protocol sends."""

    # Arrange - install a transport that detects concurrent entry.
    client = DeviceClient([])
    active = 0
    maximum_active = 0

    class SlowTransport:
        name = "test"

        async def send(self, packet: bytes) -> None:
            nonlocal active, maximum_active
            active += 1
            maximum_active = max(maximum_active, active)
            await asyncio.sleep(0)
            active -= 1

    device = MultihomeDevice("AA", "MEV", 1234)
    device._client = client
    device._transport = SlowTransport()
    device._authenticated = True

    # Act - issue two controls concurrently.
    await asyncio.gather(
        device.set_override(object(), AirflowPreset.BOOST, 60),
        device.set_override(object(), AirflowPreset.PURGE, 60),
    )

    # Assert - the per-device transaction lock kept concurrency at one.
    assert maximum_active == 1


@pytest.mark.asyncio
async def test_connection_check_waits_for_active_operation() -> None:
    """A poll cannot check a connection while a control is still running."""

    # Arrange - pause the first control after its connection check.
    device = MultihomeDevice("AA", "MEV", 1234)
    request_started = asyncio.Event()
    release_request = asyncio.Event()
    connect_calls = 0

    async def connect(ble_device) -> None:
        nonlocal connect_calls
        connect_calls += 1

    responses = iter(decode_packet(packet) for packet in _responses())

    async def send(packet_type, operation, payload=b""):
        request_started.set()
        await release_request.wait()

    async def request(packet_type, operation, payload=b""):
        return next(responses)

    device.connect = connect
    device._send = send
    device._request = request
    first = asyncio.create_task(
        device.set_override(object(), AirflowPreset.BOOST, 60)
    )
    await request_started.wait()

    # Act - start a coordinator-style telemetry poll during the active control.
    second = asyncio.create_task(device.update(object()))
    await asyncio.sleep(0)

    # Assert - the second operation has not performed a stale connection check.
    assert connect_calls == 1
    release_request.set()
    _first_result, update = await asyncio.gather(first, second)
    assert connect_calls == 2
    assert update.zone.fan_rpm == 1200
