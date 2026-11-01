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
from pathlib import Path

_DEF_RE = re.compile(r"^\s*(?:module|macromodule|interface|package)\s+(?:(?:automatic|static)\s+)?(\w+)", re.M)

# Kinds from parse_files()["missing"] that a library file can provide.
_DEF_KINDS = ("modules", "interfaces", "packages")


def _is_ignored(p: Path, ignore: set[str]) -> bool:
    return bool(ignore) and any(name in part.lower() for part in p.parts for name in ignore)


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
                    if _is_ignored(f, ign):
                        continue
                    try:
                        text = f.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        continue
                    for name in dict.fromkeys(_DEF_RE.findall(text)):
                        self.defs.setdefault(name, []).append(f)
            for ext in ("*.svh", "*.vh"):
                self.headers.extend(f for f in sorted(root.glob(f"**/{ext}")) if not _is_ignored(f, ign))

    @staticmethod
    def _rank(name: str, path: Path) -> tuple:
        """Sort key: prefer ``name.sv``, then files whose stem prefixes *name*, then shorter paths."""
        stem = path.stem
        return (stem != name, not name.startswith(stem), len(path.parts), str(path))

    def find_definition(self, name: str) -> list[Path]:
        """Return the files defining *name*, best candidate first."""
        return sorted(self.defs.get(name, []), key=lambda p: self._rank(name, p))

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
