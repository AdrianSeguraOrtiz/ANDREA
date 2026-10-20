"""Retain MINI-EX plots when an axis contains only one retained observation.

A one-row/one-column regulon matrix is valid, but hierarchical clustering
requires two observations on the corresponding axis. Only dendrograms change;
regulons, edge scores, filtering and every inference parameter are untouched.
"""
import hashlib
from pathlib import Path
import sys

UPSTREAM_SHA256 = "53658049e2ca4d1abd9d66b9382d802ed2abb25ee4a1f4c3027335024e6d064e"
OLD = """        col_colors=column_colors,
        figsize=(fig_width, fig_height),"""
NEW = """        col_colors=column_colors,
        row_cluster=df.shape[0] > 1,
        col_cluster=df.shape[1] > 1,
        figsize=(fig_width, fig_height),"""


def patch_source(source):
    if source.count(NEW) == 1 and OLD not in source:
        return source
    if source.count(OLD) != 1:
        raise ValueError("Pinned MINI-EX clustermap source changed")
    return source.replace(OLD, NEW, 1)


def main(path):
    source = path.read_text()
    original = source.replace(NEW, OLD, 1) if NEW in source else source
    if hashlib.sha256(original.encode()).hexdigest() != UPSTREAM_SHA256:
        raise ValueError("Unexpected MINIEX_makeRegulonClustermap.py; review patch against the pinned revision")
    path.write_text(patch_source(source))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
