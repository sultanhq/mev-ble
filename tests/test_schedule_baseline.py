"""Tests for semantic silent-hours review baselines."""

from custom_components.ventaxia_multihome.protocol import (
    SilentHourSlot,
    decode_silent_hour,
    encode_silent_hour,
)
from custom_components.ventaxia_multihome.schedule_time import device_slots_to_local


def _record(start: int, end: int, mask: int):
    return decode_silent_hour(encode_silent_hour(start, end, mask))


def test_volatile_packet_envelope_does_not_change_schedule_baseline() -> None:
    """Different packet metadata/CRC compares equal when the schedule is unchanged."""

    record = _record(6 * 3600 + 50 * 60, 6 * 3600 + 51 * 60, 0x02)
    first = SilentHourSlot(1, 0x2001, record, bytes.fromhex("01000120") + record.raw_record + b"\xaa")
    second = SilentHourSlot(1, 0x0802, record, bytes.fromhex("01000208") + record.raw_record + b"\xbb")

    first_local = device_slots_to_local((first,), 3600)[0]
    second_local = device_slots_to_local((second,), 3600)[0]

    assert first_local.raw_payload == second_local.raw_payload
    assert bytes(first_local.raw_payload) == first.raw_payload
    assert bytes(second_local.raw_payload) == second.raw_payload
    assert first_local.raw_payload.hex() == first.raw_payload.hex()
    assert second_local.raw_payload.hex() == second.raw_payload.hex()


def test_real_schedule_change_still_invalidates_baseline() -> None:
    """A changed record remains unequal even if only one schedule field changes."""

    original = _record(6 * 3600 + 50 * 60, 6 * 3600 + 51 * 60, 0x02)
    changed = _record(6 * 3600 + 50 * 60, 6 * 3600 + 52 * 60, 0x02)
    first = SilentHourSlot(1, 0x2001, original, b"first-envelope")
    second = SilentHourSlot(1, 0x0802, changed, b"second-envelope")

    first_local = device_slots_to_local((first,), 3600)[0]
    second_local = device_slots_to_local((second,), 3600)[0]

    assert first_local.raw_payload != second_local.raw_payload


def test_empty_slots_compare_by_slot_not_envelope() -> None:
    """Empty records ignore volatile metadata but remain tied to their slot index."""

    first = SilentHourSlot(2, 0x2001, None, b"empty-envelope-a")
    second = SilentHourSlot(2, 0x0802, None, b"empty-envelope-b")
    other_slot = SilentHourSlot(3, 0x0802, None, b"empty-envelope-a")

    first_local = device_slots_to_local((first,), 0)[0]
    second_local = device_slots_to_local((second,), 0)[0]
    other_local = device_slots_to_local((other_slot,), 0)[0]

    assert first_local.raw_payload == second_local.raw_payload
    assert first_local.raw_payload != other_local.raw_payload
