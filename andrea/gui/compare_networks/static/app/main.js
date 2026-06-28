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
import {
  $,
  contextLabel,
  contextFamily,
  escapeHtml,
  formatValue,
  interpolateColor,
  sortContext,
  stableHash,
  textColor,
} from "./shared.js";

const state = {
  jobId: null,
  pollTimer: null,
  sourceCount: 0,
  activeSourceId: null,
  report: null,
  renderedReportPath: null,
  selectedNetworks: [],
  handoffProgressPercent: 0,
  distanceContextFamily: "",
  distanceMetric: "",
  distanceEvaluationMetric: "",
  distanceSpecificQuery: "",
  distanceSelectedContexts: [],
  distanceActiveLevel: "topology",
  edgeBuilder: {},
  edgeActiveLevel: "topology",
  edgeVariabilityLevels: [],
  edgeVariabilityRequestKey: "",
  edgeVariabilityInFlightKey: "",
  edgeVariabilityRequestSeq: 0,
  edgeLimit: 100,
  edgeEvaluationMetric: "",
  edgeVisualColumns: {},
  edgeSelectedEdges: {}
};
const maxDistanceSelectedContexts = 5;
const evaluationMetricDefs = [
  { key: "auroc", label: "AUROC", bounded: true },
  { key: "aupr", label: "AUPR", bounded: true },
  { key: "f1_at_truth_count", label: "F1@truth-count", bounded: true },
  { key: "epr_at_truth_count", label: "EPR@truth-count", bounded: false },
];

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
        <span class="muted">Upload the analysis bundles exported by upstream GUI commands.</span>
      </div>
      <button class="icon-button danger" type="button" data-role="remove-source" aria-label="Remove source">&times;</button>
    </div>
    <div class="file-grid">
      <label class="file-card required" for="source-${idx}-inference">
        <span class="file-card-head">
          <span class="file-title">Infer-network analysis ZIP</span>
          <span class="requirement-pill required">Required</span>
        </span>
        <span class="file-name" data-role="inference-file-name">No file selected</span>
        <span class="file-meta" data-role="inference-file-meta">Required analysis handoff bundle.</span>
        <span class="file-button">Choose ZIP</span>
        <input id="source-${idx}-inference" data-role="inference-file" type="file" accept=".zip" required />
      </label>
      <label class="file-card optional" for="source-${idx}-evaluation">
        <span class="file-card-head">
          <span class="file-title">Evaluate-inference analysis ZIP</span>
          <span class="requirement-pill optional">Optional</span>
        </span>
        <span class="file-name" data-role="evaluation-file-name">No file selected</span>
        <span class="file-meta" data-role="evaluation-file-meta">Optional bundle for metric coloring and rank-overlap distance.</span>
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

