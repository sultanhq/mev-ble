# ESPHome Bluetooth proxy

Home Assistant can connect to the ventilation unit through a normal ESPHome
Bluetooth proxy. The ESP32 does not run any Vent-Axia-specific code: it forwards
BLE advertisements and GATT connections to Home Assistant.

This integration requires `bluetooth_proxy.active: true`. Passive or
advertisement-only proxies can discover a device but cannot complete setup,
read telemetry, or send overrides.

## Choosing hardware

| Hardware | Suitable? | Notes |
| --- | --- | --- |
| Original ESP32, ESP32-WROOM-32, ESP32-WROVER, ESP32 DevKitC, NodeMCU-32S | Yes — recommended | ESPHome describes the original ESP32 as its most mature and well-tested variant. Cheap and widely available. |
| ESP32-S3 | Yes | BLE 5.0, native USB, and ample memory. Use `variant: esp32s3`. |
| ESP32-C3 | Yes | BLE 5.0 and compact/low-cost. Use `variant: esp32c3`; antenna quality varies on very small boards. |
| ESP32-C6 | Yes | BLE-capable and supported by current ESPHome. Use `variant: esp32c6`. |
| Olimex ESP32-PoE-ISO-EA | Yes — best RF/network option | Ethernet avoids sharing the radio with Wi-Fi; the `-EA` model has an external antenna connector. |
| ESP32-S2 | No | This variant has no Bluetooth or BLE hardware. |
| ESP8266 | No | It has no BLE hardware. |
| BK72xx or LN882x proxy | No for this integration | ESPHome supports advertisement forwarding only, not the active GATT connections this integration needs. |

ESP32-C2/C5/C61 variants include BLE but are newer choices; use an original
ESP32, ESP32-S3, or ESP32-C3 unless you already have one of those boards. An
ESP32-H2 has no Wi-Fi, while an ESP32-P4 has no integrated Bluetooth, so neither
is a straightforward Wi-Fi proxy choice.

See ESPHome's current [ESP32 platform](https://esphome.io/components/esp32/)
and [Bluetooth proxy](https://esphome.io/components/bluetooth_proxy/)
documentation for upstream hardware details.

## Wi-Fi proxy configuration

Copy [`examples/esp32-bluetooth-proxy.yaml`](../examples/esp32-bluetooth-proxy.yaml)
into the ESPHome Device Builder. The example targets an original ESP32. Change
only the `variant` if you use an S3, C3, or C6 board.

The referenced `secrets.yaml` values are:

```yaml
wifi_ssid: "YOUR_WIFI_NAME"
wifi_password: "YOUR_WIFI_PASSWORD"
esphome_api_encryption_key: "A_32_BYTE_BASE64_KEY"
esphome_ota_password: "A_LONG_UNIQUE_PASSWORD"
```

When a device is created in the ESPHome Device Builder, it normally generates
an API encryption key and OTA password. Keep those generated values instead of
reusing credentials from another node. A valid API key can also be generated
with `openssl rand -base64 32`.

Important proxy settings:

```yaml
esp32_ble_tracker:
  scan_parameters:
    active: true

bluetooth_proxy:
  active: true
  cache_services: true
  connection_slots: 3
```

`active: true` under `bluetooth_proxy` enables GATT connections. The similarly
named tracker setting controls active *scanning*. Service caching makes repeated
connections faster. ESPHome defaults to three ESP32 connection slots and
recommends no more than five for stability.

## Ethernet proxy configuration

For an Olimex ESP32-PoE-ISO or ESP32-PoE-ISO-EA, use
[`examples/esp32-ethernet-bluetooth-proxy.yaml`](../examples/esp32-ethernet-bluetooth-proxy.yaml).
It follows ESPHome's documented LAN8720 pinout and uses four connection slots,
which ESPHome recommends for Ethernet proxies.

## Flash and add the proxy

1. In **ESPHome Device Builder**, create or import the YAML.
2. Validate the configuration.
3. Connect the board by USB for the first installation and choose **Install**.
4. Wait for the node to join the network and appear online.
5. In Home Assistant, open **Settings → Devices & services** and configure the
   discovered **ESPHome** node. If it is not discovered, add the ESPHome
   integration manually using the node's hostname or IP address.
6. Confirm the proxy appears under the Home Assistant Bluetooth integration's
   adapters/scanners before configuring Vent-Axia Multihome.

Future firmware updates can use OTA. Current ESPHome OTA syntax is the
platform-style list used in both examples:

```yaml
ota:
  - platform: esphome
```

## Placement and reliability

- Place the proxy close to the Vent-Axia controller, ideally with few walls or
  metal services between them.
- Keep it away from USB 3 cables, Wi-Fi access points, switches, racks, and
  other RF-noisy equipment. ESPHome recommends at least 3 metres from network
  equipment where practical.
- Do not tune scan interval/window unless diagnosing a specific issue; ESPHome
  recommends the defaults.
- A Wi-Fi ESP32 shares one 2.4 GHz radio between Wi-Fi and BLE. Ethernet plus an
  external antenna gives the best reliability in difficult installations.
- Ensure at least one active connection slot is free. This integration connects
  briefly for each transaction and releases the slot afterwards.

