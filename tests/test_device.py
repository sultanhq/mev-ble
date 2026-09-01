"""Device abstraction and reconnection tests."""

from __future__ import annotations

import asyncio
import struct
from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.ventaxia_multihome import device as device_module
from custom_components.ventaxia_multihome.bluetooth import (
    FragmentedTransport,
    WholePacketTransport,
)
from custom_components.ventaxia_multihome.const import (
    DEVICE_INFO_CHARACTERISTICS,
    FRAGMENT_CHARACTERISTIC_UUID,
    PIN_CHARACTERISTIC_UUID,
    PIN_CONFIRM_CHARACTERISTIC_UUID,
    WHOLE_PACKET_CHARACTERISTIC_UUID,
)
from custom_components.ventaxia_multihome.device import (
    CalibrationWriteUncertainError,
    DeviceError,
    GlobalSettingsUnavailableError,
    GlobalSettingUpdateError,
    MultihomeDevice,
    MultihomeDeviceInfo,
    SetupCodeRejectedError,
    SilentHoursUnavailableError,
    SilentHourUpdateError,
)
from custom_components.ventaxia_multihome.protocol import (
    AirflowPreset,
    DataObjectType,
    GlobalSettingField,
    Operation,
    PacketType,
    decode_data_object_array,
    decode_global_settings,
    decode_packet,
    decode_silent_hour,
    decode_silent_hour_slot,
    encode_cancel_override,
    encode_co2_calibration,
    encode_data_object_array,
    encode_global_setting_update,
    encode_packet,
    encode_silent_hour,
    encode_silent_hour_request,
    encode_silent_hour_update,
    encode_user_override,
    fragment_ack,
    fragment_packet,
    global_settings_after_update,
    reassemble_fragments,
)


def _silent_slots(
    populated: dict[int, bytes] | None = None,
) -> tuple:
    """Build one deterministic six-slot decoded table."""

    populated = populated or {}
    return tuple(
        decode_silent_hour_slot(
            struct.pack("<HH", index, 0) + populated.get(index, bytes(9))
        )
        for index in range(6)
    )


@pytest.mark.asyncio
async def test_read_silent_hours_maps_selected_record_firmware_responses() -> None:
    """Record-only firmware responses inherit the slot requested by the device."""

    # Arrange - return six selected-slot responses without indexed table headers.
    device = MultihomeDevice("AA", "MEV", 1234)
    record = encode_silent_hour(8 * 3600, 9 * 3600, 0x01)
    device._request = AsyncMock(
        side_effect=[
            SimpleNamespace(payload=record),
            *[SimpleNamespace(payload=b"")] * 5,
        ]
    )

    # Act - read the complete six-slot table through the production device path.
    slots = await device._read_silent_hours()

    # Assert - every response receives its requested index and slot zero is populated.
    assert [slot.index for slot in slots] == list(range(6))
    assert slots[0].record == decode_silent_hour(record)
    assert all(slot.record is None for slot in slots[1:])
    assert device._request.await_count == 6


@pytest.mark.asyncio
async def test_read_silent_hours_preserves_every_unsupported_response() -> None:
    """Unknown firmware forms remain diagnosable without blocking telemetry."""

    # Arrange - return the observed 14-byte form for every requested slot.
    device = MultihomeDevice("AA", "MEV", 1234)
    payloads = []
    for index in range(6):
        payload = bytearray.fromhex(f"{index:02x}0001200000000000000000000c")
        payload[-1] ^= 0xFF
        payloads.append(bytes(payload))
    device._request = AsyncMock(
        side_effect=[SimpleNamespace(payload=payload) for payload in payloads]
    )

    # Act - read all slots through the fail-soft production path.
    slots = await device._read_silent_hours()

    # Assert - all six reads complete and every byte is retained without decoding.
    assert device._request.await_count == 6
    assert [slot.index for slot in slots] == list(range(6))
    assert [slot.raw_payload for slot in slots] == payloads
    assert all(not slot.is_known and slot.record is None for slot in slots)


@pytest.mark.asyncio
async def test_unknown_silent_hours_do_not_block_telemetry_or_enable_writes() -> None:
    """Unsupported schedule responses fail soft while writes remain closed."""

    # Arrange - combine valid telemetry with six unknown schedule responses.
    device = MultihomeDevice("AA", "MEV", 1234)
    device.device_info = MultihomeDeviceInfo(model="10")
    telemetry = [
        SimpleNamespace(payload=decode_packet(packet).payload)
        for packet in _responses()
    ]
    unknown = bytearray.fromhex("000001200000000000000000000c")
    unknown[-1] ^= 0xFF
    unknown = bytes(unknown)
    device._request = AsyncMock(
        side_effect=[
            *telemetry,
            *[SimpleNamespace(payload=unknown) for _ in range(6)],
        ]
    )

    # Act - perform the same coherent read used during setup and polling.
    data = await device._read_data()

    # Assert - telemetry loads, raw evidence survives, and mutation stays disabled.
    assert data.zone.fan_rpm == 1200
    assert len(data.silent_hours) == 6
    assert all(slot.raw_payload == unknown for slot in data.silent_hours)
    assert all(not slot.is_known for slot in data.silent_hours)
    assert device.confirmed_silent_hours == data.silent_hours
    assert not device.silent_hours_write_ready


@pytest.mark.asyncio
async def test_checksummed_silent_hours_enable_writes_after_complete_poll() -> None:
    """Six valid model-10 CRC responses establish a writable table."""

    # Arrange - combine valid telemetry with the six captured empty-slot responses.
    device = MultihomeDevice("AA", "MEV", 1234)
    device.device_info = MultihomeDeviceInfo(model="10")
    telemetry = [
        SimpleNamespace(payload=decode_packet(packet).payload)
        for packet in _responses()
    ]
    payloads = [
        bytes.fromhex(payload)
        for payload in (
            "000001200000000000000000000c",
            "0100012000000000000000000064",
            "02000120000000000000000000db",
            "03000120000000000000000000b3",
            "0400020800000000000000000032",
            "050002080000000000000000005a",
        )
    ]
    device._request = AsyncMock(
        side_effect=[
            *telemetry,
            *[SimpleNamespace(payload=payload) for payload in payloads],
        ]
    )

    # Act - perform the coherent read used during setup and polling.
    data = await device._read_data()

    # Assert - every verified slot is known and the guarded write gate opens.
    assert [slot.index for slot in data.silent_hours] == list(range(6))
    assert all(slot.is_known and slot.record is None for slot in data.silent_hours)
    assert device.confirmed_silent_hours == data.silent_hours
    assert device.silent_hours_write_ready


