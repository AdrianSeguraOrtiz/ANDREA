import { state } from "../core/state.js";

export function toolById(toolId) {
  const tools = Array.isArray(state.bootstrap?.tools) ? state.bootstrap.tools : [];
  return tools.find((item) => item.tool_id === toolId) || null;
}

export function listAvailableTools() {
  const tools = Array.isArray(state.bootstrap?.tools) ? state.bootstrap.tools : [];
  if (!Array.isArray(state.eligibleToolIds) || !state.eligibleToolIds.length) {
    return [];
  }
  const allowed = new Set(state.eligibleToolIds);
  return tools.filter((tool) => allowed.has(tool.tool_id));
}

export function defaultGroupModeForTool(tool) {
  const capabilities = Array.isArray(tool?.execution_capabilities)
    ? tool.execution_capabilities.map((item) => String(item || "").trim()).filter(Boolean)
    : [];
  if (capabilities.includes("global")) {
    return "global";
  }
  return capabilities[0] || "global";
}

function normalizeIssues(items, severity = null) {
  const out = [];
  const seen = new Set();
  const rawIssues = Array.isArray(items) ? items : [];

  for (const raw of rawIssues) {
    const itemSeverity = String(raw?.severity || "").trim();
    if (severity && itemSeverity !== severity) {
      continue;
    }
    const message = String(raw?.message || "").trim();
    if (!message) {
      continue;
    }
    const key = `${itemSeverity}:${message}`;
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    out.push({
      severity: itemSeverity,
      code: String(raw?.code || "").trim(),
      message,
    });
  }
  return out;
}

export function toolIssuePayload(tool, entry, kind) {
  const blockIssues = normalizeIssues(entry?.issues, "block");
  const warningIssues = normalizeIssues(entry?.issues, "warn");
  const conditionalIssues = warningIssues.filter((issue) => issue.code === "conditional_required");
  const otherWarnings = warningIssues.filter((issue) => issue.code !== "conditional_required");
  const isBlocked = kind === "blocked";
  const sections = [];
  const messageSection = (title, items, emptyText, open = true) => ({
    title,
    open,
    ...(items.length ? { items: items.map((issue) => issue.message) } : { text: emptyText }),
  });
  if (isBlocked || blockIssues.length) {
    sections.push(messageSection("Blocking Reasons", blockIssues, "No blocking reason was reported."));
  }
  sections.push(
    messageSection(
      isBlocked ? "Warnings Also Reported" : "Warnings",
      otherWarnings,
      "No warnings were reported."
    )
  );
  if (conditionalIssues.length) {
    sections.push(messageSection("Pending Conditional Inputs", conditionalIssues, ""));
  }
  return {
    title: `${tool?.name || entry?.tool_id || "Tool"} · ${isBlocked ? "Why blocked" : "Why warned"}`,
    description: isBlocked
      ? "This tool cannot be selected until the blocking conditions are resolved."
      : "This tool can be selected, but the catalog reported warnings for the current dataset or configuration.",
    chips: [
      { label: "tool", value: String(entry?.tool_id || tool?.tool_id || "-") },
      { label: "blocking", value: String(blockIssues.length), tone: blockIssues.length ? "blocked" : "" },
      { label: "warnings", value: String(otherWarnings.length), tone: otherWarnings.length ? "warning" : "" },
      { label: "conditional", value: String(conditionalIssues.length), tone: conditionalIssues.length ? "warning" : "" },
    ],
    sections,
    raw: entry || null,
  };
}

