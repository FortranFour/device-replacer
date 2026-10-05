"""Device registry inventory and read-only capability validation.

The existing manager owns transactions. This workflow never edits registries,
attaches triggers, or calls actions/services to test replacement hardware.
"""

from __future__ import annotations

import asyncio
import copy
import logging

from homeassistant.helpers import entity_registry as er

from .device import MAX_DEVICE_ENTITIES, authorize_block, collect_blocks, role_name, suggest_mappings
from .engine import ENTITY_ID, REGISTRY_ID, ReplacementError, digest, json_bytes

_LOGGER = logging.getLogger(__name__)


class DeviceWorkflow:
    """Mixin using the manager's preview and transaction helpers."""

    def _device_registry(self):
        from homeassistant.helpers import device_registry as dr

        return dr.async_get(self.hass)

    def _device_list(self) -> list[dict]:
        # Entity-only installs/mocked adapters can still use the original mode.
        try:
            registry = self._device_registry()
        except ImportError:
            return []
        entities = er.async_get(self.hass)
        counts: dict[str, int] = {}
        for entry in entities.entities.values():
            if entry.device_id:
                counts[entry.device_id] = counts.get(entry.device_id, 0) + 1
        return sorted([{
            "id": entry.id,
            "name": entry.name_by_user or entry.name or entry.id,
            "manufacturer": entry.manufacturer or "",
            "model": entry.model or "",
            "disabled": getattr(entry, "disabled_by", None) is not None,
            "entities": counts.get(entry.id, 0),
        } for entry in registry.devices.values()], key=lambda item: (item["name"].casefold(), item["id"]))

    def _entity_info(self, entry) -> dict:
        state = self.hass.states.get(entry.entity_id)
        attributes = state.attributes if state else {}
        info = {
            "entity_id": entry.entity_id, "registry_id": getattr(entry, "id", None),
            "device_id": entry.device_id, "domain": entry.entity_id.split(".", 1)[0],
            "name": entry.name or entry.original_name or attributes.get("friendly_name", entry.entity_id),
            "state": "disabled" if entry.disabled_by is not None else state.state if state else "not loaded",
            "disabled": entry.disabled_by is not None,
            "platform": getattr(entry, "platform", None),
            "translation_key": getattr(entry, "translation_key", None),
            "role_name": role_name(getattr(entry, "original_name_unprefixed", None) or entry.original_name),
        }
        for field in ("device_class", "unit_of_measurement", "supported_features"):
            fallback = getattr(entry, field, None)
            if field == "device_class":
                fallback = fallback or getattr(entry, "original_device_class", None)
            info[field] = attributes.get(field, fallback)
        return info

    def _device_entities(self, device_id: str) -> list[dict]:
        result = [self._entity_info(entry) for entry in er.async_get(self.hass).entities.values() if entry.device_id == device_id]
        if len(result) > MAX_DEVICE_ENTITIES:
            raise ReplacementError(f"Device has more than {MAX_DEVICE_ENTITIES} entities. Use single-entity mode for smaller batches.")
        return sorted(result, key=lambda item: item["entity_id"])

    def _validate_devices(self, source: str, target: str, allow_unavailable=False):
        if not REGISTRY_ID.fullmatch(source) or not REGISTRY_ID.fullmatch(target) or source == target:
            raise ReplacementError("Choose different devices, or enter the removed source's 32-character device ID.")
        registry = self._device_registry()
        replacement = registry.async_get(target)
        if replacement is None:
            raise ReplacementError("The replacement device is not in Home Assistant's device registry. Add/pair it first.")
        if getattr(replacement, "disabled_by", None) is not None and not allow_unavailable:
            raise ReplacementError("The replacement device is disabled. Enable it, or explicitly allow unavailable replacements.")
        return registry.async_get(source), replacement

    def _device_name(self, device, device_id):
        return ((device.name_by_user or device.name) if device else "Removed device") or device_id

    async def device_plan(self, source: str, target: str) -> dict:
        old, new = self._validate_devices(source, target, True)
        sources, targets = self._device_entities(source), self._device_entities(target)
        return {
            "source_device": source, "target_device": target,
            "source_name": self._device_name(old, source), "target_name": self._device_name(new, target),
            "source_entities": sources, "target_entities": targets,
            "suggestions": suggest_mappings(sources, targets), "source_removed": old is None,
        }

    def _mapping_inventory(self, source_device, target_device, rows, allow_unavailable):
        _, replacement = self._validate_devices(source_device, target_device, allow_unavailable)
        if not isinstance(rows, list) or len(rows) > MAX_DEVICE_ENTITIES:
            raise ReplacementError(f"Choose at most {MAX_DEVICE_ENTITIES} entity mappings.")
        registry = er.async_get(self.hass)
        sources = {item["entity_id"]: item for item in self._device_entities(source_device)}
        targets = {item["entity_id"]: item for item in self._device_entities(target_device)}
        chosen, used_targets, warnings = {}, set(), []
        if getattr(replacement, "disabled_by", None) is not None:
            warnings.append("The replacement device is disabled. Its references will need the device enabled before they can work.")
        if targets and all(item["state"] in {"disabled", "not loaded", "unknown", "unavailable"} for item in targets.values()):
            if not allow_unavailable:
                raise ReplacementError("All replacement device entities are unavailable, unknown, disabled, or not loaded. Choose a working device, or explicitly allow unavailable replacements.")
            warnings.append("No replacement device entity currently reports a working state. Confirm device behavior after enabling it.")
        elif not targets:
            warnings.append("The replacement device has no registered entities. Online status cannot be inferred from entity states; confirm that its device events work.")
        ids = {getattr(entry, "id", None): entry for entry in registry.entities.values() if getattr(entry, "id", None)}
        for row in rows:
            if not isinstance(row, dict):
                raise ReplacementError("Each entity mapping must contain a source and replacement entity ID.")
            source, target = row.get("source", ""), row.get("target", "") or ""
            if not isinstance(source, str) or not ENTITY_ID.fullmatch(source) or source in chosen:
                raise ReplacementError("Source entity IDs must be valid and appear only once in the mapping table.")
            entry = registry.async_get(source)
            if entry is not None and entry.device_id != source_device:
                raise ReplacementError(f"{source} does not belong to the selected source device.")
            old_registry_id = row.get("source_registry_id") or (getattr(entry, "id", None) if entry else None)
            if old_registry_id and (not isinstance(old_registry_id, str) or not REGISTRY_ID.fullmatch(old_registry_id)):
                raise ReplacementError("Old entity registry IDs must be 32 lowercase hexadecimal characters.")
            if entry and old_registry_id != getattr(entry, "id", None):
                raise ReplacementError("An old registry ID disagrees with Home Assistant's current entity registry.")
            if old_registry_id in ids and ids[old_registry_id].entity_id != source:
                raise ReplacementError("An old registry ID belongs to another registered entity.")
            if not isinstance(target, str):
                raise ReplacementError("Replacement entity IDs must be text values.")
            if target:
                if target not in targets:
                    raise ReplacementError(f"{target} is not an entity on the replacement device.")
                if target in used_targets:
                    raise ReplacementError("Two old entities cannot use the same replacement. Assign each role once.")
                warnings.extend(f"{source}: {note}" for note in self._validate_target(source, target, allow_unavailable))
                used_targets.add(target)
            chosen[source] = {"source": source, "target": target or None, "source_registry_id": old_registry_id,
                              "target_registry_id": targets[target].get("registry_id") if target else None}
        # Include the entire device inventory, even rows omitted by a client.
        for source, info in sources.items():
            chosen.setdefault(source, {"source": source, "target": None, "source_registry_id": info["registry_id"], "target_registry_id": None})
        if len(chosen) > MAX_DEVICE_ENTITIES:
            raise ReplacementError("The combined source entity inventory is too large. Use smaller batches in entity mode.")
        rows = sorted(chosen.values(), key=lambda item: item["source"])
        tokens = {row["source"]: row["target"] for row in rows}
        for row in rows:
            if row["source_registry_id"]:
                if row["source_registry_id"] in tokens:
                    raise ReplacementError("Old registry IDs must be unique across entity mappings.")
                tokens[row["source_registry_id"]] = row["target_registry_id"]
        if source_device in tokens:
            raise ReplacementError("A device ID cannot also identify an entity registry entry.")
        tokens[source_device] = target_device
        missing = [row["source"] for row in rows if not row["target"]]
        if missing:
            warnings.append("Unmapped entities remain unchanged and appear for manual review: " + ", ".join(missing))
        return rows, tokens, warnings

    def _registry_fingerprint(self, source, target, rows):
        registry = self._device_registry()
        devices = []
        for device_id in (source, target):
            entry = registry.async_get(device_id)
            devices.append(None if entry is None else {
                "id": entry.id, "disabled": str(getattr(entry, "disabled_by", None)),
                "config_entries": sorted(getattr(entry, "config_entries", [])),
                "manufacturer": entry.manufacturer, "model": entry.model,
            })
        entities = self._device_entities(source) + self._device_entities(target)
        # Runtime measurements are irrelevant; identity, membership, units and
        # features must remain stable. Availability is checked separately.
        stable = [{key: value for key, value in item.items() if key not in {"state", "name"}} for item in entities]
        return digest(json_bytes([devices, stable, rows]))

    async def _device_capabilities(self, target, kinds):
        try:
            from homeassistant.components.device_automation import DeviceAutomationType, async_get_device_automations
        except ImportError:
            return {kind: None for kind in kinds}

        async def load(kind):
            try:
                result = await asyncio.wait_for(async_get_device_automations(self.hass, getattr(DeviceAutomationType, kind.upper()), [target]), 10)
                return kind, result.get(target, [])
            except Exception:
                _LOGGER.debug("Replacement device %s capability lookup failed", kind, exc_info=True)
                return kind, None

        return dict(await asyncio.gather(*(load(kind) for kind in kinds if kind != "target")))

    def _resolve_entity(self, value):
        registry = er.async_get(self.hass)
        entry = registry.async_get(value) if isinstance(value, str) else None
        if entry:
            return entry.entity_id
        if isinstance(value, str) and REGISTRY_ID.fullmatch(value):
            for item in registry.entities.values():
                if getattr(item, "id", None) == value:
                    return item.entity_id
        return value

    def _roles_compatible(self, rows, entities):
        registry = er.async_get(self.hass)
        for row in rows:
            if row["source"] not in entities and row["source_registry_id"] not in entities:
                continue
            old, new = registry.async_get(row["source"]), registry.async_get(row["target"]) if row["target"] else None
            if old is None:
                return "The original entity's capabilities are no longer known; rebuild this device block manually."
            if new is None:
                return "This device block uses an unmapped entity."
            before, after = self._entity_info(old), self._entity_info(new)
            if any(before[field] != after[field] for field in ("device_class", "unit_of_measurement")):
                return "Device entity classes or units differ; review thresholds and rebuild this device block manually."
            old_features, new_features = before["supported_features"] or 0, after["supported_features"] or 0
            if isinstance(old_features, int) and isinstance(new_features, int) and old_features & ~new_features:
                return "The replacement entity supports fewer features; rebuild this device block manually."
        return None

    async def _device_block_reason(self, block, source, target, rows, capabilities):
        if block.reason:
            return block.reason
        candidate = copy.deepcopy(block.candidate)
        selectors = candidate.get("device_id")
        if selectors != target and not (isinstance(selectors, list) and target in selectors):
            return "The replacement device selector could not be validated."
        original_entities = block.config.get("entity_id", [])
        original_entities = [original_entities] if isinstance(original_entities, str) else original_entities
        reason = self._roles_compatible(rows, original_entities)
        if reason:
            return reason
        if block.kind == "target":
            domain, service = block.service.split(".", 1)
            services = getattr(self.hass, "services", None)
            if services is None or not services.has_service(domain, service):
                return "The target service is not registered; review the complete service action manually."
            if domain == "homeassistant":
                return "Generic device service targets require manual review; use explicit mapped entity targets."
            old_domain = [item["entity_id"] for item in self._device_entities(source) if item["domain"] == domain]
            new_domain = {item["entity_id"] for item in self._device_entities(target) if item["domain"] == domain}
            mapped = {row["target"] for row in rows if row["source"] in old_domain and row["target"]}
            if not old_domain or len(mapped) != len(old_domain) or mapped != new_domain:
                return "Device service targets need every entity in this service domain mapped one-to-one; otherwise use explicit entity targets."
            return self._roles_compatible(rows, old_domain)
        advertised = capabilities.get(block.kind)
        if advertised is None:
            return "Replacement device capabilities could not be inspected; rebuild this block in the automation editor."
        identity = {key: value for key, value in candidate.items() if key in {"domain", "type", "subtype", "entity_id"}}
        if "entity_id" in identity:
            identity["entity_id"] = self._resolve_entity(identity["entity_id"])
        matches = []
        for descriptor in advertised:
            descriptor_identity = {key: value for key, value in descriptor.items() if key in {"domain", "type", "subtype", "entity_id"}}
            if "entity_id" in descriptor_identity:
                descriptor_identity["entity_id"] = self._resolve_entity(descriptor_identity["entity_id"])
            # Some integrations include endpoint/command/discovery selectors
            # beyond type/subtype. Never treat those as interchangeable.
            extra_selectors = {key: value for key, value in descriptor.items()
                               if key not in {"domain", "type", "subtype", "entity_id", "metadata", "device_id", "platform", "trigger", "condition"}}
            if (identity == descriptor_identity and descriptor.get("device_id") == target
                    and all(candidate.get(key) == value for key, value in extra_selectors.items())):
                matches.append(descriptor)
        if not matches:
            return "The replacement device does not advertise the same domain/type/subtype/entity capability. Rebuild this block manually."
        try:
            from homeassistant.components.device_automation import DeviceAutomationType, async_get_device_automation_platform

            platform = await asyncio.wait_for(async_get_device_automation_platform(self.hass, candidate["domain"], getattr(DeviceAutomationType, block.kind.upper())), 10)
            if block.kind == "trigger" and "trigger" in candidate:
                candidate["platform"] = candidate.pop("trigger")
            schema = getattr(platform, block.kind.upper() + "_SCHEMA", None)
            validator = getattr(platform, f"async_validate_{block.kind}_config", None)
            if schema is None and validator is None:
                return "This device automation platform exposes no config validator; review manually."
            if schema is not None:
                candidate = schema(candidate)
            if validator is not None:
                await asyncio.wait_for(validator(self.hass, candidate), 10)
        except Exception:
            _LOGGER.debug("Replacement %s config validation failed", block.kind, exc_info=True)
            return "Home Assistant rejected this block's replacement configuration; rebuild it in the automation editor."
        return None

    async def scan_device(self, owner, source, target, mappings, confirmed_mappings=False, include_text=True, include_storage=True, allow_unavailable_target=False):
        async with self.lock:
            if confirmed_mappings is not True:
                raise ReplacementError("Confirm the entity mapping table before scanning device replacements.")
            rows, tokens, warnings = self._mapping_inventory(source, target, mappings, allow_unavailable_target)
            fingerprint = self._registry_fingerprint(source, target, rows)
            documents, urls, notes, file_count, dashboard_count = await self._scan_documents(tokens, None, include_text, include_storage)
            blocks = []
            for document in documents:
                # Opaque IDs are never writable outside a validated YAML device
                # selector, even if they happen to appear in an unrelated field.
                for item in document.occurrences:
                    if item.source and REGISTRY_ID.fullmatch(item.source):
                        item.writable = False
                        item.reason = "Device/registry ID outside a validated device block; update the owning editor manually."
                blocks.extend(await self._io(collect_blocks, document, source, tokens))
            capabilities = await self._device_capabilities(target, {block.kind for block in blocks if not block.reason and block.kind != "unknown"})
            for block in blocks:
                authorize_block(block, await self._device_block_reason(block, source, target, rows, capabilities))
            old, new = self._validate_devices(source, target, allow_unavailable_target)
            if fingerprint != self._registry_fingerprint(source, target, rows):
                raise ReplacementError("Device/entity registry changed while scanning. Load the mapping table again.")
            preview_id, preview = self._remember_preview(owner, f"{self._device_name(old, source)} ({source})", f"{self._device_name(new, target)} ({target})", documents, urls, allow_unavailable_target,
                mode="device", mappings=rows, source_device=source, target_device=target, registry_snapshot=fingerprint, device_blocks=blocks)
            if old is None:
                warnings.append("The old device is removed. Only its entered device ID and explicitly supplied entity/registry IDs can be found; omitted former entities cannot be discovered.")
            warnings.append("Review unassigned entities and manual device blocks. Pairing, identities, areas/labels, and historical statistics are not transferred.")
            return await self._public_preview(preview_id, preview, warnings + notes, file_count, dashboard_count)

    async def _check_device_preview(self, preview, selected):
        rows, _, _ = self._mapping_inventory(preview.source_device, preview.target_device, preview.mappings, preview.allow_unavailable_target)
        if self._registry_fingerprint(preview.source_device, preview.target_device, rows) != preview.registry_snapshot:
            raise ReplacementError("Device/entity identities or capabilities changed since preview. Load mappings and scan again.")
        blocks = []
        for block in preview.device_blocks:
            group_ids = {item.id for item in block.occurrences}
            if selected & group_ids:
                if not group_ids <= selected:
                    raise ReplacementError("Select each complete device block together; changing only its device or entity ID is unsafe.")
                blocks.append(block)
        capabilities = await self._device_capabilities(preview.target_device, {block.kind for block in blocks})
        for block in blocks:
            reason = await self._device_block_reason(block, preview.source_device, preview.target_device, rows, capabilities)
            if reason:
                raise ReplacementError("Device block is no longer eligible: " + reason + " Scan again.")