@pytest.mark.asyncio
async def test_set_silent_hour_accepts_only_exact_full_table_readback() -> None:
    """A slot update becomes current only after all six slots are reread."""

    # Arrange - prepare model 10 with one complete confirmed empty table.
    device = MultihomeDevice("AA", "MEV", 1234)
    device.device_info = MultihomeDeviceInfo(model="10")
    device._confirmed_silent_hours = _silent_slots()
    device._silent_hours_write_ready = True
    device.connect = AsyncMock()
    device._send = AsyncMock()
    record = decode_silent_hour(encode_silent_hour(22 * 3600, 6 * 3600, 0x1F))
    readback = _silent_slots({2: record.raw_record})
    device._read_silent_hours = AsyncMock(return_value=readback)

    # Act - update slot two through the serialized device operation.
    result = await device.set_silent_hour(object(), 2, record)

    # Assert - packet 49 was written and only matching readback was published.
    device._send.assert_awaited_once_with(
        PacketType.SILENT_HOURS,
        Operation.UPDATE,
        encode_silent_hour_update(2, record),
    )
    device._read_silent_hours.assert_awaited_once()
    assert result == readback
    assert device.confirmed_silent_hours == readback
    assert device.silent_hours_write_ready


@pytest.mark.asyncio
async def test_delete_silent_hour_confirms_empty_slot() -> None:
    """Deletion uses 0xffff and succeeds only when the slot rereads empty."""

    # Arrange - start with one populated slot and an empty-table readback.
    device = MultihomeDevice("AA", "MEV", 1234)
    device.device_info = MultihomeDeviceInfo(model="10")
    record = encode_silent_hour(9 * 3600, 17 * 3600, 0x7F)
    previous = _silent_slots({4: record})
    device._confirmed_silent_hours = previous
    device._silent_hours_write_ready = True
    device.connect = AsyncMock()
    device._send = AsyncMock()
    device._read_silent_hours = AsyncMock(return_value=_silent_slots())

    # Act - delete slot four.
    result = await device.delete_silent_hour(object(), 4)

    # Assert - the marker is exact and confirmed state now reports empty.
    device._send.assert_awaited_once_with(
        PacketType.SILENT_HOURS, Operation.UPDATE, bytes.fromhex("0400ffff")
    )
    assert result[4].record is None
    assert device.confirmed_silent_hours == result


@pytest.mark.asyncio
async def test_silent_hour_mismatch_retains_previous_confirmed_table() -> None:
    """A rejected or ignored write never replaces the previous snapshot."""

    # Arrange - return an unchanged empty table after requesting a populated slot.
    device = MultihomeDevice("AA", "MEV", 1234)
    device.device_info = MultihomeDeviceInfo(model="10")
    previous = _silent_slots()
    device._confirmed_silent_hours = previous
    device._silent_hours_write_ready = True
    device.connect = AsyncMock()
    device._send = AsyncMock()
    device._read_silent_hours = AsyncMock(return_value=previous)
    record = decode_silent_hour(encode_silent_hour(3600, 7200, 1))

    # Act - attempt the update whose readback remains unchanged.
    with pytest.raises(SilentHourUpdateError, match="did not match") as raised:
        await device.set_silent_hour(object(), 0, record)

    # Assert - mismatch is explicit and disables another stale write.
    assert raised.value
    assert device.confirmed_silent_hours is previous
    assert not device.silent_hours_write_ready


@pytest.mark.asyncio
async def test_silent_hour_requires_complete_current_table() -> None:
    """No mutation is sent before all six slots have been confirmed."""

    # Arrange - use supported hardware with no current schedule snapshot.
    device = MultihomeDevice("AA", "MEV", 1234)
    device.device_info = MultihomeDeviceInfo(model="10")
    device.connect = AsyncMock()
    device._send = AsyncMock()
    record = decode_silent_hour(encode_silent_hour(3600, 7200, 1))

    # Act - attempt a mutation without a complete current table.
    with pytest.raises(SilentHoursUnavailableError) as raised:
        await device.set_silent_hour(object(), 0, record)

    # Assert - the precondition fails without a packet-49 write.
    assert raised.value
    device._send.assert_not_awaited()


class FakeServices:
    """Minimal service collection exposing one protocol characteristic."""

    def __init__(self, protocol_uuid: str = WHOLE_PACKET_CHARACTERISTIC_UUID) -> None:
        self._characteristics = {
            protocol_uuid: SimpleNamespace(
                properties=["read"]
                if protocol_uuid == WHOLE_PACKET_CHARACTERISTIC_UUID
                else []
            ),
        }

    def get_characteristic(self, uuid: str):
        return self._characteristics.get(uuid)


class DeviceClient:
    """Bleak client fake with characteristic-specific responses."""

    def __init__(
        self,
        protocol_reads: list[bytes],
        protocol_uuid: str = WHOLE_PACKET_CHARACTERISTIC_UUID,
    ) -> None:
        self.protocol_reads = deque(protocol_reads)
        self.protocol_uuid = protocol_uuid
        self.services = FakeServices(protocol_uuid)
        self.is_connected = True
        self.writes: list[tuple[str, bytes, bool | None]] = []
        self.callback = None

    async def read_gatt_char(self, uuid: str) -> bytearray:
        if uuid == PIN_CONFIRM_CHARACTERISTIC_UUID:
            return bytearray(b"\x01")
        if uuid == self.protocol_uuid:
            return bytearray(self.protocol_reads.popleft())
        raise AssertionError(f"unexpected read {uuid}")

    async def write_gatt_char(
        self, uuid: str, data: bytes, response: bool | None = None
    ) -> None:
        self.writes.append((uuid, bytes(data), response))

    async def disconnect(self) -> bool:
        self.is_connected = False
        return True


def _responses(
    *,
    co2: float = 800.0,
    fan_level: int = 3,
    fan_state: int = 2,
    override_remaining: int = 60,
    schedules: bool = False,
) -> list[bytes]:
    zone = struct.pack("<BBBHfffI", 1, fan_level, fan_state, 1200, 22.0, 55.0, co2, 0)
    status = encode_data_object_array(
        DataObjectType.RAW, struct.pack("<BHI", 3, override_remaining, 0)
    )
    global_settings = bytes(
        [
            10,
            35,
            70,
            100,
            65,
            75,
            1,
            0,
            1,
            0,
            1,
            0,
            2,
            3,
            15,
            25,
            4,
            11,
            12,
            5,
            6,
            7,
            100,
            0,
            150,
            0,
            21,
            91,
            8,
            9,
            22,
            92,
            13,
            14,
            15,
            16,
        ]
    )
    responses = [
        encode_packet(PacketType.ZONE_VIEW_ROW, Operation.RESPONSE, zone, timestamp=1),
        encode_packet(
            PacketType.SYSTEM_STATUS, Operation.RESPONSE, status, timestamp=2
        ),
        encode_packet(
            PacketType.GLOBAL_DATA,
            Operation.RESPONSE,
            global_settings,
            timestamp=3,
        ),
    ]
    if schedules:
        for index in range(6):
            responses.append(
                encode_packet(
                    PacketType.SILENT_HOURS,
                    Operation.RESPONSE,
                    struct.pack("<HH", index, 0) + bytes(9),
                    timestamp=4 + index,
                )
            )
    return responses


