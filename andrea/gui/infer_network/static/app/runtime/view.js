import { $ } from "/static-common/app/core/dom.js?v=20260428a";
import { pushToast } from "/static-common/app/ui/toasts.js?v=20260428a";
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
  finalizing_group_aggregated: "Aggregating Cell-Native Runs",
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
  finalizing_group_aggregated: "ANDREA is aggregating cell-native outputs by group.",
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

function appendCounter(parent, label, value, className = "") {
  const item = document.createElement("div");
  item.className = `andrea-progress-kpi ${className}`.trim();
  const number = document.createElement("span");
  number.textContent = String(value ?? 0);
  const caption = document.createElement("small");
  caption.textContent = label;
  item.appendChild(number);
  item.appendChild(caption);
  parent.appendChild(item);
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

function waveIssueBlocks(wave = {}, tools = {}, logicalRuns = {}) {
  const toolIds = Array.isArray(wave.tools) ? wave.tools : [];
  const blocks = [];
  const seen = new Set();

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
      seen.add(`tool:${toolId}`);
    }
  }

  for (const [runId, logical] of Object.entries(logicalRuns || {})) {
    if (!logical || typeof logical !== "object") {
      continue;
    }
    const physicalTasks = Array.isArray(logical.physical_tasks)
      ? logical.physical_tasks.map((item) => String(item || "").trim())
      : [];
    if (!physicalTasks.some((taskId) => toolIds.includes(taskId))) {
      continue;
    }
    const errors = entryErrors(logical);
    const warnings = entryWarnings(logical);
    if (!errors.length && !warnings.length && String(logical.status || "") !== "failed") {
      continue;
    }
    const key = `logical:${runId}`;
    if (seen.has(key)) {
      continue;
    }
    blocks.push({
      id: runId,
      kind: "run",
      title: `${runId} · logical run`,
      status: logical.status,
      errors,
      warnings,
    });
    seen.add(key);
  }

  return blocks;
}

