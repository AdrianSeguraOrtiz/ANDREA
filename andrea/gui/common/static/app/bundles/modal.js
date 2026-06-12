import { $, formatBytes } from "../core/dom.js";

const DEFAULT_IDS = {
  modal: "bundle-modal",
  close: "bundle-modal-close",
  title: "bundle-modal-title",
  body: "bundle-modal-body",
};

function id(ids, key) {
  return ids?.[key] || DEFAULT_IDS[key];
}

function node(ids, key) {
  return $(id(ids, key));
}

function asList(value) {
  return Array.isArray(value) ? value.map((item) => String(item || "")).filter(Boolean) : [];
}

function appendText(parent, tag, className, text) {
  const child = document.createElement(tag);
  if (className) {
    child.className = className;
  }
  child.textContent = text;
  parent.appendChild(child);
  return child;
}

function replaceChildren(parent, children = []) {
  parent.innerHTML = "";
  for (const child of children) {
    parent.appendChild(child);
  }
}

function bundleVariantId(bundle) {
  return String(bundle.variant_id || bundle.dataset_id || "").trim();
}

function bundleVariantLabel(bundle) {
  return String(bundleVariantId(bundle) || bundle.display_id || bundle.label || bundle.id || "").trim();
}

function commonPrefix(values) {
  if (!values.length) {
    return "";
  }
  let prefix = values[0];
  for (const value of values.slice(1)) {
    while (prefix && !value.startsWith(prefix)) {
      prefix = prefix.slice(0, -1);
    }
  }
  return prefix;
}

function compactVariantLabels(bundles) {
  const labels = bundles.map(bundleVariantLabel);
  if (labels.length <= 1) {
    return labels.map((label) => {
      const parts = label.split("__");
      return parts.length >= 4 ? parts.slice(1).join("__") : label;
    });
  }
  const prefix = commonPrefix(labels);
  const cut = Math.max(prefix.lastIndexOf("__") + 2, prefix.lastIndexOf("/") + 1);
  return labels.map((label) => {
    const compact = cut > 0 ? label.slice(cut) : label;
    return compact || label;
  });
}

function baseBundleLabel(bundle) {
  let label = String(bundle.label || bundle.id || "");
  const variant = bundleVariantId(bundle);
  if (variant) {
    for (const separator of [" - ", " · ", ": "]) {
      const suffix = `${separator}${variant}`;
      if (label.endsWith(suffix)) {
        label = label.slice(0, -suffix.length);
        break;
      }
    }
  }
  return label || String(bundle.id || "");
}

function bundleMetaLine(bundle) {
  const bits = [];
  if (Number(bundle.file_count || 0) > 0) {
    bits.push(`${bundle.file_count} file(s)`);
  }
  const size = formatBytes(bundle.total_size_bytes);
  if (size) {
    bits.push(size);
  }
  return bits.join(" · ");
}

function sharedCliNote(bundles) {
  const notes = bundles
    .map((bundle) => String(bundle.cli_note || "").trim())
    .filter(Boolean);
  if (!notes.length) {
    return "";
  }
  return notes.every((note) => note === notes[0]) ? notes[0] : "";
}

function renderStatusNode(bundle) {
  const status = document.createElement("span");
  status.className = `bundle-status ${bundle.available ? "ok" : "blocked"}`;
  status.textContent = bundle.available ? "available" : "not ready";
  return status;
}

function renderDownstream(bundle) {
  const downstream = asList(bundle.intended_downstream_commands);
  if (!downstream.length) {
    return [];
  }
  const label = document.createElement("div");
  label.className = "bundle-section-label";
  label.textContent = "Designed for";
  const chips = document.createElement("div");
  chips.className = "bundle-chip-row";
  for (const item of downstream) {
    appendText(chips, "span", "bundle-chip", item);
  }
  return [label, chips];
}

function renderContents(bundle) {
  const contents = asList(bundle.contents_summary);
  if (!contents.length) {
    return [];
  }
  const label = document.createElement("div");
  label.className = "bundle-section-label";
  label.textContent = "Contents";
  const list = document.createElement("ul");
  list.className = "bundle-content-list";
  for (const item of contents) {
    appendText(list, "li", "", item);
  }
  return [label, list];
}

function renderMissing(bundle) {
  const missing = asList(bundle.missing_required);
  if (!missing.length || bundle.available) {
    return [];
  }
  const label = document.createElement("div");
  label.className = "bundle-section-label warning";
  label.textContent = "Missing";
  const list = document.createElement("ul");
  list.className = "bundle-missing-list";
  for (const item of missing) {
    appendText(list, "li", "", item);
  }
  return [label, list];
}

