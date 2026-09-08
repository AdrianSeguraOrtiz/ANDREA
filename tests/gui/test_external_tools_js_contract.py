from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
EXTERNAL_TOOLS_PATH = (
    REPO_ROOT
    / "andrea"
    / "gui"
    / "infer_network"
    / "static"
    / "app"
    / "catalog"
    / "external_tools.js"
)
RUN_CARDS_PATH = (
    REPO_ROOT
    / "andrea"
    / "gui"
    / "infer_network"
    / "static"
    / "app"
    / "runs"
    / "cards.js"
)
MAIN_PATH = (
    REPO_ROOT
    / "andrea"
    / "gui"
    / "infer_network"
    / "static"
    / "app"
    / "main.js"
)

NODE_CONTRACT_TEST = r"""
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const cache = new Map();
async function loadModule(filename) {
  const absolute = path.resolve(filename);
  if (cache.has(absolute)) return cache.get(absolute);
  const module = new vm.SourceTextModule(fs.readFileSync(absolute, "utf8"), {
    identifier: absolute,
  });
  cache.set(absolute, module);
  await module.link((specifier, referencingModule) =>
    loadModule(path.resolve(path.dirname(referencingModule.identifier), specifier))
  );
  return module;
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function assertThrows(callback, expected) {
  try {
    callback();
  } catch (error) {
    assert(
      String(error.message).includes(expected),
      `Expected ${JSON.stringify(expected)}, got ${JSON.stringify(error.message)}`
    );
    return;
  }
  throw new Error(`Expected error containing ${JSON.stringify(expected)}`);
}

(async () => {
  const externalModule = await loadModule(process.argv[2]);
  await externalModule.evaluate();
  const statePath = path.resolve(path.dirname(process.argv[2]), "../core/state.js");
  const state = cache.get(statePath).namespace.state;
  const api = externalModule.namespace;
  const clone = (value) => JSON.parse(JSON.stringify(value));
  const reset = () => {
    state.bootstrap = { tools: [] };
    state.customTools = [];
    state.preflightReport = null;
    state.eligibleToolIds = [];
  };

  const valid = {
    run_id: "demo_01",
    name: "Demo Tool",
    docker_image: "example/demo:1.0",
    execution_mode: "global",
    extra_inputs: [],
    outputs: { directed: true, sign: "none" },
    runtime_resources: {
      threading: {
        supported: true,
        default_threads: 2,
        max_threads: 8,
        upstream_mapping: "cli:--threads",
      },
    },
  };
  const paramsSchema = {
    alpha: {
      type: "float",
      required: false,
      default: 0.5,
      description: "External Docker tool runtime parameter.",
    },
  };

  reset();
  assert(
    api.addCustomToolDefinition(clone(valid), clone(paramsSchema)) ===
      "custom_demo_01",
    "custom tool ID is derived from the complete run ID"
  );
  assert(
    api.normalizeCustomToolId("custom_demo_01") === "custom_custom_demo_01",
    "an existing custom_ prefix is not treated as an alias"
  );
  assert(
    JSON.stringify(state.bootstrap.tools[0].outputs) ===
      JSON.stringify({ directed: true, sign: "none", evidence: "external_tool_output" }),
    "bootstrap output capabilities"
  );
  assert(
    JSON.stringify(state.bootstrap.tools[0].params_schema) === JSON.stringify(paramsSchema),
    "runtime parameter schema stays outside the public custom-tool payload"
  );
  assert(
    JSON.stringify(api.customToolsPayload()) === JSON.stringify({ tools: [valid] }),
    "payload includes explicit output semantics"
  );
  const bootstrapTool = state.bootstrap.tools[0];
  assert(api.customToolRunId(bootstrapTool) === "demo_01", "fixed custom run ID");
  assert(
    api.validateCustomToolRunIdentity(bootstrapTool, "demo_01") === "demo_01",
    "exact custom run identity"
  );
  assertThrows(
    () => api.validateCustomToolRunIdentity(bootstrapTool, " demo_01 "),
    "without surrounding whitespace"
  );
  assertThrows(
    () => api.validateCustomToolRunIdentity(bootstrapTool, "different_01"),
    "must be exactly demo_01"
  );

  assert(
    api.customToolGroupsAreManagedByAndrea("group_emulated") === true,
    "group_emulated groups are managed by ANDREA"
  );
  assert(
    api.customToolGroupsAreManagedByAndrea("group_aggregated") === true,
    "group_aggregated groups are managed by ANDREA"
  );
  assert(
    api.customToolGroupsAreManagedByAndrea("group_native") === false,
    "group_native can deliver groups to the image"
  );

  for (const executionMode of ["group_emulated", "group_aggregated"]) {
    const orchestrated = {
      ...clone(valid),
      run_id: `${executionMode}_01`,
      execution_mode: executionMode,
      extra_inputs: [],
    };
    reset();
    api.addCustomToolDefinition(orchestrated);
    assert(
      JSON.stringify(api.customToolsPayload()) ===
        JSON.stringify({ tools: [orchestrated] }),
      `${executionMode} public payload omits orchestration-only groups`
    );

    const invalidRuntimeGroups = { ...orchestrated, extra_inputs: ["groups"] };
    reset();
    assertThrows(
      () => api.addCustomToolDefinition(invalidRuntimeGroups),
      "ANDREA manages groups.tsv as an orchestration-only input"
    );
  }

  for (const extraInputs of [["groups"], ["column_phenotypes"]]) {
    const groupNative = {
      ...clone(valid),
      run_id: `group_native_${extraInputs[0]}`,
      execution_mode: "group_native",
      extra_inputs: extraInputs,
    };
    reset();
    api.addCustomToolDefinition(groupNative);
  }
  for (const extraInputs of [[], ["groups", "column_phenotypes"]]) {
    const invalidGroupNative = {
      ...clone(valid),
      execution_mode: "group_native",
      extra_inputs: extraInputs,
    };
    reset();
    assertThrows(
      () => api.addCustomToolDefinition(invalidGroupNative),
      "group_native requires exactly one runtime context input"
    );
  }

  const missingOutputs = clone(valid);
  delete missingOutputs.outputs;
  reset();
  assertThrows(() => api.addCustomToolDefinition(missingOutputs), "outputs is required");

  const prefixed = clone(valid);
  prefixed.run_id = "custom_demo_01";
  reset();
  assert(
    api.addCustomToolDefinition(prefixed) === "custom_custom_demo_01",
    "custom-prefixed run IDs still derive an unambiguous tool ID"
  );
  assert(api.removeCustomToolDefinition("custom_demo_01") === false, "no alias removal");
  assert(
    api.removeCustomToolDefinition("custom_custom_demo_01") === true,
    "exact derived ID removal"
  );

  const paramRow = (key, value, type) => ({
    querySelector(selector) {
      if (selector === ".custom-tool-param-key") return { value: key };
      if (selector === ".custom-tool-param-value") return { value };
      if (selector === ".custom-tool-param-type") return { value: type };
      return null;
    },
  });
  const formValues = {
    "custom-tool-needed-extras": "TF_LIST; extras/groups.tsv,tf-list",
    "custom-tool-run-id": "form_run_01",
    "custom-tool-name": "Form Tool",
    "custom-tool-image-name": " example/form-tool ",
    "custom-tool-image-tag": ":1.0",
    "custom-tool-output-directed": "false",
    "custom-tool-output-sign": "mixed",
    "custom-tool-threading-supported": "true",
    "custom-tool-default-threads": "2",
    "custom-tool-max-threads": "",
    "custom-tool-thread-mapping": "cli:--threads",
  };
  global.document = {
    getElementById: (id) => ({ value: formValues[id] }),
    querySelector: (selector) =>
      selector === "input[name='custom-tool-execution-mode']:checked"
        ? { value: "global", disabled: false }
        : null,
    querySelectorAll: () => [
      paramRow(" alpha ", "+1", "number"),
      paramRow("enabled", "YES", "boolean"),
      paramRow("", "", "string"),
    ],
  };
  const built = api.buildSimpleCustomToolFromForm();
  assert(built.tool.docker_image === "example/form-tool:1.0", "friendly image form");
  assert(
    JSON.stringify(built.tool.extra_inputs) === JSON.stringify(["tf_list", "groups"]),
    "common extra-input delimiters and filenames are normalized"
  );
  assert(
    JSON.stringify(built.run.params) === JSON.stringify({ alpha: 1, enabled: true }),
    "runtime values retain the existing permissive parser"
  );
  assert(
    JSON.stringify(built.tool.outputs) === JSON.stringify({ directed: false, sign: "mixed" }),
    "form output capabilities"
  );
  assert(
    built.tool.runtime_resources.threading.max_threads === null,
    "an empty maximum declares no intrinsic tool limit"
  );

  process.stdout.write("ok\n");
})().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
"""


