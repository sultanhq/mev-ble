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
sends the command. It permits one attempt every five minutes and does not claim
that calibration succeeded merely because the BLE packet was accepted. The
firmware does not expose calibration readback.

## Recommended: fresh-air exposure

This method uses the documented outdoor-air baseline of **400 ppm**.

1. Open external windows or doors in every room from which the MEV extracts
   air, such as bathrooms, utility rooms, and kitchens.
2. Ventilate those rooms for **10–15 minutes**.
3. Keep the rooms unoccupied and leave the openings open.
4. In the integration's Configure flow, choose **Fresh-air exposure** and
   review the final confirmation.
5. Confirm once. The MEV should flash its status LED magenta while sampling for
   about three minutes.
6. Keep the rooms open, unoccupied, and unchanged until the LED stops flashing.

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

## What success looks like

After protocol acceptance, observe the physical unit:

- the status LED should flash magenta for approximately three minutes;
- the rooms and reference conditions should remain stable during that period;
- later CO₂ readings should settle plausibly rather than change instantly.

The completion screen means only that the integration delivered the recovered
packet successfully. If the expected LED indication does not occur, assume the
calibration did not run and collect debug logs before trying again. Do not repeat
commands rapidly; the integration enforces a five-minute cooldown even after a
failed attempt because the device may have received it before the connection
failed.

## v0.3 physical validation record

Before v0.3 is promoted from release candidate, record:

| Check | Expected result |
| --- | --- |
| Open Configure without confirmation | No calibration packet is sent |
| Fresh-air preparation screen | Clearly states all extracted rooms, 10–15 minutes, unoccupied, and 400 ppm |
| Final confirmation | Defaults to off and names the device/reference |
| Confirm fresh-air calibration | Physical LED flashes magenta for about three minutes |
| Attempt again within five minutes | Flow reports rate limiting; no second packet is sent |
| Reference entity becomes unavailable before confirmation | Flow returns to sensor selection; no packet is sent |
| Select the MEV's own CO₂ entity | Selection is rejected; no packet is sent |
| Restart/reload integration | No calibration is repeated automatically |

Report the unit's model number, firmware version, transport (`fragmented` or
`whole`), reference method, visible LED behaviour, and before/after readings in
the v0.3 validation issue. Do not include the stored application setup code.
