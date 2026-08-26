"""Regression tests for the independent Multihome protocol codec."""

from __future__ import annotations

import struct

import pytest

from custom_components.ventaxia_multihome.protocol import (
    AirflowPreset,
    DataObjectType,
    FaultFlag,
    MevCommand,
    Operation,
    PacketType,
    ProtocolError,
    crc8_zirconia,
    decode_data_object_array,
    decode_faults,
    decode_packet,
    decode_system_status,
    decode_zone_telemetry,
    encode_cancel_override,
    encode_data_object_array,
    encode_packet,
    encode_setup_code,
    encode_user_override,
    fragment_ack,
    fragment_packet,
    reassemble_fragments,
)


def test_crc_regression() -> None:
    """Zirconia CRC must not be replaced by conventional CRC-8."""

    # Arrange - use the report codec's deterministic CRC input.
    data = bytes.fromhex("010203")

    # Act - calculate the nonstandard shift-then-test CRC.
    result = crc8_zirconia(data)

    # Assert - the byte matches the recovered implementation.
    assert result == 0xF7
    assert crc8_zirconia(b"") == 0


def test_packet_encoding_and_decoding() -> None:
    """A packet preserves documented header fields and payload."""

    # Arrange - create a fixed packet with a visible byte-order timestamp.
    payload = b"\x00\x01\x02"

    # Act - encode and decode the packet.
    encoded = encode_packet(
        PacketType.ZONE_VIEW_ROW,
        Operation.DATA_REQUEST,
        payload,
        timestamp=0x01020304,
    )
    decoded = decode_packet(encoded)

    # Assert - size, flags, timestamps, and payload are exact.
    assert encoded[1] == 13
    assert decoded.packet_type == PacketType.ZONE_VIEW_ROW
    assert decoded.operation == Operation.DATA_REQUEST
    assert decoded.timestamp_be == 0x01020304
    assert decoded.timestamp_le == 0x04030201
    assert decoded.payload == payload


@pytest.mark.parametrize(
    "mutator",
    [
        lambda packet: packet[:9],
        lambda packet: bytes([packet[0], 9]) + packet[2:],
        lambda packet: bytes([packet[0] ^ 1]) + packet[1:],
    ],
)
def test_malformed_packets(mutator) -> None:
    """Short, invalid-size, and bad-checksum packets are rejected."""

    # Arrange - start with a valid response packet.
    packet = encode_packet(PacketType.SYSTEM_STATUS, Operation.RESPONSE, timestamp=1)

    # Act / Assert - malformed input raises a typed codec error.
    with pytest.raises(ProtocolError):
        decode_packet(mutator(packet))


def test_packet_size_limit() -> None:
    """The app's 128-byte packet maximum is enforced."""

    # Arrange / Act / Assert - a 119-byte payload would create 129 bytes.
    with pytest.raises(ProtocolError, match="128"):
        encode_packet(PacketType.SYSTEM_STATUS, Operation.DATA_REQUEST, bytes(119))


def test_data_object_array_round_trip() -> None:
    """Raw and RawWithId DataObjectArrays round-trip exactly."""

    # Arrange - create both required wrapper forms.
    raw = encode_data_object_array(DataObjectType.RAW, b"abc")
    raw_with_id = encode_data_object_array(
        DataObjectType.RAW_WITH_ID, b"value", object_id=0x12345678
    )

    # Act - decode both values.
    raw_result = decode_data_object_array(raw)
    id_result = decode_data_object_array(raw_with_id)

    # Assert - magic/type/length processing preserves the semantic payload.
    assert raw == bytes.fromhex("ba0a0003616263")
    assert raw_result.payload == b"abc"
    assert id_result.object_id == 0x12345678
    assert id_result.payload == b"value"


@pytest.mark.parametrize(
    "data",
    [b"", b"\xba\x0a\x00", b"\x00\x00\x00\x00", b"\xba\x0a\x00\x02\x01"],
)
def test_malformed_data_object_array(data: bytes) -> None:
    """Malformed wrappers do not leak partial values."""

    # Arrange / Act / Assert - every malformed wrapper is rejected.
    with pytest.raises(ProtocolError):
        decode_data_object_array(data)


def test_known_boost_regression_vector() -> None:
    """Boost for 1800 seconds reproduces the report byte-for-byte."""

    # Arrange - encode the report's deterministic command parameters.
    payload = encode_user_override(AirflowPreset.BOOST, 1800)

    # Act - create the whole packet and legacy fragments.
    whole = encode_packet(
        PacketType.USER_OVERRIDE,
        Operation.DATA_REQUEST,
        payload,
        timestamp=16909060,
    )
    fragments = fragment_packet(whole)

    # Assert - every documented regression byte is exact.
    assert whole.hex() == "91160038020001020304ba0a00080103000308070000"
    assert [fragment.hex() for fragment in fragments] == [
        "12e50091160038020001020304ba0a0008010300",
        "220c000308070000000000000000000000000000",
    ]
    assert reassemble_fragments(fragments) == whole


