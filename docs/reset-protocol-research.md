# Reset protocol research

This document records the evidence boundary for the v0.7.0 destructive reset work.
It is intentionally conservative: no reset packet should be sent to hardware from
this research branch.

## Packet 61: HardReset

The retained reverse-engineering report identifies packet type 61 as
`HardReset`. The recovered official-client call path serializes a Raw
DataObjectArray containing ASCII `RH`. That evidence is sufficient to keep
packet 61 distinct for issue #29, but it is not yet exposed by the Home Assistant
integration.

Known evidence:

- packet type: 61 (`HardReset`)
- payload wrapper: Raw DataObjectArray
- raw payload: ASCII `RH`
- destructive scope: hard reset / reboot path; exact retained configuration and
  pairing consequences still require designated-hardware validation
- physical validation: not yet performed

## Packet 62: RestoreDefaults

The recovered packet enum names type 62 `RestoreDefaults`, but the retained
evidence currently does **not** establish a complete callable MEV/Multihome path.

Known evidence:

- packet type: 62
- enum name: `RestoreDefaults`

Not yet recovered from primary official-client evidence:

- operation flags
- packet target/destination
- payload type
- payload bytes or fields
- response/acknowledgement behaviour
- whether the command reboots the unit
- which state is restored: calibration, global installer settings, schedules,
  pairing/application code, attached-device state, or some wider combination
- whether the command is reachable for the validated MEV model family at all

The enum identity alone is not enough to infer any of these details. In
particular, packet 62 must not reuse packet 61's `RH` payload and must not be
treated as a CO2-calibration reset.

## Current safety decision

Until the official-client call path is recovered, packet 62 remains blocked:

- do not add a production serializer for `RestoreDefaults`
- do not add it to the integration's production `PacketType` enum
- do not expose an entity, action, service, or Configure flow
- do not infer a payload or target from neighbouring packet types
- do not test a guessed packet on the installed ventilation unit

This is a research block, not a conclusion that packet 62 is permanently
unsupported. Issue #49 remains the evidence-recovery gate.

## Evidence recovery required for #49

Reinspect both retained official-client versions:

1. Vent-Axia Connect 6.0.28, APK SHA-256
   `8191d00ea87328f3dffbf0cf183c22725f1947f95a0d264902183ea61d7def37`.
2. Vent-Axia Connect 7.2.2 base APK, SHA-256
   `82296ae79e2ae87ce97b5eed86c1263186700d5a1f5ab843d58aeaa62e94a5c8`.

For each version:

1. Find every reference to the packet enum value/name for `RestoreDefaults`.
2. Trace each reference to the request/packet constructor.
3. Record operation, target, payload type and exact payload construction.
4. Trace the response handler or disconnect/reboot path.
5. Trace the UI/presenter/service call that invokes it, if one exists.
6. Record what state the UI says will be removed or restored.
7. Cross-check whether the path is MEV/Multihome-specific or belongs to another
   product family.

If both versions expose only the enum declaration and no reachable call path,
record that negative result with the searched symbols/locations and keep packet
62 explicitly blocked. If a coherent call path is found, add deterministic
offline fixtures before any hardware work.

## Relationship to the remaining v0.7.0 work

- #49 (this research): establish or explicitly block packet 62.
- #29: implement only the separately recovered packet-61 hard-reset command
  behind an internal guarded API.
- #30: add device-specific typed destructive confirmation after #29/#49 settle
  command scope.
- #31: handle expected reset disconnect, rediscovery and recovery.
- #32: perform destructive validation only on designated hardware with a
  recorded restoration plan.
