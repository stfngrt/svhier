"""Generics VHDL passes to SV modules.  The end-to-end tests need pyGHDL."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from svhier_ghdl import GhdlFrontend, parse_tree, to_literal, tree_to_defs

GENERICS = Path(__file__).parent / "generics"
needs_ghdl = pytest.mark.skipif(importlib.util.find_spec("pyGHDL") is None, reason="pyGHDL is not installed")

_TREE = """\
architecture rtl of top
[entity]
generic "N": discrete = 3
[architecture]
signal "clk": logic = signal 1
  u_sv: component sv_m [not bound]
  constant interface "Depth": discrete = 6
  constant interface "T": discrete = 5000000 fs
  signal interface "clk": logic = signal 3
  g: for-generate (0 to 1)
    generate statement body
    u_q: component sv_q [not bound]
    constant interface "I": discrete = 10
    generate statement body
    u_q: component sv_q [not bound]
    constant interface "I": discrete = 11
  u_none: component sv_none [not bound]
  blk: block
    constant interface "NOT_U_NONE": discrete = 1
  u_vhd: component leaf
  constant interface "W": discrete = 5
    architecture a of leaf
    [entity]
    generic "W": discrete = 5
    [architecture]
"""


def _uses() -> dict:
    defs = {d.name: d for d in tree_to_defs(parse_tree(_TREE), {})}
    return defs["top"].uses


def test_generics_of_unbound_instances_are_read_and_keep_their_spelling():
    assert _uses()["sv_m"] == [{"generics": {"Depth": "6"}, "unresolved": ["T = 5000000 fs"]}]


def test_each_generate_iteration_has_its_own_values():
    assert _uses()["sv_q"] == [{"generics": {"I": "10"}, "unresolved": []}, {"generics": {"I": "11"}, "unresolved": []}]


def test_lines_after_another_scope_do_not_attach_to_the_previous_instance():
    assert _uses()["sv_none"] == [{"generics": {}, "unresolved": []}]


def test_bound_instances_are_not_passed_on():
    assert "leaf" not in _uses()


def test_a_merged_repeat_with_the_same_values_is_kept_once():
    tree = """\
architecture rtl of top
  g: for-generate (0 to 1)
    generate statement body
    u_q: component sv_q [not bound]
    constant interface "I": discrete = 10
    generate statement body
    u_q: component sv_q [not bound]
    constant interface "I": discrete = 10
