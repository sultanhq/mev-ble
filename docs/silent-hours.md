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

## Release-candidate hardware validation

Before v0.5 can become stable, a model 10 unit must confirm:

1. Create a short daytime schedule in slot 1 and confirm its start/end state.
2. Create an overnight schedule in slot 2 and confirm it crosses midnight as
   described.
3. Verify the Monday-through-Sunday mapping with observable boundary tests.
4. Create and read back a record in every slot, 1 through 6.
5. Delete each test record and confirm every slot returns empty.
6. Attempt an edit while the Bluetooth proxy is unavailable, confirm that no
   success is reported, restore the proxy, and verify the previous table returns.

Do not use schedules that would leave necessary household ventilation reduced
for an unsafe period. Keep the normal RF/app control available during testing.
