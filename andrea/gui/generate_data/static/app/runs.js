export function createRunHelpers({ state }) {
  function selectedSimulatorRunCounts() {
    const counts = new Map();
    document.querySelectorAll(".run-card .simulator-id").forEach((input) => {
      const simulatorId = String(input?.value || "").trim();
      if (!simulatorId) {
        return;
      }
      counts.set(simulatorId, (counts.get(simulatorId) || 0) + 1);
    });
    return counts;
  }

  function refreshSimulatorCatalogRunCounts() {
    const counts = selectedSimulatorRunCounts();
    document.querySelectorAll(".tool-item[data-simulator-id]").forEach((card) => {
      const simulatorId = String(card.dataset.simulatorId || "").trim();
      const count = counts.get(simulatorId) || 0;
      card.classList.toggle("has-selected-runs", count > 0);
      const badge = card.querySelector(".selection-count-badge");
      if (!badge) {
        return;
      }
      badge.textContent = String(count);
      badge.classList.toggle("is-active", count > 0);
      badge.setAttribute(
        "aria-label",
        `${count} selected ${count === 1 ? "run" : "runs"} for ${simulatorId}`
      );
    });
  }

  function availableSimulatorIds() {
    const report = state.preflightReport;
    if (!report) {
      return [];
    }
    return [...(report.eligible || []), ...(report.warning || [])]
      .map((entry) => String(entry.simulator_id || ""))
      .filter(Boolean);
  }

  function buildRunId(simulatorId) {
    const existing = Array.from(document.querySelectorAll(".run-id")).map((node) => node.value.trim());
    let idx = 1;
    while (existing.includes(`${simulatorId}__${String(idx).padStart(2, "0")}`)) {
      idx += 1;
    }
    return `${simulatorId}__${String(idx).padStart(2, "0")}`;
  }

  return {
    availableSimulatorIds,
    buildRunId,
    refreshSimulatorCatalogRunCounts,
    selectedSimulatorRunCounts,
  };
}
