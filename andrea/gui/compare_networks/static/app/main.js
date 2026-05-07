import {
  initReproducibility,
  renderReproducibility,
  resetReproducibility,
} from "/static-common/app/repro/view.js";

const state = {
  jobId: null,
  pollTimer: null,
  sourceCount: 0,
  activeSourceId: null,
  selectedNetworks: []
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

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
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

function addSourceCard() {
  const idx = state.sourceCount;
  state.sourceCount += 1;
  const card = document.createElement("article");
  card.className = "source-upload-card";
  card.dataset.sourceIndex = String(idx);
  card.innerHTML = `
    <div class="source-upload-head">
      <div class="source-upload-title">
        <input class="source-label-input" name="source_${idx}_label" value="Source ${idx + 1}" aria-label="Source label">
        <span class="muted">Upload one inference package and, optionally, its evaluation package.</span>
      </div>
      <button class="icon-button danger" type="button" data-role="remove-source" aria-label="Remove source">&times;</button>
    </div>
    <div class="file-grid">
      <label class="file-card required" for="source-${idx}-inference">
        <span class="file-card-head">
          <span class="file-title">Inference output ZIP</span>
          <span class="requirement-pill required">Required</span>
        </span>
        <span class="file-name" data-role="inference-file-name">No file selected</span>
        <span class="file-meta" data-role="inference-file-meta">Choose a .zip produced by infer-network.</span>
        <span class="file-button">Choose ZIP</span>
        <input id="source-${idx}-inference" data-role="inference-file" type="file" accept=".zip" required />
      </label>
      <label class="file-card optional" for="source-${idx}-evaluation">
        <span class="file-card-head">
          <span class="file-title">Evaluation output ZIP</span>
          <span class="requirement-pill optional">Optional</span>
        </span>
        <span class="file-name" data-role="evaluation-file-name">No file selected</span>
        <span class="file-meta" data-role="evaluation-file-meta">Adds metric coloring and rank-overlap distance.</span>
        <span class="file-button secondary">Choose ZIP</span>
        <input id="source-${idx}-evaluation" data-role="evaluation-file" type="file" accept=".zip" />
      </label>
    </div>
  `;
  card.querySelector('[data-role="remove-source"]').addEventListener("click", () => {
    if ($("#source-upload-list").children.length <= 1) return;
    card.remove();
  });
  for (const input of card.querySelectorAll('input[type="file"]')) {
    input.addEventListener("change", () => updateSourceFileLabels(card));
  }
  $("#source-upload-list").appendChild(card);
  updateSourceFileLabels(card);
}

function updateSourceFileLabels(card) {
  const inferenceInput = card.querySelector('[data-role="inference-file"]');
  const evaluationInput = card.querySelector('[data-role="evaluation-file"]');
  card.querySelector('[data-role="inference-file-name"]').textContent = fileName(inferenceInput);
  card.querySelector('[data-role="evaluation-file-name"]').textContent = fileName(evaluationInput);
  card.querySelector('[data-role="inference-file-meta"]').textContent = fileSizeLabel(inferenceInput) || "Choose a .zip produced by infer-network.";
  card.querySelector('[data-role="evaluation-file-meta"]').textContent = fileSizeLabel(evaluationInput) || "Adds metric coloring and rank-overlap distance.";
  inferenceInput.closest(".file-card").classList.toggle("has-file", Boolean(inferenceInput.files?.[0]));
  evaluationInput.closest(".file-card").classList.toggle("has-file", Boolean(evaluationInput.files?.[0]));
}

function candidateText(candidate) {
  const bits = [
    candidate.dataset_id ? `dataset=${candidate.dataset_id}` : null,
    candidate.run_id ? `run=${candidate.run_id}` : null,
    candidate.inference_run_id ? `run=${candidate.inference_run_id}` : null,
    candidate.inference_dataset_id ? `dataset=${candidate.inference_dataset_id}` : null,
    Number.isFinite(Number(candidate.metrics)) ? `metrics=${candidate.metrics}` : null
  ].filter(Boolean);
  return bits.length ? `${candidate.label} (${bits.join(", ")})` : candidate.label;
}

function fillSelect(select, candidates, includeEmpty = false) {
  select.innerHTML = "";
  if (includeEmpty) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "No evaluation report";
    select.appendChild(option);
  }
  for (const candidate of candidates) {
    const option = document.createElement("option");
    option.value = candidate.path;
    option.textContent = candidateText(candidate);
    select.appendChild(option);
  }
}

