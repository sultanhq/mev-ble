"""Coordinator Bluetooth-path retention tests."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from bleak.exc import BleakError
from homeassistant.components.bluetooth import BluetoothScanningMode
from homeassistant.const import CONF_ADDRESS
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.ventaxia_multihome import coordinator as coordinator_module
from custom_components.ventaxia_multihome.bluetooth import TransactionTimeoutError
from custom_components.ventaxia_multihome.const import (
    CONF_LAST_CO2_CALIBRATION_ATTEMPT,
    STARTUP_ADVERTISEMENT_TIMEOUT,
)
from custom_components.ventaxia_multihome.coordinator import (
    AirflowConfigurationNotSupportedError,
    AirflowConfigurationUnavailableError,
    CalibrationCommandNotSentError,
    CalibrationDeliveryUncertainError,
    CalibrationNotSupportedError,
    CalibrationRateLimitedError,
    ComfortModeConfigurationNotSupportedError,
    ComfortModeConfigurationUnavailableError,
    HumidityResponseConfigurationNotSupportedError,
    HumidityResponseConfigurationUnavailableError,
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
    GlobalSettingsUnavailableError,
    MultihomeData,
)
from custom_components.ventaxia_multihome.protocol import (
    AirflowPreset,
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
