"""The instance tree: per-parameter children, the merged fallback, and the plain printer."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from svhier.cli import iter_tree, main, render_tree_line, tree_style
from svhier.frontends import stitch
from svhier.parser import parse_files
from svhier.slang_frontend import _merge_files, _nothing_elaborated
from tests.test_frontends import FakeFrontend

VERILOG_DIR = Path(__file__).parent.parent / "verilog"
PARAM_WALK = VERILOG_DIR / "param_walk" / "param_walk.sv"
SVHIER = [sys.executable, "-c", "from svhier.cli import main; main()"]


def _tree(argv: list[str], monkeypatch, capsys) -> str:
    monkeypatch.setattr("sys.argv", ["svhier", *argv])
    main()
    return capsys.readouterr().out


def _lines(hierarchy: dict, tops: list[str] | None = None) -> list[str]:
    """The tree as plain text, one line per node."""
    return [render_tree_line(line) for line in iter_tree(hierarchy, tops)]


def _hierarchy(table: dict) -> dict:
    """A plugin-style result, ``{name: [(inst, child)]}``, with only the merged ``insts``."""
    defs = [{"mod_name": n, "pkg_imports": [], "insts": [{"mod_name": c, "inst_name": i} for i, c in kids]} for n, kids in table.items()]
    return {"files": [{"file_name": "x.fake", "defs": defs}]}


# ---------------------------------------------------------------------------
# Each instance shows its own children
# ---------------------------------------------------------------------------

def test_each_instance_shows_only_its_own_generate_children(monkeypatch, capsys):
    assert _tree([str(PARAM_WALK)], monkeypatch, capsys) == (
        "pw_top\n"
        "├── u_m1 (pw_mid)\n"
        "│   └── g_a.u_a (pw_leaf_a)\n"
        "├── u_m2 (pw_mid)\n"
        "│   └── g_b.u_b (pw_leaf_b)\n"
        "│       └── u_deep (pw_deep)\n"
        "└── u_m3 (pw_mid)\n"
        "    └── g_a.u_a (pw_leaf_a)\n"
    )


def test_settings_stay_apart_through_several_levels(tmp_path, monkeypatch, capsys):
    src = tmp_path / "nest.sv"
    src.write_text(
        "module leaf_a; endmodule\nmodule leaf_b; endmodule\n"
        "module inner #(parameter int N = 0);\n"
        "  if (N == 1) begin : g leaf_a u(); end else begin : g leaf_b u(); end\n"
        "endmodule\n"
        "module outer #(parameter int N = 0); inner #(.N(N)) u_i(); endmodule\n"
        "module top; outer #(.N(1)) u1(); outer #(.N(2)) u2(); endmodule\n",
        encoding="utf-8",
    )
    assert _tree([str(src)], monkeypatch, capsys) == (
        "top\n"
        "├── u1 (outer)\n"
        "│   └── u_i (inner)\n"
        "│       └── g.u (leaf_a)\n"
        "└── u2 (outer)\n"
        "    └── u_i (inner)\n"
        "        └── g.u (leaf_b)\n"
    )


def test_top_shows_its_own_setting_when_also_instantiated_with_another(tmp_path, monkeypatch, capsys):
    """``sub`` is both a top, with its default, and a child with ``N=1``."""
    src = tmp_path / "both.sv"
    src.write_text(
        "module leaf_a; endmodule\nmodule leaf_b; endmodule\n"
        "module sub #(parameter int N = 0);\n"
        "  if (N == 1) begin : g leaf_a u(); end else begin : g leaf_b u(); end\n"
        "endmodule\n"
        "module top; sub #(.N(1)) u_sub(); endmodule\n",
        encoding="utf-8",
    )
    assert _tree([str(src), "--top", "top", "--top", "sub"], monkeypatch, capsys) == (
        "top\n"
        "└── u_sub (sub)\n"
        "    └── g.u (leaf_a)\n"
        "sub\n"
        "└── g.u (leaf_b)\n"
    )


def test_recursion_that_parameters_end_is_not_a_cycle(tmp_path, monkeypatch, capsys):
    src = tmp_path / "rec.sv"
    src.write_text(
        "module rec #(parameter int N = 2);\n"
        "  if (N > 0) begin : g rec #(.N(N - 1)) u(); end\n"
        "endmodule\n"
        "module top; rec u_rec(); endmodule\n",
        encoding="utf-8",
    )
    out = _tree([str(src)], monkeypatch, capsys)
    assert out == (
        "top\n"
        "└── u_rec (rec)\n"
        "    └── g.u (rec)\n"
        "        └── g.u (rec)\n"
    )


def test_tree_counts_every_instance_of_a_repeated_module(tmp_path, monkeypatch, capsys):
    """The walk visits one setting once, but the tree still shows every instance of it."""
    src = tmp_path / "many.sv"
    src.write_text(
        "module leaf; endmodule\n"
        "module mid; leaf u_a(); leaf u_b(); endmodule\n"
        "module top; mid u_m[2:0](); for (genvar i = 0; i < 2; i++) begin : g mid u(); end endmodule\n",
        encoding="utf-8",
    )
    out = _tree([str(src)], monkeypatch, capsys)
    assert out.count("(mid)") == 5
    assert out.count("(leaf)") == 10


# ---------------------------------------------------------------------------
# The result: insts stays merged, the rest is internal
# ---------------------------------------------------------------------------

def test_parse_files_records_children_per_setting_and_keeps_insts_merged():
    result = parse_files([str(PARAM_WALK)], frontend_info=True)
    defs = {d["mod_name"]: d for f in result["files"] for d in f["defs"]}
    assert defs["pw_mid"]["insts"] == [
        {"mod_name": "pw_leaf_a", "inst_name": "g_a.u_a"},
        {"mod_name": "pw_leaf_b", "inst_name": "g_b.u_b"},
    ]
    assert len(defs["pw_mid"]["variants"]) == 2
    children = [[i["inst_name"] for i in kids] for kids in defs["pw_mid"]["variants"].values()]
    assert children == [["g_a.u_a"], ["g_b.u_b"]]
    assert "top_variant" in defs["pw_top"] and "top_variant" not in defs["pw_mid"]


def test_parse_files_leaves_out_the_per_setting_data_unless_asked():
    assert all(set(d) == {"mod_name", "pkg_imports", "insts"} for f in parse_files([str(PARAM_WALK)])["files"] for d in f["defs"])


def test_yaml_is_the_merged_insts_without_the_per_setting_data(tmp_path, monkeypatch, capsys):
    dest = tmp_path / "out.yaml"
    _tree([str(PARAM_WALK), "--yaml", str(dest), "--no-tree"], monkeypatch, capsys)
    text = dest.read_text(encoding="utf-8")
    assert "variant" not in text
    data = YAML(typ="safe").load(text)
    defs = {d["mod_name"]: d for f in data["files"] for d in f["defs"]}
    assert set(defs["pw_mid"]) == {"mod_name", "insts"}
    assert [i["inst_name"] for i in defs["pw_mid"]["insts"]] == ["g_a.u_a", "g_b.u_b"]


def test_merging_compilations_joins_the_settings():
    def unit(sig, *, top=False):
        out = {"mod_name": "m", "pkg_imports": [], "insts": [], "variants": {sig: [{"mod_name": "c", "inst_name": str(sig), "variant": None}]}}
        return out | ({"top_variant": sig} if top else {})

    base = [{"file_name": "a.sv", "defs": [unit(("N=1",))]}]
    _merge_files(base, [{"file_name": "a.sv", "defs": [unit(("N=2",), top=True)]}])
    _merge_files(base, [{"file_name": "a.sv", "defs": [unit(("N=3",), top=True)]}])
    merged = base[0]["defs"][0]
    assert list(merged["variants"]) == [("N=1",), ("N=2",), ("N=3",)]
    assert merged["top_variant"] == ("N=2",)  # the first compilation that has it as a top wins


def test_merging_results_without_per_setting_data_still_works():
    base = [{"file_name": "a.sv", "defs": [{"mod_name": "m", "pkg_imports": [], "insts": []}]}]
    _merge_files(base, [{"file_name": "a.sv", "defs": [{"mod_name": "m", "pkg_imports": [], "insts": [{"mod_name": "c", "inst_name": "u"}]}]}])
    assert base[0]["defs"][0]["insts"] == [{"mod_name": "c", "inst_name": "u"}]
    assert "variants" not in base[0]["defs"][0]


def test_nothing_elaborated_forgets_the_settings():
    result = parse_files([str(PARAM_WALK)], frontend_info=True)
    _nothing_elaborated(result)
    assert all(not d["insts"] and not d.get("variants") and "top_variant" not in d for f in result["files"] for d in f["defs"])


# ---------------------------------------------------------------------------
# Fallback to the merged insts
# ---------------------------------------------------------------------------

def test_plugin_results_use_the_merged_insts_with_markers():
    hierarchy = _hierarchy({
        "top": [("u_a", "a"), ("u_ghost", "ghost")],
        "a": [("u_b", "b")],
        "b": [("u_a", "a")],
    })
    assert _lines(hierarchy) == [
        "top",
        "├── u_a (a)",
        "│   └── u_b (b)",
        "│       └── u_a (a) (cycle)",
        "└── u_ghost (ghost) (blackbox)",
    ]


def test_stitched_plugin_with_no_per_setting_data():
    frontend = FakeFrontend("fake", {"top": ("t.fake", [("u_m", "mid"), ("u_x", "nowhere")]), "mid": ("m.fake", [("u_l", "leaf")]), "leaf": ("m.fake", [])})
    result = stitch([(frontend, ["t.fake", "m.fake"])], ["top"])
    assert _lines(result, ["top"]) == [
        "top",
        "├── u_m (mid)",
        "│   └── u_l (leaf)",
        "└── u_x (nowhere) (blackbox)",
    ]


class _SvChild(FakeFrontend):
    """Instantiates the SystemVerilog ``sv`` from ``vtop``, passing the values in *settings*."""

    def __init__(self, table: dict, settings: list[dict]):
        super().__init__("fake", table)
        self.settings = settings

    def elaborate(self, files, request):
        result = super().elaborate(files, request)
        result["unresolved"] = {"sv": self.settings}
        return result

    def drop_resolved(self, fragment, names):
        """Nothing to drop."""


SV_WITH_SETTINGS = (
    "module sv_a; endmodule\nmodule sv_b; endmodule\n"
    "module sv #(parameter int N = 0);\n"
    "  if (N == 1) begin : g sv_a u(); end else begin : g sv_b u(); end\n"
    "endmodule\n"
)


def _slang_and_plugin(tmp_path, settings: list[dict], sv_extra: str = "") -> tuple:
    from svhier.slang_frontend import SlangFrontend  # pylint: disable=import-outside-toplevel

    src = tmp_path / "sv.sv"
    src.write_text(SV_WITH_SETTINGS + sv_extra, encoding="utf-8")
    plugin = _SvChild({"vtop": ("v.fake", [("u_sv", "sv")])}, settings)
    return (plugin, ["v.fake"]), (SlangFrontend(), [str(src)])


def test_plugin_instance_of_a_module_with_several_settings_falls_back_to_merged(tmp_path):
    """The plugin doesn't say which setting ``u_sv`` uses, so it lists what they elaborate."""
    uses = [{"generics": {"N": "1"}, "unresolved": []}, {"generics": {"N": "2"}, "unresolved": []}]
    result = stitch(list(_slang_and_plugin(tmp_path, uses)), ["vtop"])
    assert _lines(result, result["tops"]) == [
        "vtop",
        "└── u_sv (sv)",
        "    ├── g.u (sv_a)",
        "    └── g.u (sv_b)",
    ]


