import { fetchBootstrapData, submitPlanRequest, submitPreflightRequest, submitRunRequest } from "./core/api.js";
import { $, fillSelect } from "./core/dom.js";
import { state } from "./core/state.js";
import {
  closeBundleDownloadModal,
  initBundleDownloadModal,
  openBundleDownloadModal,
} from "/static-common/app/bundles/modal.js?v=20260612a";
import { buildToolIssueReportUrl, buildToolRequestIssueUrl, defaultGroupModeForTool, listAvailableTools, populateToolIssueSelect, toolById } from "./catalog/model.js";
import { initCatalogView, refreshToolCatalogRunCounts, updateToolEligibilityView } from "./catalog/view.js";
import {
  addCustomToolParamRow,
  addCustomToolDefinition,
  buildSimpleCustomToolFromForm,
  customToolDockerImageFromForm,
  customToolsPayload,
  pruneCustomToolsToSelectedToolIds,
  removeCustomToolDefinition,
  resetCustomToolParamRows,
} from "./catalog/external_tools.js";
import { applyDatasetDefaults, handleExpressionSelected, initExpressionDropzone, syncExpressionHelpTooltip } from "./dataset/expression.js";
import { closeExtraInputModal, getExtraRows, initExtras, listProvidedExtraKeys, openExtraInputModal, updateExtrasEmptyState } from "./dataset/extras.js";
import { renderAndreaExecutionProgress, renderRuntimeProgress } from "./runtime/view.js";
import { fetchFiles, resetFilesView } from "./files/explorer.js?v=20260611a";
import { freezeActions, startPolling, syncActionButtons, updateResultsExplorerVisibility } from "./jobs/controller.js";
import { resetPlanView } from "./plan/view.js";
import { closeReproducibilityStepsModal, initReproducibility, resetReproducibility } from "./repro/view.js";
import { closeParamsModal, initParamsModal, openParamsModal, applyParamsModal, setParamsModalStatus } from "./runs/params_modal.js";
import { addRunCard, collectRuns, initRunCards, readParamsFromCard, refreshRunCardsValidation, renderRunParamsForm, updateRunsEmptyState } from "./runs/cards.js";
import { executionModeAvailability } from "./runs/execution_modes.js";
import { readParamsFromHost, renderParamsHost, resolvedDefaultParams } from "/static-common/app/params/schema_form.js?v=20260615a";
import { buildInfoTooltip, hideInfoTooltip, readHelpPayload, showInfoTooltip } from "./ui/popovers.js";
import { setActiveStep, setStepState } from "./ui/steps.js";
import { pushToast } from "./ui/toasts.js";

function buildDatasetConfig() {
  const datasetId = $("dataset-id").value.trim();
  if (!datasetId) {
    throw new Error("dataset_id is required.");
  }
  const taxonomicGroup = $("taxonomic-group").value.trim();
  const organismTaxIdRaw = $("organism-ncbi-taxon-id").value.trim();
  const organismTaxId = organismTaxIdRaw ? Number(organismTaxIdRaw) : null;
  if (!taxonomicGroup) {
    throw new Error("organism.taxonomic_group is required.");
  }
  if (
    organismTaxIdRaw &&
    (!Number.isInteger(organismTaxId) || organismTaxId < 1)
  ) {
    throw new Error("organism.ncbi_taxon_id must be a positive integer or empty for synthetic/unknown datasets.");
  }
  if (!["synthetic", "unknown"].includes(taxonomicGroup) && organismTaxId === null) {
    throw new Error("organism.ncbi_taxon_id is required for biological taxonomic groups.");
  }

  return {
    dataset: {
      id: datasetId,
      column_kind: $("column-kind").value,
      expression_profile: $("expression-profile").value,
      organism: {
        taxonomic_group: taxonomicGroup,
        ncbi_taxon_id: organismTaxId,
      },
    },
    options: buildOptions(),
  };
}

