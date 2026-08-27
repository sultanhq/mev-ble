"""Diagnostics evidence tests."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from custom_components.ventaxia_multihome.diagnostics import (
    async_get_config_entry_diagnostics,
)


@pytest.mark.asyncio
async def test_diagnostics_include_control_validation_state() -> None:
    """Diagnostics expose the confirmed fields needed by the v0.2 matrix."""

    # Arrange - create one confirmed coordinator snapshot and device description.
    updated = datetime(2026, 8, 27, 8, 30, tzinfo=UTC)
    data = SimpleNamespace(
        last_successful_update=updated,
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
        device=SimpleNamespace(
            transport_name="fragmented",
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
