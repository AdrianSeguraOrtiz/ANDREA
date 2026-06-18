import { $, formatBytes } from "/static-common/app/core/dom.js";
import {
  closeBundleDownloadModal,
  initBundleDownloadModal,
  openBundleDownloadModal,
} from "/static-common/app/bundles/modal.js?v=20260612a";
import { fetchFiles, resetFilesView } from "/static-common/app/files/explorer.js?v=20260611a";
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
  simulatorInputMetrics: new Map(),
};

const LARGE_TRUTH_NETWORK_BYTES = 25 * 1024 * 1024;
const TRUTH_OUTPUT_ORDER = ["global", "group", "cell"];

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
    fetchFiles: async (bundleId) => {
      const response = await fetch(`/api/generate-data/jobs/${state.jobId}/files?bundle_id=${encodeURIComponent(bundleId)}`);
      return readJson(response, "Failed to load files");
    },
    fetchFileContent: async (path, bundleId, options = {}) => {
      const response = await fetch(
        `/api/generate-data/jobs/${state.jobId}/file-content?bundle_id=${encodeURIComponent(bundleId)}&path=${encodeURIComponent(path)}`,
        {
          signal: options.signal,
        }
      );
      return readJson(response, "Failed to load file preview");
    },
  };
}

function fileExplorerOptions() {
  return {
    preferredPathSuffixes: ["benchmark-manifest.json"],
    renderSummary: renderGenerateFilesSummary,
  };
}

function renderGenerateFilesSummary({ summaryEl, entries, mode, filesCount, dirsCount }) {
  summaryEl.innerHTML = "";
  const line = document.createElement("div");
  line.className = "files-summary-line";
  line.textContent = `bundle=${mode} | files=${filesCount} | dirs=${dirsCount}`;
  summaryEl.appendChild(line);

  const allEntries = Array.isArray(entries) ? entries : [];
  const firstMatchingPath = (suffix) => {
    const match = allEntries.find((item) => (
      item.kind === "file"
      && String(item.path || "").toLowerCase().endsWith(suffix)
    ));
    return match ? String(match.path || "") : "";
  };
  const keyFiles = [
    {
      label: "expression.tsv",
      path: firstMatchingPath("/expression.tsv"),
      description: "normalized expression matrix",
    },
    {
      label: "truth/networks.csv",
      path: firstMatchingPath("/truth/networks.csv"),
      description: "ground-truth GRN rows with context labels",
    },
    {
      label: "extras/groups.tsv",
      path: firstMatchingPath("/extras/groups.tsv"),
      description: "cell-to-group assignments",
    },
  ].filter((item) => item.path);
  if (keyFiles.length) {
    const keyBox = document.createElement("div");
    keyBox.className = "files-summary-keyfiles";
    const keyTitle = document.createElement("strong");
    keyTitle.textContent = "Key files";
    keyBox.appendChild(keyTitle);
    const keyList = document.createElement("div");
    keyList.className = "files-summary-keyfile-list";
    for (const item of keyFiles) {
      const chip = document.createElement("span");
      chip.className = "files-summary-keyfile";
      const name = document.createElement("strong");
      name.textContent = item.label;
      const description = document.createElement("span");
      description.textContent = item.description;
      chip.title = item.path;
      chip.append(name, description);
      keyList.appendChild(chip);
    }
    keyBox.appendChild(keyList);
    summaryEl.appendChild(keyBox);
  }

  const truthNetworks = (entries || []).find((item) => (
    item.kind === "file"
    && String(item.path || "").toLowerCase().endsWith("/truth/networks.csv")
  ));
  const warnings = [];
  if (
    truthNetworks
    && Number(truthNetworks.size_bytes || 0) >= LARGE_TRUTH_NETWORK_BYTES
  ) {
    warnings.push(
      `truth/networks.csv is large (${formatBytes(truthNetworks.size_bytes)}). `
      + "Preview is row-limited; download the ZIP for full inspection."
    );
  }
  if (!warnings.length) {
    return;
  }
  const list = document.createElement("div");
  list.className = "files-summary-warnings";
  for (const warning of warnings) {
    const item = document.createElement("div");
    item.className = "files-summary-warning";
    item.textContent = warning;
    list.appendChild(item);
  }
  summaryEl.appendChild(list);
}

function simulatorById(id) {
  return state.simulatorsById.get(String(id || ""));
}

function profileSpec(profileId) {
  return (state.bootstrap?.profiles || []).find((item) => item.id === profileId) || null;
}

function profileRequiredTruthOutputs(profileId = selectedProfileId()) {
  const profile = profileSpec(profileId);
  if (Array.isArray(profile?.required_truth_outputs)) {
    return profile.required_truth_outputs.map((item) => String(item || "").trim()).filter(Boolean);
  }
  if (profileId === "scrna_grouped") {
    return ["global", "group"];
  }
  if (profileId === "scrna_cell_specific") {
    return ["global", "group", "cell"];
  }
  return ["global"];
}

function primaryTruthOutputForProfile(profileId = selectedProfileId()) {
  if (profileId === "scrna_grouped") {
    return "group";
  }
  if (profileId === "scrna_cell_specific") {
    return "cell";
  }
  return "global";
}