@unittest.skipIf(shutil.which("node") is None, "Node.js is not installed")
class ExternalToolsJavaScriptContractTests(unittest.TestCase):
    def test_catalog_run_ids_keep_existing_trim_behavior(self) -> None:
        source = RUN_CARDS_PATH.read_text(encoding="utf-8")

        self.assertIn('tool?.tool_origin === "custom"', source)
        self.assertIn(': String(rawRunId || "").trim();', source)
        self.assertIn(": rawRunId.trim();", source)

    def test_custom_run_identity_and_execution_mode_are_fixed(self) -> None:
        source = RUN_CARDS_PATH.read_text(encoding="utf-8")

        self.assertIn("validateCustomToolRunIdentity(tool, runId);", source)
        self.assertIn("const fixedRunId = fixedCustomRunId(tool);", source)
        self.assertIn("runIdInput.readOnly = fixedRunId !== null;", source)
        self.assertIn("initial.run_id !== fixedRunId", source)
        self.assertIn('const executionMode = tool?.spec?.execution_mode;', source)
        self.assertIn(
            "fixedExecutionMode !== null && executionMode !== fixedExecutionMode",
            source,
        )
        self.assertIn("? [fixedExecutionMode]", source)

    def test_run_resources_include_exact_ram_outside_scientific_params(self) -> None:
        source = RUN_CARDS_PATH.read_text(encoding="utf-8")

        self.assertIn('card.querySelector(".runtime-ram-gb")', source)
        self.assertIn("resources.ram_gb = ramGb;", source)
        self.assertIn("Number.isFinite(ramGb)", source)

    def test_run_resources_include_operational_timeout(self) -> None:
        source = RUN_CARDS_PATH.read_text(encoding="utf-8")

        self.assertIn('card.querySelector(".runtime-timeout")', source)
        self.assertIn("resources.timeout_seconds = timeoutSeconds;", source)
        self.assertIn("Number.isFinite(timeoutSeconds)", source)

    def test_orchestration_only_groups_are_disabled_in_the_form(self) -> None:
        source = MAIN_PATH.read_text(encoding="utf-8")

        self.assertIn(
            "customToolGroupsAreManagedByAndrea(executionMode)",
            source,
        )
        self.assertIn('selected.delete("groups");', source)
        self.assertIn("checkbox.disabled = managedByAndrea;", source)
        self.assertIn("(managed by ANDREA)", source)

    def test_external_tool_contract_and_form_normalization(self) -> None:
        result = subprocess.run(
            [
                shutil.which("node") or "node",
                "--experimental-vm-modules",
                "-",
                str(EXTERNAL_TOOLS_PATH),
            ],
            cwd=REPO_ROOT,
            input=NODE_CONTRACT_TEST,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout, "ok\n")


if __name__ == "__main__":
    unittest.main()
