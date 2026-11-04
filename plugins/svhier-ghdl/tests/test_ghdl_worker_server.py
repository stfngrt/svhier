"""One worker process serves many elaborations, and answers exactly as a process per request does."""

import importlib.util
import json
import os
import signal
import subprocess
import time
from argparse import Namespace
from pathlib import Path

import pytest
from svhier.frontends import Request

import svhier_ghdl
from svhier_ghdl import GhdlFrontend, GhdlWorkerTool

MIXED = Path(__file__).parent / "mixed"
needs_ghdl = pytest.mark.skipif(importlib.util.find_spec("pyGHDL") is None, reason="pyGHDL is not installed")

_DESIGN = """\
entity leaf is end entity;
architecture a of leaf is begin end architecture;

entity sized is generic (N : integer := 1); end entity;
architecture a of sized is
  component other is end component;
begin
  g : for i in 1 to N generate
    u : entity work.leaf;
  end generate;
  big : if N > 2 generate
    u_o : other;
  end generate;
end architecture;

entity needs_generic is generic (N : integer); end entity;
architecture a of needs_generic is
  signal s : bit_vector(N - 1 downto 0);
begin
end architecture;

entity generic_types is generic (type t; W : integer := 1); end entity;
architecture a of generic_types is begin end architecture;

entity crashes is end entity;
architecture a of crashes is
begin
  u : entity work.generic_types generic map (t => integer, W => 2);
end architecture;
"""
_BROKEN = "entity broken is\n  this is not vhdl;\nend entity;\n"
_HANGS = """\
entity hangs is end entity;
architecture a of hangs is
  function forever return integer is begin while true loop null; end loop; return 0; end function;
  constant C : integer := forever;
begin
end architecture;
"""


def _request(files: list[Path], top: str, generics: dict | None = None, objects: bool = True) -> dict:
    return {"std": "08", "work": "work", "op": "elaborate", "files": [str(f) for f in files], "top": top,
            "generics": generics or {}, "top_library": "work", "libraries": {}, "objects": objects}


def _answer(proc: subprocess.CompletedProcess) -> tuple:
    return proc.returncode, proc.stdout, proc.stderr


def _group_ends(group: int) -> bool:
    """Wait for a killed process group to vanish.  A killed child must first be reaped by init."""
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.killpg(group, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.01)
    return False


@pytest.fixture(name="design")
def _design(tmp_path: Path) -> Path:
    path = tmp_path / "design.vhd"
    path.write_text(_DESIGN)
    return path


@pytest.fixture(name="tool")
def _tool():
    tool = GhdlWorkerTool()
    yield tool
    tool.close()


@needs_ghdl
def test_a_sequence_of_requests_gets_the_answers_of_separate_processes(design, tool):
    """Overrides, errors and crashes in one request must not reach the next, in any order."""
    requests = [
        _request([design], "sized"),
        _request([design], "sized", {"N": "3"}),
        _request([design], "sized"),
        _request([design], "no_such_entity"),
        _request([design], "leaf"),
        _request([design], "needs_generic"),
        _request([design], "needs_generic", {"N": "4"}),
        _request([design], "crashes"),
        _request([design], "crashes", objects=False),
        _request([design], "sized", {"N": "2"}),
    ]
    for request in [*requests, *reversed(requests)]:
        assert _answer(tool.run(request)) == _answer(tool.run_once(request)), request["top"]
    # The comparison only means something if the requests really do these different things.
    assert "for-generate (1 to 3)" in tool.run(requests[1]).stdout
    assert "for-generate (1 to 1)" in tool.run(requests[0]).stdout
    assert tool.run(requests[3]).returncode != 0
    assert tool.run(requests[5]).returncode != 0
    assert tool.run(requests[6]).returncode == 0
    assert "raised " in tool.run(requests[7]).stderr
    assert tool.run(requests[8]).returncode == 0


