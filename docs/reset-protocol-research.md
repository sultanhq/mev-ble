# Reset protocol research

This document records the evidence boundary for the v0.7.0 destructive reset
work. No reset packet was sent to hardware during this research.

## Evidence sources

The retained reverse-engineering set was built from:

- Vent-Axia Connect 6.0.28, version code 6000028, APK SHA-256
  `8191d00ea87328f3dffbf0cf183c22725f1947f95a0d264902183ea61d7def37`
- Vent-Axia Connect 7.2.2, version code 6000222, base APK SHA-256
  `82296ae79e2ae87ce97b5eed86c1263186700d5a1f5ab843d58aeaa62e94a5c8`

The 6.0.28 analysis covered the readable first-party
`com/volution/wrapper/acdeviceconnection/` Java/Kotlin tree, including the
MEV request package. The 7.2.2 analysis covered the React Native/Hermes
first-party application logic and its recovered packet enum and request paths.

The retained report explicitly states that its protocol conclusions were based
on coherent serializer/deserializer and call-path evidence rather than isolated
strings.

## Packet 61: HardReset

Packet 61 has a coherent recovered MEV request shape:

- packet type: 61 (`HardReset`)
- operation: 0
- payload wrapper: Raw DataObjectArray
- payload bytes: ASCII `RH`
- response/reboot consequences: not yet physically validated

This request is therefore sufficiently evidenced for #29 to implement behind an
internal guarded API. It is not yet exposed by Home Assistant and must remain
separate from packet 62.

## Packet 62: RestoreDefaults

Packet 62 is present in the recovered packet enum as:

- packet type: 62
- enum name: `RestoreDefaults`

No coherent MEV/Multihome request path was recovered for it.

### Connect 6.0.28 trace

The retained 6.0.28 analysis includes the complete first-party MEV request
package. It recovered normal MEV request serializers and the distinct packet-61
hard-reset request, but did not recover any MEV request class, presenter path,
service path, serializer, payload construction, target selection, response
handler, or recovery path for packet 62.

The retained evidence therefore does not establish that 6.0.28 can invoke
`RestoreDefaults` for an MEV/Multihome device.

### Connect 7.2.2 trace

The retained 7.2.2 Hermes analysis recovered the current MEV manager, packet
enum, transport, request/response paths and the principal command serializers.
`RestoreDefaults = 62` is retained in the packet enum, but the normal MEV
command/call-path evidence contains no packet-62 request construction or caller.

In particular, no MEV path was recovered that establishes:

- operation flags
- packet target/destination
- DataObjectArray type or other payload type
- payload bytes or fields
- expected response/acknowledgement
- reboot/disconnect behaviour
- what configuration domain would be restored
- whether the command is even reachable for the MEV model family

The prior CO2-calibration safety review reached the same boundary: packet 62 had
no recovered normal MEV call path, payload, scope, or calibration-only
behaviour.

### Cross-version result

Neither recovered app version supplies enough primary MEV evidence to construct
packet 62 safely.

The only retained packet-62 fact is its enum identity/name. That is insufficient
to infer a request. Packet 61's `RH` payload cannot be reused for packet 62,
and packet 62 cannot be treated as a CO2-calibration reset.

## v0.7.0 safety decision

Packet 62 `RestoreDefaults` is explicitly **blocked** for v0.7.0.

The integration must:

- not add packet 62 to the production `PacketType` enum
- not add a production serializer for packet 62
- not expose an entity, action, service, button or Configure flow for it
- not infer its payload, operation or target from neighbouring packet IDs
- not send a guessed packet to designated or production hardware

This is an evidence-based blocked decision, not a claim that packet 62 can never
be supported. It may be reconsidered only if new primary evidence supplies a
coherent MEV/Multihome call path, for example a later official-client
implementation or a known-safe capture from a designated reset test unit.

## Why packet 62 is not part of #29

Issue #29 can now proceed with packet 61 only. Packet 61 and packet 62 have
different evidence states:

| Command | Evidence state | v0.7.0 disposition |
| --- | --- | --- |
| 61 `HardReset` | coherent serializer/request evidence: Raw + ASCII `RH` | implement internally behind guards |
| 62 `RestoreDefaults` | enum identity only; no recovered MEV request path | explicitly blocked |

The typed destructive confirmation in #30 must therefore refer only to the
packet-61 hard reset unless new packet-62 evidence is recovered before release.

## Remaining v0.7.0 work

- #29: implement packet-61 hard reset behind an internal guarded API.
- #30: add device-specific typed destructive confirmation.
- #31: handle expected disconnect, rediscovery and recovery.
- #32: perform destructive validation only on designated hardware with a
  recorded restoration plan.
