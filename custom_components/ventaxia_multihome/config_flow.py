"""Config flow for Vent-Axia Multihome."""

from __future__ import annotations

import logging
from typing import Any, override

import voluptuous as vol
from bleak.exc import BleakError
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback
from homeassistant.helpers import selector

from .bluetooth import TransportError, async_establish_connection
from .const import (
    CONF_OVERRIDE_DURATION,
    CONF_SETUP_CODE,
    DEFAULT_OVERRIDE_DURATION,
    DOMAIN,
    MAX_OVERRIDE_DURATION,
    MIN_OVERRIDE_DURATION,
    NAME,
    SUPPORTED_LOCAL_NAMES,
)
from .device import (
    DeviceError,
    MissingCharacteristicError,
    MultihomeDevice,
    MultihomeDeviceInfo,
    SetupCodeRejectedError,
)
from .entity import format_identifier
from .protocol import ProtocolError

_LOGGER = logging.getLogger(__name__)


def is_supported_name(name: str | None) -> bool:
    """Return whether a local name is one of the documented names."""

    return bool(name and name.casefold() in SUPPORTED_LOCAL_NAMES)


class VentaxiaMultihomeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle discovery, automatic pairing, and reauthentication."""

    VERSION = 1

    def __init__(self) -> None:
        self._address: str | None = None
        self._name = NAME
        self._discovered: dict[str, BluetoothServiceInfoBleak] = {}

    @staticmethod
    @callback
    @override
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""

        return VentaxiaMultihomeOptionsFlow()

    @override
    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle automatic Bluetooth discovery."""

        _LOGGER.debug(
            "Discovered Bluetooth device %s at %s",
            discovery_info.name,
            discovery_info.address,
        )
        if not is_supported_name(discovery_info.name):
            return self.async_abort(reason="not_supported")
        await self.async_set_unique_id(format_identifier(discovery_info.address))
        self._abort_if_unique_id_configured()
        self._set_discovery(discovery_info)
        self.context["title_placeholders"] = {
            "name": self._name,
            "address": discovery_info.address,
        }
        return await self.async_step_pair()

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Explain pairing mode, then find a documented device."""

        if user_input is None:
            return self._show_pairing_instructions()

        await bluetooth.async_request_active_scan(self.hass)
        configured = self._async_current_ids(include_ignore=False)
        self._discovered = {
            info.address: info
            for info in bluetooth.async_discovered_service_info(
                self.hass, connectable=True
            )
            if is_supported_name(info.name)
            and format_identifier(info.address) not in configured
        }
        if not self._discovered:
            return self._show_pairing_instructions(
                errors={"base": "no_devices_found"}
            )
        if len(self._discovered) == 1:
            self._set_discovery(next(iter(self._discovered.values())))
            await self.async_set_unique_id(format_identifier(self._address or ""))
            self._abort_if_unique_id_configured()
            return await self.async_step_pair({})
        return await self.async_step_select_device()

    async def async_step_select_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user choose a discovered documented device."""

        if user_input is None:
            return self.async_show_form(
                step_id="select_device",
                data_schema=vol.Schema(
                    {
                        vol.Required(CONF_ADDRESS): vol.In(
                            {
                                address: f"{info.name} ({address})"
                                for address, info in self._discovered.items()
                            }
                        )
                    }
                ),
            )

        self._set_discovery(self._discovered[user_input[CONF_ADDRESS]])
        assert self._address is not None
        await self.async_set_unique_id(format_identifier(self._address))
        self._abort_if_unique_id_configured()
        return await self.async_step_pair({})

    async def async_step_pair(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Retrieve and store the unit-generated application setup code."""

        errors: dict[str, str] = {}
        assert self._address is not None
        if user_input is not None:
            try:
                setup_code, info = await self._async_pair()
            except SetupCodeRejectedError:
                errors["base"] = "pairing_failed"
            except MissingCharacteristicError:
                errors["base"] = "not_supported"
            except DeviceUnavailableError:
                errors["base"] = "device_unavailable"
            except (
                BleakError,
                TransportError,
                DeviceError,
                ProtocolError,
                TimeoutError,
            ):
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error validating Multihome setup")
                errors["base"] = "unknown"
            else:
                suffix = format_identifier(self._address)[-4:].upper()
                title = f"{NAME} {suffix}"
                if info.model:
                    title = f"Vent-Axia {info.model} {suffix}"
                return self.async_create_entry(
                    title=title,
                    data={
                        CONF_ADDRESS: self._address,
                        CONF_SETUP_CODE: setup_code,
                    },
                    options={
                        CONF_OVERRIDE_DURATION: DEFAULT_OVERRIDE_DURATION,
                    },
                )

        return self.async_show_form(
            step_id="pair",
            data_schema=vol.Schema({}),
            errors=errors,
            description_placeholders={"name": self._name},
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Start automatic setup-code reauthentication."""

        self._address = entry_data[CONF_ADDRESS]
        self._name = self._get_reauth_entry().title
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Retrieve a replacement setup code from pairing mode."""

        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                setup_code, _info = await self._async_pair()
            except SetupCodeRejectedError:
                errors["base"] = "pairing_failed"
            except DeviceUnavailableError:
                errors["base"] = "device_unavailable"
            except (
                BleakError,
                TransportError,
                DeviceError,
                ProtocolError,
                TimeoutError,
            ):
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error reauthenticating Multihome")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(),
                    data_updates={CONF_SETUP_CODE: setup_code},
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({}),
            errors=errors,
        )

    def _show_pairing_instructions(
        self, errors: dict[str, str] | None = None
    ) -> ConfigFlowResult:
        """Show the physical pairing steps before scanning."""

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({}),
            errors=errors or {},
        )

    def _set_discovery(self, info: BluetoothServiceInfoBleak) -> None:
        self._address = info.address
        self._name = info.name or NAME

    async def _async_pair(self) -> tuple[int, MultihomeDeviceInfo]:
        """Pair through HA Bluetooth without logging the generated code."""

        assert self._address is not None
        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, self._address, connectable=True
        )
        if ble_device is None:
            raise DeviceUnavailableError
        device = MultihomeDevice(
            self._address,
            self._name,
            0,
            client_factory=async_establish_connection,
        )
        try:
            setup_code = await device.pair(ble_device)
            return setup_code, device.device_info
        finally:
            await device.disconnect()


class DeviceUnavailableError(Exception):
    """Raised when no connectable HA Bluetooth path can reach the unit."""


class VentaxiaMultihomeOptionsFlow(OptionsFlow):
    """Configure the default duration used by standard fan preset calls."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage integration options."""

        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_OVERRIDE_DURATION,
                        default=self.config_entry.options.get(
                            CONF_OVERRIDE_DURATION, DEFAULT_OVERRIDE_DURATION
                        ),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=MIN_OVERRIDE_DURATION,
                            max=MAX_OVERRIDE_DURATION,
                            step=1,
                            mode=selector.NumberSelectorMode.BOX,
                            unit_of_measurement="s",
                        )
                    )
                }
            ),
        )
