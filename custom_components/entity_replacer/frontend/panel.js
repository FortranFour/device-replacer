/* Device Replacer 1.1.3. No external frontend libraries or build step. */
const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (character) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
}[character]));

class EntityReplacerPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._entities = [];
    this._devices = [];
    this._mode = "device";
    this._sourceDevice = "";
    this._targetDevice = "";
    this._sourceRemoved = false;
    this._sourceFilter = "";
    this._targetFilter = "";
    this._mappingPlan = null;
    this._mappings = [];
    this._mappingConfirmed = false;
    this._history = [];
    this._selected = new Set();
    this._source = "";
    this._target = "";
    this._text = true;
    this._storage = true;
    this._allowUnavailable = false;
    this._busy = false;
    this._error = "";
    this._notice = "";
    this._preview = null;
    this._review = null;
    this._started = false;
    this._render();
  }

  set hass(value) {
    this._hass = value;
    if (this.isConnected && !this._started && value?.callWS) {
      this._started = true;
      this._load();
    }
  }
  get hass() { return this._hass; }
  set narrow(value) { this.toggleAttribute("narrow", Boolean(value)); }
  get narrow() { return this.hasAttribute("narrow"); }
  connectedCallback() {
    if (!this._started && this._hass?.callWS) {
      this._started = true;
      this._load();
    }
  }

  _query(selector) { return this.shadowRoot.querySelector(selector); }
  _request(action, fields = {}) {
    return this._hass.callWS({ type: `entity_replacer/${action}`, ...fields });
  }

  async _load() {
    this._invalidate();
    this._resetMappings();
    await this._run("Loading devices and entities…", async () => {
      const status = await this._request("status");
      this._entities = status.entities;
      this._devices = status.devices || [];
      this._reconcileDevices();
      this._history = status.history;
      this._version = status.version;
    });
  }

  async _refreshHistory() {
    const status = await this._request("status");
    this._entities = status.entities;
    this._devices = status.devices || [];
    this._reconcileDevices();
    this._history = status.history;
  }

  async _run(message, action) {
    if (this._busy) return;
    this._busy = true;
    this._busyMessage = message;
    this._error = "";
    this._render();
    try {
      await action();
    } catch (error) {
      this._error = error?.message || String(error);
      // A failed write may have a recoverable backup; show its actual status.
      try { await this._refreshHistory(); } catch (_) { /* retain original error */ }
    } finally {
      this._busy = false;
      this._render();
    }
  }

  _invalidate() {
    this._preview = null;
    this._review = null;
    this._selected.clear();
    this._notice = "";
    this._query("#results").innerHTML = "";
    this._updateControls();
  }

  async _scan() {
    if (this._mode === "device" && (!this._mappingPlan || !this._mappingConfirmed)) return;
    this._invalidate();
    await this._run("Scanning configuration and dashboards…", async () => {
      const fields = this._mode === "device" ? {
        source_device: this._sourceDevice.trim(), target_device: this._targetDevice.trim(),
        mappings: this._mappings.map(({ source, target, source_registry_id }) => ({ source: source.trim(), target,
          ...(source_registry_id ? { source_registry_id: source_registry_id.trim() } : {}) })),
        confirmed_mappings: this._mappingConfirmed,
      } : { source: this._source.trim(), target: this._target.trim() };
      const preview = await this._request(this._mode === "device" ? "scan_device" : "scan", {
        ...fields,
        include_text: this._text, include_storage: this._storage,
        allow_unavailable_target: this._allowUnavailable,
      });
      this._preview = preview;
      for (const document of preview.documents) {
        for (const item of document.occurrences) {
          if (item.writable) this._selected.add(item.id);
        }
      }
    });
  }

  _resetMappings() {
    this._mappingPlan = null;
    this._mappings = [];
    this._mappingConfirmed = false;
    const container = this._query("#mappings");
    if (container) container.innerHTML = "";
  }

  async _loadMappings() {
    this._invalidate();
    this._resetMappings();
    await this._run("Loading device entities and suggesting matches…", async () => {
      const plan = await this._request("device_plan", { source_device: this._sourceDevice.trim(), target_device: this._targetDevice.trim() });
      this._mappingPlan = plan;
      this._mappings = plan.suggestions.map((item) => ({ ...item, source_registry_id: plan.source_entities.find((entry) => entry.entity_id === item.source)?.registry_id || "" }));
    });
  }

  _mappingChanged() {
    this._mappingConfirmed = false;
    const confirm = this._query("#confirm-mappings");
    if (confirm) confirm.checked = false;
    this._invalidate();
  }

  _reconcileDevices() {
    if (this._sourceDevice && !this._sourceRemoved && !this._devices.some((item) => item.id === this._sourceDevice)) this._sourceRemoved = true;
    if (this._targetDevice && !this._devices.some((item) => item.id === this._targetDevice)) this._targetDevice = "";
  }

  _deviceSelectOptions(role) {
    const source = role === "source";
    const selected = source ? (this._sourceRemoved ? "__removed" : this._sourceDevice) : this._targetDevice;
    const search = (source ? this._sourceFilter : this._targetFilter).trim().toLocaleLowerCase();
    const filtered = this._devices.filter((item) => (source || item.id !== this._sourceDevice) &&
      (item.id === selected || !search || [item.name, item.manufacturer, item.model, item.id].join(" ").toLocaleLowerCase().includes(search)));
    return `<option value="" ${!selected ? "selected" : ""}>Choose ${source ? "the old" : "the replacement"} device…</option>` +
      filtered.map((item) => `<option value="${escapeHtml(item.id)}" ${item.id === selected ? "selected" : ""}>${escapeHtml(item.name)} · ${escapeHtml([item.manufacturer, item.model].filter(Boolean).join(" "))} · ${item.entities} entities${item.disabled ? " · disabled" : ""} · ${escapeHtml(item.id.slice(-6))}</option>`).join("") +
      (source ? `<option value="__removed" ${this._sourceRemoved ? "selected" : ""}>Removed device: enter its former ID…</option>` : "");
  }

  _chooseDevice(role, value) {
    if (role === "source") {
      this._sourceRemoved = value === "__removed";
      this._sourceDevice = this._sourceRemoved ? "" : value;
      if (this._sourceDevice === this._targetDevice) this._targetDevice = "";
    } else {
      this._targetDevice = value;
    }
    this._resetMappings();
    this._invalidate();
    this._render();
  }

  _renderMappings() {
    const plan = this._mappingPlan;
    if (!plan) return "";
    return `<div class="mapping-heading"><h2>Map the device's entities</h2><p>${escapeHtml(plan.source_name)} → ${escapeHtml(plan.target_name)}</p><p class="secondary">Confirm each role. Same-domain suggestions are proposals. Leave an entity unmapped to report its references without changing them.</p></div>
      ${plan.source_removed ? '<p class="warning">The old device is removed. Add its former entity IDs below. Enter an old entity registry ID too if you have it from a device automation; it differs from an entity ID such as sensor.old.</p>' : ""}
      ${!this._mappings.length ? '<p>No source entities found. Device-only triggers can still be scanned. Add former entity IDs if needed.</p>' : ""}
      <div class="mapping-list">${this._mappings.map((row, index) => {
        const source = plan.source_entities.find((item) => item.entity_id === row.source);
        const domain = row.source.trim().split(".")[0];
        const targets = plan.target_entities.filter((item) => item.domain === domain && item.entity_id !== row.source);
        return `<div class="mapping-row"><div>${row.manual ? `<label class="field">Former entity ID<input type="text" data-map-source="${index}" value="${escapeHtml(row.source)}" placeholder="sensor.old_entity" spellcheck="false" ${this._busy ? "disabled" : ""}></label><label class="field registry-field">Former registry ID (optional)<input type="text" data-map-registry="${index}" value="${escapeHtml(row.source_registry_id)}" placeholder="32-character registry ID" spellcheck="false" ${this._busy ? "disabled" : ""}></label><button data-remove-map="${index}" ${this._busy ? "disabled" : ""}>Remove row</button>` : `<strong>${escapeHtml(source?.name || row.source)}</strong><code class="hint">${escapeHtml(row.source)}</code><span class="hint">${escapeHtml(source?.state || "removed")}${source?.unit_of_measurement ? ` · ${escapeHtml(source.unit_of_measurement)}` : ""}</span>`}</div>
        <div><label class="field">Replacement entity<select data-map-target="${index}" ${this._busy ? "disabled" : ""}><option value="">Leave unmapped</option>${targets.map((item) => `<option value="${escapeHtml(item.entity_id)}" ${row.target === item.entity_id ? "selected" : ""}>${escapeHtml(item.name)} · ${escapeHtml(item.entity_id)} · ${escapeHtml(item.state)}${item.unit_of_measurement ? ` · ${escapeHtml(item.unit_of_measurement)}` : ""}</option>`).join("")}</select></label><span class="hint">${escapeHtml(row.reason || "Choose the equivalent role on the replacement device.")}</span></div></div>`;
      }).join("")}</div><div class="actions"><button id="add-map" ${this._busy ? "disabled" : ""}>Add a former entity</button></div><label class="check"><input id="confirm-mappings" type="checkbox" ${this._mappingConfirmed ? "checked" : ""} ${this._busy ? "disabled" : ""}> I have checked these entity roles and chosen which entities to leave unmapped</label>`;
  }

  _setReference(id, checked) {
    const occurrences = this._preview.documents.flatMap((file) => file.occurrences);
    const item = occurrences.find((entry) => entry.id === id);
    if (!item?.writable) return;
    for (const entry of occurrences) {
      if (entry.id === id || (item.group && entry.group === item.group)) {
        if (entry.writable) checked ? this._selected.add(entry.id) : this._selected.delete(entry.id);
      }
    }
    for (const check of this.shadowRoot.querySelectorAll("[data-ref]")) check.checked = this._selected.has(check.dataset.ref);
    this._selectionChanged();
    this._updateFileChecks();
  }

  async _reviewSelected() {
    this._review = null;
    await this._run("Checking selected replacements…", async () => {
      this._review = await this._request("review", {
        preview_id: this._preview.preview_id,
        selected_ids: [...this._selected],
      });
    });
    if (this._review) this._query("#reviewed")?.scrollIntoView({ block: "start", behavior: "smooth" });
  }

  async _apply() {
    if (!this._preview || !this._review) return;
    await this._run("Saving backups and applying replacements…", async () => {
      let result;
      try {
        result = await this._request("apply", {
          preview_id: this._preview.preview_id,
          selected_ids: [...this._selected],
        });
      } finally {
        // Never offer a blind retry after a write with an uncertain outcome.
        this._preview = null;
        this._review = null;
        this._selected.clear();
      }
      this._notice = `${result.references_changed} references changed in ${result.documents_changed} files/dashboards. ${result.message} Backup: ${result.backup_path}`;
      await this._refreshHistory();
    });
  }

  async _restore() {
    const jobId = this._query("#backup").value;
    if (!jobId) return;
    await this._run("Checking files and restoring backup…", async () => {
      const result = await this._request("restore", { job_id: jobId });
      this._preview = null;
      this._review = null;
      this._selected.clear();
      this._notice = `${result.documents_restored} files/dashboards restored. ${result.message}`;
      await this._refreshHistory();
    });
  }

  _download(filename, text, type = "text/plain;charset=utf-8") {
    const url = URL.createObjectURL(new Blob([text], { type }));
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    this.shadowRoot.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  _options(entities) {
    return entities.map((item) => `<option value="${escapeHtml(item.entity_id)}" label="${escapeHtml(item.name)} · ${escapeHtml(item.state)}"></option>`).join("");
  }

  _targetOptions() {
    const domain = this._source.split(".")[0];
    return this._entities.filter((item) => item.entity_id !== this._source && (!this._source.includes(".") || item.entity_id.startsWith(`${domain}.`)));
  }

  _selectionChanged() {
    this._review = null;
    this._query("#reviewed")?.remove();
    this._updateControls();
  }

  _updateControls() {
    const review = this._query("#review");
    const apply = this._query("#apply");
    if (review) review.disabled = this._busy || !this._preview || !this._selected.size;
    if (apply) apply.disabled = this._busy || !this._review || !this._selected.size;
    const count = this._query("#selection-count");
    if (count) count.textContent = `${this._selected.size} selected`;
    const scan = this._query("#scan");
    if (scan) scan.disabled = this._busy || (this._mode === "device"
      ? !this._mappingPlan || !this._mappingConfirmed || this._mappings.some((row) => !row.source.trim())
      : !this._source.trim() || !this._target.trim());
    const load = this._query("#load-mappings");
    if (load) load.disabled = this._busy || !/^[a-f0-9]{32}$/.test(this._sourceDevice.trim()) || !/^[a-f0-9]{32}$/.test(this._targetDevice.trim()) || this._sourceDevice.trim() === this._targetDevice.trim();
  }

  _renderResults() {
    const preview = this._preview;
    if (!preview) return "";
    let available = 0;
    let manual = 0;
    for (const document of preview.documents) {
      for (const item of document.occurrences) item.writable ? available++ : manual++;
    }
    const summary = `<section class="bubble"><strong>${available} selectable references · ${manual} for manual review</strong><p><code>${escapeHtml(preview.source)}</code> → <code>${escapeHtml(preview.target)}</code></p><p>${preview.files_scanned} files &amp; ${preview.dashboards_scanned} UI dashboards scanned. Preview expires after 15 minutes.</p></section>`;
    const warnings = preview.warnings.length ? `<div class="warning" role="status"><strong>Scan notes</strong><ul>${preview.warnings.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul></div>` : "";
    const documents = preview.documents.map((file) => {
      const items = file.occurrences.map((item) => {
        const location = item.line ? `Line ${item.line}:${item.column}` : `Pointer ${item.pointer}`;
        const control = item.writable
          ? `<input type="checkbox" data-ref="${item.id}" ${this._selected.has(item.id) ? "checked" : ""} ${this._busy ? "disabled" : ""} aria-label="Select ${escapeHtml(location)}">`
          : `<span class="manual">Review</span>`;
        return `<div class="reference"><div>${control}</div><div class="reference-body"><strong>${escapeHtml(location)}</strong><pre>${escapeHtml(item.snippet)}</pre><p class="secondary">${escapeHtml(item.source || preview.source)} → ${escapeHtml(item.source ? item.target || "Unmapped; unchanged" : preview.target)}</p>${item.reason ? `<p class="secondary">${escapeHtml(item.reason)}</p>` : ""}</div></div>`;
      }).join("");
      const eligible = file.occurrences.filter((item) => item.writable);
      const selected = eligible.filter((item) => this._selected.has(item.id)).length;
      return `<details class="file"><summary>${escapeHtml(file.label)} <span class="secondary">(${file.occurrences.length})</span></summary><div class="file-body"><p class="path">${escapeHtml(file.file || `Dashboard /${file.dashboard_url}`)}</p>${eligible.length ? `<label class="check"><input type="checkbox" data-file="${escapeHtml(file.key)}" ${selected === eligible.length ? "checked" : ""} ${this._busy ? "disabled" : ""}> Select all ${eligible.length} eligible references in this file/dashboard</label>` : ""}${file.warnings.map((item) => `<p class="warning">${escapeHtml(item)}</p>`).join("")}${items}</div></details>`;
    }).join("");
    const review = this._review ? `<section id="reviewed" class="card"><h2>Review these ${this._review.references} replacements</h2><p>These diffs reflect your current selection. Changes are backed up before they are written.</p>${this._review.diffs.map((item) => `<details class="diff" open><summary>${escapeHtml(item.label)} · ${item.references} replacements</summary><pre>${escapeHtml(item.diff.slice(0, 100000))}${item.diff.length > 100000 ? "\n… Display shortened; download the diff to read it all." : ""}</pre></details>`).join("")}<div class="actions"><button id="download-diff">Download selected diff</button><button id="apply" class="primary" ${this._busy ? "disabled" : ""}>Apply reviewed replacements</button></div></section>` : "";
    return `${summary}${warnings}<div class="actions"><button id="select-all" ${this._busy ? "disabled" : ""}>Select all eligible</button><button id="select-none" ${this._busy ? "disabled" : ""}>Select none</button><span id="selection-count" class="secondary">${this._selected.size} selected</span></div>${documents}${!preview.documents.length ? '<p class="empty">No literal references found in the scanned scope.</p>' : ""}<div class="actions"><button id="download-report">Download paths &amp; line numbers</button><button id="download-json">Download JSON report</button><button id="review" class="primary" ${this._busy || !this._selected.size ? "disabled" : ""}>Review selected replacements</button></div>${review}<details class="coverage"><summary>Scan coverage &amp; manual follow-up</summary><ul>${preview.coverage.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul><p>Review unmapped entities and manual device blocks even if you apply every eligible reference. For internal storage references, open the owning helper/integration settings rather than editing its file. Avoid editing the selected configuration while applying or restoring.</p></details>`;
  }

  _renderChooser() {
    if (this._mode === "entity") return `<div class="fields"><label class="field">Entity to replace<input id="source" type="text" list="source-entities" autocomplete="off" spellcheck="false" value="${escapeHtml(this._source)}" placeholder="sensor.old_entity" ${this._busy ? "disabled" : ""}><span class="hint">Choose a suggestion or type an ID that no longer exists.</span></label><label class="field">Replacement entity<input id="target" type="text" list="target-entities" autocomplete="off" spellcheck="false" value="${escapeHtml(this._target)}" placeholder="sensor.new_entity" ${this._busy ? "disabled" : ""}><span class="hint">Choose an existing entity in the same domain.</span></label></div><datalist id="source-entities">${this._options(this._entities)}</datalist><datalist id="target-entities">${this._options(this._targetOptions())}</datalist>`;
    return `<div class="fields"><div><label class="field">Find the old device<input id="source-device-filter" type="text" value="${escapeHtml(this._sourceFilter)}" placeholder="Search name, manufacturer, or model" ${this._busy ? "disabled" : ""}></label><label class="field device-select-label">Device to replace<select id="source-device" ${this._busy ? "disabled" : ""}>${this._deviceSelectOptions("source")}</select><span class="hint">Working and unavailable devices are included. Choose a removed device below if it is no longer registered.</span></label>${this._sourceRemoved ? `<label class="field device-select-label">Former device ID<input id="removed-device" type="text" spellcheck="false" autocomplete="off" value="${escapeHtml(this._sourceDevice)}" placeholder="32-character device ID from its old YAML or URL" ${this._busy ? "disabled" : ""}></label>` : ""}</div><div><label class="field">Find the replacement device<input id="target-device-filter" type="text" value="${escapeHtml(this._targetFilter)}" placeholder="Search name, manufacturer, or model" ${this._busy ? "disabled" : ""}></label><label class="field device-select-label">Replacement device<select id="target-device" ${this._busy ? "disabled" : ""}>${this._deviceSelectOptions("target")}</select><span class="hint">Add/pair the new hardware in Home Assistant first. Choose its name here, then match its entities below.</span></label></div></div><div class="actions"><button id="load-mappings">Load entity mappings</button></div><div id="mappings">${this._renderMappings()}</div>`;
  }

  _render() {
    this.shadowRoot.innerHTML = `<style>
      :host { display:block; height:100%; color:var(--primary-text-color,#202a35); background:var(--primary-background-color,#f4f6f8); font:14px/1.5 var(--paper-font-body1_-_font-family,Arial,sans-serif); overflow:auto; }
      * { box-sizing:border-box; } header { display:flex; align-items:center; gap:16px; padding:14px 20px; border-bottom:1px solid var(--divider-color,#dce1e7); background:var(--card-background-color,#fff); position:sticky; top:0; z-index:2; }
      header strong { font-size:20px; } .brand-icon { width:32px; height:32px; flex-shrink:0; } .menu { display:none; border:0; padding:5px 10px; font-size:22px; background:transparent; color:inherit; } .version { margin-left:auto; color:var(--secondary-text-color,#657180); }
      :host([narrow]) .menu { display:inline-flex; align-items:center; justify-content:center; }
      main { max-width:1080px; margin:0 auto; padding:20px; } h1 { font-size:21px; margin:0 0 8px; } h2 { font-size:18px; margin:0 0 10px; } p { margin:6px 0; } code { overflow-wrap:anywhere; }
      .card { background:var(--card-background-color,#fff); border:1px solid var(--divider-color,#dce1e7); border-radius:14px; padding:20px; margin:16px 0; }
      .bubble { padding:16px 20px; border-radius:18px; background:var(--entity-replacer-summary-background,rgba(3,169,244,.13)); border:1px solid rgba(3,169,244,.24); margin:0 0 16px; } .bubble strong { display:block; font-size:17px; }
      .fields { display:grid; grid-template-columns:1fr 1fr; gap:20px; } label.field { display:block; font-weight:600; } .hint { display:block; margin-top:6px; font-weight:400; color:var(--secondary-text-color,#657180); }
      .device-select-label { margin-top:12px; } .mapping-heading { margin:20px 0 12px; } .mapping-row { display:grid; grid-template-columns:1fr 1fr; gap:20px; padding:16px 0; border-bottom:1px solid var(--divider-color,#dce1e7); } .mapping-row>div { min-width:0; overflow-wrap:anywhere; } .registry-field { margin:10px 0; } .mapping-row code { display:block; } .mode-buttons button[aria-pressed=true] { border:2px solid var(--primary-color,#0288d1); } .mapping-row select { text-overflow:ellipsis; }
      input[type=text], select { width:100%; min-height:44px; margin-top:6px; padding:10px 12px; border-radius:8px; border:1px solid var(--divider-color,#b9c3ce); background:var(--card-background-color,#fff); color:inherit; font:inherit; }
      input:focus-visible,button:focus-visible,summary:focus-visible,select:focus-visible { outline:3px solid var(--primary-color,#039be5); outline-offset:2px; }
      input[type=checkbox] { width:19px; height:19px; accent-color:var(--primary-color,#039be5); flex-shrink:0; } .check { display:flex; align-items:flex-start; gap:9px; margin:12px 0; } .check input { margin-top:1px; }
      button { border:1px solid var(--divider-color,#b9c3ce); border-radius:8px; background:var(--card-background-color,#fff); color:inherit; min-height:40px; padding:9px 14px; font-family:inherit; font-size:14px; font-weight:600; cursor:pointer; } button.primary { background:var(--primary-color,#0288d1); border-color:transparent; color:var(--text-primary-color,#fff); } button:disabled { opacity:.45; cursor:default; }
      .actions { display:flex; flex-wrap:wrap; align-items:center; gap:10px; margin:16px 0; } .secondary { color:var(--secondary-text-color,#657180); font-weight:400; } .warning,.error,.notice { padding:14px 16px; border-radius:10px; margin:14px 0; overflow-wrap:anywhere; } .warning { background:rgba(255,170,0,.12); } .error { background:rgba(225,55,55,.12); } .notice { background:rgba(50,160,90,.12); }
      .file { background:var(--card-background-color,#fff); border:1px solid var(--divider-color,#dce1e7); border-radius:10px; margin:9px 0; overflow:hidden; } summary { cursor:pointer; padding:14px; font-weight:600; overflow-wrap:anywhere; } .file-body { padding:0 16px 12px; } .path { color:var(--secondary-text-color,#657180); overflow-wrap:anywhere; font-family:monospace; }
      .reference { display:flex; gap:12px; padding:12px 0; border-top:1px solid var(--divider-color,#e4e8ec); } .reference-body { min-width:0; flex:1; } .manual { display:block; padding:2px 6px; border-radius:5px; font-size:12px; background:rgba(255,170,0,.18); } pre { margin:6px 0; white-space:pre-wrap; overflow-wrap:anywhere; font:13px/1.5 monospace; background:var(--secondary-background-color,#f4f6f8); border-radius:7px; padding:10px; }
      .diff { border-top:1px solid var(--divider-color,#dce1e7); } .diff pre { max-height:520px; overflow:auto; white-space:pre; } .coverage { margin:20px 0; } li { margin:6px 0; } .empty { padding:20px; } .progress { color:var(--primary-color,#0288d1); margin:12px 0; } .backup-row { display:flex; flex-wrap:wrap; align-items:center; gap:12px; } .backup-row select { flex:1; min-width:230px; } .restore-check { margin-top:16px; }
      @media(max-width:640px) { main { padding:12px; } .fields,.mapping-row { grid-template-columns:1fr; gap:14px; } .card { padding:15px; } header { padding:12px; } .reference { gap:8px; } button { min-height:44px; } .backup-row select { min-width:0; flex-basis:100%; } }
    </style><header><button class="menu" id="menu" aria-label="Open sidebar">☰</button><img class="brand-icon" src="/entity_replacer_static/icon.svg" alt=""><strong>Device Replacer</strong><span class="version">${escapeHtml(this._version || "1.1.3")}</span></header><main>
      <section class="bubble"><strong>Replace a device and its entity references</strong><p>Choose the old and new devices, confirm their entity roles, then review and apply selected changes with backups. Single-entity replacement is also available.</p></section>
      ${this._error ? `<div class="error" role="alert">${escapeHtml(this._error)}</div>` : ""}${this._notice ? `<div class="notice" role="status">${escapeHtml(this._notice)}</div>` : ""}
      <section class="card"><div class="actions mode-buttons" aria-label="Replacement mode"><button data-mode="device" aria-pressed="${this._mode === "device"}" ${this._busy ? "disabled" : ""}>Whole device</button><button data-mode="entity" aria-pressed="${this._mode === "entity"}" ${this._busy ? "disabled" : ""}>Single entity</button></div>${this._renderChooser()}
      <details><summary>Additional scan options</summary><label class="check"><input id="text" type="checkbox" ${this._text ? "checked" : ""} ${this._busy ? "disabled" : ""}> Report references in additional text/code files</label><label class="check"><input id="storage" type="checkbox" ${this._storage ? "checked" : ""} ${this._busy ? "disabled" : ""}> Report references in other internal storage (manual review)</label><label class="check"><input id="allow-unavailable" type="checkbox" ${this._allowUnavailable ? "checked" : ""} ${this._busy ? "disabled" : ""}> Allow unavailable or disabled replacements</label></details>
      <div class="actions"><button id="scan" class="primary">Scan references</button><button id="refresh" ${this._busy ? "disabled" : ""}>Refresh device/entity lists</button></div><p class="secondary">YAML formatting and comments are preserved. UI dashboards are saved through Home Assistant. Device blocks are selectable only when their selectors and replacement capabilities can be validated. Unsupported blocks and other storage get manual instructions.</p></section>
      ${this._busy ? `<p class="progress" role="status" aria-live="polite">${escapeHtml(this._busyMessage)}</p>` : ""}<div id="results">${this._renderResults()}</div>
      <section class="card"><h2>Restore a replacement</h2><p class="secondary">Restore checks every file/dashboard first and refuses to overwrite newer edits. Backups survive Home Assistant restarts.</p>${this._history.length ? `<div class="backup-row"><select id="backup" aria-label="Choose replacement backup" ${this._busy ? "disabled" : ""}>${this._history.map((item) => `<option value="${escapeHtml(item.job_id)}">${escapeHtml(item.created)} · ${escapeHtml(item.source)} → ${escapeHtml(item.target)} · ${escapeHtml(item.status)}</option>`).join("")}</select><button id="restore" ${this._busy ? "disabled" : ""}>Restore selected backup</button></div><label class="check restore-check"><input id="restore-confirm" type="checkbox" ${this._busy ? "disabled" : ""}> Restore the files/dashboard settings changed by this replacement</label>` : '<p>No replacements have been applied yet.</p>'}</section>
    </main>`;
    this._bind();
    this._updateControls();
  }

  _bind() {
    const on = (selector, event, handler) => this._query(selector)?.addEventListener(event, handler);
    on("#menu", "click", () => this.dispatchEvent(new CustomEvent("hass-toggle-menu", { bubbles: true, composed: true })));
    for (const button of this.shadowRoot.querySelectorAll("[data-mode]")) {
      button.addEventListener("click", () => {
        this._mode = button.dataset.mode;
        this._invalidate();
        this._render();
      });
    }
    for (const role of ["source", "target"]) {
      on(`#${role}-device`, "change", (event) => this._chooseDevice(role, event.target.value));
      on(`#${role}-device-filter`, "input", (event) => {
        this[role === "source" ? "_sourceFilter" : "_targetFilter"] = event.target.value;
        this._query(`#${role}-device`).innerHTML = this._deviceSelectOptions(role);
      });
    }
    on("#removed-device", "input", (event) => {
      this._sourceDevice = event.target.value;
      this._resetMappings();
      this._invalidate();
    });
    on("#load-mappings", "click", () => this._loadMappings());
    on("#confirm-mappings", "change", (event) => { this._mappingConfirmed = event.target.checked; this._invalidate(); });
    on("#add-map", "click", () => {
      this._mappings.push({ source: "", target: "", source_registry_id: "", manual: true });
      this._mappingChanged();
      this._render();
    });
    for (const input of this.shadowRoot.querySelectorAll("[data-map-target]")) {
      input.addEventListener("change", () => { this._mappings[Number(input.dataset.mapTarget)].target = input.value; this._mappingChanged(); });
    }
    for (const input of this.shadowRoot.querySelectorAll("[data-map-source]")) {
      input.addEventListener("input", () => {
        const row = this._mappings[Number(input.dataset.mapSource)];
        if (row.source.split(".")[0] !== input.value.split(".")[0]) row.target = "";
        row.source = input.value;
        this._mappingChanged();
      });
      input.addEventListener("change", () => this._render());
    }
    for (const input of this.shadowRoot.querySelectorAll("[data-map-registry]")) {
      input.addEventListener("input", () => { this._mappings[Number(input.dataset.mapRegistry)].source_registry_id = input.value; this._mappingChanged(); });
    }
    for (const button of this.shadowRoot.querySelectorAll("[data-remove-map]")) {
      button.addEventListener("click", () => { this._mappings.splice(Number(button.dataset.removeMap), 1); this._mappingChanged(); this._render(); });
    }
    on("#source", "input", (event) => {
      this._source = event.target.value;
      this._query("#target-entities").innerHTML = this._options(this._targetOptions());
      this._invalidate();
    });
    on("#target", "input", (event) => { this._target = event.target.value; this._invalidate(); });
    for (const [selector, property] of [["#text", "_text"], ["#storage", "_storage"], ["#allow-unavailable", "_allowUnavailable"]]) {
      on(selector, "change", (event) => { this[property] = event.target.checked; this._invalidate(); });
    }
    on("#scan", "click", () => this._scan());
    on("#refresh", "click", () => this._load());
    on("#review", "click", () => this._reviewSelected());
    on("#apply", "click", () => this._apply());
    on("#download-report", "click", () => this._download("entity_replacer_report.md", this._preview.report, "text/markdown;charset=utf-8"));
    on("#download-json", "click", () => this._download("entity_replacer_report.json", JSON.stringify(this._preview, null, 2), "application/json"));
    on("#download-diff", "click", () => this._download("entity_replacer_selected.diff", this._review.diffs.map((item) => item.diff).join("\n")));
    for (const [selector, all] of [["#select-all", true], ["#select-none", false]]) {
      on(selector, "click", () => {
        this._selected.clear();
        if (all) for (const document of this._preview.documents) for (const item of document.occurrences) if (item.writable) this._selected.add(item.id);
        this._review = null;
        this._render();
      });
    }
    for (const input of this.shadowRoot.querySelectorAll("[data-ref]")) {
      input.addEventListener("change", () => {
        this._setReference(input.dataset.ref, input.checked);
      });
    }
    for (const input of this.shadowRoot.querySelectorAll("[data-file]")) {
      input.addEventListener("change", () => {
        const file = this._preview.documents.find((item) => item.key === input.dataset.file);
        for (const item of file.occurrences) {
          if (item.writable) input.checked ? this._selected.add(item.id) : this._selected.delete(item.id);
        }
        for (const check of this.shadowRoot.querySelectorAll("[data-ref]")) check.checked = this._selected.has(check.dataset.ref);
        this._selectionChanged();
        this._updateFileChecks();
      });
    }
    this._updateFileChecks();
    const restore = this._query("#restore");
    if (restore) restore.disabled = true;
    on("#restore-confirm", "change", (event) => { restore.disabled = this._busy || !event.target.checked; });
    on("#backup", "change", () => {
      this._query("#restore-confirm").checked = false;
      restore.disabled = true;
    });
    on("#restore", "click", () => this._restore());
  }

  _updateFileChecks() {
    for (const input of this.shadowRoot.querySelectorAll("[data-file]")) {
      const file = this._preview?.documents.find((item) => item.key === input.dataset.file);
      const eligible = file?.occurrences.filter((item) => item.writable) || [];
      const count = eligible.filter((item) => this._selected.has(item.id)).length;
      input.checked = eligible.length > 0 && count === eligible.length;
      input.indeterminate = count > 0 && count < eligible.length;
    }
  }
}

if (!customElements.get("entity-replacer-panel")) customElements.define("entity-replacer-panel", EntityReplacerPanel);
