"""Opt-in regression test against the cva6 core (#2).

Set ``SVHIER_CVA6_DIR`` to a cva6 checkout (cloned with submodules) to run it::

    git clone --depth 1 --recurse-submodules --shallow-submodules \\
        https://github.com/openhwfoundation/cva6 external/cva6
    SVHIER_CVA6_DIR=external/cva6 uv run pytest tests/test_cva6.py -v
"""

import os
from pathlib import Path

import pytest

from pyslang.ast import SymbolKind, VisitAction
from pyslang.driver import Driver

from svhier.parser import compute_filelist, parse_files

CVA6_DIR = os.environ.get("SVHIER_CVA6_DIR")

pytestmark = pytest.mark.skipif(not CVA6_DIR, reason="set SVHIER_CVA6_DIR to a cva6 checkout")

# Distinct modules instantiated under cva6 (TARGET_CFG=cv64a6_imafdc_sv39).  slang's full
# symbol visit finds 94, but 3 of those are placeholder elaborations of modules that are
# never instantiated (no name, outside the cva6 hierarchy); see _slang_modules.
EXPECTED_MODULES = 91


def _flist() -> str:
    return str(Path(CVA6_DIR).resolve() / "core" / "Flist.cva6")


@pytest.fixture(scope="module", autouse=True)
def cva6_env():
    root = Path(CVA6_DIR).resolve()
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("CVA6_REPO_DIR", str(root))
        mp.setenv("HPDCACHE_DIR", str(root / "core" / "cache_subsystem" / "hpdcache"))
        mp.setenv("TARGET_CFG", "cv64a6_imafdc_sv39")
        yield


@pytest.fixture(scope="module")
def cva6_result(cva6_env):  # pylint: disable=redefined-outer-name,unused-argument
    return parse_files([], tops=["cva6"], command_files=[("-f", _flist())])


def _slang_modules() -> set[str]:
    """Module names slang instantiates under cva6, from a full visit of its symbol tree."""
    driver = Driver()
    driver.addStandardArgs()
    assert driver.parseCommandLine(f'svhier -f "{_flist()}" --top cva6 --timescale 1ns/1ns')
    assert driver.processOptions() and driver.parseAllSources()
    names: set[str] = set()

    def visit(sym):
        if sym.kind == SymbolKind.Instance and sym.isModule and sym.hierarchicalPath.startswith("cva6"):
            names.add(sym.definition.name)
        return VisitAction.Advance

    driver.createCompilation().getRoot().visit(visit)
    return names


def _reachable(result, top):
    defs = {d["mod_name"]: d for f in result["files"] for d in f["defs"]}
    seen, todo = set(), [top]
    while todo:
        name = todo.pop()
        if name in seen or name not in defs:
            continue
        seen.add(name)
        todo.extend(i["mod_name"] for i in defs[name]["insts"])
    return seen


def test_cva6_elaborates_without_errors(cva6_result):  # pylint: disable=redefined-outer-name
    assert cva6_result["has_errors"] is False


def test_cva6_reaches_all_modules(cva6_result):  # pylint: disable=redefined-outer-name
    reached = _reachable(cva6_result, "cva6")
    assert reached == _slang_modules()
    assert len(reached) == EXPECTED_MODULES


def test_cva6_top_trims_filelist(cva6_result):  # pylint: disable=redefined-outer-name
    order = compute_filelist(cva6_result)
    assert set(order) == set(cva6_result["needed"])
    assert len(order) < len(cva6_result["files"])


def test_cva6_filelist_respects_dependencies(cva6_result):  # pylint: disable=redefined-outer-name
    order = compute_filelist(cva6_result)
    pos = {f: i for i, f in enumerate(order)}
    names = {d["mod_name"] for f in cva6_result["files"] for d in f["defs"]}
    assert "cva6" in names
    by_pkg = {p: f["file_name"] for f in cva6_result["files"] for p in f["pkgs"]}
    for entry in cva6_result["files"]:
        if entry["file_name"] not in pos:
            continue
        for ref in entry["refs"]:
            dep = by_pkg.get(ref)
            if dep in pos:
                assert pos[dep] < pos[entry["file_name"]], f"{entry['file_name']} before {dep}"
