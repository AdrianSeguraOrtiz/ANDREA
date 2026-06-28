(function () {
  const metricDefs = [
    { key: "auroc", label: "AUROC" },
    { key: "aupr", label: "AUPR" },
    { key: "f1_at_truth_count", label: "F1@truth-count" },
    { key: "epr_at_truth_count", label: "EPR@truth-count" }
  ];
  const levels = ["topology", "directed", "signed"];
  const contextFamilyOrder = ["global", "group", "column", "sample", "timepoint", "perturbation", "other"];
  const specificContextFamilies = ["column", "sample", "timepoint", "perturbation"];
  const specificContextLabels = {
    column: "Column Contexts",
    sample: "Sample Contexts",
    timepoint: "Timepoint Contexts",
    perturbation: "Perturbation Contexts"
  };
  let activeLevel = "topology";
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

  function svgIdPart(value) {
    return String(value ?? "")
      .replace(/[^A-Za-z0-9_-]+/g, "-")
      .replace(/^-+|-+$/g, "") || "item";
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
    if (text === "global") return "global";
    const prefixes = [
      ["group:", "group"],
      ["column:", "column"],
      ["sample:", "sample"],
      ["timepoint:", "timepoint"],
      ["perturbation:", "perturbation"]
    ];
    for (const [prefix, label] of prefixes) {
      if (text.startsWith(prefix)) {
        const value = text.slice(prefix.length);
        return value ? `${label} ${value}` : label;
      }
    }
    return text;
  }

  function contextFamily(context) {
    const text = String(context || "");
    if (text === "global") return "global";
    if (text.startsWith("group:")) return "group";
    if (text.startsWith("column:")) return "column";
    if (text.startsWith("sample:")) return "sample";
    if (text.startsWith("timepoint:")) return "timepoint";
    if (text.startsWith("perturbation:")) return "perturbation";
    return "other";
  }

  function sortContext(a, b) {
    const order = Object.fromEntries(contextFamilyOrder.map((family, idx) => [family, idx]));
    const familyDiff = order[contextFamily(a)] - order[contextFamily(b)];
    if (familyDiff !== 0) return familyDiff;
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

  function quantile(sortedValues, probability) {
    if (!sortedValues.length) return null;
    if (sortedValues.length === 1) return sortedValues[0];
    const idx = (sortedValues.length - 1) * probability;
    const lower = Math.floor(idx);
    const upper = Math.ceil(idx);
    if (lower === upper) return sortedValues[lower];
    const ratio = idx - lower;
    return sortedValues[lower] + ((sortedValues[upper] - sortedValues[lower]) * ratio);
  }

  function summarizeValues(values) {
    const sorted = values.filter((value) => Number.isFinite(value)).sort((a, b) => a - b);
    if (!sorted.length) return null;
    return {
      min: sorted[0],
      q1: quantile(sorted, 0.25),
      median: quantile(sorted, 0.5),
      q3: quantile(sorted, 0.75),
      max: sorted[sorted.length - 1]
    };
  }

  function plotPosition(value, metricKey, maxValue) {
    return scaledWidth(value, metricKey, maxValue) * 100;
  }

  function specificMissingByTool(report, family) {
    const entries = report?.context_matching?.missing_truth_contexts_by_tool;
    if (!Array.isArray(entries)) return new Map();
    return new Map(entries.map((entry) => [
      String(entry.tool_id || ""),
      Number(entry.missing_context_counts_by_family?.[family] || 0)
    ]).filter(([tool]) => tool));
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
        <section class="level-tabs" data-role="level-tabs" role="tablist" aria-label="Evaluation levels"></section>
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
    return `<div class="matrix-wrap group-matrix-wrap"><table class="matrix group-matrix">${head}${body}</table></div>`;
  }

  function violinDensityPath(values, metricKey, maxValue) {
    const positions = values
      .map((value) => scaledWidth(value, metricKey, maxValue))
      .filter((value) => Number.isFinite(value))
      .map((value) => Math.max(0, Math.min(1, value)));
    if (!positions.length) return "";
    const sampleCount = 48;
    const bandwidth = Math.max(0.035, Math.min(0.16, 0.55 / Math.sqrt(Math.max(1, positions.length))));
    const samples = Array.from({ length: sampleCount }, (_item, idx) => {
      const x = sampleCount === 1 ? 0 : idx / (sampleCount - 1);
      const density = positions.reduce((sum, pos) => {
        const z = (x - pos) / bandwidth;
        return sum + Math.exp(-0.5 * z * z);
      }, 0);
      return { x, density };
    });
    const maxDensity = Math.max(...samples.map((sample) => sample.density));
    if (!Number.isFinite(maxDensity) || maxDensity <= 0) return "";
    const top = samples.map((sample) => {
      const radius = Math.max(0.8, (sample.density / maxDensity) * 11.5);
      return `${(sample.x * 100).toFixed(2)},${(15 - radius).toFixed(2)}`;
    });
    const bottom = samples.slice().reverse().map((sample) => {
      const radius = Math.max(0.8, (sample.density / maxDensity) * 11.5);
      return `${(sample.x * 100).toFixed(2)},${(15 + radius).toFixed(2)}`;
    });
    return `M ${top.concat(bottom).join(" L ")} Z`;
  }

  function violinGradientStops(metricKey, maxValue) {
    const stops = metricKey === "epr_at_truth_count"
      ? (() => {
        const neutral = Math.max(0, Math.min(100, scaledWidth(1, metricKey, maxValue) * 100));
        if (neutral >= 99.5) return [[0, "#b91c1c"], [100, "#f8fafc"]];
        if (neutral <= 0.5) return [[0, "#f8fafc"], [100, "#075985"]];
        return [[0, "#b91c1c"], [neutral, "#f8fafc"], [100, "#075985"]];
      })()
      : [
        [0, "#f7fbff"],
        [35, "#c6dbef"],
        [70, "#6baed6"],
        [100, "#08519c"]
      ];
    return stops.map(([offset, color]) => (
      `<stop offset="${offset.toFixed(2)}%" stop-color="${color}" stop-opacity="0.58"></stop>`
    )).join("");
  }

  function renderSpecificViolin(values, summary, metricKey, maxValue, gradientId) {
    const min = plotPosition(summary.min, metricKey, maxValue);
    const q1 = plotPosition(summary.q1, metricKey, maxValue);
    const median = plotPosition(summary.median, metricKey, maxValue);
    const q3 = plotPosition(summary.q3, metricKey, maxValue);
    const max = plotPosition(summary.max, metricKey, maxValue);
    const path = violinDensityPath(values, metricKey, maxValue);
    return `
      <div class="specific-violin" title="min ${formatValue(summary.min)}, q1 ${formatValue(summary.q1)}, median ${formatValue(summary.median)}, q3 ${formatValue(summary.q3)}, max ${formatValue(summary.max)}">
        <svg viewBox="0 0 100 30" preserveAspectRatio="none" aria-hidden="true">
          <defs>
            <linearGradient id="${gradientId}" x1="0%" x2="100%" y1="0%" y2="0%">
              ${violinGradientStops(metricKey, maxValue)}
            </linearGradient>
          </defs>
          ${path ? `<path class="specific-violin-shape" d="${path}" fill="url(#${gradientId})"></path>` : ""}
          <line class="specific-violin-range" x1="${min}" x2="${max}" y1="15" y2="15"></line>
          <line class="specific-violin-iqr" x1="${q1}" x2="${q3}" y1="15" y2="15"></line>
          <line class="specific-violin-median" x1="${median}" x2="${median}" y1="4" y2="26"></line>
        </svg>
      </div>
    `;
  }

  function renderSpecificContexts(rows, metricKey, maxValue, report, level, family) {
    const familyRows = rows.filter((row) => {
      return contextFamily(row.context) === family;
    });
    const missingByTool = specificMissingByTool(report, family);
    const tools = [...new Set(familyRows
      .filter(statusOk)
      .filter((row) => metricValue(row, metricKey) !== null)
      .map((row) => row.tool_id)
      .filter(Boolean)
    )].sort((a, b) => String(a).localeCompare(String(b)));
    if (!tools.length) return `<div class="empty">No applicable ${escapeHtml(family)} contexts.</div>`;

    const rowsHtml = tools.map((tool, index) => {
      const toolRows = familyRows.filter((row) => row.tool_id === tool);
      const values = toolRows
        .filter(statusOk)
        .map((row) => metricValue(row, metricKey))
        .filter((value) => value !== null);
      const summary = summarizeValues(values);
      const nUnavailable = toolRows.filter((row) => !statusOk(row)).length;
      const nMissing = missingByTool.get(tool) || 0;
      const unavailable = nUnavailable + nMissing;
      const stats = summary ? [
        ["contexts", values.length],
        ["unmatched", unavailable],
        ["median", formatValue(summary.median)],
        ["q1-q3", `${formatValue(summary.q1)}-${formatValue(summary.q3)}`],
        ["min-max", `${formatValue(summary.min)}-${formatValue(summary.max)}`]
      ] : [
        ["contexts", 0],
        ["unmatched", unavailable]
      ];
      return `
        <div class="specific-dist-row">
          <div class="tool-label" title="${escapeHtml(tool)}">${escapeHtml(tool)}</div>
          ${summary ? renderSpecificViolin(
            values,
            summary,
            metricKey,
            maxValue,
            `specific-violin-gradient-${svgIdPart(level)}-${svgIdPart(metricKey)}-${svgIdPart(family)}-${svgIdPart(tool)}-${index}`
          ) : `<div class="empty compact">No evaluated ${escapeHtml(family)} contexts.</div>`}
          <div class="specific-stats">
            ${stats.map(([label, value]) => `
              <span><strong>${escapeHtml(value)}</strong><small>${escapeHtml(label)}</small></span>
            `).join("")}
          </div>
        </div>
      `;
    }).join("");
    return `<div class="specific-dist-list">${rowsHtml}</div>`;
  }

  function renderOtherContexts(rows, metricKey, maxValue) {
    const otherRows = rows
      .filter((row) => {
        const context = String(row.context || "");
        const family = contextFamily(context);
        return context && family !== "global" && family !== "group" && !specificContextFamilies.includes(family);
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
          <td>${escapeHtml(contextLabel(row.context))}</td>
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
          <span class="subtle">compact table</span>
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

  function renderLevelCard({ level, rows, metricKey, maxValue, report }) {
    const evaluated = rows.filter(statusOk).length;
    const na = rows.filter((row) => row.status === "not_applicable").length;
    const otherContexts = renderOtherContexts(rows, metricKey, maxValue);
    const specificSections = specificContextFamilies
      .filter((family) => rows.some((row) => contextFamily(row.context) === family))
      .map((family) => `
        <section class="column-contexts">
          <div class="panel-title"><h3>${escapeHtml(specificContextLabels[family] || `${family[0].toUpperCase() + family.slice(1)} Contexts`)}</h3><span class="subtle">distributions by tool</span></div>
          ${renderSpecificContexts(rows, metricKey, maxValue, report, level, family)}
        </section>
      `).join("");
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
            <div class="viz-scroll-panel">${renderGlobal(rows, metricKey, maxValue)}</div>
          </section>
          <section>
            <div class="panel-title"><h3>Groups</h3><span class="subtle">heatmap</span></div>
            ${renderGroups(rows, metricKey, maxValue)}
          </section>
        </div>
        ${specificSections}
        ${otherContexts}
        <div class="legend">
          <span>Scale</span>
          <span class="legend-ramp ${metricKey === "epr_at_truth_count" ? "epr" : ""}"></span>
          <span>${metricKey === "epr_at_truth_count" ? "log2 EPR, centered at 1" : "0 to 1"}</span>
        </div>
      </article>
    `;
  }

  function renderLevelTabs(root, levelRows) {
    const tabs = root.querySelector('[data-role="level-tabs"]');
    tabs.innerHTML = levels.map((level) => {
      const active = level === activeLevel;
      const rows = levelRows.get(level) || [];
      const evaluated = rows.filter(statusOk).length;
      return `
        <button type="button" class="level-tab ${active ? "active" : ""}" data-level="${escapeHtml(level)}" role="tab" aria-selected="${active ? "true" : "false"}">
          <span>${escapeHtml(level[0].toUpperCase() + level.slice(1))}</span>
          <small>${evaluated} evaluated</small>
        </button>
      `;
    }).join("");
    for (const button of tabs.querySelectorAll("[data-level]")) {
      button.addEventListener("click", () => {
        activeLevel = button.dataset.level;
        renderLevels(root, root.__andreaEvaluationReport || {}, root.__andreaEvaluationMetrics || []);
      });
    }
  }

  function renderLevels(root, report, metrics) {
    const metricKey = root.querySelector('[data-role="metric-select"]').value;
    const target = root.querySelector('[data-role="levels"]');
    const levelRows = new Map(levels.map((level) => [
      level,
      metrics.filter((row) => row.level === level)
    ]));
    if (!levels.includes(activeLevel)) {
      activeLevel = levels[0];
    }
    renderLevelTabs(root, levelRows);
    const rows = levelRows.get(activeLevel) || [];
    const values = rows.map((row) => metricValue(row, metricKey)).filter((value) => value !== null);
    const maxValue = scaleMax(values, metricKey);
    target.innerHTML = renderLevelCard({
      level: activeLevel,
      rows,
      metricKey,
      maxValue,
      report,
    });
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
    root.__andreaEvaluationReport = report || {};
    root.__andreaEvaluationMetrics = metrics;
    activeLevel = levels.includes(activeLevel) ? activeLevel : "topology";
    const metricSelect = root.querySelector('[data-role="metric-select"]');
    for (const metric of metricDefs) {
      const option = document.createElement("option");
      option.value = metric.key;
      option.textContent = metric.label;
      metricSelect.appendChild(option);
    }
    renderSummary(root, report || {}, metrics);
    renderLevels(root, report || {}, metrics);
    renderMetricsTable(root, metrics);
    metricSelect.addEventListener("change", () => renderLevels(root, report || {}, metrics));
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
