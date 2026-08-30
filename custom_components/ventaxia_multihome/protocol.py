"""Dependency-free codec for the Vent-Axia MEV/Multihome protocol.

The formats in this module are specified by ``findings/REPORT.md``. Keep this
module independent from Home Assistant and BLE transport code.
"""

from __future__ import annotations

import math
import struct
import time
from collections.abc import Sequence
from dataclasses import dataclass
from enum import IntEnum, IntFlag
from typing import Final

MAX_PACKET_SIZE: Final = 128
PROTOCOL_HEADER_SIZE: Final = 10
DATA_OBJECT_MAGIC: Final = 0x0ABA
FRAGMENT_SIZE: Final = 20
FRAGMENT_PAYLOAD_SIZE: Final = 17
MIN_CO2_CALIBRATION_REFERENCE: Final = 400
MAX_CO2_CALIBRATION_REFERENCE: Final = 2000
DEFAULT_CO2_CALIBRATION_REFERENCE: Final = 450

WHOLE_PACKET_ACK: Final = b"\x00\x00\x01"
WHOLE_PACKET_CANCEL: Final = b"\x00\x00\xff"


class ProtocolError(ValueError):
    """Base error raised for invalid protocol data."""


class PacketType(IntEnum):
    """Packet types used by the first integration release."""

    USER_OVERRIDE = 56
    SYSTEM_STATUS = 67
    CO2_CALIBRATION = 116
    DEVICE_VIEW_HEADER = 141
    DEVICE_VIEW_ROW = 142
    ZONE_VIEW_ROW = 146


class Operation(IntFlag):
    """Protocol operation flags."""

    NONE = 0
    UPDATE = 1
    DATA_REQUEST = 2
    ACKNOWLEDGEMENT_REQUEST = 4
    RESERVED = 8
    SUBSCRIBE = 16
    ACKNOWLEDGE = 32
    RESPONSE = 64
    ENCRYPTED = 128


class DataObjectType(IntEnum):
    """DataObjectArray types."""

    RAW = 0
    OBJECT = 1
    OBJECT_ARRAY = 2
    PROTOCOL = 3
    TEXT = 4
    INT_ARRAY = 5
    FLOAT_ARRAY = 6
    RAW_WITH_ID = 7


class MevCommand(IntEnum):
    """User-override commands."""

    NONE = 0
    SET_SPEED = 1
    CANCEL = 2


class AirflowPreset(IntEnum):
    """Documented airflow presets."""

    LOW = 1
    NORMAL = 2
    BOOST = 3
    PURGE = 4


class DeviceType(IntEnum):
    """Device-table types needed to resolve command destinations."""

    INTERNAL_CO2_SENSOR = 6
    MEV_CONTROL_UNIT = 10


class VentilationMode(IntEnum):
    """Ventilation-mode byte values."""

    HEAT_RECOVERY = 1
    VENTILATION = 2
    OFF = 3
    STOP = 4


class FanState(IntEnum):
    """Documented zone fan states."""

    ALARM = 0
    SILENT_HOUR = 1
    USER_OVERRIDE = 2
    LS_INPUT_ACTIVE = 3
    LS_INPUT_INACTIVE = 4
    DIGITAL_INPUT = 5
    ANALOGUE_INPUT = 6
    SENSOR_OVERRIDE = 7
    DEFAULT = 8
    MAXIMUM = 9


FAN_STATE_NAMES: Final = {
    FanState.ALARM: "alarm",
    FanState.SILENT_HOUR: "silent_hour",
    FanState.USER_OVERRIDE: "user_override",
    FanState.LS_INPUT_ACTIVE: "ls_input_active",
    FanState.LS_INPUT_INACTIVE: "ls_input_inactive",
    FanState.DIGITAL_INPUT: "digital_input",
    FanState.ANALOGUE_INPUT: "analogue_input",
    FanState.SENSOR_OVERRIDE: "sensor_override",
    FanState.DEFAULT: "default",
    FanState.MAXIMUM: "maximum",
}


