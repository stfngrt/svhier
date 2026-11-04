"""One worker process serves many elaborations, and answers exactly as a process per request does."""

import importlib.util
import json
import os
import select
import signal
import subprocess
import sys
import time
import uuid
from argparse import Namespace
from pathlib import Path

import pytest
from svhier.frontends import Request

import svhier_ghdl
from svhier_ghdl import GhdlFrontend, GhdlWorkerTool

MIXED = Path(__file__).parent / "mixed"
needs_ghdl = pytest.mark.skipif(importlib.util.find_spec("pyGHDL") is None, reason="pyGHDL is not installed")
needs_proc = pytest.mark.skipif(not Path("/proc/self/environ").exists(), reason="needs /proc to find processes")

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


def _group_members(group: int) -> list[int]:
    """Return every process in a group, zombies included, as a signal to the group would reach them."""
    members = []
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit():
            try:
                fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            except OSError:
                continue
            if int(fields[2]) == group:
                members.append(int(entry.name))
    return members


def _marked_processes(marker: str) -> list[str]:
    """Return what is still running with *marker* in its environment, as all worker processes inherit it."""
    found = []
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit():
            try:
                if marker.encode() in (entry / "environ").read_bytes():
                    found.append((entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace"))
            except OSError:
                continue
    return found


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
def test_output_that_is_not_valid_text_fails_as_it_does_in_subprocess_text_mode(tmp_path, tool):
    path = tmp_path / "latin.vhd"
    path.write_bytes(b"entity latin is\nend entity;\narchitecture a of latin is\nbegin\n  caf\xe9 caf\xe9;\nend architecture;\n")
    request = _request([path], "latin")
    with pytest.raises(UnicodeDecodeError):
        tool.run_once(request)
    with pytest.raises(UnicodeDecodeError):
        tool.run(request)
    other = tmp_path / "other.vhd"  # The answer was read whole, so the next one still lines up.
    other.write_text("entity other is end entity;\narchitecture a of other is begin end architecture;\n")
    next_request = _request([other], "other")
    assert _answer(tool.run(next_request)) == _answer(tool.run_once(next_request))


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
@needs_proc
def test_a_timeout_has_ended_the_elaboration_when_it_is_reported(design, tmp_path, tool, monkeypatch):
    """The child is already reaped, not merely signalled, and the server carries on without loading again."""
    hangs = tmp_path / "hangs.vhd"
    hangs.write_text(_HANGS)
    files = [design, hangs]
    assert tool.run(_request(files, "leaf")).returncode == 0
    server = tool.server_pid()
    monkeypatch.setattr(svhier_ghdl, "_TIMEOUT_S", 1)
    for _ in range(3):  # Each check is at the very moment the timeout is reported, so must not depend on luck.
        with pytest.raises(subprocess.TimeoutExpired):
            tool.run(_request(files, "hangs"))
        assert _group_members(server) == [server]
    monkeypatch.setattr(svhier_ghdl, "_TIMEOUT_S", 120)
    request = _request(files, "sized")
    assert _answer(tool.run(request)) == _answer(tool.run_once(request))
    assert tool.server_pid() == server


@needs_ghdl
def test_a_timeout_is_reported_as_a_separate_process_reports_it(tmp_path, tool, monkeypatch):
    hangs = tmp_path / "hangs.vhd"
    hangs.write_text(_HANGS)
    (tmp_path / "leaf.vhd").write_text("entity leaf is end entity;\narchitecture a of leaf is begin end architecture;\n")
    files = [hangs, tmp_path / "leaf.vhd"]
    assert tool.run(_request(files, "leaf")).returncode == 0  # So the short limit below only meets the elaboration.
    server = tool.server_pid()
    monkeypatch.setattr(svhier_ghdl, "_TIMEOUT_S", 1)
    with pytest.raises(subprocess.TimeoutExpired) as served:
        tool.run(_request(files, "hangs"))
    assert tool.server_pid() == server
    with pytest.raises(subprocess.TimeoutExpired) as alone:
        tool.run_once(_request([hangs], "hangs"))
    assert served.value.timeout == alone.value.timeout == 1


@needs_ghdl
@needs_proc
def test_a_stuck_server_is_killed_and_the_request_answered_without_it(design, tool, monkeypatch):
    request = _request([design], "sized", {"N": "3"})
    want = _answer(tool.run(request))
    server = tool.server_pid()
    os.kill(server, signal.SIGSTOP)  # It can't answer, however long it's given.
    # Give up on it a second in.  The request on its own still gets its full time.
    monkeypatch.setattr(svhier_ghdl, "_SERVER_GRACE_S", 1 - svhier_ghdl._TIMEOUT_S)
    assert _answer(tool.run(request)) == want
    assert not _group_members(server)
    assert tool.server_pid() is None


@needs_ghdl
def test_a_timeout_while_loading_leaves_no_server_behind(design, tool, monkeypatch):
    started = _count_worker_processes(monkeypatch)
    monkeypatch.setattr(svhier_ghdl, "_TIMEOUT_S", 0)
    with pytest.raises(subprocess.TimeoutExpired):
        tool.run(_request([design], "leaf"))
    assert len(started) == 2  # The server, then the request alone, which times out as it always did.
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
    assert not _group_members(first)


@needs_ghdl
def test_closing_stops_the_server(design, tool):
    tool.run(_request([design], "leaf"))
    group = tool.server_pid()
    tool.close()
    assert not _group_members(group)


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


_SERVER_NEEDS = [(os, "fork"), (os, "memfd_create"), (os, "killpg"), (os, "setsid"), (select, "poll"),
                 (signal, "sigtimedwait"), (signal, "pthread_sigmask")]


@pytest.mark.parametrize("module, name", _SERVER_NEEDS, ids=[name for _, name in _SERVER_NEEDS])
def test_without_what_the_server_needs_each_request_gets_a_process(module, name, monkeypatch):
    """Windows has none of these and macOS has no memfd_create.  None may surface as AttributeError."""
    tool = GhdlWorkerTool()
    once = subprocess.CompletedProcess([], 0, "alone", "")
    monkeypatch.setattr(tool, "run_once", lambda request: once)
    monkeypatch.delattr(module, name)
    started = _count_worker_processes(monkeypatch)
    assert tool.run(_request([Path("x.vhd")], "top")) is once
    assert not started
    assert tool.server_pid() is None


@needs_ghdl
@pytest.mark.parametrize("module, name", _SERVER_NEEDS, ids=[name for _, name in _SERVER_NEEDS])
def test_real_requests_without_what_the_server_needs_match(design, tool, module, name, monkeypatch):
    request = _request([design], "sized", {"N": "3"})
    want = _answer(tool.run_once(request))
    monkeypatch.delattr(module, name)
    started = _count_worker_processes(monkeypatch)
    assert _answer(tool.run(request)) == want
    assert len(started) == 1
    assert "--serve" not in started[0].args


def _site(tmp_path: Path, code: str) -> dict[str, str]:
    """Return an environment where Python runs *code* first, in the CLI and in every worker."""
    site = tmp_path / "site"
    site.mkdir(exist_ok=True)
    (site / "sitecustomize.py").write_text(code)
    return {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(site), os.environ.get("PYTHONPATH")]))}


