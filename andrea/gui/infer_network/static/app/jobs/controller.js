import { $ } from "../core/dom.js";
import { fetchJobData, fetchPlanData } from "../core/api.js";
import { state } from "../core/state.js";
import { updateToolEligibilityView } from "../catalog/view.js";
import { fetchFiles, resetFilesView } from "../files/explorer.js?v=20260611a";
import {
  renderPlan,
  renderPlanFailure,
  renderPlanningProgress,
  stopPlanningProgress,
} from "../plan/view.js?v=20260623b";
import { resetReproducibility, renderReproducibility } from "../repro/view.js";
import {
  renderAndreaExecutionProgress,
  pushRuntimeFailureToasts,
  renderRuntimeProgress,
} from "../runtime/view.js";
import { setStepState, setActiveStep } from "../ui/steps.js";
import { pushToast } from "../ui/toasts.js";
import { refreshRunCardsValidation } from "../runs/cards.js?v=20260623b";

const EXPLORER_FILES_MODE = "available_outputs";

export function freezeActions(disabled) {
  const ids = [
    "analyze-btn",
    "plan-btn",
    "execute-btn",
    "add-all-tools-btn",
    "clear-runs-btn",
    "step-1-next-btn",
    "step-2-next-btn",
    "refresh-files-btn",
  ];
  for (const id of ids) {
    const node = $(id);
    if (node) {
      node.disabled = disabled;
    }
  }
}

export function hasExecutionArtifacts(job = null, outputReadiness = null) {
  if (!job || !job.run_dir) {
    return false;
  }
  if (outputReadiness && typeof outputReadiness === "object") {
    return Boolean(outputReadiness.explorer_available);
  }
  const stage = String(job.stage || "");
  const status = String(job.status || "");
  return stage === "executed" || status === "failed";
}

function resultsExplorerWaitingMessage(job = null, executionState = null, outputReadiness = null) {
  const readinessMessage = String(outputReadiness?.message || "").trim();
  if (!job || !job.run_dir) {
    return readinessMessage || "Results Explorer will be available after execution.";
  }
  if (readinessMessage) {
    return readinessMessage;
  }
  const phase = String(executionState?.phase || "").trim();
  if (phase === "running_tools") {
    return "Tools are still running. Results Explorer will be available after ANDREA finalizes outputs.";
  }
  if (
    [
      "collecting_results",
      "finalizing_grouped",
      "finalizing_group_aggregated",
      "merging_raw_networks",
      "normalizing_scores",
    ].includes(phase)
  ) {
    return "ANDREA is finalizing merged network outputs. Results Explorer will be available shortly.";
  }
  if (phase === "exporting_artifacts") {
    return "ANDREA is exporting graph artifacts. Results Explorer will be available when exports complete.";
  }
  if (phase === "writing_report") {
    return "ANDREA is writing the final report. Results Explorer will be available shortly.";
  }
  if (String(job.status || "") === "running") {
    return "ANDREA is executing the run. Results Explorer will be available after output finalization.";
  }
  return "Results Explorer will be available after execution.";
}

function renderResultsExplorerPlaceholder(node, message) {
  node.replaceChildren();
  const title = document.createElement("div");
  title.className = "results-placeholder-title";
  title.textContent = "Results Explorer";
  const body = document.createElement("div");
  body.className = "results-placeholder-message";
  body.textContent = message;
  node.append(title, body);
}

function appendReadinessChip(parent, label, ready) {
  const chip = document.createElement("span");
  chip.className = `results-readiness-chip ${ready ? "is-ready" : "is-pending"}`;
  chip.textContent = `${label}: ${ready ? "ready" : "pending"}`;
  parent.appendChild(chip);
}