class FaultFlag(IntFlag):
    """Documented system and zone fault/status flags."""

    NONE = 0
    NO_ERROR = 1
    MOTOR_NOT_RUNNING = 2
    INTERNAL_TEMPERATURE_SENSOR_FAULT = 4
    INTERNAL_CO2_SENSOR_FAULT = 8
    EXTERNAL_TEMPERATURE_SENSOR_OFFLINE = 16
    EXTERNAL_CO2_SENSOR_OFFLINE = 32
    SPEED_SWITCH_UNIT_OFFLINE = 64
    ALARM_TRIGGERED = 128
    DEVICE_LOST = 256
    FIRMWARE_UPDATE_FAILED = 512
    BATTERY_CRITICAL = 1024
    FILTER_TIMEOUT = 2048
    SERVICE_TIMEOUT = 4096
    NOTIFICATION = 8192


DIAGNOSTIC_FAULTS: Final = (
    FaultFlag.MOTOR_NOT_RUNNING,
    FaultFlag.INTERNAL_TEMPERATURE_SENSOR_FAULT,
    FaultFlag.INTERNAL_CO2_SENSOR_FAULT,
    FaultFlag.EXTERNAL_TEMPERATURE_SENSOR_OFFLINE,
    FaultFlag.EXTERNAL_CO2_SENSOR_OFFLINE,
    FaultFlag.SPEED_SWITCH_UNIT_OFFLINE,
    FaultFlag.ALARM_TRIGGERED,
    FaultFlag.DEVICE_LOST,
    FaultFlag.FIRMWARE_UPDATE_FAILED,
    FaultFlag.BATTERY_CRITICAL,
    FaultFlag.FILTER_TIMEOUT,
    FaultFlag.SERVICE_TIMEOUT,
)


def crc8_zirconia(data: bytes | bytearray | memoryview) -> int:
    """Return the protocol's nonstandard Zirconia CRC value."""

    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc << 1) & 0xFF
            if crc & 0x80:
                crc ^= 0x07
    return crc


@dataclass(frozen=True, slots=True)
class ProtocolPacket:
    """Decoded protocol packet."""

    packet_type: int
    operation: int
    target: int
    timestamp_be: int
    timestamp_le: int
    backtracking_count: int
    payload: bytes
    checksum: int


def encode_packet(
    packet_type: int,
    operation: int,
    payload: bytes = b"",
    *,
    target: int = 0,
    timestamp: int | None = None,
    backtracking_count: int = 0,
) -> bytes:
    """Encode the documented ten-byte-header protocol packet."""

    if timestamp is None:
        timestamp = int(time.time())
    for label, value, maximum in (
        ("packet_type", packet_type, 0xFF),
        ("operation", operation, 0xFF),
        ("target", target, 0xFF),
        ("timestamp", timestamp, 0xFFFFFFFF),
        ("backtracking_count", backtracking_count, 0xFF),
    ):
        if not 0 <= int(value) <= maximum:
            raise ProtocolError(f"{label} is outside its encoded range")
    if backtracking_count:
        raise ProtocolError("encoding backtracking records is not supported")
    total_size = PROTOCOL_HEADER_SIZE + len(payload)
    if total_size > MAX_PACKET_SIZE:
        raise ProtocolError("protocol packet exceeds 128 bytes")

    packet = bytearray(total_size)
    packet[1] = total_size
    packet[2] = backtracking_count
    packet[3] = int(packet_type)
    packet[4] = int(operation)
    packet[5] = target
    packet[6:10] = int(timestamp).to_bytes(4, "big")
    packet[10:] = payload
    packet[0] = crc8_zirconia(packet[1:])
    return bytes(packet)