function truthContextPrefixForOutput(outputId) {
  const normalized = String(outputId || "").trim();
  if (normalized === "group") {
    return "group:";
  }
  if (normalized === "cell") {
    return "cell:";
  }
  return normalized || "-";
}

function profileRequiredTruthContexts(profileId = selectedProfileId()) {
  const profile = profileSpec(profileId);
  if (Array.isArray(profile?.required_truth_contexts)) {
    return profile.required_truth_contexts.map((item) => String(item || "").trim()).filter(Boolean);
  }
  return profileRequiredTruthOutputs(profileId).map(truthContextPrefixForOutput).filter(Boolean);
}

function profileRequiredExtras(profileId = selectedProfileId()) {
  const profile = profileSpec(profileId);
  return Array.isArray(profile?.required_extras)
    ? profile.required_extras.map((item) => String(item || "").trim()).filter(Boolean)
    : [];
}

function fixedOutputFilesForProfile(profileId = selectedProfileId()) {
  const files = [
    {
      path: "expression.tsv",
      description: "Normalized expression matrix.",
    },
    {
      path: "truth/networks.csv",
      description: "Unified ground-truth network table.",
      highlight: true,
    },
    {
      path: "truth/gene_universe.txt",
      description: "Genes covered by the ground-truth networks.",
    },
  ];
  for (const extra of profileRequiredExtras(profileId)) {
    if (extra === "groups") {
      files.push({
        path: "extras/groups.tsv",
        description: "Cell-to-group assignments used by group-level truth.",
      });
    }
  }
  return files;
}

function truthContextExplanation(context) {
  const normalized = String(context || "").trim();
  if (normalized === "global") {
    return "one dataset-level GRN.";
  }
  if (normalized === "group:") {
    return "one GRN per group, stored as context values like group:<group_id>.";
  }
  if (normalized === "cell:") {
    return "one GRN per cell, stored as context values like cell:<cell_id>.";
  }
  return "truth rows distinguished by this context value.";
}

function extraByKey(key) {
  return (state.bootstrap?.extras || []).find((item) => item.key === key) || null;
}

function selectedProfileId() {
  return state.preflightReport?.scenario?.profile || $("profile").value;
}

