"""Diagnostics support for Vent-Axia Multihome."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import VentaxiaMultihomeConfigEntry
from .const import CONF_SETUP_CODE
from .protocol import fan_state_name

TO_REDACT = {CONF_SETUP_CODE}


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
        "state": {
            "fan_state": fan_state_name(data.zone.fan_state) if data else None,
            "fan_level": data.zone.fan_level if data else None,
            "co2_supported": data.zone.co2_supported if data else None,
            "zone_fault_mask": data.zone.fault_mask if data else None,
            "system_fault_mask": data.system.fault_mask if data else None,
        },
    }
