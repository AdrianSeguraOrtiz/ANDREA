import { $ } from "/static-common/app/core/dom.js?v=20260428a";
import { state } from "../core/state.js";
import {
  pushRuntimeFailureToasts,
  renderRuntimeProgress as renderCommonRuntimeProgress,
} from "/static-common/app/runtime/view.js?v=20260428a";

export {
  pushRuntimeFailureToasts,
};

const PHASE_LABELS = {
  planned: "Planned",
  verifying_inputs: "Verifying Inputs",
  preparing_runtime: "Preparing Runtime",
  running_tools: "Running Tools",
  collecting_results: "Collecting Results",
  finalizing_grouped: "Finalizing Grouped Runs",
  finalizing_group_aggregated: "Aggregating Column-Native Runs",
  merging_raw_networks: "Merging Raw Networks",
  normalizing_scores: "Normalizing Scores",
  exporting_artifacts: "Exporting Artifacts",
  writing_report: "Writing Report",
  completed: "Completed",
  completed_with_failures: "Completed With Failures",
  failed: "Failed",
};

const PHASE_MESSAGES = {
  planned: "Execution is planned and waiting to start.",
  verifying_inputs: "ANDREA is checking the frozen input files.",
  preparing_runtime: "ANDREA is preparing shared runtime inputs.",
  running_tools: "Inference tools are running inside their planned waves.",
  collecting_results: "ANDREA is collecting tool execution results.",
  finalizing_grouped: "ANDREA is combining grouped child outputs.",
  finalizing_group_aggregated: "ANDREA is aggregating column-native outputs by group.",
  merging_raw_networks: "ANDREA is writing the merged raw network table.",
  normalizing_scores: "ANDREA is normalizing per-tool score magnitudes.",
  exporting_artifacts: "ANDREA is exporting graph artifacts.",
  writing_report: "ANDREA is writing the final run report.",
  completed: "Execution finished and outputs are ready.",
  completed_with_failures: "Execution finished with failed run(s); partial outputs may be available.",
  failed: "Execution halted before producing a complete output set.",
};

const POST_PROCESSING_PHASES = new Set([
  "collecting_results",
  "finalizing_grouped",
  "finalizing_group_aggregated",
  "merging_raw_networks",
  "normalizing_scores",
  "exporting_artifacts",
  "writing_report",
]);

function executionStatePlaceholder(job = null) {
  const status = String(job?.status || "").trim();
  const stage = String(job?.stage || "").trim();
  if (status === "running") {
    return "Waiting for ANDREA runtime state...";
  }
  if (stage === "planned") {
    return "Execution is planned. Start the run to see ANDREA progress.";
  }
  return "ANDREA execution progress will appear after execution starts.";
}

function shouldRenderRuntimeWaitingState(job = null) {
  if (!job || !job.run_dir) {
    return false;
  }
  const status = String(job.status || "").trim();
  const stage = String(job.stage || "").trim();
  if (status === "queued" || status === "running") {
    return true;
  }
  return stage === "planned" && status !== "failed";
}

function renderRuntimeWaitingState(root, job = null) {
  root.innerHTML = "";
  root.className = "runtime-progress runtime-progress-waiting muted-box step3-status-panel";

  const title = document.createElement("div");
  title.className = "runtime-progress-waiting-title";
  title.textContent = "Execution progress";
  root.appendChild(title);

  const body = document.createElement("div");
  body.className = "runtime-progress-waiting-body";
  body.textContent = executionStatePlaceholder(job);
  root.appendChild(body);
}

function phaseLabel(phase) {
  const key = String(phase || "").trim();
  return PHASE_LABELS[key] || key.replaceAll("_", " ") || "Execution";
}

function phaseMessage(executionState) {
  const direct = String(executionState?.message || "").trim();
  if (direct) {
    return direct;
  }
  const phase = String(executionState?.phase || "").trim();
  return PHASE_MESSAGES[phase] || "ANDREA is processing the run.";
}

function normalizeTopStatus(status) {
  const value = String(status || "").trim().toLowerCase();
  if (value === "completed_with_failures") {
    return "warning";
  }
  if (value === "failed") {
    return "failed";
  }
  if (value === "completed") {
    return "completed";
  }
  if (value === "running") {
    return "running";
  }
  return "pending";
}

function emptyUnitSummary() {
  return {
    total: 0,
    queued: 0,
    running: 0,
    completed: 0,
    failed: 0,
    warnings: 0,
    errors: 0,
  };
}

function entryIssueCount(entry = {}, key = "warnings") {
  const values = Array.isArray(entry[key]) ? entry[key] : [];
  return values.length;
}

function summarizeEntries(entries = []) {
  const summary = emptyUnitSummary();
  summary.total = entries.length;
  for (const entry of entries) {
    const status = String(entry?.status || "queued");
    if (status === "queued") {
      summary.queued += 1;
    } else if (status === "running") {
      summary.running += 1;
    } else if (status === "failed") {
      summary.failed += 1;
    } else if (status === "completed" || status === "completed_with_warnings") {
      summary.completed += 1;
    }
    summary.warnings += entryIssueCount(entry, "warnings");
    summary.errors += entryIssueCount(entry, "errors");
  }
  return summary;
}

function summarizeWaves(waves = []) {
  const summary = emptyUnitSummary();
  summary.total = waves.length;
  for (const wave of waves) {
    const status = String(wave?.status || "queued");
    if (status === "queued") {
      summary.queued += 1;
    } else if (status === "running") {
      summary.running += 1;
    } else if (status === "failed") {
      summary.failed += 1;
      summary.errors += 1;
    } else if (
      status === "completed"
      || status === "completed_with_warnings"
      || status === "completed_with_failures"
    ) {
      summary.completed += 1;
      if (status === "completed_with_warnings") {
        summary.warnings += 1;
      } else if (status === "completed_with_failures") {
        summary.errors += 1;
      }
    }
  }
  return summary;
}