function uploadProgressItemsForCards() {
  const items = [];
  for (const card of $("#source-upload-list").querySelectorAll(".source-upload-card")) {
    const inferenceInput = card.querySelector('[data-role="inference-file"]');
    const evaluationInput = card.querySelector('[data-role="evaluation-file"]');
    const inferenceFile = inferenceInput.files?.[0] || null;
    const evaluationFile = evaluationInput.files?.[0] || null;
    if (inferenceFile) {
      items.push({ file: inferenceFile });
    }
    if (evaluationFile) {
      items.push({ file: evaluationFile });
    }
  }
  return items;
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

function resetUploadProgressRows({ hide = true } = {}) {
  state.handoffProgressPercent = 0;
  setHidden("#upload-progress-panel", hide);
  resetUploadProgress([overallUploadProgressItem()]);
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

function updateSourceFileLabels(card) {
  const inferenceInput = card.querySelector('[data-role="inference-file"]');
  const evaluationInput = card.querySelector('[data-role="evaluation-file"]');
  card.querySelector('[data-role="inference-file-name"]').textContent = fileName(inferenceInput);
  card.querySelector('[data-role="evaluation-file-name"]').textContent = fileName(evaluationInput);
  card.querySelector('[data-role="inference-file-meta"]').textContent = fileSizeLabel(inferenceInput) || "Required analysis handoff bundle.";
  card.querySelector('[data-role="evaluation-file-meta"]').textContent = fileSizeLabel(evaluationInput) || "Optional bundle for metric coloring and rank-overlap distance.";
  inferenceInput.closest(".file-card").classList.toggle("has-file", Boolean(inferenceInput.files?.[0]));
  evaluationInput.closest(".file-card").classList.toggle("has-file", Boolean(evaluationInput.files?.[0]));
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

function evaluationSummaryValue(summary) {
  const value = Number(summary?.median);
  return Number.isFinite(value) ? value : null;
}

function evaluationScale(level) {
  const metric = level.evaluation?.metric || "";
  if (!metric) return { min: 0, max: 1, span: 1 };
  if (evaluationMetricIsBounded(metric)) return { min: 0, max: 1, span: 1 };
  const values = [
    ...(level.evaluation?.aggregate || []).map(evaluationSummaryValue),
    ...(level.evaluation?.selected || []).map(evaluationSummaryValue),
    ...(level.selected?.coordinates || []).map((row) => Number(row.metric_value)),
  ].filter((value) => Number.isFinite(value));
  if (!values.length) return { min: 0, max: 1, span: 1 };
  const min = Math.min(0, ...values);
  const max = Math.max(...values);
  return { min, max, span: max - min || 1 };
}

function evaluationRatio(value, scale) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return null;
  return Math.max(0, Math.min(1, (numeric - scale.min) / scale.span));
}

function evaluationColor(value, variant, scale) {
  const ratio = evaluationRatio(value, scale);
  if (ratio === null) return "#94a3b8";
  const palettes = {
    selected: [[236, 253, 245], [15, 118, 110]],
    aggregate: [[239, 246, 255], [3, 105, 161]],
  };
  const [start, end] = palettes[variant] || palettes.aggregate;
  return interpolateColor(start, end, ratio);
}

function evaluationTextColor(value, scale) {
  const ratio = evaluationRatio(value, scale);
  return ratio !== null && ratio >= 0.58 ? "#ffffff" : "#0f172a";
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    headers: {
      Accept: "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.detail || "Request failed");
  }
  return payload;
}

function comparisonMetricOptions(report) {
  const distances = report.distances_available || [];
  return distances.length ? distances : ["weighted_jaccard_distance"];
}

function evaluationMetricOptions(report) {
  const available = new Set(report.metrics_available || []);
  return evaluationMetricDefs.filter((metric) => available.has(metric.key));
}

function evaluationMetricLabel(metricKey) {
  return evaluationMetricDefs.find((metric) => metric.key === metricKey)?.label || metricKey || "Evaluation";
}

function evaluationMetricIsBounded(metricKey) {
  return Boolean(evaluationMetricDefs.find((metric) => metric.key === metricKey)?.bounded);
}

function edgeContextType(context) {
  return contextFamily(context);
}

function edgeContextElementLabel(context) {
  const text = String(context || "");
  const separator = text.indexOf(":");
  return separator > 0 ? text.slice(separator + 1) : text;
}

function sourceAccent(report, sourceId) {
  const palette = ["#d97706", "#e11d48", "#64748b", "#ea580c", "#a16207", "#be185d", "#52525b", "#b91c1c"];
  const sources = report.sources || [];
  const index = Math.max(0, sources.findIndex((source) => source.source_id === sourceId));
  return palette[index % palette.length];
}

function sourceAccentStyle(report, sourceId) {
  return `--source-accent:${sourceAccent(report, sourceId)}`;
}

function sourceSwatchHtml(report, sourceId) {
  return `<span class="source-swatch" style="${sourceAccentStyle(report, sourceId)}"></span>`;
}

function contextVisual(type) {
  const normalized = String(type || "context").trim() || "context";
  const known = {
    global: {
      shape: "circle",
      color: "#2563eb",
      border: "#bfdbfe",
      bg: "#eff6ff",
      text: "#1d4ed8",
    },
    group: {
      shape: "stack",
      color: "#0f766e",
      border: "#99f6e4",
      bg: "#ecfdf5",
      text: "#0f766e",
    },
    column: {
      shape: "grid",
      color: "#7c3aed",
      border: "#ddd6fe",
      bg: "#f5f3ff",
      text: "#6d28d9",
    },
    sample: {
      shape: "square",
      color: "#0891b2",
      border: "#a5f3fc",
      bg: "#ecfeff",
      text: "#0e7490",
    },
    timepoint: {
      shape: "diamond",
      color: "#9333ea",
      border: "#e9d5ff",
      bg: "#faf5ff",
      text: "#7e22ce",
    },
    perturbation: {
      shape: "hex",
      color: "#c2410c",
      border: "#fed7aa",
      bg: "#fff7ed",
      text: "#9a3412",
    },
    other: {
      shape: "triangle",
      color: "#475569",
      border: "#cbd5e1",
      bg: "#f8fafc",
      text: "#334155",
    },
  };
  if (known[normalized]) return known[normalized];
  const hash = stableHash(normalized);
  const coolHues = [205, 225, 245, 265, 285, 175, 190];
  const hue = coolHues[hash % coolHues.length];
  const shapes = ["circle", "square", "diamond", "triangle", "hex"];
  return {
    shape: shapes[hash % shapes.length],
    color: `hsl(${hue}, 64%, 38%)`,
    border: `hsl(${hue}, 72%, 82%)`,
    bg: `hsl(${hue}, 78%, 96%)`,
    text: `hsl(${hue}, 68%, 28%)`,
  };
}

function contextVisualStyle(type) {
  const visual = contextVisual(type);
  return [
    `--context-color:${visual.color}`,
    `--context-border:${visual.border}`,
    `--context-bg:${visual.bg}`,
    `--context-text:${visual.text}`,
  ].join(";");
}

function contextChipHtml(type) {
  const visual = contextVisual(type);
  return `
    <span class="context-pill" style="${contextVisualStyle(type)}">
      <span class="context-shape ${escapeHtml(visual.shape)}"></span>
      ${escapeHtml(type || "context")}
    </span>
  `;
}

function edgeContextTypeOptions(sourceNetworks) {
  const order = {
    global: 0,
    group: 1,
    column: 2,
    sample: 3,
    timepoint: 4,
    perturbation: 5,
    other: 6,
  };
  return [...new Set(sourceNetworks.map((row) => edgeContextType(row.context)))]
    .sort((a, b) => (order[a] ?? 99) - (order[b] ?? 99) || String(a).localeCompare(String(b), undefined, { numeric: true }));
}

function edgeContextsForType(sourceNetworks, type) {
  return [...new Set(
    sourceNetworks
      .filter((row) => edgeContextType(row.context) === type)
      .map((row) => row.context)
  )].sort(sortContext);
}

function edgeConfigurationsForContext(sourceNetworks, context) {
  return sourceNetworks
    .filter((row) => row.context === context)
    .map((row) => row.tool_id)
    .filter((tool, idx, tools) => tools.indexOf(tool) === idx)
    .sort((a, b) => String(a).localeCompare(String(b), undefined, { numeric: true }));
}

function edgeBuilderSelection(report) {
  const networks = uniqueNetworkRows(report);
  const sources = report.sources || [];
  const sourceIds = sources.map((source) => source.source_id)
    .filter((sourceId) => networks.some((row) => row.source_id === sourceId));
  if (!sourceIds.length) return null;
  const builder = state.edgeBuilder || {};
  if (!sourceIds.includes(builder.sourceId)) {
    builder.sourceId = sourceIds[0];
  }
  const sourceNetworks = networks.filter((row) => row.source_id === builder.sourceId);
  const types = edgeContextTypeOptions(sourceNetworks);
  if (!types.includes(builder.contextType)) {
    builder.contextType = types[0] || "";
  }
  const contexts = edgeContextsForType(sourceNetworks, builder.contextType);
  if (!contexts.includes(builder.context)) {
    builder.context = contexts[0] || "";
  }
  const configurations = edgeConfigurationsForContext(sourceNetworks, builder.context);
  if (!configurations.includes(builder.toolId)) {
    builder.toolId = configurations[0] || "";
  }
  const selectedNetwork = sourceNetworks.find((row) => (
    row.context === builder.context && row.tool_id === builder.toolId
  )) || null;
  state.edgeBuilder = builder;
  return {
    builder,
    networks,
    sources,
    sourceNetworks,
    types,
    contexts,
    configurations,
    selectedNetwork,
  };
}

function distanceFamilies(report) {
  const counts = report.context_counts_by_family || {};
  return Object.entries(counts)
    .filter(([_family, count]) => Number(count) > 0)
    .map(([family]) => family)
    .sort((a, b) => {
      const order = {
        global: 0,
        group: 1,
        column: 2,
        sample: 3,
        timepoint: 4,
        perturbation: 5,
        other: 6,
      };
      return (order[a] ?? 10) - (order[b] ?? 10) || a.localeCompare(b);
    });
}

function distanceFamilyLabel(family) {
  if (family === "global") return "global";
  if (family === "group") return "group";
  if (family === "column") return "column";
  if (family === "sample") return "sample";
  if (family === "timepoint") return "timepoint";
  if (family === "perturbation") return "perturbation";
  return family || "context";
}

function distancePairKey(toolA, toolB) {
  return [toolA, toolB].sort().join("\u0000");
}

function distanceCellColor(value, variant = "aggregate") {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "#f8fafc";
  const clamped = Math.max(0, Math.min(1, numeric));
  const start = variant === "selected" ? [236, 253, 245] : [239, 246, 255];
  const end = variant === "selected" ? [15, 118, 110] : [3, 105, 161];
  const rgb = start.map((channel, idx) => Math.round(channel + ((end[idx] - channel) * clamped)));
  return `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})`;
}

function heatmapTitle(item, mode) {
  if (!item) return "";
  if (mode === "selected" && item.median === undefined) {
    return [
      `distance ${formatValue(item.distance)}`,
      item.status ? `status ${item.status}` : null,
      item.warning || null,
    ].filter(Boolean).join(" · ");
  }
  return [
    `median ${formatValue(item.median)}`,
    `Q1-Q3 ${formatValue(item.q1)}-${formatValue(item.q3)}`,
    `min-max ${formatValue(item.min)}-${formatValue(item.max)}`,
    `n ${item.n}`,
    `unavailable ${item.unavailable}`,
  ].join(" · ");
}

function metricSummaryTitle(summary, metric, label) {
  if (!summary || !Number(summary.n)) return `${label} ${evaluationMetricLabel(metric)}: n/a`;
  return [
    `${label} ${evaluationMetricLabel(metric)} median ${formatValue(summary.median)}`,
    `Q1-Q3 ${formatValue(summary.q1)}-${formatValue(summary.q3)}`,
    `min-max ${formatValue(summary.min)}-${formatValue(summary.max)}`,
    `n ${summary.n}`,
    `unavailable ${summary.unavailable}`,
  ].join(" · ");
}

function metricChip(summary, variant, metric, scale) {
  if (!metric) return "";
  const label = variant === "selected" ? "sel" : "agg";
  const value = evaluationSummaryValue(summary);
  if (value === null || !Number(summary?.n)) {
    return `<span class="precision-chip missing" title="${escapeHtml(metricSummaryTitle(summary, metric, label))}">${label} n/a</span>`;
  }
  return `
    <span
      class="precision-chip ${variant}"
      style="background:${evaluationColor(value, variant, scale)}; color:${evaluationTextColor(value, scale)}"
      title="${escapeHtml(metricSummaryTitle(summary, metric, label))}"
    >${label} ${formatValue(value)}</span>
  `;
}

function metricSummaryMaps(level) {
  return {
    metric: level.evaluation?.metric || "",
    aggregate: new Map((level.evaluation?.aggregate || []).map((row) => [row.tool_id, row])),
    selected: new Map((level.evaluation?.selected || []).map((row) => [row.tool_id, row])),
  };
}

function renderHeatmapColumnHeader(tool, metricMaps, scale) {
  if (!metricMaps.metric || !metricMaps.selected.size) return escapeHtml(tool);
  return `
    <span class="heatmap-tool-label">
      <span>${escapeHtml(tool)}</span>
      <span class="precision-chip-row">
        ${metricChip(metricMaps.selected.get(tool), "selected", metricMaps.metric, scale)}
      </span>
    </span>
  `;
}

function renderHeatmapRowHeader(tool, metricMaps, scale) {
  if (!metricMaps.metric) return `<span class="heatmap-tool-label">${escapeHtml(tool)}</span>`;
  return `
    <span class="heatmap-tool-label">
      <span>${escapeHtml(tool)}</span>
      <span class="precision-chip-row">
        ${metricChip(metricMaps.aggregate.get(tool), "aggregate", metricMaps.metric, scale)}
      </span>
    </span>
  `;
}

function renderDistanceHeatmap(level) {
  const tools = (level.tools || []).map((tool) => tool.tool_id);
  if (tools.length < 2) return '<div class="empty">No comparable tools for this level.</div>';
  const scale = evaluationScale(level);
  const metricMaps = metricSummaryMaps(level);
  const aggregate = new Map();
  for (const row of level.aggregate?.distances || []) {
    aggregate.set(distancePairKey(row.tool_a, row.tool_b), row);
  }
  const selected = new Map();
  for (const row of level.selected?.distances || []) {
    selected.set(distancePairKey(row.tool_a, row.tool_b), row);
  }
  return `
    <div class="distance-heatmap-wrap">
      <table class="distance-heatmap">
        <thead>
          <tr>
            <th></th>
            ${tools.map((tool) => `<th>${renderHeatmapColumnHeader(tool, metricMaps, scale)}</th>`).join("")}
          </tr>
        </thead>
        <tbody>
          ${tools.map((left, rowIdx) => `
            <tr>
              <th>${renderHeatmapRowHeader(left, metricMaps, scale)}</th>
              ${tools.map((right, colIdx) => {
                if (rowIdx === colIdx) return '<td class="matrix-diagonal">0</td>';
                const key = distancePairKey(left, right);
                if (rowIdx > colIdx) {
                  const item = aggregate.get(key);
                  if (!item || !Number(item.n)) return '<td class="matrix-empty">N/A</td>';
                  const value = Number(item.median);
                  return `
                    <td class="aggregate-cell" style="background:${distanceCellColor(value)}; color:${textColor(value)}" title="${escapeHtml(heatmapTitle(item, "aggregate"))}">
                      <span class="distance-cell-value">${formatValue(value)}</span>
                      <span class="distance-cell-meta">n=${escapeHtml(item.n)}</span>
                    </td>
                  `;
                }
                if (!level.selected) return '<td class="matrix-empty"></td>';
                const item = selected.get(key);
                if (!item || !Number(item.n)) return '<td class="matrix-empty" title="No selected-context comparison">N/A</td>';
                const value = Number(item.median);
                return `
                  <td class="selected-cell" style="background:${distanceCellColor(value, "selected")}; color:${textColor(value)}" title="${escapeHtml(heatmapTitle(item, "selected"))}">
                    <span class="distance-cell-value">${formatValue(value)}</span>
                    <span class="distance-cell-meta">n=${escapeHtml(item.n)}</span>
                  </td>
                `;
              }).join("")}
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function numericPoint(row) {
  const x = Number(row?.x);
  const y = Number(row?.y);
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
  return {
    tool_id: row.tool_id,
    context: row.context || "",
    selection_index: Number(row.selection_index || 0),
    metric_value: row.metric_value,
    x,
    y,
  };
}

function ellipseBoundaryPoints(ellipse, count = 48) {
  const cx = Number(ellipse?.center_x);
  const cy = Number(ellipse?.center_y);
  const rx = Number(ellipse?.rx);
  const ry = Number(ellipse?.ry);
  const angle = (Number(ellipse?.angle_deg) || 0) * Math.PI / 180;
  if (!Number.isFinite(cx) || !Number.isFinite(cy) || !Number.isFinite(rx) || !Number.isFinite(ry)) return [];
  if (rx <= 0 && ry <= 0) return [{ x: cx, y: cy }];
  const cosA = Math.cos(angle);
  const sinA = Math.sin(angle);
  return Array.from({ length: count }, (_item, idx) => {
    const theta = (idx / count) * Math.PI * 2;
    const localX = rx * Math.cos(theta);
    const localY = ry * Math.sin(theta);
    return {
      x: cx + (localX * cosA) - (localY * sinA),
      y: cy + (localX * sinA) + (localY * cosA),
    };
  });
}

function estimateLabelWidth(text) {
  return Math.max(28, String(text || "").length * 7);
}

function rectsOverlap(a, b, pad = 2) {
  return !(
    a.x + a.width + pad < b.x ||
    b.x + b.width + pad < a.x ||
    a.y + a.height + pad < b.y ||
    b.y + b.height + pad < a.y
  );
}

function pointRect(point, radius) {
  return {
    x: point.x - radius,
    y: point.y - radius,
    width: radius * 2,
    height: radius * 2,
  };
}

function labelCandidates(point, text, width, height) {
  const textWidth = estimateLabelWidth(text);
  const textHeight = 14;
  const gap = 12;
  const candidates = [
    { dx: gap, dy: 4, anchor: "start" },
    { dx: -gap, dy: 4, anchor: "end" },
    { dx: 0, dy: -gap, anchor: "middle" },
    { dx: 0, dy: gap + textHeight, anchor: "middle" },
    { dx: gap, dy: -gap, anchor: "start" },
    { dx: -gap, dy: -gap, anchor: "end" },
    { dx: gap, dy: gap + textHeight, anchor: "start" },
    { dx: -gap, dy: gap + textHeight, anchor: "end" },
  ];
  return candidates.map((candidate) => {
    const x = point.x + candidate.dx;
    const y = point.y + candidate.dy;
    const rectX = candidate.anchor === "end" ? x - textWidth : (candidate.anchor === "middle" ? x - (textWidth / 2) : x);
    const rectY = y - textHeight;
    return {
      x,
      y,
      anchor: candidate.anchor,
      rect: {
        x: rectX,
        y: rectY,
        width: textWidth,
        height: textHeight,
      },
    };
  });
}

function placeDistanceLabels(points, selectedPoints, xScale, yScale, width, height) {
  const aggregatePixels = points.map((point) => ({
    tool_id: point.tool_id,
    x: xScale(point.x),
    y: yScale(point.y),
  }));
  const selectedPixels = selectedPoints.map((point) => ({
    x: xScale(point.x),
    y: yScale(point.y),
  }));
  const obstacles = [
    ...aggregatePixels.map((point) => pointRect(point, 12)),
    ...selectedPixels.map((point) => pointRect(point, 8)),
  ];
  const placements = new Map();
  const orderedPoints = [...aggregatePixels].sort((a, b) => String(a.tool_id).localeCompare(String(b.tool_id)));
  for (const point of orderedPoints) {
    const candidates = labelCandidates(point, point.tool_id, width, height);
    let best = candidates[0];
    let bestScore = Infinity;
    for (const candidate of candidates) {
      const overlapCount = obstacles.reduce((count, obstacle) => count + (rectsOverlap(candidate.rect, obstacle) ? 1 : 0), 0);
      const overflow =
        Math.max(0, -candidate.rect.x) +
        Math.max(0, candidate.rect.x + candidate.rect.width - width) +
        Math.max(0, -candidate.rect.y) +
        Math.max(0, candidate.rect.y + candidate.rect.height - height);
      const score = (overlapCount * 1000) + overflow + Math.abs(candidate.x - point.x) + Math.abs(candidate.y - point.y);
      if (score < bestScore) {
        best = candidate;
        bestScore = score;
      }
      if (score === 0) break;
    }
    placements.set(point.tool_id, best);
    obstacles.push(best.rect);
  }
  return placements;
}

function selectedMarkerSvg(point, cx, cy) {
  const shape = Number(point.selection_index || 0) % maxDistanceSelectedContexts;
  const title = [
    `${point.tool_id} · ${point.context ? contextLabel(point.context) : "selected context"}`,
    point.metric_label ? `${point.metric_label}: ${formatValue(point.metric_value)}` : "",
  ].filter(Boolean).join(" · ");
  const fill = point.color || "#0f766e";
  const fillStyle = `style="fill:${fill}"`;
  if (shape === 1) {
    return `<rect class="distance-selected-marker" ${fillStyle} x="${cx - 4}" y="${cy - 4}" width="8" height="8"><title>${escapeHtml(title)}</title></rect>`;
  }
  if (shape === 2) {
    return `<polygon class="distance-selected-marker" ${fillStyle} points="${cx},${cy - 5} ${cx + 5},${cy + 4} ${cx - 5},${cy + 4}"><title>${escapeHtml(title)}</title></polygon>`;
  }
  if (shape === 3) {
    return `<polygon class="distance-selected-marker" ${fillStyle} points="${cx},${cy - 6} ${cx + 6},${cy} ${cx},${cy + 6} ${cx - 6},${cy}"><title>${escapeHtml(title)}</title></polygon>`;
  }
  if (shape === 4) {
    return `
      <g class="distance-selected-cross" style="--selected-fill:${fill}">
        <title>${escapeHtml(title)}</title>
        <line class="cross-outline" x1="${cx - 4.5}" y1="${cy - 4.5}" x2="${cx + 4.5}" y2="${cy + 4.5}"></line>
        <line class="cross-outline" x1="${cx - 4.5}" y1="${cy + 4.5}" x2="${cx + 4.5}" y2="${cy - 4.5}"></line>
        <line class="cross-mark" x1="${cx - 4.5}" y1="${cy - 4.5}" x2="${cx + 4.5}" y2="${cy + 4.5}"></line>
        <line class="cross-mark" x1="${cx - 4.5}" y1="${cy + 4.5}" x2="${cx + 4.5}" y2="${cy - 4.5}"></line>
      </g>
    `;
  }
  return `<circle class="distance-selected-marker" ${fillStyle} cx="${cx}" cy="${cy}" r="4"><title>${escapeHtml(title)}</title></circle>`;
}

function selectedContextShapeLabel(index) {
  return ["circle", "square", "triangle", "diamond", "cross"][Number(index || 0) % maxDistanceSelectedContexts];
}

function renderDistanceMap(level) {
  const aggregatePoints = (level.aggregate?.coordinates || []).map(numericPoint).filter(Boolean);
  const selectedPoints = (level.selected?.coordinates || []).map(numericPoint).filter(Boolean);
  const scale = evaluationScale(level);
  const metricMaps = metricSummaryMaps(level);
  const ellipses = level.aggregate?.ellipses || [];
  const ellipsePoints = ellipses.flatMap((ellipse) => ellipseBoundaryPoints(ellipse));
  const allPoints = [
    ...aggregatePoints,
    ...selectedPoints,
    ...ellipsePoints,
  ];
  if (aggregatePoints.length < 2 || allPoints.length < 2) {
    return '<div class="empty">Distance map is not available for this level.</div>';
  }
  const width = 760;
  const height = 500;
  const pad = 28;
  let minX = Math.min(...allPoints.map((point) => point.x));
  let maxX = Math.max(...allPoints.map((point) => point.x));
  let minY = Math.min(...allPoints.map((point) => point.y));
  let maxY = Math.max(...allPoints.map((point) => point.y));
  if (minX === maxX) { minX -= 1; maxX += 1; }
  if (minY === maxY) { minY -= 1; maxY += 1; }
  const xPad = (maxX - minX) * 0.04;
  const yPad = (maxY - minY) * 0.04;
  minX -= xPad; maxX += xPad; minY -= yPad; maxY += yPad;
  const x = (value) => pad + ((value - minX) / (maxX - minX)) * (width - (pad * 2));
  const y = (value) => height - pad - ((value - minY) / (maxY - minY)) * (height - (pad * 2));
  const aggregateByTool = new Map(aggregatePoints.map((point) => [point.tool_id, point]));
  const labelPlacements = placeDistanceLabels(aggregatePoints, selectedPoints, x, y, width, height);
  const selectedContexts = level.selected?.contexts || [];
  const aggregateMetricValue = (toolId) => evaluationSummaryValue(metricMaps.aggregate.get(toolId));
  const pointTitle = (point, mode) => {
    const lines = [`${point.tool_id} ${mode}`];
    if (metricMaps.metric) {
      const value = mode === "aggregate"
        ? aggregateMetricValue(point.tool_id)
        : Number(point.metric_value);
      lines.push(`${evaluationMetricLabel(metricMaps.metric)}: ${formatValue(value)}`);
    }
    return lines.join(" · ");
  };
  return `
    <svg class="distance-map-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="Aggregated distance map">
      ${ellipses.map((ellipse) => {
        const points = ellipseBoundaryPoints(ellipse);
        if (points.length >= 3) {
          return `<polygon class="distance-ellipse" points="${points.map((point) => `${x(point.x)},${y(point.y)}`).join(" ")}"><title>${escapeHtml(ellipse.tool_id)} anchored contextual variability · ${ellipse.point_count} contexts</title></polygon>`;
        }
        return "";
      }).join("")}
      ${selectedPoints.map((point) => {
        const aggregate = aggregateByTool.get(point.tool_id);
        if (!aggregate) return "";
        return `<line class="distance-selected-link" x1="${x(aggregate.x)}" y1="${y(aggregate.y)}" x2="${x(point.x)}" y2="${y(point.y)}"></line>`;
      }).join("")}
      ${aggregatePoints.map((point) => `
        <g>
          <circle
            class="distance-aggregate-point ${metricMaps.metric && aggregateMetricValue(point.tool_id) === null ? "missing-metric" : ""}"
            style="${metricMaps.metric ? `fill:${evaluationColor(aggregateMetricValue(point.tool_id), "aggregate", scale)}` : ""}"
            cx="${x(point.x)}"
            cy="${y(point.y)}"
            r="7"
          ><title>${escapeHtml(pointTitle(point, "aggregate"))}</title></circle>
          ${(() => {
            const placement = labelPlacements.get(point.tool_id);
            if (!placement) return "";
            return `<text class="distance-map-label" x="${placement.x}" y="${placement.y}" text-anchor="${placement.anchor}">${escapeHtml(point.tool_id)}</text>`;
          })()}
        </g>
      `).join("")}
      ${selectedPoints.map((point) => selectedMarkerSvg(
        {
          ...point,
          color: metricMaps.metric
            ? evaluationColor(Number(point.metric_value), "selected", scale)
            : "#0f766e",
          metric_label: metricMaps.metric ? evaluationMetricLabel(metricMaps.metric) : "",
        },
        x(point.x),
        y(point.y)
      )).join("")}
    </svg>
    ${selectedContexts.length ? `
      <div class="distance-shape-legend">
        ${selectedContexts.map((context, idx) => `
          <span><b>${escapeHtml(selectedContextShapeLabel(idx))}</b> ${escapeHtml(contextLabel(context))}</span>
        `).join("")}
      </div>
    ` : ""}
  `;
}

function renderDistanceMethodSummary() {
  return `
    <section class="distance-method-summary">
      <article>
        <h3>Heatmap</h3>
        <p>The lower triangle shows the aggregated distance, using the median across contexts. The small value below each aggregate cell is the number of contexts included in that median.</p>
        <p>When specific contexts are selected, the upper triangle shows the median distance across only those selected elements. If an evaluation metric is selected, green chips above columns summarize selected-context precision and blue chips in the first column summarize aggregate precision.</p>
      </article>
      <article>
        <h3>Distance map</h3>
        <p>Large points are computed with MDS from the same aggregated distance matrix used by the heatmap.</p>
        <p>Selected context points are computed with anchored MDS in the aggregate coordinate system. Marker shape identifies each selected context; point color follows the selected evaluation metric when available.</p>
      </article>
    </section>
  `;
}

function renderDistanceLevel(level) {
  const title = level.level ? level.level[0].toUpperCase() + level.level.slice(1) : "Level";
  const warnings = (level.warnings || []).filter(Boolean);
  const selectedContexts = level.selected?.contexts || [];
  const selectedLabel = selectedContexts.length === 0
    ? "Aggregate"
    : (selectedContexts.length === 1 ? contextLabel(selectedContexts[0]) : `${selectedContexts.length} selected contexts`);
  return `
    <article class="distance-level-card">
      <div class="level-head">
        <div>
          <h2>${escapeHtml(title)}</h2>
          <div class="subtle">${escapeHtml(selectedLabel)} · aggregate lower triangle${level.selected ? " · selected-context upper triangle" : ""}</div>
        </div>
        <div class="badges">
          <span class="badge">${(level.tools || []).length} tools</span>
          ${warnings.length ? `<span class="badge warning">${warnings.length} warning${warnings.length === 1 ? "" : "s"}</span>` : ""}
        </div>
      </div>
      ${warnings.map((warning) => `<div class="warning-box">${escapeHtml(warning)}</div>`).join("")}
      <div class="distance-level-body">
        <section>
          <div class="panel-title"><h3>Distance Heatmap</h3><span class="subtle">lower aggregate · upper selected contexts</span></div>
          ${renderDistanceHeatmap(level)}
        </section>
        <section>
          <div class="panel-title"><h3>Distance Map</h3><span class="subtle">aggregate points with context variability</span></div>
          ${renderDistanceMap(level)}
        </section>
      </div>
    </article>
  `;
}

function renderDistanceLevelTabs(levels) {
  return `
    <div class="distance-level-tabs" role="tablist" aria-label="Distance levels">
      ${levels.map((level) => {
        const label = level.level ? level.level[0].toUpperCase() + level.level.slice(1) : "Level";
        const active = level.level === state.distanceActiveLevel;
        return `
          <button type="button" class="level-tab ${active ? "active" : ""}" data-distance-level="${escapeHtml(level.level)}" role="tab" aria-selected="${active ? "true" : "false"}">
            ${escapeHtml(label)}
          </button>
        `;
      }).join("")}
    </div>
  `;
}

function updateDistanceLevelPanels(levels) {
  const levelIds = levels.map((level) => level.level);
  if (!levelIds.includes(state.distanceActiveLevel)) {
    state.distanceActiveLevel = levelIds[0] || "topology";
  }
  $("#distance-level-tabs").innerHTML = renderDistanceLevelTabs(levels);
  $("#distance-levels").innerHTML = levels
    .filter((level) => level.level === state.distanceActiveLevel)
    .map(renderDistanceLevel)
    .join("");
  for (const button of document.querySelectorAll("[data-distance-level]")) {
    button.addEventListener("click", () => {
      state.distanceActiveLevel = button.dataset.distanceLevel;
      updateDistanceLevelPanels(levels);
    });
  }
}

function renderSelectedDistanceContextChips() {
  if (!state.distanceSelectedContexts.length) {
    return '<span class="muted">Aggregate view</span>';
  }
  return state.distanceSelectedContexts.map((context, idx) => `
    <span class="distance-selected-chip">
      <b>${escapeHtml(selectedContextShapeLabel(idx))}</b>
      ${escapeHtml(contextLabel(context))}
    </span>
  `).join("");
}

function renderDistanceExplorer(report) {
  if (!report || !state.jobId) return;
  const sources = report.sources || [];
  const metrics = comparisonMetricOptions(report);
  const evaluationMetrics = evaluationMetricOptions(report);
  const families = distanceFamilies(report);
  state.activeSourceId = state.activeSourceId || sources[0]?.source_id || "";
  state.distanceMetric = metrics.includes(state.distanceMetric) ? state.distanceMetric : (metrics[0] || "weighted_jaccard_distance");
  state.distanceEvaluationMetric = evaluationMetrics.some((metric) => metric.key === state.distanceEvaluationMetric)
    ? state.distanceEvaluationMetric
    : (evaluationMetrics[0]?.key || "");
  state.distanceContextFamily = families.includes(state.distanceContextFamily) ? state.distanceContextFamily : (families[0] || "global");
  $("#distance-map-view").innerHTML = `
    <main class="andrea-comparison-view">
      <header class="page distance-page-header">
        <div>
          <h1>Distance Maps</h1>
          <div class="subtle">Each level uses the same source, context family, distance metric and optional context drilldown.</div>
        </div>
      </header>
      ${renderDistanceMethodSummary()}
      <section class="distance-control-bar">
        <div class="distance-primary-controls">
          <label>
            <strong>Source</strong>
            <select id="distance-source-select">
              ${sources.map((source) => `<option value="${escapeHtml(source.source_id)}" ${source.source_id === state.activeSourceId ? "selected" : ""}>${escapeHtml(sourceDisplayName(report, source.source_id))}</option>`).join("")}
            </select>
          </label>
          <label>
            <strong>Context</strong>
            <select id="distance-family-select">
              ${families.map((family) => `<option value="${escapeHtml(family)}" ${family === state.distanceContextFamily ? "selected" : ""}>${escapeHtml(distanceFamilyLabel(family))}</option>`).join("")}
            </select>
          </label>
          <label>
            <strong>Distance</strong>
            <select id="distance-metric-select">
              ${metrics.map((metric) => `<option value="${escapeHtml(metric)}" ${metric === state.distanceMetric ? "selected" : ""}>${escapeHtml(metric)}</option>`).join("")}
            </select>
          </label>
          <label>
            <strong>Evaluation metric</strong>
            <select id="distance-evaluation-metric-select" ${evaluationMetrics.length ? "" : "disabled"}>
              <option value="">No metric overlay</option>
              ${evaluationMetrics.map((metric) => `<option value="${escapeHtml(metric.key)}" ${metric.key === state.distanceEvaluationMetric ? "selected" : ""}>${escapeHtml(metric.label)}</option>`).join("")}
            </select>
          </label>
        </div>
        <div class="distance-specific-control">
          <span class="distance-specific-head">
            <strong>Specific contexts</strong>
            <span id="distance-selected-count">${state.distanceSelectedContexts.length}/${maxDistanceSelectedContexts} selected</span>
          </span>
          <input id="distance-specific-query" type="search" placeholder="Find context" value="${escapeHtml(state.distanceSpecificQuery || "")}" ${state.distanceContextFamily === "global" ? "disabled" : ""}>
          <div id="distance-selected-contexts" class="distance-selected-contexts">${renderSelectedDistanceContextChips()}</div>
          <div id="distance-specific-list" class="distance-specific-list"></div>
        </div>
      </section>
      <section id="distance-view-status" class="empty hidden">Loading distance maps...</section>
      <section id="distance-level-tabs"></section>
      <section class="distance-levels" id="distance-levels"></section>
    </main>
  `;
  $("#distance-source-select")?.addEventListener("change", (event) => {
    state.activeSourceId = event.target.value;
    state.distanceSelectedContexts = [];
    state.distanceSpecificQuery = "";
    refreshDistanceSpecificContexts(report).then(() => refreshDistanceView(report));
  });
  $("#distance-family-select")?.addEventListener("change", (event) => {
    state.distanceContextFamily = event.target.value;
    state.distanceSelectedContexts = [];
    state.distanceSpecificQuery = "";
    renderDistanceExplorer(report);
  });
  $("#distance-metric-select")?.addEventListener("change", (event) => {
    state.distanceMetric = event.target.value;
    refreshDistanceView(report);
  });
  $("#distance-evaluation-metric-select")?.addEventListener("change", (event) => {
    state.distanceEvaluationMetric = event.target.value;
    refreshDistanceView(report);
  });
  $("#distance-specific-query")?.addEventListener("input", (event) => {
    state.distanceSpecificQuery = event.target.value;
    refreshDistanceSpecificContexts(report);
  });
  refreshDistanceSpecificContexts(report).then(() => refreshDistanceView(report));
}

async function refreshDistanceSpecificContexts(report) {
  const list = $("#distance-specific-list");
  const selectedBox = $("#distance-selected-contexts");
  if (!list) return;
  const family = state.distanceContextFamily || "global";
  if (family === "global") {
    state.distanceSelectedContexts = [];
    list.innerHTML = '<div class="distance-context-empty">Global has a single aggregate context.</div>';
    if (selectedBox) selectedBox.innerHTML = renderSelectedDistanceContextChips();
    return;
  }
  const sourceId = state.activeSourceId || report.sources?.[0]?.source_id || "";
  const payload = await fetchJson(
    `/api/compare-networks/jobs/${state.jobId}/contexts?source_id=${encodeURIComponent(sourceId)}&family=${encodeURIComponent(family)}&query=${encodeURIComponent(state.distanceSpecificQuery || "")}&limit=100`
  );
  const contexts = payload.contexts || [];
  const selected = new Set(state.distanceSelectedContexts);
  const atLimit = state.distanceSelectedContexts.length >= maxDistanceSelectedContexts;
  const selectedCount = $("#distance-selected-count");
  if (selectedCount) {
    selectedCount.textContent = `${state.distanceSelectedContexts.length}/${maxDistanceSelectedContexts} selected`;
  }
  list.innerHTML = `
    <button type="button" class="distance-context-clear" data-action="clear-distance-contexts">Aggregate</button>
    ${contexts.length ? contexts.map((item) => {
      const checked = selected.has(item.context);
      const disabled = !checked && atLimit;
      return `
        <label class="distance-context-option ${checked ? "selected" : ""} ${disabled ? "disabled" : ""}">
          <input class="distance-context-checkbox" type="checkbox" value="${escapeHtml(item.context)}" ${checked ? "checked" : ""} ${disabled ? "disabled" : ""}>
          <span>${escapeHtml(contextLabel(item.context))}</span>
          <small>${escapeHtml(item.network_instances)}</small>
        </label>
      `;
    }).join("") : '<div class="distance-context-empty">No contexts match this search.</div>'}
  `;
  if (selectedBox) selectedBox.innerHTML = renderSelectedDistanceContextChips();
  list.querySelector('[data-action="clear-distance-contexts"]')?.addEventListener("click", () => {
    state.distanceSelectedContexts = [];
    refreshDistanceSpecificContexts(report).then(() => refreshDistanceView(report));
  });
  for (const checkbox of list.querySelectorAll(".distance-context-checkbox")) {
    checkbox.addEventListener("change", () => {
      const context = checkbox.value;
      if (checkbox.checked) {
        if (!state.distanceSelectedContexts.includes(context) && state.distanceSelectedContexts.length < maxDistanceSelectedContexts) {
          state.distanceSelectedContexts.push(context);
        }
      } else {
        state.distanceSelectedContexts = state.distanceSelectedContexts.filter((item) => item !== context);
      }
      refreshDistanceSpecificContexts(report).then(() => refreshDistanceView(report));
    });
  }
}

async function refreshDistanceView(report) {
  const sourceId = state.activeSourceId || report.sources?.[0]?.source_id || "";
  const family = state.distanceContextFamily || "global";
  const metric = state.distanceMetric || comparisonMetricOptions(report)[0] || "weighted_jaccard_distance";
  if (!sourceId || !family || !metric) return;
  $("#distance-view-status").textContent = "Loading distance maps...";
  $("#distance-view-status").classList.remove("hidden");
  $("#distance-levels").innerHTML = "";
  const contextParam = state.distanceSelectedContexts.length
    ? `&contexts=${encodeURIComponent(state.distanceSelectedContexts.join(","))}`
    : "";
  const evaluationParam = state.distanceEvaluationMetric
    ? `&evaluation_metric=${encodeURIComponent(state.distanceEvaluationMetric)}`
    : "";
  try {
    const payload = await fetchJson(
      `/api/compare-networks/jobs/${state.jobId}/distance-view?source_id=${encodeURIComponent(sourceId)}&context_family=${encodeURIComponent(family)}&distance_metric=${encodeURIComponent(metric)}${evaluationParam}${contextParam}`
    );
    $("#distance-view-status").textContent = "";
    $("#distance-view-status").classList.add("hidden");
    updateDistanceLevelPanels(payload.levels || []);
  } catch (error) {
    $("#distance-view-status").textContent = String(error.message || error);
    $("#distance-view-status").classList.remove("hidden");
  }
}

function renderEdgeSourceCards(report) {
  const target = $("#edge-source-cards");
  const selection = edgeBuilderSelection(report);
  if (!selection) {
    target.innerHTML = '<div class="empty">No network instances are available.</div>';
    setHidden("#edge-source-cards-panel", false);
    updateNetworkSelectionUi();
    return;
  }
  const {
    builder,
    networks,
    sources,
    sourceNetworks,
    types,
    contexts,
    configurations,
    selectedNetwork,
  } = selection;
  const sourceOptions = sources
    .filter((source) => networks.some((row) => row.source_id === source.source_id));
  const alreadyIndex = selectedNetwork
    ? state.selectedNetworks.findIndex((item) => item.key === selectedNetwork.key)
    : -1;
  const alreadySelected = alreadyIndex >= 0;
  const previewContext = selectedNetwork?.context || builder.context || "";
  const previewType = edgeContextType(previewContext);
  const previewElement = edgeContextElementLabel(previewContext);
  const previewTool = selectedNetwork?.tool_id || builder.toolId || "No configuration";
  const previewSource = sourceDisplayName(report, builder.sourceId);
  const previewState = !selectedNetwork
    ? "No matching instance"
    : (alreadySelected ? `Already in order #${alreadyIndex + 1}` : "Ready to add");
  target.innerHTML = `
    <article class="source-card edge-builder-card">
      <div class="source-card-head">
        <div>
          <h3>Add Network Instance</h3>
          <div class="muted">Choose one concrete source, context and configuration, then append it to the ordered list.</div>
        </div>
        <span class="status-pill idle">${sourceNetworks.length} available</span>
      </div>
      <div class="edge-composer">
        <div class="edge-builder-grid">
          <label>
            <span class="edge-step-label"><b>1</b> Source ${sourceSwatchHtml(report, builder.sourceId)}</span>
            <select id="edge-builder-source">
              ${sourceOptions.map((source) => `<option value="${escapeHtml(source.source_id)}" ${source.source_id === builder.sourceId ? "selected" : ""}>${escapeHtml(sourceDisplayName(report, source.source_id))}</option>`).join("")}
            </select>
          </label>
          <label>
            <span class="edge-step-label"><b>2</b> Context type ${contextChipHtml(builder.contextType)}</span>
            <select id="edge-builder-context-type">
              ${types.map((type) => `<option value="${escapeHtml(type)}" ${type === builder.contextType ? "selected" : ""}>${escapeHtml(type)} (${edgeContextsForType(sourceNetworks, type).length})</option>`).join("")}
            </select>
          </label>
          <label>
            <span class="edge-step-label"><b>3</b> Context element</span>
            <select id="edge-builder-context" ${contexts.length ? "" : "disabled"}>
              ${contexts.map((context) => `<option value="${escapeHtml(context)}" ${context === builder.context ? "selected" : ""}>${escapeHtml(edgeContextElementLabel(context))}</option>`).join("")}
            </select>
          </label>
          <label>
            <span class="edge-step-label"><b>4</b> Configuration</span>
            <select id="edge-builder-tool" ${configurations.length ? "" : "disabled"}>
              ${configurations.map((toolId) => `<option value="${escapeHtml(toolId)}" ${toolId === builder.toolId ? "selected" : ""}>${escapeHtml(toolId)}</option>`).join("")}
            </select>
          </label>
          <div class="edge-builder-meta">
            ${contexts.length} ${escapeHtml(builder.contextType)} context${contexts.length === 1 ? "" : "s"} · ${configurations.length} configuration${configurations.length === 1 ? "" : "s"}
          </div>
        </div>
        <aside class="edge-preview-card ${alreadySelected ? "is-added" : ""}" style="${sourceAccentStyle(report, builder.sourceId)}">
          <span class="edge-preview-eyebrow">Preview</span>
          <div class="edge-preview-title">${sourceSwatchHtml(report, builder.sourceId)} ${escapeHtml(previewSource)}</div>
          <div class="edge-preview-chain">
            ${contextChipHtml(previewType)}
            <span>${escapeHtml(previewElement || "context")}</span>
            <strong>${escapeHtml(previewTool)}</strong>
          </div>
          <div class="edge-preview-status ${alreadySelected ? "added" : ""}">${escapeHtml(previewState)}</div>
          <button id="edge-builder-add" type="button" class="button" ${selectedNetwork && !alreadySelected ? "" : "disabled"}>
            Add
          </button>
        </aside>
      </div>
      <div id="edge-selected-order" class="edge-selected-order"></div>
    </article>
  `;
  $("#edge-builder-source")?.addEventListener("change", (event) => {
    state.edgeBuilder = { sourceId: event.target.value };
    renderEdgeSourceCards(report);
  });
  $("#edge-builder-context-type")?.addEventListener("change", (event) => {
    state.edgeBuilder.contextType = event.target.value;
    state.edgeBuilder.context = "";
    state.edgeBuilder.toolId = "";
    renderEdgeSourceCards(report);
  });
  $("#edge-builder-context")?.addEventListener("change", (event) => {
    state.edgeBuilder.context = event.target.value;
    state.edgeBuilder.toolId = "";
    renderEdgeSourceCards(report);
  });
  $("#edge-builder-tool")?.addEventListener("change", (event) => {
    state.edgeBuilder.toolId = event.target.value;
    renderEdgeSourceCards(report);
  });
  $("#edge-builder-add")?.addEventListener("click", () => {
    if (!selectedNetwork || alreadySelected) return;
    state.selectedNetworks.push({
      key: selectedNetwork.key,
      source_id: selectedNetwork.source_id,
      tool_id: selectedNetwork.tool_id,
      context: selectedNetwork.context,
    });
    renderEdgeSourceCards(report);
  });
  setHidden("#edge-source-cards-panel", false);
  updateNetworkSelectionUi();
}

function updateNetworkSelectionUi() {
  const orderTarget = $("#edge-selected-order");
  if (orderTarget) {
    if (!state.selectedNetworks.length) {
      orderTarget.innerHTML = '<div class="empty">No network instances in the ordered list.</div>';
    } else {
      orderTarget.innerHTML = `
        <div class="edge-order-head">
          <h3>Selected Order</h3>
          <span>${state.selectedNetworks.length} item${state.selectedNetworks.length === 1 ? "" : "s"}</span>
        </div>
        <ol class="edge-order-list">
          ${state.selectedNetworks.map((item, idx) => `
            <li class="edge-order-item" style="${sourceAccentStyle(state.report || {}, item.source_id)}">
              <span class="selection-index">${idx + 1}</span>
              <div class="edge-order-main">
                <div class="edge-order-title">
                  <span>${sourceSwatchHtml(state.report || {}, item.source_id)} ${escapeHtml(sourceDisplayName(state.report || {}, item.source_id))}</span>
                  <strong>${escapeHtml(item.tool_id)}</strong>
                </div>
                <div class="edge-order-meta">
                  ${contextChipHtml(edgeContextType(item.context))}
                  <span>${escapeHtml(edgeContextElementLabel(item.context))}</span>
                </div>
              </div>
              <button type="button" class="icon-button danger" data-remove-edge-index="${idx}" aria-label="Remove ${escapeHtml(item.tool_id)}">x</button>
            </li>
          `).join("")}
        </ol>
      `;
      for (const button of orderTarget.querySelectorAll("[data-remove-edge-index]")) {
        button.addEventListener("click", () => {
          const index = Number(button.dataset.removeEdgeIndex);
          state.selectedNetworks.splice(index, 1);
          if (state.report) renderEdgeSourceCards(state.report);
        });
      }
    }
  }
  refreshEdgeVariability();
}

function renderEdgeExplorer(report) {
  const evalMetrics = evaluationMetricOptions(report);
  if (!state.edgeEvaluationMetric && evalMetrics.length) {
    state.edgeEvaluationMetric = evalMetrics[0].key;
  }
  state.edgeLimit = state.edgeLimit || 100;
  $("#edge-difference-view").innerHTML = `
    <main class="andrea-comparison-view">
      <header class="page">
        <div>
          <h1>Edge Variability</h1>
          <div class="subtle">Top variable interactions are computed server-side from comparison.sqlite.</div>
        </div>
        <div class="toolbar">
          ${evalMetrics.length ? `
            <label>
              <strong>Evaluation metric</strong>
              <select id="edge-evaluation-metric-select">
                <option value="">None</option>
                ${evalMetrics.map((item) => `<option value="${escapeHtml(item.key)}" ${item.key === state.edgeEvaluationMetric ? "selected" : ""}>${escapeHtml(item.label)}</option>`).join("")}
              </select>
            </label>
          ` : ""}
          <label>
            <strong>Top N</strong>
            <select id="edge-limit-select">
              ${[100, 500, 1000].map((item) => `<option value="${item}" ${item === state.edgeLimit ? "selected" : ""}>${item}</option>`).join("")}
            </select>
          </label>
        </div>
      </header>
      <section class="table-card edge-differences-card">
        <div class="table-controls">
          <div>
            <h2>Ordered Edge Differences</h2>
            <div class="subtle">Select ordered tools from the source cards. Only the requested top rows are sent to the browser.</div>
          </div>
        </div>
        <section id="edge-level-tabs"></section>
        <section class="edge-level-grid" id="edge-level-results"></section>
      </section>
    </main>
  `;
  $("#edge-limit-select").addEventListener("change", (event) => {
    state.edgeLimit = Number(event.target.value) || 100;
    refreshEdgeVariability();
  });
  $("#edge-evaluation-metric-select")?.addEventListener("change", (event) => {
    state.edgeEvaluationMetric = event.target.value || "";
    refreshEdgeVariability();
  });
}

function renderEdgeLevelTabs(levels) {
  if (!levels.length) return "";
  return `
    <div class="distance-level-tabs" role="tablist" aria-label="Edge-difference levels">
      ${levels.map((level) => {
        const levelId = level.level || "";
        const label = levelId ? levelId[0].toUpperCase() + levelId.slice(1) : "Level";
        const active = levelId === state.edgeActiveLevel;
        return `
          <button type="button" class="level-tab ${active ? "active" : ""}" data-edge-level="${escapeHtml(levelId)}" role="tab" aria-selected="${active ? "true" : "false"}">
            ${escapeHtml(label)}
          </button>
        `;
      }).join("")}
    </div>
  `;
}

function updateEdgeLevelPanels(levels) {
  state.edgeVariabilityLevels = levels;
  const levelIds = levels.map((level) => level.level);
  if (!levelIds.includes(state.edgeActiveLevel)) {
    state.edgeActiveLevel = levelIds[0] || "topology";
  }
  const tabHost = $("#edge-level-tabs");
  const target = $("#edge-level-results");
  if (!tabHost || !target) return;
  tabHost.innerHTML = renderEdgeLevelTabs(levels);
  target.innerHTML = levels
    .filter((level) => level.level === state.edgeActiveLevel)
    .map(renderEdgeLevelResult)
    .join("");
  bindEdgeLevelInteractions(state.edgeActiveLevel);
  for (const button of document.querySelectorAll("[data-edge-level]")) {
    button.addEventListener("click", () => {
      state.edgeActiveLevel = button.dataset.edgeLevel;
      updateEdgeLevelPanels(state.edgeVariabilityLevels);
    });
  }
}

async function refreshEdgeVariability() {
  if (!state.report || !state.jobId || !$("#edge-level-results")) return;
  const target = $("#edge-level-results");
  if (state.selectedNetworks.length < 2) {
    state.edgeVariabilityRequestKey = "";
    state.edgeVariabilityInFlightKey = "";
    state.edgeVariabilityRequestSeq += 1;
    $("#edge-level-tabs").innerHTML = "";
    state.edgeVariabilityLevels = [];
    target.innerHTML = '<div class="empty">Select two or more ordered tools from the source cards to compare edge scores.</div>';
    return;
  }
  const requestKey = JSON.stringify({
    selected_networks: state.selectedNetworks,
    limit: state.edgeLimit || 100,
    evaluation_metric: state.edgeEvaluationMetric || "",
  });
  if (requestKey === state.edgeVariabilityRequestKey && state.edgeVariabilityLevels.length) {
    updateEdgeLevelPanels(state.edgeVariabilityLevels);
    return;
  }
  if (requestKey === state.edgeVariabilityInFlightKey) {
    return;
  }
  const requestSeq = state.edgeVariabilityRequestSeq + 1;
  state.edgeVariabilityRequestSeq = requestSeq;
  state.edgeVariabilityInFlightKey = requestKey;
  target.innerHTML = '<div class="empty">Computing top variable interactions...</div>';
  try {
    const payload = await fetchJson(`/api/compare-networks/jobs/${state.jobId}/edge-variability`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        selected_networks: state.selectedNetworks,
        limit: state.edgeLimit || 100,
        evaluation_metric: state.edgeEvaluationMetric || null,
      }),
    });
    if (requestSeq !== state.edgeVariabilityRequestSeq || requestKey !== state.edgeVariabilityInFlightKey) {
      return;
    }
    state.edgeVariabilityRequestKey = requestKey;
    state.edgeVisualColumns = {};
    state.edgeSelectedEdges = {};
    updateEdgeLevelPanels(payload.levels || []);
  } catch (error) {
    if (requestSeq !== state.edgeVariabilityRequestSeq) {
      return;
    }
    state.edgeVariabilityRequestKey = "";
    $("#edge-level-tabs").innerHTML = "";
    state.edgeVariabilityLevels = [];
    target.innerHTML = `<div class="empty">${escapeHtml(error.message || error)}</div>`;
  } finally {
    if (requestKey === state.edgeVariabilityInFlightKey) {
      state.edgeVariabilityInFlightKey = "";
    }
  }
}

