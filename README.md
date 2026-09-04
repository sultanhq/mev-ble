# Vent-Axia Multihome for Home Assistant

[![HACS validation](https://github.com/sultanhq/mev-ble/actions/workflows/validate.yml/badge.svg)](https://github.com/sultanhq/mev-ble/actions/workflows/validate.yml)
[![Tests](https://github.com/sultanhq/mev-ble/actions/workflows/tests.yml/badge.svg)](https://github.com/sultanhq/mev-ble/actions/workflows/tests.yml)

A local Home Assistant custom integration for the documented Bluetooth Low
Energy interface used by Vent-Axia MEV/Multihome ventilation units. It exposes
telemetry, faults, timed airflow presets, and override cancellation without a
cloud account.

> [!IMPORTANT]
> Physical testing on an MEV unit has confirmed automatic pairing, fragmented
> telemetry polling, all four timed speeds, Cancel, timed expiry, and automatic
> recovery through an ESPHome Bluetooth proxy. Other models, firmware versions,
> whole-packet transport, and Bluetooth adapters still need wider validation, so
> please report your unit model, firmware, and results.

## What you need

- Home Assistant 2026.8.0 or newer with the Bluetooth integration enabled.
- A Vent-Axia unit advertising as `MEV` or `Multihome`.
- A connectable Bluetooth route from Home Assistant to the unit. Either a local
  Bluetooth adapter or an **active** ESPHome Bluetooth proxy can provide it.
- Access to the unit's physical pairing/setup mode. The integration completes
  the internal MEV application-code exchange automatically; Bluetooth itself
  remains OS-pairing/PIN-less. See
  [Setup code versus Bluetooth PIN](docs/configuration.md#setup-code-versus-bluetooth-pin).

## Choosing the Bluetooth route

An ESP32 is **optional**. If the Home Assistant host already has a supported
Bluetooth adapter and the MEV is within reliable range, the integration can
connect directly through that adapter.

The physically tested MEV had poor practical Bluetooth range. If discovery is
intermittent, setup fails, or entities become unavailable, place an active
ESPHome Bluetooth proxy close to the ventilation controller. An original
ESP32/ESP32-WROOM-32 development board is the safest inexpensive proxy choice.
See the complete [ESPHome proxy guide](docs/esphome-bluetooth-proxy.md) and the
ready-to-copy [Wi-Fi proxy example](examples/esp32-bluetooth-proxy.yaml).

## Quick start

1. Use Home Assistant's local Bluetooth adapter if it reaches the MEV reliably.
   Otherwise, flash and add an
   [active ESPHome Bluetooth proxy](docs/esphome-bluetooth-proxy.md) nearby.
2. In HACS, open the top-right menu, choose **Custom repositories**, add
   `https://github.com/sultanhq/mev-ble`, and select **Integration** as the type.
3. Open the new **Vent-Axia Multihome** repository in HACS, choose **Download**,
   and restart Home Assistant.
4. Put the Vent-Axia unit into Bluetooth pairing/setup mode.
5. In Home Assistant, open **Settings → Devices & services** and configure the
   discovered **Vent-Axia Multihome** device. If it is not shown, choose
   **Add integration** and search for the same name.
6. Select **Submit** while the blue LED is flashing. No PIN is required.

HACS calls this repository type an *Integration*. There is no dashboard plugin
or ESP32-specific Vent-Axia firmware to install.

Detailed instructions:

- [Install and update with HACS](docs/installation.md)
- [Choose and configure an ESPHome Bluetooth proxy](docs/esphome-bluetooth-proxy.md)
- [Configure the integration and use its entities/actions](docs/configuration.md)
- [Safely calibrate an internal CO₂ sensor](docs/co2-calibration.md)
- [Global-settings safety model](docs/global-settings.md)
- [Troubleshooting and diagnostics](docs/troubleshooting.md)
- [v0.2 physical validation procedure and evidence](docs/validation-v0.2.md)

## Features

- Local BLE polling through Home Assistant's Bluetooth connection manager
- Automatic routing through local and ESPHome proxy adapters
- Temperature, relative humidity, optional CO₂, fan RPM, fan level/state, and
  device-reported or guarded locally estimated remaining override time
- Low, Normal, Boost, and Purge timed override presets
- Separate Cancel override button
- Twelve documented diagnostic fault sensors
- Device Information reads for model, serial, and firmware details when exposed
- Preferred whole-packet transport with legacy 20-byte fragmented fallback
- Automatic application-code pairing and reauthentication without an OS bond
- Serialized controls/polling and automatic reconnection after a dropped link
- Guarded internal CO₂ calibration for validated CO₂-equipped models, using
  either documented fresh-air exposure or trusted Home Assistant references
- Lossless global-settings snapshots in redacted diagnostics
- Disabled-by-default read-only diagnostic entities for every decoded installer
  value; unknown units, scaling, and action meanings remain explicit raw codes
- Guarded four-level airflow commissioning for a validated device identity,
  with exact readback and explicit review
- Read-only Boost minimum diagnostics; its reversible 0%/1% storage test did
  not establish a safe general configuration range or operating meaning
- Guarded CO₂/humidity threshold configuration on the exact physically validated
  model/firmware/hardware identity
- Guarded Rapid/Ambient humidity-response configuration on the exact physically
  validated model/firmware/hardware identity
- Guarded Comfort-mode configuration on the exact physically validated
  model/firmware/hardware identity
- Guarded one-field-at-a-time temperature action and threshold configuration
  on the exact validated identity while Low-temperature protection remains Off
- Guarded Low-temperature protection configuration on the exact physically
  validated identity, with full-profile review and exact readback
- Guarded Delay time, Overrun, and Overrun time configuration on the exact
  validated identity; the failed Delay enabled field remains read-only
- Redacted Home Assistant diagnostics

The physically inspected MEV remote exposes speed levels 1–4 and timers for 30,
60, 120, and 240 minutes, with no On, Off, Stop, or Cancel control. The
integration therefore does not expose inferred ventilation-mode power commands.
Cancel override is retained as a separate official-app protocol operation; it
returns control to the unit and is not a power action. Internal CO₂ calibration
is available only through a guarded Configure flow on validated CO₂-equipped
models. The exact validated model 10 / firmware 2.03.08 / hardware 01.00
identity can configure Low, Normal, Boost, and Purge through a guarded Configure
flow. These values are commissioned motor-speed percentages, not measured RPM;
the flow applies documented limits, preserves strict ordering, and requires
exact immediate readback. Other environmental options, resets, and unvalidated
installer controls remain unavailable. Version 0.5 adds
guarded six-slot silent-hours management for validated model 10 hardware; see
[Silent-hours schedules](docs/silent-hours.md) for semantics and validation.

Internal CO₂ calibration can be run again after its five-minute safety cooldown.
Home Assistant does not expose the unvalidated general `RestoreDefaults` packet
as a calibration reset because its payload and scope were not recovered and it
may affect wider unit settings. To correct a previous calibration, repeat the
guarded flow with a properly prepared reference. The Vent-Axia option defaults
to 450 ppm, accepts a manually entered 400–2,000 ppm value like the official
app, and can instead use trusted Home Assistant CO₂ references. See the
[calibration guide](docs/co2-calibration.md#rerunning-or-correcting-calibration).

## Example action

The standard `fan.set_preset_mode` action uses the configurable default duration
(30 minutes initially). For an explicit duration between 1 and 28,800 seconds:

```yaml
action: ventaxia_multihome.set_timed_override
target:
  entity_id: fan.vent_axia_multihome_ventilation
data:
  preset: boost
  duration: 1800
```

Cancel an override by pressing the integration's **Cancel override** button.
The recovered BLE app protocol includes cancellation even though the tested RF
remote has no Cancel button. It is intentionally separate from fan power.

## Supported devices and limitations

Discovery accepts the case-insensitive BLE local names `MEV` and `Multihome`.
The official client knows multiple model numbers, but compatibility with every
physical model and firmware combination still needs hardware validation.
`Multivent` is not matched because the documented Multihome protocol does not
establish a safe discovery or control path for it.

Initial testing has used one physical unit advertising as `MEV` through an
ESPHome proxy. Automatic pairing, fragmented telemetry, and long-running
connection recovery have been observed on that setup. Guarded airflow
commissioning is writable only on model 10 / firmware 2.03.08 / hardware 01.00;
the threshold flow is restricted to that same identity after exact physical
change-and-restore validation. Version 0.6.2 prereleases additionally expose
Rapid/Ambient response as read-only diagnostics and a guarded exact-identity
configuration flow after both flags were independently changed, read back and
restored. Models 1, 2 and 9 retain read-only settings
diagnostics. Whole-packet transport,
other model/firmware combinations, telemetry scaling across the full operating
range, and broader timed-override behaviour still need more hardware reports.
The v0.2 speed/timer and Cancel matrix is explicitly tracked in the linked
validation record. Recovered Off/Stop mode bytes remain offline research and are
not sent by the Home Assistant integration.

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
