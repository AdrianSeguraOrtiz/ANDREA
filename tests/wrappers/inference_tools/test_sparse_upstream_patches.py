"""Regressions for upstream failures on valid sparse inference outputs."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
TOOLS = ROOT / 'wrappers/inference_tools/tools'


def load_patch(tool, filename):
    spec = importlib.util.spec_from_file_location(tool + '_sparse_patch', TOOLS / tool / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ARACNE = load_patch('aracne3', 'patch_sparse_pruning.py')
MINIEX = load_patch('miniex3', 'patch_singleton_clustermap.py')


@pytest.mark.parametrize('patch', [ARACNE, MINIEX])
def test_patch_rejects_unreviewed_upstream_source_and_is_idempotent(patch, tmp_path):
    source = patch.OLD
    patched = patch.patch_source(source)
    assert patch.patch_source(patched) == patched
    with pytest.raises(ValueError, match='source changed'):
        patch.patch_source('unexpected source revision')
    path = tmp_path / 'unexpected-source'
    path.write_text(source)
    with pytest.raises(ValueError, match='Unexpected'):
        patch.main(path)
    assert path.read_text() == source


@pytest.mark.skipif(shutil.which('g++') is None, reason='C++ compiler is required')
def test_aracne_reverse_edge_removal_preserves_asymmetric_and_symmetric_networks(tmp_path):
    # Exercise the exact C++ patch, including the previously crashing absence of
    # the reverse regulator. No scores or missing regulators may be invented.
    before = r'''
#include <cassert>
#include <map>
#include <set>
#include <utility>
using Network = std::map<int, std::map<int, float>>;
void trim_reverse(Network& network_reg_reg_only,
                  const std::set<std::pair<int,int>>& to_remove) {
'''
    after = r'''
}
int main() {
  Network asymmetric{{1, {{2, 0.8f}}}};
  trim_reverse(asymmetric, {{2, 1}});
  assert(asymmetric.size() == 1);
  assert(asymmetric.at(1).at(2) == 0.8f);

  Network symmetric{{1, {{2, 0.8f}}}, {2, {{1, 0.8f}, {3, 0.4f}}}};
  trim_reverse(symmetric, {{2, 1}});
  assert(symmetric.at(1).at(2) == 0.8f);
  assert(symmetric.at(2).count(1) == 0);
  assert(symmetric.at(2).at(3) == 0.4f);

  Network empty;
  trim_reverse(empty, {});
  assert(empty.empty());
}
'''
    source = tmp_path / 'regression.cpp'
    source.write_text(ARACNE.patch_source(before + ARACNE.OLD + after))
    executable = tmp_path / 'regression'
    subprocess.run(['g++', '-std=c++17', str(source), '-o', str(executable)], check=True,
                   capture_output=True, text=True)
    subprocess.run([str(executable)], check=True, capture_output=True, text=True)
    # Confirm the exact original statement would still reproduce the crash.
    source.write_text(before + ARACNE.OLD + after)
    subprocess.run(['g++', '-std=c++17', str(source), '-o', str(executable)], check=True,
                   capture_output=True, text=True)
    original = subprocess.run([str(executable)], capture_output=True, text=True)
    assert original.returncode != 0
    assert 'out_of_range' in original.stderr


@pytest.mark.parametrize('shape,expected', [((31, 1), (True, False)), ((1, 3), (False, True)),
                                            ((1, 1), (False, False)), ((31, 3), (True, True))])
def test_miniex_only_disables_clustering_on_singleton_axes(shape, expected):
    calls = []
    sns = SimpleNamespace(clustermap=lambda *args, **kwargs: calls.append((args, kwargs)))
    source = 'def plot(df, column_colors):\n    fig_width, fig_height = 10, 5\n    sns.clustermap(\n        df,\n' + MINIEX.OLD + '\n    )\n'
    namespace = {'sns': sns}
    exec(MINIEX.patch_source(source), namespace)
    data = SimpleNamespace(shape=shape)
    colors = object()
    namespace['plot'](data, colors)
    args, kwargs = calls[0]
    assert args == (data,)
    assert kwargs['col_colors'] is colors
    assert (kwargs['row_cluster'], kwargs['col_cluster']) == expected
    assert kwargs['figsize'] == (10, 5)
