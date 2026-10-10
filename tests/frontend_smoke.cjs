/* Optional frontend QA: PLAYWRIGHT_MODULE=/path/to/playwright node tests/frontend_smoke.cjs */
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");

(async () => {
  const browser = await chromium.launch({ headless: true, args: ["--no-sandbox"] });
  try {
    for (const viewport of [{ width: 1360, height: 1000 }, { width: 390, height: 844 }]) {
    for (const mode of ["device", "entity"]) {
      const page = await browser.newPage({ viewport });
      const errors = [];
      page.on("pageerror", (error) => errors.push(error.message));
      await page.setContent("<!DOCTYPE html><html><head><meta name='viewport' content='width=device-width, initial-scale=1'></head><body style='margin:0'></body></html>");
      await page.addScriptTag({ path: path.join(__dirname, "../custom_components/entity_replacer/frontend/panel.js") });
      await page.evaluate(() => {
        window.calls = [];
        window.restored = false;
        const status = {
          version: "1.1.3",
          devices: [{ id: "a".repeat(32), name: "Old plug", manufacturer: "Example", model: "Meter", entities: 1 }, { id: "b".repeat(32), name: "New plug", manufacturer: "Example", model: "Meter", entities: 1 }],
          entities: [
            { entity_id: "sensor.old", name: "Old power sensor", state: "unavailable" },
            { entity_id: "sensor.new", name: "Replacement power sensor", state: "35" },
            { entity_id: "switch.other", name: "Different domain", state: "off" },
          ], history: [],
        };
        const preview = {
          preview_id: "demo", source: "sensor.old", target: "sensor.new",
          files_scanned: 84, dashboards_scanned: 3,
          warnings: [], coverage: ["Literal YAML references and saved UI dashboards; device-based blocks require manual review."],
          report: "Demo report with /config/automations.yaml:12 and /config/.storage/lovelace.main:27",
          documents: [
            { key: "yaml:automations.yaml", label: "automations.yaml", kind: "yaml", file: "/config/automations.yaml", warnings: [],
              occurrences: [{ id: "first", line: 12, column: 18, pointer: "/0/triggers/0/entity_id", snippet: "entity_id: sensor.old", writable: true, reason: "" }] },
            { key: "dashboard:main", label: "UI dashboard: main", kind: "dashboard", file: "/config/.storage/lovelace.main", warnings: [],
              occurrences: [{ id: "second", line: 27, column: 21, pointer: "/views/0/cards/0/entity", snippet: '"entity": "sensor.old"', writable: true, reason: "" }] },
            { key: "yaml:packages/device.yaml", label: "packages/device.yaml", kind: "yaml", file: "/config/packages/device.yaml", warnings: [],
              occurrences: [{ id: "manual", line: 8, column: 20, pointer: "/automation/0/triggers/0/entity_id", snippet: "entity_id: sensor.old", writable: false, reason: "This block also targets a device_id. Recreate it with the replacement device." }] },
          ],
        };
        const devicePreview = JSON.parse(JSON.stringify(preview));
        devicePreview.mode = "device";
        devicePreview.coverage = ["Mapped entity references and validated whole device selector blocks."];
        devicePreview.source = "Old plug";
        devicePreview.target = "New plug";
        devicePreview.documents[0].occurrences = [
          { id: "first", source: "a".repeat(32), target: "b".repeat(32), group: "block", line: 12, column: 18, snippet: "device_id: " + "a".repeat(32), writable: true, reason: "Validated block. Select together." },
          { id: "linked", source: "sensor.old", target: "sensor.new", group: "block", line: 13, column: 18, snippet: "entity_id: sensor.old", writable: true, reason: "Validated block. Select together." },
        ];
        const mappingPlan = {
          source_name: "Old plug", target_name: "New plug", source_removed: false,
          source_entities: [{ entity_id: "sensor.old", domain: "sensor", name: "Power", state: "unavailable", registry_id: "c".repeat(32), unit_of_measurement: "W" }],
          target_entities: [{ entity_id: "sensor.new", domain: "sensor", name: "Power", state: "35", unit_of_measurement: "W" }, { entity_id: "switch.other", domain: "switch", name: "Wrong domain", state: "on" }],
          suggestions: [{ source: "sensor.old", target: "sensor.new", reason: "Proposed by role metadata; confirm this role." }],
        };
        window.panel = document.createElement("entity-replacer-panel");
        document.body.append(window.panel);
        window.panel.hass = { callWS: async (message) => {
          window.calls.push(message);
          switch (message.type) {
            case "entity_replacer/status": return status;
            case "entity_replacer/scan": return preview;
            case "entity_replacer/device_plan": return mappingPlan;
            case "entity_replacer/scan_device":
              if (!message.confirmed_mappings || message.mappings[0].target !== "sensor.new") throw new Error("Unconfirmed mapping");
              return devicePreview;
            case "entity_replacer/review": return {
              references: message.selected_ids.length, documents: message.selected_ids.length,
              diffs: message.selected_ids.map((id) => ({ label: id === "first" ? "automations.yaml" : "UI dashboard: main", references: 1, diff: "--- before\n+++ after\n@@ -1 +1 @@\n-entity: sensor.old\n+entity: sensor.new\n" })),
            };
            case "entity_replacer/apply":
              status.history = [{ job_id: "20261002T170000Z-abcdef123456", created: "2026-10-02T17:00:00+00:00", source: "sensor.old", target: "sensor.new", status: "applied" }];
              return { references_changed: message.selected_ids.length, documents_changed: message.selected_ids.length, message: "Changes saved. Check configuration, then restart Home Assistant.", backup_path: "/config/.entity_replacer_backups/demo" };
            case "entity_replacer/restore": window.restored = true; return { documents_restored: 2, message: "Backup restored." };
            default: throw new Error("Unexpected request");
          }
        } };
      });
      const panel = page.locator("entity-replacer-panel");
      if (mode === "device") {
        await panel.locator("#source-device").selectOption("a".repeat(32));
        await panel.locator("#target-device").selectOption("b".repeat(32));
        assert.equal(await panel.locator("#scan").isDisabled(), true);
        await panel.locator("#load-mappings").click();
        await panel.locator("#confirm-mappings").waitFor();
        assert.equal(await panel.locator('[data-map-target] option[value="switch.other"]').count(), 0);
        await panel.locator("#confirm-mappings").check();
        await panel.locator("[data-map-target]").selectOption("");
        assert.equal(await panel.locator("#confirm-mappings").isChecked(), false);
        assert.equal(await panel.locator("#scan").isDisabled(), true);
        await panel.locator("[data-map-target]").selectOption("sensor.new");
        await panel.locator("#confirm-mappings").check();
      } else {
        await panel.locator('[data-mode="entity"]').click();
        await panel.locator("#source").fill("sensor.old");
        await panel.locator("#target").fill("sensor.new");
      }
      await panel.locator("#scan").click();
      await panel.locator("#review").waitFor();
      assert.equal(await panel.locator("[data-ref]:checked").count(), mode === "device" ? 3 : 2);
      await panel.locator("#select-none").click();
      assert.equal(await panel.locator("#review").isDisabled(), true);
      await panel.locator("#select-all").click();
      await panel.locator(".file summary").first().click();
      await panel.locator("[data-ref]").first().uncheck();
      assert.equal(await panel.locator("#selection-count").textContent(), "1 selected");
      await panel.locator("#review").click();
      await panel.locator("#apply").waitFor();
      assert.equal(await panel.locator("#apply").isEnabled(), true);
      await panel.locator("[data-ref]").first().check();
      assert.equal(await panel.locator("#apply").count(), 0);
      await panel.locator("#review").click();
      await panel.locator("#apply").waitFor();
      await page.evaluate(() => {
        for (const item of window.panel.shadowRoot.querySelectorAll(".file")) item.open = true;
        window.panel.scrollTop = 0;
      });
      const overflows = await page.evaluate(() => {
        const root = window.panel.shadowRoot;
        return [...root.querySelectorAll("main,.card,.fields,.mapping-row,.reference,.reference-body,.file")].filter((node) => node.scrollWidth > node.clientWidth + 1).map((node) => node.className || node.tagName);
      });
      assert.deepEqual(overflows, [], "Horizontal overflow");
      const qaDir = process.env.QA_OUTPUT;
      if (qaDir) {
        fs.mkdirSync(qaDir, { recursive: true });
        await page.screenshot({ path: path.join(qaDir, `device-replacer-${mode}-${viewport.width}.png`), fullPage: true });
      }
      await panel.locator("#apply").click();
      await panel.locator("#restore").waitFor();
      assert.equal(await panel.locator("#restore").isDisabled(), true);
      await panel.locator("#restore-confirm").check();
      await panel.locator("#restore").click();
      await page.waitForFunction(() => window.restored);
      assert.deepEqual(errors, []);
      await page.close();
      console.log(`Frontend ${mode} workflow and overflow checks passed at ${viewport.width}px.`);
    }
    }
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