function renderSelection(job) {
  const target = $("#selection-list");
  target.innerHTML = "";
  for (const source of job.sources || []) {
    const card = document.createElement("article");
    card.className = "selection-card";
    card.dataset.sourceId = source.source_id;
    card.innerHTML = `
      <div class="section-head">
        <div>
          <h3>${escapeHtml(source.label)}</h3>
          <span class="muted">${escapeHtml(source.source_id)}</span>
        </div>
      </div>
      <div class="selection-grid">
        <label>
          <span>Run report</span>
          <select data-role="run-report-select"></select>
        </label>
        <label>
          <span>Evaluation report</span>
          <select data-role="evaluation-report-select"></select>
        </label>
      </div>
    `;
    fillSelect(card.querySelector('[data-role="run-report-select"]'), source.run_candidates || []);
    fillSelect(
      card.querySelector('[data-role="evaluation-report-select"]'),
      source.evaluation_candidates || [],
      true
    );
    target.appendChild(card);
  }
  setHidden("#selection-panel", false);
}

function renderError(job) {
  $("#error-text").textContent = [job.error, job.traceback].filter(Boolean).join("\n\n");
  setHidden("#error-panel", false);
}

function uniqueNetworkRows(report) {
  const seen = new Set();
  const rows = [];
  for (const row of report.network_index || []) {
    const key = `${row.source_id}\u0000${row.tool_id}\u0000${row.context}`;
    if (seen.has(key)) continue;
    seen.add(key);
    rows.push({
      source_id: row.source_id,
      tool_id: row.tool_id,
      catalog_tool_id: row.catalog_tool_id,
      context: row.context,
      key
    });
  }
  return rows;
}

function sourceDisplayName(report, sourceId) {
  const source = (report.sources || []).find((item) => item.source_id === sourceId);
  return source?.label || sourceId;
}

function contextLabel(context) {
  const text = String(context || "");
  return text.startsWith("group:") ? text.slice(6) : text;
}

function syncDistanceMapSource(sourceId) {
  state.activeSourceId = sourceId;
  if (window.AndreaComparisonView?.updateSource) {
    window.AndreaComparisonView.updateSource($("#distance-map-view"), sourceId);
  }
}

function renderDistanceSourceCards(report) {
  const target = $("#distance-source-cards");
  const sources = report.sources || [];
  const networks = uniqueNetworkRows(report);
  target.innerHTML = "";
  if (!state.activeSourceId && sources.length) {
    state.activeSourceId = sources[0].source_id;
  }
  for (const source of sources) {
    const sourceNetworks = networks.filter((row) => row.source_id === source.source_id);
    const contexts = [...new Set(sourceNetworks.map((row) => row.context))].sort();
    const card = document.createElement("article");
    card.className = `source-card source-map-card ${state.activeSourceId === source.source_id ? "selected" : ""}`;
    card.dataset.sourceId = source.source_id;
    card.innerHTML = `
      <div class="source-card-head">
        <div>
          <label>
            <input class="source-map-radio" type="radio" name="distance-source" data-source-id="${escapeHtml(source.source_id)}" ${state.activeSourceId === source.source_id ? "checked" : ""}>
            <span>${escapeHtml(sourceDisplayName(report, source.source_id))}</span>
          </label>
          <div class="muted">${escapeHtml(source.run_id || source.source_id)} · ${sourceNetworks.length} runs · ${contexts.length} contexts</div>
        </div>
        <span class="status-pill ${source.evaluation_report ? "completed" : "idle"}">${source.evaluation_report ? "evaluation" : "no evaluation"}</span>
      </div>
      <div class="context-tags">
        ${contexts.map((context) => `<span>${escapeHtml(contextLabel(context))}</span>`).join("")}
      </div>
    `;
    target.appendChild(card);
  }

  for (const radio of target.querySelectorAll(".source-map-radio")) {
    radio.addEventListener("change", () => {
      if (!radio.checked) return;
      for (const card of target.querySelectorAll(".source-map-card")) {
        card.classList.toggle("selected", card.dataset.sourceId === radio.dataset.sourceId);
      }
      $("#distance-source-status").textContent = sourceDisplayName(report, radio.dataset.sourceId);
      syncDistanceMapSource(radio.dataset.sourceId);
    });
  }
  $("#distance-source-status").textContent = state.activeSourceId ? sourceDisplayName(report, state.activeSourceId) : "";
  setHidden("#distance-source-cards-panel", sources.length === 0);
}

