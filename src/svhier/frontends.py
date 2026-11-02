# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Stefan Engert

"""SPIKE: every source language is a plugin; this module knows none of them.

A frontend is an object registered in the ``svhier.frontends`` entry point group, including
the bundled slang one.  :func:`stitch` runs the frontends in turn: whatever one can't
resolve and another one defines becomes a root of the other, until nothing changes.  The
merged result has the :func:`svhier.parser.parse_files` shape, so the tree, YAML, tables
and filelist don't need to know which language a definition came from.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from typing import Protocol

ENTRY_POINT_GROUP = "svhier.frontends"
_MAX_ROUNDS = 20
_MISSING_KINDS = ("modules", "interfaces", "packages", "includes", "macros")
# Suffix -> what to install, for files no enabled frontend reads.
KNOWN_SUFFIXES = {".vhd": "svhier-ghdl", ".vhdl": "svhier-ghdl", ".sv": "pyslang", ".v": "pyslang"}


@dataclass(frozen=True)
class Root:
    """A unit to elaborate.  *requested* means the user named it, so failing is an error.

    *generics* are ``(name, value)`` pairs.  Names are spelled as the instantiating language
    has them; the frontend matches them to its own.  Values use one notation for every
    language: ``6``, ``-4``, ``1'b1``, ``4'b1010``, ``2.5E+00`` or ``"text"``.
    """

    name: str
    generics: tuple[tuple[str, str], ...] = ()
    requested: bool = False


@dataclass
class Request:
    """What a frontend should elaborate.

    With *auto* (no ``--top``), the frontend also finds its own roots; *roots* are then the
    units other languages instantiate, possibly with parameter values.
    """

    auto: bool = False
    roots: list[Root] = field(default_factory=list)


class Frontend(Protocol):
    """What a plugin implements.  Must not raise for bad designs; report in ``diagnostics``.

    :meth:`elaborate` returns a ``parse_files``-shaped dict (see :func:`empty_fragment`) with
    these extra keys:

    * ``unresolved`` — ``{name: [{"generics": {...}, "unresolved": [text]}]}`` for every
      instantiated name the frontend has no definition for.  ``generics`` hold values in
      :class:`Root`'s notation; ``unresolved`` describes the ones that have no such form.
    * ``rejected_roots`` — roots that can't be elaborated with what is known about them.
    * ``notes`` — remarks for the user.
    """

    name: str
    suffixes: tuple[str, ...]
    catch_all: bool  # Gets explicitly named files with an unknown suffix, and unknown tops.

    def add_arguments(self, parser) -> None:
        """Add plugin options to the ``svhier`` argument parser."""

    def configure(self, args) -> None:
        """Read the parsed command line, including the core's ``paths``, ``recursive``, ``ignore`` and ``libdirs``."""

    def unavailable_reason(self) -> str | None:
        """Return why the plugin can't run (for example, tool not installed), or ``None``."""

    def extra_inputs(self) -> list[str]:
        """Describe inputs other than files, from the plugin's own options, such as ``2 command file(s)``."""

    def units(self, files: list[str]) -> dict[str, str]:
        """Return ``{lowercase unit name: file}`` for what the inputs define."""

    def elaborate(self, files: list[str], request: Request) -> dict:
        """Elaborate what *request* asks for."""

    def drop_resolved(self, fragment: dict, names: set[str]) -> None:
        """Remove the complaints in *fragment* about *names*, which another frontend defines."""


def discover_frontends(on_error: Callable[[str, Exception], None] | None = None) -> list[Frontend]:
    """Load every registered plugin.  A plugin that fails to load must not break svhier."""
    found: list[Frontend] = []
    for entry in entry_points(group=ENTRY_POINT_GROUP):
        try:
            found.append(entry.load()())
        except Exception as exc:  # pylint: disable=broad-exception-caught
            if on_error:
                on_error(entry.name, exc)
    return found


def empty_fragment() -> dict:
    """Return an elaboration result with nothing in it."""
    return {
        "files": [], "duplicates": [], "tops": [], "needed": None, "defines": [], "inc_dirs": [],
        "diagnostics": [], "has_errors": False, "unresolved": {}, "rejected_roots": [], "notes": [],
        "missing": {kind: {} for kind in _MISSING_KINDS},
    }


def _add_unique(target: list, items) -> None:
    target.extend(item for item in items if item not in target)


