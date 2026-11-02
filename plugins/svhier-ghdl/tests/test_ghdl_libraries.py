"""Compiling each VHDL file into the right library without being told which."""

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from svhier_ghdl import assign_libraries

LIBS = Path(__file__).parent / "libs"
needs_ghdl = pytest.mark.skipif(importlib.util.find_spec("pyGHDL") is None, reason="pyGHDL is not installed")


def _file(units=(), arch_of=(), refs=(), libs=()) -> dict:
    return {"units": list(units), "arch_of": list(arch_of), "refs": [list(r) for r in refs], "libs": list(libs)}


def test_named_reference_places_the_only_file_defining_the_unit():
    scan = {"top.vhd": _file(["top"], refs=[("utils", "pkg")], libs=["utils"]), "x/pkg.vhd": _file(["pkg"])}
    assert assign_libraries(scan, "work")[0] == {"top.vhd": "work", "x/pkg.vhd": "utils"}


def test_directory_named_after_the_library_breaks_a_tie():
    scan = {
        "top.vhd": _file(["top"], refs=[("lib_b", "leaf")], libs=["lib_b"]),
        "lib_a/leaf.vhd": _file(["leaf"]),
        "lib_b/leaf.vhd": _file(["leaf"]),
    }
    libraries, notes = assign_libraries(scan, "work")
    assert libraries["lib_b/leaf.vhd"] == "lib_b"
    assert libraries["lib_a/leaf.vhd"] == "work"
    assert not notes


def test_tie_nothing_breaks_is_noted():
    scan = {
        "top.vhd": _file(["top"], refs=[("lib", "leaf")], libs=["lib"]),
        "a/leaf.vhd": _file(["leaf"]),
        "b/leaf.vhd": _file(["leaf"]),
    }
    libraries, notes = assign_libraries(scan, "work")
    assert libraries["a/leaf.vhd"] == "lib"
    assert libraries["b/leaf.vhd"] == "work"
    assert notes == ["several files define leaf for library lib; using a/leaf.vhd"]


def test_work_reference_pulls_a_file_into_the_library_of_the_file_that_says_it():
    scan = {
        "top.vhd": _file(["top"], refs=[("lib", "chain")], libs=["lib"]),
        "chain.vhd": _file(["chain"], refs=[("work", "helper")]),
        "helper.vhd": _file(["helper"], refs=[("work", "deeper")]),
        "deeper.vhd": _file(["deeper"]),
    }
    libraries = assign_libraries(scan, "work")[0]
    assert libraries["chain.vhd"] == libraries["helper.vhd"] == libraries["deeper.vhd"] == "lib"


def test_a_file_using_work_joins_the_library_of_what_it_references():
    scan = {
        "top.vhd": _file(["top"], refs=[("lib", "pkg")], libs=["lib"]),
        "pkg.vhd": _file(["pkg"]),
        "user.vhd": _file(["user"], refs=[("work", "pkg")]),
    }
    assert assign_libraries(scan, "work")[0]["user.vhd"] == "lib"


def test_architecture_in_its_own_file_follows_its_entity():
    scan = {
        "top.vhd": _file(["top"], refs=[("lib", "leaf")], libs=["lib"]),
        "leaf.vhd": _file(["leaf"]),
        "leaf_rtl.vhd": _file(arch_of=["leaf"]),
    }
    assert assign_libraries(scan, "work")[0]["leaf_rtl.vhd"] == "lib"


def test_entity_follows_an_architecture_that_was_placed_first():
    scan = {
        "user.vhd": _file(["user"], refs=[("lib", "only_arch_ref")], libs=["lib"]),
        "arch.vhd": _file(["only_arch_ref"], arch_of=["ent"]),
        "ent.vhd": _file(["ent"]),
    }
    assert assign_libraries(scan, "work")[0]["ent.vhd"] == "lib"


def test_everything_else_goes_to_the_default_library():
    scan = {"a.vhd": _file(["a"], refs=[("work", "b")]), "b.vhd": _file(["b"])}
    assert assign_libraries(scan, "mine") == ({"a.vhd": "mine", "b.vhd": "mine"}, [])


def test_references_to_libraries_or_units_outside_the_inputs_are_ignored():
    scan = {"a.vhd": _file(["a"], refs=[("ieee", "std_logic_1164"), ("gone", "a_unit")], libs=["ieee", "gone"])}
    assert assign_libraries(scan, "work") == ({"a.vhd": "work"}, [])


def test_a_field_of_a_record_is_not_a_library_reference():
    scan = {"a.vhd": _file(["a"], refs=[("rec", "b")]), "b.vhd": _file(["b"])}
    assert assign_libraries(scan, "work")[0]["b.vhd"] == "work"


