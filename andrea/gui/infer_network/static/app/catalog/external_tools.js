import { state } from "../core/state.js";

const CAPABILITIES = [
  "global",
  "group_native",
  "group_emulated",
  "column_native",
  "group_aggregated",
];
const CUSTOM_TOOL_OUTPUT_KEYS = new Set(["directed", "sign"]);
const CUSTOM_TOOL_DEFINITION_KEYS = new Set([
  "run_id",
  "name",
  "docker_image",
  "execution_mode",
  "extra_inputs",
  "outputs",
]);
const CUSTOM_TOOL_EVIDENCE = "external_tool_output";
const OUTPUT_SIGN_SEMANTICS = new Set(["none", "signed", "mixed"]);

function capabilitiesForExecutionMode(executionMode) {
  if (executionMode === "group_aggregated") {
    return ["column_native", "group_aggregated"];
  }
  return executionMode ? [executionMode] : [];
}

export function normalizeCustomToolId(rawId) {
  if (
    typeof rawId !== "string" ||
    !rawId ||
    rawId !== rawId.trim()
  ) {
    throw new Error(
      "run_id must be a non-empty string without surrounding whitespace."
    );
  }
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(rawId)) {
    throw new Error(
      "run_id must match [A-Za-z0-9][A-Za-z0-9._-]* exactly."
    );
  }
  return `custom_${rawId}`;
}

export function validateCanonicalRunId(rawId) {
  normalizeCustomToolId(rawId);
  return rawId;
}

export function customToolRunId(tool) {
  if (!isPlainObject(tool) || tool.tool_origin !== "custom") {
    throw new Error("Custom tool metadata is required.");
  }
  const runId = tool?.spec?.run_id;
  validateCanonicalRunId(runId);
  const expectedToolId = normalizeCustomToolId(runId);
  if (tool.tool_id !== expectedToolId || tool?.spec?.id !== expectedToolId) {
    throw new Error(
      "Custom tool identity metadata does not match its canonical run_id."
    );
  }
  return runId;
}

export function validateCustomToolRunIdentity(tool, rawRunId) {
  const expectedRunId = customToolRunId(tool);
  validateCanonicalRunId(rawRunId);
  if (rawRunId !== expectedRunId) {
    throw new Error(`Custom tool run_id must be exactly ${expectedRunId}.`);
  }
  return rawRunId;
}

function validateCanonicalCustomToolId(rawToolId) {
  if (
    typeof rawToolId !== "string" ||
    rawToolId !== rawToolId.trim() ||
    !/^custom_[A-Za-z0-9][A-Za-z0-9._-]*$/.test(rawToolId)
  ) {
    throw new Error(
      "tool_id must be an exact custom_ ID derived from a canonical run_id."
    );
  }
  return rawToolId;
}

function isPlainObject(value) {
  return value && typeof value === "object" && !Array.isArray(value);
}

function normalizeCustomToolOutputs(rawOutputs) {
  if (!isPlainObject(rawOutputs)) {
    throw new Error("outputs is required and must be an object.");
  }
  const unexpected = Object.keys(rawOutputs).filter(
    (key) => !CUSTOM_TOOL_OUTPUT_KEYS.has(key)
  );
  if (unexpected.length) {
    throw new Error(`outputs has unsupported keys: ${unexpected.sort().join(", ")}.`);
  }
  if (!Object.hasOwn(rawOutputs, "directed")) {
    throw new Error("outputs.directed is required.");
  }
  if (!Object.hasOwn(rawOutputs, "sign")) {
    throw new Error("outputs.sign is required.");
  }
  const directed = rawOutputs.directed;
  const sign = rawOutputs.sign;
  if (typeof directed !== "boolean") {
    throw new Error("outputs.directed must be true or false.");
  }
  if (typeof sign !== "string" || !OUTPUT_SIGN_SEMANTICS.has(sign)) {
    throw new Error("outputs.sign must be none, signed or mixed.");
  }
  return {
    directed,
    sign,
  };
}

function requireCanonicalString(rawTool, key) {
  if (!Object.hasOwn(rawTool, key)) {
    throw new Error(`${key} is required.`);
  }
  const value = rawTool[key];
  if (
    typeof value !== "string" ||
    !value ||
    value !== value.trim()
  ) {
    throw new Error(
      `${key} must be a non-empty string without surrounding whitespace.`
    );
  }
  return value;
}