function unitSummaries(executionState = {}) {
  const payload = executionState?.unit_summaries;
  if (payload && typeof payload === "object") {
    return {
      waves: { ...emptyUnitSummary(), ...(payload.waves || {}) },
      configurations: { ...emptyUnitSummary(), ...(payload.configurations || {}) },
      executions: { ...emptyUnitSummary(), ...(payload.executions || {}) },
    };
  }

  const waves = Array.isArray(executionState?.waves) ? executionState.waves : [];
  const tools = executionState?.tools && typeof executionState.tools === "object"
    ? Object.values(executionState.tools)
    : [];
  const logicalRuns =
    executionState?.logical_runs && typeof executionState.logical_runs === "object"
      ? Object.values(executionState.logical_runs)
      : [];
  return {
    waves: summarizeWaves(waves),
    configurations: summarizeEntries(logicalRuns.length ? logicalRuns : tools),
    executions: summarizeEntries(tools),
  };
}

function appendUnitSummaryItem(parent, label, value, className = "") {
  const item = document.createElement("span");
  item.className = `andrea-progress-unit-item ${className}`.trim();
  const number = document.createElement("strong");
  number.textContent = String(value ?? 0);
  const caption = document.createElement("span");
  caption.textContent = label;
  item.appendChild(number);
  item.appendChild(caption);
  parent.appendChild(item);
}

function appendCompactProgressSummary(parent, summaries) {
  const summary = document.createElement("div");
  summary.className = "andrea-progress-compact-summary";

  const executions = summaries.executions || emptyUnitSummary();
  const executionRow = document.createElement("div");
  executionRow.className = "andrea-progress-execution-row";
  const label = document.createElement("div");
  label.className = "andrea-progress-execution-label";
  label.textContent = `Executions · ${executions.total || 0} total`;
  executionRow.appendChild(label);
  appendUnitSummaryItem(executionRow, "completed", executions.completed, "ok");
  appendUnitSummaryItem(executionRow, "running", executions.running, "running");
  appendUnitSummaryItem(executionRow, "queued", executions.queued, "queued");
  appendUnitSummaryItem(executionRow, "failed", executions.failed, "failed");
  appendUnitSummaryItem(
    executionRow,
    "issues",
    Number(executions.warnings || 0) + Number(executions.errors || 0),
    "warning"
  );
  summary.appendChild(executionRow);

  const waves = summaries.waves || emptyUnitSummary();
  const configurations = summaries.configurations || emptyUnitSummary();
  const context = document.createElement("div");
  context.className = "andrea-progress-context";
  context.textContent = [
    `Waves ${waves.completed || 0}/${waves.total || 0} done`,
    `Configurations ${configurations.completed || 0}/${configurations.total || 0} completed`,
  ].join(" · ");
  summary.appendChild(context);
  parent.appendChild(summary);
}

function normalizeRuntimeStatus(status) {
  const value = String(status || "").trim().toLowerCase();
  if (value === "completed_with_warnings" || value === "completed_with_failures") {
    return "warning";
  }
  if (value === "queued") {
    return "pending";
  }
  if (["pending", "running", "completed", "failed", "warning"].includes(value)) {
    return value;
  }
  return "pending";
}

function statusLabel(status) {
  const value = String(status || "").trim().toLowerCase();
  if (value === "completed_with_warnings") {
    return "completed with warnings";
  }
  if (value === "completed_with_failures") {
    return "completed with failures";
  }
  if (value === "queued") {
    return "queued";
  }
  return value.replaceAll("_", " ") || "pending";
}

function displayToolName(toolId, tool = {}) {
  const rawToolId = String(toolId || "").trim();
  const runId = String(tool.run_id || "").trim();
  if (runId && rawToolId.startsWith(`${runId}__`)) {
    return `${runId} · ${rawToolId.slice(runId.length + 2)}`;
  }
  return rawToolId || runId || "tool";
}

function entryErrors(entry = {}) {
  return Array.isArray(entry.errors)
    ? entry.errors.map((item) => String(item || "").trim()).filter(Boolean)
    : [];
}

function entryWarnings(entry = {}) {
  return Array.isArray(entry.warnings)
    ? entry.warnings.map((item) => String(item || "").trim()).filter(Boolean)
    : [];
}

function waveIssueBlocks(wave = {}, tools = {}) {
  const toolIds = Array.isArray(wave.tools) ? wave.tools : [];
  const blocks = [];

  for (const toolId of toolIds) {
    const tool = tools[toolId];
    if (!tool || typeof tool !== "object") {
      continue;
    }
    const errors = entryErrors(tool);
    const warnings = entryWarnings(tool);
    if (errors.length || warnings.length || String(tool.status || "") === "failed") {
      blocks.push({
        id: toolId,
        kind: "tool",
        title: displayToolName(toolId, tool),
        status: tool.status,
        errors,
        warnings,
      });
    }
  }

  return blocks;
}

function childIssueMessages(physicalTasks = [], tools = {}) {
  const messages = new Set();
  for (const toolId of physicalTasks) {
    const tool = tools[toolId];
    if (!tool || typeof tool !== "object") {
      continue;
    }
    for (const message of [...entryErrors(tool), ...entryWarnings(tool)]) {
      messages.add(message);
    }
  }
  return messages;
}

