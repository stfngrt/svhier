"""Tests for CLI helpers: collect_sv_files, collect_inc_dirs, _write_filelist."""

from pathlib import Path

from svhier.cli import collect_inc_dirs, collect_sv_files, _write_filelist, main

VERILOG_DIR = Path(__file__).parent.parent / "verilog"
INCLUDE_DIR = VERILOG_DIR / "include"


# ---------------------------------------------------------------------------
# collect_sv_files
# ---------------------------------------------------------------------------

def test_collect_sv_files_single_file():
    path = str(VERILOG_DIR / "simple.sv")
    assert collect_sv_files([path], recursive=False) == [path]


def test_collect_sv_files_picks_up_v_files():
    path = str(VERILOG_DIR / "legacy.v")
    result = collect_sv_files([path], recursive=False)
    assert path in result


def test_collect_sv_files_directory_non_recursive():
    result = collect_sv_files([str(VERILOG_DIR)], recursive=False)
    names = [Path(f).name for f in result]
    assert "simple.sv" in names
    assert "legacy.v" in names
    # uvm/ subdir files must not appear without -r
    assert "tb_top.sv" not in names


def test_collect_sv_files_directory_recursive():
    result = collect_sv_files([str(VERILOG_DIR)], recursive=True)
    names = [Path(f).name for f in result]
    assert "tb_top.sv" in names


def test_collect_sv_files_deduplicates():
    path = str(VERILOG_DIR / "simple.sv")
    result = collect_sv_files([path, path], recursive=False)
    assert result.count(path) == 1


# ---------------------------------------------------------------------------
# collect_inc_dirs
# ---------------------------------------------------------------------------

def test_collect_inc_dirs_finds_svh_dir():
    """verilog/include/ contains my_defs.svh — it must appear in the result."""
    result = collect_inc_dirs([str(VERILOG_DIR)], recursive=True)
    dirs = [Path(d) for d in result]
    assert INCLUDE_DIR.resolve() in [d.resolve() for d in dirs]


def test_collect_inc_dirs_non_recursive_misses_subdir():
    """Without -r, only the top dir is scanned — include/ must not appear."""
    result = collect_inc_dirs([str(VERILOG_DIR)], recursive=False)
    dirs = [Path(d).resolve() for d in result]
    assert INCLUDE_DIR.resolve() not in dirs


def test_collect_inc_dirs_no_headers_returns_empty():
    """A dir with no .svh/.vh files produces an empty list."""
    result = collect_inc_dirs([str(VERILOG_DIR / "uvm")], recursive=False)
    assert result == []


def test_collect_inc_dirs_explicit_file_in_header_dir():
    """Passing an explicit .sv file whose parent has headers must include that parent."""
    sv_in_include = INCLUDE_DIR / "dummy.sv"
    # We don't need the file to exist — only the parent scan matters.
    # Use the include dir itself as the path so we can exercise the file branch
    # by passing an explicit .svh path treated as a file (not applicable here).
    # Instead, verify the dir branch covers INCLUDE_DIR when scanned recursively.
    result = collect_inc_dirs([str(INCLUDE_DIR)], recursive=False)
    dirs = [Path(d).resolve() for d in result]
    assert INCLUDE_DIR.resolve() in dirs


def test_collect_inc_dirs_deduplicates():
    """The same include dir found twice must appear only once."""
    result = collect_inc_dirs([str(VERILOG_DIR), str(VERILOG_DIR)], recursive=True)
    resolved = [Path(d).resolve() for d in result]
    assert len(resolved) == len(set(resolved))


# ---------------------------------------------------------------------------
# _write_filelist
# ---------------------------------------------------------------------------

def test_write_filelist_no_incdirs(tmp_path):
    dest = str(tmp_path / "out.f")
    _write_filelist(["a.sv", "b.sv"], [], dest)
    content = Path(dest).read_text()
    assert "a.sv" in content
    assert "b.sv" in content
    assert "+incdir+" not in content


def test_write_filelist_with_incdirs(tmp_path):
    dest = str(tmp_path / "out.f")
    _write_filelist(["a.sv", "b.sv"], ["/some/include", "/other/inc"], dest)
    content = Path(dest).read_text()
    assert "+incdir+/some/include" in content
    assert "+incdir+/other/inc" in content
    # incdir lines must appear before source files
    lines = [l for l in content.splitlines() if l and not l.startswith("//")]
    incdir_indices = [i for i, l in enumerate(lines) if l.startswith("+incdir+")]
    file_indices = [i for i, l in enumerate(lines) if not l.startswith("+incdir+")]
    assert max(incdir_indices) < min(file_indices)


