"""Diagnostics support for Vent-Axia Multihome."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant

from . import VentaxiaMultihomeConfigEntry
from .capabilities import (
    INSTALLER_FIELD_DEFINITIONS,
    installer_writable_fields,
    model_capability,
)
from .const import CONF_SETUP_CODE
from .protocol import GLOBAL_SETTING_FIELD_SPECS, GlobalSettings, fan_state_name

TO_REDACT = {CONF_ADDRESS, CONF_SETUP_CODE, "unique_id"}


def _global_settings_diagnostics(
    settings: GlobalSettings | None,
) -> dict[str, Any] | None:
    """Return every decoded setting plus its lossless raw record."""

    if settings is None:
        return None
    result = asdict(settings)
    result["raw_record"] = settings.raw_record.hex()
    result["invalid_boolean_fields"] = list(settings.invalid_boolean_fields)
    return result


def _model_number(model: str | None) -> int | None:
    """Return a numeric model without guessing from a non-numeric value."""

    if model is None:
        return None
    try:
        return int(model)
    except ValueError:
        return None


def _installer_field_raw_value(
    settings: GlobalSettings | None,
    record_offset: int,
    *,
    co2: bool,
) -> int | str | None:
    """Return the source byte or bytes behind one decoded diagnostic value."""

    if settings is None:
        return None
    size = 2 if co2 else 1
    raw = settings.raw_record[record_offset : record_offset + size]
    return raw.hex() if co2 else raw[0]


def _installer_capability_diagnostics(
    info: Any,
    settings: GlobalSettings | None,
) -> dict[str, Any]:
    """Describe selected read/write capabilities without inventing semantics."""

    model_number = _model_number(info.model)
    model = model_capability(model_number)
    writable = installer_writable_fields(
        model_number,
        info.firmware,
        info.hardware,
    )
    fields: dict[str, Any] = {}
    for field, definition in INSTALLER_FIELD_DEFINITIONS.items():
        spec = GLOBAL_SETTING_FIELD_SPECS[field]
        value = getattr(settings, definition.attribute) if settings else None
        if settings is None:
            value_status = "unavailable"
        elif spec.boolean and value is None:
            value_status = "unknown_boolean_value"
        elif definition.unit == "raw_code":
            value_status = "raw_code_semantics_unknown"
        else:
            value_status = "decoded"
        fields[definition.attribute] = {
            "field_id": int(field),
            "record_offset": definition.record_offset,
            "encoding": definition.encoding,
            "unit": definition.unit,
            "codec_minimum": definition.minimum,
            "codec_maximum": definition.maximum,
            "step": definition.step,
            "dependencies": definition.dependencies,
            "risk": definition.risk.value,
            "evidence": definition.evidence.value,
            "writable": field in writable,
            "decoded_value": value,
            "raw_value": _installer_field_raw_value(
                settings,
                definition.record_offset,
                co2=spec.co2,
            ),
            "value_status": value_status,
        }
    return {
        "identity_complete": all(
            value is not None for value in (model_number, info.firmware, info.hardware)
        ),
        "model": {
            "number": model_number,
            "name": model.name if model else None,
            "recognised": model is not None,
            "four_speed_airflow": model.four_speed_airflow if model else False,
            "internal_co2": model.internal_co2 if model else False,
        },
        "snapshot_available": settings is not None,
        "writable_field_ids": sorted(int(field) for field in writable),
        "fields": fields,
        "read_only_unaddressable_fields": {
            "purge_low_mode": {
                "record_offset": 16,
                "unit": "raw_code",
                "decoded_value": settings.purge_low_mode if settings else None,
                "raw_value": settings.raw_record[16] if settings else None,
                "reason": "no packet-136 field ID was recovered",
            }
        },
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: VentaxiaMultihomeConfigEntry
) -> dict[str, Any]:
    """Return non-sensitive integration diagnostics."""

    coordinator = entry.runtime_data
    info = coordinator.device.device_info
    data = coordinator.data
    return {
        "config_entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "device": {
            "model": info.model,
            "firmware": info.firmware,
            "hardware": info.hardware,
            "software": info.software,
            "reported_manufacturer": info.manufacturer,
            "selected_transport": coordinator.device.transport_name,
        },
        "last_successful_update": (
            data.last_successful_update.isoformat() if data else None
        ),
        "last_update_success": coordinator.last_update_success,
        "calibration": {
            "last_outcome": coordinator.last_calibration_outcome,
            "last_error": coordinator.last_calibration_error,
            "device_table_version": (
                coordinator.device.last_calibration_device_table_version
            ),
            "discovered_routes": [
                {
                    "address": address,
                    "device_type": device_type,
                    "hardware_type": hardware_type,
                }
                for address, device_type, hardware_type in (
                    coordinator.device.last_calibration_target_scan
                )
            ],
            "selected_target": coordinator.device.last_calibration_target,
        },
        "global_settings": _global_settings_diagnostics(
            data.global_settings if data else None
        ),
        "installer_capabilities": _installer_capability_diagnostics(
            info,
            data.global_settings if data else None,
        ),
        "global_settings_write_ready": (coordinator.device.global_settings_write_ready),
        "silent_hours": [
            {
                "index": slot.index,
                "total_count": slot.total_count,
                "start_seconds": (
                    slot.record.start_seconds if slot.record is not None else None
                ),
                "end_seconds": (
                    slot.record.end_seconds if slot.record is not None else None
                ),
                "weekdays_mask": (
                    slot.record.weekdays_mask if slot.record is not None else None
                ),
                "known": slot.is_known,
                "valid": (
                    slot.record.is_valid
                    if slot.record is not None
                    else True
                    if slot.is_known
                    else None
                ),
                "raw_payload": slot.raw_payload.hex(),
            }
            for slot in (data.silent_hours if data else ())
        ],
        "silent_hours_write_ready": coordinator.device.silent_hours_write_ready,
        "state": {
            "fan_state": fan_state_name(data.zone.fan_state) if data else None,
            "fan_level": data.zone.fan_level if data else None,
            "fan_speed": data.system.fan_speed if data else None,
            "fan_rpm": data.zone.fan_rpm if data else None,
            "override_remaining": data.system.override_remaining if data else None,
            "override_remaining_source": (
                data.system.override_remaining_source if data else None
            ),
            "co2_supported": data.zone.co2_supported if data else None,
            "zone_fault_mask": data.zone.fault_mask if data else None,
            "system_fault_mask": data.system.fault_mask if data else None,
        },
    }
