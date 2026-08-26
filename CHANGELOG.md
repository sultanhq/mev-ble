# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- Complete MEV pairing automatically by reading and confirming the internal
  application code exposed while the physical unit is in pairing mode.
- Remove the incorrect user-facing PIN field and show pairing instructions for
  both manual setup and automatic Bluetooth discovery.
- Show a readable message when another setup flow for the device is active.
- Serialize connection checks with complete poll/control operations so a failed
  transaction cannot leave a concurrent refresh using cleared connection state.

## [0.1.0] - 2026-08-25

### Added

- Initial HACS-compatible Home Assistant integration.
- Bluetooth discovery for `MEV` and `Multihome` units.
- Application-level setup-code authentication without OS Bluetooth bonding.
- Whole-packet protocol transport and legacy fragmented fallback.
- Zone telemetry, system status, device information, and diagnostic faults.
- Low, Normal, Boost, and Purge timed overrides plus independent cancellation.
- ESPHome Wi-Fi and Ethernet Bluetooth proxy examples and end-user guides.

[Unreleased]: https://github.com/robadams/mev-ble/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/robadams/mev-ble/releases/tag/v0.1.0
