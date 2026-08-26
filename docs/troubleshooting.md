# Troubleshooting

## The integration does not appear in HACS

- Add `https://github.com/sultanhq/mev-ble` through HACS's
  **Custom repositories** dialog and select **Integration**, not Plugin.
- Confirm HACS can access GitHub and the repository is public.
- Refresh HACS after adding the custom repository.
- After download, restart Home Assistant before searching under
  **Settings → Devices & services → Add integration**.

## No Vent-Axia device is discovered

- Put the unit into its Bluetooth pairing/setup mode.
- Confirm it advertises as `MEV` or `Multihome`. `Multivent` is not currently a
  supported discovery name.
- Confirm the Bluetooth integration and at least one connectable local adapter
  or active proxy are loaded in Home Assistant.
- For ESPHome, confirm both `esp32_ble_tracker:` and
  `bluetooth_proxy.active: true` are present and the proxy is connected to Home
  Assistant through the ESPHome integration.
- Move the proxy closer to the controller and away from metal enclosures,
  routers, switches, access points, USB 3 cables, and racks.
- Close the official mobile app while testing so it does not hold a connection.

## The device is discovered but setup cannot connect

- An advertisement-only proxy can discover the unit but cannot connect. Use a
  full ESP32 (or another ESPHome platform with active GATT proxy support).
- Confirm the ESPHome proxy has a free connection slot. Three slots is the ESP32
  default.
- Restart or update the ESPHome proxy if its Bluetooth scanner is unhealthy.
- Return the Vent-Axia unit to setup mode and retry near the controller.
- Confirm no operating-system Bluetooth pairing is being attempted. This
  integration uses an application setup code, not an OS Bluetooth bond.

## Automatic pairing fails

- Confirm the unit is still in pairing/setup mode; that mode may time out.
- Do not enter or guess a PIN. The integration completes the internal MEV
  application-code exchange automatically.
- If an existing entry requests reauthentication, put the unit into pairing
  mode again and select **Submit**.

## Entities are intermittently unavailable

- If using the Home Assistant host's local Bluetooth adapter, try a nearby active
  ESPHome proxy. The tested MEV had poor practical range.
- If already using a proxy, move it close to the controller and check that Home
  Assistant routes the MEV through that proxy. RSSI alone does not prove that
  GATT connections are reliable.
- Keep Wi-Fi proxies away from 2.4 GHz interference. Consider an Ethernet proxy
  with an external antenna for difficult locations.
- Ensure other BLE integrations are not exhausting every proxy connection slot.
- Do not use aggressive custom BLE scan interval/window settings.
- Check that the proxy remains connected to Home Assistant and that its API
  encryption credentials have not changed.

The integration disconnects and clears its transport after communication
failures, retains the last known local/proxy route, and retries on the next poll.
This specifically handles the MEV disappearing from the scanner cache while a
long-lived GATT connection is open. A manual reload should not normally be
needed with version 0.1.1 or newer.

## Debug logging

Add this to `configuration.yaml`, restart Home Assistant, and reproduce the
problem:

```yaml
logger:
  default: info
  logs:
    custom_components.ventaxia_multihome: debug
    bleak_retry_connector: debug
    habluetooth: debug
```

Download diagnostics from the device or config-entry page under
**Settings → Devices & services**. The stored setup code is redacted.

Before posting logs publicly, review them for other household identifiers and
Bluetooth addresses. The integration does not deliberately log the setup code.

## Report a useful issue

Open an issue at <https://github.com/sultanhq/mev-ble/issues> and include:

- Home Assistant version
- Integration version
- Vent-Axia model and firmware version
- Local Bluetooth adapter or exact ESP32/proxy board
- ESPHome version and relevant proxy YAML (with secrets removed)
- Whether discovery, setup-code confirmation, reads, or writes fail
- Redacted diagnostics and the relevant debug-log section
