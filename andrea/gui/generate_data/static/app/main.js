import { $, formatBytes } from "/static-common/app/core/dom.js";
import { fetchFiles, resetFilesView } from "/static-common/app/files/explorer.js?v=20260423a";
import {
  deepEqualJson,
  readParamsFromHost,
  renderParamsHost,
  resolvedDefaultParams,
} from "/static-common/app/params/schema_form.js?v=20260423c";
import {
  initReproducibility,
  renderReproducibility,
  resetReproducibility,
} from "/static-common/app/repro/view.js";
import {
  pushRuntimeFailureToasts,
  renderRuntimeProgress,
} from "/static-common/app/runtime/view.js";
import { buildInfoTooltip, initInfoPopover, showInfoTooltip } from "/static-common/app/ui/popovers.js";
import { setActiveStep, setStepState, initSteps } from "/static-common/app/ui/steps.js";
import { pushToast } from "/static-common/app/ui/toasts.js";

const state = {
  bootstrap: null,
  simulatorsById: new Map(),
  jobId: null,
  currentJob: null,
  preflightReport: null,
  pollTimer: null,
  selectedFilePath: null,
  filesEntries: [],
  filesMode: "full",
  collapsedDirs: new Set(),
  loadedFilesKey: null,
  paramsModalCard: null,
  notifiedFailures: new Set(),
};

async function readJson(response, fallbackMessage) {
  let payload = null;
  try {
    payload = await response.json();
  } catch (_err) {
    payload = null;
  }
  if (!response.ok) {
    throw new Error(payload?.detail || fallbackMessage || `Request failed (${response.status})`);
  }
  return payload;
}

async function fetchBootstrapData() {
  const response = await fetch("/api/generate-data/bootstrap");
  return readJson(response, "Failed to load bootstrap data");
}

async function submitPreflight(formData) {
  const response = await fetch("/api/generate-data/preflight", { method: "POST", body: formData });
  return readJson(response, "Preflight submission failed");
}

