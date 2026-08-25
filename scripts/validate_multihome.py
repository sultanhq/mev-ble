#!/usr/bin/env python3
"""Optional local-Bluetooth hardware validator for Vent-Axia Multihome.

This development helper uses the host's local Bleak adapter. The Home Assistant
integration itself uses HA Bluetooth and therefore supports ESPHome proxies.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys
from dataclasses import asdict
from pathlib import Path

from bleak import BleakScanner

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.ventaxia_multihome.bluetooth import (
    async_establish_connection,
)
from custom_components.ventaxia_multihome.const import SUPPORTED_LOCAL_NAMES
from custom_components.ventaxia_multihome.device import MultihomeDevice
from custom_components.ventaxia_multihome.protocol import AirflowPreset


async def _discover(timeout: float):
    discoveries = await BleakScanner.discover(timeout=timeout, return_adv=True)
    for ble_device, advertisement in discoveries.values():
        name = advertisement.local_name or ble_device.name
        if name and name.casefold() in SUPPORTED_LOCAL_NAMES:
            return ble_device, name
    raise RuntimeError("No local-name MEV or Multihome device was discovered")


def _read_setup_code() -> int:
    value = getpass.getpass("Multihome setup code (input hidden): ")
    setup_code = int(value)
    if not 1 <= setup_code <= 0xFFFFFFFF:
        raise ValueError("setup code must be a nonzero UInt32")
    return setup_code


async def _read_only(timeout: float) -> None:
    ble_device, name = await _discover(timeout)
    device = MultihomeDevice(
        ble_device.address,
        name,
        _read_setup_code(),
        client_factory=async_establish_connection,
    )
    try:
        data = await device.update(ble_device)
        print(
            json.dumps(
                {
                    "address": ble_device.address,
                    "name": name,
                    "transport": device.transport_name,
                    "device_information": asdict(device.device_info),
                    "zone_telemetry": asdict(data.zone),
                    "system_status": asdict(data.system),
                },
                indent=2,
            )
        )
    finally:
        await device.disconnect()


async def _test_override(
    timeout: float, preset: AirflowPreset, duration: int, confirmed: bool
) -> None:
    if not confirmed:
        answer = input(
            f"Apply {preset.name.lower()} for {duration} seconds? Type 'yes': "
        )
        if answer.casefold() != "yes":
            raise RuntimeError("Override test cancelled")
    ble_device, name = await _discover(timeout)
    device = MultihomeDevice(
        ble_device.address,
        name,
        _read_setup_code(),
        client_factory=async_establish_connection,
    )
    try:
        await device.set_override(ble_device, preset, duration)
        print(f"Applied {preset.name.lower()} override for {duration} seconds")
    finally:
        await device.disconnect()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scan-timeout", type=float, default=10.0)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "read-only",
        help="discover, authenticate, read device info/telemetry/status, disconnect",
    )
    override = commands.add_parser(
        "test-override", help="explicitly exercise one short timed override"
    )
    override.add_argument(
        "--preset",
        choices=[preset.name.lower() for preset in AirflowPreset],
        default="boost",
    )
    override.add_argument("--duration", type=int, default=60)
    override.add_argument(
        "--yes", action="store_true", help="skip the interactive write confirmation"
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.command == "read-only":
        asyncio.run(_read_only(args.scan_timeout))
        return
    if not 1 <= args.duration <= 28_800:
        raise SystemExit("duration must be 1..28800 seconds")
    asyncio.run(
        _test_override(
            args.scan_timeout,
            AirflowPreset[args.preset.upper()],
            args.duration,
            args.yes,
        )
    )


if __name__ == "__main__":
    main()
