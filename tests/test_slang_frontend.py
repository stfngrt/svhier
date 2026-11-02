"""The slang frontend elaborating roots with parameter values from another language."""

from argparse import Namespace
from pathlib import Path

import pytest

from svhier.frontends import Request, Root
from svhier.libsearch import LibraryIndex, LibrarySearch
from svhier.parser import parse_files
from svhier.slang_frontend import SlangFrontend

_SOURCE = """\
module m #(parameter int COUNT) ();
  for (genvar i = 0; i < COUNT; i++) begin : g leaf u_leaf (); end
endmodule
module gated #(parameter int EN = 0) ();
  if (EN) begin : on leaf u_on (); end else begin : off leaf u_off (); end
endmodule
module body_param;
  parameter int INNER = 0;
  localparam int HIDDEN = 1;
  if (INNER == 5) begin : b leaf u_inner (); end
endmodule
module kinds #(parameter logic [3:0] V = 0, parameter string S = "", parameter real R = 0.0,
               parameter bit B = 0, parameter int NEG = 0) ();
  if (V == 4'b1010) begin : a leaf u_v (); end
  if (S == "a \\"b\\" c") begin : c leaf u_s (); end
  if (R > 2.0) begin : d leaf u_r (); end
  if (B) begin : e leaf u_b (); end
  if (NEG == -4) begin : f leaf u_neg (); end
endmodule
module plain; leaf u_plain (); endmodule
module leaf; endmodule
"""


@pytest.fixture(name="files")
def _files(tmp_path: Path) -> list[str]:
    path = tmp_path / "d.sv"
    path.write_text(_SOURCE)
    return [str(path)]


def _elaborate(files: list[str], *roots: Root, auto: bool = False) -> dict:
    return SlangFrontend().elaborate(list(files), Request(auto=auto, roots=list(roots)))


def _insts(result: dict, name: str) -> list[str]:
    return [i["inst_name"] for f in result["files"] for d in f["defs"] if d["mod_name"] == name for i in d["insts"]]


def test_value_drives_generate_and_satisfies_required_parameter(files):
    result = _elaborate(files, Root("m", (("COUNT", "2"),)))
    assert _insts(result, "m") == ["g[0].u_leaf", "g[1].u_leaf"]
    assert result["rejected_roots"] == []


def test_two_settings_of_one_module_are_both_listed(files):
    result = _elaborate(files, Root("gated", (("EN", "0"),)), Root("gated", (("EN", "1"),)))
    assert sorted(_insts(result, "gated")) == ["off.u_off", "on.u_on"]


def test_value_replaces_the_default(files):
    assert _insts(_elaborate(files, Root("gated", (("EN", "1"),))), "gated") == ["on.u_on"]


def test_names_match_regardless_of_case(files):
    assert _insts(_elaborate(files, Root("m", (("count", "1"),))), "m") == ["g[0].u_leaf"]


def test_parameter_declared_in_the_body_can_be_set(files):
    assert _insts(_elaborate(files, Root("body_param", (("inner", "5"),))), "body_param") == ["b.u_inner"]


def test_unknown_and_local_parameters_are_noted(files):
    result = _elaborate(files, Root("body_param", (("NOPE", "1"), ("HIDDEN", "2"))))
    assert "body_param: has no parameter NOPE" in result["notes"]
    assert "body_param: has no parameter HIDDEN" in result["notes"]


def test_required_parameter_still_missing_is_rejected(files):
    result = _elaborate(files, Root("m", (("OTHER", "1"),)))
    assert result["rejected_roots"] == ["m"]
    assert not result["has_errors"]


def test_a_rejected_setting_does_not_reject_a_root_that_works_with_another(files):
    result = _elaborate(files, Root("m", (("OTHER", "1"),)), Root("m", (("COUNT", "1"),)))
    assert result["rejected_roots"] == []
    assert _insts(result, "m") == ["g[0].u_leaf"]


def test_every_literal_form_is_accepted(files):
    generics = (("V", "4'b1010"), ("S", '"a \\"b\\" c"'), ("R", "2.50000000000000E+00"), ("B", "1'b1"), ("NEG", "-4"))
    result = _elaborate(files, Root("kinds", generics))
    assert sorted(_insts(result, "kinds")) == ["a.u_v", "c.u_s", "d.u_r", "e.u_b", "f.u_neg"]
    assert not result["has_errors"]


def test_roots_with_and_without_values_combine(files):
    result = _elaborate(files, Root("plain"), Root("m", (("COUNT", "1"),)))
    assert _insts(result, "plain") == ["u_plain"]
    assert _insts(result, "m") == ["g[0].u_leaf"]
    assert result["needed"] == files


def test_without_top_a_root_with_values_is_not_also_elaborated_with_its_default(files):
    result = _elaborate(files, Root("gated", (("EN", "1"),)), auto=True)
    assert _insts(result, "gated") == ["on.u_on"]
    assert _insts(result, "plain") == ["u_plain"]
    assert result["needed"] is None