function buildOptions() {
  return {
    output_dir: $("output-dir").value.trim() || "./inferred_networks",
    max_cores: Number($("max-cores").value),
    max_ram_gb: $("max-ram").value.trim() ? Number($("max-ram").value) : null,
    planner: $("planner").value,
    planner_time_limit_seconds: Number($("planner-time").value),
    progress_poll_seconds: Number($("progress-poll").value),
  };
}

function selectedRunToolIds() {
  return new Set(
    Array.from(document.querySelectorAll(".run-card .tool-id"))
      .map((input) => String(input?.value || "").trim())
      .filter(Boolean)
  );
}

function purgeUnreferencedCustomTools() {
  const removed = pruneCustomToolsToSelectedToolIds(selectedRunToolIds());
  if (removed > 0) {
    updateToolEligibilityView(state.preflightReport);
  }
}

function maybeRemoveCustomToolForDeletedRun(toolId) {
  const normalizedToolId = String(toolId || "").trim();
  if (!normalizedToolId.startsWith("custom_")) {
    return;
  }
  if (selectedRunToolIds().has(normalizedToolId)) {
    return;
  }
  if (removeCustomToolDefinition(normalizedToolId)) {
    updateToolEligibilityView(state.preflightReport);
  }
}

function showExternalDockerToolGuide() {
  showInfoTooltip(buildInfoTooltip({
    title: "External Docker image contract",
    description: "The image can be local, from Docker Hub, from another registry, or pinned by digest. ANDREA only requires that its entrypoint follows this command-line contract.",
    sections: [
      {
        title: "How ANDREA runs the image",
        text: "Your image entrypoint must accept these flags. ANDREA mounts a writable run folder at /io, disables networking for custom tools and applies Docker CPU/RAM limits.",
        json: "docker run --network none \\\n  --cpus <threads> --memory <ram>g \\\n  -v <run>/io:/io IMAGE:TAG \\\n  --input /io/expression.tsv \\\n  --params /io/params.json \\\n  --extra /io/extra \\\n  --output-dir /io/out \\\n  --threads <threads>",
      },
      {
        title: "What the image receives",
        items: [
          "/io/expression.tsv is the normalized expression matrix provided in Step 1.",
          "/io/params.json is a key-value JSON object for the image's own internal parameters. The image implementation must parse it; ANDREA only writes it.",
          "/io/extra contains the Step 1 extra files whose standardized names are also listed in this external-tool form.",
        ],
      },
      {
        title: "What the image must write",
        items: [
          "Run the inference method inside the container and write /io/out/network.csv.",
          "network.csv must include source,target,score,sign,evidence,context.",
          "score must be a positive magnitude. Activation/repression direction must be stored only in sign.",
          "context must match the selected execution mode: global, group:<group_id> or cell:<cell_id>.",
          "/io/out/progress.json is optional but recommended for live progress updates.",
        ],
      },
    ],
  }));
}

function parseExternalExtraInputKeys(value) {
  return String(value || "")
    .split(/[\s,;]+/g)
    .map((item) => item.trim())
    .filter(Boolean);
}

function syncExternalToolExtraOptions() {
  const host = $("custom-tool-extra-options");
  const input = $("custom-tool-needed-extras");
  const provided = Array.from(listProvidedExtraKeys()).sort((left, right) =>
    String(left).localeCompare(String(right))
  );
  const selected = new Set(parseExternalExtraInputKeys(input.value));
  host.innerHTML = "";
  if (!provided.length) {
    const empty = document.createElement("span");
    empty.className = "custom-tool-extra-empty";
    empty.textContent = "No Step 1 extra inputs have been provided yet.";
    host.appendChild(empty);
    return;
  }
  for (const key of provided) {
    const label = document.createElement("label");
    label.className = `custom-tool-extra-choice${selected.has(key) ? " selected" : ""}`;
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = selected.has(key);
    const text = document.createElement("span");
    text.textContent = key;
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) {
        selected.add(key);
      } else {
        selected.delete(key);
      }
      input.value = Array.from(selected).sort().join(", ");
      syncExternalToolExtraOptions();
    });
    label.append(checkbox, text);
    host.appendChild(label);
  }
}