"""
    defs = {d.name: d for d in tree_to_defs(parse_tree(tree), {})}
    assert defs["top"].uses["sv_q"] == [{"generics": {"I": "10"}, "unresolved": []}]


@pytest.mark.parametrize(("kind", "value", "literal"), [
    ("discrete", "6", "6"),
    ("discrete", "-4", "-4"),
    ("bit", "true", "1'b1"),
    ("bit", "false", "1'b0"),
    ("bit", "'1'", "1'b1"),
    ("bit", "'0'", "1'b0"),
    ("logic", "'1'", "1'b1"),
    ("logic", "'0'", "1'b0"),
    ("logic", "'X'", "1'bx"),
    ("logic", "'Z'", "1'bz"),
    ("vector", '"1010"', "4'b1010"),
    ("vector", '"XZ"', "2'bxz"),
    ("float", "2.50000000000000E+00", "2.50000000000000E+00"),
    ("array", '"hi there"', '"hi there"'),
    ("array", '""', '""'),
    ("array", '"a"b"', '"a\\"b"'),
    ("array", '"back\\slash"', '"back\\\\slash"'),
])
def test_values_with_an_sv_form_are_translated(kind, value, literal):
    assert to_literal(kind, value) == literal


@pytest.mark.parametrize(("kind", "value"), [
    ("discrete", "5000000 fs"),
    ("discrete", "green"),
    ("discrete", "'c'"),
    ("logic", "'H'"),
    ("logic", "'U'"),
    ("vector", '"10HL"'),
    ("vector", '""'),
    ("array", "(3, 4)"),
    ("record", "(a: 1, b: true)"),
])
def test_values_without_an_sv_form_are_not_translated(kind, value):
    assert to_literal(kind, value) is None


def _svhier(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", "from svhier.cli import main; main()", *args],
                          capture_output=True, text=True, cwd=GENERICS, check=False)


@pytest.fixture(name="tree", scope="module")
def _tree() -> subprocess.CompletedProcess:
    return _svhier(".", "--top", "gen_top", "--tree")


@needs_ghdl
def test_evaluated_value_drives_sv_generate(tree):
    assert tree.returncode == 0, tree.stderr
    assert "g[2].u_leaf (sv_leaf)" in tree.stdout  # COUNT => N, with N = 3
    assert "g[3].u_leaf" not in tree.stdout


@needs_ghdl
def test_required_parameter_gets_its_value_so_no_note(tree):
    assert "u_count (sv_count)" in tree.stdout
    assert "sv_count: its parameters" not in tree.stderr


@needs_ghdl
def test_vhdl_component_default_beats_the_sv_default(tree):
    assert "on.u_on (sv_leaf)" in tree.stdout
    assert "off.u_off" not in tree.stdout


@needs_ghdl
def test_generic_names_match_regardless_of_case(tree):
    assert "d[1].u_d (sv_leaf)" in tree.stdout  # depth => N - 1 reaches DEPTH
    assert "d[2].u_d" not in tree.stdout


@needs_ghdl
def test_instances_with_different_values_show_both_settings(tree):
    assert "m0.u_mode0 (sv_leaf)" in tree.stdout
    assert "m1.u_mode1 (sv_leaf)" in tree.stdout


@needs_ghdl
def test_every_literal_kind_reaches_sv(tree):
    for name in ("u_i", "u_neg", "u_b", "u_sl", "u_v", "u_s", "u_r"):
        assert f".{name} (sv_leaf)" in tree.stdout, name


@needs_ghdl
def test_value_without_an_sv_form_is_noted_and_the_rest_still_pass(tree):
    assert "w.u_w (sv_leaf)" in tree.stdout
    assert "sv_time: can't pass parameter T = 5000000 fs" in " ".join(tree.stderr.split())


@needs_ghdl
def test_required_parameter_that_cannot_be_passed_is_still_noted(tree):
    assert "sv_needs_time: its parameters" in " ".join(tree.stderr.split())
    assert "u_never" not in tree.stdout


@needs_ghdl
def test_generic_the_sv_module_lacks_is_noted(tree):
    assert "sv_extra: has no parameter EXTRA" in " ".join(tree.stderr.split())


@needs_ghdl
def test_values_follow_generics_set_from_sv():
    proc = _svhier(".", "--top", "sv_wrap", "--tree")
    assert proc.returncode == 0, proc.stderr
    assert "g[1].u_leaf (sv_leaf)" in proc.stdout  # .N(2) in SV, then COUNT => N in VHDL
    assert "g[2].u_leaf" not in proc.stdout


@needs_ghdl
def test_filelist_has_files_only_a_passed_value_reaches():
    proc = _svhier(".", "--top", "gen_top", "--filelist", "-", "--no-tree")
    files = [ln for ln in proc.stdout.splitlines() if ln and not ln.startswith(("//", "+"))]
    assert {"sv_count.sv", "sv_leaf.sv", "gen_top.vhd"} <= set(files)


@needs_ghdl
def test_without_top_a_module_vhdl_sets_is_not_also_shown_with_its_default():
    proc = _svhier(".", "--tree")
    assert "on.u_on (sv_leaf)" in proc.stdout
    assert "off.u_off" not in proc.stdout


_AWKWARD_DECLARATIONS = """\
library ieee; use ieee.std_logic_1164.all;
entity real_one is generic (W : integer := 1); port (y : in std_logic := '0'); end entity;
architecture a of real_one is begin end architecture;

library ieee; use ieee.std_logic_1164.all;
entity awk_top is generic (N : integer := 2); end entity;
architecture a of awk_top is
  type rec_t is record a : integer; end record;
  type ptr_t is access integer;
  type int_file_t is file of integer;
  file f_out : int_file_t;
  signal s : std_logic bus;
  disconnect s : std_logic after 1 ns;
  alias s_alias : std_logic is s;
  attribute keep : string;
  attribute keep of s : signal is "true";
  group g_t is (signal <>);
  function f (x : integer) return integer is begin return x; end function;
  component shown is generic (W : integer); port (x : in std_logic); end component;
  component sv_after is generic (COUNT : integer); end component;
  for u_s : shown use entity work.real_one generic map (W => W) port map (y => x);
