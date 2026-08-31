#!/usr/bin/env python3
"""Offline reference codec for the Vent-Axia MEV/Multihome BLE protocol.

This module performs no Bluetooth I/O.  It is a small, dependency-free
translation of the packet formats found in Vent-Axia Connect 7.2.2, with the
compatible fragmented transport retained from 6.0.28.
"""

from __future__ import annotations

import argparse
import json
import math
import struct
import time
from dataclasses import asdict, dataclass
from enum import IntEnum
from typing import Iterable, Sequence


PROTOCOL_SERVICE_UUID = "e6ec2fd8-e888-4eb2-9680-e78ed6ea89e1"
FRAGMENT_CHARACTERISTIC_UUID = "e6ec2fd8-e888-4eb2-9681-e78ed6ea89e1"
WHOLE_PACKET_CHARACTERISTIC_UUID = "a8e23cea-978d-ac8d-374c-cbb4eeb63f41"
NOTIFIER_CHARACTERISTIC_UUID = "6b1526ff-e3de-1fab-2c4e-10ae479b9245"

CONNECTION_SERVICE_UUID = "e6834e4b-7b3a-48e6-91e4-f1d005f564d3"
PIN_CHARACTERISTIC_UUID = "4cad343a-209a-40b7-b911-4d9b3df569b2"
PIN_CONFIRM_CHARACTERISTIC_UUID = "d1ae6b70-ee12-4f6d-b166-d2063dcaffe1"
DESCRIPTION_CHARACTERISTIC_UUID = "b85fa07a-9382-4838-871c-81d045dcc2ff"
BLUETOOTH_ADDRESS_CHARACTERISTIC_UUID = "638ff62c-3823-4e0f-8179-1695c46ee8af"


class PacketType(IntEnum):
    FLASH_SILENT_HOURS_PARAMETER = 49
    USER_OVERRIDE = 56
    HARD_RESET = 61
    SYSTEM_STATUS = 67
    CO2_CALIBRATION = 116
    GLOBAL_DATA_FIELD = 136
    GLOBAL_DATA = 137
    DEVICE_ROW_FIELD = 140
    DEVICE_VIEW_HEADER = 141
    DEVICE_VIEW_ROW = 142
    ZONE_ROW_FIELD = 144
    ZONE_VIEW_HEADER = 145
    ZONE_VIEW_ROW = 146


class Operation(IntEnum):
    NONE = 0
    UPDATE = 1
    DATA_REQUEST = 2
    ACKNOWLEDGEMENT_REQUEST = 4
    RESERVED_1 = 8
    SUBSCRIBE = 16
    ACKNOWLEDGE = 32
    RESPONSE = 64
    ENCRYPTED = 128


class DataObjectArrayType(IntEnum):
    RAW = 0
    OBJECT = 1
    OBJECT_ARRAY = 2
    PROTOCOL = 3
    TEXT = 4
    INT_ARRAY = 5
    FLOAT_ARRAY = 6
    RAW_WITH_ID = 7


class MevCommand(IntEnum):
    NO_TYPE = 0
    SET_SPEED = 1
    CANCEL = 2


class AirflowPreset(IntEnum):
    LOW = 1
    NORMAL = 2
    BOOST = 3
    PURGE = 4


class VentilationMode(IntEnum):
    HEAT_RECOVERY = 1
    VENTILATION = 2
    OFF = 3
    STOP = 4


FAULT_FLAGS = {
    1: "NoError",
    2: "MotorNotRunning",
    4: "InternalTemperatureSensorFault",
    8: "InternalCO2SensorFault",
    16: "ExternalTemperatureSensorOffline",
    32: "ExternalCO2SensorOffline",
    64: "SpeedSwitchUnitOffline",
    128: "AlarmTriggered",
    256: "DeviceLost",
    512: "FirmwareUpdateFailed",
    1024: "BatteryCritical",
    2048: "FilterTimeout",
    4096: "ServiceTimeout",
    8192: "Notification",
}


def crc8_zirconia(data: bytes | bytearray | memoryview) -> int:
    """Return the app's nonstandard 'CRC8 Zirconia' value.

    The implementation shifts first and tests bit 7 afterwards.  That detail
    makes it differ from a conventional CRC-8/ATM implementation even though
    both use polynomial 0x07.
    """

    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc << 1) & 0xFF
            if crc & 0x80:
                crc ^= 0x07
    return crc


