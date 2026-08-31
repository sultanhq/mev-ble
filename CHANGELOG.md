# Changelog

## 0.5.0-rc.3

- Preserve unsupported packet-49 responses for all six requested slots instead
  of failing integration setup, so normal telemetry remains available.
- Keep silent-hours writes disabled when any slot response is not understood and
  expose the exact raw response plus its decoding status in diagnostics.

## 0.5.0-rc.2

- Accept model-10 firmware responses that return the selected nine-byte
  silent-hours record without its indexed table header.
- Accept the same selected-slot response in the protocol's Raw DataObjectArray
  envelope, while retaining the original bytes in diagnostics.
- Include the exact response length and hexadecimal payload when an unknown
  firmware form is rejected.

## 0.5.0-rc.1

- Read all six packet-49 silent-hours slots during each serialized device poll.
- Add lossless nine-byte record and thirteen-byte indexed-table codecs, including
  daytime, overnight, empty, malformed and unknown-record handling.
- Add guarded create, edit and delete operations with exact full-table readback.
- Retain the previous confirmed table and disable further writes after rejection,
  timeout, disconnect or mismatched readback until a successful poll recovers it.
- Add a Home Assistant management flow with time selectors, named weekdays,
  explicit overnight semantics and confirmed deletion of populated slots.
- Enable the release-candidate flow only on model 10 while physical validation is
  completed. Other models retain read-only safety behaviour.

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.0] - 2026-08-31

### Added

- Release the guarded four-level airflow commissioning flow for physically
  validated model 10 units.
- Commit the supported-field matrix, official per-level percentage ranges, and
  model-specific write status to the user documentation.

### Safety

- Limit stable packet-136 writes to model 10. Models 1, 2 and 9 retain decoded
  read-only diagnostics until their own hardware evidence exists.
- Keep humidity, comfort, temperature, timeout, input-action, CO₂-threshold,
  schedule, and restore-default fields read-only because their remaining
  protocol semantics or model-specific write behaviour are medium confidence.
- Reject confirmation when the current settings snapshot is unavailable and
  recover the flow automatically after Bluetooth connectivity returns.

### Validated

- On model 10, firmware 2.03.08, hardware 01.00, physically changed the complete
  profile from 6/8/37/50% to 7/9/38/51% and restored it exactly.
- Confirmed only raw-record offsets 0–3 changed, each by one, while the other 32
  bytes remained identical through the four-field update and restoration.
- Confirmed exact readback, write readiness, unavailable-state rejection with
  no setting changed, ESPHome proxy recovery, and successful post-recovery use.

### Notes

- A complete Home Assistant restart and client refresh may be required after a
  HACS update because Home Assistant retains custom-integration translations
  for the lifetime of the process.

## [0.4.0-rc.3] - 2026-08-30

### Added

- Add a guarded **Configure airflow levels** options flow for validated
  four-speed MEV models, with one review screen and explicit confirmation.
- Show the current and proposed Low, Normal, Boost, and Purge motor-speed
  percentages without presenting measured fan RPM as a configuration value.

### Safety

- Enforce the official per-level ranges (Low 1–97%, Normal 2–98%, Boost
  3–99%, Purge 4–100%) and strict `Low < Normal < Boost < Purge` ordering.
- Plan changed fields so every intermediate one-field update also retains valid
  ordering, and require exact packet-137 readback before sending the next field.
- Recheck the complete settings record at final confirmation and reject stale,
  unavailable, unsupported, fractional, unchanged, or unconfirmed updates.
- Keep environmental, input-action, timeout, and CO₂-threshold settings
  read-only while their model semantics and physical write behaviour remain
  unvalidated.

### Validation

- Mark this prerelease for a minimal reversible physical test on the recorded
  model 10 / firmware 2.03.08 unit: Low 6% → 7% → 6%. Stable v0.4.0 remains
  blocked until the packet-136 write and unrelated-byte preservation are proven.

## [0.4.0-rc.2] - 2026-08-30

### Added

- Add the internal packet 136 field-update codec for all 33 official-app field
  IDs, including RawWithId framing and the distinct UInt16LE CO₂ encoding.
