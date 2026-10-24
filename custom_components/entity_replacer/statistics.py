"""Conservative recorder statistics transfer, separate from reference replacement.

Recorder imports are lazy: ordinary reference replacement works without recorder.
Only its metadata API writes to the database; no SQL or measurement rows are edited.
"""

from __future__ import annotations

import asyncio
from functools import partial
import inspect
import time
import uuid

from homeassistant.helpers import entity_registry as er

from .const import PREVIEW_TTL
from .engine import ReplacementError, digest, json_bytes, validate_pair

TRANSFER_TIMEOUT = 30


class RecorderStatistics:
    """Small adapter for HA's recorder APIs (2026.3+), never a raw DB writer."""

    def __init__(self, hass):
        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder import statistics

            self.instance = get_instance(hass)
            self.api = statistics
            required = inspect.signature(statistics.async_update_statistics_metadata).parameters
            if not {"new_statistic_id", "on_done"} <= required.keys():
                raise ValueError("Unsupported recorder metadata API")
            if not self.instance.is_alive():
                raise ValueError("Recorder is not running")
        except (ImportError, KeyError, ValueError, AttributeError) as err:
            raise ReplacementError("Statistics transfer needs a running, compatible recorder (Home Assistant 2026.3+).") from err
        self.hass = hass

    async def metadata(self, source, target):
        return await self.instance.async_add_executor_job(
            partial(self.api.get_metadata, self.hass, statistic_ids={source, target})
        )

    async def latest(self, source):
        async def read(function):
            rows = await self.instance.async_add_executor_job(
                function, self.hass, 1, source, False, {"mean", "min", "max"}
            )
            return rows.get(source, [])

        hourly, short_term = await asyncio.gather(
            read(self.api.get_last_statistics), read(self.api.get_last_short_term_statistics)
        )
        return {"hourly": hourly, "five_minute": short_term}

    async def rename(self, source, target):
        done = asyncio.Event()
        loop = asyncio.get_running_loop()
        self.api.async_update_statistics_metadata(
            self.hass, source, new_statistic_id=target,
            on_done=lambda: loop.call_soon_threadsafe(done.set),
        )
        # Timing out does not cancel the queued recorder job.
        await asyncio.wait_for(done.wait(), TRANSFER_TIMEOUT)


def _identity(hass, entity_id):
    entry = er.async_get(hass).async_get(entity_id)
    if entry is None:
        raise ReplacementError(f"Keep {entity_id} registered and disabled so its measurement role can be verified.")
    if entry.disabled_by is None or hass.states.get(entity_id) is not None:
        raise ReplacementError(f"Disable {entity_id} in Settings → Devices & services → Entities and wait until it is unloaded. Unavailable alone is insufficient.")
    capabilities = getattr(entry, "capabilities", None) or {}
    device_class = getattr(entry, "device_class", None) or getattr(entry, "original_device_class", None)
    if capabilities.get("state_class") != "measurement" or not device_class:
        raise ReplacementError(f"{entity_id} needs a known device class and state_class measurement. Cumulative counters and unknown roles are not supported.")
    return {
        "registry_id": entry.id, "unique_id": entry.unique_id,
        "platform": entry.platform, "device_id": entry.device_id,
        "device_class": device_class, "state_class": capabilities["state_class"],
        "unit": getattr(entry, "unit_of_measurement", None),
        "disabled_by": str(entry.disabled_by),
    }


