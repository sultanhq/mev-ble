"""Shared entity availability tests."""

from __future__ import annotations

from types import SimpleNamespace

from homeassistant.const import CONF_ADDRESS

from custom_components.ventaxia_multihome.entity import VentaxiaMultihomeEntity


def test_entity_unavailable_while_hard_reset_recovery_owns_bluetooth() -> None:
    """Recovery ownership suppresses availability without coordinator errors."""

    # Arrange - start from a healthy coordinator whose reset recovery owns BLE.
    coordinator = SimpleNamespace(
        last_update_success=True,
        hard_reset_recovery_active=True,
    )
    entry = SimpleNamespace(data={CONF_ADDRESS: "AA:BB"})
    entity = VentaxiaMultihomeEntity(coordinator, entry, "availability_test")

    # Act / Assert - recovery alone makes the entity unavailable.
    assert entity.available is False

    # Act / Assert - completing recovery restores normal coordinator availability.
    coordinator.hard_reset_recovery_active = False
    assert entity.available is True

    # Act / Assert - ordinary coordinator failures still control availability.
    coordinator.last_update_success = False
    assert entity.available is False