function syncExternalToolExecutionModes() {
  const providedExtras = listProvidedExtraKeys();
  const radios = Array.from(document.querySelectorAll("input[name='custom-tool-execution-mode']"));
  let checkedIsDisabled = false;
  for (const radio of radios) {
    const availability = executionModeAvailability({
      mode: radio.value,
      providedExtras,
    });
    radio.disabled = !availability.available;
    const label = radio.closest("label");
    if (label) {
      label.classList.toggle("disabled", !availability.available);
      label.title = availability.reason || "";
    }
    if (radio.checked && radio.disabled) {
      checkedIsDisabled = true;
    }
  }
  if (checkedIsDisabled) {
    const firstAvailable = radios.find((radio) => !radio.disabled);
    if (firstAvailable) {
      firstAvailable.checked = true;
    }
  }
}

function setCustomToolImageCheckState(kind, message) {
  const grid = document.querySelector(".external-tool-docker-grid");
  const status = $("custom-tool-image-status");
  if (grid) {
    grid.classList.toggle("image-valid", kind === "valid");
    grid.classList.toggle("image-invalid", kind === "invalid");
  }
  if (status) {
    status.className = "custom-tool-image-status";
    if (kind === "valid") {
      status.classList.add("valid");
    } else if (kind === "invalid") {
      status.classList.add("invalid");
    }
    status.textContent = message || "Not checked";
  }
}

async function validateCustomToolDockerImage() {
  const button = $("custom-tool-check-image-btn");
  const image = customToolDockerImageFromForm();
  if (!image) {
    setCustomToolImageCheckState("invalid", "Image name is required.");
    pushToast({
      title: "Docker image missing",
      message: "Enter a Docker image name before validating access.",
      kind: "warning",
      ttlMs: 6000,
    });
    return;
  }
  button.disabled = true;
  setCustomToolImageCheckState("checking", "Checking image...");
  try {
    const response = await fetch("/api/infer-network/docker-image/check", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ image }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.detail || "Docker image check failed.");
    }
    if (payload.available) {
      setCustomToolImageCheckState(
        "valid",
        payload.source === "local" ? "Valid: local image." : "Valid: registry image."
      );
      return;
    }
    setCustomToolImageCheckState("invalid", payload.message || "Image not found.");
  } catch (err) {
    setCustomToolImageCheckState("invalid", err.message);
  } finally {
    button.disabled = false;
  }
}

function buildPreflightFormData(config) {
  const formData = new FormData();
  formData.append("config", JSON.stringify(config));
  const customTools = customToolsPayload();
  if (customTools) {
    formData.append("custom_tools", JSON.stringify(customTools));
  }

  const expressionInput = $("expression-file");
  if (!expressionInput.files || !expressionInput.files[0]) {
    throw new Error("Expression matrix file is required.");
  }
  formData.append("expression_file", expressionInput.files[0]);

  const seenExtraKeys = new Set();
  getExtraRows().forEach((row) => {
    const key = row.querySelector(".extra-key").value;
    const input = row.querySelector(".extra-file");
    if (!key) {
      return;
    }
    if (seenExtraKeys.has(key)) {
      throw new Error(`Optional input '${key}' is duplicated.`);
    }
    seenExtraKeys.add(key);
    if (!input.files || !input.files[0]) {
      throw new Error(`Additional input '${key}' needs a file. Upload a valid file or remove it.`);
    }
    const status = row.querySelector(".extra-file-status");
    if (status?.classList.contains("err")) {
      throw new Error(`Additional input '${key}' is not valid yet. Fix it or remove it.`);
    }
    formData.append(`extra__${key}`, input.files[0]);
  });

  return formData;
}