def test_names_are_case_insensitive():
    scan = {"a.vhd": _file(["a"], refs=[("LIB", "PKG")], libs=["Lib"]), "pkg.vhd": _file(["pkg"])}
    assert assign_libraries(scan, "work")[0]["pkg.vhd"] == "lib"


def test_file_referenced_from_two_libraries_is_noted_and_stays_in_the_first():
    scan = {
        "a.vhd": _file(["a"], refs=[("lib_x", "pkg")], libs=["lib_x"]),
        "b.vhd": _file(["b"], refs=[("lib_y", "pkg")], libs=["lib_y"]),
        "pkg.vhd": _file(["pkg"]),
    }
    libraries, notes = assign_libraries(scan, "work")
    assert libraries["pkg.vhd"] == "lib_x"
    assert notes == ["pkg.vhd is referenced as part of both lib_x and lib_y; using lib_x"]


def _svhier(*args: str, cwd: Path = LIBS) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", "from svhier.cli import main; main()", *args],
                          capture_output=True, text=True, cwd=cwd, check=False)


def _filelist(proc: subprocess.CompletedProcess) -> list[str]:
    return [ln for ln in proc.stdout.splitlines() if ln and not ln.startswith(("//", "+"))]


@needs_ghdl
def test_design_using_libraries_elaborates_without_naming_them():
    proc = _svhier("-r", ".", "--top", "lib_top", "--tree")
    assert proc.returncode == 0, proc.stderr
    assert "u_leaf (leaf)" in proc.stdout


@needs_ghdl
def test_same_entity_name_in_two_libraries_binds_to_the_referenced_one():
    proc = _svhier("-r", ".", "--top", "lib_top", "--tree")
    assert "u_from_b (only_in_b)" in proc.stdout
    assert "u_from_a" not in proc.stdout


@needs_ghdl
def test_work_means_the_library_of_the_file_that_says_it():
    proc = _svhier("-r", ".", "--top", "lib_top", "--tree")
    assert "u_helper (helper)" in proc.stdout


@needs_ghdl
def test_library_with_no_matching_directory_is_found_from_the_reference():
    proc = _svhier("-r", ".", "--top", "lib_top", "--filelist", "-", "--no-tree")
    assert proc.returncode == 0, proc.stderr
    assert "shared/deep/util_pkg.vhd" in _filelist(proc)


@needs_ghdl
def test_filelist_has_only_the_referenced_library_and_a_valid_order():
    files = _filelist(_svhier("-r", ".", "--top", "lib_top", "--filelist", "-", "--no-tree"))
    assert "lib_b/leaf.vhd" in files
    assert "lib_a/leaf.vhd" not in files
    assert files.index("shared/deep/util_pkg.vhd") < files.index("top.vhd")
    assert files.index("lib_b/helper.vhd") < files.index("lib_b/chain.vhd")


@needs_ghdl
def test_library_nothing_defines_is_reported_by_name(tmp_path):
    shutil.copy(LIBS / "missing_lib.vhd.txt", tmp_path / "missing_lib.vhd")
    proc = _svhier(".", "--top", "lost_top", "-E", cwd=tmp_path)
    assert proc.returncode != 0
    assert "nowhere" in proc.stderr
    assert "Traceback" not in proc.stderr