function renderEdgeSourceCards(report) {
  const target = $("#edge-source-cards");
  const sources = report.sources || [];
  const networks = uniqueNetworkRows(report);
  target.innerHTML = "";
  state.selectedNetworks = [];
  for (const source of sources) {
    const sourceNetworks = networks.filter((row) => row.source_id === source.source_id);
    const contexts = [...new Set(sourceNetworks.map((row) => row.context))].sort();
    const card = document.createElement("article");
    card.className = "source-card";
    card.dataset.sourceId = source.source_id;
    card.innerHTML = `
      <div class="source-card-head">
        <div>
          <h3>${escapeHtml(sourceDisplayName(report, source.source_id))}</h3>
          <div class="muted">${escapeHtml(source.run_id || source.source_id)} · ${sourceNetworks.length} runs</div>
        </div>
        <span class="status-pill ${source.evaluation_report ? "completed" : "idle"}">${source.evaluation_report ? "evaluation" : "no evaluation"}</span>
      </div>
      ${contexts.map((context) => `
        <div class="context-group" data-context="${escapeHtml(context)}">
          <h3>${escapeHtml(contextLabel(context))}</h3>
          <div class="network-list">
            ${sourceNetworks.filter((row) => row.context === context).map((network) => `
              <label class="network-chip">
                <input class="network-select-checkbox" type="checkbox"
                  data-selection-key="${escapeHtml(network.key)}"
                  data-source-id="${escapeHtml(network.source_id)}"
                  data-tool-id="${escapeHtml(network.tool_id)}"
                  data-context="${escapeHtml(network.context)}">
                <span class="selection-index hidden" data-role="selection-index"></span>
                <span class="chip-label">${escapeHtml(network.tool_id)}</span>
              </label>
            `).join("")}
          </div>
        </div>
      `).join("")}
    `;
    target.appendChild(card);
  }

  for (const checkbox of target.querySelectorAll(".network-select-checkbox")) {
    checkbox.addEventListener("change", () => toggleNetworkSelection(checkbox));
  }
  setHidden("#edge-source-cards-panel", sources.length === 0);
  updateNetworkSelectionUi();
}

function toggleNetworkSelection(checkbox) {
  const item = {
    key: checkbox.dataset.selectionKey,
    source_id: checkbox.dataset.sourceId,
    tool_id: checkbox.dataset.toolId,
    context: checkbox.dataset.context
  };
  if (checkbox.checked) {
    state.selectedNetworks.push(item);
  } else {
    state.selectedNetworks = state.selectedNetworks.filter((selected) => selected.key !== item.key);
  }
  updateNetworkSelectionUi();
}

function updateNetworkSelectionUi() {
  const byKey = new Map(state.selectedNetworks.map((item, idx) => [item.key, idx + 1]));
  for (const checkbox of document.querySelectorAll(".network-select-checkbox")) {
    const index = byKey.get(checkbox.dataset.selectionKey);
    const chip = checkbox.closest(".network-chip");
    const badge = chip.querySelector('[data-role="selection-index"]');
    checkbox.checked = Boolean(index);
    chip.classList.toggle("selected", Boolean(index));
    badge.classList.toggle("hidden", !index);
    badge.textContent = index ? String(index) : "";
  }
  if (!state.selectedNetworks.length) {
    $("#tool-selection-status").textContent = "No ordered tools selected.";
  } else {
    $("#tool-selection-status").innerHTML = `
      <span class="selection-summary-count">${state.selectedNetworks.length} selected</span>
      ${state.selectedNetworks.map((item, idx) => `
        <span class="selection-summary-chip">
          <span>${idx + 1}</span>
          ${escapeHtml(item.source_id)}:${escapeHtml(item.tool_id)} · ${escapeHtml(contextLabel(item.context))}
        </span>
      `).join("")}
    `;
  }
  if (window.AndreaComparisonView?.updateSelectedNetworks) {
    window.AndreaComparisonView.updateSelectedNetworks($("#edge-difference-view"), state.selectedNetworks);
  }
}

