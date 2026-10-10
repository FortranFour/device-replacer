<p><img src="custom_components/entity_replacer/brand/icon.png" width="80" height="80" alt="Device Replacer connection-handoff icon"></p>

# Device Replacer

This Home Assistant custom integration addresses the problem when a device fails and its entities are used across a Home Assistant installation, including in dashboards, automations, scripts, etc. It automates substitution of the replacement device's entities in user configurable files.

Select the old and new devices, confirm the mapping between their entities, inspect the preview, and apply selected changes with backups. Single-entity mode is also available. Working, unavailable and disabled source devices/entities are included; removed sources can be entered explicitly.

The integration updates literal references in YAML and saved UI dashboards. Compatible YAML device automation blocks can change both device and entity selectors after capability validation. Other settings get a report with file paths and line numbers for manual follow-up. Add/pair the replacement hardware through its normal integration first.

It adds a **Device Replacer** page to the administrator sidebar. There is no dashboard card, frontend resource entry, or `configuration.yaml` setup. The internal domain, installation directory and panel URL remain `entity_replacer` and `/entity-replacer` so existing Entity Replacer installs upgrade in place.

Maintained by [FortranFour](https://github.com/FortranFour). Current release: **1.1.3**. Requires **Home Assistant 2026.3.0 or newer**, including the bundled local brand images. It uses Home Assistant's existing Python dependencies and has no frontend CDN or additional pip requirements.

<img width="1352" height="1151" alt="Screenshot 2026-10-05 104154" src="https://github.com/user-attachments/assets/89a87a1b-38c2-409e-babf-6179e8c9e9a3" />


## Before you begin

Use an administrator account and Home Assistant 2026.3.0 or newer. For HACS installation, [install and configure HACS](https://www.hacs.dev/docs/use/) first. Pair/add the new hardware through its normal integration and verify its entities before replacing references. Keep a current Home Assistant backup; replacement-job backups cover the selected references, not your entire installation.

## Install through HACS

1. Open **HACS → ⋮ → Custom repositories**.
2. Add `https://github.com/FortranFour/device-replacer` with type **Integration**.
3. Find **Device Replacer**, select **Download**, and choose the latest release.
4. Restart Home Assistant.
5. Open **Settings → Devices & services → Add integration → Device Replacer** and submit the short setup form.
6. Open **Device Replacer** in the sidebar while signed in as an administrator. If it does not appear, refresh the browser/app. Its direct path is `/entity-replacer` on your Home Assistant server.

This is a HACS **custom repository**; adding it does not require inclusion in HACS's default catalog.

### Upgrade an existing manual installation

Download through HACS and restart Home Assistant. **Device Replacer replaces the existing integration in place; it does not run as a second side-by-side integration.** Keep your existing Entity Replacer or Device Replacer entry: the integration domain is still `entity_replacer`, and backups remain in `/config/.entity_replacer_backups`. If HACS offers to replace the existing integration files, proceed with its download. There is no need to configure a second integration entry.

### Manual installation

1. Download `device_replacer_1.1.3.zip` from [GitHub Releases](https://github.com/FortranFour/device-replacer/releases) and upload it to `/config` using your usual file editor or Samba share.
2. In the Home Assistant Terminal/SSH add-on, extract the integration:

   ```bash
   unzip -o /config/device_replacer_1.1.3.zip 'custom_components/entity_replacer/*' -d /config
   ```

   The final path must be `/config/custom_components/entity_replacer/manifest.json`. Do not put an extra `entity_replacer` directory between `custom_components` and the integration files.

3. Restart Home Assistant. If using the Terminal/SSH add-on with the HA CLI:

   ```bash
   ha core restart
   ```

4. Complete the same integration setup and open the sidebar page as described above.
5. You can delete the installation ZIP after extraction:

   ```bash
   rm -f /config/device_replacer_1.1.3.zip
   ```

The final path must be `/config/custom_components/entity_replacer/manifest.json`, with `brand/` and `frontend/` alongside it. All runtime files are inside that integration directory.

## Replacing a device

1. Open **Whole device** (the default). Choose **Device to replace** and **Replacement device**. Use the searchable named lists; filter by name, manufacturer, model or ID. Each option shows its name, model, entity count and shortened ID.
2. Click **Load entity mappings**. The table includes registered source entities even if they are disabled or unavailable. Suggestions use domain, role metadata, device class and units; ambiguous matches stay unassigned.
3. Choose the replacement for each entity role. Replacements must belong to the chosen new device and have the same domain. Each replacement can be assigned once. Choose **Leave unmapped** for roles you do not want to change; omitted entities also remain unmapped.

   | Original entity | Replacement role |
   | --- | --- |
   | `switch.old_plug` | `switch.new_plug` |
   | `sensor.old_power` | `sensor.new_power` |
   | `sensor.old_energy` | `sensor.new_energy` |
   | `sensor.old_diagnostic` | Leave unmapped |

4. Check **I have checked these entity roles…**, then **Scan references**. Editing mappings clears the confirmation. A scan does not write configuration or create a replacement backup.
5. Expand each file/dashboard to inspect the references, paths, 1-based line/column numbers, and proposed old → new IDs. Saved UI dashboards also have JSON pointers; pointers replace line numbers when the live configuration has not reached disk yet.
6. Select the references you want. Device/entity selectors in a validated device automation block toggle together. References marked **Review** stay manual. Mapped ordinary entity references can be selected independently.
7. Click **Review selected replacements**. Read the exact diffs. Duplicate keys, changed files, changed registries, unavailable targets, or capability conflicts stop the operation before writing.
8. Click **Apply reviewed replacements**. The confirmed entity mappings and selected device blocks form one backed-up job. Changing selection requires a fresh review. Multiple source IDs are replaced from their original spans, so one replacement cannot accidentally become another replacement's source.
9. For YAML changes, run **Developer tools → YAML → Check configuration**, then restart Home Assistant or reload the affected reloadable sections. Saved UI dashboards refresh through the native Lovelace API. No restart or automation/script reload is performed automatically.
10. Scan again and finish the manual items. Applying all eligible references does not mean every setting has been migrated.

### Device automation checks

Device mode recognizes literal YAML device triggers, conditions and actions. It maps ordinary entity IDs and the opaque 32-character entity registry IDs used by device automations. The old device ID and mapped entity selectors change as a complete group only when:

- The selector block has literal, fully mapped values, without shared anchors, encoded selectors, duplicate keys or unknown tags.
- The new device advertises the same domain/type/subtype/entity capability.
- The platform's native schema and/or dynamic config validator accepts the candidate.
- Mapped entities have compatible device classes/units and retain the source's supported features when known.

Capabilities and registry membership are checked again at review and apply. The tool does not execute an action or attach a trigger to test compatibility. A matching advertisement is not a physical hardware test: confirm behavior afterward.

Literal service `target.device_id` blocks are eligible when the service exists, every old entity in its domain has a compatible mapping, and those mappings exactly cover the new device's entities in that domain. Generic `homeassistant` device targets and incomplete mappings remain manual; use explicit entity targets or rebuild the action.

A device selector in a dashboard, blueprint input, unknown structure, or internal JSON store remains manual. Different hardware protocols often expose different device event types; those blocks must be rebuilt in the automation editor even if their ordinary entity references can be updated.

### Removed source devices

Choose **Removed device: enter its former ID…**, enter the former device's 32-character ID, then load mappings. Use **Add a former entity** to enter its old entity IDs and choose replacements. If available, also enter each old **entity registry ID** from a device automation. Registry IDs are different from IDs such as `sensor.old_power`; they are not the hardware integration's `unique_id`.

The device ID is visible at the end of a Home Assistant device-page URL while the device exists. Keep it before removal, or obtain it from a saved YAML device block. Without the former inventory or explicitly entered IDs, the tool cannot discover all of a removed device's entities. Blocks whose old entity capabilities cannot be established remain manual.

### Replacing one entity

Choose **Single entity**, enter/select the source ID and an existing replacement in the same domain, then follow the same scan → select → review → apply steps. Removed source entity IDs can be typed directly. Device-based blocks stay manual in single-entity mode.

Replacements must be working by default. **Additional scan options → Allow unavailable or disabled replacements** permits known targets that are not usable yet. A replacement device with entities that are all unavailable also requires this option, even for a device-only event. Devices without entities may expose no online state; the report says when availability cannot be inferred. Completely unknown replacement devices/entities are rejected. Differences in classes, units or supported features are reported when metadata is available; review numeric thresholds and service data even for ordinary entity-only changes.

## Coverage

| Configuration | Handling |
| --- | --- |
| `configuration.yaml`, `automations.yaml`, `scripts.yaml`, `scenes.yaml`, groups, and other `.yaml` / `.yml` files | Reviewed automatic replacement |
| Packages, included YAML fragments, blueprint YAML, and YAML dashboards beneath the configuration directory | Reviewed automatic replacement |
| Literal entity IDs in Jinja templates, `states.sensor.entity` access, URLs, button-card JavaScript, and other strings inside YAML | Reviewed automatic replacement |
| Saved dashboards edited through the UI | Reviewed replacement through the native Lovelace save API; persisted content is checked afterward |
| UI helpers, integration options, Energy configuration, exposure settings, and other active `.storage` files | Paths, line numbers, JSON pointers, and manual instructions; `core.config_entries` results identify the owning integration/helper when available |
| Additional `.json`, `.js`, `.jinja`, `.j2`, `.html`, and `.py` files | Manual-reference report |
| YAML device triggers, conditions, and actions | Complete-group replacement in device mode after native capability/config checks; otherwise manual |
| YAML service device targets | Conditional replacement for complete compatible domain mappings; otherwise manual |
| Dashboard device selectors and blueprint device inputs | Manual report with locations |
| Comments, YAML tags, identity fields such as `unique_id` / `default_entity_id`, and encoded YAML values | Manual/informational report; unchanged automatically |

The scan covers literal references in the listed configuration sources. It does not establish that every file is currently loaded by Home Assistant: an old YAML file in an ordinary configuration folder may also appear, and you can deselect it. Included fragments must have a `.yaml` or `.yml` extension and be beneath the configuration directory.

Known binary storage extensions and SQLite, binary pickle, archive/compression, image and PDF signatures are skipped quietly. Recognizable formats without an extension are also excluded. Relevant JSON and extensionless Home Assistant stores remain included. Unknown unreadable content still receives a warning. Binary data is never deserialized or modified.

Excluded sources are `secrets.yaml`; authentication stores; entity/device/area/label/floor identity registries; restore/history caches and traces; hidden directories except the explicitly scanned `.storage` directory; custom integration code; `www/community`; dependency directories; backups; symbolic links; and files outside the configuration directory. Excluded sources are not edited. YAML files named `secrets.yaml` are excluded at any depth.

The limits are 8 MiB per file or dashboard, 64 MiB of filesystem content, 10,000 filesystem files, and 100,000 graph visits per parsed document, and 200 entities per device/mapping batch. Skipped, unreadable, oversized, or unparseable sources are reported; a limit makes the scan incomplete. Files must be UTF-8. Scans run only on request, with filesystem access, parsing, and diff generation off Home Assistant's event loop. The integration does not poll or watch your configuration continuously.

### What needs manual work

**Unsupported device blocks:** Recreate the trigger/condition/action using the replacement device in the automation editor, or convert it to an entity-based block. The preview explains why a block was left manual. Do not independently replace only the device ID in an incompatible block. Blueprint device inputs and dashboard device selectors require their owning editor.

**UI helpers and integration settings:** Open **Settings → Devices & services**, select the named integration or helper, and edit its referenced entity through the owning UI. Some integrations require a helper to be recreated. The report shows the physical storage path and location for identification, but the integration never rewrites those internal stores directly.

**Energy dashboard:** Enable **Additional scan options → Report references in other internal storage (manual review)** to report literal matching references in Energy settings. Change the configured sensor under **Settings → Dashboards → Energy**. Energy configuration is report-only in this release. Replacing an entity reference does not migrate or merge the old sensor's historical energy statistics.

**Cross-domain changes:** Replacing a `switch` with a `light`, or a `sensor` with a `binary_sensor`, can require different actions, values, conditions, and card types. This release restricts automatic replacement to the same domain. Even within a domain, inspect device capabilities, service data, thresholds, and units in the preview.

**Computed references and external systems:** IDs assembled from pieces, wildcard/regex selections, aliases selected through areas or labels, former device/entity IDs you have not supplied after registry removal, and add-on-owned configurations such as Node-RED are not fully discoverable by a literal scan. Review those separately. Replacing references does not rename or delete the source entity, migrate recorder history or statistics, pair hardware, or transfer entity/device registry identity.

## Report-only use

Click **Download paths & line numbers** for a Markdown report or **Download JSON report** for structured locations. You can download these immediately after scanning and make manual edits without applying any replacements. **Download selected diff** is available after the review step.

For a saved UI dashboard, use its menu → **Edit dashboard → Raw configuration editor** for manual changes. The JSON pointer identifies the setting; physical `.storage` line numbers are for locating it, not for editing the live storage file.

The repository also includes a standalone, read-only `audit.py`. It works against a configuration directory or exported copy without loading Home Assistant. It requires Python with PyYAML and must remain beside the repository's `custom_components` folder. It is not needed or installed by HACS:

```bash
python3 audit.py --config /path/to/config-copy --source sensor.old_power --replacement sensor.new_power > entity_replacer_report.md
```

This offline mode reports UI dashboard storage as manual references. It does not write configuration, create backups, or apply replacements.

## Privacy and portability

No author entities, device IDs, automations, dashboards, backups, credentials or Home Assistant configuration are distributed. Device and entity lists come from your own instance when you open the panel. Documentation examples and test fixtures are synthetic. FortranFour appears only as the public project owner and maintainer.

Scanning and editing run on your Home Assistant server. The integration has no telemetry, cloud account, external frontend libraries or analytics. HACS/GitHub perform their normal download/update operations separately. Reports, diffs and job backups can contain your entity/device IDs and configuration excerpts; review/redact them before attaching them to a public issue.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Integration missing from Add integration | Confirm `/config/custom_components/entity_replacer/manifest.json` exists, restart Home Assistant, refresh the browser and search for **Device Replacer**. Review **Settings → System → Logs** for `entity_replacer`. |
| Sidebar page missing or access denied | Use an administrator account, ensure the integration entry is loaded, refresh the app/browser and try `/entity-replacer`. |
| Device list empty or a replacement missing | Add the hardware through its owning integration first. Search by name/model, clear the filter and refresh the inventory. The source and replacement must be different devices. |
| Scan or apply rejected | Read the message. Confirm the mappings again, make targets available, or perform a fresh scan/review after external edits or registry changes. |
| A reference cannot be selected | Its reason is shown in the preview. Use the owning editor for unsupported device blocks, internal storage and other manual items. |
| No changes in running automations after applying | Check YAML configuration and reload the affected section or restart Home Assistant. Verify the automation behavior afterward. |
| HACS shows a generic icon | The icon is bundled locally for Home Assistant 2026.3+. A placeholder in a HACS catalog view can depend on that HACS version; the panel uses its bundled SVG directly. |
| Restore refuses | A selected file/dashboard has changed since the replacement. Compare the retained backup with the current version and recover the intended changes manually instead of overwriting newer work. |

For support, include the Home Assistant, HACS and Device Replacer versions, the operation/mode, the exact error and a small redacted example. Do not post your complete `.storage` files, credentials, authentication tokens or unredacted replacement backups.

## Backups and restore

Backups are saved at `/config/.entity_replacer_backups/<backup-id>/`. The page displays the exact path after applying. Each backup contains a `manifest.json`, original payloads named `0000.before`, and replacement payloads named `0000.after`. The manifest maps these payloads to configuration paths or dashboard URL paths. YAML payloads are exact original bytes; dashboard payloads are the dashboard configuration, not a raw `.storage` wrapper.

Use **Restore a replacement**, select a backup, check the restore checkbox, and click **Restore selected backup**. Restore prechecks the entire job and refuses to overwrite any file/dashboard with a newer edit. Already-restored items are skipped. After restoring YAML, check configuration and restart/reload as appropriate.

Each YAML file is replaced atomically and its permission bits and ownership are preserved. A whole job spans multiple files and dashboard API calls, so it is not a single filesystem transaction. If an ordinary error occurs, the tool attempts to roll back the writes already attempted. A newer external edit is preserved and reported instead of being overwritten. Durable before/after snapshots also allow recovery after an interrupted operation; no automatic restore runs at startup.

Avoid simultaneous edits to the selected configuration while applying or restoring. Home Assistant's native editors do not share this integration's operation lock; hash checks catch changed snapshots but cannot make all external editors participate in one transaction.

If Home Assistant cannot start, open the backup's `manifest.json` through your usual file access, identify the affected YAML item's `relative` and `before_file`, and copy that original payload back to its YAML path while Home Assistant is stopped. Dashboard snapshots need to be restored through the integration/dashboard UI once Home Assistant is running; they cannot be copied directly over `.storage/lovelace...` because their formats differ. Backups are retained until you remove them; the sidebar lists the 30 most recent valid jobs.

Removing the integration from Settings removes its sidebar panel but leaves replacement backups intact. To uninstall its code, remove `/config/custom_components/entity_replacer` after removing its Settings entry and restart Home Assistant.

## Validation

Automated Python tests cover scanning, exact-token boundaries, templates, byte preservation, aliases/encoded values, binary exclusions, multi-entity mappings, ambiguous role suggestions, device capability/schema validation, whole-block selection, registry changes, unavailable targets, native dashboard saves, stale edits, backups, multi-entity rollback, and restore conflicts. Browser-independent frontend checks cover escaped rendering, domain filtering, mapping confirmation, device requests, selector grouping, and review/apply state. Python compilation and JavaScript syntax are checked.

Tests use real temporary files and mocked Home Assistant states, registries, device automation APIs and dashboards. They do not establish compatibility with every live Home Assistant version. An optional Playwright desktop/mobile workflow test is included as `tests/frontend_smoke.cjs`; its browser run was unavailable in the build environment. GitHub Actions runs HACS validation and hassfest after upload; those checks must pass before the tagged release is published. Live installation and hardware behavior have not been verified by the packaging checks.

For local validation:

```bash
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
node tests/frontend_unit.cjs
node --check custom_components/entity_replacer/frontend/panel.js
python scripts/build_release.py --check-only
```

## Credits

Project concept, requirements, design choices, field testing and maintenance: **FortranFour**. Implementation, review, automated tests, packaging, documentation and icon development received substantial assistance from **OpenAI ChatGPT/Codex**. Thanks to the Home Assistant and HACS maintainers and community for the APIs and ecosystem this project uses. This is a community custom integration, independently maintained.

## Publishing and maintenance

See [Windows Git Bash publishing instructions](docs/PUBLISHING.md) for the initial upload to `FortranFour/device-replacer`, the automated release workflow, and future version tags. See [CHANGELOG.md](CHANGELOG.md) for release changes and [the brand guide](docs/BRAND.md) for the shared cyan outline style.

Report issues at [FortranFour/device-replacer/issues](https://github.com/FortranFour/device-replacer/issues). Licensed under [MIT](LICENSE).

## API references

- [Home Assistant custom panels](https://developers.home-assistant.io/docs/frontend/custom-ui/creating-custom-panels/)
- [Extending the WebSocket API](https://developers.home-assistant.io/docs/frontend/extending/websocket-api/)
- [Asynchronous static paths](https://developers.home-assistant.io/blog/2024/06/18/async_register_static_paths/)
- [Lovelace integration source](https://github.com/home-assistant/core/blob/dev/homeassistant/components/lovelace/__init__.py)
- [Device automation API source](https://github.com/home-assistant/core/blob/dev/homeassistant/components/device_automation/__init__.py)
- [Device action validation](https://developers.home-assistant.io/docs/device_automation_action/)
- [Device trigger validation](https://developers.home-assistant.io/docs/device_automation_trigger/)
- [Native storage persistence source](https://github.com/home-assistant/core/blob/dev/homeassistant/helpers/storage.py)

The integration reads version-dependent Lovelace objects to enumerate dashboards and identify their storage paths. If those interfaces are unavailable, unsupported dashboard references remain manual-review items rather than being written by an unsafe fallback.