function normalizeReadinessStatus(value) {
  const status = String(value || "").trim().toLowerCase();
  if (["ready", "done", "available"].includes(status)) {
    return "ready";
  }
  if (["failed", "error"].includes(status)) {
    return "failed";
  }
  if (["exporting", "running", "validating", "writing"].includes(status)) {
    return "working";
  }
  if (["not required", "not_required", "optional"].includes(status)) {
    return "neutral";
  }
  return "pending";
}

function renderReadiness(bundle) {
  const items = Array.isArray(bundle.readiness) ? bundle.readiness : [];
  if (!items.length) {
    return [];
  }
  const label = document.createElement("div");
  label.className = "bundle-section-label";
  label.textContent = "Status";
  const chips = document.createElement("div");
  chips.className = "bundle-readiness-row";
  for (const item of items) {
    const chip = document.createElement("span");
    const status = normalizeReadinessStatus(item?.status);
    chip.className = `bundle-readiness-chip ${status}`;
    const chipLabel = String(item?.label || "").trim();
    const chipStatus = String(item?.status || "").trim().replace(/_/g, " ");
    chip.textContent = chipLabel ? `${chipLabel}: ${chipStatus || status}` : chipStatus || status;
    chips.appendChild(chip);
  }
  return [label, chips];
}

function applyAvailabilityClass(card, bundle) {
  card.className = `bundle-card${bundle.available ? " is-available" : " is-unavailable"}`;
}

function renderBundleCard({ bundle, downloadUrlForBundle, modalCliNote = "" }) {
  const card = document.createElement("article");
  applyAvailabilityClass(card, bundle);

  const head = document.createElement("div");
  head.className = "bundle-card-head";
  const titleWrap = document.createElement("div");
  appendText(titleWrap, "h5", "", String(bundle.label || bundle.id));
  appendText(titleWrap, "div", "bundle-id", String(bundle.display_id || bundle.id || ""));
  head.appendChild(titleWrap);
  head.appendChild(renderStatusNode(bundle));
  card.appendChild(head);

  appendText(card, "p", "bundle-purpose", String(bundle.purpose || ""));

  const meta = bundleMetaLine(bundle);
  if (meta) {
    appendText(card, "div", "bundle-meta", meta);
  }

  card.append(...renderDownstream(bundle));
  card.append(...renderReadiness(bundle));
  card.append(...renderContents(bundle));
  card.append(...renderMissing(bundle));

  if (bundle.cli_note && bundle.cli_note !== modalCliNote) {
    appendText(card, "p", "bundle-cli-note", String(bundle.cli_note));
  }

  const actions = document.createElement("div");
  actions.className = "bundle-card-actions";
  const download = document.createElement("button");
  download.type = "button";
  download.textContent = "Download";
  download.disabled = !bundle.available;
  download.addEventListener("click", () => {
    if (!bundle.available) {
      return;
    }
    window.location.href = downloadUrlForBundle(bundle.id, bundle);
  });
  actions.appendChild(download);
  card.appendChild(actions);

  return card;
}