function renderResultsExplorerStatus(outputReadiness = null) {
  const statusNode = $("results-explorer-status");
  if (!statusNode) {
    return;
  }
  if (!outputReadiness || !outputReadiness.explorer_available) {
    statusNode.hidden = true;
    statusNode.className = "results-explorer-status";
    statusNode.textContent = "";
    return;
  }

  const message = String(outputReadiness.message || "Execution outputs are available.").trim();
  const classes = ["results-explorer-status"];
  if (outputReadiness.partial) {
    classes.push("status-partial");
  } else if (outputReadiness.finalizing_artifacts || !outputReadiness.graph_exports_ready) {
    classes.push("status-finalizing");
  } else {
    classes.push("status-ready");
  }
  statusNode.className = classes.join(" ");
  statusNode.replaceChildren();
  const main = document.createElement("div");
  main.className = "results-explorer-status-main";
  main.textContent = message;
  const chips = document.createElement("div");
  chips.className = "results-explorer-status-chips";
  appendReadinessChip(chips, "Merged CSVs", Boolean(outputReadiness.csv_ready));
  appendReadinessChip(chips, "Run report", Boolean(outputReadiness.final_report_ready));
  appendReadinessChip(chips, "Graph exports", Boolean(outputReadiness.graph_exports_ready));
  statusNode.append(main, chips);
  if (outputReadiness.finalizing_artifacts && outputReadiness.csv_ready) {
    const note = document.createElement("div");
    note.className = "results-explorer-status-note";
    note.textContent = "CSV inspection is available while ANDREA finishes report and graph artifacts.";
    statusNode.appendChild(note);
  }
  statusNode.hidden = false;
}

export function updateResultsExplorerVisibility(job = null, executionState = null, outputReadiness = null) {
  const section = $("results-explorer-section");
  const placeholder = $("results-explorer-placeholder");
  const visible = hasExecutionArtifacts(job, outputReadiness);
  const waitingMessage = resultsExplorerWaitingMessage(job, executionState, outputReadiness);
  if (section) {
    section.hidden = !visible;
  }
  if (placeholder) {
    placeholder.hidden = visible;
    if (!visible) {
      renderResultsExplorerPlaceholder(placeholder, waitingMessage);
    }
  }
  if (!visible) {
    resetFilesView(waitingMessage);
    resetReproducibility("Reproducibility snippets will be available after final report writing.");
  }
  renderResultsExplorerStatus(visible ? outputReadiness : null);
}

function updateExplorerViewLabel() {
  const node = $("results-explorer-view-label");
  if (!node) {
    return;
  }
  node.textContent = "Explorer view: available output files";
}

export async function fetchPlan(jobId) {
  const payload = await fetchPlanData(jobId);
  renderPlan(payload.plan);
  return payload.plan;
}

export async function refreshArtifacts(job, outputReadiness = null) {
  if (!job || !job.job_id) {
    return;
  }

  if (job.plan_path) {
    const planKey = `${job.job_id}:${job.plan_path}`;
    if (state.loadedPlanKey !== planKey) {
      await fetchPlan(job.job_id);
      state.loadedPlanKey = planKey;
    }
  }

  if (hasExecutionArtifacts(job, outputReadiness)) {
    if (outputReadiness && !outputReadiness.explorer_available) {
      state.filesMode = EXPLORER_FILES_MODE;
      resetFilesView(outputReadiness?.message || "Merged output files are still being prepared.");
      return;
    }
    state.filesMode = EXPLORER_FILES_MODE;
    updateExplorerViewLabel();
    const readinessKey = [
      outputReadiness?.csv_ready ? "csv" : "no-csv",
      outputReadiness?.final_report_ready ? "report" : "no-report",
      outputReadiness?.graph_exports_ready ? "graphs" : "no-graphs",
      outputReadiness?.partial ? "partial" : "complete",
      Object.entries(outputReadiness?.paths || {})
        .filter(([, value]) => value)
        .map(([key]) => key)
        .sort()
        .join(","),
    ].join(":");
    const desiredFilesKey = `${job.job_id}:${EXPLORER_FILES_MODE}:${job.status}:${job.run_dir || ""}:${readinessKey}`;
    if (state.loadedFilesKey !== desiredFilesKey) {
      try {
        await fetchFiles(job.job_id);
        state.loadedFilesKey = desiredFilesKey;
      } catch (err) {
        resetFilesView(err.message || "Result files are not ready yet.");
      }
    }
  }
}

