import { $ } from "../core/dom.js";

function appendMetricCard(parent, { label, value, detail = "", tone = "" }) {
  const card = document.createElement("article");
  card.className = `preflight-kpi${tone ? ` ${tone}` : ""}`;
  const strong = document.createElement("strong");
  strong.textContent = label;
  const span = document.createElement("span");
  span.textContent = String(value ?? "-");
  card.append(strong, span);
  if (detail) {
    const small = document.createElement("small");
    small.textContent = detail;
    card.appendChild(small);
  }
  parent.appendChild(card);
}

function countInputValidation(preflightReport) {
  const validation = preflightReport?.input_validation || {};
  const exprValidation = validation.expression_matrix || {};
  const extrasValidation = validation.extras || {};
  const counts = { ok: 0, missing: 0, warning: 0, error: 0, unknown: 0 };
  const increment = (statusRaw) => {
    const status = String(statusRaw || "unknown").trim().toLowerCase();
    if (status === "ok") {
      counts.ok += 1;
    } else if (status === "missing") {
      counts.missing += 1;
    } else if (status === "warning") {
      counts.warning += 1;
    } else if (status === "error" || status === "err" || status === "invalid") {
      counts.error += 1;
    } else {
      counts.unknown += 1;
    }
  };
  increment(exprValidation.status);
  for (const item of Object.values(extrasValidation)) {
    increment(item?.status);
  }
  return counts;
}

function appendSummaryBand(parent, title, cards) {
  const section = document.createElement("section");
  section.className = "preflight-summary-section";
  const heading = document.createElement("h4");
  heading.textContent = title;
  const grid = document.createElement("div");
  grid.className = "preflight-grid";
  for (const card of cards) {
    appendMetricCard(grid, card);
  }
  section.append(heading, grid);
  parent.appendChild(section);
}

export function updatePreflightSummary(preflightReport = null) {
  const viewEl = $("preflight-report-view");
  if (!viewEl) {
    return;
  }
  if (!preflightReport || !preflightReport.catalog) {
    viewEl.textContent = "No preflight report yet.";
    return;
  }
  viewEl.innerHTML = "";

  const dataset = preflightReport?.dataset || {};
  const organism = dataset.organism && typeof dataset.organism === "object" ? dataset.organism : {};
  const taxonomicGroup = String(organism.taxonomic_group || "-");
  const taxonRaw = organism.ncbi_taxon_id;
  const taxonId = taxonRaw === null || taxonRaw === undefined
    ? "-"
    : String(taxonRaw);
  appendSummaryBand(viewEl, "Dataset", [
    { label: "Dataset", value: String(preflightReport?.dataset?.dataset_id || "-") },
    {
      label: "Expression",
      value: `${preflightReport?.dataset?.genes ?? "-"} × ${preflightReport?.dataset?.columns ?? "-"}`,
      detail: "genes × columns",
    },
    { label: "Organism", value: taxonomicGroup, detail: `NCBI taxon ${taxonId}` },
  ]);

  const inputCounts = countInputValidation(preflightReport);
  appendSummaryBand(viewEl, "Input Validation", [
    { label: "Valid", value: inputCounts.ok, tone: "ok" },
    { label: "Missing", value: inputCounts.missing, tone: inputCounts.missing ? "warning" : "" },
    { label: "Warnings", value: inputCounts.warning, tone: inputCounts.warning ? "warning" : "" },
    { label: "Errors", value: inputCounts.error, tone: inputCounts.error ? "blocked" : "" },
  ]);

  const warningIssues = Array.isArray(preflightReport.issues)
    ? preflightReport.issues.filter((issue) => String(issue?.severity || "") === "warn")
    : [];
  const catalog = preflightReport.catalog || {};
  const eligible = Array.isArray(catalog.eligible) ? catalog.eligible.length : 0;
  const warning = Array.isArray(catalog.warning) ? catalog.warning.length : 0;
  const blocked = Array.isArray(catalog.blocked) ? catalog.blocked.length : 0;
  appendSummaryBand(viewEl, "Tool Eligibility Preview", [
    { label: "Accepted", value: eligible, tone: "ok" },
    { label: "Warning", value: warning, tone: warning ? "warning" : "" },
    { label: "Blocked", value: blocked, tone: blocked ? "blocked" : "" },
    { label: "Preflight warnings", value: warningIssues.length, tone: warningIssues.length ? "warning" : "" },
  ]);

  const rawDetails = document.createElement("details");
  rawDetails.className = "preflight-list";
  const summaryNode = document.createElement("summary");
  summaryNode.textContent = "Raw preflight_report.json";
  rawDetails.appendChild(summaryNode);
  const pre = document.createElement("pre");
  pre.textContent = JSON.stringify(preflightReport, null, 2);
  rawDetails.appendChild(pre);
  viewEl.appendChild(rawDetails);
}