function logicalIssueBlocks(logicalRuns = {}, tools = {}) {
  const blocks = [];
  for (const [runId, logical] of Object.entries(logicalRuns || {})) {
    if (!logical || typeof logical !== "object") {
      continue;
    }
    const physicalTasks = Array.isArray(logical.physical_tasks)
      ? logical.physical_tasks.map((item) => String(item || "").trim())
      : [];
    const childMessages = childIssueMessages(physicalTasks, tools);
    const errors = entryErrors(logical).filter((message) => !childMessages.has(message));
    const warnings = entryWarnings(logical).filter((message) => !childMessages.has(message));
    if (!errors.length && !warnings.length && String(logical.status || "") !== "failed") {
      continue;
    }
    blocks.push({
      id: runId,
      kind: "run",
      title: `${runId} · configuration`,
      status: logical.status,
      errors,
      warnings,
    });
  }
  return blocks;
}

function ensureIssueUiState() {
  if (!state.runtimeWaveUi || typeof state.runtimeWaveUi !== "object") {
    state.runtimeWaveUi = {};
  }
  if (!(state.runtimeWaveUi.openIssueKeys instanceof Set)) {
    state.runtimeWaveUi.openIssueKeys = new Set();
  }
  if (!(state.runtimeWaveUi.closedIssueKeys instanceof Set)) {
    state.runtimeWaveUi.closedIssueKeys = new Set();
  }
  if (!state.runtimeWaveUi.scrollTops || typeof state.runtimeWaveUi.scrollTops !== "object") {
    state.runtimeWaveUi.scrollTops = {};
  }
  return state.runtimeWaveUi;
}

function issueBlockKey(block = {}) {
  return `${String(block.kind || "issue")}:${String(block.id || block.title || "unknown")}`;
}

function rememberRuntimeUiFromDom(root) {
  const ui = ensureIssueUiState();
  root.querySelectorAll("[data-runtime-scroll-key]").forEach((node) => {
    const key = node.dataset.runtimeScrollKey || "";
    if (key) {
      ui.scrollTops[key] = node.scrollTop || 0;
    }
  });
  root.querySelectorAll("details[data-runtime-issue-key]").forEach((node) => {
    const key = node.dataset.runtimeIssueKey || "";
    if (!key) {
      return;
    }
    if (node.open) {
      ui.openIssueKeys.add(key);
      ui.closedIssueKeys.delete(key);
    } else {
      ui.openIssueKeys.delete(key);
      ui.closedIssueKeys.add(key);
    }
  });
}

function restoreRuntimeUiToDom(root) {
  const ui = ensureIssueUiState();
  root.querySelectorAll("[data-runtime-scroll-key]").forEach((node) => {
    const key = node.dataset.runtimeScrollKey || "";
    if (!key || !(key in ui.scrollTops)) {
      return;
    }
    const scrollTop = Number(ui.scrollTops[key] || 0);
    node.scrollTop = scrollTop;
    window.requestAnimationFrame(() => {
      node.scrollTop = scrollTop;
    });
  });
}

function setRuntimeScrollKey(node, key) {
  node.dataset.runtimeScrollKey = key;
  node.addEventListener("scroll", () => {
    const ui = ensureIssueUiState();
    ui.scrollTops[key] = node.scrollTop || 0;
  });
}

function waveHasIssues(wave = {}, tools = {}) {
  return waveIssueBlocks(wave, tools).length > 0;
}

function toolStatusBucket(status) {
  const value = String(status || "queued").trim().toLowerCase();
  if (value === "running") {
    return "running";
  }
  if (value === "failed") {
    return "failed";
  }
  if (value === "completed" || value === "completed_with_warnings") {
    return "completed";
  }
  return "queued";
}

function summarizeWaveTools(toolIds = [], tools = {}) {
  const counts = {
    total: toolIds.length,
    completed: 0,
    running: 0,
    queued: 0,
    failed: 0,
    warnings: 0,
    errors: 0,
  };
  for (const toolId of toolIds) {
    const tool = tools[toolId];
    const bucket = toolStatusBucket(tool?.status);
    counts[bucket] += 1;
    if (tool && typeof tool === "object") {
      counts.warnings += entryWarnings(tool).length;
      counts.errors += entryErrors(tool).length;
    }
  }
  return counts;
}

function isFinalWaveStatus(status) {
  return [
    "completed",
    "completed_with_warnings",
    "completed_with_failures",
    "failed",
  ].includes(String(status || "").trim().toLowerCase());
}

function waveHasModelIssues({ wave, tools, counts }) {
  const status = String(wave?.status || "").trim().toLowerCase();
  return Boolean(
    waveHasIssues(wave, tools)
    || status === "completed_with_warnings"
    || status === "completed_with_failures"
    || status === "failed"
    || counts.failed
    || counts.warnings
    || counts.errors
  );
}

function buildWaveModel(wave = {}, executionState = {}, tools = {}) {
  const index = Number(wave.index || 0);
  const toolIds = Array.isArray(wave.tools) ? wave.tools : [];
  const counts = summarizeWaveTools(toolIds, tools);
  const status = String(wave.status || "queued").trim().toLowerCase();
  const statusClass = normalizeRuntimeStatus(status);
  const isCurrent = Number(executionState.current_wave || 0) === index;
  const hasIssues = waveHasModelIssues({ wave, tools, counts });
  const rawPercent = Number(wave.percent);
  let percent = Number.isFinite(rawPercent) ? rawPercent : 0;
  if (isFinalWaveStatus(status) && percent <= 0) {
    percent = 100;
  }
  let pool = "active";
  if (status === "queued") {
    pool = "queued";
  } else if (isCurrent || status === "running") {
    pool = "active";
  } else if (isFinalWaveStatus(status)) {
    pool = hasIssues ? "completedIssue" : "completedClean";
  }
  return {
    wave,
    index,
    toolIds,
    counts,
    status,
    statusClass,
    percent: Math.max(0, Math.min(100, Math.round(percent))),
    isCurrent,
    hasIssues,
    pool,
  };
}