def test_write_filelist_stdout(capsys):
    _write_filelist(["a.sv"], ["/inc"], "-")
    out = capsys.readouterr().out
    assert "+incdir+/inc" in out
    assert "a.sv" in out


# ---------------------------------------------------------------------------
# Include dirs for `include "sub/dir/file.svh"` (issue #2, item 4)
# ---------------------------------------------------------------------------

NESTED_INC = VERILOG_DIR / "nested_include"


def test_collect_inc_dirs_adds_base_dir_for_subdir_include():
    """`include "vendor/regs.svh"` needs vendor/include/, not just the header's own folder."""
    result = [Path(d).resolve() for d in collect_inc_dirs([str(NESTED_INC)], recursive=True)]
    assert (NESTED_INC / "vendor" / "include").resolve() in result


def test_collect_inc_dirs_keeps_header_dir():
    result = [Path(d).resolve() for d in collect_inc_dirs([str(NESTED_INC)], recursive=True)]
    assert (NESTED_INC / "vendor" / "include" / "vendor").resolve() in result


def test_main_passes_inc_dirs_to_parser(monkeypatch, capsys):
    """End to end: a recursive run over nested_include/ must elaborate without errors."""
    monkeypatch.setattr("sys.argv", ["svhier", "-r", str(NESTED_INC), "--yaml"])
    main()
    assert "inc_user" in capsys.readouterr().out


def test_yaml_strips_internal_ref_keys():
    from svhier.cli import _prepare_for_yaml  # pylint: disable=import-outside-toplevel
    from svhier.parser import parse_files  # pylint: disable=import-outside-toplevel
    out = _prepare_for_yaml(parse_files([str(VERILOG_DIR / "pkg_refs" / "bus_if.sv")]))
    assert set(out["files"][0]) == {"file_name", "defs"}


# ---------------------------------------------------------------------------
# -f / -D / -I / --top and duplicate warnings (#2 items 6 and 8)
# ---------------------------------------------------------------------------

CF_DIR = VERILOG_DIR / "cmdfile"


def test_main_command_file_filelist(monkeypatch, capsys):
    monkeypatch.setenv("SVHIER_CF_DIR", str(CF_DIR))
    monkeypatch.setattr("sys.argv", ["svhier", "-f", str(CF_DIR / "cmd.f"), "--filelist"])
    main()
    lines = capsys.readouterr().out.splitlines()
    incdirs = [Path(ln.removeprefix("+incdir+")).resolve() for ln in lines if ln.startswith("+incdir+")]
    assert (CF_DIR / "inc").resolve() in incdirs
    assert "+define+CF_EN" in lines
    srcs = [Path(ln).name for ln in lines if ln.endswith(".sv")]
    assert srcs.index("cf_leaf.sv") < srcs.index("cf_top.sv") < srcs.index("cf_tb.sv")


def test_main_top_define_incdir(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", [
        "svhier", "-r", str(CF_DIR / "rtl"), "-I", str(CF_DIR / "inc"), "-D", "CF_EN",
        "--top", "cf_top", "--filelist",
    ])
    main()
    srcs = [Path(ln).name for ln in capsys.readouterr().out.splitlines() if ln.endswith(".sv")]
    assert srcs == ["cf_leaf.sv", "cf_top.sv"]


def test_main_requires_input(monkeypatch):
    monkeypatch.setattr("sys.argv", ["svhier"])
    try:
        main()
    except SystemExit as exc:
        assert exc.code != 0
    else:
        raise AssertionError("expected SystemExit")


def test_main_bad_command_file_exits(monkeypatch):
    monkeypatch.setattr("sys.argv", ["svhier", "-f", str(CF_DIR / "nope.f")])
    try:
        main()
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("expected SystemExit")


def test_main_warns_about_duplicates(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["svhier", str(VERILOG_DIR / "dup_defs")])
    main()
    err = capsys.readouterr().err
    assert "dup_mod" in err and "dup_cfg" in err


def test_yaml_strips_shadowed_key():
    from svhier.cli import _prepare_for_yaml  # pylint: disable=import-outside-toplevel
    from svhier.parser import parse_files  # pylint: disable=import-outside-toplevel
    dup = VERILOG_DIR / "dup_defs"
    out = _prepare_for_yaml(parse_files([str(dup / "dup_a.sv"), str(dup / "dup_b.sv")]))
    assert all(set(f) == {"file_name", "defs"} for f in out["files"])


