"""Coordinator Bluetooth-path retention tests."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch

import pytest
from bleak.exc import BleakError
from homeassistant.components.bluetooth import BluetoothScanningMode
from homeassistant.const import CONF_ADDRESS
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.ventaxia_multihome import coordinator as coordinator_module
from custom_components.ventaxia_multihome.bluetooth import TransactionTimeoutError
from custom_components.ventaxia_multihome.const import (
    CONF_CONFIGURATION_BACKUP,
    CONF_LAST_CO2_CALIBRATION_ATTEMPT,
    HARD_RESET_RECOVERY_TIMEOUT,
    STARTUP_ADVERTISEMENT_TIMEOUT,
)
from custom_components.ventaxia_multihome.coordinator import (
    AirflowConfigurationNotSupportedError,
    AirflowConfigurationUnavailableError,
    AnalogueInput1ValidationNotSupportedError,
    AnalogueInput1ValidationUnavailableError,
    AnalogueInput2ValidationNotSupportedError,
    AnalogueInput2ValidationUnavailableError,
    CalibrationCommandNotSentError,
    CalibrationDeliveryUncertainError,
    CalibrationNotSupportedError,
    CalibrationRateLimitedError,
    ComfortModeConfigurationNotSupportedError,
    ComfortModeConfigurationUnavailableError,
    ConfigurationBackupUnavailableError,
    ConfigurationRestoreError,
    DelayOverrunConfigurationNotSupportedError,
    DelayOverrunConfigurationUnavailableError,
    DigitalInputValidationNotSupportedError,
    DigitalInputValidationUnavailableError,
    HardResetDeliveryUncertainError,
    HardResetNotSupportedError,
    HardResetRecoveryResult,
    HardResetUnavailableError,
    HumidityResponseConfigurationNotSupportedError,
    HumidityResponseConfigurationUnavailableError,
    LowTemperatureProtectionValidationNotSupportedError,
    LowTemperatureProtectionValidationUnavailableError,
    SensorThresholdConfigurationNotSupportedError,
    SensorThresholdConfigurationUnavailableError,
    SilentHoursConfigurationUnavailableError,
    SilentHoursNotSupportedError,
    TemperatureValidationNotSupportedError,
    TemperatureValidationUnavailableError,
    VentaxiaMultihomeCoordinator,
)
from custom_components.ventaxia_multihome.device import (
    CalibrationTargetDiscoveryError,
    CalibrationWriteUncertainError,
    DeviceError,
    GlobalSettingsUnavailableError,
    HardResetDispatchResult,
    HardResetDispatchUncertainError,
    MultihomeData,
    SetupCodeRejectedError,
)
from custom_components.ventaxia_multihome.protocol import (
    AirflowPreset,
    GlobalSettingField,
    ProtocolError,
    decode_global_settings,
    decode_silent_hour,
    decode_silent_hour_slot,
    encode_silent_hour,
)


def _silent_hours():
    """Return one complete empty six-slot table."""

    return tuple(
        decode_silent_hour_slot(index.to_bytes(2, "little") + bytes(11))
        for index in range(6)
    )


def _coordinator() -> VentaxiaMultihomeCoordinator:
    """Create the small coordinator subset needed by Bluetooth-path tests."""

    coordinator = object.__new__(VentaxiaMultihomeCoordinator)
    coordinator.hass = object()
    coordinator.config_entry = SimpleNamespace(data={CONF_ADDRESS: "AA:BB"})
    coordinator._last_ble_device = None
    coordinator._hard_reset_recovery_mode = False
    coordinator._hard_reset_recovery_task = None
    coordinator._hard_reset_baseline_global_settings = None
    coordinator._hard_reset_baseline_silent_hours = None
    coordinator._hard_reset_baseline_advertisement_time = None
    coordinator.last_hard_reset_recovery_result = None
    return coordinator


def test_ble_device_retains_last_connectable_path(monkeypatch) -> None:
    """A reconnect can reuse the path hidden from scanners during connection."""

    # Arrange - return a device once, then simulate expiry from the scanner cache.
    coordinator = _coordinator()
    ble_device = object()
    discovered = iter([ble_device, None])
    monkeypatch.setattr(
        coordinator_module.bluetooth,
        "async_ble_device_from_address",
        lambda hass, address, connectable: next(discovered),
    )

    # Act - resolve the path before and after scanner-cache expiry.
    first = coordinator._ble_device()
    reconnect = coordinator._ble_device()

    # Assert - reconnect retains the known proxy/device route.
    assert first is ble_device
    assert reconnect is ble_device


def test_ble_device_uses_newly_discovered_path(monkeypatch) -> None:
    """A fresh scanner result replaces a previously retained path."""

    # Arrange - seed an old path and expose a newer scanner result.
    coordinator = _coordinator()
    old_device = object()
    new_device = object()
    coordinator._last_ble_device = old_device
    monkeypatch.setattr(
        coordinator_module.bluetooth,
        "async_ble_device_from_address",
        lambda hass, address, connectable: new_device,
    )

    # Act - resolve the currently available Bluetooth path.
    result = coordinator._ble_device()

    # Assert - current discovery wins and refreshes the retained path.
    assert result is new_device
    assert coordinator._last_ble_device is new_device


def test_ble_device_reports_unreachable_without_any_known_path(monkeypatch) -> None:
    """A never-seen device retains Home Assistant's reachability diagnostics."""

    # Arrange - expose neither a current discovery nor a retained adapter route.
    coordinator = _coordinator()
    monkeypatch.setattr(
        coordinator_module.bluetooth,
        "async_ble_device_from_address",
        lambda hass, address, connectable: None,
    )
    monkeypatch.setattr(
        coordinator_module.bluetooth,
        "async_address_reachability_diagnostics",
        lambda hass, address, intent: "unknown (never seen by any scanner)",
    )

    # Act / Assert - setup fails with the useful HA diagnostic instead of guessing.
    with pytest.raises(UpdateFailed, match="never seen by any scanner"):
        coordinator._ble_device()

@pytest.mark.asyncio
async def test_initial_bluetooth_uses_cached_connectable_path(monkeypatch) -> None:
    """Startup does not wait when HA already knows a connectable route."""

    # Arrange - expose the saved device in Home Assistant's Bluetooth cache.
    coordinator = _coordinator()
    ble_device = object()
    scanner_count = Mock()
    process_advertisements = AsyncMock()
    monkeypatch.setattr(
        coordinator_module.bluetooth,
        "async_ble_device_from_address",
        Mock(return_value=ble_device),
    )
    monkeypatch.setattr(
        coordinator_module.bluetooth, "async_scanner_count", scanner_count
    )
    monkeypatch.setattr(
        coordinator_module.bluetooth,
        "async_process_advertisements",
        process_advertisements,
    )

    # Act - prepare the coordinator's initial Bluetooth route.
    await coordinator.async_wait_for_initial_bluetooth()

    # Assert - the cached route is retained without scanning or waiting.
    assert coordinator._last_ble_device is ble_device
    scanner_count.assert_not_called()
    process_advertisements.assert_not_awaited()

@pytest.mark.asyncio
async def test_initial_bluetooth_waits_for_saved_address(monkeypatch) -> None:
    """Startup waits for the proxy to advertise the configured device."""

    # Arrange - make the device appear only after an address-specific scan.
    coordinator = _coordinator()
    ble_device = object()
    discovered = iter([None, ble_device])
    lookup = Mock(side_effect=lambda hass, address, connectable: next(discovered))
    process_advertisements = AsyncMock(return_value=object())
    monkeypatch.setattr(
        coordinator_module.bluetooth, "async_ble_device_from_address", lookup
    )
    monkeypatch.setattr(
        coordinator_module.bluetooth,
        "async_scanner_count",
        Mock(return_value=1),
    )
    monkeypatch.setattr(
        coordinator_module.bluetooth,
        "async_process_advertisements",
        process_advertisements,
    )

    # Act - wait for Home Assistant's connectable advertisement cache to warm.
    await coordinator.async_wait_for_initial_bluetooth()

    # Assert - only the configured address is actively awaited and then retained.
    process_advertisements.assert_awaited_once()
    args = process_advertisements.await_args.args
    assert args[0] is coordinator.hass
    assert args[1](object()) is True
    assert args[2] == {"address": "AA:BB", "connectable": True}
    assert args[3] is BluetoothScanningMode.ACTIVE
    assert args[4] == STARTUP_ADVERTISEMENT_TIMEOUT
    assert coordinator._last_ble_device is ble_device

@pytest.mark.asyncio
async def test_initial_bluetooth_defers_without_connectable_scanner(
    monkeypatch,
) -> None:
    """Startup remains retryable when no connection-capable route exists."""

    # Arrange - expose no saved device and no connectable local or proxy scanner.
    coordinator = _coordinator()
    process_advertisements = AsyncMock()
    monkeypatch.setattr(
        coordinator_module.bluetooth,
        "async_ble_device_from_address",
        Mock(return_value=None),
    )
    monkeypatch.setattr(
        coordinator_module.bluetooth,
        "async_scanner_count",
        Mock(return_value=0),
    )
    monkeypatch.setattr(
        coordinator_module.bluetooth,
        "async_process_advertisements",
        process_advertisements,
    )
    monkeypatch.setattr(
        coordinator_module.bluetooth,
        "async_address_reachability_diagnostics",
        Mock(return_value="no connectable scanner available"),
    )

    # Act / Assert - HA receives a retryable error without starting a BLE wait.
    with pytest.raises(ConfigEntryNotReady, match="no connectable scanner"):
        await coordinator.async_wait_for_initial_bluetooth()
    process_advertisements.assert_not_awaited()

@pytest.mark.asyncio
async def test_initial_bluetooth_retries_after_advertisement_timeout(
    monkeypatch,
) -> None:
    """A later setup retry succeeds after the proxy sees the saved device."""

    # Arrange - time out once, then expose the device during the next setup retry.
    coordinator = _coordinator()
    ble_device = object()
    discovered = iter([None, None, ble_device])
    process_advertisements = AsyncMock(side_effect=[TimeoutError, object()])
    monkeypatch.setattr(
        coordinator_module.bluetooth,
        "async_ble_device_from_address",
        Mock(side_effect=lambda hass, address, connectable: next(discovered)),
    )
    monkeypatch.setattr(
        coordinator_module.bluetooth,
        "async_scanner_count",
        Mock(return_value=1),
    )
    monkeypatch.setattr(
        coordinator_module.bluetooth,
        "async_process_advertisements",
        process_advertisements,
    )
    monkeypatch.setattr(
        coordinator_module.bluetooth,
        "async_address_reachability_diagnostics",
        Mock(return_value="unknown (never seen by any scanner)"),
    )

    # Act - let the first bounded wait expire, then run HA's later setup retry.
    with pytest.raises(ConfigEntryNotReady, match="never seen by any scanner"):
        await coordinator.async_wait_for_initial_bluetooth()
    await coordinator.async_wait_for_initial_bluetooth()

    # Assert - retry uses another bounded wait and recovers without manual reload.
    assert process_advertisements.await_count == 2
    assert coordinator._last_ble_device is ble_device