export function buildRuntimePoolViewModel(executionState = {}) {
  const tools = executionState?.tools && typeof executionState.tools === "object"
    ? executionState.tools
    : {};
  const logicalRuns =
    executionState?.logical_runs && typeof executionState.logical_runs === "object"
      ? executionState.logical_runs
      : {};
  const waveModels = (Array.isArray(executionState?.waves) ? executionState.waves : [])
    .map((wave) => buildWaveModel(wave, executionState, tools));
  return {
    waves: waveModels,
    completedCleanWaves: waveModels.filter((wave) => wave.pool === "completedClean"),
    completedIssueWaves: waveModels.filter((wave) => wave.pool === "completedIssue"),
    activeWaves: waveModels.filter((wave) => wave.pool === "active"),
    queuedWaves: waveModels.filter((wave) => wave.pool === "queued"),
    logicalIssueRuns: logicalIssueBlocks(logicalRuns, tools),
    tools,
    logicalRuns,
  };
}

function appendWaveToolChip(parent, toolId, tool = {}) {
  const statusClass = normalizeRuntimeStatus(tool.status);
  const chip = document.createElement("div");
  chip.className = `runtime-wave-tool-chip status-${statusClass}`;
  chip.title = String(tool.message || "").trim() || statusLabel(tool.status);

  const label = document.createElement("span");
  label.className = "runtime-wave-tool-label";
  label.textContent = displayToolName(toolId, tool);
  chip.appendChild(label);

  const percent = Number(tool.percent || 0);
  if (
    String(tool.status || "").toLowerCase() === "running" ||
    (percent > 0 && percent < 100)
  ) {
    const percentNode = document.createElement("small");
    percentNode.textContent = `${Math.max(0, Math.min(100, Math.round(percent)))}%`;
    chip.appendChild(percentNode);
  }

  const issueCount =
    (Array.isArray(tool.errors) ? tool.errors.length : 0) +
    (Array.isArray(tool.warnings) ? tool.warnings.length : 0);
  if (issueCount > 0) {
    const badge = document.createElement("strong");
    badge.textContent = String(issueCount);
    chip.appendChild(badge);
  }

  parent.appendChild(chip);
}

function appendIssueList(parent, title, messages, className, scrollKey = "") {
  if (!messages.length) {
    return;
  }
  const section = document.createElement("div");
  section.className = `runtime-wave-issue-section ${className}`;
  const heading = document.createElement("div");
  heading.className = "runtime-wave-issue-section-title";
  heading.textContent = title;
  section.appendChild(heading);
  const list = document.createElement("div");
  list.className = "runtime-wave-issue-list";
  if (scrollKey) {
    setRuntimeScrollKey(list, scrollKey);
  }
  for (const message of messages) {
    const line = document.createElement("div");
    line.className = "runtime-wave-issue-line";
    line.textContent = message;
    list.appendChild(line);
  }
  section.appendChild(list);
  parent.appendChild(section);
}

function appendIssueBlocks(
  parent,
  issueBlocks = [],
  {
    heading = "",
    openByDefault = false,
  } = {}
) {
  if (!issueBlocks.length) {
    return;
  }

  const issuesRoot = document.createElement("div");
  issuesRoot.className = "runtime-wave-issues";
  if (heading) {
    const headingNode = document.createElement("div");
    headingNode.className = "runtime-wave-issues-heading";
    headingNode.textContent = heading;
    issuesRoot.appendChild(headingNode);
  }
  for (const block of issueBlocks) {
    const key = issueBlockKey(block);
    const section = document.createElement("details");
    section.className = "runtime-wave-issue-block";
    section.dataset.runtimeIssueKey = key;
    section.open = Boolean(
      ensureIssueUiState().openIssueKeys.has(key) ||
      (openByDefault && !ensureIssueUiState().closedIssueKeys.has(key))
    );
    section.addEventListener("toggle", () => {
      const ui = ensureIssueUiState();
      if (section.open) {
        ui.openIssueKeys.add(key);
        ui.closedIssueKeys.delete(key);
      } else {
        ui.openIssueKeys.delete(key);
        ui.closedIssueKeys.add(key);
      }
    });
    const title = document.createElement("summary");
    title.className = "runtime-wave-issue-title";
    const titleText = document.createElement("span");
    titleText.textContent = block.title;
    const badge = document.createElement("span");
    badge.className = `runtime-wave-issue-badge status-${normalizeRuntimeStatus(block.status)}`;
    badge.textContent = block.kind === "run" ? "run" : "tool";
    title.appendChild(titleText);
    title.appendChild(badge);
    section.appendChild(title);
    const body = document.createElement("div");
    body.className = "runtime-wave-issue-body";
    appendIssueList(body, "Errors", block.errors, "errors", `${key}:errors`);
    appendIssueList(body, "Warnings", block.warnings, "warnings", `${key}:warnings`);
    if (!block.errors.length && !block.warnings.length) {
      appendIssueList(body, "Status", [statusLabel(block.status)], "errors", `${key}:status`);
    }
    section.appendChild(body);
    issuesRoot.appendChild(section);
  }
  parent.appendChild(issuesRoot);
}

function appendLogicalRunIssues(parent, issueBlocks = []) {
  if (!issueBlocks.length) {
    return;
  }

  const section = document.createElement("section");
  section.className = "runtime-logical-issues";

  const header = document.createElement("div");
  header.className = "runtime-logical-issues-head";
  const title = document.createElement("div");
  title.className = "runtime-logical-issues-title";
  title.textContent = "Configuration Issues";
  const count = document.createElement("span");
  count.className = "runtime-logical-issues-count";
  count.textContent = `${issueBlocks.length} configuration${issueBlocks.length === 1 ? "" : "s"}`;
  header.appendChild(title);
  header.appendChild(count);
  section.appendChild(header);

  appendIssueBlocks(section, issueBlocks, {
    heading: "Configuration Summaries",
    openByDefault: false,
  });
  parent.appendChild(section);
}

