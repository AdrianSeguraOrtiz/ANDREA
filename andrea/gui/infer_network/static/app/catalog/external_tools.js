import { state } from "../core/state.js";

const CAPABILITIES = [
  "global",
  "group_native",
  "group_emulated",
  "column_native",
  "group_aggregated",
];
const DEFAULT_CUSTOM_TOOL_OUTPUTS = {
  directed: true,
  sign: "mixed",
  evidence: "external_tool_output",
};

function capabilitiesForExecutionMode(executionMode) {
  if (executionMode === "group_aggregated") {
    return ["column_native", "group_aggregated"];
  }
  return executionMode ? [executionMode] : [];
}

function slugifyToken(value) {
  const slug = String(value || "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
  return slug || "tool";
}

export function normalizeCustomToolId(rawId) {
  const slug = slugifyToken(rawId);
  return slug.startsWith("custom_") ? slug : `custom_${slug}`;
}

function isPlainObject(value) {
  return value && typeof value === "object" && !Array.isArray(value);
}

function composeDockerImage(nameValue, tagValue) {
  const name = String(nameValue || "").trim();
  const tag = String(tagValue || "").trim().replace(/^:/, "");
  const imageName = name.split("/").pop() || name;
  if (!name || !tag || name.includes("@") || imageName.includes(":")) {
    return name;
  }
  return `${name}:${tag}`;
}

export function customToolDockerImageFromForm() {
  return composeDockerImage(
    document.getElementById("custom-tool-image-name")?.value || "",
    document.getElementById("custom-tool-image-tag")?.value || ""
  );
}

function normalizeExtraInputList(rawValue) {
  const items = Array.isArray(rawValue)
    ? rawValue
    : String(rawValue || "").split(/[\s,;]+/g);
  const seen = new Set();
  const out = [];
  for (const item of items) {
    const input = normalizeExtraInputToken(isPlainObject(item) ? item.input : item);
    if (!input || seen.has(input)) {
      continue;
    }
    seen.add(input);
    out.push({
      input,
      usage: "External Docker tool declares this standardized input as needed for execution.",
    });
  }
  return out;
}

function normalizeExtraInputToken(value) {
  return String(value || "")
    .trim()
    .replace(/^extras[\/\\]/i, "")
    .replace(/\.(tsv|txt|csv)$/i, "")
    .replace(/[^a-zA-Z0-9_]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .toLowerCase();
}

function parseExtraInputs(value) {
  return normalizeExtraInputList(value);
}

function rawCustomTools() {
  return Array.isArray(state.customTools) ? state.customTools : [];
}

function bootstrapTools() {
  if (!Array.isArray(state.bootstrap?.tools)) {
    return [];
  }
  return state.bootstrap.tools;
}

function removeCatalogEntry(report, toolId) {
  if (!report?.catalog || typeof report.catalog !== "object") {
    return;
  }
  for (const bucket of ["eligible", "warning", "blocked"]) {
    if (!Array.isArray(report.catalog[bucket])) {
      continue;
    }
    report.catalog[bucket] = report.catalog[bucket].filter(
      (entry) => String(entry?.tool_id || "") !== toolId
    );
  }
}

function optimisticWarningEntry(toolId, dockerImage) {
  const issues = [
    {
      severity: "warn",
      code: "custom_tool",
      message: `[${toolId}] external Docker tool is user-provided and has no cost.json; fallback runtime estimation will be used.`,
      tool_id: toolId,
    },
    {
      severity: "warn",
      code: "custom_tool_security",
      message: `[${toolId}] external Docker images execute arbitrary code; only run trusted images.`,
      tool_id: toolId,
    },
  ];
  const tag = String(dockerImage || "").split("/").pop() || "";
  if (!tag.includes(":") || tag.endsWith(":latest")) {
    issues.push({
      severity: "warn",
      code: "custom_tool_unpinned_image",
      message: `[${toolId}] docker_image is not pinned to an explicit non-latest tag or digest.`,
      tool_id: toolId,
    });
  }
  return { tool_id: toolId, status: "warning", tool_origin: "custom", issues };
}

function toBootstrapTool(rawTool) {
  const toolId = normalizeCustomToolId(rawTool.run_id);
  const name = String(rawTool.name || toolId).trim();
  const executionMode = String(rawTool.execution_mode || "").trim();
  const capabilities = executionMode ? [executionMode] : [];
  const extraInputs = normalizeExtraInputList(rawTool.extra_inputs || []);
  return {
    tool_id: toolId,
    name,
    schema_version: "custom-1.0",
    execution_capabilities: capabilities,
    taxonomic_scope: {},
    compatibility_rules: [],
    method_summary: "User-provided external Docker inference tool.",
    method_keywords: ["custom", "docker"],
    assumes: "external_docker",
    accepts: [],
    required_extras: extraInputs.map((item) => item.input),
    optional_extras: [],
    conditional_required_extras: [],
    publication: [],
    first_author: "User-provided",
    year: null,
    implementation_url: "",
    docker_image: String(rawTool.docker_image || ""),
    outputs: { ...DEFAULT_CUSTOM_TOOL_OUTPUTS },
    progress: { kind: "none" },
    artifacts_aux: [],
    params_schema: rawTool._params_schema || {},
    default_params: {},
    spec: {
      ...rawTool,
      id: toolId,
      execution_capabilities: capabilities,
      andrea_contract_capabilities: capabilitiesForExecutionMode(executionMode),
    },
    tool_origin: "custom",
  };
}

function schemaFromRuntimeValue(value) {
  const description = "External Docker tool runtime parameter.";
  if (typeof value === "boolean") {
    return { type: "bool", required: false, default: value, description };
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    return { type: Number.isInteger(value) ? "int" : "float", required: false, default: value, description };
  }
  return { type: "string", required: false, default: String(value ?? ""), description };
}

function schemaFromRuntimeParams(params) {
  const out = {};
  if (!isPlainObject(params)) {
    return out;
  }
  for (const [key, value] of Object.entries(params)) {
    out[key] = schemaFromRuntimeValue(value);
  }
  return out;
}

export function addCustomToolDefinition(rawTool) {
  if (!rawTool || typeof rawTool !== "object" || Array.isArray(rawTool)) {
    throw new Error("Custom tool definition must be an object.");
  }
  const runId = String(rawTool.run_id || "").trim();
  if (!runId) {
    throw new Error("Run ID is required.");
  }
  const toolId = normalizeCustomToolId(runId);
  const dockerImage = String(rawTool.docker_image || "").trim();
  const executionMode = String(rawTool.execution_mode || "").trim();
  if (!dockerImage) {
    throw new Error("docker_image is required.");
  }
  if (!CAPABILITIES.includes(executionMode)) {
    throw new Error("A valid execution mode is required.");
  }

  const normalizedRaw = {
    run_id: runId,
    name: String(rawTool.name || toolId).trim(),
    docker_image: dockerImage,
    execution_mode: executionMode,
    extra_inputs: normalizeExtraInputList(rawTool.extra_inputs || []).map((item) => item.input),
    _params_schema: isPlainObject(rawTool._params_schema) ? rawTool._params_schema : {},
  };

  state.customTools = rawCustomTools().filter(
    (item) => normalizeCustomToolId(item.run_id) !== toolId
  );
  state.customTools.push(normalizedRaw);

  const tools = bootstrapTools().filter((item) => item.tool_id !== toolId);
  tools.push(toBootstrapTool(normalizedRaw));
  state.bootstrap.tools = tools.sort((left, right) =>
    String(left.tool_id).localeCompare(String(right.tool_id))
  );

  if (state.preflightReport?.catalog) {
    removeCatalogEntry(state.preflightReport, toolId);
    const warning = Array.isArray(state.preflightReport.catalog.warning)
      ? state.preflightReport.catalog.warning
      : [];
    warning.push(optimisticWarningEntry(toolId, dockerImage));
    state.preflightReport.catalog.warning = warning;
  }
  if (Array.isArray(state.eligibleToolIds) && !state.eligibleToolIds.includes(toolId)) {
    state.eligibleToolIds.push(toolId);
  }
  return toolId;
}

export function customToolsPayload(selectedToolIds = null) {
  const selected = selectedToolIds
    ? new Set(Array.from(selectedToolIds).map((item) => String(item || "").trim()))
    : null;
  const tools = rawCustomTools().filter((tool) => {
    if (!selected) {
      return true;
    }
    return selected.has(normalizeCustomToolId(tool.run_id));
  });
  if (!tools.length) {
    return null;
  }
  return {
    tools: tools.map((tool) => ({
      run_id: tool.run_id,
      name: tool.name,
      docker_image: tool.docker_image,
      execution_mode: tool.execution_mode || "global",
      extra_inputs: tool.extra_inputs || [],
    })),
  };
}

export function removeCustomToolDefinition(toolIdOrRunId) {
  const toolId = normalizeCustomToolId(toolIdOrRunId);
  const before = rawCustomTools().length;
  state.customTools = rawCustomTools().filter(
    (item) => normalizeCustomToolId(item.run_id) !== toolId
  );
  if (Array.isArray(state.bootstrap?.tools)) {
    state.bootstrap.tools = state.bootstrap.tools.filter((item) => item.tool_id !== toolId);
  }
  if (Array.isArray(state.eligibleToolIds)) {
    state.eligibleToolIds = state.eligibleToolIds.filter((item) => item !== toolId);
  }
  removeCatalogEntry(state.preflightReport, toolId);
  return rawCustomTools().length !== before;
}

export function pruneCustomToolsToSelectedToolIds(selectedToolIds) {
  const selected = new Set(Array.from(selectedToolIds || []).map((item) => String(item || "").trim()));
  let removed = 0;
  for (const tool of [...rawCustomTools()]) {
    const toolId = normalizeCustomToolId(tool.run_id);
    if (selected.has(toolId)) {
      continue;
    }
    if (removeCustomToolDefinition(toolId)) {
      removed += 1;
    }
  }
  return removed;
}

function readRuntimeParamRows() {
  const rows = Array.from(document.querySelectorAll("#custom-tool-param-rows .custom-tool-param-row"));
  const params = {};
  const seen = new Set();
  for (const [idx, row] of rows.entries()) {
    const key = String(row.querySelector(".custom-tool-param-key")?.value || "").trim();
    const rawValue = String(row.querySelector(".custom-tool-param-value")?.value || "");
    const type = String(row.querySelector(".custom-tool-param-type")?.value || "string");
    if (!key && !rawValue.trim()) {
      continue;
    }
    if (!key) {
      throw new Error(`Parameter row ${idx + 1}: key is required.`);
    }
    if (seen.has(key)) {
      throw new Error(`Duplicate parameter key: ${key}`);
    }
    seen.add(key);
    params[key] = parseRuntimeParamValue(rawValue, type, key);
  }
  return params;
}

function parseRuntimeParamValue(rawValue, type, key) {
  if (type === "number") {
    const parsed = Number(rawValue);
    if (!Number.isFinite(parsed)) {
      throw new Error(`${key}: number value is invalid.`);
    }
    return parsed;
  }
  if (type === "boolean") {
    const normalized = String(rawValue || "").trim().toLowerCase();
    if (["true", "1", "yes"].includes(normalized)) {
      return true;
    }
    if (["false", "0", "no"].includes(normalized)) {
      return false;
    }
    throw new Error(`${key}: boolean value must be true or false.`);
  }
  return String(rawValue);
}

function createRuntimeParamValueControl(type, value) {
  if (type === "number") {
    const input = document.createElement("input");
    input.className = "custom-tool-param-value";
    input.type = "number";
    input.step = "any";
    input.placeholder = "0.0";
    input.value = value === undefined ? "" : String(value);
    return input;
  }
  if (type === "boolean") {
    const select = document.createElement("select");
    select.className = "custom-tool-param-value";
    [
      ["true", "true"],
      ["false", "false"],
    ].forEach(([optionValue, label]) => {
      const option = document.createElement("option");
      option.value = optionValue;
      option.textContent = label;
      select.appendChild(option);
    });
    select.value = String(value).toLowerCase() === "true" ? "true" : "false";
    return select;
  }
  const input = document.createElement("input");
  input.className = "custom-tool-param-value";
  input.type = "text";
  input.placeholder = "value";
  input.value = value === undefined ? "" : String(value);
  return input;
}

function replaceRuntimeParamValueControl(row, type) {
  const current = row.querySelector(".custom-tool-param-value");
  const currentValue = current?.value;
  const next = createRuntimeParamValueControl(type, currentValue);
  current?.replaceWith(next);
}

export function buildSimpleCustomToolFromForm() {
  const executionMode = String(
    document.querySelector("input[name='custom-tool-execution-mode']:checked")?.value ||
      "global"
  ).trim();
  const selectedModeInput = document.querySelector("input[name='custom-tool-execution-mode']:checked");
  if (selectedModeInput?.disabled) {
    throw new Error("Selected execution mode is not available for the current Step 1 inputs.");
  }
  const neededExtras = parseExtraInputs(
    document.getElementById("custom-tool-needed-extras")?.value || ""
  );
  const runId = String(document.getElementById("custom-tool-run-id")?.value || "").trim();
  if (!runId) {
    throw new Error("Run ID is required.");
  }
  const runtimeParams = readRuntimeParamRows();
  const toolId = normalizeCustomToolId(runId);
  const tool = {
    run_id: runId,
    name: document.getElementById("custom-tool-name")?.value || "",
    docker_image: customToolDockerImageFromForm(),
    execution_mode: executionMode,
    extra_inputs: neededExtras.map((item) => item.input),
    _params_schema: schemaFromRuntimeParams(runtimeParams),
  };
  return {
    tool,
    run: {
      run_id: runId,
      tool_id: toolId,
      params: runtimeParams,
      execution: { mode: executionMode || "global" },
    },
  };
}

function updateParamEmptyState() {
  const host = document.getElementById("custom-tool-param-rows");
  if (!host) {
    return;
  }
  const existing = host.querySelector(".custom-tool-param-empty");
  const hasRows = Boolean(host.querySelector(".custom-tool-param-row"));
  if (hasRows && existing) {
    existing.remove();
  }
  if (!hasRows && !existing) {
    const empty = document.createElement("div");
    empty.className = "custom-tool-param-empty";
    empty.textContent = "No image parameters added. /io/params.json will be {}.";
    host.appendChild(empty);
  }
}

export function addCustomToolParamRow(initial = {}) {
  const host = document.getElementById("custom-tool-param-rows");
  if (!host) {
    return;
  }
  const row = document.createElement("div");
  row.className = "custom-tool-param-row";

  const key = document.createElement("input");
  key.className = "custom-tool-param-key";
  key.type = "text";
  key.placeholder = "parameter_key";
  key.value = initial.key || "";

  const type = document.createElement("select");
  type.className = "custom-tool-param-type";
  [
    ["string", "string"],
    ["number", "number"],
    ["boolean", "boolean"],
  ].forEach(([optionValue, label]) => {
    const option = document.createElement("option");
    option.value = optionValue;
    option.textContent = label;
    type.appendChild(option);
  });
  type.value = initial.type || "string";

  const value = createRuntimeParamValueControl(type.value, initial.value);
  type.addEventListener("change", () => {
    replaceRuntimeParamValueControl(row, type.value);
  });

  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "danger remove-param-row";
  remove.textContent = "×";
  remove.setAttribute("aria-label", "Remove parameter");
  remove.addEventListener("click", () => {
    row.remove();
    updateParamEmptyState();
  });

  row.append(key, value, type, remove);
  host.appendChild(row);
  updateParamEmptyState();
}

export function resetCustomToolParamRows() {
  const host = document.getElementById("custom-tool-param-rows");
  if (!host) {
    return;
  }
  host.innerHTML = "";
  updateParamEmptyState();
}