function validateCustomToolDefinition(rawTool) {
  if (!isPlainObject(rawTool)) {
    throw new Error("Custom tool definition must be an object.");
  }
  const unexpected = Object.keys(rawTool).filter(
    (key) => !CUSTOM_TOOL_DEFINITION_KEYS.has(key)
  );
  if (unexpected.length) {
    throw new Error(
      `Custom tool definition has unsupported keys: ${unexpected.sort().join(", ")}.`
    );
  }
  for (const key of CUSTOM_TOOL_DEFINITION_KEYS) {
    if (!Object.hasOwn(rawTool, key)) {
      throw new Error(`${key} is required.`);
    }
  }

  const runId = requireCanonicalString(rawTool, "run_id");
  normalizeCustomToolId(runId);
  const name = requireCanonicalString(rawTool, "name");
  const dockerImage = requireCanonicalString(rawTool, "docker_image");
  const executionMode = requireCanonicalString(rawTool, "execution_mode");
  if (!CAPABILITIES.includes(executionMode)) {
    throw new Error(
      `execution_mode must be one of: ${CAPABILITIES.join(", ")}.`
    );
  }

  if (!Array.isArray(rawTool.extra_inputs)) {
    throw new Error("extra_inputs must be an array.");
  }
  const extraInputs = [];
  const seenExtras = new Set();
  for (const [idx, input] of rawTool.extra_inputs.entries()) {
    if (
      typeof input !== "string" ||
      !input ||
      input !== input.trim() ||
      !/^[a-z][a-z0-9_]*$/.test(input)
    ) {
      throw new Error(`extra_inputs[${idx}] must be a canonical input key.`);
    }
    if (seenExtras.has(input)) {
      throw new Error(`extra_inputs contains duplicate input: ${input}.`);
    }
    seenExtras.add(input);
    extraInputs.push(input);
  }

  return {
    run_id: runId,
    name,
    docker_image: dockerImage,
    execution_mode: executionMode,
    extra_inputs: extraInputs,
    outputs: normalizeCustomToolOutputs(rawTool.outputs),
  };
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
  if (!Array.isArray(state.customTools)) {
    throw new Error("Internal customTools state must be an array.");
  }
  return state.customTools;
}

function bootstrapTools() {
  if (!Array.isArray(state.bootstrap?.tools)) {
    throw new Error("Internal bootstrap.tools state must be an array.");
  }
  return state.bootstrap.tools;
}