@needs_ghdl
def test_files_naming_their_own_library_need_no_option(tmp_path):
    """The neorv32 pattern: everything is in one library, and files refer to it by name."""
    (tmp_path / "pkg.vhd").write_text("package own_pkg is constant W : integer := 1; end package;\n")
    (tmp_path / "leaf.vhd").write_text("entity own_leaf is end entity;\narchitecture a of own_leaf is begin end architecture;\n")
    (tmp_path / "top.vhd").write_text(
        "library own;\nuse own.own_pkg.all;\nentity own_top is end entity;\n"
        "architecture a of own_top is begin\n  u_leaf : entity own.own_leaf;\nend architecture;\n")
    proc = _svhier(".", "--top", "own_top", "--tree", cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "u_leaf (own_leaf)" in proc.stdout


@needs_ghdl
def test_load_order_does_not_change_the_library_of_a_file(tmp_path):
    """The default library's file comes last, after files for other libraries were loaded."""
    (tmp_path / "a_pkg.vhd").write_text("package a_pkg is constant W : integer := 1; end package;\n")
    (tmp_path / "z_top.vhd").write_text(
        "library named;\nuse named.a_pkg.all;\nentity z_top is end entity;\narchitecture a of z_top is begin end architecture;\n")
    proc = _svhier(".", "--top", "z_top", "--tree", cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "z_top" in proc.stdout


@needs_ghdl
def test_an_explicit_work_library_still_applies(tmp_path):
    (tmp_path / "pkg.vhd").write_text("package pkg is constant W : integer := 1; end package;\n")
    (tmp_path / "top.vhd").write_text(
        "library mine;\nuse mine.pkg.all;\nentity m_top is end entity;\narchitecture a of m_top is begin end architecture;\n")
    proc = _svhier(".", "--top", "m_top", "--vhdl-work", "MINE", "--tree", cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr


def _component_design(path: Path) -> None:
    """``own.user`` declares and instantiates the component ``cell``."""
    (path / "top.vhd").write_text(
        "library own;\nentity c_top is end entity;\narchitecture a of c_top is begin\n  u_user : entity own.user;\nend architecture;\n")
    (path / "user.vhd").write_text(
        "entity user is end entity;\narchitecture a of user is\n  component cell is end component;\nbegin\n  u_cell : cell;\nend architecture;\n")
    (path / "cell.vhd").write_text(
        "library own;\nuse own.cell_pkg.all;\nentity cell is end entity;\narchitecture a of cell is\n"
        "  component inner is end component;\nbegin\n  u_inner : inner;\nend architecture;\n")
    (path / "cell_pkg.vhd").write_text("package cell_pkg is constant W : integer := 1; end package;\n")


@needs_ghdl
def test_component_binds_to_the_entity_in_the_library_declaring_it(tmp_path):
    _component_design(tmp_path)
    proc = _svhier(".", "--top", "c_top", "--tree", cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "u_cell (cell)" in proc.stdout
    assert "u_inner (inner) (blackbox)" in proc.stdout  # cell itself was elaborated
    assert "u_cell (cell) (blackbox)" not in proc.stdout


@needs_ghdl
def test_files_reached_only_through_a_component_are_in_the_filelist(tmp_path):
    _component_design(tmp_path)
    proc = _svhier(".", "--top", "c_top", "--filelist", "-", "--no-tree", cwd=tmp_path)
    files = _filelist(proc)
    assert "cell.vhd" in files and "cell_pkg.vhd" in files
    assert files.index("cell_pkg.vhd") < files.index("cell.vhd")


@needs_ghdl
def test_explicit_entity_binding_is_not_replaced_by_the_component_name(tmp_path):
    (tmp_path / "top.vhd").write_text(
        "library lib_x;\nentity b_top is end entity;\narchitecture a of b_top is\n"
        "  component shown is end component;\n  for u_s : shown use entity lib_x.real_one;\n"
        "begin\n  u_s : shown;\nend architecture;\n")
    (tmp_path / "real_one.vhd").write_text("entity real_one is end entity;\narchitecture a of real_one is begin end architecture;\n")
    proc = _svhier(".", "--top", "b_top", "--tree", cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "u_s (real_one)" in proc.stdout


@needs_ghdl
def test_component_declared_in_a_package_binds_in_the_library_of_the_package(tmp_path):
    """The neorv32 pattern: the instantiating file isn't in the library, the package is."""
    (tmp_path / "pkg.vhd").write_text("package own_comps is\n  component memo is end component;\nend package;\n")
    (tmp_path / "memo.vhd").write_text(
        "entity memo is end entity;\narchitecture a of memo is\n  component deep is end component;\n"
        "begin\n  u_deep : deep;\nend architecture;\n")
    (tmp_path / "top.vhd").write_text(
        "library own;\nuse own.own_comps.all;\nentity p_top is end entity;\n"
        "architecture a of p_top is begin\n  u_memo : memo;\nend architecture;\n")
    proc = _svhier(".", "--top", "p_top", "--tree", cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "u_deep (deep) (blackbox)" in proc.stdout
    assert "u_memo (memo) (blackbox)" not in proc.stdout


@needs_ghdl
def test_yaml_says_which_library_each_file_belongs_to(tmp_path):
    out = tmp_path / "out.yaml"
    proc = _svhier("-r", ".", "--top", "lib_top", "--yaml", str(out), "--no-tree")
    assert proc.returncode == 0, proc.stderr
    files = {f["file_name"]: f["library"] for f in YAML(typ="safe").load(out.read_text())["files"]}
    assert files == {"top.vhd": "work", "lib_b/leaf.vhd": "lib_b", "lib_b/chain.vhd": "lib_b",
                     "lib_b/helper.vhd": "lib_b", "shared/deep/util_pkg.vhd": "utils"}
