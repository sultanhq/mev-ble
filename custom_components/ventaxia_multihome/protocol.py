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
from itertools import permutations
from typing import Final

MAX_PACKET_SIZE: Final = 128
PROTOCOL_HEADER_SIZE: Final = 10
DATA_OBJECT_MAGIC: Final = 0x0ABA
FRAGMENT_SIZE: Final = 20
FRAGMENT_PAYLOAD_SIZE: Final = 17
MIN_CO2_CALIBRATION_REFERENCE: Final = 400
MAX_CO2_CALIBRATION_REFERENCE: Final = 2000
DEFAULT_CO2_CALIBRATION_REFERENCE: Final = 450
GLOBAL_SETTINGS_SIZE: Final = 36
MIN_BOOST_MINIMUM: Final = 0
MAX_BOOST_MINIMUM: Final = 100
MIN_GLOBAL_CO2_THRESHOLD: Final = 0
MAX_GLOBAL_CO2_THRESHOLD: Final = 2000
GLOBAL_CO2_THRESHOLD_STEP: Final = 10
MIN_GLOBAL_TIMER_MINUTES: Final = 1
MAX_GLOBAL_TIMER_MINUTES: Final = 60
MIN_LOW_TEMPERATURE_THRESHOLD: Final = 0
MAX_LOW_TEMPERATURE_THRESHOLD: Final = 30
MIN_HIGH_TEMPERATURE_THRESHOLD: Final = 15
MAX_HIGH_TEMPERATURE_THRESHOLD: Final = 40
SILENT_HOUR_SLOT_COUNT: Final = 6
SILENT_HOUR_RECORD_SIZE: Final = 9
SILENT_HOUR_TABLE_ITEM_SIZE: Final = 13
SECONDS_PER_DAY: Final = 86_400

AIRFLOW_SPEED_LIMITS: Final = {
    "low": (1, 97),
    "normal": (2, 98),
    "boost": (3, 99),
    "purge": (4, 100),
}

WHOLE_PACKET_ACK: Final = b"\x00\x00\x01"
WHOLE_PACKET_CANCEL: Final = b"\x00\x00\xff"


class ProtocolError(ValueError):
    """Base error raised for invalid protocol data."""


class PacketType(IntEnum):
    """Packet types used by the first integration release."""

    SILENT_HOURS = 49
    USER_OVERRIDE = 56
    SYSTEM_STATUS = 67
    CO2_CALIBRATION = 116
    GLOBAL_DATA_FIELD = 136
    GLOBAL_DATA = 137
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


class GlobalSettingField(IntEnum):
    """Official-app field identifiers for packet 136 updates."""

    SPEED_LOW = 0
    SPEED_MEDIUM = 1
    SPEED_BOOST = 2
    SPEED_PURGE = 3
    BOOST_MINIMUM = 4
    HUMIDITY_THRESHOLD = 5
    COMFORT_ENABLED = 6
    DELAY_ENABLED = 7
    OVERRUN_ENABLED = 8
    OVERRUN_TIMEOUT_MINUTES = 9
    DELAY_TIMEOUT_MINUTES = 10
    LS1_ACTION = 11
    LS2_ACTION = 12
    LS3_ACTION = 13
    RAPID_RESPONSE_ENABLED = 14
    AMBIENT_RESPONSE_ENABLED = 15
    LOW_TEMPERATURE_ENABLED = 16
    LOW_THRESHOLD_ACTION = 17
    HIGH_THRESHOLD_ACTION = 18
    LOW_TEMPERATURE_THRESHOLD = 19
    HIGH_TEMPERATURE_THRESHOLD = 20
    CO2_BOOST_THRESHOLD = 21
    CO2_PURGE_THRESHOLD = 22
    ANALOGUE_INPUT_1_LOW_ACTION = 23
    ANALOGUE_INPUT_1_HIGH_ACTION = 24
    ANALOGUE_INPUT_1_LOW_VALUE = 25
    ANALOGUE_INPUT_1_HIGH_VALUE = 26
    ANALOGUE_INPUT_2_LOW_ACTION = 27
    ANALOGUE_INPUT_2_HIGH_ACTION = 28
    ANALOGUE_INPUT_2_LOW_VALUE = 29
    ANALOGUE_INPUT_2_HIGH_VALUE = 30
    DIGITAL_INPUT_1_ACTION = 31
    DIGITAL_INPUT_2_ACTION = 32


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


class TemperatureThresholdAction(IntEnum):
    """Actions offered by the recovered Multihome temperature screen."""

    LOW = 1
    BOOST = 3
    PURGE = 4


TEMPERATURE_THRESHOLD_ACTION_NAMES: Final = {
    TemperatureThresholdAction.LOW: "low",
    TemperatureThresholdAction.BOOST: "boost",
    TemperatureThresholdAction.PURGE: "purge",
}