export function toolSpecInfoPayload(tool) {
  const accepts = Array.isArray(tool?.accepts) ? tool.accepts : [];
  const requiredExtras = Array.isArray(tool?.required_extras) ? tool.required_extras : [];
  const optionalExtras = Array.isArray(tool?.optional_extras) ? tool.optional_extras : [];
  const conditionalExtras = Array.isArray(tool?.conditional_required_extras) ? tool.conditional_required_extras : [];
  const publication = Array.isArray(tool?.publication) ? tool.publication : [];
  const firstAuthor = String(tool?.first_author || "").trim();
  const outputs = tool?.outputs && typeof tool.outputs === "object" ? tool.outputs : {};
  const progress = tool?.progress && typeof tool.progress === "object" ? tool.progress : {};
  const params = tool?.params_schema && typeof tool.params_schema === "object" ? tool.params_schema : {};
  const artifactsAux = Array.isArray(tool?.artifacts_aux) ? tool.artifacts_aux : [];
  const keywords = Array.isArray(tool?.method_keywords) ? tool.method_keywords : [];
  const taxonomicScope = tool?.taxonomic_scope && typeof tool.taxonomic_scope === "object" ? tool.taxonomic_scope : {};
  const compatibilityRules = Array.isArray(tool?.compatibility_rules) ? tool.compatibility_rules : [];
  const capabilities = Array.isArray(tool?.execution_capabilities)
    ? tool.execution_capabilities.map((item) => String(item || "").trim()).filter(Boolean)
    : [];
  const conditionalRequirements = conditionalExtras
    .map((item) => conditionalExtraDetail(item))
    .filter(Boolean);
  const compatibilityRuleDetails = compatibilityRules
    .map((item) => compatibilityRuleDetail(item))
    .filter(Boolean);

  const publicationLinks = publication.map((item) => ({
    label: String(item || "").trim(),
    url: String(item || "").trim(),
  }));
  return {
    title: tool?.name || tool?.tool_id || "Tool Info",
    description: String(tool?.method_summary || "").trim(),
    chips: [
      { label: "id", value: tool?.tool_id || "-" },
      { label: "year", value: tool?.year ? String(tool.year) : "-" },
      { label: "assumes", value: tool?.assumes || "-" },
      ...capabilities.map((mode) => ({ label: "mode", value: mode, tone: "mode" })),
    ],
    sections: [
      {
        title: "Overview",
        open: true,
        fields: [
          { label: "Schema version", value: tool?.schema_version || "-" },
          {
            label: "Publication(s)",
            links: publicationLinks.length ? publicationLinks : [{ label: "-", url: "" }],
          },
          { label: "First author", value: firstAuthor || "-" },
          { label: "Publication year", value: tool?.year ? String(tool.year) : "-" },
          { label: "Keywords", value: keywords.length ? keywords.join(", ") : "-" },
          {
            label: "Implementation",
            link: {
              label: String(tool?.implementation_url || "-"),
              url: String(tool?.implementation_url || ""),
            },
            value: tool?.implementation_url || "-",
          },
          { label: "Docker image", value: tool?.docker_image || "-" },
        ],
      },
      {
        title: "Execution and Inputs",
        open: true,
        fields: [
          { label: "Execution capabilities", value: capabilities.length ? capabilities.join(", ") : "-" },
          { label: "Accepts", value: accepts.length ? accepts.join(", ") : "-" },
          {
            label: "Taxonomic groups",
            value: Array.isArray(taxonomicScope.allowed_groups) && taxonomicScope.allowed_groups.length
              ? taxonomicScope.allowed_groups.join(", ")
              : "-",
          },
          {
            label: "Supported species",
            value: Array.isArray(taxonomicScope.supported_species) && taxonomicScope.supported_species.length
              ? taxonomicScope.supported_species.join(", ")
              : "not restricted by species",
          },
          { label: "Required extra inputs", value: requiredExtras.length ? requiredExtras.join(", ") : "none" },
          { label: "Optional extra inputs", value: optionalExtras.length ? optionalExtras.join(", ") : "none" },
        ],
        conditionsLabel: "Conditional required inputs",
        conditions: conditionalRequirements,
      },
      {
        title: "Compatibility Rules",
        open: compatibilityRuleDetails.length > 0,
        text: compatibilityRuleDetails.length ? "" : "No parameter-specific compatibility rules declared.",
        conditionsLabel: "Dataset, parameter and execution rules",
        conditions: compatibilityRuleDetails,
      },
      {
        title: "Outputs and Artifacts",
        open: true,
        fields: [
          { label: "Directed", value: String(outputs.directed ?? "-") },
          { label: "Sign", value: outputs.sign ?? "-" },
          { label: "Evidence", value: outputs.evidence ?? "-" },
          { label: "Progress reporting", value: progress.kind || "-" },
          { label: "Progress details", value: progress.note ? String(progress.note) : "-" },
        ],
        artifactsLabel: "Auxiliary artifacts",
        artifacts: artifactsAux,
      },
      {
        title: "Parameters",
        open: false,
        text: Object.keys(params).length ? "" : "No parameters declared.",
        params,
      },
    ],
    raw: tool?.spec || null,
    example: "",
  };
}

function conditionalExtraDetail(rule) {
  if (!rule || typeof rule !== "object") {
    return null;
  }
  const input = String(rule.input || "").trim();
  const message = String(rule.message || "").trim();
  const op = String(rule.op || "").trim();
  const value = rule.value === undefined ? "" : JSON.stringify(rule.value);
  const left = rule.param
    ? `param.${String(rule.param).trim()}`
    : rule.execution
      ? `execution.${String(rule.execution).trim()}`
      : "";
  const condition = left && op ? `${left} ${formatConditionalOperator(op)} ${value}` : "";
  return input || condition || message
    ? { input, condition, message: [String(rule.usage || "").trim(), message].filter(Boolean).join(" ") }
    : null;
}

