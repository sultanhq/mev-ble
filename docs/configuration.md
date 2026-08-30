# Configuration and use

## Add a Vent-Axia unit

1. Confirm the Bluetooth adapter or active ESPHome proxy is online.
2. Put the physical unit into Bluetooth pairing/setup mode.
3. Open **Settings → Devices & services**.
4. Configure the discovered **Vent-Axia Multihome** device. If no discovery card
   appears, choose **Add integration**, search for **Vent-Axia Multihome**, and
   select the discovered `MEV` or `Multihome` device.
5. Select **Submit** while the blue LED is flashing. No PIN is required.

Each BLE address can be configured once. The integration stores the address and
internal application code in the Home Assistant config entry, redacts the code
from diagnostic downloads, and never writes it to its own logs.

## Setup code versus Bluetooth PIN

The connection is PIN-less at the operating-system Bluetooth layer: Home
Assistant does not pair or bond the ESP32/host with the Vent-Axia unit and no OS
pairing dialog should appear.

The Vent-Axia application protocol still exchanges a four-byte code internally,
but the MEV does not ask the user to enter a PIN. While the unit is in physical
pairing mode, the integration reads the code exposed by its documented GATT
characteristic, writes the same value back, and checks the confirmation
characteristic. The internal config key is named `setup_code` only to
distinguish this stored value from an OS Bluetooth PIN.

If the stored value is rejected, reauthentication asks you to put the unit back
into setup mode so Home Assistant can repeat the exchange automatically.

## Entities

| Entity | Purpose |
| --- | --- |
| Temperature | Zone temperature reported by the unit |
| Relative humidity | Zone relative humidity |
| CO₂ | CO₂ concentration, created only when the zone reports sensor support |
| Fan RPM | Reported motor speed |
| Fan level | Numeric/documented airflow level |
| Fan state | Reported operating state |
| Override remaining | Device-reported or locally estimated seconds remaining |
| Ventilation fan | Low, Normal, Boost, and Purge timed presets |
| Cancel override | Ends the current BLE override and returns control to the unit |
| Fault binary sensors | Motor, sensor, attachment, alarm, firmware, battery, filter, and service diagnostics |

## Global settings and airflow commissioning

The integration reads the unit's complete 36-byte global-settings record and
includes it in **Download diagnostics**. On validated four-speed models with a
current successful read, **Configure → Configure airflow levels** provides one
guarded four-field flow for Low, Normal, Boost, and Purge.

The form values are commissioned motor-speed percentages, not the measured
**Fan RPM** sensor. They use the official ranges Low 1–97%, Normal 2–98%, Boost
3–99%, and Purge 4–100%, in 1% steps. They must remain strictly ordered
`Low < Normal < Boost < Purge`.

The flow shows the current and proposed complete profile before it writes. At
final confirmation it checks that the original 36-byte record is still current,
then applies only changed fields in a safe order. Every individual write must be
returned exactly by an immediate fresh read before the next field is sent. If
the settings are unavailable, stale, unsupported, or change during review, the
flow does not expose or perform a write.

The diagnostic snapshot includes airflow percentages, humidity and temperature
options, delay and overrun timeouts, LS/analogue/digital input actions, CO₂
thresholds, the original raw record, and a list of any boolean fields whose
firmware value was not the documented zero or one. Unknown action numbers are
retained numerically instead of being assigned guessed meanings.

All non-airflow global fields remain read-only. See the
[global-settings safety model and supported-field matrix](global-settings.md)
for the current confidence and hardware-validation status.

## Supported control scope

The physically inspected MEV RF remote exposes speed levels 1–4 and timer
buttons for 30, 60, 120, and 240 minutes. It has no On, Off, Stop, or Cancel
button. Home Assistant therefore exposes Low, Normal, Boost, and Purge timed
presets, but it does not advertise standard fan On/Off or a Stop action.

The official BLE app protocol contains a distinct Cancel override operation,
which returns control to the unit without sending an inferred power mode. There
is no equivalent button on the inspected RF remote, so Cancel remains clearly
separate from the hardware-matched speed/timer controls.

The recovered protocol also assigns byte values to Off and Stop modes. Those
bytes remain available in the offline reference codec for research, but the
Home Assistant integration does not send them without physical capability and
safety evidence.

After a preset or Cancel request, the integration reads fresh zone and system
telemetry before publishing state. A rejected or timed-out request retains the
last confirmed values and raises an action error.

Some MEV firmware accepts timed overrides but reports zero in the system-status
countdown field while the override is visibly active. After Home Assistant
successfully starts an override, the integration uses the commanded duration as
a local countdown only when that false-zero condition occurs. A nonzero device
value always remains authoritative. If an override was started externally and
the firmware reports zero, the entity is unavailable because its duration is
unknown. Diagnostics identify the source as `device`, `estimated`, or
`unavailable`.