@pytest.mark.asyncio
async def test_raw_model_number_is_preserved_for_capability_detection() -> None:
    """A one-byte model characteristic becomes its documented numeric string."""

    # Arrange - expose model 11 as a raw byte instead of an ASCII string.
    client = DeviceClient([])
    model_uuid = DEVICE_INFO_CHARACTERISTICS["model"]
    client.services._characteristics[model_uuid] = SimpleNamespace(properties=["read"])
    original_read = client.read_gatt_char

    async def read(uuid: str) -> bytearray:
        if uuid == model_uuid:
            return bytearray(b"\x0b")
        return await original_read(uuid)

    client.read_gatt_char = read
    device = MultihomeDevice("AA", "MEV", 1234)
    device._client = client

    # Act - read optional Device Information through the production decoder.
    info = await device.read_device_information()
    device.device_info = info

    # Assert - the non-ASCII model value remains useful device information.
    assert info.model == "11"
    assert device.model_number == 11


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
    assert result.system.override_remaining_source == "device"
    assert result.global_settings.co2_boost_threshold == 1000
    assert device.transport_name == "whole_packet"


@pytest.mark.asyncio
async def test_zero_device_timeout_uses_local_command_countdown(monkeypatch) -> None:
    """An accepted HA override gets a countdown when firmware reports zero."""

    # Arrange - hold time and return zero timeout for the command and next poll.
    now = [100.0]
    monkeypatch.setattr(device_module, "monotonic", lambda: now[0])
    client = DeviceClient([])
    responses = deque(
        [
            *_responses(fan_level=1, override_remaining=0),
            *_responses(fan_level=1, override_remaining=0),
        ]
    )

    class ZeroTimeoutTransport:
        name = "test"

        async def send(self, packet: bytes) -> None:
            return None

        async def request(self, packet: bytes) -> bytes:
            return responses.popleft()

    device = MultihomeDevice("AA", "MEV", 1234)
    device._client = client
    device._transport = ZeroTimeoutTransport()
    device._authenticated = True

    # Act - start 90 seconds of Low, advance time, and poll again.
    started = await device.set_override(object(), AirflowPreset.LOW, 90)
    now[0] = 112.2
    updated = await device.update(object())

    # Assert - Home Assistant counts down from its accepted command deadline.
    assert started.system.override_remaining == 90
    assert started.system.override_remaining_source == "estimated"
    assert updated.system.override_remaining == 78
    assert updated.system.override_remaining_source == "estimated"


@pytest.mark.asyncio
async def test_external_zero_timeout_is_unavailable() -> None:
    """A zero timeout without a matching HA command is not published as zero."""

    # Arrange - expose an active user override with no locally known deadline.
    client = DeviceClient(_responses(fan_level=1, override_remaining=0))

    async def factory(ble_device, name, callback):
        client.callback = callback
        return client

    device = MultihomeDevice("AA", "MEV", 1234, client_factory=factory)

    # Act - read the externally initiated override.
    result = await device.update(object())

    # Assert - an unknown duration is unavailable rather than a false zero.
    assert result.system.override_remaining is None
    assert result.system.override_remaining_source == "unavailable"


@pytest.mark.asyncio
async def test_cancel_clears_estimated_countdown(monkeypatch) -> None:
    """A confirmed Cancel removes the locally estimated override deadline."""

    # Arrange - return zero for an active Low override, then a cancelled default state.
    monkeypatch.setattr(device_module, "monotonic", lambda: 100.0)
    client = DeviceClient([])
    responses = deque(
        [
            *_responses(fan_level=1, override_remaining=0),
            *_responses(fan_level=1, fan_state=8, override_remaining=0),
        ]
    )

    class CancelTransport:
        name = "test"

        async def send(self, packet: bytes) -> None:
            return None

        async def request(self, packet: bytes) -> bytes:
            return responses.popleft()

    device = MultihomeDevice("AA", "MEV", 1234)
    device._client = client
    device._transport = CancelTransport()
    device._authenticated = True

    # Act - start an estimated countdown and then cancel the override.
    started = await device.set_override(object(), AirflowPreset.LOW, 90)
    cancelled = await device.cancel_override(object())

    # Assert - Cancel returns to the device's genuine non-override zero state.
    assert started.system.override_remaining == 90
    assert started.system.override_remaining_source == "estimated"
    assert cancelled.system.override_remaining == 0
    assert cancelled.system.override_remaining_source == "device"


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
    confirmed_before_disconnect = device.confirmed_global_settings

    # Act - simulate proxy loss, then allow the regular next poll to recover.
    assert first.callback is not None
    first.is_connected = False
    first.callback(first)
    cleared_transport = device.transport_name
    ready_while_disconnected = device.global_settings_write_ready
    retained_while_disconnected = device.confirmed_global_settings
    await device.update(object())

    # Assert - stale transport cleared immediately; reconnect reauthenticated.
    assert cleared_transport is None
    assert ready_while_disconnected is False
    assert retained_while_disconnected == confirmed_before_disconnect
    assert device.global_settings_write_ready is True
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
async def test_override_controls_send_then_read_fresh_telemetry() -> None:
    """Override and cancel send once, then read a complete telemetry snapshot."""

    # Arrange - install a transport that records sends and serves telemetry reads.
    client = DeviceClient([])
    sent: list[bytes] = []
    requested: list[bytes] = []
    responses = deque([*_responses(), *_responses()])

    class SendOnlyTransport:
        name = "test"

        async def send(self, packet: bytes) -> None:
            sent.append(packet)

        async def request(self, packet: bytes) -> bytes:
            requested.append(packet)
            return responses.popleft()

    device = MultihomeDevice("AA", "MEV", 1234)
    device._client = client
    device._transport = SendOnlyTransport()
    device._authenticated = True

    # Act - apply one boost and then send the independent cancel command.
    override_data = await device.set_override(object(), AirflowPreset.BOOST, 90)
    cancel_data = await device.cancel_override(object())

    # Assert - packet 56 is send-only and each command is followed by zone/status.
    override = decode_packet(sent[0])
    cancel = decode_packet(sent[1])
    assert override.packet_type == PacketType.USER_OVERRIDE
    assert override.operation == Operation.DATA_REQUEST
    assert override.payload == encode_user_override(AirflowPreset.BOOST, 90)
    assert cancel.packet_type == PacketType.USER_OVERRIDE
    assert cancel.operation == Operation.DATA_REQUEST
    assert cancel.payload == encode_cancel_override()
    assert [decode_packet(packet).packet_type for packet in requested] == [
        PacketType.ZONE_VIEW_ROW,
        PacketType.SYSTEM_STATUS,
        PacketType.GLOBAL_DATA,
        PacketType.ZONE_VIEW_ROW,
        PacketType.SYSTEM_STATUS,
        PacketType.GLOBAL_DATA,
    ]
    assert override_data.zone.fan_rpm == 1200
    assert cancel_data.system.fan_speed == 3


