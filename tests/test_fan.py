"""Home Assistant timed fan-preset tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.components.fan import FanEntityFeature
from homeassistant.const import CONF_ADDRESS

from custom_components.ventaxia_multihome.fan import MultihomeFan
from custom_components.ventaxia_multihome.protocol import AirflowPreset


def _fan(
    *,
    fan_speed: int = 2,
    fan_rpm: int = 900,
) -> tuple[MultihomeFan, SimpleNamespace]:
    """Create the fan entity subset needed by preset-control tests."""

    coordinator = SimpleNamespace(
        device=SimpleNamespace(device_info=SimpleNamespace(model="11")),
        data=SimpleNamespace(
            system=SimpleNamespace(fan_speed=fan_speed),
            zone=SimpleNamespace(fan_rpm=fan_rpm, fan_level=2),
        ),
        async_set_override=AsyncMock(),
    )
    entry = SimpleNamespace(data={CONF_ADDRESS: "AA:BB:CC:DD:EE:FF"})
    return MultihomeFan(coordinator, entry), coordinator


def test_fan_exposes_presets_without_inferred_power_controls() -> None:
    """The physical MEV evidence permits presets but not Off, On, or Stop."""

    # Arrange - create a fan for the reported MEV hardware control surface.
    entity, _coordinator = _fan()

    # Act - read the features Home Assistant will expose for the entity.
    features = entity.supported_features

    # Assert - presets remain available while inferred power controls stay hidden.
    assert features & FanEntityFeature.PRESET_MODE
    assert not features & FanEntityFeature.TURN_OFF
    assert not features & FanEntityFeature.TURN_ON
    assert not hasattr(entity, "async_stop_ventilation")


def test_fan_state_comes_from_confirmed_telemetry() -> None:
    """The entity reports off only after both speed and RPM telemetry reach zero."""

    # Arrange - create stopped telemetry without issuing any control.
    entity, _ = _fan(fan_speed=0, fan_rpm=0)

    # Act - read Home Assistant's fan state.
    result = entity.is_on

    # Assert - no optimistic or remembered command state is involved.
    assert result is False


@pytest.mark.asyncio
@pytest.mark.parametrize("preset", ["low", "normal", "boost", "purge"])
async def test_all_home_assistant_presets_use_documented_overrides(
    preset: str,
) -> None:
    """Every advertised preset delegates to its matching protocol enum."""

    # Arrange - create a supported entity with a recording coordinator.
    entity, coordinator = _fan()

    # Act - select one preset through Home Assistant's standard fan API.
    await entity.async_set_preset_mode(preset)

    # Assert - the matching recovered enum is sent with the default duration.
    coordinator.async_set_override.assert_awaited_once_with(
        AirflowPreset[preset.upper()]
    )
