"""Tests for -y library directory resolution."""

from argparse import Namespace
from pathlib import Path

import pytest

from svhier.cli import main
from svhier.frontends import Request, Root
from svhier.libsearch import LibraryIndex, resolve_missing
from svhier.slang_frontend import SlangFrontend


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


def test_top_limits_what_is_pulled_in(design, monkeypatch, capsys):
    """A module outside --top's tree must not pull library files in, or list them as pulled."""
    (design / "src" / "orphan.sv").write_text("module orphan;\n  unused u_unused ();\nendmodule\n")
    monkeypatch.setattr("sys.argv", [
        "svhier", str(design / "src"), "-y", str(design / "lib"), "--top", "top", "--no-tree"])
    main()
    err = capsys.readouterr().err
    assert "Pulled in from -y" in err and "mid" in err
    assert "unused" not in err


def test_options_and_paths_can_be_intermixed(design, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", [
        "svhier", "-y", str(design / "lib"), str(design / "src"), "--no-tree", str(design / "src" / "top.sv")])
    main()
    assert "Pulled in from -y" in capsys.readouterr().err


def test_any_case_lookup_keeps_exact_lookup_strict(tmp_path):
    (tmp_path / "Top_Cell.sv").write_text("module Top_Cell;\nendmodule\n")
    index = LibraryIndex([str(tmp_path)])
    assert not index.find_definition("top_cell")
    assert index.find("top_cell") == ([tmp_path / "Top_Cell.sv"], 0)


def test_library_index_ignore_matches_whole_names_below_the_library_dir(tmp_path):
    lib = tmp_path / "tb_vendor" / "lib"
    (lib / "tb").mkdir(parents=True)
    (lib / "btb.sv").write_text("module btb; endmodule\n", encoding="utf-8")
    (lib / "tb" / "bench.sv").write_text("module bench; endmodule\n", encoding="utf-8")
    (lib / "tb" / "bench.svh").write_text("", encoding="utf-8")
    (lib / "regs.svh").write_text("", encoding="utf-8")

    index = LibraryIndex([str(lib)], {"tb"})

    assert index.find_definition("btb") == [lib / "btb.sv"]
    assert not index.find_definition("bench")
    assert index.headers == [lib / "regs.svh"]


def test_include_with_any_suffix_is_found(tmp_path):
    """`include can name anything: prim_assert.sv, bsg_defines.sv, HardFloat_consts.vi."""
    (tmp_path / "lib/a").mkdir(parents=True)
    (tmp_path / "lib/b").mkdir(parents=True)
    (tmp_path / "lib/a/defs.sv").write_text("`define A\n")
    (tmp_path / "lib/b/consts.vi").write_text("`define B\n")
    index = LibraryIndex([str(tmp_path / "lib")])
    assert index.find_include_dir("defs.sv") == tmp_path / "lib/a"
    assert index.find_include_dir("consts.vi") == tmp_path / "lib/b"
    assert index.find_include_dir("b/consts.vi") == tmp_path / "lib"
    assert index.find_include_dir("nope.vi") is None


def test_include_search_leaves_out_ignored_files(tmp_path):
    (tmp_path / "lib/dv").mkdir(parents=True)
    (tmp_path / "lib/dv/defs.sv").write_text("`define A\n")
    assert LibraryIndex([str(tmp_path / "lib")], {"dv"}).find_include_dir("defs.sv") is None


def test_y_resolves_an_include_named_like_a_source(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "lib/inc").mkdir(parents=True)
    (tmp_path / "src/top.sv").write_text('`include "defs.sv"\nmodule top; logic [`W-1:0] x; endmodule\n')
    (tmp_path / "lib/inc/defs.sv").write_text("`define W 4\n")
    frontend = SlangFrontend()
    frontend.configure(Namespace(paths=[], recursive=False, ignore=None, defines=[], include_dirs=[], command_files=[], libdirs=[str(tmp_path / "lib")]))
    result = frontend.elaborate([str(tmp_path / "src/top.sv")], Request(roots=[Root("top", requested=True)]))
    assert not result["has_errors"], result["diagnostics"]
    assert result["missing"]["includes"] == {}
    assert str(tmp_path / "lib/inc") in result["inc_dirs"]