async function submitPlan(body) {
  const response = await fetch("/api/generate-data/plan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return readJson(response, "Plan submission failed");
}

async function submitRun(body) {
  const response = await fetch("/api/generate-data/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return readJson(response, "Run submission failed");
}

async function fetchJob(jobId) {
  const response = await fetch(`/api/generate-data/jobs/${jobId}`);
  return readJson(response, "Failed to load job status");
}

async function fetchPlan(jobId) {
  const response = await fetch(`/api/generate-data/jobs/${jobId}/plan`);
  return readJson(response, "Failed to load plan");
}

function fileApi() {
  return {
    fetchFiles: async (mode) => {
      const response = await fetch(`/api/generate-data/jobs/${state.jobId}/files?mode=${encodeURIComponent(mode)}`);
      return readJson(response, "Failed to load files");
    },
    fetchFileContent: async (path, mode) => {
      const response = await fetch(
        `/api/generate-data/jobs/${state.jobId}/file-content?mode=${encodeURIComponent(mode)}&path=${encodeURIComponent(path)}`
      );
      return readJson(response, "Failed to load file preview");
    },
  };
}

function fileExplorerOptions() {
  return {
    preferredPathSuffixes: ["benchmark/benchmark-manifest.json"],
  };
}

function simulatorById(id) {
  return state.simulatorsById.get(String(id || ""));
}

function profileSpec(profileId) {
  return (state.bootstrap?.profiles || []).find((item) => item.id === profileId) || null;
}

function extraByKey(key) {
  return (state.bootstrap?.extras || []).find((item) => item.key === key) || null;
}

function selectedProfileId() {
  return state.preflightReport?.scenario?.profile || $("profile").value;
}

function nativeOutputDefsForSimulator(simulator, profileId = selectedProfileId()) {
  const profileCapabilities = simulator?.profile_capabilities && typeof simulator.profile_capabilities === "object"
    ? simulator.profile_capabilities
    : {};
  const capability = profileCapabilities?.[profileId];
  return Array.isArray(capability?.native_outputs)
    ? capability.native_outputs.filter((item) => item && typeof item === "object" && String(item.id || "").trim())
    : [];
}

function nativeOutputLabel(definitionOrId) {
  if (!definitionOrId) {
    return "-";
  }
  if (typeof definitionOrId === "string") {
    return definitionOrId;
  }
  return String(definitionOrId.id || "-");
}

function nativeOutputMatches(definition, params) {
  const conditions = Array.isArray(definition?.conditions) ? definition.conditions : [];
  if (!conditions.length) {
    return true;
  }
  return conditionalInputMatches(definition, params);
}

function readNativeOutputsFromHost(host) {
  if (!host) {
    return [];
  }
  return Array.from(host.querySelectorAll(".native-output-checkbox:checked"))
    .map((node) => String(node.value || "").trim())
    .filter(Boolean);
}

function renderNativeOutputsHost(host, simulator, selected = []) {
  host.innerHTML = "";
  const defs = nativeOutputDefsForSimulator(simulator);
  if (!defs.length) {
    const empty = document.createElement("div");
    empty.className = "muted-box";
    empty.textContent = "No simulator-specific native outputs are available for this profile.";
    host.appendChild(empty);
    return;
  }
  const selectedSet = new Set((selected || []).map((item) => String(item || "").trim()).filter(Boolean));
  const grid = document.createElement("div");
  grid.className = "native-output-grid";
  for (const def of defs) {
    const row = document.createElement("label");
    row.className = "native-output-row";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.className = "native-output-checkbox";
    input.value = String(def.id);
    input.checked = selectedSet.has(String(def.id));

    const meta = document.createElement("div");
    meta.className = "native-output-meta";

    const title = document.createElement("div");
    title.className = "native-output-title";
    const name = document.createElement("strong");
    name.textContent = nativeOutputLabel(def);
    title.appendChild(name);

    const desc = document.createElement("div");
    desc.className = "native-output-desc";
    const formats = Array.isArray(def.formats) && def.formats.length ? ` Formats: ${def.formats.join(", ")}.` : "";
    desc.textContent = `${String(def.description || "").trim()}${formats}`.trim();

    meta.appendChild(title);
    meta.appendChild(desc);

    const conditionText = formatInputConditions(def);
    if (conditionText) {
      const conditions = document.createElement("div");
      conditions.className = "native-output-notes";
      conditions.textContent = `Available when ${conditionText}.`;
      meta.appendChild(conditions);
    }

    if (def.notes) {
      const notes = document.createElement("div");
      notes.className = "native-output-notes";
      notes.textContent = String(def.notes);
      meta.appendChild(notes);
    }

    row.appendChild(input);
    row.appendChild(meta);
    grid.appendChild(row);
  }
  host.appendChild(grid);
}

function updateRunNativeOutputsSummary(card, simulator, selected = null) {
  const summary = card.querySelector(".run-native-outputs-summary");
  if (!summary) {
    return;
  }
  const defs = nativeOutputDefsForSimulator(simulator);
  if (!defs.length) {
    summary.textContent = "No simulator-specific native outputs available for this profile.";
    return;
  }
  const selectedIds = selected || readNativeOutputsFromHost(card.querySelector(".run-native-outputs-form"));
  if (!selectedIds.length) {
    summary.textContent = "No native outputs selected.";
    return;
  }
  const defsById = new Map(defs.map((item) => [String(item.id), item]));
  summary.textContent = selectedIds
    .map((outputId) => nativeOutputLabel(defsById.get(String(outputId)) || String(outputId)))
    .join(", ");
}

function renderCardNativeOutputs(card, simulator, selected = null) {
  const host = card.querySelector(".run-native-outputs-form");
  renderNativeOutputsHost(host, simulator, selected || []);
  updateRunNativeOutputsSummary(card, simulator, readNativeOutputsFromHost(host));
}

function simulatorInputById(inputId) {
  return (state.bootstrap?.simulator_inputs || []).find((item) => item.id === String(inputId || "")) || null;
}

function checkedExtras() {
  return Array.from(document.querySelectorAll(".extra-checkbox:checked")).map((node) => node.value);
}

function resetScenarioDerivedState() {
  if (state.pollTimer) {
    window.clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
  state.jobId = null;
  state.currentJob = null;
  state.preflightReport = null;
  state.notifiedFailures = new Set();
  state.loadedFilesKey = null;
  $("runs-container").innerHTML = "";
  updateRunsEmptyState();
  renderSimulatorEligibility(null);
  renderPlan(null);
  renderRuntimeProgress(null);
  renderExecutionAlerts(null);
  updateExplorerVisibility(null);
  syncButtons();
}

function renderExtras() {
  const host = $("extras-grid");
  host.innerHTML = "";
  const selectedProfile = $("profile").value;
  const profile = profileSpec(selectedProfile);
  const required = new Set(profile?.required_extras || []);
  const available = (profile?.available_extras || [])
    .map((key) => extraByKey(key))
    .filter(Boolean);
  $("extras-empty").hidden = available.length > 0;
  for (const extra of available) {
    const row = document.createElement("label");
    row.className = "checkbox-row";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.className = "extra-checkbox";
    input.value = extra.key;
    input.checked = required.has(extra.key);
    input.disabled = required.has(extra.key);
    input.addEventListener("change", resetScenarioDerivedState);
    const text = document.createElement("span");
    const title = document.createElement("div");
    title.className = "checkbox-title";
    title.textContent = extra.label;
    const desc = document.createElement("div");
    desc.className = "checkbox-desc";
    desc.textContent = required.has(extra.key)
      ? `${extra.description} Required by ${selectedProfile}.`
      : extra.description;
    text.appendChild(title);
    text.appendChild(desc);
    row.appendChild(input);
    row.appendChild(text);
    host.appendChild(row);
  }
}

function initBootstrapView() {
  const profileSelect = $("profile");
  profileSelect.innerHTML = "";
  for (const profile of state.bootstrap.profiles || []) {
    const option = document.createElement("option");
    option.value = profile.id;
    option.textContent = profile.id;
    profileSelect.appendChild(option);
  }
  if ((state.bootstrap.profiles || []).some((item) => item.id === "scrna_grouped")) {
    profileSelect.value = "scrna_grouped";
  }
  profileSelect.addEventListener("change", renderExtras);
  profileSelect.addEventListener("change", resetScenarioDerivedState);
  const defaultMaxParallel = Number.parseInt(
    String(state.bootstrap?.planning_defaults?.max_parallel_tasks || ""),
    10
  );
  if (Number.isInteger(defaultMaxParallel) && defaultMaxParallel >= 1) {
    $("max-parallel-tasks").value = String(defaultMaxParallel);
  }
  const defaultMaxCores = Number.parseInt(String(state.bootstrap?.planning_defaults?.max_cores || ""), 10);
  if (Number.isInteger(defaultMaxCores) && defaultMaxCores >= 1) {
    $("max-cores").value = String(defaultMaxCores);
  }
  const defaultMaxRam = Number(state.bootstrap?.planning_defaults?.max_ram_gb || 0);
  if (Number.isFinite(defaultMaxRam) && defaultMaxRam >= 1) {
    $("max-ram-gb").value = String(defaultMaxRam);
  }
  renderExtras();
  populateSimulatorIssueSelect();
}

function updateInputsEmptyState() {
  $("inputs-empty").style.display = selectedInputRows().length ? "none" : "block";
}

function selectedInputRows() {
  return Array.from(document.querySelectorAll("#inputs-list .extra-row"));
}

function selectedInputIds() {
  return new Set(
    selectedInputRows()
      .map((row) => String(row.querySelector(".input-kind")?.value || "").trim())
      .filter(Boolean)
  );
}

function rowRequiresOrganism(row) {
  const meta = simulatorInputById(row.querySelector(".input-kind")?.value);
  return Boolean(meta?.requires_organism);
}

function updateOrganismRequirement(row = null) {
  const rows = row ? [row] : selectedInputRows();
  for (const currentRow of rows) {
    const box = currentRow.querySelector(".input-organism-box");
    const required = rowRequiresOrganism(currentRow);
    if (!box) {
      continue;
    }
    box.hidden = !required;
    currentRow.classList.toggle("requires-organism", required);
    if (required) {
      box.open = true;
    }
  }
}

function rowOrganismPayload(row) {
  const taxonomicGroup = row.querySelector(".taxonomic-group")?.value || "synthetic";
  const taxIdRaw = row.querySelector(".organism-ncbi-taxon-id")?.value.trim() || "";
  return {
    taxonomic_group: taxonomicGroup,
    ncbi_taxon_id: taxIdRaw ? Number.parseInt(taxIdRaw, 10) : null,
  };
}

function providedSimulatorInputIds() {
  return new Set(
    selectedInputRows()
      .map((row) => {
        const inputId = String(row.querySelector(".input-kind")?.value || "").trim();
        const file = row.querySelector(".input-file")?.files?.[0] || null;
        return inputId && file ? inputId : "";
      })
      .filter(Boolean)
  );
}

function inputUsageToolCount(meta) {
  const usedBy = meta?.used_by && typeof meta.used_by === "object" ? meta.used_by : {};
  const ids = new Set();
  for (const relation of ["required", "optional", "conditional"]) {
    const items = Array.isArray(usedBy[relation]) ? usedBy[relation] : [];
    for (const item of items) {
      const id = String(item?.simulator_id || item?.name || "").trim();
      if (id) {
        ids.add(id);
      }
    }
  }
  return ids.size;
}

function inputRelationLabel(relation) {
  if (relation === "conditional") {
    return "Conditional required";
  }
  return relation === "required" ? "Required" : "Optional";
}

function appendInputDetailField(parent, labelText, valueText, { code = false } = {}) {
  const normalized = String(valueText ?? "").trim();
  if (!normalized) {
    return;
  }
  const label = document.createElement("dt");
  label.textContent = labelText;
  const value = document.createElement("dd");
  if (code) {
    const codeEl = document.createElement("code");
    codeEl.textContent = normalized;
    value.appendChild(codeEl);
  } else {
    value.textContent = normalized;
  }
  parent.append(label, value);
}

function formatInputConditions(item) {
  const conditions = Array.isArray(item?.conditions) ? item.conditions : [];
  return conditions.map(formatSimulatorInputCondition).filter(Boolean).join(" AND ");
}

function renderInputUsageDetail(detailPanel, item, relation) {
  detailPanel.innerHTML = "";
  const title = document.createElement("div");
  title.className = "input-usage-detail-head";
  const name = document.createElement("strong");
  name.textContent = String(item?.name || item?.simulator_id || "").trim();
  const badge = document.createElement("span");
  badge.className = `input-usage-relation ${relation}`;
  badge.textContent = inputRelationLabel(relation);
  title.append(name, badge);

  const usage = document.createElement("p");
  usage.textContent = String(item?.usage || "").trim() || "No usage note available.";
  detailPanel.append(title, usage);

  if (relation === "conditional") {
    const details = document.createElement("dl");
    details.className = "input-usage-detail-meta";
    appendInputDetailField(details, "Condition", formatInputConditions(item), { code: true });
    appendInputDetailField(details, "Message", item?.message);
    if (details.children.length) {
      detailPanel.appendChild(details);
    }
  }
  detailPanel.hidden = false;
}

function inputUsageTag(item, relation, detailPanel) {
  const tag = document.createElement("button");
  tag.type = "button";
  tag.className = `input-tool-tag ${relation}`;
  tag.textContent = String(item?.name || item?.simulator_id || "").trim();
  tag.addEventListener("click", () => {
    const activeTags = tag.closest(".input-catalog-card")?.querySelectorAll(".input-tool-tag.active") || [];
    for (const activeTag of activeTags) {
      activeTag.classList.remove("active");
    }
    tag.classList.add("active");
    renderInputUsageDetail(detailPanel, item, relation);
  });
  return tag;
}

function renderInputUsage(meta) {
  const usedBy = meta?.used_by && typeof meta.used_by === "object" ? meta.used_by : {};
  const groups = [
    ["required", "Required by"],
    ["optional", "Optional for"],
    ["conditional", "Conditional for"],
  ];
  const host = document.createElement("div");
  host.className = "input-usage-groups";
  const detailPanel = document.createElement("div");
  detailPanel.className = "input-usage-detail";
  detailPanel.hidden = true;
  let hasUsage = false;
  for (const [relation, label] of groups) {
    const items = Array.isArray(usedBy[relation]) ? usedBy[relation] : [];
    if (!items.length) {
      continue;
    }
    hasUsage = true;
    const group = document.createElement("div");
    group.className = "input-usage-group";
    const title = document.createElement("div");
    title.className = "input-usage-title";
    title.textContent = label;
    const tags = document.createElement("div");
    tags.className = "input-tool-tags";
    for (const item of items) {
      tags.appendChild(inputUsageTag(item, relation, detailPanel));
    }
    group.append(title, tags);
    host.appendChild(group);
  }
  if (!hasUsage) {
    const empty = document.createElement("div");
    empty.className = "input-usage-empty";
    empty.textContent = "No catalog simulator currently declares this input.";
    host.appendChild(empty);
  }
  host.appendChild(detailPanel);
  return host;
}

function renderInputModalBody() {
  const body = $("input-modal-body");
  if (!body) {
    return;
  }
  const metas = Array.isArray(state.bootstrap?.simulator_inputs)
    ? [...state.bootstrap.simulator_inputs]
    : [];
  const added = selectedInputIds();
  body.innerHTML = "";
  if (!metas.length) {
    const empty = document.createElement("div");
    empty.className = "muted-box";
    empty.textContent = "No simulator input specs are available.";
    body.appendChild(empty);
    return;
  }
  metas.sort((a, b) => {
    const usageDelta = inputUsageToolCount(b) - inputUsageToolCount(a);
    if (usageDelta !== 0) {
      return usageDelta;
    }
    return String(a.id || "").localeCompare(String(b.id || ""));
  });
  for (const meta of metas) {
    const inputId = String(meta.id || "").trim();
    if (!inputId) {
      continue;
    }
    const card = document.createElement("article");
    card.className = "input-catalog-card";

    const head = document.createElement("div");
    head.className = "input-catalog-card-head";
    const title = document.createElement("div");
    title.className = "input-catalog-card-title";
    title.textContent = meta.label || inputId;
    const actions = document.createElement("div");
    actions.className = "input-catalog-card-actions";

    const infoBtn = document.createElement("button");
    infoBtn.type = "button";
    infoBtn.className = "info-icon";
    infoBtn.textContent = "i";
    infoBtn.setAttribute("aria-label", `Show ${inputId} example`);
    infoBtn.addEventListener("click", () => {
      const formats = Array.isArray(meta.formats) && meta.formats.length ? meta.formats.join(", ") : "any";
      showInfoTooltip(
        buildInfoTooltip({
          title: `${inputId} input`,
          description: `Accepted formats: ${formats}.`,
          example: String(meta.example || "").trim() || "No example available.",
        })
      );
    });

    const addBtn = document.createElement("button");
    addBtn.type = "button";
    addBtn.className = "secondary";
    addBtn.textContent = added.has(inputId) ? "Added" : "Add";
    addBtn.disabled = added.has(inputId);
    addBtn.addEventListener("click", () => {
      addInputRow(inputId);
      closeInputModal();
    });
    actions.append(infoBtn, addBtn);
    head.append(title, actions);

    const description = document.createElement("p");
    description.className = "input-catalog-description";
    description.textContent = String(meta.description || "Simulator input file.").trim();

    const format = document.createElement("div");
    format.className = "input-catalog-format";
    const formats = Array.isArray(meta.formats) && meta.formats.length ? meta.formats.join(", ") : "any";
    format.textContent = `Formats: ${formats}`;

    card.append(head, description, format, renderInputUsage(meta));
    body.appendChild(card);
  }
}

function openInputModal() {
  renderInputModalBody();
  openModal("input-modal");
}

function closeInputModal() {
  closeModal("input-modal");
}

function setInputRowState(row, stateName) {
  row.classList.remove("missing", "valid", "invalid");
  if (stateName) {
    row.classList.add(stateName);
  }
}

function syncInputRowMeta(row) {
  const inputId = String(row.querySelector(".input-kind")?.value || "").trim();
  const meta = simulatorInputById(inputId) || {};
  const label = row.querySelector(".input-kind-label");
  const description = row.querySelector(".input-kind-description");
  const descriptionInput = row.querySelector(".input-description");
  const fileInput = row.querySelector(".input-file");
  const fileName = row.querySelector(".extra-file-name");
  const pickerName = row.querySelector(".extra-file-picker-name");
  const status = row.querySelector(".input-file-status");
  const file = fileInput?.files?.[0] || null;

  label.textContent = String(meta.label || inputId);
  const formats = Array.isArray(meta.formats) && meta.formats.length ? ` Formats: ${meta.formats.join(", ")}.` : "";
  const descriptionText = `${String(meta.description || "Simulator input file.").trim()}${formats}`.trim();
  description.textContent = descriptionText;
  descriptionInput.value = String(meta.description || "");
  fileInput.accept = String(meta.accept || "");

  const fileLabel = file ? `${file.name} (${formatBytes(file.size)})` : "No file selected";
  fileName.textContent = fileLabel;
  pickerName.textContent = file ? file.name : "No file selected";
  if (!file) {
    setInputRowState(row, "missing");
    status.classList.remove("ok");
    status.classList.add("err");
    status.textContent = "Select a file";
  } else {
    setInputRowState(row, "valid");
    status.classList.remove("err");
    status.classList.add("ok");
    status.textContent = "Ready";
  }
  updateOrganismRequirement(row);
}

function addInputRow(inputId) {
  const selectedId = String(inputId || "").trim();
  const meta = simulatorInputById(selectedId);
  if (!meta) {
    pushToast({
      title: "Simulator input",
      message: selectedId ? `Unknown simulator input '${selectedId}'.` : "No simulator input selected.",
      kind: "error",
      ttlMs: 5000,
    });
    return;
  }
  if (selectedInputIds().has(selectedId)) {
    pushToast({
      title: "Simulator input",
      message: `${meta.label || selectedId} is already added.`,
      kind: "warning",
      ttlMs: 4500,
    });
    return;
  }
  const template = $("input-template");
  const node = template.content.firstElementChild.cloneNode(true);
  node.querySelector(".input-kind").value = selectedId;
  const infoBtn = node.querySelector(".input-info-btn");
  infoBtn.addEventListener("click", () => {
    const formats = Array.isArray(meta.formats) && meta.formats.length ? meta.formats.join(", ") : "any";
    showInfoTooltip(
      buildInfoTooltip({
        title: `${selectedId} input`,
        description: `Accepted formats: ${formats}. ${String(meta.description || "").trim()}`.trim(),
        example: String(meta.example || "").trim() || "No example available.",
      })
    );
  });
  node.querySelector(".input-file").addEventListener("change", () => {
    syncInputRowMeta(node);
    resetScenarioDerivedState();
  });
  node.querySelector(".taxonomic-group")?.addEventListener("change", resetScenarioDerivedState);
  node.querySelector(".organism-ncbi-taxon-id")?.addEventListener("input", resetScenarioDerivedState);
  node.querySelector(".remove-input").addEventListener("click", () => {
    node.remove();
    updateInputsEmptyState();
    updateOrganismRequirement();
    renderInputModalBody();
    resetScenarioDerivedState();
  });
  $("inputs-list").appendChild(node);
  syncInputRowMeta(node);
  updateInputsEmptyState();
  renderInputModalBody();
  resetScenarioDerivedState();
}

function valueAtPath(payload, rawPath) {
  const parts = String(rawPath || "").trim().split(".").map((item) => item.trim()).filter(Boolean);
  let current = payload;
  for (const part of parts) {
    if (!current || typeof current !== "object" || !(part in current)) {
      return null;
    }
    current = current[part];
  }
  return current;
}

function compareConditionValue(actual, op, expected) {
  if (op === "eq") {
    return actual === expected;
  }
  if (op === "ne") {
    return actual !== expected;
  }
  if (op === "in") {
    return Array.isArray(expected) && expected.includes(actual);
  }
  if (op === "not_in") {
    return Array.isArray(expected) && !expected.includes(actual);
  }
  if (["gt", "gte", "lt", "lte"].includes(op)) {
    const actualNum = Number(actual);
    const expectedNum = Number(expected);
    if (!Number.isFinite(actualNum) || !Number.isFinite(expectedNum)) {
      return false;
    }
    if (op === "gt") {
      return actualNum > expectedNum;
    }
    if (op === "gte") {
      return actualNum >= expectedNum;
    }
    if (op === "lt") {
      return actualNum < expectedNum;
    }
    if (op === "lte") {
      return actualNum <= expectedNum;
    }
  }
  return false;
}

function conditionActualValue(field, params) {
  const normalized = String(field || "").trim();
  if (normalized === "profile") {
    return selectedProfileId();
  }
  if (normalized === "requested_extra") {
    return checkedExtras().sort();
  }
  if (normalized.startsWith("param.")) {
    return valueAtPath(params, normalized.slice("param.".length));
  }
  return null;
}

function conditionalInputMatches(requirement, params) {
  const conditions = Array.isArray(requirement?.conditions) ? requirement.conditions : [];
  if (!conditions.length) {
    return false;
  }
  const requestedExtras = new Set(checkedExtras());
  for (const condition of conditions) {
    if (!condition || typeof condition !== "object") {
      return false;
    }
    const field = String(condition.field || "").trim();
    const op = String(condition.op || "").trim();
    const expected = condition.value;
    if (field === "requested_extra" && op === "eq") {
      if (!requestedExtras.has(String(expected))) {
        return false;
      }
      continue;
    }
    if (field === "requested_extra" && op === "ne") {
      if (requestedExtras.has(String(expected))) {
        return false;
      }
      continue;
    }
    if (field === "requested_extra" && op === "in") {
      const values = Array.isArray(expected) ? expected.map((item) => String(item)) : [];
      if (!values.some((item) => requestedExtras.has(item))) {
        return false;
      }
      continue;
    }
    if (field === "requested_extra" && op === "not_in") {
      const values = Array.isArray(expected) ? expected.map((item) => String(item)) : [];
      if (values.some((item) => requestedExtras.has(item))) {
        return false;
      }
      continue;
    }
    if (!compareConditionValue(conditionActualValue(field, params), op, expected)) {
      return false;
    }
  }
  return true;
}

function simulatorInputMessages(simulator, params) {
  const simulatorInputs = simulator?.simulator_inputs && typeof simulator.simulator_inputs === "object"
    ? simulator.simulator_inputs
    : {};
  const providedInputs = providedSimulatorInputIds();
  const messages = [];
  const requiredInputs = Array.isArray(simulatorInputs.required) ? simulatorInputs.required : [];
  for (const item of requiredInputs) {
    const inputId = String(item?.input || "").trim();
    if (inputId && !providedInputs.has(inputId)) {
      messages.push(String(item?.message || `Missing input: ${inputId}`).trim());
    }
  }
  const conditionalInputs = Array.isArray(simulatorInputs.conditional_required)
    ? simulatorInputs.conditional_required
    : [];
  for (const requirement of conditionalInputs) {
    const inputId = String(requirement?.input || "").trim();
    if (!inputId || providedInputs.has(inputId)) {
      continue;
    }
    if (conditionalInputMatches(requirement, params)) {
      messages.push(
        String(requirement?.message || `Missing conditionally required input: ${inputId}`).trim()
      );
    }
  }
  return messages.filter(Boolean);
}

function validateScenarioForm() {
  const benchmarkId = $("benchmark-id").value.trim();
  if (!benchmarkId) {
    throw new Error("Benchmark ID is required.");
  }
  const seenInputs = new Set();
  for (const row of selectedInputRows()) {
    const inputId = row.querySelector(".input-kind").value;
    const label = simulatorInputById(inputId)?.label || inputId;
    if (seenInputs.has(inputId)) {
      throw new Error(`Duplicate input file type: ${label}`);
    }
    seenInputs.add(inputId);
    if (!row.querySelector(".input-file").files?.[0]) {
      throw new Error(`Input file is required for ${label}.`);
    }
    if (rowRequiresOrganism(row)) {
      const organism = rowOrganismPayload(row);
      if (!["synthetic", "unknown"].includes(organism.taxonomic_group) && !organism.ncbi_taxon_id) {
        throw new Error(`NCBI Taxon ID is required for ${label} when taxonomic group is biological.`);
      }
    }
  }
}

function collectScenarioConfig() {
  const organismRow = selectedInputRows().find((row) => rowRequiresOrganism(row)) || null;
  const organism = organismRow
    ? rowOrganismPayload(organismRow)
    : { taxonomic_group: "synthetic", ncbi_taxon_id: null };
  const scenario = {
    id: $("benchmark-id").value.trim(),
    profile: $("profile").value,
    requested_extras: checkedExtras(),
    organism,
  };
  const baseSeed = $("base-seed").value.trim();
  if (baseSeed) {
    scenario.base_seed = Number.parseInt(baseSeed, 10);
  }
  const inputs = {};
  selectedInputRows().forEach((row) => {
    const inputId = row.querySelector(".input-kind").value.trim();
    if (!inputId) {
      return;
    }
    const meta = simulatorInputById(inputId) || {};
    inputs[inputId] = {
      path: `uploaded:${inputId}`,
      format: Array.isArray(meta.formats) && meta.formats.length ? meta.formats[0] : undefined,
      description: meta.description || undefined,
    };
    if (rowRequiresOrganism(row)) {
      inputs[inputId].organism = rowOrganismPayload(row);
    }
    const description = row.querySelector(".input-description").value.trim();
    if (description) {
      inputs[inputId].description = description;
    }
  });
  if (Object.keys(inputs).length) {
    scenario.inputs = inputs;
  }
  return {
    scenario,
    options: {
      output_dir: $("output-dir").value.trim() || "./benchmarks",
    },
  };
}

function buildPreflightFormData() {
  validateScenarioForm();
  const formData = new FormData();
  formData.append("config", JSON.stringify(collectScenarioConfig()));
  selectedInputRows().forEach((row) => {
    const inputId = row.querySelector(".input-kind").value.trim();
    const file = row.querySelector(".input-file").files?.[0];
    if (inputId && file) {
      formData.append(`input__${inputId}`, file);
    }
  });
  return formData;
}

function simulatorInfoPayload(simulator) {
  const profileCapabilities = simulator.profile_capabilities && typeof simulator.profile_capabilities === "object"
    ? simulator.profile_capabilities
    : {};
  const publications = Array.isArray(simulator.publication) ? simulator.publication : [];
  const keywords = Array.isArray(simulator.simulation_keywords) ? simulator.simulation_keywords : [];
  const rawInputs = simulator.simulator_inputs && typeof simulator.simulator_inputs === "object"
    ? simulator.simulator_inputs
    : {};
  const params = simulator?.params_schema && typeof simulator.params_schema === "object" ? simulator.params_schema : {};
  const profileIds = Object.keys(profileCapabilities);
  const requiredInputs = (Array.isArray(rawInputs.required) ? rawInputs.required : [])
    .map((item) => simulatorInputSummary(item))
    .filter(Boolean);
  const optionalInputs = (Array.isArray(rawInputs.optional) ? rawInputs.optional : [])
    .map((item) => simulatorInputSummary(item))
    .filter(Boolean);
  const conditionalInputs = (Array.isArray(rawInputs.conditional_required) ? rawInputs.conditional_required : [])
    .map((item) => conditionalSimulatorInputDetail(item))
    .filter(Boolean);
  return buildInfoTooltip({
    title: simulator.name || simulator.simulator_id || "Simulator Info",
    description: String(simulator.simulation_summary || "").trim(),
    chips: [
      { label: "id", value: simulator.simulator_id || "-" },
      { label: "year", value: simulator.year ? String(simulator.year) : "-" },
      { label: "profiles", value: profileIds.length ? String(profileIds.length) : "0" },
    ],
    sections: [
      {
        title: "Overview",
        open: true,
        fields: [
          { label: "Schema version", value: simulator.schema_version || "-" },
          {
            label: "Publication(s)",
            links: publications.length
              ? publications.map((item) => ({ label: String(item || "").trim(), url: String(item || "").trim() }))
              : [{ label: "-", url: "" }],
          },
          { label: "First author", value: simulator.first_author || "-" },
          { label: "Publication year", value: simulator.year ? String(simulator.year) : "-" },
          { label: "Keywords", value: keywords.length ? keywords.join(", ") : "-" },
          { label: "Runtime resources", value: simulatorRuntimeResourceSummary(simulator.runtime_resources) },
          {
            label: "Implementation",
            link: {
              label: String(simulator.implementation_url || "-"),
              url: String(simulator.implementation_url || ""),
            },
            value: simulator.implementation_url || "-",
          },
          { label: "Docker image", value: simulator.docker_image || "-" },
          { label: "Notes", value: simulatorNotesSummary(simulator.notes) },
        ],
      },
      {
        title: "Simulator Inputs",
        open: true,
        fields: [
          { label: "Required", value: requiredInputs.length ? requiredInputs.join("\n") : "none" },
          { label: "Optional", value: optionalInputs.length ? optionalInputs.join("\n") : "none" },
        ],
        conditionsLabel: "Conditional required inputs",
        conditions: conditionalInputs,
      },
      {
        title: "Parameters",
        open: false,
        text: Object.keys(params).length ? "" : "No parameters declared.",
        params,
      },
    ],
    raw: simulator.spec || null,
    example: "",
  });
}

function simulatorRuntimeResourceSummary(resources) {
  const threading = resources?.threading && typeof resources.threading === "object"
    ? resources.threading
    : {};
  const supported = Boolean(threading.supported);
  const defaultThreads = threading.default_threads ?? 1;
  const maxThreads = threading.max_threads ?? 1;
  const mapping = String(threading.upstream_mapping || "").trim();
  return [
    `threading: ${supported ? "supported" : "not supported"}`,
    `default_threads: ${defaultThreads}`,
    `max_threads: ${maxThreads}`,
    mapping ? `mapping: ${mapping}` : "",
  ].filter(Boolean).join("\n");
}

function simulatorNotesSummary(notes) {
  if (Array.isArray(notes)) {
    return notes.map((item) => String(item || "").trim()).filter(Boolean).join("\n") || "none";
  }
  return String(notes || "").trim() || "none";
}

function simulatorInputSummary(item) {
  if (!item || typeof item !== "object") {
    return "";
  }
  const id = String(item.input || "").trim();
  const description = String(item.usage || item.message || "").trim();
  return [id, description].filter(Boolean).join(": ");
}

function conditionalSimulatorInputDetail(rule) {
  if (!rule || typeof rule !== "object") {
    return null;
  }
  const input = String(rule.input || "").trim();
  const message = String(rule.message || "").trim();
  const conditions = Array.isArray(rule.conditions)
    ? rule.conditions.map(formatSimulatorInputCondition).filter(Boolean)
    : [];
  const condition = conditions.join(" AND ");
  return input || condition || message
    ? { input, condition, message }
    : null;
}

function formatSimulatorInputCondition(condition) {
  if (!condition || typeof condition !== "object") {
    return "";
  }
  const field = String(condition.field || "").trim();
  const op = String(condition.op || "").trim();
  const value = condition.value === undefined ? "" : JSON.stringify(condition.value);
  return field && op ? `${field} ${formatConditionalOperator(op)} ${value}` : "";
}

function formatConditionalOperator(op) {
  const normalized = String(op || "").trim();
  const labels = {
    eq: "==",
    ne: "!=",
    in: "in",
    not_in: "not in",
    gt: ">",
    gte: ">=",
    lt: "<",
    lte: "<=",
  };
  return labels[normalized] || normalized;
}

function profileDerivations(capability) {
  const items = Array.isArray(capability?.derivations) ? capability.derivations : [];
  const derivations = new Map();
  for (const item of items) {
    const artifact = String(item?.artifact || "").trim();
    if (artifact && !derivations.has(artifact)) {
      derivations.set(artifact, item);
    }
  }
  return derivations;
}

function artifactDisplayLabel(artifact) {
  const extra = extraByKey(artifact);
  if (extra?.label) {
    return extra.label;
  }
  const truthLabels = {
    global_network: "global_network.csv",
    group_networks: "group_networks/*.csv",
  };
  return truthLabels[artifact] || artifact;
}

function artifactHasDetail(item, derivation) {
  if (derivation) {
    return true;
  }
  return Boolean(
    String(item.kind || "").trim()
    || String(item.description || "").trim()
    || String(item.notes || "").trim()
    || (Array.isArray(item.formats) && item.formats.length)
  );
}

function appendArtifactDetailContent(detailBox, item, artifact, derivation) {
  detailBox.hidden = false;
  detailBox.dataset.artifact = artifact;
  detailBox.innerHTML = "";

  const title = document.createElement("div");
  title.className = "simulator-derivation-head";
  const titleText = document.createElement("strong");
  titleText.textContent = `${String(item.label || artifactDisplayLabel(artifact))} details`;
  const closeBtn = document.createElement("button");
  closeBtn.type = "button";
  closeBtn.className = "simulator-derivation-close";
  closeBtn.textContent = "x";
  closeBtn.addEventListener("click", () => {
    detailBox.hidden = true;
    detailBox.innerHTML = "";
    detailBox.dataset.artifact = "";
  });
  title.appendChild(titleText);
  title.appendChild(closeBtn);
  detailBox.appendChild(title);

  const description = String(item.description || "").trim();
  if (description) {
    const desc = document.createElement("p");
    desc.className = "simulator-derivation-method";
    desc.textContent = description;
    detailBox.appendChild(desc);
  }

  const metadata = [
    ["Kind", String(item.kind || "").trim()],
    ["Formats", Array.isArray(item.formats) && item.formats.length ? item.formats.join(", ") : ""],
    ["Notes", String(item.notes || "").trim()],
  ].filter(([, value]) => value);
  for (const [label, value] of metadata) {
    const block = document.createElement("div");
    block.className = "simulator-derivation-block";
    const blockTitle = document.createElement("strong");
    blockTitle.textContent = label;
    const blockValue = document.createElement("div");
    blockValue.className = "simulator-derivation-impl";
    blockValue.textContent = value;
    block.appendChild(blockTitle);
    block.appendChild(blockValue);
    detailBox.appendChild(block);
  }

  if (!derivation) {
    return;
  }

  const methodText = String(derivation.method || "").trim();
  if (methodText) {
    const method = document.createElement("p");
    method.className = "simulator-derivation-method";
    method.textContent = methodText;
    detailBox.appendChild(method);
  }

  const sections = [
    ["Source artifacts", Array.isArray(derivation.source_artifacts) ? derivation.source_artifacts : []],
    ["Assumptions", Array.isArray(derivation.assumptions) ? derivation.assumptions : []],
    ["Limitations", Array.isArray(derivation.limitations) ? derivation.limitations : []],
  ];
  for (const [label, values] of sections) {
    const block = document.createElement("div");
    block.className = "simulator-derivation-block";
    const blockTitle = document.createElement("strong");
    blockTitle.textContent = label;
    block.appendChild(blockTitle);
    const list = document.createElement("ul");
    list.className = "simulator-derivation-list";
    if (values.length) {
      for (const value of values) {
        const li = document.createElement("li");
        li.textContent = String(value);
        list.appendChild(li);
      }
    } else {
      const li = document.createElement("li");
      li.textContent = "-";
      list.appendChild(li);
    }
    block.appendChild(list);
    detailBox.appendChild(block);
  }

  const impl = document.createElement("div");
  impl.className = "simulator-derivation-block";
  const implTitle = document.createElement("strong");
  implTitle.textContent = "Implemented in";
  const implValue = document.createElement("div");
  implValue.className = "simulator-derivation-impl";
  implValue.textContent = String(derivation.implemented_in || "-");
  impl.appendChild(implTitle);
  impl.appendChild(implValue);
  detailBox.appendChild(impl);
}

function appendArtifactChipRow(host, items, { derivations, simulator, profileId }) {
  if (!items.length) {
    const empty = document.createElement("span");
    empty.className = "artifact-empty";
    empty.textContent = "none";
    host.appendChild(empty);
    return;
  }
  for (const item of items) {
    const artifact = String(item.artifact || "").trim();
    if (!artifact) {
      continue;
    }
    const chip = document.createElement("span");
    chip.className = `artifact-chip mode-${item.mode || "none"}`;
    const text = document.createElement("span");
    text.textContent = String(item.label || artifactDisplayLabel(artifact));
    chip.appendChild(text);
    const derivation = derivations.get(artifact);
    if (artifactHasDetail(item, derivation)) {
      const infoBtn = document.createElement("button");
      infoBtn.type = "button";
      infoBtn.className = "artifact-chip-info";
      infoBtn.textContent = "i";
      infoBtn.title = `Details for ${artifact}`;
      infoBtn.addEventListener("click", (event) => {
        event.stopPropagation();
        const detailBox = host.closest(".simulator-capability-card")?.querySelector(".simulator-derivation-detail");
        if (!detailBox) {
          return;
        }
        const currentArtifact = detailBox.dataset.artifact || "";
        if (!detailBox.hidden && currentArtifact === artifact) {
          detailBox.hidden = true;
          detailBox.innerHTML = "";
          detailBox.dataset.artifact = "";
          return;
        }
        appendArtifactDetailContent(detailBox, item, artifact, derivation);
      });
      chip.appendChild(infoBtn);
    }
    host.appendChild(chip);
  }
}

function appendCapabilitySection(host, simulator) {
  const profileCapabilities = simulator.profile_capabilities && typeof simulator.profile_capabilities === "object"
    ? simulator.profile_capabilities
    : {};
  const profileIds = Object.keys(profileCapabilities);
  if (!profileIds.length) {
    return;
  }

  const section = document.createElement("details");
  section.className = "info-details simulator-capability-details";
  section.open = true;
  const title = document.createElement("summary");
  title.textContent = "Profile Capabilities";
  section.appendChild(title);

  const body = document.createElement("div");
  body.className = "info-details-body";

  const cards = document.createElement("div");
  cards.className = "simulator-capability-list";
  for (const profileId of profileIds) {
    const capability = profileCapabilities[profileId] || {};
    const derivations = profileDerivations(capability);
    const card = document.createElement("article");
    card.className = "simulator-capability-card";

    const header = document.createElement("div");
    header.className = "simulator-capability-head";
    const profileTitle = document.createElement("h6");
    profileTitle.textContent = profileId;
    header.appendChild(profileTitle);
    card.appendChild(header);

    const notes = String(capability.notes || "").trim();
    if (notes) {
      const notesNode = document.createElement("p");
      notesNode.className = "simulator-capability-notes";
      notesNode.textContent = notes;
      card.appendChild(notesNode);
    }

    const extrasRow = document.createElement("div");
    extrasRow.className = "simulator-capability-row";
    const extrasLabel = document.createElement("strong");
    extrasLabel.textContent = "Optional outputs";
    const extrasWrap = document.createElement("div");
    extrasWrap.className = "artifact-chip-row";
    const nativeExtras = Array.isArray(capability.native_extras) ? capability.native_extras : [];
    const derivableExtras = Array.isArray(capability.derivable_extras) ? capability.derivable_extras : [];
    appendArtifactChipRow(
      extrasWrap,
      [
        ...nativeExtras.map((artifact) => ({ artifact, mode: "native" })),
        ...derivableExtras.map((artifact) => ({ artifact, mode: "derivable" })),
      ],
      { derivations, simulator, profileId }
    );
    extrasRow.appendChild(extrasLabel);
    extrasRow.appendChild(extrasWrap);
    card.appendChild(extrasRow);

    const nativeOutputRow = document.createElement("div");
    nativeOutputRow.className = "simulator-capability-row";
    const nativeOutputLabel = document.createElement("strong");
    nativeOutputLabel.textContent = "Native outputs";
    const nativeOutputWrap = document.createElement("div");
    nativeOutputWrap.className = "artifact-chip-row";
    const nativeOutputs = Array.isArray(capability.native_outputs) ? capability.native_outputs : [];
    appendArtifactChipRow(
      nativeOutputWrap,
      nativeOutputs.map((item) => ({
        artifact: String(item?.id || "").trim(),
        label: String(item?.id || "").trim(),
        kind: String(item?.kind || "").trim(),
        description: String(item?.description || "").trim(),
        formats: Array.isArray(item?.formats) ? item.formats : [],
        notes: String(item?.notes || "").trim(),
        mode: "native",
      })),
      { derivations, simulator, profileId }
    );
    nativeOutputRow.appendChild(nativeOutputLabel);
    nativeOutputRow.appendChild(nativeOutputWrap);
    card.appendChild(nativeOutputRow);

    const truthRow = document.createElement("div");
    truthRow.className = "simulator-capability-row";
    const truthLabel = document.createElement("strong");
    truthLabel.textContent = "Truth outputs";
    const truthWrap = document.createElement("div");
    truthWrap.className = "artifact-chip-row";
    const truthEntries = Object.entries(capability.truth_outputs || {})
      .filter(([, mode]) => mode && mode !== "none")
      .map(([artifact, mode]) => ({ artifact, mode }));
    appendArtifactChipRow(truthWrap, truthEntries, { derivations, simulator, profileId });
    truthRow.appendChild(truthLabel);
    truthRow.appendChild(truthWrap);
    card.appendChild(truthRow);

    const auxArtifacts = Array.isArray(capability.artifacts_aux) ? capability.artifacts_aux : [];
    const auxRow = document.createElement("div");
    auxRow.className = "simulator-capability-row";
    const auxLabel = document.createElement("strong");
    auxLabel.textContent = "Auxiliary artifacts";
    const auxWrap = document.createElement("div");
    auxWrap.className = "artifact-chip-row";
    appendArtifactChipRow(
      auxWrap,
      auxArtifacts.map((item) => ({
        artifact: String(item?.id || item?.path_pattern || "").trim(),
        label: String(item?.id || item?.path_pattern || "").trim(),
        kind: String(item?.kind || "").trim(),
        description: String(item?.description || "").trim(),
        formats: Array.isArray(item?.formats) ? item.formats : [],
        notes: String(item?.notes || "").trim(),
        mode: "native",
      })),
      { derivations, simulator, profileId }
    );
    auxRow.appendChild(auxLabel);
    auxRow.appendChild(auxWrap);
    card.appendChild(auxRow);

    const detailBox = document.createElement("div");
    detailBox.className = "simulator-derivation-detail";
    detailBox.hidden = true;
    detailBox.dataset.artifact = "";
    card.appendChild(detailBox);

    cards.appendChild(card);
  }
  body.appendChild(cards);
  section.appendChild(body);
  const rawSection = Array.from(host.children).find((child) => (
    child.classList?.contains("info-details")
    && child.querySelector("summary")?.textContent === "Raw Spec"
  ));
  host.insertBefore(section, rawSection || null);
}

function showSimulatorInfo(simulator) {
  showInfoTooltip(simulatorInfoPayload(simulator));
  const content = $("info-popover-content");
  if (!content) {
    return;
  }
  appendCapabilitySection(content, simulator);
}

function populateSimulatorIssueSelect() {
  const select = $("simulator-issue-id");
  if (!select) {
    return;
  }
  select.innerHTML = "";
  for (const simulator of state.bootstrap?.simulators || []) {
    const option = document.createElement("option");
    option.value = simulator.simulator_id;
    option.textContent = simulator.name || simulator.simulator_id;
    select.appendChild(option);
  }
}

function openModal(id) {
  $(id)?.classList.remove("hidden");
}

function closeModal(id) {
  $(id)?.classList.add("hidden");
}

function buildSimulatorRequestIssueUrl() {
  const simulatorName = String($("simulator-request-name")?.value || "").trim();
  const doi = String($("simulator-request-doi")?.value || "").trim();
  const repoUrl = String($("simulator-request-repo")?.value || "").trim();
  const expectedProfiles = String($("simulator-request-profiles")?.value || "").trim();
  const expectedArtifacts = String($("simulator-request-artifacts")?.value || "").trim();
  const notes = String($("simulator-request-notes")?.value || "").trim();

  if (!simulatorName) {
    throw new Error("Simulator Name is required to create the issue.");
  }

  const params = new URLSearchParams();
  params.set("title", `[Simulator Request] ${simulatorName}`);
  params.set("labels", "simulator-request");
  params.set("body", [
    "## Simulator Request",
    "",
    `- Simulator name: ${simulatorName}`,
    `- DOI / publication: ${doi || "-"}`,
    `- Implementation repository: ${repoUrl || "-"}`,
    `- Expected canonical profiles: ${expectedProfiles || "-"}`,
    `- Expected extras / inputs: ${expectedArtifacts || "-"}`,
    "",
    "## Notes",
    notes || "-",
    "",
    "## Submitted From",
    "- ANDREA GUI generate-data",
  ].join("\n"));
  return `https://github.com/AdrianSeguraOrtiz/ANDREA/issues/new?${params.toString()}`;
}

function buildSimulatorIssueReportUrl() {
  const simulatorId = String($("simulator-issue-id")?.value || "").trim();
  const issueType = String($("simulator-issue-type")?.value || "other").trim();
  const observed = String($("simulator-issue-observed")?.value || "").trim();
  const expected = String($("simulator-issue-expected")?.value || "").trim();
  const context = String($("simulator-issue-context")?.value || "").trim();

  if (!simulatorId) {
    throw new Error("Select a simulator to report.");
  }
  if (!observed) {
    throw new Error("Observed Behavior is required.");
  }

  const simulator = simulatorById(simulatorId);
  const simulatorName = String(simulator?.name || simulatorId);
  const params = new URLSearchParams();
  params.set("title", `[Simulator Catalog Issue] ${simulatorName} (${issueType})`);
  params.set("labels", "simulator-catalog-issue");
  params.set("body", [
    "## Simulator Catalog Issue",
    "",
    `- simulator_id: ${simulatorId}`,
    `- simulator_name: ${simulatorName}`,
    `- issue_type: ${issueType}`,
    "",
    "## Observed",
    observed,
    "",
    "## Expected",
    expected || "-",
    "",
    "## Context",
    context || "-",
    "",
    "## Submitted From",
    "- ANDREA GUI generate-data",
  ].join("\n"));
  return `https://github.com/AdrianSeguraOrtiz/ANDREA/issues/new?${params.toString()}`;
}

function renderPreflightSummary(report) {
  const root = $("preflight-report-view");
  if (!report) {
    root.textContent = "No preflight report yet.";
    return;
  }
  const summary = report.catalog_summary || {};
  root.textContent = [
    `scenario: ${report.scenario?.id || "-"}`,
    `profile: ${report.scenario?.profile || "-"}`,
    `requested_extras: ${(report.scenario?.requested_extras || []).join(", ") || "(none)"}`,
    `effective_extras: ${(report.scenario?.effective_extras || []).join(", ") || "(none)"}`,
    `catalog: total=${summary.total ?? 0} eligible=${summary.eligible ?? 0} warning=${summary.warning ?? 0} blocked=${summary.blocked ?? 0}`,
  ].join("\n");
}

function simulatorIssues(entry, severity = null) {
  return (entry.issues || [])
    .filter((issue) => !severity || issue?.severity === severity)
    .map((issue) => ({
      severity: String(issue?.severity || "").trim(),
      code: String(issue?.code || "").trim(),
      message: String(issue?.message || "").trim(),
    }))
    .filter((issue) => issue.message);
}

function simulatorIssueDescription(entry) {
  const blocks = simulatorIssues(entry, "block");
  const warnings = simulatorIssues(entry, "warn");
  const parts = [];
  if (blocks.length) {
    parts.push(["Blocking issues", ...blocks.map((issue) => `- ${issue.message}`)].join("\n"));
  }
  if (warnings.length) {
    parts.push(["Warnings", ...warnings.map((issue) => `- ${issue.message}`)].join("\n"));
  }
  return parts.join("\n\n");
}

function renderSimulatorList(containerId, entries, kind) {
  const host = $(containerId);
  host.innerHTML = "";
  if (!entries.length) {
    const empty = document.createElement("div");
    empty.className = "muted-box";
    empty.textContent = "No simulators in this group.";
    host.appendChild(empty);
    return;
  }
  const template = $("simulator-catalog-item-template");
  for (const entry of entries) {
    const simulator = simulatorById(entry.simulator_id);
    if (!simulator) {
      continue;
    }
    const node = template.content.firstElementChild.cloneNode(true);
    node.querySelector(".tool-item-name").textContent = simulator.name;
    node.querySelector(".tool-item-badge").textContent = kind;
    node.querySelector(".tool-item-meta").textContent =
      `${simulator.first_author || "-"} ${simulator.year || ""} · ${entry.truth_outputs?.global_network || "truth?"} truth`;
    const actions = node.querySelector(".tool-item-actions");
    const infoBtn = document.createElement("button");
    infoBtn.type = "button";
    infoBtn.className = "secondary";
    infoBtn.textContent = "Simulator Info";
    infoBtn.addEventListener("click", () => showSimulatorInfo(simulator));
    actions.appendChild(infoBtn);
    if (kind !== "blocked") {
      const addBtn = document.createElement("button");
      addBtn.type = "button";
      addBtn.className = "secondary";
      addBtn.textContent = "Add Run";
      addBtn.addEventListener("click", () => {
        try {
          addRunCard({ simulator_id: simulator.simulator_id });
        } catch (err) {
          pushToast({ title: "Run configuration error", message: err.message, kind: "error", ttlMs: 8000 });
        }
      });
      actions.appendChild(addBtn);
    }
    const issues = simulatorIssues(entry);
    if (issues.length) {
      const detailsBtn = document.createElement("button");
      detailsBtn.type = "button";
      detailsBtn.className = "secondary";
      detailsBtn.textContent = kind === "blocked" ? "Why Blocked" : "Why Warned";
      detailsBtn.addEventListener("click", () =>
        showInfoTooltip({
          title: `${simulator.name} · ${kind === "blocked" ? "Why blocked" : "Why warned"}`,
          description: simulatorIssueDescription(entry),
          example: "",
        })
      );
      actions.appendChild(detailsBtn);
    }
    host.appendChild(node);
  }
}

function renderSimulatorEligibility(report) {
  state.preflightReport = report;
  renderPreflightSummary(report);
  const eligible = Array.isArray(report?.eligible) ? report.eligible : [];
  const warning = Array.isArray(report?.warning) ? report.warning : [];
  const blocked = Array.isArray(report?.blocked) ? report.blocked : [];
  $("eligible-count").textContent = String(eligible.length);
  $("warning-count").textContent = String(warning.length);
  $("blocked-count").textContent = String(blocked.length);
  renderSimulatorList("simulators-eligible-list", eligible, "eligible");
  renderSimulatorList("simulators-warning-list", warning, "warning");
  renderSimulatorList("simulators-blocked-list", blocked, "blocked");
}

function availableSimulatorIds() {
  const report = state.preflightReport;
  if (!report) {
    return [];
  }
  return [...(report.eligible || []), ...(report.warning || [])]
    .map((entry) => String(entry.simulator_id || ""))
    .filter(Boolean);
}

function buildRunId(simulatorId) {
  const existing = Array.from(document.querySelectorAll(".run-id")).map((node) => node.value.trim());
  let idx = 1;
  while (existing.includes(`${simulatorId}__${String(idx).padStart(2, "0")}`)) {
    idx += 1;
  }
  return `${simulatorId}__${String(idx).padStart(2, "0")}`;
}

function updateRunParamsSummary(card, simulator, params = null) {
  const summary = card.querySelector(".run-params-summary");
  const current = params || readParamsFromHost(simulator, card.querySelector(".run-params-form"));
  summary.textContent = deepEqualJson(current, resolvedDefaultParams(simulator))
    ? "Default parameters"
    : "Custom parameters";
}

function renderCardParams(card, simulator, params = null) {
  const host = card.querySelector(".run-params-form");
  renderParamsHost(host, simulator, params, () => {
    refreshRunCardsValidation();
  });
  updateRunParamsSummary(card, simulator, readParamsFromHost(simulator, host));
}

function addRunCard(initial = {}) {
  const ids = availableSimulatorIds();
  if (!ids.length) {
    throw new Error("No eligible simulators available. Run preflight first.");
  }
  const simulatorId = initial.simulator_id || ids[0];
  const simulator = simulatorById(simulatorId);
  if (!simulator) {
    throw new Error(`Unknown simulator '${simulatorId}'`);
  }
  const node = $("run-template").content.firstElementChild.cloneNode(true);
  node.querySelector(".simulator-id").value = simulatorId;
  node.querySelector(".run-tool-name").textContent = simulator.name;
  node.querySelector(".run-id").value = initial.run_id || buildRunId(simulatorId);
  node.querySelector(".replicates").value = String(initial.replicates || 1);
  renderCardParams(node, simulator, initial.params || null);
  renderCardNativeOutputs(node, simulator, initial.native_outputs || []);
  node.querySelectorAll("input").forEach((input) => input.addEventListener("input", refreshRunCardsValidation));
  node.querySelector(".open-params").addEventListener("click", () => openParamsModal(node));
  node.querySelector(".reset-params").addEventListener("click", () => {
    renderCardParams(node, simulator, resolvedDefaultParams(simulator));
    renderCardNativeOutputs(node, simulator, []);
    refreshRunCardsValidation();
  });
  node.querySelector(".remove-run").addEventListener("click", () => {
    node.remove();
    updateRunsEmptyState();
    refreshRunCardsValidation();
    syncButtons();
  });
  $("runs-container").appendChild(node);
  updateRunsEmptyState();
  refreshRunCardsValidation();
  syncButtons();
}

function updateRunsEmptyState() {
  const hasRuns = Boolean(document.querySelectorAll(".run-card").length);
  $("runs-empty").style.display = hasRuns ? "none" : "block";
}

function refreshRunCardsValidation({ sync = true } = {}) {
  let ok = true;
  const seen = new Set();
  document.querySelectorAll(".run-card").forEach((card) => {
    const messages = [];
    const runId = card.querySelector(".run-id").value.trim();
    const replicates = Number.parseInt(card.querySelector(".replicates").value || "0", 10);
    if (!runId) {
      messages.push("Run ID is required.");
    } else if (seen.has(runId)) {
      messages.push(`Duplicate run_id: ${runId}`);
    }
    seen.add(runId);
    if (!Number.isInteger(replicates) || replicates < 1) {
      messages.push("Replicates must be >= 1.");
    }
    const simulator = simulatorById(card.querySelector(".simulator-id").value);
    let params = null;
    try {
      params = readParamsFromHost(simulator, card.querySelector(".run-params-form"));
    } catch (err) {
      messages.push(String(err?.message || "Invalid parameters"));
    }
    if (params) {
      messages.push(...simulatorInputMessages(simulator, params));
    }
    const selectedNativeOutputs = readNativeOutputsFromHost(card.querySelector(".run-native-outputs-form"));
    const nativeOutputDefs = nativeOutputDefsForSimulator(simulator);
    const supportedNativeOutputs = new Set(nativeOutputDefs.map((item) => String(item.id)));
    const nativeOutputDefsById = new Map(nativeOutputDefs.map((item) => [String(item.id), item]));
    const unsupportedNativeOutputs = selectedNativeOutputs.filter((item) => !supportedNativeOutputs.has(String(item)));
    if (unsupportedNativeOutputs.length) {
      messages.push(`Unsupported native outputs: ${unsupportedNativeOutputs.join(", ")}`);
    }
    if (params) {
      for (const outputId of selectedNativeOutputs) {
        const outputDef = nativeOutputDefsById.get(String(outputId));
        if (outputDef && !nativeOutputMatches(outputDef, params)) {
          messages.push(
            String(
              outputDef.message
              || `${outputId} is not available with the current run parameters.`
            ).trim()
          );
        }
      }
    }
    const validation = card.querySelector(".run-validation");
    const uniqueMessages = [...new Set(messages.filter(Boolean))];
    validation.classList.toggle("ok", uniqueMessages.length === 0);
    validation.classList.toggle("err", uniqueMessages.length > 0);
    validation.textContent = uniqueMessages.length ? uniqueMessages.join("\n") : "Run configuration looks valid.";
    card.classList.toggle("invalid", uniqueMessages.length > 0);
    if (uniqueMessages.length) {
      ok = false;
    }
  });
  if (sync) {
    syncButtons();
  }
  return ok;
}

function collectRuns() {
  const cards = Array.from(document.querySelectorAll(".run-card"));
  if (!cards.length) {
    throw new Error("At least one simulator run is required.");
  }
  if (!refreshRunCardsValidation()) {
    throw new Error("Fix invalid run configuration before planning.");
  }
  return cards.map((card) => {
    const simulator = simulatorById(card.querySelector(".simulator-id").value);
    return {
      run_id: card.querySelector(".run-id").value.trim(),
      simulator_id: simulator.simulator_id,
      replicates: Number.parseInt(card.querySelector(".replicates").value, 10),
      params: readParamsFromHost(simulator, card.querySelector(".run-params-form")),
      native_outputs: readNativeOutputsFromHost(card.querySelector(".run-native-outputs-form")),
    };
  });
}

function openParamsModal(card) {
  const simulator = simulatorById(card.querySelector(".simulator-id").value);
  const currentParams = readParamsFromHost(simulator, card.querySelector(".run-params-form"));
  const currentNativeOutputs = readNativeOutputsFromHost(card.querySelector(".run-native-outputs-form"));
  state.paramsModalCard = card;
  $("params-modal-title").textContent = `${simulator.name} · Configuration`;
  const status = $("params-modal-status");
  status.classList.remove("ok", "err");
  status.textContent = "Adjust native outputs and parameters, then apply changes.";
  renderParamsHost($("params-modal-form"), simulator, currentParams, () => {
    try {
      readParamsFromHost(simulator, $("params-modal-form"));
      status.classList.remove("ok", "err");
      status.textContent = "Adjust native outputs and parameters, then apply changes.";
    } catch (err) {
      status.classList.remove("ok");
      status.classList.add("err");
      status.textContent = String(err?.message || "Invalid parameter value");
    }
  });
  renderNativeOutputsHost($("params-modal-native-outputs"), simulator, currentNativeOutputs);
  $("params-modal").classList.remove("hidden");
}

function closeParamsModal() {
  $("params-modal").classList.add("hidden");
  $("params-modal-form").innerHTML = "";
  $("params-modal-native-outputs").innerHTML = "";
  $("params-modal-title").textContent = "Configuration";
  $("params-modal-status").classList.remove("ok", "err");
  $("params-modal-status").textContent = "Adjust native outputs and parameters, then apply changes.";
  state.paramsModalCard = null;
}

function applyParamsModal() {
  if (!state.paramsModalCard) {
    closeParamsModal();
    return;
  }
  const simulator = simulatorById(state.paramsModalCard.querySelector(".simulator-id").value);
  try {
    const params = readParamsFromHost(simulator, $("params-modal-form"));
    const nativeOutputs = readNativeOutputsFromHost($("params-modal-native-outputs"));
    renderCardParams(state.paramsModalCard, simulator, params);
    renderCardNativeOutputs(state.paramsModalCard, simulator, nativeOutputs);
    refreshRunCardsValidation();
    closeParamsModal();
  } catch (err) {
    $("params-modal-status").classList.remove("ok");
    $("params-modal-status").classList.add("err");
    $("params-modal-status").textContent = String(err?.message || "Invalid parameters");
  }
}

function renderPlan(plan) {
  const summary = $("plan-summary");
  const tables = $("plan-tables");
  tables.innerHTML = "";
  if (!plan || typeof plan !== "object") {
    summary.textContent = "No plan loaded yet.";
    return;
  }
  summary.textContent = [
    `benchmark: ${plan.id || "-"}`,
    `profile: ${plan.profile || "-"}`,
    `runs: ${(plan.runs || []).length}`,
    `tasks: ${(plan.tasks || []).length}`,
    `max_parallel_tasks: ${plan.execution?.max_parallel_tasks ?? "-"}`,
    `max_cores: ${plan.execution?.max_cores ?? "-"}`,
    `max_ram_gb: ${plan.execution?.max_ram_gb ?? "-"}`,
    `estimated_total_time_s: ${plan.execution?.eta_total_seconds ?? "-"}`,
    `waves: ${(plan.execution?.waves || []).length}`,
  ].join("\n");

  const warnings = Array.isArray(plan.execution?.warnings) ? plan.execution.warnings.filter(Boolean) : [];
  if (warnings.length) {
    const warningBox = document.createElement("div");
    warningBox.className = "muted-box warning-box";
    warningBox.textContent = warnings.join("\n");
    tables.appendChild(warningBox);
  }

  const runsCard = document.createElement("article");
  runsCard.className = "wave-card";
  runsCard.innerHTML = "<h3>Simulator Runs</h3>";
  const runsTable = document.createElement("table");
  runsTable.className = "wave-table";
  runsTable.innerHTML = "<thead><tr><th>run_id</th><th>simulator</th><th>replicates</th><th>threads</th><th>RAM GB</th><th>ETA s</th><th>ETA source</th><th>native_outputs</th><th>base_seed</th><th>replicate_seeds</th></tr></thead>";
  const runsBody = document.createElement("tbody");
  for (const run of plan.runs || []) {
    const tr = document.createElement("tr");
    [
      run.run_id,
      run.simulator_id,
      run.replicates,
      run.runtime_resources?.threads ?? "-",
      run.ram_gb ?? "-",
      run.eta_seconds ?? "-",
      run.eta_source ?? "-",
      (run.native_outputs || []).join(", ") || "-",
      run.base_seed,
      (run.replicate_seeds || []).join(", "),
    ].forEach((value) => {
      const td = document.createElement("td");
      td.textContent = String(value ?? "-");
      tr.appendChild(td);
    });
    runsBody.appendChild(tr);
  }
  runsTable.appendChild(runsBody);
  runsCard.appendChild(runsTable);
  tables.appendChild(runsCard);

  const wavesCard = document.createElement("article");
  wavesCard.className = "wave-card";
  wavesCard.innerHTML = "<h3>Execution Waves</h3>";
  const wavesTable = document.createElement("table");
  wavesTable.className = "wave-table";
  wavesTable.innerHTML = "<thead><tr><th>wave</th><th>tasks</th><th>threads_used</th><th>RAM GB</th><th>ETA s</th><th>window</th></tr></thead>";
  const wavesBody = document.createElement("tbody");
  for (const wave of plan.execution?.waves || []) {
    const tr = document.createElement("tr");
    [
      wave.index,
      (wave.tasks || []).map((task) => task.task_id).join(", "),
      wave.threads_used,
      wave.ram_gb_used,
      wave.eta_seconds,
      `${wave.eta_start_seconds ?? "-"}-${wave.eta_end_seconds ?? "-"}`,
    ].forEach((value) => {
      const td = document.createElement("td");
      td.textContent = String(value ?? "-");
      tr.appendChild(td);
    });
    wavesBody.appendChild(tr);
  }
  wavesTable.appendChild(wavesBody);
  wavesCard.appendChild(wavesTable);
  tables.appendChild(wavesCard);

  const tasksCard = document.createElement("article");
  tasksCard.className = "wave-card";
  tasksCard.innerHTML = "<h3>Planned Tasks</h3>";
  const tasksTable = document.createElement("table");
  tasksTable.className = "wave-table";
  tasksTable.innerHTML = "<thead><tr><th>task_id</th><th>run_id</th><th>replicate</th><th>seed</th><th>threads</th><th>RAM GB</th><th>ETA s</th><th>wave</th><th>dataset_id</th></tr></thead>";
  const tasksBody = document.createElement("tbody");
  for (const task of plan.tasks || []) {
    const tr = document.createElement("tr");
    [
      task.task_id,
      task.run_id,
      task.replicate_index,
      task.seed,
      task.runtime_resources?.threads ?? "-",
      task.ram_gb ?? "-",
      task.eta_seconds ?? "-",
      task.eta_wave ?? "-",
      task.dataset_id,
    ].forEach((value) => {
      const td = document.createElement("td");
      td.textContent = String(value ?? "-");
      tr.appendChild(td);
    });
    tasksBody.appendChild(tr);
  }
  tasksTable.appendChild(tasksBody);
  tasksCard.appendChild(tasksTable);
  tables.appendChild(tasksCard);
}

function renderExecutionAlerts(job, runtimeProgress = null) {
  const root = $("execution-alerts");
  const error = String(job?.error || "").trim();
  const failedTasks = Array.isArray(runtimeProgress?.tasks)
    ? runtimeProgress.tasks.filter((task) => String(task?.status || "") === "failed")
    : [];
  const messages = [];
  if (error) {
    messages.push(error);
  }
  for (const task of failedTasks) {
    const taskId = String(task?.task_id || "").trim() || "task";
    const message = String(task?.message || "Task failed.").trim();
    messages.push(`${taskId}: ${message}`);
  }
  const uniqueMessages = [...new Set(messages.filter(Boolean))];
  root.classList.toggle("has-errors", uniqueMessages.length > 0);
  if (!uniqueMessages.length) {
    root.textContent = "No execution errors or warnings.";
    return;
  }
  root.textContent = uniqueMessages.join("\n");
  if (error) {
    pushToast({ title: "Job failed", message: error, kind: "error", ttlMs: 9000 });
  }
}

function hasExecutionArtifacts(job) {
  return Boolean(job?.benchmark_root) && (job.stage === "executed" || job.status === "failed");
}

function updateExplorerVisibility(job) {
  const visible = hasExecutionArtifacts(job);
  $("results-explorer-section").hidden = !visible;
  $("results-explorer-placeholder").hidden = visible;
  if (!visible) {
    resetFilesView(state, "Results Explorer will be available after execution.");
    resetReproducibility("Reproducibility snippets will be available after execution.");
  }
}

async function refreshFilesIfNeeded(job) {
  if (!hasExecutionArtifacts(job)) {
    return;
  }
  const key = `${job.job_id}:${$("bundle-mode").value}:${job.status}:${job.benchmark_root}`;
  if (state.loadedFilesKey === key) {
    return;
  }
  await fetchFiles(state, fileApi(), {}, fileExplorerOptions());
  state.loadedFilesKey = key;
}

function syncButtons() {
  const job = state.currentJob;
  const busy = job?.status === "queued" || job?.status === "running";
  const preflightReady = ["preflight_ok", "planned", "executed"].includes(job?.stage || "");
  const planReady = ["planned", "executed"].includes(job?.stage || "");
  const executed = job?.stage === "executed";
  const hasRuns = Boolean(document.querySelectorAll(".run-card").length);
  const runsValid = hasRuns ? refreshRunCardsValidation({ sync: false }) : true;
  $("preflight-btn").disabled = busy;
  $("step-1-next-btn").disabled = busy || !preflightReady;
  $("add-all-simulators-btn").disabled = busy || !preflightReady || availableSimulatorIds().length === 0;
  $("clear-runs-btn").disabled = busy || !hasRuns;
  $("plan-btn").disabled = busy || !preflightReady || !hasRuns || !runsValid;
  $("step-2-next-btn").disabled = busy || !planReady || !hasRuns || !runsValid;
  $("execute-btn").disabled = busy || !planReady || executed || !hasRuns || !runsValid;
  setStepState(1, preflightReady ? "ready" : busy ? "running" : "draft");
  setStepState(2, planReady ? "ready" : preflightReady ? "ready" : "blocked");
  setStepState(3, executed ? "ready" : planReady ? "ready" : "blocked");
}

async function pollJob(jobId) {
  const payload = await fetchJob(jobId);
  const job = payload.job;
  state.currentJob = job;
  if (payload.preflight_report) {
    renderSimulatorEligibility(payload.preflight_report);
  }
  if (payload.plan) {
    renderPlan(payload.plan);
  } else if (job.plan_path) {
    const planPayload = await fetchPlan(job.job_id);
    renderPlan(planPayload.plan);
  }
  renderRuntimeProgress(payload.runtime_progress);
  pushRuntimeFailureToasts(payload.runtime_progress, state.notifiedFailures);
  renderExecutionAlerts(job, payload.runtime_progress);
  renderReproducibility(payload.reproducibility);
  updateExplorerVisibility(job);
  await refreshFilesIfNeeded(job);
  if (job.status === "running" && job.stage === "planned") {
    setActiveStep(3, { scroll: false });
  }
  syncButtons();
  if (job.status === "completed" || job.status === "failed") {
    if (state.pollTimer) {
      window.clearInterval(state.pollTimer);
      state.pollTimer = null;
    }
  }
}

async function startPolling(jobId) {
  if (state.pollTimer) {
    window.clearInterval(state.pollTimer);
  }
  await pollJob(jobId);
  state.pollTimer = window.setInterval(() => {
    pollJob(jobId).catch((err) => pushToast({ title: "Polling error", message: err.message, kind: "error" }));
  }, 1500);
}

async function handlePreflight() {
  try {
    const payload = await submitPreflight(buildPreflightFormData());
    state.jobId = payload.job_id;
    state.currentJob = { job_id: payload.job_id, status: "queued", stage: "draft" };
    renderPlan(null);
    $("runs-container").innerHTML = "";
    updateRunsEmptyState();
    syncButtons();
    await startPolling(payload.job_id);
  } catch (err) {
    pushToast({ title: "Preflight failed", message: err.message, kind: "error" });
  }
}

async function handlePlan() {
  try {
    const payload = await submitPlan({
      job_id: state.jobId,
      runs: collectRuns(),
      options: {
        max_parallel_tasks: Number.parseInt($("max-parallel-tasks").value || "1", 10),
        max_cores: Number.parseInt($("max-cores").value || "1", 10),
        max_ram_gb: Number($("max-ram-gb").value || "1"),
        output_dir: $("output-dir").value.trim() || "./benchmarks",
      },
    });
    await startPolling(payload.job_id);
  } catch (err) {
    pushToast({ title: "Plan failed", message: err.message, kind: "error" });
  }
}

async function handleRun() {
  try {
    if (!document.querySelectorAll(".run-card").length) {
      throw new Error("At least one simulator run is required.");
    }
    if (!refreshRunCardsValidation()) {
      throw new Error("Fix invalid run configuration before execution.");
    }
    const payload = await submitRun({
      job_id: state.jobId,
      options: {
        output_dir: $("output-dir").value.trim() || "./benchmarks",
        progress_poll_seconds: Number($("progress-poll").value || 0.5),
      },
    });
    setActiveStep(3);
    await startPolling(payload.job_id);
  } catch (err) {
    pushToast({ title: "Run failed", message: err.message, kind: "error" });
  }
}

function initEvents() {
  $("add-input-btn").addEventListener("click", openInputModal);
  $("preflight-btn").addEventListener("click", handlePreflight);
  $("plan-btn").addEventListener("click", handlePlan);
  $("execute-btn").addEventListener("click", handleRun);
  $("step-1-next-btn").addEventListener("click", () => setActiveStep(2));
  $("step-2-next-btn").addEventListener("click", () => setActiveStep(3));
  $("add-all-simulators-btn").addEventListener("click", () => {
    try {
      for (const simulatorId of availableSimulatorIds()) {
        addRunCard({ simulator_id: simulatorId });
      }
    } catch (err) {
      pushToast({ title: "Run configuration error", message: err.message, kind: "error", ttlMs: 8000 });
    }
  });
  $("clear-runs-btn").addEventListener("click", () => {
    $("runs-container").innerHTML = "";
    updateRunsEmptyState();
    refreshRunCardsValidation();
    syncButtons();
  });
  $("open-simulator-request-modal-btn").addEventListener("click", () => openModal("simulator-request-modal"));
  $("open-simulator-issue-modal-btn").addEventListener("click", () => openModal("simulator-issue-modal"));
  $("simulator-request-modal-close").addEventListener("click", () => closeModal("simulator-request-modal"));
  $("simulator-issue-modal-close").addEventListener("click", () => closeModal("simulator-issue-modal"));
  $("input-modal-close").addEventListener("click", closeInputModal);
  $("simulator-request-modal").addEventListener("click", (event) => {
    if (event.target && event.target.id === "simulator-request-modal") {
      closeModal("simulator-request-modal");
    }
  });
  $("simulator-issue-modal").addEventListener("click", (event) => {
    if (event.target && event.target.id === "simulator-issue-modal") {
      closeModal("simulator-issue-modal");
    }
  });
  $("input-modal").addEventListener("click", (event) => {
    if (event.target && event.target.id === "input-modal") {
      closeInputModal();
    }
  });
  $("open-simulator-request-issue-btn").addEventListener("click", () => {
    try {
      window.open(buildSimulatorRequestIssueUrl(), "_blank", "noopener");
      closeModal("simulator-request-modal");
      pushToast({
        title: "GitHub issue opened",
        message: "Opened prefilled simulator request in a new tab.",
        kind: "success",
        ttlMs: 4500,
      });
    } catch (err) {
      pushToast({ title: "Simulator request error", message: err.message, kind: "error", ttlMs: 7000 });
    }
  });
  $("open-simulator-issue-btn").addEventListener("click", () => {
    try {
      window.open(buildSimulatorIssueReportUrl(), "_blank", "noopener");
      closeModal("simulator-issue-modal");
      pushToast({
        title: "GitHub issue opened",
        message: "Opened prefilled simulator issue in a new tab.",
        kind: "success",
        ttlMs: 4500,
      });
    } catch (err) {
      pushToast({ title: "Simulator issue error", message: err.message, kind: "error", ttlMs: 7000 });
    }
  });
  $("params-modal-close").addEventListener("click", closeParamsModal);
  $("params-modal-save").addEventListener("click", applyParamsModal);
  $("params-modal-reset").addEventListener("click", () => {
    if (!state.paramsModalCard) {
      return;
    }
    const simulator = simulatorById(state.paramsModalCard.querySelector(".simulator-id").value);
    renderParamsHost($("params-modal-form"), simulator, resolvedDefaultParams(simulator));
    renderNativeOutputsHost($("params-modal-native-outputs"), simulator, []);
    $("params-modal-status").classList.remove("ok", "err");
    $("params-modal-status").textContent = "Default parameters restored and native outputs cleared in the modal. Apply to save them.";
  });
  $("params-modal").addEventListener("click", (event) => {
    if (event.target && event.target.id === "params-modal") {
      closeParamsModal();
    }
  });
  $("refresh-files-btn").addEventListener("click", () => fetchFiles(state, fileApi(), {}, fileExplorerOptions()).catch((err) => {
    pushToast({ title: "Files error", message: err.message, kind: "error" });
  }));
  $("bundle-mode").addEventListener("change", () => {
    state.loadedFilesKey = null;
    if (state.currentJob) {
      refreshFilesIfNeeded(state.currentJob).catch((err) => pushToast({ title: "Files error", message: err.message, kind: "error" }));
    }
  });
  $("download-bundle-btn").addEventListener("click", () => {
    if (!state.jobId) {
      return;
    }
    window.location.href = `/api/generate-data/jobs/${state.jobId}/bundle?mode=${encodeURIComponent($("bundle-mode").value)}`;
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") {
      return;
    }
    closeParamsModal();
    closeInputModal();
    closeModal("simulator-request-modal");
    closeModal("simulator-issue-modal");
  });
}

async function main() {
  initSteps(3);
  initInfoPopover();
  initReproducibility();
  initEvents();
  state.bootstrap = await fetchBootstrapData();
  state.simulatorsById = new Map((state.bootstrap.simulators || []).map((item) => [item.simulator_id, item]));
  initBootstrapView();
  renderInputModalBody();
  updateInputsEmptyState();
  updateRunsEmptyState();
  syncButtons();
}

main().catch((err) => {
  pushToast({ title: "Startup failed", message: err.message, kind: "error", ttlMs: 10000 });
});