def temperature_threshold_action_name(action: int) -> str:
    """Return a known temperature action name without hiding unknown codes."""

    try:
        return TEMPERATURE_THRESHOLD_ACTION_NAMES[TemperatureThresholdAction(action)]
    except ValueError:
        return f"unknown_{action}"


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

    if (
        not MIN_CO2_CALIBRATION_REFERENCE
        <= reference_ppm
        <= (MAX_CO2_CALIBRATION_REFERENCE)
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
class SilentHour:
    """One recovered nine-byte silent-hours record."""

    start_seconds: int
    end_seconds: int
    weekdays_mask: int
    raw_record: bytes

    @property
    def is_valid(self) -> bool:
        """Return whether the record can safely be edited through the UI."""

        return (
            0 <= self.start_seconds < SECONDS_PER_DAY
            and 0 <= self.end_seconds < SECONDS_PER_DAY
            and 0 < self.weekdays_mask <= 0x7F
        )

    @property
    def is_overnight(self) -> bool:
        """Return whether the schedule crosses midnight."""

        return self.end_seconds <= self.start_seconds


@dataclass(frozen=True, slots=True)
class SilentHourSlot:
    """One indexed table response, including unknown raw firmware data."""

    index: int
    total_count: int
    record: SilentHour | None
    raw_payload: bytes
    is_known: bool = True


def preserve_unknown_silent_hour_slot(index: int, raw_payload: bytes) -> SilentHourSlot:
    """Retain an unsupported selected-slot response without interpreting it."""

    return SilentHourSlot(
        _validate_silent_hour_slot(index),
        0,
        None,
        bytes(raw_payload),
        False,
    )


def _validate_silent_hour_slot(index: int) -> int:
    """Validate and return one supported zero-based slot index."""

    if not 0 <= int(index) < SILENT_HOUR_SLOT_COUNT:
        raise ProtocolError(
            f"silent-hours slot must be 0..{SILENT_HOUR_SLOT_COUNT - 1}"
        )
    return int(index)


def encode_silent_hour(
    start_seconds: int, end_seconds: int, weekdays_mask: int
) -> bytes:
    """Encode one validated nine-byte silent-hours record."""

    if not 0 <= start_seconds < SECONDS_PER_DAY:
        raise ProtocolError("silent-hours start must be 0..86399 seconds")
    if not 0 <= end_seconds < SECONDS_PER_DAY:
        raise ProtocolError("silent-hours end must be 0..86399 seconds")
    if not 0 < weekdays_mask <= 0x7F:
        raise ProtocolError("silent-hours weekdays must select Monday..Sunday")
    return struct.pack("<IIB", start_seconds, end_seconds, weekdays_mask)


def decode_silent_hour(data: bytes) -> SilentHour:
    """Decode one record while retaining out-of-range firmware values."""

    if len(data) != SILENT_HOUR_RECORD_SIZE:
        raise ProtocolError("silent-hours record must be exactly nine bytes")
    start_seconds, end_seconds, weekdays_mask = struct.unpack("<IIB", data)
    return SilentHour(start_seconds, end_seconds, weekdays_mask, bytes(data))


def encode_silent_hour_request(index: int) -> bytes:
    """Encode the official app's indexed read request."""

    return struct.pack("<HH", _validate_silent_hour_slot(index), 0) + bytes(
        SILENT_HOUR_RECORD_SIZE
    )


def encode_silent_hour_update(index: int, record: SilentHour | bytes) -> bytes:
    """Encode an indexed create/update record."""

    raw_record = record.raw_record if isinstance(record, SilentHour) else bytes(record)
    if len(raw_record) != SILENT_HOUR_RECORD_SIZE:
        raise ProtocolError("silent-hours record must be exactly nine bytes")
    decoded = decode_silent_hour(raw_record)
    if not decoded.is_valid:
        raise ProtocolError("silent-hours update contains invalid time or weekday data")
    return struct.pack("<HH", _validate_silent_hour_slot(index), 0) + raw_record


def encode_silent_hour_delete(index: int) -> bytes:
    """Encode the recovered 0xffff slot-deletion marker."""

    return struct.pack("<HH", _validate_silent_hour_slot(index), 0xFFFF)


def decode_silent_hour_slot(
    data: bytes, *, expected_index: int | None = None
) -> SilentHourSlot:
    """Decode indexed and selected-slot firmware response forms losslessly."""

    raw_payload = bytes(data)
    payload = raw_payload
    if len(payload) >= 4 and payload[:2] == struct.pack("<H", DATA_OBJECT_MAGIC):
        data_object = decode_data_object_array(payload)
        if data_object.object_type != DataObjectType.RAW:
            raise ProtocolError(
                "silent-hours DataObjectArray response must contain Raw data"
            )
        payload = data_object.payload

    if len(payload) == SILENT_HOUR_TABLE_ITEM_SIZE + 1:
        expected_checksum = crc8_zirconia(payload[:-1])
        if payload[-1] != expected_checksum:
            raise ProtocolError(
                "silent-hours table checksum mismatch: "
                f"expected {expected_checksum:02x}, received {payload[-1]:02x}"
            )
        payload = payload[:-1]

    if len(payload) in (0, SILENT_HOUR_RECORD_SIZE):
        if expected_index is None:
            raise ProtocolError(
                "selected silent-hours response requires its requested slot index"
            )
        index = _validate_silent_hour_slot(expected_index)
        total_count = 0
        record_payload = payload
    elif len(payload) in (4, SILENT_HOUR_TABLE_ITEM_SIZE):
        index, total_count = struct.unpack_from("<HH", payload)
        _validate_silent_hour_slot(index)
        record_payload = payload[4:]
    else:
        raise ProtocolError(
            "silent-hours response has unsupported payload "
            f"length {len(payload)}: {payload.hex()}"
        )

    _validate_silent_hour_slot(index)
    record: SilentHour | None = None
    if len(record_payload) == SILENT_HOUR_RECORD_SIZE:
        raw_record = record_payload
        decoded = decode_silent_hour(raw_record)
        if raw_record != bytes(SILENT_HOUR_RECORD_SIZE):
            record = decoded
    return SilentHourSlot(index, total_count, record, raw_payload)


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


@dataclass(frozen=True, slots=True)
class GlobalSettings:
    """Losslessly decoded 36-byte MEV global-settings record."""

    raw_record: bytes
    speed_low: int
    speed_medium: int
    speed_boost: int
    speed_purge: int
    boost_minimum: int
    humidity_threshold: int
    comfort_enabled: bool | None
    delay_enabled: bool | None
    overrun_enabled: bool | None
    rapid_response_enabled: bool | None
    ambient_response_enabled: bool | None
    low_temperature_enabled: bool | None
    low_threshold_action: int
    high_threshold_action: int
    low_temperature_threshold: int
    high_temperature_threshold: int
    purge_low_mode: int
    overrun_timeout_minutes: int
    delay_timeout_minutes: int
    ls1_action: int
    ls2_action: int
    ls3_action: int
    co2_boost_threshold: int
    co2_purge_threshold: int
    analogue_input_1_low_value: int
    analogue_input_1_high_value: int
    analogue_input_1_low_action: int
    analogue_input_1_high_action: int
    analogue_input_2_low_value: int
    analogue_input_2_high_value: int
    analogue_input_2_low_action: int
    analogue_input_2_high_action: int
    digital_input_1_action: int
    digital_input_2_action: int

    @property
    def invalid_boolean_fields(self) -> tuple[str, ...]:
        """Return flag fields whose raw byte was neither zero nor one."""

        return tuple(
            name
            for name in (
                "comfort_enabled",
                "delay_enabled",
                "overrun_enabled",
                "rapid_response_enabled",
                "ambient_response_enabled",
                "low_temperature_enabled",
            )
            if getattr(self, name) is None
        )


@dataclass(frozen=True, slots=True)
class GlobalSettingFieldSpec:
    """Encoding and readback location for one packet 136 field."""

    attribute: str
    record_offset: int
    minimum: int
    maximum: int
    boolean: bool = False
    co2: bool = False


GLOBAL_SETTING_FIELD_SPECS: Final = {
    GlobalSettingField.SPEED_LOW: GlobalSettingFieldSpec("speed_low", 0, 1, 97),
    GlobalSettingField.SPEED_MEDIUM: GlobalSettingFieldSpec("speed_medium", 1, 2, 98),
    GlobalSettingField.SPEED_BOOST: GlobalSettingFieldSpec("speed_boost", 2, 3, 99),
    GlobalSettingField.SPEED_PURGE: GlobalSettingFieldSpec("speed_purge", 3, 4, 100),
    GlobalSettingField.BOOST_MINIMUM: GlobalSettingFieldSpec(
        "boost_minimum", 4, MIN_BOOST_MINIMUM, MAX_BOOST_MINIMUM
    ),
    GlobalSettingField.HUMIDITY_THRESHOLD: GlobalSettingFieldSpec(
        "humidity_threshold", 5, 0, 100
    ),
    GlobalSettingField.COMFORT_ENABLED: GlobalSettingFieldSpec(
        "comfort_enabled", 6, 0, 1, boolean=True
    ),
    GlobalSettingField.DELAY_ENABLED: GlobalSettingFieldSpec(
        "delay_enabled", 7, 0, 1, boolean=True
    ),
    GlobalSettingField.OVERRUN_ENABLED: GlobalSettingFieldSpec(
        "overrun_enabled", 8, 0, 1, boolean=True
    ),
    GlobalSettingField.OVERRUN_TIMEOUT_MINUTES: GlobalSettingFieldSpec(
        "overrun_timeout_minutes",
        17,
        MIN_GLOBAL_TIMER_MINUTES,
        MAX_GLOBAL_TIMER_MINUTES,
    ),
    GlobalSettingField.DELAY_TIMEOUT_MINUTES: GlobalSettingFieldSpec(
        "delay_timeout_minutes",
        18,
        MIN_GLOBAL_TIMER_MINUTES,
        MAX_GLOBAL_TIMER_MINUTES,
    ),
    GlobalSettingField.LS1_ACTION: GlobalSettingFieldSpec("ls1_action", 19, 0, 0xFF),
    GlobalSettingField.LS2_ACTION: GlobalSettingFieldSpec("ls2_action", 20, 0, 0xFF),
    GlobalSettingField.LS3_ACTION: GlobalSettingFieldSpec("ls3_action", 21, 0, 0xFF),
    GlobalSettingField.RAPID_RESPONSE_ENABLED: GlobalSettingFieldSpec(
        "rapid_response_enabled", 9, 0, 1, boolean=True
    ),
    GlobalSettingField.AMBIENT_RESPONSE_ENABLED: GlobalSettingFieldSpec(
        "ambient_response_enabled", 10, 0, 1, boolean=True
    ),
    GlobalSettingField.LOW_TEMPERATURE_ENABLED: GlobalSettingFieldSpec(
        "low_temperature_enabled", 11, 0, 1, boolean=True
    ),
    GlobalSettingField.LOW_THRESHOLD_ACTION: GlobalSettingFieldSpec(
        "low_threshold_action", 12, 0, 0xFF
    ),
    GlobalSettingField.HIGH_THRESHOLD_ACTION: GlobalSettingFieldSpec(
        "high_threshold_action", 13, 0, 0xFF
    ),
    GlobalSettingField.LOW_TEMPERATURE_THRESHOLD: GlobalSettingFieldSpec(
        "low_temperature_threshold",
        14,
        MIN_LOW_TEMPERATURE_THRESHOLD,
        MAX_LOW_TEMPERATURE_THRESHOLD,
    ),
    GlobalSettingField.HIGH_TEMPERATURE_THRESHOLD: GlobalSettingFieldSpec(
        "high_temperature_threshold",
        15,
        MIN_HIGH_TEMPERATURE_THRESHOLD,
        MAX_HIGH_TEMPERATURE_THRESHOLD,
    ),
    GlobalSettingField.CO2_BOOST_THRESHOLD: GlobalSettingFieldSpec(
        "co2_boost_threshold",
        22,
        MIN_GLOBAL_CO2_THRESHOLD,
        MAX_GLOBAL_CO2_THRESHOLD,
        co2=True,
    ),
    GlobalSettingField.CO2_PURGE_THRESHOLD: GlobalSettingFieldSpec(
        "co2_purge_threshold",
        24,
        MIN_GLOBAL_CO2_THRESHOLD,
        MAX_GLOBAL_CO2_THRESHOLD,
        co2=True,
    ),
    GlobalSettingField.ANALOGUE_INPUT_1_LOW_ACTION: GlobalSettingFieldSpec(
        "analogue_input_1_low_action", 28, 0, 0xFF
    ),
    GlobalSettingField.ANALOGUE_INPUT_1_HIGH_ACTION: GlobalSettingFieldSpec(
        "analogue_input_1_high_action", 29, 0, 0xFF
    ),
    GlobalSettingField.ANALOGUE_INPUT_1_LOW_VALUE: GlobalSettingFieldSpec(
        "analogue_input_1_low_value", 26, 0, 100
    ),
    GlobalSettingField.ANALOGUE_INPUT_1_HIGH_VALUE: GlobalSettingFieldSpec(
        "analogue_input_1_high_value", 27, 0, 100
    ),
    GlobalSettingField.ANALOGUE_INPUT_2_LOW_ACTION: GlobalSettingFieldSpec(
        "analogue_input_2_low_action", 32, 0, 0xFF
    ),
    GlobalSettingField.ANALOGUE_INPUT_2_HIGH_ACTION: GlobalSettingFieldSpec(
        "analogue_input_2_high_action", 33, 0, 0xFF
    ),
    GlobalSettingField.ANALOGUE_INPUT_2_LOW_VALUE: GlobalSettingFieldSpec(
        "analogue_input_2_low_value", 30, 0, 100
    ),
    GlobalSettingField.ANALOGUE_INPUT_2_HIGH_VALUE: GlobalSettingFieldSpec(
        "analogue_input_2_high_value", 31, 0, 100
    ),
    GlobalSettingField.DIGITAL_INPUT_1_ACTION: GlobalSettingFieldSpec(
        "digital_input_1_action", 34, 0, 0xFF
    ),
    GlobalSettingField.DIGITAL_INPUT_2_ACTION: GlobalSettingFieldSpec(
        "digital_input_2_action", 35, 0, 0xFF
    ),
}


def _decode_global_boolean(value: int) -> bool | None:
    """Decode a strict zero/one flag without inventing unknown semantics."""

    if value == 0:
        return False
    if value == 1:
        return True
    return None


def _decode_global_co2(data: bytes, offset: int) -> int:
    """Decode the app's two-byte decimal CO2 threshold representation."""

    return data[offset] * 10 + data[offset + 1]


def decode_global_settings(data: bytes) -> GlobalSettings:
    """Decode the documented MEV global-settings record at every offset."""

    if len(data) != GLOBAL_SETTINGS_SIZE:
        raise ProtocolError(
            "global settings require exactly "
            f"{GLOBAL_SETTINGS_SIZE} bytes; got {len(data)}"
        )
    raw_record = bytes(data)
    return GlobalSettings(
        raw_record=raw_record,
        speed_low=data[0],
        speed_medium=data[1],
        speed_boost=data[2],
        speed_purge=data[3],
        boost_minimum=data[4],
        humidity_threshold=data[5],
        comfort_enabled=_decode_global_boolean(data[6]),
        delay_enabled=_decode_global_boolean(data[7]),
        overrun_enabled=_decode_global_boolean(data[8]),
        rapid_response_enabled=_decode_global_boolean(data[9]),
        ambient_response_enabled=_decode_global_boolean(data[10]),
        low_temperature_enabled=_decode_global_boolean(data[11]),
        low_threshold_action=data[12],
        high_threshold_action=data[13],
        low_temperature_threshold=data[14],
        high_temperature_threshold=data[15],
        purge_low_mode=data[16],
        overrun_timeout_minutes=data[17],
        delay_timeout_minutes=data[18],
        ls1_action=data[19],
        ls2_action=data[20],
        ls3_action=data[21],
        co2_boost_threshold=_decode_global_co2(data, 22),
        co2_purge_threshold=_decode_global_co2(data, 24),
        analogue_input_1_low_value=data[26],
        analogue_input_1_high_value=data[27],
        analogue_input_1_low_action=data[28],
        analogue_input_1_high_action=data[29],
        analogue_input_2_low_value=data[30],
        analogue_input_2_high_value=data[31],
        analogue_input_2_low_action=data[32],
        analogue_input_2_high_action=data[33],
        digital_input_1_action=data[34],
        digital_input_2_action=data[35],
    )


def encode_global_settings(settings: GlobalSettings) -> bytes:
    """Serialize a read-only snapshot without losing unknown raw values."""

    if len(settings.raw_record) != GLOBAL_SETTINGS_SIZE:
        raise ProtocolError("global settings snapshot has an invalid raw record")
    return settings.raw_record


def _normalize_global_setting_field(
    field: GlobalSettingField | int,
) -> GlobalSettingField:
    """Return a known setting field or reject an unsupported numeric ID."""

    if isinstance(field, bool) or not isinstance(field, int):
        raise ProtocolError(f"unknown global setting field ID {field}")
    try:
        return GlobalSettingField(field)
    except (TypeError, ValueError) as err:
        raise ProtocolError(f"unknown global setting field ID {field}") from err


def encode_global_setting_value(
    field: GlobalSettingField | int, value: int | bool
) -> bytes:
    """Encode one strictly validated packet 136 field value."""

    normalized_field = _normalize_global_setting_field(field)
    spec = GLOBAL_SETTING_FIELD_SPECS[normalized_field]
    if spec.boolean:
        if not isinstance(value, bool):
            raise ProtocolError(f"{spec.attribute} requires a boolean value")
        normalized_value = int(value)
    else:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ProtocolError(f"{spec.attribute} requires an integer value")
        normalized_value = value
    if not spec.minimum <= normalized_value <= spec.maximum:
        raise ProtocolError(f"{spec.attribute} must be {spec.minimum}..{spec.maximum}")
    if spec.co2:
        if normalized_value % GLOBAL_CO2_THRESHOLD_STEP:
            raise ProtocolError(
                f"{spec.attribute} must use {GLOBAL_CO2_THRESHOLD_STEP} ppm increments"
            )
        return struct.pack("<H", normalized_value // GLOBAL_CO2_THRESHOLD_STEP)
    return bytes([normalized_value])


def encode_global_setting_update(
    field: GlobalSettingField | int, value: int | bool
) -> bytes:
    """Encode the official app's RawWithId body for one setting update."""

    normalized_field = _normalize_global_setting_field(field)
    return encode_data_object_array(
        DataObjectType.RAW_WITH_ID,
        encode_global_setting_value(normalized_field, value),
        object_id=int(normalized_field),
    )


def global_settings_after_update(
    settings: GlobalSettings,
    field: GlobalSettingField | int,
    value: int | bool,
) -> GlobalSettings:
    """Return the exact record expected after one isolated field update."""

    if len(settings.raw_record) != GLOBAL_SETTINGS_SIZE:
        raise ProtocolError("global settings snapshot has an invalid raw record")
    normalized_field = _normalize_global_setting_field(field)
    spec = GLOBAL_SETTING_FIELD_SPECS[normalized_field]
    encoded_value = encode_global_setting_value(normalized_field, value)
    expected = bytearray(settings.raw_record)
    expected[spec.record_offset : spec.record_offset + len(encoded_value)] = (
        encoded_value
    )
    return decode_global_settings(bytes(expected))


def validate_airflow_profile(
    low: int,
    normal: int,
    boost: int,
    purge: int,
) -> None:
    """Validate the official commissioning ranges and strict speed ordering."""

    values = {
        "low": low,
        "normal": normal,
        "boost": boost,
        "purge": purge,
    }
    for name, value in values.items():
        minimum, maximum = AIRFLOW_SPEED_LIMITS[name]
        if isinstance(value, bool) or not isinstance(value, int):
            raise ProtocolError(f"airflow {name} requires an integer percentage")
        if not minimum <= value <= maximum:
            raise ProtocolError(f"airflow {name} must be {minimum}..{maximum} percent")
    if not low < normal < boost < purge:
        raise ProtocolError("airflow speeds must satisfy Low < Normal < Boost < Purge")


def plan_airflow_profile_updates(
    settings: GlobalSettings,
    *,
    low: int,
    normal: int,
    boost: int,
    purge: int,
) -> tuple[tuple[GlobalSettingField, int], ...]:
    """Plan one-field writes without creating an invalid intermediate profile."""

    validate_airflow_profile(low, normal, boost, purge)
    validate_airflow_profile(
        settings.speed_low,
        settings.speed_medium,
        settings.speed_boost,
        settings.speed_purge,
    )
    desired = {
        GlobalSettingField.SPEED_LOW: low,
        GlobalSettingField.SPEED_MEDIUM: normal,
        GlobalSettingField.SPEED_BOOST: boost,
        GlobalSettingField.SPEED_PURGE: purge,
    }
    current = {
        GlobalSettingField.SPEED_LOW: settings.speed_low,
        GlobalSettingField.SPEED_MEDIUM: settings.speed_medium,
        GlobalSettingField.SPEED_BOOST: settings.speed_boost,
        GlobalSettingField.SPEED_PURGE: settings.speed_purge,
    }
    changed = tuple(
        field for field, value in desired.items() if current[field] != value
    )
    for order in permutations(changed):
        candidate = dict(current)
        for field in order:
            candidate[field] = desired[field]
            try:
                validate_airflow_profile(
                    candidate[GlobalSettingField.SPEED_LOW],
                    candidate[GlobalSettingField.SPEED_MEDIUM],
                    candidate[GlobalSettingField.SPEED_BOOST],
                    candidate[GlobalSettingField.SPEED_PURGE],
                )
            except ProtocolError:
                break
        else:
            return tuple((field, desired[field]) for field in order)
    raise ProtocolError(
        "airflow profile cannot be applied safely as isolated field updates"
    )


def validate_sensor_thresholds(
    humidity: int,
    co2_boost: int,
    co2_purge: int,
) -> None:
    """Validate recovered threshold encodings and their safe ordering."""

    values = {
        GlobalSettingField.HUMIDITY_THRESHOLD: humidity,
        GlobalSettingField.CO2_BOOST_THRESHOLD: co2_boost,
        GlobalSettingField.CO2_PURGE_THRESHOLD: co2_purge,
    }
    for field, value in values.items():
        spec = GLOBAL_SETTING_FIELD_SPECS[field]
        if isinstance(value, bool) or not isinstance(value, int):
            raise ProtocolError(f"{spec.attribute} requires an integer")
        if not spec.minimum <= value <= spec.maximum:
            raise ProtocolError(
                f"{spec.attribute} must be {spec.minimum}..{spec.maximum}"
            )
    if co2_boost % GLOBAL_CO2_THRESHOLD_STEP:
        raise ProtocolError(
            f"CO2 boost threshold must use {GLOBAL_CO2_THRESHOLD_STEP} ppm steps"
        )
    if co2_purge % GLOBAL_CO2_THRESHOLD_STEP:
        raise ProtocolError(
            f"CO2 purge threshold must use {GLOBAL_CO2_THRESHOLD_STEP} ppm steps"
        )
    if co2_boost >= co2_purge:
        raise ProtocolError("CO2 thresholds must satisfy Boost < Purge")


def plan_sensor_threshold_updates(
    settings: GlobalSettings,
    *,
    humidity: int,
    co2_boost: int,
    co2_purge: int,
) -> tuple[tuple[GlobalSettingField, int], ...]:
    """Plan isolated threshold writes without an invalid intermediate state."""

    validate_sensor_thresholds(humidity, co2_boost, co2_purge)
    validate_sensor_thresholds(
        settings.humidity_threshold,
        settings.co2_boost_threshold,
        settings.co2_purge_threshold,
    )
    desired = {
        GlobalSettingField.HUMIDITY_THRESHOLD: humidity,
        GlobalSettingField.CO2_BOOST_THRESHOLD: co2_boost,
        GlobalSettingField.CO2_PURGE_THRESHOLD: co2_purge,
    }
    current = {
        GlobalSettingField.HUMIDITY_THRESHOLD: settings.humidity_threshold,
        GlobalSettingField.CO2_BOOST_THRESHOLD: settings.co2_boost_threshold,
        GlobalSettingField.CO2_PURGE_THRESHOLD: settings.co2_purge_threshold,
    }
    changed = tuple(
        field for field, value in desired.items() if current[field] != value
    )
    for order in permutations(changed):
        candidate = dict(current)
        for field in order:
            candidate[field] = desired[field]
            try:
                validate_sensor_thresholds(
                    candidate[GlobalSettingField.HUMIDITY_THRESHOLD],
                    candidate[GlobalSettingField.CO2_BOOST_THRESHOLD],
                    candidate[GlobalSettingField.CO2_PURGE_THRESHOLD],
                )
            except ProtocolError:
                break
        else:
            return tuple((field, desired[field]) for field in order)
    raise ProtocolError(
        "sensor thresholds cannot be applied safely as isolated field updates"
    )


def validate_temperature_threshold_profile(
    low_action: int,
    high_action: int,
    low_threshold: int,
    high_threshold: int,
) -> None:
    """Validate the recovered Multihome temperature choices conservatively."""

    known_actions = {int(action) for action in TEMPERATURE_THRESHOLD_ACTION_NAMES}
    for name, value in {
        "low temperature action": low_action,
        "high temperature action": high_action,
    }.items():
        if isinstance(value, bool) or not isinstance(value, int):
            raise ProtocolError(f"{name} requires an integer action code")
        if value not in known_actions:
            raise ProtocolError(f"{name} is not a recovered app choice")
    for field, value in {
        GlobalSettingField.LOW_TEMPERATURE_THRESHOLD: low_threshold,
        GlobalSettingField.HIGH_TEMPERATURE_THRESHOLD: high_threshold,
    }.items():
        spec = GLOBAL_SETTING_FIELD_SPECS[field]
        if isinstance(value, bool) or not isinstance(value, int):
            raise ProtocolError(f"{spec.attribute} requires an integer")
        if not spec.minimum <= value <= spec.maximum:
            raise ProtocolError(
                f"{spec.attribute} must be {spec.minimum}..{spec.maximum}"
            )
    if low_threshold >= high_threshold:
        raise ProtocolError("temperature thresholds must satisfy Low < High")


def plan_temperature_validation_update(
    settings: GlobalSettings,
    *,
    low_action: int,
    high_action: int,
    low_threshold: int,
    high_threshold: int,
) -> tuple[GlobalSettingField, int]:
    """Plan exactly one reversible temperature-field validation write."""

    if settings.low_temperature_enabled is not False:
        raise ProtocolError(
            "temperature validation requires low-temperature protection disabled"
        )
    validate_temperature_threshold_profile(
        settings.low_threshold_action,
        settings.high_threshold_action,
        settings.low_temperature_threshold,
        settings.high_temperature_threshold,
    )
    validate_temperature_threshold_profile(
        low_action,
        high_action,
        low_threshold,
        high_threshold,
    )
    current = {
        GlobalSettingField.LOW_THRESHOLD_ACTION: settings.low_threshold_action,
        GlobalSettingField.HIGH_THRESHOLD_ACTION: settings.high_threshold_action,
        GlobalSettingField.LOW_TEMPERATURE_THRESHOLD: (
            settings.low_temperature_threshold
        ),
        GlobalSettingField.HIGH_TEMPERATURE_THRESHOLD: (
            settings.high_temperature_threshold
        ),
    }
    desired = {
        GlobalSettingField.LOW_THRESHOLD_ACTION: low_action,
        GlobalSettingField.HIGH_THRESHOLD_ACTION: high_action,
        GlobalSettingField.LOW_TEMPERATURE_THRESHOLD: low_threshold,
        GlobalSettingField.HIGH_TEMPERATURE_THRESHOLD: high_threshold,
    }
    changed = tuple(
        (field, value) for field, value in desired.items() if current[field] != value
    )
    if len(changed) != 1:
        raise ProtocolError("temperature validation requires exactly one changed field")
    return changed[0]


def plan_low_temperature_protection_validation_update(
    settings: GlobalSettings,
    *,
    enabled: bool,
) -> tuple[GlobalSettingField, bool]:
    """Plan one guarded field-16 validation write."""

    if settings.low_temperature_enabled is None:
        raise ProtocolError("low-temperature protection state is unavailable")
    if not isinstance(enabled, bool):
        raise ProtocolError("low-temperature protection requires a boolean value")
    validate_temperature_threshold_profile(
        settings.low_threshold_action,
        settings.high_threshold_action,
        settings.low_temperature_threshold,
        settings.high_temperature_threshold,
    )
    if enabled == settings.low_temperature_enabled:
        raise ProtocolError("low-temperature protection value is unchanged")
    return GlobalSettingField.LOW_TEMPERATURE_ENABLED, enabled


def plan_humidity_response_updates(
    settings: GlobalSettings,
    *,
    rapid: bool,
    ambient: bool,
) -> tuple[tuple[GlobalSettingField, bool], ...]:
    """Plan strict boolean humidity-response writes in field order."""

    if not isinstance(rapid, bool) or not isinstance(ambient, bool):
        raise ProtocolError("humidity response settings require boolean values")
    if (
        settings.rapid_response_enabled is None
        or settings.ambient_response_enabled is None
    ):
        raise ProtocolError("current humidity response settings are invalid")
    desired = (
        (GlobalSettingField.RAPID_RESPONSE_ENABLED, rapid),
        (GlobalSettingField.AMBIENT_RESPONSE_ENABLED, ambient),
    )
    return tuple(
        (field, value)
        for field, value in desired
        if getattr(settings, GLOBAL_SETTING_FIELD_SPECS[field].attribute) != value
    )


def plan_comfort_mode_update(
    settings: GlobalSettings,
    *,
    enabled: bool,
) -> tuple[tuple[GlobalSettingField, bool], ...]:
    """Plan one strict boolean comfort-mode write when its value changes."""

    if not isinstance(enabled, bool):
        raise ProtocolError("comfort mode requires a boolean value")
    if settings.comfort_enabled is None:
        raise ProtocolError("current comfort mode setting is invalid")
    if settings.comfort_enabled == enabled:
        return ()
    return ((GlobalSettingField.COMFORT_ENABLED, enabled),)


def plan_delay_overrun_updates(
    settings: GlobalSettings,
    *,
    delay_enabled: bool,
    delay_minutes: int,
    overrun_enabled: bool,
    overrun_minutes: int,
) -> tuple[tuple[GlobalSettingField, int | bool], ...]:
    """Plan paired LS timer writes without invalid enabled intermediates."""

    if not isinstance(delay_enabled, bool) or not isinstance(overrun_enabled, bool):
        raise ProtocolError("delay and overrun enabled values must be boolean")
    for name, value in (
        ("delay", delay_minutes),
        ("overrun", overrun_minutes),
    ):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ProtocolError(f"{name} timeout requires whole minutes")
        if not MIN_GLOBAL_TIMER_MINUTES <= value <= MAX_GLOBAL_TIMER_MINUTES:
            raise ProtocolError(
                f"{name} timeout must be "
                f"{MIN_GLOBAL_TIMER_MINUTES}..{MAX_GLOBAL_TIMER_MINUTES} minutes"
            )
    if settings.delay_enabled is None or settings.overrun_enabled is None:
        raise ProtocolError("current delay or overrun enabled value is invalid")
    if not (
        MIN_GLOBAL_TIMER_MINUTES
        <= settings.delay_timeout_minutes
        <= MAX_GLOBAL_TIMER_MINUTES
        and MIN_GLOBAL_TIMER_MINUTES
        <= settings.overrun_timeout_minutes
        <= MAX_GLOBAL_TIMER_MINUTES
    ):
        raise ProtocolError("current delay or overrun timeout is outside 1..60 minutes")

    updates: list[tuple[GlobalSettingField, int | bool]] = []
    pairs = (
        (
            GlobalSettingField.DELAY_ENABLED,
            GlobalSettingField.DELAY_TIMEOUT_MINUTES,
            settings.delay_enabled,
            settings.delay_timeout_minutes,
            delay_enabled,
            delay_minutes,
        ),
        (
            GlobalSettingField.OVERRUN_ENABLED,
            GlobalSettingField.OVERRUN_TIMEOUT_MINUTES,
            settings.overrun_enabled,
            settings.overrun_timeout_minutes,
            overrun_enabled,
            overrun_minutes,
        ),
    )
    for (
        enabled_field,
        timeout_field,
        current_enabled,
        current_minutes,
        desired_enabled,
        desired_minutes,
    ) in pairs:
        if current_enabled and not desired_enabled:
            updates.append((enabled_field, False))
        if current_minutes != desired_minutes:
            updates.append((timeout_field, desired_minutes))
        if not current_enabled and desired_enabled:
            updates.append((enabled_field, True))
    return tuple(updates)


def decode_faults(mask: int) -> tuple[FaultFlag, ...]:
    """Return every documented flag present in a fault mask."""

    return tuple(flag for flag in FaultFlag if flag and mask & flag == flag)


def fan_state_name(value: int) -> str:
    """Return a stable state name while retaining unknown numeric states."""

    try:
        return FAN_STATE_NAMES[FanState(value)]
    except (ValueError, KeyError):
        return f"unknown_{value}"
