#!/usr/bin/env python3
"""Read-only entity-reference report for a configuration directory or copy.

Uses the same engine without importing Home Assistant. PyYAML is required.
UI dashboard .storage content is reported for manual review in this offline mode.
"""

import argparse
import importlib.util
from pathlib import Path
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--config", type=Path, default=Path("/config"))
parser.add_argument("--source", required=True)
parser.add_argument("--replacement", required=True)
parser.add_argument("--yaml-only", action="store_true")
args = parser.parse_args()
component = Path(__file__).resolve().parent / "custom_components/entity_replacer/engine.py"
spec = importlib.util.spec_from_file_location("entity_replacer_audit_engine", component)
engine = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = engine
spec.loader.exec_module(engine)

try:
    engine.validate_pair(args.source, args.replacement)
    root = args.config.resolve(strict=True)
    if not root.is_dir():
        raise engine.ReplacementError("Configuration path must be a directory.")
    documents, warnings, count = engine.scan_files(root, args.source, include_text=not args.yaml_only, include_storage=not args.yaml_only)
    preview = {
        "source": args.source, "target": args.replacement, "warnings": warnings,
        "files_scanned": count, "dashboards_scanned": 0,
        "documents": [document.public(root, args.source, args.replacement) for document in documents],
    }
    print(engine.render_report(preview))
except (OSError, engine.ReplacementError) as error:
    parser.error(str(error))
