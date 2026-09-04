# Changelog

## [0.6.3-rc.1] - 2026-09-04

### Added

- Add an exact-identity prerelease validation flow for Low-temperature
  protection (packet-136 field 16).
- Show the current protection state, complete stored temperature profile, and
  measured temperature before confirmation.

### Safety

- Restrict field 16 to model 10 / firmware 2.03.08 / hardware 01.00 while it
  awaits installed-unit validation.
- Require a recognised complete temperature profile, the opposite boolean
  value, explicit confirmation, a fresh full-record concurrency check, and
  exact packet-137 readback.
- Change only field 16. Enabling the flag may trigger the stored temperature
  behaviour immediately; the result screen directs the tester to observe the
  fan separately and restore Disabled through the same guarded flow.

## [0.6.2] - 2026-09-04

### Added

- Add disabled-by-default diagnostic entities for every decoded installer
  setting, retaining unknown values and raw codes without guessed meanings.
- Add guarded exact-identity configuration for CO₂/humidity thresholds,
  Rapid/Ambient humidity response, Comfort mode, Delay time, Overrun, Overrun
  time, and temperature actions/thresholds.

### Safety

- Require current/proposed review, confirmation, stale-snapshot rejection, and
  exact packet-137 readback for enabled writes.
- Keep Boost minimum field 4, Delay enabled field 7, Low-temperature protection
  field 16, unresolved input actions/analogue scaling, and the unaddressable
  purge-low byte read-only or blocked.
- Document temperature and switched-live timer evidence as storage validation;
  their runtime triggering was not demonstrated during physical testing.

## [0.6.2-rc.19] - 2026-09-04

### Fixed

- Correct the exact-identity expectations for the already-supported sensor
  thresholds and the now-read-only Boost minimum capability.

## [0.6.2-rc.18] - 2026-09-04

### Changed

- Promote packet-136 fields 8–10 and 17–20 to guarded exact-identity writes
  after each field was changed, read back exactly, and restored on the
  validated unit.
- Present the temperature flow as normal guarded configuration while retaining
  its one-field-at-a-time safety checks.
- Remove the Boost minimum validation candidate from the Configure menu; field
  4 remains available as a read-only diagnostic.

### Safety

- Keep Delay enabled field 7 blocked after its repeated physical readback
  mismatch, and keep Low-temperature protection field 16 permanently read-only.
- Record temperature writes as storage validation only. Changing an action and
  threshold did not change fan level, RPM, or state, so runtime temperature
  triggering remains unproven.
- Keep Boost minimum read-only because its 0%/1% storage test did not establish
  its operating meaning, dependencies, or safe general range.

## [0.6.2-rc.17] - 2026-09-04

### Added

- Add an exact-identity prerelease validation flow for Multihome temperature
  action and threshold fields 17–20.
- Show the complete current/proposed temperature profile and the current zone
  temperature before confirmation.

### Safety

- Keep field 16 (Low-temperature protection) read-only and require its current
  value to be Off before the validation flow is available.
- Permit exactly one field change per submission, accept only recovered Low,
  Boost, or Purge action codes, require `Low < High`, reject stale full-record
  reviews, and require exact packet-137 readback.
- Treat the flow as reversible storage validation rather than proof of runtime
  temperature behaviour.

## [0.6.2-rc.16] - 2026-09-02

### Added

- Decode the verified Multihome temperature action choices as Low (1), Boost
  (3), and Purge (4), while preserving every other byte as an explicit unknown
  code.
- Type the read-only low and high temperature threshold entities as Celsius,
  using the recovered 0–30 °C and 15–40 °C app ranges.

### Safety

- Keep packet-136 fields 16–20 read-only. The recovered MEV screen does not
  write field 16, contains visible defects, and does not establish a safe
  cross-threshold ordering rule or exact model applicability.
- Exclude the newer app's unrelated IAQ-manager 0–40 °C control from the
  Multihome evidence.

## [0.6.2-rc.15] - 2026-09-02

### Fixed

- Update the installer diagnostics regression to include Boost minimum field 4
  in the exact identity's validation-candidate list.

## [0.6.2-rc.14] - 2026-09-02

### Fixed

- Import the protocol exception used by the restricted Boost minimum
  pre-Bluetooth rejection regression.

## [0.6.2-rc.13] - 2026-09-02

### Added

- Add an exact-identity guarded validation flow for packet-136 field 4 after the
  validated unit reported a 0% Boost minimum baseline.

### Safety

- Restrict the prerelease flow and device API to the reversible 0%/1% envelope.
- Require complete current/proposed review, explicit acknowledgement, stale
  snapshot rejection, and exact fresh packet-137 readback.
- Keep general 0–100% configuration unavailable because the field's runtime
  meaning and relationship to the commissioned airflow profile are unproven.

