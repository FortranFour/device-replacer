# Device Replacer 1.2.0 (unreleased)

Add a separate statistics transfer workflow for compatible arithmetic measurement sensors. The source and replacement must both be disabled and unloaded, retain verifiable registry metadata, and use matching units/classes. The replacement must have no stored statistics metadata.

Transfer uses recorder's metadata API and verifies the result. Hourly and retained five-minute measurements keep their existing metadata ID; raw state history and configuration are unchanged. Cumulative counters (including Energy sensors), merges, unit conversion and removed/unverifiable sources are excluded in this first version.

A database-inclusive backup is required for recovery. Statistics operations have separate audit records and are never undone by configuration Restore. Uncertain recorder outcomes consume the preview and block further transfers involving the pair until reconciled.

No release has been published. Real recorder tests target Home Assistant 2026.3.0 with SQLite; other database engines and production installations still need validation.
