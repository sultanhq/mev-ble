"""Config flow for Vent-Axia Multihome."""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import time as dt_time
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
    AirflowConfigurationNotSupportedError,
    AirflowConfigurationUnavailableError,
    CalibrationCommandNotSentError,
    CalibrationDeliveryUncertainError,
    CalibrationNotSupportedError,
    CalibrationRateLimitedError,
    ComfortModeConfigurationNotSupportedError,
    ComfortModeConfigurationUnavailableError,
    DelayOverrunConfigurationNotSupportedError,
    DelayOverrunConfigurationUnavailableError,
    HumidityResponseConfigurationNotSupportedError,
    HumidityResponseConfigurationUnavailableError,
    SensorThresholdConfigurationNotSupportedError,
    SensorThresholdConfigurationUnavailableError,
    SilentHoursConfigurationUnavailableError,
    SilentHoursNotSupportedError,
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
    AIRFLOW_SPEED_LIMITS,
    DEFAULT_CO2_CALIBRATION_REFERENCE,
    GLOBAL_CO2_THRESHOLD_STEP,
    GLOBAL_SETTING_FIELD_SPECS,
    MAX_CO2_CALIBRATION_REFERENCE,
    MAX_GLOBAL_TIMER_MINUTES,
    MIN_CO2_CALIBRATION_REFERENCE,
    MIN_GLOBAL_TIMER_MINUTES,
    GlobalSettingField,
    GlobalSettings,
    ProtocolError,
    SilentHour,
    SilentHourSlot,
    decode_silent_hour,
    encode_silent_hour,
    plan_delay_overrun_updates,
    validate_airflow_profile,
    validate_sensor_thresholds,
)

_LOGGER = logging.getLogger(__name__)

CONF_CALIBRATION_METHOD = "calibration_method"
CONF_REFERENCE_PPM = "reference_ppm"
CONF_REFERENCE_SENSORS = "reference_sensors"
CONF_CONFIRM_CALIBRATION = "confirm_calibration"
CONF_AIRFLOW_LOW = "airflow_low"
CONF_AIRFLOW_NORMAL = "airflow_normal"
CONF_AIRFLOW_BOOST = "airflow_boost"
CONF_AIRFLOW_PURGE = "airflow_purge"
CONF_CONFIRM_AIRFLOW = "confirm_airflow"
CONF_HUMIDITY_THRESHOLD = "humidity_threshold"
CONF_CO2_BOOST_THRESHOLD = "co2_boost_threshold"
CONF_CO2_PURGE_THRESHOLD = "co2_purge_threshold"
CONF_CONFIRM_SENSOR_THRESHOLDS = "confirm_sensor_thresholds"
CONF_RAPID_RESPONSE = "rapid_response"
CONF_AMBIENT_RESPONSE = "ambient_response"
CONF_CONFIRM_HUMIDITY_RESPONSE = "confirm_humidity_response"
CONF_COMFORT_MODE = "comfort_mode"
CONF_CONFIRM_COMFORT_MODE = "confirm_comfort_mode"
CONF_DELAY_ENABLED = "delay_enabled"
CONF_DELAY_TIMEOUT = "delay_timeout"
CONF_OVERRUN_ENABLED = "overrun_enabled"
CONF_OVERRUN_TIMEOUT = "overrun_timeout"
CONF_CONFIRM_DELAY_OVERRUN = "confirm_delay_overrun"
CONF_SILENT_HOUR_SLOT = "silent_hour_slot"
CONF_SILENT_HOUR_ACTION = "silent_hour_action"
CONF_SILENT_HOUR_START = "silent_hour_start"
CONF_SILENT_HOUR_END = "silent_hour_end"
CONF_SILENT_HOUR_WEEKDAYS = "silent_hour_weekdays"
CONF_CONFIRM_SILENT_HOUR_DELETE = "confirm_silent_hour_delete"

