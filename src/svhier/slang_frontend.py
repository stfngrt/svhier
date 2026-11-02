# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Stefan Engert

"""SPIKE: the SystemVerilog frontend, registered like any other plugin.

Built like the GHDL plugin: a tool that alone touches pyslang, a library of what ``-y`` holds,
and a frontend that takes both.  pyslang is only imported when the tool runs, so svhier starts
without it.
"""

import argparse
import importlib.util
import re
from pathlib import Path

from svhier.frontends import Request, Root, empty_fragment
from svhier.libsearch import LibraryFactory, LibraryIndex, LibrarySearch, glob_files, is_ignored, resolve_missing

# Upper bound on parse/resolve rounds for -y; each round pulls in one more layer of dependencies.
_MAX_LIB_ROUNDS = 20


_INCLUDE_RE = re.compile(r'`include\s+["<]([^">]+)[">]')


def _include_base_dirs(sources: list[Path], headers: list[Path]) -> list[Path]:
    """Return the base dirs needed by `` `include "sub/dir/file.svh"`` directives.

    For every include path with a directory component, each known header whose path
    ends with that include path contributes the directory it must be resolved from,
    e.g. ``vendor/include/`` for ``vendor/include/vendor/regs.svh``.
    """
    by_name: dict[str, list[Path]] = {}
    for hdr in headers:
        by_name.setdefault(hdr.name, []).append(hdr)

    bases: list[Path] = []
    for src in sources:
        try:
            text = src.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for inc in _INCLUDE_RE.findall(text):
            parts = Path(inc).parts
            if len(parts) < 2 or Path(inc).is_absolute() or ".." in parts:
                continue
            for hdr in by_name.get(parts[-1], []):
                if hdr.parts[-len(parts):] == parts and len(hdr.parts) > len(parts):
                    bases.append(hdr.parents[len(parts) - 1])
    return bases


def collect_inc_dirs(paths: list[str], recursive: bool, ignore: set[str] | None = None) -> list[str]:
    """Return include directories for the ``.svh``/``.vh`` header files under *paths*.

    The result is passed to slang as `` `include`` search paths and written as
    ``+incdir+<dir>`` lines at the top of the generated filelist, which is the
    format expected by VCS, Questa, and Xcelium.

    Every directory that contains a header is returned.  In addition, when a source
    or header uses `` `include "sub/dir/file.svh"`` and a matching header exists,
    the directory that include path is relative to is returned too.

    For directory inputs the search respects the *recursive* flag.
    For explicit file inputs the parent directory is checked for headers.
    Directories are returned in discovery order, deduplicated.
    """
    ign = ignore or set()
    headers: list[Path] = []
    sources: list[Path] = []

    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            headers.extend(glob_files(p, recursive, ("*.svh", "*.vh"), ign))
            sources.extend(glob_files(p, recursive, ("*.sv", "*.v"), ign))
        elif p.is_file() and not is_ignored(p, ign):
            sources.append(p)
            headers.extend(glob_files(p.parent, False, ("*.svh", "*.vh"), ign))

    seen: set[Path] = set()
    result: list[str] = []
    for d in [h.parent for h in headers] + _include_base_dirs(sources + headers, headers):
        resolved = d.resolve()
        if resolved not in seen:
            seen.add(resolved)
            result.append(str(d))
    return result


class _CommandFileAction(argparse.Action):  # pylint: disable=too-few-public-methods
    """Collect ``-f``/``-F`` options as ``(flag, path)`` pairs, in command-line order."""

    def __call__(self, parser, namespace, values, option_string=None):
        items = list(getattr(namespace, self.dest) or [])
        items.append((option_string, values))
        setattr(namespace, self.dest, items)


def _add_unique(target: list, items: list) -> None:
    target.extend(item for item in items if item not in target)


def _merge_files(base: list[dict], other: list[dict]) -> None:
    """Add the instances another compilation of the same sources found."""
    entries = {entry["file_name"]: entry for entry in base}
    for entry in other:
        if entry["file_name"] not in entries:
            base.append(entry)
            continue
        defs = {unit["mod_name"]: unit for unit in entries[entry["file_name"]]["defs"]}
        for unit in entry["defs"]:
            if unit["mod_name"] in defs:
                _add_unique(defs[unit["mod_name"]]["insts"], unit["insts"])
                _add_unique(defs[unit["mod_name"]]["pkg_imports"], unit["pkg_imports"])
            else:
                entries[entry["file_name"]]["defs"].append(unit)