class StatisticsWorkflow:
    """Session-bound review and single-pair execution under the manager's lock."""

    def _statistics_adapter(self):
        return RecorderStatistics(self.hass)

    async def statistics_verify(self, job_id):
        """Reconcile only a provably completed rename; never retry recorder work."""
        async with self.lock:
            manifest = await self._io(self.backups.load, job_id)
            if manifest.get("mode") != "statistics":
                raise ReplacementError("This audit record is not a statistics transfer.")
            snapshot = manifest["statistics"]
            source, target = manifest["source"], manifest["target"]
            after = await self._statistics_adapter().metadata(source, target)
            expected = dict(snapshot["metadata"], statistic_id=target)
            verified = source not in after and after.get(target) == (snapshot["metadata_id"], expected)
            if verified:
                manifest["status"] = "statistics_transferred"
                await self._io(self.backups.save, manifest)
            return {"job_id": job_id, "verified": verified,
                    "message": "Statistics rename verified; the audit is reconciled. Keep the old sensor disabled." if verified else
                    "Rename cannot be verified. No database changes or retries were made; the audit remains unresolved. Inspect the database and recorder log before recovery."}

    async def _statistics_check(self, source, target):
        validate_pair(source, target)
        if not source.startswith("sensor."):
            raise ReplacementError("Only sensor statistics can be transferred.")
        conflicts = await self._io(self.backups.statistics_conflicts, source, target)
        if conflicts:
            raise ReplacementError("An earlier statistics transfer has an unresolved outcome. Inspect the recorder/database and audit record before further transfers: " + ", ".join(conflicts))
        old, new = _identity(self.hass, source), _identity(self.hass, target)
        if old["device_class"] != new["device_class"] or old["unit"] != new["unit"] or not old["unit"]:
            raise ReplacementError("Sensors must have the same known device class and unit; unit conversion is not supported.")
        adapter = self._statistics_adapter()
        metadata = await adapter.metadata(source, target)
        if target in metadata:
            raise ReplacementError("The replacement already has statistics metadata. Merging or deleting its history is not supported.")
        if source not in metadata:
            raise ReplacementError("No stored statistics metadata exists for the old sensor.")
        metadata_id, meta = metadata[source]
        # ARITHMETIC is 1 in the supported HA releases. Reject circular means.
        if (meta.get("source") != "recorder" or meta.get("has_sum")
                or meta.get("mean_type") != 1 or meta.get("unit_of_measurement") != old["unit"]):
            raise ReplacementError("Only recorder-owned arithmetic measurement statistics with identical stored units are supported.")
        snapshot = {"source_identity": old, "target_identity": new,
                    "metadata_id": metadata_id, "metadata": dict(meta)}
        return adapter, snapshot

    async def statistics_preview(self, owner, source, target):
        async with self.lock:
            try:
                adapter, snapshot = await self._statistics_check(source, target)
            except ReplacementError as err:
                return {"source": source, "target": target, "eligible": False, "reason": str(err)}
            latest = await adapter.latest(source)
            if not any(latest.values()):
                return {"source": source, "target": target, "eligible": False, "reason": "The source has metadata but no retained hourly or five-minute statistics."}
            now = time.monotonic()
            self.statistics_previews = {key: item for key, item in self.statistics_previews.items()
                                        if now - item["created"] < PREVIEW_TTL}
            while len(self.statistics_previews) >= 3:
                self.statistics_previews.pop(next(iter(self.statistics_previews)))
            token = uuid.uuid4().hex
            self.statistics_previews[token] = {
                "owner": owner, "source": source, "target": target, "created": now,
                "snapshot": snapshot, "fingerprint": digest(json_bytes(snapshot)),
            }
            return {"preview_id": token, "source": source, "target": target,
                    "eligible": True, "unit": snapshot["metadata"]["unit_of_measurement"],
                    "device_class": snapshot["source_identity"]["device_class"],
                    "latest": latest, "expires_in_seconds": PREVIEW_TTL}

    async def statistics_apply(self, owner, preview_id, confirmed=False):
        async with self.lock:
            preview = self.statistics_previews.get(preview_id)
            if (not preview or preview["owner"] != owner
                    or time.monotonic() - preview["created"] >= PREVIEW_TTL):
                raise ReplacementError("Statistics preview expired or belongs to another session. Preview again.")
            if confirmed is not True:
                raise ReplacementError("Confirm the reviewed pair, measurement role and database-inclusive backup before transfer.")
            source, target = preview["source"], preview["target"]
            adapter, snapshot = await self._statistics_check(source, target)
            if digest(json_bytes(snapshot)) != preview["fingerprint"]:
                raise ReplacementError("Sensor identity or statistics metadata changed. Preview again.")
            # Persist intent before queuing recorder work. Never mix this audit
            # record into the file restore transaction or promise a DB rollback.
            manifest = await self._io(self.backups.create, source, target, [])
            manifest.update(mode="statistics", status="statistics_applying", statistics=snapshot)
            await self._io(self.backups.save, manifest)
            self.statistics_previews.pop(preview_id, None)
            try:
                # No await between this final runtime check and entering rename.
                if (_identity(self.hass, source) != snapshot["source_identity"]
                        or _identity(self.hass, target) != snapshot["target_identity"]):
                    raise ReplacementError("Sensor identities changed before queuing the transfer.")
            except Exception:
                manifest["status"] = "statistics_not_queued"
                await self._io(self.backups.save, manifest)
                raise
            try:
                await adapter.rename(source, target)
                after = await adapter.metadata(source, target)
                expected = dict(snapshot["metadata"], statistic_id=target)
                if source in after or after.get(target) != (snapshot["metadata_id"], expected):
                    raise ReplacementError("Recorder did not verify the requested rename; no success is assumed.")
                manifest["status"] = "statistics_transferred"
                await self._io(self.backups.save, manifest)
            except (Exception, asyncio.CancelledError) as err:
                manifest["status"] = "statistics_outcome_unknown"
                manifest["errors"] = [str(err) or type(err).__name__]
                await self._io(self.backups.save, manifest)
                if isinstance(err, asyncio.CancelledError):
                    raise
                raise ReplacementError(f"Statistics outcome is unverified. Recorder work may still complete; do not retry blindly. Audit {manifest['job_id']}. Configuration was not changed.") from err
            return {"job_id": manifest["job_id"], "source": source, "target": target,
                    "status": manifest["status"],
                    "message": "Statistics metadata rename verified. Enable only the replacement sensor. Raw state history and configuration were unchanged. Configuration restore cannot undo this transfer."}