## [0.6.2-rc.12] - 2026-09-02

### Added

- Add disabled-by-default read-only entities for all remaining decoded
  packet-137 installer values, including airflow settings, boost minimum,
  low-temperature fields, LS actions, analogue values/actions, digital actions,
  and raw purge-low mode.

### Safety

- Keep unresolved action codes, temperature units, analogue scaling, and
  purge-low semantics numeric and unitless rather than assigning guessed labels.
- Do not add any writable entity or widen the packet-136 capability profile.

## [0.6.2-rc.11] - 2026-09-01

### Fixed

- Make the Delay/Overrun stale-snapshot regression submit a real Delay timeout
  change now that the unsafe Delay On field is read-only.

## [0.6.2-rc.10] - 2026-09-01

### Fixed

- Correct the Home Assistant ppm-constant migration imports caught by Ruff.

## [0.6.2-rc.9] - 2026-09-01

### Fixed

- Block packet-136 field 7 after the validated unit returned a non-exact
  packet-137 result; Delay On remains visible as a read-only diagnostic.
- Include the requested field, differing offsets, expected record, and received
  record in future global-setting mismatch errors.
- Render Delay/Overrun descriptions with real line breaks.
- Replace Home Assistant's deprecated ppm constant with
  `UnitOfRatio.PARTS_PER_MILLION`.

### Validated

- Fields 8–10 (Overrun enabled, Overrun time, and Delay time) changed with exact
  readback on model 10 / firmware 2.03.08 / hardware 01.00. They remain
  prerelease candidates until restoration is explicitly confirmed.

## [0.6.2-rc.8] - 2026-09-01

### Fixed

- Align regression fixtures with Home Assistant selector validation, the new
  Delay/Overrun candidate scope, and the official 1–60 minute codec limits.

## [0.6.2-rc.7] - 2026-09-01

### Added

- Add disabled-by-default diagnostics for Delay On, Overrun, and both paired
  timeout values.
- Add an exact-identity guarded validation flow for packet-136 fields 7–10.

### Safety

- Use the official Multihome 1–60 minute limits and LS-input-only semantics.
- Treat each enabled flag and timeout as one pair: write a valid timeout before
  enabling, and disable the flag before changing its timeout.
- Require strict booleans, valid current timers, full 36-byte snapshot review,
  explicit acknowledgement, stale-snapshot rejection, and exact fresh
  packet-137 readback after every changed field.
- Keep fields 7–10 in the validation-candidate profile until physical
  change/readback/restore evidence is recorded.

## [0.6.2-rc.6] - 2026-09-01

### Fixed

- Keep a physically validated Boolean installer field non-writable when its
  current firmware byte is outside the strict `0`/`1` encoding, while
  retaining the raw byte and explicit unknown-value status in diagnostics.

## [0.6.2-rc.5] - 2026-09-01

### Validated

- On model 10, firmware 2.03.08, hardware 01.00, disabled Comfort
  mode, received exact fresh packet-137 readback, and restored enabled through
  the same guarded flow.
- Promote packet-136 field 6 from validation candidate to the exact identity's
  physically validated installer-write profile.

## [0.6.2-rc.4] - 2026-09-01

### Fixed

- Keep the negative unsupported-field regression test on Delay rather than the
  newly enabled Comfort validation candidate.

## [0.6.2-rc.3] - 2026-09-01

### Added

- Add a disabled-by-default read-only diagnostic binary sensor for the recovered
  Comfort mode flag.
- Add an exact-identity guarded validation flow for packet-136 field 6.

### Safety

- Keep Comfort mode in the validation-candidate matrix until a physical
  change/readback/restore test confirms firmware behaviour.
- Require a strict boolean current value, complete 36-byte settings snapshot,
  explicit review and acknowledgement, stale-snapshot rejection, and exact
  fresh packet-137 readback.
- Do not infer temperature, timing, or airflow behaviour from the flag name.

## [0.6.2-rc.2] - 2026-09-01

### Validated

- On model 10, firmware 2.03.08, hardware 01.00, independently changed
  Ambient and Rapid humidity response, received exact fresh packet-137
  readback, and restored each original value through the same guarded flow.
- Promote packet-136 fields 14 and 15 from validation candidates to the exact
  identity's physically validated installer-write profile.

## [0.6.2-rc.1] - 2026-09-01

### Added

- Add disabled-by-default diagnostic binary sensors for the recovered Rapid and
  Ambient humidity-response installer flags.
- Add a guarded validation flow for packet-136 fields 14 and 15 on model 10 /
  firmware 2.03.08 / hardware 01.00.

### Safety

- Keep both fields in the prerelease validation-candidate matrix until a
  physical change/readback/restore test confirms firmware behaviour.
