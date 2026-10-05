"""Transaction tests with fake HA states/registry and the real file engine.

These verify control flow, conflict handling, and cache-aware dashboard saves;
they do not pretend to be a live Home Assistant compatibility test.
"""

import asyncio
import copy
import importlib
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components/entity_replacer"


def module(name, **attributes):
    result = types.ModuleType(name)
    result.__dict__.update(attributes)
    sys.modules[name] = result
    return result


module("homeassistant", __path__=[])
module("homeassistant.core", HomeAssistant=object)
module("homeassistant.components", __path__=[])
module("homeassistant.components.lovelace", __path__=[])
module("homeassistant.components.lovelace.const", LOVELACE_DATA="lovelace")
module("homeassistant.helpers", __path__=[])
module("homeassistant.helpers.entity_registry", async_get=lambda hass: hass.registry)
module("er_test_package", __path__=[str(COMPONENT)])
manager_module = importlib.import_module("er_test_package.manager")
engine = importlib.import_module("er_test_package.engine")

OLD = "sensor.old"
NEW = "sensor.new"


class FakeStates:
    def __init__(self):
        self.values = {
            OLD: types.SimpleNamespace(entity_id=OLD, state="unavailable", attributes={"friendly_name": "Old Sensor"}),
            NEW: types.SimpleNamespace(entity_id=NEW, state="12", attributes={"friendly_name": "New Sensor"}),
        }

    def get(self, entity_id):
        return self.values.get(entity_id)

    def async_all(self):
        return list(self.values.values())


class FakeRegistry:
    def __init__(self):
        self.entities = {
            OLD: types.SimpleNamespace(entity_id=OLD, disabled_by=None, device_id="old-device", name=None, original_name="Old Sensor"),
            NEW: types.SimpleNamespace(entity_id=NEW, disabled_by=None, device_id="new-device", name=None, original_name="New Sensor"),
        }

    def async_get(self, entity_id):
        return self.entities.get(entity_id)


class FakeDashboard:
    mode = "storage"

    def __init__(self, root):
        self.value = {"views": [{"cards": [{"entity": OLD}]}]}
        self.calls = []
        self.fail_after_cache = False
        self.silent_failure = False
        self._store = types.SimpleNamespace(path=str(root / ".storage/lovelace.test-id"))
        Path(self._store.path).parent.mkdir(exist_ok=True)
        self.persist()

    def persist(self):
        Path(self._store.path).write_text(json.dumps({"version": 1, "data": {"config": self.value}}, indent=2), encoding="utf-8")

    async def async_load(self, force):
        return self.value

    async def async_save(self, config):
        self.value = config
        self.calls.append(copy.deepcopy(config))
        if self.fail_after_cache:
            self.fail_after_cache = False
            raise OSError("simulated dashboard disk failure after updating cache")
        if self.silent_failure:
            self.silent_failure = False
            return
        self.persist()


class FakeHass:
    def __init__(self, root):
        self.config = types.SimpleNamespace(config_dir=str(root))
        self.states = FakeStates()
        self.registry = FakeRegistry()
        self.dashboard = FakeDashboard(root)
        self.data = {"lovelace": types.SimpleNamespace(dashboards={"main": self.dashboard})}

    async def async_add_executor_job(self, function, *args):
        return await asyncio.to_thread(function, *args)


class ManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.yaml = self.root / "automations.yaml"
        self.before = b"- alias: Test\r\n  triggers:\r\n    - trigger: state\r\n      entity_id: sensor.old\r\n  actions: []\r\n"
        self.yaml.write_bytes(self.before)
        self.hass = FakeHass(self.root)
        self.manager = manager_module.EntityReplacer(self.hass)

    async def asyncTearDown(self):
        self.temporary.cleanup()

    async def scan(self):
        return await self.manager.scan("owner", OLD, NEW)

    def selected(self, preview):
        return [item["id"] for document in preview["documents"] for item in document["occurrences"] if item["writable"]]

    async def apply(self, preview=None, selected=None):
        preview = preview or await self.scan()
        selected = selected if selected is not None else self.selected(preview)
        await self.manager.review("owner", preview["preview_id"], selected)
        return await self.manager.apply("owner", preview["preview_id"], selected)

    async def test_scan_includes_unavailable_source_and_excludes_duplicate_storage(self):
        preview = await self.scan()
        self.assertEqual(preview["dashboards_scanned"], 1)
        self.assertEqual(len(self.selected(preview)), 2)
        self.assertFalse(any(document["kind"] == "report" for document in preview["documents"]))
        dashboard = next(document for document in preview["documents"] if document["kind"] == "dashboard")
        self.assertIsNotNone(dashboard["occurrences"][0]["line"])

    async def test_apply_and_restore_yaml_and_live_dashboard(self):
        result = await self.apply()
        self.assertEqual(result["references_changed"], 2)
        self.assertIn(b"sensor.new", self.yaml.read_bytes())
        self.assertEqual(self.hass.dashboard.value["views"][0]["cards"][0]["entity"], NEW)
        self.assertTrue(result["reload_required"])
        restored = await self.manager.restore(result["job_id"])
        self.assertEqual(restored["documents_restored"], 2)
        self.assertEqual(self.yaml.read_bytes(), self.before)
        self.assertEqual(self.hass.dashboard.value["views"][0]["cards"][0]["entity"], OLD)

    async def test_removed_source_allowed(self):
        self.hass.registry.entities.pop(OLD)
        self.hass.states.values.pop(OLD)
        preview = await self.scan()
        self.assertEqual(len(self.selected(preview)), 2)

    async def test_available_source_allowed(self):
        self.hass.states.values[OLD].state = "1"
        result = await self.apply()
        self.assertEqual(result["references_changed"], 2)

    async def test_unavailable_target_requires_explicit_option(self):
        self.hass.states.values[NEW].state = "unavailable"
        with self.assertRaises(engine.ReplacementError):
            await self.scan()
        preview = await self.manager.scan("owner", OLD, NEW, allow_unavailable_target=True)
        self.assertTrue(any("unavailable" in item for item in preview["warnings"]))
        result = await self.apply(preview)
        self.assertEqual(result["references_changed"], 2)

    async def test_unknown_target_rejected(self):
        self.hass.registry.entities.pop(NEW)
        self.hass.states.values.pop(NEW)
        with self.assertRaises(engine.ReplacementError):
            await self.scan()

    async def test_disabled_target_rejected(self):
        self.hass.registry.entities[NEW].disabled_by = "user"
        with self.assertRaises(engine.ReplacementError):
            await self.scan()

    async def test_cross_domain_rejected(self):
        with self.assertRaises(engine.ReplacementError):
            await self.manager.scan("owner", OLD, "switch.new")

    async def test_review_required_and_selection_change_invalidates_it(self):
        preview = await self.scan()
        selected = self.selected(preview)
        with self.assertRaises(engine.ReplacementError):
            await self.manager.apply("owner", preview["preview_id"], selected)
        await self.manager.review("owner", preview["preview_id"], selected)
        with self.assertRaises(engine.ReplacementError):
            await self.manager.apply("owner", preview["preview_id"], selected[:1])
        self.assertEqual(self.yaml.read_bytes(), self.before)

    async def test_partial_selection_only_changes_reviewed_reference(self):
        preview = await self.scan()
        yaml_document = next(document for document in preview["documents"] if document["kind"] == "yaml")
        selected = [yaml_document["occurrences"][0]["id"]]
        reviewed = await self.manager.review("owner", preview["preview_id"], selected)
        self.assertEqual(reviewed["documents"], 1)
        self.assertIn("sensor.new", reviewed["diffs"][0]["diff"])
        result = await self.manager.apply("owner", preview["preview_id"], selected)
        self.assertEqual(result["references_changed"], 1)
        self.assertEqual(self.hass.dashboard.value["views"][0]["cards"][0]["entity"], OLD)

    async def test_owner_and_expiry_checks(self):
        preview = await self.scan()
        with self.assertRaises(engine.ReplacementError):
            await self.manager.review("someone-else", preview["preview_id"], self.selected(preview))
        self.manager.previews[preview["preview_id"]].created -= 901
        with self.assertRaises(engine.ReplacementError):
            await self.manager.review("owner", preview["preview_id"], self.selected(preview))

    async def test_stale_yaml_refuses_everything_before_backup(self):
        preview = await self.scan()
        self.yaml.write_bytes(b"newer edit")
        with self.assertRaises(engine.ReplacementError):
            await self.apply(preview)
        self.assertEqual(self.hass.dashboard.calls, [])
        self.assertFalse((self.root / engine.BACKUP_DIR).exists())

    async def test_stale_dashboard_does_not_mutate_snapshot(self):
        preview = await self.scan()
        self.hass.dashboard.value["views"][0]["cards"][0]["entity"] = "sensor.different"
        with self.assertRaises(engine.ReplacementError):
            await self.apply(preview)
        self.assertEqual(self.yaml.read_bytes(), self.before)

    async def test_target_checked_again_before_apply(self):
        preview = await self.scan()
        selected = self.selected(preview)
        await self.manager.review("owner", preview["preview_id"], selected)
        self.hass.states.values[NEW].state = "unavailable"
        with self.assertRaises(engine.ReplacementError):
            await self.manager.apply("owner", preview["preview_id"], selected)
        self.assertEqual(self.yaml.read_bytes(), self.before)

    async def test_failure_on_second_write_rolls_back_first(self):
        original_write = self.manager._write
        calls = 0

        async def failing_write(record, expected, payload):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated write failure")
            await original_write(record, expected, payload)

        with patch.object(self.manager, "_write", failing_write):
            with self.assertRaises(engine.ReplacementError):
                await self.apply()
        self.assertEqual(self.yaml.read_bytes(), self.before)
        self.assertEqual(self.manager.backups.history()[0]["status"], "rolled_back")

    async def test_native_dashboard_failure_after_cache_change_is_rolled_back(self):
        self.hass.dashboard.fail_after_cache = True
        with self.assertRaises(engine.ReplacementError):
            await self.apply()
        self.assertEqual(self.yaml.read_bytes(), self.before)
        self.assertEqual(self.hass.dashboard.value["views"][0]["cards"][0]["entity"], OLD)
        disk = json.loads(Path(self.hass.dashboard._store.path).read_text())
        self.assertEqual(disk["data"]["config"]["views"][0]["cards"][0]["entity"], OLD)

    async def test_silent_dashboard_save_failure_is_detected_and_rolled_back(self):
        self.hass.dashboard.silent_failure = True
        with self.assertRaises(engine.ReplacementError):
            await self.apply()
        self.assertEqual(self.yaml.read_bytes(), self.before)
        self.assertEqual(self.hass.dashboard.value["views"][0]["cards"][0]["entity"], OLD)
        self.assertEqual(self.manager.backups.history()[0]["status"], "rolled_back")

    async def test_dashboard_storage_symlink_refused_before_writing(self):
        store_path = Path(self.hass.dashboard._store.path)
        destination = self.root / "other.json"
        destination.write_bytes(store_path.read_bytes())
        store_path.unlink()
        store_path.symlink_to(destination)
        preview = await self.scan()
        with self.assertRaises(engine.ReplacementError):
            await self.apply(preview)
        self.assertEqual(self.yaml.read_bytes(), self.before)
        self.assertEqual(self.hass.dashboard.calls, [])

    async def test_missing_storage_path_is_manual_review_only(self):
        self.hass.dashboard._store = None
        preview = await self.scan()
        document = next(document for document in preview["documents"] if document["kind"] == "dashboard")
        self.assertFalse(any(item["writable"] for item in document["occurrences"]))

    async def test_rollback_preserves_a_newer_external_edit(self):
        original_write = self.manager._write
        calls = 0

        async def failing_write(record, expected, payload):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.yaml.write_bytes(b"newer external edit")
                raise OSError("simulated failure")
            await original_write(record, expected, payload)

        with patch.object(self.manager, "_write", failing_write):
            with self.assertRaises(engine.ReplacementError):
                await self.apply()
        self.assertEqual(self.yaml.read_bytes(), b"newer external edit")
        self.assertEqual(self.manager.backups.history()[0]["status"], "rollback_incomplete")

    async def test_restore_refuses_newer_edits_before_restoring_anything(self):
        result = await self.apply()
        self.yaml.write_bytes(b"user edit after replacement")
        with self.assertRaises(engine.ReplacementError):
            await self.manager.restore(result["job_id"])
        self.assertEqual(self.hass.dashboard.value["views"][0]["cards"][0]["entity"], NEW)
        self.assertEqual(self.yaml.read_bytes(), b"user edit after replacement")

    async def test_restore_checks_all_backup_payloads_before_writing(self):
        result = await self.apply()
        manifest = self.manager.backups.load(result["job_id"])
        record = manifest["items"][-1]
        path = self.root / engine.BACKUP_DIR / result["job_id"] / record["before_file"]
        path.write_bytes(b"bad backup")
        after = self.yaml.read_bytes()
        with self.assertRaises(engine.ReplacementError):
            await self.manager.restore(result["job_id"])
        self.assertEqual(self.yaml.read_bytes(), after)

    async def test_restore_recovers_interrupted_pending_job(self):
        second = self.root / "scripts.yaml"
        second.write_bytes(b"entity: sensor.old\n")
        first_after = self.before.replace(b"sensor.old", b"sensor.new")
        manifest = self.manager.backups.create(OLD, NEW, [
            {"kind": "yaml", "relative": "automations.yaml", "label": "Automations", "before": self.before, "after": first_after},
            {"kind": "yaml", "relative": "scripts.yaml", "label": "Scripts", "before": second.read_bytes(), "after": b"entity: sensor.new\n"},
        ])
        engine.replace_file(self.root, "automations.yaml", engine.digest(self.before), first_after)
        result = await self.manager.restore(manifest["job_id"])
        self.assertEqual(result["documents_restored"], 1)
        self.assertEqual(self.yaml.read_bytes(), self.before)
        self.assertEqual(second.read_bytes(), b"entity: sensor.old\n")

    async def test_backups_survive_new_manager_instance(self):
        result = await self.apply()
        new_manager = manager_module.EntityReplacer(self.hass)
        status = await new_manager.status()
        self.assertEqual(status["history"][0]["job_id"], result["job_id"])
        await new_manager.restore(result["job_id"])
        self.assertEqual(self.yaml.read_bytes(), self.before)

    async def test_ui_helper_references_remain_manual(self):
        helper = self.root / ".storage/core.config_entries"
        helper.write_text(json.dumps({"data": {"options": {"entity_id": OLD}}}))
        preview = await self.scan()
        document = next(document for document in preview["documents"] if document["kind"] == "report")
        item = document["occurrences"][0]
        self.assertFalse(item["writable"])
        with self.assertRaises(engine.ReplacementError):
            await self.manager.review("owner", preview["preview_id"], [item["id"]])

    async def test_preview_limit(self):
        previews = [await self.scan() for _ in range(4)]
        self.assertEqual(len(self.manager.previews), 3)
        self.assertNotIn(previews[0]["preview_id"], self.manager.previews)

    async def test_changed_configuration_after_review_blocks_apply(self):
        preview = await self.scan()
        selected = self.selected(preview)
        await self.manager.review("owner", preview["preview_id"], selected)
        self.yaml.write_bytes(b"edit after review")
        with self.assertRaises(engine.ReplacementError):
            await self.manager.apply("owner", preview["preview_id"], selected)
        self.assertEqual(self.hass.dashboard.calls, [])

    async def test_unknown_selection_refused(self):
        preview = await self.scan()
        with self.assertRaises(engine.ReplacementError):
            await self.manager.review("owner", preview["preview_id"], ["invented-id"])

    async def test_unit_and_feature_mismatches_reported(self):
        self.hass.states.values[OLD].attributes.update(unit_of_measurement="W", supported_features=3)
        self.hass.states.values[NEW].attributes.update(unit_of_measurement="kW", supported_features=1)
        preview = await self.scan()
        self.assertTrue(any("unit_of_measurement" in item for item in preview["warnings"]))
        self.assertTrue(any("fewer actions" in item for item in preview["warnings"]))


if __name__ == "__main__":
    unittest.main()
