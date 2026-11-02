# SPDX-License-Identifier: MIT
"""SPIKE: svhier frontend that elaborates VHDL with GHDL through pyGHDL.

GHDL is not a dependency and isn't bundled: the plugin looks for an installed pyGHDL at
runtime and reports what's missing if there is none.  Only :mod:`svhier_ghdl.worker`
imports pyGHDL, in a child process.
"""

import importlib.util
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from svhier.frontends import Request, empty_fragment

_MARKER = "@@svhier-order "
_TIMEOUT_S = 120
_INSTALL_HINT = "install GHDL's pyGHDL wheel from https://github.com/ghdl/ghdl/releases"

_LINE = re.compile(r"^( *)(.*)$")
_ARCH = re.compile(r"^architecture (\w+) of (\w+)$")
_INSTANCE = re.compile(r"^(\w+): (component|entity|configuration) (\w+)(\(\w+\))?( \[not bound\])?$")
_FOR = re.compile(r"^(\w+): for-generate \((-?\d+) (to|downto) (-?\d+)\)$")
_SCOPE = re.compile(r"^(\w+): (if-generate|case-generate|block)\b.*$")
_BODY = "generate statement body"


@dataclass
class ForeignDef:
    """One elaborated entity."""

    name: str
    file: str | None = None
    insts: list[dict[str, str]] = field(default_factory=list)


