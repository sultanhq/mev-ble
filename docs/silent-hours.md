# Silent-hours schedules

Version 0.5 adds guarded management of the six silent-hours slots recovered from
the official Vent-Axia applications. This feature is initially enabled only for
physically tested model 10 Multihome units.

Open **Settings → Devices & services → Vent-Axia Multihome → Configure → Manage
silent hours**. Home Assistant displays the complete table returned by the unit
before offering any write.

Some model-10 firmware appends a Zirconia CRC byte to each indexed table item.
The integration retains that byte in diagnostics and accepts the slot only when
the checksum, requested index and record structure all validate.

## Schedule meaning

- A slot contains a start time, end time and one or more weekdays.
- The selected weekday is the day on which the schedule **starts**.
- When the end time is equal to or earlier than the start time, the schedule is
  overnight and ends on the following day.
- Model 10 firmware 2.03.08 was physically observed to execute the stored clock
  values as UTC. Home Assistant therefore displays and accepts schedule times in
  its configured local time zone, then converts the record to or from the MEV's
  UTC clock. Weekday masks are rotated when that conversion crosses midnight.
- The packet-49 record contains no time-zone or daylight-saving identifier. The
  conversion therefore uses Home Assistant's **current** UTC offset. When the
  local UTC offset changes for daylight saving, an existing recurring schedule
  will execute at the corresponding shifted local time until it is reviewed and
  saved again. Reopening the flow shows the schedule's current local equivalent.
- Empty slots are shown explicitly. Unknown firmware records are retained in
  diagnostics and kept read-only rather than being overwritten.

## Physically observed active state

On model 10, firmware 2.03.08, hardware 01.00, a stored 15:50–15:55 UTC schedule
was observed at 15:50:42 UTC / 16:50:42 Europe/London during BST. The reported
fan state changed from `default` to `silent_hour`, while fan level remained 2
and measured fan RPM remained 625. Silent hours should therefore be interpreted
as a firmware operating state, not as a guarantee that RPM will visibly fall on
every commissioned installation.

## Write and recovery guarantees

Every create, edit or delete operation is serialized with telemetry and other
controls. After the mutation, Home Assistant rereads all six slots. It reports
success only when the selected slot exactly matches the requested record or,
for deletion, is returned empty.

If a write, timeout, disconnect or readback fails:

1. Home Assistant retains the previous confirmed table.
2. Further schedule writes are disabled until a complete successful poll has
   reread all six slots.
3. Reopen **Manage silent hours** after the device recovers and review the table
   before retrying.

Deletion of a populated slot requires a separate explicit confirmation. The
integration never sends the deletion marker for an already empty slot.

## Validated hardware scope

Stable v0.5 schedule writes are enabled only for model 10. Physical testing on
firmware 2.03.08 and hardware 01.00 confirmed:

- deterministic reads of all six indexed slots with the firmware's appended
  Zirconia CRC;
- creation and exact complete-table readback of populated schedule records;
- local-time display and entry with conversion to the firmware's UTC clock;
- activation at the expected converted boundary, reported as `silent_hour`;
- deletion of a populated slot with the recovered `0xffff` marker, followed by
  an empty selected slot in a fresh complete-table readback; and
- recovery of write readiness after successful communication resumes.

Automated tests additionally cover daytime and overnight records, weekday-mask
and midnight rotation, every slot index, unsupported or corrupt responses,
unavailable writes, stale review state and mismatched readback. Other models
remain read-only until their packet-49 behaviour is physically validated.

Do not use schedules that would leave necessary household ventilation reduced
for an unsafe period. Keep the normal RF/app control available during testing.
