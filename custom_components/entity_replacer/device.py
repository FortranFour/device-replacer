"""Pure device mapping suggestions and YAML block analysis.

No hardware is controlled here. Native Home Assistant capability/schema checks
are performed by the manager before blocks become selectable.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode

from .engine import Document, MAX_NODES, PROTECTED_KEYS, Occurrence, ReplacementError, digest, json_bytes

MAX_DEVICE_ENTITIES = 200


def suggest_mappings(sources: list[dict], targets: list[dict]) -> list[dict]:
    """Return proposals, never permission to write. Ties remain unassigned."""
    proposals = []
    for source in sources:
        candidates = [target for target in targets if target["domain"] == source["domain"]]
        scored = []
        for target in candidates:
            score, reasons = 0, []
            for field, weight in (("translation_key", 100), ("device_class", 50), ("unit_of_measurement", 20), ("role_name", 60)):
                old, new = source.get(field), target.get(field)
                if old and new and old == new:
                    score += weight
                    reasons.append(field.replace("_", " "))
                elif field in {"device_class", "unit_of_measurement"} and old and new:
                    score -= 100
            if len(candidates) == 1:
                score += 10
                reasons.append("only entity in the same domain")
            scored.append((score, target["entity_id"], reasons))
        scored.sort(key=lambda item: (-item[0], item[1]))
        unique = bool(scored and scored[0][0] > 0 and (len(scored) == 1 or scored[0][0] > scored[1][0]))
        proposals.append({
            "source": source["entity_id"], "target": scored[0][1] if unique else "",
            "reason": "Proposed by " + ", ".join(scored[0][2]) + "; confirm this role." if unique else "No unambiguous match; choose a replacement or leave unmapped.",
        })
    # Do not propose the same replacement for two original entities.
    counts: dict[str, int] = {}
    for proposal in proposals:
        if proposal["target"]:
            counts[proposal["target"]] = counts.get(proposal["target"], 0) + 1
    for proposal in proposals:
        if counts.get(proposal["target"], 0) > 1:
            proposal.update(target="", reason="Several source entities compete for this role; choose explicitly.")
    return proposals


def role_name(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


@dataclass
class DeviceBlock:
    group: str
    path: tuple[Any, ...]
    kind: str
    config: dict
    candidate: dict
    occurrences: list[Occurrence]
    reason: str = ""
    service: str | None = None


def _map_values(value: Any, mappings: dict[str, str | None]) -> Any:
    if isinstance(value, str):
        return mappings.get(value) or value
    if isinstance(value, list):
        return [_map_values(item, mappings) for item in value]
    if isinstance(value, dict):
        # Identity selectors are rewritten, not arbitrary type/subtype strings.
        return {key: _map_values(item, mappings) if key in {"device_id", "entity_id"} else item
                for key, item in value.items()}
    return value


def collect_blocks(document: Document, source_device: str, mappings: dict[str, str | None]) -> list[DeviceBlock]:
    if document.kind != "yaml":
        return []
    text = document.original.decode("utf-8")
    try:
        roots = list(yaml.compose_all(text, Loader=yaml.SafeLoader))
    except (yaml.YAMLError, RecursionError):
        return []
    nodes: list[tuple[tuple, MappingNode, MappingNode | None]] = []
    visits: dict[int, int] = {}
    seen: set[int] = set()

    def walk(node: Any, path: tuple, parent: MappingNode | None = None):
        if node is None:
            return
        visits[id(node)] = visits.get(id(node), 0) + 1
        if id(node) in seen:
            return
        seen.add(id(node))
        if len(seen) > MAX_NODES:
            raise ReplacementError("Device YAML graph exceeds the scan limit.")
        if isinstance(node, MappingNode):
            values = {key.value: value for key, value in node.value if isinstance(key, ScalarNode)}
            selector = values.get("device_id")
            if (isinstance(selector, ScalarNode) and selector.value == source_device) or (isinstance(selector, SequenceNode) and any(isinstance(item, ScalarNode) and item.value == source_device for item in selector.value)):
                nodes.append((path, node, parent))
            for key, value in node.value:
                name = key.value if isinstance(key, ScalarNode) else "<complex-key>"
                walk(key, path + (name,), node)
                walk(value, path + (name,), node)
        elif isinstance(node, SequenceNode):
            for index, value in enumerate(node.value):
                walk(value, path + (index,), parent)

    try:
        for index, root in enumerate(roots):
            walk(root, () if len(roots) == 1 else (f"document:{index + 1}",))
    except (ReplacementError, RecursionError):
        return []
    reused = {key for key, count in visits.items() if count > 1}
    blocks = []

    def subtree_ids(node):
        ids, pending = set(), [node]
        while pending:
            current = pending.pop()
            if id(current) in ids:
                continue
            ids.add(id(current))
            if isinstance(current, MappingNode):
                pending.extend(child for pair in current.value for child in pair)
            elif isinstance(current, SequenceNode):
                pending.extend(current.value)
        return ids

    def construct(node):
        loader = yaml.SafeLoader("")
        try:
            return loader.construct_object(node, deep=True)
        finally:
            loader.dispose()

    for path, node, parent in nodes:
        occurrences = [item for item in document.occurrences if not item.is_key and item.kind != "comment" and item.path[:len(path)] == path]
        group = "device:" + digest(json_bytes([document.key, list(path)]))[:24]
        for item in occurrences:
            item.group = group
            item.writable = False
        reason = ""
        config = {}
        service = None
        kind = "unknown"
        try:
            config = construct(node)
            if not isinstance(config, dict):
                raise ValueError("not a mapping")
            if subtree_ids(node) & reused:
                reason = "Shared YAML anchors/aliases require manual device replacement."
            keys = [key.value for key, _ in node.value if isinstance(key, ScalarNode)]
            if len(keys) != len(set(keys)):
                reason = "Duplicate YAML selector keys require manual device replacement."
            elif any(key.value == "<<" for key, _ in node.value if isinstance(key, ScalarNode)):
                reason = "Merged YAML blocks require manual replacement."
            if config.get("trigger", config.get("platform")) == "device":
                kind = "trigger"
            elif config.get("condition") == "device":
                kind = "condition"
            elif config.get("domain") and config.get("type") and (len(path) <= 1 or any(part in {"actions", "action", "sequence", "then", "else", "default"} for part in path)):
                kind = "action"
            elif path and path[-1] == "target" and parent is not None:
                parent_values = {key.value: value for key, value in parent.value if isinstance(key, ScalarNode)}
                action_node = parent_values.get("action", parent_values.get("service"))
                if isinstance(action_node, ScalarNode) and re.fullmatch(r"[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*", action_node.value):
                    kind, service = "target", action_node.value
            if kind == "unknown":
                reason = reason or "Device reference in an unknown context; update the owning editor or blueprint input manually."
            if any(part in PROTECTED_KEYS for part in path if isinstance(part, str)):
                reason = reason or "Device reference beneath an identity/definition field; update manually."
            for item in occurrences:
                local_path = item.path[len(path):]
                field = next((part for part in reversed(local_path) if isinstance(part, str)), None)
                if field not in {"device_id", "entity_id"} or len(local_path) > 2 or item.kind == "encoded" or item.source not in mappings or not item.target:
                    reason = reason or "This device block contains unmapped, encoded, or non-selector references; replace the complete block manually."
                if item.reason.startswith("Tagged/typed"):
                    reason = reason or "Tagged selector values require manual device replacement."
                value = config.get(field)
                if isinstance(value, list) and local_path and isinstance(local_path[-1], int) and local_path[-1] < len(value):
                    value = value[local_path[-1]]
                if value != item.source:
                    reason = reason or "Device selectors must be literal IDs; templates and compound values need manual review."
            if not any(item.source == source_device for item in occurrences):
                reason = reason or "A literal device selector could not be located safely."
        except (yaml.YAMLError, ValueError, TypeError, RecursionError):
            reason = "Tagged, recursive, or unsupported device block; review manually."
        candidate = _map_values(config, mappings)
        blocks.append(DeviceBlock(group, path, kind, config, candidate, occurrences, reason, service))
    # An occurrence cannot belong to two atomic replacement groups.
    counts: dict[str, int] = {}
    for block in blocks:
        for item in block.occurrences:
            counts[item.id] = counts.get(item.id, 0) + 1
    for block in blocks:
        if any(counts[item.id] > 1 for item in block.occurrences):
            block.reason = "Overlapping/nested device blocks require manual replacement."
    return blocks


def authorize_block(block: DeviceBlock, reason: str | None) -> None:
    reason = block.reason or reason
    for item in block.occurrences:
        item.writable = not reason
        item.reason = reason or "Device block validated against the replacement's current capabilities. Select this group together."