@dataclass
class Elaboration:
    """What one worker run reports."""

    defs: list[ForeignDef] = field(default_factory=list)
    # {file: [files it must be compiled after]}, for every file the top needs.
    file_deps: dict[str, list[str]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


@dataclass
class _Node:
    text: str
    children: list["_Node"] = field(default_factory=list)


def parse_tree(text: str) -> _Node:
    """Turn indented ``Disp_Hierarchy`` output into nodes."""
    root = _Node("")
    stack: list[tuple[int, _Node]] = [(-1, root)]
    for line in text.splitlines():
        if not line.strip():
            continue
        indent, body = _LINE.match(line).groups()
        while stack[-1][0] >= len(indent):
            stack.pop()
        node = _Node(body)
        stack[-1][1].children.append(node)
        stack.append((len(indent), node))
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


def _scan(children: list[_Node], prefix: str, found: list[tuple[str, str, _Node | None]]) -> None:
    """Collect ``(instance path, component name, architecture node)`` below a scope."""
    for child in children:
        match = _INSTANCE.match(child.text)
        if match:
            arch = next((c for c in child.children if _ARCH.match(c.text)), None)
            found.append((f"{prefix}{match.group(1)}", match.group(3), arch))
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
        entity = _ARCH.match(arch.text).group(2).lower()
        unit = defs.setdefault(entity, ForeignDef(entity, files.get(entity)))
        found: list[tuple[str, str, _Node | None]] = []
        _scan(arch.children, "", found)
        for path, component, child_arch in found:
            # A bound component may map to an entity with another name; trust the binding.
            child = visit(child_arch) if child_arch is not None else component.lower()
            inst = {"mod_name": child, "inst_name": path}
            if inst not in unit.insts:
                unit.insts.append(inst)
        return entity

    for node in root.children:
        if _ARCH.match(node.text):
            visit(node)
    return list(defs.values())


class GhdlFrontend:
    """Elaborate VHDL by running :mod:`svhier_ghdl.worker` once per top and setting."""

    name = "ghdl"
    suffixes = (".vhd", ".vhdl")
    catch_all = False

    def __init__(self) -> None:
        self.std = "08"
        self.work = "work"
        self._units: dict[str, str] = {}
        self._cache: dict[tuple, Elaboration] = {}

    def add_arguments(self, parser) -> None:
        """Add the plugin's options."""
        group = parser.add_argument_group("VHDL (svhier-ghdl)")
        group.add_argument("--vhdl-std", default="08", help="VHDL standard: 87, 93, 02, 08 or 19 (default: 08).")
        group.add_argument("--vhdl-work", default="work", metavar="LIB", help="Library the VHDL files belong to (default: work).")

    def configure(self, args) -> None:
        """Read the plugin's options."""
        self.std = args.vhdl_std
        self.work = args.vhdl_work

    def unavailable_reason(self) -> str | None:
        """Report why GHDL can't be used; None if it can."""
        if importlib.util.find_spec("pyGHDL") is None:
            return f"pyGHDL is not installed; {_INSTALL_HINT}"
        return None

    def has_inputs(self, files: list[str]) -> bool:
        """Only files count."""
        return bool(files)

    def _worker(self, request: dict) -> subprocess.CompletedProcess:
        spec = importlib.util.find_spec("pyGHDL")
        env = dict(os.environ)
        # The wheel bundles libgnat, but the loader only finds it through this path.
        lib_dir = Path(spec.origin).parent / "lib"
        env["LD_LIBRARY_PATH"] = os.pathsep.join(filter(None, [str(lib_dir), env.get("LD_LIBRARY_PATH", "")]))
        request = {"std": self.std, "work": self.work, **request}
        return subprocess.run([sys.executable, "-m", "svhier_ghdl.worker", json.dumps(request)],
                              capture_output=True, text=True, timeout=_TIMEOUT_S, env=env, check=False)

    def units(self, files: list[str]) -> dict[str, str]:
        """Return the entities in *files*, or nothing if the worker failed."""
        by_abs = {str(Path(f).resolve()): f for f in files}
        proc = self._worker({"op": "units", "files": list(by_abs)})
        for line in proc.stdout.splitlines():
            if line.startswith(_MARKER):
                self._units = {name: by_abs[path] for name, path in json.loads(line[len(_MARKER):]).items()}
        return self._units

    def elaborate(self, files: list[str], request: Request) -> dict:
        """Elaborate the roots; with ``auto``, also every entity nothing has reached yet."""
        if not self._units:
            self.units(files)
        runs = [(r.name, dict(r.generics), False) for r in request.roots]
        defs: dict[str, ForeignDef] = {}
        file_deps: dict[str, list[str]] = {}
        errors: list[str] = []
        soft: dict[str, list[str]] = {}

        def run(top: str, generics: dict[str, str], is_soft: bool) -> None:
            key = (top.lower(), tuple(sorted(generics.items())))
            if key not in self._cache:
                self._cache[key] = self.elaborate_one(files, top, generics)
            result = self._cache[key]
            for new in result.defs:
                have = defs.setdefault(new.name, ForeignDef(new.name, new.file))
                have.insts.extend(i for i in new.insts if i not in have.insts)
            for fname, needs in result.file_deps.items():
                have_deps = file_deps.setdefault(fname, [])
                have_deps.extend(n for n in needs if n not in have_deps)
            if is_soft:
                soft[top.lower()] = result.errors
            else:
                errors.extend(e for e in result.errors if e not in errors)

        for top, generics, is_soft in runs:
            run(top, generics, is_soft)
        if request.auto:
            for unit in self._units:
                if unit not in defs:
                    run(unit, {}, True)
        # An entity that fails alone may only need the generics its parent passes.
        reached = {i["mod_name"].lower() for d in defs.values() for i in d.insts}
        for unit, unit_errors in soft.items():
            if unit not in reached:
                errors.extend(e for e in unit_errors if e not in errors)
        return self._fragment(defs, file_deps, errors, auto=request.auto)

    def _fragment(self, defs: dict[str, ForeignDef], file_deps: dict[str, list[str]], errors: list[str], *, auto: bool) -> dict:
        """Build the result shape core expects, one entry per file."""
        result = empty_fragment()
        by_file: dict[str, list[ForeignDef]] = {}
        for unit in defs.values():
            if unit.file:
                by_file.setdefault(unit.file, []).append(unit)
        for fname in list(file_deps) + [f for f in by_file if f not in file_deps]:
            result["files"].append({
                "file_name": fname, "pkgs": [], "ifaces": [], "shadowed": [], "refs": [],
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
        result["diagnostics"] = [{"severity": "error", "file": "", "message": e} for e in errors]
        result["has_errors"] = bool(errors)
        result["needed"] = None if auto else [e["file_name"] for e in result["files"]]
        return result

    def drop_resolved(self, fragment: dict, names: set[str]) -> None:
        """Forget names another language defines."""
        for name in names:
            fragment["missing"]["modules"].pop(name, None)

    def elaborate_one(self, files: list[str], top: str, generics: dict[str, str]) -> Elaboration:
        """Elaborate *top*.  Failures, including worker crashes, come back in ``errors``."""
        by_abs = {str(Path(f).resolve()): f for f in files}
        try:
            proc = self._worker({"op": "elaborate", "files": list(by_abs), "top": top, "generics": generics})
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
            detail = [ln for ln in proc.stderr.splitlines() if ":error:" in ln or "raised " in ln]
            return Elaboration(errors=[f"ghdl: {top}: {d}" for d in detail] or [f"ghdl: {top}: worker exited with code {proc.returncode}"])
        return Elaboration(defs=tree_to_defs(parse_tree("\n".join(body)), self._units), file_deps=file_deps)
