import { $ } from "../core/dom.js";
import { fetchJobData, fetchPlanData } from "../core/api.js";
import { state } from "../core/state.js";
import { updateToolEligibilityView } from "../catalog/view.js";
import { fetchFiles, resetFilesView } from "../files/explorer.js";
import { renderPlan } from "../plan/view.js";
import { resetReproducibility, renderReproducibility } from "../repro/view.js";
import {
  renderAndreaExecutionProgress,
  renderExecutionAlerts,
  pushRuntimeFailureToasts,
  renderRuntimeProgress,
} from "../runtime/view.js";
import { setStepState, setActiveStep } from "../ui/steps.js";
import { pushToast } from "../ui/toasts.js";
import { refreshRunCardsValidation } from "../runs/cards.js";

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
    "download-bundle-btn",
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
  const bits = [];
  if (outputReadiness.csv_ready) {
    bits.push("merged CSVs ready");
  }
  if (outputReadiness.final_report_ready) {
    bits.push("final report ready");
  }
  if (outputReadiness.graph_exports_ready) {
    bits.push("graph exports ready");
  } else if (outputReadiness.csv_ready) {
    bits.push("graph exports pending");
  }
  statusNode.className = classes.join(" ");
  statusNode.textContent = bits.length ? `${message} (${bits.join(" · ")})` : message;
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
      placeholder.textContent = waitingMessage;
    }
  }
  if (!visible) {
    resetFilesView(waitingMessage);
    resetReproducibility("Reproducibility snippets will be available after final report writing.");
  }
  renderResultsExplorerStatus(visible ? outputReadiness : null);
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
    const bundleMode = String($("bundle-mode")?.value || "full");
    const readinessKey = [
      outputReadiness?.csv_ready ? "csv" : "no-csv",
      outputReadiness?.final_report_ready ? "report" : "no-report",
      outputReadiness?.graph_exports_ready ? "graphs" : "no-graphs",
      outputReadiness?.partial ? "partial" : "complete",
    ].join(":");
    const desiredFilesKey = `${job.job_id}:${bundleMode}:${job.status}:${job.run_dir || ""}:${readinessKey}`;
    if (state.loadedFilesKey !== desiredFilesKey) {
      await fetchFiles(job.job_id);
      state.loadedFilesKey = desiredFilesKey;
    }
  }
}

export async function pollJob(jobId) {
  const payload = await fetchJobData(jobId);
  const job = payload.job;
  state.currentJob = job;
  const runReport = payload.run_report;
  const preflightReport = payload.preflight_report;
  const executionState = payload.execution_state;
  const outputReadiness = payload.output_readiness;
  const runtimeProgress = payload.runtime_progress;
  const reproducibility = payload.reproducibility;
  state.stage = job.stage || state.stage;
  state.executionState = executionState || null;
  state.outputReadiness = outputReadiness || null;
  state.runtimeProgress = runtimeProgress || null;
  if (job.stage === "planned" && state.activeStep < 3) {
    setActiveStep(2, { scroll: false });
  }
  if (
    job.status === "running" &&
    (job.stage === "planned" || job.stage === "executed") &&
    state.activeStep < 3
  ) {
    setActiveStep(3, { scroll: false });
  }

  updateToolEligibilityView(preflightReport);
  renderAndreaExecutionProgress(executionState, job);
  renderRuntimeProgress(runtimeProgress);
  pushRuntimeFailureToasts(runtimeProgress, state.notifiedFailures);
  renderExecutionAlerts(job, runReport, executionState);
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