class _Stitcher:  # pylint: disable=too-few-public-methods
    """Runs all frontends in turn until none of them finds anything new."""

    def __init__(self, inputs: list[tuple[Frontend, list[str]]], tops: list[str]):
        self.inputs = inputs
        self.tops = tops
        self.units = {id(fe): fe.units(files) for fe, files in inputs}
        catch_all = next((fe for fe, _ in inputs if fe.catch_all), inputs[0][0])
        self.requests = {id(fe): Request(auto=not tops) for fe, _ in inputs}
        for top in tops:
            owner = next((fe for fe, _ in inputs if top.lower() in self.units[id(fe)]), catch_all)
            self.requests[id(owner)].roots.append(Root(top, requested=True))
        self.fragments: dict[int, dict] = {}
        self.notes: list[str] = []

    def _route(self, source: Frontend, fragment: dict) -> bool:
        """Turn what *source* can't resolve into roots of the frontends that define it."""
        changed = False
        for name, uses in fragment["unresolved"].items():
            for other, _ in self.inputs:
                if other is source or name.lower() not in self.units[id(other)]:
                    continue
                for use in uses or [{"generics": {}, "unresolved": []}]:
                    for param in use["unresolved"]:
                        _add_unique(self.notes, [f"{name}: can't pass parameter {param} to {other.name}, so its default was used"])
                    root = Root(name, tuple(sorted(use["generics"].items())))
                    roots = self.requests[id(other)].roots
                    if not any(r.name.lower() == name.lower() and r.generics == root.generics for r in roots):
                        roots.append(root)
                        changed = True
        return changed

    def run(self) -> dict:
        """Iterate to a fixed point and return the merged result."""
        done: dict[int, list[Root]] = {}
        for _ in range(_MAX_ROUNDS):
            changed = False
            for frontend, files in self.inputs:
                key = id(frontend)
                if done.get(key) != self.requests[key].roots:
                    done[key] = list(self.requests[key].roots)
                    self.fragments[key] = frontend.elaborate(files, self.requests[key])
            for frontend, _ in self.inputs:
                changed = self._route(frontend, self.fragments[id(frontend)]) or changed
            if not changed:
                break
        return self._merge()

    def _merge(self) -> dict:
        """Combine all fragments and resolve names across languages."""
        merged = empty_fragment()
        for frontend, _ in self.inputs:
            fragment = self.fragments[id(frontend)]
            others = {name for fe, _ in self.inputs if fe is not frontend for name in self.units[id(fe)]}
            frontend.drop_resolved(fragment, {n for n in fragment["unresolved"] if n.lower() in others})
            merged["files"].extend(fragment["files"])
            merged["duplicates"].extend(fragment["duplicates"])
            merged["diagnostics"].extend(fragment["diagnostics"])
            _add_unique(merged["defines"], fragment["defines"])
            _add_unique(merged["inc_dirs"], fragment["inc_dirs"])
            _add_unique(self.notes, fragment.get("notes", []))
            for name in fragment["rejected_roots"]:
                _add_unique(self.notes, [f"{name}: its parameters aren't known from the instantiating language, so its children aren't shown"])
            for kind in _MISSING_KINDS:
                for name, users in fragment["missing"][kind].items():
                    _add_unique(merged["missing"][kind].setdefault(name, []), users)
            merged.setdefault("pulled", []).extend(fragment.get("pulled", []))
            if self.tops:
                merged["needed"] = (merged["needed"] or []) + list(fragment["needed"] or [])

        # Names are case-insensitive in VHDL, so two languages may spell one name differently.
        names = {d["mod_name"].lower(): d["mod_name"] for f in merged["files"] for d in f["defs"]}
        for entry in merged["files"]:
            for unit in entry["defs"]:
                for inst in unit["insts"]:
                    inst["mod_name"] = names.get(inst["mod_name"].lower(), inst["mod_name"])
        merged["tops"] = [names.get(t.lower(), t) for t in self.tops]
        merged["has_errors"] = any(d["severity"] == "error" for d in merged["diagnostics"])
        merged["frontend_notes"] = self.notes
        return merged


def stitch(inputs: list[tuple[Frontend, list[str]]], tops: list[str]) -> dict:
    """Elaborate *inputs*, ``(frontend, files)`` pairs, into one hierarchy."""
    if len(inputs) == 1:
        # One language: nothing to stitch, and no extra work to find out what it defines.
        frontend, files = inputs[0]
        result = frontend.elaborate(files, Request(auto=not tops, roots=[Root(t, requested=True) for t in tops]))
        result["frontend_notes"] = result.get("notes", [])
        return result
    return _Stitcher(inputs, tops).run()
