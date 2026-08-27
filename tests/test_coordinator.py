"""Coordinator Bluetooth-path retention tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from bleak.exc import BleakError
from homeassistant.const import CONF_ADDRESS
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.ventaxia_multihome import coordinator as coordinator_module
from custom_components.ventaxia_multihome.bluetooth import TransactionTimeoutError
from custom_components.ventaxia_multihome.coordinator import (
    CalibrationNotSupportedError,
    CalibrationRateLimitedError,
    VentaxiaMultihomeCoordinator,
)
from custom_components.ventaxia_multihome.protocol import (
    AirflowPreset,
    ProtocolError,
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
        _ble_device=lambda: ble_device,
    )
    monkeypatch.setattr(coordinator_module, "monotonic", lambda: 100.0)

    # Act - start a fresh-air reference calibration.
    await VentaxiaMultihomeCoordinator.async_calibrate_internal_co2(
        coordinator, 400
    )

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
        _ble_device=lambda: object(),
    )
    monkeypatch.setattr(coordinator_module, "monotonic", lambda: 110.0)

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
        _ble_device=lambda: object(),
    )

    # Act / Assert - capability validation happens before Bluetooth I/O.
    with pytest.raises(CalibrationNotSupportedError, match="not validated"):
        await VentaxiaMultihomeCoordinator.async_calibrate_internal_co2(
            coordinator, 400
        )
    device.calibrate_internal_co2.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        TransactionTimeoutError("timed out"),
        BleakError("disconnected"),
        ProtocolError("malformed acknowledgement"),
    ],
)
async def test_failed_calibration_disconnects_without_false_success(
    monkeypatch, error
) -> None:
    """Transport failures clear the connection and retain the cooldown."""

    # Arrange - fail one validated calibration transport operation.
    device = SimpleNamespace(
        supports_internal_co2_calibration=True,
        calibrate_internal_co2=AsyncMock(side_effect=error),
        disconnect=AsyncMock(),
    )
    coordinator = SimpleNamespace(
        device=device,
        _last_calibration_attempt=None,
        _ble_device=lambda: object(),
    )
    monkeypatch.setattr(coordinator_module, "monotonic", lambda: 100.0)

    # Act - send the command and receive an uncertain transport outcome.
    with pytest.raises(HomeAssistantError, match="Unable to start"):
        await VentaxiaMultihomeCoordinator.async_calibrate_internal_co2(
            coordinator, 400
        )

    # Assert - no success is returned, stale BLE state is cleared, and an
    # immediate retry remains blocked in case the firmware received the write.
    assert coordinator._last_calibration_attempt == 100.0
    device.disconnect.assert_awaited_once()
    device.calibrate_internal_co2.assert_awaited_once()


@pytest.mark.asyncio
async def test_polling_recovers_after_failed_calibration(monkeypatch) -> None:
    """A calibration transport failure does not poison the next coordinator poll."""

    # Arrange - fail calibration once, then make ordinary polling return data.
    ble_device = object()
    fresh_data = object()
    device = SimpleNamespace(
        supports_internal_co2_calibration=True,
        calibrate_internal_co2=AsyncMock(
            side_effect=TransactionTimeoutError("timed out")
        ),
        disconnect=AsyncMock(),
        update=AsyncMock(return_value=fresh_data),
    )
    coordinator = SimpleNamespace(
        device=device,
        _last_calibration_attempt=None,
        _ble_device=lambda: ble_device,
    )
    monkeypatch.setattr(coordinator_module, "monotonic", lambda: 100.0)

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
        _ble_device=lambda: ble_device,
    )
    monkeypatch.setattr(coordinator_module, "monotonic", lambda: 100.0)

    # Act - send calibration, then run the next ordinary refresh.
    await VentaxiaMultihomeCoordinator.async_calibrate_internal_co2(
        coordinator, 400
    )
    result = await VentaxiaMultihomeCoordinator._async_update_data(coordinator)

    # Assert - no forced disconnect occurred and polling returned normally.
    device.disconnect.assert_not_awaited()
    device.update.assert_awaited_once_with(ble_device)
    assert result is fresh_data
