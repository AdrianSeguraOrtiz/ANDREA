(function () {
  const metricDefs = [
    { key: "auroc", label: "AUROC" },
    { key: "aupr", label: "AUPR" },
    { key: "f1_at_truth_count", label: "F1@truth-count" },
    { key: "epr_at_truth_count", label: "EPR@truth-count" }
  ];
  const levels = ["topology", "directed", "signed"];
  const tableColumns = [
    "tool_id", "catalog_tool_id", "context", "level", "status",
    "auroc", "aupr", "f1_at_truth_count", "epr_at_truth_count",
    "n_truth_edges", "n_predicted_edges", "n_candidate_genes", "n_candidates", "reason"
  ];

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function metricLabel(metricKey) {
    return metricDefs.find((metric) => metric.key === metricKey)?.label || metricKey;
  }

  function metricValue(row, metricKey) {
    const value = Number(row?.[metricKey]);
    return Number.isFinite(value) ? Math.max(0, value) : null;
  }

  function formatValue(value) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return "N/A";
    const numeric = Number(value);
    if (Math.abs(numeric) >= 100) return numeric.toFixed(0);
    if (Math.abs(numeric) >= 10) return numeric.toFixed(1);
    return numeric.toFixed(2);
  }

  function contextLabel(context) {
    const text = String(context || "");
    return text.startsWith("group:") ? text.slice(6) : text;
  }

  function sortContext(a, b) {
    if (a === "global" && b !== "global") return -1;
    if (b === "global" && a !== "global") return 1;
    return contextLabel(a).localeCompare(contextLabel(b), undefined, { numeric: true });
  }

  function statusOk(row) {
    return row && (row.status === "ok" || row.status === "partial");
  }

  function scaleMax(values, metricKey) {
    const finite = values.filter((value) => Number.isFinite(value));
    if (metricKey === "epr_at_truth_count") return Math.max(1, ...finite, 1);
    return 1;
  }

  function scaledWidth(value, metricKey, maxValue) {
    if (!Number.isFinite(value) || value <= 0) return 0;
    if (metricKey === "epr_at_truth_count") {
      return Math.max(0, Math.min(1, Math.log1p(value) / Math.log1p(Math.max(1, maxValue))));
    }
    return Math.max(0, Math.min(1, value));
  }

  function interpolate(left, right, ratio) {
    const clamped = Math.max(0, Math.min(1, ratio));
    const rgb = left.map((channel, idx) => Math.round(channel + ((right[idx] - channel) * clamped)));
    return `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})`;
  }

  function sequentialColor(value) {
    const stops = [
      [0.0, [247, 251, 255]],
      [0.35, [198, 219, 239]],
      [0.7, [107, 174, 214]],
      [1.0, [8, 81, 156]]
    ];
    const clamped = Math.max(0, Math.min(1, value));
    for (let idx = 0; idx < stops.length - 1; idx += 1) {
      const [leftValue, leftColor] = stops[idx];
      const [rightValue, rightColor] = stops[idx + 1];
      if (clamped <= rightValue) {
        const span = rightValue - leftValue;
        return interpolate(leftColor, rightColor, span <= 0 ? 0 : (clamped - leftValue) / span);
      }
    }
    return "rgb(8, 81, 156)";
  }

  function eprColor(value, maxValue) {
    if (!Number.isFinite(value) || value <= 0) return "#e5e7eb";
    const centered = Math.log2(value);
    const maxAbs = Math.max(1, Math.abs(Math.log2(Math.max(1.000001, maxValue))));
    const normalized = Math.max(-1, Math.min(1, centered / maxAbs));
    if (normalized >= 0) return interpolate([248, 250, 252], [7, 89, 133], normalized);
    return interpolate([185, 28, 28], [248, 250, 252], normalized + 1);
  }

  function colorFor(value, metricKey, maxValue) {
    if (!Number.isFinite(value)) return "#e5e7eb";
    if (metricKey === "epr_at_truth_count") return eprColor(value, maxValue);
    return sequentialColor(scaledWidth(value, metricKey, maxValue));
  }

  function textColorFor(value, metricKey, maxValue) {
    if (!Number.isFinite(value)) return "#64748b";
    if (metricKey === "epr_at_truth_count") {
      const centered = Math.abs(Math.log2(Math.max(0.000001, value)));
      const maxAbs = Math.max(1, Math.abs(Math.log2(Math.max(1.000001, maxValue))));
      return centered / maxAbs >= 0.6 ? "#ffffff" : "#0f172a";
    }
    return scaledWidth(value, metricKey, maxValue) >= 0.62 ? "#ffffff" : "#0f172a";
  }

  function buildShell() {
    return `
      <main class="andrea-evaluation-view">
        <header class="page">
          <div>
            <h1>Inference Evaluation</h1>
            <div class="subtle" data-role="subtitle"></div>
          </div>
          <div class="toolbar">
            <label>
              <strong>Metric</strong>
              <select data-role="metric-select"></select>
            </label>
          </div>
        </header>
        <section class="summary" data-role="summary"></section>
        <section class="level-grid" data-role="levels"></section>
        <section class="table-card">
          <div class="table-controls">
            <div>
              <h2>Metrics</h2>
              <div class="subtle">Exact values from metrics.csv</div>
            </div>
            <input data-role="metric-search" type="search" placeholder="Filter rows">
          </div>
          <div class="metrics-table-wrap">
            <table class="metrics" data-role="metrics-table"></table>
          </div>
        </section>
      </main>
    `;
  }

  function renderSummary(root, report, metrics) {
    const tools = new Set(metrics.map((row) => row.tool_id).filter(Boolean));
    const contexts = new Set(metrics.map((row) => row.context).filter(Boolean));
    const evaluated = metrics.filter(statusOk).length;
    const notApplicable = metrics.filter((row) => row.status === "not_applicable").length;
    root.querySelector('[data-role="subtitle"]').textContent = [
      report.inputs?.ground_truth_dataset_id ? `truth dataset: ${report.inputs.ground_truth_dataset_id}` : null,
      report.inputs?.ground_truth_simulator_id ? `simulator: ${report.inputs.ground_truth_simulator_id}` : null,
      report.inputs?.inference_run_id ? `run: ${report.inputs.inference_run_id}` : null
    ].filter(Boolean).join(" | ");
    root.querySelector('[data-role="summary"]').innerHTML = [
      ["Tools", tools.size],
      ["Contexts", contexts.size],
      ["Evaluated rows", evaluated],
      ["N/A rows", notApplicable]
    ].map(([label, value]) => `
      <div class="stat">
        <div class="stat-label">${escapeHtml(label)}</div>
        <div class="stat-value">${escapeHtml(value)}</div>
      </div>
    `).join("");
  }

  function renderGlobal(rows, metricKey, maxValue) {
    const values = rows
      .filter((row) => row.context === "global" && statusOk(row))
      .map((row) => ({ row, value: metricValue(row, metricKey) }))
      .filter((item) => item.value !== null)
      .sort((a, b) => (b.value - a.value) || String(a.row.tool_id).localeCompare(String(b.row.tool_id)));
    if (!values.length) return '<div class="empty">No applicable global values.</div>';
    return `<div class="bar-list">${values.map(({ row, value }) => {
      const width = scaledWidth(value, metricKey, maxValue) * 100;
      const color = colorFor(value, metricKey, maxValue);
      const title = `${row.tool_id} | global | ${metricKey} = ${value}`;
      return `
        <div class="bar-row" title="${escapeHtml(title)}">
          <div class="tool-label">${escapeHtml(row.tool_id)}</div>
          <div class="bar-track"><div class="bar-fill" style="width:${width}%; background:${color}"></div></div>
          <div class="value-label">${formatValue(value)}</div>
        </div>
      `;
    }).join("")}</div>`;
  }

  function renderGroups(rows, metricKey, maxValue) {
    const groupRows = rows.filter((row) => String(row.context || "").startsWith("group:"));
    const contexts = [...new Set(groupRows.map((row) => row.context))].sort(sortContext);
    const tools = [...new Set(groupRows.map((row) => row.tool_id))].sort((a, b) => String(a).localeCompare(String(b)));
    if (!contexts.length || !tools.length) return '<div class="empty">No applicable group values.</div>';
    const byKey = new Map(groupRows.map((row) => [`${row.tool_id}\u0000${row.context}`, row]));
    const head = `<tr><th>Tool</th>${contexts.map((context) => `<th>${escapeHtml(contextLabel(context))}</th>`).join("")}</tr>`;
    const body = tools.map((tool) => {
      const cells = contexts.map((context) => {
        const row = byKey.get(`${tool}\u0000${context}`);
        const value = statusOk(row) ? metricValue(row, metricKey) : null;
        if (value === null) {
          const reason = row?.reason || row?.status || "missing";
          return `<td class="status-missing" title="${escapeHtml(reason)}">N/A</td>`;
        }
        const color = colorFor(value, metricKey, maxValue);
        const textColor = textColorFor(value, metricKey, maxValue);
        const title = `${tool} | ${context} | ${metricKey} = ${value}`;
        return `<td style="background:${color}; color:${textColor}" title="${escapeHtml(title)}">${formatValue(value)}</td>`;
      }).join("");
      return `<tr><td title="${escapeHtml(tool)}">${escapeHtml(tool)}</td>${cells}</tr>`;
    }).join("");
    return `<div class="matrix-wrap"><table class="matrix">${head}${body}</table></div>`;
  }

  function renderOtherContexts(rows, metricKey, maxValue) {
    const otherRows = rows
      .filter((row) => {
        const context = String(row.context || "");
        return context && context !== "global" && !context.startsWith("group:");
      })
      .sort((a, b) => sortContext(a.context, b.context) || String(a.tool_id).localeCompare(String(b.tool_id)));
    if (!otherRows.length) return "";
    const body = otherRows.map((row) => {
      const value = statusOk(row) ? metricValue(row, metricKey) : null;
      const color = value === null ? "#e5e7eb" : colorFor(value, metricKey, maxValue);
      const textColor = value === null ? "#64748b" : textColorFor(value, metricKey, maxValue);
      const reason = row.reason || row.status || "";
      return `
        <tr>
          <td>${escapeHtml(row.context)}</td>
          <td>${escapeHtml(row.tool_id)}</td>
          <td style="background:${color}; color:${textColor}">${value === null ? "N/A" : formatValue(value)}</td>
          <td>${escapeHtml(reason)}</td>
        </tr>
      `;
    }).join("");
    return `
      <section class="other-contexts">
        <div class="panel-title">
          <h3>Other Contexts</h3>
          <span class="subtle">generic table</span>
        </div>
        <div class="matrix-wrap">
          <table class="matrix other-context-table">
            <tr><th>Context</th><th>Tool</th><th>${escapeHtml(metricLabel(metricKey))}</th><th>Status</th></tr>
            ${body}
          </table>
        </div>
      </section>
    `;
  }

  function renderLevels(root, metrics) {
    const metricKey = root.querySelector('[data-role="metric-select"]').value;
    const target = root.querySelector('[data-role="levels"]');
    target.innerHTML = levels.map((level) => {
      const rows = metrics.filter((row) => row.level === level);
      const values = rows.map((row) => metricValue(row, metricKey)).filter((value) => value !== null);
      const maxValue = scaleMax(values, metricKey);
      const evaluated = rows.filter(statusOk).length;
      const na = rows.filter((row) => row.status === "not_applicable").length;
      const otherContexts = renderOtherContexts(rows, metricKey, maxValue);
      return `
        <article class="level-card">
          <div class="level-head">
            <div>
              <h2>${escapeHtml(level[0].toUpperCase() + level.slice(1))}</h2>
              <div class="subtle">${escapeHtml(metricLabel(metricKey))}</div>
            </div>
            <div class="badges">
              <span class="badge">${evaluated} evaluated</span>
              <span class="badge">${na} N/A</span>
              ${metricKey === "epr_at_truth_count" ? '<span class="badge">1 = random baseline</span>' : ''}
            </div>
          </div>
          <div class="viz-grid">
            <section>
              <div class="panel-title"><h3>Global</h3><span class="subtle">bars</span></div>
              ${renderGlobal(rows, metricKey, maxValue)}
            </section>
            <section>
              <div class="panel-title"><h3>Groups</h3><span class="subtle">heatmap</span></div>
              ${renderGroups(rows, metricKey, maxValue)}
            </section>
          </div>
          ${otherContexts}
          <div class="legend">
            <span>Scale</span>
            <span class="legend-ramp ${metricKey === "epr_at_truth_count" ? "epr" : ""}"></span>
            <span>${metricKey === "epr_at_truth_count" ? "log2 EPR, centered at 1" : "0 to 1"}</span>
          </div>
        </article>
      `;
    }).join("");
  }

  function renderMetricsTable(root, metrics) {
    const query = root.querySelector('[data-role="metric-search"]').value.trim().toLowerCase();
    const rows = metrics.filter((row) => !query || tableColumns.some((column) => String(row[column] ?? "").toLowerCase().includes(query)));
    const head = `<thead><tr>${tableColumns.map((column) => `<th>${escapeHtml(column)}</th>`).join("")}</tr></thead>`;
    const body = `<tbody>${rows.map((row) => `
      <tr>
        ${tableColumns.map((column) => {
          const value = row[column];
          const display = typeof value === "number" ? formatValue(value) : (value ?? "");
          const cls = column === "status" ? ` class="status-${escapeHtml(String(value))}"` : "";
          return `<td${cls}>${escapeHtml(display)}</td>`;
        }).join("")}
      </tr>
    `).join("")}</tbody>`;
    root.querySelector('[data-role="metrics-table"]').innerHTML = head + body;
  }

  function render(root, report) {
    const metrics = Array.isArray(report?.metrics) ? report.metrics : [];
    root.innerHTML = buildShell();
    const metricSelect = root.querySelector('[data-role="metric-select"]');
    for (const metric of metricDefs) {
      const option = document.createElement("option");
      option.value = metric.key;
      option.textContent = metric.label;
      metricSelect.appendChild(option);
    }
    renderSummary(root, report || {}, metrics);
    renderLevels(root, metrics);
    renderMetricsTable(root, metrics);
    metricSelect.addEventListener("change", () => renderLevels(root, metrics));
    root.querySelector('[data-role="metric-search"]').addEventListener("input", () => renderMetricsTable(root, metrics));
  }

  window.AndreaEvaluationView = {
    render,
    metricDefs: metricDefs.slice(),
    levels: levels.slice()
  };

  document.addEventListener("DOMContentLoaded", () => {
    const data = document.getElementById("evaluation-data");
    const root = document.getElementById("evaluation-view-root");
    if (!data || !root) return;
    window.AndreaEvaluationView.render(root, JSON.parse(data.textContent));
  });
}());