@dataclass(frozen=True)
class ProtocolPacket:
    packet_type: int
    operation: int
    target: int
    timestamp_be: int
    timestamp_le: int
    backtracking_count: int
    payload: bytes
    checksum: int

    def to_json(self) -> dict[str, object]:
        result = asdict(self)
        result["payload"] = self.payload.hex()
        return result


def serialize_protocol_v2(
    packet_type: int,
    operation: int,
    payload: bytes = b"",
    *,
    target: int = 0,
    timestamp: int | None = None,
    backtracking_count: int = 0,
) -> bytes:
    """Serialize the 7.x 10-byte-header protocol packet.

    Connect 7.2.2 writes a Unix timestamp in big-endian order.  Its own
    deserializer reads those four bytes as little-endian; ``deserialize``
    exposes both interpretations rather than hiding that code-level anomaly.
    """

    if timestamp is None:
        timestamp = int(time.time())
    if not 0 <= timestamp <= 0xFFFFFFFF:
        raise ValueError("timestamp must fit uint32")
    for label, value in (
        ("packet_type", packet_type),
        ("operation", operation),
        ("target", target),
        ("backtracking_count", backtracking_count),
    ):
        if not 0 <= int(value) <= 0xFF:
            raise ValueError(f"{label} must fit uint8")
    if backtracking_count:
        raise ValueError("reference serializer only supports the app's normal zero-backtracking requests")
    total_size = 10 + len(payload)
    if total_size > 128:
        raise ValueError("protocol packet exceeds the app's 128-byte maximum")
    packet = bytearray(total_size)
    packet[1] = total_size
    packet[2] = backtracking_count
    packet[3] = int(packet_type)
    packet[4] = int(operation)
    packet[5] = target
    packet[6:10] = timestamp.to_bytes(4, "big")
    packet[10:] = payload
    packet[0] = crc8_zirconia(packet[1:])
    return bytes(packet)


def deserialize_protocol_v2(data: bytes, *, validate_checksum: bool = True) -> ProtocolPacket:
    if len(data) < 10:
        raise ValueError("protocol packet is shorter than its 10-byte header")
    total_size = data[1]
    if total_size < 10 or total_size > len(data):
        raise ValueError(f"invalid declared packet size {total_size} for {len(data)} bytes")
    packet = data[:total_size]
    expected = crc8_zirconia(packet[1:])
    if validate_checksum and packet[0] != expected:
        raise ValueError(f"checksum mismatch: got 0x{packet[0]:02x}, expected 0x{expected:02x}")
    backtracking_count = packet[2]
    backtracking_size = backtracking_count * 3
    payload_end = total_size - backtracking_size
    if payload_end < 10:
        raise ValueError("backtracking records overlap the packet header")
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


def serialize_data_object_array(
    object_type: int,
    payload: bytes,
    *,
    object_id: int | None = None,
) -> bytes:
    object_type = int(object_type)
    if object_type == DataObjectArrayType.RAW_WITH_ID:
        if object_id is None or not 0 <= object_id <= 0xFFFFFFFF:
            raise ValueError("RawWithId requires a uint32 object_id")
        payload = struct.pack("<I", object_id) + payload
    elif object_id is not None:
        raise ValueError("object_id is only valid with RawWithId")
    if len(payload) > 0xFF:
        raise ValueError("DataObjectArray payload exceeds uint8 length")
    return struct.pack("<HBB", 0x0ABA, object_type, len(payload)) + payload


@dataclass(frozen=True)
class DataObjectArray:
    object_type: int
    payload: bytes
    object_id: int | None = None

    def to_json(self) -> dict[str, object]:
        return {
            "object_type": self.object_type,
            "object_id": self.object_id,
            "payload": self.payload.hex(),
        }


def deserialize_data_object_array(data: bytes) -> DataObjectArray:
    if len(data) < 4:
        raise ValueError("DataObjectArray is shorter than four bytes")
    magic, object_type, length = struct.unpack_from("<HBB", data)
    if magic != 0x0ABA:
        raise ValueError(f"invalid DataObjectArray magic 0x{magic:04x}")
    if len(data) < 4 + length:
        raise ValueError("truncated DataObjectArray payload")
    payload = data[4 : 4 + length]
    object_id = None
    if object_type == DataObjectArrayType.RAW_WITH_ID:
        if len(payload) < 4:
            raise ValueError("RawWithId payload is shorter than its uint32 id")
        object_id = struct.unpack_from("<I", payload)[0]
        payload = payload[4:]
    return DataObjectArray(object_type, payload, object_id)