def decode_packet(data: bytes, *, validate_checksum: bool = True) -> ProtocolPacket:
    """Decode and validate a protocol packet."""

    if len(data) < PROTOCOL_HEADER_SIZE:
        raise ProtocolError("protocol packet is shorter than its ten-byte header")
    total_size = data[1]
    if total_size < PROTOCOL_HEADER_SIZE or total_size > len(data):
        raise ProtocolError(
            f"invalid declared packet size {total_size} for {len(data)} bytes"
        )
    if total_size > MAX_PACKET_SIZE:
        raise ProtocolError("declared packet size exceeds 128 bytes")

    packet = data[:total_size]
    expected_checksum = crc8_zirconia(packet[1:])
    if validate_checksum and packet[0] != expected_checksum:
        raise ProtocolError(
            f"checksum mismatch: got 0x{packet[0]:02x}, "
            f"expected 0x{expected_checksum:02x}"
        )

    backtracking_count = packet[2]
    payload_end = total_size - (backtracking_count * 3)
    if payload_end < PROTOCOL_HEADER_SIZE:
        raise ProtocolError("backtracking records overlap the packet header")

    return ProtocolPacket(
        packet_type=packet[3],
        operation=packet[4],
        target=packet[5],
        timestamp_be=int.from_bytes(packet[6:10], "big"),
        timestamp_le=int.from_bytes(packet[6:10], "little"),
        backtracking_count=backtracking_count,
        payload=packet[10:payload_end],
        checksum=packet[0],
    )


@dataclass(frozen=True, slots=True)
class DataObjectArray:
    """Decoded DataObjectArray wrapper."""

    object_type: int
    payload: bytes
    object_id: int | None = None


def encode_data_object_array(
    object_type: int, payload: bytes, *, object_id: int | None = None
) -> bytes:
    """Encode a DataObjectArray wrapper."""

    if not 0 <= int(object_type) <= 0xFF:
        raise ProtocolError("object type must fit uint8")
    if int(object_type) == DataObjectType.RAW_WITH_ID:
        if object_id is None or not 0 <= object_id <= 0xFFFFFFFF:
            raise ProtocolError("RawWithId requires a uint32 object id")
        payload = struct.pack("<I", object_id) + payload
    elif object_id is not None:
        raise ProtocolError("object id is only valid for RawWithId")
    if len(payload) > 0xFF:
        raise ProtocolError("DataObjectArray payload exceeds uint8 length")
    return (
        struct.pack("<HBB", DATA_OBJECT_MAGIC, int(object_type), len(payload)) + payload
    )


def decode_data_object_array(data: bytes) -> DataObjectArray:
    """Decode a DataObjectArray wrapper."""

    if len(data) < 4:
        raise ProtocolError("DataObjectArray is shorter than four bytes")
    magic, object_type, length = struct.unpack_from("<HBB", data)
    if magic != DATA_OBJECT_MAGIC:
        raise ProtocolError(f"invalid DataObjectArray magic 0x{magic:04x}")
    if len(data) < 4 + length:
        raise ProtocolError("truncated DataObjectArray payload")

    payload = data[4 : 4 + length]
    object_id: int | None = None
    if object_type == DataObjectType.RAW_WITH_ID:
        if len(payload) < 4:
            raise ProtocolError("RawWithId payload is shorter than its object id")
        object_id = struct.unpack_from("<I", payload)[0]
        payload = payload[4:]
    return DataObjectArray(object_type, payload, object_id)


def encode_setup_code(setup_code: int) -> bytes:
    """Encode a nonzero application setup code as UInt32 little-endian."""

    if not 1 <= setup_code <= 0xFFFFFFFF:
        raise ProtocolError("setup code must be a nonzero uint32")
    return setup_code.to_bytes(4, "little")


