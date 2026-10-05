/* Browser-independent checks of the real panel renderer and write state. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

let Panel;
vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../custom_components/entity_replacer/frontend/panel.js"), "utf8"), {
  HTMLElement: class {},
  customElements: { get: () => undefined, define: (_, value) => { Panel = value; } },
});

function panel() {
  const value = Object.create(Panel.prototype);
  value._selected = new Set(["ref"]);
  value._busy = false;
  value._source = "sensor.old";
  value._target = "sensor.new";
  value._mode = "entity";
  value._sourceDevice = "a".repeat(32);
  value._targetDevice = "b".repeat(32);
  value._sourceFilter = "";
  value._targetFilter = "";
  value._sourceRemoved = false;
  value._devices = [
    { id: "a".repeat(32), name: "Old plug", manufacturer: "Example", model: "Meter", entities: 3 },
    { id: "b".repeat(32), name: "New plug", manufacturer: "Example", model: "Meter", entities: 3 },
    { id: "c".repeat(32), name: "<Garage bulb>", manufacturer: "Other", model: "Lamp", entities: 1 },
  ];
  value._mappingPlan = null;
  value._mappingConfirmed = false;
  value._mappings = [];
  value._error = "";
  value._entities = [
    { entity_id: "sensor.old", name: "Old", state: "unavailable" },
    { entity_id: "sensor.new", name: "New", state: "1" },
    { entity_id: "switch.other", name: "Other", state: "on" },
  ];
  value._preview = {
    preview_id: "preview", source: "sensor.old", target: "sensor.new",
    files_scanned: 1, dashboards_scanned: 0, warnings: [], coverage: ["Scope"],
    documents: [{ key: "yaml:x", kind: "yaml", label: "<img src=x onerror=alert(1)>", file: "/config/x.yaml", warnings: [], occurrences: [
      { id: "ref", writable: true, line: 2, column: 10, snippet: "<script>alert(1)</script>", reason: "" },
      { id: "manual", writable: false, line: 3, column: 10, snippet: "sensor.old", reason: "Device block" },
    ] }],
  };
  value._review = null;
  value._render = () => {};
  const nodes = new Map();
  value._query = (selector) => {
    if (!nodes.has(selector)) nodes.set(selector, { disabled: false, textContent: "", innerHTML: "", scrollIntoView() {} });
    return nodes.get(selector);
  };
  return value;
}

(async () => {
  let value = panel();
  let html = value._renderResults();
  assert.ok(html.includes("&lt;script&gt;"));
  assert.ok(html.includes("&lt;img src=x onerror=alert(1)&gt;"));
  assert.ok(!html.includes("<script>"));
  assert.ok(html.includes('data-ref="ref"'));
  assert.ok(!html.includes('data-ref="manual"'));
  assert.ok(!html.includes('id="apply"'));
  console.log("Renderer escapes configuration and omits manual-reference controls.");

  assert.equal(value._targetOptions().length, 1);
  assert.equal(value._targetOptions()[0].entity_id, "sensor.new");
  assert.ok(value._options(value._entities).includes("unavailable"));
  console.log("Entity suggestions retain unavailable sources and filter replacement domains.");

  value._updateControls();
  assert.equal(value._query("#review").disabled, false);
  assert.equal(value._query("#apply").disabled, true);
  value._selected.clear();
  value._updateControls();
  assert.equal(value._query("#review").disabled, true);
  console.log("Review/apply gating follows selection and review state.");

  value = panel();
  const calls = [];
  value._hass = { callWS: async (message) => { calls.push(message); return { references: 1, documents: 1, diffs: [] }; } };
  await value._reviewSelected();
  assert.equal(calls[0].type, "entity_replacer/review");
  assert.equal(calls[0].preview_id, "preview");
  assert.equal(calls[0].selected_ids.join(","), "ref");
  assert.ok(value._renderResults().includes('id="apply"'));
  console.log("The review request contains the current selected reference IDs.");

  value = panel();
  let requests = 0;
  value._hass = { callWS: async () => { requests++; } };
  await value._apply();
  assert.equal(requests, 0);
  console.log("An unreviewed panel cannot request a write.");

  value = panel();
  value._review = { references: 1, documents: 1, diffs: [] };
  value._hass = { callWS: async () => { throw new Error("simulated connection loss after write request"); } };
  value._refreshHistory = async () => {};
  await value._apply();
  assert.equal(value._preview, null);
  assert.equal(value._review, null);
  assert.equal(value._selected.size, 0);
  assert.ok(value._error.includes("connection loss"));
  console.log("An uncertain write clears its preview and prevents a blind retry.");

  value = panel();
  value._mode = "device";
  value._hass = { callWS: async () => { throw new Error("Scan should remain gated"); } };
  value._updateControls();
  assert.equal(value._query("#scan").disabled, true);
  await value._scan();
  assert.ok(value._preview);
  value._mappingPlan = { source_name: "<Old>", target_name: "New", source_entities: [{ entity_id: "sensor.old", name: "Power", state: "unavailable" }], target_entities: [{ entity_id: "sensor.new", domain: "sensor", name: "New", state: "1" }, { entity_id: "switch.other", domain: "switch", name: "Other", state: "on" }] };
  value._mappings = [{ source: "sensor.old", target: "sensor.new", reason: "Confirm" }];
  html = value._renderMappings();
  assert.ok(html.includes("&lt;Old&gt;"));
  assert.ok(html.includes('value="sensor.new" selected'));
  assert.ok(!html.includes('value="switch.other"'));
  assert.ok(html.includes("Leave unmapped"));
  value._mappingConfirmed = true;
  value._updateControls();
  assert.equal(value._query("#scan").disabled, false);
  value._mappingChanged();
  assert.equal(value._mappingConfirmed, false);
  assert.equal(value._query("#scan").disabled, true);
  assert.equal(value._preview, null);
  console.log("Device suggestions are escaped, domain-filtered, and require mapping confirmation.");

  value._mappingConfirmed = true;
  const deviceCalls = [];
  value._hass = { callWS: async (message) => { deviceCalls.push(message); return { ...panel()._preview, mode: "device" }; } };
  await value._scan();
  assert.equal(deviceCalls[0].type, "entity_replacer/scan_device");
  assert.equal(deviceCalls[0].source_device, "a".repeat(32));
  assert.equal(deviceCalls[0].confirmed_mappings, true);
  assert.equal(deviceCalls[0].mappings[0].target, "sensor.new");
  console.log("Whole-device scan sends the confirmed mapping table and device IDs.");

  value = panel();
  value.shadowRoot = { querySelectorAll: () => [] };
  value._query("#reviewed").remove = () => {};
  value._preview.documents[0].occurrences = [{ id: "device", writable: true, group: "block" }, { id: "entity", writable: true, group: "block" }, { id: "other", writable: true }];
  value._selected = new Set(["other"]);
  value._setReference("entity", true);
  assert.deepEqual([...value._selected].sort(), ["device", "entity", "other"]);
  value._setReference("device", false);
  assert.deepEqual([...value._selected], ["other"]);
  console.log("Device and entity selectors toggle as a complete group.");

  value = panel();
  html = value._deviceSelectOptions("target");
  assert.ok(html.includes("New plug"));
  assert.ok(html.includes("&lt;Garage bulb&gt;"));
  assert.ok(!html.includes('value="' + "a".repeat(32) + '"'));
  value._sourceFilter = "garage";
  html = value._deviceSelectOptions("source");
  assert.ok(html.includes("Old plug"), "Keep the selected device visible while filtering");
  assert.ok(html.includes("&lt;Garage bulb&gt;"));
  assert.ok(!html.includes("New plug"));
  value._chooseDevice("source", "b".repeat(32));
  assert.equal(value._targetDevice, "", "A device cannot replace itself");
  value._chooseDevice("source", "__removed");
  assert.equal(value._sourceRemoved, true);
  assert.equal(value._sourceDevice, "");
  assert.equal(value._mappingPlan, null);
  console.log("Named device choices support search, escape labels, exclude self-replacement and separate removed-device entry.");

  value = panel();
  value._devices = [];
  value._reconcileDevices();
  assert.equal(value._sourceRemoved, true);
  assert.equal(value._sourceDevice, "a".repeat(32));
  assert.equal(value._targetDevice, "");
  value._updateControls();
  assert.equal(value._query("#load-mappings").disabled, true);
  console.log("A removed selection becomes explicit, and a missing replacement cannot load mappings.");
})().catch((error) => { console.error(error); process.exitCode = 1; });
