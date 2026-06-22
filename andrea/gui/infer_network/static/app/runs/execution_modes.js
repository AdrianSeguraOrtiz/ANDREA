export const EXECUTION_MODE_LABELS = {
  global: "Global",
  group_native: "Group native",
  group_emulated: "Group emulated",
  column_native: "Column native",
  group_aggregated: "Group aggregated",
};

const GROUP_REQUIRED_MODES = new Set([
  "group_emulated",
  "group_aggregated",
]);

export function executionModeLabel(mode) {
  const key = String(mode || "").trim();
  if (EXECUTION_MODE_LABELS[key]) {
    return EXECUTION_MODE_LABELS[key];
  }
  return key ? key.replace(/_/g, " ") : "Global";
}

export function executionModeAvailability({ mode, providedExtras }) {
  const key = String(mode || "").trim();
  const extras = providedExtras instanceof Set ? providedExtras : new Set(providedExtras || []);
  if (GROUP_REQUIRED_MODES.has(key) && !extras.has("groups")) {
    return {
      available: false,
      reason: "Requires groups.tsv from Step 1.",
    };
  }
  return { available: true, reason: "" };
}
