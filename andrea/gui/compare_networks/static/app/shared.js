export const $ = (selector) => document.querySelector(selector);

export function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

export function formatValue(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) {
    return "N/A";
  }
  const numeric = Number(value);
  if (Math.abs(numeric) >= 100) {
    return numeric.toFixed(0);
  }
  if (Math.abs(numeric) >= 10) {
    return numeric.toFixed(1);
  }
  return numeric.toFixed(2);
}

export function textColor(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) && numeric >= 0.62 ? "#ffffff" : "#0f172a";
}

export function interpolateColor(start, end, ratio) {
  const clamped = Math.max(0, Math.min(1, ratio));
  const rgb = start.map((channel, idx) => Math.round(channel + ((end[idx] - channel) * clamped)));
  return `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})`;
}

export function stableHash(text) {
  let hash = 0;
  const raw = String(text || "");
  for (let idx = 0; idx < raw.length; idx += 1) {
    hash = ((hash << 5) - hash) + raw.charCodeAt(idx);
    hash |= 0;
  }
  return Math.abs(hash);
}

export function contextFamily(context) {
  const raw = String(context || "");
  if (raw.includes(":")) {
    return raw.split(":", 1)[0] || "other";
  }
  if (raw === "global") {
    return "global";
  }
  return raw || "other";
}

export function sortContext(a, b) {
  const order = { global: 0, group: 1, cell: 2, other: 3 };
  const familyA = contextFamily(a);
  const familyB = contextFamily(b);
  const rankA = order[familyA] ?? order.other;
  const rankB = order[familyB] ?? order.other;
  if (rankA !== rankB) {
    return rankA - rankB;
  }
  return contextSortLabel(a).localeCompare(contextSortLabel(b), undefined, { numeric: true });
}

function contextSortLabel(context) {
  const text = String(context || "");
  const idx = text.indexOf(":");
  if (idx >= 0) {
    const prefix = text.slice(0, idx);
    const value = text.slice(idx + 1);
    return value ? `${prefix} ${value}` : prefix;
  }
  return text;
}
