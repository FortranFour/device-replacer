# Device Replacer 1.2.1 — prerelease

Add **Disable both sensor entities** directly to the statistics transfer interface. After checking a pair, the panel shows whether each sensor is enabled, disabled but still loaded, or disabled and unloaded. For an otherwise eligible pair, the new action disables just those two sensor entities through Home Assistant's entity registry API; the whole devices do not need to be disabled, and existing disable reasons are preserved.

The interface checks readiness briefly after disabling and provides **Refresh status**. It does not force-remove states or automatically reload integrations. If an integration still has the sensors loaded, reload it or restart Home Assistant before refreshing. Statistics transfer remains a separate reviewed action and stays blocked until both entities are disabled and unloaded. After transfer, enable only the replacement through its entity settings.

The measurement-only restrictions remain: matching units/classes, recorder-owned arithmetic statistics, and no existing replacement statistics metadata. Cumulative counters, history merges, unit conversion and removed/unverifiable sources are excluded. Disabling cannot make an existing destination history eligible.

This is a prerelease for testing. A database-inclusive backup is required for statistics recovery. Configuration Restore does not undo statistics transfer or entity disabling. Other database engines and Home Assistant versions still require validation.
