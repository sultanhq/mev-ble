"""Silent-hours wall-clock conversion for MEV firmware that schedules in UTC."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .protocol import (
    SECONDS_PER_DAY,
    SilentHour,
    SilentHourSlot,
    decode_silent_hour,
    encode_silent_hour,
)

_WEEKDAY_MASK = 0x7F
_WEEKDAY_COUNT = 7


def current_utc_offset_seconds(
    time_zone: str, *, at: datetime | None = None
) -> int:
    """Return the configured zone's UTC offset for one instant."""

    try:
        zone = ZoneInfo(time_zone)
    except ZoneInfoNotFoundError as err:
        raise ValueError(f"unknown time zone: {time_zone}") from err

    instant = datetime.now(UTC) if at is None else at
    if instant.tzinfo is None:
        raise ValueError("offset instant must be timezone-aware")
    offset = instant.astimezone(zone).utcoffset()
    if offset is None:
        raise ValueError(f"time zone has no UTC offset: {time_zone}")
    return int(offset.total_seconds())


def _shift_seconds(seconds: int, delta_seconds: int) -> tuple[int, int]:
    """Shift one clock value and return the resulting weekday delta."""

    if not 0 <= seconds < SECONDS_PER_DAY:
        raise ValueError("schedule time is outside one day")
    day_shift, shifted = divmod(seconds + delta_seconds, SECONDS_PER_DAY)
    return shifted, day_shift


def _rotate_weekday_mask(mask: int, day_shift: int) -> int:
    """Rotate a Monday-first seven-bit weekday mask by whole days."""

    if not 0 < mask <= _WEEKDAY_MASK:
        raise ValueError("weekday mask must contain at least one known day")
    shift = day_shift % _WEEKDAY_COUNT
    if shift == 0:
        return mask
    return ((mask << shift) | (mask >> (_WEEKDAY_COUNT - shift))) & _WEEKDAY_MASK


def _convert_record(record: SilentHour, delta_seconds: int) -> SilentHour:
    """Convert one schedule while preserving its recurring local-time meaning."""

    if not record.is_valid or delta_seconds == 0:
        return record
    start_seconds, start_day_shift = _shift_seconds(
        record.start_seconds, delta_seconds
    )
    end_seconds, _end_day_shift = _shift_seconds(record.end_seconds, delta_seconds)
    weekdays_mask = _rotate_weekday_mask(record.weekdays_mask, start_day_shift)
    return decode_silent_hour(
        encode_silent_hour(start_seconds, end_seconds, weekdays_mask)
    )


def device_record_to_local(record: SilentHour, utc_offset_seconds: int) -> SilentHour:
    """Convert a UTC device schedule to the current Home Assistant wall clock."""

    return _convert_record(record, utc_offset_seconds)


def local_record_to_device(record: SilentHour, utc_offset_seconds: int) -> SilentHour:
    """Convert a Home Assistant wall-clock schedule to the MEV UTC clock."""

    return _convert_record(record, -utc_offset_seconds)


def device_slots_to_local(
    slots: tuple[SilentHourSlot, ...], utc_offset_seconds: int
) -> tuple[SilentHourSlot, ...]:
    """Return display/edit slots while retaining raw device payload evidence."""

    if utc_offset_seconds == 0:
        return slots
    return tuple(
        replace(
            slot,
            record=(
                device_record_to_local(slot.record, utc_offset_seconds)
                if slot.record is not None
                else None
            ),
        )
        for slot in slots
    )