async function submitPreflight() {
  try {
    const expressionStatus = $("expression-file-status");
    if (expressionStatus.classList.contains("err")) {
      throw new Error("Expression matrix is invalid. Fix it before submitting.");
    }
    const expressionInput = $("expression-file");
    if (!expressionInput.files || !expressionInput.files[0]) {
      throw new Error("Expression matrix file is required.");
    }
    freezeActions(true);
    const config = buildDatasetConfig();
    const formData = buildPreflightFormData(config);

    state.loadedPlanKey = null;
    state.loadedFilesKey = null;
    state.selectedFilePath = null;
    state.collapsedDirs.clear();
    state.eligibleToolIds = null;
    state.preflightReport = null;
    state.lastPlan = null;
    state.executionState = null;
    state.outputReadiness = null;
    state.runtimeProgress = null;
    state.runtimeWaveUi.runId = null;
    state.runtimeWaveUi.inspectedWaveIndex = null;
    state.runtimeWaveUi.openIssueKeys.clear();
    state.runtimeWaveUi.closedIssueKeys.clear();
    state.runtimeWaveUi.scrollTops = {};
    state.currentJob = null;
    state.notifiedFailures.clear();
    state.autoFollowExecutionStep = true;
    setActiveStep(1, { scroll: false });
    $("runs-container").innerHTML = "";
    updateRunsEmptyState();
    updateToolEligibilityView(null);
    resetPlanView("Waiting for preflight/plan output...");
    updateResultsExplorerVisibility(null);
    resetReproducibility();
    renderAndreaExecutionProgress(null);
    renderRuntimeProgress(null);

    const payload = await submitPreflightRequest(formData);
    state.jobId = payload.job_id;
    state.currentJob = {
      job_id: payload.job_id,
      stage: "draft",
      status: "queued",
    };
    state.stage = "draft";
    setStepState(1, "running");
    setStepState(2, "blocked");
    setStepState(3, "blocked");
    pushToast({
      title: "Preflight submitted",
      message: `Job: ${payload.job_id}`,
      kind: "success",
      ttlMs: 4000,
    });

    await startPolling(state.jobId);
  } catch (err) {
    freezeActions(false);
    syncActionButtons();
    pushToast({ title: "Preflight error", message: err.message, kind: "error", ttlMs: 9000 });
  }
}

async function submitPlan() {
  try {
    if (!state.jobId) {
      throw new Error("Analyze inputs first.");
    }
    setActiveStep(2, { scroll: false });
    const runs = collectRuns();
    freezeActions(true);
    setStepState(2, "running");

    const payload = await submitPlanRequest({
      job_id: state.jobId,
      runs,
      custom_tools: customToolsPayload(new Set(runs.map((run) => run.tool_id))),
      options: buildOptions(),
    });
    pushToast({
      title: "Plan submitted",
      message: `Job: ${payload.job_id}`,
      kind: "success",
      ttlMs: 4000,
    });
    await startPolling(state.jobId);
  } catch (err) {
    freezeActions(false);
    syncActionButtons();
    pushToast({ title: "Planning error", message: err.message, kind: "error", ttlMs: 9000 });
  }
}

async function submitRun() {
  try {
    if (!state.jobId) {
      throw new Error("No planned job found. Generate a plan first.");
    }
    state.autoFollowExecutionStep = true;
    setActiveStep(3, { scroll: false });
    freezeActions(true);
    setStepState(3, "running");

    const payload = await submitRunRequest({
      job_id: state.jobId,
      options: {
        progress_poll_seconds: Number($("progress-poll").value),
      },
    });
    pushToast({
      title: "Execution submitted",
      message: `Job: ${payload.job_id}`,
      kind: "success",
      ttlMs: 4000,
    });
    await startPolling(state.jobId);
  } catch (err) {
    freezeActions(false);
    syncActionButtons();
    pushToast({ title: "Execution error", message: err.message, kind: "error", ttlMs: 9000 });
  }
}