function renderEdgeLevelResult(levelResult) {
  const level = levelResult.level || "";
  if (levelResult.status !== "ok") {
    return `
      <article class="edge-level-card">
        <div class="level-head"><h3>${escapeHtml(level)}</h3></div>
        <div class="empty">${escapeHtml(levelResult.warning || "No comparable edges.")}</div>
      </article>
    `;
  }
  const rows = levelResult.rows || [];
  return `
    <article class="edge-level-card">
      <div class="level-head">
        <div>
          <h3>${escapeHtml(level[0].toUpperCase() + level.slice(1))}</h3>
          <div class="subtle">${escapeHtml(levelResult.common_genes)} common genes · ${escapeHtml(levelResult.comparable_edges)} comparable edges</div>
        </div>
        <div class="badges">
          <span class="badge">top ${escapeHtml(levelResult.limit)}</span>
          ${levelResult.truncated ? '<span class="badge">truncated</span>' : ""}
        </div>
      </div>
      ${renderEdgeDifferenceBody(level, levelResult.networks || [], rows)}
    </article>
  `;
}

function renderEdgeDifferenceBody(level, networks, rows) {
  if (!rows.length) return '<div class="empty">No comparable edges for the selected ordered tools.</div>';
  const matrix = renderEdgeSparklineMatrix(level, networks, rows);
  const chart = renderEdgeDifferenceChart(level, networks, rows);
  if (!chart) return matrix;
  return `<div class="edge-diff-layout"><div>${matrix}</div>${chart}</div>`;
}

