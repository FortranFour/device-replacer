# Device Replacer 1.1.2

Public HACS packaging of the device-first editor, with the selected cyan connection-handoff icon.

- Search named devices, map their entity roles, review selected changes and apply with recoverable backups.
- Keep single-entity replacement, unavailable/removed source support and existing Entity Replacer upgrades.
- Validate compatible YAML device automation blocks and preserve manual reports with paths and line numbers.
- Skip known binary formats quietly while reporting relevant internal JSON stores.
- Read devices and entities from the installer’s Home Assistant instance; no author configuration is bundled.

Requires Home Assistant 2026.3.0 or newer. Add https://github.com/FortranFour/device-replacer to HACS as an **Integration**, download, restart, then add **Device Replacer** under Settings → Devices & services. Existing Entity Replacer users keep their existing entry.

Energy settings remain manual. Historical statistics, hardware pairing and registry identity are not transferred. Read README.md for coverage, recovery and limitations.

Automated file/transaction and frontend logic tests use mocked Home Assistant adapters. Live HACS/hassfest checks run on GitHub; actual device behavior still needs testing on the user's instance.
