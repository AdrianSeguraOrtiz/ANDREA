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
