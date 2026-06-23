import { $ } from "../core/dom.js";
import { state } from "../core/state.js";

let planningProgressTimer = null;
let planningProgressJob = null;

export function stopPlanningProgress() {
  if (planningProgressTimer) {
    window.clearInterval(planningProgressTimer);
    planningProgressTimer = null;
  }
  planningProgressJob = null;
}

export function resetPlanView(message) {
  stopPlanningProgress();
  state.lastPlan = null;
  $("plan-summary").classList.remove("planning-progress-card", "is-over-limit");
  $("plan-summary").textContent = message || "No plan loaded yet.";
  $("plan-waves").innerHTML = "";
}

export function renderPlanFailure(job = {}) {
  stopPlanningProgress();
  state.lastPlan = null;

  const summary = $("plan-summary");
  const wavesRoot = $("plan-waves");
  if (!summary || !wavesRoot) {
    return;
  }

  summary.classList.remove("planning-progress-card", "is-over-limit");
  summary.replaceChildren();

  const title = document.createElement("strong");
  title.textContent = "Planning failed";
  const detail = document.createElement("div");
  detail.className = "planning-progress-detail";
  detail.textContent = String(
    job.error ||
      job.progress_detail ||
      "The execution plan could not be generated. Review the selected run configuration."
  );
  summary.append(title, detail);

  wavesRoot.innerHTML = "";
  const hint = document.createElement("div");
  hint.className = "muted-box warning-box";
  hint.textContent =
    "Fix the blocked run configuration in Selected Runs, then generate the plan again.";
  wavesRoot.appendChild(hint);
}

function elapsedSecondsFromIso(value) {
  const startedAt = Date.parse(String(value || ""));
  if (!Number.isFinite(startedAt)) {
    return 0;
  }
  return Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
}

export function renderPlanningProgress(job = {}) {
  planningProgressJob = job;
  if (!planningProgressTimer) {
    planningProgressTimer = window.setInterval(() => {
      if (planningProgressJob) {
        renderPlanningProgress(planningProgressJob);
      }
    }, 1000);
  }

  const summary = $("plan-summary");
  const wavesRoot = $("plan-waves");
  if (!summary || !wavesRoot) {
    return;
  }
  const elapsed = elapsedSecondsFromIso(job.started_at);
  const limitRaw = Number(job.planner_time_limit_seconds || 0);
  const limit = Number.isFinite(limitRaw) && limitRaw > 0 ? Math.round(limitRaw) : null;
  const planner = String(job.planner || "auto").toLowerCase();
  const usesCpSatBudget = planner !== "heuristic";
  const overLimit = Boolean(usesCpSatBudget && limit && elapsed >= limit);

  summary.replaceChildren();
  summary.classList.toggle("planning-progress-card", true);
  summary.classList.toggle("is-over-limit", overLimit);

  const head = document.createElement("div");
  head.className = "planning-progress-head";
  const title = document.createElement("strong");
  title.textContent = String(job.progress_label || "Planning execution");
  const counter = document.createElement("span");
  counter.textContent = usesCpSatBudget && limit ? `${elapsed}s / ${limit}s` : `${elapsed}s`;
  head.append(title, counter);

  const detail = document.createElement("div");
  detail.className = "planning-progress-detail";
  if (planner === "heuristic") {
    detail.textContent =
      "The heuristic planner is building execution waves directly. The CP-SAT time limit is not used in this mode.";
  } else if (overLimit) {
    detail.textContent =
      "The CP-SAT search budget has been consumed. ANDREA will use a feasible CP-SAT plan if one was found, otherwise it will fall back to the heuristic planner.";
  } else {
    detail.textContent = String(
      job.progress_detail ||
        "ANDREA is selecting tool resources and scheduling execution waves. A larger CP-SAT budget can improve the plan and reduce later compute time."
    );
  }

  summary.append(head, detail);
  wavesRoot.innerHTML = "";
}

