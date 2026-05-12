(function () {
  const levels = ["topology", "directed", "signed"];
  const metricDefs = [
    { key: "", label: "No metric coloring" },
    { key: "auroc", label: "AUROC" },
    { key: "aupr", label: "AUPR" },
    { key: "f1_at_truth_count", label: "F1@truth-count" },
    { key: "epr_at_truth_count", label: "EPR@truth-count" }
  ];
  const distanceColumns = [
    "source_id", "context", "level", "distance_metric", "network_a", "network_b",
    "distance", "n_common_genes", "n_edges_considered", "status", "warning"
  ];
  const rootStates = new WeakMap();
  const maxEdgeDifferenceRows = 200;

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
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
      ["cell:", "cell"]
    ];
    for (const [prefix, label] of prefixes) {
      if (text.startsWith(prefix)) {
        const value = text.slice(prefix.length);
        return value ? `${label} ${value}` : label;
      }
    }
    return text;
  }

  function sourceLabel(sourceId, sources) {
    const source = sources.find((item) => item.source_id === sourceId);
    return source?.label || sourceId;
  }

  function metricLabel(metricKey) {
    return metricDefs.find((metric) => metric.key === metricKey)?.label || metricKey;
  }

  function sortContext(a, b) {
    if (a === "global" && b !== "global") return -1;
    if (b === "global" && a !== "global") return 1;
    return contextLabel(a).localeCompare(contextLabel(b), undefined, { numeric: true });
  }

  function interpolate(left, right, ratio) {
    const clamped = Math.max(0, Math.min(1, ratio));
    const rgb = left.map((channel, idx) => Math.round(channel + ((right[idx] - channel) * clamped)));
    return `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})`;
  }

  function sequentialColor(value, maxValue) {
    if (!Number.isFinite(value)) return "#94a3b8";
    const scale = maxValue > 1 ? Math.log1p(value) / Math.log1p(maxValue) : value;
    return interpolate([191, 219, 254], [3, 105, 161], Math.max(0, Math.min(1, scale)));
  }

  function textColorFor(value, maxValue) {
    if (!Number.isFinite(value)) return "#0f172a";
    const scale = maxValue > 1 ? Math.log1p(value) / Math.log1p(maxValue) : value;
    return scale >= 0.62 ? "#ffffff" : "#0f172a";
  }

  function metricScale(values) {
    const clean = values.filter((value) => Number.isFinite(value));
    if (!clean.length) return { min: 0, max: 1, span: 1 };
    const min = Math.min(...clean);
    const max = Math.max(...clean);
    return { min, max, span: max - min || 1 };
  }

  function metricColor(value, scale) {
    if (!Number.isFinite(value)) return "#94a3b8";
    const ratio = (value - scale.min) / scale.span;
    return interpolate([219, 234, 254], [3, 105, 161], ratio);
  }

  function metricTextColor(value, scale) {
    if (!Number.isFinite(value)) return "#0f172a";
    return ((value - scale.min) / scale.span) >= 0.62 ? "#ffffff" : "#0f172a";
  }

  function distanceMetricLabel(metric) {
    if (metric === "weighted_jaccard_distance") return "Weighted Jaccard distance";
    if (metric === "rank_overlap_distance_at_truth_count") return "Rank-overlap distance @ truth-count";
    return metric || "Distance";
  }

  function metricMap(metrics) {
    const byKey = new Map();
    for (const row of metrics) {
      byKey.set(`${row.source_id}\u0000${row.tool_id}\u0000${row.context}\u0000${row.level}`, row);
    }
    return byKey;
  }

  function metricForNetwork(network, metricKey, metricsByKey) {
    if (!metricKey || !network) return null;
    const metric = metricsByKey.get(`${network.source_id}\u0000${network.tool_id}\u0000${network.context}\u0000${network.level}`);
    const rawValue = metric?.[metricKey];
    if (rawValue === null || rawValue === undefined || rawValue === "") return null;
    const value = Number(rawValue);
    return Number.isFinite(value) ? value : null;
  }

  function buildShell() {
    return `
      <main class="andrea-comparison-view">
        <header class="page">
          <div>
            <h1>Network Comparison</h1>
            <div class="subtle" data-role="subtitle"></div>
          </div>
          <div class="toolbar">
            <label>
              <strong>Source</strong>
              <select data-role="source-select"></select>
            </label>
            <label>
              <strong>Context</strong>
              <select data-role="context-select"></select>
            </label>
            <label>
              <strong>Distance</strong>
              <select data-role="distance-select"></select>
            </label>
            <label>
              <strong>Metric</strong>
              <select data-role="metric-select"></select>
            </label>
          </div>
        </header>
        <section class="summary" data-role="summary"></section>
        <section class="level-grid" data-role="levels"></section>
        <section class="table-card edge-differences-card">
          <div class="table-controls">
            <div>
              <h2>Ordered Edge Differences</h2>
              <div class="subtle">Edges are ranked by score variance across the selected ordered tools.</div>
            </div>
            <span class="subtle" data-role="edge-selection-summary"></span>
          </div>
          <section class="edge-level-grid" data-role="edge-difference-levels"></section>
        </section>
        <section class="table-card">
          <div class="table-controls">
            <div>
              <h2>Distances</h2>
              <div class="subtle">Exact values from distances.csv</div>
            </div>
            <input data-role="distance-search" type="search" placeholder="Filter rows">
          </div>
          <div class="metrics-table-wrap">
            <table class="distances" data-role="distances-table"></table>
          </div>
        </section>
      </main>
    `;
  }

  function buildDistanceMapsShell(options = {}) {
    const showSourceSelect = options.showSourceSelect !== false;
    const showSummary = options.showSummary !== false;
    const showDistancesTable = options.showDistancesTable !== false;
    return `
      <main class="andrea-comparison-view">
        <header class="page">
          <div>
            <h1>${escapeHtml(options.title || "Distance Maps")}</h1>
            <div class="subtle" data-role="subtitle"></div>
          </div>
          <div class="toolbar">
            <label class="${showSourceSelect ? "" : "hidden-control"}">
              <strong>Source</strong>
              <select data-role="source-select"></select>
            </label>
            <label>
              <strong>Context</strong>
              <select data-role="context-select"></select>
            </label>
            <label>
              <strong>Distance</strong>
              <select data-role="distance-select"></select>
            </label>
            <label>
              <strong>Metric</strong>
              <select data-role="metric-select"></select>
            </label>
          </div>
        </header>
        ${showSummary ? '<section class="summary" data-role="summary"></section>' : ""}
        <section class="level-grid" data-role="levels"></section>
        ${showDistancesTable ? `
          <section class="table-card">
            <div class="table-controls">
              <div>
                <h2>Distances</h2>
                <div class="subtle">Exact values from distances.csv</div>
              </div>
              <input data-role="distance-search" type="search" placeholder="Filter rows">
            </div>
            <div class="metrics-table-wrap">
              <table class="distances" data-role="distances-table"></table>
            </div>
          </section>
        ` : ""}
      </main>
    `;
  }

  function buildEdgeDifferencesShell(options = {}) {
    return `
      <main class="andrea-comparison-view">
        <header class="page">
          <div>
            <h1>${escapeHtml(options.title || "Edge Differences")}</h1>
            <div class="subtle">Compare ordered tools edge-by-edge using only common genes.</div>
          </div>
          <div class="toolbar">
            <label>
              <strong>Distance</strong>
              <select data-role="distance-select"></select>
            </label>
            <label>
              <strong>Metric</strong>
              <select data-role="metric-select"></select>
            </label>
          </div>
        </header>
        <section class="table-card edge-differences-card">
          <div class="table-controls">
            <div>
              <h2>Ordered Edge Differences</h2>
              <div class="subtle">Edges are ranked by score variance across the selected ordered tools.</div>
            </div>
            <span class="subtle" data-role="edge-selection-summary"></span>
          </div>
          <section class="edge-level-grid" data-role="edge-difference-levels"></section>
        </section>
      </main>
    `;
  }

  function renderSummary(root, report) {
    const summary = report.summary || {};
    const sources = Array.isArray(report.sources) ? report.sources : [];
    const contexts = Array.isArray(report.contexts) ? report.contexts : [];
    const subtitle = root.querySelector('[data-role="subtitle"]');
    if (subtitle) subtitle.textContent = [
      report.request?.id ? `request: ${report.request.id}` : null,
      report.created_at ? `created: ${report.created_at}` : null
    ].filter(Boolean).join(" | ");
    const summaryTarget = root.querySelector('[data-role="summary"]');
    if (!summaryTarget) return;
    summaryTarget.innerHTML = [
      ["Sources", sources.length || summary.sources || 0],
      ["Contexts", contexts.length],
      ["Networks", summary.network_instances || 0],
      ["Distances", summary.distance_rows || 0],
      ["Warnings", summary.warnings || 0]
    ].map(([label, value]) => `
      <div class="stat">
        <div class="stat-label">${escapeHtml(label)}</div>
        <div class="stat-value">${escapeHtml(value)}</div>
      </div>
    `).join("");
  }

  function setOptions(select, values, labels) {
    const previous = select.value;
    select.innerHTML = "";
    for (const value of values) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = labels?.get(value) || value;
      select.appendChild(option);
    }
    if (values.includes(previous)) select.value = previous;
  }

  function orderedDistanceMetrics(values) {
    const preferred = ["weighted_jaccard_distance", "rank_overlap_distance_at_truth_count"];
    const seen = new Set();
    const clean = values.filter((value) => {
      const key = String(value || "").trim();
      if (!key || seen.has(key)) return false;
      seen.add(key);
      return true;
    });
    return [
      ...preferred.filter((metric) => seen.has(metric)),
      ...clean.filter((metric) => !preferred.includes(metric)).sort()
    ];
  }

  function selectedState(root) {
    const current = rootStates.get(root) || {};
    const sourceSelect = root.querySelector('[data-role="source-select"]');
    const contextSelect = root.querySelector('[data-role="context-select"]');
    const distanceSelect = root.querySelector('[data-role="distance-select"]');
    const metricSelect = root.querySelector('[data-role="metric-select"]');
    return {
      sourceId: sourceSelect?.value || current.sourceId || "",
      context: contextSelect?.value || current.context || "",
      distanceMetric: distanceSelect?.value || current.distanceMetric || "",
      metricKey: metricSelect?.value || current.metricKey || ""
    };
  }

  function refreshContextOptions(root, report) {
    const contextSelect = root.querySelector('[data-role="context-select"]');
    if (!contextSelect) return;
    const current = rootStates.get(root) || {};
    const sourceId = root.querySelector('[data-role="source-select"]')?.value || current.sourceId || "";
    const networkIndex = Array.isArray(report.network_index) ? report.network_index : [];
    const contexts = [...new Set(networkIndex
      .filter((row) => row.source_id === sourceId)
      .map((row) => row.context)
      .filter(Boolean))]
      .sort(sortContext);
    const labels = new Map(contexts.map((context) => [context, contextLabel(context)]));
    setOptions(contextSelect, contexts, labels);
    current.sourceId = sourceId;
    current.context = contextSelect.value;
  }

  function networkLabel(networkId, indexById) {
    const item = indexById.get(networkId);
    if (!item) return networkId;
    return item.tool_id || networkId;
  }

  function renderScatter(coords, indexById, metricKey, metricsByKey, scale) {
    const okCoords = coords
      .filter((row) => row.status === "ok")
      .map((row) => ({
        row,
        x: Number(row.x),
        y: Number(row.y),
        network: indexById.get(row.network_id)
      }))
      .filter((item) => Number.isFinite(item.x) && Number.isFinite(item.y));
    if (!okCoords.length) {
      const warning = coords.find((row) => row.warning)?.warning || "Coordinates are not available for this panel.";
      return `<div class="empty">${escapeHtml(warning)}</div>`;
    }

    const width = 640;
    const height = 360;
    const pad = 48;
    const xs = okCoords.map((item) => item.x);
    const ys = okCoords.map((item) => item.y);
    let minX = Math.min(...xs);
    let maxX = Math.max(...xs);
    let minY = Math.min(...ys);
    let maxY = Math.max(...ys);
    if (minX === maxX) { minX -= 1; maxX += 1; }
    if (minY === maxY) { minY -= 1; maxY += 1; }
    const xPad = (maxX - minX) * 0.12;
    const yPad = (maxY - minY) * 0.12;
    minX -= xPad; maxX += xPad;
    minY -= yPad; maxY += yPad;
    const scaleX = (x) => pad + ((x - minX) / (maxX - minX)) * (width - (pad * 2));
    const scaleY = (y) => height - pad - ((y - minY) / (maxY - minY)) * (height - (pad * 2));
    const zeroX = minX <= 0 && maxX >= 0 ? scaleX(0) : null;
    const zeroY = minY <= 0 && maxY >= 0 ? scaleY(0) : null;
    return `
      <div class="scatter-wrap">
        <svg class="scatter" viewBox="0 0 ${width} ${height}" role="img" aria-label="MDS distance map">
          ${zeroX !== null ? `<line class="axis" x1="${zeroX}" x2="${zeroX}" y1="${pad}" y2="${height - pad}"></line>` : ""}
          ${zeroY !== null ? `<line class="axis" x1="${pad}" x2="${width - pad}" y1="${zeroY}" y2="${zeroY}"></line>` : ""}
          ${okCoords.map((item) => {
            const value = metricForNetwork(item.network, metricKey, metricsByKey);
            const color = metricKey && value !== null ? metricColor(value, scale) : "#0369a1";
            const label = networkLabel(item.row.network_id, indexById);
            const title = [
              label,
              item.row.network_id,
              metricKey && value !== null ? `${metricKey}: ${formatValue(value)}` : null
            ].filter(Boolean).join(" | ");
            const cx = scaleX(item.x);
            const cy = scaleY(item.y);
            return `
              <g>
                <title>${escapeHtml(title)}</title>
                <circle class="node" cx="${cx}" cy="${cy}" r="8" style="fill:${color}"></circle>
                <text class="label" x="${cx + 11}" y="${cy + 4}">${escapeHtml(label)}</text>
              </g>
            `;
          }).join("")}
        </svg>
      </div>
    `;
  }

  function distanceValue(row) {
    const value = Number(row?.distance);
    return Number.isFinite(value) ? value : null;
  }

  function renderMatrix(distanceRows, networkIds, indexById) {
    if (networkIds.length < 2) return '<div class="empty">At least two networks are required for a distance matrix.</div>';
    const byPair = new Map();
    for (const row of distanceRows) {
      const key = [row.network_a, row.network_b].sort().join("\u0000");
      byPair.set(key, row);
    }
    const head = `<tr><th>Network</th>${networkIds.map((networkId) => `<th>${escapeHtml(networkLabel(networkId, indexById))}</th>`).join("")}</tr>`;
    const body = networkIds.map((left, leftIdx) => {
      const cells = networkIds.map((right, rightIdx) => {
        if (rightIdx > leftIdx) return '<td class="matrix-empty"></td>';
        if (left === right) return '<td class="matrix-diagonal">0.00</td>';
        const row = byPair.get([left, right].sort().join("\u0000"));
        const value = distanceValue(row);
        if (value === null) {
          const warning = row?.warning || row?.status || "missing";
          return `<td class="status-not_available" title="${escapeHtml(warning)}">N/A</td>`;
        }
        const color = sequentialColor(1 - Math.max(0, Math.min(1, value)), 1);
        const textColor = textColorFor(1 - Math.max(0, Math.min(1, value)), 1);
        return `<td style="background:${color}; color:${textColor}">${formatValue(value)}</td>`;
      }).join("");
      return `<tr><td title="${escapeHtml(left)}">${escapeHtml(networkLabel(left, indexById))}</td>${cells}</tr>`;
    }).join("");
    return `<div class="matrix-wrap"><table class="matrix">${head}${body}</table></div>`;
  }

  function renderLevels(root, report) {
    const state = selectedState(root);
    const networkIndex = Array.isArray(report.network_index) ? report.network_index : [];
    const indexById = new Map(networkIndex.map((row) => [row.network_id, row]));
    const distances = Array.isArray(report.distances) ? report.distances : [];
    const coords = Array.isArray(report.distance_coordinates) ? report.distance_coordinates : [];
    const metricsByKey = metricMap(Array.isArray(report.evaluation_metrics) ? report.evaluation_metrics : []);
    const metricValues = networkIndex
      .map((network) => metricForNetwork(network, state.metricKey, metricsByKey))
      .filter((value) => value !== null);
    const scale = metricScale(metricValues);
    root.querySelector('[data-role="levels"]').innerHTML = levels.map((level) => {
      const levelNetworks = networkIndex
        .filter((row) => row.source_id === state.sourceId && row.context === state.context && row.level === level)
        .map((row) => row.network_id)
        .sort();
      const levelDistances = distances.filter((row) =>
        row.source_id === state.sourceId &&
        row.context === state.context &&
        row.level === level &&
        row.distance_metric === state.distanceMetric
      );
      const levelCoords = coords.filter((row) =>
        row.source_id === state.sourceId &&
        row.context === state.context &&
        row.level === level &&
        row.distance_metric === state.distanceMetric
      );
      const okDistances = levelDistances.filter((row) => row.status === "ok").length;
      return `
        <article class="level-card">
          <div class="level-head">
            <div>
              <h2>${escapeHtml(level[0].toUpperCase() + level.slice(1))}</h2>
              <div class="subtle">${escapeHtml(state.distanceMetric || "no distance selected")}</div>
            </div>
            <div class="badges">
              <span class="badge">${levelNetworks.length} networks</span>
              <span class="badge">${okDistances} distances</span>
              ${state.metricKey ? `<span class="badge">${escapeHtml(metricLabel(state.metricKey))}</span>` : ""}
            </div>
          </div>
          <div class="viz-grid">
            <section>
              <div class="panel-title"><h3>Distance Map</h3><span class="subtle">PCoA/MDS</span></div>
              ${renderScatter(levelCoords, indexById, state.metricKey, metricsByKey, scale)}
            </section>
            <section>
              <div class="panel-title"><h3>Distance Matrix</h3><span class="subtle">exact values</span></div>
              ${renderMatrix(levelDistances, levelNetworks, indexById)}
            </section>
          </div>
          <div class="legend">
            <span>Node color</span>
            <span class="legend-ramp"></span>
            <span>${state.metricKey ? `${escapeHtml(metricLabel(state.metricKey))} · min ${formatValue(scale.min)} · max ${formatValue(scale.max)}` : "source-neutral"}</span>
          </div>
        </article>
      `;
    }).join("");
  }

  function selectedNetworksForRoot(root) {
    const state = rootStates.get(root);
    return Array.isArray(state?.selectedNetworks) ? state.selectedNetworks : [];
  }

  function edgeScoresByNetwork(report) {
    const byNetwork = new Map();
    const edgeScores = Array.isArray(report.edge_scores) ? report.edge_scores : [];
    for (const row of edgeScores) {
      if (!row.network_id || !row.edge_key) continue;
      if (!byNetwork.has(row.network_id)) byNetwork.set(row.network_id, new Map());
      byNetwork.get(row.network_id).set(row.edge_key, row);
    }
    return byNetwork;
  }

  function nodesForRows(rows) {
    const nodes = new Set();
    for (const row of rows) {
      if (row?.source) nodes.add(row.source);
      if (row?.target) nodes.add(row.target);
    }
    return nodes;
  }

  function intersectSets(sets) {
    if (!sets.length) return new Set();
    const common = new Set(sets[0]);
    for (const item of [...common]) {
      if (!sets.every((set) => set.has(item))) common.delete(item);
    }
    return common;
  }

  function rowInNodes(row, nodes) {
    return nodes.has(row.source) && nodes.has(row.target);
  }

  function signedValue(row, level) {
    if (!row) return 0;
    const score = Number(row.score);
    if (!Number.isFinite(score)) return 0;
    if (level === "signed" && row.sign === "-") return -score;
    return score;
  }

  function displayScore(row, level) {
    if (!row) return "0.00";
    const score = Number(row.score);
    if (!Number.isFinite(score)) return "0.00";
    if (level === "signed") {
      const prefix = row.sign === "-" ? "-" : "+";
      return `${prefix}${formatValue(score)}`;
    }
    return formatValue(score);
  }

  function edgeDifferenceKey(row, level) {
    if (!row) return "";
    if (level === "signed") return `${row.source}|${row.target}`;
    return row.edge_key || `${row.source}|${row.target}`;
  }

  function edgeDifferenceLabel(row, fallbackKey, level) {
    if (row?.source && row?.target) {
      if (level === "topology") return `${row.source} - ${row.target}`;
      return `${row.source} → ${row.target}`;
    }
    return fallbackKey;
  }

  function keepEdgeDifferenceRow(currentRow, nextRow, currentValue, nextValue) {
    if (!currentRow) return true;
    const currentAbs = Math.abs(Number(currentValue) || 0);
    const nextAbs = Math.abs(Number(nextValue) || 0);
    if (nextAbs !== currentAbs) return nextAbs > currentAbs;
    return String(nextRow?.sign || "").localeCompare(String(currentRow?.sign || "")) < 0;
  }

  function variance(values) {
    if (!values.length) return 0;
    const mean = values.reduce((acc, value) => acc + value, 0) / values.length;
    return values.reduce((acc, value) => acc + ((value - mean) ** 2), 0) / values.length;
  }

  function comparisonKey(item, level) {
    return `${item.source_id}\u0000${item.tool_id}\u0000${item.context}\u0000${level}`;
  }

  function selectedNetworkInstances(selectedNetworks, level, indexByComparisonKey) {
    return selectedNetworks
      .map((item) => indexByComparisonKey.get(comparisonKey(item, level)))
      .filter(Boolean);
  }

  function comparisonNetworkLabel(network) {
    if (!network) return "";
    return `${network.source_id}:${network.tool_id} · ${contextLabel(network.context)}`;
  }

  function metricBadge(network, metricKey, metricsByKey, scale) {
    if (!metricKey || !network) return "";
    const value = metricForNetwork(network, metricKey, metricsByKey);
    if (value === null) return "";
    const color = metricColor(value, scale);
    const textColor = metricTextColor(value, scale);
    return `<span class="order-metric" style="background:${color}; color:${textColor}">${formatValue(value)}</span>`;
  }

  function orderHeader(networks, metricKey, metricsByKey, scale) {
    if (!networks.length) return "";
    return `
      <div class="order-strip">
        ${networks.map((network, idx) => `
          <span class="order-node">
            <span class="selection-index">${idx + 1}</span>
            <span>${escapeHtml(comparisonNetworkLabel(network))}</span>
            ${metricBadge(network, metricKey, metricsByKey, scale)}
          </span>
          ${idx < networks.length - 1 ? '<span class="order-arrow">→</span>' : ''}
        `).join("")}
      </div>
    `;
  }

  function weightedJaccardForMaps(leftMap, rightMap) {
    const keys = new Set([...leftMap.keys(), ...rightMap.keys()]);
    if (!keys.size) return { status: "not_available", value: null, warning: "no comparable edges" };
    let numerator = 0;
    let denominator = 0;
    for (const key of keys) {
      const left = Math.abs(Number(leftMap.get(key) || 0));
      const right = Math.abs(Number(rightMap.get(key) || 0));
      numerator += Math.min(left, right);
      denominator += Math.max(left, right);
    }
    if (denominator <= 0) return { status: "not_available", value: null, warning: "zero comparable weights" };
    return { status: "ok", value: 1 - (numerator / denominator), warning: "" };
  }

  function topKSet(scoreMap, k) {
    return new Set([...scoreMap.entries()]
      .sort((a, b) => (Math.abs(b[1]) - Math.abs(a[1])) || String(a[0]).localeCompare(String(b[0])))
      .slice(0, k)
      .map(([key]) => key));
  }

  function truthCountForNetworks(networks, metricsByKey, level) {
    const values = networks
      .map((network) => metricsByKey.get(`${network.source_id}\u0000${network.tool_id}\u0000${network.context}\u0000${level}`)?.n_truth_edges)
      .map((value) => Number(value))
      .filter((value) => Number.isInteger(value) && value > 0);
    if (!values.length) return { value: null, warning: "truth_count is unavailable" };
    const unique = [...new Set(values)].sort((a, b) => a - b);
    return {
      value: unique[0],
      warning: unique.length > 1 ? `truth_count differs; using minimum value ${unique[0]}` : ""
    };
  }

  function rankOverlapForMaps(leftMap, rightMap, k) {
    if (!Number.isInteger(k) || k <= 0) return { status: "not_available", value: null, warning: "truth_count is unavailable" };
    const leftTop = topKSet(leftMap, k);
    const rightTop = topKSet(rightMap, k);
    let overlap = 0;
    for (const key of leftTop) {
      if (rightTop.has(key)) overlap += 1;
    }
    return { status: "ok", value: 1 - (overlap / k), warning: "" };
  }

  function consecutiveDistanceBadges(networks, scoreMaps, metric, metricsByKey, level) {
    if (networks.length < 2) return "";
    const truthCount = metric === "rank_overlap_distance_at_truth_count"
      ? truthCountForNetworks(networks, metricsByKey, level)
      : { value: null, warning: "" };
    return `
      <div class="distance-badges">
        ${networks.slice(0, -1).map((network, idx) => {
          const next = networks[idx + 1];
          const leftMap = scoreMaps.get(network.network_id) || new Map();
          const rightMap = scoreMaps.get(next.network_id) || new Map();
          const result = metric === "rank_overlap_distance_at_truth_count"
            ? rankOverlapForMaps(leftMap, rightMap, truthCount.value)
            : weightedJaccardForMaps(leftMap, rightMap);
          const warning = [result.warning, idx === 0 ? truthCount.warning : ""].filter(Boolean).join("; ");
          return `
            <span class="distance-badge ${result.status === "ok" ? "" : "status-not_available"}" title="${escapeHtml([warning, 'Lower values indicate more similar networks.'].filter(Boolean).join(' '))}">
              <span class="distance-kind">${escapeHtml(distanceMetricLabel(metric))}</span>
              <span class="distance-pair">${escapeHtml(comparisonNetworkLabel(network))} → ${escapeHtml(comparisonNetworkLabel(next))}</span>
              <strong>${result.status === "ok" ? formatValue(result.value) : "N/A"}</strong>
            </span>
          `;
        }).join("")}
      </div>
    `;
  }

  function buildComparableEdgeRows(networks, edgeRowsByNetwork, level) {
    const rawRows = networks.map((network) => [...(edgeRowsByNetwork.get(network.network_id)?.values() || [])]);
    const commonNodes = intersectSets(rawRows.map(nodesForRows));
    if (!commonNodes.size) {
      return {
        rows: [],
        scoreMaps: new Map(),
        commonGenes: 0,
        warning: "No common genes across selected networks for this level."
      };
    }
    const scoreMaps = new Map();
    const rowMaps = new Map();
    for (const network of networks) {
      const rows = (edgeRowsByNetwork.get(network.network_id)?.values() || []);
      const filtered = [...rows].filter((row) => rowInNodes(row, commonNodes));
      const scoreMap = new Map();
      const rowMap = new Map();
      for (const row of filtered) {
        const key = edgeDifferenceKey(row, level);
        const value = signedValue(row, level);
        if (!key) continue;
        if (keepEdgeDifferenceRow(rowMap.get(key), row, scoreMap.get(key), value)) {
          scoreMap.set(key, value);
          rowMap.set(key, row);
        }
      }
      scoreMaps.set(network.network_id, scoreMap);
      rowMaps.set(network.network_id, rowMap);
    }
    const edgeKeys = new Set();
    for (const scoreMap of scoreMaps.values()) {
      for (const key of scoreMap.keys()) edgeKeys.add(key);
    }
    const rows = [...edgeKeys].map((edgeKey) => {
      const values = networks.map((network) => scoreMaps.get(network.network_id)?.get(edgeKey) || 0);
      const raw = networks.map((network) => rowMaps.get(network.network_id)?.get(edgeKey) || null);
      const representative = raw.find(Boolean);
      return {
        edgeKey,
        edgeLabel: edgeDifferenceLabel(representative, edgeKey, level),
        values,
        raw,
        variance: variance(values)
      };
    }).sort((a, b) => (b.variance - a.variance) || a.edgeLabel.localeCompare(b.edgeLabel));
    return { rows, scoreMaps, commonGenes: commonNodes.size, warning: "" };
  }

  function renderEdgeDifferenceTable(level, networks, edgeRows, truncated) {
    if (!edgeRows.length) return '<div class="empty">No comparable edges for the selected ordered tools.</div>';
    const headCells = ['<th>Edge</th>'];
    networks.forEach((network, idx) => {
      if (idx > 0) headCells.push(`<th>Δ ${escapeHtml(comparisonNetworkLabel(networks[idx - 1]))}→${escapeHtml(comparisonNetworkLabel(network))}</th>`);
      headCells.push(`<th>${escapeHtml(comparisonNetworkLabel(network))}</th>`);
    });
    headCells.push('<th>Variance</th>');
    const body = edgeRows.slice(0, maxEdgeDifferenceRows).map((edge) => {
      const cells = [`<td title="${escapeHtml(edge.edgeKey)}">${escapeHtml(edge.edgeLabel || edge.edgeKey)}</td>`];
      networks.forEach((_network, idx) => {
        if (idx > 0) {
          const delta = edge.values[idx] - edge.values[idx - 1];
          const signChanged = level === "signed" && edge.values[idx] * edge.values[idx - 1] < 0;
          const deltaClass = delta > 0 ? "delta-positive" : (delta < 0 ? "delta-negative" : "");
          cells.push(`<td class="${deltaClass} ${signChanged ? "sign-change" : ""}">${delta >= 0 ? "+" : ""}${formatValue(delta)}</td>`);
        }
        cells.push(`<td>${escapeHtml(displayScore(edge.raw[idx], level))}</td>`);
      });
      cells.push(`<td>${formatValue(edge.variance)}</td>`);
      return `<tr>${cells.join("")}</tr>`;
    }).join("");
    return `
      ${truncated ? `<div class="subtle edge-limit-note">Showing top ${maxEdgeDifferenceRows} variable edges.</div>` : ""}
      <div class="edge-diff-table-wrap">
        <table class="edge-diff"><thead><tr>${headCells.join("")}</tr></thead><tbody>${body}</tbody></table>
      </div>
    `;
  }

  function edgeChartColor(index) {
    const palette = ["#0369a1", "#047857", "#b45309", "#7c3aed", "#be123c", "#0f766e"];
    return palette[index % palette.length];
  }

  function renderPairScatter(networks, edgeRows, level) {
    if (networks.length !== 2 || edgeRows.length === 0) return "";
    const rows = edgeRows.slice(0, 300);
    const values = rows.flatMap((edge) => edge.values);
    const min = Math.min(0, ...values);
    const max = Math.max(1, ...values);
    const span = max - min || 1;
    const width = 420;
    const height = 330;
    const pad = 46;
    const scaleX = (value) => pad + ((value - min) / span) * (width - (pad * 2));
    const scaleY = (value) => height - pad - ((value - min) / span) * (height - (pad * 2));
    const diagonalStart = scaleX(min);
    const diagonalEnd = scaleX(max);
    return `
      <section class="edge-chart-card">
        <div class="panel-title">
          <h3>Edge Score Scatter</h3>
          <span class="subtle">top ${rows.length} variable edges</span>
        </div>
        <svg class="edge-scatter" viewBox="0 0 ${width} ${height}" role="img" aria-label="Edge score scatter">
          <line class="edge-chart-axis" x1="${pad}" x2="${width - pad}" y1="${height - pad}" y2="${height - pad}"></line>
          <line class="edge-chart-axis" x1="${pad}" x2="${pad}" y1="${pad}" y2="${height - pad}"></line>
          <line class="edge-chart-diagonal" x1="${diagonalStart}" y1="${scaleY(min)}" x2="${diagonalEnd}" y2="${scaleY(max)}"></line>
          <text class="edge-chart-label" x="${width / 2}" y="${height - 10}" text-anchor="middle">${escapeHtml(comparisonNetworkLabel(networks[0]))}</text>
          <text class="edge-chart-label" transform="translate(14 ${height / 2}) rotate(-90)" text-anchor="middle">${escapeHtml(comparisonNetworkLabel(networks[1]))}</text>
          ${rows.map((edge) => {
            const x = scaleX(edge.values[0]);
            const y = scaleY(edge.values[1]);
            const signChanged = level === "signed" && edge.values[0] * edge.values[1] < 0;
            return `
              <circle class="edge-point ${signChanged ? "sign-change-point" : ""}" cx="${x}" cy="${y}" r="4">
                <title>${escapeHtml(edge.edgeLabel || edge.edgeKey)} | ${formatValue(edge.values[0])} → ${formatValue(edge.values[1])}</title>
              </circle>
            `;
          }).join("")}
        </svg>
      </section>
    `;
  }

  function renderSlopeChart(networks, edgeRows) {
    if (networks.length < 3 || networks.length > 6 || edgeRows.length === 0) return "";
    const rows = edgeRows.slice(0, 20);
    const values = rows.flatMap((edge) => edge.values);
    const min = Math.min(0, ...values);
    const max = Math.max(1, ...values);
    const span = max - min || 1;
    const width = 460;
    const height = 340;
    const padX = 52;
    const padY = 32;
    const step = networks.length > 1 ? (width - (padX * 2)) / (networks.length - 1) : 0;
    const scaleX = (idx) => padX + (idx * step);
    const scaleY = (value) => height - padY - ((value - min) / span) * (height - (padY * 2));
    return `
      <section class="edge-chart-card">
        <div class="panel-title">
          <h3>Edge Score Trajectories</h3>
          <span class="subtle">top ${rows.length} variable edges</span>
        </div>
        <svg class="edge-slope" viewBox="0 0 ${width} ${height}" role="img" aria-label="Edge score slope chart">
          ${networks.map((network, idx) => {
            const x = scaleX(idx);
            return `
              <line class="edge-chart-axis light" x1="${x}" x2="${x}" y1="${padY}" y2="${height - padY}"></line>
              <text class="edge-chart-label" x="${x}" y="${height - 9}" text-anchor="middle">${escapeHtml(network.tool_id)}</text>
            `;
          }).join("")}
          ${rows.map((edge, edgeIdx) => {
            const points = edge.values.map((value, idx) => `${scaleX(idx)},${scaleY(value)}`).join(" ");
            const color = edgeChartColor(edgeIdx);
            return `
              <polyline class="edge-slope-line" points="${points}" style="stroke:${color}">
                <title>${escapeHtml(edge.edgeLabel || edge.edgeKey)} | ${edge.values.map(formatValue).join(" → ")}</title>
              </polyline>
              ${edge.values.map((value, idx) => `<circle class="edge-slope-dot" cx="${scaleX(idx)}" cy="${scaleY(value)}" r="3" style="fill:${color}"></circle>`).join("")}
            `;
          }).join("")}
        </svg>
      </section>
    `;
  }

  function renderEdgeDifferenceChart(level, networks, edgeRows) {
    if (networks.length === 2) return renderPairScatter(networks, edgeRows, level);
    if (networks.length >= 3 && networks.length <= 6) return renderSlopeChart(networks, edgeRows);
    return "";
  }

  function renderEdgeDifferenceBody(level, networks, edgeRows, truncated) {
    const table = renderEdgeDifferenceTable(level, networks, edgeRows, truncated);
    const chart = renderEdgeDifferenceChart(level, networks, edgeRows);
    if (!chart) return table;
    return `<div class="edge-diff-layout"><div>${table}</div>${chart}</div>`;
  }

  function renderEdgeDifferences(root, report) {
    const selected = selectedNetworksForRoot(root);
    const target = root.querySelector('[data-role="edge-difference-levels"]');
    const summary = root.querySelector('[data-role="edge-selection-summary"]');
    if (!target || !summary) return;
    if (selected.length < 2) {
      summary.textContent = "Select at least two tools.";
      target.innerHTML = '<div class="empty">Select two or more ordered tools from the source cards to compare edge scores.</div>';
      return;
    }
    const state = selectedState(root);
    const networkIndex = Array.isArray(report.network_index) ? report.network_index : [];
    const indexByComparisonKey = new Map(networkIndex.map((row) => [comparisonKey(row, row.level), row]));
    const edgeRowsByNetwork = edgeScoresByNetwork(report);
    const metricsByKey = metricMap(Array.isArray(report.evaluation_metrics) ? report.evaluation_metrics : []);
    const metricValues = networkIndex
      .map((network) => metricForNetwork(network, state.metricKey, metricsByKey))
      .filter((value) => value !== null);
    const scale = metricScale(metricValues);
    summary.textContent = selected.map((item, idx) => `${idx + 1}. ${item.source_id}:${item.tool_id} · ${contextLabel(item.context)}`).join(" → ");
    target.innerHTML = levels.map((level) => {
      const networks = selectedNetworkInstances(selected, level, indexByComparisonKey);
      if (networks.length !== selected.length) {
        return `
          <article class="edge-level-card">
            <div class="level-head"><h3>${escapeHtml(level)}</h3></div>
            <div class="empty">One or more selected tools do not have a ${escapeHtml(level)} network.</div>
          </article>
        `;
      }
      const comparable = buildComparableEdgeRows(networks, edgeRowsByNetwork, level);
      const truncated = comparable.rows.length > maxEdgeDifferenceRows;
      return `
        <article class="edge-level-card">
          <div class="level-head">
            <div>
              <h3>${escapeHtml(level[0].toUpperCase() + level.slice(1))}</h3>
              <div class="subtle">${comparable.commonGenes} common genes · ${comparable.rows.length} comparable edges</div>
            </div>
            <div class="badges">
              <span class="badge">${escapeHtml(state.distanceMetric || "weighted_jaccard_distance")}</span>
            </div>
          </div>
          ${orderHeader(networks, state.metricKey, metricsByKey, scale)}
          ${consecutiveDistanceBadges(networks, comparable.scoreMaps, state.distanceMetric, metricsByKey, level)}
          ${comparable.warning ? `<div class="empty">${escapeHtml(comparable.warning)}</div>` : renderEdgeDifferenceBody(level, networks, comparable.rows, truncated)}
        </article>
      `;
    }).join("");
  }

  function renderDistancesTable(root, distances) {
    const search = root.querySelector('[data-role="distance-search"]');
    const table = root.querySelector('[data-role="distances-table"]');
    if (!search || !table) return;
    const query = search.value.trim().toLowerCase();
    const rows = distances.filter((row) => !query || distanceColumns.some((column) => String(row[column] ?? "").toLowerCase().includes(query)));
    const head = `<thead><tr>${distanceColumns.map((column) => `<th>${escapeHtml(column)}</th>`).join("")}</tr></thead>`;
    const body = `<tbody>${rows.map((row) => `
      <tr>
        ${distanceColumns.map((column) => {
          const value = row[column];
          const display = column === "distance" && value !== "" ? formatValue(value) : (value ?? "");
          const cls = column === "status" ? ` class="status-${escapeHtml(String(value))}"` : "";
          return `<td${cls}>${escapeHtml(display)}</td>`;
        }).join("")}
      </tr>
    `).join("")}</tbody>`;
    table.innerHTML = head + body;
  }

  function updateSelectedNetworks(root, selectedNetworks) {
    const state = rootStates.get(root);
    if (!state) return;
    state.selectedNetworks = Array.isArray(selectedNetworks) ? selectedNetworks.slice() : [];
    renderEdgeDifferences(root, state.report);
  }

  function updateSource(root, sourceId) {
    const state = rootStates.get(root);
    if (!state) return;
    const sourceIds = Array.isArray(state.sourceIds) ? state.sourceIds : [];
    const nextSourceId = sourceIds.includes(sourceId) ? sourceId : (sourceIds[0] || "");
    const sourceSelect = root.querySelector('[data-role="source-select"]');
    if (sourceSelect && nextSourceId) sourceSelect.value = nextSourceId;
    state.sourceId = nextSourceId;
    refreshContextOptions(root, state.report);
    renderLevels(root, state.report);
  }

  function configureMetricSelect(root, report) {
    const metricSelect = root.querySelector('[data-role="metric-select"]');
    if (!metricSelect) return;
    metricSelect.innerHTML = "";
    const metricsAvailable = new Set(Array.isArray(report.metrics_available) ? report.metrics_available : []);
    const metrics = metricDefs.filter((metric) => !metric.key || metricsAvailable.has(metric.key));
    for (const metric of metrics) {
      const option = document.createElement("option");
      option.value = metric.key;
      option.textContent = metric.label;
      metricSelect.appendChild(option);
    }
  }

  function distanceMetricsForReport(report) {
    const distances = Array.isArray(report.distances) ? report.distances : [];
    return orderedDistanceMetrics(
      Array.isArray(report.distances_available) && report.distances_available.length
        ? report.distances_available
        : distances.map((row) => row.distance_metric).filter(Boolean)
    );
  }

  function sourceIdsForReport(report) {
    return (Array.isArray(report.sources) ? report.sources : [])
      .map((source) => source.source_id)
      .filter(Boolean);
  }

  function renderDistanceMaps(root, report, options = {}) {
    const safeReport = report || {};
    const sources = Array.isArray(safeReport.sources) ? safeReport.sources : [];
    const sourceIds = sourceIdsForReport(safeReport);
    const sourceLabels = new Map(sourceIds.map((sourceId) => [sourceId, sourceLabel(sourceId, sources)]));
    const distances = Array.isArray(safeReport.distances) ? safeReport.distances : [];
    const distanceMetrics = distanceMetricsForReport(safeReport);
    const selectedSource = sourceIds.includes(options.sourceId) ? options.sourceId : (sourceIds[0] || "");
    rootStates.set(root, {
      report: safeReport,
      selectedNetworks: [],
      sourceIds,
      sourceId: selectedSource,
      context: "",
      distanceMetric: distanceMetrics[0] || "",
      metricKey: ""
    });
    root.innerHTML = buildDistanceMapsShell(options);

    const sourceSelect = root.querySelector('[data-role="source-select"]');
    if (sourceSelect) {
      setOptions(sourceSelect, sourceIds, sourceLabels);
      if (selectedSource) sourceSelect.value = selectedSource;
    }
    setOptions(root.querySelector('[data-role="distance-select"]'), distanceMetrics, null);
    configureMetricSelect(root, safeReport);

    refreshContextOptions(root, safeReport);
    renderSummary(root, safeReport);
    renderLevels(root, safeReport);
    renderDistancesTable(root, distances);

    sourceSelect?.addEventListener("change", () => updateSource(root, sourceSelect.value));
    root.querySelector('[data-role="context-select"]')?.addEventListener("change", () => renderLevels(root, safeReport));
    root.querySelector('[data-role="distance-select"]')?.addEventListener("change", () => renderLevels(root, safeReport));
    root.querySelector('[data-role="metric-select"]')?.addEventListener("change", () => renderLevels(root, safeReport));
    root.querySelector('[data-role="distance-search"]')?.addEventListener("input", () => renderDistancesTable(root, distances));
  }

  function renderEdgeDifferenceView(root, report, options = {}) {
    const safeReport = report || {};
    const distanceMetrics = distanceMetricsForReport(safeReport);
    rootStates.set(root, {
      report: safeReport,
      selectedNetworks: Array.isArray(options.selectedNetworks) ? options.selectedNetworks.slice() : [],
      sourceIds: sourceIdsForReport(safeReport),
      distanceMetric: distanceMetrics[0] || "",
      metricKey: ""
    });
    root.innerHTML = buildEdgeDifferencesShell(options);
    setOptions(root.querySelector('[data-role="distance-select"]'), distanceMetrics, null);
    configureMetricSelect(root, safeReport);
    renderEdgeDifferences(root, safeReport);
    root.querySelector('[data-role="distance-select"]')?.addEventListener("change", () => renderEdgeDifferences(root, safeReport));
    root.querySelector('[data-role="metric-select"]')?.addEventListener("change", () => renderEdgeDifferences(root, safeReport));
  }

  function render(root, report, options = {}) {
    const safeReport = report || {};
    const sources = Array.isArray(safeReport.sources) ? safeReport.sources : [];
    const sourceIds = sources.map((source) => source.source_id).filter(Boolean);
    const sourceLabels = new Map(sourceIds.map((sourceId) => [sourceId, sourceLabel(sourceId, sources)]));
    const distances = Array.isArray(safeReport.distances) ? safeReport.distances : [];
    const distanceMetrics = distanceMetricsForReport(safeReport);
    rootStates.set(root, {
      report: safeReport,
      selectedNetworks: Array.isArray(options.selectedNetworks) ? options.selectedNetworks.slice() : [],
      sourceIds,
      sourceId: sourceIds[0] || "",
      distanceMetric: distanceMetrics[0] || "",
      metricKey: ""
    });
    root.innerHTML = buildShell();
    setOptions(root.querySelector('[data-role="source-select"]'), sourceIds, sourceLabels);
    setOptions(root.querySelector('[data-role="distance-select"]'), distanceMetrics, null);
    configureMetricSelect(root, safeReport);

    refreshContextOptions(root, safeReport);
    renderSummary(root, safeReport);
    renderLevels(root, safeReport);
    renderEdgeDifferences(root, safeReport);
    renderDistancesTable(root, distances);

    root.querySelector('[data-role="source-select"]').addEventListener("change", () => {
      refreshContextOptions(root, safeReport);
      renderLevels(root, safeReport);
      renderEdgeDifferences(root, safeReport);
    });
    root.querySelector('[data-role="context-select"]').addEventListener("change", () => {
      renderLevels(root, safeReport);
      renderEdgeDifferences(root, safeReport);
    });
    root.querySelector('[data-role="distance-select"]').addEventListener("change", () => {
      renderLevels(root, safeReport);
      renderEdgeDifferences(root, safeReport);
    });
    root.querySelector('[data-role="metric-select"]').addEventListener("change", () => {
      renderLevels(root, safeReport);
      renderEdgeDifferences(root, safeReport);
    });
    root.querySelector('[data-role="distance-search"]').addEventListener("input", () => renderDistancesTable(root, distances));
  }

  window.AndreaComparisonView = {
    render,
    renderDistanceMaps,
    renderEdgeDifferenceView,
    updateSelectedNetworks,
    updateSource,
    levels: levels.slice()
  };

  document.addEventListener("DOMContentLoaded", () => {
    const data = document.getElementById("comparison-data");
    const root = document.getElementById("comparison-view-root");
    if (!data || !root) return;
    window.AndreaComparisonView.render(root, JSON.parse(data.textContent));
  });
}());
