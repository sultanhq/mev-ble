# v0.2 physical validation

This is the release-gate procedure and evidence record for the v0.2 timed
fan-control milestone. Automated tests prove packet encoding, transport
behaviour, locking, and Home Assistant state reconciliation. Physical evidence
defines which controls are safe to expose.

## Current evidence status

| Item | Recorded evidence | Status |
|---|---|---|
| Unit | Physical Vent-Axia unit advertising as `MEV`; model and firmware were not recorded | Observed |
| RF controls | Speed levels 1–4 and timers for 30, 60, 120, and 240 minutes | Physically reported |
| Absent RF controls | No On, Off, Stop, or Cancel button | Physically reported |
| Bluetooth path | Active ESPHome Bluetooth proxy placed near the controller | Observed |
| Transport | Fragmented transport, identified by Home Assistant's earlier `fragmented transaction timed out` action error | Observed |
| Discovery and pairing | Discovery, automatic application-code pairing, and entity creation | Observed |
| Telemetry | Fan entity and telemetry were available in Home Assistant | Observed |
| Connection recovery | Proxy removal made entities unavailable; restoring it recovered fresh previous state without a reload, and the skipped unavailable action was not applied | Physically passed |
| Low | Speed 1 became active and telemetry reported `user_override` on v0.2.0-rc.1 | Physically passed |
| Normal, Boost, Purge | All four presets physically changed the MEV to levels 1–4 on RC2 | Physically passed |
| Timer values | A 70-second BLE override counted down and returned automatically; firmware reports zero, so RC2 estimates HA-started time locally | Physical expiry passed; exact long-duration encodings automated |
| Cancel override | Recovered from the official BLE app; physically returned the tested unit to automatic control | Physically passed |
| On, Off, Stop | Recovered mode bytes have no matching tested hardware control and are not exposed by HA | Excluded from v0.2 |
| Whole-packet transport | Automated parity tests pass; no physical unit using this transport is recorded | Pending hardware report |
| Local HA Bluetooth adapter | Supported by Home Assistant's Bluetooth manager but not used in the recorded MEV test | Not tested |

The existing observations support a safe speed-and-timer control surface. They
do not justify sending inferred ventilation-mode power commands. Off and Stop
remain in the offline protocol codec as reverse-engineering evidence only.

## Safety and prerequisites

- Test only when changing ventilation speed is safe for the building and its
  occupants. Do not test during an alarm or when ventilation is required for
  combustion, fumes, excessive humidity, or another safety function.
- Close the official Vent-Axia app so it cannot compete for the BLE connection.
- Use one v0.2 release-candidate commit for the whole run and record its SHA.
- Download integration diagnostics before and after the run. Record the reported
  model, firmware, selected transport, Home Assistant version, ESPHome version,
  and local-adapter or proxy hardware.
- Do not attempt `fan.turn_off`, `fan.turn_on`, or a Stop action. They are
  intentionally unsupported by this release.

## Supported control matrix

Speed rows use protocol packet type 56, operation `DataRequest` (2), command
Set speed (1), target 0, zone 0, and the recovered default mode byte 3.

| Home Assistant preset | RF speed | Preset byte | Recommended test timeout | Expected fresh telemetry |
|---|---:|---:|---:|---|
| Low | 1 | 1 | 1,800 seconds | Fan level 1; user override; device or estimated time near 1,800 |
| Normal | 2 | 2 | 1,800 seconds | Fan level 2; user override; device or estimated time near 1,800 |
| Boost | 3 | 3 | 1,800 seconds | Fan level 3; user override; device or estimated time near 1,800 |
| Purge | 4 | 4 | 1,800 seconds | Fan level 4; user override; device or estimated time near 1,800 |

The RF timer values map to BLE timeouts as follows:

| RF timer | BLE timeout |
|---:|---:|
| 30 minutes | 1,800 seconds |
| 60 minutes | 3,600 seconds |
| 120 minutes | 7,200 seconds |
| 240 minutes | 14,400 seconds |

The recovered BLE app permits other durations up to 28,800 seconds, so Home
Assistant retains an explicit seconds field rather than restricting it to the
four RF buttons.