export function renderPlan(plan) {
  if (!plan || typeof plan !== "object") {
    resetPlanView("No plan available for this job yet.");
    state.lastPlan = null;
    return;
  }
  stopPlanningProgress();
  state.lastPlan = plan;
  $("plan-summary").classList.remove("planning-progress-card", "is-over-limit");

  const planner = plan.planner || {};
  const totals = plan.totals || {};
  const lines = [
    `run_id: ${plan.run_id || "-"}`,
    `planner: requested=${planner.requested || "-"}, used=${planner.used || "-"}`,
    `logical_runs_total: ${totals.logical_runs_total ?? "-"}`,
    `physical_tasks_total: ${totals.physical_tasks_total ?? totals.tasks_total ?? "-"}`,
    `waves_total: ${totals.waves_total ?? "-"}`,
    `threads_peak: ${totals.threads_peak ?? "-"}`,
    `ram_peak_gb: ${totals.ram_peak_gb ?? "-"}`,
    `eta_total_seconds: ${plan.eta_total_seconds ?? "-"}`,
  ];
  $("plan-summary").textContent = lines.join("\n");

  const wavesRoot = $("plan-waves");
  wavesRoot.innerHTML = "";
  const warnings = Array.isArray(plan.warnings)
    ? plan.warnings.map((item) => String(item || "").trim()).filter(Boolean)
    : [];
  if (warnings.length) {
    const warningBox = document.createElement("div");
    warningBox.className = "muted-box warning-box";
    warningBox.textContent = warnings.slice(0, 12).join("\n");
    if (warnings.length > 12) {
      const more = document.createElement("div");
      more.textContent = `... and ${warnings.length - 12} more warning(s)`;
      warningBox.appendChild(more);
    }
    wavesRoot.appendChild(warningBox);
  }
  const logicalRuns = Array.isArray(plan.runs) ? plan.runs : [];
  const waves = Array.isArray(plan.waves) ? plan.waves : [];
  if (!logicalRuns.length && !waves.length) {
    wavesRoot.textContent = "This plan has no waves.";
    return;
  }

  if (logicalRuns.length) {
    const runsCard = document.createElement("article");
    runsCard.className = "wave-card";

    const runsHead = document.createElement("div");
    runsHead.className = "wave-head";
    runsHead.innerHTML =
      `<span class="wave-title">Configured Runs</span>` +
      `<span>runs=${logicalRuns.length}</span>`;
    runsCard.appendChild(runsHead);

    const runsTable = document.createElement("table");
    runsTable.className = "wave-table";
    runsTable.innerHTML =
      "<thead><tr>" +
      "<th>run_id</th><th>tool_id</th><th>mode</th><th>physical_tasks</th><th>eta_s</th>" +
      "</tr></thead>";
    const runsBody = document.createElement("tbody");
    for (const run of logicalRuns) {
      const tr = document.createElement("tr");
      const cells = [
        run.run_id || "-",
        run.tool_id || "-",
        run?.execution?.mode || "-",
        run.physical_tasks_total ?? "-",
        run.eta_seconds ?? "-",
      ];
      for (const value of cells) {
        const td = document.createElement("td");
        td.textContent = String(value);
        tr.appendChild(td);
      }
      runsBody.appendChild(tr);
    }
    runsTable.appendChild(runsBody);
    runsCard.appendChild(runsTable);
    wavesRoot.appendChild(runsCard);
  }

  if (!waves.length) {
    return;
  }

  const wavesDetails = document.createElement("details");
  wavesDetails.className = "muted-box";
  if (!logicalRuns.length) {
    wavesDetails.open = true;
  }
  const wavesSummary = document.createElement("summary");
  wavesSummary.textContent = `Internal waves (${waves.length})`;
  wavesDetails.appendChild(wavesSummary);

  const wavesHost = document.createElement("div");
  wavesHost.className = "plan-waves";

  for (const wave of waves) {
    const card = document.createElement("article");
    card.className = "wave-card";

    const head = document.createElement("div");
    head.className = "wave-head";
    const headParts = [
      { label: `Wave ${wave.index}`, cls: "wave-title" },
      { label: `tasks=${Array.isArray(wave.tasks) ? wave.tasks.length : 0}` },
      { label: `cores=${wave.threads_used ?? "-"}` },
      { label: `ram=${wave.ram_gb_used ?? "-"}GB` },
      { label: `eta=${wave.eta_seconds ?? "-"}s` },
      { label: `window=[${wave.eta_start_seconds ?? "-"}, ${wave.eta_end_seconds ?? "-"}]` },
    ];
    for (const part of headParts) {
      const span = document.createElement("span");
      if (part.cls) {
        span.className = part.cls;
      }
      span.textContent = part.label;
      head.appendChild(span);
    }

    const table = document.createElement("table");
    table.className = "wave-table";
    table.innerHTML =
      "<thead><tr>" +
      "<th>run_id</th><th>task_id</th><th>group</th><th>threads</th><th>ram_gb</th><th>eta_s</th><th>source</th><th>note</th>" +
      "</tr></thead>";

    const tbody = document.createElement("tbody");
    const tasks = Array.isArray(wave.tasks) ? wave.tasks : [];
    for (const task of tasks) {
      const tr = document.createElement("tr");
      const cells = [
        task.run_id || task.tool_id || "-",
        task.tool_id || "-",
        task.group_label || "-",
        task.threads ?? "-",
        task.ram_gb ?? "-",
        task.eta_seconds ?? "-",
        task.eta_source || "-",
        task.note || "",
      ];
      for (const value of cells) {
        const td = document.createElement("td");
        td.textContent = String(value);
        tr.appendChild(td);
      }
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);

    card.appendChild(head);
    card.appendChild(table);
    wavesHost.appendChild(card);
  }
  wavesDetails.appendChild(wavesHost);
  wavesRoot.appendChild(wavesDetails);
}

