"""Diagnostics support for Vent-Axia Multihome."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import VentaxiaMultihomeConfigEntry
from .const import CONF_SETUP_CODE
from .protocol import GlobalSettings, fan_state_name

TO_REDACT = {CONF_SETUP_CODE}


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
        "global_settings_write_ready": (coordinator.device.global_settings_write_ready),
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
