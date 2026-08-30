"""Constants for the Vent-Axia Multihome integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "ventaxia_multihome"
NAME: Final = "Vent-Axia Multihome"
MANUFACTURER: Final = "Vent-Axia"

CONF_SETUP_CODE: Final = "setup_code"
CONF_OVERRIDE_DURATION: Final = "override_duration"
CONF_LAST_CO2_CALIBRATION_ATTEMPT: Final = "last_co2_calibration_attempt"

DEFAULT_OVERRIDE_DURATION: Final = 1800
MIN_OVERRIDE_DURATION: Final = 1
MAX_OVERRIDE_DURATION: Final = 28_800
CO2_CALIBRATION_COOLDOWN: Final = 300
CO2_CALIBRATION_SAMPLING_DURATION: Final = 180
CO2_CALIBRATION_PROGRESS_INTERVAL: Final = 1
STARTUP_ADVERTISEMENT_TIMEOUT: Final = 10
UPDATE_INTERVAL: Final = timedelta(seconds=10)

SUPPORTED_LOCAL_NAMES: Final = frozenset({"mev", "multihome"})

CONNECTION_SERVICE_UUID: Final = "e6834e4b-7b3a-48e6-91e4-f1d005f564d3"
PIN_CHARACTERISTIC_UUID: Final = "4cad343a-209a-40b7-b911-4d9b3df569b2"
PIN_CONFIRM_CHARACTERISTIC_UUID: Final = "d1ae6b70-ee12-4f6d-b166-d2063dcaffe1"

PROTOCOL_SERVICE_UUID: Final = "e6ec2fd8-e888-4eb2-9680-e78ed6ea89e1"
WHOLE_PACKET_CHARACTERISTIC_UUID: Final = "a8e23cea-978d-ac8d-374c-cbb4eeb63f41"
FRAGMENT_CHARACTERISTIC_UUID: Final = "e6ec2fd8-e888-4eb2-9681-e78ed6ea89e1"

DEVICE_INFO_CHARACTERISTICS: Final = {
    "model": "00002a24-0000-1000-8000-00805f9b34fb",
    "serial": "00002a25-0000-1000-8000-00805f9b34fb",
    "firmware": "00002a26-0000-1000-8000-00805f9b34fb",
    "hardware": "00002a27-0000-1000-8000-00805f9b34fb",
    "software": "00002a28-0000-1000-8000-00805f9b34fb",
    "manufacturer": "00002a29-0000-1000-8000-00805f9b34fb",
}

PRESET_NAMES: Final = ("low", "normal", "boost", "purge")