- Record whether the device currently has a successful global-settings read
  that could safely precede a guarded update.

### Safety

- Reject unknown IDs, invalid types, unsafe percentage/threshold values, and
  non-representable CO₂ increments before any Bluetooth write.
- Require a current 36-byte settings snapshot, use target zero instead of the
  decompiled target-byte anomaly, and immediately read packet 137 after every
  internal update.
- Commit updated state only when every readback byte matches the expected
  one-field change. Timeout, disconnect, malformed data, or mismatch retains
  the last confirmed snapshot and blocks another update until a fresh poll.
- Keep the update layer internal in this release candidate; Home Assistant
  does not expose a settings entity, action, service, or flow yet.

## [0.4.0-rc.1] - 2026-08-30

### Added

- Decode the complete 36-byte MEV global-settings record, including airflow
  percentages, environmental options, timeouts, input actions, and CO₂
  thresholds.
- Include every decoded value, the original raw record, and any invalid
  boolean fields in redacted Home Assistant diagnostics.

### Safety

- Keep all global settings read-only in this release candidate. Unknown action
  values and malformed flags remain numeric or unavailable rather than being
  assigned guessed semantics, and malformed records fail as a complete unit.

## [0.3.0] - 2026-08-30

### Added

- Guarded internal CO₂ calibration from the integration's Configure flow,
  using the official app's 450 ppm default, a manual 400–2,000 ppm reference,
  or one or more trusted Home Assistant CO₂ sensors.
- Mandatory preparation and confirmation steps, a five-minute persisted safety
  cooldown, and an honest three-minute elapsed-time progress display.
- Calibration routing and outcome evidence in redacted diagnostics.

### Validated

- Physically validated the built-in-sensor route on Multihome firmware 2.03.08:
  an 800 ppm calibration produced 799 ppm, and a subsequent 450 ppm calibration
  restored the reading to 452 ppm.
- Confirmed repeat calibration, manual reference preservation, address-zero
  MEV control-unit routing, and the required Raw DataObjectArray payload.

## [0.3.0-rc.10] - 2026-08-30

### Fixed

- Wrap the four-byte CO₂ calibration body in the Raw DataObjectArray emitted
  by the official app. Earlier release candidates produced a valid transport
  packet that the MEV acknowledged but ignored because its calibration payload
  omitted this required wrapper.

## [0.3.0-rc.9] - 2026-08-30

### Fixed

- Route built-in CO₂ calibration through an address-zero MEV control-unit
  row (device type 10) when a validated CO₂-capable model has no separate
  internal-sensor row. A standalone internal CO₂ sensor row (device type 6)
  remains preferred when present. This matches the official app's two routing
  paths and the device table reported by physical Multihome hardware.

## [0.3.0-rc.8] - 2026-08-30

### Fixed

- Distinguish failures that occur before the CO₂ calibration packet is sent
  from uncertain final-write failures. Pre-write failures can now be retried
  immediately; only a successful or potentially delivered write starts the
  five-minute cooldown.
- Replace the misleading generic retry message with separate not-sent and
  delivery-uncertain guidance.

### Diagnostics

- Record the device-table version, discovered routing rows, selected internal
  CO₂ target, outcome, and non-sensitive error from the latest calibration
  attempt so hardware-specific routing failures can be identified.

## [0.3.0-rc.7] - 2026-08-30

### Fixed

- Wait for a bounded, address-specific connectable advertisement during initial
  setup when an ESPHome Bluetooth proxy is scanning but has not yet populated
  Home Assistant's device cache. If the proxy or device is still unavailable,
  defer through Home Assistant's normal setup retry instead of requiring a
  manual integration reload.

## [0.3.0-rc.6] - 2026-08-29

### Fixed

- Resolve the internal CO₂ sensor's device-table address before sending packet
  116, matching the official app instead of always targeting master address 0.

### Changed

- Match the current official app with a 450 ppm calibration default and a
  validated manual 400–2,000 ppm input, while retaining trusted Home Assistant
  reference-sensor averaging.

## [0.3.0-rc.5] - 2026-08-28

### Fixed

