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
async def test_user_flow_continues_after_finding_one_device(hass) -> None:
    """A successful manual scan advances to setup-code entry."""

    # Arrange - expose one supported device after the pairing instructions.
    with (
        patch(
            "custom_components.ventaxia_multihome.config_flow.bluetooth.async_request_active_scan",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.ventaxia_multihome.config_flow.bluetooth.async_discovered_service_info",
            return_value=[_discovery("Multihome")],
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

    # Assert - the discovered unit is ready for its application setup code.
    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "setup_code"


@pytest.mark.asyncio
async def test_bluetooth_discovery_and_setup(hass) -> None:
    """Case-insensitive documented names lead to setup-code entry creation."""

    # Arrange - expose a connectable HA-managed BLEDevice and successful auth.
    with (
        patch(
            "custom_components.ventaxia_multihome.config_flow.bluetooth.async_ble_device_from_address",
            return_value=object(),
        ),
        patch(
            "custom_components.ventaxia_multihome.config_flow.MultihomeDevice.connect",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.ventaxia_multihome.config_flow.MultihomeDevice.disconnect",
            new=AsyncMock(),
        ),
    ):
        # Act - start from Bluetooth discovery, then submit the setup code.
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_BLUETOOTH},
            data=_discovery(),
        )
        configured = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_SETUP_CODE: 123456}
        )

    # Assert - discovery prompts for the code and creates a config entry.
    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "setup_code"
    assert configured["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert configured["data"][CONF_SETUP_CODE] == 123456


@pytest.mark.asyncio
async def test_setup_code_rejection(hass) -> None:
    """A rejected code returns a useful form error instead of creating an entry."""

    # Arrange - make authentication return the distinguishable rejection.
    with (
        patch(
            "custom_components.ventaxia_multihome.config_flow.bluetooth.async_ble_device_from_address",
            return_value=object(),
        ),
        patch(
            "custom_components.ventaxia_multihome.config_flow.MultihomeDevice.connect",
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

        # Act - submit a rejected setup code.
        rejected = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_SETUP_CODE: 1111}
        )

    # Assert - the flow stays open with a specific non-sensitive error.
    assert rejected["type"] is data_entry_flow.FlowResultType.FORM
    assert rejected["errors"] == {"base": "setup_code_rejected"}


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
