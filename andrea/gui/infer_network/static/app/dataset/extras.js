import { $, formatBytes } from "../core/dom.js";
import { pushToast } from "../ui/toasts.js";
import { buildInfoTooltip, readHelpPayload, showInfoTooltip } from "../ui/popovers.js";
import { state } from "../core/state.js";
import { getExtraMeta, setStatusBadge, validateOptionalFile } from "./validation.js";

let onDatasetChangedFn = null;

export function initExtras({ onDatasetChanged }) {
  onDatasetChangedFn = onDatasetChanged;
}

function notifyDatasetChanged() {
  if (typeof onDatasetChangedFn === "function") {
    onDatasetChangedFn();
  }
}

export function getExtraRows() {
  return Array.from(document.querySelectorAll(".extra-row"));
}

export function updateExtrasEmptyState() {
  const hasExtras = getExtraRows().length > 0;
  $("extras-empty").style.display = hasExtras ? "none" : "block";
}

export function listExtraKeys() {
  return Array.isArray(state.bootstrap?.extra_inputs)
    ? state.bootstrap.extra_inputs.map((item) => item.key)
    : [];
}

export function listProvidedExtraKeys() {
  return new Set(
    getExtraRows()
      .map((row) => {
        const key = String(row.querySelector(".extra-key")?.value || "").trim();
        const file = row.querySelector(".extra-file")?.files?.[0] || null;
        return key && file ? key : "";
      })
      .filter(Boolean)
  );
}

function listAddedExtraKeys() {
  return new Set(
    getExtraRows()
      .map((row) => String(row.querySelector(".extra-key")?.value || "").trim())
      .filter(Boolean)
  );
}

function formatCondition(condition) {
  if (!condition || typeof condition !== "object") {
    return "";
  }
  const opLabels = {
    eq: "==",
    ne: "!=",
    in: "in",
    not_in: "not in",
    gt: ">",
    gte: ">=",
    lt: "<",
    lte: "<=",
  };
  const left = condition.param
    ? `param.${String(condition.param).trim()}`
    : condition.execution
      ? `execution.${String(condition.execution).trim()}`
      : "";
  const op =
    opLabels[String(condition.op || "").trim()] ||
    String(condition.op || "").trim();
  const value = condition.value === undefined ? "" : JSON.stringify(condition.value);
  return left && op ? `${left} ${op} ${value}` : "";
}

function relationLabel(relation) {
  if (relation === "conditional") {
    return "Conditional required";
  }
  return relation === "required" ? "Required" : "Optional";
}

function usageToolCount(meta) {
  const usedBy =
    meta?.used_by && typeof meta.used_by === "object" ? meta.used_by : {};
  const ids = new Set();
  for (const relation of ["required", "optional", "conditional"]) {
    const items = Array.isArray(usedBy[relation]) ? usedBy[relation] : [];
    for (const item of items) {
      const id = String(item?.tool_id || item?.name || "").trim();
      if (id) {
        ids.add(id);
      }
    }
  }
  return ids.size;
}

function appendDetailField(parent, labelText, valueText, { code = false } = {}) {
  const normalized = String(valueText ?? "").trim();
  if (!normalized) {
    return;
  }
  const label = document.createElement("dt");
  label.textContent = labelText;
  const value = document.createElement("dd");
  if (code) {
    const codeEl = document.createElement("code");
    codeEl.textContent = normalized;
    value.appendChild(codeEl);
  } else {
    value.textContent = normalized;
  }
  parent.append(label, value);
}

function renderUsageDetail(detailPanel, item, relation) {
  detailPanel.innerHTML = "";
  const title = document.createElement("div");
  title.className = "input-usage-detail-head";
  const name = document.createElement("strong");
  name.textContent = String(item?.name || item?.tool_id || "").trim();
  const badge = document.createElement("span");
  badge.className = `input-usage-relation ${relation}`;
  badge.textContent = relationLabel(relation);
  title.append(name, badge);

  const usage = document.createElement("p");
  usage.textContent = String(item?.usage || "").trim() || "No usage note available.";

  detailPanel.append(title, usage);

  if (relation === "conditional") {
    const condition =
      item?.condition && typeof item.condition === "object" ? item.condition : {};
    const details = document.createElement("dl");
    details.className = "input-usage-detail-meta";
    appendDetailField(details, "Condition", formatCondition(condition), { code: true });
    appendDetailField(details, "Message", condition.message);
    appendDetailField(
      details,
      "Field",
      condition.param
        ? `param.${condition.param}`
        : condition.execution
          ? `execution.${condition.execution}`
          : ""
    );
    appendDetailField(details, "Operator", condition.op);
    if (condition.value !== undefined) {
      appendDetailField(details, "Value", JSON.stringify(condition.value), { code: true });
    }
    if (details.children.length) {
      detailPanel.appendChild(details);
    }
  }

  detailPanel.hidden = false;
}