def _merge_results(base: dict, other: dict) -> None:
    """Merge a :func:`svhier.parser.parse_files` result for another setting into *base*."""
    _merge_files(base["files"], other["files"])
    if base["needed"] is not None:
        _add_unique(base["needed"], other["needed"] or [])
    for key in ("duplicates", "diagnostics", "defines", "inc_dirs"):
        _add_unique(base[key], other[key])
    base["has_errors"] = base["has_errors"] or other["has_errors"]
    for kind, names in other["missing"].items():
        for name, users in names.items():
            _add_unique(base["missing"][kind].setdefault(name, []), users)
    for name, uses in other["blackbox_params"].items():
        _add_unique(base["blackbox_params"].setdefault(name, []), uses)


def _nothing_elaborated(result: dict) -> dict:
    """Keep what the sources define from a no-tops *result*, but not what elaborating found.

    For when no root can be elaborated: modules slang picked itself aren't part of the design.
    """
    for entry in result["files"]:
        for unit in entry["defs"]:
            unit["insts"] = []
    result["blackbox_params"] = {}
    result["missing"]["modules"] = {}
    return result


class PyslangTool:
    """Everything the frontend needs from pyslang."""

    def unavailable_reason(self) -> str | None:
        """Report if pyslang is missing."""
        return None if importlib.util.find_spec("pyslang") else "pyslang is not installed"

    def parse(self, files: list[str], inc_dirs: list[str], **options) -> dict:
        """Elaborate *files*; see :func:`svhier.parser.parse_files`."""
        from svhier.parser import parse_files  # pylint: disable=import-outside-toplevel

        return parse_files(files, inc_dirs, **options)