@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("coordinator_method", "device_method", "arguments"),
    [
        ("async_set_override", "set_override", (AirflowPreset.BOOST, 60)),
        ("async_cancel_override", "cancel_override", ()),
    ],
)
async def test_control_publishes_only_its_fresh_telemetry(
    coordinator_method: str,
    device_method: str,
    arguments: tuple,
) -> None:
    """Every control publishes the zone/system snapshot returned with its write."""

    # Arrange - make one device control return a distinct confirmed snapshot.
    fresh_data = object()
    ble_device = object()
    control = AsyncMock(return_value=fresh_data)
    device = SimpleNamespace(disconnect=AsyncMock())
    setattr(device, device_method, control)
    coordinator = SimpleNamespace(
        device=device,
        _ble_device=lambda: ble_device,
        async_set_updated_data=Mock(),
        async_set_update_error=Mock(),
    )

    # Act - invoke the production coordinator control method.
    await getattr(VentaxiaMultihomeCoordinator, coordinator_method)(
        coordinator, *arguments
    )

    # Assert - only fresh returned telemetry is published; no error is reported.
    control.assert_awaited_once_with(ble_device, *arguments)
    coordinator.async_set_updated_data.assert_called_once_with(fresh_data)
    coordinator.async_set_update_error.assert_not_called()
    device.disconnect.assert_not_awaited()

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        TransactionTimeoutError("timed out"),
        BleakError("disconnected"),
        ProtocolError("malformed telemetry"),
    ],
)
async def test_failed_control_retains_data_and_updates_availability(
    error: Exception,
) -> None:
    """A failed write/readback marks failure without replacing confirmed data."""

    # Arrange - retain confirmed data and fail the atomic control/readback operation.
    confirmed_data = object()
    device = SimpleNamespace(
        set_override=AsyncMock(side_effect=error),
        disconnect=AsyncMock(),
    )
    coordinator = SimpleNamespace(
        device=device,
        data=confirmed_data,
        _ble_device=lambda: object(),
        async_set_updated_data=Mock(),
        async_set_update_error=Mock(),
    )

    # Act - run a control that times out, disconnects, or returns malformed data.
    with pytest.raises(HomeAssistantError):
        await VentaxiaMultihomeCoordinator.async_set_override(
            coordinator, AirflowPreset.BOOST, 60
        )

    # Assert - old telemetry remains, availability is failed, and transport resets.
    assert coordinator.data is confirmed_data
    coordinator.async_set_updated_data.assert_not_called()
    coordinator.async_set_update_error.assert_called_once_with(error)
    device.disconnect.assert_awaited_once()


def _settings(raw: str = "06082532"):
    """Decode a complete settings record with a selectable airflow prefix."""

    suffix = "005101000100000001040f19000a0a0103049600af000f4b01030f4b01030103"
    return decode_global_settings(bytes.fromhex(raw + suffix))


def _reset_data(raw: str = "06082532") -> MultihomeData:
    """Return one complete snapshot suitable for hard-reset tests."""

    return MultihomeData(
        zone=object(),
        system=object(),
        global_settings=_settings(raw),
        last_successful_update=datetime.now(UTC),
        silent_hours=_silent_hours(),
    )


def _reset_dispatch_coordinator(device, ble_device):
    """Return the coordinator subset used by hard-reset dispatch tests."""

    return SimpleNamespace(
        hass=object(),
        config_entry=SimpleNamespace(data={CONF_ADDRESS: "AA:BB"}),
        device=device,
        data=_reset_data(),
        last_update_success=True,
        _hard_reset_dispatch_claimed=False,
        _ble_device=lambda: ble_device,
        save_configuration_backup=Mock(return_value={}),
        _begin_hard_reset_recovery=Mock(),
    )


def test_configuration_backup_persists_confirmed_snapshot() -> None:
    """A backup stores the complete confirmed settings and schedule table."""

    # Arrange - expose one fresh, write-ready validated unit and HA storage hook.
    update_entry = Mock()
    hass = SimpleNamespace(
        config=SimpleNamespace(time_zone="Europe/London"),
        config_entries=SimpleNamespace(async_update_entry=update_entry),
    )
    entry = SimpleNamespace(data={CONF_ADDRESS: "AA:BB"}, options={"existing": True})
    device = SimpleNamespace(
        model_number=10,
        device_info=SimpleNamespace(
            serial="TEST-123",
            firmware="2.03.08",
            hardware="01.00",
        ),
        global_settings_write_ready=True,
        supports_silent_hours_management=True,
        silent_hours_write_ready=True,
    )
    coordinator = SimpleNamespace(
        hass=hass,
        config_entry=entry,
        device=device,
        data=_reset_data(),
        last_update_success=True,
    )

    # Act - capture the current confirmed configuration.
    backup = VentaxiaMultihomeCoordinator.save_configuration_backup(
        coordinator, reason="manual"
    )

    # Assert - JSON-safe settings evidence is persisted without touching the unit.
    assert backup["version"] == 1
    assert backup["reason"] == "manual"
    assert backup["time_zone"] == "Europe/London"
    assert backup["identity"] == {
        "address": "AA:BB",
        "model_number": 10,
        "serial": "TEST-123",
        "firmware": "2.03.08",
        "hardware": "01.00",
    }
    assert backup["global_settings"] == _settings().raw_record.hex()
    assert backup["silent_hours"] == [None] * 6
    update_entry.assert_called_once_with(
        entry,
        options={
            "existing": True,
            CONF_CONFIGURATION_BACKUP: backup,
        },
    )

def test_configuration_backup_rejects_invalid_silent_hour_snapshot() -> None:
    """Invalid schedule data cannot qualify as a restorable reset backup."""

    # Arrange - expose a known slot whose decoded time is outside one day.
    update_entry = Mock()
    hass = SimpleNamespace(
        config=SimpleNamespace(time_zone="Europe/London"),
        config_entries=SimpleNamespace(async_update_entry=update_entry),
    )
    current = _reset_data()
    invalid_record = decode_silent_hour(
        (86400).to_bytes(4, "little")
        + (3600).to_bytes(4, "little")
        + b"\x01"
    )
    silent_hours = list(current.silent_hours)
    silent_hours[0] = replace(silent_hours[0], record=invalid_record)
    device = SimpleNamespace(
        model_number=10,
        device_info=SimpleNamespace(
            serial="TEST-123",
            firmware="2.03.08",
            hardware="01.00",
        ),
        global_settings_write_ready=True,
        supports_silent_hours_management=True,
        silent_hours_write_ready=True,
    )
    coordinator = SimpleNamespace(
        hass=hass,
        config_entry=SimpleNamespace(
            data={CONF_ADDRESS: "AA:BB"},
            options={},
        ),
        device=device,
        data=replace(current, silent_hours=tuple(silent_hours)),
        last_update_success=True,
    )

    # Act - attempt to persist the snapshot used as the reset prerequisite.
    with pytest.raises(
        ConfigurationBackupUnavailableError,
        match="contains invalid records",
    ):
        VentaxiaMultihomeCoordinator.save_configuration_backup(
            coordinator, reason="hard_reset"
        )

    # Assert - an invalid schedule can never be persisted to authorize packet 61.
    update_entry.assert_not_called()

@pytest.mark.asyncio
async def test_temperature_restore_reenables_protection_when_profile_write_fails(
) -> None:
    """A failed temperature restore puts enabled low-temperature protection back."""

    # Arrange - require a temperature change while protection starts and ends enabled.
    current = _reset_data()
    current_settings = replace(
        current.global_settings,
        low_temperature_enabled=True,
    )
    current = replace(current, global_settings=current_settings)
    target_raw = bytearray(current_settings.raw_record)
    target_raw[11] = 1
    target_raw[14] = 16
    target = decode_global_settings(bytes(target_raw))
    backup = {
        "version": 1,
        "reason": "hard_reset",
        "captured_at": current.last_successful_update.isoformat(),
        "time_zone": "Europe/London",
        "identity": {
            "address": "AA:BB",
            "model_number": 10,
            "serial": "TEST-123",
            "firmware": "2.03.08",
            "hardware": "01.00",
        },
        "global_settings": target.raw_record.hex(),
        "silent_hours": [],
    }
    coordinator = object.__new__(VentaxiaMultihomeCoordinator)
    coordinator.config_entry = SimpleNamespace(
        data={CONF_ADDRESS: "AA:BB"},
        options={CONF_CONFIGURATION_BACKUP: backup},
    )
    coordinator.device = SimpleNamespace(
        model_number=10,
        device_info=SimpleNamespace(
            serial="TEST-123",
            firmware="2.03.08",
            hardware="01.00",
        ),
        writable_installer_fields=frozenset(
            {
                GlobalSettingField.LOW_TEMPERATURE_ENABLED,
                GlobalSettingField.LOW_THRESHOLD_ACTION,
                GlobalSettingField.HIGH_THRESHOLD_ACTION,
                GlobalSettingField.LOW_TEMPERATURE_THRESHOLD,
                GlobalSettingField.HIGH_TEMPERATURE_THRESHOLD,
            }
        ),
        global_settings_write_ready=True,
        supports_silent_hours_management=False,
    )
    coordinator.data = current
    coordinator.last_update_success = True

    async def set_protection(*, enabled: bool) -> None:
        settings = replace(
            coordinator.data.global_settings,
            low_temperature_enabled=enabled,
        )
        coordinator.data = replace(coordinator.data, global_settings=settings)

    coordinator.async_set_low_temperature_protection_validation = AsyncMock(
        side_effect=set_protection
    )
    coordinator.async_set_temperature_threshold_validation = AsyncMock(
        side_effect=HomeAssistantError("temperature write failed")
    )

    # Act - fail the first guarded temperature-profile write after protection is off.
    with pytest.raises(HomeAssistantError, match="temperature write failed"):
        await coordinator.async_restore_configuration_backup()

    # Assert - restore disables only temporarily, then puts protection back on.
    assert (
        coordinator.async_set_low_temperature_protection_validation.await_args_list
        == [
            call(enabled=False),
            call(enabled=True),
        ]
    )
    assert coordinator.data.global_settings.low_temperature_enabled is True

@pytest.mark.asyncio
async def test_hard_reset_stops_before_packet_61_when_backup_fails() -> None:
    """A destructive reset cannot proceed without a fresh restorable backup."""

    # Arrange - expose a supported unit but make pre-reset persistence unavailable.
    ble_device = object()
    device = SimpleNamespace(
        supports_guarded_hard_reset=True,
        _dispatch_hard_reset=AsyncMock(),
        disconnect=AsyncMock(),
    )
    coordinator = _reset_dispatch_coordinator(device, ble_device)
    coordinator.save_configuration_backup.side_effect = (
        ConfigurationBackupUnavailableError("silent hours unavailable")
    )

    # Act - try to dispatch the guarded reset.
    with pytest.raises(HardResetUnavailableError, match="requires a fresh"):
        await _dispatch_reset(coordinator)

    # Assert - packet 61 is never attempted and recovery is not claimed.
    device._dispatch_hard_reset.assert_not_awaited()
    coordinator._begin_hard_reset_recovery.assert_not_called()
    assert coordinator._hard_reset_dispatch_claimed is False

@pytest.mark.asyncio
async def test_restore_replays_changed_validated_field_with_readback_state() -> None:
    """Restore uses the existing guarded setter rather than replaying raw packets."""

    # Arrange - save a target differing only in validated Boost minimum field 4.
    current = _reset_data()
    target_raw = bytearray(current.global_settings.raw_record)
    target_raw[4] = 1
    target = decode_global_settings(bytes(target_raw))
    backup = {
        "version": 1,
        "reason": "manual",
        "captured_at": current.last_successful_update.isoformat(),
        "time_zone": "Europe/London",
        "identity": {
            "address": "AA:BB",
            "model_number": 10,
            "serial": "TEST-123",
            "firmware": "2.03.08",
            "hardware": "01.00",
        },
        "global_settings": target.raw_record.hex(),
        "silent_hours": [],
    }
    coordinator = object.__new__(VentaxiaMultihomeCoordinator)
    coordinator.config_entry = SimpleNamespace(
        data={CONF_ADDRESS: "AA:BB"},
        options={CONF_CONFIGURATION_BACKUP: backup},
    )
    coordinator.device = SimpleNamespace(
        model_number=10,
        device_info=SimpleNamespace(
            serial="TEST-123",
            firmware="2.03.08",
            hardware="01.00",
        ),
        writable_installer_fields=frozenset({GlobalSettingField.BOOST_MINIMUM}),
        global_settings_write_ready=True,
        supports_silent_hours_management=False,
    )
    coordinator.data = current
    coordinator.last_update_success = True

    async def set_boost_minimum(*, value: int) -> None:
        assert value == 1
        coordinator.data = replace(coordinator.data, global_settings=target)

    coordinator.async_set_boost_minimum = AsyncMock(side_effect=set_boost_minimum)

    # Act - restore the persisted target.
    result = await coordinator.async_restore_configuration_backup()

    # Assert - one guarded field path ran and the confirmed snapshot matches backup.
    coordinator.async_set_boost_minimum.assert_awaited_once_with(value=1)
    assert result.global_fields_restored == 1
    assert result.silent_hours_restored == 0
    assert result.raw_record_matches is True
    assert coordinator.data.global_settings.raw_record == target.raw_record

