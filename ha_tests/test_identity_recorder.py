"""Real recorder ID reuse, including raw-history and statistics collisions."""
import unittest
from datetime import datetime, timezone

from homeassistant.helpers import entity_registry as er
from homeassistant.components.recorder.statistics import async_import_statistics

import test_statistics_recorder as fixtures

OLD, NEW = fixtures.OLD, fixtures.NEW


class IdentityRecorderTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.RealRecorderTests.asyncSetUp
    asyncTearDown = fixtures.RealRecorderTests.asyncTearDown
    recording_done = fixtures.RealRecorderTests.recording_done
    rows = fixtures.RealRecorderTests.rows

    async def seed_raw(self, entity_id, value):
        self.hass.states.async_set(entity_id, str(value), {
            "device_class": "temperature", "state_class": "measurement", "unit_of_measurement": "°C"})
        await self.recording_done()
        self.hass.states.async_remove(entity_id)
        await self.recording_done()

    async def takeover(self):
        mappings = [{"source": OLD, "target": NEW}]
        preview = await self.manager.reuse_preview("owner", mappings)
        self.assertTrue(preview["ready"], preview)
        return await self.manager.reuse_apply("owner", preview["preview_id"], True)

    async def test_old_raw_and_both_statistics_tables_keep_all_original_rows(self):
        await self.seed_raw(OLD, 20)
        before = await self.rows()
        old_id = er.async_get(self.hass).async_get(OLD).id
        new_id = er.async_get(self.hass).async_get(NEW).id
        result = await self.takeover()
        self.assertIn("verified", result["message"])
        self.assertEqual(await self.rows(), before)
        self.assertNotEqual(old_id, new_id)
        self.assertEqual(er.async_get(self.hass).async_get(OLD).id, new_id)
        self.assertIsNone(er.async_get(self.hass).async_get(NEW))
        meta = await self.manager._identity_adapter().snapshot({OLD, NEW})
        self.assertIn(OLD, meta["statistics"])
        self.assertNotIn(NEW, meta["statistics"])
        old_meta_id = meta["statistics"][OLD][0]
        er.async_get(self.hass).async_update_entity(OLD, disabled_by=None)
        self.hass.states.async_set(OLD, "23", {"device_class": "temperature", "state_class": "measurement", "unit_of_measurement": "°C"})
        await self.recording_done()
        now = datetime.now(timezone.utc)
        start = now.replace(minute=now.minute - now.minute % 5, second=0, microsecond=0)
        self.recorder.queue_task(fixtures.AddRecorderPlatformTask("sensor", fixtures.sensor_recorder))
        self.recorder.queue_task(fixtures.StatisticsTask(start, False))
        await self.recording_done()
        after = await self.rows()
        self.assertEqual(len(after["five_minute"]), 2)
        self.assertTrue(all(row[1] == old_meta_id for row in after["five_minute"]))

    async def test_existing_replacement_history_is_preserved_separately_without_merge(self):
        await self.seed_raw(OLD, 20)
        await self.seed_raw(NEW, 99)
        async_import_statistics(self.hass, dict(self.meta, statistic_id=NEW), [
            {"start": self.start, "mean": 99.0, "min": 99.0, "max": 99.0}])
        await self.recording_done()
        before = await self.rows()
        identities = await self.manager._identity_adapter().snapshot({OLD, NEW})
        await self.takeover()
        self.assertEqual(await self.rows(), before)
        self.assertEqual(await self.manager._identity_adapter().snapshot({OLD, NEW}), identities)

    async def test_batch_reuse_keeps_unmapped_entity_and_records_new_states_under_old_id(self):
        registry = er.async_get(self.hass)
        for name in ("old_switch", "new_switch", "unmapped"):
            registry.async_get_or_create("switch", "test", name, suggested_object_id=name,
                                         disabled_by=er.RegistryEntryDisabler.USER)
        self.hass.states.async_set("switch.old_switch", "on")
        await self.recording_done()
        self.hass.states.async_remove("switch.old_switch")
        await self.recording_done()
        before = await self.rows()
        unmapped = registry.async_get("switch.unmapped").id
        preview = await self.manager.reuse_preview("owner", [
            {"source": OLD, "target": NEW},
            {"source": "switch.old_switch", "target": "switch.new_switch"}])
        self.assertTrue(preview["ready"])
        await self.manager.reuse_apply("owner", preview["preview_id"], True)
        self.assertEqual(await self.rows(), before)
        self.assertEqual(registry.async_get("switch.unmapped").id, unmapped)
        raw_id = (await self.manager._identity_adapter().snapshot({"switch.old_switch"}))["states"]["switch.old_switch"]
        registry.async_update_entity("switch.old_switch", disabled_by=None)
        self.hass.states.async_set("switch.old_switch", "off")
        await self.recording_done()
        self.assertEqual((await self.manager._identity_adapter().snapshot({"switch.old_switch"}))["states"]["switch.old_switch"], raw_id)
        self.assertEqual((await self.rows())["states"][-1][1], raw_id)

    async def test_removed_old_entry_still_reuses_retained_history(self):
        await self.seed_raw(OLD, 20)
        before = await self.rows()
        er.async_get(self.hass).async_remove(OLD)
        await self.recording_done()
        await self.takeover()
        self.assertEqual(await self.rows(), before)

    async def test_cumulative_counter_and_enabled_entities_cannot_apply(self):
        registry = er.async_get(self.hass)
        registry.async_update_entity(NEW, capabilities={"state_class": "total_increasing"})
        with self.assertRaisesRegex(fixtures.manager_module.ReplacementError, "Cumulative"):
            await self.manager.reuse_preview("owner", [{"source": OLD, "target": NEW}])
        registry.async_update_entity(NEW, capabilities={"state_class": "measurement"}, disabled_by=None)
        preview = await self.manager.reuse_preview("owner", [{"source": OLD, "target": NEW}])
        self.assertFalse(preview["ready"])
        before = await self.rows()
        with self.assertRaisesRegex(fixtures.manager_module.ReplacementError, "unloading"):
            await self.manager.reuse_apply("owner", preview["preview_id"], True)
        self.assertEqual(await self.rows(), before)