function edgeActiveColumnIndices(level, networkCount) {
  const existing = (state.edgeVisualColumns[level] || [])
    .map((idx) => Number(idx))
    .filter((idx) => Number.isInteger(idx) && idx >= 0 && idx < networkCount);
  if (existing.length >= 2 && existing.length <= 6) {
    return existing;
  }
  const initial = Array.from({ length: Math.min(6, networkCount) }, (_item, idx) => idx);
  state.edgeVisualColumns[level] = initial;
  return initial;
}

function edgeSelectedKeySet(level) {
  return new Set(state.edgeSelectedEdges[level] || []);
}

function edgeRowKey(row) {
  return String(row.edge_key || row.edge_label || "");
}

function edgeValueScale(level, rows, columnIndices = null) {
  const values = rows.flatMap((row) => {
    const rowValues = (row.values || []).map((value) => Number(value) || 0);
    const indices = columnIndices || rowValues.map((_value, idx) => idx);
    return indices.map((idx) => rowValues[idx]).filter((value) => Number.isFinite(value));
  });
  if (!values.length) return { min: 0, max: 1, span: 1, signed: level === "signed" };
  const signed = level === "signed" && values.some((value) => value < 0);
  const min = signed ? Math.min(...values) : Math.min(0, ...values);
  const max = Math.max(1, ...values);
  return { min, max, span: max - min || 1, signed };
}