## Default override duration

The fan preset control initially uses 1,800 seconds (30 minutes).

To match the inspected RF timer buttons, use 1,800, 3,600, 7,200, or 14,400
seconds for 30, 60, 120, or 240 minutes respectively. The recovered BLE app
allows other durations up to eight hours, so the integration does not restrict
the field to only those four values.

1. Open **Settings → Devices & services → Vent-Axia Multihome**.
2. Open the configured device's **Configure** or options dialog.
3. Set **Default override duration** between 1 and 28,800 seconds.

Changing a preset on the fan entity uses this duration.

## Internal CO₂ calibration

For validated models with an internal CO₂ sensor, the integration's Configure
menu includes a guarded calibration flow. The Vent-Axia method exposes every
extracted room to outdoor air for at least 15 minutes and defaults to 450 ppm,
matching the current official app. The value can be replaced with a current
400–2,000 ppm reading from a trusted calibrated measurement device. An advanced method can average
independent Home Assistant CO₂ sensor entities, but Home Assistant cannot prove
those sensors are calibrated or even measure true CO₂ rather than eCO₂.

Calibration requires a separate final confirmation, cannot use the MEV's own
CO₂ entity as its reference, and is limited to one attempt every five minutes.
It is not exposed as an action or automation service. Read the complete
[CO₂ calibration safety and validation guide](co2-calibration.md) before use.

After the command is delivered, Home Assistant displays a progress bar for the
manual's three-minute internal-sensor sampling period. Users do not need to see
or reach the installed MEV/Multihome unit. The progress is an elapsed-time guide
rather than device readback because the recovered BLE protocol exposes no
completion state.

The official app's 450 ppm default is a calibration reference, not a live
worldwide or local outdoor measurement. Local outdoor air can vary, so use a
trusted calibrated instrument value when greater accuracy is required.

Calibration may be run again after the five-minute safety cooldown. Home
Assistant exposes no reset because the recovered general `RestoreDefaults`
enum has no validated MEV payload or calibration-only scope and may affect wider
unit settings. To correct an earlier calibration, repeat the guarded flow with
a properly prepared fresh-air or trusted-sensor reference.

## Timed override action

Use `ventaxia_multihome.set_timed_override` when an automation needs an explicit
duration. Valid presets are `low`, `normal`, `boost`, and `purge`.

```yaml
action: ventaxia_multihome.set_timed_override
target:
  entity_id: fan.vent_axia_multihome_ventilation
data:
  preset: boost
  duration: 900
```

The duration is in seconds and must be between 1 and 28,800. The action is also
available in **Developer tools → Actions**, where Home Assistant provides field
selectors.

To apply the configured default duration, use the standard fan action:

```yaml
action: fan.set_preset_mode
target:
  entity_id: fan.vent_axia_multihome_ventilation
data:
  preset_mode: boost
```

To end an override, use `button.press` on the **Cancel override** entity:

```yaml
action: button.press
target:
  entity_id: button.vent_axia_multihome_cancel_override
```

Entity IDs are generated by Home Assistant and may differ from these examples.
Choose the actual entity in the automation editor.

## Polling and availability

Home Assistant polls zone telemetry, system status, and global settings every
10 seconds over a normally long-lived GATT connection. Failed
communication makes the entities
unavailable, clears stale protocol state, and retries normally. The integration
retains the last known Bluetooth route so it can reconnect even when the MEV's
advertisement has expired from Home Assistant's scanner cache.

When the unit exposes the whole-packet characteristic, requests are written
without response and incomplete results are polled with a 50 ms cooperative
delay. A valid response is acknowledged; malformed responses are discarded and
polling continues until the five-second deadline. Timeout, task cancellation,
or a GATT failure sends the documented cancellation before transport state is
cleared. Older firmware uses the equivalent acknowledged 20-byte fragmented
path.

Home Assistant's Bluetooth manager chooses a reachable local adapter or active
proxy automatically; there is no adapter setting in this integration. An ESP32
proxy is optional, but the tested MEV had poor practical range and benefited
from a proxy placed close to the controller.

Only one operation is sent to a unit at a time. Each user action keeps the same
device lock from its packet-56 write through a fresh zone and system-status
readback, so a scheduled poll cannot consume or overwrite the control result.
Home Assistant publishes the result only after that readback succeeds. If the
write or readback fails, the last confirmed values are retained while the
entities become unavailable until communication recovers.

Home Assistant Core excludes entities that are already unavailable when it
dispatches entity services. Consequently, an action targeting an unavailable
fan may be skipped without a user-facing action error because the integration
is never called. An operation that starts while the entity is still available
will report its Bluetooth failure normally.

The locally estimated countdown survives ordinary BLE reconnects while Home
Assistant remains running. It cannot be reconstructed after a Home Assistant
restart unless the firmware supplies a nonzero remaining value.