def test_plugin_instance_of_a_module_with_one_setting_is_exact(tmp_path):
    """Only one setting exists, so there is nothing to guess."""
    uses = [{"generics": {"N": "1"}, "unresolved": []}]
    result = stitch(list(_slang_and_plugin(tmp_path, uses)), ["vtop"])
    assert _lines(result, result["tops"]) == [
        "vtop",
        "└── u_sv (sv)",
        "    └── g.u (sv_a)",
    ]


def test_module_a_plugin_defines_keeps_its_spelling_and_stays_exact_around_it(tmp_path):
    """An SV instance of a plugin's unit is matched by name, in whatever case the plugin has it."""
    from svhier.slang_frontend import SlangFrontend  # pylint: disable=import-outside-toplevel

    src = tmp_path / "sv.sv"
    src.write_text(SV_WITH_SETTINGS + "module top; sv #(.N(1)) u_1(); sv #(.N(2)) u_2(); VLEAF u_v(); endmodule\n", encoding="utf-8")
    plugin = FakeFrontend("fake", {"vleaf": ("v.fake", [])})
    result = stitch([(plugin, ["v.fake"]), (SlangFrontend(), [str(src)])], ["top"])
    assert _lines(result, result["tops"]) == [
        "top",
        "├── u_1 (sv)",
        "│   └── g.u (sv_a)",
        "├── u_2 (sv)",
        "│   └── g.u (sv_b)",
        "└── u_v (vleaf)",
    ]


