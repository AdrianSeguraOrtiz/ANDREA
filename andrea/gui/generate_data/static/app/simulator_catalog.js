export function createSimulatorCatalogHelpers({ truthContextFamily }) {
  function simulatorRuntimeResourceSummary(resources) {
    const threading = resources?.threading && typeof resources.threading === "object"
      ? resources.threading
      : {};
    const supported = Boolean(threading.supported);
    const defaultThreads = threading.default_threads ?? 1;
    const maxThreads = threading.max_threads ?? 1;
    const mapping = String(threading.upstream_mapping || "").trim();
    return [
      `threading: ${supported ? "supported" : "not supported"}`,
      `default_threads: ${defaultThreads}`,
      `max_threads: ${maxThreads}`,
      mapping ? `mapping: ${mapping}` : "",
    ].filter(Boolean).join("\n");
  }

  function simulatorInputSummary(item) {
    if (!item || typeof item !== "object") {
      return "";
    }
    const id = String(item.input || "").trim();
    const description = String(item.usage || item.message || "").trim();
    return [id, description].filter(Boolean).join(": ");
  }

  function conditionalSimulatorInputDetail(rule) {
    if (!rule || typeof rule !== "object") {
      return null;
    }
    const input = String(rule.input || "").trim();
    const message = String(rule.message || "").trim();
    const conditions = Array.isArray(rule.conditions)
      ? rule.conditions.map(formatSimulatorInputCondition).filter(Boolean)
      : [];
    const condition = conditions.join(" AND ");
    return input || condition || message
      ? { input, condition, message }
      : null;
  }

  function formatSimulatorInputCondition(condition) {
    if (!condition || typeof condition !== "object") {
      return "";
    }
    const field = String(condition.field || "").trim();
    const op = String(condition.op || "").trim();
    const value = condition.value === undefined ? "" : JSON.stringify(condition.value);
    return field && op ? `${field} ${formatConditionalOperator(op)} ${value}` : "";
  }

  function formatConditionalOperator(op) {
    const normalized = String(op || "").trim();
    const labels = {
      eq: "==",
      ne: "!=",
      in: "in",
      not_in: "not in",
      gt: ">",
      gte: ">=",
      lt: "<",
      lte: "<=",
    };
    return labels[normalized] || normalized;
  }

  function capabilityDerivations(capability) {
    const items = Array.isArray(capability?.derivations) ? capability.derivations : [];
    const derivations = new Map();
    for (const item of items) {
      const artifact = String(item?.artifact || "").trim();
      if (artifact && !derivations.has(artifact)) {
        derivations.set(artifact, item);
      }
    }
    return derivations;
  }

  function truthOutputStatusLabel(status) {
    if (status === "native") {
      return "native";
    }
    if (status === "derivable") {
      return "derived";
    }
    return "unavailable";
  }

  function truthOutputStatusMap(truthOutputs) {
    const byContext = new Map();
    const rows = Array.isArray(truthOutputs)
      ? truthOutputs.filter((item) => item && typeof item === "object")
      : [];
    for (const row of rows) {
      const context = truthContextFamily(row.context);
      const status = String(row.status || "").trim();
      if (context && (status === "native" || status === "derivable")) {
        byContext.set(context, status);
      }
    }
    return byContext;
  }

  function normalizedTruthContextKey(context) {
    return truthContextFamily(context);
  }

  function truthContextMap(truthContexts) {
    const contexts = Array.isArray(truthContexts)
      ? truthContexts.filter((item) => item && typeof item === "object")
      : [];
    const byOutput = new Map();
    for (const context of contexts) {
      const key = normalizedTruthContextKey(context.context);
      if (key && !byOutput.has(key)) {
        byOutput.set(key, context);
      }
    }
    return byOutput;
  }

  function truthContextHasDetail(context, status) {
    if (!context || status === "none") {
      return false;
    }
    const textFields = ["explanation", "generation", "score_semantics"];
    if (textFields.some((field) => String(context?.[field] || "").trim())) {
      return true;
    }
    const listFields = ["upstream_configuration", "source_artifacts", "limitations"];
    return listFields.some((field) => Array.isArray(context?.[field]) && context[field].length);
  }

  return {
    capabilityDerivations,
    conditionalSimulatorInputDetail,
    formatConditionalOperator,
    formatSimulatorInputCondition,
    simulatorInputSummary,
    simulatorRuntimeResourceSummary,
    truthContextHasDetail,
    truthContextMap,
    truthOutputStatusLabel,
    truthOutputStatusMap,
  };
}
