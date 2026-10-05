"""Home Assistant adapters and reviewed replacement transactions."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
import time
from typing import Any
import uuid

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

try:
    from homeassistant.components.lovelace.const import LOVELACE_DATA
except ImportError:
    LOVELACE_DATA = "lovelace"

from .const import PREVIEW_TTL, VERSION
from .device import DeviceBlock
from .device_manager import DeviceWorkflow
from .engine import (
    BackupStore, Document, MAX_FILE_BYTES, ReplacementError, build_candidate,
    digest, json_bytes, read_file, render_report, replace_file, scan_dashboard,
    safe_path, scan_files, unified_diff_text, validate_pair,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class Preview:
    owner: str
    source: str
    target: str
    created: float
    documents: list[Document]
    dashboard_urls: dict[str, str | None]
    allow_unavailable_target: bool
    reviewed_ids: set[str] | None = None
    mode: str = "entity"
    mappings: list[dict] = field(default_factory=list)
    source_device: str | None = None
    target_device: str | None = None
    registry_snapshot: str | None = None
    device_blocks: list[DeviceBlock] = field(default_factory=list)


class EntityReplacer(DeviceWorkflow):
    """All mutations are serialized; external editors are guarded by hashes."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.root = Path(hass.config.config_dir)
        self.backups = BackupStore(self.root)
        self.previews: dict[str, Preview] = {}
        self.lock = asyncio.Lock()

    async def _io(self, function, *args):
        return await self.hass.async_add_executor_job(function, *args)

    def _dashboards(self) -> dict:
        data = self.hass.data.get(LOVELACE_DATA)
        if data is None:
            raise ReplacementError("Lovelace is not loaded. Reload Device Replacer after the dashboard integration is ready.")
        dashboards = getattr(data, "dashboards", None)
        if dashboards is None and isinstance(data, dict):
            dashboards = data.get("dashboards")
        if not isinstance(dashboards, dict):
            raise ReplacementError("This Home Assistant release has an unsupported Lovelace API. YAML scanning still works; UI dashboards will be reported as manual references.")
        return dashboards

    def _dashboard(self, url_path: str | None):
        dashboard = self._dashboards().get(url_path)
        if dashboard is None or getattr(dashboard, "mode", None) != "storage":
            raise ReplacementError(f"UI dashboard is missing or is now YAML: {url_path or 'default'}")
        return dashboard

    def _dashboard_path(self, dashboard) -> str | None:
        # Read-only use of Store.path for real on-disk line numbers. Saving is
        # always through LovelaceConfig.async_save, never raw .storage writes.
        store = getattr(dashboard, "_store", None)
        filename = getattr(store, "path", None)
        if filename is None:
            return None
        try:
            return str(Path(filename).relative_to(self.root))
        except ValueError:
            return None

    def _validate_target(self, source: str, target: str, allow_unavailable: bool) -> list[str]:
        validate_pair(source, target)
        registry = er.async_get(self.hass)
        entry = registry.async_get(target)
        state = self.hass.states.get(target)
        if entry is None and state is None:
            raise ReplacementError("The replacement entity is not known to Home Assistant.")
        warnings: list[str] = []
        if (entry is not None and entry.disabled_by is not None) or state is None or state.state in {"unknown", "unavailable"}:
            if not allow_unavailable:
                raise ReplacementError("The replacement is unavailable, unknown, or disabled. Choose a working entity, or explicitly enable unavailable replacements.")
            warnings.append("The replacement is unavailable, unknown, or disabled. References can be changed, but they will not function until the entity is enabled and working.")
        old_state = self.hass.states.get(source)
        if old_state and state:
            for attribute in ("unit_of_measurement", "device_class"):
                old_value = old_state.attributes.get(attribute)
                new_value = state.attributes.get(attribute)
                if old_value != new_value:
                    warnings.append(f"Different {attribute}: {old_value!r} → {new_value!r}. Review numeric thresholds, units, and card behavior.")
            old_features = old_state.attributes.get("supported_features", 0)
            new_features = state.attributes.get("supported_features", 0)
            if isinstance(old_features, int) and isinstance(new_features, int) and old_features & ~new_features:
                warnings.append("The replacement supports fewer actions/features. Review service data and controls before applying.")
        return warnings

    async def status(self) -> dict:
        registry = er.async_get(self.hass)
        entities: dict[str, dict] = {}
        for entry in registry.entities.values():
            entities[entry.entity_id] = {
                "entity_id": entry.entity_id,
                "name": entry.name or entry.original_name or entry.entity_id,
                "state": "disabled" if entry.disabled_by is not None else "not loaded",
            }
        for state in self.hass.states.async_all():
            entities[state.entity_id] = {
                "entity_id": state.entity_id,
                "name": state.attributes.get("friendly_name", state.entity_id),
                "state": state.state,
            }
        return {"version": VERSION, "entities": sorted(entities.values(), key=lambda item: item["entity_id"]), "devices": self._device_list(), "history": await self._io(self.backups.history)}

    async def _scan_documents(self, source, device_id, include_text, include_storage):
        warnings: list[str] = []
        dashboard_documents: list[Document] = []
        dashboard_urls: dict[str, str | None] = {}
        excluded_stores: set[str] = set()
        dashboard_count = 0
        try:
            dashboards = dict(self._dashboards())
        except ReplacementError as err:
            dashboards = {}
            warnings.append(str(err))
        for url_path, dashboard in dashboards.items():
            if getattr(dashboard, "mode", None) != "storage":
                continue
            relative = self._dashboard_path(dashboard)
            try:
                config = await dashboard.async_load(False)
                canonical = await self._io(json_bytes, config)
                if len(canonical) > MAX_FILE_BYTES:
                    raise ReplacementError("Dashboard exceeds the 8 MiB scan limit.")
                # Copy live state. A subsequent native dashboard edit must
                # not silently mutate the snapshot kept for this preview.
                config = await self._io(json.loads, canonical)
                disk_raw = None
                if relative:
                    try:
                        disk_raw = await self._io(read_file, self.root, relative)
                    except (OSError, ReplacementError):
                        pass
                label = "UI dashboard: " + (url_path or "default")
                document = await self._io(scan_dashboard, url_path or "", label, config, source, relative, disk_raw, device_id)
                if relative is None:
                    for occurrence in document.occurrences:
                        occurrence.writable = False
                        occurrence.reason = "This Home Assistant release does not expose a verifiable dashboard storage path. Edit this dashboard through its Raw configuration editor."
                dashboard_count += 1
                if relative and relative.startswith(".storage/"):
                    excluded_stores.add(Path(relative).name)
                if document.occurrences:
                    dashboard_documents.append(document)
                    dashboard_urls[document.key] = url_path
            except Exception as err:
                # Generated dashboards have no saved config. They contain
                # no literal references and need no replacement.
                if type(err).__name__ == "ConfigNotFound":
                    continue
                _LOGGER.debug("Dashboard scan failed: %s", url_path, exc_info=True)
                warnings.append(f"Could not inspect UI dashboard {url_path or 'default'} through its API ({type(err).__name__}). Its storage file, if present, is manual-review only.")
        documents, file_warnings, file_count = await self._io(scan_files, self.root, source, device_id, include_text, include_storage, excluded_stores)
        documents += dashboard_documents
        warnings += file_warnings
        return documents, dashboard_urls, warnings, file_count, dashboard_count

    def _remember_preview(self, owner, source, target, documents, urls, allow_unavailable, **extra):
        now = time.monotonic()
        self.previews = {key: preview for key, preview in self.previews.items() if now - preview.created < PREVIEW_TTL}
        while len(self.previews) >= 3:
            self.previews.pop(next(iter(self.previews)))
        preview_id = uuid.uuid4().hex
        preview = Preview(owner, source, target, now, documents, urls, allow_unavailable, **extra)
        self.previews[preview_id] = preview
        return preview_id, preview

    async def _public_preview(self, preview_id, preview, warnings, file_count, dashboard_count):
        public_documents = await self._io(lambda: [document.public(self.root, preview.source, preview.target) for document in preview.documents])
        result = {
            "preview_id": preview_id, "source": preview.source, "target": preview.target,
            "mode": preview.mode, "mappings": preview.mappings,
            "source_device": preview.source_device, "target_device": preview.target_device,
            "expires_in_seconds": PREVIEW_TTL, "files_scanned": file_count,
            "dashboards_scanned": dashboard_count, "documents": public_documents,
            "warnings": warnings,
            "coverage": [
                "YAML throughout the configuration directory, including packages, blueprints, templates, scenes, and YAML dashboards.",
                "Saved UI dashboards through Home Assistant's Lovelace API.",
                "Device mode maps entity references together and permits complete device blocks only after replacement capability checks.",
                "Other active storage and additional text/code files are manual-review only when their scan options are enabled. Known binary storage formats are skipped quietly.",
                "Secrets, entity/device identity registries, history caches, vendor code, backups, symlinks, external add-ons, and files outside the configuration directory are excluded.",
                "Computed/concatenated IDs, area/label selections, blueprint device inputs, and unsupported device blocks need manual follow-up.",
                "Device pairing, unique IDs, entity/device registries, areas/labels, and historical statistics are not transferred.",
            ],
        }
        result["report"] = await self._io(render_report, result)
        return result

    async def scan(self, owner: str, source: str, target: str, include_text: bool = True, include_storage: bool = True, allow_unavailable_target: bool = False) -> dict:
        async with self.lock:
            warnings = self._validate_target(source, target, allow_unavailable_target)
            source_entry = er.async_get(self.hass).async_get(source)
            device_id = source_entry.device_id if source_entry else None
            documents, urls, notes, file_count, dashboard_count = await self._scan_documents(source, device_id, include_text, include_storage)
            preview_id, preview = self._remember_preview(owner, source, target, documents, urls, allow_unavailable_target)
            return await self._public_preview(preview_id, preview, warnings + notes, file_count, dashboard_count)

    async def _current(self, record: dict) -> bytes:
        if record["kind"] == "yaml":
            return await self._io(read_file, self.root, record["relative"])
        config = await self._dashboard(record["url_path"]).async_load(False)
        return await self._io(json_bytes, config)

    async def _write(self, record: dict, expected_hash: str, payload: bytes) -> None:
        if record["kind"] == "yaml":
            await self._io(replace_file, self.root, record["relative"], expected_hash, payload)
        else:
            dashboard = self._dashboard(record["url_path"])
            relative = self._dashboard_path(dashboard)
            if relative is None:
                raise ReplacementError("Cannot verify dashboard persistence on this Home Assistant release; edit its Raw configuration manually.")
            await self._io(safe_path, self.root, relative)
            if digest(await self._io(json_bytes, await dashboard.async_load(False))) != expected_hash:
                raise ReplacementError(f"Dashboard changed during replacement: {record['label']}")
            config = await self._io(json.loads, payload)
            await dashboard.async_save(config)
            # HA's Store may log a disk write error and return successfully.
            # Inspect persisted config without directly modifying .storage.
            disk_raw = await self._io(read_file, self.root, relative)
            stored = await self._io(json.loads, disk_raw)
            disk_config = stored.get("data", {}).get("config")
            if digest(await self._io(json_bytes, disk_config)) != digest(payload):
                raise ReplacementError(f"Dashboard was not persisted as reviewed: {record['label']}. Check the Home Assistant log and backup history.")
            live = await dashboard.async_load(False)
            if digest(await self._io(json_bytes, live)) != digest(payload):
                raise ReplacementError(f"Dashboard was edited concurrently: {record['label']}. The newer edit will be preserved.")

    def _preview(self, owner: str, preview_id: str) -> Preview:
        preview = self.previews.get(preview_id)
        if preview is None or preview.owner != owner or time.monotonic() - preview.created >= PREVIEW_TTL:
            raise ReplacementError("Preview expired or belongs to another session. Scan again.")
        return preview

    async def _prepare(self, preview: Preview, selected: set[str]) -> list[dict]:
        if preview.mode == "device":
            await self._check_device_preview(preview, selected)
        else:
            self._validate_target(preview.source, preview.target, preview.allow_unavailable_target)
        known = {item.id: item for document in preview.documents for item in document.occurrences}
        if not selected or selected - known.keys():
            raise ReplacementError("Select at least one valid reference from the current preview.")
        if any(not known[key].writable for key in selected):
            raise ReplacementError("Manual-review references cannot be applied automatically.")
        prepared: list[dict] = []
        for document in preview.documents:
            ids = selected & {item.id for item in document.occurrences}
            if not ids:
                continue
            candidate = await self._io(build_candidate, document, ids, preview.source, preview.target)
            before = document.original if document.kind == "yaml" else await self._io(json_bytes, document.original)
            after = candidate if document.kind == "yaml" else await self._io(json_bytes, candidate)
            record = {
                "kind": document.kind, "relative": document.relative,
                "label": document.label, "references": len(ids),
                "url_path": preview.dashboard_urls.get(document.key),
            }
            if document.kind == "dashboard":
                relative = self._dashboard_path(self._dashboard(record["url_path"]))
                if relative is None:
                    raise ReplacementError("Dashboard storage path cannot be verified; edit it manually.")
                await self._io(safe_path, self.root, relative)
            if digest(await self._current(record)) != digest(before):
                raise ReplacementError(f"Changed since preview: {document.label}. Nothing was written; scan again.")
            prepared.append({**record, "before": before, "after": after})
        return prepared

    async def review(self, owner: str, preview_id: str, selected_ids: list[str]) -> dict:
        async with self.lock:
            preview = self._preview(owner, preview_id)
            preview.reviewed_ids = None
            selected = set(selected_ids)
            prepared = await self._prepare(preview, selected)

            def differences():
                diffs = []
                for item in prepared:
                    before = item["before"].decode("utf-8") if item["kind"] == "yaml" else json.dumps(json.loads(item["before"]), indent=2, ensure_ascii=False)
                    after = item["after"].decode("utf-8") if item["kind"] == "yaml" else json.dumps(json.loads(item["after"]), indent=2, ensure_ascii=False)
                    diff = unified_diff_text(before, after, item["label"])
                    diffs.append({"label": item["label"], "references": item["references"], "diff": diff})
                return diffs

            diffs = await self._io(differences)
            preview.reviewed_ids = selected
            return {"references": len(selected), "documents": len(prepared), "diffs": diffs}

    async def apply(self, owner: str, preview_id: str, selected_ids: list[str]) -> dict:
        async with self.lock:
            preview = self._preview(owner, preview_id)
            selected = set(selected_ids)
            if preview.reviewed_ids != selected:
                raise ReplacementError("Review the current selection before applying it.")
            prepared = await self._prepare(preview, selected)
            # All candidates and hashes are checked before any mutation; write
            # durable before/after snapshots before touching live configuration.
            manifest = await self._io(self.backups.create, preview.source, preview.target, prepared)
            if preview.mode == "device":
                manifest.update(mode="device", source_device=preview.source_device, target_device=preview.target_device, mappings=preview.mappings)
                await self._io(self.backups.save, manifest)
            touched: list[dict] = []
            try:
                manifest["status"] = "applying"
                await self._io(self.backups.save, manifest)
                for record in manifest["items"]:
                    if digest(await self._current(record)) != record["before_hash"]:
                        raise ReplacementError(f"Changed during replacement: {record['label']}")
                    payload = await self._io(self.backups.payload, manifest, record, "after")
                    # Include the attempted write: Lovelace may update its cache
                    # before disk persistence raises an exception.
                    touched.append(record)
                    await self._write(record, record["before_hash"], payload)
                    record["status"] = "applied"
                    await self._io(self.backups.save, manifest)
                manifest["status"] = "applied"
                await self._io(self.backups.save, manifest)
            except Exception as err:
                rollback_errors = await self._rollback(manifest, touched)
                manifest["status"] = "rollback_incomplete" if rollback_errors else "rolled_back"
                manifest["errors"] = [str(err)] + rollback_errors
                try:
                    await self._io(self.backups.save, manifest)
                except OSError:
                    _LOGGER.exception("Could not persist rollback status for %s", manifest["job_id"])
                self.previews.pop(preview_id, None)
                message = f"Replacement failed ({err}). Backup {manifest['job_id']}. "
                message += "Rollback needs manual attention: " + "; ".join(rollback_errors) if rollback_errors else "All attempted changes were rolled back."
                raise ReplacementError(message) from err
            self.previews.pop(preview_id, None)
            return {
                "job_id": manifest["job_id"], "references_changed": len(selected),
                "documents_changed": len(prepared),
                "backup_path": str(self.root / ".entity_replacer_backups" / manifest["job_id"]),
                "reload_required": any(item["kind"] == "yaml" for item in prepared),
                "message": "Changes saved. Check configuration, then restart Home Assistant for YAML changes. Saved UI dashboards refresh automatically.",
            }

    async def _rollback(self, manifest: dict, records: list[dict]) -> list[str]:
        errors = []
        for record in reversed(records):
            try:
                current_hash = digest(await self._current(record))
                if current_hash == record["before_hash"]:
                    record["status"] = "rolled_back"
                    continue
                if current_hash != record["after_hash"]:
                    raise ReplacementError("A newer edit was detected and has been preserved.")
                before = await self._io(self.backups.payload, manifest, record, "before")
                await self._write(record, record["after_hash"], before)
                record["status"] = "rolled_back"
            except Exception as err:
                errors.append(f"{record['label']}: {err}")
        return errors

    async def restore(self, job_id: str) -> dict:
        async with self.lock:
            manifest = await self._io(self.backups.load, job_id)
            candidates = []
            for record in manifest["items"]:
                before = await self._io(self.backups.payload, manifest, record, "before")
                await self._io(self.backups.payload, manifest, record, "after")
                current_hash = digest(await self._current(record))
                if current_hash == record["before_hash"]:
                    continue
                if current_hash != record["after_hash"]:
                    raise ReplacementError(f"Restore refused: {record['label']} has newer edits. No files were restored. Recover the needed lines manually from backup {job_id}.")
                candidates.append((record, before))
            try:
                manifest["status"] = "restoring"
                await self._io(self.backups.save, manifest)
                for record, before in reversed(candidates):
                    await self._write(record, record["after_hash"], before)
                    record["status"] = "restored"
                    await self._io(self.backups.save, manifest)
                manifest["status"] = "restored"
                manifest["errors"] = []
                await self._io(self.backups.save, manifest)
            except Exception as err:
                manifest["status"] = "restore_incomplete"
                manifest["errors"] = [str(err)]
                try:
                    await self._io(self.backups.save, manifest)
                except OSError:
                    _LOGGER.exception("Could not persist restore status")
                raise ReplacementError(f"Restore stopped at a conflict or I/O error ({err}). Backup {job_id} is retained. Retry to recover remaining unchanged files, or restore individual lines manually.") from err
            self.previews.clear()
            return {
                "job_id": job_id, "documents_restored": len(candidates),
                "reload_required": any(record["kind"] == "yaml" for record, _ in candidates),
                "message": "Backup restored. Check configuration and restart Home Assistant for restored YAML changes.",
            }