function setActiveResultTab(tabId) {
  for (const button of document.querySelectorAll(".tab-button")) {
    button.classList.toggle("active", button.dataset.tabTarget === tabId);
  }
  for (const panel of document.querySelectorAll(".tab-panel")) {
    panel.classList.toggle("hidden", panel.id !== tabId);
  }
}

function renderReport(report, job, reproducibility) {
  if (!report || !window.AndreaComparisonView) return;
  $("#raw-report").textContent = JSON.stringify(report, null, 2);
  const summary = report.summary || {};
  $("#result-summary").textContent = `${summary.network_instances || 0} network instances, ${summary.distance_rows || 0} distance rows`;
  const sources = report.sources || [];
  state.activeSourceId = state.activeSourceId || sources[0]?.source_id || null;
  state.selectedNetworks = [];
  window.AndreaComparisonView.renderDistanceMaps($("#distance-map-view"), report, {
    sourceId: state.activeSourceId,
    showSourceSelect: false,
    showSummary: false,
    showDistancesTable: false
  });
  window.AndreaComparisonView.renderEdgeDifferenceView($("#edge-difference-view"), report, {
    selectedNetworks: state.selectedNetworks
  });
  renderDistanceSourceCards(report);
  renderEdgeSourceCards(report);
  renderReproducibility(reproducibility);
  $("#download-link").href = `/api/compare-networks/jobs/${job.job_id}/bundle`;
  setHidden("#download-link", false);
  setHidden("#result-panel", false);
  setActiveResultTab("distance-tab");
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
    renderReport(payload.comparison_report, job, payload.reproducibility);
    return;
  }
  if (job.status === "queued" || job.status === "running") {
    startPolling(job.job_id);
  }
}

async function refreshJob(jobId) {
  const response = await fetch(`/api/compare-networks/jobs/${jobId}`, {
    headers: { Accept: "application/json" }
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.detail || "Unable to refresh job");
  }
  renderJob(payload);
}

function buildUploadFormData() {
  const cards = [...$("#source-upload-list").querySelectorAll(".source-upload-card")];
  const form = new FormData();
  form.append("output_dir", $("#output-dir").value.trim() || "./comparisons");
  form.append("source_count", String(cards.length));
  cards.forEach((card, idx) => {
    form.append(`source_${idx}_label`, card.querySelector(".source-label-input").value);
    const inferenceFile = card.querySelector('[data-role="inference-file"]').files?.[0];
    const evaluationFile = card.querySelector('[data-role="evaluation-file"]').files?.[0];
    if (inferenceFile) form.append(`source_${idx}_inference_zip`, inferenceFile);
    if (evaluationFile) form.append(`source_${idx}_evaluation_zip`, evaluationFile);
  });
  return form;
}

async function submitUploads(event) {
  event.preventDefault();
  stopPolling();
  setHidden("#selection-panel", true);
  setHidden("#error-panel", true);
  setHidden("#result-panel", true);
  setHidden("#download-link", true);
  resetReproducibility();
  state.activeSourceId = null;
  state.selectedNetworks = [];
  $("#distance-map-view").innerHTML = "";
  $("#edge-difference-view").innerHTML = "";
  $("#raw-report").textContent = "";
  $("#run-button").disabled = true;
  setStatus("running", "uploading");
  try {
    const response = await fetch("/api/compare-networks/run", {
      method: "POST",
      body: buildUploadFormData()
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || "Comparison failed");
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
  const sources = [...$("#selection-list").querySelectorAll(".selection-card")].map((card) => ({
    source_id: card.dataset.sourceId,
    run_report: card.querySelector('[data-role="run-report-select"]').value,
    evaluation_report: card.querySelector('[data-role="evaluation-report-select"]').value || null
  }));
  try {
    const response = await fetch(`/api/compare-networks/jobs/${state.jobId}/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ sources })
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || "Comparison failed");
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

$("#add-source-button").addEventListener("click", addSourceCard);
$("#upload-form").addEventListener("submit", submitUploads);
$("#selection-run-button").addEventListener("click", submitSelection);
for (const button of document.querySelectorAll(".tab-button")) {
  button.addEventListener("click", () => setActiveResultTab(button.dataset.tabTarget));
}
initReproducibility();
addSourceCard();
