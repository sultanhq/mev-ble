"""Home Assistant Bluetooth discovery and config-flow tests."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant import config_entries, data_entry_flow
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_UNIT_OF_MEASUREMENT,
    CONF_ADDRESS,
    UnitOfRatio,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ventaxia_multihome import config_flow as config_flow_module
from custom_components.ventaxia_multihome.config_flow import (
    CALIBRATION_METHOD_FRESH_AIR,
    CALIBRATION_METHOD_REFERENCE_SENSORS,
    CONF_AIRFLOW_BOOST,
    CONF_AIRFLOW_LOW,
    CONF_AIRFLOW_NORMAL,
    CONF_AIRFLOW_PURGE,
    CONF_AMBIENT_RESPONSE,
    CONF_BOOST_MINIMUM,
    CONF_CALIBRATION_METHOD,
    CONF_CO2_BOOST_THRESHOLD,
    CONF_CO2_PURGE_THRESHOLD,
    CONF_COMFORT_MODE,
    CONF_CONFIRM_AIRFLOW,
    CONF_CONFIRM_BOOST_MINIMUM,
    CONF_CONFIRM_CALIBRATION,
    CONF_CONFIRM_COMFORT_MODE,
    CONF_CONFIRM_DELAY_OVERRUN,
    CONF_CONFIRM_HUMIDITY_RESPONSE,
    CONF_CONFIRM_LOW_TEMPERATURE_PROTECTION,
    CONF_CONFIRM_SENSOR_THRESHOLDS,
    CONF_CONFIRM_SILENT_HOUR_DELETE,
    CONF_CONFIRM_TEMPERATURE_VALIDATION,
    CONF_DELAY_ENABLED,
    CONF_DELAY_TIMEOUT,
    CONF_HIGH_TEMPERATURE_ACTION,
    CONF_HIGH_TEMPERATURE_THRESHOLD,
    CONF_HUMIDITY_THRESHOLD,
    CONF_LOW_TEMPERATURE_ACTION,
    CONF_LOW_TEMPERATURE_PROTECTION,
    CONF_LOW_TEMPERATURE_THRESHOLD,
    CONF_OVERRUN_ENABLED,
    CONF_OVERRUN_TIMEOUT,
    CONF_RAPID_RESPONSE,
    CONF_REFERENCE_PPM,
    CONF_REFERENCE_SENSORS,
    CONF_SILENT_HOUR_ACTION,
    CONF_SILENT_HOUR_END,
    CONF_SILENT_HOUR_SLOT,
    CONF_SILENT_HOUR_START,
    CONF_SILENT_HOUR_WEEKDAYS,
    SILENT_HOUR_ACTION_DELETE,
    SILENT_HOUR_ACTION_EDIT,
)
from custom_components.ventaxia_multihome.const import (
    CONF_OVERRIDE_DURATION,
    CONF_SETUP_CODE,
    DOMAIN,
)
from custom_components.ventaxia_multihome.coordinator import (
    AirflowConfigurationUnavailableError,
    CalibrationCommandNotSentError,
    CalibrationDeliveryUncertainError,
    CalibrationRateLimitedError,
    SensorThresholdConfigurationUnavailableError,
)
from custom_components.ventaxia_multihome.device import SetupCodeRejectedError
from custom_components.ventaxia_multihome.protocol import (
    crc8_zirconia,
    decode_global_settings,
    decode_silent_hour_slot,
    encode_silent_hour,
)


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


def _options_entry(
    hass,
    *,
    supports_calibration: bool = True,
    supports_airflow: bool = False,
    supports_boost_minimum: bool = False,
    supports_thresholds: bool = False,
    supports_humidity_response: bool = False,
    supports_comfort_mode: bool = False,
    supports_delay_overrun: bool = False,
    supports_temperature_validation: bool = False,
    supports_low_temperature_protection: bool = False,
    airflow_available: bool = True,
    supports_schedules: bool = False,
    schedules_available: bool = True,
):
    """Create a loaded-looking entry without starting Bluetooth I/O."""

    settings = decode_global_settings(
        bytes.fromhex(
            "06082532005101000100000001040f19000a0a0103049600af000f4b01030f4b01030103"
        )
    )
    silent_hours = tuple(
        decode_silent_hour_slot(index.to_bytes(2, "little") + bytes(2) + bytes(9))
        for index in range(6)
    )
    coordinator = SimpleNamespace(
        device=SimpleNamespace(
            supports_internal_co2_calibration=supports_calibration,
            supports_global_airflow_configuration=supports_airflow,
            supports_boost_minimum_configuration=supports_boost_minimum,
            supports_sensor_threshold_configuration=supports_thresholds,
            supports_humidity_response_configuration=supports_humidity_response,
            supports_comfort_mode_configuration=supports_comfort_mode,
            supports_delay_overrun_configuration=supports_delay_overrun,
            supports_temperature_threshold_validation=(supports_temperature_validation),
            supports_low_temperature_protection_validation=(
                supports_low_temperature_protection
            ),
            global_settings_write_ready=airflow_available,
            supports_silent_hours_management=supports_schedules,
            silent_hours_write_ready=schedules_available,
        ),
        data=(
            SimpleNamespace(
                global_settings=settings,
                silent_hours=silent_hours,
                zone=SimpleNamespace(temperature=20.5),
            )
            if airflow_available and schedules_available
            else None
        ),
        last_update_success=airflow_available and schedules_available,
        async_calibrate_internal_co2=AsyncMock(),
        async_set_airflow_profile=AsyncMock(),
        async_set_boost_minimum=AsyncMock(),
        async_set_sensor_thresholds=AsyncMock(),
        async_set_humidity_response=AsyncMock(),
        async_set_comfort_mode=AsyncMock(),
        async_set_delay_overrun=AsyncMock(),
        async_set_temperature_threshold_validation=AsyncMock(),
        async_set_low_temperature_protection_validation=AsyncMock(),
        async_set_silent_hour=AsyncMock(),
        async_delete_silent_hour=AsyncMock(),
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Vent-Axia Multihome 78D0",
        data={
            CONF_ADDRESS: "70:B3:D5:68:78:D0",
            CONF_SETUP_CODE: 123456,
        },
        options={CONF_OVERRIDE_DURATION: 1800},
        unique_id="70b3d56878d0",
    )
    entry.runtime_data = coordinator
    entry.add_to_hass(hass)
    return entry, coordinator


async def _open_airflow_options(hass, entry):
    """Open the four-level airflow screen from the options menu."""

    initial = await hass.config_entries.options.async_init(entry.entry_id)
    assert initial["type"] is data_entry_flow.FlowResultType.MENU
    assert initial["step_id"] == "init"
    return await hass.config_entries.options.async_configure(
        initial["flow_id"], {"next_step_id": "airflow_profile"}
    )


async def _open_calibration_options(hass, entry):
    """Open the calibration method screen from the options menu."""

    initial = await hass.config_entries.options.async_init(entry.entry_id)
    assert initial["type"] is data_entry_flow.FlowResultType.MENU
    assert initial["step_id"] == "init"
    return await hass.config_entries.options.async_configure(
        initial["flow_id"], {"next_step_id": "calibrate_co2"}
    )


async def _open_sensor_threshold_options(hass, entry):
    """Open the guarded CO2/humidity threshold screen."""

    initial = await hass.config_entries.options.async_init(entry.entry_id)
    assert initial["type"] is data_entry_flow.FlowResultType.MENU
    assert initial["step_id"] == "init"
    return await hass.config_entries.options.async_configure(
        initial["flow_id"], {"next_step_id": "sensor_thresholds"}
    )


async def _open_humidity_response_options(hass, entry):
    """Open the guarded Rapid/Ambient humidity-response screen."""

    initial = await hass.config_entries.options.async_init(entry.entry_id)
    assert initial["type"] is data_entry_flow.FlowResultType.MENU
    assert initial["step_id"] == "init"
    return await hass.config_entries.options.async_configure(
        initial["flow_id"], {"next_step_id": "humidity_response"}
    )


async def _open_boost_minimum_options(hass, entry):
    """Open the guarded Boost minimum configuration screen."""

    initial = await hass.config_entries.options.async_init(entry.entry_id)
    assert initial["type"] is data_entry_flow.FlowResultType.MENU
    assert initial["step_id"] == "init"
    return await hass.config_entries.options.async_configure(
        initial["flow_id"], {"next_step_id": "boost_minimum"}
    )


async def _open_comfort_mode_options(hass, entry):
    """Open the guarded Comfort mode validation screen."""

    initial = await hass.config_entries.options.async_init(entry.entry_id)
    assert initial["type"] is data_entry_flow.FlowResultType.MENU
    assert initial["step_id"] == "init"
    return await hass.config_entries.options.async_configure(
        initial["flow_id"], {"next_step_id": "comfort_mode"}
    )


async def _open_delay_overrun_options(hass, entry):
    """Open the guarded paired LS timer validation screen."""

    initial = await hass.config_entries.options.async_init(entry.entry_id)
    assert initial["type"] is data_entry_flow.FlowResultType.MENU
    assert initial["step_id"] == "init"
    return await hass.config_entries.options.async_configure(
        initial["flow_id"], {"next_step_id": "delay_overrun"}
    )


async def _open_temperature_validation_options(hass, entry):
    """Open the one-field temperature validation screen."""

    initial = await hass.config_entries.options.async_init(entry.entry_id)
    assert initial["type"] is data_entry_flow.FlowResultType.MENU
    assert initial["step_id"] == "init"
    return await hass.config_entries.options.async_configure(
        initial["flow_id"], {"next_step_id": "temperature_validation"}
    )


async def _open_low_temperature_protection_options(hass, entry):
    """Open the guarded field-16 validation screen."""

    initial = await hass.config_entries.options.async_init(entry.entry_id)
    assert initial["type"] is data_entry_flow.FlowResultType.MENU
    assert initial["step_id"] == "init"
    return await hass.config_entries.options.async_configure(
        initial["flow_id"],
        {"next_step_id": "low_temperature_protection_validation"},
    )


async def _open_silent_hours_options(hass, entry):
    """Open the six-slot schedule management screen."""

    initial = await hass.config_entries.options.async_init(entry.entry_id)
    assert initial["type"] is data_entry_flow.FlowResultType.MENU
    assert initial["step_id"] == "init"
    return await hass.config_entries.options.async_configure(
        initial["flow_id"], {"next_step_id": "silent_hours"}
    )


@pytest.fixture
def fast_calibration_progress(monkeypatch):
    """Keep end-to-end progress-flow tests fast."""

    monkeypatch.setattr(config_flow_module, "CO2_CALIBRATION_SAMPLING_DURATION", 0.001)
    monkeypatch.setattr(config_flow_module, "CO2_CALIBRATION_PROGRESS_INTERVAL", 0.001)


async def _complete_calibration_progress(hass, progress):
    """Wait for a shortened progress task and return its result form."""

    assert progress["type"] is data_entry_flow.FlowResultType.SHOW_PROGRESS
    assert progress["step_id"] == "calibration_progress"
    flow = hass.config_entries.options._progress[progress["flow_id"]]
    if task := flow.async_get_progress_task():
        await task
    await hass.async_block_till_done()
    return hass.config_entries.options.async_get(progress["flow_id"])


@pytest.mark.asyncio
async def test_airflow_profile_menu_requires_supported_current_settings(hass) -> None:
    """Airflow commissioning appears only with a validated writable snapshot."""

    # Arrange - create separate supported, unsupported, and unavailable entries.
    supported, _ = _options_entry(hass, supports_airflow=True, airflow_available=True)
    unsupported, _ = _options_entry(
        hass, supports_airflow=False, airflow_available=True
    )
    unavailable, _ = _options_entry(
        hass, supports_airflow=True, airflow_available=False
    )

    # Act - open each entry's top-level options menu.
    supported_menu = await hass.config_entries.options.async_init(supported.entry_id)
    unsupported_menu = await hass.config_entries.options.async_init(
        unsupported.entry_id
    )
    unavailable_menu = await hass.config_entries.options.async_init(
        unavailable.entry_id
    )

    # Assert - only the proven model with current packet-137 data exposes writes.
    assert "airflow_profile" in supported_menu["menu_options"]
    assert "airflow_profile" not in unsupported_menu["menu_options"]
    assert "airflow_profile" not in unavailable_menu["menu_options"]


@pytest.mark.asyncio
async def test_airflow_profile_uses_documented_percent_ranges(hass) -> None:
    """The commissioning form labels motor percentages with exact limits."""

    # Arrange - open the airflow form with the user's confirmed current profile.
    entry, _ = _options_entry(hass, supports_airflow=True)

    # Act - inspect the rendered Home Assistant selector definitions.
    result = await _open_airflow_options(hass, entry)
    fields = {
        marker.schema: field for marker, field in result["data_schema"].schema.items()
    }

    # Assert - defaults, units, limits, and step match the official manual.
    assert result["description_placeholders"]["current_profile"] == (
        "Low 6% · Normal 8% · Boost 37% · Purge 50%"
    )
    expected = {
        CONF_AIRFLOW_LOW: (1, 97, 6),
        CONF_AIRFLOW_NORMAL: (2, 98, 8),
        CONF_AIRFLOW_BOOST: (3, 99, 37),
        CONF_AIRFLOW_PURGE: (4, 100, 50),
    }
    for name, (minimum, maximum, default) in expected.items():
        assert fields[name].config["min"] == minimum
        assert fields[name].config["max"] == maximum
        assert fields[name].config["step"] == 1
        assert fields[name].config["unit_of_measurement"] == "%"
        marker = next(
            item for item in result["data_schema"].schema if item.schema == name
        )
        assert marker.default() == default


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "profile",
    [
        (8, 8, 37, 50),
        (6.5, 8, 37, 50),
    ],
)
async def test_airflow_profile_rejects_invalid_values(hass, profile) -> None:
    """Unsafe ordering, range, and fractional values never reach Bluetooth."""

    # Arrange - open a writable airflow profile form.
    entry, coordinator = _options_entry(hass, supports_airflow=True)
    form = await _open_airflow_options(hass, entry)

    # Act - submit a profile that cannot be a valid commissioned profile.
    result = await hass.config_entries.options.async_configure(
        form["flow_id"],
        dict(
            zip(
                (
                    CONF_AIRFLOW_LOW,
                    CONF_AIRFLOW_NORMAL,
                    CONF_AIRFLOW_BOOST,
                    CONF_AIRFLOW_PURGE,
                ),
                profile,
                strict=True,
            )
        ),
    )

    # Assert - validation remains in the form and sends no packet-136 update.
    assert result["step_id"] == "airflow_profile"
    assert result["errors"] == {"base": "airflow_profile_invalid"}
    coordinator.async_set_airflow_profile.assert_not_awaited()


@pytest.mark.asyncio
async def test_airflow_selector_rejects_out_of_range_value(hass) -> None:
    """Home Assistant enforces the documented field bounds before the flow."""

    # Arrange - open the profile whose Low selector permits only 1..97 percent.
    entry, coordinator = _options_entry(hass, supports_airflow=True)
    form = await _open_airflow_options(hass, entry)

    # Act / Assert - HA's schema rejects zero before calling the write flow.
    with pytest.raises(data_entry_flow.InvalidData):
        await hass.config_entries.options.async_configure(
            form["flow_id"],
            {
                CONF_AIRFLOW_LOW: 0,
                CONF_AIRFLOW_NORMAL: 8,
                CONF_AIRFLOW_BOOST: 37,
                CONF_AIRFLOW_PURGE: 50,
            },
        )
    coordinator.async_set_airflow_profile.assert_not_awaited()


@pytest.mark.asyncio
async def test_airflow_profile_requires_review_and_exact_confirmation(hass) -> None:
    """A valid profile is reviewed and written only after positive confirmation."""

    # Arrange - open a supported profile and prepare a minimal Low change.
    entry, coordinator = _options_entry(hass, supports_airflow=True)
    form = await _open_airflow_options(hass, entry)

    # Act - submit 7/8/37/50, then decline and finally confirm the review.
    confirm = await hass.config_entries.options.async_configure(
        form["flow_id"],
        {
            CONF_AIRFLOW_LOW: 7,
            CONF_AIRFLOW_NORMAL: 8,
            CONF_AIRFLOW_BOOST: 37,
            CONF_AIRFLOW_PURGE: 50,
        },
    )

    # Assert - reaching review is not itself a write operation.
    assert confirm["step_id"] == "airflow_confirm"
    assert confirm["description_placeholders"]["new_profile"] == (
        "Low 7% · Normal 8% · Boost 37% · Purge 50%"
    )
    coordinator.async_set_airflow_profile.assert_not_awaited()

    # Act - refuse once, then explicitly authorize the exact reviewed profile.
    declined = await hass.config_entries.options.async_configure(
        confirm["flow_id"], {CONF_CONFIRM_AIRFLOW: False}
    )
    result = await hass.config_entries.options.async_configure(
        declined["flow_id"], {CONF_CONFIRM_AIRFLOW: True}
    )

    # Assert - only positive confirmation writes, then shows confirmed readback.
    assert declined["errors"] == {"base": "airflow_confirmation_required"}
    coordinator.async_set_airflow_profile.assert_awaited_once_with(
        low=7, normal=8, boost=37, purge=50
    )
    assert result["step_id"] == "airflow_result"
    completed = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert completed["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert completed["data"] == {CONF_OVERRIDE_DURATION: 1800}


@pytest.mark.asyncio
async def test_airflow_profile_rechecks_snapshot_at_confirmation(hass) -> None:
    """An independently changed settings record invalidates a stale review."""

    # Arrange - reach review with the user's original 6/8/37/50 record.
    entry, coordinator = _options_entry(hass, supports_airflow=True)
    form = await _open_airflow_options(hass, entry)
    confirm = await hass.config_entries.options.async_configure(
        form["flow_id"],
        {
            CONF_AIRFLOW_LOW: 7,
            CONF_AIRFLOW_NORMAL: 8,
            CONF_AIRFLOW_BOOST: 37,
            CONF_AIRFLOW_PURGE: 50,
        },
    )
    changed = bytearray(coordinator.data.global_settings.raw_record)
    changed[5] = 82
    coordinator.data = SimpleNamespace(
        global_settings=decode_global_settings(bytes(changed))
    )

    # Act - confirm after another client has changed the global record.
    result = await hass.config_entries.options.async_configure(
        confirm["flow_id"], {CONF_CONFIRM_AIRFLOW: True}
    )

    # Assert - HA returns to fresh inputs instead of overwriting stale state.
    assert result["step_id"] == "airflow_profile"
    assert result["errors"] == {"base": "airflow_settings_changed"}
    coordinator.async_set_airflow_profile.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected_error"),
    [
        (
            AirflowConfigurationUnavailableError("poll first"),
            "global_settings_unavailable",
        ),
        (HomeAssistantError("readback mismatch"), "airflow_update_failed"),
    ],
)
async def test_airflow_profile_failure_never_shows_success(
    hass, error, expected_error
) -> None:
    """Unavailable and failed writes remain on the review without false success."""

    # Arrange - reach final review with a coordinator that will reject the write.
    entry, coordinator = _options_entry(hass, supports_airflow=True)
    coordinator.async_set_airflow_profile.side_effect = error
    form = await _open_airflow_options(hass, entry)
    confirm = await hass.config_entries.options.async_configure(
        form["flow_id"],
        {
            CONF_AIRFLOW_LOW: 7,
            CONF_AIRFLOW_NORMAL: 8,
            CONF_AIRFLOW_BOOST: 37,
            CONF_AIRFLOW_PURGE: 50,
        },
    )

    # Act - authorize the command that cannot produce confirmed readback.
    failed = await hass.config_entries.options.async_configure(
        confirm["flow_id"], {CONF_CONFIRM_AIRFLOW: True}
    )

    # Assert - the review reports failure and does not create a result screen.
    assert failed["step_id"] == "airflow_confirm"
    assert failed["errors"] == {"base": expected_error}
    coordinator.async_set_airflow_profile.assert_awaited_once()


@pytest.mark.asyncio
async def test_sensor_threshold_menu_requires_supported_current_settings(hass) -> None:
    """The guarded threshold flow appears only with an exact current capability."""

    # Arrange - create supported, unsupported, and unavailable entries.
    supported, _ = _options_entry(hass, supports_thresholds=True)
    unsupported, _ = _options_entry(hass, supports_thresholds=False)
    unavailable, _ = _options_entry(
        hass, supports_thresholds=True, airflow_available=False
    )

    # Act - open each entry's top-level Configure menu.
    menus = [
        await hass.config_entries.options.async_init(entry.entry_id)
        for entry in (supported, unsupported, unavailable)
    ]

    # Assert - only the exact capability with fresh packet-137 data is exposed.
    assert "sensor_thresholds" in menus[0]["menu_options"]
    assert "sensor_thresholds" not in menus[1]["menu_options"]
    assert "sensor_thresholds" not in menus[2]["menu_options"]


@pytest.mark.asyncio
async def test_sensor_threshold_form_uses_recovered_ranges_and_current_values(
    hass,
) -> None:
    """Threshold selectors retain the packet codec's exact range and step."""

    # Arrange - open the form with the current 81/1500/1750 settings record.
    entry, _ = _options_entry(hass, supports_thresholds=True)

    # Act - inspect Home Assistant's rendered selector definitions.
    result = await _open_sensor_threshold_options(hass, entry)
    fields = {
        marker.schema: field for marker, field in result["data_schema"].schema.items()
    }

    # Assert - humidity uses percent and CO2 uses exact ten-ppm wire steps.
    expected = {
        CONF_HUMIDITY_THRESHOLD: (0, 100, 1, "%", 81),
        CONF_CO2_BOOST_THRESHOLD: (0, 2000, 10, "ppm", 1500),
        CONF_CO2_PURGE_THRESHOLD: (0, 2000, 10, "ppm", 1750),
    }
    for name, (minimum, maximum, step, unit, default) in expected.items():
        assert fields[name].config["min"] == minimum
        assert fields[name].config["max"] == maximum
        assert fields[name].config["step"] == step
        assert fields[name].config["unit_of_measurement"] == unit
        marker = next(
            item for item in result["data_schema"].schema if item.schema == name
        )
        assert marker.default() == default