@pytest.mark.asyncio
async def test_restore_never_writes_unvalidated_delay_enabled_field() -> None:
    """Backed-up field 7 is comparison evidence, not a speculative restore write."""

    # Arrange - change only field 7 while exposing validated timer fields 8..10.
    current = _reset_data()
    target_raw = bytearray(current.global_settings.raw_record)
    target_raw[7] = 1
    target = decode_global_settings(bytes(target_raw))
    backup = {
        "version": 1,
        "reason": "hard_reset",
        "captured_at": current.last_successful_update.isoformat(),
        "time_zone": "Europe/London",
        "identity": {
            "address": "AA:BB",
            "model_number": 10,
            "serial": "TEST-123",
            "firmware": "2.03.08",
            "hardware": "01.00",
        },
        "global_settings": target.raw_record.hex(),
        "silent_hours": [],
    }
    coordinator = object.__new__(VentaxiaMultihomeCoordinator)
    coordinator.config_entry = SimpleNamespace(
        data={CONF_ADDRESS: "AA:BB"},
        options={CONF_CONFIGURATION_BACKUP: backup},
    )
    coordinator.device = SimpleNamespace(
        model_number=10,
        device_info=SimpleNamespace(
            serial="TEST-123",
            firmware="2.03.08",
            hardware="01.00",
        ),
        writable_installer_fields=frozenset(
            {
                GlobalSettingField.OVERRUN_ENABLED,
                GlobalSettingField.OVERRUN_TIMEOUT_MINUTES,
                GlobalSettingField.DELAY_TIMEOUT_MINUTES,
            }
        ),
        global_settings_write_ready=True,
        supports_silent_hours_management=False,
    )
    coordinator.data = current
    coordinator.last_update_success = True
    coordinator.async_set_delay_overrun = AsyncMock()

    # Act - restore against a backup whose only difference is unvalidated field 7.
    result = await coordinator.async_restore_configuration_backup()

    # Assert - field 7 is not written; the raw mismatch remains visible to recovery.
    coordinator.async_set_delay_overrun.assert_not_awaited()
    assert result.global_fields_restored == 0
    assert result.raw_record_matches is False
    assert coordinator.data.global_settings.delay_enabled is False

@pytest.mark.asyncio
async def test_restore_rejects_different_device_identity_before_writes() -> None:
    """A backup from another unit cannot be written to the connected unit."""

    # Arrange - attach a valid-looking backup to a different serial number.
    current = _reset_data()
    backup = {
        "version": 1,
        "reason": "manual",
        "captured_at": current.last_successful_update.isoformat(),
        "time_zone": "Europe/London",
        "identity": {
            "address": "AA:BB",
            "model_number": 10,
            "serial": "OTHER-UNIT",
            "firmware": "2.03.08",
            "hardware": "01.00",
        },
        "global_settings": current.global_settings.raw_record.hex(),
        "silent_hours": [],
    }
    coordinator = object.__new__(VentaxiaMultihomeCoordinator)
    coordinator.config_entry = SimpleNamespace(
        data={CONF_ADDRESS: "AA:BB"},
        options={CONF_CONFIGURATION_BACKUP: backup},
    )
    coordinator.device = SimpleNamespace(
        model_number=10,
        device_info=SimpleNamespace(
            serial="THIS-UNIT",
            firmware="2.03.08",
            hardware="01.00",
        ),
    )

    # Act - validate the backup before any restore operation can start.
    with pytest.raises(ConfigurationRestoreError, match="different serial number"):
        coordinator._load_configuration_backup()

    # Assert - validation fails before a Bluetooth write path is even available.
    assert coordinator.device.device_info.serial == "THIS-UNIT"


async def _dispatch_reset(coordinator, *, baseline_time: float | None = None):
    """Invoke reset dispatch with a deterministic pre-reset advertisement."""

    service_info = (
        SimpleNamespace(time=baseline_time) if baseline_time is not None else None
    )
    with patch.object(
        coordinator_module.bluetooth,
        "async_last_service_info",
        return_value=service_info,
    ):
        return await (
            VentaxiaMultihomeCoordinator.async_dispatch_hard_reset_from_options(
                coordinator
            )
        )

@pytest.mark.asyncio
async def test_hard_reset_options_dispatch_delegates_once_when_fresh() -> None:
    """The options-only coordinator path delegates one guarded packet-61 dispatch."""

    # Arrange - expose one supported, fresh device and deterministic BLE route.
    ble_device = object()
    expected = HardResetDispatchResult(transport="test")
    device = SimpleNamespace(
        supports_guarded_hard_reset=True,
        _dispatch_hard_reset=AsyncMock(return_value=expected),
        disconnect=AsyncMock(),
    )
    coordinator = _reset_dispatch_coordinator(device, ble_device)

    # Act - invoke the production options-only coordinator method.
    result = await _dispatch_reset(coordinator, baseline_time=42.5)

    # Assert - dispatch retains the pre-reset advertisement boundary for recovery.
    assert result is expected
    assert coordinator._hard_reset_baseline_advertisement_time == 42.5
    coordinator.save_configuration_backup.assert_called_once_with(reason="hard_reset")
    device._dispatch_hard_reset.assert_awaited_once_with(ble_device)
    coordinator._begin_hard_reset_recovery.assert_called_once_with(
        delivery_uncertain=False
    )
    device.disconnect.assert_not_awaited()

@pytest.mark.asyncio
async def test_hard_reset_options_rejects_unsupported_identity() -> None:
    """Unsupported identities cannot resolve a BLE route for hard reset."""

    # Arrange - expose fresh data but no reset capability.
    device = SimpleNamespace(
        supports_guarded_hard_reset=False,
        _dispatch_hard_reset=AsyncMock(),
    )
    coordinator = SimpleNamespace(
        device=device,
        data=object(),
        last_update_success=True,
        _hard_reset_dispatch_claimed=False,
        _ble_device=Mock(),
    )

    # Act / Assert - identity gating happens before any Bluetooth lookup.
    with pytest.raises(HardResetNotSupportedError):
        await _dispatch_reset(coordinator)
    coordinator._ble_device.assert_not_called()
    device._dispatch_hard_reset.assert_not_awaited()

@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("data", "last_success"),
    [
        (None, True),
        (object(), False),
    ],
)
async def test_hard_reset_options_requires_fresh_coordinator_state(
    data, last_success
) -> None:
    """Missing or stale coordinator data cannot reach the reset transport."""

    # Arrange - vary the two freshness conditions independently.
    device = SimpleNamespace(
        supports_guarded_hard_reset=True,
        _dispatch_hard_reset=AsyncMock(),
    )
    coordinator = SimpleNamespace(
        device=device,
        data=data,
        last_update_success=last_success,
        _hard_reset_dispatch_claimed=False,
        _ble_device=Mock(),
    )

    # Act / Assert - stale state is rejected before resolving a BLE route.
    with pytest.raises(HardResetUnavailableError):
        await _dispatch_reset(coordinator)
    coordinator._ble_device.assert_not_called()
    device._dispatch_hard_reset.assert_not_awaited()

@pytest.mark.asyncio
async def test_hard_reset_options_rejects_concurrent_sibling_flow() -> None:
    """Two armed Configure flows cannot queue two packet-61 dispatches."""

    # Arrange - hold the first reset inside the shared coordinator after its claim.
    ble_device = object()
    started = asyncio.Event()
    release = asyncio.Event()
    expected = HardResetDispatchResult(transport="test")

    async def dispatch(_ble_device) -> HardResetDispatchResult:
        started.set()
        await release.wait()
        return expected

    device = SimpleNamespace(
        supports_guarded_hard_reset=True,
        _dispatch_hard_reset=AsyncMock(side_effect=dispatch),
        disconnect=AsyncMock(),
    )
    coordinator = _reset_dispatch_coordinator(device, ble_device)

    # Act - start one flow, then submit a sibling flow while the first is in flight.
    first = asyncio.create_task(_dispatch_reset(coordinator))
    await started.wait()
    with pytest.raises(HardResetUnavailableError, match="already been claimed"):
        await _dispatch_reset(coordinator)
    release.set()
    result = await first

    # Assert - only the first flow reaches the device and the claim remains consumed.
    assert result is expected
    assert coordinator._hard_reset_dispatch_claimed is True
    device._dispatch_hard_reset.assert_awaited_once_with(ble_device)
    device.disconnect.assert_not_awaited()

@pytest.mark.asyncio
async def test_hard_reset_claim_remains_consumed_after_uncertain_delivery() -> None:
    """An uncertain first dispatch prevents a sibling flow from retrying reset."""

    # Arrange - make the first packet-61 delivery uncertain.
    ble_device = object()
    device = SimpleNamespace(
        supports_guarded_hard_reset=True,
        _dispatch_hard_reset=AsyncMock(
            side_effect=HardResetDispatchUncertainError("may have rebooted")
        ),
        disconnect=AsyncMock(),
    )
    coordinator = _reset_dispatch_coordinator(device, ble_device)

    # Act - record uncertainty, then attempt a second Configure-flow dispatch.
    with pytest.raises(HardResetDeliveryUncertainError, match="may have rebooted"):
        await _dispatch_reset(coordinator)
    with pytest.raises(HardResetUnavailableError, match="already been claimed"):
        await _dispatch_reset(coordinator)

    # Assert - uncertainty never permits an automatic or sibling retry.
    assert coordinator._hard_reset_dispatch_claimed is True
    device._dispatch_hard_reset.assert_awaited_once_with(ble_device)
    coordinator._begin_hard_reset_recovery.assert_called_once_with(
        delivery_uncertain=True
    )
    device.disconnect.assert_not_awaited()

@pytest.mark.asyncio
async def test_hard_reset_claim_releases_after_definite_pre_dispatch_failure() -> None:
    """A definite pre-send failure permits a later fresh Configure-flow retry."""

    # Arrange - fail before packet 61 can enter the uncertain send phase, then recover.
    ble_device = object()
    expected = HardResetDispatchResult(transport="test")
    device = SimpleNamespace(
        supports_guarded_hard_reset=True,
        _dispatch_hard_reset=AsyncMock(
            side_effect=[DeviceError("authentication failed"), expected]
        ),
        disconnect=AsyncMock(),
    )
    coordinator = _reset_dispatch_coordinator(device, ble_device)

    # Act - the first flow fails definitely, then a later fresh flow retries.
    with pytest.raises(HardResetUnavailableError, match="not dispatched"):
        await _dispatch_reset(coordinator)
    claim_after_failure = coordinator._hard_reset_dispatch_claimed
    result = await _dispatch_reset(coordinator)

    # Assert - only the definite failure releases the claim; retry then consumes it.
    assert claim_after_failure is False
    assert result is expected
    assert coordinator._hard_reset_dispatch_claimed is True
    assert device._dispatch_hard_reset.await_count == 2
    assert device._dispatch_hard_reset.await_args_list == [
        call(ble_device),
        call(ble_device),
    ]
    coordinator._begin_hard_reset_recovery.assert_called_once_with(
        delivery_uncertain=False
    )
    device.disconnect.assert_awaited_once_with()

