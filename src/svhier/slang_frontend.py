# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Stefan Engert

"""SPIKE: the SystemVerilog frontend, registered like any other plugin.

Imports pyslang only when it runs, through :mod:`svhier.parser`, so svhier starts without it.
"""

import importlib.util

from svhier.frontends import Request, empty_fragment
from svhier.libsearch import LibraryIndex, resolve_missing

# Upper bound on parse/resolve rounds for -y; each round pulls in one more layer of dependencies.
_MAX_LIB_ROUNDS = 20


class SlangFrontend:
    """Elaborate SystemVerilog with slang, including ``-f``, ``-I``, ``-D`` and ``-y``."""

    name = "slang"
    suffixes = (".sv", ".v")
    catch_all = True

    def __init__(self) -> None:
        self.defines: list[str] = []
        self.inc_dirs: list[str] = []
        self.command_files: list = []
        self.index: LibraryIndex | None = None
        self.pulled: list = []
        self._units: dict[str, str] | None = None

    def add_arguments(self, parser) -> None:
        """The SV options still live in the CLI's parser (spike shortcut)."""

    def configure(self, args) -> None:
        """Read the SV options."""
        ignore = {n.lower() for n in args.ignore} if args.ignore else set()
        self.defines = args.defines
        self.inc_dirs = list(args.include_dirs)
        self.command_files = args.command_files
        self.index = LibraryIndex(args.libdirs, ignore) if args.libdirs else None

    def unavailable_reason(self) -> str | None:
        """Report if pyslang is missing."""
        return None if importlib.util.find_spec("pyslang") else "pyslang is not installed"

    def has_inputs(self, files: list[str]) -> bool:
        """Command files count as inputs too."""
        return bool(files or self.command_files)

    def _parse(self, files: list[str], tops: list[str]) -> dict:
        """Parse, then pull missing definitions from ``-y`` until nothing new resolves.

        *files* and the include dirs grow in place.
        """
        from svhier.parser import parse_files  # pylint: disable=import-outside-toplevel

        options = {"defines": self.defines, "tops": tops, "command_files": self.command_files, "frontend_info": True}
        hierarchy = parse_files(files, self.inc_dirs, **options)
        for _ in range(_MAX_LIB_ROUNDS):
            if self.index is None:
                break
            loaded = [f["file_name"] for f in hierarchy["files"]]
            found, incs = resolve_missing(hierarchy["missing"], self.index, loaded, self.inc_dirs)
            if not found and not incs:
                break
            for name, (path, others) in found.items():
                if str(path) not in files:
                    files.append(str(path))
                self.pulled.append((name, path, others))
            for include, directory in incs.items():
                if str(directory) not in self.inc_dirs:
                    self.inc_dirs.append(str(directory))
                self.pulled.append((include, directory, 0))
            hierarchy = parse_files(files, self.inc_dirs, **options)
        return hierarchy

    def units(self, files: list[str]) -> dict[str, str]:
        """Return the modules the inputs define."""
        if self._units is None:
            result = self._parse(files, [])
            self._units = {d["mod_name"].lower(): f["file_name"] for f in result["files"] for d in f["defs"]}
        return self._units

    def elaborate(self, files: list[str], request: Request) -> dict:
        """Elaborate the roots.  slang can't take parameter values from another language here."""
        if not self.has_inputs(files):
            return empty_fragment()
        wanted = [r.name for r in request.roots]
        if not wanted and not request.auto:
            return empty_fragment()  # --top names only other languages' units so far
        if request.auto:
            result = self._parse(files, [])
            # slang silently skips modules that need parameters when it picks its own tops.
            rejected = set(self._parse(files, wanted)["invalid_tops"]) & set(wanted) if wanted else set()
            result["needed"] = None
        else:
            rejected: set[str] = set()
            required = [r.name for r in request.roots if r.requested]
            result = self._parse(files, wanted)
            optional = set(wanted) - set(required)
            while set(result["invalid_tops"]) & optional:
                rejected |= set(result["invalid_tops"]) & optional
                optional -= rejected
                result = self._parse(files, [n for n in wanted if n not in rejected])
            # Instantiated, so still needed, even though slang can't elaborate them on their own.
            # Only when needed: units() parses with no top, which pulls in files for unused modules.
            units = self.units(files) if rejected else {}
            result["needed"] = list(result["needed"] or []) + [units[n.lower()] for n in sorted(rejected) if n.lower() in units]
        result["unresolved"] = result.pop("blackbox_params")
        for name in result["missing"]["modules"]:
            result["unresolved"].setdefault(name, [])
        result["rejected_roots"] = sorted(rejected)
        result["pulled"] = self.pulled
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