def test_fragment_validation_and_acknowledgement() -> None:
    """Fragment CRC/header validation and acknowledgement are exact."""

    # Arrange - fragment a valid packet then corrupt its CRC.
    packet = encode_packet(
        PacketType.SYSTEM_STATUS, Operation.DATA_REQUEST, timestamp=1
    )
    fragment = fragment_packet(packet)[0]
    corrupted = fragment[:1] + bytes([fragment[1] ^ 1]) + fragment[2:]

    # Act / Assert - corruption fails and acknowledgements use byte two.
    with pytest.raises(ProtocolError, match="checksum"):
        reassemble_fragments([corrupted])
    assert fragment_ack(2) == b"\x00\x00\x02" + bytes(17)


def test_setup_code_encoding() -> None:
    """Setup codes are nonzero UInt32 little-endian values."""

    # Arrange / Act - encode a value with four distinct bytes.
    encoded = encode_setup_code(0x01020304)

    # Assert - byte order and validation match the report.
    assert encoded == bytes.fromhex("04030201")
    with pytest.raises(ProtocolError):
        encode_setup_code(0)


def test_zone_telemetry_decoding() -> None:
    """The 21-byte zone record decodes all fields at documented offsets."""

    # Arrange - create a valid Multihome zone telemetry response.
    record = struct.pack(
        "<BBBHfffI", 1, 3, 2, 1450, 21.5, 58.25, 775.0, int(FaultFlag.FILTER_TIMEOUT)
    )

    # Act - decode the response.
    result = decode_zone_telemetry(record)

    # Assert - temperature, fan RPM, CO2, and fault flags match.
    assert result.co2_supported is True
    assert result.fan_rpm == 1450
    assert result.temperature == pytest.approx(21.5)
    assert result.relative_humidity == pytest.approx(58.25)
    assert result.co2 == pytest.approx(775.0)
    assert result.fault_mask == FaultFlag.FILTER_TIMEOUT


def test_unsupported_co2_is_unavailable_not_zero() -> None:
    """An unsupported CO2 field is represented as unavailable."""

    # Arrange - include a numeric field but clear the support byte.
    record = struct.pack("<BBBHfffI", 0, 2, 8, 900, 20.0, 50.0, 999.0, 0)

    # Act - decode the response.
    result = decode_zone_telemetry(record)

    # Assert - the integration does not publish a fake zero or stale value.
    assert result.co2_supported is False
    assert result.co2 is None


@pytest.mark.parametrize(
    "invalid_co2",
    [0.0, -1.0, float("nan"), float("inf"), float("-inf")],
)
def test_invalid_supported_co2_is_unavailable(invalid_co2: float) -> None:
    """Invalid readings from a supported CO2 sensor are unavailable."""

    # Arrange - report CO2 support with a value that cannot be a real reading.
    record = struct.pack("<BBBHfffI", 1, 2, 8, 900, 20.0, 50.0, invalid_co2, 0)

    # Act - decode the otherwise complete and valid telemetry response.
    result = decode_zone_telemetry(record)

    # Assert - the invalid value cannot become a Home Assistant measurement.
    assert result.co2_supported is True
    assert result.co2 is None


def test_system_status_and_fault_decoding() -> None:
    """System status uses the Raw wrapper and documented B/H/I layout."""

    # Arrange - create speed, timeout, and two active faults.
    mask = int(FaultFlag.MOTOR_NOT_RUNNING | FaultFlag.SERVICE_TIMEOUT)
    wrapped = encode_data_object_array(
        DataObjectType.RAW, struct.pack("<BHI", 3, 90, mask)
    )

    # Act - decode the status and fault mask.
    status = decode_system_status(wrapped)
    faults = decode_faults(status.fault_mask)

    # Assert - values and individual flags are exact.
    assert status.fan_speed == 3
    assert status.override_remaining == 90
    assert faults == (
        FaultFlag.MOTOR_NOT_RUNNING,
        FaultFlag.SERVICE_TIMEOUT,
    )


def test_override_and_cancel_encoding() -> None:
    """Override and cancel bodies use documented command/preset/mode values."""

    # Arrange / Act - encode set-speed and cancel payloads.
    override = decode_data_object_array(encode_user_override(AirflowPreset.PURGE, 60))
    cancel = decode_data_object_array(encode_cancel_override())

    # Assert - both eight-byte bodies match their documented semantics.
    assert override.payload == struct.pack("<BBBBI", 1, 4, 0, 3, 60)
    assert cancel.payload == struct.pack(
        "<BBBBI", MevCommand.CANCEL, AirflowPreset.LOW, 0, 3, 0
    )


def test_malformed_telemetry() -> None:
    """Truncated telemetry/status values are rejected."""

    # Arrange / Act / Assert - neither decoder accepts incomplete data.
    with pytest.raises(ProtocolError):
        decode_zone_telemetry(bytes(20))
    with pytest.raises(ProtocolError):
        decode_system_status(encode_data_object_array(DataObjectType.RAW, bytes(6)))
