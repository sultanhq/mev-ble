"""Sensor entity-description tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import EntityCategory

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
