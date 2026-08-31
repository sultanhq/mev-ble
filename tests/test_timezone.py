"""Tests for silent-hours local/UTC conversion."""

from custom_components.ventaxia_multihome.protocol import (
    SilentHourSlot,
    decode_silent_hour,
    encode_silent_hour,
)
from custom_components.ventaxia_multihome.schedule_time import (
    device_record_to_local,
    device_slots_to_local,
    local_record_to_device,
)


def _record(start: int, end: int, mask: int):
    return decode_silent_hour(encode_silent_hour(start, end, mask))


def test_device_schedule_converts_to_current_local_wall_clock() -> None:
    """BST display is one hour ahead of a UTC device schedule."""

    # Arrange - use the physically observed 15:50-15:55 UTC all-days schedule.
    device_record = _record(15 * 3600 + 50 * 60, 15 * 3600 + 55 * 60, 0x7F)

    # Act - convert using the +01:00 offset active during the hardware test.
    local_record = device_record_to_local(device_record, 3600)

    # Assert - Home Assistant shows 16:50-16:55 without changing the weekday set.
    assert local_record.start_seconds == 16 * 3600 + 50 * 60
    assert local_record.end_seconds == 16 * 3600 + 55 * 60
    assert local_record.weekdays_mask == 0x7F


def test_local_schedule_converts_back_to_device_utc() -> None:
    """A local BST entry is stored one hour earlier in the MEV."""

    # Arrange - enter the schedule in Home Assistant local wall time.
    local_record = _record(16 * 3600 + 50 * 60, 16 * 3600 + 55 * 60, 0x7F)

    # Act - convert using the +01:00 offset active during BST.
    device_record = local_record_to_device(local_record, 3600)

    # Assert - the outgoing record matches the physically observed UTC schedule.
    assert device_record.start_seconds == 15 * 3600 + 50 * 60
    assert device_record.end_seconds == 15 * 3600 + 55 * 60
    assert device_record.weekdays_mask == 0x7F


def test_midnight_conversion_rotates_weekday_mask() -> None:
    """Local Monday shortly after midnight becomes Sunday in UTC during BST."""

    # Arrange - Monday 00:30-01:30 local crosses into the previous UTC day.
    local_record = _record(30 * 60, 90 * 60, 0x01)

    # Act - convert to UTC and then back to the current local offset.
    device_record = local_record_to_device(local_record, 3600)
    roundtrip = device_record_to_local(device_record, 3600)

    # Assert - storage uses Sunday 23:30-00:30 and the UI round-trips to Monday.
    assert device_record.start_seconds == 23 * 3600 + 30 * 60
    assert device_record.end_seconds == 30 * 60
    assert device_record.weekdays_mask == 0x40
    assert roundtrip.start_seconds == local_record.start_seconds
    assert roundtrip.end_seconds == local_record.end_seconds
    assert roundtrip.weekdays_mask == local_record.weekdays_mask


def test_local_overnight_schedule_can_be_non_overnight_in_utc() -> None:
    """Conversion preserves meaning even when the UTC record changes shape."""

    # Arrange - Monday 23:30-00:30 is an overnight local schedule.
    local_record = _record(23 * 3600 + 30 * 60, 30 * 60, 0x01)

    # Act - convert the schedule to UTC during BST and back again.
    device_record = local_record_to_device(local_record, 3600)
    roundtrip = device_record_to_local(device_record, 3600)

    # Assert - UTC stores 22:30-23:30 Monday while local semantics round-trip.
    assert device_record.start_seconds == 22 * 3600 + 30 * 60
    assert device_record.end_seconds == 23 * 3600 + 30 * 60
    assert not device_record.is_overnight
    assert roundtrip.is_overnight
    assert roundtrip.start_seconds == local_record.start_seconds
    assert roundtrip.end_seconds == local_record.end_seconds
    assert roundtrip.weekdays_mask == local_record.weekdays_mask


def test_slot_conversion_preserves_raw_device_evidence() -> None:
    """Coordinator-localized records keep the exact packet-49 payload bytes."""

    # Arrange - attach one valid record to an indexed slot with captured raw bytes.
    record = _record(15 * 3600, 16 * 3600, 0x01)
    slot = SilentHourSlot(0, 0x0802, record, b"raw-device-evidence")

    # Act - localize the complete slot tuple for display/editing.
    localized = device_slots_to_local((slot,), 3600)

    # Assert - record fields change while raw readback remains byte-for-byte intact.
    assert localized[0].record is not None
    assert localized[0].record.start_seconds == 16 * 3600
    assert localized[0].raw_payload == b"raw-device-evidence"