function appendActiveToolRow(parent, toolId, tool = {}) {
  const percent = Math.max(0, Math.min(100, Math.round(Number(tool.percent || 0))));
  const row = document.createElement("article");
  row.className = "runtime-active-tool-row";

  const head = document.createElement("div");
  head.className = "runtime-active-tool-head";
  const title = document.createElement("div");
  title.className = "runtime-active-tool-title";
  title.textContent = displayToolName(toolId, tool);
  const meta = document.createElement("div");
  meta.className = "runtime-active-tool-meta";
  const wave = Number(tool.wave || 0);
  const phase = String(tool.phase || "").trim();
  meta.textContent = [
    wave ? `wave ${wave}` : "",
    phase ? phaseLabel(phase) : "Running",
    `${percent}%`,
  ].filter(Boolean).join(" · ");
  head.appendChild(title);
  head.appendChild(meta);
  row.appendChild(head);

  const bar = document.createElement("div");
  bar.className = "runtime-active-tool-bar";
  const fill = document.createElement("div");
  fill.className = "runtime-active-tool-fill";
  fill.style.width = `${percent}%`;
  bar.appendChild(fill);
  row.appendChild(bar);

  const message = document.createElement("div");
  message.className = "runtime-active-tool-message";
  message.textContent = String(tool.message || "").trim() || "Running";
  row.appendChild(message);

  parent.appendChild(row);
}

function toolMatchesBucket(tool = {}, bucket) {
  return toolStatusBucket(tool.status) === bucket;
}

function appendFocusToolChips(parent, title, toolIds = [], tools = {}) {
  if (!toolIds.length) {
    return;
  }
  const section = document.createElement("div");
  section.className = "runtime-focus-section";
  const heading = document.createElement("div");
  heading.className = "runtime-focus-section-title";
  heading.textContent = title;
  section.appendChild(heading);
  const chips = document.createElement("div");
  chips.className = "runtime-wave-tools";
  for (const toolId of toolIds) {
    appendWaveToolChip(chips, toolId, tools[toolId] || {});
  }
  section.appendChild(chips);
  parent.appendChild(section);
}

function appendFocusRunningTools(parent, toolIds = [], tools = {}) {
  const running = toolIds.filter((toolId) => toolMatchesBucket(tools[toolId] || {}, "running"));
  if (!running.length) {
    return;
  }
  const section = document.createElement("div");
  section.className = "runtime-focus-section";
  const heading = document.createElement("div");
  heading.className = "runtime-focus-section-title";
  heading.textContent = "Running";
  section.appendChild(heading);
  const list = document.createElement("div");
  list.className = "runtime-active-tools-list";
  for (const toolId of running) {
    appendActiveToolRow(list, toolId, tools[toolId] || {});
  }
  section.appendChild(list);
  parent.appendChild(section);
}

function appendFocusProgressBar(parent, percent, statusClass = "running") {
  const bar = document.createElement("div");
  bar.className = "runtime-focus-progress-bar";
  const fill = document.createElement("div");
  fill.className = `runtime-focus-progress-fill status-${statusClass}`;
  fill.style.width = `${Math.max(0, Math.min(100, Number(percent || 0)))}%`;
  bar.appendChild(fill);
  parent.appendChild(bar);
}

function appendFocusCounts(parent, counts = {}) {
  const row = document.createElement("div");
  row.className = "runtime-focus-counts";
  const items = [
    ["done", counts.completed || 0, "ok"],
    ["running", counts.running || 0, "running"],
    ["queued", counts.queued || 0, "queued"],
    ["failed", counts.failed || 0, "failed"],
    ["issues", Number(counts.warnings || 0) + Number(counts.errors || 0), "warning"],
  ];
  for (const [label, value, className] of items) {
    appendUnitSummaryItem(row, label, value, className);
  }
  parent.appendChild(row);
}

function appendActiveWaveFocus(parent, waveModel, tools = {}, { onBackToActive = null } = {}) {
  const wave = waveModel.wave;
  const panel = document.createElement("section");
  panel.className = `runtime-active-tools-panel runtime-focus-panel status-${waveModel.statusClass}`;

  const header = document.createElement("div");
  header.className = "runtime-active-tools-head";
  const title = document.createElement("div");
  title.className = "runtime-active-tools-title";
  title.textContent = waveModel.isCurrent
    ? `Active Wave ${waveModel.index || "-"}`
    : `Inspecting Wave ${waveModel.index || "-"}`;
  const actions = document.createElement("div");
  actions.className = "runtime-active-tools-actions";
  const count = document.createElement("span");
  count.className = "runtime-active-tools-count";
  count.textContent = `${waveModel.counts.total} tool${waveModel.counts.total === 1 ? "" : "s"}`;
  actions.appendChild(count);
  if (typeof onBackToActive === "function") {
    const backButton = document.createElement("button");
    backButton.type = "button";
    backButton.className = "runtime-focus-back-button";
    backButton.textContent = "Back to active";
    backButton.addEventListener("click", onBackToActive);
    actions.appendChild(backButton);
  }
  header.appendChild(title);
  header.appendChild(actions);
  panel.appendChild(header);

  const message = document.createElement("div");
  message.className = "runtime-focus-message";
  message.textContent = [
    statusLabel(wave.status),
    `${waveModel.percent}%`,
    `${waveModel.counts.completed}/${waveModel.counts.total} completed`,
  ].filter(Boolean).join(" · ");
  panel.appendChild(message);
  appendFocusProgressBar(panel, waveModel.percent, waveModel.statusClass);
  appendFocusCounts(panel, waveModel.counts);

  appendFocusRunningTools(panel, waveModel.toolIds, tools);
  appendFocusToolChips(
    panel,
    "Completed",
    waveModel.toolIds.filter((toolId) => (
      toolMatchesBucket(tools[toolId] || {}, "completed") &&
      !entryWarnings(tools[toolId] || {}).length
    )),
    tools
  );
  appendFocusToolChips(
    panel,
    "Queued",
    waveModel.toolIds.filter((toolId) => toolMatchesBucket(tools[toolId] || {}, "queued")),
    tools
  );
  appendIssueBlocks(panel, waveIssueBlocks(wave, tools), {
    heading: "Physical Tool Issues",
    openByDefault: true,
  });

  parent.appendChild(panel);
}