@pytest.mark.asyncio
async def test_sensor_thresholds_require_review_and_confirmation(hass) -> None:
    """Valid thresholds are written only after explicit reviewed confirmation."""

    # Arrange - open the exact-identity threshold form.
    entry, coordinator = _options_entry(hass, supports_thresholds=True)
    form = await _open_sensor_threshold_options(hass, entry)

    # Act - submit a temporary validation profile, decline once, then confirm.
    confirm = await hass.config_entries.options.async_configure(
        form["flow_id"],
        {
            CONF_HUMIDITY_THRESHOLD: 80,
            CONF_CO2_BOOST_THRESHOLD: 1490,
            CONF_CO2_PURGE_THRESHOLD: 1740,
        },
    )
    declined = await hass.config_entries.options.async_configure(
        confirm["flow_id"], {CONF_CONFIRM_SENSOR_THRESHOLDS: False}
    )
    result = await hass.config_entries.options.async_configure(
        declined["flow_id"], {CONF_CONFIRM_SENSOR_THRESHOLDS: True}
    )

    # Assert - only positive confirmation calls the coordinator and shows success.
    assert confirm["step_id"] == "sensor_thresholds_confirm"
    assert declined["errors"] == {"base": "sensor_thresholds_confirmation_required"}
    coordinator.async_set_sensor_thresholds.assert_awaited_once_with(
        humidity=80, co2_boost=1490, co2_purge=1740
    )
    assert result["step_id"] == "sensor_thresholds_result"