function renderBundleVariantCard({ bundles, downloadUrlForBundle, modalCliNote = "" }) {
  const selectedIndex = Math.max(0, bundles.findIndex((bundle) => bundle.available));
  let selectedBundle = bundles[selectedIndex] || bundles[0];
  const compactLabels = compactVariantLabels(bundles);
  const card = document.createElement("article");
  applyAvailabilityClass(card, selectedBundle);

  const head = document.createElement("div");
  head.className = "bundle-card-head";
  const titleWrap = document.createElement("div");
  appendText(titleWrap, "h5", "", baseBundleLabel(selectedBundle));
  appendText(titleWrap, "div", "bundle-id", String(selectedBundle.id || ""));
  const status = renderStatusNode(selectedBundle);
  head.append(titleWrap, status);
  card.appendChild(head);

  const purpose = appendText(card, "p", "bundle-purpose", String(selectedBundle.purpose || ""));

  const selectorWrap = document.createElement("label");
  selectorWrap.className = "bundle-variant-field";
  const selectorLabel = document.createElement("span");
  selectorLabel.textContent = "Dataset";
  const selector = document.createElement("select");
  bundles.forEach((bundle, index) => {
    const option = document.createElement("option");
    option.value = String(index);
    option.textContent = compactLabels[index] || bundleVariantLabel(bundle);
    selector.appendChild(option);
  });
  selector.value = String(selectedIndex);
  selectorWrap.append(selectorLabel, selector);
  card.appendChild(selectorWrap);

  const meta = appendText(card, "div", "bundle-meta", "");
  const downstreamHost = document.createElement("div");
  downstreamHost.className = "bundle-dynamic-section";
  const contentsHost = document.createElement("div");
  contentsHost.className = "bundle-dynamic-section";
  const readinessHost = document.createElement("div");
  readinessHost.className = "bundle-dynamic-section";
  const missingHost = document.createElement("div");
  missingHost.className = "bundle-dynamic-section";
  const cliNote = document.createElement("p");
  cliNote.className = "bundle-cli-note";
  card.append(downstreamHost, readinessHost, contentsHost, missingHost, cliNote);

  const actions = document.createElement("div");
  actions.className = "bundle-card-actions";
  const download = document.createElement("button");
  download.type = "button";
  download.textContent = "Download";
  download.addEventListener("click", () => {
    if (!selectedBundle.available) {
      return;
    }
    window.location.href = downloadUrlForBundle(selectedBundle.id, selectedBundle);
  });
  actions.appendChild(download);
  card.appendChild(actions);

  function applySelectedBundle(bundle) {
    selectedBundle = bundle;
    applyAvailabilityClass(card, bundle);
    status.className = `bundle-status ${bundle.available ? "ok" : "blocked"}`;
    status.textContent = bundle.available ? "available" : "not ready";
    purpose.textContent = String(bundle.purpose || "");
    const metaText = bundleMetaLine(bundle);
    meta.textContent = metaText;
    meta.hidden = !metaText;
    replaceChildren(downstreamHost, renderDownstream(bundle));
    replaceChildren(readinessHost, renderReadiness(bundle));
    replaceChildren(contentsHost, renderContents(bundle));
    replaceChildren(missingHost, renderMissing(bundle));
    const note = String(bundle.cli_note || "");
    cliNote.textContent = note;
    cliNote.hidden = !note || note === modalCliNote;
    download.disabled = !bundle.available;
  }

  selector.addEventListener("change", () => {
    const index = Number(selector.value);
    applySelectedBundle(bundles[index] || bundles[0]);
  });
  applySelectedBundle(selectedBundle);
  return card;
}

function bundleCardEntries(bundles) {
  const entries = [];
  const byId = new Map();
  for (const bundle of bundles) {
    const key = String(bundle.id || "");
    if (!byId.has(key)) {
      const entry = { id: key, bundles: [] };
      byId.set(key, entry);
      entries.push(entry);
    }
    byId.get(key).bundles.push(bundle);
  }
  return entries;
}

export function closeBundleDownloadModal(ids = {}) {
  node(ids, "modal")?.classList.add("hidden");
}

export function initBundleDownloadModal(ids = {}) {
  node(ids, "close")?.addEventListener("click", () => closeBundleDownloadModal(ids));
  node(ids, "modal")?.addEventListener("click", (event) => {
    if (event.target && event.target.id === id(ids, "modal")) {
      closeBundleDownloadModal(ids);
    }
  });
}

export async function openBundleDownloadModal({
  metadataUrl,
  downloadUrlForBundle,
  title = "Download ZIP",
  ids = {},
}) {
  const modal = node(ids, "modal");
  const body = node(ids, "body");
  const titleNode = node(ids, "title");
  if (!modal || !body) {
    throw new Error("Bundle modal is not available in this page.");
  }
  if (titleNode) {
    titleNode.textContent = title;
  }
  body.innerHTML = "";
  appendText(body, "div", "bundle-modal-loading", "Loading bundle options...");
  modal.classList.remove("hidden");

  const response = await fetch(metadataUrl);
  let payload = null;
  try {
    payload = await response.json();
  } catch (_err) {
    payload = null;
  }
  if (!response.ok) {
    throw new Error(payload?.detail || `Failed to load bundle options (${response.status})`);
  }

  const bundles = Array.isArray(payload?.bundles) ? payload.bundles : [];
  body.innerHTML = "";
  if (!bundles.length) {
    appendText(body, "div", "muted-box", "No bundle options are available for this job.");
    return payload;
  }

  const intro = appendText(
    body,
    "p",
    "bundle-modal-intro",
    "Choose the smallest bundle that matches your next step. Use full archives for storage or debugging."
  );
  intro.setAttribute("aria-live", "polite");

  const modalCliNote = sharedCliNote(bundles);
  if (modalCliNote) {
    appendText(body, "p", "bundle-modal-note", modalCliNote);
  }

  const grid = document.createElement("div");
  grid.className = "bundle-card-grid";
  for (const entry of bundleCardEntries(bundles)) {
    if (entry.bundles.length > 1 || entry.bundles.some((bundle) => bundleVariantId(bundle))) {
      grid.appendChild(renderBundleVariantCard({ bundles: entry.bundles, downloadUrlForBundle, modalCliNote }));
    } else {
      grid.appendChild(renderBundleCard({ bundle: entry.bundles[0], downloadUrlForBundle, modalCliNote }));
    }
  }
  body.appendChild(grid);
  return payload;
}