function appendPostProcessingFocus(parent, executionState = {}) {
  const phase = String(executionState?.phase || "").trim();
  const status = String(executionState?.status || "").trim();
  const panel = document.createElement("section");
  panel.className = "runtime-active-tools-panel runtime-focus-panel status-running";

  const header = document.createElement("div");
  header.className = "runtime-active-tools-head";
  const title = document.createElement("div");
  title.className = "runtime-active-tools-title";
  title.textContent = "ANDREA Post-processing";
  const count = document.createElement("span");
  count.className = "runtime-active-tools-count";
  count.textContent = `${Math.max(0, Math.min(100, Math.round(Number(executionState.percent || 0))))}%`;
  header.appendChild(title);
  header.appendChild(count);
  panel.appendChild(header);

  const phaseNode = document.createElement("div");
  phaseNode.className = "runtime-focus-phase";
  phaseNode.textContent = phaseLabel(phase);
  panel.appendChild(phaseNode);
  appendFocusProgressBar(panel, Number(executionState.percent || 0), "running");

  const note = document.createElement("div");
  note.className = "runtime-active-tools-note";
  note.textContent = String(executionState.message || "").trim()
    || PHASE_MESSAGES[phase]
    || "ANDREA is preparing output artifacts.";
  panel.appendChild(note);

  if (status && status !== "running") {
    const statusNode = document.createElement("div");
    statusNode.className = "runtime-focus-message";
    statusNode.textContent = statusLabel(status);
    panel.appendChild(statusNode);
  }

  parent.appendChild(panel);
}

function appendFinalFocus(parent, executionState = {}, viewModel = {}) {
  const statusClass = normalizeTopStatus(executionState.status);
  const summaries = unitSummaries(executionState);
  const panel = document.createElement("section");
  panel.className = `runtime-active-tools-panel runtime-focus-panel status-${statusClass}`;

  const header = document.createElement("div");
  header.className = "runtime-active-tools-head";
  const title = document.createElement("div");
  title.className = "runtime-active-tools-title";
  title.textContent =
    statusClass === "failed"
      ? "Execution Failed"
      : statusClass === "warning"
        ? "Execution Finished With Issues"
        : "Execution Complete";
  const count = document.createElement("span");
  count.className = "runtime-active-tools-count";
  count.textContent = `${viewModel.waves?.length || 0} wave${(viewModel.waves?.length || 0) === 1 ? "" : "s"}`;
  header.appendChild(title);
  header.appendChild(count);
  panel.appendChild(header);

  const message = document.createElement("div");
  message.className = "runtime-focus-message";
  message.textContent = phaseMessage(executionState);
  panel.appendChild(message);
  appendFocusProgressBar(panel, Number(executionState.percent || 100), statusClass);
  appendFocusCounts(panel, summaries.configurations || emptyUnitSummary());

  if (viewModel.completedIssueWaves?.length || viewModel.logicalIssueRuns?.length) {
    const note = document.createElement("div");
    note.className = "runtime-active-tools-note";
    note.textContent = "Review the execution pools and Configuration Issues section for failed or warning-producing runs.";
    panel.appendChild(note);
  }

  parent.appendChild(panel);
}

function appendExecutionFocusPanel(root, executionState, viewModel, { onBackToActive = null } = {}) {
  const inspectedIndex = Number(state.runtimeWaveUi?.inspectedWaveIndex || 0);
  const inspectedWave = inspectedIndex
    ? viewModel.waves.find((wave) => wave.index === inspectedIndex)
    : null;
  if (inspectedWave) {
    appendActiveWaveFocus(root, inspectedWave, viewModel.tools, { onBackToActive });
    return;
  }

  const activeWave =
    viewModel.activeWaves.find((wave) => wave.isCurrent || wave.status === "running")
    || viewModel.activeWaves[0];
  if (activeWave) {
    appendActiveWaveFocus(root, activeWave, viewModel.tools);
    return;
  }

  const phase = String(executionState?.phase || "").trim();
  const status = String(executionState?.status || "").trim();
  if (status === "running" && POST_PROCESSING_PHASES.has(phase)) {
    appendPostProcessingFocus(root, executionState);
    return;
  }

  if (["completed", "completed_with_failures", "failed"].includes(status)) {
    appendFinalFocus(root, executionState, viewModel);
  }
}

function ensureRuntimeWaveUi(executionState = {}) {
  if (!state.runtimeWaveUi || typeof state.runtimeWaveUi !== "object") {
    state.runtimeWaveUi = {
      runId: null,
      inspectedWaveIndex: null,
      openIssueKeys: new Set(),
      scrollTops: {},
    };
  }
  ensureIssueUiState();
  const runId = String(executionState?.run_id || "").trim();
  if (state.runtimeWaveUi.runId !== runId) {
    state.runtimeWaveUi.runId = runId;
    state.runtimeWaveUi.inspectedWaveIndex = null;
    state.runtimeWaveUi.openIssueKeys.clear();
    state.runtimeWaveUi.scrollTops = {};
  }
  return state.runtimeWaveUi;
}