@needs_ghdl
def test_syntax_errors_while_loading_are_in_every_answer(design, tmp_path, tool):
    broken = tmp_path / "broken.vhd"
    broken.write_text(_BROKEN)
    for top in ("leaf", "sized", "leaf"):
        request = _request([design, broken], top)
        answer = _answer(tool.run(request))
        assert answer == _answer(tool.run_once(request))
        assert ":error:" in answer[2]


@needs_ghdl
def test_real_sources_with_libraries_match(tool):
    files = sorted(MIXED.glob("*.vhd"))
    for top, generics in (("top_vhd", {}), ("top_vhd", {"N": "3"}), ("vhdl_bottom", {}), ("leaf_vhd", {})):
        request = _request(files, top, generics)
        assert _answer(tool.run(request)) == _answer(tool.run_once(request))


@needs_ghdl
def test_output_is_decoded_as_subprocess_text_mode_does(tmp_path, tool):
    path = tmp_path / "crlf.vhd"
    path.write_bytes(b"entity crlf is\r\nend entity;\r\narchitecture a of crlf is\r\nbegin\r\n  this is wrong;\r\nend architecture;\r\n")
    request = _request([path], "crlf")
    assert _answer(tool.run(request)) == _answer(tool.run_once(request))


@needs_ghdl
def test_failing_while_loading_falls_back_to_one_process_per_request(design, tmp_path, tool, monkeypatch):
    request = _request([design, tmp_path / "missing.vhd"], "leaf")
    want = _answer(tool.run_once(request))
    assert want[0] != 0
    popen = _count_worker_processes(monkeypatch)
    assert _answer(tool.run(request)) == want
    assert _answer(tool.run(request)) == want
    assert len(popen) == 3  # One failed server, then two requests on their own.


@needs_ghdl
def test_a_dead_server_falls_back_without_changing_the_answer(design, tool):
    request = _request([design], "sized", {"N": "3"})
    want = _answer(tool.run(request))
    os.kill(tool.server_pid(), signal.SIGKILL)
    assert _answer(tool.run(request)) == want


@needs_ghdl
def test_a_timeout_kills_the_elaboration_and_the_next_request_still_works(design, tmp_path, tool, monkeypatch):
    hangs = tmp_path / "hangs.vhd"
    hangs.write_text(_HANGS)
    files = [design, hangs]
    assert tool.run(_request(files, "leaf")).returncode == 0
    group = tool.server_pid()
    monkeypatch.setattr(svhier_ghdl, "_TIMEOUT_S", 1)
    with pytest.raises(subprocess.TimeoutExpired):
        tool.run(_request(files, "hangs"))
    assert _group_ends(group)  # Nothing is left of the server or its child.
    monkeypatch.setattr(svhier_ghdl, "_TIMEOUT_S", 120)
    request = _request(files, "sized")
    assert _answer(tool.run(request)) == _answer(tool.run_once(request))


@needs_ghdl
def test_a_timeout_while_loading_leaves_no_server_behind(design, tool, monkeypatch):
    started = _count_worker_processes(monkeypatch)
    monkeypatch.setattr(svhier_ghdl, "_TIMEOUT_S", 0)
    with pytest.raises(subprocess.TimeoutExpired):
        tool.run(_request([design], "leaf"))
    assert len(started) == 1
    assert started[0].poll() is not None
    assert tool.server_pid() is None


@needs_ghdl
def test_changed_sources_get_a_new_server_and_the_old_one_stops(design, tmp_path, tool):
    other = tmp_path / "other.vhd"
    other.write_text("entity other is end entity;\narchitecture a of other is begin end architecture;\n")
    assert "other" not in tool.run(_request([design], "sized", {"N": "3"})).stdout.replace("component other", "")
    first = tool.server_pid()
    assert "architecture a of other" in tool.run(_request([design, other], "sized", {"N": "3"})).stdout
    assert tool.server_pid() != first
    assert _group_ends(first)


@needs_ghdl
def test_closing_stops_the_server(design, tool):
    tool.run(_request([design], "leaf"))
    group = tool.server_pid()
    tool.close()
    assert _group_ends(group)


