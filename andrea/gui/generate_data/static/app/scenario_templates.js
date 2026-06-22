import {
  contextFamily as truthContextFamily,
  contextPrefixForFamily as truthContextPrefixForOutput,
  sortContextFamilies as sortTruthContextFamilies,
} from "/static-common/app/network_context.js?v=20260620a";

const TRUTH_GRANULARITY_LEVELS = [
  { contexts: ["global"], shortLabel: "Global" },
  { contexts: ["global", "group"], shortLabel: "+ Group" },
  { contexts: ["global", "group", "column"], shortLabel: "+ Column" },
];

export function createScenarioTemplateModel({ state, $ }) {
  function scenarioTemplates() {
    return Array.isArray(state.bootstrap?.scenario_templates)
      ? state.bootstrap.scenario_templates
      : [];
  }

  function templateDataAxes(template) {
    return template?.data_axes && typeof template.data_axes === "object"
      ? template.data_axes
      : {};
  }

  function templateTruthContexts(template) {
    return Array.isArray(template?.truth_requirements?.contexts)
      ? template.truth_requirements.contexts.map((item) => String(item || "").trim()).filter(Boolean)
      : ["global"];
  }

  function scenarioTemplateSpec(templateId) {
    return scenarioTemplates().find((item) => item.id === templateId) || null;
  }

  function scenarioTemplateRequiredTruthOutputs(templateId = selectedScenarioTemplateId()) {
    const template = scenarioTemplateSpec(templateId);
    if (Array.isArray(template?.required_truth_outputs)) {
      return template.required_truth_outputs.map((item) => String(item || "").trim()).filter(Boolean);
    }
    return ["global"];
  }

  function knownTruthContextFamilies() {
    const values = new Set(["global"]);
    for (const template of scenarioTemplates()) {
      for (const context of templateTruthContexts(template)) {
        const family = truthContextFamily(context);
        if (family) {
          values.add(family);
        }
      }
      for (const context of scenarioTemplateRequiredTruthOutputs(template.id)) {
        const family = truthContextFamily(context);
        if (family) {
          values.add(family);
        }
      }
    }
    for (const simulator of state.bootstrap?.simulators || []) {
      const semanticCapabilities = simulator?.semantic_capabilities && typeof simulator.semantic_capabilities === "object"
        ? simulator.semantic_capabilities
        : {};
      for (const capability of Object.values(semanticCapabilities)) {
        for (const row of Array.isArray(capability?.truth_outputs) ? capability.truth_outputs : []) {
          const family = truthContextFamily(row?.context);
          if (family) {
            values.add(family);
          }
        }
        for (const row of Array.isArray(capability?.truth_contexts) ? capability.truth_contexts : []) {
          const family = truthContextFamily(row?.context);
          if (family) {
            values.add(family);
          }
        }
      }
    }
    return values;
  }

  function truthGranularityKeyFromContexts(contexts) {
    return (contexts || []).map((item) => String(item || "").trim()).filter(Boolean).join("|");
  }

  function templateTruthGranularityKey(template) {
    return truthGranularityKeyFromContexts(templateTruthContexts(template));
  }

  function readableToken(value) {
    return String(value || "")
      .trim()
      .replace(/_/g, " ")
      .replace(/\b\w/g, (char) => char.toUpperCase());
  }

  function axisValueLabel(axis, value) {
    const labels = {
      resolution: {
        bulk: "Bulk RNA",
        pseudo_bulk: "Pseudo-bulk RNA",
        single_cell: "scRNA",
        spatial: "Spatial RNA",
      },
      column_kind: {
        cells: "Cells",
        conditions: "Conditions",
        metacells: "Metacells",
        perturbations: "Perturbations",
        samples: "Samples",
        spots: "Spots",
        timepoints: "Timepoints",
      },
      experimental_design: {
        differentiation: "Differentiation",
        observational: "Observational",
        perturbational: "Perturbational",
        steady_state: "Steady state",
        time_series: "Time series",
        trajectory: "Trajectory",
      },
    };
    return labels[axis]?.[value] || readableToken(value);
  }

  function truthGranularityLabel(key) {
    const contexts = String(key || "").split("|").filter(Boolean);
    if (!contexts.length) {
      return "Global";
    }
    const labels = {
      global: "Global",
      group: "Group",
      column: "Column",
      sample: "Sample",
      timepoint: "Timepoint",
      perturbation: "Perturbation",
    };
    return contexts.map((context) => labels[context] || readableToken(context)).join(" + ");
  }

  function axisSortOrder(axis) {
    return {
      resolution: ["single_cell", "bulk", "pseudo_bulk", "spatial", "mixed"],
      column_kind: ["cells", "samples", "timepoints", "perturbations", "spots", "metacells", "conditions"],
      experimental_design: ["observational", "steady_state", "differentiation", "trajectory", "time_series", "perturbational"],
    }[axis] || [];
  }

  function configuredAxisValues(axis) {
    const configured = state.bootstrap?.semantic_options?.axes?.[axis];
    const values = Array.isArray(configured) ? configured : axisSortOrder(axis);
    const templateValues = scenarioTemplates().map((template) => templateDataAxes(template)[axis]);
    return [...values, ...templateValues]
      .map((value) => String(value || "").trim())
      .filter((value) => value && value !== "unknown");
  }

  function sortedUniqueOptions(values, axis, labelFn = (value) => axisValueLabel(axis, value)) {
    const order = axisSortOrder(axis);
    return [...new Set(values.map((value) => String(value || "").trim()).filter(Boolean))]
      .sort((a, b) => {
        const ai = order.indexOf(a);
        const bi = order.indexOf(b);
        if (ai >= 0 || bi >= 0) {
          return (ai >= 0 ? ai : 999) - (bi >= 0 ? bi : 999);
        }
        return a.localeCompare(b);
      })
      .map((value) => ({ value, label: labelFn(value) }));
  }

  function axisOptionsForSelection(axis, filters = {}) {
    return sortedUniqueOptions(configuredAxisValues(axis), axis)
      .map((option) => {
        const isAvailable = templatesForSelection({
          ...filters,
          [axis]: option.value,
        }).length > 0;
        return {
          ...option,
          disabled: !isAvailable,
          unavailableLabel: "not available yet",
        };
      });
  }

  function truthGranularityOptionsForSelection(filters = {}) {
    const configuredFamilies = state.bootstrap?.semantic_options?.truth_context_families;
    const families = new Set(
      Array.isArray(configuredFamilies) && configuredFamilies.length
        ? configuredFamilies.map((item) => String(item || "").trim()).filter(Boolean)
        : ["global", "group", "column"]
    );
    const availableKeys = new Set(
      templatesForSelection(filters).map((template) => templateTruthGranularityKey(template))
    );
    return TRUTH_GRANULARITY_LEVELS
      .filter((level) => level.contexts.every((context) => families.has(context)))
      .map((level) => {
        const key = truthGranularityKeyFromContexts(level.contexts);
        return {
          value: key,
          label: truthGranularityLabel(key),
          shortLabel: level.shortLabel,
          disabled: !availableKeys.has(key),
          unavailableLabel: "not available yet",
        };
      });
  }

  function setSelectOptions(select, options, preferredValue = "") {
    const previousValue = String(preferredValue || select.value || "").trim();
    select.innerHTML = "";
    const normalizedOptions = options.map((optionSpec) => ({
      value: String(optionSpec.value || "").trim(),
      label: String(optionSpec.label || optionSpec.value || "").trim(),
      disabled: Boolean(optionSpec.disabled),
      unavailableLabel: String(optionSpec.unavailableLabel || "").trim(),
    })).filter((optionSpec) => optionSpec.value);
    for (const optionSpec of normalizedOptions) {
      const option = document.createElement("option");
      option.value = optionSpec.value;
      option.disabled = optionSpec.disabled;
      option.textContent = optionSpec.disabled && optionSpec.unavailableLabel
        ? `${optionSpec.label || optionSpec.value} (${optionSpec.unavailableLabel})`
        : (optionSpec.label || optionSpec.value);
      select.appendChild(option);
    }
    const enabledOptions = normalizedOptions.filter((optionSpec) => !optionSpec.disabled);
    const enabledValues = new Set(enabledOptions.map((optionSpec) => optionSpec.value));
    select.value = enabledValues.has(previousValue)
      ? previousValue
      : (enabledOptions[0]?.value || normalizedOptions[0]?.value || "");
    select.disabled = normalizedOptions.length <= 1 || enabledOptions.length === 0;
    return select.value;
  }

  function renderTruthGranularityControl(options, selectedValue) {
    const host = $("truth-granularity-control");
    if (!host) {
      return;
    }
    host.innerHTML = "";
    const selectedIndex = Math.max(0, options.findIndex((option) => option.value === selectedValue));
    for (const [index, option] of options.entries()) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "truth-granularity-step";
      button.setAttribute("role", "radio");
      button.setAttribute("aria-checked", option.value === selectedValue ? "true" : "false");
      button.disabled = Boolean(option.disabled);
      button.title = option.disabled
        ? `${option.label} is not available for the selected scenario.`
        : option.label;
      if (index <= selectedIndex) {
        button.classList.add("is-included");
      }
      if (option.value === selectedValue) {
        button.classList.add("is-selected");
      }
      button.textContent = option.shortLabel || option.label;
      button.addEventListener("click", () => {
        if (option.disabled) {
          return;
        }
        const select = $("truth-granularity");
        select.value = option.value;
        select.dispatchEvent(new Event("change", { bubbles: true }));
      });
      host.appendChild(button);
    }
  }

  function templatesForSelection(filters) {
    return scenarioTemplates().filter((template) => {
      const axes = templateDataAxes(template);
      if (filters.resolution && axes.resolution !== filters.resolution) {
        return false;
      }
      if (filters.experimental_design && axes.experimental_design !== filters.experimental_design) {
        return false;
      }
      if (filters.column_kind && axes.column_kind !== filters.column_kind) {
        return false;
      }
      if (filters.truth_key && templateTruthGranularityKey(template) !== filters.truth_key) {
        return false;
      }
      return true;
    });
  }

  function preferredScenarioTemplate() {
    return scenarioTemplateSpec(selectedScenarioTemplateId())
      || scenarioTemplates().find((template) => templateTruthContexts(template).includes("group"))
      || scenarioTemplates()[0]
      || null;
  }

  function syncScenarioTemplateOptions(selectedTemplateId = "") {
    const templateSelect = $("scenario-template");
    const options = scenarioTemplates().map((template) => ({
      value: template.id,
      label: template.id,
    }));
    return setSelectOptions(templateSelect, options, selectedTemplateId);
  }

  function refreshScenarioAxisControls({ preserve = true } = {}) {
    const templates = scenarioTemplates();
    if (!templates.length) {
      syncScenarioTemplateOptions("");
      renderTruthGranularityControl([], "");
      return null;
    }

    const preferred = preferredScenarioTemplate();
    const preferredAxes = templateDataAxes(preferred);
    const preferredTruthKey = templateTruthGranularityKey(preferred);
    const currentResolution = preserve ? $("scenario-resolution").value : "";
    const currentDesign = preserve ? $("scenario-design").value : "";
    const currentColumnKind = preserve ? $("scenario-column-kind").value : "";
    const currentTruthKey = preserve ? $("truth-granularity").value : "";

    const resolution = setSelectOptions(
      $("scenario-resolution"),
      axisOptionsForSelection("resolution"),
      currentResolution || preferredAxes.resolution
    );

    const columnKind = setSelectOptions(
      $("scenario-column-kind"),
      axisOptionsForSelection("column_kind", {
        resolution,
      }),
      currentColumnKind || preferredAxes.column_kind
    );

    const experimentalDesign = setSelectOptions(
      $("scenario-design"),
      axisOptionsForSelection("experimental_design", {
        resolution,
        column_kind: columnKind,
      }),
      currentDesign || preferredAxes.experimental_design
    );

    const truthOptions = truthGranularityOptionsForSelection({
      resolution,
      experimental_design: experimentalDesign,
      column_kind: columnKind,
    });
    const truthKey = setSelectOptions(
      $("truth-granularity"),
      truthOptions,
      currentTruthKey || preferredTruthKey
    );
    renderTruthGranularityControl(truthOptions, truthKey);

    const selectedTemplate = templatesForSelection({
      resolution,
      experimental_design: experimentalDesign,
      column_kind: columnKind,
      truth_key: truthKey,
    })[0] || null;
    syncScenarioTemplateOptions(selectedTemplate?.id || "");
    return selectedTemplate;
  }

  function primaryTruthOutputForScenarioTemplate(templateId = selectedScenarioTemplateId()) {
    const required = scenarioTemplateRequiredTruthOutputs(templateId);
    return required[required.length - 1] || "global";
  }

  function truthContextChipLabel(context) {
    return truthContextFamily(context) || "-";
  }

  function truthContextArtifactLabel(context) {
    const family = truthContextFamily(context);
    if (!family) {
      return "-";
    }
    return family === "global"
      ? "truth/networks.csv · global"
      : `truth/networks.csv · ${family}:<id>`;
  }

  function truthContextFamiliesForDisplay({ templateId = selectedScenarioTemplateId(), truthOutputs = [], truthContexts = [] } = {}) {
    const contexts = [
      ...scenarioTemplateRequiredTruthOutputs(templateId),
    ];
    if (Array.isArray(truthOutputs)) {
      contexts.push(...truthOutputs.map((item) => item?.context));
    }
    if (Array.isArray(truthContexts)) {
      contexts.push(...truthContexts.map((item) => item?.context));
    }
    const sorted = sortTruthContextFamilies(contexts);
    return sorted.length ? sorted : ["global"];
  }

  function scenarioTemplateRequiredTruthContexts(templateId = selectedScenarioTemplateId()) {
    const template = scenarioTemplateSpec(templateId);
    if (Array.isArray(template?.required_truth_contexts)) {
      return template.required_truth_contexts.map((item) => String(item || "").trim()).filter(Boolean);
    }
    return scenarioTemplateRequiredTruthOutputs(templateId).map(truthContextPrefixForOutput).filter(Boolean);
  }

  function scenarioTemplateRequiredExtras(templateId = selectedScenarioTemplateId()) {
    const template = scenarioTemplateSpec(templateId);
    return Array.isArray(template?.required_extras)
      ? template.required_extras.map((item) => String(item || "").trim()).filter(Boolean)
      : [];
  }

  function fixedOutputFilesForScenarioTemplate(templateId = selectedScenarioTemplateId()) {
    const files = [
      {
        path: "expression.tsv",
        description: "Normalized expression matrix.",
      },
      {
        path: "truth/networks.csv",
        description: "Unified ground-truth network table.",
        highlight: true,
      },
      {
        path: "truth/gene_universe.txt",
        description: "Genes covered by the ground-truth networks.",
      },
    ];
    for (const extra of scenarioTemplateRequiredExtras(templateId)) {
      if (extra === "groups") {
        files.push({
          path: "extras/groups.tsv",
          description: "Column-to-group assignments used by group-level truth.",
        });
      }
    }
    return files;
  }

  function truthContextExplanation(context) {
    const normalized = String(context || "").trim();
    if (normalized === "global") {
      return "one dataset-level GRN.";
    }
    if (normalized === "group:") {
      return "one GRN per group, stored as context values like group:<id>.";
    }
    if (normalized === "column:") {
      return "one GRN per expression column, stored as context values like column:<id>.";
    }
    if (normalized === "sample:") {
      return "one GRN per sample, stored as context values like sample:<id>.";
    }
    if (normalized === "timepoint:") {
      return "one GRN per timepoint, stored as context values like timepoint:<id>.";
    }
    if (normalized === "perturbation:") {
      return "one GRN per perturbation, stored as context values like perturbation:<id>.";
    }
    if (normalized.endsWith(":")) {
      const family = normalized.slice(0, -1);
      return `one GRN per ${readableToken(family).toLowerCase()}, stored with ${family}:<id> context values.`;
    }
    return "truth rows distinguished by this context value.";
  }

  function scenarioSemanticLabel(scenario) {
    const axes = scenario?.data_axes && typeof scenario.data_axes === "object" ? scenario.data_axes : {};
    const contexts = Array.isArray(scenario?.truth_requirements?.contexts)
      ? scenario.truth_requirements.contexts.join("+")
      : "global";
    return [
      axes.resolution || "unknown",
      axes.column_kind || "columns",
      axes.experimental_design || "design",
      contexts,
    ].join(" / ");
  }

  function extraByKey(key) {
    return (state.bootstrap?.extras || []).find((item) => item.key === key) || null;
  }

  function selectedScenarioTemplateId() {
    return $("scenario-template").value;
  }

  return {
    axisValueLabel,
    extraByKey,
    fixedOutputFilesForScenarioTemplate,
    knownTruthContextFamilies,
    primaryTruthOutputForScenarioTemplate,
    readableToken,
    refreshScenarioAxisControls,
    scenarioSemanticLabel,
    scenarioTemplateRequiredExtras,
    scenarioTemplateRequiredTruthContexts,
    scenarioTemplateRequiredTruthOutputs,
    scenarioTemplateSpec,
    scenarioTemplates,
    selectedScenarioTemplateId,
    syncScenarioTemplateOptions,
    templateDataAxes,
    templateTruthContexts,
    templateTruthGranularityKey,
    truthContextArtifactLabel,
    truthContextChipLabel,
    truthContextExplanation,
    truthContextFamiliesForDisplay,
    truthGranularityLabel,
  };
}
