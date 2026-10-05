"""Pure reference scanning and durable file operations; no HA imports.

YAML is composed, never constructed or dumped. Edits only replace reviewed
character spans, preserving comments, tags, indentation, and line endings.
"""

from __future__ import annotations

import bisect
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any
import uuid

import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode
from yaml.tokens import ScalarToken

ENTITY_ID = re.compile(r"[a-z_][a-z0-9_]*\.[a-z0-9_]+\Z")
REGISTRY_ID = re.compile(r"[a-f0-9]{32}\Z")
JOB_ID = re.compile(r"\d{8}T\d{6}Z-[a-f0-9]{12}\Z")
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_FILES = 10000
MAX_NODES = 100000
BACKUP_DIR = ".entity_replacer_backups"
SKIP_DIRS = {
    "custom_components", "deps", "node_modules", "venv", "__pycache__",
    "backups", "backup", "tts", "media", "entity_replacer_reports",
}
REPORT_EXTENSIONS = {".json", ".js", ".jinja", ".j2", ".html", ".py"}
# These are binary persistence formats, not Home Assistant configuration JSON.
# Match the final suffix only: a file named foo.db.json must still be scanned.
BINARY_STORAGE_SUFFIXES = (
    ".db", ".db-shm", ".db-wal", ".sqlite", ".sqlite-shm", ".sqlite-wal",
    ".sqlite3", ".sqlite3-shm", ".sqlite3-wal", ".pickle", ".pkl", ".ser",
    ".bin", ".zip", ".gz", ".bz2", ".xz", ".png", ".jpg", ".jpeg",
    ".gif", ".webp", ".pdf",
)
PROTECTED_KEYS = {
    "unique_id", "default_entity_id", "object_id", "device_id", "id",
    "statistic_id", "source_entity_id",  # registry identities, not references
}
# source_entity_id is a real reference in YAML integrations; only protect it in
# storage reports (which are always manual). Do not suppress its YAML usage.
PROTECTED_KEYS.remove("source_entity_id")
SKIP_STORES = {
    "auth", "auth_provider.homeassistant", "onboarding", "core.uuid",
    "core.restore_state", "core.device_registry", "core.entity_registry",
    "core.area_registry", "core.label_registry", "core.floor_registry",
    "repairs.issue_registry",  # identity/cache, not transferable config
}


class ReplacementError(Exception):
    """A reviewable error without making assumptions about configuration."""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(data: Any) -> bytes:
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def entity_pattern(entity_id: str | dict[str, str | None]) -> re.Pattern[str]:
    if isinstance(entity_id, dict):
        if not entity_id or len(entity_id) > 601:
            raise ReplacementError("Device mapping exceeds the 600-reference limit.")
        if any(not ENTITY_ID.fullmatch(token) and not REGISTRY_ID.fullmatch(token) for token in entity_id):
            raise ReplacementError("Device mappings contain an invalid entity or registry ID.")
        alternatives = "|".join(re.escape(token) for token in sorted(entity_id, key=len, reverse=True))
        return re.compile(r"(?:(?<![A-Za-z0-9_.])|(?<=states\.))(?:" + alternatives + r")(?![A-Za-z0-9_])")
    if not ENTITY_ID.fullmatch(entity_id):
        raise ReplacementError(f"Invalid entity ID: {entity_id!r}")
    # Also recognize Jinja's states.sensor.name.attribute form. Do not match
    # sensor.name_extra or a token embedded in another dotted identifier.
    return re.compile(r"(?:(?<![A-Za-z0-9_.])|(?<=states\.))" + re.escape(entity_id) + r"(?![A-Za-z0-9_])")


def token_fields(source: str | dict[str, str | None], literal: str) -> dict:
    return {"source": literal, "target": source.get(literal)} if isinstance(source, dict) else {}


