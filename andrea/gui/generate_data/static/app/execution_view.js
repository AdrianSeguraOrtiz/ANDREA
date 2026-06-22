export function createExecutionView({
  state,
  $,
  pushToast,
  resetFilesView,
  resetReproducibility,
  fetchFiles,
  fileApi,
  fileExplorerOptions,
}) {
  function renderExecutionAlerts(job, runtimeProgress = null) {
    const root = $("execution-alerts");
    const error = String(job?.error || "").trim();
    const failedTasks = Array.isArray(runtimeProgress?.tasks)
      ? runtimeProgress.tasks.filter((task) => String(task?.status || "") === "failed")
      : [];
    const messages = [];
    if (error) {
      messages.push(error);
    }
    for (const task of failedTasks) {
      const taskId = String(task?.task_id || "").trim() || "task";
      const message = String(task?.message || "Task failed.").trim();
      messages.push(`${taskId}: ${message}`);
    }
    const uniqueMessages = [...new Set(messages.filter(Boolean))];
    root.classList.toggle("has-errors", uniqueMessages.length > 0);
    if (!uniqueMessages.length) {
      root.textContent = "No execution errors or warnings.";
      return;
    }
    root.textContent = uniqueMessages.join("\n");
    if (error) {
      pushToast({ title: "Job failed", message: error, kind: "error", ttlMs: 9000 });
    }
  }

  function hasExecutionArtifacts(job) {
    return Boolean(job?.benchmark_root) && (job.stage === "executed" || job.status === "failed");
  }

  function updateExplorerVisibility(job) {
    const visible = hasExecutionArtifacts(job);
    $("results-explorer-section").hidden = !visible;
    $("results-explorer-placeholder").hidden = visible;
    if (!visible) {
      resetFilesView(state, "Results Explorer will be available after execution.");
      resetReproducibility("Reproducibility snippets will be available after execution.");
    }
  }

  async function refreshFilesIfNeeded(job) {
    if (!hasExecutionArtifacts(job)) {
      return;
    }
    const key = `${job.job_id}:${state.filesMode}:${job.status}:${job.benchmark_root}`;
    if (state.loadedFilesKey === key) {
      return;
    }
    await fetchFiles(state, fileApi(), {}, fileExplorerOptions());
    state.loadedFilesKey = key;
  }

  return {
    hasExecutionArtifacts,
    refreshFilesIfNeeded,
    renderExecutionAlerts,
    updateExplorerVisibility,
  };
}