- Make the calibration progress page self-contained when a Home Assistant
  client remains on it at 100% instead of displaying the result step: 100% is
  explicitly identified as elapsed, with safe close guidance.

## [0.3.0-rc.4] - 2026-08-28

### Safety

- Persist the five-minute CO₂ calibration cooldown across integration reloads
  and Home Assistant restarts so an uncertain or completed command cannot be
  repeated early by recreating the coordinator.

## [0.3.0-rc.3] - 2026-08-28

### Changed

- Refer to an installed MEV/Multihome unit that may be out of sight or easy
  reach, without assuming a particular installation location.
- Explain that calibration can be run again after the five-minute safety
  cooldown, while the unvalidated general `RestoreDefaults` packet is not a
  safe calibration reset and remains unavailable.
- Identify 400 ppm as Vent-Axia's fixed fresh-air assumption rather than a live
  worldwide or local outdoor measurement.

## [0.3.0-rc.2] - 2026-08-27

### Added

- Display Home Assistant progress for the Vent-Axia manual's documented
  three-minute internal-CO₂ sampling period after successful command delivery.

### Changed

- Remove the need to observe an installed MEV/Multihome unit: preparation and
  result screens now use the Home Assistant progress bar, with the magenta LED
  retained only as optional physical evidence.
- Distinguish elapsed sampling time from verified firmware completion because
  the recovered BLE protocol exposes no calibration-status readback.
- Clarify that the manual's five-minute condition applies to paired room sensors;
  this integration targets only the MEV internal sensor and tracks three minutes.

## [0.3.0-rc.1] - 2026-08-27

### Added

- Add a deliberately guarded internal CO₂ calibration flow for validated
  CO₂-equipped model numbers, with documented 400 ppm fresh-air exposure as the
  recommended method.
- Add an advanced option that averages one or more independent Home Assistant
  CO₂ references and re-validates their current ppm states at confirmation.
- Add a physical v0.3 validation guide covering device indication, safety
  interlocks, failure handling, and the absence of automatic retries.

### Safety

- Require a separate opt-in confirmation immediately before the BLE write;
  calibration is not exposed as an entity, service, action, or automation.
- Reject unavailable, non-ppm, non-finite, out-of-range, and self-referential
  sensors, and rate-limit attempts for five minutes even after transport errors.
- Describe protocol delivery accurately without claiming firmware completion or
  calibration readback.

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

[Unreleased]: https://github.com/sultanhq/mev-ble/compare/v0.3.0-rc.7...HEAD
[0.3.0-rc.7]: https://github.com/sultanhq/mev-ble/compare/v0.3.0-rc.6...v0.3.0-rc.7
[0.3.0-rc.6]: https://github.com/sultanhq/mev-ble/compare/v0.3.0-rc.5...v0.3.0-rc.6
[0.3.0-rc.5]: https://github.com/sultanhq/mev-ble/compare/v0.3.0-rc.4...v0.3.0-rc.5
[0.3.0-rc.4]: https://github.com/sultanhq/mev-ble/compare/v0.3.0-rc.3...v0.3.0-rc.4
[0.3.0-rc.3]: https://github.com/sultanhq/mev-ble/compare/v0.3.0-rc.2...v0.3.0-rc.3
[0.3.0-rc.2]: https://github.com/sultanhq/mev-ble/compare/v0.3.0-rc.1...v0.3.0-rc.2
[0.3.0-rc.1]: https://github.com/sultanhq/mev-ble/compare/v0.2.0...v0.3.0-rc.1
[0.2.0]: https://github.com/sultanhq/mev-ble/compare/v0.1.2...v0.2.0
[0.2.0-rc.2]: https://github.com/sultanhq/mev-ble/compare/v0.2.0-rc.1...v0.2.0-rc.2
[0.2.0-rc.1]: https://github.com/sultanhq/mev-ble/compare/v0.1.2...v0.2.0-rc.1
[0.1.2]: https://github.com/sultanhq/mev-ble/releases/tag/v0.1.2
[0.1.1]: https://github.com/sultanhq/mev-ble/releases/tag/v0.1.1
[0.1.0]: https://github.com/sultanhq/mev-ble/commit/42bd8ee
