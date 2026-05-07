import {
  initReproducibility,
  renderReproducibility,
  resetReproducibility,
} from "/static-common/app/repro/view.js";

const state = {
  jobId: null,
  pollTimer: null
};

const $ = (selector) => document.querySelector(selector);

function setHidden(selector, hidden) {
  $(selector).classList.toggle("hidden", hidden);
}

function setStatus(status, label = null) {
  const pill = $("#status-pill");
  pill.className = `status-pill ${status || "idle"}`;
  pill.textContent = label || status || "Idle";
}

function fileName(input) {
  return input.files && input.files[0] ? input.files[0].name : "No file selected";
}

function updateFileLabels() {
  $("#inference-file-name").textContent = fileName($("#inference-zip"));
  $("#truth-file-name").textContent = fileName($("#truth-zip"));
}

function stopPolling() {
  if (state.pollTimer !== null) {
    clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
}

function startPolling(jobId) {
  stopPolling();
  state.pollTimer = setInterval(() => refreshJob(jobId), 1000);
}

function candidateText(candidate) {
  const bits = [
    candidate.dataset_id ? `dataset=${candidate.dataset_id}` : null,
    candidate.run_id ? `run=${candidate.run_id}` : null,
    candidate.simulator_id ? `simulator=${candidate.simulator_id}` : null,
    candidate.profile ? `profile=${candidate.profile}` : null
  ].filter(Boolean);
  return bits.length ? `${candidate.label} (${bits.join(", ")})` : candidate.label;
}

function fillSelect(select, candidates) {
  select.innerHTML = "";
  for (const candidate of candidates) {
    const option = document.createElement("option");
    option.value = candidate.path;
    option.textContent = candidateText(candidate);
    select.appendChild(option);
  }
}

function renderSelection(job) {
  fillSelect($("#run-report-select"), job.run_candidates || []);
  fillSelect($("#truth-manifest-select"), job.truth_candidates || []);
  setHidden("#selection-panel", false);
}

function renderError(job) {
  $("#error-text").textContent = [job.error, job.traceback].filter(Boolean).join("\n\n");
  setHidden("#error-panel", false);
}

function renderReport(report, job, reproducibility) {
  if (!report || !window.AndreaEvaluationView) return;
  $("#raw-report").textContent = JSON.stringify(report, null, 2);
  const evaluated = (report.metrics || []).filter((row) => row.status === "ok" || row.status === "partial").length;
  const total = (report.metrics || []).length;
  $("#result-summary").textContent = `${evaluated} of ${total} metric rows evaluated`;
  window.AndreaEvaluationView.render($("#evaluation-view"), report);
  renderReproducibility(reproducibility);
  $("#download-link").href = `/api/evaluate-inference/jobs/${job.job_id}/bundle`;
  setHidden("#download-link", false);
  setHidden("#result-panel", false);
}

function renderJob(payload) {
  const job = payload.job;
  state.jobId = job.job_id;
  setStatus(job.status, job.stage || job.status);
  setHidden("#selection-panel", true);
  setHidden("#error-panel", true);
  setHidden("#result-panel", true);
  setHidden("#download-link", true);
  resetReproducibility();

  if (job.status === "needs_selection") {
    stopPolling();
    renderSelection(job);
    return;
  }
  if (job.status === "failed") {
    stopPolling();
    renderError(job);
    return;
  }
  if (job.status === "completed") {
    stopPolling();
    renderReport(payload.evaluation_report, job, payload.reproducibility);
    return;
  }
  if (job.status === "queued" || job.status === "running") {
    startPolling(job.job_id);
  }
}

async function refreshJob(jobId) {
  const response = await fetch(`/api/evaluate-inference/jobs/${jobId}`, {
    headers: { Accept: "application/json" }
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.detail || "Unable to refresh job");
  }
  renderJob(payload);
}

async function submitUploads(event) {
  event.preventDefault();
  stopPolling();
  setHidden("#selection-panel", true);
  setHidden("#error-panel", true);
  setHidden("#result-panel", true);
  setHidden("#download-link", true);
  resetReproducibility();
  $("#evaluation-view").innerHTML = "";
  $("#raw-report").textContent = "";
  $("#run-button").disabled = true;
  setStatus("running", "uploading");
  try {
    const form = new FormData($("#upload-form"));
    const response = await fetch("/api/evaluate-inference/run", {
      method: "POST",
      body: form
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || "Evaluation failed");
    }
    renderJob(payload);
  } catch (error) {
    setStatus("failed", "failed");
    $("#error-text").textContent = String(error.message || error);
    setHidden("#error-panel", false);
  } finally {
    $("#run-button").disabled = false;
  }
}

async function submitSelection() {
  if (!state.jobId) return;
  $("#selection-run-button").disabled = true;
  setStatus("running", "queued");
  try {
    const response = await fetch(`/api/evaluate-inference/jobs/${state.jobId}/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({
        run_report: $("#run-report-select").value,
        ground_truth_manifest: $("#truth-manifest-select").value
      })
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || "Evaluation failed");
    }
    renderJob(payload);
  } catch (error) {
    setStatus("failed", "failed");
    $("#error-text").textContent = String(error.message || error);
    setHidden("#error-panel", false);
  } finally {
    $("#selection-run-button").disabled = false;
  }
}

$("#inference-zip").addEventListener("change", updateFileLabels);
$("#truth-zip").addEventListener("change", updateFileLabels);
$("#upload-form").addEventListener("submit", submitUploads);
$("#selection-run-button").addEventListener("click", submitSelection);
initReproducibility();
updateFileLabels();