function waveHasIssues(wave = {}, tools = {}, logicalRuns = {}) {
  return waveIssueBlocks(wave, tools, logicalRuns).length > 0;
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

function appendIssueList(parent, title, messages, className) {
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
  for (const message of messages) {
    const line = document.createElement("div");
    line.className = "runtime-wave-issue-line";
    line.textContent = message;
    list.appendChild(line);
  }
  section.appendChild(list);
  parent.appendChild(section);
}

function appendWaveIssues(parent, wave = {}, tools = {}, logicalRuns = {}) {
  const issueBlocks = waveIssueBlocks(wave, tools, logicalRuns);
  if (!issueBlocks.length) {
    return;
  }

  const issuesRoot = document.createElement("div");
  issuesRoot.className = "runtime-wave-issues";
  for (const block of issueBlocks) {
    const section = document.createElement("details");
    section.className = "runtime-wave-issue-block";
    section.open = normalizeRuntimeStatus(block.status) === "failed";
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
    appendIssueList(body, "Errors", block.errors, "errors");
    appendIssueList(body, "Warnings", block.warnings, "warnings");
    if (!block.errors.length && !block.warnings.length) {
      appendIssueList(body, "Status", [statusLabel(block.status)], "errors");
    }
    section.appendChild(body);
    issuesRoot.appendChild(section);
  }
  parent.appendChild(issuesRoot);
}

function runningToolEntries(tools = {}) {
  return Object.entries(tools)
    .filter(([_toolId, tool]) => (
      tool &&
      typeof tool === "object" &&
      normalizeRuntimeStatus(tool.status) === "running"
    ))
    .sort(([leftId], [rightId]) => leftId.localeCompare(rightId));
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

function appendActiveToolsPanel(root, executionState, tools = {}) {
  const running = runningToolEntries(tools);
  const phase = String(executionState?.phase || "").trim();
  const status = String(executionState?.status || "").trim();
  const shouldShowPostProcessingNote =
    running.length === 0 &&
    status === "running" &&
    POST_PROCESSING_PHASES.has(phase);

  if (!running.length && !shouldShowPostProcessingNote) {
    return;
  }

  const panel = document.createElement("section");
  panel.className = "runtime-active-tools-panel";

  const header = document.createElement("div");
  header.className = "runtime-active-tools-head";
  const title = document.createElement("div");
  title.className = "runtime-active-tools-title";
  title.textContent = "Currently Running";
  const count = document.createElement("span");
  count.className = "runtime-active-tools-count";
  count.textContent = `${running.length} active`;
  header.appendChild(title);
  header.appendChild(count);
  panel.appendChild(header);

  if (running.length) {
    const list = document.createElement("div");
    list.className = "runtime-active-tools-list";
    for (const [toolId, tool] of running) {
      appendActiveToolRow(list, toolId, tool);
    }
    panel.appendChild(list);
  } else {
    const note = document.createElement("div");
    note.className = "runtime-active-tools-note";
    note.textContent = `${phaseLabel(phase)} is running. No tool containers are active; follow the ANDREA Progress card above.`;
    panel.appendChild(note);
  }

  root.appendChild(panel);
}

function renderWaveTimeline(executionState, root) {
  const waves = Array.isArray(executionState?.waves) ? executionState.waves : [];
  const tools = executionState?.tools && typeof executionState.tools === "object"
    ? executionState.tools
    : {};
  const logicalRuns =
    executionState?.logical_runs && typeof executionState.logical_runs === "object"
      ? executionState.logical_runs
      : {};
  if (!waves.length) {
    renderCommonRuntimeProgress(state.runtimeProgress, "runtime-progress");
    return;
  }

  root.innerHTML = "";
  root.className = "runtime-progress runtime-wave-timeline";

  const title = document.createElement("div");
  title.className = "runtime-wave-timeline-title";
  title.textContent = `Wave timeline · ${waves.length} wave${waves.length === 1 ? "" : "s"}`;
  root.appendChild(title);

  const grid = document.createElement("div");
  grid.className = "runtime-wave-grid";

  for (const wave of waves) {
    const waveIndex = Number(wave.index || 0);
    const statusClass = normalizeRuntimeStatus(wave.status);
    const hasIssues = waveHasIssues(wave, tools, logicalRuns);
    const isCurrent = Number(executionState.current_wave || 0) === waveIndex;
    const details = document.createElement("details");
    details.className = `runtime-wave-card status-${statusClass}`;
    if (isCurrent) {
      details.classList.add("is-current");
    }
    if (hasIssues) {
      details.classList.add("has-issues");
    }
    details.open = isCurrent || hasIssues || statusClass === "running" || statusClass === "pending";

    const summary = document.createElement("summary");
    summary.className = "runtime-wave-summary";

    const heading = document.createElement("div");
    heading.className = "runtime-wave-heading";
    const waveTitle = document.createElement("span");
    waveTitle.className = "runtime-wave-name";
    waveTitle.textContent = `Wave ${waveIndex || "-"}`;
    const waveStatus = document.createElement("span");
    waveStatus.className = `runtime-wave-status status-${statusClass}`;
    waveStatus.textContent = statusLabel(wave.status);
    heading.appendChild(waveTitle);
    heading.appendChild(waveStatus);

    const meta = document.createElement("div");
    meta.className = "runtime-wave-meta";
    meta.textContent =
      `${Array.isArray(wave.tools) ? wave.tools.length : 0} tool(s) · ` +
      `${Math.max(0, Math.min(100, Math.round(Number(wave.percent || 0))))}%`;

    summary.appendChild(heading);
    summary.appendChild(meta);
    details.appendChild(summary);

    const body = document.createElement("div");
    body.className = "runtime-wave-body";

    const chipGrid = document.createElement("div");
    chipGrid.className = "runtime-wave-tools";
    for (const toolId of Array.isArray(wave.tools) ? wave.tools : []) {
      appendWaveToolChip(chipGrid, toolId, tools[toolId] || {});
    }
    body.appendChild(chipGrid);
    appendWaveIssues(body, wave, tools, logicalRuns);

    details.appendChild(body);
    grid.appendChild(details);
  }

  root.appendChild(grid);
  appendActiveToolsPanel(root, executionState, tools);
}

export function renderAndreaExecutionProgress(executionState = null, job = null) {
  const root = $("andrea-execution-progress");
  if (!root) {
    return;
  }
  root.innerHTML = "";

  if (!executionState || typeof executionState !== "object") {
    root.className = "andrea-progress-card muted-box is-empty";
    root.textContent = executionStatePlaceholder(job);
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

  const summary = executionState.summary || {};
  const counters = document.createElement("div");
  counters.className = "andrea-progress-kpis";
  appendCounter(counters, "completed", summary.completed, "ok");
  appendCounter(counters, "running", summary.running, "running");
  appendCounter(counters, "queued", summary.queued, "queued");
  appendCounter(counters, "failed", summary.failed, "failed");
  appendCounter(counters, "warnings", summary.warnings, "warning");
  root.appendChild(counters);
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
  renderCommonRuntimeProgress(runtimeProgress, rootId);
}

function normalizeExecutionAlertMessage(message) {
  let normalized = String(message || "").trim();
  normalized = normalized.replace(/^\[([^\]]+)\]\s+execution failed:\s*/i, "");
  normalized = normalized.replace(/^[A-Za-z0-9_]+:\s*/i, "");
  return normalized.trim();
}

function isPlanningWarning(issue) {
  return String(issue?.code || "") === "planning_warning";
}

function countStateIssues(executionState = null) {
  if (!executionState || typeof executionState !== "object") {
    return null;
  }
  const entriesPayload =
    executionState.logical_runs && typeof executionState.logical_runs === "object"
      ? executionState.logical_runs
      : executionState.tools && typeof executionState.tools === "object"
        ? executionState.tools
        : {};
  const entries = Object.values(entriesPayload).filter((item) => (
    item && typeof item === "object"
  ));
  const failed = entries.filter((entry) => (
    normalizeRuntimeStatus(entry.status) === "failed"
  )).length;
  const warnings = entries.reduce(
    (total, entry) => total + entryWarnings(entry).length,
    0
  );
  return { failed, warnings };
}

function renderExecutionAlertSummary(root, job = null, executionState = null) {
  const counts = countStateIssues(executionState);
  if (counts === null) {
    return false;
  }
  const jobFailed = String(job?.status || "") === "failed";
  if (!counts.failed && !counts.warnings && !jobFailed) {
    root.textContent = "No execution errors or warnings.";
    return true;
  }

  const title = document.createElement("div");
  title.className = "execution-alerts-title";
  title.textContent =
    `Execution summary: ${counts.failed} failed run(s), ` +
    `${counts.warnings} warning(s)`;
  root.appendChild(title);

  const summary = document.createElement("div");
  summary.className =
    counts.failed || jobFailed
      ? "execution-alert-summary error"
      : "execution-alert-summary warning";
  summary.textContent =
    counts.failed || jobFailed
      ? "Review the highlighted wave/tool entries below for failure details."
      : "Review the highlighted wave/tool entries below for warning details.";
  root.appendChild(summary);
  return true;
}

export function renderExecutionAlerts(job = null, runReport = null, executionState = null) {
  const root = $("execution-alerts");
  if (!root) {
    return;
  }
  root.innerHTML = "";

  if (renderExecutionAlertSummary(root, job, executionState)) {
    return;
  }

  const errors = [];
  const executionWarnings = [];
  const rawWarningIssues =
    runReport && Array.isArray(runReport.issues)
      ? runReport.issues
          .filter(
            (issue) =>
              String(issue?.severity || "") === "warn" &&
              !isPlanningWarning(issue) &&
              String(issue?.message || "").trim()
          )
      : [];
  const errorSignatures = new Set();

  const failedMap = runReport?.tools?.failed;
  if (failedMap && typeof failedMap === "object") {
    for (const [runId, reason] of Object.entries(failedMap)) {
      const message = `${runId}: ${String(reason || "failed")}`;
      errors.push(message);
      errorSignatures.add(normalizeExecutionAlertMessage(message));
    }
  }

  const jobError = String(job?.error || "").trim();
  if (jobError) {
    errors.push(jobError);
    errorSignatures.add(normalizeExecutionAlertMessage(jobError));
    const signature = `${job?.job_id || ""}:${jobError}`;
    if (state.notifiedJobError !== signature) {
      state.notifiedJobError = signature;
      pushToast({
        title: "Job failed",
        message: jobError,
        kind: "error",
        ttlMs: 10000,
      });
    }
  }

  const warningSeen = new Set();
  for (const issue of rawWarningIssues) {
    const message = String(issue?.message || "").trim();
    const signature = normalizeExecutionAlertMessage(message);
    if (errorSignatures.has(signature)) {
      continue;
    }
    const dedupeKey = `exec:${signature}`;
    if (warningSeen.has(dedupeKey)) {
      continue;
    }
    warningSeen.add(dedupeKey);
    executionWarnings.push(message);
  }

  if (!errors.length && !executionWarnings.length) {
    root.textContent = "No execution errors or warnings.";
    return;
  }

  const title = document.createElement("div");
  title.className = "execution-alerts-title";
  title.textContent =
    `Execution alerts: ${errors.length} error(s), ` +
    `${executionWarnings.length} execution warning(s)`;
  root.appendChild(title);

  const renderAlertSection = (sectionTitle, messages, className) => {
    if (!messages.length) {
      return;
    }
    const section = document.createElement("section");
    section.className = "execution-alerts-section";
    const heading = document.createElement("div");
    heading.className = "execution-alerts-section-title";
    heading.textContent = sectionTitle;
    section.appendChild(heading);
    for (const message of messages.slice(0, 8)) {
      const line = document.createElement("div");
      line.className = `execution-alert ${className}`;
      line.textContent = message;
      section.appendChild(line);
    }
    root.appendChild(section);
  };

  renderAlertSection("Execution errors", errors, "error");
  renderAlertSection("Execution warnings", executionWarnings, "warning");

  const hiddenErrors = Math.max(0, errors.length - 8);
  const hiddenExecutionWarnings = Math.max(0, executionWarnings.length - 8);
  if (hiddenErrors || hiddenExecutionWarnings) {
    const parts = [];
    if (hiddenErrors) {
      parts.push(`${hiddenErrors} more error(s)`);
    }
    if (hiddenExecutionWarnings) {
      parts.push(`${hiddenExecutionWarnings} more execution warning(s)`);
    }
    const more = document.createElement("div");
    more.className = "execution-alert";
    more.textContent = `... and ${parts.join(", ")}`;
    root.appendChild(more);
  }
}
