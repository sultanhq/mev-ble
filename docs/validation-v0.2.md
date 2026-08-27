# v0.2 physical validation

This is the release-gate procedure and evidence record for the v0.2 fan-control
milestone. Automated tests prove packet encoding, transport behaviour, locking,
and Home Assistant state reconciliation. They cannot prove how every physical
MEV firmware reacts to Off or Stop.

## Current evidence status

| Item | Recorded evidence | Status |
|---|---|---|
| Unit | Physical Vent-Axia unit advertising as `MEV`; model and firmware were not recorded | Observed |
| Bluetooth path | Active ESPHome Bluetooth proxy placed near the controller | Observed |
| Transport | Fragmented transport, identified by Home Assistant's earlier `fragmented transaction timed out` action error | Observed |
| Discovery and pairing | Discovery, automatic application-code pairing, and entity creation | Observed |
| Telemetry | Fan entity and telemetry were available in Home Assistant | Observed |
| Connection recovery | Availability remained stable after the serialized-operation and reconnect fixes | User-reported smoke test |
| Low, Normal, Boost, Purge, Cancel | No complete recorded matrix on one build | Pending |
| Off, normal-mode restore, Stop | Packet shapes are recovered and automated tests pass; no physical result is recorded | **Pending — do not claim as validated** |
| Whole-packet transport | Automated parity tests pass; no physical unit using this transport is recorded | Pending hardware report |
| Local HA Bluetooth adapter | Supported by Home Assistant's Bluetooth manager but not used in the recorded MEV test | Not tested |

The existing observations are useful compatibility evidence, but they do not
satisfy the v0.2 physical control gate. Issue #12 and the v0.2 milestone must
remain open until the matrix below has actual results.

## Safety and prerequisites

- Test only when briefly reducing or stopping ventilation is safe for the
  building and its occupants. Do not use this procedure during an alarm or when
  ventilation is required for combustion, fumes, excessive humidity, or another
  safety function.
- Keep the normal Home Assistant On action ready so the model's documented
  Ventilation or Heat recovery mode can be restored immediately.
- Close the official Vent-Axia app so it cannot compete for the BLE connection.
- Use one v0.2 release-candidate commit for the whole run and record its SHA.
- Download integration diagnostics before and after the run. Record the reported
  model, firmware, selected transport, Home Assistant version, ESPHome version,
  and local-adapter or proxy hardware.
- Use a 60-second duration for preset tests so the remaining-time readback is
  easy to inspect without leaving a long override active.

## Recovered control matrix

All rows use protocol packet type 56, operation `DataRequest` (2), target 0,
and zone 0.

| Control | Command | Preset byte | Mode byte | Timeout | Expected fresh telemetry |
|---|---:|---:|---:|---:|---|
| Low | Set speed (1) | 1 | Off/default (3) | 60 | Fan level 1; user override; remaining time 1–60 |
| Normal | Set speed (1) | 2 | Off/default (3) | 60 | Fan level 2; user override; remaining time 1–60 |
| Boost | Set speed (1) | 3 | Off/default (3) | 60 | Fan level 3; user override; remaining time 1–60 |
| Purge | Set speed (1) | 4 | Off/default (3) | 60 | Fan level 4; user override; remaining time 1–60 |
| Cancel override | Cancel (2) | 1 | Off/default (3) | 0 | Remaining time returns to 0 and automatic control resumes |
| Off | No type (0) | 1 | Off (3) | 0 | Record actual speed/RPM transition; both should reach zero before HA reports Off |
| Turn on | No type (0) | 1 | Heat recovery (1) or Ventilation (2) | 0 | Model's normal mode resumes and speed/RPM become nonzero |
| Stop | No type (0) | 1 | Stop (4) | 0 | Record actual behaviour separately from Off; both speed and RPM must be observed |

Turn on uses Heat recovery for reported models 1, 2, 9, and 10. It uses
Ventilation for models 3, 4, 6, and 11. Power and Stop controls are hidden for
unknown models 5, 7, 8, an absent model number, or any unrecognised value.

## Test procedure

1. Record baseline fan level, fan state, system fan speed, RPM, override
   remaining time, faults, availability, transport, and Bluetooth route.
2. Run Low for 60 seconds. Confirm fresh telemetry and then press Cancel
   override. Confirm remaining time returns to zero.
3. Repeat step 2 for Normal, Boost, and Purge.
4. Run `fan.turn_off`. Observe speed and RPM until they settle, but for no longer
   than the agreed safe test interval. Record whether Home Assistant reports Off.
5. Run `fan.turn_on` and confirm the model's normal mode and nonzero telemetry
   return.
6. Run `ventaxia_multihome.stop_ventilation`. Record its behaviour independently
   of Off, then immediately run `fan.turn_on` and confirm recovery.
7. Start a 60-second Boost, interrupt the Bluetooth route, and attempt one
   further control. Confirm the action fails, the previous confirmed values are
   retained, and entities become unavailable rather than accepting an optimistic
   state.
8. Restore the adapter/proxy. Confirm automatic reconnection, fresh telemetry,
   and control recovery without reloading the integration.
9. Confirm there are no new faults, restore the original operating state, and
   download final diagnostics and the relevant redacted debug-log interval.

Use Home Assistant's ordinary actions for the matrix:

| Purpose | Action/entity |
|---|---|
| Timed preset | `ventaxia_multihome.set_timed_override` with `preset` and `duration: 60` |
| Cancel | Press the integration's Cancel override button entity |
| Off | `fan.turn_off` |
| Restore | `fan.turn_on` |
| Stop | `ventaxia_multihome.stop_ventilation` |

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
| Off + Turn on | Pending |
| Stop + Turn on | Pending |
| Failed-command state retention | Pending |
| Automatic reconnect | Pending |
| Final restored state and faults | Pending |

## Release gate

The v0.2 milestone may close only when:

- every matrix row has a recorded result from a physical MEV;
- any failed or ambiguous behaviour is disabled or clearly excluded from the
  release;
- the tested model, firmware, transport, and Bluetooth path appear in the v0.2
  release notes; and
- Tests, HACS validation, and Hassfest all pass on the final release commit.
