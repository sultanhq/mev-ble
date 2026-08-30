"""Config flow for Vent-Axia Multihome."""

from __future__ import annotations

import asyncio
import logging
import math
from statistics import fmean
from typing import Any, override

import voluptuous as vol
from bleak.exc import BleakError
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_UNIT_OF_MEASUREMENT,
    CONCENTRATION_PARTS_PER_MILLION,
    CONF_ADDRESS,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from .bluetooth import TransportError, async_establish_connection
from .const import (
    CO2_CALIBRATION_PROGRESS_INTERVAL,
    CO2_CALIBRATION_SAMPLING_DURATION,
    CONF_OVERRIDE_DURATION,
    CONF_SETUP_CODE,
    DEFAULT_OVERRIDE_DURATION,
    DOMAIN,
    MAX_OVERRIDE_DURATION,
    MIN_OVERRIDE_DURATION,
    NAME,
    SUPPORTED_LOCAL_NAMES,
)
from .coordinator import (
    CalibrationCommandNotSentError,
    CalibrationDeliveryUncertainError,
    CalibrationNotSupportedError,
    CalibrationRateLimitedError,
)
from .device import (
    DeviceError,
    MissingCharacteristicError,
    MultihomeDevice,
    MultihomeDeviceInfo,
    SetupCodeRejectedError,
)
from .entity import format_identifier
from .protocol import (
    DEFAULT_CO2_CALIBRATION_REFERENCE,
    MAX_CO2_CALIBRATION_REFERENCE,
    MIN_CO2_CALIBRATION_REFERENCE,
    ProtocolError,
)

_LOGGER = logging.getLogger(__name__)

CONF_CALIBRATION_METHOD = "calibration_method"
CONF_REFERENCE_PPM = "reference_ppm"
CONF_REFERENCE_SENSORS = "reference_sensors"
CONF_CONFIRM_CALIBRATION = "confirm_calibration"

CALIBRATION_METHOD_FRESH_AIR = "fresh_air"
CALIBRATION_METHOD_REFERENCE_SENSORS = "reference_sensors"


