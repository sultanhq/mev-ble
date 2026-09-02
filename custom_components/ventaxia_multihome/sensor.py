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
    EntityCategory,
    UnitOfRatio,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import VentaxiaMultihomeConfigEntry
from .device import MultihomeData
from .entity import VentaxiaMultihomeEntity
from .protocol import fan_state_name, temperature_threshold_action_name


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
        key="speed_low",
        translation_key="speed_low",
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.global_settings.speed_low,
    ),
    MultihomeSensorDescription(
        key="speed_medium",
        translation_key="speed_medium",
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.global_settings.speed_medium,
    ),
    MultihomeSensorDescription(
        key="speed_boost",
        translation_key="speed_boost",
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.global_settings.speed_boost,
    ),
    MultihomeSensorDescription(
        key="speed_purge",
        translation_key="speed_purge",
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.global_settings.speed_purge,
    ),
    MultihomeSensorDescription(
        key="boost_minimum",
        translation_key="boost_minimum",
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.global_settings.boost_minimum,
    ),
    MultihomeSensorDescription(
        key="humidity_threshold",
        translation_key="humidity_threshold",
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.global_settings.humidity_threshold,
    ),
    MultihomeSensorDescription(
        key="low_threshold_action",
        translation_key="low_threshold_action",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: temperature_threshold_action_name(
            data.global_settings.low_threshold_action
        ),
    ),
    MultihomeSensorDescription(
        key="high_threshold_action",
        translation_key="high_threshold_action",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: temperature_threshold_action_name(
            data.global_settings.high_threshold_action
        ),
    ),
    MultihomeSensorDescription(
        key="low_temperature_threshold",
        translation_key="low_temperature_threshold",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.global_settings.low_temperature_threshold,
    ),
    MultihomeSensorDescription(
        key="high_temperature_threshold",
        translation_key="high_temperature_threshold",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.global_settings.high_temperature_threshold,
    ),
    MultihomeSensorDescription(
        key="purge_low_mode",
        translation_key="purge_low_mode",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.global_settings.purge_low_mode,
    ),
    MultihomeSensorDescription(
        key="overrun_timeout_minutes",
        translation_key="overrun_timeout",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.global_settings.overrun_timeout_minutes,
    ),
    MultihomeSensorDescription(
        key="delay_timeout_minutes",
        translation_key="delay_timeout",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.global_settings.delay_timeout_minutes,
    ),
    MultihomeSensorDescription(
        key="ls1_action",
        translation_key="ls1_action",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.global_settings.ls1_action,
    ),
    MultihomeSensorDescription(
        key="ls2_action",
        translation_key="ls2_action",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.global_settings.ls2_action,
    ),
    MultihomeSensorDescription(
        key="ls3_action",
        translation_key="ls3_action",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.global_settings.ls3_action,
    ),
    MultihomeSensorDescription(
        key="co2_boost_threshold",
        translation_key="co2_boost_threshold",
        device_class=SensorDeviceClass.CO2,
        native_unit_of_measurement=UnitOfRatio.PARTS_PER_MILLION,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        exists_fn=lambda data: data.zone.co2_supported,
        value_fn=lambda data: data.global_settings.co2_boost_threshold,
    ),
    MultihomeSensorDescription(
        key="co2_purge_threshold",
        translation_key="co2_purge_threshold",
        device_class=SensorDeviceClass.CO2,
        native_unit_of_measurement=UnitOfRatio.PARTS_PER_MILLION,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        exists_fn=lambda data: data.zone.co2_supported,
        value_fn=lambda data: data.global_settings.co2_purge_threshold,
    ),
    MultihomeSensorDescription(
        key="analogue_input_1_low_value",
        translation_key="analogue_input_1_low_value",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.global_settings.analogue_input_1_low_value,
    ),
    MultihomeSensorDescription(
        key="analogue_input_1_high_value",
        translation_key="analogue_input_1_high_value",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.global_settings.analogue_input_1_high_value,
    ),
    MultihomeSensorDescription(
        key="analogue_input_1_low_action",
        translation_key="analogue_input_1_low_action",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.global_settings.analogue_input_1_low_action,
    ),
    MultihomeSensorDescription(
        key="analogue_input_1_high_action",
        translation_key="analogue_input_1_high_action",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.global_settings.analogue_input_1_high_action,
    ),
    MultihomeSensorDescription(
        key="analogue_input_2_low_value",
        translation_key="analogue_input_2_low_value",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.global_settings.analogue_input_2_low_value,
    ),
    MultihomeSensorDescription(
        key="analogue_input_2_high_value",
        translation_key="analogue_input_2_high_value",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.global_settings.analogue_input_2_high_value,
    ),
    MultihomeSensorDescription(
        key="analogue_input_2_low_action",
        translation_key="analogue_input_2_low_action",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.global_settings.analogue_input_2_low_action,
    ),
    MultihomeSensorDescription(
        key="analogue_input_2_high_action",
        translation_key="analogue_input_2_high_action",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.global_settings.analogue_input_2_high_action,
    ),
    MultihomeSensorDescription(
        key="digital_input_1_action",
        translation_key="digital_input_1_action",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.global_settings.digital_input_1_action,
    ),
    MultihomeSensorDescription(
        key="digital_input_2_action",
        translation_key="digital_input_2_action",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.global_settings.digital_input_2_action,
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