function setExtraRowState(row, stateName) {
  row.classList.remove("missing", "valid", "invalid");
  if (stateName) {
    row.classList.add(stateName);
  }
}

function toolTag(item, relation, detailPanel) {
  const tag = document.createElement("button");
  tag.type = "button";
  tag.className = `input-tool-tag ${relation}`;
  tag.textContent = String(item?.name || item?.tool_id || "").trim();
  tag.addEventListener("click", () => {
    const siblings =
      tag
        .closest(".extra-input-card")
        ?.querySelectorAll(".input-tool-tag.active") || [];
    for (const sibling of siblings) {
      sibling.classList.remove("active");
    }
    tag.classList.add("active");
    renderUsageDetail(detailPanel, item, relation);
  });
  return tag;
}

function renderToolUsage(meta) {
  const usedBy =
    meta?.used_by && typeof meta.used_by === "object" ? meta.used_by : {};
  const groups = [
    ["required", "Required by"],
    ["optional", "Optional for"],
    ["conditional", "Conditional for"],
  ];
  const host = document.createElement("div");
  host.className = "input-usage-groups";
  const detailPanel = document.createElement("div");
  detailPanel.className = "input-usage-detail";
  detailPanel.hidden = true;
  let hasUsage = false;
  for (const [relation, label] of groups) {
    const items = Array.isArray(usedBy[relation]) ? usedBy[relation] : [];
    if (!items.length) {
      continue;
    }
    hasUsage = true;
    const group = document.createElement("div");
    group.className = "input-usage-group";
    const title = document.createElement("div");
    title.className = "input-usage-title";
    title.textContent = label;
    const tags = document.createElement("div");
    tags.className = "input-tool-tags";
    for (const item of items) {
      tags.appendChild(toolTag(item, relation, detailPanel));
    }
    group.append(title, tags);
    host.appendChild(group);
  }
  if (!hasUsage) {
    const empty = document.createElement("div");
    empty.className = "input-usage-empty";
    empty.textContent = "No catalog tool currently declares this input.";
    host.appendChild(empty);
  }
  host.appendChild(detailPanel);
  return host;
}

function syncExtraRowMeta(row) {
  const keyInput = row.querySelector(".extra-key");
  const labelEl = row.querySelector(".extra-key-label");
  const descriptionEl = row.querySelector(".extra-key-description");
  const infoBtn = row.querySelector(".extra-info-btn");
  const fileInput = row.querySelector(".extra-file");
  const fileName = row.querySelector(".extra-file-name");
  const pickerName = row.querySelector(".extra-file-picker-name");
  const statusEl = row.querySelector(".extra-file-status");
  const key = keyInput.value;
  const meta = getExtraMeta(key);
  const description = String(meta?.description || "").trim();
  const example = String(meta?.example || "").trim();
  const payload = buildInfoTooltip({
    title: key,
    description: description || "Additional input file.",
    example,
  });
  labelEl.textContent = key;
  descriptionEl.textContent = description || "Additional input file.";
  infoBtn.dataset.help = JSON.stringify(payload);
  infoBtn.title = payload.description || payload.title || "Input format info";
  const file = fileInput.files && fileInput.files[0] ? fileInput.files[0] : null;
  const fileLabel = file ? `${file.name} (${formatBytes(file.size)})` : "No file selected";
  fileName.textContent = fileLabel;
  if (pickerName) {
    pickerName.textContent = file ? file.name : "No file selected";
  }
  if (!file) {
    setExtraRowState(row, "missing");
    setStatusBadge(statusEl, "err", "Select a valid file before running preflight");
  }
}

async function validateExtraRowFile(row) {
  const keyInput = row.querySelector(".extra-key");
  const fileInput = row.querySelector(".extra-file");
  const statusEl = row.querySelector(".extra-file-status");
  const file = fileInput.files && fileInput.files[0] ? fileInput.files[0] : null;
  const key = keyInput.value;
  if (!file) {
    setExtraRowState(row, "missing");
    setStatusBadge(statusEl, "err", "Select a valid file before running preflight");
    notifyDatasetChanged();
    return;
  }
  try {
    const inspected = await validateOptionalFile(file, key);
    const detail =
      inspected.columns !== undefined
        ? `rows=${inspected.rows}, cols=${inspected.columns}`
        : `rows=${inspected.rows}`;
    setExtraRowState(row, "valid");
    setStatusBadge(statusEl, "ok", `Looks valid (${detail})`);
  } catch (err) {
    setExtraRowState(row, "invalid");
    setStatusBadge(statusEl, "err", `Invalid file: ${err.message}`);
  }
  notifyDatasetChanged();
}