export async function pollJob(jobId) {
  const payload = await fetchJobData(jobId);
  const job = payload.job;
  state.currentJob = job;
  const preflightReport = payload.preflight_report;
  const executionState = payload.execution_state;
  const outputReadiness = payload.output_readiness;
  const runtimeProgress = payload.runtime_progress;
  const reproducibility = payload.reproducibility;
  state.stage = job.stage || state.stage;
  state.executionState = executionState || null;
  state.outputReadiness = outputReadiness || null;
  state.runtimeProgress = runtimeProgress || null;
  if (job.stage === "planned" && job.status !== "running" && state.activeStep < 3) {
    setActiveStep(2, { scroll: false });
  }
  if (
    job.status === "running" &&
    (job.stage === "planned" || job.stage === "executed") &&
    state.activeStep < 3 &&
    state.autoFollowExecutionStep !== false
  ) {
    setActiveStep(3, { scroll: false });
  }
  if (job.active_action === "plan" && ["queued", "running"].includes(String(job.status || ""))) {
    renderPlanningProgress(job);
  } else {
    stopPlanningProgress();
  }
  const planFailed = job.status === "failed" && job.tools_params_path && !job.plan_path;
  if (planFailed) {
    renderPlanFailure(job);
    const failureKey = `${job.job_id}:plan:${job.error || job.progress_detail || ""}`;
    if (!state.notifiedFailures.has(failureKey)) {
      state.notifiedFailures.add(failureKey);
      pushToast({
        title: "Planning failed",
        message: String(
          job.error ||
            job.progress_detail ||
            "The execution plan could not be generated."
        ),
        kind: "error",
        ttlMs: 12000,
      });
    }
  }

  updateToolEligibilityView(preflightReport);
  renderAndreaExecutionProgress(executionState, job);
  renderRuntimeProgress(runtimeProgress);
  pushRuntimeFailureToasts(runtimeProgress, state.notifiedFailures);
  renderReproducibility(reproducibility);
  updateResultsExplorerVisibility(job, executionState, outputReadiness);

  syncActionButtons(job);
  await refreshArtifacts(job, outputReadiness);

  if (job.status === "completed" || job.status === "failed") {
    freezeActions(false);
    syncActionButtons(job);
    if (state.pollTimer) {
      window.clearInterval(state.pollTimer);
      state.pollTimer = null;
    }
  }
}

export function syncActionButtons(job = null) {
  const activeJob = job || state.currentJob;
  const analyzeBtn = $("analyze-btn");
  const planBtn = $("plan-btn");
  const executeBtn = $("execute-btn");
  const addAllBtn = $("add-all-tools-btn");
  const clearRunsBtn = $("clear-runs-btn");
  const step1NextBtn = $("step-1-next-btn");
  const step2NextBtn = $("step-2-next-btn");

  if (!activeJob) {
    analyzeBtn.disabled = false;
    planBtn.disabled = true;
    executeBtn.disabled = true;
    addAllBtn.disabled = true;
    clearRunsBtn.disabled = true;
    step1NextBtn.disabled = true;
    step2NextBtn.disabled = true;
    setStepState(1, "ready");
    setStepState(2, "blocked");
    setStepState(3, "blocked");
    return;
  }

  const isBusy = activeJob.status === "queued" || activeJob.status === "running";
  const preflightReady = ["preflight_ok", "planned", "executed"].includes(activeJob.stage || "");
  const planReady = ["planned", "executed"].includes(activeJob.stage || "");
  const runReady = String(activeJob.stage || "") === "planned";
  const hasRuns = document.querySelectorAll(".run-card").length > 0;
  const runsValid = hasRuns ? refreshRunCardsValidation() : true;

  analyzeBtn.disabled = isBusy;
  planBtn.disabled = isBusy || !preflightReady || !hasRuns || !runsValid;
  executeBtn.disabled = isBusy || !runReady;
  addAllBtn.disabled = isBusy || !preflightReady;
  clearRunsBtn.disabled = isBusy || !preflightReady || !hasRuns;
  step1NextBtn.disabled = !preflightReady || isBusy;
  step2NextBtn.disabled = !planReady || isBusy;

  const step1State = preflightReady ? "ready" : isBusy ? "running" : "blocked";
  const step2State = planReady ? "ready" : preflightReady ? (isBusy ? "running" : "ready") : "blocked";
  const step3State = activeJob.stage === "executed" ? "ready" : runReady ? (isBusy ? "running" : "ready") : "blocked";
  setStepState(1, step1State);
  setStepState(2, step2State);
  setStepState(3, step3State);
}

export async function startPolling(jobId) {
  if (state.pollTimer) {
    window.clearInterval(state.pollTimer);
  }
  await pollJob(jobId);
  state.pollTimer = window.setInterval(() => {
    pollJob(jobId).catch((err) => {
      pushToast({ title: "Polling error", message: err.message, kind: "error", ttlMs: 7000 });
    });
  }, 1500);
}
