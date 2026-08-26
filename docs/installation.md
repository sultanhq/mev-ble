# Installation

## Prerequisites

- Home Assistant 2026.8.0 or newer
- The Home Assistant Bluetooth integration
- A connectable local Bluetooth adapter or active ESPHome Bluetooth proxy
- A unit advertising as `MEV` or `Multihome`
- Access to the unit's physical pairing/setup mode

An ESP32 is not required when the Home Assistant host already has a supported
Bluetooth adapter with a reliable connection to the MEV. The physically tested
MEV had poor practical range, so a nearby ESPHome proxy may still be needed even
when Home Assistant has local Bluetooth.

The integration reads and writes BLE GATT characteristics. If a proxy is used,
it must support active connections; an advertisement-only proxy is insufficient.
See [Choosing the Bluetooth route](../README.md#choosing-the-bluetooth-route) or
the [ESPHome Bluetooth proxy guide](esphome-bluetooth-proxy.md).

## Install with HACS

This is a HACS *Integration* repository, not a frontend plugin.

1. Install and configure [HACS](https://www.hacs.xyz/docs/use/) if it is not
   already present.
2. In Home Assistant, open **HACS**.
3. Open the three-dot menu in the top-right and select
   **Custom repositories**.
4. Enter `https://github.com/sultanhq/mev-ble` as the repository URL.
5. Select **Integration** as the repository type and choose **Add**.
6. Open **Vent-Axia Multihome** in HACS and choose **Download**.
7. Restart Home Assistant when HACS prompts you.
8. Open **Settings → Devices & services**. Configure the discovered
   **Vent-Axia Multihome** device, or choose **Add integration** and search for
   it manually.

If the custom repository dialog rejects the URL, confirm the GitHub repository
is public, its default branch contains `hacs.json`, and
`custom_components/ventaxia_multihome/manifest.json` exists.

## Configure the device

1. Confirm that the local adapter or ESPHome proxy is online and within BLE
   range of the Vent-Axia controller.
2. Put the unit into its Bluetooth pairing/setup mode.
3. Start configuration in Home Assistant.
4. Select **Submit** while the blue LED is flashing. No PIN is required.

The integration completes the internal Vent-Axia GATT exchange automatically.
Home Assistant does not create an operating-system Bluetooth pairing or bond. See the
[configuration guide](configuration.md) for entities, options, and actions.

## Update

HACS displays an update when a newer repository release is available. Open the
repository in HACS, choose **Update**, and restart Home Assistant. Check the
[changelog](../CHANGELOG.md) before updating.

## Remove

1. Open **Settings → Devices & services → Vent-Axia Multihome** and delete the
   device's config entry.
2. Open the repository in HACS and choose **Remove**.
3. Restart Home Assistant.

Removing the integration does not change the ventilation unit's configuration.

## Manual installation

If HACS is unavailable, copy the entire
`custom_components/ventaxia_multihome` directory to
`<home-assistant-config>/custom_components/ventaxia_multihome`, restart Home
Assistant, and then add the integration from **Settings → Devices & services**.
Manual installations do not receive HACS update notifications.