@pytest.mark.asyncio
async def test_sensor_thresholds_recheck_complete_snapshot_before_write(hass) -> None:
    """Any concurrent global-setting change invalidates the reviewed profile."""

    # Arrange - reach confirmation, then mutate an unrelated global setting.
    entry, coordinator = _options_entry(hass, supports_thresholds=True)
    form = await _open_sensor_threshold_options(hass, entry)
    confirm = await hass.config_entries.options.async_configure(
        form["flow_id"],
        {
            CONF_HUMIDITY_THRESHOLD: 80,
            CONF_CO2_BOOST_THRESHOLD: 1490,
            CONF_CO2_PURGE_THRESHOLD: 1740,
        },
    )
    changed = bytearray(coordinator.data.global_settings.raw_record)
    changed[4] += 1
    coordinator.data = SimpleNamespace(
        global_settings=decode_global_settings(bytes(changed))
    )

    # Act - authorize the now-stale review.
    result = await hass.config_entries.options.async_configure(
        confirm["flow_id"], {CONF_CONFIRM_SENSOR_THRESHOLDS: True}
    )

    # Assert - return to current inputs without issuing any write.
    assert result["step_id"] == "sensor_thresholds"
    assert result["errors"] == {"base": "sensor_thresholds_settings_changed"}
    coordinator.async_set_sensor_thresholds.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected_error"),
    [
        (
            SensorThresholdConfigurationUnavailableError("poll first"),
            "global_settings_unavailable",
        ),
        (HomeAssistantError("readback mismatch"), "sensor_thresholds_update_failed"),
    ],
)
async def test_sensor_threshold_failure_never_shows_success(
    hass, error, expected_error
) -> None:
    """Unavailable or mismatched writes stay on review with explicit failure."""

    # Arrange - reach review with a coordinator that will reject the operation.
    entry, coordinator = _options_entry(hass, supports_thresholds=True)
    coordinator.async_set_sensor_thresholds.side_effect = error
    form = await _open_sensor_threshold_options(hass, entry)
    confirm = await hass.config_entries.options.async_configure(
        form["flow_id"],
        {
            CONF_HUMIDITY_THRESHOLD: 80,
            CONF_CO2_BOOST_THRESHOLD: 1490,
            CONF_CO2_PURGE_THRESHOLD: 1740,
        },
    )

    # Act - authorize a command that cannot be confirmed.
    failed = await hass.config_entries.options.async_configure(
        confirm["flow_id"], {CONF_CONFIRM_SENSOR_THRESHOLDS: True}
    )

    # Assert - no result screen is shown and the failure is explicit.
    assert failed["step_id"] == "sensor_thresholds_confirm"
    assert failed["errors"] == {"base": expected_error}
    coordinator.async_set_sensor_thresholds.assert_awaited_once()


@pytest.mark.asyncio
async def test_humidity_response_requires_review_and_confirmation(hass) -> None:
    """Candidate response flags are written only after reviewed confirmation."""

    # Arrange - open the exact-identity candidate flow at Rapid off/Ambient on.
    entry, coordinator = _options_entry(hass, supports_humidity_response=True)
    form = await _open_humidity_response_options(hass, entry)

    # Act - request both reversed flags, decline once, then confirm explicitly.
    confirm = await hass.config_entries.options.async_configure(
        form["flow_id"],
        {CONF_RAPID_RESPONSE: True, CONF_AMBIENT_RESPONSE: False},
    )
    declined = await hass.config_entries.options.async_configure(
        confirm["flow_id"], {CONF_CONFIRM_HUMIDITY_RESPONSE: False}
    )
    result = await hass.config_entries.options.async_configure(
        declined["flow_id"], {CONF_CONFIRM_HUMIDITY_RESPONSE: True}
    )

    # Assert - no write precedes confirmation and the readback result is shown.
    assert form["step_id"] == "humidity_response"
    assert confirm["step_id"] == "humidity_response_confirm"
    assert declined["errors"] == {"base": "humidity_response_confirmation_required"}
    coordinator.async_set_humidity_response.assert_awaited_once_with(
        rapid=True, ambient=False
    )
    assert result["step_id"] == "humidity_response_result"


