"""Coordinator Bluetooth-path retention tests."""

from __future__ import annotations

from types import SimpleNamespace

from homeassistant.const import CONF_ADDRESS

from custom_components.ventaxia_multihome import coordinator as coordinator_module
from custom_components.ventaxia_multihome.coordinator import (
    VentaxiaMultihomeCoordinator,
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