function profileCapabilityForSimulator(simulator, profileId = selectedProfileId()) {
  const profileCapabilities = simulator?.profile_capabilities && typeof simulator.profile_capabilities === "object"
    ? simulator.profile_capabilities
    : {};
  return profileCapabilities?.[profileId] || null;
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

function profileTruthConditionMessages(simulator, params) {
  const profileId = selectedProfileId();
  const capability = profileCapabilityForSimulator(simulator, profileId);
  if (!capability || !params) {
    return [];
  }
  const requiredTruth = new Set(profileRequiredTruthOutputs(profileId));
  const messages = [];

  const explicitRequirements = Array.isArray(capability.truth_parameter_requirements)
    ? capability.truth_parameter_requirements
    : [];
  for (const requirement of explicitRequirements) {
    const truthOutput = String(requirement?.truth_output || "").trim();
    if (truthOutput && !requiredTruth.has(truthOutput)) {
      continue;
    }
    if (Array.isArray(requirement?.conditions) && !conditionalInputMatches(requirement, params)) {
      messages.push(
        String(
          requirement?.message
          || `${truthOutput || "Required truth output"} is not available with the current run parameters.`
        ).trim()
      );
    }
  }
  return [...new Set(messages.filter(Boolean))];
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
  return (state.bootstrap?.simulation_inputs || []).find((item) => item.id === String(inputId || "")) || null;
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

async function updateSimulatorInputMetrics(row) {
  const inputId = String(row.querySelector(".input-kind")?.value || "").trim();
  const file = row.querySelector(".input-file")?.files?.[0] || null;
  if (!inputId || !file) {
    if (inputId) {
      state.simulatorInputMetrics.delete(inputId);
    }
    return;
  }
  if (inputId !== "regulatory_network") {
    state.simulatorInputMetrics.delete(inputId);
    return;
  }
  const text = await file.text();
  const lines = text.split(/\r?\n/).filter((line) => line.trim());
  if (!lines.length) {
    state.simulatorInputMetrics.delete(inputId);
    return;
  }
  const header = lines[0].split("\t").map((item) => item.trim());
  const targetIndex = header.indexOf("target");
  const regulatorIndex = header.indexOf("regulator");
  if (targetIndex < 0 || regulatorIndex < 0) {
    state.simulatorInputMetrics.delete(inputId);
    return;
  }
  const genes = new Set();
  for (const line of lines.slice(1)) {
    const parts = line.split("\t");
    const target = String(parts[targetIndex] || "").trim();
    const regulator = String(parts[regulatorIndex] || "").trim();
    if (target) {
      genes.add(target);
    }
    if (regulator) {
      genes.add(regulator);
    }
  }
  if (genes.size) {
    state.simulatorInputMetrics.set(inputId, { unique_gene_count: genes.size });
  } else {
    state.simulatorInputMetrics.delete(inputId);
  }
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
    const isRequired = required.has(extra.key);
    const row = document.createElement("label");
    row.className = "checkbox-row extra-card";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.className = "extra-checkbox";
    input.value = extra.key;
    input.checked = isRequired;
    input.disabled = isRequired;
    input.setAttribute("aria-label", extra.label);
    const text = document.createElement("span");
    text.className = "checkbox-copy";
    const head = document.createElement("div");
    head.className = "checkbox-head";
    const title = document.createElement("div");
    title.className = "checkbox-title";
    title.textContent = extra.label;
    const statePill = document.createElement("span");
    statePill.className = "extra-state-pill";
    head.append(title, statePill);
    const desc = document.createElement("div");
    desc.className = "checkbox-desc";
    desc.textContent = isRequired
      ? `${extra.description} Generated automatically for ${selectedProfile}.`
      : extra.description;
    const syncState = () => {
      const isSelected = input.checked;
      row.classList.toggle("is-required", isRequired);
      row.classList.toggle("is-selected", isSelected && !isRequired);
      row.classList.toggle("is-optional", !isSelected && !isRequired);
      statePill.textContent = isRequired ? "Fixed" : (isSelected ? "Selected" : "Optional");
    };
    input.addEventListener("change", () => {
      syncState();
      resetScenarioDerivedState();
    });
    syncState();
    text.appendChild(head);
    text.appendChild(desc);
    row.appendChild(input);
    row.appendChild(text);
    host.appendChild(row);
  }
}

function renderScenarioProfileControls() {
  renderProfileTruthContextSummary();
  renderExtras();
}

function renderProfileTruthContextSummary() {
  const host = $("profile-truth-contexts");
  if (!host) {
    return;
  }
  host.innerHTML = "";
  const profileId = $("profile").value;
  const contexts = profileRequiredTruthContexts(profileId);
  const files = fixedOutputFilesForProfile(profileId);

  const head = document.createElement("div");
  head.className = "profile-output-head";
  const badge = document.createElement("span");
  badge.className = "profile-output-badge";
  badge.textContent = "Selected profile";
  const title = document.createElement("strong");
  title.textContent = profileId;
  const subtitle = document.createElement("span");
  subtitle.textContent = "ANDREA will generate these standardized outputs automatically.";
  head.append(badge, title, subtitle);

  const fileList = document.createElement("div");
  fileList.className = "profile-output-file-list";
  for (const file of files) {
    const item = document.createElement("div");
    item.className = file.highlight
      ? "profile-output-file is-highlight"
      : "profile-output-file";
    const path = document.createElement("strong");
    path.textContent = file.path;
    const description = document.createElement("span");
    description.textContent = file.description;
    item.append(path, description);
    fileList.appendChild(item);
  }

  const network = document.createElement("div");
  network.className = "profile-network-contexts";
  const networkTitle = document.createElement("strong");
  networkTitle.textContent = "truth/networks.csv contexts";
  const chips = document.createElement("div");
  chips.className = "profile-context-chip-row";
  for (const context of contexts) {
    const chip = document.createElement("div");
    chip.className = "profile-context-chip";
    const label = document.createElement("strong");
    label.textContent = context;
    const description = document.createElement("span");
    description.textContent = truthContextExplanation(context);
    chip.append(label, description);
    chips.appendChild(chip);
  }
  network.append(networkTitle, chips);

  host.append(head, fileList, network);
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
  profileSelect.addEventListener("change", renderScenarioProfileControls);
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
  renderScenarioProfileControls();
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
  const metas = Array.isArray(state.bootstrap?.simulation_inputs)
    ? [...state.bootstrap.simulation_inputs]
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
    updateSimulatorInputMetrics(node).then(() => refreshRunCardsValidation({ sync: false }));
    resetScenarioDerivedState();
  });
  node.querySelector(".taxonomic-group")?.addEventListener("change", resetScenarioDerivedState);
  node.querySelector(".organism-ncbi-taxon-id")?.addEventListener("input", resetScenarioDerivedState);
  node.querySelector(".remove-input").addEventListener("click", () => {
    state.simulatorInputMetrics.delete(selectedId);
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

function conditionActualValue(field, params, nativeOutputs = []) {
  const normalized = String(field || "").trim();
  if (normalized === "profile") {
    return selectedProfileId();
  }
  if (normalized === "requested_extra") {
    return checkedExtras().sort();
  }
  if (normalized === "native_output") {
    return [...nativeOutputs].map((item) => String(item)).sort();
  }
  if (normalized.startsWith("param.")) {
    return valueAtPath(params, normalized.slice("param.".length));
  }
  return null;
}

function inputMetricValue(field) {
  const parts = String(field || "").trim().split(".");
  if (parts.length !== 3 || parts[0] !== "input") {
    return null;
  }
  const metrics = state.simulatorInputMetrics.get(parts[1]);
  return metrics && Object.hasOwn(metrics, parts[2]) ? metrics[parts[2]] : null;
}

function compatibilityConditionValue(field, params, nativeOutputs = []) {
  const normalized = String(field || "").trim();
  if (normalized.startsWith("input.")) {
    return inputMetricValue(normalized);
  }
  return conditionActualValue(normalized, params, nativeOutputs);
}

function conditionExpectedValue(condition, params, nativeOutputs = []) {
  const valueFrom = String(condition?.value_from || "").trim();
  if (valueFrom) {
    return compatibilityConditionValue(valueFrom, params, nativeOutputs);
  }
  return condition?.value;
}

function compatibilityConditionMatches(condition, params, nativeOutputs = []) {
  if (!condition || typeof condition !== "object") {
    return false;
  }
  const field = String(condition.field || "").trim();
  const op = String(condition.op || "").trim();
  const expected = conditionExpectedValue(condition, params, nativeOutputs);
  const requestedExtras = new Set(checkedExtras());
  const selectedNativeOutputs = new Set((nativeOutputs || []).map((item) => String(item)));
  if (field === "requested_extra" && op === "eq") {
    return requestedExtras.has(String(expected));
  }
  if (field === "requested_extra" && op === "ne") {
    return !requestedExtras.has(String(expected));
  }
  if (field === "requested_extra" && op === "in") {
    const values = Array.isArray(expected) ? expected.map((item) => String(item)) : [];
    return values.some((item) => requestedExtras.has(item));
  }
  if (field === "requested_extra" && op === "not_in") {
    const values = Array.isArray(expected) ? expected.map((item) => String(item)) : [];
    return !values.some((item) => requestedExtras.has(item));
  }
  if (field === "native_output" && op === "eq") {
    return selectedNativeOutputs.has(String(expected));
  }
  if (field === "native_output" && op === "ne") {
    return !selectedNativeOutputs.has(String(expected));
  }
  if (field === "native_output" && op === "in") {
    const values = Array.isArray(expected) ? expected.map((item) => String(item)) : [];
    return values.some((item) => selectedNativeOutputs.has(item));
  }
  if (field === "native_output" && op === "not_in") {
    const values = Array.isArray(expected) ? expected.map((item) => String(item)) : [];
    return !values.some((item) => selectedNativeOutputs.has(item));
  }
  return compareConditionValue(compatibilityConditionValue(field, params, nativeOutputs), op, expected);
}

function compatibilityRuleMatches(rule, params, nativeOutputs = []) {
  const conditions = Array.isArray(rule?.conditions) ? rule.conditions : [];
  return conditions.length > 0
    && conditions.every((condition) => compatibilityConditionMatches(condition, params, nativeOutputs));
}

function simulatorCompatibilityMessages(simulator, params, nativeOutputs = []) {
  const spec = simulator?.spec && typeof simulator.spec === "object" ? simulator.spec : simulator;
  const rules = Array.isArray(spec?.compatibility_rules) ? spec.compatibility_rules : [];
  const messages = [];
  for (const rule of rules) {
    if (!rule || typeof rule !== "object" || rule.action !== "block") {
      continue;
    }
    if (compatibilityRuleMatches(rule, params, nativeOutputs)) {
      messages.push(String(rule.message || "Run configuration is not compatible.").trim());
    }
  }
  return messages.filter(Boolean);
}

function conditionalInputMatches(requirement, params, nativeOutputs = []) {
  const conditions = Array.isArray(requirement?.conditions) ? requirement.conditions : [];
  if (!conditions.length) {
    return false;
  }
  const requestedExtras = new Set(checkedExtras());
  const selectedNativeOutputs = new Set((nativeOutputs || []).map((item) => String(item)));
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
    if (field === "native_output" && op === "eq") {
      if (!selectedNativeOutputs.has(String(expected))) {
        return false;
      }
      continue;
    }
    if (field === "native_output" && op === "ne") {
      if (selectedNativeOutputs.has(String(expected))) {
        return false;
      }
      continue;
    }
    if (field === "native_output" && op === "in") {
      const values = Array.isArray(expected) ? expected.map((item) => String(item)) : [];
      if (!values.some((item) => selectedNativeOutputs.has(item))) {
        return false;
      }
      continue;
    }
    if (field === "native_output" && op === "not_in") {
      const values = Array.isArray(expected) ? expected.map((item) => String(item)) : [];
      if (values.some((item) => selectedNativeOutputs.has(item))) {
        return false;
      }
      continue;
    }
    if (!compareConditionValue(conditionActualValue(field, params, nativeOutputs), op, expected)) {
      return false;
    }
  }
  return true;
}

function simulatorInputMessages(simulator, params, nativeOutputs = []) {
  const simulatorInputs = simulator?.extra_inputs && typeof simulator.extra_inputs === "object"
    ? simulator.extra_inputs
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
    if (conditionalInputMatches(requirement, params, nativeOutputs)) {
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
  const rawInputs = simulator.extra_inputs && typeof simulator.extra_inputs === "object"
    ? simulator.extra_inputs
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
    global: "truth/networks.csv · global",
    group: "truth/networks.csv · group:<id>",
    cell: "truth/networks.csv · cell:<id>",
  };
  return truthLabels[artifact] || artifact;
}

function truthOutputStatusLabel(status) {
  if (status === "native") {
    return "native";
  }
  if (status === "derivable") {
    return "derived";
  }
  return "unavailable";
}

function appendSimulatorTruthContextChips(host, entry) {
  const truthOutputs = entry?.truth_outputs || {};
  const primaryOutput = primaryTruthOutputForProfile(entry?.requested_profile || selectedProfileId());
  const row = document.createElement("div");
  row.className = "simulator-context-chip-row";
  for (const outputId of TRUTH_OUTPUT_ORDER) {
    const rawStatus = String(truthOutputs[outputId] || "none");
    const status = rawStatus === "native" || rawStatus === "derivable" ? rawStatus : "none";
    const chip = document.createElement("span");
    chip.className = `simulator-context-chip status-${status}`;
    if (outputId === primaryOutput) {
      chip.classList.add("is-primary");
    }
    const label = document.createElement("strong");
    label.textContent = truthContextChipLabel(outputId);
    const statusText = document.createElement("span");
    statusText.textContent = truthOutputStatusLabel(status);
    chip.append(label, statusText);
    row.appendChild(chip);
  }
  host.appendChild(row);
}

function truthContextChipLabel(context) {
  const normalized = String(context || "").trim();
  if (normalized === "group") {
    return "group";
  }
  if (normalized === "cell") {
    return "cell";
  }
  return normalized || "-";
}

function appendTruthContextList(host, title, values) {
  const cleanValues = Array.isArray(values)
    ? values.map((value) => String(value || "").trim()).filter(Boolean)
    : [];
  if (!cleanValues.length) {
    return;
  }
  const block = document.createElement("div");
  block.className = "simulator-truth-context-block";
  const heading = document.createElement("strong");
  heading.textContent = title;
  const list = document.createElement("ul");
  list.className = "simulator-truth-context-list";
  for (const value of cleanValues) {
    const li = document.createElement("li");
    li.textContent = value;
    list.appendChild(li);
  }
  block.append(heading, list);
  host.appendChild(block);
}

function normalizedTruthContextKey(context) {
  const normalized = String(context || "").trim();
  if (normalized === "global") {
    return "global";
  }
  if (normalized === "group" || normalized.startsWith("group:")) {
    return "group";
  }
  if (normalized === "cell" || normalized.startsWith("cell:")) {
    return "cell";
  }
  return normalized;
}

function truthContextMap(truthContexts) {
  const contexts = Array.isArray(truthContexts)
    ? truthContexts.filter((item) => item && typeof item === "object")
    : [];
  const byOutput = new Map();
  for (const context of contexts) {
    const key = normalizedTruthContextKey(context.context);
    if (key && !byOutput.has(key)) {
      byOutput.set(key, context);
    }
  }
  return byOutput;
}

function truthContextHasDetail(context, status) {
  if (!context || status === "none") {
    return false;
  }
  const textFields = ["explanation", "generation", "score_semantics"];
  if (textFields.some((field) => String(context?.[field] || "").trim())) {
    return true;
  }
  const listFields = ["upstream_configuration", "source_artifacts", "limitations"];
  return listFields.some((field) => Array.isArray(context?.[field]) && context[field].length);
}

function appendTruthContextDetail(host, context) {
  const explanation = String(context.explanation || "").trim();
  const generation = String(context.generation || "").trim();
  const scoreSemantics = String(context.score_semantics || "").trim();
  if (explanation) {
    const text = document.createElement("p");
    text.textContent = explanation;
    host.appendChild(text);
  }
  appendTruthContextList(host, "Upstream configuration", context.upstream_configuration);
  if (generation) {
    const block = document.createElement("div");
    block.className = "simulator-truth-context-block";
    const heading = document.createElement("strong");
    heading.textContent = "Generation";
    const text = document.createElement("p");
    text.textContent = generation;
    block.append(heading, text);
    host.appendChild(block);
  }
  if (scoreSemantics) {
    const block = document.createElement("div");
    block.className = "simulator-truth-context-block";
    const heading = document.createElement("strong");
    heading.textContent = "Score semantics";
    const text = document.createElement("p");
    text.textContent = scoreSemantics;
    block.append(heading, text);
    host.appendChild(block);
  }
  appendTruthContextList(host, "Source artifacts", context.source_artifacts);
  appendTruthContextList(host, "Limitations", context.limitations);
}

function appendTruthNetworksSummary(host, truthOutputs, truthContexts, profileId) {
  const primaryOutput = primaryTruthOutputForProfile(profileId);
  const contextByOutput = truthContextMap(truthContexts);
  const panel = document.createElement("div");
  panel.className = "truth-networks-summary";

  const file = document.createElement("div");
  file.className = "truth-networks-file";
  const fileName = document.createElement("strong");
  fileName.textContent = "truth/networks.csv";
  const fileDesc = document.createElement("span");
  fileDesc.textContent = "Unified public truth table; context selects the GRN scope.";
  file.append(fileName, fileDesc);
  panel.appendChild(file);

  const contexts = document.createElement("div");
  contexts.className = "truth-network-context-list";
  for (const outputId of TRUTH_OUTPUT_ORDER) {
    const rawStatus = String(truthOutputs?.[outputId] || "none");
    const status = rawStatus === "native" || rawStatus === "derivable" ? rawStatus : "none";
    const context = contextByOutput.get(outputId) || { context: outputId, status };
    const hasDetail = truthContextHasDetail(context, status);
    const row = document.createElement(hasDetail ? "details" : "div");
    row.className = `truth-network-context-row status-${status}`;
    if (hasDetail) {
      row.classList.add("has-detail");
    }
    if (outputId === primaryOutput) {
      row.classList.add("is-primary");
    }
    const head = document.createElement(hasDetail ? "summary" : "div");
    head.className = "truth-network-context-head";
    const label = document.createElement("strong");
    label.textContent = truthContextChipLabel(outputId);
    const statusText = document.createElement("span");
    statusText.className = "truth-network-context-status";
    statusText.textContent = truthOutputStatusLabel(status);
    head.append(label, statusText);
    row.appendChild(head);
    if (hasDetail) {
      const detail = document.createElement("div");
      detail.className = "truth-network-context-detail";
      appendTruthContextDetail(detail, context);
      row.appendChild(detail);
    }
    contexts.appendChild(row);
  }
  panel.appendChild(contexts);
  host.appendChild(panel);
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

    const truthRow = document.createElement("div");
    truthRow.className = "simulator-capability-row";
    const truthLabel = document.createElement("strong");
    truthLabel.textContent = "Truth networks";
    const truthWrap = document.createElement("div");
    truthWrap.className = "truth-networks-host";
    appendTruthNetworksSummary(
      truthWrap,
      capability.truth_outputs || {},
      capability.truth_contexts,
      profileId
    );
    truthRow.appendChild(truthLabel);
    truthRow.appendChild(truthWrap);
    card.appendChild(truthRow);

    const truthRequirements = Array.isArray(capability.truth_parameter_requirements)
      ? capability.truth_parameter_requirements
      : [];
    if (truthRequirements.length) {
      const truthRulesRow = document.createElement("div");
      truthRulesRow.className = "simulator-capability-row";
      const truthRulesLabel = document.createElement("strong");
      truthRulesLabel.textContent = "Truth parameter rules";
      const truthRulesWrap = document.createElement("div");
      truthRulesWrap.className = "simulator-truth-rules";
      for (const requirement of truthRequirements) {
        const rule = document.createElement("div");
        rule.className = "simulator-truth-rule";
        const head = document.createElement("strong");
        head.textContent = artifactDisplayLabel(String(requirement?.truth_output || ""));
        const condition = document.createElement("code");
        const conditionText = Array.isArray(requirement?.conditions)
          ? requirement.conditions.map(formatSimulatorInputCondition).filter(Boolean).join(" AND ")
          : "";
        condition.textContent = conditionText || "always";
        const message = document.createElement("span");
        message.textContent = String(requirement?.message || "").trim();
        rule.append(head, condition, message);
        truthRulesWrap.appendChild(rule);
      }
      truthRulesRow.append(truthRulesLabel, truthRulesWrap);
      card.appendChild(truthRulesRow);
    }

    const extrasRow = document.createElement("div");
    extrasRow.className = "simulator-capability-row";
    const extrasLabel = document.createElement("strong");
    extrasLabel.textContent = "Standardized extras";
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
    nativeOutputLabel.textContent = "Native / provenance outputs";
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
  root.innerHTML = "";
  const summary = report.catalog_summary || {};
  const scenario = report.scenario || {};
  const organism = scenario.organism && typeof scenario.organism === "object" ? scenario.organism : {};
  const taxonRaw = organism.ncbi_taxon_id;
  const taxonId = taxonRaw === null || taxonRaw === undefined ? "-" : String(taxonRaw);
  const issueCounts = countPreflightIssues(report);

  appendPreflightSummaryBand(root, "Scenario", [
    { label: "Scenario", value: scenario.id || "-" },
    { label: "Profile", value: scenario.profile || "-" },
    {
      label: "Organism",
      value: organism.taxonomic_group || "synthetic",
      detail: `NCBI taxon ${taxonId}`,
    },
  ]);

  appendPreflightExtrasBand(root, scenario);

  appendPreflightSummaryBand(root, "Simulator Catalog", [
    { label: "Total", value: summary.total ?? 0 },
    { label: "Eligible", value: summary.eligible ?? 0, tone: "ok" },
    { label: "Warning", value: summary.warning ?? 0, tone: Number(summary.warning || 0) ? "warning" : "" },
    { label: "Blocked", value: summary.blocked ?? 0, tone: Number(summary.blocked || 0) ? "blocked" : "" },
    { label: "Issues", value: issueCounts.total, detail: `${issueCounts.warn} warn · ${issueCounts.block} block` },
  ]);

  const rawDetails = document.createElement("details");
  rawDetails.className = "preflight-list";
  const summaryNode = document.createElement("summary");
  summaryNode.textContent = "Raw preflight_report.json";
  rawDetails.appendChild(summaryNode);
  const pre = document.createElement("pre");
  pre.textContent = JSON.stringify(report, null, 2);
  rawDetails.appendChild(pre);
  root.appendChild(rawDetails);
}

function countPreflightIssues(report) {
  const counts = { warn: 0, block: 0, total: 0 };
  const entries = [
    ...(Array.isArray(report?.eligible) ? report.eligible : []),
    ...(Array.isArray(report?.warning) ? report.warning : []),
    ...(Array.isArray(report?.blocked) ? report.blocked : []),
  ];
  for (const entry of entries) {
    for (const issue of entry.issues || []) {
      const severity = String(issue?.severity || "").trim();
      if (severity === "warn") {
        counts.warn += 1;
      } else if (severity === "block") {
        counts.block += 1;
      }
    }
  }
  counts.total = counts.warn + counts.block;
  return counts;
}

function appendPreflightMetricCard(parent, { label, value, detail = "", tone = "" }) {
  const card = document.createElement("article");
  card.className = `preflight-kpi${tone ? ` ${tone}` : ""}`;
  const title = document.createElement("strong");
  title.textContent = label;
  const main = document.createElement("span");
  main.textContent = String(value ?? "-");
  card.append(title, main);
  if (detail) {
    const small = document.createElement("small");
    small.textContent = detail;
    card.appendChild(small);
  }
  parent.appendChild(card);
}

function appendPreflightSummaryBand(parent, title, cards) {
  const section = document.createElement("section");
  section.className = "preflight-summary-section";
  const heading = document.createElement("h4");
  heading.textContent = title;
  const grid = document.createElement("div");
  grid.className = "preflight-grid";
  for (const card of cards) {
    appendPreflightMetricCard(grid, card);
  }
  section.append(heading, grid);
  parent.appendChild(section);
}

function appendPreflightExtrasBand(parent, scenario) {
  const section = document.createElement("section");
  section.className = "preflight-summary-section";
  const heading = document.createElement("h4");
  heading.textContent = "Standardized Extras";
  const grid = document.createElement("div");
  grid.className = "preflight-extra-grid";
  const requested = normalizedExtraList(scenario.requested_extras || []);
  const required = normalizedExtraList(profileRequiredExtras(scenario.profile || selectedProfileId()));
  const requiredSet = new Set(required);
  const selected = requested.filter((item) => !requiredSet.has(item));
  appendPreflightExtraSet(grid, "Selected", selected);
  appendPreflightExtraSet(
    grid,
    "Added automatically",
    required,
    required.length ? "Required by the selected canonical profile." : ""
  );
  section.append(heading, grid);
  parent.appendChild(section);
}

function normalizedExtraList(items) {
  return [
    ...new Set(
      (Array.isArray(items) ? items : [])
        .map((item) => String(item || "").trim())
        .filter(Boolean)
    ),
  ].sort();
}

function appendPreflightExtraSet(parent, title, items, note = "") {
  const block = document.createElement("div");
  block.className = "preflight-extra-set";
  const head = document.createElement("div");
  head.className = "preflight-extra-head";
  const label = document.createElement("strong");
  label.textContent = title;
  const count = document.createElement("span");
  count.textContent = String(items.length);
  head.append(label, count);
  const chips = document.createElement("div");
  chips.className = "preflight-chip-row";
  if (!items.length) {
    const empty = document.createElement("span");
    empty.className = "preflight-chip muted";
    empty.textContent = "-";
    chips.appendChild(empty);
  } else {
    for (const item of items) {
      const chip = document.createElement("span");
      chip.className = "preflight-chip";
      chip.textContent = String(item);
      chips.appendChild(chip);
    }
  }
  block.append(head, chips);
  if (note) {
    const noteNode = document.createElement("small");
    noteNode.className = "preflight-extra-note";
    noteNode.textContent = note;
    block.appendChild(noteNode);
  }
  parent.appendChild(block);
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
    node.dataset.simulatorId = simulator.simulator_id;
    node.querySelector(".tool-item-name").textContent = simulator.name;
    const statusBadge = node.querySelector(".tool-item-badge");
    statusBadge.textContent = kind;
    const badgeWrap = document.createElement("div");
    badgeWrap.className = "tool-item-badges";
    statusBadge.replaceWith(badgeWrap);
    badgeWrap.appendChild(statusBadge);
    const countBadge = document.createElement("span");
    countBadge.className = "selection-count-badge";
    countBadge.dataset.selectionCountFor = simulator.simulator_id;
    countBadge.title = "Selected runs for this simulator";
    countBadge.textContent = "0";
    badgeWrap.appendChild(countBadge);
    const meta = node.querySelector(".tool-item-meta");
    meta.innerHTML = "";
    const byline = document.createElement("span");
    byline.textContent = `${simulator.first_author || "-"} ${simulator.year || ""}`.trim();
    meta.appendChild(byline);
    const actions = node.querySelector(".tool-item-actions");
    appendSimulatorTruthContextChips(meta, entry);
    const infoBtn = document.createElement("button");
    infoBtn.type = "button";
    infoBtn.className = "neutral";
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
      detailsBtn.className = kind === "blocked" ? "danger" : "warning";
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
  refreshSimulatorCatalogRunCounts();
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
  refreshSimulatorCatalogRunCounts();
}

function selectedSimulatorRunCounts() {
  const counts = new Map();
  document.querySelectorAll(".run-card .simulator-id").forEach((input) => {
    const simulatorId = String(input?.value || "").trim();
    if (!simulatorId) {
      return;
    }
    counts.set(simulatorId, (counts.get(simulatorId) || 0) + 1);
  });
  return counts;
}

function refreshSimulatorCatalogRunCounts() {
  const counts = selectedSimulatorRunCounts();
  document.querySelectorAll(".tool-item[data-simulator-id]").forEach((card) => {
    const simulatorId = String(card.dataset.simulatorId || "").trim();
    const count = counts.get(simulatorId) || 0;
    card.classList.toggle("has-selected-runs", count > 0);
    const badge = card.querySelector(".selection-count-badge");
    if (!badge) {
      return;
    }
    badge.textContent = String(count);
    badge.classList.toggle("is-active", count > 0);
    badge.setAttribute(
      "aria-label",
      `${count} selected ${count === 1 ? "run" : "runs"} for ${simulatorId}`
    );
  });
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
    refreshSimulatorCatalogRunCounts();
  });
  $("runs-container").appendChild(node);
  updateRunsEmptyState();
  refreshRunCardsValidation();
  syncButtons();
  refreshSimulatorCatalogRunCounts();
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
    const selectedNativeOutputs = readNativeOutputsFromHost(card.querySelector(".run-native-outputs-form"));
    if (params) {
      messages.push(...simulatorInputMessages(simulator, params, selectedNativeOutputs));
      messages.push(...simulatorCompatibilityMessages(simulator, params, selectedNativeOutputs));
      messages.push(...profileTruthConditionMessages(simulator, params));
    }
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
  const appendPlanCell = (tr, value, className = "") => {
    const td = document.createElement("td");
    td.textContent = String(value ?? "-");
    if (className) {
      td.className = className;
    }
    tr.appendChild(td);
  };
  for (const run of plan.runs || []) {
    const tr = document.createElement("tr");
    appendPlanCell(tr, run.run_id);
    appendPlanCell(tr, run.simulator_id);
    appendPlanCell(tr, run.replicates);
    appendPlanCell(tr, run.runtime_resources?.threads ?? "-");
    appendPlanCell(tr, run.ram_gb ?? "-");
    appendPlanCell(tr, run.eta_seconds ?? "-");
    appendPlanCell(tr, run.eta_source ?? "-");
    appendPlanCell(
      tr,
      (run.native_outputs || []).join(", ") || "-",
      "wrap-cell native-outputs-cell",
    );
    appendPlanCell(tr, run.base_seed);
    appendPlanCell(tr, (run.replicate_seeds || []).join(", "), "wrap-cell");
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
    appendPlanCell(tr, wave.index);
    appendPlanCell(
      tr,
      (wave.tasks || []).map((task) => task.task_id).join(", "),
      "wrap-cell",
    );
    appendPlanCell(tr, wave.threads_used);
    appendPlanCell(tr, wave.ram_gb_used);
    appendPlanCell(tr, wave.eta_seconds);
    appendPlanCell(tr, `${wave.eta_start_seconds ?? "-"}-${wave.eta_end_seconds ?? "-"}`);
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
    appendPlanCell(tr, task.task_id);
    appendPlanCell(tr, task.run_id);
    appendPlanCell(tr, task.replicate_index);
    appendPlanCell(tr, task.seed);
    appendPlanCell(tr, task.runtime_resources?.threads ?? "-");
    appendPlanCell(tr, task.ram_gb ?? "-");
    appendPlanCell(tr, task.eta_seconds ?? "-");
    appendPlanCell(tr, task.eta_wave ?? "-");
    appendPlanCell(tr, task.dataset_id, "wrap-cell");
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
  const key = `${job.job_id}:${state.filesMode}:${job.status}:${job.benchmark_root}`;
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
  refreshSimulatorCatalogRunCounts();
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
      refreshSimulatorCatalogRunCounts();
    } catch (err) {
      pushToast({ title: "Run configuration error", message: err.message, kind: "error", ttlMs: 8000 });
    }
  });
  $("clear-runs-btn").addEventListener("click", () => {
    $("runs-container").innerHTML = "";
    updateRunsEmptyState();
    refreshRunCardsValidation();
    syncButtons();
    refreshSimulatorCatalogRunCounts();
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
  $("refresh-files-btn").addEventListener("click", () => {
    state.loadedFilesKey = null;
    state.filePreviewLoadedKey = null;
    state.filePreviewPendingKey = null;
    fetchFiles(state, fileApi(), {}, fileExplorerOptions()).catch((err) => {
      pushToast({ title: "Files error", message: err.message, kind: "error" });
    });
  });
  $("download-bundle-btn").addEventListener("click", () => {
    if (!state.jobId) {
      return;
    }
    openBundleDownloadModal({
      title: "Download Generate-data ZIP",
      metadataUrl: `/api/generate-data/jobs/${state.jobId}/bundles`,
      downloadUrlForBundle: (bundleId, bundle = {}) => {
        const params = new URLSearchParams({ bundle_id: bundleId });
        if (bundle.dataset_id) {
          params.set("dataset_id", bundle.dataset_id);
        }
        return `/api/generate-data/jobs/${state.jobId}/bundle?${params.toString()}`;
      },
    }).catch((err) => {
      pushToast({ title: "Bundle options error", message: err.message, kind: "error" });
    });
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") {
      return;
    }
    closeParamsModal();
    closeInputModal();
    closeModal("simulator-request-modal");
    closeModal("simulator-issue-modal");
    closeBundleDownloadModal();
  });
}

async function main() {
  initSteps(3);
  initInfoPopover();
  initReproducibility();
  initBundleDownloadModal();
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