CALIBRATION_METHOD_FRESH_AIR = "fresh_air"
CALIBRATION_METHOD_REFERENCE_SENSORS = "reference_sensors"
SILENT_HOUR_ACTION_EDIT = "edit"
SILENT_HOUR_ACTION_DELETE = "delete"
WEEKDAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)


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
            return self._show_pairing_instructions(errors={"base": "no_devices_found"})
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
        self._airflow_profile: tuple[int, int, int, int] | None = None
        self._airflow_baseline_raw: bytes | None = None
        self._sensor_thresholds: tuple[int, int, int] | None = None
        self._sensor_thresholds_baseline_raw: bytes | None = None
        self._humidity_response: tuple[bool, bool] | None = None
        self._humidity_response_baseline_raw: bytes | None = None
        self._comfort_mode: bool | None = None
        self._comfort_mode_baseline_raw: bytes | None = None
        self._delay_overrun: tuple[bool, int, bool, int] | None = None
        self._delay_overrun_baseline_raw: bytes | None = None
        self._silent_hour_index: int | None = None
        self._silent_hours_baseline: tuple[tuple[int, bytes | None], ...] | None = (
            None
        )
        self._silent_hour_result = ""
        self._silent_hour_operation_active = False

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the integration options menu."""

        menu_options = ["fan_options"]
        coordinator = self.config_entry.runtime_data
        if (
            coordinator.device.supports_global_airflow_configuration
            and coordinator.data is not None
            and coordinator.last_update_success
            and coordinator.device.global_settings_write_ready
        ):
            menu_options.append("airflow_profile")
        if (
            coordinator.device.supports_sensor_threshold_configuration
            and coordinator.data is not None
            and coordinator.last_update_success
            and coordinator.device.global_settings_write_ready
        ):
            menu_options.append("sensor_thresholds")
        if (
            coordinator.device.supports_humidity_response_configuration
            and coordinator.data is not None
            and coordinator.last_update_success
            and coordinator.device.global_settings_write_ready
            and coordinator.data.global_settings.rapid_response_enabled is not None
            and coordinator.data.global_settings.ambient_response_enabled is not None
        ):
            menu_options.append("humidity_response")
        if (
            coordinator.device.supports_comfort_mode_configuration
            and coordinator.data is not None
            and coordinator.last_update_success
            and coordinator.device.global_settings_write_ready
            and coordinator.data.global_settings.comfort_enabled is not None
        ):
            menu_options.append("comfort_mode")
        if (
            coordinator.device.supports_delay_overrun_configuration
            and self._current_delay_overrun_settings() is not None
        ):
            menu_options.append("delay_overrun")
        if self.config_entry.runtime_data.device.supports_internal_co2_calibration:
            menu_options.append("calibrate_co2")
        if (
            coordinator.device.supports_silent_hours_management
            and coordinator.data is not None
            and coordinator.last_update_success
            and coordinator.device.silent_hours_write_ready
            and len(coordinator.data.silent_hours) == 6
        ):
            menu_options.append("silent_hours")
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

    async def async_step_airflow_profile(
        self,
        user_input: dict[str, Any] | None = None,
        *,
        errors: dict[str, str] | None = None,
    ) -> ConfigFlowResult:
        """Collect one complete, ordered motor-speed percentage profile."""

        coordinator = self.config_entry.runtime_data
        if not coordinator.device.supports_global_airflow_configuration:
            return self.async_abort(reason="airflow_not_supported")
        settings = self._current_airflow_settings()
        if settings is None:
            return self.async_abort(reason="global_settings_unavailable")

        if user_input is not None:
            try:
                profile = tuple(
                    self._integer_percentage(user_input[key])
                    for key in (
                        CONF_AIRFLOW_LOW,
                        CONF_AIRFLOW_NORMAL,
                        CONF_AIRFLOW_BOOST,
                        CONF_AIRFLOW_PURGE,
                    )
                )
                validate_airflow_profile(*profile)
            except (KeyError, ProtocolError, TypeError, ValueError):
                errors = {"base": "airflow_profile_invalid"}
            else:
                current = (
                    settings.speed_low,
                    settings.speed_medium,
                    settings.speed_boost,
                    settings.speed_purge,
                )
                if profile == current:
                    errors = {"base": "airflow_profile_unchanged"}
                else:
                    self._airflow_profile = profile
                    self._airflow_baseline_raw = settings.raw_record
                    return await self.async_step_airflow_confirm()

        return self.async_show_form(
            step_id="airflow_profile",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_AIRFLOW_LOW, default=settings.speed_low
                    ): self._airflow_selector("low"),
                    vol.Required(
                        CONF_AIRFLOW_NORMAL, default=settings.speed_medium
                    ): self._airflow_selector("normal"),
                    vol.Required(
                        CONF_AIRFLOW_BOOST, default=settings.speed_boost
                    ): self._airflow_selector("boost"),
                    vol.Required(
                        CONF_AIRFLOW_PURGE, default=settings.speed_purge
                    ): self._airflow_selector("purge"),
                }
            ),
            errors=errors or {},
            description_placeholders={
                "current_profile": self._format_airflow_profile(
                    settings.speed_low,
                    settings.speed_medium,
                    settings.speed_boost,
                    settings.speed_purge,
                )
            },
        )

    async def async_step_airflow_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Recheck the baseline and require confirmation before any BLE write."""

        assert self._airflow_profile is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input[CONF_CONFIRM_AIRFLOW]:
                errors["base"] = "airflow_confirmation_required"
            else:
                settings = self._current_airflow_settings()
                if settings is None:
                    errors["base"] = "global_settings_unavailable"
                elif settings.raw_record != self._airflow_baseline_raw:
                    self._airflow_profile = None
                    self._airflow_baseline_raw = None
                    return await self.async_step_airflow_profile(
                        errors={"base": "airflow_settings_changed"}
                    )
                else:
                    low, normal, boost, purge = self._airflow_profile
                    try:
                        await self.config_entry.runtime_data.async_set_airflow_profile(
                            low=low,
                            normal=normal,
                            boost=boost,
                            purge=purge,
                        )
                    except AirflowConfigurationNotSupportedError:
                        return self.async_abort(reason="airflow_not_supported")
                    except AirflowConfigurationUnavailableError:
                        errors["base"] = "global_settings_unavailable"
                    except HomeAssistantError as err:
                        _LOGGER.warning(
                            "Unable to update Multihome airflow profile: %s", err
                        )
                        errors["base"] = "airflow_update_failed"
                    else:
                        return await self.async_step_airflow_result()

        settings = self._current_airflow_settings()
        current_profile = (
            self._format_airflow_profile(
                settings.speed_low,
                settings.speed_medium,
                settings.speed_boost,
                settings.speed_purge,
            )
            if settings is not None
            else "Unavailable"
        )
        return self.async_show_form(
            step_id="airflow_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CONFIRM_AIRFLOW, default=False
                    ): selector.BooleanSelector()
                }
            ),
            errors=errors,
            description_placeholders={
                "device": self.config_entry.title,
                "current_profile": current_profile,
                "new_profile": self._format_airflow_profile(*self._airflow_profile),
            },
        )

    async def async_step_airflow_result(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Report the exact profile confirmed through packet-137 readback."""

        assert self._airflow_profile is not None
        if user_input is not None:
            return self.async_create_entry(
                title="", data=dict(self.config_entry.options)
            )
        return self.async_show_form(
            step_id="airflow_result",
            data_schema=vol.Schema({}),
            description_placeholders={
                "device": self.config_entry.title,
                "new_profile": self._format_airflow_profile(*self._airflow_profile),
            },
        )

    async def async_step_sensor_thresholds(
        self,
        user_input: dict[str, Any] | None = None,
        *,
        errors: dict[str, str] | None = None,
    ) -> ConfigFlowResult:
        """Collect one reviewed humidity and CO2 threshold profile."""

        coordinator = self.config_entry.runtime_data
        if not coordinator.device.supports_sensor_threshold_configuration:
            return self.async_abort(reason="sensor_thresholds_not_supported")
        settings = self._current_sensor_threshold_settings()
        if settings is None:
            return self.async_abort(reason="global_settings_unavailable")

        if user_input is not None:
            try:
                thresholds = (
                    self._integer_setting(user_input[CONF_HUMIDITY_THRESHOLD]),
                    self._integer_setting(user_input[CONF_CO2_BOOST_THRESHOLD]),
                    self._integer_setting(user_input[CONF_CO2_PURGE_THRESHOLD]),
                )
                validate_sensor_thresholds(*thresholds)
            except (KeyError, ProtocolError, TypeError, ValueError):
                errors = {"base": "sensor_thresholds_invalid"}
            else:
                current = (
                    settings.humidity_threshold,
                    settings.co2_boost_threshold,
                    settings.co2_purge_threshold,
                )
                if thresholds == current:
                    errors = {"base": "sensor_thresholds_unchanged"}
                else:
                    self._sensor_thresholds = thresholds
                    self._sensor_thresholds_baseline_raw = settings.raw_record
                    return await self.async_step_sensor_thresholds_confirm()

        return self.async_show_form(
            step_id="sensor_thresholds",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_HUMIDITY_THRESHOLD,
                        default=settings.humidity_threshold,
                    ): self._sensor_threshold_selector(
                        GlobalSettingField.HUMIDITY_THRESHOLD, "%"
                    ),
                    vol.Required(
                        CONF_CO2_BOOST_THRESHOLD,
                        default=settings.co2_boost_threshold,
                    ): self._sensor_threshold_selector(
                        GlobalSettingField.CO2_BOOST_THRESHOLD, "ppm"
                    ),
                    vol.Required(
                        CONF_CO2_PURGE_THRESHOLD,
                        default=settings.co2_purge_threshold,
                    ): self._sensor_threshold_selector(
                        GlobalSettingField.CO2_PURGE_THRESHOLD, "ppm"
                    ),
                }
            ),
            errors=errors or {},
            description_placeholders={
                "current_thresholds": self._format_sensor_thresholds(
                    settings.humidity_threshold,
                    settings.co2_boost_threshold,
                    settings.co2_purge_threshold,
                )
            },
        )

    async def async_step_sensor_thresholds_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Recheck current settings and require confirmation before writing."""

        assert self._sensor_thresholds is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input[CONF_CONFIRM_SENSOR_THRESHOLDS]:
                errors["base"] = "sensor_thresholds_confirmation_required"
            else:
                settings = self._current_sensor_threshold_settings()
                if settings is None:
                    errors["base"] = "global_settings_unavailable"
                elif settings.raw_record != self._sensor_thresholds_baseline_raw:
                    self._sensor_thresholds = None
                    self._sensor_thresholds_baseline_raw = None
                    return await self.async_step_sensor_thresholds(
                        errors={"base": "sensor_thresholds_settings_changed"}
                    )
                else:
                    humidity, co2_boost, co2_purge = self._sensor_thresholds
                    try:
                        coordinator = self.config_entry.runtime_data
                        await coordinator.async_set_sensor_thresholds(
                            humidity=humidity,
                            co2_boost=co2_boost,
                            co2_purge=co2_purge,
                        )
                    except SensorThresholdConfigurationNotSupportedError:
                        return self.async_abort(
                            reason="sensor_thresholds_not_supported"
                        )
                    except SensorThresholdConfigurationUnavailableError:
                        errors["base"] = "global_settings_unavailable"
                    except HomeAssistantError as err:
                        _LOGGER.warning(
                            "Unable to update Multihome sensor thresholds: %s", err
                        )
                        errors["base"] = "sensor_thresholds_update_failed"
                    else:
                        return await self.async_step_sensor_thresholds_result()

        settings = self._current_sensor_threshold_settings()
        current = (
            self._format_sensor_thresholds(
                settings.humidity_threshold,
                settings.co2_boost_threshold,
                settings.co2_purge_threshold,
            )
            if settings is not None
            else "Unavailable"
        )
        return self.async_show_form(
            step_id="sensor_thresholds_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CONFIRM_SENSOR_THRESHOLDS, default=False
                    ): selector.BooleanSelector()
                }
            ),
            errors=errors,
            description_placeholders={
                "device": self.config_entry.title,
                "current_thresholds": current,
                "new_thresholds": self._format_sensor_thresholds(
                    *self._sensor_thresholds
                ),
            },
        )

    async def async_step_sensor_thresholds_result(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Report thresholds confirmed through exact packet-137 readback."""

        assert self._sensor_thresholds is not None
        if user_input is not None:
            return self.async_create_entry(
                title="", data=dict(self.config_entry.options)
            )
        return self.async_show_form(
            step_id="sensor_thresholds_result",
            data_schema=vol.Schema({}),
            description_placeholders={
                "device": self.config_entry.title,
                "new_thresholds": self._format_sensor_thresholds(
                    *self._sensor_thresholds
                ),
            },
        )

    async def async_step_humidity_response(
        self,
        user_input: dict[str, Any] | None = None,
        *,
        errors: dict[str, str] | None = None,
    ) -> ConfigFlowResult:
        """Collect one reviewed rapid/ambient humidity-response profile."""

        coordinator = self.config_entry.runtime_data
        if not coordinator.device.supports_humidity_response_configuration:
            return self.async_abort(reason="humidity_response_not_supported")
        settings = self._current_humidity_response_settings()
        if settings is None:
            return self.async_abort(reason="global_settings_unavailable")

        if user_input is not None:
            rapid = user_input.get(CONF_RAPID_RESPONSE)
            ambient = user_input.get(CONF_AMBIENT_RESPONSE)
            if not isinstance(rapid, bool) or not isinstance(ambient, bool):
                errors = {"base": "humidity_response_invalid"}
            elif (rapid, ambient) == (
                settings.rapid_response_enabled,
                settings.ambient_response_enabled,
            ):
                errors = {"base": "humidity_response_unchanged"}
            else:
                self._humidity_response = (rapid, ambient)
                self._humidity_response_baseline_raw = settings.raw_record
                return await self.async_step_humidity_response_confirm()

        return self.async_show_form(
            step_id="humidity_response",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_RAPID_RESPONSE,
                        default=settings.rapid_response_enabled,
                    ): selector.BooleanSelector(),
                    vol.Required(
                        CONF_AMBIENT_RESPONSE,
                        default=settings.ambient_response_enabled,
                    ): selector.BooleanSelector(),
                }
            ),
            errors=errors or {},
            description_placeholders={
                "current_response": self._format_humidity_response(
                    settings.rapid_response_enabled,
                    settings.ambient_response_enabled,
                )
            },
        )

    async def async_step_humidity_response_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Recheck the full record and require confirmation before writing."""

        assert self._humidity_response is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input[CONF_CONFIRM_HUMIDITY_RESPONSE]:
                errors["base"] = "humidity_response_confirmation_required"
            else:
                settings = self._current_humidity_response_settings()
                if settings is None:
                    errors["base"] = "global_settings_unavailable"
                elif settings.raw_record != self._humidity_response_baseline_raw:
                    self._humidity_response = None
                    self._humidity_response_baseline_raw = None
                    return await self.async_step_humidity_response(
                        errors={"base": "humidity_response_settings_changed"}
                    )
                else:
                    rapid, ambient = self._humidity_response
                    try:
                        coordinator = self.config_entry.runtime_data
                        await coordinator.async_set_humidity_response(
                            rapid=rapid, ambient=ambient
                        )
                    except HumidityResponseConfigurationNotSupportedError:
                        return self.async_abort(
                            reason="humidity_response_not_supported"
                        )
                    except HumidityResponseConfigurationUnavailableError:
                        errors["base"] = "global_settings_unavailable"
                    except HomeAssistantError as err:
                        _LOGGER.warning(
                            "Unable to update Multihome humidity response: %s", err
                        )
                        errors["base"] = "humidity_response_update_failed"
                    else:
                        return await self.async_step_humidity_response_result()

        settings = self._current_humidity_response_settings()
        current = (
            self._format_humidity_response(
                settings.rapid_response_enabled,
                settings.ambient_response_enabled,
            )
            if settings is not None
            else "Unavailable"
        )
        return self.async_show_form(
            step_id="humidity_response_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CONFIRM_HUMIDITY_RESPONSE, default=False
                    ): selector.BooleanSelector()
                }
            ),
            errors=errors,
            description_placeholders={
                "device": self.config_entry.title,
                "current_response": current,
                "new_response": self._format_humidity_response(
                    *self._humidity_response
                ),
            },
        )

    async def async_step_humidity_response_result(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Report flags confirmed through exact packet-137 readback."""

        assert self._humidity_response is not None
        if user_input is not None:
            return self.async_create_entry(
                title="", data=dict(self.config_entry.options)
            )
        return self.async_show_form(
            step_id="humidity_response_result",
            data_schema=vol.Schema({}),
            description_placeholders={
                "device": self.config_entry.title,
                "new_response": self._format_humidity_response(
                    *self._humidity_response
                ),
            },
        )

    async def async_step_comfort_mode(
        self,
        user_input: dict[str, Any] | None = None,
        *,
        errors: dict[str, str] | None = None,
    ) -> ConfigFlowResult:
        """Collect one reviewed Comfort mode candidate value."""

        coordinator = self.config_entry.runtime_data
        if not coordinator.device.supports_comfort_mode_configuration:
            return self.async_abort(reason="comfort_mode_not_supported")
        settings = self._current_comfort_mode_settings()
        if settings is None:
            return self.async_abort(reason="global_settings_unavailable")

        if user_input is not None:
            enabled = user_input.get(CONF_COMFORT_MODE)
            if not isinstance(enabled, bool):
                errors = {"base": "comfort_mode_invalid"}
            elif enabled == settings.comfort_enabled:
                errors = {"base": "comfort_mode_unchanged"}
            else:
                self._comfort_mode = enabled
                self._comfort_mode_baseline_raw = settings.raw_record
                return await self.async_step_comfort_mode_confirm()

        return self.async_show_form(
            step_id="comfort_mode",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_COMFORT_MODE,
                        default=settings.comfort_enabled,
                    ): selector.BooleanSelector()
                }
            ),
            errors=errors or {},
            description_placeholders={
                "current_comfort_mode": self._format_enabled(
                    settings.comfort_enabled
                )
            },
        )

    async def async_step_comfort_mode_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Recheck the full record and require confirmation before writing."""

        assert self._comfort_mode is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input[CONF_CONFIRM_COMFORT_MODE]:
                errors["base"] = "comfort_mode_confirmation_required"
            else:
                settings = self._current_comfort_mode_settings()
                if settings is None:
                    errors["base"] = "global_settings_unavailable"
                elif settings.raw_record != self._comfort_mode_baseline_raw:
                    self._comfort_mode = None
                    self._comfort_mode_baseline_raw = None
                    return await self.async_step_comfort_mode(
                        errors={"base": "comfort_mode_settings_changed"}
                    )
                else:
                    try:
                        coordinator = self.config_entry.runtime_data
                        await coordinator.async_set_comfort_mode(
                            enabled=self._comfort_mode
                        )
                    except ComfortModeConfigurationNotSupportedError:
                        return self.async_abort(reason="comfort_mode_not_supported")
                    except ComfortModeConfigurationUnavailableError:
                        errors["base"] = "global_settings_unavailable"
                    except HomeAssistantError as err:
                        _LOGGER.warning(
                            "Unable to update Multihome Comfort mode: %s", err
                        )
                        errors["base"] = "comfort_mode_update_failed"
                    else:
                        return await self.async_step_comfort_mode_result()

        settings = self._current_comfort_mode_settings()
        current = (
            self._format_enabled(settings.comfort_enabled)
            if settings is not None
            else "Unavailable"
        )
        return self.async_show_form(
            step_id="comfort_mode_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CONFIRM_COMFORT_MODE, default=False
                    ): selector.BooleanSelector()
                }
            ),
            errors=errors,
            description_placeholders={
                "device": self.config_entry.title,
                "current_comfort_mode": current,
                "new_comfort_mode": self._format_enabled(self._comfort_mode),
            },
        )

    async def async_step_comfort_mode_result(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Report Comfort mode confirmed through exact packet-137 readback."""

        assert self._comfort_mode is not None
        if user_input is not None:
            return self.async_create_entry(
                title="", data=dict(self.config_entry.options)
            )
        return self.async_show_form(
            step_id="comfort_mode_result",
            data_schema=vol.Schema({}),
            description_placeholders={
                "device": self.config_entry.title,
                "new_comfort_mode": self._format_enabled(self._comfort_mode),
            },
        )


    async def async_step_delay_overrun(
        self,
        user_input: dict[str, Any] | None = None,
        *,
        errors: dict[str, str] | None = None,
    ) -> ConfigFlowResult:
        """Collect one reviewed paired LS timer profile."""

        coordinator = self.config_entry.runtime_data
        if not coordinator.device.supports_delay_overrun_configuration:
            return self.async_abort(reason="delay_overrun_not_supported")
        settings = self._current_delay_overrun_settings()
        if settings is None:
            return self.async_abort(reason="global_settings_unavailable")

        if user_input is not None:
            try:
                profile = (
                    user_input[CONF_DELAY_ENABLED],
                    self._integer_setting(user_input[CONF_DELAY_TIMEOUT]),
                    user_input[CONF_OVERRUN_ENABLED],
                    self._integer_setting(user_input[CONF_OVERRUN_TIMEOUT]),
                )
                plan = plan_delay_overrun_updates(
                    settings,
                    delay_enabled=profile[0],
                    delay_minutes=profile[1],
                    overrun_enabled=profile[2],
                    overrun_minutes=profile[3],
                )
            except (KeyError, ProtocolError, TypeError, ValueError):
                errors = {"base": "delay_overrun_invalid"}
            else:
                if not plan:
                    errors = {"base": "delay_overrun_unchanged"}
                else:
                    self._delay_overrun = profile
                    self._delay_overrun_baseline_raw = settings.raw_record
                    return await self.async_step_delay_overrun_confirm()

        return self.async_show_form(
            step_id="delay_overrun",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_DELAY_ENABLED, default=settings.delay_enabled
                    ): selector.BooleanSelector(),
                    vol.Required(
                        CONF_DELAY_TIMEOUT,
                        default=settings.delay_timeout_minutes,
                    ): self._timer_selector(),
                    vol.Required(
                        CONF_OVERRUN_ENABLED, default=settings.overrun_enabled
                    ): selector.BooleanSelector(),
                    vol.Required(
                        CONF_OVERRUN_TIMEOUT,
                        default=settings.overrun_timeout_minutes,
                    ): self._timer_selector(),
                }
            ),
            errors=errors or {},
            description_placeholders={
                "current_timers": self._format_delay_overrun(
                    settings.delay_enabled,
                    settings.delay_timeout_minutes,
                    settings.overrun_enabled,
                    settings.overrun_timeout_minutes,
                )
            },
        )

    async def async_step_delay_overrun_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Recheck all 36 bytes and confirm the paired timer profile."""

        assert self._delay_overrun is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input[CONF_CONFIRM_DELAY_OVERRUN]:
                errors["base"] = "delay_overrun_confirmation_required"
            else:
                settings = self._current_delay_overrun_settings()
                if settings is None:
                    errors["base"] = "global_settings_unavailable"
                elif settings.raw_record != self._delay_overrun_baseline_raw:
                    self._delay_overrun = None
                    self._delay_overrun_baseline_raw = None
                    return await self.async_step_delay_overrun(
                        errors={"base": "delay_overrun_settings_changed"}
                    )
                else:
                    try:
                        coordinator = self.config_entry.runtime_data
                        await coordinator.async_set_delay_overrun(
                            delay_enabled=self._delay_overrun[0],
                            delay_minutes=self._delay_overrun[1],
                            overrun_enabled=self._delay_overrun[2],
                            overrun_minutes=self._delay_overrun[3],
                        )
                    except DelayOverrunConfigurationNotSupportedError:
                        return self.async_abort(reason="delay_overrun_not_supported")
                    except DelayOverrunConfigurationUnavailableError:
                        errors["base"] = "global_settings_unavailable"
                    except HomeAssistantError as err:
                        _LOGGER.warning(
                            "Unable to update Multihome delay/overrun timers: %s", err
                        )
                        errors["base"] = "delay_overrun_update_failed"
                    else:
                        return await self.async_step_delay_overrun_result()

        settings = self._current_delay_overrun_settings()
        current = (
            self._format_delay_overrun(
                settings.delay_enabled,
                settings.delay_timeout_minutes,
                settings.overrun_enabled,
                settings.overrun_timeout_minutes,
            )
            if settings is not None
            else "Unavailable"
        )
        return self.async_show_form(
            step_id="delay_overrun_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CONFIRM_DELAY_OVERRUN, default=False
                    ): selector.BooleanSelector()
                }
            ),
            errors=errors,
            description_placeholders={
                "device": self.config_entry.title,
                "current_timers": current,
                "new_timers": self._format_delay_overrun(*self._delay_overrun),
            },
        )

    async def async_step_delay_overrun_result(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Report paired LS timers confirmed by exact packet-137 readback."""

        assert self._delay_overrun is not None
        if user_input is not None:
            return self.async_create_entry(
                title="", data=dict(self.config_entry.options)
            )
        return self.async_show_form(
            step_id="delay_overrun_result",
            data_schema=vol.Schema({}),
            description_placeholders={
                "device": self.config_entry.title,
                "new_timers": self._format_delay_overrun(*self._delay_overrun),
            },
        )

    def _current_airflow_settings(self) -> GlobalSettings | None:
        """Return a current writable settings record or no capability."""

        coordinator = self.config_entry.runtime_data
        if (
            coordinator.data is None
            or not coordinator.last_update_success
            or not coordinator.device.global_settings_write_ready
        ):
            return None
        return coordinator.data.global_settings

    def _current_sensor_threshold_settings(self) -> GlobalSettings | None:
        """Return a current threshold snapshot or no write capability."""

        coordinator = self.config_entry.runtime_data
        if (
            coordinator.data is None
            or not coordinator.last_update_success
            or not coordinator.device.global_settings_write_ready
        ):
            return None
        return coordinator.data.global_settings

    def _current_humidity_response_settings(self) -> GlobalSettings | None:
        """Return a current, strictly decoded response snapshot."""

        settings = self._current_sensor_threshold_settings()
        if (
            settings is None
            or settings.rapid_response_enabled is None
            or settings.ambient_response_enabled is None
        ):
            return None
        return settings

    def _current_comfort_mode_settings(self) -> GlobalSettings | None:
        """Return a current, strictly decoded Comfort mode snapshot."""

        settings = self._current_sensor_threshold_settings()
        if settings is None or settings.comfort_enabled is None:
            return None
        return settings

    def _current_delay_overrun_settings(self) -> GlobalSettings | None:
        """Return a current, strictly decoded LS timer snapshot."""

        settings = self._current_sensor_threshold_settings()
        if (
            settings is None
            or settings.delay_enabled is None
            or settings.overrun_enabled is None
            or not MIN_GLOBAL_TIMER_MINUTES
            <= settings.delay_timeout_minutes
            <= MAX_GLOBAL_TIMER_MINUTES
            or not MIN_GLOBAL_TIMER_MINUTES
            <= settings.overrun_timeout_minutes
            <= MAX_GLOBAL_TIMER_MINUTES
        ):
            return None
        return settings

    @staticmethod
    def _format_enabled(enabled: bool) -> str:
        """Return a clear enabled/disabled review label."""

        return "Enabled" if enabled else "Disabled"

    @staticmethod
    def _timer_selector() -> selector.NumberSelector:
        """Return the official 1..60 minute LS timer selector."""

        return selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=MIN_GLOBAL_TIMER_MINUTES,
                max=MAX_GLOBAL_TIMER_MINUTES,
                step=1,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement="min",
            )
        )

    @staticmethod
    def _format_delay_overrun(
        delay_enabled: bool,
        delay_minutes: int,
        overrun_enabled: bool,
        overrun_minutes: int,
    ) -> str:
        """Return an unambiguous review string for both paired timers."""

        return (
            f"Delay {'enabled' if delay_enabled else 'disabled'} "
            f"({delay_minutes} min) · "
            f"Overrun {'enabled' if overrun_enabled else 'disabled'} "
            f"({overrun_minutes} min)"
        )

    @staticmethod
    def _format_humidity_response(rapid: bool, ambient: bool) -> str:
        """Return an unambiguous review string for both flags."""

        return (
            f"Rapid {'enabled' if rapid else 'disabled'} · "
            f"Ambient {'enabled' if ambient else 'disabled'}"
        )

    @staticmethod
    def _integer_setting(value: Any) -> int:
        """Accept a finite whole-number selector value without truncation."""

        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("setting must be numeric")
        if not math.isfinite(float(value)) or not float(value).is_integer():
            raise ValueError("setting must be a whole number")
        return int(value)

    @staticmethod
    def _sensor_threshold_selector(
        field: GlobalSettingField, unit: str
    ) -> selector.NumberSelector:
        """Return the recovered wire range for one guarded threshold."""

        spec = GLOBAL_SETTING_FIELD_SPECS[field]
        step = (
            GLOBAL_CO2_THRESHOLD_STEP
            if field
            in {
                GlobalSettingField.CO2_BOOST_THRESHOLD,
                GlobalSettingField.CO2_PURGE_THRESHOLD,
            }
            else 1
        )
        return selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=spec.minimum,
                max=spec.maximum,
                step=step,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement=unit,
            )
        )

    @staticmethod
    def _format_sensor_thresholds(
        humidity: int, co2_boost: int, co2_purge: int
    ) -> str:
        """Return an unambiguous review string for all three thresholds."""

        return (
            f"Humidity {humidity}% · CO₂ boost {co2_boost} ppm · "
            f"CO₂ purge {co2_purge} ppm"
        )

    @staticmethod
    def _integer_percentage(value: Any) -> int:
        """Accept only whole-number selector values without truncation."""

        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("airflow percentage must be numeric")
        if not math.isfinite(float(value)) or not float(value).is_integer():
            raise ValueError("airflow percentage must be a whole number")
        return int(value)

    @staticmethod
    def _airflow_selector(name: str) -> selector.NumberSelector:
        """Return the exact official commissioning selector for one level."""

        minimum, maximum = AIRFLOW_SPEED_LIMITS[name]
        return selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=minimum,
                max=maximum,
                step=1,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement="%",
            )
        )

    @staticmethod
    def _format_airflow_profile(low: int, normal: int, boost: int, purge: int) -> str:
        """Return one unambiguous review string for the four percentages."""

        return f"Low {low}% · Normal {normal}% · Boost {boost}% · Purge {purge}%"

    async def async_step_silent_hours(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show all six slots and choose one guarded operation."""

        slots = self._current_silent_hours()
        if slots is None:
            return self.async_abort(reason="silent_hours_unavailable")
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                index = int(user_input[CONF_SILENT_HOUR_SLOT])
                action = user_input[CONF_SILENT_HOUR_ACTION]
                slot = slots[index]
            except (IndexError, KeyError, TypeError, ValueError):
                errors["base"] = "silent_hour_invalid"
            else:
                if slot.record is not None and not slot.record.is_valid:
                    errors["base"] = "silent_hour_unknown"
                elif action == SILENT_HOUR_ACTION_DELETE and slot.record is None:
                    errors["base"] = "silent_hour_empty"
                else:
                    self._silent_hour_index = index
                    self._silent_hours_baseline = self._silent_hours_fingerprint(slots)
                    if action == SILENT_HOUR_ACTION_DELETE:
                        return await self.async_step_silent_hour_delete()
                    return await self.async_step_silent_hour_edit()

        return self.async_show_form(
            step_id="silent_hours",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SILENT_HOUR_SLOT, default="0"
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                {"value": str(index), "label": f"Slot {index + 1}"}
                                for index in range(6)
                            ]
                        )
                    ),
                    vol.Required(
                        CONF_SILENT_HOUR_ACTION,
                        default=SILENT_HOUR_ACTION_EDIT,
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                SILENT_HOUR_ACTION_EDIT,
                                SILENT_HOUR_ACTION_DELETE,
                            ],
                            translation_key="silent_hour_action",
                        )
                    ),
                }
            ),
            errors=errors,
            description_placeholders={
                "schedule_table": self._format_silent_hours(slots)
            },
        )

    async def async_step_silent_hour_edit(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create or edit one schedule using times and named weekdays."""

        assert self._silent_hour_index is not None
        slots = self._current_silent_hours()
        if slots is None:
            return self.async_abort(reason="silent_hours_unavailable")
        current = slots[self._silent_hour_index].record
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                start = self._time_to_seconds(user_input[CONF_SILENT_HOUR_START])
                end = self._time_to_seconds(user_input[CONF_SILENT_HOUR_END])
                weekdays = list(user_input[CONF_SILENT_HOUR_WEEKDAYS])
                mask = self._weekdays_to_mask(weekdays)
                record = decode_silent_hour(encode_silent_hour(start, end, mask))
            except (KeyError, ProtocolError, TypeError, ValueError):
                errors["base"] = "silent_hour_invalid"
            else:
                if not self._silent_hours_unchanged(slots):
                    return self.async_abort(reason="silent_hours_changed")
                if current is not None and current.raw_record == record.raw_record:
                    errors["base"] = "silent_hour_unchanged"
                elif self._silent_hour_operation_active:
                    errors["base"] = "silent_hour_busy"
                else:
                    self._silent_hour_operation_active = True
                    try:
                        await self.config_entry.runtime_data.async_set_silent_hour(
                            self._silent_hour_index, record
                        )
                    except SilentHoursNotSupportedError:
                        return self.async_abort(reason="silent_hours_not_supported")
                    except SilentHoursConfigurationUnavailableError:
                        errors["base"] = "silent_hours_unavailable"
                    except HomeAssistantError as err:
                        _LOGGER.warning(
                            "Unable to update Multihome silent hours: %s", err
                        )
                        errors["base"] = "silent_hour_update_failed"
                    else:
                        self._silent_hour_result = self._format_silent_hour(record)
                        return await self.async_step_silent_hour_result()
                    finally:
                        self._silent_hour_operation_active = False

        default_start = self._seconds_to_time(
            current.start_seconds if current else 22 * 3600
        )
        default_end = self._seconds_to_time(
            current.end_seconds if current else 7 * 3600
        )
        default_weekdays = self._mask_to_weekdays(
            current.weekdays_mask if current else 0x7F
        )
        return self.async_show_form(
            step_id="silent_hour_edit",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SILENT_HOUR_START, default=default_start
                    ): selector.TimeSelector(),
                    vol.Required(
                        CONF_SILENT_HOUR_END, default=default_end
                    ): selector.TimeSelector(),
                    vol.Required(
                        CONF_SILENT_HOUR_WEEKDAYS, default=default_weekdays
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=list(WEEKDAYS),
                            multiple=True,
                            translation_key="silent_hour_weekdays",
                        )
                    ),
                }
            ),
            errors=errors,
            description_placeholders={
                "slot": str(self._silent_hour_index + 1),
                "current_schedule": self._format_silent_hour(current),
            },
        )

    async def async_step_silent_hour_delete(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Require explicit confirmation before deleting a populated slot."""

        assert self._silent_hour_index is not None
        slots = self._current_silent_hours()
        if slots is None:
            return self.async_abort(reason="silent_hours_unavailable")
        record = slots[self._silent_hour_index].record
        if record is None:
            return self.async_abort(reason="silent_hour_empty")
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input[CONF_CONFIRM_SILENT_HOUR_DELETE]:
                errors["base"] = "silent_hour_delete_confirmation_required"
            elif not self._silent_hours_unchanged(slots):
                return self.async_abort(reason="silent_hours_changed")
            elif self._silent_hour_operation_active:
                errors["base"] = "silent_hour_busy"
            else:
                self._silent_hour_operation_active = True
                try:
                    await self.config_entry.runtime_data.async_delete_silent_hour(
                        self._silent_hour_index
                    )
                except SilentHoursNotSupportedError:
                    return self.async_abort(reason="silent_hours_not_supported")
                except SilentHoursConfigurationUnavailableError:
                    errors["base"] = "silent_hours_unavailable"
                except HomeAssistantError as err:
                    _LOGGER.warning("Unable to delete Multihome silent hour: %s", err)
                    errors["base"] = "silent_hour_update_failed"
                else:
                    self._silent_hour_result = "Deleted"
                    return await self.async_step_silent_hour_result()
                finally:
                    self._silent_hour_operation_active = False

        return self.async_show_form(
            step_id="silent_hour_delete",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CONFIRM_SILENT_HOUR_DELETE, default=False
                    ): selector.BooleanSelector()
                }
            ),
            errors=errors,
            description_placeholders={
                "slot": str(self._silent_hour_index + 1),
                "current_schedule": self._format_silent_hour(record),
            },
        )

    async def async_step_silent_hour_result(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Report the schedule state confirmed by complete table readback."""

        if user_input is not None:
            return self.async_create_entry(
                title="", data=dict(self.config_entry.options)
            )
        assert self._silent_hour_index is not None
        return self.async_show_form(
            step_id="silent_hour_result",
            data_schema=vol.Schema({}),
            description_placeholders={
                "slot": str(self._silent_hour_index + 1),
                "result": self._silent_hour_result,
            },
        )

    def _current_silent_hours(self) -> tuple[SilentHourSlot, ...] | None:
        """Return one current complete writable schedule table."""

        coordinator = self.config_entry.runtime_data
        if (
            coordinator.data is None
            or not coordinator.last_update_success
            or not coordinator.device.silent_hours_write_ready
            or len(coordinator.data.silent_hours) != 6
        ):
            return None
        return coordinator.data.silent_hours

    def _silent_hours_unchanged(self, slots: tuple[SilentHourSlot, ...]) -> bool:
        """Return whether the table still matches the selection-screen baseline."""

        return self._silent_hours_baseline == self._silent_hours_fingerprint(slots)

    @staticmethod
    def _silent_hours_fingerprint(
        slots: tuple[SilentHourSlot, ...],
    ) -> tuple[tuple[int, bytes | None], ...]:
        """Return stable slot identities and records without packet metadata."""

        return tuple(
            (slot.index, slot.record.raw_record if slot.record is not None else None)
            for slot in slots
        )

    @staticmethod
    def _time_to_seconds(value: Any) -> int:
        """Convert an HA time selector value to seconds since midnight."""

        if isinstance(value, dt_time):
            return value.hour * 3600 + value.minute * 60 + value.second
        if not isinstance(value, str):
            raise ValueError("time must be an HH:MM[:SS] value")
        parts = value.split(":")
        if len(parts) not in (2, 3):
            raise ValueError("time must be HH:MM[:SS]")
        hour, minute = (int(part) for part in parts[:2])
        second = int(parts[2]) if len(parts) == 3 else 0
        if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
            raise ValueError("time is outside a day")
        return hour * 3600 + minute * 60 + second

    @staticmethod
    def _seconds_to_time(value: int) -> str:
        """Format seconds since midnight for the HA time selector."""

        hour, remainder = divmod(value, 3600)
        minute, second = divmod(remainder, 60)
        return f"{hour:02d}:{minute:02d}:{second:02d}"

    @staticmethod
    def _weekdays_to_mask(weekdays: list[str]) -> int:
        """Encode named weekdays as the recovered Monday-first bitmask."""

        if not weekdays or any(day not in WEEKDAYS for day in weekdays):
            raise ValueError("at least one known weekday is required")
        return sum(1 << WEEKDAYS.index(day) for day in set(weekdays))

    @staticmethod
    def _mask_to_weekdays(mask: int) -> list[str]:
        """Decode the Monday-first mask for an HA multi-select."""

        return [day for index, day in enumerate(WEEKDAYS) if mask & (1 << index)]

    @classmethod
    def _format_silent_hour(cls, record: SilentHour | None) -> str:
        """Return one readable schedule including overnight semantics."""

        if record is None:
            return "Empty"
        if not record.is_valid:
            return "Unknown firmware record (read-only)"
        days = ", ".join(
            day[:3].title() for day in cls._mask_to_weekdays(record.weekdays_mask)
        )
        overnight = " · overnight" if record.is_overnight else ""
        return (
            f"{cls._seconds_to_time(record.start_seconds)[:5]}–"
            f"{cls._seconds_to_time(record.end_seconds)[:5]} · {days}{overnight}"
        )

    @classmethod
    def _format_silent_hours(cls, slots: tuple[SilentHourSlot, ...]) -> str:
        """Return all six deterministic slot summaries."""

        return "\n".join(
            f"**Slot {slot.index + 1}:** {cls._format_silent_hour(slot.record)}"
            for slot in slots
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
            raw_entity_ids = [selected] if isinstance(selected, str) else list(selected)
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
                    _LOGGER.warning("Multihome CO2 calibration was not sent: %s", err)
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
                CO2_CALIBRATION_SAMPLING_DURATION / CO2_CALIBRATION_PROGRESS_INTERVAL
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

    def _read_reference_sensors(self, entity_ids: list[str]) -> tuple[int, str]:
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
                MIN_CO2_CALIBRATION_REFERENCE <= value <= MAX_CO2_CALIBRATION_REFERENCE
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