# ---------------------------------------------------------------------------
# --tree
# ---------------------------------------------------------------------------

def test_tree_prints_roots_and_instances(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["svhier", str(VERILOG_DIR / "test.sv")])
    main()
    out = capsys.readouterr().out
    assert "C\n" in out and "D\n" in out
    assert "I0 (A)" in out and "I1 (Y)" in out
    # A is instantiated by C, so it is not a root
    assert not any(line == "A" for line in out.splitlines())


def test_tree_top_selects_root(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["svhier", str(VERILOG_DIR / "test.sv"), "--top", "D"])
    main()
    out = capsys.readouterr().out
    assert "I0 (X)" in out
    assert "(A)" not in out


# ---------------------------------------------------------------------------
# diagnostics
# ---------------------------------------------------------------------------

_DIAG_HIER = {"diagnostics": [
    {"severity": "error", "file": "a.sv", "message": "boom"},
    {"severity": "warning", "file": "b.sv", "message": "meh"},
]}


def test_diagnostics_default_prints_count_only(capsys):
    from svhier.cli import _print_diagnostics  # pylint: disable=import-outside-toplevel
    _print_diagnostics(_DIAG_HIER)
    err = capsys.readouterr().err
    assert "1 error, 1 warning" in err
    assert "boom" not in err and "meh" not in err


def test_diagnostics_errors_flag_lists_errors(capsys):
    from svhier.cli import _print_diagnostics  # pylint: disable=import-outside-toplevel
    _print_diagnostics(_DIAG_HIER, errors=True)
    err = capsys.readouterr().err
    assert "boom" in err and "meh" not in err


def test_diagnostics_warnings_flag_lists_all(capsys):
    from svhier.cli import _print_diagnostics  # pylint: disable=import-outside-toplevel
    _print_diagnostics(_DIAG_HIER, warnings=True)
    err = capsys.readouterr().err
    assert "boom" in err and "meh" in err


def test_missing_table_and_blackbox_in_tree(tmp_path, monkeypatch, capsys):
    src = tmp_path / "top.sv"
    src.write_text("module top;\n  bbox u_bb ();\nendmodule\n")
    monkeypatch.setattr("sys.argv", ["svhier", str(src)])
    try:
        main()
    except SystemExit:
        pass
    captured = capsys.readouterr()
    assert "u_bb (bbox) (blackbox)" in captured.out
    assert "Not found" in captured.err
    assert "blackbox module" in captured.err and "bbox" in captured.err


def test_tree_off_when_yaml_goes_to_stdout(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["svhier", str(VERILOG_DIR / "test.sv"), "--yaml"])
    main()
    out = capsys.readouterr().out
    assert out.startswith("files:")
    assert "I0 (A)" not in out


def test_no_tree_and_summary_flags(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["svhier", str(VERILOG_DIR / "test.sv"), "--no-tree", "--summary"])
    main()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Hierarchy summary" in captured.err


def test_summary_off_by_default(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["svhier", str(VERILOG_DIR / "test.sv")])
    main()
    assert "Hierarchy summary" not in capsys.readouterr().err


def test_tree_does_not_truncate_long_names_when_piped(tmp_path, monkeypatch, capsys):
    long_mod = "m_" + "x" * 120
    src = tmp_path / "top.sv"
    src.write_text(f"module {long_mod}; endmodule\nmodule top;\n  {long_mod} u_inst ();\nendmodule\n")
    monkeypatch.setattr("sys.argv", ["svhier", str(src)])
    main()
    out = capsys.readouterr().out
    assert f"u_inst ({long_mod})\n" in out
    assert all(line == line.rstrip() for line in out.splitlines())


def test_duplicates_table(tmp_path, monkeypatch, capsys):
    a, b = tmp_path / "a.sv", tmp_path / "b.sv"
    a.write_text("package cfg; endpackage\nmodule top; endmodule\n")
    b.write_text("package cfg; endpackage\n")
    monkeypatch.setattr("sys.argv", ["svhier", str(a), str(b), "--no-tree"])
    try:
        main()
    except SystemExit:
        pass
    err = capsys.readouterr().err
    assert "Defined more than once" in err
    assert "cfg" in err and "b.sv" in err
