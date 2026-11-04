# SPDX-License-Identifier: MIT
"""svhier frontend that elaborates VHDL with GHDL through pyGHDL.

GHDL is not a dependency and isn't bundled: the plugin looks for an installed pyGHDL at
runtime and reports what's missing if there is none.

Built like the slang plugin: a tool that alone touches pyGHDL, through :mod:`svhier_ghdl.worker`
in a child process, a library of what ``-y`` holds, and a frontend that takes both.
"""

import importlib.util
import io
import json
import os
import re
import select
import signal
import subprocess
import sys
import time
import weakref
from dataclasses import dataclass, field
from pathlib import Path

from svhier.frontends import Request, empty_fragment
from svhier.libsearch import Library, LibraryFactory, LibrarySearch, glob_files

_MARKER = "@@svhier-order "
_TIMEOUT_S = 120
_INSTALL_HINT = "install GHDL's pyGHDL wheel from https://github.com/ghdl/ghdl/releases"

_ARCH = re.compile(r"^architecture (\w+) of (\w+)$")
_INSTANCE = re.compile(r"^(\w+): (component|entity|configuration) (\w+)(\(\w+\))?( \[not bound\])?$")
_FOR = re.compile(r"^(\w+): for-generate \((-?\d+) (to|downto) (-?\d+)\)$")
_SCOPE = re.compile(r"^(\w+): (if-generate|case-generate|block)\b.*$")
_BODY = "generate statement body"
_GENERIC = re.compile(r'^constant interface "([^"]+)": (\w+)(?: \([^)]*\))? = (.*)$')
_INTEGER = re.compile(r"^-?\d+$")
_BITS = re.compile(r'^"([01XZ]+)"$')
_LOGIC = {"'0'": "1'b0", "'1'": "1'b1", "'X'": "1'bx", "'Z'": "1'bz"}
_BOOLEAN = {"true": "1'b1", "false": "1'b0", "'1'": "1'b1", "'0'": "1'b0"}
_COMMENT = re.compile(r"--.*$", re.M)
_PRIMARY_UNIT = re.compile(r"^\s*(?:entity|package|context|configuration\s+\w+\s+of)\s+(\w+)\s+is\b", re.I | re.M)
_SECONDARY_UNIT = re.compile(r"^\s*(?:architecture\s+\w+\s+of|package\s+body)\s+(\w+)\s+is\b", re.I | re.M)
_BUILTIN_LIBRARIES = {"std", "ieee"}


@dataclass
class ForeignDef:
    """One elaborated entity."""

    name: str
    file: str | None = None
    insts: list[dict[str, str]] = field(default_factory=list)
    # {unbound component: [{"generics": {name: literal}, "unresolved": [text]}]}
    uses: dict[str, list[dict]] = field(default_factory=dict)


