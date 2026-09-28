# Global settings safety model

The MEV/Multihome global-settings response is packet type 137. It contains one
36-byte record covering airflow percentages, environmental options, timeouts,
input actions, and CO₂ thresholds. Home Assistant polls and decodes the entire
record; the redacted integration diagnostics retain both the decoded fields and
the original bytes.

## Update protocol

The official app changes one setting at a time with packet type 136 and a
`RawWithId` body:

- the object ID is one of the documented field IDs 0–32;
- most values are one byte;
- CO₂ threshold fields 21 and 22 contain `ppm / 10` as UInt16LE;
- the packet target is zero for validated fields;
- recovered code maps field 7's requested boolean to the packet target, but the
  field is not writable because the installed firmware rejected both targets.

Field IDs and record offsets are not interchangeable after field ID 8. The
integration therefore uses an explicit mapping and preserves all bytes outside
the selected field.

## Confirmation rules

The internal update layer applies these rules before it can be exposed through
Home Assistant:

1. A complete packet-137 record must have been read successfully during the
   current connection.
2. The field ID, type, range, and encoded step must pass validation before the
   packet-136 write.
3. The write is serialized with polling and every other protocol transaction.
4. Packet 137 is read immediately after the write.
5. The new record is accepted only if it equals the old record with exactly the
   requested field bytes changed.

If the send or readback times out, the device disconnects, the response is
malformed, or any byte differs unexpectedly, the integration retains its last
confirmed snapshot and disables further updates until a normal poll reads a
fresh complete record.

## Guarded airflow flow

Home Assistant exposes only the four airflow fields through **Settings → Devices
& services → Vent-Axia Multihome → Configure → Configure airflow levels** on
the exact physically validated device identity with a current writable settings
record.