@pytest.mark.asyncio
async def test_hard_reset_options_preserves_uncertain_delivery() -> None:
    """A possible reboot before acknowledgement remains explicitly uncertain."""

    # Arrange - make the internal reset primitive report uncertain dispatch.
    ble_device = object()
    device = SimpleNamespace(
        supports_guarded_hard_reset=True,
        _dispatch_hard_reset=AsyncMock(
            side_effect=HardResetDispatchUncertainError("may have rebooted")
        ),
        disconnect=AsyncMock(),
    )
    coordinator = _reset_dispatch_coordinator(device, ble_device)

    # Act / Assert - uncertainty is translated without pretending reset was not sent.
    with pytest.raises(HardResetDeliveryUncertainError, match="may have rebooted"):
        await _dispatch_reset(coordinator)
    device._dispatch_hard_reset.assert_awaited_once_with(ble_device)
    coordinator._begin_hard_reset_recovery.assert_called_once_with(
        delivery_uncertain=True
    )
    device.disconnect.assert_not_awaited()

@pytest.mark.asyncio
async def test_hard_reset_cancellation_after_possible_send_starts_recovery() -> None:
    """Flow cancellation cannot abandon a reset that may already have been sent."""

    # Arrange - simulate cancellation after the device primitive entered reset state.
    ble_device = object()
    device = SimpleNamespace(
        supports_guarded_hard_reset=True,
        hard_reset_recovery_pending=True,
        _dispatch_hard_reset=AsyncMock(side_effect=asyncio.CancelledError),
        disconnect=AsyncMock(),
    )
    coordinator = _reset_dispatch_coordinator(device, ble_device)

    # Act - cancel the interactive caller while packet-61 delivery is uncertain.
    with pytest.raises(asyncio.CancelledError):
        await _dispatch_reset(coordinator, baseline_time=12.0)

    # Assert - recovery ownership transfers synchronously before cancellation escapes.
    assert coordinator._hard_reset_dispatch_claimed is True
    assert coordinator._hard_reset_baseline_advertisement_time == 12.0
    coordinator._begin_hard_reset_recovery.assert_called_once_with(
        delivery_uncertain=True
    )
    device.disconnect.assert_not_awaited()

@pytest.mark.asyncio
async def test_hard_reset_recovery_waits_for_fresh_advertisement_and_recovers(
    monkeypatch,
) -> None:
    """Recovery uses one address-filtered advertisement before one reconnect."""

    # Arrange - retain a pre-reset snapshot and make the unit reappear once.
    coordinator = _coordinator()
    baseline = _reset_data()
    recovered = _reset_data()
    ble_device = object()
    coordinator.device = SimpleNamespace(
        recover_after_hard_reset=AsyncMock(return_value=recovered),
        disconnect=AsyncMock(),
    )
    coordinator._hard_reset_recovery_mode = True
    coordinator._hard_reset_baseline_global_settings = (
        baseline.global_settings.raw_record
    )
    coordinator._hard_reset_baseline_silent_hours = tuple(baseline.silent_hours)
    coordinator._hard_reset_baseline_advertisement_time = 100.0
    coordinator.async_set_updated_data = Mock()
    coordinator.async_set_update_error = Mock()
    process_advertisements = AsyncMock(return_value=object())
    lookup = Mock(return_value=ble_device)
    monkeypatch.setattr(
        coordinator_module.bluetooth, "async_scanner_count", Mock(return_value=1)
    )
    monkeypatch.setattr(
        coordinator_module.bluetooth,
        "async_process_advertisements",
        process_advertisements,
    )
    monkeypatch.setattr(
        coordinator_module.bluetooth, "async_ble_device_from_address", lookup
    )

    # Act - let the coordinator observe the reboot advertisement and reconnect once.
    result = await VentaxiaMultihomeCoordinator._async_recover_after_hard_reset(
        coordinator, delivery_uncertain=False
    )

    # Assert - only this address was awaited and normal availability is restored.
    assert result == HardResetRecoveryResult(
        outcome="recovered",
        detail=(
            "The unit returned on a fresh Bluetooth advertisement and Home Assistant "
            "completed one authenticated telemetry/settings read."
        ),
        configuration_changed=False,
        delivery_uncertain=False,
    )
    process_advertisements.assert_awaited_once()
    args = process_advertisements.await_args.args
    assert args[0] is coordinator.hass
    assert args[1](SimpleNamespace(time=100.0)) is False
    assert args[1](SimpleNamespace(time=99.0)) is False
    assert args[1](SimpleNamespace(time=100.1)) is True
    assert args[2] == {"address": "AA:BB", "connectable": True}
    assert args[3] is BluetoothScanningMode.ACTIVE
    assert args[4] == HARD_RESET_RECOVERY_TIMEOUT
    lookup.assert_called_once_with(coordinator.hass, "AA:BB", connectable=True)
    coordinator.device.recover_after_hard_reset.assert_awaited_once_with(ble_device)
    coordinator.device.disconnect.assert_awaited_once_with()
    assert coordinator._last_ble_device is ble_device
    assert coordinator._hard_reset_recovery_mode is False
    coordinator.async_set_updated_data.assert_called_once_with(recovered)
    coordinator.async_set_update_error.assert_not_called()

@pytest.mark.asyncio
async def test_hard_reset_recovery_surfaces_configuration_change(
    monkeypatch,
) -> None:
    """A successful reconnect reports changed commissioning state."""

    # Arrange - recover with a different packet-137 settings prefix.
    coordinator = _coordinator()
    baseline = _reset_data("06082532")
    recovered = _reset_data("07082532")
    coordinator.device = SimpleNamespace(
        recover_after_hard_reset=AsyncMock(return_value=recovered),
        disconnect=AsyncMock(),
    )
    coordinator._hard_reset_recovery_mode = True
    coordinator._hard_reset_baseline_global_settings = (
        baseline.global_settings.raw_record
    )
    coordinator._hard_reset_baseline_silent_hours = tuple(baseline.silent_hours)
    coordinator.async_set_updated_data = Mock()
    coordinator.async_set_update_error = Mock()
    monkeypatch.setattr(
        coordinator_module.bluetooth, "async_scanner_count", Mock(return_value=1)
    )
    monkeypatch.setattr(
        coordinator_module.bluetooth,
        "async_process_advertisements",
        AsyncMock(return_value=object()),
    )
    monkeypatch.setattr(
        coordinator_module.bluetooth,
        "async_ble_device_from_address",
        Mock(return_value=object()),
    )

    # Act - complete one controlled post-reset reconnect.
    result = await VentaxiaMultihomeCoordinator._async_recover_after_hard_reset(
        coordinator, delivery_uncertain=True
    )

    # Assert - recovery succeeds but requires configuration review/restoration.
    assert result.outcome == "recovered_configuration_changed"
    assert result.configuration_changed is True
    assert result.delivery_uncertain is True
    assert "differ from the pre-reset snapshot" in result.detail
    assert coordinator._hard_reset_recovery_mode is False
    coordinator.async_set_updated_data.assert_called_once_with(recovered)

@pytest.mark.asyncio
async def test_hard_reset_recovery_requires_repair_after_setup_code_rejection(
    monkeypatch,
) -> None:
    """A reset-cleared setup code is surfaced as a re-pairing requirement."""

    # Arrange - let the device advertise but reject the stored application code.
    coordinator = _coordinator()
    coordinator.device = SimpleNamespace(
        recover_after_hard_reset=AsyncMock(
            side_effect=SetupCodeRejectedError("rejected")
        ),
        disconnect=AsyncMock(),
    )
    coordinator._hard_reset_recovery_mode = True
    coordinator.async_set_updated_data = Mock()
    coordinator.async_set_update_error = Mock()
    monkeypatch.setattr(
        coordinator_module.bluetooth, "async_scanner_count", Mock(return_value=1)
    )
    monkeypatch.setattr(
        coordinator_module.bluetooth,
        "async_process_advertisements",
        AsyncMock(return_value=object()),
    )
    monkeypatch.setattr(
        coordinator_module.bluetooth,
        "async_ble_device_from_address",
        Mock(return_value=object()),
    )

    # Act - attempt the single reconnect after the fresh advertisement.
    result = await VentaxiaMultihomeCoordinator._async_recover_after_hard_reset(
        coordinator, delivery_uncertain=False
    )

    # Assert - no reconnect loop runs and HA remains deliberately unavailable.
    assert result.outcome == "pairing_required"
    assert "physical pairing mode" in result.detail
    assert coordinator._hard_reset_recovery_mode is True
    coordinator.device.recover_after_hard_reset.assert_awaited_once()
    assert coordinator.device.disconnect.await_count == 2
    coordinator.async_set_updated_data.assert_not_called()
    coordinator.async_set_update_error.assert_called_once()

@pytest.mark.asyncio
async def test_hard_reset_recovery_timeout_is_bounded_and_actionable(
    monkeypatch,
) -> None:
    """A unit that never returns times out without a reconnect storm."""

    # Arrange - keep the shared scanner alive but never advertise this address.
    coordinator = _coordinator()
    coordinator.device = SimpleNamespace(
        recover_after_hard_reset=AsyncMock(),
        disconnect=AsyncMock(),
    )
    coordinator._hard_reset_recovery_mode = True
    coordinator.async_set_updated_data = Mock()
    coordinator.async_set_update_error = Mock()
    process_advertisements = AsyncMock(side_effect=TimeoutError)
    lookup = Mock()
    monkeypatch.setattr(
        coordinator_module.bluetooth, "async_scanner_count", Mock(return_value=1)
    )
    monkeypatch.setattr(
        coordinator_module.bluetooth,
        "async_process_advertisements",
        process_advertisements,
    )
    monkeypatch.setattr(
        coordinator_module.bluetooth, "async_ble_device_from_address", lookup
    )

    # Act - let the address-specific recovery window expire.
    result = await VentaxiaMultihomeCoordinator._async_recover_after_hard_reset(
        coordinator, delivery_uncertain=False
    )

    # Assert - no connection is attempted and guidance explicitly forbids reset retry.
    assert result.outcome == "timed_out"
    assert str(HARD_RESET_RECOVERY_TIMEOUT) in result.detail
    assert "Do not resend the reset" in result.detail
    assert coordinator._hard_reset_recovery_mode is True
    process_advertisements.assert_awaited_once()
    coordinator.device.disconnect.assert_awaited_once_with()
    coordinator.device.recover_after_hard_reset.assert_not_awaited()
    lookup.assert_not_called()
    coordinator.async_set_updated_data.assert_not_called()
    coordinator.async_set_update_error.assert_called_once()

@pytest.mark.asyncio
async def test_normal_polling_is_suppressed_while_reset_recovery_owns_route() -> None:
    """The 10-second coordinator poll cannot race reset reboot recovery."""

    # Arrange - make every Bluetooth path fail the test if normal polling reaches it.
    coordinator = SimpleNamespace(
        _hard_reset_recovery_mode=True,
        _ble_device=Mock(),
        device=SimpleNamespace(update=AsyncMock(), disconnect=AsyncMock()),
    )

    # Act / Assert - polling reports unavailable before any Bluetooth lookup.
    with pytest.raises(UpdateFailed, match="recovery owns the Bluetooth route"):
        await VentaxiaMultihomeCoordinator._async_update_data(coordinator)
    coordinator._ble_device.assert_not_called()
    coordinator.device.update.assert_not_awaited()
    coordinator.device.disconnect.assert_not_awaited()