def _cli(*args: str, env: dict[str, str] | None = None, cwd: Path = MIXED) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", "from svhier.cli import main; main()", *args],
                          capture_output=True, text=True, cwd=cwd, check=False, env=env)


@needs_ghdl
def test_the_cli_on_a_platform_without_memfd_create_gives_the_same_output(tmp_path):
    """macOS, as near as this can get: both the CLI and the workers lack it."""
    want = _cli(".", "--yaml", "-", "--no-tree")
    got = _cli(".", "--yaml", "-", "--no-tree", env=_site(tmp_path, "import os\ndel os.memfd_create\n"))
    assert (got.returncode, got.stdout, got.stderr) == (want.returncode, want.stdout, want.stderr)
    assert want.returncode == 0


@needs_ghdl
def test_a_call_that_exists_but_fails_in_the_server_falls_back(design, tmp_path, tool, monkeypatch):
    """A kernel or sandbox may refuse what Python offers.  The server dies, and nothing else changes."""
    request = _request([design], "sized", {"N": "3"})
    want = _answer(tool.run_once(request))
    code = "import os\ndef refuse(*_):\n    raise OSError(38, 'refused')\nos.memfd_create = refuse\n"
    monkeypatch.setenv("PYTHONPATH", _site(tmp_path, code)["PYTHONPATH"])
    started = _count_worker_processes(monkeypatch)
    assert _answer(tool.run(request)) == want
    assert _answer(tool.run(request)) == want
    assert len(started) == 3  # One failed server, not tried again for these sources, and two requests alone.