# ---------------------------------------------------------------------------
# Walking: no recursion limit, and no building it all up front
# ---------------------------------------------------------------------------

def test_deep_hierarchy_does_not_hit_the_recursion_limit():
    depth = sys.getrecursionlimit() * 3
    table = {f"m{i}": [("u", f"m{i + 1}")] for i in range(depth)}
    table[f"m{depth}"] = []
    lines = _lines(_hierarchy(table), ["m0"])
    assert len(lines) == depth + 1
    assert lines[-1].endswith(f"└── u (m{depth})")


def test_the_tree_is_produced_line_by_line():
    """A huge tree must show progress, so nothing may walk all of it first."""
    table = {"top": [("u_a", "a"), ("u_b", "a")], "a": [("u_x", "b"), ("u_y", "b")], "b": []}
    lines = iter_tree(_hierarchy(table), ["top"])
    assert iter(lines) is lines
    assert render_tree_line(next(lines)) == "top"


def test_unknown_top_is_skipped():
    assert _lines(_hierarchy({"a": []}), ["nosuch", "a"]) == ["a"]


def test_cycle_in_per_setting_data_is_cut_off():
    """Without parameters to end it, recursion still stops at the first repeat."""
    variant = ()
    defs = [{"mod_name": "a", "pkg_imports": [], "insts": [{"mod_name": "a", "inst_name": "u"}],
             "variants": {variant: [{"mod_name": "a", "inst_name": "u", "variant": variant}]}, "top_variant": variant}]
    assert _lines({"files": [{"file_name": "a.sv", "defs": defs}]}, ["a"]) == ["a", "└── u (a) (cycle)"]