@pytest.mark.asyncio
async def test_humidity_response_rechecks_full_snapshot_before_write(hass) -> None:
    """Any intervening packet-137 change invalidates the reviewed profile."""

    # Arrange - open and review a changed response profile.
    entry, coordinator = _options_entry(hass, supports_humidity_response=True)
    form = await _open_humidity_response_options(hass, entry)
    confirm = await hass.config_entries.options.async_configure(
        form["flow_id"],
        {CONF_RAPID_RESPONSE: True, CONF_AMBIENT_RESPONSE: False},
    )
    changed = bytearray(coordinator.data.global_settings.raw_record)
    changed[4] += 1
    coordinator.data.global_settings = decode_global_settings(bytes(changed))

    # Act - confirm after an unrelated setting changed.
    result = await hass.config_entries.options.async_configure(
        confirm["flow_id"], {CONF_CONFIRM_HUMIDITY_RESPONSE: True}
    )

    # Assert - the complete snapshot guard aborts before Bluetooth I/O.
    assert result["step_id"] == "humidity_response"
    assert result["errors"] == {"base": "humidity_response_settings_changed"}
    coordinator.async_set_humidity_response.assert_not_awaited()


@pytest.mark.asyncio
async def test_boost_minimum_requires_review_and_confirmation(hass) -> None:
    """A field-4 value is written only after explicit confirmation."""

    # Arrange - open the exact-identity flow with the observed 0% baseline.
    entry, coordinator = _options_entry(hass, supports_boost_minimum=True)
    form = await _open_boost_minimum_options(hass, entry)

    # Act - request a general in-range value, decline once, then confirm explicitly.
    confirm = await hass.config_entries.options.async_configure(
        form["flow_id"], {CONF_BOOST_MINIMUM: 50}
    )
    declined = await hass.config_entries.options.async_configure(
        confirm["flow_id"], {CONF_CONFIRM_BOOST_MINIMUM: False}
    )
    result = await hass.config_entries.options.async_configure(
        declined["flow_id"], {CONF_CONFIRM_BOOST_MINIMUM: True}
    )

    # Assert - no write precedes confirmation and the result is explicit.
    assert form["step_id"] == "boost_minimum"
    assert confirm["step_id"] == "boost_minimum_confirm"
    assert declined["errors"] == {"base": "boost_minimum_confirmation_required"}
    coordinator.async_set_boost_minimum.assert_awaited_once_with(value=50)
    assert result["step_id"] == "boost_minimum_result"


@pytest.mark.asyncio
async def test_boost_minimum_rechecks_full_snapshot_before_write(hass) -> None:
    """Any intervening packet-137 change invalidates the field-4 review."""

    # Arrange - review a guarded 0% to 50% change.
    entry, coordinator = _options_entry(hass, supports_boost_minimum=True)
    form = await _open_boost_minimum_options(hass, entry)
    confirm = await hass.config_entries.options.async_configure(
        form["flow_id"], {CONF_BOOST_MINIMUM: 50}
    )
    changed = bytearray(coordinator.data.global_settings.raw_record)
    changed[5] += 1
    coordinator.data.global_settings = decode_global_settings(bytes(changed))

    # Act - confirm after an unrelated installer setting changed.
    result = await hass.config_entries.options.async_configure(
        confirm["flow_id"], {CONF_CONFIRM_BOOST_MINIMUM: True}
    )

    # Assert - the complete snapshot guard blocks Bluetooth I/O.
    assert result["step_id"] == "boost_minimum"
    assert result["errors"] == {"base": "boost_minimum_settings_changed"}
    coordinator.async_set_boost_minimum.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [-1, 101])
async def test_boost_minimum_rejects_out_of_range_percentage(hass, value: int) -> None:
    """The Home Assistant selector rejects values outside the recovered bounds."""

    # Arrange - open the exact-identity guarded configuration screen.
    entry, coordinator = _options_entry(hass, supports_boost_minimum=True)
    form = await _open_boost_minimum_options(hass, entry)

    # Act - submit a value outside the recovered 0% to 100% wire range.
    with pytest.raises(data_entry_flow.InvalidData) as raised:
        await hass.config_entries.options.async_configure(
            form["flow_id"], {CONF_BOOST_MINIMUM: value}
        )

    # Assert - schema validation rejects it before coordinator or Bluetooth I/O.
    assert raised.value
    coordinator.async_set_boost_minimum.assert_not_awaited()


@pytest.mark.asyncio
async def test_boost_minimum_rejects_unchanged_value(hass) -> None:
    """The guarded flow requires a real field-4 change."""

    # Arrange - open the flow with the installed unit's 0% baseline.
    entry, coordinator = _options_entry(hass, supports_boost_minimum=True)
    form = await _open_boost_minimum_options(hass, entry)

    # Act - submit the unchanged current value.
    result = await hass.config_entries.options.async_configure(
        form["flow_id"], {CONF_BOOST_MINIMUM: 0}
    )

    # Assert - the flow stays on review and no write reaches the coordinator.
    assert result["step_id"] == "boost_minimum"
    assert result["errors"] == {"base": "boost_minimum_unchanged"}
    coordinator.async_set_boost_minimum.assert_not_awaited()


@pytest.mark.asyncio
async def test_temperature_validation_requires_review_and_one_change(hass) -> None:
    """One temperature field is written only after explicit confirmation."""

    # Arrange - open the exact-identity flow with the RC16 hardware baseline.
    entry, coordinator = _options_entry(hass, supports_temperature_validation=True)
    form = await _open_temperature_validation_options(hass, entry)
    profile = {
        CONF_LOW_TEMPERATURE_ACTION: "1",
        CONF_HIGH_TEMPERATURE_ACTION: "4",
        CONF_LOW_TEMPERATURE_THRESHOLD: 14,
        CONF_HIGH_TEMPERATURE_THRESHOLD: 25,
    }

    # Act - request one threshold change, decline, then explicitly confirm.
    confirm = await hass.config_entries.options.async_configure(
        form["flow_id"], profile
    )
    declined = await hass.config_entries.options.async_configure(
        confirm["flow_id"], {CONF_CONFIRM_TEMPERATURE_VALIDATION: False}
    )
    result = await hass.config_entries.options.async_configure(
        declined["flow_id"], {CONF_CONFIRM_TEMPERATURE_VALIDATION: True}
    )

    # Assert - the complete profile is reviewed but only one write is requested.
    assert form["step_id"] == "temperature_validation"
    assert form["description_placeholders"]["current_temperature"] == "20.5 °C"
    assert confirm["step_id"] == "temperature_validation_confirm"
    assert declined["errors"] == {
        "base": "temperature_validation_confirmation_required"
    }
    coordinator.async_set_temperature_threshold_validation.assert_awaited_once_with(
        low_action=1,
        high_action=4,
        low_threshold=14,
        high_threshold=25,
    )
    assert result["step_id"] == "temperature_validation_result"


@pytest.mark.asyncio
async def test_temperature_validation_rejects_multiple_changes(hass) -> None:
    """The validation flow cannot submit two temperature fields at once."""

    # Arrange - open the exact-identity one-field validation screen.
    entry, coordinator = _options_entry(hass, supports_temperature_validation=True)
    form = await _open_temperature_validation_options(hass, entry)

    # Act - change both the low action and low threshold in one submission.
    result = await hass.config_entries.options.async_configure(
        form["flow_id"],
        {
            CONF_LOW_TEMPERATURE_ACTION: "3",
            CONF_HIGH_TEMPERATURE_ACTION: "4",
            CONF_LOW_TEMPERATURE_THRESHOLD: 14,
            CONF_HIGH_TEMPERATURE_THRESHOLD: 25,
        },
    )

    # Assert - the form stays open and no coordinator write is requested.
    assert result["step_id"] == "temperature_validation"
    assert result["errors"] == {"base": "temperature_validation_invalid"}
    coordinator.async_set_temperature_threshold_validation.assert_not_awaited()


@pytest.mark.asyncio
async def test_temperature_validation_rechecks_full_snapshot(hass) -> None:
    """Any intervening packet-137 change invalidates the reviewed field."""

    # Arrange - review one low-threshold change from the RC16 baseline.
    entry, coordinator = _options_entry(hass, supports_temperature_validation=True)
    form = await _open_temperature_validation_options(hass, entry)
    confirm = await hass.config_entries.options.async_configure(
        form["flow_id"],
        {
            CONF_LOW_TEMPERATURE_ACTION: "1",
            CONF_HIGH_TEMPERATURE_ACTION: "4",
            CONF_LOW_TEMPERATURE_THRESHOLD: 14,
            CONF_HIGH_TEMPERATURE_THRESHOLD: 25,
        },
    )
    changed = bytearray(coordinator.data.global_settings.raw_record)
    changed[5] += 1
    coordinator.data.global_settings = decode_global_settings(bytes(changed))

    # Act - confirm after an unrelated installer setting changed.
    result = await hass.config_entries.options.async_configure(
        confirm["flow_id"], {CONF_CONFIRM_TEMPERATURE_VALIDATION: True}
    )

    # Assert - the stale review is discarded before coordinator or Bluetooth I/O.
    assert result["step_id"] == "temperature_validation"
    assert result["errors"] == {"base": "temperature_validation_settings_changed"}
    coordinator.async_set_temperature_threshold_validation.assert_not_awaited()