@pytest.mark.asyncio
async def test_global_setting_write_requires_a_current_confirmed_record() -> None:
    """Packet 136 remains unavailable until packet 137 has decoded successfully."""

    # Arrange - connect an authenticated device without performing a settings read.
    sent: list[bytes] = []

    class NoReadTransport:
        name = "test"

        async def send(self, packet: bytes) -> None:
            sent.append(packet)

        async def request(self, packet: bytes) -> bytes:
            raise AssertionError("no readback expected")

    device = MultihomeDevice("AA", "MEV", 1234)
    device.device_info = MultihomeDeviceInfo(
        model="10", firmware="2.03.08", hardware="01.00"
    )
    device._client = DeviceClient([])
    device._transport = NoReadTransport()
    device._authenticated = True

    # Act / Assert - the guard rejects the write before any transport I/O.
    with pytest.raises(GlobalSettingsUnavailableError, match="must be read"):
        await device.set_global_setting(object(), GlobalSettingField.SPEED_LOW, 12)
    assert sent == []
    assert device.confirmed_global_settings is None
    assert device.global_settings_write_ready is False


@pytest.mark.asyncio
async def test_global_setting_write_rejects_unvalidated_field_before_io() -> None:
    """A decoded but unvalidated field cannot reach packet 136."""

    # Arrange - use the validated identity but select a static-analysis-only field.
    device = MultihomeDevice("AA", "MEV", 1234)
    device.device_info = MultihomeDeviceInfo(
        model="10", firmware="2.03.08", hardware="01.00"
    )
    device._client_factory = AsyncMock(side_effect=AssertionError("no I/O expected"))

    # Act - attempt a write whose wire format is known but physically unvalidated.
    with pytest.raises(DeviceError) as error:
        await device.set_global_setting(
            object(),
            GlobalSettingField.COMFORT_ENABLED,
            1,
        )

    # Assert - the identity-aware field guard rejects it before Bluetooth I/O.
    assert "model, firmware, and hardware" in str(error.value)
    device._client_factory.assert_not_awaited()


@pytest.mark.asyncio
async def test_global_setting_write_uses_target_zero_and_exact_readback() -> None:
    """A valid field update is committed only after exact packet 137 readback."""

    # Arrange - prime the confirmed snapshot and prepare its one-field successor.
    current_record = decode_packet(_responses()[2]).payload
    expected_record = bytearray(current_record)
    expected_record[0] = 12
    sent: list[bytes] = []
    requested: list[bytes] = []

    class ConfirmingTransport:
        name = "test"

        async def send(self, packet: bytes) -> None:
            sent.append(packet)

        async def request(self, packet: bytes) -> bytes:
            requested.append(packet)
            return encode_packet(
                PacketType.GLOBAL_DATA,
                Operation.RESPONSE,
                bytes(expected_record),
                timestamp=2,
            )

    device = MultihomeDevice("AA", "MEV", 1234)
    device.device_info = MultihomeDeviceInfo(
        model="10", firmware="2.03.08", hardware="01.00"
    )
    device._client = DeviceClient([])
    device._transport = ConfirmingTransport()
    device._authenticated = True
    device._confirmed_global_settings = decode_global_settings(current_record)
    device._global_settings_write_ready = True

    # Act - update only the low airflow percentage.
    result = await device.set_global_setting(object(), GlobalSettingField.SPEED_LOW, 12)

    # Assert - target zero, RawWithId body, one readback, and cache all agree.
    command = decode_packet(sent[0])
    request = decode_packet(requested[0])
    wrapped = decode_data_object_array(command.payload)
    assert command.packet_type == PacketType.GLOBAL_DATA_FIELD
    assert command.operation == Operation.UPDATE
    assert command.target == 0
    assert command.payload == encode_global_setting_update(
        GlobalSettingField.SPEED_LOW, 12
    )
    assert wrapped.object_id == GlobalSettingField.SPEED_LOW
    assert wrapped.payload == b"\x0c"
    assert request.packet_type == PacketType.GLOBAL_DATA
    assert request.operation == Operation.DATA_REQUEST
    assert result.raw_record == bytes(expected_record)
    assert device.confirmed_global_settings == result
    assert device.global_settings_write_ready is True


@pytest.mark.asyncio
async def test_global_setting_mismatch_retains_last_confirmed_snapshot() -> None:
    """Unexpected readback never replaces the last confirmed settings state."""

    # Arrange - return an unchanged record after a valid update command.
    current_record = decode_packet(_responses()[2]).payload
    confirmed = decode_global_settings(current_record)
    sent: list[bytes] = []

    class MismatchTransport:
        name = "test"

        async def send(self, packet: bytes) -> None:
            sent.append(packet)

        async def request(self, packet: bytes) -> bytes:
            return encode_packet(
                PacketType.GLOBAL_DATA,
                Operation.RESPONSE,
                current_record,
                timestamp=2,
            )

    device = MultihomeDevice("AA", "MEV", 1234)
    device.device_info = MultihomeDeviceInfo(
        model="10", firmware="2.03.08", hardware="01.00"
    )
    device._client = DeviceClient([])
    device._transport = MismatchTransport()
    device._authenticated = True
    device._confirmed_global_settings = confirmed
    device._global_settings_write_ready = True

    # Act / Assert - mismatch is explicit and blocks another write until a poll.
    with pytest.raises(GlobalSettingUpdateError, match="did not match"):
        await device.set_global_setting(object(), GlobalSettingField.SPEED_LOW, 12)
    assert len(sent) == 1
    assert device.confirmed_global_settings == confirmed
    assert device.global_settings_write_ready is False


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["send", "readback"])
async def test_global_setting_failure_retains_confirmed_snapshot(
    failure_stage: str,
) -> None:
    """Send and readback failures preserve state and require a fresh poll."""

    # Arrange - fail either the write or its mandatory packet 137 readback.
    current_record = decode_packet(_responses()[2]).payload
    confirmed = decode_global_settings(current_record)
    sent: list[bytes] = []

    class FailingTransport:
        name = "test"

        async def send(self, packet: bytes) -> None:
            sent.append(packet)
            if failure_stage == "send":
                raise TimeoutError("write acknowledgement timed out")

        async def request(self, packet: bytes) -> bytes:
            raise ConnectionError("device disconnected during readback")

    device = MultihomeDevice("AA", "MEV", 1234)
    device.device_info = MultihomeDeviceInfo(
        model="10", firmware="2.03.08", hardware="01.00"
    )
    device._client = DeviceClient([])
    device._transport = FailingTransport()
    device._authenticated = True
    device._confirmed_global_settings = confirmed
    device._global_settings_write_ready = True

    # Act / Assert - uncertainty is reported without optimistic state mutation.
    with pytest.raises(GlobalSettingUpdateError, match="not confirmed"):
        await device.set_global_setting(object(), GlobalSettingField.SPEED_LOW, 12)
    assert len(sent) == 1
    assert device.confirmed_global_settings == confirmed
    assert device.global_settings_write_ready is False


