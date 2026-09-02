"""Sensor entity-description tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import PERCENTAGE, EntityCategory

from custom_components.ventaxia_multihome.sensor import SENSORS


def _description(key: str):
    """Return one production sensor description by key."""

    return next(description for description in SENSORS if description.key == key)


def test_installer_threshold_entities_are_disabled_diagnostics() -> None:
    """Installer values do not add enabled entities without user choice."""

    # Arrange - select all three new threshold descriptions.
    humidity = _description("humidity_threshold")
    co2_boost = _description("co2_boost_threshold")
    co2_purge = _description("co2_purge_threshold")

    # Act - inspect their Home Assistant registry and device metadata.
    descriptions = (humidity, co2_boost, co2_purge)

    # Assert - each is diagnostic, disabled by default, and semantically typed.
    assert all(
        description.entity_category is EntityCategory.DIAGNOSTIC
        and description.entity_registry_enabled_default is False
        for description in descriptions
    )
    assert humidity.device_class is SensorDeviceClass.HUMIDITY
    assert co2_boost.device_class is SensorDeviceClass.CO2
    assert co2_purge.device_class is SensorDeviceClass.CO2


def test_installer_timer_entities_are_disabled_duration_diagnostics() -> None:
    """Paired timer values are read-only minute diagnostics by default."""

    # Arrange - select both recovered packet-137 timer descriptions.
    delay = _description("delay_timeout_minutes")
    overrun = _description("overrun_timeout_minutes")

    # Act - inspect their registry, semantic type, unit, and decoded values.
    data = SimpleNamespace(
        global_settings=SimpleNamespace(
            delay_timeout_minutes=10,
            overrun_timeout_minutes=12,
        )
    )
    descriptions = (delay, overrun)
    values = tuple(description.value_fn(data) for description in descriptions)

    # Assert - both are disabled diagnostic duration sensors backed by settings.
    assert values == (10, 12)
    assert all(
        description.entity_category is EntityCategory.DIAGNOSTIC
        and description.entity_registry_enabled_default is False
        and description.device_class is SensorDeviceClass.DURATION
        for description in descriptions
    )


def test_installer_threshold_entities_read_packet_137_values() -> None:
    """Diagnostic values come from global settings, not live zone telemetry."""

    # Arrange - provide deliberately different live readings and installer values.
    data = SimpleNamespace(
        zone=SimpleNamespace(relative_humidity=55.5, co2=700, co2_supported=True),
        global_settings=SimpleNamespace(
            humidity_threshold=81,
            co2_boost_threshold=1500,
            co2_purge_threshold=1750,
        ),
    )

    # Act - resolve each description's coordinator-data value.
    values = {
        key: _description(key).value_fn(data)
        for key in (
            "humidity_threshold",
            "co2_boost_threshold",
            "co2_purge_threshold",
        )
    }

    # Assert - entities expose installer thresholds rather than measurements.
    assert values == {
        "humidity_threshold": 81,
        "co2_boost_threshold": 1500,
        "co2_purge_threshold": 1750,
    }


def test_all_numeric_installer_values_are_disabled_diagnostics() -> None:
    """Every decoded numeric installer value is available without becoming a control."""

    # Arrange - provide a distinct value for every numeric packet-137 field.
    expected = {
        "speed_low": 1,
        "speed_medium": 2,
        "speed_boost": 3,
        "speed_purge": 4,
        "boost_minimum": 5,
        "humidity_threshold": 6,
        "low_threshold_action": 7,
        "high_threshold_action": 8,
        "low_temperature_threshold": 9,
        "high_temperature_threshold": 10,
        "purge_low_mode": 11,
        "overrun_timeout_minutes": 12,
        "delay_timeout_minutes": 13,
        "ls1_action": 14,
        "ls2_action": 15,
        "ls3_action": 16,
        "co2_boost_threshold": 1700,
        "co2_purge_threshold": 1800,
        "analogue_input_1_low_value": 19,
        "analogue_input_1_high_value": 20,
        "analogue_input_1_low_action": 21,
        "analogue_input_1_high_action": 22,
        "analogue_input_2_low_value": 23,
        "analogue_input_2_high_value": 24,
        "analogue_input_2_low_action": 25,
        "analogue_input_2_high_action": 26,
        "digital_input_1_action": 27,
        "digital_input_2_action": 28,
    }
    data = SimpleNamespace(global_settings=SimpleNamespace(**expected))
    descriptions = {key: _description(key) for key in expected}

    # Act - resolve the state exposed by each diagnostic entity description.
    values = {
        key: description.value_fn(data)
        for key, description in descriptions.items()
    }

    # Assert - all decoded values are read-only diagnostics disabled by default.
    assert values == expected
    assert all(
        description.entity_category is EntityCategory.DIAGNOSTIC
        and description.entity_registry_enabled_default is False
        for description in descriptions.values()
    )


def test_unknown_installer_semantics_remain_raw_and_unitless() -> None:
    """Unrecovered action, scaling and temperature meanings are not guessed."""

    # Arrange - select every field whose unit or enum meaning is still unknown.
    raw_keys = (
        "low_threshold_action",
        "high_threshold_action",
        "low_temperature_threshold",
        "high_temperature_threshold",
        "purge_low_mode",
        "ls1_action",
        "ls2_action",
        "ls3_action",
        "analogue_input_1_low_value",
        "analogue_input_1_high_value",
        "analogue_input_1_low_action",
        "analogue_input_1_high_action",
        "analogue_input_2_low_value",
        "analogue_input_2_high_value",
        "analogue_input_2_low_action",
        "analogue_input_2_high_action",
        "digital_input_1_action",
        "digital_input_2_action",
    )

    # Act - inspect the semantic metadata Home Assistant would publish.
    descriptions = tuple(_description(key) for key in raw_keys)
    percentage_descriptions = tuple(
        _description(key)
        for key in (
            "speed_low",
            "speed_medium",
            "speed_boost",
            "speed_purge",
            "boost_minimum",
        )
    )

    # Assert - unknown fields stay unitless while confirmed percentages use %.
    assert all(
        description.device_class is None
        and description.native_unit_of_measurement is None
        for description in descriptions
    )
    assert all(
        description.native_unit_of_measurement == PERCENTAGE
        for description in percentage_descriptions
    )
