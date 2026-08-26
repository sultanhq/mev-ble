"""Home Assistant fan power-control tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.components.fan import FanEntityFeature
from homeassistant.const import CONF_ADDRESS
from homeassistant.exceptions import HomeAssistantError

from custom_components.ventaxia_multihome.fan import MultihomeFan
from custom_components.ventaxia_multihome.protocol import VentilationMode


def _fan(
    *,
    model: str | None = "11",
    fan_speed: int = 2,
    fan_rpm: int = 900,
) -> tuple[MultihomeFan, SimpleNamespace]:
    """Create the fan entity subset needed by power-control tests."""

    normal_mode = VentilationMode.VENTILATION if model == "11" else None
    device = SimpleNamespace(
        device_info=SimpleNamespace(model=model),
        normal_ventilation_mode=normal_mode,
        supports_ventilation_mode_control=normal_mode is not None,
    )
    coordinator = SimpleNamespace(
        device=device,
        data=SimpleNamespace(
            system=SimpleNamespace(fan_speed=fan_speed),
            zone=SimpleNamespace(fan_rpm=fan_rpm, fan_level=2),
        ),
        async_set_ventilation_mode=AsyncMock(),
        async_set_override=AsyncMock(),
    )
    entry = SimpleNamespace(data={CONF_ADDRESS: "AA:BB:CC:DD:EE:FF"})
    return MultihomeFan(coordinator, entry), coordinator


@pytest.mark.asyncio
async def test_known_model_exposes_distinct_off_stop_and_restore_controls() -> None:
    """A known model exposes standard power plus the separate stop action."""

    # Arrange - create a running, supported ventilation-model entity.
    entity, coordinator = _fan()

    # Act - invoke standard off, explicit stop, and standard on in order.
    await entity.async_turn_off()
    await entity.async_stop_ventilation()
    await entity.async_turn_on()

    # Assert - Home Assistant features and requested protocol modes are exact.
    assert entity.supported_features & FanEntityFeature.TURN_OFF
    assert entity.supported_features & FanEntityFeature.TURN_ON
    assert entity.is_on is True
    assert coordinator.async_set_ventilation_mode.await_args_list == [
        ((VentilationMode.OFF,), {}),
        ((VentilationMode.STOP,), {}),
        ((VentilationMode.VENTILATION,), {}),
    ]


@pytest.mark.asyncio
async def test_unknown_model_hides_and_rejects_power_controls() -> None:
    """A model without a validated restore mode remains preset-only."""

    # Arrange - create an entity with no recognised model number.
    entity, coordinator = _fan(model=None)

    # Act / Assert - features stay hidden and direct calls fail before I/O.
    assert not entity.supported_features & FanEntityFeature.TURN_OFF
    assert not entity.supported_features & FanEntityFeature.TURN_ON
    with pytest.raises(HomeAssistantError, match="reported Multihome model unknown"):
        await entity.async_turn_off()
    coordinator.async_set_ventilation_mode.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_power_write_does_not_change_confirmed_state() -> None:
    """Rejected writes cannot optimistically change the fan state."""

    # Arrange - retain running telemetry and make the coordinator reject off.
    entity, coordinator = _fan()
    coordinator.async_set_ventilation_mode.side_effect = HomeAssistantError(
        "write rejected"
    )

    # Act / Assert - the error is surfaced and telemetry-confirmed state remains on.
    with pytest.raises(HomeAssistantError, match="write rejected"):
        await entity.async_turn_off()
    assert entity.is_on is True


def test_fan_state_comes_from_confirmed_telemetry() -> None:
    """The entity reports off only after both speed and RPM telemetry reach zero."""

    # Arrange - create stopped telemetry without issuing any control.
    entity, _ = _fan(fan_speed=0, fan_rpm=0)

    # Act - read Home Assistant's fan state.
    result = entity.is_on

    # Assert - no optimistic or remembered command state is involved.
    assert result is False
