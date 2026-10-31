"""Opt-in regression test against the cva6 core (#2).

Set ``SVHIER_CVA6_DIR`` to a cva6 checkout (cloned with submodules) to run it::

    git clone --depth 1 --recurse-submodules --shallow-submodules \\
        https://github.com/openhwfoundation/cva6 external/cva6
    SVHIER_CVA6_DIR=external/cva6 uv run pytest tests/test_cva6.py -v
"""

import os
from pathlib import Path

import pytest

from svhier.parser import compute_filelist, parse_files

CVA6_DIR = os.environ.get("SVHIER_CVA6_DIR")

pytestmark = pytest.mark.skipif(not CVA6_DIR, reason="set SVHIER_CVA6_DIR to a cva6 checkout")

# slang's own driver elaborates 94 distinct modules under cva6 from core/Flist.cva6.
EXPECTED_MODULES = 94


@pytest.fixture(scope="module")
def cva6_result():
    root = Path(CVA6_DIR).resolve()
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("CVA6_REPO_DIR", str(root))
        mp.setenv("HPDCACHE_DIR", str(root / "core" / "cache_subsystem" / "hpdcache"))
        mp.setenv("TARGET_CFG", "cv64a6_imafdc_sv39")
        return parse_files([], tops=["cva6"], command_files=[("-f", str(root / "core" / "Flist.cva6"))])


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
    assert len(_reachable(cva6_result, "cva6")) == EXPECTED_MODULES


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