function edgeChartRange(values) {
  const clean = (values || []).filter((value) => Number.isFinite(value));
  if (!clean.length) return { min: 0, max: 1, span: 1 };
  const rawMin = Math.min(...clean);
  const rawMax = Math.max(...clean);
  if (rawMin === rawMax) {
    const margin = Math.max(0.05, Math.abs(rawMin) * 0.08);
    return { min: rawMin - margin, max: rawMax + margin, span: margin * 2 };
  }
  const margin = (rawMax - rawMin) * 0.06;
  const min = rawMin - margin;
  const max = rawMax + margin;
  return { min, max, span: max - min || 1 };
}

function edgeScoreColor(value, scale) {
  const numeric = Number(value) || 0;
  if (scale.signed && numeric < 0) {
    const ratio = Math.min(1, Math.abs(numeric) / Math.max(Math.abs(scale.min), 1e-9));
    return interpolateColor([254, 242, 242], [185, 28, 28], ratio);
  }
  const ratio = Math.max(0, Math.min(1, (numeric - Math.max(0, scale.min)) / (scale.max - Math.max(0, scale.min) || 1)));
  return interpolateColor([239, 246, 255], [3, 105, 161], ratio);
}

function edgeBarWidth(value, scale) {
  const numeric = Number(value) || 0;
  if (scale.signed) {
    const maxAbs = Math.max(Math.abs(scale.min), Math.abs(scale.max), 1e-9);
    return Math.max(4, Math.min(100, (Math.abs(numeric) / maxAbs) * 100));
  }
  return Math.max(4, Math.min(100, ((numeric - scale.min) / scale.span) * 100));
}

