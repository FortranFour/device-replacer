# Changelog

## 1.1.2 — 2026-10-05

- Package the device-first 1.1.1 workflow for public installation through a HACS custom repository.
- Restore GitHub metadata, validation and tagged release workflows for FortranFour/device-replacer.
- Include the cyan connection-handoff icon and all light/dark brand assets.
- Add detailed public usage, recovery and privacy documentation, plus Windows Git Bash publishing instructions.
- Preserve device/entity discovery from each installation; include only synthetic examples and test fixtures.

## 1.1.1 — 2026-10-03

- Rebuild for local installation with device replacement as the default workflow.
- Replace raw device-ID suggestions with searchable named device lists and a separate removed-device option.
- Preserve confirmed entity role mappings, native device capability checks, single-entity mode, backups and restore.
- Include the chosen cyan connection-handoff icon and light/dark wordmarks.
- Retain quiet binary exclusions and active JSON storage reporting.
- Defer GitHub/HACS publication metadata and workflows; provide direct installation instructions.

## 1.1.0 — 2026-10-03

- Expand Entity Replacer into **Device Replacer**, with whole-device and single-entity modes.
- Inventory both devices' entities, suggest unambiguous same-domain role matches, and require explicit mapping confirmation.
- Replace multiple approved entity IDs simultaneously in YAML and saved UI dashboards; report unmapped entities without changing them.
- Include entity registry IDs used by device automations as well as ordinary entity IDs.
- Permit complete YAML device triggers, conditions and actions only when the new device advertises matching capabilities and native config validators accept the replacement.
- Keep device/entity selectors together; recheck registry identity, availability and capabilities before review and apply.
- Support device service targets only when every entity in the service domain has a compatible one-to-one mapping.
- Support removed source devices through explicitly entered device/entity/registry IDs, with missing-inventory limitations explained.
- Quietly exclude known binary storage extensions and recognizable binary signatures; keep relevant active JSON stores in the scan.
- Keep domain `entity_replacer`, existing config entries, backups and restore support for previous releases.
- Retain the chosen cyan mark; update wordmarks, GitHub/HACS metadata and publishing instructions for `FortranFour/device-replacer`.

## 1.0.1 — 2026-10-03

- Prepare the integration for installation from `FortranFour/entity-replacer` through HACS.
- Add the cyan connection-handoff icon, local Home Assistant brand images, and a branded panel header.
- Set repository documentation, issue-tracker, and code-owner metadata.
- Add GitHub validation, automated tagged releases, and a Git Bash publishing helper.
- Document Energy dashboard references as manual review and clarify the lack of history/statistics migration.
- Preserve the replacement, review, backup, and restore behavior of 1.0.0.

## 1.0.0 — 2026-10-02

- Initial Entity Replacer integration: scan literal references, review individual changes, replace YAML and native UI-dashboard references, and restore durable backups.
- Include paths and line numbers for references that require manual work.