function clearInspectedWave(root, executionState) {
  if (state.runtimeWaveUi) {
    state.runtimeWaveUi.inspectedWaveIndex = null;
  }
  renderWaveTimeline(executionState, root);
}

function inspectWave(root, executionState, waveIndex) {
  if (state.runtimeWaveUi) {
    state.runtimeWaveUi.inspectedWaveIndex = Number(waveIndex || 0) || null;
  }
  renderWaveTimeline(executionState, root);
}

function compactWaveMeta(waveModel) {
  const counts = waveModel.counts || {};
  if (waveModel.pool === "queued") {
    return `${counts.total || 0} tool${counts.total === 1 ? "" : "s"} queued`;
  }
  const bits = [
    `${counts.completed || 0}/${counts.total || 0} done`,
    counts.running ? `${counts.running} running` : "",
    counts.failed ? `${counts.failed} failed` : "",
    counts.warnings ? `${counts.warnings} warning${counts.warnings === 1 ? "" : "s"}` : "",
  ].filter(Boolean);
  return bits.join(" · ") || `${waveModel.percent || 0}%`;
}

function appendCompactWaveRow(parent, waveModel, { selected = false, onInspect = null } = {}) {
  const row = document.createElement("button");
  row.type = "button";
  row.className = `runtime-compact-wave status-${waveModel.statusClass}`;
  if (selected) {
    row.classList.add("is-selected");
  }
  if (waveModel.hasIssues) {
    row.classList.add("has-issues");
  }
  row.addEventListener("click", () => {
    if (typeof onInspect === "function") {
      onInspect(waveModel.index);
    }
  });

  const main = document.createElement("div");
  main.className = "runtime-compact-wave-main";

  const title = document.createElement("span");
  title.className = "runtime-compact-wave-title";
  title.textContent = `Wave ${waveModel.index || "-"}`;

  const status = document.createElement("span");
  status.className = `runtime-wave-status status-${waveModel.statusClass}`;
  status.textContent = statusLabel(waveModel.status);

  main.appendChild(title);

  const meta = document.createElement("div");
  meta.className = "runtime-compact-wave-meta";
  meta.textContent = compactWaveMeta(waveModel);

  main.appendChild(meta);

  row.appendChild(main);
  row.appendChild(status);

  if (waveModel.statusClass === "running") {
    const strip = document.createElement("div");
    strip.className = "runtime-compact-wave-progress";
    const fill = document.createElement("div");
    fill.className = `runtime-compact-wave-progress-fill status-${waveModel.statusClass}`;
    fill.style.width = `${Math.max(0, Math.min(100, Number(waveModel.percent || 0)))}%`;
    strip.appendChild(fill);
    row.appendChild(strip);
  }

  if (waveModel.hasIssues) {
    const issueMeta = document.createElement("div");
    issueMeta.className = "runtime-compact-wave-issues";
    const issueCount = Number(waveModel.counts.failed || 0)
      + Number(waveModel.counts.errors || 0)
      + Number(waveModel.counts.warnings || 0);
    issueMeta.textContent = `${issueCount} issue${issueCount === 1 ? "" : "s"}`;
    row.appendChild(issueMeta);
  }

  parent.appendChild(row);
}

function appendPoolSection(parent, {
  title,
  subtitle = "",
  waves = [],
  empty = "No waves.",
  onInspect = null,
  inspectedIndex = null,
  scrollKey = "",
}) {
  const section = document.createElement("section");
  section.className = "runtime-pool-section";

  const head = document.createElement("div");
  head.className = "runtime-pool-section-head";
  const titleNode = document.createElement("div");
  titleNode.className = "runtime-pool-section-title";
  titleNode.textContent = title;
  const count = document.createElement("span");
  count.className = "runtime-pool-section-count";
  count.textContent = String(waves.length);
  head.appendChild(titleNode);
  head.appendChild(count);
  section.appendChild(head);

  if (subtitle) {
    const sub = document.createElement("div");
    sub.className = "runtime-pool-section-subtitle";
    sub.textContent = subtitle;
    section.appendChild(sub);
  }

  const list = document.createElement("div");
  list.className = "runtime-compact-wave-list";
  if (scrollKey) {
    section.classList.add("is-scrollable");
    setRuntimeScrollKey(list, scrollKey);
  }
  if (!waves.length) {
    const emptyNode = document.createElement("div");
    emptyNode.className = "runtime-pool-empty";
    emptyNode.textContent = empty;
    list.appendChild(emptyNode);
  } else {
    for (const waveModel of waves) {
      appendCompactWaveRow(list, waveModel, {
        selected: Number(inspectedIndex || 0) === waveModel.index,
        onInspect,
      });
    }
  }
  section.appendChild(list);
  parent.appendChild(section);
}

function appendPoolColumn(parent, className, title, sections, { scrollKey = "" } = {}) {
  const column = document.createElement("aside");
  column.className = `runtime-pool-column ${className}`;
  if (scrollKey) {
    setRuntimeScrollKey(column, scrollKey);
  }
  const heading = document.createElement("div");
  heading.className = "runtime-pool-column-title";
  heading.textContent = title;
  column.appendChild(heading);
  for (const section of sections) {
    appendPoolSection(column, section);
  }
  parent.appendChild(column);
}

