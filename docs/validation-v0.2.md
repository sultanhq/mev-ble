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
| Connection recovery | Availability remained stable after the serialized-operation and reconnect fixes | User-reported smoke test |
| Low, Normal, Boost, Purge | Exact BLE packets and Home Assistant paths pass automated tests | Pending complete physical matrix |
| Timer values | BLE accepts seconds; RF-matched 30/60/120/240-minute values are documented | Pending complete physical matrix |
| Cancel override | Recovered from the official BLE app; no RF equivalent | Pending physical BLE result |
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
| Low | 1 | 1 | 1,800 seconds | Fan level 1; user override; remaining time near 1,800 |
| Normal | 2 | 2 | 1,800 seconds | Fan level 2; user override; remaining time near 1,800 |
| Boost | 3 | 3 | 1,800 seconds | Fan level 3; user override; remaining time near 1,800 |
| Purge | 4 | 4 | 1,800 seconds | Fan level 4; user override; remaining time near 1,800 |

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
3. Repeat step 2 for Normal, Boost, and Purge.
4. On Normal, apply 3,600, 7,200, and 14,400 seconds in turn. Confirm the fresh
   remaining-time value after each command; Cancel between tests if physical
   validation confirms cancellation works.
5. Start a 1,800-second Boost, interrupt the Bluetooth route, and attempt one
   further speed command. Confirm the action fails, the previous confirmed
   values are retained, and entities become unavailable rather than accepting
   an optimistic state.
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
| Date/time | Pending |
| Tester | Pending |
| Integration commit/version | Pending |
| Home Assistant version | Pending |
| MEV model/firmware | Pending |
| Selected transport | Pending |
| Adapter/proxy and ESPHome version | Pending |
| Low + Cancel | Pending |
| Normal + Cancel | Pending |
| Boost + Cancel | Pending |
| Purge + Cancel | Pending |
| 30/60/120/240-minute telemetry | Pending |
| Failed-command state retention | Pending |
| Automatic reconnect | Pending |
| Final restored state and faults | Pending |

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