function edgeNetworkDisplay(network) {
  return `${network.source_id}:${network.tool_id} · ${contextLabel(network.context)}`;
}

function edgeEvaluationScale(networks) {
  const metric = state.edgeEvaluationMetric || "";
  if (!metric) return null;
  const values = (networks || [])
    .map((network) => edgeMetricValue(network))
    .filter((value) => value !== null);
  if (!values.length) return { metric, min: 0, max: 1, span: 1, empty: true };
  if (evaluationMetricIsBounded(metric)) return { metric, min: 0, max: 1, span: 1, empty: false };
  const min = Math.min(0, ...values);
  const max = Math.max(...values);
  return { metric, min, max, span: max - min || 1, empty: false };
}

function edgeMetricValue(network) {
  const value = Number(network?.metric_value);
  const status = String(network?.metric_status ?? "missing").trim();
  if (!Number.isFinite(value)) return null;
  if (!["", "ok", "partial"].includes(status)) return null;
  return value;
}

function edgeMetricChipData(network, scale) {
  const label = evaluationMetricLabel(scale.metric);
  const status = network?.metric_status || "missing";
  const value = edgeMetricValue(network);
  if (value === null) {
    return {
      text: "no eval",
      background: "#f8fafc",
      color: "#64748b",
      border: "#cbd5e1",
      title: `${label}: not evaluated (${status})`,
      missing: true,
    };
  }
  return {
    text: formatValue(value),
    background: evaluationColor(value, "selected", scale),
    color: evaluationTextColor(value, scale),
    border: "rgba(15, 118, 110, 0.28)",
    title: `${label}: ${formatValue(value)}`,
    missing: false,
  };
}

