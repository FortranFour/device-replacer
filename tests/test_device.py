"""Device mapping and transaction guards with native API-shaped fake adapters."""

import copy
from enum import Enum
import importlib
import json
from pathlib import Path
import pickle
import tempfile
import types
import unittest
from unittest.mock import patch

import test_manager as fixtures

engine = fixtures.engine
devices = importlib.import_module("er_test_package.device")
OLD_DEVICE = "a" * 32
NEW_DEVICE = "b" * 32
OLD_REGISTRY = "c" * 32
NEW_REGISTRY = "d" * 32
OLD_SWITCH_REGISTRY = "e" * 32
NEW_SWITCH_REGISTRY = "f" * 32


class MappingTests(unittest.TestCase):
    def test_ambiguous_roles_remain_unassigned(self):
        sources = [{"entity_id": "sensor.old", "domain": "sensor", "unit_of_measurement": "W", "device_class": "power"}]
        targets = [{**sources[0], "entity_id": entity} for entity in ("sensor.new1", "sensor.new2")]
        self.assertEqual(devices.suggest_mappings(sources, targets)[0]["target"], "")
        targets[0]["translation_key"] = sources[0]["translation_key"] = "power"
        self.assertEqual(devices.suggest_mappings(sources, targets)[0]["target"], "sensor.new1")

    def test_competing_roles_and_units_are_not_suggested(self):
        sources = [{"entity_id": "sensor.old1", "domain": "sensor", "device_class": "power"}, {"entity_id": "sensor.old2", "domain": "sensor", "device_class": "power"}]
        targets = [{"entity_id": "sensor.new", "domain": "sensor", "device_class": "power"}]
        self.assertFalse(any(row["target"] for row in devices.suggest_mappings(sources, targets)))
        sources[0]["unit_of_measurement"] = "W"
        targets[0]["unit_of_measurement"] = "kW"
        self.assertEqual(devices.suggest_mappings(sources[:1], targets)[0]["target"], "")

    def test_multi_entity_spans_do_not_cascade_yaml_or_dashboard(self):
        mapping = {"sensor.old": "sensor.new", "sensor.new": "sensor.final_longer"}
        text = b"value: '{{ states.sensor.old.state + states.sensor.new.state }}'\r\n"
        document = engine.scan_yaml("x.yaml", text, mapping)
        selected = {item.id for item in document.occurrences if item.writable}
        candidate = engine.build_candidate(document, selected, "labels", "labels")
        self.assertEqual(candidate, b"value: '{{ states.sensor.new.state + states.sensor.final_longer.state }}'\r\n")
        document = engine.scan_dashboard("x", "x", {"entity": "sensor.old sensor.new"}, mapping, None)
        selected = {item.id for item in document.occurrences if item.writable}
        self.assertEqual(engine.build_candidate(document, selected, "labels", "labels"), {"entity": "sensor.new sensor.final_longer"})

    def test_unmapped_entity_is_reported_without_becoming_selectable(self):
        document = engine.scan_yaml("x.yaml", b"entity: sensor.old\nother: switch.old\n", {"sensor.old": "sensor.new", "switch.old": None})
        self.assertEqual([item.writable for item in document.occurrences], [True, False])
        self.assertIn("mapping", document.occurrences[1].reason)

    def test_known_binary_storage_is_quiet_but_active_json_remains(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            storage = root / ".storage"
            storage.mkdir()
            for name, value in {"watchman.db": b"\xff", "cache.pkl": b"\xff", "binary_no_extension": pickle.dumps({"entity": "sensor.old"}), "sqlite_no_extension": b"SQLite format 3\x00" + b"\xff" * 64}.items():
                (storage / name).write_bytes(value)
            for name in ("core.config_entries", "energy", "power.db.json"):
                (storage / name).write_text('{"entity_id": "sensor.old"}')
            (storage / "broken_json").write_bytes(b"\xffnot json")
            documents, warnings, count = engine.scan_files(root, "sensor.old")
            self.assertEqual({document.relative for document in documents}, {".storage/core.config_entries", ".storage/energy", ".storage/power.db.json"})
            self.assertEqual(len(warnings), 1)
            self.assertIn("broken_json", warnings[0])
            self.assertEqual(count, 4)
            self.assertFalse(any(item.writable for document in documents for item in document.occurrences))


class DeviceManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.hass = fixtures.FakeHass(self.root)
        for entity, registry_id, device in ((fixtures.OLD, OLD_REGISTRY, OLD_DEVICE), (fixtures.NEW, NEW_REGISTRY, NEW_DEVICE), ("switch.old", OLD_SWITCH_REGISTRY, OLD_DEVICE), ("switch.new", NEW_SWITCH_REGISTRY, NEW_DEVICE)):
            self.hass.registry.entities[entity] = types.SimpleNamespace(entity_id=entity, id=registry_id, device_id=device, disabled_by=None, name=None, original_name=entity.split(".")[0], original_name_unprefixed=entity.split(".")[0], translation_key=entity.split(".")[0], platform="demo", device_class=None, original_device_class=None, unit_of_measurement=None, supported_features=0)
            attrs = {"supported_features": 0}
            if entity.startswith("sensor."):
                attrs.update(device_class="power", unit_of_measurement="W")
            self.hass.states.values[entity] = types.SimpleNamespace(entity_id=entity, state="unavailable" if entity.endswith(".old") else "on", attributes=attrs)
        self.hass.devices = types.SimpleNamespace(devices={key: types.SimpleNamespace(id=key, name_by_user=None, name=name, manufacturer="Example", model="Meter", disabled_by=None, config_entries={"demo"}) for key, name in ((OLD_DEVICE, "Old meter"), (NEW_DEVICE, "New meter"))})
        self.hass.devices.async_get = self.hass.devices.devices.get
        self.hass.services = types.SimpleNamespace(has_service=lambda domain, service: (domain, service) in {("switch", "turn_on"), ("homeassistant", "turn_on")})
        self.advertised = {
            "trigger": [{"device_id": NEW_DEVICE, "domain": "sensor", "type": "power", "entity_id": NEW_REGISTRY}],
            "condition": [{"device_id": NEW_DEVICE, "domain": "switch", "type": "is_on", "entity_id": NEW_SWITCH_REGISTRY}],
            "action": [{"device_id": NEW_DEVICE, "domain": "switch", "type": "turn_on", "entity_id": "switch.new"}],
        }
        self.schema_calls = []
        self.validation_fails = False

        class AutomationType(Enum):
            TRIGGER = "trigger"
            CONDITION = "condition"
            ACTION = "action"

        async def automations(hass, automation_type, device_ids):
            self.assertEqual(device_ids, [NEW_DEVICE])
            return {NEW_DEVICE: copy.deepcopy(self.advertised[automation_type.value])}

        async def platform(hass, domain, automation_type):
            def schema(config):
                self.schema_calls.append(copy.deepcopy(config))
                if self.validation_fails or config.get("above") == "invalid":
                    raise ValueError("incompatible config")
                if automation_type.value == "trigger":
                    self.assertEqual(config["platform"], "device")
                    self.assertNotIn("trigger", config)
                self.assertEqual(config["device_id"], NEW_DEVICE)
                return config
            return types.SimpleNamespace(**{automation_type.value.upper() + "_SCHEMA": schema})

        device_module = types.ModuleType("homeassistant.helpers.device_registry")
        device_module.async_get = lambda hass: hass.devices
        native = types.ModuleType("homeassistant.components.device_automation")
        native.DeviceAutomationType = AutomationType
        native.async_get_device_automations = automations
        native.async_get_device_automation_platform = platform
        self.modules = patch.dict("sys.modules", {device_module.__name__: device_module, native.__name__: native})
        self.modules.start()
        self.manager = fixtures.manager_module.EntityReplacer(self.hass)
        self.yaml = self.root / "automations.yaml"
        self.before = f'''- alias: Meter
  triggers:
    - trigger: device
      device_id: {OLD_DEVICE}
      domain: sensor
      type: power
      entity_id: {OLD_REGISTRY}
      above: 10
  conditions:
    - condition: device
      device_id: {OLD_DEVICE}
      domain: switch
      type: is_on
      entity_id: switch.old
  actions:
    - device_id: {OLD_DEVICE}
      domain: switch
      type: turn_on
      entity_id: {OLD_SWITCH_REGISTRY}
    - action: switch.turn_on
      target:
        device_id: [{OLD_DEVICE}]
  variables:
    power: "{{{{ states('sensor.old') }}}}"
'''.encode()
        self.yaml.write_bytes(self.before)
        self.rows = [{"source": "sensor.old", "target": "sensor.new"}, {"source": "switch.old", "target": "switch.new"}]

    async def asyncTearDown(self):
        self.modules.stop()
        self.temporary.cleanup()

    async def scan(self, rows=None, **kwargs):
        return await self.manager.scan_device("owner", OLD_DEVICE, NEW_DEVICE, self.rows if rows is None else rows, confirmed_mappings=True, **kwargs)

    def selected(self, preview):
        return [item["id"] for document in preview["documents"] for item in document["occurrences"] if item["writable"]]

    def groups(self, preview):
        result = {}
        for document in preview["documents"]:
            for item in document["occurrences"]:
                if item["group"]:
                    result.setdefault(item["group"], []).append(item)
        return result

    async def apply(self, preview):
        selected = self.selected(preview)
        await self.manager.review("owner", preview["preview_id"], selected)
        return await self.manager.apply("owner", preview["preview_id"], selected)

    async def test_inventory_suggestions_disabled_and_confirmed_mapping_gate(self):
        self.hass.registry.entities["switch.old"].disabled_by = "user"
        plan = await self.manager.device_plan(OLD_DEVICE, NEW_DEVICE)
        self.assertEqual(len(plan["source_entities"]), 2)
        self.assertEqual(plan["source_entities"][1]["state"], "disabled")
        self.assertEqual([row["target"] for row in plan["suggestions"]], ["sensor.new", "switch.new"])
        status = await self.manager.status()
        self.assertEqual(len(status["devices"]), 2)
        with self.assertRaisesRegex(engine.ReplacementError, "Confirm"):
            await self.manager.scan_device("owner", OLD_DEVICE, NEW_DEVICE, self.rows)

    async def test_complete_device_groups_native_validation_apply_and_restore(self):
        preview = await self.scan()
        self.assertEqual(len(self.groups(preview)), 4)
        self.assertTrue(all(item["writable"] for group in self.groups(preview).values() for item in group))
        self.assertEqual(len(self.schema_calls), 3)
        self.assertIn("Confirmed entity mappings", preview["report"])
        self.assertTrue(all(item["line"] for document in preview["documents"] for item in document["occurrences"]))
        result = await self.apply(preview)
        after = self.yaml.read_bytes()
        self.assertNotIn(OLD_DEVICE.encode(), after)
        self.assertIn(NEW_REGISTRY.encode(), after)
        self.assertIn(NEW_SWITCH_REGISTRY.encode(), after)
        self.assertIn(b"switch.new", after)
        self.assertEqual(self.hass.dashboard.value["views"][0]["cards"][0]["entity"], "sensor.new")
        manifest = self.manager.backups.load(result["job_id"])
        self.assertEqual(manifest["mode"], "device")
        self.assertEqual(len(manifest["mappings"]), 2)
        await self.manager.restore(result["job_id"])
        self.assertEqual(self.yaml.read_bytes(), self.before)
        self.assertEqual(self.hass.dashboard.value["views"][0]["cards"][0]["entity"], "sensor.old")

    async def test_partial_device_group_selection_is_refused(self):
        preview = await self.scan()
        selected = self.selected(preview)
        group = next(group for group in self.groups(preview).values() if len(group) > 1)
        selected.remove(group[0]["id"])
        with self.assertRaisesRegex(engine.ReplacementError, "complete device block"):
            await self.manager.review("owner", preview["preview_id"], selected)
        self.assertEqual(self.yaml.read_bytes(), self.before)

    async def test_unsupported_type_leaves_complete_group_manual(self):
        self.advertised["trigger"][0]["type"] = "temperature"
        preview = await self.scan()
        group = next(group for group in self.groups(preview).values() if any(item["source"] == OLD_REGISTRY for item in group))
        self.assertTrue(all(not item["writable"] for item in group))
        self.assertTrue(all("does not advertise" in item["reason"] for item in group))
        await self.apply(preview)
        self.assertIn(OLD_REGISTRY.encode(), self.yaml.read_bytes())
        self.assertIn(OLD_DEVICE.encode(), self.yaml.read_bytes())

    async def test_schema_rejection_and_capability_changes_block_writes(self):
        self.validation_fails = True
        preview = await self.scan()
        self.assertTrue(any("rejected" in item["reason"] for document in preview["documents"] for item in document["occurrences"]))
        self.validation_fails = False
        preview = await self.scan()
        selected = self.selected(preview)
        await self.manager.review("owner", preview["preview_id"], selected)
        self.advertised["action"].clear()
        with self.assertRaisesRegex(engine.ReplacementError, "no longer eligible"):
            await self.manager.apply("owner", preview["preview_id"], selected)
        self.assertEqual(self.yaml.read_bytes(), self.before)
        self.assertEqual(self.manager.backups.history(), [])

    async def test_registry_or_target_availability_change_invalidates_preview(self):
        preview = await self.scan()
        selected = self.selected(preview)
        self.hass.registry.entities["sensor.new"].id = "1" * 31 + "a"
        with self.assertRaisesRegex(engine.ReplacementError, "changed since preview"):
            await self.manager.review("owner", preview["preview_id"], selected)
        preview = await self.scan()
        selected = self.selected(preview)
        self.hass.states.values["sensor.new"].state = "unavailable"
        with self.assertRaisesRegex(engine.ReplacementError, "unavailable"):
            await self.manager.review("owner", preview["preview_id"], selected)

    async def test_unmapped_inventory_is_included_and_protects_device_blocks(self):
        preview = await self.scan(self.rows[:1])
        self.assertEqual(preview["mappings"][1]["source"], "switch.old")
        self.assertIsNone(preview["mappings"][1]["target"])
        switch_groups = [group for group in self.groups(preview).values() if any(item["source"] in {"switch.old", OLD_SWITCH_REGISTRY} for item in group)]
        self.assertTrue(all(not item["writable"] for group in switch_groups for item in group))
        self.assertTrue(any("Unmapped entities" in warning for warning in preview["warnings"]))

    async def test_cross_domain_foreign_device_and_many_to_one_mappings_rejected(self):
        for rows in ([{"source": "sensor.old", "target": "switch.new"}], [{"source": "sensor.old", "target": "sensor.new"}, {"source": "sensor.other", "target": "sensor.new"}], [{"source": "sensor.new", "target": "sensor.new"}]):
            with self.subTest(rows=rows), self.assertRaises(engine.ReplacementError):
                await self.scan(rows)
        with self.assertRaises(engine.ReplacementError):
            await self.scan([{"source": "sensor.old", "target": "sensor.new", "source_registry_id": OLD_SWITCH_REGISTRY}])

    async def test_removed_device_explicit_entities_still_replace_plain_references(self):
        self.hass.devices.devices.pop(OLD_DEVICE)
        self.hass.registry.entities.pop("sensor.old")
        self.hass.registry.entities.pop("switch.old")
        self.hass.states.values.pop("sensor.old")
        self.hass.states.values.pop("switch.old")
        preview = await self.scan([{"source": "sensor.old", "target": "sensor.new", "source_registry_id": OLD_REGISTRY}])
        self.assertTrue(any("old device is removed" in warning for warning in preview["warnings"]))
        await self.apply(preview)
        self.assertIn(b"states('sensor.new')", self.yaml.read_bytes())
        self.assertIn(OLD_DEVICE.encode(), self.yaml.read_bytes())

    async def test_units_features_templates_aliases_and_unknown_device_context_stay_manual(self):
        self.hass.states.values["sensor.new"].attributes["unit_of_measurement"] = "kW"
        preview = await self.scan()
        group = next(group for group in self.groups(preview).values() if any(item["source"] == OLD_REGISTRY for item in group))
        self.assertTrue(all(not item["writable"] for item in group))
        for text in (f"input:\n  device_id: {OLD_DEVICE}\n  entity_id: sensor.old\n", f"action:\n  - action: switch.turn_on\n    target:\n      device_id: {OLD_DEVICE}\n      entity_id: \"{{{{ states('switch.old') }}}}\"\n", f"shared: &device {OLD_DEVICE}\nactions:\n  - device_id: *device\n    domain: switch\n    type: turn_on\n    entity_id: switch.old\n"):
            self.yaml.write_text(text)
            preview = await self.scan()
            self.assertFalse(any(item["writable"] for document in preview["documents"] if document["kind"] == "yaml" for item in document["occurrences"]))

    async def test_opaque_id_outside_selector_is_manual_and_storage_remains_report_only(self):
        self.yaml.write_text(f"entity: sensor.old\nother: {OLD_REGISTRY}\n")
        (self.root / ".storage/energy").write_text(json.dumps({"entity_id": "sensor.old"}))
        preview = await self.scan()
        self.assertFalse(next(item for document in preview["documents"] for item in document["occurrences"] if item["source"] == OLD_REGISTRY)["writable"])
        storage = next(document for document in preview["documents"] if document["file"] == str(self.root / ".storage/energy"))
        self.assertFalse(any(item["writable"] for item in storage["occurrences"]))

    async def test_multi_entity_failure_rolls_back_yaml_and_dashboard(self):
        preview = await self.scan()
        self.hass.dashboard.fail_after_cache = True
        with self.assertRaisesRegex(engine.ReplacementError, "rolled back"):
            await self.apply(preview)
        self.assertEqual(self.yaml.read_bytes(), self.before)
        self.assertEqual(self.hass.dashboard.value["views"][0]["cards"][0]["entity"], "sensor.old")
        self.assertEqual(self.manager.backups.history()[0]["status"], "rolled_back")

    async def test_device_only_event_without_entities_can_be_replaced(self):
        self.hass.registry.entities = {}
        self.yaml.write_text(f"triggers:\n  - trigger: device\n    device_id: {OLD_DEVICE}\n    domain: demo\n    type: press\n    subtype: button_1\n")
        self.advertised["trigger"] = [{"device_id": NEW_DEVICE, "domain": "demo", "type": "press", "subtype": "button_1"}]
        preview = await self.scan([])
        self.assertTrue(next(iter(self.groups(preview).values()))[0]["writable"])
        await self.apply(preview)
        self.assertIn(NEW_DEVICE, self.yaml.read_text())

    async def test_service_target_with_extra_replacement_entity_is_manual(self):
        entry = copy.copy(self.hass.registry.entities["switch.new"])
        entry.entity_id = "switch.extra"
        entry.id = "1" * 31 + "a"
        self.hass.registry.entities[entry.entity_id] = entry
        preview = await self.scan()
        group = next(group for group in self.groups(preview).values() if len(group) == 1)
        self.assertFalse(group[0]["writable"])
        self.assertIn("one-to-one", group[0]["reason"])

    async def test_dynamic_config_validation_is_used_without_static_schema(self):
        async def platform(hass, domain, automation_type):
            async def validator(hass, config):
                if config.get("type") == "power":
                    raise ValueError("dynamic compatibility rejection")
                return config
            return types.SimpleNamespace(**{f"async_validate_{automation_type.value}_config": validator})
        with patch("homeassistant.components.device_automation.async_get_device_automation_platform", platform):
            preview = await self.scan()
        group = next(group for group in self.groups(preview).values() if any(item["source"] == OLD_REGISTRY for item in group))
        self.assertTrue(all(not item["writable"] for item in group))
        self.assertIn("rejected", group[0]["reason"])

    async def test_advertised_endpoint_selectors_must_match(self):
        self.advertised["trigger"][0]["endpoint_id"] = 2
        self.yaml.write_bytes(self.before.replace(b"      above: 10", b"      endpoint_id: 1\n      above: 10"))
        preview = await self.scan()
        group = next(group for group in self.groups(preview).values() if any(item["source"] == OLD_REGISTRY for item in group))
        self.assertTrue(all(not item["writable"] for item in group))

    async def test_identity_fields_duplicate_selectors_and_yaml_documents_are_guarded(self):
        self.yaml.write_text(f"unique_id:\n  - trigger: device\n    device_id: {OLD_DEVICE}\n    domain: sensor\n    type: power\n    entity_id: {OLD_REGISTRY}\n")
        preview = await self.scan()
        self.assertTrue(all(not item["writable"] for group in self.groups(preview).values() for item in group))
        self.yaml.write_bytes(self.before.replace(f"      device_id: {OLD_DEVICE}".encode(), f"      device_id: {OLD_DEVICE}\n      device_id: {OLD_DEVICE}".encode()))
        preview = await self.scan()
        self.assertTrue(all(not item["writable"] for group in self.groups(preview).values() if len(group) > 2 for item in group))
        self.yaml.write_bytes(b"---\n" + self.before + b"---\nentity: sensor.old\n")
        preview = await self.scan()
        self.assertTrue(all(item["writable"] for group in self.groups(preview).values() for item in group))
        await self.apply(preview)
        self.assertIn(b"---\nentity: sensor.new", self.yaml.read_bytes())

    async def test_unavailable_and_disabled_replacements_require_explicit_option(self):
        self.hass.devices.devices[NEW_DEVICE].disabled_by = "user"
        with self.assertRaisesRegex(engine.ReplacementError, "disabled"):
            await self.scan()
        self.hass.states.values["sensor.new"].state = "unavailable"
        preview = await self.scan(allow_unavailable_target=True)
        self.assertTrue(any("unavailable" in note for note in preview["warnings"]))
        await self.apply(preview)

    async def test_device_availability_is_checked_even_for_unmapped_device_events(self):
        self.hass.states.values["sensor.new"].state = "unavailable"
        self.hass.states.values["switch.new"].state = "unknown"
        with self.assertRaisesRegex(engine.ReplacementError, "All replacement device entities"):
            await self.scan([])
        preview = await self.scan([], allow_unavailable_target=True)
        self.assertTrue(any("working state" in note for note in preview["warnings"]))


if __name__ == "__main__":
    unittest.main()
