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

## Current release status

`v0.4.0-rc.2` contains this codec and transaction layer for automated testing,
but deliberately exposes no user-facing global-settings control. The guarded
Home Assistant controls and their first physical packet-136 validation belong
to the next v0.4 implementation stage.
