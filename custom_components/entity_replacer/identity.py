"""Reviewed reuse of retiring entity IDs using native HA registry operations."""
from __future__ import annotations

import asyncio
import time
import uuid

from homeassistant.helpers import entity_registry as er

from .const import PREVIEW_TTL
from .device import MAX_DEVICE_ENTITIES
from .engine import ReplacementError, digest, json_bytes, validate_pair


class HistoryIdentity:
    """Read recorder identities and wait for native rename listeners; never write SQL."""
    def __init__(self, hass):
        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.tasks import SynchronizeTask
            self.instance = get_instance(hass)
            self.barrier = SynchronizeTask
            if not self.instance.is_alive():
                raise ValueError("Recorder stopped")
        except (ImportError, KeyError, ValueError) as err:
            raise ReplacementError("Entity ID reuse requires a running compatible recorder.") from err
        self.hass = hass

    async def flush(self):
        # Always enqueue a barrier, even when a job has left the queue but has
        # not finished its DB transaction yet. Also drain registry bus listeners.
        # Recorder's registry listeners are synchronous callbacks. Yield to
        # scheduled bus callbacks, without waiting for unrelated HA tasks.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        done = asyncio.get_running_loop().create_future()
        self.instance.queue_task(self.barrier(done))
        await asyncio.wait_for(done, 30)

    async def snapshot(self, ids):
        def read():
            from sqlalchemy import select
            from homeassistant.components.recorder.db_schema import StatesMeta
            from homeassistant.components.recorder.statistics import get_metadata_with_session
            from homeassistant.components.recorder.util import session_scope
            with session_scope(hass=self.hass, read_only=True) as session:
                states = dict(list(session.execute(select(StatesMeta.entity_id, StatesMeta.metadata_id)
                                             .where(StatesMeta.entity_id.in_(ids))).tuples()))
                stats = get_metadata_with_session(self.instance, session, statistic_ids=set(ids))
                return {"states": states, "statistics": stats}
        return await self.instance.async_add_executor_job(read)


def entry_snapshot(entry):
    if entry is None:
        return None
    return {"id": entry.id, "unique_id": entry.unique_id, "platform": entry.platform,
            "device_id": entry.device_id, "disabled_by": str(entry.disabled_by),
            "name": entry.name or entry.original_name,
            "device_class": getattr(entry, "device_class", None) or getattr(entry, "original_device_class", None),
            "unit": getattr(entry, "unit_of_measurement", None),
            "capabilities": dict(getattr(entry, "capabilities", None) or {}),
            "supported_features": getattr(entry, "supported_features", 0) or 0}