Cancel override is a separate official-app command: command 2, preset byte 1,
zone 0, mode byte 3, and timeout 0. It has no equivalent button on the inspected
RF remote. It must be recorded as a BLE-only result and never described as Off.

## Test procedure

1. Record baseline fan level, fan state, system fan speed, RPM, override
   remaining time, faults, availability, transport, and Bluetooth route.
2. Run Low for 1,800 seconds. Confirm fresh fan-level and remaining-time
   telemetry, then use Cancel override and confirm automatic control resumes.
   Record whether diagnostics labels remaining time `device` or `estimated`.
3. Repeat step 2 for Normal, Boost, and Purge.
4. On Normal, apply 3,600, 7,200, and 14,400 seconds in turn. Confirm the fresh
   remaining-time value after each command; Cancel between tests if physical
   validation confirms cancellation works.
5. Start a 1,800-second Boost, interrupt the Bluetooth route, and attempt one
   further speed command **before** the next coordinator poll marks the entity
   unavailable. Confirm the in-flight action fails, the previous confirmed
   values are retained, and entities become unavailable rather than accepting
   an optimistic state. Once an entity is already unavailable, Home Assistant
   Core filters entity-service targets before calling the integration; a later
   action can therefore be skipped without displaying an action error.
6. Restore the adapter/proxy. Confirm automatic reconnection, fresh telemetry,
   and speed-control recovery without reloading the integration.
7. Confirm there are no new faults, restore the original speed/automatic state,
   and download final diagnostics and the relevant redacted debug-log interval.

Use Home Assistant's ordinary actions for the matrix:

| Purpose | Action/entity |
|---|---|
| Timed speed | `ventaxia_multihome.set_timed_override` with `preset` and `duration` |
| Default timed speed | `fan.set_preset_mode` |
| Cancel | Press the integration's Cancel override button entity |

## Results record

Copy this table into issue #12 and replace every Pending entry with the observed
result. Attach redacted diagnostics/logs; never post the stored application code
or full household Bluetooth inventory.

| Field | Result |
|---|---|
| Date/time | 2026-08-27 |
| Tester | Hardware result reported through issue #12 |
| Integration commit/version | v0.2.0-rc.2 |
| Home Assistant version | Pending |
| MEV model/firmware | Not reported |
| Selected transport | Fragmented |
| Adapter/proxy and ESPHome version | Active ESPHome Bluetooth proxy; version not reported |
| Low + Cancel | Passed on v0.2.0-rc.1; level 1 and `user_override` observed; Cancel worked |
| Normal + Cancel | Normal speed passed; shared Cancel operation previously passed |
| Boost + Cancel | Boost speed passed; shared Cancel operation previously passed |
| Purge + Cancel | Purge speed passed; shared Cancel operation previously passed |
| 30/60/120/240-minute telemetry | RC2 local estimate passed and a 70-second command expired automatically; device-reported remaining time is unavailable on this firmware |
| Failed-command state retention | Passed: Home Assistant Core skipped the already-unavailable target and the command was not applied after recovery |
| Automatic reconnect | Passed: entities recovered after proxy restoration without reloading the integration |
| Final restored state and faults | Previous confirmed state restored; no new fault was reported |

## Release gate

The v0.2 milestone may close only when:

- every supported speed/timer row has a recorded physical result;
- Cancel is either physically confirmed or remains clearly labelled as an
  official-app-derived operation without an RF equivalent;
- On, Off, and Stop remain unexposed unless later physical capability evidence
  supports a separately reviewed implementation;
- the tested model, firmware, transport, and Bluetooth path appear in the v0.2
  release notes; and
- Tests, HACS validation, and Hassfest all pass on the final release commit.

The tested firmware cannot expose a genuine remaining-time value, so an
end-to-end 240-minute wait would provide no additional telemetry. Physical
expiry was instead confirmed with a 70-second BLE command; the four RF timer
capabilities were physically identified, and exact 1,800/3,600/7,200/14,400
second BLE encodings are covered by automated tests. Together with the complete
speed, Cancel, and reconnect matrix, this satisfies the v0.2 gate for the tested
MEV while retaining the firmware limitation in the documentation.
