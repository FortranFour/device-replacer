"""Statistics control-flow tests. Real recorder coverage lives in ha_tests/."""

import asyncio
import copy
import time
import types
import unittest
from unittest.mock import patch

import test_manager as fixtures
from test_manager import OLD, NEW, manager_module, engine


class MemoryRecorder:
    def __init__(self):
        self.meta = {OLD: (7, {"statistic_id": OLD, "source": "recorder", "has_sum": False,
                              "mean_type": 1, "unit_of_measurement": "°C", "unit_class": "temperature", "name": None})}
        self.rows = {"hourly": [{"start": 1000.0, "mean": 20.0}], "five_minute": []}
        self.calls = 0
        self.outcome = "success"

    async def metadata(self, source, target):
        return copy.deepcopy(self.meta)

    async def latest(self, source):
        return copy.deepcopy(self.rows)

    async def rename(self, source, target):
        self.calls += 1
        if self.outcome == "timeout":
            raise TimeoutError()
        if self.outcome == "cancel":
            raise asyncio.CancelledError()
        if self.outcome == "silent_refusal":
            return
        id_, meta = self.meta.pop(source)
        meta["statistic_id"] = target
        self.meta[target] = (id_, meta)


# Reuse fixtures, not the unrelated inherited test methods.
class StatisticsTests(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = fixtures.ManagerTests.asyncTearDown

    async def asyncSetUp(self):
        await fixtures.ManagerTests.asyncSetUp(self)
        for index, name in enumerate((OLD, NEW)):
            self.hass.states.values.pop(name)
            entry = self.hass.registry.entities[name]
            entry.id = str(index) * 32
            entry.unique_id = f"unique-{index}"
            entry.platform = "test"
            entry.disabled_by = "user"
            entry.original_device_class = "temperature"
            entry.capabilities = {"state_class": "measurement"}
            entry.unit_of_measurement = "°C"
        self.recorder = MemoryRecorder()
        self.manager._statistics_adapter = lambda: self.recorder

    async def preview(self):
        return await self.manager.statistics_preview("owner", OLD, NEW)

    async def transfer(self, preview=None):
        preview = preview or await self.preview()
        return await self.manager.statistics_apply("owner", preview["preview_id"], True)

    async def test_verified_transfer_keeps_metadata_id_and_configuration_unchanged(self):
        before = self.yaml.read_bytes()
        preview = await self.preview()
        self.assertTrue(preview["eligible"])
        self.assertEqual(self.recorder.calls, 0)
        result = await self.transfer(preview)
        self.assertEqual(result["status"], "statistics_transferred")
        self.assertEqual(self.recorder.meta[NEW][0], 7)
        self.assertEqual(self.yaml.read_bytes(), before)
        self.assertEqual(self.hass.dashboard.calls, [])
        with self.assertRaisesRegex(engine.ReplacementError, "not configuration backups"):
            await self.manager.restore(result["job_id"])
        with self.assertRaises(engine.ReplacementError):
            await self.transfer(preview)

    async def test_existing_destination_including_empty_metadata_is_blocked(self):
        self.recorder.meta[NEW] = (8, dict(self.recorder.meta[OLD][1], statistic_id=NEW))
        self.assertIn("already has", (await self.preview())["reason"])
        self.assertEqual(self.recorder.calls, 0)

    async def test_source_missing_external_sum_circular_units_and_no_rows_are_blocked(self):
        original = copy.deepcopy(self.recorder.meta)
        for changes in ({"source": "external"}, {"has_sum": True}, {"mean_type": 2}, {"unit_of_measurement": "°F"}):
            self.recorder.meta = copy.deepcopy(original)
            self.recorder.meta[OLD][1].update(changes)
            self.assertFalse((await self.preview())["eligible"])
        self.recorder.meta = {}
        self.assertIn("No stored", (await self.preview())["reason"])
        self.recorder.meta = original
        self.recorder.rows = {"hourly": [], "five_minute": []}
        self.assertFalse((await self.preview())["eligible"])

    async def test_disabled_unloaded_known_compatible_measurements_required(self):
        for name in (OLD, NEW):
            entry = self.hass.registry.entities[name]
            entry.disabled_by = None
            self.assertIn("Disable", (await self.preview())["reason"])
            entry.disabled_by = "user"
            self.hass.states.values[name] = types.SimpleNamespace(state="unavailable")
            self.assertFalse((await self.preview())["eligible"])
            self.hass.states.values.pop(name)
        for attribute, bad in (("unit_of_measurement", "°F"), ("original_device_class", "humidity"), ("capabilities", {"state_class": "total_increasing"})):
            entry = self.hass.registry.entities[NEW]
            original = getattr(entry, attribute)
            setattr(entry, attribute, bad)
            self.assertFalse((await self.preview())["eligible"])
            setattr(entry, attribute, original)
        self.hass.registry.entities.pop(OLD)
        self.assertFalse((await self.preview())["eligible"])

    async def test_owner_expiry_and_explicit_confirmation(self):
        preview = await self.preview()
        token = preview["preview_id"]
        with self.assertRaises(engine.ReplacementError):
            await self.manager.statistics_apply("other", token, True)
        with self.assertRaises(engine.ReplacementError):
            await self.manager.statistics_apply("owner", token, False)
        self.manager.statistics_previews[token]["created"] = time.monotonic() - 10000
        with self.assertRaises(engine.ReplacementError):
            await self.transfer(preview)
        self.assertEqual(self.recorder.calls, 0)

    async def test_changed_registry_or_metadata_and_destination_race_refuse_before_write(self):
        preview = await self.preview()
        self.hass.registry.entities[NEW].unique_id = "changed"
        with self.assertRaisesRegex(engine.ReplacementError, "changed"):
            await self.transfer(preview)
        preview = await self.preview()
        self.recorder.meta[OLD][1]["name"] = "changed"
        with self.assertRaisesRegex(engine.ReplacementError, "changed"):
            await self.transfer(preview)
        preview = await self.preview()
        self.recorder.meta[NEW] = (8, {})
        with self.assertRaisesRegex(engine.ReplacementError, "already has"):
            await self.transfer(preview)
        self.assertEqual(self.recorder.calls, 0)

    async def test_silent_refusal_timeout_and_cancel_never_report_success_or_allow_retry(self):
        for outcome in ("silent_refusal", "timeout", "cancel"):
            with self.subTest(outcome=outcome):
                self.recorder.outcome = outcome
                preview = await self.preview()
                expected = asyncio.CancelledError if outcome == "cancel" else engine.ReplacementError
                with self.assertRaises(expected):
                    await self.transfer(preview)
                manifest = self.manager.backups.load(self.manager.backups.statistics_conflicts(OLD, NEW)[0])
                self.assertEqual(manifest["status"], "statistics_outcome_unknown")
                restarted = manager_module.EntityReplacer(self.hass)
                restarted._statistics_adapter = lambda: self.recorder
                blocked = await restarted.statistics_preview("owner", OLD, NEW)
                self.assertIn("unresolved", blocked["reason"])
                # Only isolate the next subtest; production never clears this automatically.
                manifest["status"] = "statistics_not_queued"
                self.manager.backups.save(manifest)

    async def test_pending_audit_and_corrupt_audit_fail_closed(self):
        manifest = self.manager.backups.create(OLD, NEW, [])
        manifest.update(mode="statistics", status="statistics_applying")
        self.manager.backups.save(manifest)
        self.assertIn("unresolved", (await self.preview())["reason"])
        path = self.manager.backups._job(manifest["job_id"]) / "manifest.json"
        path.write_text("broken")
        self.assertIn("Cannot verify audit", (await self.preview())["reason"])

    async def test_no_manifest_write_means_no_recorder_write(self):
        preview = await self.preview()
        with patch.object(self.manager.backups, "save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                await self.transfer(preview)
        self.assertEqual(self.recorder.calls, 0)

    async def test_only_sensors_and_distinct_pairs(self):
        for old, new in ((OLD, OLD), ("switch.old", "switch.new"), (OLD, "switch.new")):
            self.assertFalse((await self.manager.statistics_preview("owner", old, new))["eligible"])

    async def test_late_completion_can_be_verified_without_retrying(self):
        self.recorder.outcome = "timeout"
        with self.assertRaises(engine.ReplacementError):
            await self.transfer()
        job_id = self.manager.backups.statistics_conflicts(OLD, NEW)[0]
        unresolved = await self.manager.statistics_verify(job_id)
        self.assertFalse(unresolved["verified"])
        self.assertEqual(self.recorder.calls, 1)
        # Simulate the already queued recorder job completing after timeout.
        self.recorder.outcome = "success"
        await self.recorder.rename(OLD, NEW)
        calls = self.recorder.calls
        result = await self.manager.statistics_verify(job_id)
        self.assertTrue(result["verified"])
        self.assertEqual(self.recorder.calls, calls)
        self.assertEqual(self.manager.backups.statistics_conflicts(OLD, NEW), [])
