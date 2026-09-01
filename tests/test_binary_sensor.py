"""Binary-sensor entity-description tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.const import EntityCategory

from custom_components.ventaxia_multihome.binary_sensor import (
    INSTALLER_FLAG_ENTITIES,
    MultihomeInstallerFlagBinarySensor,
)


def test_installer_boolean_entities_are_disabled_diagnostics() -> None:
    """Installer flags do not become enabled controls or entities by default."""

    # Arrange - construct both coordinator-backed response entities.
    settings = SimpleNamespace(
        comfort_enabled=True,
        rapid_response_enabled=True,
        ambient_response_enabled=False,
    )
    coordinator = SimpleNamespace(data=SimpleNamespace(global_settings=settings))
    entry = SimpleNamespace(data={"address": "AA:BB"})
    entities = [
        MultihomeInstallerFlagBinarySensor(
            coordinator, entry, attribute, translation_key
        )
        for attribute, translation_key in INSTALLER_FLAG_ENTITIES
    ]

    # Act - read the values and Home Assistant registry metadata.
    values = tuple(entity.is_on for entity in entities)

    # Assert - values are read-only diagnostics and disabled until user-enabled.
    assert values == (True, True, False)
    assert all(
        entity.entity_category is EntityCategory.DIAGNOSTIC
        and entity.entity_registry_enabled_default is False
        for entity in entities
    )
