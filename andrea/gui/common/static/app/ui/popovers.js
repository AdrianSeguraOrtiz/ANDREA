import {
  $,
  appendLinkifiedText,
  normalizeHelpText,
  normalizeUrlOrDoi,
} from "../core/dom.js";

export function buildInfoTooltip({
  title,
  description = "",
  example = "",
  fields = [],
  chips = [],
  sections = [],
  raw = null,
}) {
  return {
    title: String(title || "Info"),
    description: String(description || ""),
    example: String(example || ""),
    fields: Array.isArray(fields) ? fields : [],
    chips: Array.isArray(chips) ? chips : [],
    sections: Array.isArray(sections) ? sections : [],
    raw,
  };
}

export function readHelpPayload(node) {
  const raw = String(node?.dataset?.help || "").trim();
  if (!raw) {
    return null;
  }
  try {
    return JSON.parse(raw);
  } catch (_err) {
    return null;
  }
}

export function closeInfoTooltip() {
  const popover = $("info-popover");
  const content = $("info-popover-content");
  if (popover) {
    popover.classList.add("hidden");
  }
  if (content) {
    content.innerHTML = "";
  }
}

export function hideInfoTooltip() {
  closeInfoTooltip();
}

function renderInfoValue(parent, field) {
  const links = Array.isArray(field.links) ? field.links.filter((item) => item && typeof item === "object") : [];
  if (links.length) {
    const ul = document.createElement("ul");
    ul.className = "info-link-list";
    for (const item of links) {
      const rawUrl = String(item.url || "").trim();
      const href = normalizeUrlOrDoi(rawUrl);
      const li = document.createElement("li");
      if (href) {
        const anchor = document.createElement("a");
        anchor.href = href;
        anchor.target = "_blank";
        anchor.rel = "noopener noreferrer";
        anchor.textContent = String(item.label || rawUrl || href).trim() || href;
        li.appendChild(anchor);
      } else {
        li.textContent = String(item.label || rawUrl || "-").trim() || "-";
      }
      ul.appendChild(li);
    }
    parent.appendChild(ul);
    return;
  }

  if (field.link && typeof field.link === "object") {
    const rawUrl = String(field.link.url || "").trim();
    const href = normalizeUrlOrDoi(rawUrl);
    if (href) {
      const anchor = document.createElement("a");
      anchor.href = href;
      anchor.target = "_blank";
      anchor.rel = "noopener noreferrer";
      anchor.textContent = String(field.link.label || rawUrl || href).trim() || href;
      parent.appendChild(anchor);
      return;
    }
  }

  appendLinkifiedText(parent, String(field.value ?? "-"));
}

function renderInfoFields(content, fields) {
  const normalized = Array.isArray(fields)
    ? fields.filter((field) => field && typeof field === "object" && String(field.label || "").trim())
    : [];
  if (!normalized.length) {
    return;
  }
  const list = document.createElement("dl");
  list.className = "info-kv-list";
  for (const field of normalized) {
    const label = String(field.label || "").trim();
    const term = document.createElement("dt");
    term.textContent = label;
    const valueNode = document.createElement("dd");
    renderInfoValue(valueNode, field);
    list.appendChild(term);
    list.appendChild(valueNode);
  }
  content.appendChild(list);
}

function renderInfoChips(content, chips) {
  const normalized = Array.isArray(chips)
    ? chips.map((chip) => {
        if (chip && typeof chip === "object") {
          return {
            label: String(chip.label || "").trim(),
            value: String(chip.value || "").trim(),
            tone: String(chip.tone || "").trim(),
          };
        }
        return { label: "", value: String(chip || "").trim(), tone: "" };
      }).filter((chip) => chip.value)
    : [];
  if (!normalized.length) {
    return;
  }
  const row = document.createElement("div");
  row.className = "info-chip-row";
  for (const chip of normalized) {
    const node = document.createElement("span");
    node.className = `info-chip${chip.tone ? ` tone-${chip.tone}` : ""}`;
    if (chip.label) {
      const label = document.createElement("span");
      label.className = "info-chip-label";
      label.textContent = chip.label;
      node.appendChild(label);
    }
    const value = document.createElement("span");
    value.textContent = chip.value;
    node.appendChild(value);
    row.appendChild(node);
  }
  content.appendChild(row);
}

