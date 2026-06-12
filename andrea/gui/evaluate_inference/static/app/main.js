import {
  initReproducibility,
  renderReproducibility,
  resetReproducibility,
} from "/static-common/app/repro/view.js";
import {
  initBundleDownloadModal,
  openBundleDownloadModal,
} from "/static-common/app/bundles/modal.js?v=20260612a";
import {
  resetUploadProgress,
  uploadFormDataWithProgress,
} from "/static-common/app/uploads/progress.js?v=20260521a";

const state = {
  jobId: null,
  pollTimer: null,
  handoffProgressPercent: 0
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

function fileSizeLabel(input) {
  const file = input.files && input.files[0];
  if (!file) return "";
  if (file.size >= 1024 * 1024) return `${(file.size / (1024 * 1024)).toFixed(1)} MB`;
  if (file.size >= 1024) return `${(file.size / 1024).toFixed(1)} KB`;
  return `${file.size} B`;
}

function updateFileLabels() {
  const inferenceInput = $("#inference-zip");
  const truthInput = $("#truth-zip");
  $("#inference-file-name").textContent = fileName(inferenceInput);
  $("#truth-file-name").textContent = fileName(truthInput);
  $("#inference-file-meta").textContent = fileSizeLabel(inferenceInput) || "Must contain run_report.json and merged_network_raw.csv at ZIP root.";
  $("#truth-file-meta").textContent = fileSizeLabel(truthInput) || "Must contain ground-truth-manifest.json and truth files at ZIP root.";
  inferenceInput.closest(".file-card").classList.toggle("has-file", Boolean(inferenceInput.files?.[0]));
  truthInput.closest(".file-card").classList.toggle("has-file", Boolean(truthInput.files?.[0]));
}

function uploadProgressItems() {
  return [
    { file: $("#inference-zip").files?.[0] || null },
    { file: $("#truth-zip").files?.[0] || null },
  ];
}

function overallUploadProgressItem() {
  return {
    row: $("#overall-upload-progress"),
    status: $("#overall-upload-status"),
    fill: $("#overall-upload-fill"),
    percent: $("#overall-upload-percent"),
    idleLabel: "Waiting",
  };
}

function resetUploadProgressPanel() {
  state.handoffProgressPercent = 0;
  resetUploadProgress([overallUploadProgressItem()]);
  setHidden("#upload-progress-panel", true);
}

function setOverallHandoffProgress({ stateClass, label, percent }) {
  const item = overallUploadProgressItem();
  const safePercent = Math.max(0, Math.min(100, Number(percent) || 0));
  state.handoffProgressPercent = safePercent;
  item.row?.classList.remove("uploading", "uploaded", "validating", "failed");
  if (stateClass) {
    item.row?.classList.add(stateClass);
  }
  if (item.status) {
    item.status.textContent = label;
  }
  if (item.fill) {
    item.fill.style.width = `${safePercent}%`;
  }
  if (item.percent) {
    item.percent.textContent = `${Math.round(safePercent)}%`;
  }
}

function updateHandoffJobProgress(job) {
  const stage = String(job.stage || job.status || "");
  const label = job.progress_label || stage.replace(/_/g, " ") || "Processing";
  const percent = Number.isFinite(Number(job.progress_percent))
    ? Number(job.progress_percent)
    : Math.max(state.handoffProgressPercent, job.status === "queued" ? 10 : 50);
  const detail = job.progress_detail || "";
  setHidden("#upload-progress-panel", false);
  setOverallHandoffProgress({
    stateClass: "validating",
    label: detail ? `${label}: ${detail}` : label,
    percent: Math.max(state.handoffProgressPercent, percent),
  });
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
  setHidden("#download-link", false);
  setHidden("#result-panel", false);
}

function renderJob(payload) {
  const job = payload.job;
  state.jobId = job.job_id;
  setStatus(job.status, job.stage || job.status);
  setHidden("#error-panel", true);
  setHidden("#result-panel", true);
  setHidden("#download-link", true);
  resetReproducibility();

  if (job.status === "failed") {
    stopPolling();
    setHidden("#upload-progress-panel", false);
    setOverallHandoffProgress({
      stateClass: "failed",
      label: "Failed",
      percent: 100,
    });
    renderError(job);
    return;
  }
  if (job.status === "completed") {
    stopPolling();
    setHidden("#upload-progress-panel", false);
    setOverallHandoffProgress({
      stateClass: "uploaded",
      label: "Ready",
      percent: 100,
    });
    renderReport(payload.evaluation_report, job, payload.reproducibility);
    return;
  }
  if (job.status === "queued" || job.status === "running") {
    updateHandoffJobProgress(job);
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
  setHidden("#error-panel", true);
  setHidden("#result-panel", true);
  setHidden("#download-link", true);
  resetReproducibility();
  $("#evaluation-view").innerHTML = "";
  $("#raw-report").textContent = "";
  $("#run-button").disabled = true;
  setStatus("running", "uploading");
  const items = uploadProgressItems();
  const overallItem = overallUploadProgressItem();
  state.handoffProgressPercent = 0;
  resetUploadProgress([overallItem]);
  setHidden("#upload-progress-panel", false);
  try {
    const form = new FormData($("#upload-form"));
    const payload = await uploadFormDataWithProgress({
      url: "/api/evaluate-inference/run",
      formData: form,
      fileItems: items,
      overallItem,
      overallCompleteOnLoad: false,
      onServerProcessing: () => setStatus("running", "validating"),
    });
    setOverallHandoffProgress({
      stateClass: "validating",
      label: "Starting evaluation",
      percent: Math.max(state.handoffProgressPercent, 55),
    });
    renderJob(payload);
  } catch (error) {
    setStatus("failed", "failed");
    setOverallHandoffProgress({
      stateClass: "failed",
      label: "Failed",
      percent: 100,
    });
    $("#error-text").textContent = String(error.message || error);
    setHidden("#error-panel", false);
  } finally {
    $("#run-button").disabled = false;
  }
}

function showClientError(message) {
  $("#error-text").textContent = message;
  setHidden("#error-panel", false);
}

function openDownloadBundles() {
  if (!state.jobId) {
    showClientError("Submit and complete an evaluation job before downloading bundles.");
    return;
  }
  openBundleDownloadModal({
    title: "Download Evaluation ZIP",
    metadataUrl: `/api/evaluate-inference/jobs/${state.jobId}/bundles`,
    downloadUrlForBundle: (bundleId) => (
      `/api/evaluate-inference/jobs/${state.jobId}/bundle?bundle_id=${encodeURIComponent(bundleId)}`
    ),
  }).catch((error) => {
    showClientError(String(error.message || error));
  });
}

$("#inference-zip").addEventListener("change", updateFileLabels);
$("#truth-zip").addEventListener("change", updateFileLabels);
$("#upload-form").addEventListener("submit", submitUploads);
$("#download-link").addEventListener("click", openDownloadBundles);
initReproducibility();
initBundleDownloadModal();
updateFileLabels();
resetUploadProgressPanel();