@pytest.mark.asyncio
async def test_temperature_validation_requires_protection_off(hass) -> None:
    """An active or unknown field 16 prevents the validation menu entry."""

    # Arrange - expose the exact candidate identity but mark field 16 enabled.
    entry, coordinator = _options_entry(hass, supports_temperature_validation=True)
    changed = bytearray(coordinator.data.global_settings.raw_record)
    changed[11] = 1
    coordinator.data.global_settings = decode_global_settings(bytes(changed))

    # Act - open the options menu without selecting any action.
    result = await hass.config_entries.options.async_init(entry.entry_id)

    # Assert - no temperature write route is offered while protection is active.
    assert result["type"] is data_entry_flow.FlowResultType.MENU
    assert "temperature_validation" not in result["menu_options"]
    coordinator.async_set_temperature_threshold_validation.assert_not_awaited()


@pytest.mark.asyncio
async def test_low_temperature_protection_requires_review_and_confirmation(
    hass,
) -> None:
    """Field 16 is written only after the complete profile is reviewed."""

    # Arrange - open the exact-identity flow with protection currently disabled.
    entry, coordinator = _options_entry(
        hass, supports_low_temperature_protection=True
    )
    form = await _open_low_temperature_protection_options(hass, entry)

    # Act - request enabled, decline once, then explicitly confirm.
    confirm = await hass.config_entries.options.async_configure(
        form["flow_id"], {CONF_LOW_TEMPERATURE_PROTECTION: True}
    )
    declined = await hass.config_entries.options.async_configure(
        confirm["flow_id"], {CONF_CONFIRM_LOW_TEMPERATURE_PROTECTION: False}
    )
    result = await hass.config_entries.options.async_configure(
        declined["flow_id"], {CONF_CONFIRM_LOW_TEMPERATURE_PROTECTION: True}
    )

    # Assert - the warning includes live context and exactly one write is requested.
    assert form["step_id"] == "low_temperature_protection_validation"
    assert form["description_placeholders"] == {
        "current_protection": "Disabled",
        "current_profile": (
            "Low action Low · High action Purge · Low 15 °C · High 25 °C"
        ),
        "current_temperature": "20.5 °C",
    }
    assert confirm["step_id"] == (
        "low_temperature_protection_validation_confirm"
    )
    assert declined["errors"] == {
        "base": (
            "low_temperature_protection_validation_confirmation_required"
        )
    }
    coordinator.async_set_low_temperature_protection_validation.assert_awaited_once_with(
        enabled=True
    )
    assert result["step_id"] == "low_temperature_protection_validation_result"


@pytest.mark.asyncio
async def test_low_temperature_protection_rechecks_full_snapshot(hass) -> None:
    """Any intervening packet-137 change invalidates the field-16 review."""

    # Arrange - review an enable request from a complete current baseline.
    entry, coordinator = _options_entry(
        hass, supports_low_temperature_protection=True
    )
    form = await _open_low_temperature_protection_options(hass, entry)
    confirm = await hass.config_entries.options.async_configure(
        form["flow_id"], {CONF_LOW_TEMPERATURE_PROTECTION: True}
    )
    changed = bytearray(coordinator.data.global_settings.raw_record)
    changed[5] += 1
    coordinator.data.global_settings = decode_global_settings(bytes(changed))

    # Act - confirm after an unrelated installer setting changed.
    result = await hass.config_entries.options.async_configure(
        confirm["flow_id"], {CONF_CONFIRM_LOW_TEMPERATURE_PROTECTION: True}
    )

    # Assert - the stale review is discarded before coordinator or Bluetooth I/O.
    assert result["step_id"] == "low_temperature_protection_validation"
    assert result["errors"] == {
        "base": "low_temperature_protection_validation_settings_changed"
    }
    coordinator.async_set_low_temperature_protection_validation.assert_not_awaited()


@pytest.mark.asyncio
async def test_comfort_mode_requires_review_and_confirmation(hass) -> None:
    """The candidate Comfort flag is written only after confirmation."""

    # Arrange - open the exact-identity flow with Comfort currently enabled.
    entry, coordinator = _options_entry(hass, supports_comfort_mode=True)
    form = await _open_comfort_mode_options(hass, entry)

    # Act - request disabled, decline once, then confirm explicitly.
    confirm = await hass.config_entries.options.async_configure(
        form["flow_id"], {CONF_COMFORT_MODE: False}
    )
    declined = await hass.config_entries.options.async_configure(
        confirm["flow_id"], {CONF_CONFIRM_COMFORT_MODE: False}
    )
    result = await hass.config_entries.options.async_configure(
        declined["flow_id"], {CONF_CONFIRM_COMFORT_MODE: True}
    )

    # Assert - no write precedes confirmation and the result is explicit.
    assert form["step_id"] == "comfort_mode"
    assert confirm["step_id"] == "comfort_mode_confirm"
    assert declined["errors"] == {"base": "comfort_mode_confirmation_required"}
    coordinator.async_set_comfort_mode.assert_awaited_once_with(enabled=False)
    assert result["step_id"] == "comfort_mode_result"


@pytest.mark.asyncio
async def test_comfort_mode_rechecks_full_snapshot_before_write(hass) -> None:
    """Any intervening packet-137 change invalidates the Comfort review."""

    # Arrange - open and review a changed Comfort value.
    entry, coordinator = _options_entry(hass, supports_comfort_mode=True)
    form = await _open_comfort_mode_options(hass, entry)
    confirm = await hass.config_entries.options.async_configure(
        form["flow_id"], {CONF_COMFORT_MODE: False}
    )
    changed = bytearray(coordinator.data.global_settings.raw_record)
    changed[4] += 1
    coordinator.data.global_settings = decode_global_settings(bytes(changed))

    # Act - confirm after an unrelated setting changed.
    result = await hass.config_entries.options.async_configure(
        confirm["flow_id"], {CONF_CONFIRM_COMFORT_MODE: True}
    )

    # Assert - the complete snapshot guard blocks Bluetooth I/O.
    assert result["step_id"] == "comfort_mode"
    assert result["errors"] == {"base": "comfort_mode_settings_changed"}
    coordinator.async_set_comfort_mode.assert_not_awaited()


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
        result = await hass.config_entries.flow.async_configure(initial["flow_id"], {})

    # Assert - setup stays open with a useful retry error.
    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "no_devices_found"}
    request_active_scan.assert_awaited_once()


@pytest.mark.asyncio
async def test_user_flow_pairs_after_finding_one_device(hass) -> None:
    """A successful manual scan completes pairing without a PIN field."""

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
        result = await hass.config_entries.flow.async_configure(initial["flow_id"], {})

    # Assert - pairing stores the internal code without showing it to the user.
    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SETUP_CODE] == 123456