function renderWaveTimeline(executionState, root) {
  rememberRuntimeUiFromDom(root);
  const viewModel = buildRuntimePoolViewModel(executionState);
  const { waves } = viewModel;
  if (!waves.length) {
    renderCommonRuntimeProgress(state.runtimeProgress, "runtime-progress");
    return;
  }
  ensureRuntimeWaveUi(executionState);
  const inspectedIndex = Number(state.runtimeWaveUi?.inspectedWaveIndex || 0);
  if (
    inspectedIndex
    && !viewModel.waves.some((waveModel) => waveModel.index === inspectedIndex)
  ) {
    state.runtimeWaveUi.inspectedWaveIndex = null;
  }
  root.innerHTML = "";
  root.className = "runtime-progress runtime-execution-pools";

  const title = document.createElement("div");
  title.className = "runtime-pools-title";
  title.textContent = [
    `Execution pools · ${waves.length} wave${waves.length === 1 ? "" : "s"}`,
    `${viewModel.activeWaves.length} active`,
    `${viewModel.queuedWaves.length} queued`,
    `${viewModel.completedIssueWaves.length} with issues`,
  ].join(" · ");
  root.appendChild(title);

  const layout = document.createElement("div");
  layout.className = "runtime-pools-layout";

  appendPoolColumn(
    layout,
    "runtime-pool-history",
    "Completed",
    [
      {
        title: "Clean",
        subtitle: "Finished waves without physical issues.",
        waves: viewModel.completedCleanWaves,
        empty: "No clean completed waves yet.",
        onInspect: (waveIndex) => inspectWave(root, executionState, waveIndex),
        inspectedIndex: state.runtimeWaveUi?.inspectedWaveIndex,
      },
      {
        title: "With Issues",
        subtitle: "Finished waves with failed or warning-producing executions.",
        waves: viewModel.completedIssueWaves,
        empty: "No completed waves with issues.",
        onInspect: (waveIndex) => inspectWave(root, executionState, waveIndex),
        inspectedIndex: state.runtimeWaveUi?.inspectedWaveIndex,
      },
    ],
    { scrollKey: "completed-column" }
  );

  const center = document.createElement("section");
  center.className = "runtime-pool-column runtime-pool-active";
  setRuntimeScrollKey(center, "active-column");
  const centerHeading = document.createElement("div");
  centerHeading.className = "runtime-pool-column-title";
  centerHeading.textContent = state.runtimeWaveUi?.inspectedWaveIndex ? "Inspecting" : "Active";
  center.appendChild(centerHeading);
  appendExecutionFocusPanel(center, executionState, viewModel, {
    onBackToActive: () => clearInspectedWave(root, executionState),
  });
  if (center.children.length === 1) {
    const emptyNode = document.createElement("div");
    emptyNode.className = "runtime-pool-empty";
    emptyNode.textContent = "No active wave is currently available.";
    center.appendChild(emptyNode);
  }
  layout.appendChild(center);

  appendPoolColumn(
    layout,
    "runtime-pool-queued",
    "Queued",
    [
      {
        title: "Future Waves",
        subtitle: "Compact list of work not started yet.",
        waves: viewModel.queuedWaves,
        empty: "No queued waves.",
        onInspect: (waveIndex) => inspectWave(root, executionState, waveIndex),
        inspectedIndex: state.runtimeWaveUi?.inspectedWaveIndex,
      },
    ],
    { scrollKey: "queued-column" }
  );

  root.appendChild(layout);
  appendLogicalRunIssues(root, viewModel.logicalIssueRuns);
  restoreRuntimeUiToDom(root);
}

export function renderAndreaExecutionProgress(executionState = null, job = null) {
  const root = $("andrea-execution-progress");
  if (!root) {
    return;
  }
  root.innerHTML = "";

  if (!executionState || typeof executionState !== "object") {
    root.className = "andrea-progress-card muted-box is-empty step3-status-panel";
    const title = document.createElement("div");
    title.className = "step3-status-title";
    title.textContent = "ANDREA Progress";
    const body = document.createElement("div");
    body.className = "step3-status-message";
    body.textContent = executionStatePlaceholder(job);
    root.append(title, body);
    return;
  }

  const percent = Math.max(0, Math.min(100, Number(executionState.percent || 0)));
  const statusClass = normalizeTopStatus(executionState.status);
  root.className = `andrea-progress-card status-${statusClass}`;

  const header = document.createElement("div");
  header.className = "andrea-progress-header";

  const titleWrap = document.createElement("div");
  const eyebrow = document.createElement("div");
  eyebrow.className = "andrea-progress-eyebrow";
  eyebrow.textContent = "ANDREA Progress";
  const title = document.createElement("div");
  title.className = "andrea-progress-title";
  title.textContent = phaseLabel(executionState.phase);
  titleWrap.appendChild(eyebrow);
  titleWrap.appendChild(title);

  const percentNode = document.createElement("div");
  percentNode.className = "andrea-progress-percent";
  percentNode.textContent = `${Math.round(percent)}%`;

  header.appendChild(titleWrap);
  header.appendChild(percentNode);
  root.appendChild(header);

  const message = document.createElement("div");
  message.className = "andrea-progress-message";
  message.textContent = phaseMessage(executionState);
  root.appendChild(message);

  const bar = document.createElement("div");
  bar.className = "andrea-progress-bar";
  const fill = document.createElement("div");
  fill.className = "andrea-progress-fill";
  fill.style.width = `${percent}%`;
  bar.appendChild(fill);
  root.appendChild(bar);

  const summaries = unitSummaries(executionState);
  appendCompactProgressSummary(root, summaries);
}

export function renderRuntimeProgress(runtimeProgress = null, rootId = "runtime-progress") {
  const root = $(rootId);
  if (!root) {
    return;
  }
  if (state.executionState && typeof state.executionState === "object") {
    renderWaveTimeline(state.executionState, root);
    return;
  }
  if (shouldRenderRuntimeWaitingState(state.currentJob)) {
    renderRuntimeWaitingState(root, state.currentJob);
    return;
  }
  renderCommonRuntimeProgress(runtimeProgress, rootId);
}