function compatibilityRuleDetail(rule) {
  if (!rule || typeof rule !== "object") {
    return null;
  }
  const conditions = Array.isArray(rule.conditions)
    ? rule.conditions.map((item) => compatibilityConditionText(item)).filter(Boolean)
    : [];
  const action = String(rule.action || "").trim();
  const message = String(rule.message || "").trim();
  return conditions.length || action || message
    ? {
        input: action ? `action: ${action}` : "",
        condition: conditions.join(" AND "),
        message,
      }
    : null;
}

function compatibilityConditionText(condition) {
  if (!condition || typeof condition !== "object") {
    return "";
  }
  const field = String(condition.field || "").trim();
  const op = formatConditionalOperator(condition.op);
  const value = condition.value_from
    ? `$${String(condition.value_from).trim()}`
    : JSON.stringify(condition.value);
  return field && op ? `${field} ${op} ${value}` : "";
}

function formatConditionalOperator(op) {
  const normalized = String(op || "").trim();
  const labels = {
    eq: "==",
    ne: "!=",
    neq: "!=",
    in: "in",
    not_in: "not in",
    exists: "exists",
  };
  return labels[normalized] || normalized;
}

export function populateToolIssueSelect() {
  const select = document.getElementById("tool-issue-tool-id");
  if (!select) {
    return;
  }
  const tools = Array.isArray(state.bootstrap?.tools) ? [...state.bootstrap.tools] : [];
  tools.sort((a, b) => String(a.name || "").localeCompare(String(b.name || "")));
  select.innerHTML = "";
  for (const tool of tools) {
    const option = document.createElement("option");
    option.value = String(tool.tool_id || "");
    option.textContent = String(tool.name || tool.tool_id || "");
    select.appendChild(option);
  }
}

export function buildToolRequestIssueUrl() {
  const toolName = String(document.getElementById("tool-request-tool-name")?.value || "").trim();
  const doi = String(document.getElementById("tool-request-doi")?.value || "").trim();
  const repoUrl = String(document.getElementById("tool-request-repo")?.value || "").trim();
  const expectedInputs = String(document.getElementById("tool-request-inputs")?.value || "").trim();
  const expectedOutputs = String(document.getElementById("tool-request-outputs")?.value || "").trim();
  const notes = String(document.getElementById("tool-request-notes")?.value || "").trim();

  if (!toolName) {
    throw new Error("Tool Name is required to create the issue.");
  }

  const issueTitle = `[Tool Request] ${toolName}`;
  const bodyLines = [
    "## Tool Request",
    "",
    `- Tool name: ${toolName}`,
    `- DOI / publication: ${doi || "-"}`,
    `- Implementation repository: ${repoUrl || "-"}`,
    `- Expected inputs: ${expectedInputs || "-"}`,
    `- Expected outputs: ${expectedOutputs || "-"}`,
    "",
    "## Notes",
    notes || "-",
    "",
    "## Submitted From",
    "- ANDREA GUI infer-network",
  ];

  const params = new URLSearchParams();
  params.set("title", issueTitle);
  params.set("body", bodyLines.join("\n"));
  params.set("labels", "tool-request");

  return `https://github.com/AdrianSeguraOrtiz/ANDREA/issues/new?${params.toString()}`;
}

export function buildToolIssueReportUrl() {
  const toolId = String(document.getElementById("tool-issue-tool-id")?.value || "").trim();
  const issueType = String(document.getElementById("tool-issue-type")?.value || "other").trim();
  const observed = String(document.getElementById("tool-issue-observed")?.value || "").trim();
  const expected = String(document.getElementById("tool-issue-expected")?.value || "").trim();
  const context = String(document.getElementById("tool-issue-context")?.value || "").trim();

  if (!toolId) {
    throw new Error("Select a tool to report.");
  }
  if (!observed) {
    throw new Error("Observed Behavior is required.");
  }
  const tool = toolById(toolId);
  const toolName = String(tool?.name || toolId);

  const issueTitle = `[Tool Catalog Issue] ${toolName} (${issueType})`;
  const bodyLines = [
    "## Tool Catalog Issue",
    "",
    `- tool_id: ${toolId}`,
    `- tool_name: ${toolName}`,
    `- issue_type: ${issueType}`,
    "",
    "## Observed",
    observed,
    "",
    "## Expected",
    expected || "-",
    "",
    "## Context",
    context || "-",
    "",
    "## Submitted From",
    "- ANDREA GUI infer-network",
  ];

  const params = new URLSearchParams();
  params.set("title", issueTitle);
  params.set("body", bodyLines.join("\n"));
  params.set("labels", "tool-catalog-issue");
  return `https://github.com/AdrianSeguraOrtiz/ANDREA/issues/new?${params.toString()}`;
}