@pytest.mark.asyncio
async def test_hard_reset_shutdown_cancels_recovery_before_disconnect() -> None:
    """Entry unload cannot leave a stale reset-recovery task running."""

    # Arrange - keep one coordinator-owned recovery task blocked indefinitely.
    started = asyncio.Event()

    async def recovery() -> HardResetRecoveryResult:
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    coordinator = _coordinator()
    coordinator.device = SimpleNamespace(disconnect=AsyncMock())
    task = asyncio.create_task(recovery())
    coordinator._hard_reset_recovery_task = task
    await started.wait()

    # Act - unload/shutdown the coordinator.
    await VentaxiaMultihomeCoordinator.async_shutdown(coordinator)

    # Assert - recovery is cancelled before the device is disconnected.
    assert task.cancelled()
    coordinator.device.disconnect.assert_awaited_once_with()

@pytest.mark.asyncio
async def test_airflow_profile_publishes_only_confirmed_settings() -> None:
    """A successful profile write replaces settings after exact readback."""

    # Arrange - retain telemetry and return a distinct confirmed 7/8/37/50 record.
    current = MultihomeData(
        zone=object(),
        system=object(),
        global_settings=_settings(),
        last_successful_update=datetime.now(UTC),
    )
    confirmed = _settings("07082532")
    ble_device = object()
    device = SimpleNamespace(
        supports_global_airflow_configuration=True,
        global_settings_write_ready=True,
        set_airflow_profile=AsyncMock(return_value=confirmed),
        disconnect=AsyncMock(),
    )
    coordinator = SimpleNamespace(
        device=device,
        data=current,
        last_update_success=True,
        _ble_device=lambda: ble_device,
        async_set_updated_data=Mock(),
        async_set_update_error=Mock(),
    )

    # Act - apply one reviewed four-level commissioning profile.
    await VentaxiaMultihomeCoordinator.async_set_airflow_profile(
        coordinator, low=7, normal=8, boost=37, purge=50
    )

    # Assert - exactly one device operation runs and publishes its fresh readback.
    device.set_airflow_profile.assert_awaited_once_with(
        ble_device, low=7, normal=8, boost=37, purge=50
    )
    published = coordinator.async_set_updated_data.call_args.args[0]
    assert published.global_settings is confirmed
    assert published.zone is current.zone
    assert published.system is current.system
    coordinator.async_set_update_error.assert_not_called()
    device.disconnect.assert_not_awaited()

@pytest.mark.asyncio
async def test_airflow_profile_rejects_unsupported_model_before_bluetooth() -> None:
    """Unknown models cannot reach packet-136 writes through the coordinator."""

    # Arrange - expose current data but no validated airflow capability.
    device = SimpleNamespace(
        supports_global_airflow_configuration=False,
        global_settings_write_ready=True,
        set_airflow_profile=AsyncMock(),
    )
    coordinator = SimpleNamespace(
        device=device,
        data=object(),
        last_update_success=True,
        _ble_device=Mock(),
    )

    # Act / Assert - the model gate rejects the operation before Bluetooth lookup.
    with pytest.raises(AirflowConfigurationNotSupportedError):
        await VentaxiaMultihomeCoordinator.async_set_airflow_profile(
            coordinator, low=7, normal=8, boost=37, purge=50
        )
    coordinator._ble_device.assert_not_called()
    device.set_airflow_profile.assert_not_awaited()

@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("data", "last_success", "write_ready"),
    [
        (None, True, True),
        (object(), False, True),
        (object(), True, False),
    ],
)
async def test_airflow_profile_requires_current_writable_snapshot(
    data, last_success, write_ready
) -> None:
    """Missing, stale, or unconfirmed global data cannot be configured."""

    # Arrange - vary each condition that makes packet-137 state untrustworthy.
    device = SimpleNamespace(
        supports_global_airflow_configuration=True,
        global_settings_write_ready=write_ready,
        set_airflow_profile=AsyncMock(),
    )
    coordinator = SimpleNamespace(
        device=device,
        data=data,
        last_update_success=last_success,
        _ble_device=Mock(),
    )

    # Act / Assert - the write remains blocked before resolving a BLE route.
    with pytest.raises(AirflowConfigurationUnavailableError):
        await VentaxiaMultihomeCoordinator.async_set_airflow_profile(
            coordinator, low=7, normal=8, boost=37, purge=50
        )
    coordinator._ble_device.assert_not_called()
    device.set_airflow_profile.assert_not_awaited()

@pytest.mark.asyncio
async def test_sensor_thresholds_publish_only_confirmed_settings() -> None:
    """A successful threshold operation publishes its exact readback."""

    # Arrange - retain telemetry and return a distinct confirmed settings object.
    current = MultihomeData(
        zone=object(),
        system=object(),
        global_settings=_settings(),
        last_successful_update=datetime.now(UTC),
    )
    confirmed = decode_global_settings(
        bytes.fromhex(
            "06082532004601000100000001040f19000a0a010304a000b4000f4b01030f4b01030103"
        )
    )
    ble_device = object()
    device = SimpleNamespace(
        supports_sensor_threshold_configuration=True,
        global_settings_write_ready=True,
        set_sensor_thresholds=AsyncMock(return_value=confirmed),
        disconnect=AsyncMock(),
    )
    coordinator = SimpleNamespace(
        device=device,
        data=current,
        last_update_success=True,
        _ble_device=lambda: ble_device,
        async_set_updated_data=Mock(),
        async_set_update_error=Mock(),
    )

    # Act - apply one reviewed three-threshold profile.
    await VentaxiaMultihomeCoordinator.async_set_sensor_thresholds(
        coordinator, humidity=70, co2_boost=1600, co2_purge=1800
    )

    # Assert - only the confirmed settings snapshot is replaced.
    device.set_sensor_thresholds.assert_awaited_once_with(
        ble_device, humidity=70, co2_boost=1600, co2_purge=1800
    )
    published = coordinator.async_set_updated_data.call_args.args[0]
    assert published.global_settings is confirmed
    assert published.zone is current.zone
    assert published.system is current.system
    coordinator.async_set_update_error.assert_not_called()

@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("supported", "data", "last_success", "write_ready", "error"),
    [
        (False, object(), True, True, SensorThresholdConfigurationNotSupportedError),
        (True, None, True, True, SensorThresholdConfigurationUnavailableError),
        (True, object(), False, True, SensorThresholdConfigurationUnavailableError),
        (True, object(), True, False, SensorThresholdConfigurationUnavailableError),
    ],
)
async def test_sensor_thresholds_reject_unsupported_or_stale_state_before_io(
    supported, data, last_success, write_ready, error
) -> None:
    """Identity and current-snapshot guards run before Bluetooth lookup."""

    # Arrange - vary each prerequisite for the guarded threshold operation.
    device = SimpleNamespace(
        supports_sensor_threshold_configuration=supported,
        global_settings_write_ready=write_ready,
        set_sensor_thresholds=AsyncMock(),
    )
    coordinator = SimpleNamespace(
        device=device,
        data=data,
        last_update_success=last_success,
        _ble_device=Mock(),
    )

    # Act / Assert - reject without resolving or using a BLE path.
    with pytest.raises(error):
        await VentaxiaMultihomeCoordinator.async_set_sensor_thresholds(
            coordinator, humidity=70, co2_boost=1000, co2_purge=1500
        )
    coordinator._ble_device.assert_not_called()
    device.set_sensor_thresholds.assert_not_awaited()

@pytest.mark.asyncio
async def test_humidity_response_publishes_only_confirmed_settings() -> None:
    """A successful response operation publishes its exact readback."""

    # Arrange - retain telemetry and return a distinct confirmed settings object.
    current = MultihomeData(
        zone=object(),
        system=object(),
        global_settings=_settings(),
        last_successful_update=datetime.now(UTC),
    )
    confirmed = decode_global_settings(bytes(36))
    ble_device = object()
    device = SimpleNamespace(
        supports_humidity_response_configuration=True,
        global_settings_write_ready=True,
        set_humidity_response=AsyncMock(return_value=confirmed),
        disconnect=AsyncMock(),
    )
    coordinator = SimpleNamespace(
        device=device,
        data=current,
        last_update_success=True,
        _ble_device=lambda: ble_device,
        async_set_updated_data=Mock(),
        async_set_update_error=Mock(),
    )

    # Act - apply one reviewed two-flag profile.
    await VentaxiaMultihomeCoordinator.async_set_humidity_response(
        coordinator, rapid=True, ambient=False
    )

    # Assert - only the exact confirmed settings snapshot is replaced.
    device.set_humidity_response.assert_awaited_once_with(
        ble_device, rapid=True, ambient=False
    )
    published = coordinator.async_set_updated_data.call_args.args[0]
    assert published.global_settings is confirmed
    assert published.zone is current.zone
    assert published.system is current.system

@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("supported", "data", "last_success", "write_ready", "error"),
    [
        (
            False,
            object(),
            True,
            True,
            HumidityResponseConfigurationNotSupportedError,
        ),
        (True, None, True, True, HumidityResponseConfigurationUnavailableError),
        (True, object(), False, True, HumidityResponseConfigurationUnavailableError),
        (True, object(), True, False, HumidityResponseConfigurationUnavailableError),
    ],
)
async def test_humidity_response_rejects_unsupported_or_stale_state_before_io(
    supported, data, last_success, write_ready, error
) -> None:
    """Identity and current-snapshot guards run before Bluetooth lookup."""

    # Arrange - vary each prerequisite for the guarded response operation.
    device = SimpleNamespace(
        supports_humidity_response_configuration=supported,
        global_settings_write_ready=write_ready,
        set_humidity_response=AsyncMock(),
    )
    coordinator = SimpleNamespace(
        device=device,
        data=data,
        last_update_success=last_success,
        _ble_device=Mock(),
    )

    # Act / Assert - reject without resolving or using a BLE path.
    with pytest.raises(error):
        await VentaxiaMultihomeCoordinator.async_set_humidity_response(
            coordinator, rapid=True, ambient=False
        )
    coordinator._ble_device.assert_not_called()
    device.set_humidity_response.assert_not_awaited()

@pytest.mark.asyncio
async def test_comfort_mode_publishes_only_confirmed_settings() -> None:
    """A successful Comfort operation publishes its exact readback."""

    # Arrange - retain telemetry and return a distinct confirmed settings object.
    current = MultihomeData(
        zone=object(),
        system=object(),
        global_settings=_settings(),
        last_successful_update=datetime.now(UTC),
    )
    confirmed = decode_global_settings(bytes(36))
    ble_device = object()
    device = SimpleNamespace(
        supports_comfort_mode_configuration=True,
        global_settings_write_ready=True,
        set_comfort_mode=AsyncMock(return_value=confirmed),
        disconnect=AsyncMock(),
    )
    coordinator = SimpleNamespace(
        device=device,
        data=current,
        last_update_success=True,
        _ble_device=lambda: ble_device,
        async_set_updated_data=Mock(),
        async_set_update_error=Mock(),
    )

    # Act - apply one reviewed Comfort value.
    await VentaxiaMultihomeCoordinator.async_set_comfort_mode(coordinator, enabled=True)

    # Assert - only the exact confirmed settings snapshot is replaced.
    device.set_comfort_mode.assert_awaited_once_with(ble_device, enabled=True)
    published = coordinator.async_set_updated_data.call_args.args[0]
    assert published.global_settings is confirmed
    assert published.zone is current.zone

@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("supported", "data", "last_success", "write_ready", "error"),
    [
        (
            False,
            object(),
            True,
            True,
            ComfortModeConfigurationNotSupportedError,
        ),
        (True, None, True, True, ComfortModeConfigurationUnavailableError),
        (True, object(), False, True, ComfortModeConfigurationUnavailableError),
        (True, object(), True, False, ComfortModeConfigurationUnavailableError),
    ],
)
async def test_comfort_mode_rejects_unsupported_or_stale_state_before_io(
    supported, data, last_success, write_ready, error
) -> None:
    """Comfort identity and snapshot guards run before Bluetooth lookup."""

    # Arrange - vary each prerequisite for the candidate operation.
    device = SimpleNamespace(
        supports_comfort_mode_configuration=supported,
        global_settings_write_ready=write_ready,
        set_comfort_mode=AsyncMock(),
    )
    coordinator = SimpleNamespace(
        device=device,
        data=data,
        last_update_success=last_success,
        _ble_device=Mock(),
    )

    # Act / Assert - reject without resolving or using a BLE path.
    with pytest.raises(error):
        await VentaxiaMultihomeCoordinator.async_set_comfort_mode(
            coordinator, enabled=True
        )
    coordinator._ble_device.assert_not_called()
    device.set_comfort_mode.assert_not_awaited()