class CalibrationReferenceError(ValueError):
    """Raised when a selected Home Assistant reference is unsafe to use."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


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
        """Read and store the code exposed by the unit in pairing mode."""

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
        """Retrieve a replacement code from physical pairing mode."""

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
        """Pair through HA Bluetooth without exposing the internal code."""

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
    """Configure fan defaults and run guarded CO2 calibration."""

    def __init__(self) -> None:
        self._calibration_method: str | None = None
        self._reference_entity_ids: list[str] = []
        self._reference_ppm: int | None = None
        self._reference_summary = ""
        self._calibration_progress_task: asyncio.Task[None] | None = None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the integration options menu."""

        menu_options = ["fan_options"]
        if self.config_entry.runtime_data.device.supports_internal_co2_calibration:
            menu_options.append("calibrate_co2")
        return self.async_show_menu(
            step_id="init",
            menu_options=menu_options,
        )

    async def async_step_fan_options(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure the default duration used by fan preset calls."""

        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(
            step_id="fan_options",
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

    async def async_step_calibrate_co2(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the guarded internal CO2 calibration method."""

        if not self.config_entry.runtime_data.device.supports_internal_co2_calibration:
            return self.async_abort(reason="calibration_not_supported")

        if user_input is not None:
            self._calibration_method = user_input[CONF_CALIBRATION_METHOD]
            if self._calibration_method == CALIBRATION_METHOD_FRESH_AIR:
                return await self.async_step_calibration_exposure()
            return await self.async_step_calibration_reference()

        return self.async_show_form(
            step_id="calibrate_co2",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CALIBRATION_METHOD,
                        default=CALIBRATION_METHOD_FRESH_AIR,
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                CALIBRATION_METHOD_FRESH_AIR,
                                CALIBRATION_METHOD_REFERENCE_SENSORS,
                            ],
                            translation_key="calibration_method",
                        )
                    )
                }
            ),
        )

    async def async_step_calibration_exposure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Explain and acknowledge the documented fresh-air preparation."""

        if user_input is not None:
            self._reference_entity_ids = []
            self._reference_ppm = int(user_input[CONF_REFERENCE_PPM])
            self._reference_summary = (
                "Method: Vent-Axia fresh-air/manual-value procedure. "
                f"Reference entered: {self._reference_ppm} ppm. The official "
                "app defaults to 450 ppm; a different value should come from "
                "a trusted calibrated measurement device. Target: internal "
                "MEV CO2 sensor."
            )
            return await self.async_step_calibration_confirm()
        return self.async_show_form(
            step_id="calibration_exposure",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_REFERENCE_PPM,
                        default=DEFAULT_CO2_CALIBRATION_REFERENCE,
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=MIN_CO2_CALIBRATION_REFERENCE,
                            max=MAX_CO2_CALIBRATION_REFERENCE,
                            step=1,
                            mode=selector.NumberSelectorMode.BOX,
                            unit_of_measurement="ppm",
                        )
                    )
                }
            ),
        )

    async def async_step_calibration_reference(
        self,
        user_input: dict[str, Any] | None = None,
        *,
        errors: dict[str, str] | None = None,
    ) -> ConfigFlowResult:
        """Select and validate independent Home Assistant CO2 references."""

        if user_input is not None:
            selected = user_input[CONF_REFERENCE_SENSORS]
            raw_entity_ids = (
                [selected] if isinstance(selected, str) else list(selected)
            )
            entity_ids = list(dict.fromkeys(raw_entity_ids))
            try:
                reference_ppm, summary = self._read_reference_sensors(entity_ids)
            except CalibrationReferenceError as err:
                errors = {"base": err.reason}
            else:
                self._reference_entity_ids = entity_ids
                self._reference_ppm = reference_ppm
                self._reference_summary = summary
                return await self.async_step_calibration_confirm()

        return self.async_show_form(
            step_id="calibration_reference",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_REFERENCE_SENSORS): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            domain="sensor",
                            device_class=SensorDeviceClass.CO2,
                            multiple=True,
                        )
                    )
                }
            ),
            errors=errors or {},
        )

    async def async_step_calibration_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Require final review immediately before the BLE write."""

        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input[CONF_CONFIRM_CALIBRATION]:
                errors["base"] = "confirmation_required"
            else:
                if self._reference_entity_ids:
                    try:
                        self._reference_ppm, self._reference_summary = (
                            self._read_reference_sensors(self._reference_entity_ids)
                        )
                    except CalibrationReferenceError as err:
                        return await self.async_step_calibration_reference(
                            errors={"base": err.reason}
                        )
                assert self._reference_ppm is not None
                try:
                    await self.config_entry.runtime_data.async_calibrate_internal_co2(
                        self._reference_ppm
                    )
                except CalibrationNotSupportedError:
                    errors["base"] = "calibration_not_supported"
                except CalibrationRateLimitedError:
                    errors["base"] = "calibration_rate_limited"
                except CalibrationCommandNotSentError as err:
                    _LOGGER.warning(
                        "Multihome CO2 calibration was not sent: %s", err
                    )
                    errors["base"] = "calibration_not_sent"
                except CalibrationDeliveryUncertainError as err:
                    _LOGGER.warning(
                        "Multihome CO2 calibration delivery is uncertain: %s", err
                    )
                    errors["base"] = "calibration_delivery_uncertain"
                except HomeAssistantError as err:
                    _LOGGER.warning(
                        "Unable to start Multihome CO2 calibration: %s", err
                    )
                    errors["base"] = "calibration_failed"
                else:
                    return await self.async_step_calibration_progress()

        return self.async_show_form(
            step_id="calibration_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CONFIRM_CALIBRATION, default=False
                    ): selector.BooleanSelector()
                }
            ),
            errors=errors,
            description_placeholders={
                "device": self.config_entry.title,
                "reference_ppm": str(self._reference_ppm or ""),
                "reference_summary": self._reference_summary,
            },
        )

    async def async_step_calibration_progress(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Track the manual's three-minute internal-sensor sampling period."""

        if self._calibration_progress_task is None:
            self._calibration_progress_task = self.hass.async_create_task(
                self._async_track_calibration_progress(),
                "Track Multihome CO2 calibration sampling period",
            )
        if not self._calibration_progress_task.done():
            return self.async_show_progress(
                step_id="calibration_progress",
                progress_action="calibration_sampling",
                progress_task=self._calibration_progress_task,
                description_placeholders={
                    "device": self.config_entry.title,
                    "reference_ppm": str(self._reference_ppm or ""),
                },
            )
        return self.async_show_progress_done(next_step_id="calibration_result")

    async def _async_track_calibration_progress(self) -> None:
        """Update HA progress for the documented 180-second sampling period."""

        steps = max(
            1,
            math.ceil(
                CO2_CALIBRATION_SAMPLING_DURATION
                / CO2_CALIBRATION_PROGRESS_INTERVAL
            ),
        )
        for step in range(1, steps + 1):
            await asyncio.sleep(CO2_CALIBRATION_PROGRESS_INTERVAL)
            self.async_update_progress(step / steps)

    async def async_step_calibration_result(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Explain elapsed sampling time without claiming calibration readback."""

        if user_input is not None:
            return self.async_create_entry(
                title="", data=dict(self.config_entry.options)
            )
        return self.async_show_form(
            step_id="calibration_result",
            data_schema=vol.Schema({}),
            description_placeholders={
                "device": self.config_entry.title,
                "reference_ppm": str(self._reference_ppm or ""),
            },
        )

    def _read_reference_sensors(
        self, entity_ids: list[str]
    ) -> tuple[int, str]:
        """Return the rounded mean and display summary for trusted references."""

        if not entity_ids:
            raise CalibrationReferenceError("invalid_reference")

        registry = er.async_get(self.hass)
        own_entities = {
            entry.entity_id
            for entry in er.async_entries_for_config_entry(
                registry, self.config_entry.entry_id
            )
        }
        values: list[float] = []
        summaries: list[str] = []
        for entity_id in entity_ids:
            if entity_id in own_entities:
                raise CalibrationReferenceError("self_reference")
            state = self.hass.states.get(entity_id)
            if state is None or state.state in {STATE_UNKNOWN, STATE_UNAVAILABLE}:
                raise CalibrationReferenceError("reference_unavailable")
            if state.attributes.get(ATTR_DEVICE_CLASS) != SensorDeviceClass.CO2:
                raise CalibrationReferenceError("invalid_reference")
            unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
            if not isinstance(unit, str) or unit.casefold() != (
                CONCENTRATION_PARTS_PER_MILLION
            ):
                raise CalibrationReferenceError("invalid_reference")
            try:
                value = float(state.state)
            except ValueError as err:
                raise CalibrationReferenceError("invalid_reference") from err
            if not math.isfinite(value) or not (
                MIN_CO2_CALIBRATION_REFERENCE
                <= value
                <= MAX_CO2_CALIBRATION_REFERENCE
            ):
                raise CalibrationReferenceError("reference_out_of_range")
            values.append(value)
            summaries.append(
                f"{state.name}: {value:g} ppm "
                f"({state.last_updated.isoformat(timespec='seconds')})"
            )

        reference_ppm = math.floor(fmean(values) + 0.5)
        detail = "; ".join(summaries)
        if len(entity_ids) == 1:
            detail += (
                ". One reference was selected; it must represent the air "
                "entering all MEV extract paths"
            )
        return reference_ppm, f"Method: trusted HA reference. {detail}"