The values are commissioned motor-speed percentages, not fan RPM. The
[official Multihome manual](https://asset.eezybridge.com/nc/ba7b9546-29dd-40c4-87a7-885627281b87/495584.pdf)
documents these exact ranges:

| Level | Minimum | Maximum | Step |
| --- | ---: | ---: | ---: |
| Low | 1% | 97% | 1% |
| Normal | 2% | 98% | 1% |
| Boost | 3% | 99% | 1% |
| Purge | 4% | 100% | 1% |

The complete profile must satisfy `Low < Normal < Boost < Purge`. If several
values change, the integration finds a write order in which every intermediate
profile also satisfies that rule. It confirms each isolated change before
sending the next one. The review is invalidated if the full 36-byte record
changes before final confirmation.

Measured **Fan RPM** remains telemetry. It can vary for the same configured
percentage because duct resistance and motor load differ between installations.

## Guarded Boost minimum flow

The official-client enum names packet-136 field 4 `BoostMin`, and the validated
unit reports a baseline of 0% alongside its 6/8/37/50% commissioned airflow
profile. Available primary evidence does not yet define the setting's operating
meaning or profile dependency.

The exact validation unit successfully stored and restored 0% → 1% → 0% with
exact packet-137 readback and unchanged neighbouring bytes. On that exact
identity, the prerelease exposes **Configure → Configure Boost minimum** across
the recovered 0–100% wire range. It shows current and proposed values, requires
explicit acknowledgement, rejects a stale full record, sends only field 4, and
accepts success only after exact fresh packet-137 readback.

This proves isolated storage and restoration, not runtime behaviour. The flow
therefore warns that the setting's operating meaning and relationship to the
commissioned airflow profile remain uncharacterised. Fan level, RPM, and state
observations must be recorded separately.

## Model capability matrix

The official app supplies the model names and the four-speed/internal-CO₂
classification. That is static-analysis evidence, not proof that a particular
firmware accepts installer writes.

| Model | Official-app name | Four-speed family | Internal CO₂ | Installer writes |
| ---: | --- | --- | --- | --- |
| 1 | `SmvPlusHx` | Yes | No | None validated |
| 2 | `SmvPlusHxCo2` | Yes | Yes | None validated |
| 3 | `VaZonalDemand` | No | No | None validated |
| 4 | `ComairFlx` | No | No | None validated |
| 5 | `Unknown5` | No | No | None validated |
| 6 | `ComairZonalDemand` | No | No | None validated |
| 7 | `Unknown7` | No | No | None validated |
| 8 | `Unknown8` | No | No | None validated |
| 9 | `SmvHx` | Yes | No | None validated |
| 10 | `SmvHxCo2` | Yes | Yes | See exact identity below |
| 11 | `Va125Ec3Wireless` | No | No | None validated |

The only installer write profile currently enabled is:

| Model | Firmware | Hardware | Writable fields | Evidence |
| ---: | --- | --- | --- | --- |
| 10 | `2.03.08` | `01.00` | IDs 0–6 except blocked ID 7; IDs 8–10, 14–22 | Fragmented packet-136 writes with exact packet-137 readback and restored baselines; field 4 runtime meaning remains uncharacterised |

All three identity values must match. Missing identity data, a newer firmware, or
a different hardware revision exposes no installer controls until separately
validated. Models 1, 2 and 9 retain read-only decoded airflow diagnostics.

## Read-only Home Assistant entities

Every decoded packet-137 installer value is available as an entity and remains
disabled by default. Enable only the diagnostic entities useful for the
installation from the Home Assistant entity registry.

Known percentages, CO₂ thresholds, humidity, timers and temperature thresholds
carry their recovered units. Temperature action codes offered by the verified
MEV app are displayed as `low`, `boost` or `purge`; any other byte is retained
as `unknown_<code>`. Unrecovered LS/digital/analogue action enums, analogue
scaling and `purge_low_mode` remain numeric **code** or **raw** values without a
unit. These entities are read-only and do not widen the packet-136 write
capability.

## Installer-field matrix

“Codec range” is the range the recovered serializer can represent or the
official airflow range. It is **not** permission to write the field. Static-only
fields remain read-only until their units, dependencies and physical behaviour
are proven.

| ID | Decoded field | Record offset | Encoding / unit | Codec range | Dependency or unresolved question | Risk | Evidence / write status |
| ---: | --- | ---: | --- | --- | --- | --- | --- |
| 0 | `speed_low` | 0 | UInt8, % | 1–97, step 1 | `low < normal < boost < purge` | Ventilation | Physical; exact validated identity only |
| 1 | `speed_medium` | 1 | UInt8, % | 2–98, step 1 | `low < normal < boost < purge` | Ventilation | Physical; exact validated identity only |
| 2 | `speed_boost` | 2 | UInt8, % | 3–99, step 1 | `low < normal < boost < purge` | Ventilation | Physical; exact validated identity only |
| 3 | `speed_purge` | 3 | UInt8, % | 4–100, step 1 | `low < normal < boost < purge` | Ventilation | Physical; exact validated identity only |
| 4 | `boost_minimum` | 4 | UInt8, % | 0–100 | Runtime meaning and airflow-profile relationship uncharacterised | Ventilation | Physical storage/readback; exact validated identity only |
| 5 | `humidity_threshold` | 5 | UInt8, %RH | 0–100 | Humidity demand threshold | Sensor control | Physical; exact validated identity only |
| 6 | `comfort_enabled` | 6 | strict UInt8 boolean | 0/1 | Model-specific interaction | Comfort | Physical; exact validated identity only |
| 7 | `delay_enabled` | 7 | strict UInt8 boolean | 0/1 | Paired with ID 10; LS inputs only | Wired input | Physical readback mismatch; blocked/read-only |
| 8 | `overrun_enabled` | 8 | strict UInt8 boolean | 0/1 | Paired with ID 9; LS inputs only | Wired input | Physical; exact validated identity only |
| 9 | `overrun_timeout_minutes` | 17 | UInt8, minutes | 1–60 | Paired with ID 8 | Wired input | Physical; exact validated identity only |
| 10 | `delay_timeout_minutes` | 18 | UInt8, minutes | 1–60 | Paired with blocked ID 7 | Wired input | Physical; exact validated identity only |
| 11–13 | `ls1_action` … `ls3_action` | 19–21 | UInt8 action code | Low=1, Boost=3, Purge=4 | Installed switched-live mapping | Wired input | Physical storage/readback; exact validated identity only |
| 14 | `rapid_response_enabled` | 9 | strict UInt8 boolean | 0/1 | Humidity-response semantics | Sensor control | Physical; exact validated identity only |
| 15 | `ambient_response_enabled` | 10 | strict UInt8 boolean | 0/1 | Humidity-response semantics | Sensor control | Physical; exact validated identity only |
| 16 | `low_temperature_enabled` | 11 | strict UInt8 boolean | 0/1 | Paired thresholds and actions; changing it may activate the stored profile | Sensor control | Physical Disabled → Enabled → Disabled with exact full-record readback; exact validated identity only |
| 17–18 | `low_threshold_action`, `high_threshold_action` | 12–13 | UInt8 action code | App choices: 1 Low, 3 Boost, 4 Purge | Paired with the corresponding threshold; other codes preserved as unknown | Sensor control | Physical storage/readback; exact validated identity only while ID 16 is off |
| 19 | `low_temperature_threshold` | 14 | UInt8, °C | 0–30, step 1 | Integration conservatively requires `low < high` | Sensor control | Physical storage/readback; exact validated identity only while ID 16 is off |
| 20 | `high_temperature_threshold` | 15 | UInt8, °C | 15–40, step 1 | Integration conservatively requires `low < high` | Sensor control | Physical storage/readback; exact validated identity only while ID 16 is off |
| 21–22 | `co2_boost_threshold`, `co2_purge_threshold` | 22, 24 | UInt16LE value ÷ 10, ppm | 0–2000, step 10 | CO₂ model; `boost < purge` | Sensor control | Physical; exact validated identity only |
| 23–24 | analogue input 1 low/high actions | 28–29 | UInt8 action code | Low=1, Normal=2, Boost=3, Purge=4 | 0–10 V input; paired thresholds | Wired input | Physical storage/readback; exact validated identity only |
| 25–26 | analogue input 1 low/high values | 26–27 | UInt8 tenths of a volt | raw 1–99 = 0.1–9.9 V, step 0.1 V | Low < High; paired actions | Wired input | Physical storage/readback; exact validated identity only |
| 27–28 | analogue input 2 low/high actions | 32–33 | UInt8 action code | Low=1, Normal=2, Boost=3, Purge=4 | 0–10 V input; paired thresholds | Wired input | Physical storage/readback; exact validated identity only |
| 29–30 | analogue input 2 low/high values | 30–31 | UInt8 tenths of a volt | raw 1–99 = 0.1–9.9 V, step 0.1 V | Low < High; paired actions | Wired input | Physical storage/readback; exact validated identity only |
| 31–32 | digital input 1/2 actions | 34–35 | UInt8 action code | Low=1, Boost=3, Purge=4 | Low-voltage inputs; Normal excluded | Wired input | RC13 exact-identity validation candidate |

Packet-137 byte 16 decodes as `purge_low_mode`, but no corresponding packet-136
field ID was recovered. It is retained losslessly and is never written. Restore
defaults is also not exposed because its payload and scope are unknown.

### Temperature evidence and limitations

The temperature mapping comes from the SHA-256-verified Vent-Axia Connect
6.0.28 APK. Its MEV temperature screen sets the low slider to 0–30, stores the
high slider as 15–40, labels the screen as temperature, and offers only Low,
Boost and Purge action choices mapped to codes 1, 3 and 4. Its presenter writes
fields 17, 18, 19 and 20 in that order.

The same screen does not write field 16, contains visible initialization/display
defects, and performs no recovered comparison between the low and high values.
The newer 7.2.2 app still confirms the packet-136 field IDs and record layout,
but its visible 0–40 temperature slider is for a different IAQ-manager device
family and is not used as Multihome evidence.

The guarded v0.6.3 flow subsequently changed field 16 from Disabled to Enabled
and back to Disabled on the installed model 10 / firmware 2.03.08 / hardware
01.00 unit. Exact packet-137 readback confirmed both changes and restoration
with fields 17–20 unchanged. No runtime fan-speed response is inferred from
that storage validation.

Version 0.6.2 keeps field 16 read-only and enables guarded fields 17–20 only on
model 10 / firmware 2.03.08 / hardware 01.00. The device must already report
field 16 off, both action codes must be recovered app choices, the integration
requires `low < high`, and exactly one field may change per submission. A fresh
full-record concurrency check happens before the write and the changed field
must be returned exactly in a fresh packet-137 record afterward.

Version 0.6.3 RC1 adds a separate guarded validation route for field 16 on that
same exact identity. The official `GlobalDataField` enum identifies
`LowTemperatureEnabled` as field 16 and packet-137 byte 11 is a strict boolean.
Because the recovered Multihome temperature screen does not write this flag,
it remains a prerelease candidate until the installed unit has completed an
enable/readback/disable/restore cycle. The flow validates the complete stored
temperature profile, changes only field 16, rereads the complete record before
the write, requires exact readback afterward, and warns that enabling may cause
an immediate response to the stored thresholds and actions.

The physically observed starting profile for this identity is field 16 off,
low action 1 (`low`), high action 4 (`purge`), low threshold 15 °C and high
threshold 25 °C.

Physical testing changed and restored every field independently: low threshold
15 → 14 → 15 °C, high threshold 25 → 24 → 25 °C, low action Low → Boost → Low,
and high action Purge → Boost → Purge. Every change and restoration was returned
exactly by packet 137. During the high-action test the threshold was staged at
35 °C while the measured temperature was about 27 °C. Lowering and restoring
the threshold/action did not change fan level 2, roughly 625 RPM, or the
reported default state. The evidence therefore validates storage and
restoration only; runtime temperature-trigger behaviour remains unproven.

## Physical validation evidence

The stable flow was tested on model 10, firmware 2.03.08, hardware 01.00, using
the fragmented transport through an ESPHome Bluetooth proxy:

| Capture | Profile | Raw-record prefix | Remaining 32 bytes |
| --- | --- | --- | --- |
| Before | 6/8/37/50% | `06082532` | Baseline |
| Changed | 7/9/38/51% | `07092633` | Identical to baseline |
| Restored | 6/8/37/50% | `06082532` | Identical to baseline |

The four-field operation returned exact readback with write readiness retained.
Confirmation while the settings snapshot was unavailable was rejected before a
write with “No setting was changed.” Restoring the proxy recovered polling and
the configuration flow automatically, and a later update worked normally.

The threshold flow was then physically validated on the same unit. It changed
humidity/CO₂ boost/CO₂ purge from `81/1500/1750` to `82/1550/1800`, received
exact fresh readback for all three fields, and restored `81/1500/1750` with
another exact readback. The restored values match the original packet-137
snapshot.

The humidity-response flow was then physically validated on the same unit.
Ambient and Rapid were each changed independently, returned exactly by a fresh
packet-137 read, and restored to their original values through the guarded flow.

Comfort mode was then disabled on the same unit, returned exactly by a fresh
packet-137 read, and restored enabled through the guarded flow.

The official Multihome manual documents Delay On and Overrun as LS-input-only
features with 1–60 minute ranges. On the exact validation unit, fields 8–10
changed with exact readback, while field 7 (Delay On enabled) did not. Earlier
writes with packet destination 0 were rejected. The recovered official-client
`setSystemStatusField(field, value)` path also copies the requested value into
the packet destination, so RC8 tested field 7 value 1 with destination 1.

The RC8 diagnostic evidence is an exact mismatch, not a timeout: payload
`ba0a07050700000001` was sent, the expected record contained byte 7 `01`, and
the received complete record was the original
`06082532005101000100000001011a1b000a0a0103049600af000f4b01030f4b01030103`.
The sole expected/received difference was `7:01->00`; no neighbour changed.
RC9 therefore removes field 7 from the validation candidates and blocks it
before packet-136 I/O while retaining its readback and recovered protocol
mapping. Fields 8–10 remain physically validated. Field 7 can be reconsidered
if the official app changes it on the same unit or a BLE capture identifies an
additional prerequisite. Runtime electrical timing remains unverified because
no switched-live input was connected during testing.

Fields 11–13 map LS1, LS2 and combined LS1+LS2 to packet-137 offsets 19–21.
Recovered app encoding and the installer manual agree on the available actions:
Low=`1`, Boost=`3`, and Purge=`4`; Normal is deliberately excluded. RC10 exposes
these three fields only on the exact tested identity. Independent reversible
storage testing has since proven all three fields, so they are now normal
guarded installer controls. Home Assistant serializes changed fields as
LS1 → LS2 → LS3 with exact complete readback after each write. Unknown stored
action codes remain visible in diagnostics and block the flow rather than being
rewritten. Live switched-input behavior is a separate test.


### Analogue input 1 evidence and validation

Fields 23–26 map analogue input 1 to packet-137 offsets 28, 29, 26 and 27.
The recovered app and installer manual identify Low, Normal, Boost and Purge as
action codes 1–4 and use integer tenths of a volt for the two thresholds; for
example raw 52 is displayed as 5.2 V. The documented usable range is raw 1–99
(0.1–9.9 V), and the integration requires Low < High.

RC11 proved all four fields independently on model 10 / firmware 2.03.08 /
hardware 01.00 through reversible change/readback/restore tests. They are now
normal exact-identity guarded installer controls. Home Assistant shows actions
as dropdowns and thresholds as 0.1 V sliders while retaining integer packet-136
writes and exact complete packet-137 readback. Unknown stored action codes remain
visible in diagnostics and block the write flow rather than being coerced to a
named choice. Live 0–10 V electrical response remains a separate test.

### Analogue input 2 evidence and validation

Fields 27–30 map analogue input 2 to packet-137 offsets 32, 33, 30 and 31. The
recovered app and installer manual use the same Low, Normal, Boost and Purge
action codes 1–4 and raw 1–99 tenths-of-a-volt thresholds as analogue input 1.
RC12 installed-unit testing completed successfully on model 10 / firmware
2.03.08 / hardware 01.00, so these fields are now normal exact-identity guarded
installer controls. Each Home Assistant submission still changes only one field,
requires a fresh unchanged 36-byte baseline, and accepts success only after exact
complete packet-137 readback. Unknown action codes remain read-only. Live
0–10 V electrical/runtime response remains separate from the proven stored
read/write behaviour.

### Digital input evidence and validation

Fields 31–32 map digital inputs 1 and 2 to packet-137 offsets 34 and 35.
The recovered field IDs and installer manual agree that each stored action is
selectable from Low=`1`, Boost=`3`, and Purge=`4`; Normal is deliberately
excluded. RC13 exposes these fields only on model 10 / firmware 2.03.08 /
hardware 01.00 as guarded validation candidates.

Each review may change exactly one digital input action. Home Assistant rejects
unknown action codes and a stale 36-byte settings baseline before packet-136
I/O, then requires exact complete packet-137 readback after the write. Unknown
stored codes remain visible in diagnostics and block the write flow rather than
being coerced. Promotion requires independent change/readback/restore evidence
for both fields. Live low-voltage electrical input behaviour remains separate
from stored configuration validation.


### v0.6.3 release-gate audit

The packet-136 enum has 33 authoritative field IDs (0–32). For the exact model
10 / firmware 2.03.08 / hardware 01.00 identity, the release gate now classifies
every one of them explicitly: 30 fields are physically validated normal writes,
fields 31–32 are the RC13 guarded digital-input validation candidates, and field
7 (`Delay On enabled`) is explicitly deferred after the RC8 installed-unit test
ignored both reversible write attempts. Field 7 remains a documented protocol
mapping rather than being labelled permanently read-only; a future official-app
capture or newly identified prerequisite can reopen validation.

Packet-137 byte 16 (`purge_low_mode`) is a separate evidence-recovery item. It
has no authoritative packet-136 field ID in the recovered enum, so the
integration does not infer a write ID from its record offset. Issue #56 remains
open until an official write path, computed-source setting, or reserved/model-
specific meaning is demonstrated.

The stable v0.6.3 gate therefore still waits for independent reversible storage
proof for digital input 1 and digital input 2 from RC13. Live electrical/runtime
tests remain separate from stored R/W proof.