export function refreshExtraSelectOptions() {
  renderExtraInputModalBody();
}

export function closeExtraInputModal() {
  $("extra-input-modal")?.classList.add("hidden");
}

export function openExtraInputModal() {
  renderExtraInputModalBody();
  $("extra-input-modal")?.classList.remove("hidden");
}

function renderExtraInputModalBody() {
  const body = $("extra-input-modal-body");
  if (!body) {
    return;
  }
  const metas = Array.isArray(state.bootstrap?.extra_inputs)
    ? [...state.bootstrap.extra_inputs]
    : [];
  const added = listAddedExtraKeys();
  body.innerHTML = "";
  if (!metas.length) {
    const empty = document.createElement("div");
    empty.className = "muted-box";
    empty.textContent = "No additional input specs are available.";
    body.appendChild(empty);
    return;
  }
  metas.sort((a, b) => {
    const usageDelta = usageToolCount(b) - usageToolCount(a);
    if (usageDelta !== 0) {
      return usageDelta;
    }
    return String(a.key || "").localeCompare(String(b.key || ""));
  });
  for (const meta of metas) {
    const key = String(meta.key || "").trim();
    if (!key) {
      continue;
    }
    const card = document.createElement("article");
    card.className = "extra-input-card";
    const head = document.createElement("div");
    head.className = "extra-input-card-head";
    const title = document.createElement("div");
    title.className = "extra-input-card-title";
    title.textContent = key;
    const headActions = document.createElement("div");
    headActions.className = "extra-input-card-actions";
    const exampleBtn = document.createElement("button");
    exampleBtn.type = "button";
    exampleBtn.className = "info-icon extra-input-example-btn";
    exampleBtn.textContent = "i";
    exampleBtn.setAttribute("aria-label", `Show ${key} example`);
    exampleBtn.addEventListener("click", () => {
      const requiredCols =
        Array.isArray(meta.required_columns) && meta.required_columns.length
          ? meta.required_columns.join(", ")
          : "none";
      showInfoTooltip(
        buildInfoTooltip({
          title: `${key} example`,
          description: `${String(meta.file_kind || "tsv").toUpperCase()} · required columns: ${requiredCols}`,
          example: String(meta.example || "").trim() || "No example available.",
        })
      );
    });
    const addBtn = document.createElement("button");
    addBtn.type = "button";
    addBtn.className = "secondary";
    addBtn.textContent = added.has(key) ? "Added" : "Add";
    addBtn.disabled = added.has(key);
    addBtn.addEventListener("click", () => {
      addOptionalExtraRow(key);
      closeExtraInputModal();
    });
    headActions.append(exampleBtn, addBtn);
    head.append(title, headActions);

    const description = document.createElement("p");
    description.className = "extra-input-description";
    description.textContent = String(meta.description || "Additional input file.").trim();

    const format = document.createElement("div");
    format.className = "extra-input-format";
    const requiredCols = Array.isArray(meta.required_columns) && meta.required_columns.length
      ? meta.required_columns.join(", ")
      : "none";
    format.textContent = `${String(meta.file_kind || "tsv").toUpperCase()} · required columns: ${requiredCols}`;

    card.append(head, description, format, renderToolUsage(meta));
    body.appendChild(card);
  }
}

export function addOptionalExtraRow(preferredKey = null) {
  const allKeys = listExtraKeys();
  const used = listAddedExtraKeys();
  const available = allKeys.filter((key) => !used.has(key));
  const selectedKey =
    preferredKey && available.includes(preferredKey) ? preferredKey : available[0];

  if (!selectedKey) {
    pushToast({
      title: "Additional inputs",
      message: "All additional inputs are already added.",
      kind: "warning",
      ttlMs: 4500,
    });
    return;
  }

  const template = $("extra-template");
  const node = template.content.firstElementChild.cloneNode(true);
  const keyInput = node.querySelector(".extra-key");
  const removeBtn = node.querySelector(".remove-extra");
  keyInput.value = selectedKey;

  const fileInput = node.querySelector(".extra-file");
  const infoBtn = node.querySelector(".extra-info-btn");
  infoBtn.addEventListener("click", () => {
    const payload = readHelpPayload(infoBtn);
    if (payload) {
      showInfoTooltip(payload);
    }
  });
  fileInput.addEventListener("change", async () => {
    syncExtraRowMeta(node);
    await validateExtraRowFile(node);
  });
  removeBtn.addEventListener("click", () => {
    node.remove();
    updateExtrasEmptyState();
    renderExtraInputModalBody();
    notifyDatasetChanged();
  });

  $("extras-list").appendChild(node);
  updateExtrasEmptyState();
  syncExtraRowMeta(node);
  renderExtraInputModalBody();
  notifyDatasetChanged();
}