function selectedToolIdSet(selectedToolIds) {
  let values;
  try {
    values = Array.from(selectedToolIds);
  } catch (_error) {
    throw new Error("selectedToolIds must be iterable.");
  }
  if (
    !values.every(
      (item) =>
        typeof item === "string" &&
        Boolean(item) &&
        item === item.trim()
    )
  ) {
    throw new Error(
      "selectedToolIds must contain non-empty strings without surrounding whitespace."
    );
  }
  return new Set(values);
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

function toBootstrapTool(rawTool, paramsSchema) {
  const normalized = validateCustomToolDefinition(rawTool);
  if (!isPlainObject(paramsSchema)) {
    throw new Error("Internal custom tool parameter schema must be an object.");
  }
  const toolId = normalizeCustomToolId(normalized.run_id);
  const executionMode = normalized.execution_mode;
  const capabilities = capabilitiesForExecutionMode(executionMode);
  const extraInputs = normalized.extra_inputs;
  return {
    tool_id: toolId,
    name: normalized.name,
    schema_version: "custom-1.0",
    execution_capabilities: capabilities,
    taxonomic_scope: {},
    compatibility_rules: [],
    method_summary: "User-provided external Docker inference tool.",
    method_keywords: ["custom", "docker"],
    assumes: "external_docker",
    accepts: [],
    required_extras: [...extraInputs],
    optional_extras: [],
    conditional_required_extras: [],
    publication: [],
    first_author: "User-provided",
    year: null,
    implementation_url: "",
    docker_image: normalized.docker_image,
    outputs: {
      ...normalized.outputs,
      evidence: CUSTOM_TOOL_EVIDENCE,
    },
    progress: { kind: "none" },
    artifacts_aux: [],
    params_schema: paramsSchema,
    default_params: {},
    spec: {
      ...normalized,
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

export function addCustomToolDefinition(rawTool, paramsSchema = {}) {
  const normalizedRaw = validateCustomToolDefinition(rawTool);
  if (!isPlainObject(paramsSchema)) {
    throw new Error("Internal custom tool parameter schema must be an object.");
  }
  const toolId = normalizeCustomToolId(normalizedRaw.run_id);

  const existingTools = rawCustomTools().map((tool) =>
    validateCustomToolDefinition(tool)
  );
  state.customTools = existingTools.filter(
    (item) => normalizeCustomToolId(item.run_id) !== toolId
  );
  state.customTools.push(normalizedRaw);

  const tools = bootstrapTools().filter((item) => item.tool_id !== toolId);
  tools.push(toBootstrapTool(normalizedRaw, paramsSchema));
  state.bootstrap.tools = tools.sort((left, right) =>
    String(left.tool_id).localeCompare(String(right.tool_id))
  );

  if (state.preflightReport?.catalog) {
    removeCatalogEntry(state.preflightReport, toolId);
    const warning = Array.isArray(state.preflightReport.catalog.warning)
      ? state.preflightReport.catalog.warning
      : [];
    warning.push(optimisticWarningEntry(toolId, normalizedRaw.docker_image));
    state.preflightReport.catalog.warning = warning;
  }
  if (Array.isArray(state.eligibleToolIds) && !state.eligibleToolIds.includes(toolId)) {
    state.eligibleToolIds.push(toolId);
  }
  return toolId;
}

export function customToolsPayload(selectedToolIds = null) {
  let selected = null;
  if (selectedToolIds !== null) {
    selected = selectedToolIdSet(selectedToolIds);
  }
  const normalizedTools = rawCustomTools().map((tool) =>
    validateCustomToolDefinition(tool)
  );
  const tools = normalizedTools.filter((tool) => {
    if (!selected) {
      return true;
    }
    return selected.has(normalizeCustomToolId(tool.run_id));
  });
  if (!tools.length) {
    return null;
  }
  return {
    tools: tools.map((tool) => {
      const outputs = normalizeCustomToolOutputs(tool.outputs);
      return {
        run_id: tool.run_id,
        name: tool.name,
        docker_image: tool.docker_image,
        execution_mode: tool.execution_mode,
        extra_inputs: [...tool.extra_inputs],
        outputs,
      };
    }),
  };
}

export function removeCustomToolDefinition(rawToolId) {
  const toolId = validateCanonicalCustomToolId(rawToolId);
  const normalizedTools = rawCustomTools().map((tool) =>
    validateCustomToolDefinition(tool)
  );
  const before = normalizedTools.length;
  state.customTools = normalizedTools.filter(
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
  const selected = selectedToolIdSet(selectedToolIds);
  let removed = 0;
  const normalizedTools = rawCustomTools().map((tool) =>
    validateCustomToolDefinition(tool)
  );
  for (const tool of normalizedTools) {
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
  const selectedModeInput = document.querySelector("input[name='custom-tool-execution-mode']:checked");
  if (!selectedModeInput) {
    throw new Error("Execution mode must be selected explicitly.");
  }
  if (selectedModeInput?.disabled) {
    throw new Error("Selected execution mode is not available for the current Step 1 inputs.");
  }
  const executionMode = selectedModeInput.value;
  if (!CAPABILITIES.includes(executionMode)) {
    throw new Error("Execution mode is invalid.");
  }
  const neededExtras = parseExtraInputs(
    document.getElementById("custom-tool-needed-extras")?.value || ""
  );
  const runId = validateCanonicalRunId(
    document.getElementById("custom-tool-run-id")?.value
  );
  const name = requireCanonicalString(
    { name: document.getElementById("custom-tool-name")?.value },
    "name"
  );
  const runtimeParams = readRuntimeParamRows();
  const toolId = normalizeCustomToolId(runId);
  const directedValue =
    document.getElementById("custom-tool-output-directed")?.value || "";
  if (!new Set(["true", "false"]).has(directedValue)) {
    throw new Error("Directionality must be selected explicitly.");
  }
  const signValue =
    document.getElementById("custom-tool-output-sign")?.value || "";
  if (!OUTPUT_SIGN_SEMANTICS.has(signValue)) {
    throw new Error("Sign semantics must be selected explicitly.");
  }
  const tool = {
    run_id: runId,
    name,
    docker_image: customToolDockerImageFromForm(),
    execution_mode: executionMode,
    extra_inputs: neededExtras.map((item) => item.input),
    outputs: {
      directed: directedValue === "true",
      sign: signValue,
    },
  };
  return {
    tool,
    paramsSchema: schemaFromRuntimeParams(runtimeParams),
    run: {
      run_id: runId,
      tool_id: toolId,
      params: runtimeParams,
      execution: { mode: executionMode },
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