@pytest.mark.parametrize(
    ("model", "firmware", "hardware", "supported"),
    [
        ("1", "2.03.08", "01.00", False),
        ("2", "2.03.08", "01.00", False),
        ("9", "2.03.08", "01.00", False),
        ("10", "2.03.08", "01.00", True),
        ("10", "2.03.09", "01.00", False),
        ("10", "2.03.08", "01.01", False),
        ("10", None, "01.00", False),
        ("11", "2.03.08", "01.00", False),
        (None, "2.03.08", "01.00", False),
    ],
)
def test_global_airflow_capability_requires_exact_validated_identity(
    model: str | None,
    firmware: str | None,
    hardware: str | None,
    supported: bool,
) -> None:
    """Only the physically validated identity exposes stable commissioning."""

    # Arrange - apply one exact or partially matching device identity.
    device = MultihomeDevice("AA", "MEV", 1234)
    device.device_info = MultihomeDeviceInfo(
        model=model,
        firmware=firmware,
        hardware=hardware,
    )

    # Act - evaluate the user-facing capability gate.
    result = device.supports_global_airflow_configuration

    # Assert - only the identity backed by physical evidence passes.
    assert result is supported


@pytest.mark.asyncio
async def test_airflow_profile_updates_each_field_with_exact_readback() -> None:
    """A four-level profile stays serialized and confirms every intermediate."""

    # Arrange - emulate firmware applying each one-byte RawWithId update.
    record = bytearray(decode_packet(_responses()[2]).payload)
    record[:4] = bytes([10, 20, 30, 40])
    current = decode_global_settings(bytes(record))
    sent: list[bytes] = []
    requested: list[bytes] = []

    class ApplyingTransport:
        name = "test"

        async def send(self, packet: bytes) -> None:
            nonlocal current
            sent.append(packet)
            command = decode_packet(packet)
            wrapped = decode_data_object_array(command.payload)
            assert wrapped.object_id is not None
            current = global_settings_after_update(
                current,
                GlobalSettingField(wrapped.object_id),
                wrapped.payload[0],
            )

        async def request(self, packet: bytes) -> bytes:
            requested.append(packet)
            return encode_packet(
                PacketType.GLOBAL_DATA,
                Operation.RESPONSE,
                current.raw_record,
                timestamp=2,
            )

    device = MultihomeDevice("AA", "MEV", 1234)
    device.device_info = MultihomeDeviceInfo(
        model="10", firmware="2.03.08", hardware="01.00"
    )
    device._client = DeviceClient([])
    device._transport = ApplyingTransport()
    device._authenticated = True
    device._confirmed_global_settings = current
    device._global_settings_write_ready = True

    # Act - raise every level while preserving strict ordering throughout.
    result = await device.set_airflow_profile(
        object(), low=20, normal=30, boost=40, purge=50
    )

    # Assert - all four writes use target zero and each has its own readback.
    assert len(sent) == 4
    assert len(requested) == 4
    assert all(
        decode_packet(packet).packet_type == PacketType.GLOBAL_DATA_FIELD
        and decode_packet(packet).target == 0
        for packet in sent
    )
    assert all(
        decode_packet(packet).packet_type == PacketType.GLOBAL_DATA
        for packet in requested
    )
    assert (
        result.speed_low,
        result.speed_medium,
        result.speed_boost,
        result.speed_purge,
    ) == (20, 30, 40, 50)
    assert device.confirmed_global_settings == result


@pytest.mark.asyncio
async def test_airflow_profile_rejects_unsupported_model_before_write() -> None:
    """An unknown/non-MEV model cannot receive packet-136 airflow writes."""

    # Arrange - connect an unsupported model and record any attempted send.
    sent: list[bytes] = []

    class RecordingTransport:
        name = "test"

        async def send(self, packet: bytes) -> None:
            sent.append(packet)

        async def request(self, packet: bytes) -> bytes:
            raise AssertionError("no readback expected")

    device = MultihomeDevice("AA", "MEV", 1234)
    device.device_info = MultihomeDeviceInfo(model="11")
    device._client = DeviceClient([])
    device._transport = RecordingTransport()
    device._authenticated = True

    # Act / Assert - capability validation fails before connection or write use.
    with pytest.raises(DeviceError, match="not validated for this model"):
        await device.set_airflow_profile(
            object(), low=10, normal=20, boost=30, purge=40
        )
    assert sent == []


@pytest.mark.parametrize(
    ("model", "firmware", "hardware", "supported"),
    [
        ("10", "2.03.08", "01.00", True),
        ("10", "2.03.09", "01.00", False),
        ("10", "2.03.08", "01.01", False),
        ("2", "2.03.08", "01.00", False),
    ],
)
def test_sensor_threshold_capability_requires_exact_validation_identity(
    model: str,
    firmware: str,
    hardware: str,
    supported: bool,
) -> None:
    """The guarded RC flow is restricted to one exact device identity."""

    # Arrange - apply one exact or near-match identity.
    device = MultihomeDevice("AA", "MEV", 1234)
    device.device_info = MultihomeDeviceInfo(
        model=model, firmware=firmware, hardware=hardware
    )

    # Act - evaluate the separate prerelease capability gate.
    result = device.supports_sensor_threshold_configuration

    # Assert - firmware, hardware, and model must all match.
    assert result is supported


@pytest.mark.parametrize(
    ("model", "firmware", "hardware", "supported"),
    [
        ("10", "2.03.08", "01.00", True),
        ("10", "2.03.09", "01.00", False),
        ("10", "2.03.08", "01.01", False),
        ("2", "2.03.08", "01.00", False),
    ],
)
def test_humidity_response_capability_requires_exact_candidate_identity(
    model: str,
    firmware: str,
    hardware: str,
    supported: bool,
) -> None:
    """The prerelease response flow is restricted to one exact identity."""

    # Arrange - apply one exact or near-match identity.
    device = MultihomeDevice("AA", "MEV", 1234)
    device.device_info = MultihomeDeviceInfo(
        model=model, firmware=firmware, hardware=hardware
    )

    # Act - evaluate the candidate-only capability gate.
    result = device.supports_humidity_response_configuration

    # Assert - firmware, hardware, and model must all match.
    assert result is supported


