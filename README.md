# Vent-Axia Multihome for Home Assistant

[![HACS validation](https://github.com/sultanhq/mev-ble/actions/workflows/validate.yml/badge.svg)](https://github.com/sultanhq/mev-ble/actions/workflows/validate.yml)
[![Tests](https://github.com/sultanhq/mev-ble/actions/workflows/tests.yml/badge.svg)](https://github.com/sultanhq/mev-ble/actions/workflows/tests.yml)

A local Home Assistant custom integration for the documented Bluetooth Low
Energy interface used by Vent-Axia MEV/Multihome ventilation units. It exposes
telemetry, faults, timed airflow presets, and override cancellation without a
cloud account.

> [!IMPORTANT]
> The protocol implementation is covered by automated tests, but it has not yet
> been validated against a physical unit. Treat `0.1.x` as a hardware-validation
> release and report your unit model, firmware, and results.

## What you need

- Home Assistant 2026.8.0 or newer with the Bluetooth integration enabled.
- A Vent-Axia unit advertising as `MEV` or `Multihome`.
- A connectable Bluetooth route from Home Assistant to the unit:
  - a supported local Bluetooth adapter; or
  - an **active** ESPHome Bluetooth proxy near the ventilation controller.
- The unit's numeric application setup code and access to its pairing/setup
  mode. Bluetooth itself remains OS-pairing/PIN-less; see
  [Setup code versus Bluetooth PIN](docs/configuration.md#setup-code-versus-bluetooth-pin).

An original ESP32/ESP32-WROOM-32 development board is the safest inexpensive
proxy choice. ESP32-S3 and ESP32-C3 boards also have BLE and are suitable.
ESP32-S2 boards do **not** have Bluetooth. For the strongest link, ESPHome
recommends an Ethernet board with an external antenna, such as the Olimex
ESP32-PoE-ISO-EA. See the complete [ESPHome proxy guide](docs/esphome-bluetooth-proxy.md)
and the ready-to-copy [Wi-Fi proxy example](examples/esp32-bluetooth-proxy.yaml).

## Quick start

1. If Home Assistant is not within BLE range, flash and add an
   [active ESPHome Bluetooth proxy](docs/esphome-bluetooth-proxy.md).
2. In HACS, open the top-right menu, choose **Custom repositories**, add
   `https://github.com/sultanhq/mev-ble`, and select **Integration** as the type.
3. Open the new **Vent-Axia Multihome** repository in HACS, choose **Download**,
   and restart Home Assistant.
4. Put the Vent-Axia unit into Bluetooth pairing/setup mode.
5. In Home Assistant, open **Settings → Devices & services** and configure the
   discovered **Vent-Axia Multihome** device. If it is not shown, choose
   **Add integration** and search for the same name.
6. Enter the numeric setup code shown or required by the unit.

HACS calls this repository type an *Integration*. There is no dashboard plugin
or ESP32-specific Vent-Axia firmware to install.

Detailed instructions:

- [Install and update with HACS](docs/installation.md)
- [Choose and configure an ESPHome Bluetooth proxy](docs/esphome-bluetooth-proxy.md)
- [Configure the integration and use its entities/actions](docs/configuration.md)
- [Troubleshooting and diagnostics](docs/troubleshooting.md)

## Features

- Local BLE polling through Home Assistant's Bluetooth connection manager
- Automatic routing through local and ESPHome proxy adapters
- Temperature, relative humidity, optional CO₂, fan RPM, fan level/state, and
  remaining override time
- Low, Normal, Boost, and Purge timed override presets
- Separate Cancel override button
- Twelve documented diagnostic fault sensors
- Device Information reads for model, serial, and firmware details when exposed
- Preferred whole-packet transport with legacy 20-byte fragmented fallback
- Setup-code reauthentication without an OS Bluetooth bond
- Redacted Home Assistant diagnostics

For safety, this first release does not expose fan power-off, calibration,
configuration, schedules, resets, installer settings, or global airflow writes.

## Example action

The standard `fan.set_preset_mode` action uses the configurable default duration
(30 minutes initially). For an explicit duration between 1 and 28,800 seconds:

```yaml
action: ventaxia_multihome.set_timed_override
target:
  entity_id: fan.vent_axia_multihome_ventilation
data:
  preset: boost
  duration: 60
```

Cancel an override by pressing the integration's **Cancel override** button.
This is intentionally separate from turning the fan off.

## Supported devices and limitations

Discovery accepts the case-insensitive BLE local names `MEV` and `Multihome`.
The official client knows multiple model numbers, but compatibility with every
physical model and firmware combination still needs hardware validation.
`Multivent` is not matched because the documented Multihome protocol does not
establish a safe discovery or control path for it.

No physical unit was available during implementation. In particular, physical
testing must confirm characteristic availability across firmware versions,
fragmented transport fallback, post-restart setup-code behaviour, telemetry
scaling, fan-level display mapping, and timed override behaviour.

## Development

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/pytest -q
.venv/bin/ruff check custom_components tests scripts/validate_multihome.py
.venv/bin/python scripts/mev_protocol.py self-test
```

The runtime files HACS installs are entirely contained in
`custom_components/ventaxia_multihome/`. Protocol analysis workspaces, APKs, and
generated decompilation output are deliberately excluded from this repository.

## License and trademarks

Project code and documentation are available under the [MIT License](LICENSE).
Vent-Axia and related marks and artwork belong to their respective owners. This
project is independent and is not endorsed by Vent-Axia or Volution Group.

