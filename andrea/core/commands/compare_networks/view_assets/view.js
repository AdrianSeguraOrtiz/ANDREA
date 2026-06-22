(function () {
  const previewLimits = {
    network_index: 18,
    evaluation_metrics: 18,
    distances: 24,
    distance_coordinates: 12,
    runtime_profile: 18
  };
  const contextFamilyOrder = ["global", "group", "column", "sample", "timepoint", "perturbation", "other"];
  const contextFamilyLabels = {
    global: "Global",
    group: "Group",
    column: "Column",
    sample: "Sample",
    timepoint: "Timepoint",
    perturbation: "Perturbation",
    other: "Other"
  };

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function formatValue(value) {
    if (value === null || value === undefined || value === "") return "N/A";
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return String(value);
    if (Math.abs(numeric) >= 100) return numeric.toFixed(0);
    if (Math.abs(numeric) >= 10) return numeric.toFixed(1);
    return numeric.toFixed(3).replace(/0+$/, "").replace(/\.$/, "");
  }

  function countLabel(count, singular, plural = `${singular}s`) {
    return `${count} ${count === 1 ? singular : plural}`;
  }

  function shortPath(path) {
    const text = String(path || "");
    if (!text) return "not written";
    const parts = text.split(/[\\/]/).filter(Boolean);
    return parts.slice(-2).join("/");
  }

  function pathLeaf(path) {
    const text = String(path || "");
    const parts = text.split(/[\\/]/).filter(Boolean);
    return parts[parts.length - 1] || text;
  }

  function artifactRows(outputs) {
    const specs = [
      ["comparison_report", "comparison_report.json", "Lightweight machine-readable report for CLI pipelines, provenance and bundle discovery."],
      ["comparison_sqlite", "comparison.sqlite", "Indexed query store for large comparisons; useful for SQL inspection, scripts and GUI exploration."],
      ["network_index_csv", "network_index.csv", "Portable index of every network instance, source, context and level."],
      ["distances_csv", "distances.csv", "Complete pairwise distance table for CLI analysis and downstream statistics."],
      ["distance_coordinates_csv", "distance_coordinates.csv", "Coordinate table for CLI plotting, auditing and downstream inspection."],
      ["edge_scores_csv", "edge_scores.csv", "Complete edge-score table for exact CLI inspection and custom edge-level analyses."],
      ["comparison_request", "comparison-request.json", "Frozen comparison request for reproducibility and reruns."],
      ["comparison_view", "comparison_view.html", "This static HTML summary for quick human inspection."]
    ];
    return specs.map(([key, label, purpose]) => ({
      key,
      label,
      purpose,
      path: outputs?.[key] || ""
    }));
  }

  function statCards(report) {
    const summary = report.summary || {};
    const contexts = Array.isArray(report.contexts) ? report.contexts : [];
    const warnings = Array.isArray(report.warnings) ? report.warnings : [];
    return [
      ["Sources", summary.sources ?? (report.sources || []).length ?? 0],
      ["Networks", summary.network_instances ?? 0],
      ["Contexts", contexts.length],
      ["Distances", summary.distance_rows ?? 0],
      ["Edge score rows", summary.edge_score_rows ?? 0],
      ["Warnings", summary.warnings ?? warnings.length]
    ].map(([label, value]) => `
      <div class="stat">
        <div class="stat-label">${escapeHtml(label)}</div>
        <div class="stat-value">${escapeHtml(formatValue(value))}</div>
      </div>
    `).join("");
  }

  function renderSources(report) {
    const sources = Array.isArray(report.sources) ? report.sources : [];
    if (!sources.length) return '<div class="empty">No source metadata available.</div>';
    return `
      <div class="source-list">
        ${sources.map((source) => `
          <article class="source-card">
            <div>
              <h3>${escapeHtml(source.label || source.source_id)}</h3>
              <div class="subtle">${escapeHtml(source.source_id || "")}</div>
            </div>
            <dl>
              <dt>Inference run</dt>
              <dd>${escapeHtml(source.run_id || "N/A")}</dd>
              <dt>Evaluation</dt>
              <dd>${source.evaluation_report ? "available" : "not provided"}</dd>
            </dl>
          </article>
        `).join("")}
      </div>
    `;
  }

  function renderContextSummary(report) {
    const counts = report.context_counts_by_family || {};
    const contexts = Array.isArray(report.contexts) ? report.contexts : [];
    const knownFamilies = contextFamilyOrder
      .filter((family) => Object.prototype.hasOwnProperty.call(counts, family))
      .map((family) => [family, counts[family]]);
    const extraFamilies = Object.entries(counts)
      .filter(([family]) => !contextFamilyOrder.includes(family))
      .sort(([familyA], [familyB]) => familyA.localeCompare(familyB));
    const families = knownFamilies.concat(extraFamilies);
    return `
      <div class="context-summary">
        ${families.map(([family, count]) => `
          <span class="context-pill"><strong>${escapeHtml(contextFamilyLabels[family] || family)}</strong>${escapeHtml(count)}</span>
        `).join("")}
      </div>
      <p class="subtle">${escapeHtml(countLabel(contexts.length, "context"))} total. Large context-specific exploration is intentionally not embedded in this HTML.</p>
    `;
  }

  function renderArtifacts(report) {
    const rows = artifactRows(report.outputs || {});
    return `
      <div class="artifact-list">
        ${rows.map((row) => `
          <article class="artifact-card ${row.path ? "ready" : "missing"}">
            <div>
              <h3>${escapeHtml(row.label)}</h3>
              <div class="subtle">${escapeHtml(row.purpose)}</div>
            </div>
            <code title="${escapeHtml(row.path)}">${escapeHtml(shortPath(row.path))}</code>
          </article>
        `).join("")}
      </div>
    `;
  }

  function renderWarnings(report) {
    const warnings = Array.isArray(report.warnings) ? report.warnings : [];
    if (!warnings.length) {
      return '<div class="empty ok">No comparison warnings.</div>';
    }
    return `
      <ul class="warning-list">
        ${warnings.map((warning) => `<li>${escapeHtml(warning)}</li>`).join("")}
      </ul>
    `;
  }

  function tablePreview({ title, rows, columns, limit, detail }) {
    const safeRows = Array.isArray(rows) ? rows : [];
    const visible = safeRows.slice(0, limit);
    return `
      <section class="table-card">
        <div class="table-controls">
          <div>
            <h2>${escapeHtml(title)}</h2>
            <div class="subtle">${escapeHtml(detail || "")}</div>
          </div>
          <span class="badge">${escapeHtml(countLabel(safeRows.length, "row"))}</span>
        </div>
        ${visible.length ? `
          <div class="table-wrap">
            <table>
              <thead>
                <tr>${columns.map((column) => `<th>${escapeHtml(column)}</th>`).join("")}</tr>
              </thead>
              <tbody>
                ${visible.map((row) => `
                  <tr>
                    ${columns.map((column) => `<td title="${escapeHtml(row[column])}">${escapeHtml(formatValue(row[column]))}</td>`).join("")}
                  </tr>
                `).join("")}
              </tbody>
            </table>
          </div>
          ${safeRows.length > visible.length ? `<div class="preview-note">Showing first ${visible.length} rows. Use the CSV or SQLite artifact for the complete table.</div>` : ""}
        ` : '<div class="empty">No rows available.</div>'}
      </section>
    `;
  }

  function renderPreviews(report) {
    return [
      tablePreview({
        title: "Network Index Preview",
        rows: report.network_index,
        columns: ["source_id", "tool_id", "context", "level", "n_genes", "n_edges"],
        limit: previewLimits.network_index,
        detail: "Small preview of network_index.csv."
      }),
      tablePreview({
        title: "Evaluation Metrics Preview",
        rows: report.evaluation_metrics,
        columns: ["source_id", "tool_id", "context", "level", "status", "auroc", "aupr", "f1_at_truth_count", "epr_at_truth_count"],
        limit: previewLimits.evaluation_metrics,
        detail: "Only present for sources with evaluate-inference bundles."
      }),
      tablePreview({
        title: "Distances Preview",
        rows: report.distances,
        columns: ["source_id", "context", "level", "distance_metric", "network_a", "network_b", "distance", "status", "warning"],
        limit: previewLimits.distances,
        detail: "Preview of distances.csv. Interactive distance maps are available in the GUI."
      }),
      tablePreview({
        title: "Coordinate Preview",
        rows: report.distance_coordinates,
        columns: ["source_id", "context", "level", "distance_metric", "network_id", "x", "y", "status", "warning"],
        limit: previewLimits.distance_coordinates,
        detail: "Preview of distance_coordinates.csv."
      }),
      tablePreview({
        title: "Runtime Profile",
        rows: report.runtime_profile,
        columns: ["stage", "label", "detail", "elapsed_s"],
        limit: previewLimits.runtime_profile,
        detail: "Execution stages captured by the core command."
      })
    ].join("");
  }

  function renderAvailability(report) {
    const metrics = Array.isArray(report.metrics_available) ? report.metrics_available : [];
    const distances = Array.isArray(report.distances_available) ? report.distances_available : [];
    const levels = Array.isArray(report.levels) ? report.levels : [];
    return `
      <div class="chip-row">
        <span class="chip-label">Levels</span>
        ${levels.length ? levels.map((item) => `<span class="chip">${escapeHtml(item)}</span>`).join("") : '<span class="chip muted">none</span>'}
      </div>
      <div class="chip-row">
        <span class="chip-label">Distances</span>
        ${distances.length ? distances.map((item) => `<span class="chip">${escapeHtml(item)}</span>`).join("") : '<span class="chip muted">none</span>'}
      </div>
      <div class="chip-row">
        <span class="chip-label">Evaluation metrics</span>
        ${metrics.length ? metrics.map((item) => `<span class="chip">${escapeHtml(item)}</span>`).join("") : '<span class="chip muted">not provided</span>'}
      </div>
    `;
  }

  function render(root, report) {
    const safeReport = report || {};
    const requestId = safeReport.request?.id || "";
    root.innerHTML = `
      <main class="andrea-comparison-view">
        <header class="page">
          <div>
            <h1>Network Comparison Static Report</h1>
            <div class="subtle">${escapeHtml([requestId ? `request: ${requestId}` : "", safeReport.created_at ? `created: ${safeReport.created_at}` : ""].filter(Boolean).join(" | "))}</div>
          </div>
          <div class="report-kind">Static HTML summary</div>
        </header>

        <section class="notice">
          <div>
            <h2>Interactive exploration lives in the local GUI</h2>
            <p>This HTML intentionally does not embed scalable distance maps or edge-difference views. Those views query <code>${escapeHtml(pathLeaf(safeReport.outputs?.comparison_sqlite || "comparison.sqlite"))}</code> from the ANDREA GUI. CLI and Python users can use the CSV files for portable tabular analysis or query <code>comparison.sqlite</code> directly for indexed large-result inspection.</p>
          </div>
          <div class="notice-actions">
            <span class="badge">CSV complete</span>
            <span class="badge">SQLite query store</span>
            <span class="badge">HTML lightweight</span>
          </div>
        </section>

        <section class="summary">${statCards(safeReport)}</section>

        <section class="two-column">
          <article class="panel">
            <h2>Sources</h2>
            ${renderSources(safeReport)}
          </article>
          <article class="panel">
            <h2>Artifacts</h2>
            ${renderArtifacts(safeReport)}
          </article>
        </section>

        <section class="two-column">
          <article class="panel">
            <h2>Contexts</h2>
            ${renderContextSummary(safeReport)}
          </article>
          <article class="panel">
            <h2>Available Metrics</h2>
            ${renderAvailability(safeReport)}
          </article>
        </section>

        <section class="panel">
          <h2>Warnings</h2>
          ${renderWarnings(safeReport)}
        </section>

        <section class="panel">
          <h2>Edge Differences</h2>
          <p class="subtle">Edge variability is not computed in this static HTML. Open this comparison directory in the local GUI to query <code>comparison.sqlite</code>, or inspect <code>edge_scores.csv</code> directly.</p>
        </section>

        ${renderPreviews(safeReport)}
      </main>
    `;
  }

  document.addEventListener("DOMContentLoaded", () => {
    const data = document.getElementById("comparison-data");
    const root = document.getElementById("comparison-view-root");
    if (!data || !root) return;
    render(root, JSON.parse(data.textContent));
  });
}());