- Require strict booleans, a complete current packet-137 record, explicit
  review and confirmation, an unchanged 36-byte snapshot, and exact fresh
  readback after each changed field.
- Keep the diagnostic entities read-only and disabled by default; writes remain
  available only through the guarded Configure flow.

## [0.6.1] - 2026-09-01

### Added

- Add disabled-by-default diagnostic entities for the packet-137 humidity,
  CO₂ boost and CO₂ purge installer thresholds.
- Add a guarded Configure flow for those three thresholds on the physically
  validated model 10 / firmware 2.03.08 / hardware 01.00 identity.

### Safety

- Require complete current and proposed values, strict `CO₂ Boost < CO₂ Purge`
  ordering, ten-ppm CO₂ steps, explicit confirmation and an unchanged complete
  36-byte settings snapshot before writing.
- Apply only changed fields in an order that preserves CO₂ threshold ordering,
  with exact packet-137 readback after every packet-136 write.
- Report partial or uncertain failure without claiming the complete profile was
  applied; subsequent writes remain blocked until a fresh poll recovers state.

### Validated

- On model 10, firmware 2.03.08, hardware 01.00, changed humidity/CO₂ thresholds
  from `81/1500/1750` to `82/1550/1800` and restored `81/1500/1750`.
- Every field change and restoration returned exact fresh packet-137 readback,
  with all three original installer values confirmed after restoration.

## [0.6.0] - 2026-09-01

### Changed

- Add a version-controlled installer capability matrix covering all 11 recovered
  model classifications and all 33 packet-136 field definitions, including
  encoding, unit, range, dependencies, risk and evidence level.
- Select installer-write capabilities from the complete device identity. Airflow
  writes now require the physically validated model 10 / firmware 2.03.08 /
  hardware 01.00 combination; missing or unvalidated identities expose no
  installer controls.
- Route the existing packet-136 airflow write guard through the documented field
  matrix. All other decoded installer fields remain read-only.
- Add read-only installer capability diagnostics with field metadata, raw source
  values, explicit unknown-value labels and exact identity/write selection.
- Redact the BLE address and config-entry unique ID alongside the setup code in
  downloaded diagnostics.

## [0.5.0] - 2026-09-01

### Added

- Add guarded management of all six silent-hours slots for physically validated
  model 10 units, including create, edit and explicitly confirmed deletion.
- Display schedules in Home Assistant's configured local time while converting
  to the UTC clock used by model 10 firmware 2.03.08.
- Decode indexed, selected-record and Raw-wrapped packet-49 responses, including
  the checksummed 14-byte table form returned by the validated firmware.

### Safety

- Require a complete known six-slot table before exposing writes and reread the
  entire table after every mutation.
- Retain the previous confirmed table and block further writes after timeout,
  disconnect, unsupported data or mismatched readback until polling recovers.
- Compare reviewed schedules by slot index and nine-byte record or empty state,
  ignoring volatile packet metadata while still rejecting real table changes.
- Keep schedule writes disabled on models whose packet-49 behaviour has not been
  physically validated.

### Validated

- On model 10, firmware 2.03.08, hardware 01.00, confirmed all six indexed reads,
  checksums, schedule creation, exact readback, UTC/local-time activation and
  deletion of a populated slot back to a confirmed empty record.
- Confirmed active schedules report `silent_hour` independently of their genuine
  fan level and RPM; the integration retains the reported level 2 and 625 RPM.
- Confirmed the recovered `0xffff` deletion marker is accepted by the unit. The
  RC5 deletion failure occurred before transmission in a volatile-byte safety
  comparison and is corrected by the stable semantic concurrency guard.

### Notes

- Packet 49 has no time-zone or DST field, so recurring schedules use Home
  Assistant's current UTC offset and should be reviewed after a clock change.

## 0.5.0-rc.6

- Compare the reviewed six-slot silent-hours table by slot index and actual
  nine-byte schedule record or empty state, so changing packet metadata and CRC
  values cannot falsely block confirmation before a write.
- Retain the existing concurrency guard for genuine schedule changes and keep
  the original raw packet-49 responses available in diagnostics.

## 0.5.0-rc.5

- Convert schedule times between Home Assistant's configured local time and the
  UTC clock physically observed on model 10 firmware 2.03.08.
- Rotate weekday masks when the current UTC offset crosses midnight and document
  the packet format's daylight-saving limitation.

## 0.5.0-rc.4

- Decode the model-10 firmware's 14-byte packet-49 response only when its final
  byte validates as the Zirconia CRC of the documented 13-byte table item.
- Preserve the complete checksummed response in diagnostics and enable schedule
  writes only after all six indexed records pass checksum and structure checks.

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