def test_without_top_and_no_other_top_left_only_the_root_is_elaborated(tmp_path):
    path = tmp_path / "one.sv"
    path.write_text("module one #(parameter int EN = 0) (); if (EN) begin : on two u_on (); end else begin : off two u_off (); end endmodule\nmodule two; endmodule\n")
    result = _elaborate([str(path)], Root("one", (("EN", "1"),)), auto=True)
    assert _insts(result, "one") == ["on.u_on"]


def test_parse_files_takes_parameter_values(files):
    result = parse_files(files, tops=["m"], params={"COUNT": "3"})
    entry = next(d for f in result["files"] for d in f["defs"] if d["mod_name"] == "m")
    assert [i["inst_name"] for i in entry["insts"]] == ["g[0].u_leaf", "g[1].u_leaf", "g[2].u_leaf"]


def test_parse_files_keeps_its_result_shape(files):
    assert "parameter_names" not in parse_files(files)


def test_when_every_root_is_rejected_other_modules_are_not_elaborated_with_defaults(files):
    result = _elaborate(files, Root("m"))
    assert result["rejected_roots"] == ["m"]
    assert _insts(result, "gated") == []
    assert result["needed"] == files


def test_module_name_is_found_when_vhdl_lowercased_it(tmp_path):
    path = tmp_path / "mixed.sv"
    path.write_text("module Mixed_Case #(parameter int Count) (); for (genvar i = 0; i < Count; i++) begin : g leaf u (); end endmodule\nmodule leaf; endmodule\n")
    result = _elaborate([str(path)], Root("mixed_case", (("count", "1"),)))
    assert _insts(result, "Mixed_Case") == ["g[0].u"]
    assert result["rejected_roots"] == []


def test_provide_finds_a_library_module_in_any_case_and_reports_it(tmp_path):
    (tmp_path / "Lib_Cell.sv").write_text("module Lib_Cell; endmodule\n")
    frontend = SlangFrontend()
    frontend.search = LibrarySearch(LibraryIndex([str(tmp_path)]))
    files: list[str] = []
    assert frontend.provide("lib_cell", files)
    assert frontend.units(files) == {"lib_cell": str(tmp_path / "Lib_Cell.sv")}
    assert [name for name, _, _ in frontend.search.pulled] == ["lib_cell"]
    assert not frontend.provide("nothing", files)


def test_units_do_not_search_the_library(tmp_path):
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "cell.sv").write_text("module cell; endmodule\n")
    source = tmp_path / "unused.sv"
    source.write_text("module unused; cell u (); endmodule\n")
    frontend = SlangFrontend()
    frontend.search = LibrarySearch(LibraryIndex([str(tmp_path / "lib")]))
    files = [str(source)]
    assert list(frontend.units(files)) == ["unused"]
    assert files == [str(source)] and not frontend.search.pulled


def test_a_module_another_language_defines_is_not_pulled_from_the_library(tmp_path):
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "shared.sv").write_text("module shared; endmodule\n")
    source = tmp_path / "top.sv"
    source.write_text("module top; shared u (); endmodule\n")
    frontend = SlangFrontend()
    frontend.search = LibrarySearch(LibraryIndex([str(tmp_path / "lib")]))
    frontend.elaborate([str(source)], Request(roots=[Root("top", requested=True)], defined_elsewhere={"shared"}))
    assert not frontend.search.pulled


class _FakeTool:
    """Stands in for pyslang, so the frontend's logic can run without it."""

    def __init__(self) -> None:
        self.parsed: list[list[str]] = []

    def unavailable_reason(self) -> str | None:
        return "no slang here"

    def parse(self, files, inc_dirs, **options):  # pylint: disable=unused-argument
        self.parsed.append(list(files))
        needs_lib = "lib.sv" not in " ".join(files)
        return {
            "files": [{"file_name": f, "defs": [{"mod_name": "Mod"}]} for f in files],
            "parameter_names": {"Mod": ["P"]},
            "missing": {"modules": {"lib": ["a.sv"]} if needs_lib else {}, "interfaces": {}, "packages": {}, "includes": {}, "macros": {}},
        }


def test_tool_and_library_are_injected(tmp_path):
    (tmp_path / "lib.sv").write_text("module lib; endmodule\n")
    tool = _FakeTool()
    made = []

    def factory(dirs, ignore):
        made.append((dirs, ignore))
        return LibraryIndex(dirs, ignore)

    frontend = SlangFrontend(tool=tool, library_factory=factory)
    frontend.configure(Namespace(paths=[], recursive=False, ignore=["x"], defines=[], include_dirs=[], command_files=[], libdirs=[str(tmp_path)]))
    assert frontend.unavailable_reason() == "no slang here"
    assert made == [([str(tmp_path)], {"x"})]
    files = ["a.sv"]
    assert frontend.units(files) == {"mod": "a.sv"}
    assert tool.parsed == [["a.sv"]]  # units() doesn't search -y.
    frontend._parse(files, ["Mod"])  # pylint: disable=protected-access
    assert tool.parsed[-1] == ["a.sv", str(tmp_path / "lib.sv")]