function edgeMetricChip(network, scale) {
  if (!scale?.metric) return "";
  const chip = edgeMetricChipData(network, scale);
  return `
    <span
      class="edge-metric-chip ${chip.missing ? "missing" : ""}"
      style="background:${chip.background}; color:${chip.color}; border-color:${chip.border}"
      title="${escapeHtml(chip.title)}"
    >${escapeHtml(chip.text)}</span>
  `;
}

function svgEdgeMetricChip(network, scale, x, y, { anchor = "middle" } = {}) {
  if (!scale?.metric) return "";
  const chip = edgeMetricChipData(network, scale);
  const width = Math.max(46, estimateLabelWidth(chip.text) + 16);
  const height = 18;
  const left = anchor === "middle" ? x - (width / 2) : x;
  return `
    <g class="edge-svg-metric-chip ${chip.missing ? "missing" : ""}" transform="translate(${left} ${y})">
      <rect width="${width}" height="${height}" rx="9" ry="9" fill="${chip.background}" stroke="${chip.border}"></rect>
      <text x="${width / 2}" y="12" text-anchor="middle" fill="${chip.color}">
        <title>${escapeHtml(chip.title)}</title>${escapeHtml(chip.text)}
      </text>
    </g>
  `;
}

function renderEdgeSparklineMatrix(level, networks, rows) {
  const activeColumns = edgeActiveColumnIndices(level, networks.length);
  const selectedEdges = edgeSelectedKeySet(level);
  const scale = edgeValueScale(level, rows);
  const metricScale = edgeEvaluationScale(networks);
  const head = [
    '<th class="edge-cell-edge">Edge</th>',
    ...networks.map((network, idx) => {
      const active = activeColumns.includes(idx);
      const disabled = !active && activeColumns.length >= 6;
      return `
        <th class="edge-spark-column ${active ? "active" : ""}">
          <button
            type="button"
            data-edge-column-index="${idx}"
            class="edge-column-toggle ${active ? "active" : ""}"
            ${disabled ? "disabled" : ""}
            title="${escapeHtml(edgeNetworkDisplay(network))}"
          >
            <span class="edge-column-title">
              <span class="selection-index">${idx + 1}</span>
              <span>${escapeHtml(network.tool_id)}</span>
            </span>
            ${edgeMetricChip(network, metricScale)}
          </button>
        </th>
      `;
    }),
    '<th>Variance</th>',
  ];
  const body = rows.map((row) => {
    const key = edgeRowKey(row);
    const selected = selectedEdges.has(key);
    const values = (row.values || []).map((value) => Number(value) || 0);
    const cells = [`
      <td class="edge-cell-edge" title="${escapeHtml(row.edge_key)}">
        <button type="button" class="edge-row-toggle ${selected ? "selected" : ""}" data-edge-row-key="${escapeHtml(key)}">
          <span class="edge-row-mark"></span>
          <span>${escapeHtml(row.edge_label || row.edge_key)}</span>
        </button>
      </td>
    `];
    networks.forEach((_network, idx) => {
      const raw = (row.raw || [])[idx] || {};
      const display = level === "signed" && raw.sign ? `${raw.sign}${formatValue(Math.abs(values[idx]))}` : formatValue(values[idx]);
      const active = activeColumns.includes(idx);
      cells.push(`
        <td class="edge-spark-cell ${active ? "active" : ""}">
          <div class="edge-spark-bar" title="${escapeHtml(display)}" style="--score-width:${edgeBarWidth(values[idx], scale)}%; --score-color:${edgeScoreColor(values[idx], scale)}">
            <span></span>
            <strong>${escapeHtml(display)}</strong>
          </div>
        </td>
      `);
    });
    cells.push(`<td>${formatValue(row.variance)}</td>`);
    return `<tr class="${selected ? "selected" : ""}" data-edge-row="${escapeHtml(key)}">${cells.join("")}</tr>`;
  }).join("");
  return `
    <div class="edge-sparkline-head">
      <div class="subtle">Click rows to highlight interactions. Click network columns to choose 2-6 columns for the chart.</div>
      <span class="badge">${activeColumns.length} visual columns</span>
    </div>
    <div class="edge-diff-table-wrap edge-sparkline-wrap">
      <table class="edge-sparkline"><thead><tr>${head.join("")}</tr></thead><tbody>${body}</tbody></table>
    </div>
  `;
}

function bindEdgeLevelInteractions(level) {
  const levelResult = state.edgeVariabilityLevels.find((item) => item.level === level);
  const networkCount = (levelResult?.networks || []).length;
  for (const button of document.querySelectorAll("[data-edge-column-index]")) {
    button.addEventListener("click", () => {
      const index = Number(button.dataset.edgeColumnIndex);
      const current = edgeActiveColumnIndices(level, networkCount);
      const active = current.includes(index);
      let next = current;
      if (active && current.length > 2) {
        next = current.filter((item) => item !== index);
      } else if (!active && current.length < 6) {
        next = [...current, index].sort((a, b) => a - b);
      }
      state.edgeVisualColumns[level] = next;
      updateEdgeLevelPanels(state.edgeVariabilityLevels);
    });
  }
  for (const button of document.querySelectorAll("[data-edge-row-key]")) {
    button.addEventListener("click", () => {
      const key = button.dataset.edgeRowKey || "";
      const selected = edgeSelectedKeySet(level);
      if (selected.has(key)) selected.delete(key);
      else selected.add(key);
      state.edgeSelectedEdges[level] = [...selected];
      updateEdgeLevelPanels(state.edgeVariabilityLevels);
    });
  }
}

function renderEdgeDifferenceChart(level, networks, rows) {
  const activeColumns = edgeActiveColumnIndices(level, networks.length);
  const activeNetworks = activeColumns.map((idx) => networks[idx]).filter(Boolean);
  if (activeNetworks.length < 2) {
    return '<section class="edge-chart-card"><div class="empty">Select at least two network columns.</div></section>';
  }
  if (activeNetworks.length > 6) {
    return '<section class="edge-chart-card"><div class="empty">Select at most six network columns.</div></section>';
  }
  if (activeNetworks.length === 2) return renderPairScatter(level, networks, chartRowsFor(level, rows, 300), activeColumns);
  return renderSlopeChart(level, networks, chartRowsFor(level, rows, 120), activeColumns);
}

function selectedChartRows(level, rows) {
  const selected = edgeSelectedKeySet(level);
  return {
    selected,
    hasSelection: selected.size > 0,
    rows,
  };
}

function chartRowsFor(level, rows, limit) {
  const selected = edgeSelectedKeySet(level);
  if (!selected.size) return rows.slice(0, limit);
  const visible = rows.slice(0, limit);
  const visibleKeys = new Set(visible.map(edgeRowKey));
  const selectedOutside = rows.filter((row) => selected.has(edgeRowKey(row)) && !visibleKeys.has(edgeRowKey(row)));
  return [...visible, ...selectedOutside];
}

function renderPairScatter(level, networks, rows, activeColumns) {
  if (!rows.length) return "";
  const activeNetworks = activeColumns.map((idx) => networks[idx]);
  const metricScale = edgeEvaluationScale(networks);
  const values = rows.flatMap((row) => activeColumns.map((idx) => Number((row.values || [])[idx]) || 0));
  const { min, max, span } = edgeChartRange(values);
  const selected = selectedChartRows(level, rows);
  const width = 420;
  const height = 350;
  const pad = 50;
  const bottomPad = 76;
  const chartWidth = width - (pad * 2);
  const chartHeight = height - pad - bottomPad;
  const x = (value) => pad + ((value - min) / span) * chartWidth;
  const y = (value) => height - bottomPad - ((value - min) / span) * chartHeight;
  const xAxisY = height - bottomPad;
  const xLabel = activeNetworks[0].tool_id;
  const yLabel = activeNetworks[1].tool_id;
  return `
    <section class="edge-chart-card">
      <div class="panel-title"><h3>Score Scatter</h3><span class="subtle">top rows</span></div>
      <svg viewBox="0 0 ${width} ${height}" class="edge-scatter">
        <line class="edge-chart-axis" x1="${pad}" x2="${width - pad}" y1="${xAxisY}" y2="${xAxisY}"></line>
        <line class="edge-chart-axis" x1="${pad}" x2="${pad}" y1="${pad}" y2="${xAxisY}"></line>
        <line class="edge-chart-diagonal" x1="${x(min)}" x2="${x(max)}" y1="${y(min)}" y2="${y(max)}"></line>
        ${rows.map((row) => {
          const values = (row.values || []).map((value) => Number(value) || 0);
          const key = edgeRowKey(row);
          const rowSelected = selected.selected.has(key);
          const dim = selected.hasSelection && !rowSelected;
          return `<circle class="edge-scatter-point ${rowSelected ? "selected" : ""} ${dim ? "dimmed" : ""}" cx="${x(values[activeColumns[0]])}" cy="${y(values[activeColumns[1]])}" r="${rowSelected ? 5 : 3}"><title>${escapeHtml(row.edge_label || row.edge_key)}</title></circle>`;
        }).join("")}
        <text class="edge-chart-label" x="${width / 2}" y="${height - 36}" text-anchor="middle">${escapeHtml(xLabel)}</text>
        ${svgEdgeMetricChip(activeNetworks[0], metricScale, (width / 2) + (estimateLabelWidth(xLabel) / 2) + 12, height - 49, { anchor: "start" })}
        <g transform="translate(16 ${pad + (chartHeight / 2)}) rotate(-90)">
          <text class="edge-chart-label" text-anchor="middle">${escapeHtml(yLabel)}</text>
          ${svgEdgeMetricChip(activeNetworks[1], metricScale, (estimateLabelWidth(yLabel) / 2) + 12, -13, { anchor: "start" })}
        </g>
      </svg>
    </section>
  `;
}