@pytest.mark.asyncio
async def test_delay_failure_preserves_confirmed_entity_availability() -> None:
    """A failed candidate write does not masquerade as a failed telemetry poll."""

    # Arrange - fail field-7 confirmation while retaining the last good snapshot.
    error = TransactionTimeoutError("packet 137 timed out")
    confirmed_data = object()
    device = SimpleNamespace(
        supports_delay_overrun_configuration=True,
        global_settings_write_ready=True,
        set_delay_overrun=AsyncMock(side_effect=error),
        disconnect=AsyncMock(),
    )
    coordinator = SimpleNamespace(
        device=device,
        data=confirmed_data,
        last_update_success=True,
        _ble_device=lambda: object(),
        async_set_updated_data=Mock(),
        async_set_update_error=Mock(),
    )

    # Act - attempt the isolated candidate write and lose its readback.
    with pytest.raises(HomeAssistantError, match="packet 137 timed out"):
        await VentaxiaMultihomeCoordinator.async_set_delay_overrun(
            coordinator,
            delay_enabled=True,
            delay_minutes=10,
            overrun_enabled=True,
            overrun_minutes=10,
        )

    # Assert - reset BLE, but keep unrelated entities on their confirmed snapshot.
    assert coordinator.data is confirmed_data
    device.disconnect.assert_awaited_once()
    coordinator.async_set_update_error.assert_not_called()
    coordinator.async_set_updated_data.assert_not_called()


@pytest.mark.parametrize(
    ("supported", "data", "last_success", "write_ready", "error"),
    [
        (False, object(), True, True, DelayOverrunConfigurationNotSupportedError),
        (True, None, True, True, DelayOverrunConfigurationUnavailableError),
        (True, object(), False, True, DelayOverrunConfigurationUnavailableError),
        (True, object(), True, False, DelayOverrunConfigurationUnavailableError),
    ],
)
@pytest.mark.asyncio
async def test_delay_rejects_unsupported_or_stale_state_before_io(
    supported, data, last_success, write_ready, error
) -> None:
    """Delay identity and snapshot guards run before Bluetooth lookup."""

    device = SimpleNamespace(
        supports_delay_overrun_configuration=supported,
        global_settings_write_ready=write_ready,
        set_delay_overrun=AsyncMock(),
    )
    coordinator = SimpleNamespace(
        device=device,
        data=data,
        last_update_success=last_success,
        _ble_device=Mock(),
    )

    with pytest.raises(error):
        await VentaxiaMultihomeCoordinator.async_set_delay_overrun(
            coordinator,
            delay_enabled=True,
            delay_minutes=10,
            overrun_enabled=True,
            overrun_minutes=10,
        )
    coordinator._ble_device.assert_not_called()
    device.set_delay_overrun.assert_not_awaited()

@pytest.mark.asyncio
async def test_temperature_validation_publishes_only_confirmed_settings() -> None:
    """A successful temperature operation publishes its exact readback."""

    # Arrange - retain telemetry and return a distinct confirmed settings object.
    current = MultihomeData(
        zone=object(),
        system=object(),
        global_settings=_settings(),
        last_successful_update=datetime.now(UTC),
    )
    confirmed = decode_global_settings(bytes(36))
    ble_device = object()
    device = SimpleNamespace(
        supports_temperature_threshold_validation=True,
        global_settings_write_ready=True,
        set_temperature_threshold_validation=AsyncMock(return_value=confirmed),
        disconnect=AsyncMock(),
    )
    coordinator = SimpleNamespace(
        device=device,
        data=current,
        last_update_success=True,
        _ble_device=lambda: ble_device,
        async_set_updated_data=Mock(),
        async_set_update_error=Mock(),
    )

    # Act - apply one reviewed low-threshold validation change.
    await VentaxiaMultihomeCoordinator.async_set_temperature_threshold_validation(
        coordinator,
        low_action=1,
        high_action=4,
        low_threshold=14,
        high_threshold=25,
    )

    # Assert - only the exact confirmed settings snapshot is replaced.
    device.set_temperature_threshold_validation.assert_awaited_once_with(
        ble_device,
        low_action=1,
        high_action=4,
        low_threshold=14,
        high_threshold=25,
    )
    published = coordinator.async_set_updated_data.call_args.args[0]
    assert published.global_settings is confirmed
    assert published.zone is current.zone

@pytest.mark.asyncio
async def test_ls_action_validation_publishes_only_confirmed_settings() -> None:
    """A successful LS operation publishes only its exact readback snapshot."""

    # Arrange - retain telemetry and return a distinct confirmed settings object.
    current = MultihomeData(
        zone=object(),
        system=object(),
        global_settings=_settings(),
        last_successful_update=datetime.now(UTC),
    )
    confirmed = decode_global_settings(bytes(36))
    ble_device = object()
    device = SimpleNamespace(
        supports_ls_action_validation=True,
        global_settings_write_ready=True,
        set_ls_action_validation=AsyncMock(return_value=confirmed),
        disconnect=AsyncMock(),
    )
    coordinator = SimpleNamespace(
        device=device,
        data=current,
        last_update_success=True,
        _ble_device=lambda: ble_device,
        async_set_updated_data=Mock(),
        async_set_update_error=Mock(),
    )

    # Act - apply one reviewed LS1 action change.
    await VentaxiaMultihomeCoordinator.async_set_ls_action_validation(
        coordinator, ls1_action=3, ls2_action=3, ls3_action=4
    )

    # Assert - only the exact confirmed settings snapshot is replaced.
    device.set_ls_action_validation.assert_awaited_once_with(
        ble_device, ls1_action=3, ls2_action=3, ls3_action=4
    )
    published = coordinator.async_set_updated_data.call_args.args[0]
    assert published.global_settings is confirmed
    assert published.zone is current.zone

@pytest.mark.asyncio
async def test_analogue_input_1_validation_publishes_only_confirmed_settings() -> None:
    """A successful analogue-input write publishes only exact device readback."""

    # Arrange - retain telemetry and return a distinct confirmed settings object.
    current = MultihomeData(
        zone=object(),
        system=object(),
        global_settings=_settings(),
        last_successful_update=datetime.now(UTC),
    )
    confirmed = decode_global_settings(bytes(36))
    ble_device = object()
    device = SimpleNamespace(
        supports_analogue_input_1_validation=True,
        global_settings_write_ready=True,
        set_analogue_input_1_validation=AsyncMock(return_value=confirmed),
        disconnect=AsyncMock(),
    )
    coordinator = SimpleNamespace(
        device=device,
        data=current,
        last_update_success=True,
        _ble_device=lambda: ble_device,
        async_set_updated_data=Mock(),
        async_set_update_error=Mock(),
    )

    # Act - apply one reviewed 1.5 V -> 1.6 V threshold change.
    await VentaxiaMultihomeCoordinator.async_set_analogue_input_1_validation(
        coordinator,
        low_action=1,
        high_action=3,
        low_threshold=16,
        high_threshold=75,
    )

    # Assert - the exact confirmed settings snapshot replaces only settings state.
    device.set_analogue_input_1_validation.assert_awaited_once_with(
        ble_device,
        low_action=1,
        high_action=3,
        low_threshold=16,
        high_threshold=75,
    )
    published = coordinator.async_set_updated_data.call_args.args[0]
    assert published.global_settings is confirmed
    assert published.zone is current.zone

@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("supported", "data", "last_success", "write_ready", "error"),
    [
        (
            False,
            object(),
            True,
            True,
            AnalogueInput1ValidationNotSupportedError,
        ),
        (True, None, True, True, AnalogueInput1ValidationUnavailableError),
        (True, object(), False, True, AnalogueInput1ValidationUnavailableError),
        (True, object(), True, False, AnalogueInput1ValidationUnavailableError),
    ],
)
async def test_analogue_input_1_validation_rejects_stale_state_before_io(
    supported, data, last_success, write_ready, error
) -> None:
    """Analogue-input identity and snapshot guards run before Bluetooth lookup."""

    # Arrange - vary each prerequisite for one guarded field validation.
    device = SimpleNamespace(
        supports_analogue_input_1_validation=supported,
        global_settings_write_ready=write_ready,
        set_analogue_input_1_validation=AsyncMock(),
    )
    coordinator = SimpleNamespace(
        device=device,
        data=data,
        last_update_success=last_success,
        _ble_device=Mock(),
    )

    # Act / Assert - reject without resolving or using a Bluetooth path.
    with pytest.raises(error):
        await VentaxiaMultihomeCoordinator.async_set_analogue_input_1_validation(
            coordinator,
            low_action=1,
            high_action=3,
            low_threshold=16,
            high_threshold=75,
        )
    coordinator._ble_device.assert_not_called()
    device.set_analogue_input_1_validation.assert_not_awaited()
@pytest.mark.asyncio
async def test_analogue_input_2_validation_publishes_only_confirmed_settings() -> None:
    """A successful analogue-input write publishes only exact device readback."""

    # Arrange - retain telemetry and return a distinct confirmed settings object.
    current = MultihomeData(
        zone=object(),
        system=object(),
        global_settings=_settings(),
        last_successful_update=datetime.now(UTC),
    )
    confirmed = decode_global_settings(bytes(36))
    ble_device = object()
    device = SimpleNamespace(
        supports_analogue_input_2_validation=True,
        global_settings_write_ready=True,
        set_analogue_input_2_validation=AsyncMock(return_value=confirmed),
        disconnect=AsyncMock(),
    )
    coordinator = SimpleNamespace(
        device=device,
        data=current,
        last_update_success=True,
        _ble_device=lambda: ble_device,
        async_set_updated_data=Mock(),
        async_set_update_error=Mock(),
    )

    # Act - apply one reviewed 1.5 V -> 1.6 V threshold change.
    await VentaxiaMultihomeCoordinator.async_set_analogue_input_2_validation(
        coordinator,
        low_action=1,
        high_action=3,
        low_threshold=16,
        high_threshold=75,
    )

    # Assert - the exact confirmed settings snapshot replaces only settings state.
    device.set_analogue_input_2_validation.assert_awaited_once_with(
        ble_device,
        low_action=1,
        high_action=3,
        low_threshold=16,
        high_threshold=75,
    )
    published = coordinator.async_set_updated_data.call_args.args[0]
    assert published.global_settings is confirmed
    assert published.zone is current.zone

@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("supported", "data", "last_success", "write_ready", "error"),
    [
        (
            False,
            object(),
            True,
            True,
            AnalogueInput2ValidationNotSupportedError,
        ),
        (True, None, True, True, AnalogueInput2ValidationUnavailableError),
        (True, object(), False, True, AnalogueInput2ValidationUnavailableError),
        (True, object(), True, False, AnalogueInput2ValidationUnavailableError),
    ],
)
async def test_analogue_input_2_validation_rejects_stale_state_before_io(
    supported, data, last_success, write_ready, error
) -> None:
    """Analogue-input identity and snapshot guards run before Bluetooth lookup."""

    # Arrange - vary each prerequisite for one guarded field validation.
    device = SimpleNamespace(
        supports_analogue_input_2_validation=supported,
        global_settings_write_ready=write_ready,
        set_analogue_input_2_validation=AsyncMock(),
    )
    coordinator = SimpleNamespace(
        device=device,
        data=data,
        last_update_success=last_success,
        _ble_device=Mock(),
    )

    # Act / Assert - reject without resolving or using a Bluetooth path.
    with pytest.raises(error):
        await VentaxiaMultihomeCoordinator.async_set_analogue_input_2_validation(
            coordinator,
            low_action=1,
            high_action=3,
            low_threshold=16,
            high_threshold=75,
        )
    coordinator._ble_device.assert_not_called()
    device.set_analogue_input_2_validation.assert_not_awaited()

