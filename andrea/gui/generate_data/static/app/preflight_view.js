export function createPreflightView({
  $,
  scenarioSemanticLabel,
  scenarioTemplateRequiredExtras,
  selectedScenarioTemplateId,
}) {
  function renderPreflightSummary(report) {
    const root = $("preflight-report-view");
    if (!report) {
      root.textContent = "No preflight report yet.";
      return;
    }
    root.innerHTML = "";
    const summary = report.catalog_summary || {};
    const scenario = report.scenario || {};
    const organism = scenario.organism && typeof scenario.organism === "object" ? scenario.organism : {};
    const taxonRaw = organism.ncbi_taxon_id;
    const taxonId = taxonRaw === null || taxonRaw === undefined ? "-" : String(taxonRaw);
    const issueCounts = countPreflightIssues(report);

    appendPreflightSummaryBand(root, "Scenario", [
      { label: "Scenario", value: scenario.id || "-" },
      { label: "Scenario Type", value: scenarioSemanticLabel(scenario) },
      {
        label: "Organism",
        value: organism.taxonomic_group || "synthetic",
        detail: `NCBI taxon ${taxonId}`,
      },
    ]);

    appendPreflightExtrasBand(root, scenario);

    appendPreflightSummaryBand(root, "Simulator Catalog", [
      { label: "Total", value: summary.total ?? 0 },
      { label: "Eligible", value: summary.eligible ?? 0, tone: "ok" },
      { label: "Warning", value: summary.warning ?? 0, tone: Number(summary.warning || 0) ? "warning" : "" },
      { label: "Blocked", value: summary.blocked ?? 0, tone: Number(summary.blocked || 0) ? "blocked" : "" },
      { label: "Issues", value: issueCounts.total, detail: `${issueCounts.warn} warn · ${issueCounts.block} block` },
    ]);

    const rawDetails = document.createElement("details");
    rawDetails.className = "preflight-list";
    const summaryNode = document.createElement("summary");
    summaryNode.textContent = "Raw preflight_report.json";
    rawDetails.appendChild(summaryNode);
    const pre = document.createElement("pre");
    pre.textContent = JSON.stringify(report, null, 2);
    rawDetails.appendChild(pre);
    root.appendChild(rawDetails);
  }

  function countPreflightIssues(report) {
    const counts = { warn: 0, block: 0, total: 0 };
    const entries = [
      ...(Array.isArray(report?.eligible) ? report.eligible : []),
      ...(Array.isArray(report?.warning) ? report.warning : []),
      ...(Array.isArray(report?.blocked) ? report.blocked : []),
    ];
    for (const entry of entries) {
      for (const issue of entry.issues || []) {
        const severity = String(issue?.severity || "").trim();
        if (severity === "warn") {
          counts.warn += 1;
        } else if (severity === "block") {
          counts.block += 1;
        }
      }
    }
    counts.total = counts.warn + counts.block;
    return counts;
  }

  function appendPreflightMetricCard(parent, { label, value, detail = "", tone = "" }) {
    const card = document.createElement("article");
    card.className = `preflight-kpi${tone ? ` ${tone}` : ""}`;
    const title = document.createElement("strong");
    title.textContent = label;
    const main = document.createElement("span");
    main.textContent = String(value ?? "-");
    card.append(title, main);
    if (detail) {
      const small = document.createElement("small");
      small.textContent = detail;
      card.appendChild(small);
    }
    parent.appendChild(card);
  }

  function appendPreflightSummaryBand(parent, title, cards) {
    const section = document.createElement("section");
    section.className = "preflight-summary-section";
    const heading = document.createElement("h4");
    heading.textContent = title;
    const grid = document.createElement("div");
    grid.className = "preflight-grid";
    for (const card of cards) {
      appendPreflightMetricCard(grid, card);
    }
    section.append(heading, grid);
    parent.appendChild(section);
  }

  function appendPreflightExtrasBand(parent, scenario) {
    const section = document.createElement("section");
    section.className = "preflight-summary-section";
    const heading = document.createElement("h4");
    heading.textContent = "Standardized Extras";
    const grid = document.createElement("div");
    grid.className = "preflight-extra-grid";
    const requested = normalizedExtraList(scenario.requested_extras || []);
    const required = normalizedExtraList(scenarioTemplateRequiredExtras(selectedScenarioTemplateId()));
    const requiredSet = new Set(required);
    const selected = requested.filter((item) => !requiredSet.has(item));
    appendPreflightExtraSet(grid, "Selected", selected);
    appendPreflightExtraSet(
      grid,
      "Added automatically",
      required,
      required.length ? "Required by the selected benchmark scenario." : ""
    );
    section.append(heading, grid);
    parent.appendChild(section);
  }

  function normalizedExtraList(items) {
    return [
      ...new Set(
        (Array.isArray(items) ? items : [])
          .map((item) => String(item || "").trim())
          .filter(Boolean)
      ),
    ].sort();
  }

  function appendPreflightExtraSet(parent, title, items, note = "") {
    const block = document.createElement("div");
    block.className = "preflight-extra-set";
    const head = document.createElement("div");
    head.className = "preflight-extra-head";
    const label = document.createElement("strong");
    label.textContent = title;
    const count = document.createElement("span");
    count.textContent = String(items.length);
    head.append(label, count);
    const chips = document.createElement("div");
    chips.className = "preflight-chip-row";
    if (!items.length) {
      const empty = document.createElement("span");
      empty.className = "preflight-chip muted";
      empty.textContent = "-";
      chips.appendChild(empty);
    } else {
      for (const item of items) {
        const chip = document.createElement("span");
        chip.className = "preflight-chip";
        chip.textContent = String(item);
        chips.appendChild(chip);
      }
    }
    block.append(head, chips);
    if (note) {
      const noteNode = document.createElement("small");
      noteNode.className = "preflight-extra-note";
      noteNode.textContent = note;
      block.appendChild(noteNode);
    }
    parent.appendChild(block);
  }

  return {
    renderPreflightSummary,
  };
}
