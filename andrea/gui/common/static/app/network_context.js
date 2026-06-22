export const CONTEXT_FAMILY_ORDER = [
  "global",
  "group",
  "column",
  "sample",
  "timepoint",
  "perturbation",
  "other",
];

export const CONTEXT_PREFIXES = {
  group: "group:",
  column: "column:",
  sample: "sample:",
  timepoint: "timepoint:",
  perturbation: "perturbation:",
};

const CONTEXT_FAMILY_RANK = Object.fromEntries(
  CONTEXT_FAMILY_ORDER.map((family, idx) => [family, idx])
);

export function contextFamily(context) {
  const text = String(context || "").trim();
  if (text === "global") {
    return "global";
  }
  if (CONTEXT_FAMILY_ORDER.includes(text)) {
    return text;
  }
  for (const [family, prefix] of Object.entries(CONTEXT_PREFIXES)) {
    if (text.startsWith(prefix)) {
      return family;
    }
  }
  return text ? "other" : "";
}

export function contextPrefixForFamily(family) {
  const normalized = String(family || "").trim();
  if (!normalized || normalized === "global") {
    return normalized || "-";
  }
  if (normalized.endsWith(":")) {
    return normalized;
  }
  return CONTEXT_PREFIXES[normalized] || `${normalized}:`;
}

export function contextLabel(context) {
  const text = String(context || "").trim();
  const family = contextFamily(text);
  const prefix = CONTEXT_PREFIXES[family];
  if (prefix) {
    const value = text.slice(prefix.length);
    return value ? `${family} ${value}` : family;
  }
  return text || "-";
}

export function sortContext(a, b) {
  const familyA = contextFamily(a) || "other";
  const familyB = contextFamily(b) || "other";
  const rankA = CONTEXT_FAMILY_RANK[familyA] ?? CONTEXT_FAMILY_RANK.other;
  const rankB = CONTEXT_FAMILY_RANK[familyB] ?? CONTEXT_FAMILY_RANK.other;
  if (rankA !== rankB) {
    return rankA - rankB;
  }
  return contextLabel(a).localeCompare(contextLabel(b), undefined, { numeric: true });
}

export function sortContextFamilies(values) {
  return [...new Set((values || []).map((value) => contextFamily(value)).filter(Boolean))]
    .sort((a, b) => {
      const rankA = CONTEXT_FAMILY_RANK[a] ?? CONTEXT_FAMILY_RANK.other;
      const rankB = CONTEXT_FAMILY_RANK[b] ?? CONTEXT_FAMILY_RANK.other;
      if (rankA !== rankB) {
        return rankA - rankB;
      }
      return a.localeCompare(b);
    });
}
