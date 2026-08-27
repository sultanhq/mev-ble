# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-08-27

### Added

- Add physically validated Low, Normal, Boost, and Purge timed airflow controls
  plus the official BLE Cancel operation.
- Add an explicit-duration Home Assistant action and control-state diagnostics.

### Changed

- Keep control writes, fresh telemetry readback, and reconnect state serialized.
- Exclude unvalidated On, Off, and Stop commands from the Home Assistant surface.

### Fixed

- Preserve confirmed state across failed Bluetooth operations and reconnect
  automatically through the retained local/proxy route.
- Treat invalid CO₂ values as unavailable and locally estimate an HA-started
  countdown when tested MEV firmware reports a false zero.

### Validated

- Physically confirmed all four speed levels, Cancel, timed automatic expiry,
  estimated countdown, unavailable-state retention, and automatic ESPHome proxy
  recovery on the recorded fragmented-transport MEV setup.

## [0.2.0-rc.2] - 2026-08-27

### Fixed

- Count down a successfully commanded override locally when active MEV firmware
  reports a false zero remaining time. Device-reported nonzero values remain
  authoritative, and unknown externally started durations become unavailable.

## [0.2.0-rc.1] - 2026-08-27

### Added

- Add a repeatable v0.2 physical validation matrix and evidence record covering
  speed levels, RF-matched timers, cancellation, failed actions, reconnects, and
  BLE routes.
- Include fan speed, RPM, override time, and coordinator success in redacted
  diagnostics for reproducible control validation.

### Changed

- Align the Home Assistant control surface with the physically inspected MEV RF
  remote: expose speed levels 1–4 and timed overrides, but not inferred On, Off,
  or Stop commands. Keep recovered ventilation-mode bytes in the offline codec
  only.
- Derive fan power state from confirmed speed and RPM telemetry, and preserve the
  last confirmed state when a Bluetooth control request fails.
- Keep each fan write and its zone/system telemetry readback inside one device
  operation, preventing scheduled polls from interleaving with control results.
- Publish control state only from the fresh readback; failed readbacks retain the
  preceding snapshot while immediately updating entity availability.
- Bring whole-packet controls to automated parity with fragmented framing,
  including cooperative not-ready polling and cancellation on timeout,
  interruption, or GATT failure.

### Fixed

- Treat zero, negative, and non-finite CO₂ telemetry as unavailable so a
  transient invalid reading after reconnect cannot create a false zero spike in
  Home Assistant history.

## [0.1.1] - 2026-08-26

### Changed

- Document that a local Home Assistant Bluetooth adapter is sufficient when it
  is in reliable range and that an ESP32 proxy is optional.
- Add practical guidance from initial MEV hardware testing: the unit may have
  poor Bluetooth range and can benefit from a nearby active ESPHome proxy.
- Expand regression coverage for send-only controls, serialized operations, and
  reconnecting after Bluetooth scanner-cache expiry.

### Fixed

- Complete MEV pairing automatically by reading and confirming the internal
  application code exposed while the physical unit is in pairing mode.
- Remove the incorrect user-facing PIN field and show pairing instructions for
  both manual setup and automatic Bluetooth discovery.
- Show a readable message when another setup flow for the device is active.
- Serialize connection checks with complete poll/control operations so a failed
  transaction cannot leave a concurrent refresh using cleared connection state.
- Complete packet-56 fan controls after their documented transport
  acknowledgements, then confirm the new state through the normal telemetry poll.
- Retain the last known proxy/device route so a dropped long-lived connection can
  reconnect after its advertisement has expired from the Bluetooth scanner cache.

## [0.1.0] - 2026-08-25

### Added

- Initial HACS-compatible Home Assistant integration.
- Bluetooth discovery for `MEV` and `Multihome` units.
- Application-level setup-code authentication without OS Bluetooth bonding.
- Whole-packet protocol transport and legacy fragmented fallback.
- Zone telemetry, system status, device information, and diagnostic faults.
- Low, Normal, Boost, and Purge timed overrides plus independent cancellation.
- ESPHome Wi-Fi and Ethernet Bluetooth proxy examples and end-user guides.

[Unreleased]: https://github.com/sultanhq/mev-ble/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/sultanhq/mev-ble/compare/v0.1.2...v0.2.0
[0.2.0-rc.2]: https://github.com/sultanhq/mev-ble/compare/v0.2.0-rc.1...v0.2.0-rc.2
[0.2.0-rc.1]: https://github.com/sultanhq/mev-ble/compare/v0.1.2...v0.2.0-rc.1
[0.1.2]: https://github.com/sultanhq/mev-ble/releases/tag/v0.1.2
[0.1.1]: https://github.com/sultanhq/mev-ble/releases/tag/v0.1.1
[0.1.0]: https://github.com/sultanhq/mev-ble/commit/42bd8ee
