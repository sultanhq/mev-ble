"""Home Assistant Bluetooth discovery and config-flow tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant import config_entries, data_entry_flow

from custom_components.ventaxia_multihome.const import CONF_SETUP_CODE, DOMAIN
from custom_components.ventaxia_multihome.device import SetupCodeRejectedError


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading this repository's custom integration."""


def _discovery(name: str = "mEv") -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        address="AA:BB:CC:DD:EE:FF",
        connectable=True,
        device=object(),
    )


@pytest.mark.asyncio
async def test_user_flow_shows_pairing_instructions_before_scanning(hass) -> None:
    """Manual setup explains physical pairing before looking for devices."""

    # Arrange - track whether Home Assistant starts an active Bluetooth scan.
    with patch(
        "custom_components.ventaxia_multihome.config_flow.bluetooth.async_request_active_scan",
        new=AsyncMock(),
    ) as request_active_scan:
        # Act - open the integration from Add integration.
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
        )

    # Assert - the first screen shows instructions without starting the scan early.
    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "user"
    request_active_scan.assert_not_awaited()


@pytest.mark.asyncio
async def test_user_flow_retries_when_no_device_is_found(hass) -> None:
    """The pairing instructions remain available when a scan finds nothing."""

    # Arrange - expose no unconfigured supported devices to the Bluetooth scan.
    with (
        patch(
            "custom_components.ventaxia_multihome.config_flow.bluetooth.async_request_active_scan",
            new=AsyncMock(),
        ) as request_active_scan,
        patch(
            "custom_components.ventaxia_multihome.config_flow.bluetooth.async_discovered_service_info",
            return_value=[],
        ),
    ):
        initial = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
        )

        # Act - submit the pairing instructions screen to scan.
        result = await hass.config_entries.flow.async_configure(
            initial["flow_id"], {}
        )

    # Assert - setup stays open with a useful retry error.
    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "no_devices_found"}
    request_active_scan.assert_awaited_once()


@pytest.mark.asyncio
async def test_user_flow_pairs_after_finding_one_device(hass) -> None:
    """A successful manual scan retrieves the generated code and creates an entry."""

    # Arrange - expose one supported device and a successful automatic pairing.
    with (
        patch(
            "custom_components.ventaxia_multihome.config_flow.bluetooth.async_request_active_scan",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.ventaxia_multihome.config_flow.bluetooth.async_discovered_service_info",
            return_value=[_discovery("Multihome")],
        ),
        patch(
            "custom_components.ventaxia_multihome.config_flow.bluetooth.async_ble_device_from_address",
            return_value=object(),
        ),
        patch(
            "custom_components.ventaxia_multihome.config_flow.MultihomeDevice.pair",
            new=AsyncMock(return_value=123456),
        ),
        patch(
            "custom_components.ventaxia_multihome.config_flow.MultihomeDevice.disconnect",
            new=AsyncMock(),
        ),
    ):
        initial = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
        )

        # Act - submit the pairing instructions screen to scan.
        result = await hass.config_entries.flow.async_configure(
            initial["flow_id"], {}
        )

    # Assert - pairing stores the code without asking the user to type it.
    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SETUP_CODE] == 123456


@pytest.mark.asyncio
async def test_bluetooth_discovery_and_setup(hass) -> None:
    """Bluetooth discovery shows pairing help before automatic setup."""

    # Arrange - expose a connectable HA-managed BLEDevice and generated code.
    with (
        patch(
            "custom_components.ventaxia_multihome.config_flow.bluetooth.async_ble_device_from_address",
            return_value=object(),
        ),
        patch(
            "custom_components.ventaxia_multihome.config_flow.MultihomeDevice.pair",
            new=AsyncMock(return_value=123456),
        ),
        patch(
            "custom_components.ventaxia_multihome.config_flow.MultihomeDevice.disconnect",
            new=AsyncMock(),
        ),
    ):
        # Act - start from Bluetooth discovery, then submit the pairing screen.
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_BLUETOOTH},
            data=_discovery(),
        )
        configured = await hass.config_entries.flow.async_configure(
            result["flow_id"], {}
        )

    # Assert - discovery explains pairing and stores the generated code.
    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "pair"
    assert configured["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert configured["data"][CONF_SETUP_CODE] == 123456


@pytest.mark.asyncio
async def test_automatic_pairing_failure(hass) -> None:
    """A unit outside pairing mode returns a useful retry form."""

    # Arrange - make automatic pairing fail to retrieve a generated code.
    with (
        patch(
            "custom_components.ventaxia_multihome.config_flow.bluetooth.async_ble_device_from_address",
            return_value=object(),
        ),
        patch(
            "custom_components.ventaxia_multihome.config_flow.MultihomeDevice.pair",
            new=AsyncMock(side_effect=SetupCodeRejectedError),
        ),
        patch(
            "custom_components.ventaxia_multihome.config_flow.MultihomeDevice.disconnect",
            new=AsyncMock(),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_BLUETOOTH},
            data=_discovery("Multihome"),
        )

        # Act - submit the physical pairing instructions.
        rejected = await hass.config_entries.flow.async_configure(
            result["flow_id"], {}
        )

    # Assert - the flow stays open with a specific retry error.
    assert rejected["type"] is data_entry_flow.FlowResultType.FORM
    assert rejected["step_id"] == "pair"
    assert rejected["errors"] == {"base": "pairing_failed"}


@pytest.mark.asyncio
async def test_unsupported_discovery_name(hass) -> None:
    """Multivent is not accepted without hardware validation."""

    # Arrange / Act - route an unsupported name into the discovery step.
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_BLUETOOTH},
        data=_discovery("Multivent"),
    )

    # Assert - the flow aborts as unsupported.
    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "not_supported"