@pytest.mark.asyncio
async def test_bluetooth_discovery_and_setup(hass) -> None:
    """Bluetooth discovery shows pairing help before automatic setup."""

    # Arrange - expose a connectable HA-managed BLEDevice and internal code.
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

    # Assert - discovery explains pairing and stores the internal code.
    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "pair"
    assert configured["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert configured["data"][CONF_SETUP_CODE] == 123456


@pytest.mark.asyncio
async def test_automatic_pairing_failure(hass) -> None:
    """A unit outside pairing mode returns a useful retry form."""

    # Arrange - make automatic pairing fail to retrieve an internal code.
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
        rejected = await hass.config_entries.flow.async_configure(result["flow_id"], {})

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


@pytest.mark.asyncio
async def test_fresh_air_calibration_requires_final_confirmation(
    hass, fast_calibration_progress
) -> None:
    """Fresh-air calibration cannot write until the final guarded step."""

    # Arrange - open calibration for a validated internal-CO2 model.
    entry, coordinator = _options_entry(hass)
    method = await _open_calibration_options(hass, entry)
    assert method["step_id"] == "calibrate_co2"

    # Act - choose fresh-air exposure and acknowledge the preparation screen.
    exposure = await hass.config_entries.options.async_configure(
        method["flow_id"],
        {CONF_CALIBRATION_METHOD: CALIBRATION_METHOD_FRESH_AIR},
    )
    confirm = await hass.config_entries.options.async_configure(
        exposure["flow_id"], {CONF_REFERENCE_PPM: 450}
    )

    # Assert - the flow uses the official app's 450 ppm default, but has not written.
    assert exposure["step_id"] == "calibration_exposure"
    assert confirm["step_id"] == "calibration_confirm"
    assert confirm["description_placeholders"]["reference_ppm"] == "450"
    assert (
        "official app defaults to 450"
        in (confirm["description_placeholders"]["reference_summary"])
    )
    coordinator.async_calibrate_internal_co2.assert_not_awaited()

    # Act - explicitly decline, then explicitly confirm the irreversible action.
    declined = await hass.config_entries.options.async_configure(
        confirm["flow_id"], {CONF_CONFIRM_CALIBRATION: False}
    )
    progress = await hass.config_entries.options.async_configure(
        declined["flow_id"], {CONF_CONFIRM_CALIBRATION: True}
    )

    # Assert - only the positive confirmation sends one 450 ppm command and
    # starts HA's documented-duration progress screen.
    assert declined["errors"] == {"base": "confirmation_required"}
    assert progress["step_id"] == "calibration_progress"
    coordinator.async_calibrate_internal_co2.assert_awaited_once_with(450)

    result = await _complete_calibration_progress(hass, progress)
    assert result["step_id"] == "calibration_result"
    completed = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert completed["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert completed["data"] == {CONF_OVERRIDE_DURATION: 1800}


@pytest.mark.asyncio
async def test_manual_calibration_value_is_forwarded(hass) -> None:
    """The Vent-Axia-style numeric field accepts a custom trusted ppm value."""

    # Arrange - open the fresh-air/manual-value calibration path.
    entry, coordinator = _options_entry(hass)
    method = await _open_calibration_options(hass, entry)
    exposure = await hass.config_entries.options.async_configure(
        method["flow_id"],
        {CONF_CALIBRATION_METHOD: CALIBRATION_METHOD_FRESH_AIR},
    )

    # Act - enter a calibrated instrument reading instead of the 450 default.
    confirm = await hass.config_entries.options.async_configure(
        exposure["flow_id"], {CONF_REFERENCE_PPM: 475}
    )
    await hass.config_entries.options.async_configure(
        confirm["flow_id"], {CONF_CONFIRM_CALIBRATION: True}
    )

    # Assert - the exact user-entered reference reaches the guarded device API.
    assert confirm["description_placeholders"]["reference_ppm"] == "475"
    coordinator.async_calibrate_internal_co2.assert_awaited_once_with(475)


@pytest.mark.asyncio
async def test_reference_calibration_rereads_and_averages_sensors(
    hass, fast_calibration_progress
) -> None:
    """The final write uses a fresh average of independent ppm sensors."""

    # Arrange - expose two independent, plausible CO2 reference states.
    entry, coordinator = _options_entry(hass)
    attributes = {
        ATTR_DEVICE_CLASS: SensorDeviceClass.CO2,
        ATTR_UNIT_OF_MEASUREMENT: UnitOfRatio.PARTS_PER_MILLION,
    }
    hass.states.async_set("sensor.study_co2", "420", attributes)
    hass.states.async_set("sensor.bedroom_co2", "440", attributes)
    method = await _open_calibration_options(hass, entry)

    # Act - select the advanced method and both independent references.
    references = await hass.config_entries.options.async_configure(
        method["flow_id"],
        {CONF_CALIBRATION_METHOD: CALIBRATION_METHOD_REFERENCE_SENSORS},
    )
    confirm = await hass.config_entries.options.async_configure(
        references["flow_id"],
        {
            CONF_REFERENCE_SENSORS: [
                "sensor.study_co2",
                "sensor.bedroom_co2",
            ]
        },
    )

    # Assert - the review initially shows the mean, with no protocol write.
    assert confirm["description_placeholders"]["reference_ppm"] == "430"
    coordinator.async_calibrate_internal_co2.assert_not_awaited()

    # Act - change one sensor after review; confirmation must re-read both.
    hass.states.async_set("sensor.bedroom_co2", "460", attributes)
    progress = await hass.config_entries.options.async_configure(
        confirm["flow_id"], {CONF_CONFIRM_CALIBRATION: True}
    )

    # Assert - the command uses the current mean, (420 + 460) / 2 = 440 ppm.
    assert progress["step_id"] == "calibration_progress"
    coordinator.async_calibrate_internal_co2.assert_awaited_once_with(440)

    result = await _complete_calibration_progress(hass, progress)
    assert result["step_id"] == "calibration_result"


@pytest.mark.asyncio
async def test_single_reference_calibration_is_supported_with_warning(
    hass, fast_calibration_progress
) -> None:
    """One trusted reference works but the review states its placement duty."""

    # Arrange - expose one independent true-CO2 reference.
    entry, coordinator = _options_entry(hass)
    hass.states.async_set(
        "sensor.reference_co2",
        "450",
        {
            ATTR_DEVICE_CLASS: SensorDeviceClass.CO2,
            ATTR_UNIT_OF_MEASUREMENT: UnitOfRatio.PARTS_PER_MILLION,
        },
    )
    method = await _open_calibration_options(hass, entry)
    references = await hass.config_entries.options.async_configure(
        method["flow_id"],
        {CONF_CALIBRATION_METHOD: CALIBRATION_METHOD_REFERENCE_SENSORS},
    )

    # Act - select and confirm the one reference.
    confirm = await hass.config_entries.options.async_configure(
        references["flow_id"],
        {CONF_REFERENCE_SENSORS: ["sensor.reference_co2"]},
    )
    progress = await hass.config_entries.options.async_configure(
        confirm["flow_id"], {CONF_CONFIRM_CALIBRATION: True}
    )

    # Assert - placement warning is visible and exactly 450 ppm is sent once.
    assert (
        "must represent the air entering"
        in (confirm["description_placeholders"]["reference_summary"])
    )
    assert progress["step_id"] == "calibration_progress"
    coordinator.async_calibrate_internal_co2.assert_awaited_once_with(450)

    result = await _complete_calibration_progress(hass, progress)
    assert result["step_id"] == "calibration_result"


@pytest.mark.asyncio
async def test_calibration_progress_tracks_documented_180_seconds() -> None:
    """Progress advances once per second for the manual's three-minute period."""

    # Arrange - isolate the timer and replace real sleeping with an awaitable mock.
    flow = object.__new__(config_flow_module.VentaxiaMultihomeOptionsFlow)
    flow.async_update_progress = Mock()
    with patch(
        "custom_components.ventaxia_multihome.config_flow.asyncio.sleep",
        new=AsyncMock(),
    ) as sleep:
        # Act - track one complete internal-sensor sampling period.
        await flow._async_track_calibration_progress()

    # Assert - the documented 180 seconds reaches exact 100% progress.
    assert sleep.await_count == 180
    flow.async_update_progress.assert_any_call(1 / 180)
    flow.async_update_progress.assert_called_with(1.0)


def test_calibration_progress_copy_is_safe_when_client_stays_at_100() -> None:
    """A client stuck on the progress step still gives correct completion guidance."""

    # Arrange - load the source and shipped English option-flow translations.
    integration_dir = Path("custom_components/ventaxia_multihome")
    source = json.loads((integration_dir / "strings.json").read_text())
    english = json.loads((integration_dir / "translations/en.json").read_text())

    # Act - read the progress page and progress-action copy from both files.
    source_step = source["options"]["step"]["calibration_progress"]
    english_step = english["options"]["step"]["calibration_progress"]
    source_action = source["options"]["progress"]["calibration_sampling"]
    english_action = english["options"]["progress"]["calibration_sampling"]

    # Assert - 100% is explicitly complete and safe to close on every client.
    assert source_step == english_step
    assert source_action == english_action
    assert "At 100%" in source_step["description"]
    assert "close this screen with X" in source_step["description"]
    assert "sampling period has elapsed" in source_action


@pytest.mark.asyncio
async def test_reference_calibration_rejects_own_co2_entity(hass) -> None:
    """The integration's measured CO2 cannot calibrate itself."""

    # Arrange - register a CO2 sensor owned by this config entry.
    entry, coordinator = _options_entry(hass)
    registry = er.async_get(hass)
    entity = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "internal_co2",
        config_entry=entry,
        suggested_object_id="internal_co2",
    )
    hass.states.async_set(
        entity.entity_id,
        "500",
        {
            ATTR_DEVICE_CLASS: SensorDeviceClass.CO2,
            ATTR_UNIT_OF_MEASUREMENT: UnitOfRatio.PARTS_PER_MILLION,
        },
    )
    method = await _open_calibration_options(hass, entry)
    references = await hass.config_entries.options.async_configure(
        method["flow_id"],
        {CONF_CALIBRATION_METHOD: CALIBRATION_METHOD_REFERENCE_SENSORS},
    )

    # Act - attempt to select the integration's own measured value.
    rejected = await hass.config_entries.options.async_configure(
        references["flow_id"], {CONF_REFERENCE_SENSORS: [entity.entity_id]}
    )

    # Assert - circular calibration is blocked before confirmation or BLE I/O.
    assert rejected["step_id"] == "calibration_reference"
    assert rejected["errors"] == {"base": "self_reference"}
    coordinator.async_calibrate_internal_co2.assert_not_awaited()


@pytest.mark.asyncio
async def test_reference_is_rejected_if_unavailable_at_confirmation(hass) -> None:
    """A stale review cannot send after its reference becomes unavailable."""

    # Arrange - select a valid independent reference and reach final review.
    entry, coordinator = _options_entry(hass)
    attributes = {
        ATTR_DEVICE_CLASS: SensorDeviceClass.CO2,
        ATTR_UNIT_OF_MEASUREMENT: UnitOfRatio.PARTS_PER_MILLION,
    }
    hass.states.async_set("sensor.reference_co2", "450", attributes)
    method = await _open_calibration_options(hass, entry)
    references = await hass.config_entries.options.async_configure(
        method["flow_id"],
        {CONF_CALIBRATION_METHOD: CALIBRATION_METHOD_REFERENCE_SENSORS},
    )
    confirm = await hass.config_entries.options.async_configure(
        references["flow_id"],
        {CONF_REFERENCE_SENSORS: ["sensor.reference_co2"]},
    )

    # Act - lose the sensor immediately before positive confirmation.
    hass.states.async_set("sensor.reference_co2", "unavailable", attributes)
    rejected = await hass.config_entries.options.async_configure(
        confirm["flow_id"], {CONF_CONFIRM_CALIBRATION: True}
    )

    # Assert - return to selection and do not send a stale 450 ppm command.
    assert rejected["step_id"] == "calibration_reference"
    assert rejected["errors"] == {"base": "reference_unavailable"}
    coordinator.async_calibrate_internal_co2.assert_not_awaited()


@pytest.mark.asyncio
async def test_aborting_before_confirmation_never_sends_calibration(hass) -> None:
    """Closing the flow during preparation performs no device operation."""

    # Arrange - reach the fresh-air preparation screen only.
    entry, coordinator = _options_entry(hass)
    method = await _open_calibration_options(hass, entry)
    exposure = await hass.config_entries.options.async_configure(
        method["flow_id"],
        {CONF_CALIBRATION_METHOD: CALIBRATION_METHOD_FRESH_AIR},
    )

    # Act - simulate closing/cancelling the flow before review.
    hass.config_entries.options.async_abort(exposure["flow_id"])

    # Assert - selecting a method is never itself a write operation.
    coordinator.async_calibrate_internal_co2.assert_not_awaited()


@pytest.mark.asyncio
async def test_reference_calibration_rejects_non_ppm_unit(hass) -> None:
    """A CO2-shaped percentage sensor is not accepted as a reference."""

    # Arrange - expose the right device class with the wrong unit.
    entry, coordinator = _options_entry(hass)
    hass.states.async_set(
        "sensor.bad_reference",
        "450",
        {
            ATTR_DEVICE_CLASS: SensorDeviceClass.CO2,
            ATTR_UNIT_OF_MEASUREMENT: "%",
        },
    )
    method = await _open_calibration_options(hass, entry)
    references = await hass.config_entries.options.async_configure(
        method["flow_id"],
        {CONF_CALIBRATION_METHOD: CALIBRATION_METHOD_REFERENCE_SENSORS},
    )

    # Act - attempt to submit the invalid reference through the flow API.
    rejected = await hass.config_entries.options.async_configure(
        references["flow_id"],
        {CONF_REFERENCE_SENSORS: ["sensor.bad_reference"]},
    )

    # Assert - unit validation blocks the write even if UI filtering is bypassed.
    assert rejected["errors"] == {"base": "invalid_reference"}
    coordinator.async_calibrate_internal_co2.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("reading", ["399", "2001", "nan", "inf"])
async def test_reference_calibration_rejects_unsafe_values(hass, reading) -> None:
    """Out-of-range and non-finite states never reach Bluetooth."""

    # Arrange - expose a CO2-shaped entity with an unsafe numeric value.
    entry, coordinator = _options_entry(hass)
    hass.states.async_set(
        "sensor.reference_co2",
        reading,
        {
            ATTR_DEVICE_CLASS: SensorDeviceClass.CO2,
            ATTR_UNIT_OF_MEASUREMENT: UnitOfRatio.PARTS_PER_MILLION,
        },
    )
    method = await _open_calibration_options(hass, entry)
    references = await hass.config_entries.options.async_configure(
        method["flow_id"],
        {CONF_CALIBRATION_METHOD: CALIBRATION_METHOD_REFERENCE_SENSORS},
    )

    # Act - select the unsafe reference.
    rejected = await hass.config_entries.options.async_configure(
        references["flow_id"],
        {CONF_REFERENCE_SENSORS: ["sensor.reference_co2"]},
    )

    # Assert - the validation remains in the UI and sends nothing.
    assert rejected["step_id"] == "calibration_reference"
    assert rejected["errors"] == {"base": "reference_out_of_range"}
    coordinator.async_calibrate_internal_co2.assert_not_awaited()


@pytest.mark.asyncio
async def test_calibration_hidden_for_unvalidated_model(hass) -> None:
    """Unknown models cannot reach an internal calibration write."""

    # Arrange - create an entry whose connected model is not validated.
    entry, coordinator = _options_entry(hass, supports_calibration=False)

    # Act - open the options menu for a model without the internal sensor.
    result = await hass.config_entries.options.async_init(entry.entry_id)

    # Assert - calibration is absent rather than leading to a dead-end screen.
    assert result["type"] is data_entry_flow.FlowResultType.MENU
    assert result["menu_options"] == ["fan_options"]
    coordinator.async_calibrate_internal_co2.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected_error"),
    [
        (HomeAssistantError("device unavailable"), "calibration_failed"),
        (
            CalibrationCommandNotSentError("target unavailable"),
            "calibration_not_sent",
        ),
        (
            CalibrationDeliveryUncertainError("write timed out"),
            "calibration_delivery_uncertain",
        ),
        (CalibrationRateLimitedError("wait"), "calibration_rate_limited"),
    ],
)
async def test_calibration_failure_does_not_show_success(
    hass, error, expected_error
) -> None:
    """Unavailable and rate-limited writes remain on guarded confirmation."""

    # Arrange - reach final confirmation with a coordinator that will reject it.
    entry, coordinator = _options_entry(hass)
    coordinator.async_calibrate_internal_co2.side_effect = error
    method = await _open_calibration_options(hass, entry)
    exposure = await hass.config_entries.options.async_configure(
        method["flow_id"],
        {CONF_CALIBRATION_METHOD: CALIBRATION_METHOD_FRESH_AIR},
    )
    confirm = await hass.config_entries.options.async_configure(
        exposure["flow_id"], {CONF_REFERENCE_PPM: 450}
    )

    # Act - explicitly confirm the command that fails before completion.
    failed = await hass.config_entries.options.async_configure(
        confirm["flow_id"], {CONF_CONFIRM_CALIBRATION: True}
    )

    # Assert - failure is visible and no result/success screen is created.
    assert failed["step_id"] == "calibration_confirm"
    assert failed["errors"] == {"base": expected_error}
    coordinator.async_calibrate_internal_co2.assert_awaited_once_with(450)