async function bootstrap() {
  state.bootstrap = await fetchBootstrapData();
  fillSelect("column-kind", state.bootstrap.column_kinds);
  fillSelect("expression-profile", state.bootstrap.expression_profiles);
  fillSelect("taxonomic-group", state.bootstrap.taxonomic_groups);
  $("taxonomic-group").value = state.bootstrap.taxonomic_groups.includes("animal") ? "animal" : state.bootstrap.taxonomic_groups[0];
  $("organism-ncbi-taxon-id").value = "9606";
  applyDatasetDefaults();
  populateToolIssueSelect();
  syncExpressionHelpTooltip();
  initExpressionDropzone();
  await handleExpressionSelected(null);
  updateExtrasEmptyState();
  updateRunsEmptyState();
  updateToolEligibilityView(null);
  resetPlanView("No plan loaded yet.");
  updateResultsExplorerVisibility(null);
  resetReproducibility();
  renderAndreaExecutionProgress(null);
  renderRuntimeProgress(null);
  state.notifiedFailures.clear();
  state.runtimeWaveUi.runId = null;
  state.runtimeWaveUi.inspectedWaveIndex = null;
  state.runtimeWaveUi.openIssueKeys.clear();
  state.runtimeWaveUi.closedIssueKeys.clear();
  state.runtimeWaveUi.scrollTops = {};
  state.currentJob = null;
  state.autoFollowExecutionStep = true;
  setActiveStep(1, { scroll: false });
  syncActionButtons();
}