def encode_user_override(
    preset: AirflowPreset | int,
    timeout_seconds: int,
    *,
    command: MevCommand | int = MevCommand.SET_SPEED,
    zone_id: int = 0,
    ventilation_mode: VentilationMode | int = VentilationMode.OFF,
) -> bytes:
    """Encode the Raw DataObjectArray user-override command body."""

    if not 0 <= timeout_seconds <= 0xFFFFFFFF:
        raise ProtocolError("override timeout must fit uint32")
    for label, value in (
        ("command", command),
        ("preset", preset),
        ("zone_id", zone_id),
        ("ventilation_mode", ventilation_mode),
    ):
        if not 0 <= int(value) <= 0xFF:
            raise ProtocolError(f"{label} must fit uint8")
    body = struct.pack(
        "<BBBBI",
        int(command),
        int(preset),
        zone_id,
        int(ventilation_mode),
        timeout_seconds,
    )
    return encode_data_object_array(DataObjectType.RAW, body)


def encode_cancel_override() -> bytes:
    """Encode the documented cancel-override command."""

    return encode_user_override(AirflowPreset.LOW, 0, command=MevCommand.CANCEL)


def encode_co2_calibration(
    reference_ppm: int,
    *,
    automatic_enabled: bool,
    start_forced_calibration: bool,
) -> bytes:
    """Encode the recovered Raw DataObjectArray CO2 calibration payload."""

    if not MIN_CO2_CALIBRATION_REFERENCE <= reference_ppm <= (
        MAX_CO2_CALIBRATION_REFERENCE
    ):
        raise ProtocolError(
            "CO2 calibration reference must be "
            f"{MIN_CO2_CALIBRATION_REFERENCE}.."
            f"{MAX_CO2_CALIBRATION_REFERENCE} ppm"
        )
    body = struct.pack(
        "<HBB",
        reference_ppm,
        automatic_enabled,
        start_forced_calibration,
    )
    return encode_data_object_array(DataObjectType.RAW, body)


@dataclass(frozen=True, slots=True)
class DeviceViewHeader:
    """Decoded device-table header."""

    row_count: int
    version: int


def decode_device_view_header(data: bytes) -> DeviceViewHeader:
    """Decode the device-table row count and record version."""

    if len(data) < 3:
        raise ProtocolError("device view header requires at least three bytes")
    return DeviceViewHeader(data[0], struct.unpack_from("<H", data, 1)[0])


@dataclass(frozen=True, slots=True)
class DeviceViewRow:
    """Routing fields shared by documented V5 and V6 device rows."""

    address: int
    device_type: int
    hardware_type: int


def decode_device_view_row(data: bytes) -> DeviceViewRow:
    """Decode the common routing prefix of a V5 or V6 device row."""

    if len(data) not in (34, 58):
        raise ProtocolError("device view row must be a 34-byte V6 or 58-byte V5 record")
    return DeviceViewRow(address=data[0], device_type=data[1], hardware_type=data[2])


def fragment_packet(packet: bytes, *, channel: int = 0) -> list[bytes]:
    """Split a packet into documented 20-byte legacy frames."""

    if not packet:
        raise ProtocolError("cannot fragment an empty packet")
    if not 0 <= channel <= 0xFF:
        raise ProtocolError("fragment channel must fit uint8")
    total = math.ceil(len(packet) / FRAGMENT_PAYLOAD_SIZE)
    if total > 15:
        raise ProtocolError("fragment count does not fit the header nibble")

    frames: list[bytes] = []
    for zero_based_index in range(total):
        sequence = zero_based_index + 1
        chunk = packet[
            zero_based_index * FRAGMENT_PAYLOAD_SIZE : sequence * FRAGMENT_PAYLOAD_SIZE
        ].ljust(FRAGMENT_PAYLOAD_SIZE, b"\0")
        frames.append(
            bytes(
                [
                    (sequence << 4) | total,
                    crc8_zirconia(chunk),
                    channel,
                ]
            )
            + chunk
        )
    return frames


def validate_fragment(frame: bytes) -> tuple[int, int]:
    """Validate a legacy frame and return ``(sequence, total)``."""

    if len(frame) != FRAGMENT_SIZE:
        raise ProtocolError("fragment must be exactly 20 bytes")
    sequence = frame[0] >> 4
    total = frame[0] & 0x0F
    if not sequence or not total or sequence > total:
        raise ProtocolError("invalid fragment sequence/total header")
    if crc8_zirconia(frame[3:]) != frame[1]:
        raise ProtocolError(f"fragment {sequence} checksum mismatch")
    return sequence, total