@pytest.mark.asyncio
async def test_silent_hours_menu_requires_complete_current_table(hass) -> None:
    """Schedule writes appear only with supported current six-slot state."""

    # Arrange - create supported/current, unsupported, and unavailable entries.
    supported, _ = _options_entry(hass, supports_schedules=True)
    unsupported, _ = _options_entry(hass, supports_schedules=False)
    unavailable, _ = _options_entry(
        hass, supports_schedules=True, schedules_available=False
    )

    # Act - open every options menu.
    supported_menu = await hass.config_entries.options.async_init(supported.entry_id)
    unsupported_menu = await hass.config_entries.options.async_init(
        unsupported.entry_id
    )
    unavailable_menu = await hass.config_entries.options.async_init(
        unavailable.entry_id
    )

    # Assert - only the supported unit with six confirmed slots exposes the flow.
    assert "silent_hours" in supported_menu["menu_options"]
    assert "silent_hours" not in unsupported_menu["menu_options"]
    assert "silent_hours" not in unavailable_menu["menu_options"]


@pytest.mark.asyncio
async def test_silent_hours_creates_overnight_weekday_schedule(hass) -> None:
    """Named days and selector times become the recovered overnight record."""

    # Arrange - open empty slot three on a supported current table.
    entry, coordinator = _options_entry(hass, supports_schedules=True)
    slots = await _open_silent_hours_options(hass, entry)
    edit = await hass.config_entries.options.async_configure(
        slots["flow_id"],
        {
            CONF_SILENT_HOUR_SLOT: "2",
            CONF_SILENT_HOUR_ACTION: SILENT_HOUR_ACTION_EDIT,
        },
    )

    # Act - submit 22:30 to 06:15 on Monday, Wednesday, and Friday.
    result = await hass.config_entries.options.async_configure(
        edit["flow_id"],
        {
            CONF_SILENT_HOUR_START: "22:30:00",
            CONF_SILENT_HOUR_END: "06:15:00",
            CONF_SILENT_HOUR_WEEKDAYS: ["monday", "wednesday", "friday"],
        },
    )

    # Assert - the coordinator receives slot two and Monday-first mask 0x15.
    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "silent_hour_result"
    coordinator.async_set_silent_hour.assert_awaited_once()
    index, record = coordinator.async_set_silent_hour.await_args.args
    assert index == 2
    assert record.start_seconds == 22 * 3600 + 30 * 60
    assert record.end_seconds == 6 * 3600 + 15 * 60
    assert record.weekdays_mask == 0x15
    assert record.is_overnight


@pytest.mark.asyncio
async def test_silent_hours_rejects_empty_weekday_selection(hass) -> None:
    """A schedule cannot be sent without at least one selected weekday."""

    # Arrange - open the edit form for an empty slot.
    entry, coordinator = _options_entry(hass, supports_schedules=True)
    slots = await _open_silent_hours_options(hass, entry)
    edit = await hass.config_entries.options.async_configure(
        slots["flow_id"],
        {
            CONF_SILENT_HOUR_SLOT: "0",
            CONF_SILENT_HOUR_ACTION: SILENT_HOUR_ACTION_EDIT,
        },
    )

    # Act - submit valid times with no weekdays.
    result = await hass.config_entries.options.async_configure(
        edit["flow_id"],
        {
            CONF_SILENT_HOUR_START: "09:00:00",
            CONF_SILENT_HOUR_END: "17:00:00",
            CONF_SILENT_HOUR_WEEKDAYS: [],
        },
    )

    # Assert - validation remains on the form and performs no BLE mutation.
    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"]["base"] == "silent_hour_invalid"
    coordinator.async_set_silent_hour.assert_not_awaited()