function bindEvents() {
  const openModal = (id) => $(id)?.classList.remove("hidden");
  const closeModal = (id) => $(id)?.classList.add("hidden");

  const canOpenStep = (stepNumber) => {
    if (stepNumber <= 1) {
      return true;
    }
    if (stepNumber === 2) {
      return ["preflight_ok", "planned", "executed"].includes(String(state.stage || ""));
    }
    if (stepNumber === 3) {
      return ["planned", "executed"].includes(String(state.stage || ""));
    }
    return false;
  };

  const tryOpenStep = (stepNumber) => {
    if (!canOpenStep(stepNumber)) {
      pushToast({
        title: "Step not available",
        message: `Step ${stepNumber} is not available yet.`,
        kind: "warning",
        ttlMs: 5000,
      });
      return;
    }
    if (state.currentJob?.status === "running") {
      state.autoFollowExecutionStep = false;
    }
    setActiveStep(stepNumber);
  };

  $("expression-info-btn").addEventListener("click", () => {
    const payload = readHelpPayload($("expression-info-btn"));
    if (payload) {
      showInfoTooltip(payload);
    }
  });
  $("add-optional-input-btn").addEventListener("click", () => openExtraInputModal());
  $("extra-input-modal-close").addEventListener("click", () => closeExtraInputModal());
  $("extra-input-modal").addEventListener("click", (event) => {
    if (event.target && event.target.id === "extra-input-modal") {
      closeExtraInputModal();
    }
  });
  $("add-all-tools-btn").addEventListener("click", () => {
    try {
      const existingToolIds = new Set(
        Array.from(document.querySelectorAll(".run-card .tool-id"))
          .map((input) => String(input.value || "").trim())
          .filter(Boolean)
      );
      for (const tool of listAvailableTools()) {
        if (existingToolIds.has(tool.tool_id)) {
          continue;
        }
        addRunCard({
          tool_id: tool.tool_id,
        });
      }
      refreshToolCatalogRunCounts();
    } catch (err) {
      pushToast({ title: "Run configuration error", message: err.message, kind: "error", ttlMs: 8000 });
    }
  });
  $("clear-runs-btn").addEventListener("click", () => {
    $("runs-container").innerHTML = "";
    purgeUnreferencedCustomTools();
    updateRunsEmptyState();
    refreshRunCardsValidation();
    syncActionButtons();
    refreshToolCatalogRunCounts();
  });
  $("open-tool-request-modal-btn").addEventListener("click", () => openModal("tool-request-modal"));
  $("open-tool-issue-modal-btn").addEventListener("click", () => openModal("tool-issue-modal"));
  $("open-external-tool-modal-btn").addEventListener("click", () => {
    syncExternalToolExtraOptions();
    syncExternalToolExecutionModes();
    resetCustomToolParamRows();
    setCustomToolImageCheckState(null, "Not checked");
    openModal("external-tool-modal");
  });
  $("tool-request-modal-close").addEventListener("click", () => closeModal("tool-request-modal"));
  $("tool-issue-modal-close").addEventListener("click", () => closeModal("tool-issue-modal"));
  $("external-tool-modal-close").addEventListener("click", () => closeModal("external-tool-modal"));
  $("tool-request-modal").addEventListener("click", (event) => {
    if (event.target && event.target.id === "tool-request-modal") {
      closeModal("tool-request-modal");
    }
  });
  $("tool-issue-modal").addEventListener("click", (event) => {
    if (event.target && event.target.id === "tool-issue-modal") {
      closeModal("tool-issue-modal");
    }
  });
  $("external-tool-modal").addEventListener("click", (event) => {
    if (event.target && event.target.id === "external-tool-modal") {
      closeModal("external-tool-modal");
    }
  });
  $("external-tool-request-formal-btn").addEventListener("click", () => {
    closeModal("external-tool-modal");
    openModal("tool-request-modal");
  });
  $("custom-tool-image-help-btn").addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    showExternalDockerToolGuide();
  });
  $("custom-tool-check-image-btn").addEventListener("click", () => {
    validateCustomToolDockerImage();
  });
  ["custom-tool-image-name", "custom-tool-image-tag"].forEach((fieldId) => {
    $(fieldId).addEventListener("input", () => {
      setCustomToolImageCheckState(null, "Not checked");
    });
  });
  $("custom-tool-needed-extras").addEventListener("input", () => {
    syncExternalToolExtraOptions();
  });
  $("custom-tool-add-param-row").addEventListener("click", () => {
    addCustomToolParamRow();
  });
  $("custom-tool-add-btn").addEventListener("click", () => {
    try {
      const { tool, run } = buildSimpleCustomToolFromForm();
      const toolId = addCustomToolDefinition(tool);
      updateToolEligibilityView(state.preflightReport);
      addRunCard({ ...run, tool_id: toolId });
      refreshRunCardsValidation();
      syncActionButtons();
      pushToast({
        title: "External run added",
        message: `${run.run_id} will execute ${toolId} as a custom Docker image.`,
        kind: "success",
        ttlMs: 6000,
      });
      closeModal("external-tool-modal");
    } catch (err) {
      pushToast({ title: "External tool error", message: err.message, kind: "error", ttlMs: 9000 });
    }
  });
  $("open-tool-request-issue-btn").addEventListener("click", () => {
    try {
      const url = buildToolRequestIssueUrl();
      window.open(url, "_blank", "noopener");
      closeModal("tool-request-modal");
      pushToast({
        title: "GitHub issue opened",
        message: "Opened prefilled issue in a new tab.",
        kind: "success",
        ttlMs: 4500,
      });
    } catch (err) {
      pushToast({ title: "Tool request error", message: err.message, kind: "error", ttlMs: 7000 });
    }
  });
  $("open-tool-issue-btn").addEventListener("click", () => {
    try {
      const url = buildToolIssueReportUrl();
      window.open(url, "_blank", "noopener");
      closeModal("tool-issue-modal");
      pushToast({
        title: "GitHub issue opened",
        message: "Opened prefilled tool issue in a new tab.",
        kind: "success",
        ttlMs: 4500,
      });
    } catch (err) {
      pushToast({ title: "Tool issue error", message: err.message, kind: "error", ttlMs: 7000 });
    }
  });
  $("analyze-btn").addEventListener("click", () => submitPreflight());
  $("plan-btn").addEventListener("click", () => submitPlan());
  $("execute-btn").addEventListener("click", () => submitRun());
  $("step-1-toggle").addEventListener("click", () => tryOpenStep(1));
  $("step-2-toggle").addEventListener("click", () => tryOpenStep(2));
  $("step-3-toggle").addEventListener("click", () => tryOpenStep(3));
  $("step-1-next-btn").addEventListener("click", () => tryOpenStep(2));
  $("step-2-next-btn").addEventListener("click", () => tryOpenStep(3));
  $("refresh-files-btn").addEventListener("click", async () => {
    if (!state.jobId) {
      pushToast({
        title: "No job selected",
        message: "Submit a job first.",
        kind: "warning",
        ttlMs: 5000,
      });
      return;
    }
    try {
      state.loadedFilesKey = null;
      state.filePreviewLoadedKey = null;
      state.filePreviewPendingKey = null;
      await fetchFiles(state.jobId);
    } catch (err) {
      pushToast({ title: "Files refresh error", message: err.message, kind: "warning", ttlMs: 7000 });
    }
  });
  $("download-bundle-btn").addEventListener("click", () => {
    if (!state.jobId) {
      pushToast({
        title: "No job selected",
        message: "Submit a job first.",
        kind: "warning",
        ttlMs: 5000,
      });
      return;
    }
    openBundleDownloadModal({
      title: "Download Inference ZIP",
      metadataUrl: `/api/infer-network/jobs/${state.jobId}/bundles`,
      downloadUrlForBundle: (bundleId) => (
        `/api/infer-network/jobs/${state.jobId}/bundle?bundle_id=${encodeURIComponent(bundleId)}`
      ),
    }).catch((err) => {
      pushToast({ title: "Bundle options error", message: err.message, kind: "warning", ttlMs: 7000 });
    });
  });

  $("info-popover-close").addEventListener("click", () => hideInfoTooltip());
  $("info-popover").addEventListener("click", (event) => {
    if (event.target && event.target.id === "info-popover") {
      hideInfoTooltip();
    }
  });
  $("params-modal-close").addEventListener("click", () => closeParamsModal());
  $("params-modal").addEventListener("click", (event) => {
    if (event.target && event.target.id === "params-modal") {
      closeParamsModal();
    }
  });
  $("params-modal-reset").addEventListener("click", () => {
    const card = state.paramsModalCard;
    if (!card) {
      return;
    }
    const tool = toolById(String(card.querySelector(".tool-id")?.value || "").trim());
    if (!tool) {
      return;
    }
    renderParamsHost($("params-modal-form"), tool, resolvedDefaultParams(tool), () => {
      try {
        readParamsFromHost(tool, $("params-modal-form"));
        setParamsModalStatus("", "Adjust parameters and apply changes.");
      } catch (err) {
        setParamsModalStatus("err", String(err?.message || "Invalid parameter value"));
      }
    });
    setParamsModalStatus("", "Tool defaults restored in the editor.");
  });
  $("params-modal-save").addEventListener("click", () => {
    try {
      applyParamsModal();
    } catch (err) {
      setParamsModalStatus("err", String(err?.message || "Invalid parameter value"));
    }
  });
  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      hideInfoTooltip();
      closeParamsModal();
      closeModal("tool-request-modal");
      closeModal("tool-issue-modal");
      closeModal("external-tool-modal");
      closeExtraInputModal();
      closeReproducibilityStepsModal();
      closeBundleDownloadModal();
    }
  });
}

