"""Tests for -y library directory resolution."""

from pathlib import Path

import pytest

from svhier.cli import main
from svhier.libsearch import LibraryIndex, resolve_missing


@pytest.fixture(name="design")
def _design(tmp_path: Path) -> Path:
    """A top in src/ that needs one module from lib/, which in turn needs another."""
    (tmp_path / "src").mkdir()
    (tmp_path / "lib" / "cells" / "inc").mkdir(parents=True)
    (tmp_path / "lib" / "fpga").mkdir()
    (tmp_path / "src" / "top.sv").write_text(
        "module top;\n  import lib_pkg::*;\n  mid u_mid ();\nendmodule\n")
    (tmp_path / "lib" / "mid.sv").write_text(
        '`include "defs.svh"\nmodule mid;\n  leaf u_leaf ();\nendmodule\n')
    (tmp_path / "lib" / "cells" / "leaf_cells.sv").write_text("module leaf;\nendmodule\n")
    (tmp_path / "lib" / "fpga" / "leaf_cells_xilinx.sv").write_text("module leaf;\nendmodule\n")
    (tmp_path / "lib" / "cells" / "inc" / "defs.svh").write_text("`define W 4\n")
    (tmp_path / "lib" / "lib_pkg.sv").write_text("package lib_pkg;\nendpackage\n")
    (tmp_path / "lib" / "unused.sv").write_text("module unused;\nendmodule\n")
    return tmp_path


def test_index_ranks_name_match_then_shorter_path(tmp_path):
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "a" / "b" / "foo.sv").write_text("module foo; endmodule\n")
    (tmp_path / "a" / "cells.sv").write_text("module foo; endmodule\n")
    index = LibraryIndex([str(tmp_path)])
    assert index.find_definition("foo")[0].name == "foo.sv"


def test_resolve_skips_files_already_compiled(design):
    index = LibraryIndex([str(design / "lib")])
    missing = {"modules": {"mid": ["top.sv"]}}
    files, _ = resolve_missing(missing, index, [str(design / "lib" / "mid.sv")], [])
    assert not files


def test_libdir_pulls_in_only_needed_files(design, monkeypatch, capsys):
    out_f = design / "out.f"
    monkeypatch.setattr("sys.argv", [
        "svhier", str(design / "src"), "-y", str(design / "lib"), "--filelist", str(out_f)])
    main()
    captured = capsys.readouterr()
    assert "blackbox" not in captured.out
    assert "u_leaf (leaf)" in captured.out
    assert "Not found" not in captured.err
    filelist = out_f.read_text()
    assert "mid.sv" in filelist and "lib_pkg.sv" in filelist
    assert "leaf_cells.sv" in filelist and "xilinx" not in filelist
    assert "unused.sv" not in filelist
    assert f"+incdir+{design / 'lib' / 'cells' / 'inc'}" in filelist


def test_options_and_paths_can_be_intermixed(design, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", [
        "svhier", "-y", str(design / "lib"), str(design / "src"), "--no-tree", str(design / "src" / "top.sv")])
    main()
    assert "Pulled in from -y" in capsys.readouterr().err
