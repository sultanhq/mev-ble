"""Integration setup tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import custom_components.ventaxia_multihome as integration
from custom_components.ventaxia_multihome.const import CONF_SETUP_CODE


@pytest.mark.asyncio
async def test_setup_waits_for_bluetooth_before_first_refresh(monkeypatch) -> None:
    """The first coordinator refresh starts only after Bluetooth is ready."""

    # Arrange - record the startup order around the coordinator's readiness gate.
    calls: list[str] = []
    device = object()
    wait_for_bluetooth = AsyncMock(side_effect=lambda: calls.append("bluetooth"))
    first_refresh = AsyncMock(side_effect=lambda: calls.append("refresh"))
    coordinator = SimpleNamespace(
        async_wait_for_initial_bluetooth=wait_for_bluetooth,
        async_config_entry_first_refresh=first_refresh,
    )
    coordinator_factory = Mock(return_value=coordinator)
    device_factory = Mock(return_value=device)
    forward_setups = AsyncMock()
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(
            async_forward_entry_setups=forward_setups
        )
    )
    entry = SimpleNamespace(
        data={"address": "AA:BB", CONF_SETUP_CODE: 1234},
        title="Vent-Axia Multihome 78D0",
        runtime_data=None,
    )
    monkeypatch.setattr(integration, "MultihomeDevice", device_factory)
    monkeypatch.setattr(
        integration, "VentaxiaMultihomeCoordinator", coordinator_factory
    )

    # Act - set up the saved config entry.
    result = await integration.async_setup_entry(hass, entry)

    # Assert - Bluetooth readiness gates the refresh and platform forwarding.
    assert result is True
    assert calls == ["bluetooth", "refresh"]
    assert entry.runtime_data is coordinator
    forward_setups.assert_awaited_once_with(entry, integration.PLATFORMS)
