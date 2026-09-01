"""Diagnostics evidence tests."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from custom_components.ventaxia_multihome.diagnostics import (
    _installer_capability_diagnostics,
    async_get_config_entry_diagnostics,
)
from custom_components.ventaxia_multihome.protocol import (
    decode_global_settings,
    decode_silent_hour_slot,
)


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
        silent_hours=tuple(
            decode_silent_hour_slot(index.to_bytes(2, "little") + bytes(11))
            for index in range(6)
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
            global_settings_write_ready=True,
            silent_hours_write_ready=True,
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
        as_dict=lambda: {
            "data": {
                "address": "AA:BB:CC:DD:EE:FF",
                "setup_code": 1234,
            },
            "unique_id": "aabbccddeeff",
        },
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
        "discovered_routes": [{"address": 1, "device_type": 10, "hardware_type": 4}],
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
    assert result["global_settings_write_ready"] is True
    installer = result["installer_capabilities"]
    assert installer["model"] == {
        "number": 11,
        "name": "Va125Ec3Wireless",
        "recognised": True,
        "four_speed_airflow": False,
        "internal_co2": False,
    }
    assert installer["identity_complete"] is True
    assert installer["snapshot_available"] is True
    assert installer["writable_field_ids"] == []
    assert installer["validation_candidate_field_ids"] == []
    assert installer["fields"]["speed_low"] == {
        "field_id": 0,
        "record_offset": 0,
        "encoding": "uint8",
        "unit": "percent",
        "codec_minimum": 1,
        "codec_maximum": 97,
        "step": 1,
        "dependencies": "low < normal < boost < purge",
        "risk": "ventilation",
        "evidence": "physical_validation",
        "writable": False,
        "validation_candidate": False,
        "decoded_value": 10,
        "raw_value": 10,
        "value_status": "decoded",
    }
    assert installer["fields"]["ls1_action"]["value_status"] == (
        "raw_code_semantics_unknown"
    )
    assert installer["read_only_unaddressable_fields"]["purge_low_mode"] == {
        "record_offset": 16,
        "unit": "raw_code",
        "decoded_value": 4,
        "raw_value": 4,
        "reason": "no packet-136 field ID was recovered",
    }
    assert len(result["silent_hours"]) == 6
    assert result["silent_hours"][0] == {
        "index": 0,
        "total_count": 0,
        "start_seconds": None,
        "end_seconds": None,
        "weekdays_mask": None,
        "known": True,
        "valid": True,
        "raw_payload": "00000000000000000000000000",
    }
    assert result["silent_hours_write_ready"] is True
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
    assert result["config_entry"]["data"]["address"] != "AA:BB:CC:DD:EE:FF"
    assert result["config_entry"]["unique_id"] != "aabbccddeeff"


@pytest.mark.parametrize(
    (
        "model",
        "firmware",
        "hardware",
        "expected_name",
        "writable_ids",
        "candidate_ids",
    ),
    [
        ("1", "2.03.08", "01.00", "SmvPlusHx", [], []),
        ("2", "2.03.08", "01.00", "SmvPlusHxCo2", [], []),
        ("9", "2.03.08", "01.00", "SmvHx", [], []),
        (
            "10",
            "2.03.08",
            "01.00",
            "SmvHxCo2",
            [0, 1, 2, 3, 5, 14, 15, 21, 22],
            [],
        ),
        ("10", "2.03.09", "01.00", "SmvHxCo2", [], []),
        ("unknown", "2.03.08", "01.00", None, [], []),
    ],
)
def test_installer_diagnostics_select_capabilities_from_complete_identity(
    model: str,
    firmware: str,
    hardware: str,
    expected_name: str | None,
    writable_ids: list[int],
    candidate_ids: list[int],
) -> None:
    """Diagnostics explain every supported mapping without exposing controls."""

    # Arrange - provide one exact or partially matching reported identity.
    info = SimpleNamespace(model=model, firmware=firmware, hardware=hardware)

    # Act - resolve the same capability data included in downloaded diagnostics.
    result = _installer_capability_diagnostics(info, None)

    # Assert - static model recognition and physical write scope stay separate.
    assert result["model"]["name"] == expected_name
    assert result["writable_field_ids"] == writable_ids
    assert result["validation_candidate_field_ids"] == candidate_ids
    assert result["snapshot_available"] is False
    assert result["fields"]["humidity_threshold"]["decoded_value"] is None
    assert result["fields"]["humidity_threshold"]["value_status"] == "unavailable"


def test_installer_diagnostics_retain_unknown_boolean_value() -> None:
    """Malformed firmware flags remain diagnosable without raising an error."""

    # Arrange - decode a complete record whose comfort flag is neither zero nor one.
    raw_record = bytearray(36)
    raw_record[6] = 2
    settings = decode_global_settings(bytes(raw_record))
    info = SimpleNamespace(model="10", firmware="2.03.08", hardware="01.00")

    # Act - describe the malformed flag through the read-only diagnostics helper.
    result = _installer_capability_diagnostics(info, settings)

    # Assert - the unknown semantic value and original byte are both explicit.
    comfort = result["fields"]["comfort_enabled"]
    assert comfort["decoded_value"] is None
    assert comfort["raw_value"] == 2
    assert comfort["value_status"] == "unknown_boolean_value"
    assert comfort["writable"] is False
