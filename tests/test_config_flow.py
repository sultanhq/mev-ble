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
    CONCENTRATION_PARTS_PER_MILLION,
    CONF_ADDRESS,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ventaxia_multihome import config_flow as config_flow_module
from custom_components.ventaxia_multihome.config_flow import (
    CALIBRATION_METHOD_FRESH_AIR,
    CALIBRATION_METHOD_REFERENCE_SENSORS,
    CONF_CALIBRATION_METHOD,
    CONF_CONFIRM_CALIBRATION,
    CONF_REFERENCE_PPM,
    CONF_REFERENCE_SENSORS,
)
from custom_components.ventaxia_multihome.const import (
    CONF_OVERRIDE_DURATION,
    CONF_SETUP_CODE,
    DOMAIN,
)
from custom_components.ventaxia_multihome.coordinator import (
    CalibrationRateLimitedError,
)
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


def _options_entry(hass, *, supports_calibration: bool = True):
    """Create a loaded-looking entry without starting Bluetooth I/O."""

    coordinator = SimpleNamespace(
        device=SimpleNamespace(
            supports_internal_co2_calibration=supports_calibration
        ),
        async_calibrate_internal_co2=AsyncMock(),
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


async def _open_calibration_options(hass, entry):
    """Open the calibration method screen from the options menu."""

    initial = await hass.config_entries.options.async_init(entry.entry_id)
    assert initial["type"] is data_entry_flow.FlowResultType.MENU
    assert initial["step_id"] == "init"
    return await hass.config_entries.options.async_configure(
        initial["flow_id"], {"next_step_id": "calibrate_co2"}
    )


@pytest.fixture
def fast_calibration_progress(monkeypatch):
    """Keep end-to-end progress-flow tests fast."""

    monkeypatch.setattr(
        config_flow_module, "CO2_CALIBRATION_SAMPLING_DURATION", 0.001
    )
    monkeypatch.setattr(
        config_flow_module, "CO2_CALIBRATION_PROGRESS_INTERVAL", 0.001
    )


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
        result = await hass.config_entries.flow.async_configure(
            initial["flow_id"], {}
        )

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
    assert "official app defaults to 450" in (
        confirm["description_placeholders"]["reference_summary"]
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
    completed = await hass.config_entries.options.async_configure(
        result["flow_id"], {}
    )
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
        ATTR_UNIT_OF_MEASUREMENT: CONCENTRATION_PARTS_PER_MILLION,
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
            ATTR_UNIT_OF_MEASUREMENT: CONCENTRATION_PARTS_PER_MILLION,
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
    assert "must represent the air entering" in (
        confirm["description_placeholders"]["reference_summary"]
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
    english = json.loads(
        (integration_dir / "translations/en.json").read_text()
    )

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
            ATTR_UNIT_OF_MEASUREMENT: CONCENTRATION_PARTS_PER_MILLION,
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
        ATTR_UNIT_OF_MEASUREMENT: CONCENTRATION_PARTS_PER_MILLION,
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
            ATTR_UNIT_OF_MEASUREMENT: CONCENTRATION_PARTS_PER_MILLION,
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