def serialize_user_override(
    preset: int,
    timeout_seconds: int,
    *,
    command: int = MevCommand.SET_SPEED,
    zone_id: int = 0,
    ventilation_mode: int = VentilationMode.OFF,
) -> bytes:
    if not 0 <= timeout_seconds <= 0xFFFFFFFF:
        raise ValueError("timeout_seconds must fit uint32")
    body = struct.pack(
        "<BBBBI",
        int(command),
        int(preset),
        zone_id,
        int(ventilation_mode),
        timeout_seconds,
    )
    return serialize_data_object_array(DataObjectArrayType.RAW, body)


def serialize_cancel_override() -> bytes:
    return serialize_user_override(AirflowPreset.LOW, 0, command=MevCommand.CANCEL)


def serialize_ventilation_mode(mode: VentilationMode | int) -> bytes:
    """Serialize a mode change separately from speed override and cancellation."""

    return serialize_user_override(
        AirflowPreset.LOW,
        0,
        command=MevCommand.NO_TYPE,
        ventilation_mode=VentilationMode(mode),
    )


def serialize_index_param(index: int) -> bytes:
    if not 0 <= index <= 0xFF:
        raise ValueError("index must fit uint8")
    return bytes([index])


def serialize_co2_calibration(
    value_ppm: int,
    *,
    automatic_enabled: bool,
    start_forced_calibration: bool,
) -> bytes:
    if not 400 <= value_ppm <= 2000:
        raise ValueError("Connect 7.2.2 restricts calibration to 400..2000 ppm")
    return struct.pack("<HBB", value_ppm, automatic_enabled, start_forced_calibration)


def serialize_silent_hour(start_seconds: int, end_seconds: int, weekdays_mask: int) -> bytes:
    if not 0 <= start_seconds < 86_400 or not 0 <= end_seconds < 86_400:
        raise ValueError("silent-hour times must be seconds within one day")
    if not 0 < weekdays_mask <= 0x7F:
        raise ValueError("weekdays_mask must select Monday..Sunday bits 0..6")
    return struct.pack("<IIB", start_seconds, end_seconds, weekdays_mask)


def serialize_silent_hour_table(item_index: int, total_count: int, record: bytes) -> bytes:
    if not 0 <= item_index < 6:
        raise ValueError("silent-hour item index must be 0..5")
    if len(record) != 9:
        raise ValueError("silent-hour record must be nine bytes")
    return struct.pack("<HH", item_index, total_count) + record


def serialize_silent_hour_delete(item_index: int) -> bytes:
    if not 0 <= item_index < 6:
        raise ValueError("silent-hour item index must be 0..5")
    return struct.pack("<HH", item_index, 0xFFFF)


def fragment_packet(packet: bytes, *, channel: int = 0) -> list[bytes]:
    """Split a protocol packet into the legacy 20-byte/17-byte-payload frames."""

    if not packet:
        raise ValueError("cannot fragment an empty packet")
    total = math.ceil(len(packet) / 17)
    if total > 15:
        raise ValueError("fragment count does not fit the low header nibble")
    if not 0 <= channel <= 0xFF:
        raise ValueError("channel must fit uint8")
    frames = []
    for index in range(total):
        chunk = packet[index * 17 : (index + 1) * 17].ljust(17, b"\0")
        frames.append(bytes([((index + 1) << 4) | total, crc8_zirconia(chunk), channel]) + chunk)
    return frames


def reassemble_fragments(frames: Sequence[bytes]) -> bytes:
    if not frames:
        raise ValueError("no frames supplied")
    expected_total = frames[0][0] & 0x0F
    if expected_total != len(frames):
        raise ValueError(f"expected {expected_total} frames, got {len(frames)}")
    chunks = []
    for expected_index, frame in enumerate(frames, 1):
        if len(frame) != 20:
            raise ValueError("each fragmented frame must be exactly 20 bytes")
        if frame[0] >> 4 != expected_index or frame[0] & 0x0F != expected_total:
            raise ValueError("fragment sequence/total header mismatch")
        if crc8_zirconia(frame[3:20]) != frame[1]:
            raise ValueError(f"fragment {expected_index} checksum mismatch")
        chunks.append(frame[3:20])
    padded = b"".join(chunks)
    if len(padded) >= 2 and 10 <= padded[1] <= len(padded):
        return padded[: padded[1]]
    return padded