function renderSlopeChart(level, networks, rows, activeColumns) {
  if (!rows.length) return "";
  const activeNetworks = activeColumns.map((idx) => networks[idx]);
  const metricScale = edgeEvaluationScale(networks);
  const values = rows.flatMap((row) => activeColumns.map((idx) => Number((row.values || [])[idx]) || 0));
  const { min, span } = edgeChartRange(values);
  const selected = selectedChartRows(level, rows);
  const width = 460;
  const height = 350;
  const pad = 42;
  const bottomPad = 82;
  const axisBottom = height - bottomPad;
  const x = (idx) => pad + (idx / Math.max(1, activeNetworks.length - 1)) * (width - (pad * 2));
  const y = (value) => axisBottom - ((value - min) / span) * (axisBottom - pad);
  return `
    <section class="edge-chart-card">
      <div class="panel-title"><h3>Parallel Scores</h3><span class="subtle">${activeNetworks.length} selected columns</span></div>
      <svg viewBox="0 0 ${width} ${height}" class="edge-slope">
        ${activeNetworks.map((network, idx) => `
          <line class="edge-chart-axis" x1="${x(idx)}" x2="${x(idx)}" y1="${pad}" y2="${axisBottom}"></line>
          <text class="edge-chart-label" x="${x(idx)}" y="${height - 42}" text-anchor="middle">${escapeHtml(network.tool_id)}</text>
          ${svgEdgeMetricChip(network, metricScale, x(idx), height - 34, { anchor: "middle" })}
        `).join("")}
        ${rows.map((row) => {
          const key = edgeRowKey(row);
          const rowSelected = selected.selected.has(key);
          const dim = selected.hasSelection && !rowSelected;
          const values = row.values || [];
          const points = activeColumns.map((columnIdx, valueIdx) => `${x(valueIdx)},${y(Number(values[columnIdx]) || 0)}`).join(" ");
          return `<polyline class="edge-slope-line ${rowSelected ? "selected" : ""} ${dim ? "dimmed" : ""}" points="${points}"><title>${escapeHtml(row.edge_label || row.edge_key)}</title></polyline>`;
        }).join("")}
      </svg>
    </section>
  `;
}

function setActiveResultTab(tabId) {
  for (const button of document.querySelectorAll(".tab-button")) {
    button.classList.toggle("active", button.dataset.tabTarget === tabId);
  }
  for (const panel of document.querySelectorAll(".tab-panel")) {
    panel.classList.toggle("hidden", panel.id !== tabId);
  }
}

function updateResultSummary(report, job) {
  const summary = report.summary || {};
  const artifactStatus = job?.artifact_status?.edge_scores_csv || report?.artifact_status?.edge_scores_csv || "";
  const artifactSuffix = artifactStatus && artifactStatus !== "ready"
    ? ` · edge_scores.csv ${artifactStatus}`
    : "";
  $("#result-summary").textContent = `${summary.network_instances || 0} network instances, ${summary.distance_rows || 0} distance rows${artifactSuffix}`;
  const statusHost = $("#result-artifact-status");
  if (!statusHost) {
    return;
  }
  const normalizedStatus = (() => {
    const status = String(artifactStatus || "").trim().toLowerCase();
    if (["ready", "failed", "exporting"].includes(status)) return status;
    return status ? "pending" : "pending";
  })();
  const labelByStatus = {
    ready: "edge_scores.csv ready",
    exporting: "edge_scores.csv exporting",
    failed: "edge_scores.csv failed",
    pending: "edge_scores.csv pending",
  };
  const classByStatus = {
    ready: "ready",
    exporting: "working",
    failed: "failed",
    pending: "pending",
  };
  statusHost.innerHTML = "";
  const explorer = document.createElement("span");
  explorer.className = "result-artifact-chip ready";
  explorer.textContent = "Explorer ready";
  const edge = document.createElement("span");
  edge.className = `result-artifact-chip ${classByStatus[normalizedStatus] || "pending"}`;
  edge.textContent = labelByStatus[normalizedStatus] || "edge_scores.csv pending";
  statusHost.append(explorer, edge);
}

function renderReport(report, job, reproducibility) {
  if (!report) return;
  state.report = report;
  state.renderedReportPath = job?.comparison_report_path || null;
  $("#raw-report").textContent = JSON.stringify(report, null, 2);
  updateResultSummary(report, job);
  const sources = report.sources || [];
  state.activeSourceId = state.activeSourceId || sources[0]?.source_id || null;
  state.selectedNetworks = [];
  state.edgeBuilder = {};
  state.edgeActiveLevel = "topology";
  state.edgeVariabilityLevels = [];
  state.edgeVariabilityRequestKey = "";
  state.edgeVariabilityInFlightKey = "";
  state.edgeVariabilityRequestSeq += 1;
  state.distanceMetric = comparisonMetricOptions(report)[0] || "weighted_jaccard_distance";
  state.distanceEvaluationMetric = evaluationMetricOptions(report)[0]?.key || "";
  state.distanceContextFamily = "";
  state.distanceSelectedContexts = [];
  state.distanceSpecificQuery = "";
  state.distanceActiveLevel = "topology";
  renderDistanceExplorer(report);
  renderEdgeExplorer(report);
  renderEdgeSourceCards(report);
  renderReproducibility(reproducibility);
  setHidden("#download-link", false);
  setHidden("#result-panel", false);
  setActiveResultTab("distance-tab");
}

function renderJob(payload) {
  const job = payload.job;
  state.jobId = job.job_id;
  setStatus(job.status, job.stage || job.status);
  setHidden("#error-panel", true);

  if (job.status === "failed") {
    stopPolling();
    setHidden("#result-panel", true);
    setHidden("#download-link", true);
    resetReproducibility();
    state.report = null;
    state.renderedReportPath = null;
    setHidden("#upload-progress-panel", false);
    setOverallHandoffProgress({
      stateClass: "failed",
      label: "Failed",
      percent: 100,
    });
    renderError(job);
    return;
  }
  if (job.status === "completed" || job.status === "finalizing_artifacts") {
    if (job.status === "completed") {
      stopPolling();
    } else {
      startPolling(job.job_id);
    }
    setHidden("#upload-progress-panel", false);
    if (job.status === "completed") {
      setOverallHandoffProgress({
        stateClass: "uploaded",
        label: "Ready",
        percent: 100,
      });
    } else {
      updateHandoffJobProgress(job);
    }
    if (payload.comparison_report && state.renderedReportPath !== job.comparison_report_path) {
      renderReport(payload.comparison_report, job, payload.reproducibility);
    } else if (payload.comparison_report) {
      updateResultSummary(payload.comparison_report, job);
      renderReproducibility(payload.reproducibility);
      setHidden("#download-link", false);
      setHidden("#result-panel", false);
    }
    return;
  }
  if (job.status === "queued" || job.status === "running") {
    if (payload.comparison_report && state.renderedReportPath !== job.comparison_report_path) {
      renderReport(payload.comparison_report, job, payload.reproducibility);
    }
    updateHandoffJobProgress(job);
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
  setHidden("#error-panel", true);
  setHidden("#result-panel", true);
  setHidden("#download-link", true);
  resetReproducibility();
  state.activeSourceId = null;
  state.report = null;
  state.renderedReportPath = null;
  state.selectedNetworks = [];
  state.edgeBuilder = {};
  state.edgeActiveLevel = "topology";
  state.edgeVariabilityLevels = [];
  state.edgeVariabilityRequestKey = "";
  state.edgeVariabilityInFlightKey = "";
  state.edgeVariabilityRequestSeq += 1;
  $("#distance-map-view").innerHTML = "";
  $("#edge-difference-view").innerHTML = "";
  $("#raw-report").textContent = "";
  $("#run-button").disabled = true;
  setStatus("running", "uploading");
  const uploadItems = uploadProgressItemsForCards();
  const overallItem = overallUploadProgressItem();
  state.handoffProgressPercent = 0;
  setHidden("#upload-progress-panel", false);
  resetUploadProgress([overallItem]);
  try {
    const payload = await uploadFormDataWithProgress({
      url: "/api/compare-networks/run",
      formData: buildUploadFormData(),
      fileItems: uploadItems,
      overallItem,
      overallCompleteOnLoad: false,
      onServerProcessing: () => setStatus("running", "validating"),
    });
    setOverallHandoffProgress({
      stateClass: "validating",
      label: "Starting comparison",
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
    showClientError("Submit and complete a comparison job before downloading bundles.");
    return;
  }
  openBundleDownloadModal({
    title: "Download Comparison ZIP",
    metadataUrl: `/api/compare-networks/jobs/${state.jobId}/bundles`,
    downloadUrlForBundle: (bundleId) => (
      `/api/compare-networks/jobs/${state.jobId}/bundle?bundle_id=${encodeURIComponent(bundleId)}`
    ),
  }).catch((error) => {
    showClientError(String(error.message || error));
  });
}

$("#add-source-button").addEventListener("click", addSourceCard);
$("#upload-form").addEventListener("submit", submitUploads);
$("#download-link").addEventListener("click", openDownloadBundles);
for (const button of document.querySelectorAll(".tab-button")) {
  button.addEventListener("click", () => setActiveResultTab(button.dataset.tabTarget));
}
initReproducibility();
initBundleDownloadModal();
addSourceCard();
resetUploadProgressRows();
