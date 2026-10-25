"""ID reuse safety and failure tests; recorder behavior is covered in ha_tests."""
import copy
import asyncio
import time
import types
import unittest
from unittest.mock import patch

import test_statistics as fixtures
from test_manager import OLD, NEW, manager_module, engine


class MemoryHistory:
    def __init__(self, recorder):
        self.data = {"states": {OLD: 1, NEW: 2}, "statistics": copy.deepcopy(recorder.meta)}
    async def flush(self):
        pass
    async def snapshot(self, ids):
        return {kind: {key: copy.deepcopy(value) for key, value in values.items() if key in ids}
                for kind, values in self.data.items()}
    def rename(self, source, target):
        for kind in self.data:
            if target not in self.data[kind] and source in self.data[kind]:
                value = self.data[kind].pop(source)
                if kind == "statistics":
                    value = (value[0], dict(value[1], statistic_id=target))
                self.data[kind][target] = value


class IdentityTests(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = fixtures.StatisticsTests.asyncTearDown
    async def asyncSetUp(self):
        await fixtures.StatisticsTests.asyncSetUp(self)
        self.hass.registry.entities[OLD].device_id = "a" * 32
        self.hass.registry.entities[NEW].device_id = "b" * 32
        self.adapter = MemoryHistory(self.recorder)
        self.manager._identity_adapter = lambda: self.adapter
        self.operations = []
        registry = self.hass.registry
        def remove(entity_id):
            self.operations.append(("remove", entity_id))
            registry.entities.pop(entity_id)
        def update(entity_id, **kwargs):
            self.operations.append(("update", entity_id, kwargs))
            entry = registry.entities[entity_id]
            if "disabled_by" in kwargs:
                entry.disabled_by = kwargs["disabled_by"]
            if "new_entity_id" in kwargs:
                target = kwargs["new_entity_id"]
                registry.entities.pop(entity_id)
                entry.entity_id = target
                entry.name = kwargs.get("name")
                registry.entities[target] = entry
                self.adapter.rename(entity_id, target)
            return entry
        registry.async_remove = remove
        registry.async_update_entity = update
        self.mapping = [{"source": OLD, "target": NEW}]
    async def preview(self):
        return await self.manager.reuse_preview("owner", self.mapping)
    async def apply(self, preview):
        return await self.manager.reuse_apply("owner", preview["preview_id"], True)

    async def test_retire_then_rename_preserves_old_history_and_new_identity(self):
        before = copy.deepcopy(self.adapter.data)
        preview = await self.preview()
        self.assertTrue(preview["ready"])
        replacement = self.hass.registry.entities[NEW]
        result = await self.apply(preview)
        self.assertEqual(self.operations[0], ("remove", OLD))
        self.assertEqual(self.operations[1][1], NEW)
        self.assertIs(self.hass.registry.entities[OLD], replacement)
        self.assertEqual(self.adapter.data, before)
        self.assertEqual(result["job_id"], self.manager.backups.history()[0]["job_id"])
        with self.assertRaisesRegex(engine.ReplacementError, "not configuration backups"):
            await self.manager.restore(result["job_id"])
        with self.assertRaises(engine.ReplacementError):
            await self.apply(preview)

    async def test_owner_expiry_confirmation_and_loaded_entities(self):
        preview = await self.preview()
        with self.assertRaises(engine.ReplacementError):
            await self.manager.reuse_apply("other", preview["preview_id"], True)
        with self.assertRaises(engine.ReplacementError):
            await self.manager.reuse_apply("owner", preview["preview_id"], False)
        self.hass.states.values[NEW] = types.SimpleNamespace(state="20")
        with self.assertRaisesRegex(engine.ReplacementError, "unloading"):
            await self.apply(preview)
        self.manager.identity_previews[preview["preview_id"]]["created"] -= 10000
        with self.assertRaises(engine.ReplacementError):
            await self.apply(preview)
        self.assertEqual(self.operations, [])

    async def test_disable_then_fresh_review(self):
        for name in (OLD, NEW):
            self.hass.registry.entities[name].disabled_by = None
            self.hass.states.values[name] = types.SimpleNamespace(state="20")
        preview = await self.preview()
        self.assertTrue(preview["can_disable"])
        waiting = await self.manager.reuse_disable("owner", preview["preview_id"], True)
        self.assertFalse(waiting["ready"])
        self.assertIsNotNone(self.hass.states.get(OLD))
        self.hass.states.values.clear()
        ready = await self.preview()
        self.assertTrue(ready["ready"])
        await self.apply(ready)

    async def test_changed_registry_and_history_refuse_before_write(self):
        preview = await self.preview()
        self.adapter.data["states"][OLD] = 999
        with self.assertRaisesRegex(engine.ReplacementError, "changed"):
            await self.apply(preview)
        preview = await self.preview()
        self.hass.registry.entities[NEW].unique_id = "different"
        with self.assertRaisesRegex(engine.ReplacementError, "changed"):
            await self.apply(preview)
        self.assertEqual(self.operations, [])

    async def test_partial_failure_audited_and_blocked_after_restart(self):
        preview = await self.preview()
        def fail(*args, **kwargs):
            raise RuntimeError("rename failed")
        self.hass.registry.async_update_entity = fail
        with self.assertRaisesRegex(engine.ReplacementError, "manual recovery"):
            await self.apply(preview)
        restarted = manager_module.EntityReplacer(self.hass)
        restarted._identity_adapter = lambda: self.adapter
        with self.assertRaisesRegex(engine.ReplacementError, "unresolved"):
            await restarted.reuse_preview("owner", self.mapping)
        self.assertNotIn(OLD, self.hass.registry.entities)

    async def test_counters_incompatible_roles_cycles_and_duplicate_targets(self):
        self.hass.registry.entities[NEW].capabilities["state_class"] = "total_increasing"
        with self.assertRaisesRegex(engine.ReplacementError, "Cumulative"):
            await self.preview()
        self.hass.registry.entities[NEW].capabilities["state_class"] = "measurement"
        for rows in ([*self.mapping, *self.mapping], [{"source": OLD, "target": OLD}], []):
            with self.assertRaises(engine.ReplacementError):
                await self.manager.reuse_preview("owner", rows)
        self.assertEqual(self.operations, [])

    async def test_removed_source_needs_history_evidence(self):
        self.hass.registry.entities.pop(OLD)
        ready = await self.preview()
        self.assertTrue(ready["ready"])
        self.adapter.data = {"states": {}, "statistics": {}}
        with self.assertRaisesRegex(engine.ReplacementError, "retained history"):
            await self.preview()

    async def test_replacement_references_are_reported_without_editing_configuration(self):
        self.yaml.write_text("entity_id: sensor.new\n")
        preview = await self.preview()
        self.assertGreaterEqual(preview["manual_reference_count"], 1)
        await self.apply(preview)
        self.assertEqual(self.yaml.read_text(), "entity_id: sensor.new\n")

    async def test_no_audit_write_means_no_registry_write(self):
        preview = await self.preview()
        with patch.object(self.manager.backups, "save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                await self.apply(preview)
        self.assertEqual(self.operations, [])

    async def test_cancel_after_registry_changes_is_persistently_blocked(self):
        preview = await self.preview()
        async def flush():
            if self.operations:
                raise asyncio.CancelledError()
        self.adapter.flush = flush
        with self.assertRaises(asyncio.CancelledError):
            await self.apply(preview)
        self.assertTrue(self.manager.backups.statistics_conflicts(OLD, NEW))
        self.assertNotIn(preview["preview_id"], self.manager.identity_previews)