def reassemble_fragments(frames: Sequence[bytes]) -> bytes:
    """Validate and reassemble a complete ordered frame sequence."""

    if not frames:
        raise ProtocolError("no fragments supplied")
    first_sequence, expected_total = validate_fragment(frames[0])
    if first_sequence != 1 or expected_total != len(frames):
        raise ProtocolError(
            f"expected {expected_total} fragments beginning at one, got {len(frames)}"
        )
    payloads: list[bytes] = []
    for expected_sequence, frame in enumerate(frames, 1):
        sequence, total = validate_fragment(frame)
        if sequence != expected_sequence or total != expected_total:
            raise ProtocolError("fragment sequence/total header mismatch")
        payloads.append(frame[3:])
    padded = b"".join(payloads)
    if len(padded) >= 2 and PROTOCOL_HEADER_SIZE <= padded[1] <= len(padded):
        return padded[: padded[1]]
    return padded


def fragment_ack(sequence: int) -> bytes:
    """Encode a fragment acknowledgement or cancellation frame."""

    if not 0 <= sequence <= 0xFF:
        raise ProtocolError("fragment acknowledgement must fit uint8")
    return b"\x00\x00" + bytes([sequence]) + bytes(FRAGMENT_PAYLOAD_SIZE)


FRAGMENT_CANCEL: Final = fragment_ack(0xFF)


@dataclass(frozen=True, slots=True)
class ZoneTelemetry:
    """Decoded 21-byte V2 zone record."""

    co2_supported: bool
    fan_level: int
    fan_state: int
    fan_rpm: int
    temperature: float
    relative_humidity: float
    co2: float | None
    fault_mask: int


def decode_zone_telemetry(data: bytes) -> ZoneTelemetry:
    """Decode the documented 21-byte zone record."""

    if len(data) < 21:
        raise ProtocolError("zone telemetry requires 21 bytes")
    co2_supported, fan_level, fan_state, fan_rpm = struct.unpack_from("<BBBH", data)
    temperature, relative_humidity, co2_value = struct.unpack_from("<fff", data, 5)
    fault_mask = struct.unpack_from("<I", data, 17)[0]
    co2_is_valid = co2_supported and math.isfinite(co2_value) and co2_value > 0
    return ZoneTelemetry(
        co2_supported=bool(co2_supported),
        fan_level=fan_level,
        fan_state=fan_state,
        fan_rpm=fan_rpm,
        temperature=temperature,
        relative_humidity=relative_humidity,
        co2=co2_value if co2_is_valid else None,
        fault_mask=fault_mask,
    )


@dataclass(frozen=True, slots=True)
class SystemStatus:
    """Decoded system-status body."""

    fan_speed: int
    override_remaining: int | None
    fault_mask: int
    override_remaining_source: str = "device"


def decode_system_status(data: bytes) -> SystemStatus:
    """Decode the Raw DataObjectArray system-status response."""

    data_object = decode_data_object_array(data)
    if data_object.object_type != DataObjectType.RAW:
        raise ProtocolError("system status requires a Raw DataObjectArray")
    if len(data_object.payload) < 7:
        raise ProtocolError("system status payload requires at least seven bytes")
    fan_speed, override_remaining, fault_mask = struct.unpack_from(
        "<BHI", data_object.payload
    )
    return SystemStatus(fan_speed, override_remaining, fault_mask)


def decode_faults(mask: int) -> tuple[FaultFlag, ...]:
    """Return every documented flag present in a fault mask."""

    return tuple(flag for flag in FaultFlag if flag and mask & flag == flag)


def fan_state_name(value: int) -> str:
    """Return a stable state name while retaining unknown numeric states."""

    try:
        return FAN_STATE_NAMES[FanState(value)]
    except (ValueError, KeyError):
        return f"unknown_{value}"