function initModules() {
  initExtras({
    onDatasetChanged: () => {
      refreshRunCardsValidation();
      syncActionButtons();
    },
  });
  initParamsModal({
    getToolById: toolById,
    renderRunParamsForm,
    readParamsFromCard,
    onRunsChanged: () => {
      refreshRunCardsValidation();
      syncActionButtons();
    },
  });
  initRunCards({
    getToolById: toolById,
    listAvailableTools,
    listProvidedExtraKeys,
    defaultGroupModeForTool,
    openParamsModal: (card) => {
      try {
        openParamsModal(card);
      } catch (err) {
        pushToast({ title: "Parameters error", message: err.message, kind: "error", ttlMs: 7000 });
      }
    },
    onRunsChanged: () => {
      syncActionButtons();
      refreshToolCatalogRunCounts();
    },
    onRunRemoved: ({ toolId }) => {
      maybeRemoveCustomToolForDeletedRun(toolId);
    },
  });
  initCatalogView({
    onAddRun: addRunCard,
  });
  initReproducibility();
  initBundleDownloadModal();
}

window.addEventListener("DOMContentLoaded", async () => {
  initModules();
  bindEvents();
  try {
    await bootstrap();
  } catch (err) {
    pushToast({ title: "Initialization error", message: err.message, kind: "error", ttlMs: 9000 });
  }
});