def _count_worker_processes(monkeypatch) -> list[subprocess.Popen]:
    started: list[subprocess.Popen] = []
    real = subprocess.Popen

    class Counting(real):  # type: ignore[valid-type, misc]
        def __init__(self, args, *rest, **kwargs):
            super().__init__(args, *rest, **kwargs)
            if "svhier_ghdl.worker" in args:
                started.append(self)

    monkeypatch.setattr(subprocess, "Popen", Counting)
    return started


@needs_ghdl
def test_auto_mode_starts_one_process_however_many_entities_it_elaborates(tmp_path, monkeypatch):
    names = [f"ent{i}" for i in range(6)]
    paths = []
    for name in names:
        paths.append(tmp_path / f"{name}.vhd")
        paths[-1].write_text(f"entity {name} is end entity;\narchitecture a of {name} is begin end architecture;\n")
    started = _count_worker_processes(monkeypatch)
    frontend = GhdlFrontend()
    frontend.configure(Namespace(vhdl_std="08", vhdl_work="work", libdirs=[], ignore=None))
    result = frontend.elaborate([str(p) for p in paths], Request(auto=True))
    frontend.tool.close()
    assert sorted(d["mod_name"] for f in result["files"] for d in f["defs"]) == names
    assert not result["has_errors"]
    assert len(started) == 2  # The scan, then a server for all six elaborations.


class _TreeTool:
    """The worker, with each top's result given as hierarchy text or as an error."""

    def __init__(self, files: dict[str, list[str]], trees: dict[str, str], failing: tuple[str, ...] = ()) -> None:
        self.files, self.trees, self.failing = files, trees, failing
        self.tops: list[str] = []

    def unavailable_reason(self) -> str | None:
        return None

    def run(self, request: dict) -> subprocess.CompletedProcess:
        if request["op"] == "scan":
            scan = {str(Path(f).resolve()): {"units": e, "entities": e, "arch_of": e, "libs": [], "refs": [], "named": []}
                    for f, e in self.files.items()}
            return subprocess.CompletedProcess([], 0, "@@svhier-order " + json.dumps(scan), "")
        self.tops.append(request["top"])
        if request["top"] in self.failing:
            return subprocess.CompletedProcess([], 3, "", f"x.vhd:1:1:error: {request['top']} needs a generic\n")
        return subprocess.CompletedProcess([], 0, self.trees[request["top"]], "")


def _auto(tool: _TreeTool) -> dict:
    frontend = GhdlFrontend(tool=tool)  # type: ignore[arg-type]
    frontend.configure(Namespace(vhdl_std="08", vhdl_work="work", libdirs=[], ignore=None))
    return frontend.elaborate(list(tool.files), Request(auto=True))


_PARENT_TREE = """\
architecture a of parent
  u_early: entity early(a)
    architecture a of early
  u_late: entity late(a)
    architecture a of late
"""


def test_auto_elaborates_in_file_order_and_skips_only_what_is_already_reached():
    """An entity alone may differ from under its parent, so running parents first would change results."""
    tool = _TreeTool({"a.vhd": ["early"], "b.vhd": ["parent"], "c.vhd": ["late"]}, {
        "early": "architecture a of early\n  u_default: component only_with_defaults [not bound]\n",
        "parent": _PARENT_TREE,
    })
    result = _auto(tool)
    assert tool.tops == ["early", "parent"]
    early = next(d for f in result["files"] for d in f["defs"] if d["mod_name"] == "early")
    assert early["insts"] == [{"mod_name": "only_with_defaults", "inst_name": "u_default"}]


def test_auto_reports_a_failure_only_for_entities_nothing_reaches():
    tool = _TreeTool({"a.vhd": ["early"], "b.vhd": ["lonely"], "c.vhd": ["parent"], "d.vhd": ["late"]},
                     {"parent": _PARENT_TREE}, failing=("early", "lonely"))
    result = _auto(tool)
    assert tool.tops == ["early", "lonely", "parent"]
    assert [d["message"] for d in result["diagnostics"]] == ["ghdl: lonely: x.vhd:1:1:error: lonely needs a generic"]