class SlangFrontend:  # pylint: disable=too-many-instance-attributes
    """Elaborate SystemVerilog with slang, including ``-f``, ``-I``, ``-D`` and ``-y``.

    *tool* runs slang, and *library_factory* builds the index of ``-y``.
    """

    name = "slang"
    suffixes = (".sv", ".v")
    catch_all = True

    def __init__(self, tool: PyslangTool | None = None, library_factory: LibraryFactory = LibraryIndex) -> None:
        self.tool = tool or PyslangTool()
        self._library_factory = library_factory
        self.search = LibrarySearch()
        self.defines: list[str] = []
        self.inc_dirs: list[str] = []
        self.command_files: list = []
        self._defined_elsewhere: set[str] = set()
        self._units: dict[str, str] | None = None
        # {lowercase module name: (module name, its parameter names)}
        self._modules: dict[str, tuple[str, list[str]]] = {}

    def add_arguments(self, parser) -> None:
        """Add the SystemVerilog options."""
        group = parser.add_argument_group("SystemVerilog (slang)")
        group.add_argument(
            "-f", "-F",
            action=_CommandFileAction,
            metavar="FILE",
            dest="command_files",
            default=[],
            help="Read sources and options from a command file (filelist). Supports +incdir+, +define+, "
                 "nested -f/-F and ${VAR} environment variables. With -F, paths inside FILE are relative to FILE. "
                 "Repeatable.",
        )
        group.add_argument(
            "-I", "--include-dir",
            action="append",
            metavar="DIR",
            default=[],
            dest="include_dirs",
            help="Add a directory to search for include files. Repeatable.",
        )
        group.add_argument(
            "-D", "--define",
            action="append",
            metavar="NAME[=VALUE]",
            default=[],
            dest="defines",
            help="Define a preprocessor macro. Repeatable.",
        )

    def configure(self, args) -> None:
        """Read the SV options."""
        ignore = {n.lower() for n in args.ignore} if args.ignore else set()
        self.defines = args.defines
        found = collect_inc_dirs(args.paths, args.recursive, ignore)
        self.inc_dirs = list(dict.fromkeys(args.include_dirs + found))
        self.command_files = args.command_files
        self.search = LibrarySearch(self._library_factory(args.libdirs, ignore) if args.libdirs else None)

    def unavailable_reason(self) -> str | None:
        """Report why slang can't be used; None if it can."""
        return self.tool.unavailable_reason()

    def extra_inputs(self) -> list[str]:
        """Command files are inputs too."""
        return [f"{len(self.command_files)} command file(s)"] if self.command_files else []

    def units(self, files: list[str]) -> dict[str, str]:
        """Return the modules the inputs define.  With no tops, ``-y`` would serve unused modules too."""
        if self._units is None:
            self._units = self._read_units(files)
        return self._units

    def _read_units(self, files: list[str]) -> dict[str, str]:
        if not files and not self.command_files:
            return {}
        result = self._parse(files, [], library=False)
        self._modules = {name.lower(): (name, params) for name, params in result["parameter_names"].items()}
        return {d["mod_name"].lower(): f["file_name"] for f in result["files"] for d in f["defs"]}

    def has_library(self) -> bool:
        """Report whether ``-y`` holds anything for slang."""
        return bool(self.search)

    def provide(self, name: str, files: list[str]) -> bool:
        """Add the ``-y`` file defining *name*.  What it needs in turn comes when it's parsed."""
        if not self.search.has(name):
            return False
        self.search.pull(name, files)
        self._units = None  # Even if it was there already, since parsing pulls files in too.
        return True

    def _parse(self, files: list[str], tops: list[str], params: dict[str, str] | None = None, *, library: bool = True) -> dict:
        """Parse, then pull missing definitions from ``-y`` until nothing new resolves.

        *files* and the include dirs grow in place.  Without *library*, ``-y`` isn't searched.
        """
        options = {"defines": self.defines, "tops": tops, "params": params, "command_files": self.command_files, "frontend_info": True}
        hierarchy = self.tool.parse(files, self.inc_dirs, **options)
        for _ in range(_MAX_LIB_ROUNDS):
            if not library or not self._pull_missing(hierarchy, files):
                break
            hierarchy = self.tool.parse(files, self.inc_dirs, **options)
        return hierarchy

    def _pull_missing(self, hierarchy: dict, files: list[str]) -> bool:
        """Add what ``-y`` has for what *hierarchy* misses, except what sources in another language define."""
        if not self.search:
            return False
        missing = hierarchy["missing"]
        wanted = {**missing, "modules": {n: u for n, u in missing["modules"].items() if n.lower() not in self._defined_elsewhere}}
        loaded = [f["file_name"] for f in hierarchy["files"]]
        found, incs = resolve_missing(wanted, self.search.library, loaded, self.inc_dirs)
        for name, (path, others) in found.items():
            if str(path) not in files:
                files.append(str(path))
            self.search.record(name, path, others)
        for include, directory in incs.items():
            if str(directory) not in self.inc_dirs:
                self.inc_dirs.append(str(directory))
            self.search.record(include, directory)
        return bool(found or incs)

    def _module_name(self, name: str) -> str:
        """Return the spelling slang needs.  VHDL names reach this frontend in lowercase."""
        return self._modules.get(name.lower(), (name, []))[0]

    def _overrides(self, root: Root, notes: list[str]) -> dict[str, str]:
        """Match *root*'s values to the module's parameters; slang would ignore the others."""
        module, declared = self._modules.get(root.name.lower(), (root.name, []))
        by_lower = {name.lower(): name for name in declared}
        params = {}
        for name, value in root.generics:
            if name.lower() in by_lower:
                params[by_lower[name.lower()]] = value
            else:
                notes.append(f"{module}: has no parameter {name}")
        return params

    def _elaborate_settings(self, files: list[str], roots: list[Root], notes: list[str]) -> tuple[list[dict], set[str]]:
        """Compile once per setting, because ``-G`` applies to a whole compilation.

        Returns the results and the modules no setting could elaborate.
        """
        results: list[dict] = []
        failed: set[str] = set()
        worked: set[str] = set()
        done: set[tuple] = set()
        for root in roots:
            module = self._module_name(root.name)
            params = self._overrides(root, notes)
            key = (module, tuple(sorted(params.items())))
            if key in done:
                continue
            done.add(key)
            result = self._parse(files, [module], params)
            if module in result["invalid_tops"]:
                failed.add(module)
            else:
                worked.add(module)
                results.append(result)
        return results, failed - worked

    def _elaborate_auto(self, files: list[str], plain: list[str], set_elsewhere: set[str]) -> tuple[dict | None, set[str]]:
        """Elaborate what slang picks as tops, leaving out modules only used with given values."""
        result = self._parse(files, [])
        # slang silently skips modules that need parameters when it picks its own tops.
        rejected = set(self._parse(files, plain)["invalid_tops"]) & set(plain) if plain else set()
        if set_elsewhere - set(plain):
            tops = [t for t in result["elaborated_tops"] if t not in set_elsewhere or t in plain]
            result = self._parse(files, tops) if tops else None
        return result, rejected

    def _elaborate_plain(self, files: list[str], roots: list[Root], plain: list[str]) -> tuple[dict | None, set[str]]:
        """Elaborate roots without values, dropping those that can't be elaborated on their own.

        With none left there is no result: no tops would make slang elaborate everything.
        """
        rejected: set[str] = set()
        required = {self._root_name(r) for r in roots if r.requested}
        optional = set(plain) - required
        result = self._parse(files, plain)
        while set(result["invalid_tops"]) & optional:
            rejected |= set(result["invalid_tops"]) & optional
            optional -= rejected
            remaining = [n for n in plain if n not in rejected]
            if not remaining:
                return None, rejected
            result = self._parse(files, remaining)
        return result, rejected

    def _root_name(self, root: Root) -> str:
        """A name the user typed stays as it is, so slang reports it if it's wrong."""
        return root.name if root.requested else self._module_name(root.name)

    def elaborate(self, files: list[str], request: Request) -> dict:
        """Elaborate the roots, using the parameter values that come with them."""
        if not (files or self.command_files) or not (request.roots or request.auto):
            return empty_fragment()  # --top names only other languages' units so far
        self._defined_elsewhere = request.defined_elsewhere
        if any(not r.requested for r in request.roots):
            self.units(files)  # To match the names and values other languages use.
        plain = [self._root_name(r) for r in request.roots if not r.generics]
        settings = [r for r in request.roots if r.generics]
        notes: list[str] = []
        results, rejected = self._elaborate_settings(files, settings, notes)
        set_elsewhere = {self._module_name(r.name) for r in settings}
        worked = set_elsewhere - rejected
        base: dict | None = None
        if request.auto:
            base, plain_rejected = self._elaborate_auto(files, plain, set_elsewhere)
        elif plain:
            base, plain_rejected = self._elaborate_plain(files, request.roots, plain)
        else:
            plain_rejected = set()
        rejected |= plain_rejected - worked
        if base is not None:
            results.insert(0, base)
        result = results[0] if results else _nothing_elaborated(self._parse(files, [], library=False))
        for other in results[1:]:
            _merge_results(result, other)
        if request.auto:
            result["needed"] = None
        else:
            # Instantiated, so still needed, even though slang can't elaborate them on their own.
            units = self.units(files) if rejected else {}
            result["needed"] = list(result["needed"] or []) + [units[n.lower()] for n in sorted(rejected) if n.lower() in units]
        return self._fragment(result, rejected, notes)

    def _fragment(self, result: dict, rejected: set[str], notes: list[str]) -> dict:
        """Build the result shape core expects from a merged result."""
        result["unresolved"] = result.pop("blackbox_params")
        for name in result["missing"]["modules"]:
            result["unresolved"].setdefault(name, [])
        result["rejected_roots"] = sorted(rejected)
        result["notes"] = notes
        result["pulled"] = self.search.pulled
        result["inc_dirs"] = list(dict.fromkeys(self.inc_dirs + result["inc_dirs"]))
        result.pop("invalid_tops")
        return result

    def drop_resolved(self, fragment: dict, names: set[str]) -> None:
        """Remove slang's "unknown module" errors for modules another language defines."""
        for name in names:
            fragment["missing"]["modules"].pop(name, None)
        fragment["diagnostics"] = [
            d for d in fragment["diagnostics"]
            if not any(f"unknown module '{name}'" in d["message"] for name in names)
        ]