def validate_pair(source: str, target: str) -> None:
    entity_pattern(source)
    entity_pattern(target)
    if source == target:
        raise ReplacementError("Choose two different entity IDs.")
    if source.split(".", 1)[0] != target.split(".", 1)[0]:
        raise ReplacementError("The replacement must have the same domain (for example, switch → switch). Cross-domain replacements need changes to actions and card types.")


def safe_path(root: Path, relative: str) -> Path:
    """Disallow traversal and any symlink component, even within config."""
    rel = Path(relative)
    if rel.is_absolute() or ".." in rel.parts or not rel.parts:
        raise ReplacementError("Invalid configuration path.")
    current = root
    for part in rel.parts:
        current = current / part
        if current.is_symlink():
            raise ReplacementError(f"Symbolic links are not edited: {relative}")
    if not current.resolve().is_relative_to(root.resolve()):
        raise ReplacementError("Path is outside the configuration directory.")
    return current


def read_file(root: Path, relative: str) -> bytes:
    path = safe_path(root, relative)
    if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
        raise ReplacementError(f"File is missing or exceeds the 8 MiB limit: {relative}")
    # O_NOFOLLOW closes the final-component symlink race on Linux.
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as handle:
        content = handle.read(MAX_FILE_BYTES + 1)
    if len(content) > MAX_FILE_BYTES:
        raise ReplacementError(f"File exceeds the 8 MiB limit: {relative}")
    return content