function formatParamDefault(value) {
  if (value === null) {
    return "null";
  }
  if (value === undefined) {
    return "-";
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
}

function isInfoPlainObject(value) {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function formatCompactJson(value) {
  if (value === null || value === undefined) {
    return formatParamDefault(value);
  }
  if (Array.isArray(value)) {
    return `array (${value.length})`;
  }
  if (isInfoPlainObject(value)) {
    return `object (${Object.keys(value).length})`;
  }
  return formatParamDefault(value);
}

function formatParamType(schema) {
  const type = String(schema?.type || "-");
  if (Array.isArray(schema?.enum) && schema.enum.length) {
    return `${type} (${schema.enum.length} values)`;
  }
  if (Array.isArray(schema?.oneOf) && schema.oneOf.length) {
    const variants = schema.oneOf
      .map((item) => String(item?.type || "").trim())
      .filter(Boolean);
    return variants.length ? `${type}: ${variants.join(" | ")}` : type;
  }
  return type;
}

function paramConstraintItems(schema) {
  const out = [];
  if (schema?.min !== undefined) {
    out.push(`${schema.exclusive_min ? ">" : ">="}${schema.min}`);
  }
  if (schema?.max !== undefined) {
    out.push(`${schema.exclusive_max ? "<" : "<="}${schema.max}`);
  }
  if (schema?.required === true) {
    out.push("required");
  }
  return out;
}

function appendInfoBadges(parent, values) {
  const normalized = Array.isArray(values)
    ? values.map((item) => String(item || "").trim()).filter(Boolean)
    : [];
  if (!normalized.length) {
    return;
  }
  const wrap = document.createElement("span");
  wrap.className = "info-param-badges";
  for (const item of normalized) {
    const badge = document.createElement("span");
    badge.className = "info-param-badge";
    badge.textContent = item;
    wrap.appendChild(badge);
  }
  parent.appendChild(wrap);
}

function limitedInfoBadges(values, limit = 12) {
  const normalized = Array.isArray(values)
    ? values.map((item) => String(item || "").trim()).filter(Boolean)
    : [];
  if (normalized.length <= limit) {
    return normalized;
  }
  return [
    ...normalized.slice(0, limit),
    `${normalized.length - limit} more`,
  ];
}

function appendParamDefault(parent, value) {
  if (value === undefined) {
    return;
  }
  if (Array.isArray(value) || isInfoPlainObject(value)) {
    const details = document.createElement("details");
    details.className = "info-param-default-details";
    const summary = document.createElement("summary");
    summary.textContent = `Default: ${formatCompactJson(value)}`;
    details.appendChild(summary);
    const pre = document.createElement("pre");
    pre.className = "info-json info-param-json";
    pre.textContent = JSON.stringify(value, null, 2);
    details.appendChild(pre);
    parent.appendChild(details);
    return;
  }
  const item = document.createElement("span");
  item.className = "info-param-default";
  const label = document.createElement("span");
  label.textContent = "Default: ";
  const code = document.createElement("code");
  code.textContent = formatParamDefault(value);
  item.appendChild(label);
  item.appendChild(code);
  parent.appendChild(item);
}

function appendParamEnum(parent, schema) {
  if (!Array.isArray(schema?.enum) || !schema.enum.length) {
    return;
  }
  const row = document.createElement("div");
  row.className = "info-param-enum";
  const label = document.createElement("strong");
  label.textContent = "Allowed values";
  row.appendChild(label);
  appendInfoBadges(row, limitedInfoBadges(schema.enum));
  parent.appendChild(row);
}

function renderParamNode(parent, name, schema, depth = 0) {
  if (!schema || typeof schema !== "object" || Array.isArray(schema)) {
    return;
  }
  const details = document.createElement("details");
  details.className = `info-param-node depth-${Math.min(depth, 3)}`;
  details.open = depth > 0 && depth < 2;

  const summary = document.createElement("summary");
  const title = document.createElement("span");
  title.className = "info-param-name";
  title.textContent = String(name);
  summary.appendChild(title);

  const summaryMeta = document.createElement("span");
  summaryMeta.className = "info-param-summary";
  appendInfoBadges(summaryMeta, [formatParamType(schema), ...paramConstraintItems(schema)]);
  if (schema.default !== undefined && !Array.isArray(schema.default) && !isInfoPlainObject(schema.default)) {
    const defaultBadge = document.createElement("span");
    defaultBadge.className = "info-param-badge default";
    defaultBadge.textContent = `default ${formatParamDefault(schema.default)}`;
    summaryMeta.appendChild(defaultBadge);
  } else if (schema.default !== undefined) {
    const defaultBadge = document.createElement("span");
    defaultBadge.className = "info-param-badge default";
    defaultBadge.textContent = `default ${formatCompactJson(schema.default)}`;
    summaryMeta.appendChild(defaultBadge);
  }
  summary.appendChild(summaryMeta);
  details.appendChild(summary);

  const body = document.createElement("div");
  body.className = "info-param-body";
  const description = String(schema.description || "").trim();
  if (description) {
    const p = document.createElement("p");
    appendLinkifiedText(p, description);
    body.appendChild(p);
  }
  appendParamEnum(body, schema);
  appendParamDefault(body, schema.default);

  const properties = isInfoPlainObject(schema.properties) ? Object.entries(schema.properties) : [];
  if (properties.length) {
    const nested = document.createElement("div");
    nested.className = "info-param-children";
    const nestedTitle = document.createElement("div");
    nestedTitle.className = "info-param-children-title";
    nestedTitle.textContent = "Nested parameters";
    nested.appendChild(nestedTitle);
    for (const [childName, childSchema] of properties) {
      renderParamNode(nested, childName, childSchema, depth + 1);
    }
    body.appendChild(nested);
  }
  details.appendChild(body);
  parent.appendChild(details);
}

function renderInfoParams(content, params) {
  if (!params || typeof params !== "object" || Array.isArray(params)) {
    return;
  }
  const entries = Object.entries(params).filter(([, schema]) => schema && typeof schema === "object");
  if (!entries.length) {
    return;
  }
  const tree = document.createElement("div");
  tree.className = "info-param-tree";
  for (const [key, schema] of entries) {
    renderParamNode(tree, key, schema, 0);
  }
  content.appendChild(tree);
}

function renderInfoConditions(content, label, conditions) {
  const normalized = Array.isArray(conditions)
    ? conditions
        .filter((item) => item && typeof item === "object")
        .map((item) => ({
          input: String(item.input || item.target || "").trim(),
          condition: String(item.condition || item.when || "").trim(),
          message: String(item.message || item.description || "").trim(),
        }))
        .filter((item) => item.input || item.condition || item.message)
    : [];
  const group = document.createElement("div");
  group.className = "info-condition-group";
  const heading = document.createElement("div");
  heading.className = "info-subtitle";
  heading.textContent = String(label || "Conditional requirements");
  group.appendChild(heading);
  if (!normalized.length) {
    const empty = document.createElement("p");
    empty.className = "info-empty";
    empty.textContent = "none";
    group.appendChild(empty);
    content.appendChild(group);
    return;
  }
  const list = document.createElement("div");
  list.className = "info-condition-list";
  for (const item of normalized) {
    const block = document.createElement("article");
    block.className = "info-condition";
    if (item.input) {
      const input = document.createElement("div");
      input.className = "info-condition-input";
      input.textContent = item.input;
      block.appendChild(input);
    }
    if (item.condition) {
      const condition = document.createElement("code");
      condition.className = "info-condition-expression";
      condition.textContent = item.condition;
      block.appendChild(condition);
    }
    if (item.message) {
      const message = document.createElement("p");
      appendLinkifiedText(message, item.message);
      block.appendChild(message);
    }
    list.appendChild(block);
  }
  group.appendChild(list);
  content.appendChild(group);
}

function normalizeInfoArtifact(item) {
  if (!item || typeof item !== "object") {
    return null;
  }
  const path = String(item.path_pattern || item.path || item.id || item.artifact || "").trim();
  const kind = String(item.kind || "").trim();
  const description = String(item.description || item.notes || "").trim();
  let requirement = "";
  if (item.require_non_empty === true) {
    requirement = "non-empty";
  } else if (item.require_non_empty === false) {
    requirement = "empty allowed";
  }
  if (!path && !kind && !description && !requirement) {
    return null;
  }
  return { path, kind, requirement, description };
}

function renderInfoArtifacts(content, label, artifacts) {
  const normalized = Array.isArray(artifacts)
    ? artifacts.map((item) => normalizeInfoArtifact(item)).filter(Boolean)
    : [];
  const group = document.createElement("div");
  group.className = "info-artifact-group";
  const heading = document.createElement("div");
  heading.className = "info-subtitle";
  heading.textContent = String(label || "Auxiliary artifacts");
  group.appendChild(heading);
  if (!normalized.length) {
    const empty = document.createElement("p");
    empty.className = "info-empty";
    empty.textContent = "none";
    group.appendChild(empty);
    content.appendChild(group);
    return;
  }
  const list = document.createElement("div");
  list.className = "info-artifact-list";
  for (const artifact of normalized) {
    const card = document.createElement("article");
    card.className = "info-artifact";

    const head = document.createElement("div");
    head.className = "info-artifact-head";
    const path = document.createElement("code");
    path.className = "info-artifact-path";
    path.textContent = artifact.path || "-";
    head.appendChild(path);
    appendInfoBadges(head, [artifact.kind || "artifact", artifact.requirement]);
    card.appendChild(head);

    if (artifact.description) {
      const description = document.createElement("p");
      appendLinkifiedText(description, artifact.description);
      card.appendChild(description);
    }
    list.appendChild(card);
  }
  group.appendChild(list);
  content.appendChild(group);
}

function renderInfoList(content, items) {
  const normalized = Array.isArray(items)
    ? items.map((item) => String(item || "").trim()).filter(Boolean)
    : [];
  if (!normalized.length) {
    const empty = document.createElement("p");
    empty.className = "info-empty";
    empty.textContent = "none";
    content.appendChild(empty);
    return;
  }
  const ul = document.createElement("ul");
  ul.className = "info-bullet-list";
  for (const item of normalized) {
    const li = document.createElement("li");
    appendLinkifiedText(li, item);
    ul.appendChild(li);
  }
  content.appendChild(ul);
}

function renderInfoJson(content, value) {
  if (value === null || value === undefined) {
    return;
  }
  const pre = document.createElement("pre");
  pre.className = "info-json";
  pre.textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  content.appendChild(pre);
}

function renderInfoSection(content, section) {
  if (!section || typeof section !== "object") {
    return;
  }
  const title = String(section.title || "").trim();
  if (!title) {
    return;
  }
  const details = document.createElement("details");
  details.className = "info-details";
  if (section.open !== false) {
    details.open = true;
  }
  const summary = document.createElement("summary");
  summary.textContent = title;
  details.appendChild(summary);

  const body = document.createElement("div");
  body.className = "info-details-body";
  const text = normalizeHelpText(String(section.text || "")).trim();
  if (text) {
    const p = document.createElement("p");
    appendLinkifiedText(p, text);
    body.appendChild(p);
  }
  if (Array.isArray(section.chips)) {
    renderInfoChips(body, section.chips);
  }
  if (Array.isArray(section.fields)) {
    renderInfoFields(body, section.fields);
  }
  if (Array.isArray(section.items)) {
    renderInfoList(body, section.items);
  }
  if (Array.isArray(section.conditions)) {
    renderInfoConditions(body, section.conditionsLabel, section.conditions);
  }
  if (Array.isArray(section.artifacts)) {
    renderInfoArtifacts(body, section.artifactsLabel, section.artifacts);
  }
  renderInfoParams(body, section.params);
  renderInfoJson(body, section.json);
  details.appendChild(body);
  content.appendChild(details);
}

export function showInfoTooltip(payload) {
  if (!payload || typeof payload !== "object") {
    return;
  }
  const popover = $("info-popover");
  const content = $("info-popover-content");
  if (!popover || !content) {
    return;
  }
  const titleText = normalizeHelpText(String(payload.title || "Info")).trim();
  const description = normalizeHelpText(String(payload.description || "")).trim();
  const example = normalizeHelpText(String(payload.example || "")).trim();
  const fields = Array.isArray(payload.fields) ? payload.fields : [];
  const chips = Array.isArray(payload.chips) ? payload.chips : [];
  const sections = Array.isArray(payload.sections) ? payload.sections : [];
  if (!titleText && !description && !example && !fields.length && !chips.length && !sections.length && !payload.raw) {
    return;
  }

  content.innerHTML = "";
  if (titleText) {
    const title = document.createElement("h4");
    title.textContent = titleText;
    content.appendChild(title);
  }

  if (description) {
    if (description.includes("\n")) {
      const block = document.createElement("div");
      block.className = "info-multiline";
      const lines = description.split("\n");
      for (const line of lines) {
        const p = document.createElement("p");
        appendLinkifiedText(p, line);
        block.appendChild(p);
      }
      content.appendChild(block);
    } else {
      const p = document.createElement("p");
      appendLinkifiedText(p, description);
      content.appendChild(p);
    }
  }
  renderInfoChips(content, chips);
  if (fields.length) {
    renderInfoFields(content, fields);
  }
  for (const section of sections) {
    renderInfoSection(content, section);
  }

  if (example) {
    const label = document.createElement("p");
    label.textContent = "Example";
    content.appendChild(label);
    const pre = document.createElement("pre");
    pre.textContent = example;
    content.appendChild(pre);
  }
  if (payload.raw !== null && payload.raw !== undefined) {
    renderInfoSection(content, {
      title: "Raw Spec",
      open: false,
      json: payload.raw,
    });
  }
  popover.classList.remove("hidden");
}

export function initInfoPopover() {
  $("info-popover-close")?.addEventListener("click", () => closeInfoTooltip());
  $("info-popover")?.addEventListener("click", (event) => {
    if (event.target && event.target.id === "info-popover") {
      closeInfoTooltip();
    }
  });
}
