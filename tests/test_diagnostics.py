"""Diagnostics evidence tests."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from custom_components.ventaxia_multihome.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.ventaxia_multihome.protocol import decode_global_settings


@pytest.mark.asyncio
async def test_diagnostics_include_control_validation_state() -> None:
    """Diagnostics expose the confirmed fields needed by the v0.2 matrix."""

    # Arrange - create one confirmed coordinator snapshot and device description.
    updated = datetime(2026, 8, 27, 8, 30, tzinfo=UTC)
    data = SimpleNamespace(
        last_successful_update=updated,
        global_settings=decode_global_settings(
            bytes(
                [
                    10,
                    35,
                    70,
                    100,
                    65,
                    75,
                    1,
                    0,
                    1,
                    0,
                    1,
                    0,
                    2,
                    3,
                    15,
                    25,
                    4,
                    11,
                    12,
                    5,
                    6,
                    7,
                    100,
                    0,
                    150,
                    0,
                    21,
                    91,
                    8,
                    9,
                    22,
                    92,
                    13,
                    14,
                    15,
                    16,
                ]
            )
        ),
        zone=SimpleNamespace(
            fan_state=2,
            fan_level=3,
            fan_rpm=1200,
            co2_supported=True,
            fault_mask=0,
        ),
        system=SimpleNamespace(
            fan_speed=3,
            override_remaining=52,
            override_remaining_source="estimated",
            fault_mask=0,
        ),
    )
    coordinator = SimpleNamespace(
        data=data,
        last_update_success=True,
        last_calibration_outcome="not_sent",
        last_calibration_error="no internal target",
        device=SimpleNamespace(
            transport_name="fragmented",
            last_calibration_device_table_version=6,
            last_calibration_target_scan=[(1, 10, 4)],
            last_calibration_target=None,
            device_info=SimpleNamespace(
                model="11",
                firmware="1.2.3",
                hardware="4",
                software="5",
                manufacturer="Vent-Axia",
            ),
        ),
    )
    entry = SimpleNamespace(
        runtime_data=coordinator,
        as_dict=lambda: {"data": {"setup_code": 1234}},
    )

    # Act - generate Home Assistant's redacted integration diagnostics.
    result = await async_get_config_entry_diagnostics(object(), entry)

    # Assert - the physical-validation evidence is present and code stays hidden.
    assert result["device"]["selected_transport"] == "fragmented"
    assert result["last_successful_update"] == updated.isoformat()
    assert result["last_update_success"] is True
    assert result["calibration"] == {
        "last_outcome": "not_sent",
        "last_error": "no internal target",
        "device_table_version": 6,
        "discovered_routes": [
            {"address": 1, "device_type": 10, "hardware_type": 4}
        ],
        "selected_target": None,
    }
    assert result["global_settings"]["speed_low"] == 10
    assert result["global_settings"]["speed_purge"] == 100
    assert result["global_settings"]["co2_boost_threshold"] == 1000
    assert result["global_settings"]["co2_purge_threshold"] == 1500
    assert result["global_settings"]["invalid_boolean_fields"] == []
    assert result["global_settings"]["raw_record"] == (
        "0a234664414b01000100010002030f19040b0c05060764009600155b0809165c0d0e0f10"
    )
    assert result["state"] == {
        "fan_state": "user_override",
        "fan_level": 3,
        "fan_speed": 3,
        "fan_rpm": 1200,
        "override_remaining": 52,
        "override_remaining_source": "estimated",
        "co2_supported": True,
        "zone_fault_mask": 0,
        "system_fault_mask": 0,
    }
    assert result["config_entry"]["data"]["setup_code"] != 1234