def _fsync_directory(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    """Write a private temporary neighbor, then atomically replace one file."""
    fd, temporary = tempfile.mkstemp(prefix=".entity-replacer-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), mode)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def replace_file(root: Path, relative: str, expected_hash: str, data: bytes) -> None:
    path = safe_path(root, relative)
    info = path.stat()
    if info.st_nlink != 1:
        raise ReplacementError(f"Hard-linked files require manual editing: {relative}")
    if digest(read_file(root, relative)) != expected_hash:
        raise ReplacementError(f"Changed since preview: {relative}. Scan again.")
    # Preserve permission bits and ownership. A different owner cannot silently
    # become the Home Assistant process user after replacement.
    fd, temporary = tempfile.mkstemp(prefix=".entity-replacer-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            if (info.st_uid, info.st_gid) != (os.getuid(), os.getgid()):
                os.fchown(handle.fileno(), info.st_uid, info.st_gid)
            os.fchmod(handle.fileno(), stat.S_IMODE(info.st_mode))
        # Recheck immediately before the single-file atomic replace.
        if digest(read_file(root, relative)) != expected_hash:
            raise ReplacementError(f"Changed while applying: {relative}")
        safe_path(root, relative)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def pointer(path: tuple[Any, ...]) -> str:
    return "/" + "/".join(str(part).replace("~", "~0").replace("/", "~1") for part in path)


def unified_diff_text(before: str, after: str, label: str) -> str:
    # Normalize display lines only. Actual file edits retain CRLF and the
    # absence of a final newline; the preview must not merge +/- lines.
    return "".join(difflib.unified_diff(
        [line + "\n" for line in before.splitlines()],
        [line + "\n" for line in after.splitlines()],
        fromfile=label, tofile=label + " (replacement)",
    ))


def _snippet(text: str, start: int, end: int) -> str:
    # Never export a whole compact JSON file (which may contain credentials).
    left = max(0, start - 65)
    previous_newline = text.rfind("\n", left, start)
    if previous_newline >= 0:
        left = previous_newline + 1
    right = min(len(text), end + 65)
    next_newline = text.find("\n", end, right)
    if next_newline >= 0:
        right = next_newline
    return text[left:right].strip()


@dataclass
class Occurrence:
    id: str
    start: int
    end: int
    line: int | None
    column: int | None
    path: tuple[Any, ...]
    snippet: str
    writable: bool
    reason: str = ""
    kind: str = "reference"
    is_key: bool = False
    source: str | None = None
    target: str | None = None
    group: str | None = None

    def public(self) -> dict[str, Any]:
        result = asdict(self)
        result.pop("start")
        result.pop("end")
        result["pointer"] = pointer(self.path)
        return result


@dataclass
class Document:
    key: str
    kind: str
    label: str
    relative: str | None
    original: bytes | Any
    occurrences: list[Occurrence] = field(default_factory=list)
    dashboard_url: str | None = None
    warnings: list[str] = field(default_factory=list)

    def public(self, root: Path, source: str, target: str) -> dict[str, Any]:
        result = {
            "key": self.key, "kind": self.kind, "label": self.label,
            "file": str(root / self.relative) if self.relative else None,
            "dashboard_url": self.dashboard_url,
            "occurrences": [item.public() for item in self.occurrences],
            "warnings": self.warnings,
        }
        ids = {item.id for item in self.occurrences if item.writable}
        if ids:
            try:
                candidate = build_candidate(self, ids, source, target)
                before = self.original.decode("utf-8") if self.kind == "yaml" else json.dumps(self.original, indent=2, ensure_ascii=False)
                after = candidate.decode("utf-8") if self.kind == "yaml" else json.dumps(candidate, indent=2, ensure_ascii=False)
                result["diff"] = unified_diff_text(before, after, self.label)
            except ReplacementError as err:
                result["warnings"] = self.warnings + [str(err)]
                result["diff"] = "Preview contains a conflict. Deselect conflicting occurrences or edit manually."
        return result


def _occurrence_id(key: str, path: tuple[Any, ...], start: int, is_key: bool = False) -> str:
    return hashlib.sha256(json_bytes([key, list(path), start, is_key])).hexdigest()[:24]


def scan_yaml(relative: str, raw: bytes, source: str, device_id: str | None = None) -> Document:
    text = raw.decode("utf-8")  # BOM, CRLF, and non-ASCII character offsets retained
    pattern = entity_pattern(source)
    document = Document("yaml:" + relative, "yaml", relative, relative, raw)
    line_starts = [0] + [m.end() for m in re.finditer("\n", text)]
    found: dict[tuple[int, int], Occurrence] = {}
    covered: set[tuple[int, int]] = set()

    def add(start: int, end: int, path: tuple[Any, ...], reason: str = "", kind: str = "reference", is_key: bool = False) -> None:
        location = (start, end)
        if location in found:
            if reason:  # aliases can use the same scalar in a protected context
                found[location].writable = False
                found[location].reason = reason
            return
        line_index = bisect.bisect_right(line_starts, start) - 1
        found[location] = Occurrence(
            _occurrence_id(document.key, path, start, is_key), start, end,
            line_index + 1, start - line_starts[line_index] + 1, path,
            _snippet(text, start, end), not reason, reason, kind, is_key,
            **token_fields(source, text[start:end]),
        )
        if isinstance(source, dict) and text[start:end] in source and source[text[start:end]] is None:
            found[location].writable = False
            found[location].reason = "No approved replacement mapping for this source entity or registry ID."

    try:
        roots = list(yaml.compose_all(text, Loader=yaml.SafeLoader))
        scalar_tokens = {token.end_mark.index: token for token in yaml.scan(text, Loader=yaml.SafeLoader) if isinstance(token, ScalarToken)}
        visited: set[tuple[int, str]] = set()

        def visit(node: Any, path: tuple[Any, ...], inherited: str = "", is_key: bool = False) -> None:
            if node is None:
                return
            signature = (id(node), inherited)
            if signature in visited:
                return
            visited.add(signature)
            if len(visited) > MAX_NODES:
                raise ReplacementError("YAML graph exceeds the scan limit.")
            if isinstance(node, MappingNode):
                keys = {key.value for key, _ in node.value if isinstance(key, ScalarNode)}
                device_block = "device_id" in keys
                for key, value in node.value:
                    name = key.value if isinstance(key, ScalarNode) else "<complex-key>"
                    child_path = path + (name,)
                    visit(key, child_path, inherited, True)
                    reason = inherited
                    if name in PROTECTED_KEYS:
                        reason = f"Identity/definition field '{name}'; review manually."
                    elif device_block:
                        reason = "This block also targets a device_id. Recreate the device trigger/condition/action with the replacement device, or convert it to an entity-based block."
                    visit(value, child_path, reason)
            elif isinstance(node, SequenceNode):
                for index, item in enumerate(node.value):
                    visit(item, path + (index,), inherited)
            elif isinstance(node, ScalarNode):
                token = scalar_tokens.get(node.end_mark.index)
                start = token.start_mark.index if token else node.start_mark.index
                end = node.end_mark.index
                segment = text[start:end]
                reason = inherited
                if node.tag != "tag:yaml.org,2002:str":
                    reason = "Tagged/typed YAML value; review manually."
                matches = list(pattern.finditer(segment))
                for match in matches:
                    bounds = (start + match.start(), start + match.end())
                    covered.add(bounds)
                    add(*bounds, path, reason, is_key=is_key)
                if not matches and pattern.search(str(node.value)):
                    add(start, end, path, "Entity ID is encoded in a quoted YAML value; edit the decoded value manually.", "encoded", is_key)
                if device_id and path and path[-1] == "device_id" and str(node.value) == device_id:
                    add(start, end, path, "Reference to the source device. An entity replacement does not transfer a device ID; recreate this block with the replacement device.", "device")

        for index, node in enumerate(roots):
            visit(node, () if len(roots) == 1 else (f"document:{index + 1}",))
    except (yaml.YAMLError, ReplacementError, RecursionError) as err:
        found.clear()
        covered.clear()
        document.warnings.append(f"YAML could not be inspected safely: {str(err)[:200]}")
        for match in pattern.finditer(text):
            add(match.start(), match.end(), (), "YAML parse failed; correct the file and scan again.")
    else:
        for match in pattern.finditer(text):
            if (match.start(), match.end()) not in covered:
                add(match.start(), match.end(), (), "Comment, anchor, or tag; no automatic change.", "comment")
    document.occurrences = sorted(found.values(), key=lambda item: item.start)
    return document


def scan_report(relative: str, raw: bytes, source: str, device_id: str | None = None) -> Document:
    structured = relative.startswith(".storage/") or Path(relative).suffix.lower() in {".yaml", ".yml", ".json"}
    document = scan_yaml(relative, raw, source, device_id) if structured else Document("report:" + relative, "report", relative, relative, raw)
    text = raw.decode("utf-8")
    if document.kind != "report":
        document.key = "report:" + relative
        document.kind = "report"
    else:
        for match in entity_pattern(source).finditer(text):
            start = match.start()
            document.occurrences.append(Occurrence(
                _occurrence_id(document.key, (), start), start, match.end(),
                text.count("\n", 0, start) + 1, start - text.rfind("\n", 0, start), (),
                _snippet(text, start, match.end()), False,
                **token_fields(source, match.group()),
            ))
    storage = relative.startswith(".storage/")
    entries = []
    if relative == ".storage/core.config_entries":
        try:
            entries = json.loads(text).get("data", {}).get("entries", [])
        except (ValueError, AttributeError):
            pass
    for occurrence in document.occurrences:
        occurrence.writable = False
        occurrence.reason = (
            "Internal storage: change this setting in its owning integration/helper UI. Do not edit .storage while Home Assistant is running."
            if storage else "Additional text/code file: review and edit this reference manually."
        )
        if occurrence.path[:2] == ("data", "entries") and len(occurrence.path) > 2:
            index = occurrence.path[2]
            if isinstance(index, int) and 0 <= index < len(entries) and isinstance(entries[index], dict):
                owner = entries[index]
                title = owner.get("title", "unnamed")
                domain = owner.get("domain", "unknown")
                occurrence.reason += f" Owning integration/helper: {title} ({domain}); Settings → Devices & services."
    return document


def discover(root: Path, include_text: bool, include_storage: bool, excluded_stores: set[str]) -> tuple[list[str], list[str]]:
    files: list[str] = []
    warnings: list[str] = []

    def walk_error(error: OSError) -> None:
        warnings.append(f"Directory could not be scanned: {error.filename}")

    for directory, dirs, names in os.walk(root, followlinks=False, onerror=walk_error):
        base = Path(directory)
        dirs[:] = sorted(name for name in dirs if name not in SKIP_DIRS and not name.startswith(".") and not (base / name).is_symlink() and not (base.name == "www" and name == "community"))
        for name in sorted(names):
            path = base / name
            if name.lower() == "secrets.yaml" or name.startswith("."):
                continue
            if path.suffix.lower() in {".yaml", ".yml"} or (include_text and path.suffix.lower() in REPORT_EXTENSIONS):
                relative = str(path.relative_to(root))
                if path.is_symlink():
                    warnings.append(f"Symbolic link skipped: {relative}")
                else:
                    files.append(relative)
    if include_storage:
        storage = root / ".storage"
        if storage.is_symlink():
            warnings.append("The .storage directory is a symbolic link and was skipped.")
        elif storage.is_dir():
            for path in sorted(storage.iterdir()):
                name = path.name
                if name in SKIP_STORES or name in excluded_stores or name.startswith(("auth", "trace", "entity_replacer", ".")):
                    continue
                if name.lower().endswith(BINARY_STORAGE_SUFFIXES):
                    continue
                if path.is_symlink():
                    warnings.append(f"Symbolic link skipped: .storage/{name}")
                elif path.is_file():
                    files.append(".storage/" + name)
    if len(files) > MAX_FILES:
        warnings.append(f"Scan is incomplete: the {MAX_FILES}-file limit was reached.")
        files = files[:MAX_FILES]
    return files, warnings


def scan_files(root: Path, source: str, device_id: str | None = None, include_text: bool = True, include_storage: bool = True, excluded_stores: set[str] | None = None) -> tuple[list[Document], list[str], int]:
    files, warnings = discover(root, include_text, include_storage, excluded_stores or set())
    documents: list[Document] = []
    size = 0
    count = 0
    for relative in files:
        try:
            if relative.startswith(".storage/"):
                path = safe_path(root, relative)
                fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                with os.fdopen(fd, "rb") as handle:
                    header = handle.read(16)
                if known_binary_storage(header):
                    continue
            raw = read_file(root, relative)
            size += len(raw)
            if size > MAX_TOTAL_BYTES:
                warnings.append("Scan is incomplete: the 64 MiB total scan limit was reached.")
                break
            count += 1
            document = (scan_yaml(relative, raw, source, device_id) if Path(relative).suffix.lower() in {".yaml", ".yml"} and not relative.startswith(".storage/") else scan_report(relative, raw, source, device_id))
            if document.occurrences or document.warnings:
                documents.append(document)
        except (OSError, UnicodeError, ReplacementError) as err:
            warnings.append(f"Skipped {relative}: {str(err)[:200]}")
    return documents, warnings, count


def known_binary_storage(header: bytes) -> bool:
    """Recognize opaque formats without decoding/deserializing their content."""
    return (
        header.startswith((b"SQLite format 3\x00", b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08",
                           b"\x1f\x8b", b"BZh", b"\xfd7zXZ\x00", b"\x89PNG\r\n\x1a\n",
                           b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"%PDF-"))
        or (len(header) >= 2 and header[0] == 0x80 and header[1] in range(2, 6))
        or (header.startswith(b"RIFF") and header[8:12] == b"WEBP")
    )


def scan_dashboard(key: str, label: str, config: dict, source: str, relative: str | None, disk_raw: bytes | None = None, device_id: str | None = None) -> Document:
    document = Document("dashboard:" + key, "dashboard", label, relative, config, dashboard_url=key or "lovelace")
    pattern = entity_pattern(source)
    disk_locations: dict[tuple[Any, ...], list[Occurrence]] = {}
    if disk_raw:
        try:
            data = json.loads(disk_raw)
            if data.get("data", {}).get("config") == config:
                disk_scan = scan_yaml(relative or "dashboard.json", disk_raw, source, device_id)
                for item in disk_scan.occurrences:
                    if item.path[:2] == ("data", "config"):
                        disk_locations.setdefault(item.path[2:], []).append(item)
        except (ValueError, AttributeError, UnicodeError):
            pass
    visited = 0

    def walk(value: Any, path: tuple[Any, ...], reason: str = "", is_key: bool = False) -> None:
        nonlocal visited
        visited += 1
        if visited > MAX_NODES:
            raise ReplacementError("Dashboard exceeds the scan limit.")
        if isinstance(value, dict):
            device_block = "device_id" in value
            for name, child in value.items():
                next_path = path + (name,)
                walk(name, next_path, reason, True)
                child_reason = reason
                if name in PROTECTED_KEYS:
                    child_reason = f"Identity/definition field '{name}'; review manually."
                elif device_block:
                    child_reason = "This block also contains a device_id; review and update the device in its owning UI."
                walk(child, next_path, child_reason)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, path + (index,), reason)
        elif isinstance(value, str):
            matches = list(pattern.finditer(value))
            for index, match in enumerate(matches):
                locations = [item for item in disk_locations.get(path, []) if item.is_key == is_key]
                location = locations[index] if len(locations) == len(matches) else None
                document.occurrences.append(Occurrence(
                    _occurrence_id(document.key, path, match.start(), is_key),
                    match.start(), match.end(), location.line if location else None,
                    location.column if location else None, path, _snippet(value, match.start(), match.end()),
                    not reason, reason, is_key=is_key,
                    **token_fields(source, match.group()),
                ))
                if isinstance(source, dict) and source.get(match.group()) is None:
                    document.occurrences[-1].writable = False
                    document.occurrences[-1].reason = "No approved replacement mapping for this source entity or registry ID."
            if device_id and not is_key and path and path[-1] == "device_id" and value == device_id:
                document.occurrences.append(Occurrence(
                    _occurrence_id(document.key, path, 0), 0, len(value), None, None, path, value,
                    False, "Source device reference; update the device in its owning UI.", "device",
                ))

    walk(config, ())
    return document


def _duplicates(text: str) -> set[tuple[Any, ...]]:
    duplicates: set[tuple[Any, ...]] = set()
    seen: set[int] = set()

    def walk(node: Any, path: tuple[Any, ...]) -> None:
        if node is None or id(node) in seen:
            return
        seen.add(id(node))
        if isinstance(node, MappingNode):
            keys: set[tuple[str, str]] = set()
            for key, value in node.value:
                if isinstance(key, ScalarNode):
                    signature = (key.tag, key.value)
                    if signature in keys and key.value != "<<":
                        duplicates.add(path + (key.value,))
                    keys.add(signature)
                    walk(value, path + (key.value,))
                else:
                    walk(value, path + ("<complex-key>",))
        elif isinstance(node, SequenceNode):
            for index, child in enumerate(node.value):
                walk(child, path + (index,))

    try:
        for index, root in enumerate(yaml.compose_all(text, Loader=yaml.SafeLoader)):
            walk(root, (index,))
    except (yaml.YAMLError, RecursionError) as err:
        raise ReplacementError(f"Edited YAML is invalid: {str(err)[:200]}") from err
    return duplicates


def build_candidate(document: Document, selected: set[str], source: str, target: str) -> bytes | dict:
    occurrences = [item for item in document.occurrences if item.id in selected]
    if any(not item.writable for item in occurrences) or document.kind == "report":
        raise ReplacementError("A manual-review reference cannot be applied automatically.")
    if document.kind == "yaml":
        text = document.original.decode("utf-8")
        before_duplicates = _duplicates(text)
        for item in sorted(occurrences, key=lambda item: item.start, reverse=True):
            old = item.source or source
            new = item.target if item.source is not None else target
            if not isinstance(new, str) or text[item.start:item.end] != old:
                raise ReplacementError("Preview span is no longer a literal entity ID.")
            text = text[:item.start] + new + text[item.end:]
        if _duplicates(text) - before_duplicates:
            raise ReplacementError(f"Replacement would create a duplicate YAML key in {document.label}.")
        output = text.encode("utf-8")
        if len(output) > MAX_FILE_BYTES:
            raise ReplacementError("Edited YAML exceeds the 8 MiB limit.")
        return output

    # Keep scalar offsets tied to the original strings even if a key is renamed.
    groups: dict[tuple[tuple[Any, ...], bool], list[Occurrence]] = {}
    for item in occurrences:
        groups.setdefault((item.path, item.is_key), []).append(item)

    def rewrite(value: Any, path: tuple[Any, ...], is_key: bool = False) -> Any:
        if isinstance(value, str):
            for item in sorted(groups.get((path, is_key), []), key=lambda item: item.start, reverse=True):
                old = item.source or source
                new = item.target if item.source is not None else target
                if not isinstance(new, str) or value[item.start:item.end] != old:
                    raise ReplacementError("Dashboard reference changed since preview.")
                value = value[:item.start] + new + value[item.end:]
            return value
        if isinstance(value, list):
            return [rewrite(item, path + (index,)) for index, item in enumerate(value)]
        if isinstance(value, dict):
            result: dict = {}
            for key, child in value.items():
                new_key = rewrite(key, path + (key,), True)
                if new_key in result:
                    raise ReplacementError(f"Replacement would create a duplicate dashboard key in {document.label}.")
                result[new_key] = rewrite(child, path + (key,))
            return result
        return value

    output = rewrite(document.original, ())
    if len(json_bytes(output)) > MAX_FILE_BYTES:
        raise ReplacementError("Edited dashboard exceeds the 8 MiB limit.")
    return output


class BackupStore:
    """Durable before/after payloads, guarded by hashes and a private manifest."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _base(self) -> Path:
        base = safe_path(self.root, BACKUP_DIR)
        base.mkdir(mode=0o700, exist_ok=True)
        return base

    def _job(self, job_id: str) -> Path:
        if not JOB_ID.fullmatch(job_id):
            raise ReplacementError("Invalid backup ID.")
        return safe_path(self.root, BACKUP_DIR + "/" + job_id)

    def create(self, source: str, target: str, items: list[dict]) -> dict:
        job_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:12]
        directory = self._base() / job_id
        directory.mkdir(mode=0o700)
        manifest = {
            "version": 1, "job_id": job_id, "source": source, "target": target,
            "created": datetime.now(timezone.utc).isoformat(), "status": "prepared",
            "items": [], "errors": [],
        }
        for index, item in enumerate(items):
            before = item["before"]
            after = item["after"]
            record = {key: value for key, value in item.items() if key not in {"before", "after"}}
            record.update({
                "index": index, "before_hash": digest(before), "after_hash": digest(after),
                "before_file": f"{index:04d}.before", "after_file": f"{index:04d}.after",
                "status": "pending",
            })
            atomic_write(directory / record["before_file"], before)
            atomic_write(directory / record["after_file"], after)
            manifest["items"].append(record)
        self.save(manifest)
        return manifest

    def save(self, manifest: dict) -> None:
        directory = self._job(manifest["job_id"])
        atomic_write(directory / "manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8"))

    def load(self, job_id: str) -> dict:
        directory = self._job(job_id)
        manifest_path = safe_path(self.root, str((directory / "manifest.json").relative_to(self.root)))
        if manifest_path.stat().st_size > MAX_FILE_BYTES:
            raise ReplacementError("Backup manifest exceeds its limit.")
        manifest = json.loads(manifest_path.read_text("utf-8"))
        if manifest.get("version") != 1 or manifest.get("job_id") != job_id:
            raise ReplacementError("Backup manifest is invalid.")
        for index, item in enumerate(manifest["items"]):
            if item.get("index") != index or item.get("kind") not in {"yaml", "dashboard"}:
                raise ReplacementError("Invalid backup item.")
            if item["kind"] == "yaml" and (
                not isinstance(item.get("relative"), str)
                or Path(item["relative"]).suffix.lower() not in {".yaml", ".yml"}
                or Path(item["relative"]).name.lower() == "secrets.yaml"
                or any(part.startswith(".") or part in SKIP_DIRS for part in Path(item["relative"]).parts)
            ):
                raise ReplacementError("Backup target is outside the editable YAML scope.")
            if item.get("relative"):
                safe_path(self.root, item["relative"])
            for name in ("before", "after"):
                if item.get(name + "_file") != f"{index:04d}.{name}":
                    raise ReplacementError("Invalid backup payload path.")
        return manifest

    def payload(self, manifest: dict, item: dict, which: str) -> bytes:
        if which not in {"before", "after"}:
            raise ReplacementError("Invalid payload type.")
        relative = BACKUP_DIR + "/" + manifest["job_id"] + "/" + item[which + "_file"]
        raw = read_file(self.root, relative)
        if digest(raw) != item[which + "_hash"]:
            raise ReplacementError("Backup payload checksum failed.")
        return raw

    def history(self) -> list[dict]:
        base = safe_path(self.root, BACKUP_DIR)
        if not base.exists():
            return []
        result = []
        for directory in sorted(base.iterdir(), reverse=True):
            if not JOB_ID.fullmatch(directory.name) or directory.is_symlink():
                continue
            try:
                manifest = self.load(directory.name)
                result.append({key: manifest[key] for key in ("job_id", "created", "source", "target", "status")})
            except (OSError, ValueError, KeyError, ReplacementError):
                continue
            if len(result) >= 30:
                break
        return result


def render_report(preview: dict) -> str:
    lines = [
        "# Device Replacer preview", "", f"Replace `{preview['source']}` → `{preview['target']}`", "",
        f"Scanned {preview['files_scanned']} configuration files and {preview['dashboards_scanned']} UI dashboards.",
        "", "Line numbers are 1-based. UI dashboard locations without a line use a JSON pointer.",
        "This is a literal-reference scan; computed IDs, external add-ons, area/label targets, and unsupported device blocks need separate review.", "",
    ]
    if preview.get("mode") == "device":
        lines += ["## Confirmed entity mappings", "", "| Source entity | Replacement entity | Old registry ID | New registry ID |", "| --- | --- | --- | --- |"]
        for row in preview.get("mappings", []):
            lines.append(f"| `{row['source']}` | {('`' + row['target'] + '`') if row['target'] else 'Unmapped; unchanged'} | {row.get('source_registry_id') or 'Unknown'} | {row.get('target_registry_id') or 'None'} |")
        lines += ["", "Device automation selector groups must be selected together. Unsupported blocks must be rebuilt in their owning editor.", "Pairing, unique IDs, areas/labels and historical statistics are not transferred.", ""]
    for warning in preview["warnings"]:
        lines += ["- " + warning]
    for document in preview["documents"]:
        lines += ["", "## " + document["label"], "", "Path: `" + (document["file"] or "UI dashboard") + "`", ""]
        for item in document["occurrences"]:
            location = f"line {item['line']}, column {item['column']}" if item["line"] else "pointer `" + item["pointer"] + "`"
            mode = "Selectable" if item["writable"] else "Manual review"
            mapping = f"; `{item['source']}` → `{item.get('target') or 'unmapped; unchanged'}`" if item.get("source") else ""
            group = "; group `" + item["group"] + "`" if item.get("group") else ""
            lines += [f"- {mode}; {location}{mapping}{group}; {item['reason'] or 'literal entity reference'}", "", "    " + item["snippet"].replace("\n", "\n    "), ""]
        if document.get("diff"):
            lines += ["```diff", document["diff"], "```"]
    lines += ["", "After applying YAML: check Home Assistant configuration, then restart or reload affected YAML sections.", "UI dashboards are saved through Home Assistant and refresh without a restart.", ""]
    return "\n".join(lines)