@pytest.mark.asyncio
async def test_digital_input_validation_publishes_only_confirmed_settings() -> None:
    """A successful digital-input write publishes only exact device readback."""

    # Arrange - retain telemetry and return a distinct confirmed settings object.
    current = MultihomeData(
        zone=object(),
        system=object(),
        global_settings=_settings(),
        last_successful_update=datetime.now(UTC),
    )
    confirmed = decode_global_settings(bytes(36))
    ble_device = object()
    device = SimpleNamespace(
        supports_digital_input_validation=True,
        global_settings_write_ready=True,
        set_digital_input_validation=AsyncMock(return_value=confirmed),
        disconnect=AsyncMock(),
    )
    coordinator = SimpleNamespace(
        device=device,
        data=current,
        last_update_success=True,
        _ble_device=lambda: ble_device,
        async_set_updated_data=Mock(),
        async_set_update_error=Mock(),
    )

    # Act - apply one reviewed Digital input 1 action change.
    await VentaxiaMultihomeCoordinator.async_set_digital_input_validation(
        coordinator,
        digital_input_1_action=3,
        digital_input_2_action=3,
    )

    # Assert - exact confirmed settings replace only settings state.
    device.set_digital_input_validation.assert_awaited_once_with(
        ble_device,
        digital_input_1_action=3,
        digital_input_2_action=3,
    )
    published = coordinator.async_set_updated_data.call_args.args[0]
    assert published.global_settings is confirmed
    assert published.zone is current.zone

@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("supported", "data", "last_success", "write_ready", "error"),
    [
        (False, object(), True, True, DigitalInputValidationNotSupportedError),
        (True, None, True, True, DigitalInputValidationUnavailableError),
        (True, object(), False, True, DigitalInputValidationUnavailableError),
        (True, object(), True, False, DigitalInputValidationUnavailableError),
    ],
)
async def test_digital_input_validation_rejects_stale_state_before_io(
    supported, data, last_success, write_ready, error
) -> None:
    """Digital-input identity and snapshot guards run before Bluetooth lookup."""

    # Arrange - vary each prerequisite for one guarded digital action write.
    device = SimpleNamespace(
        supports_digital_input_validation=supported,
        global_settings_write_ready=write_ready,
        set_digital_input_validation=AsyncMock(),
    )
    coordinator = SimpleNamespace(
        device=device,
        data=data,
        last_update_success=last_success,
        _ble_device=Mock(),
    )

    # Act / Assert - reject without resolving or using a Bluetooth path.
    with pytest.raises(error):
        await VentaxiaMultihomeCoordinator.async_set_digital_input_validation(
            coordinator,
            digital_input_1_action=3,
            digital_input_2_action=3,
        )
    coordinator._ble_device.assert_not_called()
    device.set_digital_input_validation.assert_not_awaited()

@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("supported", "data", "last_success", "write_ready", "error"),
    [
        (False, object(), True, True, TemperatureValidationNotSupportedError),
        (True, None, True, True, TemperatureValidationUnavailableError),
        (True, object(), False, True, TemperatureValidationUnavailableError),
        (True, object(), True, False, TemperatureValidationUnavailableError),
    ],
)
async def test_temperature_validation_rejects_unsupported_or_stale_state_before_io(
    supported, data, last_success, write_ready, error
) -> None:
    """Temperature identity and snapshot guards run before Bluetooth lookup."""

    # Arrange - vary each prerequisite for the one-field validation operation.
    device = SimpleNamespace(
        supports_temperature_threshold_validation=supported,
        global_settings_write_ready=write_ready,
        set_temperature_threshold_validation=AsyncMock(),
    )
    coordinator = SimpleNamespace(
        device=device,
        data=data,
        last_update_success=last_success,
        _ble_device=Mock(),
    )

    # Act / Assert - reject without resolving or using a Bluetooth path.
    with pytest.raises(error):
        await VentaxiaMultihomeCoordinator.async_set_temperature_threshold_validation(
            coordinator,
            low_action=1,
            high_action=4,
            low_threshold=14,
            high_threshold=25,
        )
    coordinator._ble_device.assert_not_called()
    device.set_temperature_threshold_validation.assert_not_awaited()

@pytest.mark.asyncio
async def test_low_temperature_protection_publishes_confirmed_settings() -> None:
    """A successful field-16 validation publishes its exact readback."""

    # Arrange - retain telemetry and return a distinct confirmed settings object.
    current = MultihomeData(
        zone=object(),
        system=object(),
        global_settings=_settings(),
        last_successful_update=datetime.now(UTC),
    )
    confirmed = decode_global_settings(bytes(36))
    ble_device = object()
    device = SimpleNamespace(
        supports_low_temperature_protection_validation=True,
        global_settings_write_ready=True,
        set_low_temperature_protection_validation=AsyncMock(
            return_value=confirmed
        ),
        disconnect=AsyncMock(),
    )
    coordinator = SimpleNamespace(
        device=device,
        data=current,
        last_update_success=True,
        _ble_device=lambda: ble_device,
        async_set_updated_data=Mock(),
        async_set_update_error=Mock(),
    )

    # Act - apply one reviewed field-16 enable.
    await VentaxiaMultihomeCoordinator.async_set_low_temperature_protection_validation(
        coordinator, enabled=True
    )

    # Assert - only the exact confirmed settings snapshot is replaced.
    device.set_low_temperature_protection_validation.assert_awaited_once_with(
        ble_device, enabled=True
    )
    published = coordinator.async_set_updated_data.call_args.args[0]
    assert published.global_settings is confirmed
    assert published.zone is current.zone

@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("supported", "data", "last_success", "write_ready", "error"),
    [
        (
            False,
            object(),
            True,
            True,
            LowTemperatureProtectionValidationNotSupportedError,
        ),
        (
            True,
            None,
            True,
            True,
            LowTemperatureProtectionValidationUnavailableError,
        ),
        (
            True,
            object(),
            False,
            True,
            LowTemperatureProtectionValidationUnavailableError,
        ),
        (
            True,
            object(),
            True,
            False,
            LowTemperatureProtectionValidationUnavailableError,
        ),
    ],
)
async def test_low_temperature_protection_rejects_stale_state_before_io(
    supported, data, last_success, write_ready, error
) -> None:
    """Field-16 identity and snapshot guards run before Bluetooth lookup."""

    # Arrange - vary each prerequisite for the validation operation.
    device = SimpleNamespace(
        supports_low_temperature_protection_validation=supported,
        global_settings_write_ready=write_ready,
        set_low_temperature_protection_validation=AsyncMock(),
    )
    coordinator = SimpleNamespace(
        device=device,
        data=data,
        last_update_success=last_success,
        _ble_device=Mock(),
    )

    # Act / Assert - reject without resolving or using a Bluetooth path.
    with pytest.raises(error):
        update = (
            VentaxiaMultihomeCoordinator
            .async_set_low_temperature_protection_validation
        )
        await update(coordinator, enabled=True)
    coordinator._ble_device.assert_not_called()
    device.set_low_temperature_protection_validation.assert_not_awaited()

@pytest.mark.asyncio
async def test_silent_hour_publishes_only_confirmed_full_table() -> None:
    """A successful schedule update replaces only the schedule snapshot."""

    # Arrange - retain telemetry/settings and return a newly confirmed table.
    current = MultihomeData(
        zone=object(),
        system=object(),
        global_settings=_settings(),
        last_successful_update=datetime.now(UTC),
        silent_hours=_silent_hours(),
    )
    record = decode_silent_hour(encode_silent_hour(22 * 3600, 7 * 3600, 0x7F))
    confirmed = list(_silent_hours())
    confirmed[0] = decode_silent_hour_slot(bytes(4) + record.raw_record)
    confirmed = tuple(confirmed)
    ble_device = object()
    device = SimpleNamespace(
        supports_silent_hours_management=True,
        silent_hours_write_ready=True,
        set_silent_hour=AsyncMock(return_value=confirmed),
        delete_silent_hour=AsyncMock(),
        disconnect=AsyncMock(),
    )
    coordinator = SimpleNamespace(
        device=device,
        data=current,
        last_update_success=True,
        _ble_device=lambda: ble_device,
        async_set_updated_data=Mock(),
        async_set_update_error=Mock(),
    )

    # Act - apply one reviewed overnight schedule.
    await VentaxiaMultihomeCoordinator._async_mutate_silent_hour(coordinator, 0, record)

    # Assert - only exact device readback is published with other state retained.
    device.set_silent_hour.assert_awaited_once_with(ble_device, 0, record)
    published = coordinator.async_set_updated_data.call_args.args[0]
    assert published.silent_hours is confirmed
    assert published.global_settings is current.global_settings
    assert published.zone is current.zone
    coordinator.async_set_update_error.assert_not_called()

@pytest.mark.asyncio
async def test_silent_hour_delete_uses_same_guarded_publish_path() -> None:
    """Deletion publishes the complete readback returned by the device."""

    # Arrange - prepare a current table and an empty confirmed deletion result.
    current = MultihomeData(
        zone=object(),
        system=object(),
        global_settings=_settings(),
        last_successful_update=datetime.now(UTC),
        silent_hours=_silent_hours(),
    )
    confirmed = _silent_hours()
    device = SimpleNamespace(
        supports_silent_hours_management=True,
        silent_hours_write_ready=True,
        set_silent_hour=AsyncMock(),
        delete_silent_hour=AsyncMock(return_value=confirmed),
        disconnect=AsyncMock(),
    )
    coordinator = SimpleNamespace(
        device=device,
        data=current,
        last_update_success=True,
        _ble_device=lambda: object(),
        async_set_updated_data=Mock(),
        async_set_update_error=Mock(),
    )

    # Act - delete slot five.
    await VentaxiaMultihomeCoordinator._async_mutate_silent_hour(coordinator, 5, None)

    # Assert - deletion and publication occur exactly once.
    device.delete_silent_hour.assert_awaited_once()
    published = coordinator.async_set_updated_data.call_args.args[0]
    assert published.silent_hours is confirmed

@pytest.mark.asyncio
async def test_silent_hours_rejects_unsupported_or_unavailable_before_ble() -> None:
    """Capability and current-state gates prevent unsafe schedule writes."""

    # Arrange - create one unsupported and one stale coordinator facade.
    record = decode_silent_hour(encode_silent_hour(3600, 7200, 1))
    unsupported_device = SimpleNamespace(
        supports_silent_hours_management=False,
        silent_hours_write_ready=True,
        set_silent_hour=AsyncMock(),
    )
    unsupported = SimpleNamespace(
        device=unsupported_device,
        data=SimpleNamespace(silent_hours=_silent_hours()),
        last_update_success=True,
        _ble_device=Mock(),
    )
    stale_device = SimpleNamespace(
        supports_silent_hours_management=True,
        silent_hours_write_ready=False,
        set_silent_hour=AsyncMock(),
    )
    stale = SimpleNamespace(
        device=stale_device,
        data=SimpleNamespace(silent_hours=_silent_hours()),
        last_update_success=True,
        _ble_device=Mock(),
    )

    # Act - attempt one mutation through each unsafe coordinator state.
    with pytest.raises(SilentHoursNotSupportedError) as unsupported_error:
        await VentaxiaMultihomeCoordinator._async_mutate_silent_hour(
            unsupported, 0, record
        )
    with pytest.raises(SilentHoursConfigurationUnavailableError) as unavailable_error:
        await VentaxiaMultihomeCoordinator._async_mutate_silent_hour(stale, 0, record)

    # Assert - both gates fail before a Bluetooth route or packet write.
    assert unsupported_error.value
    assert unavailable_error.value
    unsupported._ble_device.assert_not_called()
    stale._ble_device.assert_not_called()