class IdentityWorkflow:
    def _identity_adapter(self):
        return HistoryIdentity(self.hass)

    async def _reuse_check(self, mappings):
        if not isinstance(mappings, list) or not 1 <= len(mappings) <= MAX_DEVICE_ENTITIES:
            raise ReplacementError("Choose at least one mapped entity, within the device entity limit.")
        registry = er.async_get(self.hass)
        rows, old_ids, new_ids = [], set(), set()
        for mapping in mappings:
            if not isinstance(mapping, dict):
                raise ReplacementError("Each mapping must name an old and replacement entity ID.")
            source, target = mapping.get("source", ""), mapping.get("target", "")
            validate_pair(source, target)
            if source in old_ids or target in new_ids:
                raise ReplacementError("Entity ID reuse needs unique one-to-one mappings.")
            old_ids.add(source)
            new_ids.add(target)
            old, new = registry.async_get(source), registry.async_get(target)
            if new is None:
                raise ReplacementError(f"Replacement {target} must be registered; pair the new device first.")
            before, after = entry_snapshot(old), entry_snapshot(new)
            if old and old.device_id and old.device_id == new.device_id:
                raise ReplacementError("Choose entities on different devices; this is not an entity-ID swap within one device.")
            if before:
                if any(before[key] != after[key] for key in ("unit", "device_class")):
                    raise ReplacementError(f"{source}: replacement units and device class must match.")
                if before["supported_features"] & ~after["supported_features"]:
                    raise ReplacementError(f"{target} lacks features of the retiring entity.")
            if source.startswith("sensor."):
                if (after["capabilities"].get("state_class") != "measurement"
                        or not after["device_class"] or not after["unit"]
                        or before and before["capabilities"].get("state_class") != "measurement"):
                    raise ReplacementError("Sensor ID reuse currently supports known measurement sensors only. Cumulative counters, including Energy Dashboard totals, are blocked.")
            rows.append({"source": source, "target": target, "old": before, "new": after})
        if old_ids & new_ids:
            raise ReplacementError("Mappings cannot form chains, cycles, or ID swaps.")
        for entity_id in old_ids | new_ids:
            conflicts = await self._io(self.backups.statistics_conflicts, entity_id, entity_id)
            if conflicts:
                raise ReplacementError("An unresolved history operation blocks reuse: " + ", ".join(conflicts))
        adapter = self._identity_adapter()
        await adapter.flush()
        history = await adapter.snapshot(old_ids | new_ids)
        for row in rows:
            source, target = row["source"], row["target"]
            stat = history["statistics"].get(source)
            if row["old"] is None and source not in history["states"] and stat is None:
                raise ReplacementError(f"{source} is neither registered nor found in retained history. Check the old entity ID.")
            for entity_id in (source, target):
                saved = history["statistics"].get(entity_id)
                if saved and (saved[1].get("source") != "recorder" or saved[1].get("has_sum")
                              or saved[1].get("mean_type") != 1
                              or saved[1].get("unit_of_measurement") != row["new"]["unit"]):
                    raise ReplacementError(f"{entity_id}: incompatible or cumulative statistics; no history will be changed.")
            # Units/classes of a removed source can only be checked against its
            # retained statistics; require that evidence for measurement sensors.
            if row["old"] is None and source.startswith("sensor.") and stat is None:
                raise ReplacementError("A removed sensor needs compatible retained statistics to verify its units.")
        snapshot = {"mappings": rows, "history": history}
        return adapter, snapshot

    def _reuse_ready(self, snapshot):
        registry = er.async_get(self.hass)
        status = []
        for row in snapshot["mappings"]:
            for entity_id in (row["source"], row["target"]):
                entry = registry.async_get(entity_id)
                status.append({"entity_id": entity_id, "disabled": entry is None or entry.disabled_by is not None,
                               "loaded": self.hass.states.get(entity_id) is not None})
        return status

    def _reuse_review(self, owner, token):
        preview = self.identity_previews.get(token)
        if not preview or preview["owner"] != owner or time.monotonic() - preview["created"] >= PREVIEW_TTL:
            raise ReplacementError("Entity ID preview expired or belongs to another session. Review again.")
        return preview

    async def reuse_preview(self, owner, mappings):
        async with self.lock:
            adapter, snapshot = await self._reuse_check(mappings)
            tokens = {}
            for row in snapshot["mappings"]:
                tokens[row["target"]] = row["source"]
                if row["old"]:
                    tokens[row["old"]["id"]] = row["new"]["id"]
                    if row["old"]["device_id"] and row["new"]["device_id"]:
                        tokens[row["old"]["device_id"]] = row["new"]["device_id"]
            documents, _, notes, _, _ = await self._scan_documents(tokens, None, True, True)
            manual = [{"file": document.relative or document.label, "line": item.line,
                       "snippet": item.snippet, "source": item.source, "target": item.target}
                      for document in documents for item in document.occurrences]
            now = time.monotonic()
            self.identity_previews = {key: value for key, value in self.identity_previews.items()
                                      if now - value["created"] < PREVIEW_TTL}
            while len(self.identity_previews) >= 3:
                self.identity_previews.pop(next(iter(self.identity_previews)))
            token = uuid.uuid4().hex
            self.identity_previews[token] = {"owner": owner, "created": now, "snapshot": snapshot,
                                            "fingerprint": digest(json_bytes(snapshot))}
            status = self._reuse_ready(snapshot)
            ready = all(item["disabled"] and not item["loaded"] for item in status)
            return {"preview_id": token, "mappings": [{"source": r["source"], "target": r["target"],
                    "retire_entry": r["old"] is not None} for r in snapshot["mappings"]], "entities": status,
                    "ready": ready, "can_disable": any(not item["disabled"] for item in status),
                    "manual_references": manual[:200], "manual_reference_count": len(manual), "warnings": notes}

    async def _reuse_revalidate(self, owner, token):
        preview = self._reuse_review(owner, token)
        mappings = [{"source": row["source"], "target": row["target"]} for row in preview["snapshot"]["mappings"]]
        adapter, snapshot = await self._reuse_check(mappings)
        if digest(json_bytes(snapshot)) != preview["fingerprint"]:
            raise ReplacementError("Registry or retained history changed since review. Review again.")
        return adapter, snapshot, mappings

    async def reuse_disable(self, owner, preview_id, confirmed=False):
        async with self.lock:
            if confirmed is not True:
                raise ReplacementError("Confirm disabling the reviewed entities first.")
            _, snapshot, mappings = await self._reuse_revalidate(owner, preview_id)
            self._reuse_check_entries(snapshot)
            self.identity_previews.pop(preview_id, None)
            registry = er.async_get(self.hass)
            try:
                for row in snapshot["mappings"]:
                    for entity_id in (row["source"], row["target"]):
                        entry = registry.async_get(entity_id)
                        if entry and entry.disabled_by is None:
                            registry.async_update_entity(entity_id, disabled_by=er.RegistryEntryDisabler.USER)
            except Exception as err:
                raise ReplacementError("Entity disabling was incomplete. Review actual entity status again; no IDs or history were changed.") from err
        return await self.reuse_preview(owner, mappings)

    def _reuse_check_entries(self, snapshot):
        registry = er.async_get(self.hass)
        for row in snapshot["mappings"]:
            for key, entity_id in (("old", row["source"]), ("new", row["target"])):
                if entry_snapshot(registry.async_get(entity_id)) != row[key]:
                    raise ReplacementError("Entity identity changed during review. Nothing further was changed.")

    async def reuse_apply(self, owner, preview_id, confirmed=False):
        async with self.lock:
            if confirmed is not True:
                raise ReplacementError("Confirm retirement of the old hardware, registry entry removal, and a full database-inclusive backup.")
            adapter, snapshot, mappings = await self._reuse_revalidate(owner, preview_id)
            if not all(item["disabled"] and not item["loaded"] for item in self._reuse_ready(snapshot)):
                raise ReplacementError("Disable all mapped entities and wait for actual unloading, then review again.")
            manifest = await self._io(self.backups.create, mappings[0]["source"], mappings[0]["target"], [])
            manifest.update(mode="id_reuse", status="id_reuse_applying", mappings=mappings, identity_snapshot=snapshot)
            await self._io(self.backups.save, manifest)
            self.identity_previews.pop(preview_id, None)
            touched = False
            try:
                self._reuse_check_entries(snapshot)
                if not all(item["disabled"] and not item["loaded"] for item in self._reuse_ready(snapshot)):
                    raise ReplacementError("Entities became loaded before ID reuse. Review again.")
                registry = er.async_get(self.hass)
                # Do NOT rename the old entries: recorder would move old history.
                # Retiring their disabled registry entries preserves their IDs in
                # recorder, then native rename listeners handle the replacements.
                for row in snapshot["mappings"]:
                    touched = True
                    if row["old"] is not None:
                        registry.async_remove(row["source"])
                    registry.async_update_entity(row["target"], new_entity_id=row["source"],
                                                 name=row["old"]["name"] if row["old"] else row["new"]["name"])
                await adapter.flush()
                after = await adapter.snapshot({i for r in mappings for i in (r["source"], r["target"])})
                for row in snapshot["mappings"]:
                    entry = registry.async_get(row["source"])
                    if entry is None or entry.id != row["new"]["id"] or registry.async_get(row["target"]) is not None:
                        raise ReplacementError("Replacement registry identity could not be verified.")
                    for kind in ("states", "statistics"):
                        old = snapshot["history"][kind].get(row["source"])
                        new = snapshot["history"][kind].get(row["target"])
                        kept = after[kind].get(row["source"])
                        if old is not None and kept != old:
                            raise ReplacementError("Retiring history identity changed unexpectedly.")
                        if old is None and new is not None:
                            expected = new if kind == "states" else (new[0], dict(new[1], statistic_id=row["source"]))
                            if kept != expected:
                                raise ReplacementError("Native history rename could not be verified.")
                manifest["status"] = "id_reuse_completed"
                await self._io(self.backups.save, manifest)
            except (Exception, asyncio.CancelledError) as err:
                manifest["status"] = "id_reuse_outcome_unknown" if touched else "id_reuse_not_started"
                manifest["errors"] = [str(err) or type(err).__name__]
                await self._io(self.backups.save, manifest)
                if isinstance(err, asyncio.CancelledError):
                    raise
                raise ReplacementError(f"Entity ID reuse needs manual recovery; do not retry blindly. Audit {manifest['job_id']}. Restore your full backup if needed; configuration Restore cannot undo this operation.") from err
            return {"job_id": manifest["job_id"], "message": "Replacement entities now use the retiring entity IDs; retained old history identities were verified. Enable only the replacement entities in Settings → Devices & services → Entities. Retiring registry entries were removed. Review device-based automation bindings manually; their device IDs and registry UUIDs were not transferred. Existing replacement history is not merged."}