@pytest.mark.asyncio
async def test_silent_hours_delete_requires_confirmation_and_readback(hass) -> None:
    """A populated slot cannot be deleted without the explicit toggle."""

    # Arrange - put a daytime record in slot one and open its delete step.
    entry, coordinator = _options_entry(hass, supports_schedules=True)
    record = encode_silent_hour(9 * 3600, 17 * 3600, 0x1F)
    slots = list(coordinator.data.silent_hours)
    slots[1] = decode_silent_hour_slot(bytes.fromhex("01000000") + record)
    coordinator.data.silent_hours = tuple(slots)
    selection = await _open_silent_hours_options(hass, entry)
    delete = await hass.config_entries.options.async_configure(
        selection["flow_id"],
        {
            CONF_SILENT_HOUR_SLOT: "1",
            CONF_SILENT_HOUR_ACTION: SILENT_HOUR_ACTION_DELETE,
        },
    )

    # Act - first decline, then explicitly confirm the same deletion.
    declined = await hass.config_entries.options.async_configure(
        delete["flow_id"], {CONF_CONFIRM_SILENT_HOUR_DELETE: False}
    )
    confirmed = await hass.config_entries.options.async_configure(
        declined["flow_id"], {CONF_CONFIRM_SILENT_HOUR_DELETE: True}
    )

    # Assert - only the confirmed submission calls the coordinator once.
    assert declined["errors"]["base"] == "silent_hour_delete_confirmation_required"
    assert confirmed["step_id"] == "silent_hour_result"
    coordinator.async_delete_silent_hour.assert_awaited_once_with(1)


@pytest.mark.asyncio
async def test_silent_hours_delete_ignores_volatile_packet_metadata(hass) -> None:
    """Changed table metadata and CRC do not look like a schedule change."""

    # Arrange - select a populated slot returned with one metadata value and CRC.
    entry, coordinator = _options_entry(hass, supports_schedules=True)
    record = encode_silent_hour(6 * 3600 + 50 * 60, 6 * 3600 + 51 * 60, 0x7F)
    slots = list(coordinator.data.silent_hours)
    baseline_payload = bytes.fromhex("01000120") + record
    slots[1] = decode_silent_hour_slot(
        baseline_payload + bytes((crc8_zirconia(baseline_payload),))
    )
    coordinator.data.silent_hours = tuple(slots)
    selection = await _open_silent_hours_options(hass, entry)
    delete = await hass.config_entries.options.async_configure(
        selection["flow_id"],
        {
            CONF_SILENT_HOUR_SLOT: "1",
            CONF_SILENT_HOUR_ACTION: SILENT_HOUR_ACTION_DELETE,
        },
    )
    current_payload = bytes.fromhex("01000220") + record
    slots[1] = decode_silent_hour_slot(
        current_payload + bytes((crc8_zirconia(current_payload),))
    )
    coordinator.data.silent_hours = tuple(slots)

    # Act - confirm after a poll changed only the packet metadata and checksum.
    confirmed = await hass.config_entries.options.async_configure(
        delete["flow_id"], {CONF_CONFIRM_SILENT_HOUR_DELETE: True}
    )

    # Assert - the semantic table matches and the actual deletion command is attempted.
    assert confirmed["step_id"] == "silent_hour_result"
    coordinator.async_delete_silent_hour.assert_awaited_once_with(1)


@pytest.mark.asyncio
async def test_silent_hours_delete_rejects_semantic_table_change(hass) -> None:
    """A real schedule change still blocks a deletion reviewed against stale state."""

    # Arrange - select a populated slot for deletion, then change another slot.
    entry, coordinator = _options_entry(hass, supports_schedules=True)
    slots = list(coordinator.data.silent_hours)
    slots[1] = decode_silent_hour_slot(
        bytes.fromhex("01000000")
        + encode_silent_hour(6 * 3600 + 50 * 60, 6 * 3600 + 51 * 60, 0x7F)
    )
    coordinator.data.silent_hours = tuple(slots)
    selection = await _open_silent_hours_options(hass, entry)
    delete = await hass.config_entries.options.async_configure(
        selection["flow_id"],
        {
            CONF_SILENT_HOUR_SLOT: "1",
            CONF_SILENT_HOUR_ACTION: SILENT_HOUR_ACTION_DELETE,
        },
    )
    slots[2] = decode_silent_hour_slot(
        bytes.fromhex("02000000") + encode_silent_hour(22 * 3600, 7 * 3600, 0x1F)
    )
    coordinator.data.silent_hours = tuple(slots)

    # Act - confirm after the actual six-slot schedule table changed.
    rejected = await hass.config_entries.options.async_configure(
        delete["flow_id"], {CONF_CONFIRM_SILENT_HOUR_DELETE: True}
    )

    # Assert - stale review protection aborts before sending a deletion command.
    assert rejected["type"] is data_entry_flow.FlowResultType.ABORT
    assert rejected["reason"] == "silent_hours_changed"
    coordinator.async_delete_silent_hour.assert_not_awaited()


@pytest.mark.asyncio
async def test_delay_overrun_requires_review_and_confirmation(hass) -> None:
    """Paired LS timers are written only after explicit review and confirmation."""

    # Arrange - open the exact-identity candidate with Delay off and Overrun on.
    entry, coordinator = _options_entry(hass, supports_delay_overrun=True)
    form = await _open_delay_overrun_options(hass, entry)

    # Act - request safe paired changes, decline once, then confirm.
    confirm = await hass.config_entries.options.async_configure(
        form["flow_id"],
        {
            CONF_DELAY_TIMEOUT: 11,
            CONF_OVERRUN_ENABLED: False,
            CONF_OVERRUN_TIMEOUT: 12,
        },
    )
    declined = await hass.config_entries.options.async_configure(
        confirm["flow_id"], {CONF_CONFIRM_DELAY_OVERRUN: False}
    )
    result = await hass.config_entries.options.async_configure(
        declined["flow_id"], {CONF_CONFIRM_DELAY_OVERRUN: True}
    )

    # Assert - no Bluetooth mutation precedes confirmation and all values are passed.
    assert form["step_id"] == "delay_overrun"
    assert confirm["step_id"] == "delay_overrun_confirm"
    assert declined["errors"] == {"base": "delay_overrun_confirmation_required"}
    coordinator.async_set_delay_overrun.assert_awaited_once_with(
        delay_enabled=False,
        delay_minutes=11,
        overrun_enabled=False,
        overrun_minutes=12,
    )
    assert result["step_id"] == "delay_overrun_result"


@pytest.mark.asyncio
async def test_delay_overrun_rechecks_full_snapshot_before_write(hass) -> None:
    """Any intervening packet-137 change invalidates the paired timer review."""

    # Arrange - review one valid timer change on the exact candidate identity.
    entry, coordinator = _options_entry(hass, supports_delay_overrun=True)
    form = await _open_delay_overrun_options(hass, entry)
    confirm = await hass.config_entries.options.async_configure(
        form["flow_id"],
        {
            CONF_DELAY_TIMEOUT: 11,
            CONF_OVERRUN_ENABLED: True,
            CONF_OVERRUN_TIMEOUT: 10,
        },
    )
    changed = bytearray(coordinator.data.global_settings.raw_record)
    changed[4] += 1
    coordinator.data.global_settings = decode_global_settings(bytes(changed))

    # Act - confirm after an unrelated installer setting changed.
    result = await hass.config_entries.options.async_configure(
        confirm["flow_id"], {CONF_CONFIRM_DELAY_OVERRUN: True}
    )

    # Assert - the complete snapshot guard aborts before Bluetooth I/O.
    assert result["step_id"] == "delay_overrun"
    assert result["errors"] == {"base": "delay_overrun_settings_changed"}
    coordinator.async_set_delay_overrun.assert_not_awaited()


@pytest.mark.asyncio
async def test_delay_overrun_rejects_out_of_range_timer(hass) -> None:
    """The official 1..60 minute range is enforced before review or writes."""

    # Arrange - open the exact-identity paired timer candidate.
    entry, coordinator = _options_entry(hass, supports_delay_overrun=True)
    form = await _open_delay_overrun_options(hass, entry)

    # Act - submit a timer beyond the selector's documented maximum.
    with pytest.raises(data_entry_flow.InvalidData) as raised:
        await hass.config_entries.options.async_configure(
            form["flow_id"],
            {
                CONF_DELAY_ENABLED: True,
                CONF_DELAY_TIMEOUT: 61,
                CONF_OVERRUN_ENABLED: True,
                CONF_OVERRUN_TIMEOUT: 10,
            },
        )

    # Assert - Home Assistant's schema blocks it before coordinator code or I/O.
    assert raised.value
    coordinator.async_set_delay_overrun.assert_not_awaited()
