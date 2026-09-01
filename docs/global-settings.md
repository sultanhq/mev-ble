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
- the packet target is zero.

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
| 10 | `2.03.08` | `01.00` | IDs 0–3 airflow; ID 5 humidity; IDs 21–22 CO₂ thresholds | Fragmented packet-136 writes with exact packet-137 readback and restored baselines |

All three identity values must match. Missing identity data, a newer firmware, or
a different hardware revision exposes no installer controls until separately
validated. Models 1, 2 and 9 retain read-only decoded airflow diagnostics.

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
| 4 | `boost_minimum` | 4 | UInt8, % | 0–100 | Model-specific interaction | Ventilation | Static; read-only |
| 5 | `humidity_threshold` | 5 | UInt8, %RH | 0–100 | Humidity demand threshold | Sensor control | Physical; exact validated identity only |
| 6 | `comfort_enabled` | 6 | strict UInt8 boolean | 0/1 | Model-specific interaction | Comfort | Static; read-only |
| 7 | `delay_enabled` | 7 | strict UInt8 boolean | 0/1 | Paired with ID 10 | Wired input | Static; read-only |
| 8 | `overrun_enabled` | 8 | strict UInt8 boolean | 0/1 | Paired with ID 9 | Wired input | Static; read-only |
| 9 | `overrun_timeout_minutes` | 17 | UInt8, minutes | 0–255 | Requires ID 8; safe UI range unknown | Wired input | Static; read-only |
| 10 | `delay_timeout_minutes` | 18 | UInt8, minutes | 0–255 | Requires ID 7; safe UI range unknown | Wired input | Static; read-only |
| 11–13 | `ls1_action` … `ls3_action` | 19–21 | UInt8 action code | 0–255 | Installed wiring and action enum | Wired input | Static; read-only |
| 14 | `rapid_response_enabled` | 9 | strict UInt8 boolean | 0/1 | Humidity-response semantics | Sensor control | Static; read-only |
| 15 | `ambient_response_enabled` | 10 | strict UInt8 boolean | 0/1 | Humidity-response semantics | Sensor control | Static; read-only |
| 16 | `low_temperature_enabled` | 11 | strict UInt8 boolean | 0/1 | Paired thresholds and actions | Sensor control | Static; read-only |
| 17–18 | `low_threshold_action`, `high_threshold_action` | 12–13 | UInt8 action code | 0–255 | Temperature action enum | Sensor control | Static; read-only |
| 19–20 | `low_temperature_threshold`, `high_temperature_threshold` | 14–15 | UInt8, unit unknown | 0–255 | Unit, ordering and safe range | Sensor control | Static; read-only |
| 21–22 | `co2_boost_threshold`, `co2_purge_threshold` | 22, 24 | UInt16LE value ÷ 10, ppm | 0–2000, step 10 | CO₂ model; `boost < purge` | Sensor control | Physical; exact validated identity only |
| 23–24 | analogue input 1 low/high actions | 28–29 | UInt8 action code | 0–255 | Installed wiring and action enum | Wired input | Static; read-only |
| 25–26 | analogue input 1 low/high values | 26–27 | UInt8, scaling unknown | 0–100 | Scaling and paired actions | Wired input | Static; read-only |
| 27–28 | analogue input 2 low/high actions | 32–33 | UInt8 action code | 0–255 | Installed wiring and action enum | Wired input | Static; read-only |
| 29–30 | analogue input 2 low/high values | 30–31 | UInt8, scaling unknown | 0–100 | Scaling and paired actions | Wired input | Static; read-only |
| 31–32 | digital input 1/2 actions | 34–35 | UInt8 action code | 0–255 | Installed wiring and action enum | Wired input | Static; read-only |

Packet-137 byte 16 decodes as `purge_low_mode`, but no corresponding packet-136
field ID was recovered. It is retained losslessly and is never written. Restore
defaults is also not exposed because its payload and scope are unknown.

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