@needs_ghdl
def test_a_server_that_cannot_start_falls_back(design, tool, monkeypatch):
    request = _request([design], "leaf")
    want = _answer(tool.run_once(request))
    real = svhier_ghdl.subprocess.Popen

    def no_server(args, *rest, **kwargs):
        if "--serve" in args:
            raise OSError(11, "no more processes")
        return real(args, *rest, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", no_server)
    assert _answer(tool.run(request)) == want


def _cli_with_short_elaborations(*args: str) -> list[str]:
    """Run the CLI with each served elaboration limited to a second, and everything else as usual."""
    code = """
import time
import svhier_ghdl
real = svhier_ghdl._WorkerServer.request
svhier_ghdl._WorkerServer.request = lambda self, request, deadline: real(self, request, time.monotonic() + 1)
from svhier.cli import main
main()
"""
    return [sys.executable, "-c", code, *args]


def _cli_leaving_mid_request(signum: signal.Signals) -> list[str]:
    """Run the CLI until it has sent a request limited to a second, then have it get *signum* before any answer."""
    code = f"""
import os, time
import svhier_ghdl
def leave(self, request, deadline):
    self._send({{"request": request, "timeout": 1}})
    os.kill(os.getpid(), {int(signum)})
    time.sleep(120)
svhier_ghdl._WorkerServer.request = leave
from svhier.cli import main
main()
"""
    return [sys.executable, "-c", code, ".", "--no-tree"]


@needs_ghdl
@needs_proc
def test_nothing_is_left_after_a_normal_exit():
    marker = uuid.uuid4().hex
    proc = _cli(".", "--no-tree", env={**os.environ, "SVHIER_TEST_MARKER": marker})
    assert proc.returncode == 0, proc.stderr
    assert not _marked_processes(marker)


@needs_ghdl
@needs_proc
def test_nothing_is_left_after_an_error_exit(tmp_path):
    (tmp_path / "broken.vhd").write_text(_BROKEN)
    (tmp_path / "good.vhd").write_text("entity good is end entity;\narchitecture a of good is begin end architecture;\n")
    marker = uuid.uuid4().hex
    proc = _cli(".", "--no-tree", env={**os.environ, "SVHIER_TEST_MARKER": marker}, cwd=tmp_path)
    assert proc.returncode == 1, proc.stderr  # Through sys.exit.
    assert not _marked_processes(marker)


@needs_ghdl
@needs_proc
def test_nothing_is_left_after_a_timeout_in_the_cli(tmp_path):
    (tmp_path / "hangs.vhd").write_text(_HANGS)
    (tmp_path / "good.vhd").write_text("entity good is end entity;\narchitecture a of good is begin end architecture;\n")
    marker = uuid.uuid4().hex
    proc = subprocess.run(_cli_with_short_elaborations(".", "--no-tree", "-E"), capture_output=True, text=True, cwd=tmp_path, check=False,
                          env={**os.environ, "SVHIER_TEST_MARKER": marker, "COLUMNS": "200"})
    assert proc.returncode == 1
    assert "elaborating hangs timed out" in " ".join(proc.stderr.split())
    assert not _marked_processes(marker)


@needs_ghdl
@needs_proc
@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM, signal.SIGKILL], ids=lambda s: s.name)
def test_nothing_outlives_a_killed_cli_even_mid_elaboration(tmp_path, signum):
    """Only SIGINT lets the program clean up.  Otherwise the server's own timeout ends the child, then the server."""
    (tmp_path / "hangs.vhd").write_text(_HANGS)
    marker = uuid.uuid4().hex
    proc = subprocess.run(_cli_leaving_mid_request(signum), capture_output=True, cwd=tmp_path, check=False, timeout=120,
                          env={**os.environ, "SVHIER_TEST_MARKER": marker})
    assert proc.returncode != 0
    # The only wait here is on purpose: once the program is gone, nothing waits for what it left to end.
    deadline = time.monotonic() + 60
    while _marked_processes(marker) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _marked_processes(marker)


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
