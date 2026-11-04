"""-y with GHDL: only what the design needs comes in, whichever language defines it."""

import importlib.util
import json
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest

from svhier.frontends import Request, Root
from svhier_ghdl import GhdlFrontend, VhdlLibrary

needs_ghdl = pytest.mark.skipif(importlib.util.find_spec("pyGHDL") is None, reason="pyGHDL is not installed")

_ENTITY = "entity {0} is end;\narchitecture rtl of {0} is begin end;\n"


def _svhier(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", "from svhier.cli import main; main()", *args], capture_output=True, text=True, check=False)


def _write(path: Path, text: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return str(path)


@pytest.fixture(name="design")
def _design(tmp_path: Path) -> Path:
    """SV top -> VHDL in -y -> package and SV in -y; plus files nothing in the top's tree needs."""
    _write(tmp_path / "src/top.sv", "module top; lib_vhd u_v (); endmodule\n")
    _write(tmp_path / "src/unused.sv", "module unused; only_for_unused u (); endmodule\n")
    _write(tmp_path / "src/unused_vhd.vhd", "use work.only_for_unused_pkg.all;\n" + _ENTITY.format("unused_vhd"))
    _write(tmp_path / "lib/lib_vhd.vhd", """\
use work.lib_pkg.all;
entity lib_vhd is end;
architecture rtl of lib_vhd is
  component lib_sv is end component;
  component lib_leaf is end component;
begin
  u_sv : lib_sv;
  u_leaf : lib_leaf;
end;
""")
    _write(tmp_path / "lib/lib_pkg.vhd", "package lib_pkg is constant C : integer := 1; end package;\n")
    _write(tmp_path / "lib/lib_leaf.vhd", "entity lib_leaf is end;\n")
    _write(tmp_path / "lib/lib_leaf_rtl.vhd", "architecture rtl of lib_leaf is begin end;\n")
    _write(tmp_path / "lib/lib_sv.sv", "module lib_sv; endmodule\n")
    _write(tmp_path / "lib/only_for_unused.sv", "module only_for_unused; endmodule\n")
    _write(tmp_path / "lib/only_for_unused_pkg.vhd", "package only_for_unused_pkg is end package;\n")
    return tmp_path


def test_index_reads_units_and_ignores_comments_and_instances(tmp_path):
    source = _write(tmp_path / "Mixed_Case.vhd", """\
-- entity commented is
ENTITY Mixed_Case IS END;
architecture rtl of mixed_case is begin
  u : entity
    work.other;
end;
package p is end package;
package body p is end package body;
""")
    library = VhdlLibrary([str(tmp_path)], set())
    assert sorted(library.primary) == ["mixed_case", "p"]
    assert sorted(library.secondary) == ["mixed_case", "p"]
    assert library.find("MIXED_CASE") == ([Path(source)], 0)


def test_find_adds_secondary_units_from_the_same_directory_only(tmp_path):
    entity = _write(tmp_path / "a/leaf.vhd", "entity leaf is end;\n")
    body = _write(tmp_path / "a/leaf_rtl.vhd", "architecture rtl of leaf is begin end;\n")
    _write(tmp_path / "b/leaf_sim.vhd", "architecture sim of leaf is begin end;\n")
    assert VhdlLibrary([str(tmp_path)], set()).find("leaf") == ([Path(entity), Path(body)], 0)


def test_find_prefers_a_directory_named_after_the_library(tmp_path):
    _write(tmp_path / "a/cell.vhd", "entity cell is end;\n")
    wanted = _write(tmp_path / "zlib/deeper/cell.vhd", "entity cell is end;\n")
    found, others = VhdlLibrary([str(tmp_path)], set()).find("cell", "deeper")
    assert found == [Path(wanted)] and others == 1


def test_ignore_applies(tmp_path):
    _write(tmp_path / "skipme/cell.vhd", "entity cell is end;\n")
    assert not VhdlLibrary([str(tmp_path)], {"skipme"}).primary


@needs_ghdl
def test_top_pulls_exactly_what_its_tree_needs_across_languages(design):
    proc = _svhier(str(design / "src"), "-y", str(design / "lib"), "--top", "top", "--filelist", "-", "--tree")
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    for line in ("u_v (lib_vhd)", "u_sv (lib_sv)", "u_leaf (lib_leaf)"):
        assert line in out
    for name in ("lib_vhd.vhd", "lib_pkg.vhd", "lib_leaf.vhd", "lib_leaf_rtl.vhd", "lib_sv.sv"):
        assert name in out
    assert "only_for_unused" not in out and "only_for_unused" not in proc.stderr
    assert "unused" not in out
    assert "Pulled in from -y" in proc.stderr and "Not found" not in proc.stderr


@needs_ghdl
def test_vhdl_only_in_library_makes_the_plugin_run(design):
    (design / "src/unused_vhd.vhd").unlink()
    proc = _svhier(str(design / "src/top.sv"), "-y", str(design / "lib"), "--top", "top")
    assert proc.returncode == 0, proc.stderr
    assert "u_v (lib_vhd)" in proc.stdout and "(blackbox)" not in proc.stdout


@needs_ghdl
def test_sources_win_over_the_library(tmp_path):
    _write(tmp_path / "src/top.sv", "module top; common_cell u (); endmodule\n")
    source = _write(tmp_path / "src/common_cell.vhd", _ENTITY.format("common_cell"))
    library = _write(tmp_path / "lib/common_cell.sv", "module common_cell; endmodule\n")
    proc = _svhier(str(tmp_path / "src"), "-y", str(tmp_path / "lib"), "--top", "top", "--filelist", "-", "--no-tree")
    assert proc.returncode == 0, proc.stderr
    assert source in proc.stdout and library not in proc.stdout
    assert "Pulled in from -y" not in proc.stderr


@needs_ghdl
def test_vhdl_alone_resolves_its_top_and_package_from_the_library(tmp_path):
    source = _write(tmp_path / "src/placeholder.vhd", _ENTITY.format("placeholder"))
    _write(tmp_path / "lib/deep.vhd", "library mylib;\nuse mylib.deep_pkg.all;\n" + _ENTITY.format("deep"))
    _write(tmp_path / "lib/mylib/deep_pkg.vhd", "package deep_pkg is end package;\n")
    proc = _svhier(source, "-y", str(tmp_path / "lib"), "--top", "deep", "--yaml", "-", "--no-tree")
    assert proc.returncode == 0, proc.stderr
    assert "library: mylib" in proc.stdout


class _FakeTool:
    """Stands in for the worker, so the frontend's logic can run without GHDL."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.requests: list[dict] = []

    def unavailable_reason(self) -> str | None:
        return "no ghdl here"

    def run(self, request: dict) -> subprocess.CompletedProcess:
        self.requests.append(request)
        if request["op"] == "scan":
            entry = {"units": ["top"], "entities": ["top"], "arch_of": ["top"], "libs": [], "refs": [], "named": []}
            out = "@@svhier-order " + json.dumps({self.path: entry})
        else:
            out = "@@svhier-order " + json.dumps({self.path: []}) + "\narchitecture rtl of top\n  u_x: component other [not bound]\n"
        return subprocess.CompletedProcess([], 0, out, "")


def test_tool_and_library_are_injected(tmp_path):
    path = str((tmp_path / "top.vhd").resolve())
    tool = _FakeTool(path)
    made = []

    def factory(dirs, ignore):
        made.append((dirs, ignore))
        return VhdlLibrary(dirs, ignore)

    frontend = GhdlFrontend(tool=tool, library_factory=factory)
    frontend.configure(Namespace(vhdl_std="93", vhdl_work="work", libdirs=[str(tmp_path)], ignore=["x"]))
    assert frontend.unavailable_reason() == "no ghdl here"
    assert made == [([str(tmp_path)], {"x"})]
    result = frontend.elaborate([path], Request(roots=[Root("top", requested=True)]))
    assert result["unresolved"] == {"other": [{"generics": {}, "unresolved": []}]}
    assert {r["std"] for r in tool.requests} == {"93"}


def test_ignore_matches_whole_names_and_globs_in_vhdl_inputs_and_libraries(tmp_path):
    """-x treats VHDL like SV: btb.vhd survives -x tb, and globs reach -y directories."""
    root = tmp_path / "tb_parent"
    _write(root / "rtl/top.sv", "module top; btb u_b (); lib_cell u_l (); endmodule\n")
    _write(root / "rtl/btb.vhd", _ENTITY.format("btb"))
    _write(root / "rtl/tb/bench.vhd", _ENTITY.format("bench"))
    _write(root / "rtl/fifo_tb.vhd", _ENTITY.format("fifo_tb"))
    _write(root / "lib/lib_cell.vhd", _ENTITY.format("lib_cell"))
    _write(root / "lib/lib_cell_tb.vhd", _ENTITY.format("lib_cell_tb"))
    _write(root / "lib/tb/lib_cell.vhd", _ENTITY.format("lib_cell"))

    library = VhdlLibrary([str(root / "lib")], {"tb", "*_tb.vhd"})
    assert library.find("lib_cell") == ([root / "lib/lib_cell.vhd"], 0)
    assert library.find("lib_cell_tb") == ([], 0)

    proc = _svhier("-r", str(root / "rtl"), "-x", "tb", "-x", "*_tb.vhd", "--no-tree", "--filelist", "-")
    names = [Path(line).name for line in proc.stdout.splitlines() if not line.startswith(("//", "+"))]
    assert "btb.vhd" in names
    assert "bench.vhd" not in names
    assert "fifo_tb.vhd" not in names