@pytest.mark.asyncio
async def test_humidity_response_updates_each_flag_with_exact_readback() -> None:
    """Rapid and Ambient response are confirmed independently."""

    # Arrange - emulate firmware applying both RawWithId boolean writes.
    record = bytearray(decode_packet(_responses()[2]).payload)
    record[9] = 0
    record[10] = 1
    current = decode_global_settings(bytes(record))
    sent: list[bytes] = []

    class ApplyingTransport:
        name = "test"

        async def send(self, packet: bytes) -> None:
            nonlocal current
            sent.append(packet)
            wrapped = decode_data_object_array(decode_packet(packet).payload)
            assert wrapped.object_id is not None
            current = global_settings_after_update(
                current,
                GlobalSettingField(wrapped.object_id),
                bool(wrapped.payload[0]),
            )

        async def request(self, packet: bytes) -> bytes:
            return encode_packet(
                PacketType.GLOBAL_DATA,
                Operation.RESPONSE,
                current.raw_record,
                timestamp=2,
            )

    device = MultihomeDevice("AA", "MEV", 1234)
    device.device_info = MultihomeDeviceInfo(
        model="10", firmware="2.03.08", hardware="01.00"
    )
    device._client = DeviceClient([])
    device._transport = ApplyingTransport()
    device._authenticated = True
    device._confirmed_global_settings = current
    device._global_settings_write_ready = True

    # Act - reverse both recovered response flags.
    result = await device.set_humidity_response(
        object(), rapid=True, ambient=False
    )

    # Assert - both writes were read back exactly and published as confirmed.
    fields = tuple(
        GlobalSettingField(
            decode_data_object_array(decode_packet(packet).payload).object_id
        )
        for packet in sent
    )
    assert fields == (
        GlobalSettingField.RAPID_RESPONSE_ENABLED,
        GlobalSettingField.AMBIENT_RESPONSE_ENABLED,
    )
    assert result.rapid_response_enabled is True
    assert result.ambient_response_enabled is False
    assert device.confirmed_global_settings == result


@pytest.mark.asyncio
async def test_sensor_thresholds_update_each_field_with_exact_readback() -> None:
    """Humidity and both CO2 thresholds are confirmed independently."""

    # Arrange - emulate firmware applying RawWithId values after every write.
    record = bytearray(decode_packet(_responses()[2]).payload)
    record[5] = 75
    record[22:24] = bytes([100, 0])
    record[24:26] = bytes([150, 0])
    current = decode_global_settings(bytes(record))
    sent: list[bytes] = []
    requested: list[bytes] = []

    class ApplyingTransport:
        name = "test"

        async def send(self, packet: bytes) -> None:
            nonlocal current
            sent.append(packet)
            wrapped = decode_data_object_array(decode_packet(packet).payload)
            assert wrapped.object_id is not None
            field = GlobalSettingField(wrapped.object_id)
            value = (
                int.from_bytes(wrapped.payload, "little") * 10
                if field
                in {
                    GlobalSettingField.CO2_BOOST_THRESHOLD,
                    GlobalSettingField.CO2_PURGE_THRESHOLD,
                }
                else wrapped.payload[0]
            )
            current = global_settings_after_update(current, field, value)

        async def request(self, packet: bytes) -> bytes:
            requested.append(packet)
            return encode_packet(
                PacketType.GLOBAL_DATA,
                Operation.RESPONSE,
                current.raw_record,
                timestamp=2,
            )

    device = MultihomeDevice("AA", "MEV", 1234)
    device.device_info = MultihomeDeviceInfo(
        model="10", firmware="2.03.08", hardware="01.00"
    )
    device._client = DeviceClient([])
    device._transport = ApplyingTransport()
    device._authenticated = True
    device._confirmed_global_settings = current
    device._global_settings_write_ready = True

    # Act - cross the old purge value so the planner must send purge before boost.
    result = await device.set_sensor_thresholds(
        object(), humidity=70, co2_boost=1600, co2_purge=1800
    )

    # Assert - three sends, three reads, safe CO2 order, and exact final state.
    fields = [
        GlobalSettingField(
            decode_data_object_array(decode_packet(packet).payload).object_id
        )
        for packet in sent
    ]
    assert len(sent) == len(requested) == 3
    assert fields.index(GlobalSettingField.CO2_PURGE_THRESHOLD) < fields.index(
        GlobalSettingField.CO2_BOOST_THRESHOLD
    )
    assert (
        result.humidity_threshold,
        result.co2_boost_threshold,
        result.co2_purge_threshold,
    ) == (70, 1600, 1800)
    assert device.confirmed_global_settings == result


@pytest.mark.asyncio
async def test_internal_co2_calibration_uses_validated_target_and_flags() -> None:
    """The device API exposes only the validated internal-sensor command."""

    # Arrange - install a send-only transport and an authenticated client.
    client = DeviceClient([])
    sent: list[bytes] = []
    requested: list[bytes] = []
    responses = deque(
        [
            encode_packet(
                PacketType.DEVICE_VIEW_HEADER,
                Operation.RESPONSE,
                bytes([2, 6, 0]),
                timestamp=1,
            ),
            encode_packet(
                PacketType.DEVICE_VIEW_ROW,
                Operation.RESPONSE,
                bytes([1, 10, 4]) + bytes(31),
                timestamp=2,
            ),
            encode_packet(
                PacketType.DEVICE_VIEW_ROW,
                Operation.RESPONSE,
                bytes([7, 6, 4]) + bytes(31),
                timestamp=3,
            ),
        ]
    )

    class CalibrationTransport:
        name = "test"

        async def send(self, packet: bytes) -> None:
            sent.append(packet)

        async def request(self, packet: bytes) -> bytes:
            requested.append(packet)
            return responses.popleft()

    device = MultihomeDevice("AA", "MEV", 1234)
    device.device_info = MultihomeDeviceInfo(model="2")
    device._client = client
    device._transport = CalibrationTransport()
    device._authenticated = True

    # Act - start the documented internal-sensor fresh-air calibration.
    await device.calibrate_internal_co2(object(), 450)

    # Assert - the device table resolves address 7 before the exact app command.
    assert len(sent) == 1
    assert [decode_packet(packet).packet_type for packet in requested] == [
        PacketType.DEVICE_VIEW_HEADER,
        PacketType.DEVICE_VIEW_ROW,
        PacketType.DEVICE_VIEW_ROW,
    ]
    assert decode_packet(requested[1]).payload == b"\x00"
    assert decode_packet(requested[2]).payload == b"\x01"
    packet = decode_packet(sent[0])
    assert packet.packet_type == PacketType.CO2_CALIBRATION
    assert packet.operation == Operation.UPDATE
    assert packet.target == 7
    assert device.last_calibration_device_table_version == 6
    assert device.last_calibration_target_scan == [(1, 10, 4), (7, 6, 4)]
    assert device.last_calibration_target == 7
    assert packet.payload == encode_co2_calibration(
        450,
        automatic_enabled=False,
        start_forced_calibration=True,
    )


