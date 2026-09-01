# Contributing

Bug reports and hardware-validation results are especially useful. Initial
testing covers one physical MEV through an ESPHome Bluetooth proxy, but other
models, firmware versions, local adapters, and proxy boards need wider coverage.

By submitting a contribution, you confirm that you have the right to contribute
it and agree that it may be distributed under this project's MIT License.

## Before opening an issue

Read the [troubleshooting guide](docs/troubleshooting.md), enable debug logging,
and remove secrets from any ESPHome YAML or diagnostics you attach. Include the
unit model/firmware, Home Assistant version, integration version, Bluetooth
adapter or proxy model, and the exact operation that failed.

## Development

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/pytest -q
.venv/bin/ruff check custom_components tests scripts/validate_multihome.py
.venv/bin/python scripts/mev_protocol.py self-test
```

Keep all runtime dependencies and files inside
`custom_components/ventaxia_multihome/`, as required by HACS. Do not commit APKs,
decompiled application output, credentials, Bluetooth captures containing
private data, or generated reverse-engineering workspaces.

Changes that write new device settings need protocol evidence, tests, and a
clear safety justification. Calibration, installer configuration, schedules,
reset commands, and fan power-off remain outside the initial integration scope.

## Release checklist

1. Update `version` in `custom_components/ventaxia_multihome/manifest.json`.
2. Move the relevant changelog entries from **Unreleased** to the new version.
3. Run tests, Ruff, the protocol self-test, HACS validation, and Hassfest.
4. Tag the commit as `vX.Y.Z` and publish a GitHub release with the same version.

HACS can install the default branch without a release, but releases give users
stable version choices and are required before applying to HACS's default
catalog.
