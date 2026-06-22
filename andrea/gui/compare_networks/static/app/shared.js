export {
  contextFamily,
  contextLabel,
  sortContext,
} from "/static-common/app/network_context.js?v=20260620a";

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