def fragment_ack(index: int) -> bytes:
    if not 0 <= index <= 0xFF:
        raise ValueError("ack index must fit uint8")
    return bytes([0, 0, index]) + bytes(17)


WHOLE_PACKET_ACK = bytes([0, 0, 1])
TRANSPORT_CANCEL = bytes([0, 0, 0xFF])


def decode_faults(mask: int) -> list[str]:
    return [name for flag, name in FAULT_FLAGS.items() if mask & flag == flag]


def parse_system_status(data_object: bytes) -> dict[str, object]:
    obj = deserialize_data_object_array(data_object)
    if obj.object_type != DataObjectArrayType.RAW or len(obj.payload) < 7:
        raise ValueError("system status requires a Raw DataObjectArray with at least seven bytes")
    fan_speed, timeout, faults = struct.unpack_from("<BHI", obj.payload)
    return {
        "fan_speed": fan_speed,
        "timeout_seconds": timeout,
        "fault_mask": faults,
        "faults": decode_faults(faults),
    }


def parse_zone_view(data: bytes, *, zone_id: int = 1) -> dict[str, object]:
    if len(data) < 21:
        raise ValueError("zone view V2 requires 21 bytes")
    co2_supported, fan_level, fan_state, rpm = struct.unpack_from("<BBB H", data)
    temperature, relative_humidity, co2 = struct.unpack_from("<fff", data, 5)
    fault_mask = struct.unpack_from("<I", data, 17)[0]
    return {
        "zone_id": zone_id,
        "co2_supported": bool(co2_supported),
        "fan_speed_level": fan_level,
        "fan_state": fan_state,
        "fan_rpm": rpm,
        "temperature_c": temperature,
        "relative_humidity_percent": relative_humidity,
        "co2_ppm": co2,
        "fault_mask": fault_mask,
        "faults": decode_faults(fault_mask),
    }


def parse_device_view_v6(data: bytes) -> dict[str, object]:
    if len(data) != 34:
        raise ValueError("device view V6 requires exactly 34 bytes")
    status = struct.unpack_from("<I", data, 5)[0]
    firmware = struct.unpack_from("<I", data, 9)[0]
    temperature, humidity, co2, battery = struct.unpack_from("<ffff", data, 13)
    return {
        "address": data[0],
        "device_type": data[1],
        "hardware_type": data[2],
        "signal_strength_dbm": struct.unpack_from("<b", data, 3)[0],
        "co2_menu_enabled": bool(data[4]),
        "device_status_mask": status,
        "firmware_version_raw": firmware,
        "temperature_c": temperature,
        "relative_humidity_percent": humidity,
        "co2_ppm": co2,
        "battery_level": battery,
        "fan_speed": data[29],
        "fan_state": data[30],
        "fan_rpm": struct.unpack_from("<H", data, 31)[0],
        "unparsed_trailer": data[33],
    }


