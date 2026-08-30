"""Vent-Axia Multihome integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant

from .bluetooth import async_establish_connection
from .const import CONF_SETUP_CODE
from .coordinator import VentaxiaMultihomeCoordinator
from .device import MultihomeDevice

if TYPE_CHECKING:
    from homeassistant.helpers.typing import ConfigType

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.FAN,
    Platform.SENSOR,
]

type VentaxiaMultihomeConfigEntry = ConfigEntry[VentaxiaMultihomeCoordinator]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the integration namespace."""

    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: VentaxiaMultihomeConfigEntry
) -> bool:
    """Set up a Multihome device from a config entry."""

    address = entry.data[CONF_ADDRESS]
    device = MultihomeDevice(
        address,
        entry.title,
        entry.data[CONF_SETUP_CODE],
        client_factory=async_establish_connection,
    )
    coordinator = VentaxiaMultihomeCoordinator(hass, entry, device)
    await coordinator.async_wait_for_initial_bluetooth()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: VentaxiaMultihomeConfigEntry
) -> bool:
    """Unload a Multihome config entry."""

    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.device.disconnect()
    return unload_ok


async def async_remove_entry(
    hass: HomeAssistant, entry: VentaxiaMultihomeConfigEntry
) -> None:
    """Make a removed device immediately eligible for rediscovery."""

    bluetooth.async_rediscover_address(hass, entry.data[CONF_ADDRESS])
