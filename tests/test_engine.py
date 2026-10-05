"""Reference and durable-file tests. Run with python -m unittest discover."""

import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

MODULE_PATH = Path(__file__).resolve().parents[1] / "custom_components/entity_replacer/engine.py"
spec = importlib.util.spec_from_file_location("entity_replacer_engine", MODULE_PATH)
engine = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = engine
spec.loader.exec_module(engine)

OLD = "sensor.old"
NEW = "sensor.new"


class ScannerTests(unittest.TestCase):
    def scan(self, text, device_id=None):
        return engine.scan_yaml("automations.yaml", text.encode("utf-8"), OLD, device_id)

    def apply(self, document, ids=None):
        if ids is None:
            ids = {item.id for item in document.occurrences if item.writable}
        return engine.build_candidate(document, ids, OLD, NEW).decode("utf-8")

    def test_complete_tokens_and_attribute_access(self):
        text = "entity: sensor.old\nother: sensor.old_2\ntemplate: '{{ states.sensor.old.state }}'\nurl: /api/states/sensor.old\nembedded: prefix.sensor.old\n"
        result = self.apply(self.scan(text))
        self.assertIn("entity: sensor.new", result)
        self.assertIn("states.sensor.new.state", result)
        self.assertIn("/api/states/sensor.new", result)
        self.assertIn("sensor.old_2", result)
        self.assertIn("prefix.sensor.old", result)

    def test_comments_crlf_indentation_and_unicode(self):
        text = "# sensor.old\r\nname: Café\r\n  # comment preserved\r\nentity: 'sensor.old' # sensor.old\r\n"
        document = self.scan(text)
        result = self.apply(document)
        self.assertEqual(result, text.replace("'sensor.old'", "'sensor.new'"))
        reference = next(item for item in document.occurrences if item.writable)
        self.assertEqual((reference.line, reference.column), (4, 10))

    def test_bom_and_no_final_newline(self):
        text = "\ufeffentity: sensor.old"
        result = self.apply(self.scan(text))
        self.assertEqual(result, "\ufeffentity: sensor.new")
        diff = engine.unified_diff_text(text, result, "x.yaml")
        self.assertIn("-\ufeffentity: sensor.old\n+\ufeffentity: sensor.new\n", diff)

    def test_block_template_and_javascript(self):
        text = "state: >-\n  {{ states('sensor.old') }}\n  {{ states.sensor.old.attributes.value }}\nstyle: |\n  [[[ return hass.states['sensor.old'].state; ]]]\n"
        document = self.scan(text)
        self.assertEqual(len([item for item in document.occurrences if item.writable]), 3)
        self.assertNotIn(OLD, self.apply(document))

    def test_literal_hash_in_string_is_replaced(self):
        document = self.scan('text: "# sensor.old"\n')
        self.assertEqual(self.apply(document), 'text: "# sensor.new"\n')

    def test_tagged_values_and_definitions_are_manual(self):
        text = "secret: !secret sensor.old\ninclude: !include sensor.old\ndefault_entity_id: sensor.old\nunique_id: sensor.old\nentity_id: sensor.old\n"
        document = self.scan(text)
        self.assertEqual(len([item for item in document.occurrences if item.writable]), 1)
        result = self.apply(document)
        self.assertIn("!secret sensor.old", result)
        self.assertIn("default_entity_id: sensor.old", result)
        self.assertIn("entity_id: sensor.new", result)

    def test_source_entity_id_reference_is_editable(self):
        self.assertEqual(self.apply(self.scan("source_entity_id: sensor.old\n")), "source_entity_id: sensor.new\n")

    def test_device_based_blocks_manual(self):
        text = "trigger:\n  device_id: abc\n  entity_id: sensor.old\ncondition:\n  entity_id: sensor.old\n"
        document = self.scan(text)
        result = self.apply(document)
        self.assertIn("trigger:\n  device_id: abc\n  entity_id: sensor.old", result)
        self.assertIn("condition:\n  entity_id: sensor.new", result)

    def test_device_only_reference_detected(self):
        document = self.scan("trigger:\n  device_id: abc\n  domain: zha\n", "abc")
        self.assertEqual(len(document.occurrences), 1)
        self.assertEqual(document.occurrences[0].kind, "device")
        self.assertFalse(document.occurrences[0].writable)

    def test_anchor_alias_replaced_once(self):
        document = self.scan("first: &entity sensor.old\nsecond: *entity\n")
        self.assertEqual(len(document.occurrences), 1)
        self.assertEqual(self.apply(document), "first: &entity sensor.new\nsecond: *entity\n")

    def test_anchor_name_is_preserved(self):
        document = self.scan("first: &sensor_old sensor.old\nsecond: *sensor_old\n")
        self.assertEqual(self.apply(document), "first: &sensor_old sensor.new\nsecond: *sensor_old\n")

    def test_alias_in_device_context_protects_shared_value(self):
        document = self.scan("first: &entity sensor.old\ntrigger:\n  device_id: abc\n  entity_id: *entity\n")
        self.assertFalse(any(item.writable for item in document.occurrences))

    def test_recursive_alias_does_not_hang(self):
        document = self.scan("first: &entity\n  recursive: *entity\n  entity: sensor.old\n")
        self.assertEqual(len(document.occurrences), 1)
        self.assertIn("sensor.new", self.apply(document))

    def test_encoded_id_manual(self):
        document = self.scan('entity: "sensor.\\u006fld"\n')
        self.assertEqual(document.occurrences[0].kind, "encoded")
        self.assertFalse(document.occurrences[0].writable)

    def test_invalid_yaml_reports_locations(self):
        document = self.scan("entity: [sensor.old\n")
        self.assertTrue(document.warnings)
        self.assertFalse(document.occurrences[0].writable)
        self.assertEqual(document.occurrences[0].line, 1)

    def test_duplicate_key_prevents_overwrite(self):
        document = self.scan("customize:\n  sensor.old:\n    icon: mdi:test\n  sensor.new:\n    icon: mdi:other\n")
        with self.assertRaises(engine.ReplacementError):
            self.apply(document)

    def test_partial_selection_preserves_unselected_references(self):
        document = self.scan("first: sensor.old\nsecond: sensor.old\n")
        result = self.apply(document, {document.occurrences[1].id})
        self.assertEqual(result, "first: sensor.old\nsecond: sensor.new\n")

    def test_manual_selection_is_rejected(self):
        document = self.scan("# sensor.old\n")
        with self.assertRaises(engine.ReplacementError):
            self.apply(document, {document.occurrences[0].id})

    def test_multiple_documents(self):
        result = self.apply(self.scan("entity: sensor.old\n---\nentity: sensor.old\n"))
        self.assertEqual(result.count(NEW), 2)

    def test_invalid_pair_and_cross_domain(self):
        for source, target in [(OLD, OLD), (OLD, "light.new"), ("../../auth", NEW), (OLD, "sensor.new-extra")]:
            with self.assertRaises(engine.ReplacementError):
                engine.validate_pair(source, target)

    def test_dashboard_nested_keys_and_template(self):
        config = {"views": [{"cards": [{"entity": OLD, "template": "{{ states.sensor.old.state }}", "sensor.old": {"entity": OLD}}]}]}
        document = engine.scan_dashboard("main", "Main", config, OLD, None)
        selected = {item.id for item in document.occurrences if item.writable}
        result = engine.build_candidate(document, selected, OLD, NEW)
        self.assertEqual(config["views"][0]["cards"][0]["entity"], OLD)
        self.assertEqual(result["views"][0]["cards"][0]["sensor.new"]["entity"], NEW)
        self.assertIn("states.sensor.new.state", result["views"][0]["cards"][0]["template"])

    def test_dashboard_physical_line_and_pointer(self):
        config = {"views": [{"cards": [{"entity": OLD}]}]}
        disk = json.dumps({"version": 1, "data": {"config": config}}, indent=2).encode()
        document = engine.scan_dashboard("main", "Main", config, OLD, ".storage/lovelace.x", disk)
        item = document.occurrences[0]
        self.assertIsNotNone(item.line)
        self.assertEqual(engine.pointer(item.path), "/views/0/cards/0/entity")
        self.assertIn(OLD, disk.decode().splitlines()[item.line - 1])

    def test_dashboard_stale_disk_uses_pointer(self):
        document = engine.scan_dashboard("main", "Main", {"entity": OLD}, OLD, ".storage/lovelace.x", b'{"data":{"config":{"entity":"sensor.different"}}}')
        self.assertIsNone(document.occurrences[0].line)

    def test_dashboard_duplicate_key_blocked(self):
        document = engine.scan_dashboard("x", "X", {OLD: {}, NEW: {}}, OLD, None)
        with self.assertRaises(engine.ReplacementError):
            engine.build_candidate(document, {item.id for item in document.occurrences}, OLD, NEW)

    def test_dashboard_multiple_identical_strings_separate_selection(self):
        document = engine.scan_dashboard("x", "X", {"entity": OLD, "template": OLD + " " + OLD}, OLD, None)
        item = next(item for item in document.occurrences if item.path == ("template",) and item.start > 0)
        result = engine.build_candidate(document, {item.id}, OLD, NEW)
        self.assertEqual(result, {"entity": OLD, "template": OLD + " " + NEW})

    def test_json_escaped_id_is_reported(self):
        document = engine.scan_report(".storage/helper", b'{"entity":"sensor.\\u006fld"}', OLD)
        self.assertTrue(document.occurrences)
        self.assertFalse(document.occurrences[0].writable)

    def test_json_mixed_literal_and_escaped_ids_both_reported(self):
        document = engine.scan_report(".storage/helper", b'{"first":"sensor.old","second":"sensor.\\u006fld"}', OLD)
        self.assertEqual(len(document.occurrences), 2)


class FileTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def test_discovery_covers_yaml_packages_and_manual_storage(self):
        self.write("configuration.yaml", b"entity: sensor.old\n")
        self.write("packages/lights.yaml", b"entity: sensor.old\n")
        self.write("www/custom.js", b"hass.states['sensor.old']")
        self.write(".storage/core.config_entries", b'{"options":{"entity":"sensor.old"}}')
        self.write(".storage/person", b'{"device_trackers":["sensor.old"]}')
        self.write("secrets.yaml", b"password: sensor.old\n")
        self.write("custom_components/vendor/fixture.yaml", b"entity: sensor.old\n")
        self.write(".storage/auth", b'"sensor.old"')
        self.write(".storage/core.restore_state", b'"sensor.old"')
        documents, warnings, count = engine.scan_files(self.root, OLD)
        self.assertFalse(warnings)
        self.assertEqual(count, 5)
        self.assertEqual({doc.kind for doc in documents}, {"yaml", "report"})
        self.assertTrue(all(not item.writable for doc in documents if doc.kind == "report" for item in doc.occurrences))

    def test_disabled_scan_options(self):
        self.write("scripts.yaml", b"entity: sensor.old")
        self.write("other.js", b"sensor.old")
        self.write(".storage/helper", b'"sensor.old"')
        documents, _, count = engine.scan_files(self.root, OLD, include_text=False, include_storage=False)
        self.assertEqual(count, 1)
        self.assertEqual(documents[0].kind, "yaml")

    def test_symlink_and_path_traversal_refused(self):
        path = self.write("safe.yaml", b"sensor.old")
        (self.root / "alias.yaml").symlink_to(path)
        for name in ("../elsewhere.yaml", str(path), "alias.yaml"):
            with self.assertRaises(engine.ReplacementError):
                engine.read_file(self.root, name)
        _, warnings, _ = engine.scan_files(self.root, OLD)
        self.assertTrue(any("Symbolic" in item for item in warnings))

    def test_stale_hash_blocks_write(self):
        path = self.write("scripts.yaml", b"before")
        expected = engine.digest(path.read_bytes())
        path.write_bytes(b"newer edit")
        with self.assertRaises(engine.ReplacementError):
            engine.replace_file(self.root, "scripts.yaml", expected, b"replacement")
        self.assertEqual(path.read_bytes(), b"newer edit")

    def test_atomic_write_preserves_mode_and_bytes(self):
        path = self.write("scripts.yaml", b"entity: sensor.old\r\n")
        path.chmod(0o640)
        engine.replace_file(self.root, "scripts.yaml", engine.digest(path.read_bytes()), b"entity: sensor.new\r\n")
        self.assertEqual(path.read_bytes(), b"entity: sensor.new\r\n")
        self.assertEqual(path.stat().st_mode & 0o777, 0o640)
        self.assertFalse(list(self.root.glob(".entity-replacer-*")))

    def test_hard_link_refused(self):
        path = self.write("scripts.yaml", b"before")
        os.link(path, self.root / "other.yaml")
        with self.assertRaises(engine.ReplacementError):
            engine.replace_file(self.root, "scripts.yaml", engine.digest(b"before"), b"after")

    def test_backup_roundtrip_checksum_and_history(self):
        backups = engine.BackupStore(self.root)
        manifest = backups.create(OLD, NEW, [{"kind": "yaml", "relative": "scripts.yaml", "label": "Scripts", "before": b"before", "after": b"after"}])
        loaded = backups.load(manifest["job_id"])
        record = loaded["items"][0]
        self.assertEqual(backups.payload(loaded, record, "before"), b"before")
        self.assertEqual(backups.history()[0]["job_id"], manifest["job_id"])
        before_path = self.root / engine.BACKUP_DIR / manifest["job_id"] / record["before_file"]
        self.assertEqual(before_path.stat().st_mode & 0o777, 0o600)
        before_path.write_bytes(b"corruption")
        with self.assertRaises(engine.ReplacementError):
            backups.payload(loaded, record, "before")

    def test_backup_traversal_and_unsafe_targets_refused(self):
        backups = engine.BackupStore(self.root)
        with self.assertRaises(engine.ReplacementError):
            backups.load("../../auth")
        manifest = backups.create(OLD, NEW, [{"kind": "yaml", "relative": "secrets.yaml", "label": "Secrets", "before": b"before", "after": b"after"}])
        with self.assertRaises(engine.ReplacementError):
            backups.load(manifest["job_id"])


if __name__ == "__main__":
    unittest.main()
