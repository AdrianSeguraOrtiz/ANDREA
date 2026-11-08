from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import unittest

from andrea.gui.infer_network.server import _load_tools_bootstrap
from andrea.core.shared.input_specs import load_input_specs


ROOT = Path(__file__).resolve().parents[2]


class StableInputContractTests(unittest.TestCase):
    def test_bootstrap_exposes_one_canonical_contract_without_profile_projections(self):
        payload = _load_tools_bootstrap()
        self.assertNotIn("temporal_input_profile", payload)
        self.assertNotIn("temporal_input_axes", payload)
        metadata = next(item for item in payload["extra_inputs"] if item["key"] == "column_descriptors")
        spec = load_input_specs()["column_descriptors"]
        self.assertNotIn("profiles", metadata)
        for field in ("description", "example", "required_columns", "column_types", "min_columns"):
            self.assertEqual(metadata[field], spec[field])

    @unittest.skipIf(shutil.which("node") is None, "Node.js unavailable")
    def test_axes_change_only_contextual_help_and_leave_contract_and_validation_unchanged(self):
        code = r"""
const fs = require("fs"), path = require("path"), vm = require("vm");
const cache = new Map();
async function load(filename) {
  filename = path.resolve(filename);
  if (cache.has(filename)) return cache.get(filename);
  const module = new vm.SourceTextModule(fs.readFileSync(filename,"utf8"), {identifier: filename});
  cache.set(filename,module);
  await module.link((name,parent) => {
    const clean = name.split("?")[0];
    return load(clean.startsWith("/static-common/")
      ? path.join(process.argv[2], clean.slice("/static-common/".length))
      : path.resolve(path.dirname(parent.identifier),clean));
  });
  return module;
}
function assert(value, message) { if (!value) throw Error(message); }
(async () => {
  const callbacks = {};
  const axes = Object.fromEntries(["column-kind", "expression-profile"].map(id => [id, {
    value: id === "column-kind" ? "cells" : "scrna",
    addEventListener: (name, callback) => {callbacks[id]=callback;},
  }]));
  let hidden = true;
  const help = {classList: {toggle: (_, value) => { hidden = value; }}};
  const row = {querySelector: selector => selector === ".extra-key" ? {value: "column_descriptors"} : null};
  global.document = {
    getElementById: id => axes[id] || (id === "bulk-temporal-help" ? help : null),
    querySelectorAll: selector => selector === ".extra-row" ? [row] : [],
  };
  const module = await load(process.argv[1]);
  await module.evaluate();
  const directory = path.dirname(process.argv[1]);
  const state = cache.get(path.resolve(directory, "../core/state.js")).namespace.state;
  const validation = cache.get(path.resolve(directory, "validation.js")).namespace;
  state.bootstrap = JSON.parse(fs.readFileSync(0,"utf8"));
  const original = validation.getExtraMeta("column_descriptors");
  const originalJSON = JSON.stringify(original);
  const generic = "column\tcondition\nS1\tcontrol\nS2\ttreatment\n";
  const finite = "column\ttimepoint\nS1\t0\nS2\t1.5\n";
  const bad = "column\ttimepoint\nS1\tinvalid\nS2\t1.5\n";
  let reads = 0;
  const file = text => ({name: "column_descriptors.tsv", text: async () => {reads++; return text;}});
  module.namespace.initExtras({onDatasetChanged: () => {}});
  for (const bulk of [false, true, false]) {
    axes["column-kind"].value = bulk ? "timepoints" : "cells";
    axes["expression-profile"].value = bulk ? "bulk" : "scrna";
    const previousReads = reads;
    callbacks["expression-profile"]();
    assert(reads === previousReads, "Changing selectors reread input contents");
    assert(hidden === !bulk, "Contextual help does not follow selected axes");
    assert(validation.getExtraMeta("column_descriptors") === original, "Changing selectors projected a different contract");
    assert(JSON.stringify(original) === originalJSON, "Changing selectors mutated the canonical contract");
    await validation.validateOptionalFile(file(generic), "column_descriptors");
    await validation.validateOptionalFile(file(finite), "column_descriptors");
    let rejected = false;
    try { await validation.validateOptionalFile(file(bad), "column_descriptors"); }
    catch (error) { rejected = error.message.includes("must be float"); }
    assert(rejected, "Optional normalized timepoint changed type validation across selectors");
  }
})().catch(error => { console.error(error); process.exitCode=1; });
"""
        result = subprocess.run(
            ["node", "--experimental-vm-modules", "-e", code,
             str(ROOT / "andrea/gui/infer_network/static/app/dataset/extras.js"),
             str(ROOT / "andrea/gui/common/static")],
            input=json.dumps(_load_tools_bootstrap()), text=True,
            capture_output=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
