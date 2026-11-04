"""The plugin against real GHDL and slang.  Skipped when pyGHDL isn't installed."""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

from svhier.frontends import empty_fragment
from svhier_ghdl import GhdlFrontend, parse_tree, tree_to_defs

MIXED = Path(__file__).parent / "mixed"
needs_ghdl = pytest.mark.skipif(importlib.util.find_spec("pyGHDL") is None, reason="pyGHDL is not installed")

_TREE = """\
architecture rtl of top
  u_sv: component sv_child [not bound]
  g_up: for-generate (1 to 2)
    generate statement body
    u_a: component leaf
      architecture sim of leaf
    generate statement body
    u_a: component leaf
      architecture sim of leaf
  g_down: for-generate (1 downto 0)
    generate statement body
    u_d: entity leaf(rtl)
      architecture rtl of leaf
    generate statement body
    u_d: entity leaf(rtl)
      architecture rtl of leaf
  g_if: if-generate [true]
    generate statement body
    u_i: component renamed
      architecture rtl of real_entity
  blk: block
    u_b: entity leaf(rtl)
      architecture rtl of leaf
  : process
"""


def _top_insts():
    defs = {d.name: d for d in tree_to_defs(parse_tree(_TREE), {})}
    return [(i["inst_name"], i["mod_name"]) for i in defs["top"].insts]


def test_tree_paths_match_the_sv_format():
    names = [n for n, _ in _top_insts()]
    assert names == ["u_sv", "g_up[1].u_a", "g_up[2].u_a", "g_down[1].u_d", "g_down[0].u_d", "g_if.u_i", "blk.u_b"]


def test_unbound_uses_component_and_bound_uses_entity():
    insts = dict(_top_insts())
    assert insts["u_sv"] == "sv_child"
    assert insts["g_if.u_i"] == "real_entity"


def _svhier(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", "from svhier.cli import main; main()", *args],
                          capture_output=True, text=True, cwd=MIXED, check=False)


@needs_ghdl
def test_tree_crosses_languages_both_ways_with_parameters():
    proc = _svhier(".", "--top", "sv_top", "--tree")
    assert proc.returncode == 0, proc.stderr
    for line in ("u_vhd (top_vhd)", "u_sv (sv_child)", "u_gc (sv_grand)", "u_vb (vhdl_bottom)", "g_for[2].u_g (leaf_vhd)"):
        assert line in proc.stdout
    assert "g_for[3]" not in proc.stdout
    assert "u_off" not in proc.stdout  # N = 3, so `if N > 5 generate` is off


@needs_ghdl
def test_vhdl_top_uses_its_own_default_generics():
    proc = _svhier(".", "--top", "TOP_VHD", "--tree")
    assert "g_for[1].u_g" in proc.stdout
    assert "g_for[2].u_g" not in proc.stdout


@needs_ghdl
def test_filelist_keeps_cross_language_files_in_a_valid_order():
    proc = _svhier(".", "--top", "sv_top", "--filelist", "-", "--no-tree")
    files = [ln for ln in proc.stdout.splitlines() if ln and not ln.startswith(("//", "+"))]
    assert {"sv_child.sv", "sv_grand.sv", "sv_req.sv", "bottom.vhd", "leaf_vhd.vhd"} <= set(files)
    assert "sv_orphan.sv" not in files
    assert files.index("pkg.vhd") < files.index("top_vhd.vhd")
    assert files.index("leaf_vhd.vhd") < files.index("top_vhd.vhd")


@needs_ghdl
def test_missing_module_is_reported_and_required_parameter_is_passed():
    proc = _svhier(".", "--top", "sv_top", "--no-tree")
    assert "nowhere" in proc.stderr
    assert "its parameters aren't known" not in " ".join(proc.stderr.split())


_LEAF_VHD = "entity leaf is end;\narchitecture rtl of leaf is begin end;\n"


