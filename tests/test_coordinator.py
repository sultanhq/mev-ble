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