@dataclass
class Elaboration:
    """What one worker run reports."""

    defs: list[ForeignDef] = field(default_factory=list)
    # {file: [files it must be compiled after]}, for every file the top needs.
    file_deps: dict[str, list[str]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class _Node:
    text: str
    children: list["_Node"] = field(default_factory=list)
    use: dict = field(default_factory=lambda: {"generics": {}, "unresolved": []})


def to_literal(kind: str, value: str) -> str | None:  # pylint: disable=too-many-return-statements
    """Translate a value GHDL printed into the notation frontends share, or ``None`` if it has none.

    *kind* is GHDL's word for the value's type.  See :class:`svhier.frontends.Root`.
    """
    if kind == "discrete":
        return value if _INTEGER.match(value) else None  # Otherwise a time or an enumeration.
    if kind == "bit":
        return _BOOLEAN.get(value)
    if kind == "logic":
        return _LOGIC.get(value)
    if kind == "float":
        return value
    if kind == "vector":
        match = _BITS.match(value)
        return f"{len(match.group(1))}'b{match.group(1).lower()}" if match else None
    if kind == "array" and value.startswith('"'):
        inner = value[1:-1].replace("\\", "\\\\").replace('"', '\\"')
        return f'"{inner}"'
    return None


class VhdlLibrary:
    """What VHDL files under ``-y`` define.  A regex is enough to choose files; GHDL reads them."""

    def __init__(self, dirs: list[str], ignore: set[str]) -> None:
        self.primary: dict[str, list[Path]] = {}
        self.secondary: dict[str, list[Path]] = {}
        for directory in dirs:
            for path in glob_files(Path(directory), True, ("*.vhd", "*.vhdl"), ignore):
                try:
                    text = _COMMENT.sub("", path.read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    continue
                for pattern, table in ((_PRIMARY_UNIT, self.primary), (_SECONDARY_UNIT, self.secondary)):
                    for name in dict.fromkeys(n.lower() for n in pattern.findall(text)):
                        table.setdefault(name, []).append(path)

    def __bool__(self) -> bool:
        return bool(self.primary)

    def find(self, name: str, scope: str = "") -> tuple[list[Path], int]:
        """Return the files for unit *name*, and how many other files also define it.

        The first file defines the unit.  Architectures and package bodies may live in their
        own files, so the others are those in the same directory.  Same preference as for SV,
        after a directory named after *scope*, the library that references it.
        """
        name = name.lower()

        def rank(path: Path) -> tuple:
            stem = path.stem.lower()
            return (scope not in (p.lower() for p in path.parent.parts), stem != name, not name.startswith(stem), len(path.parts), str(path))

        candidates = sorted(self.primary.get(name, []), key=rank)
        if not candidates:
            return [], 0
        best = candidates[0]
        rest = [p for p in self.secondary.get(name, []) if p.parent == best.parent and p != best]
        return [best, *rest], len(candidates) - 1


def _pick_named(name: str, library: str, candidates: list[str], notes: list[str]) -> str:
    """Choose which file defining *name* a ``library.name`` reference means."""
    if len(candidates) == 1:
        return candidates[0]
    in_directory = [c for c in candidates if library in (p.lower() for p in Path(c).parent.parts)]
    if in_directory:
        return in_directory[0]
    note = f"several files define {name} for library {library}; using {candidates[0]}"
    if note not in notes:
        notes.append(note)
    return candidates[0]


def _pick_local(path: str, candidates: list[str], placed: dict[str, str]) -> str | None:
    """Choose which file a reference from *path* to its own library means; None if unclear."""
    if path in candidates:
        return path
    if len(candidates) == 1:
        return candidates[0]
    near = [c for c in candidates if Path(c).parent == Path(path).parent]
    if near:
        return near[0]
    return next((c for c in candidates if c in placed and placed[c] == placed.get(path)), None)


def _place_named(scan: dict[str, dict], definers: dict[str, list[str]], placed: dict[str, str], notes: list[str]) -> None:
    """Follow every ``library.unit`` reference to a library some file declares."""
    declared = {lib.lower() for entry in scan.values() for lib in entry["libs"]} - {"work"}
    for path in sorted(scan):
        for library, unit in scan[path]["refs"]:
            library, unit = library.lower(), unit.lower()
            if library not in declared or unit not in definers:
                continue
            target = _pick_named(unit, library, definers[unit], notes)
            if placed.setdefault(target, library) != library:
                note = f"{target} is referenced as part of both {placed[target]} and {library}; using {placed[target]}"
                if note not in notes:
                    notes.append(note)


def _spread_local(scan: dict[str, dict], definers: dict[str, list[str]], placed: dict[str, str]) -> None:
    """Files linked by ``work.unit``, or by an architecture and its entity, share a library."""
    changed = True
    while changed:
        changed = False
        for path in sorted(scan):
            linked = [r[1].lower() for r in scan[path]["refs"] if r[0].lower() == "work"] + [a.lower() for a in scan[path]["arch_of"]]
            for unit in linked:
                target = _pick_local(path, definers[unit], placed) if unit in definers else None
                if target is None or target == path:
                    continue
                if path in placed and target not in placed:
                    placed[target] = placed[path]
                    changed = True
                elif target in placed and path not in placed:
                    placed[path] = placed[target]
                    changed = True


def assign_libraries(scan: dict[str, dict], default: str) -> tuple[dict[str, str], list[str]]:
    """Choose a library for each file from how the files refer to each other.

    *scan* is ``{file: {"units", "arch_of", "refs", "libs"}}``, as the worker reports it.  A
    ``library.unit`` reference places the file defining the unit, with a directory named
    after the library breaking ties.  ``work.unit`` references and architectures then spread
    that library.  Other files go to *default*.  Returns ``({file: library}, notes)``.
    """
    definers: dict[str, list[str]] = {}
    for path in sorted(scan):
        for unit in scan[path]["units"]:
            definers.setdefault(unit.lower(), []).append(path)
    placed: dict[str, str] = {}
    notes: list[str] = []
    _place_named(scan, definers, placed, notes)
    _spread_local(scan, definers, placed)
    return {path: placed.get(path, default) for path in scan}, notes


def _is_structure(body: str) -> bool:
    return bool(body == _BODY or _ARCH.match(body) or _INSTANCE.match(body) or _FOR.match(body) or _SCOPE.match(body))


def parse_tree(text: str) -> _Node:
    """Turn indented ``Disp_Hierarchy`` output into nodes.

    Object lines (signals, generics and so on) share the indent of what they belong to, so
    they can't be nodes: they would become the parent of whatever follows.  The generics
    printed right after an instance are stored on it; other object lines are dropped.
    """
    root = _Node("")
    last = root
    stack: list[tuple[int, _Node]] = [(-1, root)]
    for line in text.splitlines():
        if not line.strip():
            continue
        body = line.lstrip(" ")
        indent = line[:len(line) - len(body)]
        if not _is_structure(body):
            match = _GENERIC.match(body)
            if match and _INSTANCE.match(last.text) and stack[-1] == (len(indent), last):
                name, kind, value = match.groups()
                literal = to_literal(kind, value)
                if literal is None:
                    last.use["unresolved"].append(f"{name} = {value}")
                else:
                    last.use["generics"][name] = literal
            continue
        while stack[-1][0] >= len(indent):
            stack.pop()
        last = _Node(body)
        stack[-1][1].children.append(last)
        stack.append((len(indent), last))
    return root


def _split_bodies(children: list[_Node]) -> list[list[_Node]]:
    """Split generate children, where each body starts with a marker line at the same depth."""
    bodies: list[list[_Node]] = []
    for child in children:
        if child.text == _BODY:
            bodies.append(list(child.children))
        elif bodies:
            bodies[-1].append(child)
    return bodies


def _scan(children: list[_Node], prefix: str, found: list[tuple[str, str, _Node | None, dict]]) -> None:
    """Collect ``(instance path, component name, architecture node, use)`` below a scope."""
    for child in children:
        match = _INSTANCE.match(child.text)
        if match:
            arch = next((c for c in child.children if _ARCH.match(c.text)), None)
            found.append((f"{prefix}{match.group(1)}", match.group(3), arch, child.use))
            continue
        match = _FOR.match(child.text)
        if match:
            label, low, direction, _ = match.groups()
            step = 1 if direction == "to" else -1
            for offset, body in enumerate(_split_bodies(child.children)):
                _scan(body, f"{prefix}{label}[{int(low) + step * offset}].", found)
            continue
        match = _SCOPE.match(child.text)
        if match:
            label = match.group(1)
            bodies = _split_bodies(child.children)
            if bodies:
                for body in bodies:
                    _scan(body, f"{prefix}{label}.", found)
            else:
                _scan(child.children, f"{prefix}{label}.", found)


def tree_to_defs(root: _Node, files: dict[str, str]) -> list[ForeignDef]:
    """Flatten the elaborated tree into one definition per entity, merging repeats."""
    defs: dict[str, ForeignDef] = {}

    def visit(arch: _Node) -> str:
        match = _ARCH.match(arch.text)
        assert match is not None  # Only architecture nodes get here.
        entity = match.group(2).lower()
        unit = defs.setdefault(entity, ForeignDef(entity, files.get(entity)))
        found: list[tuple[str, str, _Node | None, dict]] = []
        _scan(arch.children, "", found)
        for path, component, child_arch, use in found:
            # A bound component may map to an entity with another name; trust the binding.
            child = visit(child_arch) if child_arch is not None else component.lower()
            inst = {"mod_name": child, "inst_name": path}
            if inst not in unit.insts:
                unit.insts.append(inst)
            if child_arch is None and use not in unit.uses.setdefault(child, []):
                unit.uses[child].append(use)
        return entity

    for node in root.children:
        if _ARCH.match(node.text):
            visit(node)
    return list(defs.values())


class _ServerFailed(Exception):
    """The serving worker died, as opposed to the child running one request."""


def _stop_server(process: subprocess.Popen, descriptors: tuple[int, ...]) -> None:
    for descriptor in descriptors:
        os.close(descriptor)
    try:  # The group holds the server and any child elaborating.
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


class _WorkerServer:
    """A worker that has loaded one set of sources, and forks a child for each request.

    Starting Python and libghdl and reading the sources takes most of the time of a run, and
    without ``--top`` there is a run per entity.  A forked child starts from the state a new
    process has after loading, so it gives the same answer, and a crash only ends the child.
    """

    def __init__(self, env: dict[str, str]) -> None:
        request_read, self._requests = os.pipe()
        self._replies, reply_write = os.pipe()
        self.command = [sys.executable, "-m", "svhier_ghdl.worker", "--serve", str(request_read), str(reply_write)]
        # In a session of its own, to kill a hanging child with it.  Stdin stays inherited, as with a process per run.
        process = subprocess.Popen(  # pylint: disable=consider-using-with
            self.command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
            pass_fds=(request_read, reply_write), start_new_session=True)
        os.close(request_read)
        os.close(reply_write)
        self.pid = process.pid
        self.close = weakref.finalize(self, _stop_server, process, (self._requests, self._replies))
        self._poll = select.poll()
        self._poll.register(self._replies, select.POLLIN)
        self._buffer = bytearray()

    def load(self, sources: dict, deadline: float) -> None:
        """Have the server load *sources*, and wait until it has."""
        self._send(sources)
        self._read_line(deadline)

    def _send(self, message: dict) -> None:
        data = json.dumps(message).encode() + b"\n"
        try:
            while data:
                data = data[os.write(self._requests, data):]
        except OSError as error:
            raise _ServerFailed from error

    def _read_more(self, deadline: float) -> None:
        if not self._poll.poll(max(0.0, deadline - time.monotonic()) * 1000):
            raise subprocess.TimeoutExpired(self.command, _TIMEOUT_S)
        data = os.read(self._replies, 1 << 16)
        if not data:
            raise _ServerFailed
        self._buffer += data

    def _read_line(self, deadline: float) -> bytes:
        while (end := self._buffer.find(b"\n")) < 0:
            self._read_more(deadline)
        line = bytes(self._buffer[:end])
        del self._buffer[:end + 1]
        return line

    def _read_text(self, size: int, deadline: float) -> str:
        while len(self._buffer) < size:
            self._read_more(deadline)
        data = bytes(self._buffer[:size])
        del self._buffer[:size]
        return io.TextIOWrapper(io.BytesIO(data)).read()  # Decoded as subprocess's text mode does.

    def request(self, request: dict, deadline: float) -> subprocess.CompletedProcess:
        """Run one elaboration request."""
        self._send(request)
        header = json.loads(self._read_line(deadline))
        stdout = self._read_text(header["stdout"], deadline)
        stderr = self._read_text(header["stderr"], deadline)
        return subprocess.CompletedProcess(self.command, header["returncode"], stdout, stderr)


class GhdlWorkerTool:
    """Everything the frontend needs from pyGHDL, which :mod:`svhier_ghdl.worker` does for it."""

    def __init__(self) -> None:
        self._server: _WorkerServer | None = None
        self._sources: dict | None = None
        self._failed_sources: dict | None = None

    def unavailable_reason(self) -> str | None:
        """Report if pyGHDL is missing."""
        return None if importlib.util.find_spec("pyGHDL") else f"pyGHDL is not installed; {_INSTALL_HINT}"

    def _environment(self) -> dict[str, str]:
        spec = importlib.util.find_spec("pyGHDL")
        if spec is None or spec.origin is None:
            raise ValueError(f"pyGHDL is not installed; {_INSTALL_HINT}")
        env = dict(os.environ)
        # The wheel bundles libgnat, but the loader only finds it through this path.
        lib_dir = Path(spec.origin).parent / "lib"
        env["LD_LIBRARY_PATH"] = os.pathsep.join(filter(None, [str(lib_dir), env.get("LD_LIBRARY_PATH", "")]))
        return env

    def run_once(self, request: dict) -> subprocess.CompletedProcess:
        """Run one worker request in a process of its own."""
        return subprocess.run([sys.executable, "-m", "svhier_ghdl.worker", json.dumps(request)],
                              capture_output=True, text=True, timeout=_TIMEOUT_S, env=self._environment(), check=False)

    def run(self, request: dict) -> subprocess.CompletedProcess:
        """Run one worker request, in the server for its sources if it's an elaboration.

        Whatever stops the server, a process per request gives the answer it would have given.
        """
        if request["op"] != "elaborate":
            return self.run_once(request)
        sources = {key: request[key] for key in ("std", "work", "files", "libraries")}
        if sources != self._failed_sources:
            try:
                return self._serve(sources, request)
            except _ServerFailed:
                self._failed_sources = sources
                self.close()
            except subprocess.TimeoutExpired:
                self.close()
                raise
        return self.run_once(request)

    def _serve(self, sources: dict, request: dict) -> subprocess.CompletedProcess:
        deadline = time.monotonic() + _TIMEOUT_S
        if self._server is None or sources != self._sources:
            self.close()
            self._server = _WorkerServer(self._environment())
            self._server.load(sources, deadline)
            self._sources = sources
        return self._server.request(request, deadline)

    def server_pid(self) -> int | None:
        """Return the process serving elaborations, if there is one."""
        return self._server.pid if self._server else None

    def close(self) -> None:
        """Stop the server, if there is one.  It also stops when the program exits."""
        if self._server:
            self._server.close()
            self._server = None


class GhdlFrontend:  # pylint: disable=too-many-instance-attributes
    """Elaborate VHDL, running the tool once per top and setting.

    *tool* runs GHDL, and *library_factory* builds the index of ``-y``.
    """

    name = "ghdl"
    suffixes = (".vhd", ".vhdl")
    catch_all = False

    def __init__(self, tool: GhdlWorkerTool | None = None, library_factory: LibraryFactory = VhdlLibrary) -> None:
        self.tool = tool or GhdlWorkerTool()
        self._library_factory = library_factory
        self.search: LibrarySearch[Library] = LibrarySearch()
        self.std = "08"
        self.work = "work"
        self._units: dict[str, str] | None = None
        # {lowercase entity name: every file defining it}, since libraries may share names.
        self._definers: dict[str, list[str]] = {}
        self._libraries: dict[str, str] = {}
        self._notes: list[str] = []
        self._cache: dict[tuple, Elaboration] = {}
        self._scan: dict[str, dict] = {}

    def add_arguments(self, parser) -> None:
        """Add the plugin's options."""
        group = parser.add_argument_group("VHDL (svhier-ghdl)")
        group.add_argument("--vhdl-std", default="08", help="VHDL standard: 87, 93, 02, 08 or 19 (default: 08).")
        group.add_argument("--vhdl-work", default="work", metavar="LIB", help="Library the VHDL files belong to (default: work).")

    def configure(self, args) -> None:
        """Read the plugin's options."""
        self.std = args.vhdl_std
        self.work = args.vhdl_work
        ignore = {n.lower() for n in args.ignore} if args.ignore else set()
        self.search = LibrarySearch(self._library_factory(args.libdirs, ignore) if args.libdirs else None)

    def unavailable_reason(self) -> str | None:
        """Report why GHDL can't be used; None if it can."""
        return self.tool.unavailable_reason()

    def extra_inputs(self) -> list[str]:
        """Only files count."""
        return []

    def _worker(self, request: dict) -> subprocess.CompletedProcess:
        return self.tool.run({"std": self.std, "work": self.work, **request})

    def units(self, files: list[str]) -> dict[str, str]:
        """Return the entities in *files*, or nothing if the worker failed.  Also chooses libraries."""
        if self._units is None:
            self._units = self._read_units(files)
        return self._units

    def _read_units(self, files: list[str]) -> dict[str, str]:
        by_abs = {str(Path(f).resolve()): f for f in files if f not in self._scan}
        if by_abs:
            proc = self._worker({"op": "scan", "files": list(by_abs)})
            for line in proc.stdout.splitlines():
                if line.startswith(_MARKER):
                    self._scan.update({by_abs[path]: entry for path, entry in json.loads(line[len(_MARKER):]).items()})
        scan = {f: self._scan[f] for f in files if f in self._scan}
        self._libraries, self._notes = assign_libraries(scan, self.work.lower())
        self._definers = {}
        for path, entry in scan.items():
            for name in entry["entities"]:
                self._definers.setdefault(name, []).append(path)
        return {name: paths[0] for name, paths in self._definers.items()}

    def has_library(self) -> bool:
        """Report whether ``-y`` holds anything for GHDL."""
        return bool(self.search)

    def provide(self, name: str, files: list[str]) -> bool:
        """Add the ``-y`` file defining entity *name*, and what it needs to compile."""
        if not self.search.has(name):
            return False
        self._pull_needed([name], files)
        self._units = None
        return True

    def _pull_needed(self, names: list[str], files: list[str]) -> bool:
        """Pull in from ``-y`` what the units *names* must see to compile, however indirectly.

        Unlike an instance through a component, ``use lib.pkg`` and ``entity lib.unit`` need the
        unit to exist.  Only following them from *names* leaves out what other files need.
        """
        if not self.search:
            return False
        pulled = False
        pending = [("", n.lower()) for n in names]
        seen: set[str] = set()
        while pending:
            library, unit = pending.pop()
            if unit in seen:
                continue
            seen.add(unit)
            if not any(unit in e["units"] for e in self._scan.values()) and self.search.pull(unit, files, library):
                pulled = True
                self._units = None
                self._cache.clear()
                self.units(files)
            for entry in self._scan.values():
                if unit in entry["units"] or unit in entry["arch_of"]:
                    local = {"work", *(lib.lower() for lib in entry["libs"])} - _BUILTIN_LIBRARIES
                    pending.extend((lib, ref) for lib, ref in entry["named"] if lib in local)
        return pulled

    def elaborate(self, files: list[str], request: Request) -> dict:
        """Elaborate the roots; with ``auto``, also every entity nothing has reached yet.

        Instances still unbound afterwards may be entities under ``-y``, which needs another run.
        """
        self.units(files)
        names = [r.name for r in request.roots]
        # With auto, every entity is a root, even those only -y has components for.
        self._pull_needed(names + [u for e in self._scan.values() for u in e["units"]] if request.auto else names, files)
        while True:
            defs, file_deps, errors = self._elaborate_all(files, request)
            unbound = {i["mod_name"] for d in defs.values() for i in d.insts} - set(defs) - request.defined_elsewhere
            if not self._pull_needed(sorted(unbound), files):
                return self._fragment(defs, file_deps, errors, auto=request.auto)

    def _elaborate_all(self, files: list[str], request: Request) -> tuple[dict[str, ForeignDef], dict[str, list[str]], list[str]]:
        """Run the worker for every root, and merge what the runs report."""
        defs: dict[str, ForeignDef] = {}
        file_deps: dict[str, list[str]] = {}
        errors: list[str] = []
        soft: dict[str, list[str]] = {}

        def run(top: str, generics: dict[str, str], is_soft: bool) -> None:
            key = (top.lower(), tuple(sorted(generics.items())))
            if key not in self._cache:
                self._cache[key] = self.elaborate_one(files, top, generics)
            result = self._cache[key]
            self._notes.extend(n for n in result.notes if n not in self._notes)
            for new in result.defs:
                have = defs.setdefault(new.name, ForeignDef(new.name, new.file))
                have.insts.extend(i for i in new.insts if i not in have.insts)
                for name, uses in new.uses.items():
                    have_uses = have.uses.setdefault(name, [])
                    have_uses.extend(u for u in uses if u not in have_uses)
            for fname, needs in result.file_deps.items():
                have_deps = file_deps.setdefault(fname, [])
                have_deps.extend(n for n in needs if n not in have_deps)
            if is_soft:
                soft[top.lower()] = result.errors
            else:
                errors.extend(e for e in result.errors if e not in errors)

        for root in request.roots:
            run(root.name, dict(root.generics), False)
        if request.auto:
            for unit in self.units(files):
                if unit not in defs:
                    run(unit, {}, True)
        # An entity that fails alone may only need the generics its parent passes.
        reached = {i["mod_name"].lower() for d in defs.values() for i in d.insts}
        for unit, unit_errors in soft.items():
            if unit not in reached:
                errors.extend(e for e in unit_errors if e not in errors)
        return defs, file_deps, errors

    def _fragment(self, defs: dict[str, ForeignDef], file_deps: dict[str, list[str]], errors: list[str], *, auto: bool) -> dict:
        """Build the result shape core expects, one entry per file."""
        result = empty_fragment()
        by_file: dict[str, list[ForeignDef]] = {}
        for unit in defs.values():
            if unit.file:
                by_file.setdefault(unit.file, []).append(unit)
        for fname in list(file_deps) + [f for f in by_file if f not in file_deps]:
            result["files"].append({
                "file_name": fname, "library": self._libraries.get(fname, self.work.lower()),
                "pkgs": [], "ifaces": [], "shadowed": [], "refs": [],
                "depends_on": file_deps.get(fname, []),
                "defs": [{"mod_name": d.name, "pkg_imports": [], "insts": [dict(i) for i in d.insts]} for d in by_file.get(fname, [])],
            })
        for entry in result["files"]:
            for unit in entry["defs"]:
                for inst in unit["insts"]:
                    if inst["mod_name"] not in defs:
                        result["unresolved"].setdefault(inst["mod_name"], [])
                        users = result["missing"]["modules"].setdefault(inst["mod_name"], [])
                        if entry["file_name"] not in users:
                            users.append(entry["file_name"])
        for unit in defs.values():
            for name, uses in unit.uses.items():
                if unit.file and name not in defs:
                    known = result["unresolved"].setdefault(name, [])
                    known.extend(u for u in uses if u not in known)
        result["diagnostics"] = [{"severity": "error", "file": "", "message": e} for e in errors]
        result["has_errors"] = bool(errors)
        result["notes"] = list(self._notes)
        result["pulled"] = self.search.pulled
        result["needed"] = None if auto else [e["file_name"] for e in result["files"]]
        return result

    def drop_resolved(self, fragment: dict, names: set[str]) -> None:
        """Forget names another language defines."""
        for name in names:
            fragment["missing"]["modules"].pop(name, None)

    def elaborate_one(self, files: list[str], top: str, generics: dict[str, str]) -> Elaboration:
        """Elaborate *top*.  Failures, including worker crashes, come back in ``errors``."""
        by_abs = {str(Path(f).resolve()): f for f in files}
        request = {
            "op": "elaborate", "files": list(by_abs), "top": top, "generics": generics,
            "top_library": self._libraries.get((self._units or {}).get(top.lower(), ""), self.work),
            "libraries": {path: self._libraries[name] for path, name in by_abs.items() if name in self._libraries},
            "objects": True,
        }
        notes: list[str] = []
        try:
            proc = self._worker(request)
            if proc.returncode != 0 and "raised " in proc.stderr:
                # Printing objects crashes on some VHDL-2008 generics.  The tree doesn't need them.
                retry = self._worker({**request, "objects": False})
                if retry.returncode == 0:
                    proc = retry
                    notes.append(f"{top}: couldn't read generics, so SV modules below it use their defaults")
        except subprocess.TimeoutExpired:
            return Elaboration(errors=[f"ghdl: elaborating {top} timed out after {_TIMEOUT_S} s"])
        file_deps: dict[str, list[str]] = {}
        body: list[str] = []
        for line in proc.stdout.splitlines():
            if line.startswith(_MARKER):
                raw = json.loads(line[len(_MARKER):])
                file_deps = {by_abs[f]: [by_abs[n] for n in needs] for f, needs in raw.items()}
            else:
                body.append(line)
        if proc.returncode != 0:
            detail = [ln for ln in proc.stderr.splitlines() if ":error:" in ln or ln.startswith("error:") or "raised " in ln]
            return Elaboration(errors=[f"ghdl: {top}: {d}" for d in detail] or [f"ghdl: {top}: worker exited with code {proc.returncode}"])
        # Of several files defining a name, the design used the one in its dependencies.
        located = {name: next((p for p in paths if p in file_deps), paths[0]) for name, paths in self._definers.items()}
        return Elaboration(defs=tree_to_defs(parse_tree("\n".join(body)), located), file_deps=file_deps, notes=notes)