@needs_ghdl
def test_command_file_with_only_vhdl(tmp_path):
    (tmp_path / "leaf.vhd").write_text(_LEAF_VHD)
    (tmp_path / "top.vhdl").write_text(
        "entity top is end;\narchitecture rtl of top is begin\n  u_leaf: entity work.leaf;\nend;\n")
    # -F: relative to the file, and through a nested file, a variable and a comment.
    (tmp_path / "inner.f").write_text("# not_a_file.vhd\n$SVHIER_TEST_DIR/top.vhdl\n")
    (tmp_path / "list.f").write_text("// not_a_file.vhd\nleaf.vhd\n-f inner.f\n")
    proc = subprocess.run(
        [sys.executable, "-c", "from svhier.cli import main; main()", "-F", str(tmp_path / "list.f"), "--top", "top", "-E"],
        capture_output=True, text=True, check=False, env={**os.environ, "SVHIER_TEST_DIR": str(tmp_path)})
    assert proc.returncode == 0, proc.stderr
    assert "u_leaf (leaf)" in proc.stdout
    assert "not_a_file" not in proc.stderr


@needs_ghdl
def test_command_file_mixing_languages(tmp_path):
    (tmp_path / "leaf.vhd").write_text(_LEAF_VHD)
    (tmp_path / "top.sv").write_text("module top; leaf u_leaf (); endmodule\n")
    (tmp_path / "list.f").write_text("leaf.vhd\ntop.sv\n")
    proc = subprocess.run(
        [sys.executable, "-c", "from svhier.cli import main; main()", "-F", str(tmp_path / "list.f"), "--top", "top", "--filelist", "-", "--tree"],
        capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    assert "u_leaf (leaf)" in proc.stdout
    assert "(blackbox)" not in proc.stdout
    assert str(tmp_path / "leaf.vhd") in proc.stdout


def _wide_svhier(*args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "COLUMNS": "400"}
    return subprocess.run([sys.executable, "-c", "from svhier.cli import main; main()", *args], capture_output=True, text=True, check=False, env=env)


def _entity(name: str) -> str:
    return f"entity {name} is end;\narchitecture rtl of {name} is begin end;\n"


@needs_ghdl
def test_entity_defined_twice_is_reported_with_the_copy_ghdl_uses(tmp_path):
    (tmp_path / "a_variant.vhd").write_text(_entity("leaf"))
    (tmp_path / "b_variant.vhd").write_text(_entity("leaf"))
    (tmp_path / "top.vhd").write_text("entity top is end;\narchitecture rtl of top is begin\n  u_leaf: entity work.leaf;\nend;\n")
    proc = _wide_svhier(str(tmp_path), "--top", "top", "--filelist", "-", "--tree")
    assert proc.returncode == 0, proc.stderr
    assert "Defined more than once" in proc.stderr
    row = next(line for line in proc.stderr.splitlines() if "leaf" in line and "entity" in line)
    assert row.index("b_variant.vhd") < row.index("a_variant.vhd")
    assert "b_variant.vhd" in proc.stdout
    assert "a_variant.vhd" not in proc.stdout


@needs_ghdl
def test_same_name_in_two_libraries_is_not_a_duplicate(tmp_path):
    (tmp_path / "lib_a").mkdir()
    (tmp_path / "lib_b").mkdir()
    (tmp_path / "lib_a/leaf.vhd").write_text(_entity("leaf"))
    (tmp_path / "lib_b/leaf.vhd").write_text(_entity("leaf"))
    (tmp_path / "top.vhd").write_text(
        "library lib_a, lib_b;\nentity top is end;\narchitecture rtl of top is begin\n"
        "  u_a: entity lib_a.leaf;\n  u_b: entity lib_b.leaf;\nend;\n")
    proc = _wide_svhier("-r", str(tmp_path), "--top", "top")
    assert proc.returncode == 0, proc.stderr
    assert "Defined more than once" not in proc.stderr


@needs_ghdl
def test_package_defined_twice_is_reported(tmp_path):
    pkg = "package common_pkg is end;\n"
    (tmp_path / "a.vhd").write_text(pkg)
    (tmp_path / "b.vhd").write_text(pkg)
    (tmp_path / "top.vhd").write_text("use work.common_pkg.all;\n" + _entity("top"))
    proc = _wide_svhier(str(tmp_path), "--top", "top")
    assert proc.returncode == 0, proc.stderr
    assert "Defined more than once" in proc.stderr
    assert "common_pkg" in proc.stderr


@needs_ghdl
def test_top_that_fails_is_not_called_missing(tmp_path):
    (tmp_path / "top.vhd").write_text(
        "library gone;\nuse gone.stuff.all;\nuse work.not_there.all;\n" + _entity("top"))
    proc = _wide_svhier(str(tmp_path / "top.vhd"), "--top", "top")
    assert proc.returncode == 1
    assert "top module 'top' not found" not in proc.stderr
    assert "top module 'top' could not be elaborated" in proc.stderr
    rows = [line.split("│")[1:4] for line in proc.stderr.splitlines() if line.startswith("│") and " unit " in line]
    assert [[cell.strip() for cell in row] for row in rows] == [
        ["unit", "gone.stuff", "top.vhd"], ["unit", "not_there", "top.vhd"]]


@needs_ghdl
def test_top_nothing_defines_is_still_called_missing(tmp_path):
    (tmp_path / "leaf.vhd").write_text(_entity("leaf"))
    proc = _wide_svhier(str(tmp_path / "leaf.vhd"), "--top", "nowhere")
    assert proc.returncode == 1
    assert "top module 'nowhere' not found" in proc.stderr
    assert "could not be elaborated" not in proc.stderr


@needs_ghdl
def test_failing_variant_points_at_the_way_out(tmp_path):
    (tmp_path / "a_sim.vhd").write_text(_entity("leaf"))
    (tmp_path / "b_fpga.vhd").write_text("library gone;\nuse gone.prims.all;\n" + _entity("leaf"))
    (tmp_path / "top.vhd").write_text("entity top is end;\narchitecture rtl of top is begin\n  u_leaf: entity work.leaf;\nend;\n")
    proc = _wide_svhier(str(tmp_path), "--top", "top")
    assert proc.returncode == 1
    assert "-x b_fpga.vhd" in proc.stderr
    proc = _wide_svhier(str(tmp_path), "--top", "top", "-x", "b_fpga.vhd")
    assert proc.returncode == 0, proc.stderr
    assert "u_leaf (leaf)" in proc.stdout


@needs_ghdl
def test_error_in_a_shared_file_is_listed_once(tmp_path):
    """Without --top every entity is a root, so one bad file would be reported once per entity."""
    (tmp_path / "pkg.vhd").write_text("library gone;\nuse gone.stuff.all;\npackage bad_pkg is end;\n")
    for name in ("one", "two", "three"):
        (tmp_path / f"{name}.vhd").write_text("use work.bad_pkg.all;\n" + _entity(name))
    proc = _wide_svhier(str(tmp_path), "-E")
    assert proc.returncode == 1
    lines = [line for line in proc.stderr.splitlines() if line.startswith("error:") and 'unit "stuff" not found' in line]
    assert len(lines) == 1, proc.stderr


def test_only_errors_with_a_location_are_the_same_error_for_every_top():
    errors = ["ghdl: a: /f.vhd:1:2:error: bad", "ghdl: b: /f.vhd:1:2:error: bad", "ghdl: a: raised OOPS", "ghdl: b: raised OOPS"]
    kept = GhdlFrontend()._report_errors(errors, empty_fragment())  # pylint: disable=protected-access
    assert kept == ["ghdl: a: /f.vhd:1:2:error: bad", "ghdl: a: raised OOPS", "ghdl: b: raised OOPS"]
