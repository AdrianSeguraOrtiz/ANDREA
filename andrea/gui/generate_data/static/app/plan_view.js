export function createPlanView({ $, scenarioSemanticLabel }) {
  function renderPlan(plan) {
    const summary = $("plan-summary");
    const tables = $("plan-tables");
    tables.innerHTML = "";
    if (!plan || typeof plan !== "object") {
      summary.textContent = "No plan loaded yet.";
      return;
    }
    summary.textContent = [
      `benchmark: ${plan.id || "-"}`,
      `scenario: ${scenarioSemanticLabel(plan)}`,
      `runs: ${(plan.runs || []).length}`,
      `tasks: ${(plan.tasks || []).length}`,
      `max_parallel_tasks: ${plan.execution?.max_parallel_tasks ?? "-"}`,
      `max_cores: ${plan.execution?.max_cores ?? "-"}`,
      `max_ram_gb: ${plan.execution?.max_ram_gb ?? "-"}`,
      `estimated_total_time_s: ${plan.execution?.eta_total_seconds ?? "-"}`,
      `waves: ${(plan.execution?.waves || []).length}`,
    ].join("\n");

    const warnings = Array.isArray(plan.execution?.warnings) ? plan.execution.warnings.filter(Boolean) : [];
    if (warnings.length) {
      const warningBox = document.createElement("div");
      warningBox.className = "muted-box warning-box";
      warningBox.textContent = warnings.join("\n");
      tables.appendChild(warningBox);
    }

    const appendPlanCell = (tr, value, className = "") => {
      const td = document.createElement("td");
      td.textContent = String(value ?? "-");
      if (className) {
        td.className = className;
      }
      tr.appendChild(td);
    };

    const runsCard = document.createElement("article");
    runsCard.className = "wave-card";
    runsCard.innerHTML = "<h3>Simulator Runs</h3>";
    const runsTable = document.createElement("table");
    runsTable.className = "wave-table";
    runsTable.innerHTML = "<thead><tr><th>run_id</th><th>simulator</th><th>replicates</th><th>threads</th><th>RAM GB</th><th>ETA s</th><th>ETA source</th><th>native_outputs</th><th>base_seed</th><th>replicate_seeds</th></tr></thead>";
    const runsBody = document.createElement("tbody");
    for (const run of plan.runs || []) {
      const tr = document.createElement("tr");
      appendPlanCell(tr, run.run_id);
      appendPlanCell(tr, run.simulator_id);
      appendPlanCell(tr, run.replicates);
      appendPlanCell(tr, run.runtime_resources?.threads ?? "-");
      appendPlanCell(tr, run.ram_gb ?? "-");
      appendPlanCell(tr, run.eta_seconds ?? "-");
      appendPlanCell(tr, run.eta_source ?? "-");
      appendPlanCell(
        tr,
        (run.native_outputs || []).join(", ") || "-",
        "wrap-column native-outputs-cell",
      );
      appendPlanCell(tr, run.base_seed);
      appendPlanCell(tr, (run.replicate_seeds || []).join(", "), "wrap-cell");
      runsBody.appendChild(tr);
    }
    runsTable.appendChild(runsBody);
    runsCard.appendChild(runsTable);
    tables.appendChild(runsCard);

    const wavesCard = document.createElement("article");
    wavesCard.className = "wave-card";
    wavesCard.innerHTML = "<h3>Execution Waves</h3>";
    const wavesTable = document.createElement("table");
    wavesTable.className = "wave-table";
    wavesTable.innerHTML = "<thead><tr><th>wave</th><th>tasks</th><th>threads_used</th><th>RAM GB</th><th>ETA s</th><th>window</th></tr></thead>";
    const wavesBody = document.createElement("tbody");
    for (const wave of plan.execution?.waves || []) {
      const tr = document.createElement("tr");
      appendPlanCell(tr, wave.index);
      appendPlanCell(
        tr,
        (wave.tasks || []).map((task) => task.task_id).join(", "),
        "wrap-cell",
      );
      appendPlanCell(tr, wave.threads_used);
      appendPlanCell(tr, wave.ram_gb_used);
      appendPlanCell(tr, wave.eta_seconds);
      appendPlanCell(tr, `${wave.eta_start_seconds ?? "-"}-${wave.eta_end_seconds ?? "-"}`);
      wavesBody.appendChild(tr);
    }
    wavesTable.appendChild(wavesBody);
    wavesCard.appendChild(wavesTable);
    tables.appendChild(wavesCard);

    const tasksCard = document.createElement("article");
    tasksCard.className = "wave-card";
    tasksCard.innerHTML = "<h3>Planned Tasks</h3>";
    const tasksTable = document.createElement("table");
    tasksTable.className = "wave-table";
    tasksTable.innerHTML = "<thead><tr><th>task_id</th><th>run_id</th><th>replicate</th><th>seed</th><th>threads</th><th>RAM GB</th><th>ETA s</th><th>wave</th><th>dataset_id</th></tr></thead>";
    const tasksBody = document.createElement("tbody");
    for (const task of plan.tasks || []) {
      const tr = document.createElement("tr");
      appendPlanCell(tr, task.task_id);
      appendPlanCell(tr, task.run_id);
      appendPlanCell(tr, task.replicate_index);
      appendPlanCell(tr, task.seed);
      appendPlanCell(tr, task.runtime_resources?.threads ?? "-");
      appendPlanCell(tr, task.ram_gb ?? "-");
      appendPlanCell(tr, task.eta_seconds ?? "-");
      appendPlanCell(tr, task.eta_wave ?? "-");
      appendPlanCell(tr, task.dataset_id, "wrap-cell");
      tasksBody.appendChild(tr);
    }
    tasksTable.appendChild(tasksBody);
    tasksCard.appendChild(tasksTable);
    tables.appendChild(tasksCard);
  }

  return { renderPlan };
}