export function renderPlanInlinePreview(plan, virtualPath) {
  const previewRoot = $("file-preview");
  previewRoot.innerHTML = "";
  $("file-preview-header").textContent = `${virtualPath} · plan`;

  if (!plan || typeof plan !== "object") {
    const pre = document.createElement("pre");
    pre.textContent = "No plan is available for this job yet.";
    previewRoot.appendChild(pre);
    return;
  }

  const summary = document.createElement("div");
  summary.className = "muted-box plan-inline-summary";
  summary.textContent = $("plan-summary").textContent || "Plan loaded.";
  previewRoot.appendChild(summary);

  const wavesHost = document.createElement("div");
  wavesHost.className = "inline-plan-waves";
  const warnings = Array.isArray(plan.warnings)
    ? plan.warnings.map((item) => String(item || "").trim()).filter(Boolean)
    : [];
  if (warnings.length) {
    const warningBox = document.createElement("div");
    warningBox.className = "muted-box warning-box";
    warningBox.textContent = warnings.slice(0, 12).join("\n");
    previewRoot.appendChild(warningBox);
  }
  const logicalRuns = Array.isArray(plan.runs) ? plan.runs : [];
  const waves = Array.isArray(plan.waves) ? plan.waves : [];
  if (logicalRuns.length) {
    const runsCard = document.createElement("article");
    runsCard.className = "wave-card";
    const runsHead = document.createElement("div");
    runsHead.className = "wave-head";
    runsHead.innerHTML = `<span class="wave-title">Configured Runs</span><span>runs=${logicalRuns.length}</span>`;
    runsCard.appendChild(runsHead);
    const runsTable = document.createElement("table");
    runsTable.className = "wave-table";
    runsTable.innerHTML =
      "<thead><tr>" +
      "<th>run_id</th><th>tool_id</th><th>mode</th><th>physical_tasks</th><th>eta_s</th>" +
      "</tr></thead>";
    const runsBody = document.createElement("tbody");
    for (const run of logicalRuns) {
      const tr = document.createElement("tr");
      const cells = [
        run.run_id || "-",
        run.tool_id || "-",
        run?.execution?.mode || "-",
        run.physical_tasks_total ?? "-",
        run.eta_seconds ?? "-",
      ];
      for (const value of cells) {
        const td = document.createElement("td");
        td.textContent = String(value);
        tr.appendChild(td);
      }
      runsBody.appendChild(tr);
    }
    runsTable.appendChild(runsBody);
    runsCard.appendChild(runsTable);
    wavesHost.appendChild(runsCard);
  }
  if (!waves.length) {
    if (logicalRuns.length) {
      previewRoot.appendChild(wavesHost);
      return;
    }
    const pre = document.createElement("pre");
    pre.textContent = "This plan has no waves.";
    previewRoot.appendChild(pre);
    return;
  }
  for (const wave of waves) {
    const card = document.createElement("article");
    card.className = "wave-card";
    const head = document.createElement("div");
    head.className = "wave-head";
    head.innerHTML = `<span class="wave-title">Wave ${wave.index}</span><span>tasks=${Array.isArray(
      wave.tasks
    ) ? wave.tasks.length : 0}</span><span>cores=${wave.threads_used ?? "-"}</span><span>ram=${
      wave.ram_gb_used ?? "-"
    }GB</span>`;
    card.appendChild(head);
    const table = document.createElement("table");
    table.className = "wave-table";
    table.innerHTML =
      "<thead><tr>" +
      "<th>run_id</th><th>task_id</th><th>group</th><th>threads</th><th>ram_gb</th><th>eta_s</th><th>source</th><th>note</th>" +
      "</tr></thead>";
    const tbody = document.createElement("tbody");
    const tasks = Array.isArray(wave.tasks) ? wave.tasks : [];
    for (const task of tasks) {
      const tr = document.createElement("tr");
      const cells = [
        task.run_id || task.tool_id || "-",
        task.tool_id || "-",
        task.group_label || "-",
        task.threads ?? "-",
        task.ram_gb ?? "-",
        task.eta_seconds ?? "-",
        task.eta_source || "-",
        task.note || "",
      ];
      for (const value of cells) {
        const td = document.createElement("td");
        td.textContent = String(value);
        tr.appendChild(td);
      }
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    card.appendChild(table);
    wavesHost.appendChild(card);
  }
  previewRoot.appendChild(wavesHost);
}
