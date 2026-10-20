"""Patch the pinned ARACNe3 MaxEnt lookup for asymmetric sparse TF networks.

Threshold pruning can retain reg1 -> reg2 without a reverse regulon entry.
Removing an absent reverse edge must be a no-op, not unordered_map::at().
This does not add edges or change pruning thresholds or random seeds.
"""
import hashlib
from pathlib import Path
import sys

UPSTREAM_SHA256 = "bbad0e4c3d2d8c103b3df55834e1c8bfcd4db97a559b7dd62a3aaf6429e99de1"
OLD = """  for (const auto &[reg1, reg2] : to_remove)
    network_reg_reg_only.at(reg1).erase(reg2);"""
NEW = """  for (const auto &[reg1, reg2] : to_remove) {
    const auto reverse_regulon = network_reg_reg_only.find(reg1);
    if (reverse_regulon != network_reg_reg_only.end())
      reverse_regulon->second.erase(reg2);
  }"""


def patch_source(source):
    if source.count(NEW) == 1 and OLD not in source:
        return source
    if source.count(OLD) != 1:
        raise ValueError("Pinned ARACNe3 sparse-pruning source changed")
    return source.replace(OLD, NEW, 1)


def main(path):
    source = path.read_text()
    original = source.replace(NEW, OLD, 1) if NEW in source else source
    if hashlib.sha256(original.encode()).hexdigest() != UPSTREAM_SHA256:
        raise ValueError("Unexpected ARACNe3 subnet_operations.cpp; review patch against the pinned revision")
    path.write_text(patch_source(source))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
