"""Sensors for Vent-Axia Multihome."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    UnitOfRatio,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import VentaxiaMultihomeConfigEntry
from .device import MultihomeData
from .entity import VentaxiaMultihomeEntity
from .protocol import fan_state_name


@dataclass(frozen=True, kw_only=True)
class MultihomeSensorDescription(SensorEntityDescription):
    """Describe a Multihome sensor."""

    value_fn: Callable[[MultihomeData], float | int | str | None]
    exists_fn: Callable[[MultihomeData], bool] = lambda _: True


SENSORS: tuple[MultihomeSensorDescription, ...] = (
    MultihomeSensorDescription(
        key="temperature",
        translation_key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda data: data.zone.temperature,
    ),
    MultihomeSensorDescription(
        key="relative_humidity",
        translation_key="relative_humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda data: data.zone.relative_humidity,
    ),
    MultihomeSensorDescription(
        key="co2",
        translation_key="co2",
        device_class=SensorDeviceClass.CO2,
        native_unit_of_measurement=UnitOfRatio.PARTS_PER_MILLION,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        exists_fn=lambda data: data.zone.co2_supported,
        value_fn=lambda data: data.zone.co2,
    ),
    MultihomeSensorDescription(
        key="fan_rpm",
        translation_key="fan_rpm",
        native_unit_of_measurement="rpm",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.zone.fan_rpm,
    ),
    MultihomeSensorDescription(
        key="fan_level",
        translation_key="fan_level",
        value_fn=lambda data: data.zone.fan_level,
    ),
    MultihomeSensorDescription(
        key="fan_state",
        translation_key="fan_state",
        value_fn=lambda data: fan_state_name(data.zone.fan_state),
    ),
    MultihomeSensorDescription(
        key="override_remaining",
        translation_key="override_remaining",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.system.override_remaining,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VentaxiaMultihomeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Multihome sensors."""

    coordinator = entry.runtime_data
    data = coordinator.data
    async_add_entities(
        MultihomeSensor(coordinator, entry, description)
        for description in SENSORS
        if data is None or description.exists_fn(data)
    )


class MultihomeSensor(VentaxiaMultihomeEntity, SensorEntity):
    """A coordinator-backed Multihome sensor."""

    entity_description: MultihomeSensorDescription

    def __init__(self, coordinator, entry, description) -> None:
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | int | str | None:
        """Return the latest decoded value."""

        if (data := self.coordinator.data) is None:
            return None
        return self.entity_description.value_fn(data)