def _hex_bytes(value: str) -> bytes:
    compact = value.replace(" ", "").replace(":", "")
    try:
        return bytes.fromhex(compact)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _self_test() -> None:
    assert crc8_zirconia(b"") == 0
    assert crc8_zirconia(bytes.fromhex("010203")) == 0xF7
    silent_hour = serialize_silent_hour(22 * 3600, 7 * 3600, 0x7F)
    assert silent_hour.hex() == "60350100706200007f"
    assert serialize_silent_hour_table(5, 0, silent_hour).hex() == (
        "0500000060350100706200007f"
    )
    assert serialize_silent_hour_delete(5).hex() == "0500ffff"
    override = serialize_user_override(AirflowPreset.BOOST, 1800)
    obj = deserialize_data_object_array(override)
    assert obj.object_type == DataObjectArrayType.RAW
    assert obj.payload == struct.pack("<BBBBI", 1, 3, 0, 3, 1800)
    assert deserialize_data_object_array(
        serialize_ventilation_mode(VentilationMode.OFF)
    ).payload == struct.pack("<BBBBI", 0, 1, 0, 3, 0)
    assert deserialize_data_object_array(
        serialize_ventilation_mode(VentilationMode.STOP)
    ).payload == struct.pack("<BBBBI", 0, 1, 0, 4, 0)
    packet = serialize_protocol_v2(
        PacketType.USER_OVERRIDE,
        Operation.DATA_REQUEST,
        override,
        timestamp=0x01020304,
    )
    assert packet.hex() == "91160038020001020304ba0a00080103000308070000"
    parsed = deserialize_protocol_v2(packet)
    assert parsed.timestamp_be == 0x01020304
    assert parsed.timestamp_le == 0x04030201
    assert parsed.payload == override
    frames = fragment_packet(packet)
    assert [frame.hex() for frame in frames] == [
        "12e50091160038020001020304ba0a0008010300",
        "220c000308070000000000000000000000000000",
    ]
    assert reassemble_fragments(frames) == packet
    assert fragment_ack(2) == bytes([0, 0, 2]) + bytes(17)
    status = parse_system_status(serialize_data_object_array(DataObjectArrayType.RAW, struct.pack("<BHI", 3, 90, 2 | 2048)))
    assert status["fan_speed"] == 3 and status["timeout_seconds"] == 90
    assert status["faults"] == ["MotorNotRunning", "FilterTimeout"]
    zone_record = struct.pack("<BBBHfffI", 1, 3, 2, 1450, 21.5, 58.25, 775.0, 0)
    zone = parse_zone_view(zone_record)
    assert zone["fan_rpm"] == 1450 and zone["co2_ppm"] == 775.0
    print("all MEV protocol self-tests passed")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("self-test")

    decode = subparsers.add_parser("decode-protocol", help="decode a hex protocol packet")
    decode.add_argument("hex", type=_hex_bytes)

    zone = subparsers.add_parser("decode-zone", help="decode a 21-byte zone-view record")
    zone.add_argument("hex", type=_hex_bytes)
    zone.add_argument("--zone-id", type=int, default=1)

    status = subparsers.add_parser("decode-status", help="decode a system-status DataObjectArray")
    status.add_argument("hex", type=_hex_bytes)

    override = subparsers.add_parser("user-override", help="build a complete whole-packet command")
    override.add_argument("preset", choices={name.lower(): item for name, item in AirflowPreset.__members__.items()})
    override.add_argument("seconds", type=int)
    override.add_argument("--timestamp", type=int)
    override.add_argument("--fragments", action="store_true")

    mode = subparsers.add_parser(
        "ventilation-mode", help="build a complete ventilation-mode command"
    )
    mode.add_argument(
        "mode",
        choices={
            name.lower().replace("_", "-") for name in VentilationMode.__members__
        },
    )
    mode.add_argument("--timestamp", type=int)
    mode.add_argument("--fragments", action="store_true")
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    if args.command == "self-test":
        _self_test()
    elif args.command == "decode-protocol":
        print(json.dumps(deserialize_protocol_v2(args.hex).to_json(), indent=2))
    elif args.command == "decode-zone":
        print(json.dumps(parse_zone_view(args.hex, zone_id=args.zone_id), indent=2))
    elif args.command == "decode-status":
        print(json.dumps(parse_system_status(args.hex), indent=2))
    elif args.command == "user-override":
        preset = AirflowPreset[args.preset.upper()]
        payload = serialize_user_override(preset, args.seconds)
        packet = serialize_protocol_v2(
            PacketType.USER_OVERRIDE,
            Operation.DATA_REQUEST,
            payload,
            timestamp=args.timestamp,
        )
        print(f"whole_packet={packet.hex()}")
        if args.fragments:
            for index, frame in enumerate(fragment_packet(packet), 1):
                print(f"fragment_{index}={frame.hex()}")
    elif args.command == "ventilation-mode":
        mode = VentilationMode[args.mode.upper().replace("-", "_")]
        payload = serialize_ventilation_mode(mode)
        packet = serialize_protocol_v2(
            PacketType.USER_OVERRIDE,
            Operation.DATA_REQUEST,
            payload,
            timestamp=args.timestamp,
        )
        print(f"whole_packet={packet.hex()}")
        if args.fragments:
            for index, frame in enumerate(fragment_packet(packet), 1):
                print(f"fragment_{index}={frame.hex()}")
    else:  # pragma: no cover
        parser.error("unknown command")


if __name__ == "__main__":
    main()