def test_blackbox_in_per_setting_data():
    defs = [{"mod_name": "a", "pkg_imports": [], "insts": [{"mod_name": "b", "inst_name": "u"}],
             "variants": {(): [{"mod_name": "b", "inst_name": "u", "variant": None}]}, "top_variant": ()}]
    assert _lines({"files": [{"file_name": "a.sv", "defs": defs}]}, ["a"]) == ["a", "└── u (b) (blackbox)"]


# ---------------------------------------------------------------------------
# Printing
# ---------------------------------------------------------------------------

def _plain_console(monkeypatch):
    for name in ("FORCE_COLOR", "NO_COLOR", "TTY_COMPATIBLE"):
        monkeypatch.delenv(name, raising=False)


def test_no_colour_when_piped(monkeypatch, capsys):
    _plain_console(monkeypatch)
    out = _tree([str(PARAM_WALK)], monkeypatch, capsys)
    assert "\x1b" not in out


def test_colours_in_a_terminal(tmp_path, monkeypatch, capsys):
    _plain_console(monkeypatch)
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.setenv("TERM", "xterm-256color")
    src = tmp_path / "top.sv"
    src.write_text("module rec; rec u(); endmodule\nmodule top; bbox u_bb(); rec u_r(); endmodule\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        _tree([str(src), "--top", "top"], monkeypatch, capsys)
    out = capsys.readouterr().out
    assert "\x1b[1mtop\x1b[0m\n" in out
    assert "u_bb \x1b[2m(bbox)\x1b[0m \x1b[33m(blackbox)\x1b[0m\n" in out
    assert "u \x1b[2m(rec)\x1b[0m \x1b[31m(cycle)\x1b[0m\n" in out


def test_no_color_keeps_bold_and_dim_but_drops_colours(monkeypatch):
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("NO_COLOR", "1")
    style = tree_style()
    lines = list(iter_tree(_hierarchy({"top": [("u", "ghost")]}), ["top"]))
    assert render_tree_line(lines[1], style) == "└── u \x1b[2m(ghost)\x1b[0m (blackbox)"


def test_names_are_not_read_as_markup():
    table = {"top": [(r"\[red]x ", "m"), ("u[0]", "m")], "m": []}
    assert _lines(_hierarchy(table)) == ["top", r"├── \[red]x  (m)", "└── u[0] (m)"]


def test_ascii_guides_when_stdout_cannot_encode_the_lines():
    env = {**os.environ, "PYTHONIOENCODING": "ascii"}
    env.pop("FORCE_COLOR", None)
    proc = subprocess.run([*SVHIER, str(PARAM_WALK)], capture_output=True, env=env, check=True)
    assert proc.stdout.decode("ascii").splitlines()[:3] == ["pw_top", "+-- u_m1 (pw_mid)", "|   `-- g_a.u_a (pw_leaf_a)"]


def test_closed_pipe_ends_quietly(tmp_path):
    """``svhier ... | head`` must not end in a traceback."""
    src = tmp_path / "wide.sv"
    insts = "\n".join(f"  leaf u_{i}();" for i in range(6000))
    src.write_text(f"module leaf; endmodule\nmodule top;\n{insts}\nendmodule\n", encoding="utf-8")
    with subprocess.Popen([*SVHIER, str(src)], stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
        assert proc.stdout is not None and proc.stderr is not None
        assert proc.stdout.readline() == b"top\n"
        proc.stdout.close()
        err = proc.stderr.read().decode()
        code = proc.wait()
    assert "Traceback" not in err and "BrokenPipe" not in err
    assert code == 1


# ---------------------------------------------------------------------------
# Output order
# ---------------------------------------------------------------------------

def test_yaml_and_filelist_are_written_before_the_tree(tmp_path, monkeypatch, capsys):
    yaml_path, filelist = tmp_path / "out.yaml", tmp_path / "out.f"

    def interrupted(*_):
        assert yaml_path.exists() and filelist.exists()
        raise KeyboardInterrupt

    monkeypatch.setattr("svhier.cli._print_tree", interrupted)
    monkeypatch.setattr("sys.argv", ["svhier", str(PARAM_WALK), "--yaml", str(yaml_path), "--filelist", str(filelist)])
    with pytest.raises(KeyboardInterrupt):
        main()
    capsys.readouterr()
    assert yaml_path.read_text(encoding="utf-8").startswith("files:")
    assert str(PARAM_WALK) in filelist.read_text(encoding="utf-8")


def test_tree_on_request_follows_the_data_on_stdout(monkeypatch, capsys):
    out = _tree([str(PARAM_WALK), "--yaml", "--tree"], monkeypatch, capsys)
    assert out.startswith("files:")
    assert out.endswith("    └── g_a.u_a (pw_leaf_a)\n")


@pytest.mark.parametrize("flag", ["--yaml", "--filelist"])
def test_tree_stays_off_when_data_goes_to_stdout(flag, monkeypatch, capsys):
    out = _tree([str(PARAM_WALK), flag], monkeypatch, capsys)
    assert "├──" not in out


def test_tree_stays_on_when_data_goes_to_files(tmp_path, monkeypatch, capsys):
    out = _tree([str(PARAM_WALK), "--yaml", str(tmp_path / "y"), "--filelist", str(tmp_path / "f")], monkeypatch, capsys)
    assert out.startswith("pw_top\n")
