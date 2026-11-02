"""Compiling each VHDL file into the right library without being told which.

Not implemented yet, so these are strict expected failures: they turn CI red once the
feature works, as a reminder to remove the marker.
"""

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

LIBS = Path(__file__).parent / "libs"
pytestmark = [
    pytest.mark.skipif(importlib.util.find_spec("pyGHDL") is None, reason="pyGHDL is not installed"),
    pytest.mark.xfail(strict=True, reason="automatic VHDL library detection isn't implemented yet"),
]


def _svhier(*args: str, cwd: Path = LIBS) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", "from svhier.cli import main; main()", *args],
                          capture_output=True, text=True, cwd=cwd, check=False)


def _filelist(proc: subprocess.CompletedProcess) -> list[str]:
    return [ln for ln in proc.stdout.splitlines() if ln and not ln.startswith(("//", "+"))]


def test_design_using_libraries_elaborates_without_naming_them():
    proc = _svhier("-r", ".", "--top", "lib_top", "--tree")
    assert proc.returncode == 0, proc.stderr
    assert "u_leaf (leaf)" in proc.stdout


def test_same_entity_name_in_two_libraries_binds_to_the_referenced_one():
    proc = _svhier("-r", ".", "--top", "lib_top", "--tree")
    assert "u_from_b (only_in_b)" in proc.stdout
    assert "u_from_a" not in proc.stdout


def test_work_means_the_library_of_the_file_that_says_it():
    proc = _svhier("-r", ".", "--top", "lib_top", "--tree")
    assert "u_helper (helper)" in proc.stdout


def test_library_with_no_matching_directory_is_found_from_the_reference():
    proc = _svhier("-r", ".", "--top", "lib_top", "--filelist", "-", "--no-tree")
    assert proc.returncode == 0, proc.stderr
    assert "shared/deep/util_pkg.vhd" in _filelist(proc)


def test_filelist_has_only_the_referenced_library_and_a_valid_order():
    files = _filelist(_svhier("-r", ".", "--top", "lib_top", "--filelist", "-", "--no-tree"))
    assert "lib_b/leaf.vhd" in files
    assert "lib_a/leaf.vhd" not in files
    assert files.index("shared/deep/util_pkg.vhd") < files.index("top.vhd")
    assert files.index("lib_b/helper.vhd") < files.index("lib_b/chain.vhd")


def test_library_nothing_defines_is_reported_by_name(tmp_path):
    shutil.copy(LIBS / "missing_lib.vhd.txt", tmp_path / "missing_lib.vhd")
    proc = _svhier(".", "--top", "lost_top", cwd=tmp_path)
    assert proc.returncode != 0
    assert "nowhere" in proc.stderr
    assert "Traceback" not in proc.stderr


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
