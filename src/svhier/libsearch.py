# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Stefan Engert

"""Resolve missing definitions from library directories (``-y DIR``).

Like a simulator's ``-y`` option, library directories are not compiled as a whole.
They are indexed up front, and only the files that define a module, interface or
package that is otherwise missing are pulled into the compilation.  Missing
`` `include`` files are resolved the same way by adding the include directory they
are found under.
"""

import re
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

_DEF_RE = re.compile(r"^\s*(?:module|macromodule|interface|package)\s+(?:(?:automatic|static)\s+)?(\w+)", re.M)

# Kinds from parse_files()["missing"] that a library file can provide.
_DEF_KINDS = ("modules", "interfaces", "packages")


def is_ignored(p: Path, ignore: set[str]) -> bool:
    """Return whether any component of *p* contains one of the lowercase names in *ignore*."""
    return bool(ignore) and any(name in part.lower() for part in p.parts for name in ignore)


def glob_files(p: Path, recursive: bool, exts: tuple[str, ...], ignore: set[str]) -> list[Path]:
    """Return the files in directory *p* matching the patterns *exts*, leaving out ignored ones."""
    prefix = "**/" if recursive else ""
    return [f for ext in exts for f in sorted(p.glob(f"{prefix}{ext}")) if not is_ignored(f, ignore)]


class Library(Protocol):
    """What a language plugin knows about the files under ``-y``."""

    def __bool__(self) -> bool:
        """Return whether the directories hold anything for this language."""

    def find(self, name: str, scope: str = "") -> tuple[list[Path], int]:
        """Return the files for *name* in any case, and how many other files also define it.

        *scope* is the library a language refers to it through, if it has such a thing.
        """


#: Builds a :class:`Library` from the ``-y`` directories and the lowercase names to ignore.
LibraryFactory = Callable[[list[str], set[str]], Library]


class LibrarySearch:
    """Pulls files from a :class:`Library` into a compilation, and records what it pulled."""

    def __init__(self, library: Library | None = None) -> None:
        self.library = library
        self.pulled: list[tuple[str, Path, int]] = []

    def __bool__(self) -> bool:
        return bool(self.library)

    def has(self, name: str) -> bool:
        """Return whether the library defines *name*."""
        return bool(self.library and self.library.find(name)[0])

    def pull(self, name: str, files: list[str], scope: str = "") -> bool:
        """Add the files defining *name* to *files*; return whether that added any."""
        found, others = self.library.find(name, scope) if self.library else ([], 0)
        have = {Path(f).resolve() for f in files}
        new = [p for p in found if p.resolve() not in have]
        if not new:
            return False
        files.extend(str(p) for p in new)
        self.record(name, found[0], others)
        return True

    def record(self, name: str, path: Path, others: int = 0) -> None:
        """Note that *path* was pulled in for *name*."""
        self.pulled.append((name, path, others))


class LibraryIndex:
    """Index of the definitions and headers found under a set of library directories."""

    def __init__(self, dirs: list[str], ignore: set[str] | None = None):
        ign = ignore or set()
        self.defs: dict[str, list[Path]] = {}
        self.headers: list[Path] = []
        for d in dirs:
            root = Path(d)
            for ext in ("*.sv", "*.v"):
                for f in sorted(root.glob(f"**/{ext}")):
                    if is_ignored(f, ign):
                        continue
                    try:
                        text = f.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        continue
                    for name in dict.fromkeys(_DEF_RE.findall(text)):
                        self.defs.setdefault(name, []).append(f)
            for ext in ("*.svh", "*.vh"):
                self.headers.extend(f for f in sorted(root.glob(f"**/{ext}")) if not is_ignored(f, ign))

    @staticmethod
    def _rank(name: str, path: Path) -> tuple:
        """Sort key: prefer ``name.sv``, then files whose stem prefixes *name*, then shorter paths."""
        stem = path.stem
        return (stem != name, not name.startswith(stem), len(path.parts), str(path))

    def find_definition(self, name: str) -> list[Path]:
        """Return the files defining *name*, best candidate first."""
        return sorted(self.defs.get(name, []), key=lambda p: self._rank(name, p))

    def __bool__(self) -> bool:
        return bool(self.defs or self.headers)

    def find(self, name: str, scope: str = "") -> tuple[list[Path], int]:  # pylint: disable=unused-argument
        """Implement :class:`Library`, for names from case-insensitive languages.  SV has no scope."""
        lowered = name.lower()
        spellings = [n for n in self.defs if n.lower() == lowered]
        candidates = sorted((p for n in spellings for p in self.defs[n]), key=lambda p: self._rank(spellings[0], p))
        return candidates[:1], max(len(candidates) - 1, 0)

    def find_include_dir(self, include: str) -> Path | None:
        """Return the directory *include* (e.g. ``"sub/regs.svh"``) resolves from, if any header matches."""
        parts = Path(include).parts
        if not parts or Path(include).is_absolute() or ".." in parts:
            return None
        matches = [h for h in self.headers if h.parts[-len(parts):] == parts and len(h.parts) > len(parts)]
        if not matches:
            return None
        best = min(matches, key=lambda h: (len(h.parts), str(h)))
        return best.parents[len(parts) - 1]


def resolve_missing(
    missing: dict[str, dict[str, list[str]]],
    index: LibraryIndex,
    have_files: list[str],
    have_inc_dirs: list[str],
) -> tuple[dict[str, tuple[Path, int]], dict[str, Path]]:
    """Look up the names in *missing* in *index*.

    Returns ``(files, inc_dirs)``: ``files`` maps each resolved definition name to
    ``(chosen file, number of other candidates)``, ``inc_dirs`` maps each resolved
    include to the directory to add.  Files and include dirs that are already part
    of the compilation are not returned again.
    """
    have = {Path(f).resolve() for f in have_files}
    have_dirs = {Path(d).resolve() for d in have_inc_dirs}

    files: dict[str, tuple[Path, int]] = {}
    for kind in _DEF_KINDS:
        for name in missing.get(kind, {}):
            candidates = index.find_definition(name)
            if not candidates or any(c.resolve() in have for c in candidates):
                continue
            files[name] = (candidates[0], len(candidates) - 1)

    inc_dirs: dict[str, Path] = {}
    for include in missing.get("includes", {}):
        d = index.find_include_dir(include)
        if d is not None and d.resolve() not in have_dirs:
            inc_dirs[include] = d
    return files, inc_dirs