begin
  u_s : shown generic map (W => 2) port map (x => s);
  blk : block
    for u_b : shown use entity work.real_one generic map (W => W) port map (y => x);
  begin
    u_b : shown generic map (W => 3) port map (x => s);
  end block;
  gen : for i in 0 to 0 generate
    for u_g : shown use entity work.real_one generic map (W => W) port map (y => x);
  begin
    u_g : shown generic map (W => 4) port map (x => s);
  end generate;
  p : process
    variable v : integer := f(1);
  begin
    wait;
  end process;
  u_after : sv_after generic map (COUNT => N);
end architecture;
"""


@needs_ghdl
def test_declarations_ghdl_cannot_print_do_not_stop_generics_being_read(tmp_path):
    (tmp_path / "awk.vhd").write_text(_AWKWARD_DECLARATIONS)
    (tmp_path / "sv_after.sv").write_text(
        "module sv_after #(parameter int COUNT) (); for (genvar i = 0; i < COUNT; i++) begin : g leaf u (); end endmodule\nmodule leaf; endmodule\n")
    proc = subprocess.run([sys.executable, "-c", "from svhier.cli import main; main()", ".", "--top", "awk_top", "--tree", "-E"],
                          capture_output=True, text=True, cwd=tmp_path, check=False)
    assert proc.returncode == 0, proc.stderr
    for line in ("u_s (real_one)", "blk.u_b (real_one)", "gen[0].u_g (real_one)", "u_after (sv_after)", "g[1].u (leaf)"):
        assert line in proc.stdout
    assert "couldn't read" not in " ".join(proc.stderr.split())


@needs_ghdl
def test_vhdl_2008_generic_types_and_subprograms_do_not_break_the_tree(tmp_path):
    (tmp_path / "g.vhd").write_text("""\
entity gen_ent is
  generic (type t; function f (x : t) return t; W : integer := 1);
end entity;
architecture a of gen_ent is begin end architecture;

entity g_top is end entity;
architecture a of g_top is
  function same (x : integer) return integer is begin return x; end function;
  component sv_after is generic (COUNT : integer); end component;
begin
  u_gen : entity work.gen_ent generic map (t => integer, f => same, W => 2);
  u_after : sv_after generic map (COUNT => 1);
end architecture;
""")
    (tmp_path / "sv_after.sv").write_text("module sv_after #(parameter int COUNT) (); endmodule\n")
    proc = subprocess.run([sys.executable, "-c", "from svhier.cli import main; main()", ".", "--top", "g_top", "--tree", "-E"],
                          capture_output=True, text=True, cwd=tmp_path, check=False)
    assert proc.returncode == 0, proc.stderr
    assert "u_gen (gen_ent)" in proc.stdout
    assert "u_after (sv_after)" in proc.stdout
    assert "g_top: couldn't read generics" in " ".join(proc.stderr.split())


def test_a_crash_while_printing_objects_keeps_the_tree_and_is_noted(monkeypatch):
    frontend = GhdlFrontend()
    calls = []

    def fake_worker(request):
        calls.append(request["objects"])
        if request["objects"]:
            return subprocess.CompletedProcess([], 1, "", "raised TYPES.INTERNAL_ERROR : vhdl-errors.adb:30\n")
        return subprocess.CompletedProcess([], 0, "architecture a of top\n  u_sv: component sv_m [not bound]\n", "")

    monkeypatch.setattr(frontend, "_worker", fake_worker)
    result = frontend.elaborate_one(["t.vhd"], "top", {})
    assert calls == [True, False]
    assert not result.errors
    assert result.defs[0].insts == [{"mod_name": "sv_m", "inst_name": "u_sv"}]
    assert result.notes == ["top: couldn't read generics, so SV modules below it use their defaults"]


def test_a_design_error_is_not_retried_without_objects(monkeypatch):
    frontend = GhdlFrontend()
    calls = []

    def fake_worker(request):
        calls.append(request["objects"])
        return subprocess.CompletedProcess([], 3, "", "t.vhd:1:1:error: nope\n")

    monkeypatch.setattr(frontend, "_worker", fake_worker)
    assert frontend.elaborate_one(["t.vhd"], "top", {}).errors == ["ghdl: top: t.vhd:1:1:error: nope"]
    assert calls == [True]
