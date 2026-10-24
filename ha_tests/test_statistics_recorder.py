"""Real HA 2026.3 recorder tests; run separately from stub-based unit tests.

  /path/to/ha-venv/bin/python -m unittest discover -s ha_tests -v
"""

import asyncio
from datetime import datetime, timedelta, timezone
import importlib
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant import loader, config_entries
from homeassistant.setup import async_setup_component
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.recorder import async_initialize_recorder
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import (
    async_import_statistics, get_last_statistics, get_last_short_term_statistics,
)
from homeassistant.components.recorder.db_schema import Statistics, StatisticsShortTerm, States
from homeassistant.components.recorder.util import session_scope
from homeassistant.components.recorder.tasks import AddRecorderPlatformTask, StatisticsTask, SynchronizeTask
from homeassistant.components.sensor import recorder as sensor_recorder
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType("er_live")
package.__path__ = [str(ROOT / "custom_components/entity_replacer")]
sys.modules["er_live"] = package
manager_module = importlib.import_module("er_live.manager")
statistics_module = importlib.import_module("er_live.statistics")

OLD, NEW = "sensor.old", "sensor.new"


class RealRecorderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.hass = HomeAssistant(self.tmp.name)
        self.addAsyncCleanup(self.hass.async_stop)
        self.hass.config.time_zone = "UTC"
        loader.async_setup(self.hass)
        self.hass.config_entries = config_entries.ConfigEntries(self.hass, {})
        await self.hass.config_entries.async_initialize()
        async_initialize_recorder(self.hass)
        self.assertTrue(await async_setup_component(self.hass, "recorder", {
            "recorder": {"db_url": "sqlite:///" + self.tmp.name + "/recorder.db", "purge_keep_days": 1000, "auto_purge": False},
        }))
        await self.hass.async_start()
        self.recorder = get_instance(self.hass)
        await self.recording_done()
        registry = er.async_get(self.hass)
        await registry.async_load(load_empty=True)
        for name in ("old", "new"):
            registry.async_get_or_create(
                "sensor", "test", name, suggested_object_id=name,
                disabled_by=er.RegistryEntryDisabler.USER,
                original_device_class="temperature", unit_of_measurement="°C",
                capabilities={"state_class": "measurement"},
            )
        self.manager = manager_module.EntityReplacer(self.hass)
        self.start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=2)
        self.meta = {"statistic_id": OLD, "source": "recorder", "name": None,
                     "mean_type": StatisticMeanType.ARITHMETIC, "has_sum": False,
                     "unit_class": "temperature", "unit_of_measurement": "°C"}
        for table in (Statistics, StatisticsShortTerm):
            self.recorder.async_import_statistics(self.meta, [
                {"start": self.start, "mean": 20.5, "min": 19.0, "max": 22.0},
            ], table)
        # A raw state for a different historical entity must remain untouched.
        self.hass.states.async_set("sensor.raw_history", "123")
        await self.hass.async_block_till_done()
        await self.recording_done()

    async def asyncTearDown(self):
        await self.hass.async_stop()
        self.tmp.cleanup()

    async def recording_done(self):
        # HA's async_block_till_done can return while a task has already been
        # taken off its queue but is still committing a separate DB session.
        # Always enqueue a barrier so snapshots cannot race an import/compile.
        await self.hass.async_block_till_done()
        done = asyncio.get_running_loop().create_future()
        self.recorder.queue_task(SynchronizeTask(done))
        await asyncio.wait_for(done, 10)

    async def rows(self):
        def read():
            with session_scope(hass=self.hass, read_only=True) as session:
                return {
                    "hourly": list(session.execute(select(Statistics.id, Statistics.metadata_id, Statistics.start_ts, Statistics.mean, Statistics.min, Statistics.max)).tuples()),
                    "five_minute": list(session.execute(select(StatisticsShortTerm.id, StatisticsShortTerm.metadata_id, StatisticsShortTerm.start_ts, StatisticsShortTerm.mean, StatisticsShortTerm.min, StatisticsShortTerm.max)).tuples()),
                    "states": list(session.execute(select(States.state_id, States.metadata_id, States.state)).tuples()),
                }
        return await self.recorder.async_add_executor_job(read)

    async def test_transfer_keeps_both_statistics_tables_and_raw_history(self):
        before = await self.rows()
        self.assertEqual(len(before["hourly"]), 1)
        self.assertEqual(len(before["five_minute"]), 1)
        preview = await self.manager.statistics_preview("owner", OLD, NEW)
        self.assertTrue(preview["eligible"], preview)
        result = await self.manager.statistics_apply("owner", preview["preview_id"], True)
        self.assertEqual(result["status"], "statistics_transferred")
        self.assertEqual(await self.rows(), before)
        adapter = self.manager._statistics_adapter()
        self.assertNotIn(OLD, await adapter.metadata(OLD, NEW))
        for function in (get_last_statistics, get_last_short_term_statistics):
            old = await self.recorder.async_add_executor_job(function, self.hass, 1, OLD, False, {"mean"})
            new = await self.recorder.async_add_executor_job(function, self.hass, 1, NEW, False, {"mean"})
            self.assertFalse(old.get(OLD))
            self.assertEqual(new[NEW][0]["mean"], 20.5)
        # Ensure queued statistics work after the rename does not resurrect old data.
        await self.recording_done()
        self.assertEqual(await self.rows(), before)

    async def test_destination_collision_after_preview_does_not_change_any_rows(self):
        preview = await self.manager.statistics_preview("owner", OLD, NEW)
        self.assertTrue(preview["eligible"], preview)
        async_import_statistics(self.hass, dict(self.meta, statistic_id=NEW), [
            {"start": self.start, "mean": 99.0, "min": 99.0, "max": 99.0},
        ])
        await self.hass.async_block_till_done()
        await self.recording_done()
        before = await self.rows()
        with self.assertRaisesRegex(manager_module.ReplacementError, "already has"):
            await self.manager.statistics_apply("owner", preview["preview_id"], True)
        self.assertEqual(await self.rows(), before)

    async def test_replacement_collects_new_statistics_under_the_same_metadata_id(self):
        preview = await self.manager.statistics_preview("owner", OLD, NEW)
        await self.manager.statistics_apply("owner", preview["preview_id"], True)
        old_rows = await self.rows()
        metadata_id = old_rows["five_minute"][0][1]
        er.async_get(self.hass).async_update_entity(NEW, disabled_by=None)
        self.hass.states.async_set(NEW, "25", {
            "device_class": "temperature", "state_class": "measurement", "unit_of_measurement": "°C",
        })
        await self.hass.async_block_till_done()
        await self.recording_done()
        now = datetime.now(timezone.utc)
        start = now.replace(minute=now.minute - now.minute % 5, second=0, microsecond=0)
        self.recorder.queue_task(AddRecorderPlatformTask("sensor", sensor_recorder))
        self.recorder.queue_task(StatisticsTask(start, False))
        await self.recording_done()
        after = await self.rows()
        self.assertEqual(len(after["five_minute"]), 2)
        self.assertTrue(all(row[1] == metadata_id for row in after["five_minute"]))
        self.assertAlmostEqual(after["five_minute"][-1][3], 25)
        self.assertNotIn(OLD, await self.manager._statistics_adapter().metadata(OLD, NEW))

    async def test_timeout_then_late_completion_is_verified_without_retry(self):
        preview = await self.manager.statistics_preview("owner", OLD, NEW)
        before = await self.rows()
        with patch.object(statistics_module, "TRANSFER_TIMEOUT", 0):
            with self.assertRaisesRegex(manager_module.ReplacementError, "unverified"):
                await self.manager.statistics_apply("owner", preview["preview_id"], True)
        await self.recording_done()
        job_id = self.manager.backups.statistics_conflicts(OLD, NEW)[0]
        result = await self.manager.statistics_verify(job_id)
        self.assertTrue(result["verified"])
        self.assertEqual(self.manager.backups.statistics_conflicts(OLD, NEW), [])
        self.assertEqual(await self.rows(), before)


if __name__ == "__main__":
    unittest.main()
