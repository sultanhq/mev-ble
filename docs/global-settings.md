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

`v0.4.0` exposes only the four airflow fields through **Settings → Devices
& services → Vent-Axia Multihome → Configure → Configure airflow levels** on
physically validated model 10 units with a current writable settings record.

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

## Supported-field matrix

| Field group | Stable UI | Confidence | Physical status |
| --- | --- | --- | --- |
| Low/Normal/Boost/Purge percentages on model 10 | Guarded four-field flow | High | Validated on firmware 2.03.08 / hardware 01.00 |
| Airflow percentages on models 1, 2 and 9 | Read-only diagnostics | Official four-speed model mapping, but no device write evidence | Not tested |
| Humidity, temperature and comfort options | Read-only diagnostics | Decoded; write semantics not fully validated | Not tested |
| Delay, overrun and input actions | Read-only diagnostics | Several numeric action meanings remain model-specific | Not tested |
| CO₂ boost/purge thresholds | Read-only diagnostics | Encoding recovered; upper-range behaviour needs proof | Not tested |
| Restore defaults | Not exposed | Payload and scope unknown | Unsafe to test |

No other global field is user-writable in v0.4.0.

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
