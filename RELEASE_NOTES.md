# Device Replacer 1.3.0 — prerelease

Add an initial choice: **Update references to the new device and entity IDs**, or **Reuse the retiring entity IDs & history**.

The new path assigns the retiring entities' technical IDs to their replacements. It removes only the mapped disabled old registry entries, never renames them, and uses native Home Assistant registry operations so retained old raw-history and compatible measurement-statistics identities remain at the old IDs. It provides entity disable/readiness controls, a separate review/confirmation and a durable audit. The replacement keeps its own device identity and pairing. Existing replacement history is not merged.

Read the manual-reference report: device-based bindings and references to the replacement's previous IDs still need manual repair. Areas and labels are not transferred. Cumulative counters, including Energy Dashboard kWh totals, remain blocked. No direct SQL writes or copying of historical measurements is performed.

This operation requires permanently retiring/disconnecting the old hardware, disabled/unloaded entities, and a **full Home Assistant backup including the recorder database**. Configuration Restore cannot undo registry/history operations. An interrupted or partially applied operation requires manual recovery; no blind retry is offered.

Testing prerelease only. Validation covers Home Assistant 2026.3.0 with SQLite; other versions/database engines and live installations require additional testing. The latest stable release remains v1.1.3.