@pytest.mark.asyncio
async def test_internal_co2_calibration_uses_mev_control_target_zero() -> None:
    """Built-in CO2 models route calibration through their MEV control row."""

    # Arrange - reproduce the live V6 table with one address-zero type-10 row.
    sent: list[bytes] = []
    responses = deque(
        [
            encode_packet(
                PacketType.DEVICE_VIEW_HEADER,
                Operation.RESPONSE,
                bytes([1, 6, 0]),
                timestamp=1,
            ),
            encode_packet(
                PacketType.DEVICE_VIEW_ROW,
                Operation.RESPONSE,
                bytes([0, 10, 4]) + bytes(31),
                timestamp=2,
            ),
        ]
    )

    class CalibrationTransport:
        name = "test"

        async def send(self, packet: bytes) -> None:
            sent.append(packet)

        async def request(self, packet: bytes) -> bytes:
            return responses.popleft()

    device = MultihomeDevice("AA", "MEV", 1234)
    device.device_info = MultihomeDeviceInfo(model="10")
    device._client = DeviceClient([])
    device._transport = CalibrationTransport()
    device._authenticated = True

    # Act - start calibration using the official-app default reference.
    await device.calibrate_internal_co2(object(), 450)

    # Assert - the live MEV control row is selected without a broad fallback.
    assert len(sent) == 1
    packet = decode_packet(sent[0])
    assert packet.packet_type == PacketType.CO2_CALIBRATION
    assert packet.operation == Operation.UPDATE
    assert packet.target == 0
    assert packet.payload == encode_co2_calibration(
        450,
        automatic_enabled=False,
        start_forced_calibration=True,
    )
    assert device.last_calibration_device_table_version == 6
    assert device.last_calibration_target_scan == [(0, 10, 4)]
    assert device.last_calibration_target == 0


@pytest.mark.asyncio
async def test_internal_co2_calibration_rejects_unvalidated_model() -> None:
    """Unknown and non-CO2 models cannot receive a calibration command."""

    # Arrange - leave the device model unknown and record any transport writes.
    client = DeviceClient([])
    sent: list[bytes] = []

    class CalibrationTransport:
        name = "test"

        async def send(self, packet: bytes) -> None:
            sent.append(packet)

        async def request(self, packet: bytes) -> bytes:
            raise AssertionError("no request expected")

    device = MultihomeDevice("AA", "MEV", 1234)
    device._client = client
    device._transport = CalibrationTransport()
    device._authenticated = True

    # Act / Assert - target validation happens before Bluetooth I/O.
    with pytest.raises(DeviceError, match="not validated"):
        await device.calibrate_internal_co2(object(), 400)
    assert sent == []


@pytest.mark.asyncio
async def test_internal_co2_calibration_marks_write_failure_as_uncertain() -> None:
    """A failure inside the final send cannot be classified as unsent."""

    # Arrange - resolve a valid target, then fail after the write is attempted.
    responses = deque(
        [
            encode_packet(
                PacketType.DEVICE_VIEW_HEADER,
                Operation.RESPONSE,
                bytes([1, 6, 0]),
                timestamp=1,
            ),
            encode_packet(
                PacketType.DEVICE_VIEW_ROW,
                Operation.RESPONSE,
                bytes([7, 6, 4]) + bytes(31),
                timestamp=2,
            ),
        ]
    )

    class CalibrationTransport:
        name = "test"

        async def send(self, packet: bytes) -> None:
            raise TimeoutError("acknowledgement timed out")

        async def request(self, packet: bytes) -> bytes:
            return responses.popleft()

    device = MultihomeDevice("AA", "MEV", 1234)
    device.device_info = MultihomeDeviceInfo(model="2")
    device._client = DeviceClient([])
    device._transport = CalibrationTransport()
    device._authenticated = True

    # Act / Assert - the caller is told delivery may have occurred.
    with pytest.raises(CalibrationWriteUncertainError, match="timed out"):
        await device.calibrate_internal_co2(object(), 450)
    assert device.last_calibration_target == 7


@pytest.mark.asyncio
async def test_internal_co2_calibration_requires_discovered_sensor_target() -> None:
    """Calibration cannot fall back to the ineffective master target zero."""

    # Arrange - expose one MEV control-unit row but no internal CO2 sensor row.
    client = DeviceClient([])
    sent: list[bytes] = []
    responses = deque(
        [
            encode_packet(
                PacketType.DEVICE_VIEW_HEADER,
                Operation.RESPONSE,
                bytes([1, 6, 0]),
                timestamp=1,
            ),
            encode_packet(
                PacketType.DEVICE_VIEW_ROW,
                Operation.RESPONSE,
                bytes([1, 10, 4]) + bytes(31),
                timestamp=2,
            ),
        ]
    )

    class CalibrationTransport:
        name = "test"

        async def send(self, packet: bytes) -> None:
            sent.append(packet)

        async def request(self, packet: bytes) -> bytes:
            return responses.popleft()

    device = MultihomeDevice("AA", "MEV", 1234)
    device.device_info = MultihomeDeviceInfo(model="2")
    device._client = client
    device._transport = CalibrationTransport()
    device._authenticated = True

    # Act / Assert - missing routing evidence blocks the calibration write.
    with pytest.raises(DeviceError, match="no internal CO2 sensor target"):
        await device.calibrate_internal_co2(object(), 450)
    assert sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize("transport_name", ["whole_packet", "fragmented"])
