#!/usr/bin/env python3
"""Validate the repository and build its manual Home Assistant installation ZIP.

HACS installs from the repository's custom_components tree. This ZIP is an
additional release asset for people who choose manual installation.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
DOMAIN = "entity_replacer"
COMPONENT = ROOT / "custom_components" / DOMAIN
REPOSITORY = "https://github.com/FortranFour/device-replacer"


def check_repository(tag: str | None) -> tuple[str, list[Path]]:
    directories = [item.name for item in (ROOT / "custom_components").iterdir()
                   if item.is_dir() and not item.name.startswith(".") and item.name != "__pycache__"]
    if directories != [DOMAIN]:
        raise ValueError("HACS needs exactly one integration in custom_components.")
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    required = {"domain", "name", "documentation", "issue_tracker", "codeowners", "version", "config_flow"}
    if missing := required - manifest.keys():
        raise ValueError(f"Missing manifest fields: {', '.join(sorted(missing))}")
    version = manifest["version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Use a stable major.minor.patch manifest version.")
    if tag is not None and tag != f"v{version}":
        raise ValueError(f"The tag must be v{version}, not {tag!r}.")
    if manifest["domain"] != DOMAIN or manifest["codeowners"] != ["@FortranFour"]:
        raise ValueError("The domain or code owner does not match this repository.")
    if manifest["documentation"] != REPOSITORY + "#readme" or manifest["issue_tracker"] != REPOSITORY + "/issues":
        raise ValueError("The manifest must link to this repository's README and issues.")
    constants = ast.parse((COMPONENT / "const.py").read_text(encoding="utf-8"))
    versions = [ast.literal_eval(node.value) for node in constants.body
                if isinstance(node, ast.Assign) and any(isinstance(item, ast.Name) and item.id == "VERSION"
                                                       for item in node.targets)]
    if versions != [version]:
        raise ValueError("const.py and manifest.json version fields disagree.")
    panel = (COMPONENT / "frontend/panel.js").read_text(encoding="utf-8")
    if f'this._version || "{version}"' not in panel:
        raise ValueError("Update the panel's fallback version for this release.")
    hacs = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))
    if hacs.get("name") != manifest["name"] or hacs.get("render_readme") is not True:
        raise ValueError("Check HACS name and README rendering settings.")
    if hacs.get("homeassistant") != "2026.3.0" or hacs.get("content_in_root", False) or hacs.get("zip_release", False):
        raise ValueError("This package uses the standard repository layout and local brands from HA 2026.3.")
    for name in ("__init__.py", "config_flow.py", "compat.py", "const.py", "engine.py", "manager.py",
                 "device.py", "device_manager.py", "websocket.py", "strings.json", "translations/en.json", "frontend/panel.js", "frontend/icon.svg"):
        if not (COMPONENT / name).is_file():
            raise ValueError(f"Missing runtime file: {name}")
    for path in COMPONENT.rglob("*.json"):
        json.loads(path.read_text(encoding="utf-8"))
    for name, size in (("icon.png", (256, 256)), ("icon@2x.png", (512, 512)),
                       ("dark_icon.png", (256, 256)), ("dark_icon@2x.png", (512, 512)),
                       ("logo.png", (1200, 256)), ("logo@2x.png", (2400, 512)),
                       ("dark_logo.png", (1200, 256)), ("dark_logo@2x.png", (2400, 512))):
        header = (COMPONENT / "brand" / name).read_bytes()[:33]
        if header[:8] != b"\x89PNG\r\n\x1a\n" or len(header) < 33 or struct.unpack(">II", header[16:24]) != size:
            raise ValueError(f"Invalid or incorrectly sized brand image: {name}")
        if header[25] != 6:
            raise ValueError(f"Expected RGBA PNG brand image: {name}")
    for name in ("README.md", "LICENSE", "CHANGELOG.md", "RELEASE_NOTES.md"):
        if not (ROOT / name).is_file():
            raise ValueError(f"Missing repository document: {name}")
    files = sorted(path for path in COMPONENT.rglob("*")
                   if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc")
    if any(path.is_symlink() for path in files):
        raise ValueError("Runtime package must not include symbolic links.")
    return version, files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--tag", help="Require a version tag matching the manifest, e.g. v1.1.2")
    args = parser.parse_args()
    try:
        version, files = check_repository(args.tag)
        print(f"Repository checks passed: Device Replacer {version}; {len(files)} runtime files.")
        if args.check_only:
            return 0
        destination = ROOT / "dist"
        destination.mkdir(exist_ok=True)
        archive = destination / f"device_replacer_{version}.zip"
        with ZipFile(archive, "w", compression=ZIP_DEFLATED) as output:
            for path in files + [ROOT / "README.md", ROOT / "LICENSE", ROOT / "CHANGELOG.md"]:
                output.write(path, path.relative_to(ROOT).as_posix())
        checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
        (destination / "SHA256SUMS.txt").write_text(f"{checksum}  {archive.name}\n", encoding="utf-8")
        print(archive)
        return 0
    except (OSError, ValueError, SyntaxError) as error:
        print(f"Release check failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