@pytest.mark.asyncio
async def test_airflow_profile_maps_device_snapshot_loss_to_unavailable() -> None:
    """A snapshot invalidated inside the serialized operation is not success."""

    # Arrange - pass the coordinator gate, then lose the device snapshot at write time.
    device = SimpleNamespace(
        supports_global_airflow_configuration=True,
        global_settings_write_ready=True,
        set_airflow_profile=AsyncMock(
            side_effect=GlobalSettingsUnavailableError("snapshot changed")
        ),
        disconnect=AsyncMock(),
    )
    coordinator = SimpleNamespace(
        device=device,
        data=object(),
        last_update_success=True,
        _ble_device=lambda: object(),
        async_set_updated_data=Mock(),
        async_set_update_error=Mock(),
    )

    # Act / Assert - HA reports unavailable without publishing false settings.
    with pytest.raises(AirflowConfigurationUnavailableError, match="snapshot"):
        await VentaxiaMultihomeCoordinator.async_set_airflow_profile(
            coordinator, low=7, normal=8, boost=37, purge=50
        )
    coordinator.async_set_updated_data.assert_not_called()
    coordinator.async_set_update_error.assert_not_called()
    device.disconnect.assert_not_awaited()

@pytest.mark.asyncio
async def test_calibration_uses_validated_device_and_reference(monkeypatch) -> None:
    """A valid calibration command reaches the device exactly once."""

    # Arrange - expose one validated model and stable monotonic time.
    ble_device = object()
    device = SimpleNamespace(
        supports_internal_co2_calibration=True,
        calibrate_internal_co2=AsyncMock(),
        disconnect=AsyncMock(),
    )
    coordinator = SimpleNamespace(
        device=device,
        _last_calibration_attempt=None,
        _record_calibration_attempt=lambda value: setattr(
            coordinator, "_last_calibration_attempt", value
        ),
        _ble_device=lambda: ble_device,
        _hard_reset_recovery_mode=False,
    )
    monkeypatch.setattr(coordinator_module, "time", lambda: 100.0)

    # Act - start a fresh-air reference calibration.
    await VentaxiaMultihomeCoordinator.async_calibrate_internal_co2(coordinator, 400)

    # Assert - the coordinator records the attempt and delegates once.
    assert coordinator._last_calibration_attempt == 100.0
    device.calibrate_internal_co2.assert_awaited_once_with(ble_device, 400)
    device.disconnect.assert_not_awaited()

@pytest.mark.asyncio
async def test_calibration_is_rate_limited_before_bluetooth(monkeypatch) -> None:
    """Repeated calibration attempts cannot hammer or restart the sensor."""

    # Arrange - retain an attempt from ten seconds ago.
    device = SimpleNamespace(
        supports_internal_co2_calibration=True,
        calibrate_internal_co2=AsyncMock(),
    )
    coordinator = SimpleNamespace(
        device=device,
        _last_calibration_attempt=100.0,
        _record_calibration_attempt=lambda value: setattr(
            coordinator, "_last_calibration_attempt", value
        ),
        _ble_device=lambda: object(),
    )
    monkeypatch.setattr(coordinator_module, "time", lambda: 110.0)

    # Act / Assert - the cooldown is reported without any device call.
    with pytest.raises(CalibrationRateLimitedError, match="290 seconds"):
        await VentaxiaMultihomeCoordinator.async_calibrate_internal_co2(
            coordinator, 400
        )
    device.calibrate_internal_co2.assert_not_awaited()

@pytest.mark.asyncio
async def test_calibration_rejects_unvalidated_model_before_bluetooth() -> None:
    """The coordinator does not guess an internal sensor target."""

    # Arrange - expose a model outside the recovered internal-CO2 map.
    device = SimpleNamespace(
        supports_internal_co2_calibration=False,
        calibrate_internal_co2=AsyncMock(),
    )
    coordinator = SimpleNamespace(
        device=device,
        _last_calibration_attempt=None,
        _record_calibration_attempt=lambda value: setattr(
            coordinator, "_last_calibration_attempt", value
        ),
        _ble_device=lambda: object(),
    )

    # Act / Assert - capability validation happens before Bluetooth I/O.
    with pytest.raises(CalibrationNotSupportedError, match="not validated"):
        await VentaxiaMultihomeCoordinator.async_calibrate_internal_co2(
            coordinator, 400
        )
    device.calibrate_internal_co2.assert_not_awaited()

@pytest.mark.asyncio
async def test_uncertain_calibration_delivery_retains_cooldown(monkeypatch) -> None:
    """An attempted write remains guarded when delivery cannot be confirmed."""

    # Arrange - fail after the calibration write has entered its transport.
    device = SimpleNamespace(
        supports_internal_co2_calibration=True,
        calibrate_internal_co2=AsyncMock(
            side_effect=CalibrationWriteUncertainError("timed out")
        ),
        disconnect=AsyncMock(),
    )
    coordinator = SimpleNamespace(
        device=device,
        _last_calibration_attempt=None,
        _record_calibration_attempt=lambda value: setattr(
            coordinator, "_last_calibration_attempt", value
        ),
        _ble_device=lambda: object(),
    )
    monkeypatch.setattr(coordinator_module, "time", lambda: 100.0)

    # Act - send the command and receive an uncertain transport outcome.
    with pytest.raises(CalibrationDeliveryUncertainError, match="may have reached"):
        await VentaxiaMultihomeCoordinator.async_calibrate_internal_co2(
            coordinator, 400
        )

    # Assert - no success is returned, stale BLE state is cleared, and an
    # immediate retry remains blocked in case the firmware received the write.
    assert coordinator._last_calibration_attempt == 100.0
    device.disconnect.assert_awaited_once()
    device.calibrate_internal_co2.assert_awaited_once()

@pytest.mark.asyncio
async def test_prewrite_calibration_failure_does_not_start_cooldown(
    monkeypatch,
) -> None:
    """Target discovery failures can be retried without a false lockout."""

    # Arrange - fail while reading routes, before a calibration packet exists.
    device = SimpleNamespace(
        supports_internal_co2_calibration=True,
        calibrate_internal_co2=AsyncMock(
            side_effect=CalibrationTargetDiscoveryError("no internal target")
        ),
        disconnect=AsyncMock(),
    )
    coordinator = SimpleNamespace(
        device=device,
        _last_calibration_attempt=None,
        _record_calibration_attempt=lambda value: setattr(
            coordinator, "_last_calibration_attempt", value
        ),
        _ble_device=lambda: object(),
    )
    monkeypatch.setattr(coordinator_module, "time", lambda: 100.0)

    # Act - attempt calibration while route discovery is unavailable.
    with pytest.raises(CalibrationCommandNotSentError, match="not sent"):
        await VentaxiaMultihomeCoordinator.async_calibrate_internal_co2(
            coordinator, 400
        )

    # Assert - no cooldown is created because the write was never attempted.
    assert coordinator._last_calibration_attempt is None
    assert coordinator.last_calibration_outcome == "not_sent"
    device.disconnect.assert_awaited_once()

@pytest.mark.asyncio
async def test_polling_recovers_after_failed_calibration(monkeypatch) -> None:
    """A calibration transport failure does not poison the next coordinator poll."""

    # Arrange - fail calibration once, then make ordinary polling return data.
    ble_device = object()
    fresh_data = object()
    device = SimpleNamespace(
        supports_internal_co2_calibration=True,
        calibrate_internal_co2=AsyncMock(
            side_effect=CalibrationWriteUncertainError("timed out")
        ),
        disconnect=AsyncMock(),
        update=AsyncMock(return_value=fresh_data),
    )
    coordinator = SimpleNamespace(
        device=device,
        _last_calibration_attempt=None,
        _record_calibration_attempt=lambda value: setattr(
            coordinator, "_last_calibration_attempt", value
        ),
        _ble_device=lambda: ble_device,
        _hard_reset_recovery_mode=False,
    )
    monkeypatch.setattr(coordinator_module, "time", lambda: 100.0)

    # Act - observe the guarded write failure, then run the next normal refresh.
    with pytest.raises(HomeAssistantError):
        await VentaxiaMultihomeCoordinator.async_calibrate_internal_co2(
            coordinator, 400
        )
    result = await VentaxiaMultihomeCoordinator._async_update_data(coordinator)

    # Assert - stale connection state was cleared and telemetry polling resumed.
    device.disconnect.assert_awaited_once()
    device.update.assert_awaited_once_with(ble_device)
    assert result is fresh_data

@pytest.mark.asyncio
async def test_polling_continues_after_successful_calibration(monkeypatch) -> None:
    """A completed send leaves the next scheduled telemetry read usable."""

    # Arrange - complete calibration and expose a following telemetry snapshot.
    ble_device = object()
    fresh_data = object()
    device = SimpleNamespace(
        supports_internal_co2_calibration=True,
        calibrate_internal_co2=AsyncMock(),
        disconnect=AsyncMock(),
        update=AsyncMock(return_value=fresh_data),
    )
    coordinator = SimpleNamespace(
        device=device,
        _last_calibration_attempt=None,
        _record_calibration_attempt=lambda value: setattr(
            coordinator, "_last_calibration_attempt", value
        ),
        _ble_device=lambda: ble_device,
        _hard_reset_recovery_mode=False,
    )
    monkeypatch.setattr(coordinator_module, "time", lambda: 100.0)

    # Act - send calibration, then run the next ordinary refresh.
    await VentaxiaMultihomeCoordinator.async_calibrate_internal_co2(coordinator, 400)
    result = await VentaxiaMultihomeCoordinator._async_update_data(coordinator)

    # Assert - no forced disconnect occurred and polling returned normally.
    device.disconnect.assert_not_awaited()
    device.update.assert_awaited_once_with(ble_device)
    assert result is fresh_data


def test_calibration_cooldown_is_persisted_and_restored() -> None:
    """A reload or restart cannot bypass the five-minute safety guard."""

    # Arrange - create the persistence-facing coordinator subset and entry.
    update_entry = Mock()
    entry = SimpleNamespace(data={CONF_ADDRESS: "AA:BB"})
    coordinator = object.__new__(VentaxiaMultihomeCoordinator)
    coordinator.hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_update_entry=update_entry)
    )
    coordinator.config_entry = entry
    coordinator._last_calibration_attempt = None

    # Act - record an attempt, then restore it as a newly loaded coordinator would.
    coordinator._record_calibration_attempt(1_787_910_400.0)
    persisted_data = update_entry.call_args.kwargs["data"]
    restored = VentaxiaMultihomeCoordinator._stored_calibration_attempt(
        SimpleNamespace(data=persisted_data)
    )

    # Assert - the absolute attempt time survives the in-memory coordinator.
    assert coordinator._last_calibration_attempt == 1_787_910_400.0
    assert persisted_data[CONF_LAST_CO2_CALIBRATION_ATTEMPT] == 1_787_910_400.0
    assert restored == 1_787_910_400.0


@pytest.mark.parametrize("stored_value", [None, True, -1, float("nan"), "now"])
def test_invalid_persisted_calibration_attempt_is_ignored(stored_value) -> None:
    """Corrupt or legacy config data cannot create an invalid cooldown."""

    # Arrange - load a config entry containing an invalid internal timestamp.
    entry = SimpleNamespace(data={CONF_LAST_CO2_CALIBRATION_ATTEMPT: stored_value})

    # Act - parse the optional persisted calibration-attempt value.
    restored = VentaxiaMultihomeCoordinator._stored_calibration_attempt(entry)

    # Assert - invalid values are treated as if no prior attempt was stored.
    assert restored is None