async def test_control_and_readback_have_transport_parity(
    transport_name: str,
) -> None:
    """The same override API sends and reconciles over either BLE transport."""

    # Arrange - build the exact acknowledgements/responses for the chosen transport.
    all_responses = _responses(schedules=True)
    zone_response, status_response, global_settings_response = all_responses[:3]
    silent_hour_responses = all_responses[3:]
    command_template = encode_packet(
        PacketType.USER_OVERRIDE,
        Operation.DATA_REQUEST,
        encode_user_override(AirflowPreset.BOOST, 90),
        timestamp=1,
    )
    if transport_name == "whole_packet":
        client = DeviceClient(all_responses)
        transport = WholePacketTransport(client, timeout=0.1, poll_interval=0)
        command_frame_count = 1
    else:
        zone_request = encode_packet(
            PacketType.ZONE_VIEW_ROW, Operation.DATA_REQUEST, b"\x00", timestamp=1
        )
        status_request = encode_packet(
            PacketType.SYSTEM_STATUS, Operation.DATA_REQUEST, timestamp=1
        )
        global_settings_request = encode_packet(
            PacketType.GLOBAL_DATA, Operation.DATA_REQUEST, timestamp=1
        )
        command_frames = fragment_packet(command_template)
        zone_request_frames = fragment_packet(zone_request)
        status_request_frames = fragment_packet(status_request)
        global_settings_request_frames = fragment_packet(global_settings_request)
        protocol_reads = [
            *(fragment_ack(index) for index in range(1, len(command_frames) + 1)),
            *(fragment_ack(index) for index in range(1, len(zone_request_frames) + 1)),
            *fragment_packet(zone_response),
            *(
                fragment_ack(index)
                for index in range(1, len(status_request_frames) + 1)
            ),
            *fragment_packet(status_response),
            *(
                fragment_ack(index)
                for index in range(1, len(global_settings_request_frames) + 1)
            ),
            *fragment_packet(global_settings_response),
        ]
        for index, response in enumerate(silent_hour_responses):
            request = encode_packet(
                PacketType.SILENT_HOURS,
                Operation.DATA_REQUEST,
                encode_silent_hour_request(index),
                timestamp=1,
            )
            request_frames = fragment_packet(request)
            protocol_reads.extend(
                fragment_ack(sequence) for sequence in range(1, len(request_frames) + 1)
            )
            protocol_reads.extend(fragment_packet(response))
        client = DeviceClient(protocol_reads, FRAGMENT_CHARACTERISTIC_UUID)
        transport = FragmentedTransport(
            client, timeout=0.1, poll_interval=0, write_delay=0
        )
        command_frame_count = len(command_frames)
    device = MultihomeDevice("AA", "MEV", 1234)
    device.device_info = MultihomeDeviceInfo(model="10")
    device._client = client
    device._transport = transport
    device._authenticated = True

    # Act - invoke the transport-independent device control API.
    data = await device.set_override(object(), AirflowPreset.BOOST, 90)

    # Assert - the command is exact and both transports return the same readback.
    if transport_name == "whole_packet":
        command = client.writes[0][1]
    else:
        command = reassemble_fragments(
            [write[1] for write in client.writes[:command_frame_count]]
        )
    decoded = decode_packet(command)
    assert decoded.packet_type == PacketType.USER_OVERRIDE
    assert decoded.payload == encode_user_override(AirflowPreset.BOOST, 90)
    assert data.zone.fan_rpm == 1200
    assert data.system.override_remaining == 60
    assert data.global_settings.co2_purge_threshold == 1500
    assert [slot.index for slot in data.silent_hours] == list(range(6))
    assert all(slot.record is None for slot in data.silent_hours)


@pytest.mark.asyncio
async def test_transactions_are_serialized() -> None:
    """Concurrent controls never overlap protocol sends."""

    # Arrange - install a transport that detects concurrent entry.
    client = DeviceClient([])
    active = 0
    maximum_active = 0
    responses = deque([*_responses(), *_responses()])

    class SlowTransport:
        name = "test"

        async def _enter(self) -> None:
            nonlocal active, maximum_active
            active += 1
            maximum_active = max(maximum_active, active)
            await asyncio.sleep(0)
            active -= 1

        async def send(self, packet: bytes) -> None:
            await self._enter()

        async def request(self, packet: bytes) -> bytes:
            await self._enter()
            return responses.popleft()

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

    responses = iter(decode_packet(packet) for packet in [*_responses(), *_responses()])

    async def send(packet_type, operation, payload=b""):
        request_started.set()
        await release_request.wait()

    async def request(packet_type, operation, payload=b""):
        return next(responses)

    device.connect = connect
    device._send = send
    device._request = request
    first = asyncio.create_task(device.set_override(object(), AirflowPreset.BOOST, 60))
    await request_started.wait()

    # Act - start a coordinator-style telemetry poll during the active control.
    second = asyncio.create_task(device.update(object()))
    await asyncio.sleep(0)

    # Assert - the second operation has not performed a stale connection check.
    assert connect_calls == 1
    release_request.set()
    control_update, scheduled_update = await asyncio.gather(first, second)
    assert connect_calls == 2
    assert control_update.zone.fan_rpm == 1200
    assert scheduled_update.zone.fan_rpm == 1200


@pytest.mark.asyncio
async def test_active_poll_finishes_before_control_and_its_readback() -> None:
    """A control waits for a poll, then keeps its own write/readback atomic."""

    # Arrange - pause an initial poll after it acquires the operation lock.
    device = MultihomeDevice("AA", "MEV", 1234)
    poll_started = asyncio.Event()
    release_poll = asyncio.Event()
    timeline: list[str] = []
    responses = iter(decode_packet(packet) for packet in [*_responses(), *_responses()])
    request_count = 0

    async def connect(ble_device) -> None:
        timeline.append("connect")

    async def send(packet_type, operation, payload=b"") -> None:
        timeline.append(f"send:{int(packet_type)}")

    async def request(packet_type, operation, payload=b""):
        nonlocal request_count
        request_count += 1
        timeline.append(f"request:{int(packet_type)}")
        if request_count == 1:
            poll_started.set()
            await release_poll.wait()
        return next(responses)

    device.connect = connect
    device._send = send
    device._request = request
    poll = asyncio.create_task(device.update(object()))
    await poll_started.wait()

    # Act - queue a control while the scheduled poll still owns the device.
    control = asyncio.create_task(
        device.set_override(object(), AirflowPreset.BOOST, 60)
    )
    await asyncio.sleep(0)
    before_release = list(timeline)
    release_poll.set()
    poll_data, control_data = await asyncio.gather(poll, control)

    # Assert - the control starts only after the poll, then reads both fresh rows.
    assert before_release == ["connect", f"request:{int(PacketType.ZONE_VIEW_ROW)}"]
    assert timeline == [
        "connect",
        f"request:{int(PacketType.ZONE_VIEW_ROW)}",
        f"request:{int(PacketType.SYSTEM_STATUS)}",
        f"request:{int(PacketType.GLOBAL_DATA)}",
        "connect",
        f"send:{int(PacketType.USER_OVERRIDE)}",
        f"request:{int(PacketType.ZONE_VIEW_ROW)}",
        f"request:{int(PacketType.SYSTEM_STATUS)}",
        f"request:{int(PacketType.GLOBAL_DATA)}",
    ]
    assert poll_data.zone.fan_rpm == 1200
    assert control_data.system.fan_speed == 3
