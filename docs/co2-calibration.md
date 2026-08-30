# Internal CO₂ calibration

> [!CAUTION]
> Calibration changes every future reading from the MEV's internal CO₂ sensor.
> Use it only when the current readings are consistently wrong. An unsuitable
> reference can make the sensor less accurate.

Calibration is available from **Settings → Devices & services → Vent-Axia
Multihome → Configure → Calibrate internal CO₂ sensor** only when the connected
model number identifies a documented internal-CO₂ model. It is deliberately not
an entity, action, service, or automation trigger.

The integration requires a separate final confirmation immediately before it
sends the command. It permits one successful or potentially delivered command
every five minutes and does not claim that calibration succeeded merely because
the BLE packet was accepted. A failure proven to have happened before the
calibration write can be retried immediately. The firmware does not expose
calibration readback. The delivery-attempt time is stored in the Home Assistant
config entry so reloading the integration or restarting Home Assistant cannot
bypass the cooldown.

## Vent-Axia value: 450 ppm default or manual input

This method mirrors the current official Vent-Axia app. Its numeric field
defaults to **450 ppm** and accepts **400–2,000 ppm**. The default is a
manufacturer-chosen fresh-air calibration reference; it is not a live
measurement of the world's atmosphere or the air at the installation.

Leave the field at 450 ppm when following the official fresh-air preparation.
If a genuinely trusted, calibrated true-CO₂ instrument is available, enter its
current reading instead. Home Assistant also retains the advanced entity-based
method for averaging several trusted reference sensors.

1. Open external windows or doors in every room from which the MEV extracts
   air, such as bathrooms, utility rooms, and kitchens.
2. Ventilate those rooms for **at least 15 minutes**.
3. Keep the rooms unoccupied and leave the openings open.
4. In the integration's Configure flow, choose **Vent-Axia value**. Leave the
   field at **450 ppm**, or replace it with a calibrated instrument reading.
5. Review the final confirmation.
6. Confirm once. Home Assistant displays progress for the MEV manual's specified
   three-minute internal-sensor sampling period.
7. Keep the rooms open, unoccupied, and unchanged until the progress finishes.

The physical MEV status LED should flash magenta during sampling, but users do
not need to see or reach the installed MEV/Multihome unit to time the process.
Closing Home Assistant's progress screen does not cancel a calibration command
already sent.

Opening only one window beside the controller is not the intended preparation.
The unit samples air arriving through all of its extract paths, so every
extracted room should receive outdoor air.

## Advanced: Home Assistant reference sensors

This option averages the current readings from one or more selected Home
Assistant CO₂ entities. Prefer one trustworthy reference in each extracted
room. The integration re-reads every selected entity immediately after the
final confirmation and rounds their arithmetic mean to the nearest whole ppm.

The safety checks are intentionally strict:

- every entity must have the CO₂ device class and report `ppm`;
- every reading must be available, numeric, finite, and between 400 and
  2,000 ppm;
- the MEV integration's own CO₂ entity cannot be selected;
- a failed check prevents the Bluetooth command from being sent.

Home Assistant metadata cannot prove that a sensor is calibrated, accurate, or
measures true CO₂. Some inexpensive air-quality sensors estimate **eCO₂** from
other gases; do not use those as calibration references. Use this method only
when you independently trust the selected instruments and their placement.

## Progress and completion

After transport delivery, Home Assistant advances a progress bar for exactly
three minutes. This duration comes from the Vent-Axia internal-sensor procedure:
the MEV repeatedly reads the CO₂ sensor for three minutes to obtain a stable
reading, then sets the calibration value.

Keep the rooms and reference conditions stable while the progress bar is
running. When it finishes, the screen says **Sampling period elapsed** and the
rooms can be used normally.

Some Home Assistant clients may leave the progress page visible at 100% instead
of automatically advancing to the result page. Reaching 100% still means the
documented three-minute sampling period has elapsed. The progress page states
that it can be closed with **X** at that point; closing it does not repeat or
undo the calibration command.

The bar tracks elapsed time; it is not live firmware status. The recovered BLE
protocol exposes no calibration completion readback, so the integration does
not claim verified success. If accessible, a magenta LED that stops flashing is
useful optional evidence. Later CO₂ readings should also settle plausibly. Do
not repeat commands rapidly. A successful send or a failure during the final
write starts the five-minute cooldown because the device may have received the
command before the connection failed. Failures during connection or internal
sensor target discovery are identified as **not sent** and do not start a new
cooldown.

Integration diagnostics include the non-sensitive device-table routes and the
last calibration delivery outcome. Download diagnostics after a failure to
distinguish a missing internal-sensor route from an uncertain Bluetooth write.
The official app has two internal-calibration routes: a standalone internal
CO₂ sensor row when present, or the address-zero MEV control-unit row for a
validated model with a built-in sensor. The integration resolves those explicit
routes and never falls back to an arbitrary device-table row.

The manual's separate five-minute room-condition instruction applies to paired
room sensors. This integration calibrates only the MEV's internal sensor, whose
documented sampling duration is three minutes.

## Rerunning or correcting calibration

Calibration can be run again. Wait at least five minutes after the previous
attempt, prepare the rooms and reference correctly, then reopen **Configure →
Calibrate internal CO₂ sensor**. If the firmware completes, the new calibration
supersedes the earlier baseline.

Home Assistant does not expose a calibration Reset button. Static analysis does
contain a general packet enum named `RestoreDefaults` (`62`), but no normal MEV
call path, payload, scope, or evidence that it resets only CO₂ calibration was
recovered. It could affect wider installer/unit configuration and is therefore
unsafe to infer or expose.

To correct a mistaken calibration, repeat either method with a properly
prepared reference. The Vent-Axia path defaults to 450 ppm or accepts a trusted
manual measurement; it does not prove that unknown factory offsets have been
restored.
The five-minute interval is a Home Assistant safety cooldown between commands,
not an automatic reset. Restarting or reloading the integration does not repeat
calibration.

## v0.3 physical validation record

Physical validation completed on 30 August 2026 using a Multihome unit with
firmware **2.03.08** and hardware **01.00**. Its V6 device table exposed the
built-in sensor through address 0, device type 10 (`MEVControlUnit`), hardware
type 4. No separate type-6 sensor row was present.

| Check | Result |
| --- | --- |
| Deliberately distinct manual value | 800 ppm calibration produced a reported 799 ppm |
| Repeat/correction path | A subsequent 450 ppm calibration produced 452 ppm |
| Calibration routing | Address-zero type-10 MEV control-unit route accepted and applied |
| Payload serialization | Raw DataObjectArray wrapper accepted and applied |
| Progress | HA tracked the documented three-minute sampling period for each command |
| Safety cooldown | The guarded rerun completed without sending overlapping commands |

The large reversible 800-to-450 change proves that the firmware applied both
manual reference values; it is stronger evidence than transport acknowledgement
or the time-based progress display alone. The installed unit did not need to be
seen or reached, so LED behaviour was not required as release evidence.

Automated coverage additionally verifies that no packet is sent before final
confirmation, unsafe or unavailable reference sensors are rejected, the MEV's
own CO₂ entity cannot be selected, pre-write failures do not create a false
cooldown, uncertain final writes do create one, and reload/restart cannot repeat
or bypass a recorded calibration attempt.
