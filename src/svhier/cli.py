# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Stefan Engert

"""Command-line interface for svhier: parse SV hierarchies and emit YAML."""

import argparse
import os
import sys
from collections.abc import Iterator
from io import StringIO
from pathlib import Path
from typing import NamedTuple

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table
from ruamel.yaml import YAML

from svhier.filelist import compute_filelist
from svhier.frontends import KNOWN_SUFFIXES, Frontend, defined_names, discover_frontends, stitch
from svhier.libsearch import glob_files, is_ignored

# Rich consoles: stderr for all diagnostics so stdout stays clean for data
_err = Console(stderr=True)

# Per-file keys returned by parse_files that are bookkeeping only, not YAML output.
_INTERNAL_FILE_KEYS = {"pkgs", "ifaces", "refs", "shadowed", "depends_on"}
# Likewise per definition: the children per parameter setting, which only the tree uses.
_INTERNAL_DEF_KEYS = {"variants", "top_variant"}


def _prepare_for_yaml(hierarchy: dict) -> dict:
    """Return a copy of *hierarchy* with internal-only keys removed.

    Strips the ``pkgs``, ``ifaces``, ``refs`` and ``shadowed`` keys from each file entry (bookkeeping only) and
    the ``variants`` and ``top_variant`` keys from module defs, and omits ``pkg_imports`` from module defs when
    the list is empty.
    """
    files = []
    for entry in hierarchy["files"]:
        defs = []
        for d in entry["defs"]:
            def_out = {k: v for k, v in d.items() if k not in _INTERNAL_DEF_KEYS and not (k == "pkg_imports" and not v)}
            defs.append(def_out)
        file_out = {k: v for k, v in entry.items() if k not in _INTERNAL_FILE_KEYS}
        file_out["defs"] = defs
        files.append(file_out)
    return {"files": files}


def collect_source_files(paths: list[str], recursive: bool, suffixes: tuple[str, ...], ignore: set[str] | None = None) -> list[str]:
    """Expand a mix of files and directories into a deduplicated file list.

    Files are kept whatever their suffix.  Directories are searched for files with one of
    *suffixes*; when *recursive* is ``True`` the search descends into subdirectories.
    Duplicate paths (by resolved canonical path) are silently dropped.  Paths that are
    neither a file nor a directory emit a warning to stderr and are skipped.
    """
    ign = ignore or set()
    result: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_file():
            if not is_ignored(p, p.parent, ign):
                result.append(p)
        elif p.is_dir():
            result.extend(glob_files(p, recursive, tuple(f"*{suffix}" for suffix in suffixes), ign))
        else:
            _err.print(f"[yellow]warning:[/yellow] {raw!r} is not a file or directory, skipping")
    seen: set[Path] = set()
    unique: list[str] = []
    for p in result:
        resolved = p.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(str(p))
    return unique


def _print_summary(hierarchy: dict) -> None:
    """Print a Rich table summarising the parsed hierarchy to stderr."""
    files = hierarchy["files"]
    total_mods = sum(len(f["defs"]) for f in files)
    total_insts = sum(len(d["insts"]) for f in files for d in f["defs"])
    total_pkgs = sum(len(f["pkgs"]) for f in files)

    table = Table(title="Hierarchy summary", show_header=True, header_style="bold cyan")
    table.add_column("File", style="dim")
    table.add_column("Modules", justify="right")
    table.add_column("Packages", justify="right")
    table.add_column("Total instances", justify="right")

    for entry in files:
        n_mods = len(entry["defs"])
        n_pkgs = len(entry["pkgs"])
        n_insts = sum(len(d["insts"]) for d in entry["defs"])
        table.add_row(Path(entry["file_name"]).name, str(n_mods), str(n_pkgs), str(n_insts))

    table.add_section()
    table.add_row("[bold]Total[/bold]", f"[bold]{total_mods}[/bold]",
                  f"[bold]{total_pkgs}[/bold]", f"[bold]{total_insts}[/bold]")
    _err.print(table)


def _print_diagnostics(hierarchy: dict, *, errors: bool = False, warnings: bool = False) -> None:
    """Report the plugins' diagnostics on stderr.

    By default only a one-line count is printed.  With *errors* every error is
    listed; with *warnings* every error and warning is listed.
    """
    diags = hierarchy.get("diagnostics", [])
    n_err = sum(1 for d in diags if d["severity"] == "error")
    n_warn = len(diags) - n_err
    if not errors and not warnings:
        if n_err or n_warn:
            parts = [f"{n} {name}{'s' if n != 1 else ''}" for n, name in ((n_err, "error"), (n_warn, "warning")) if n]
            _err.print(f"[dim]reported {', '.join(parts)} (use -E to list errors, -W to also list warnings)[/dim]")
        return
    for d in diags:
        if d["severity"] == "error":
            tag = "[bold red]error[/bold red]"
        elif warnings:
            tag = "[bold yellow]warning[/bold yellow]"
        else:
            continue
        location = f"[dim]{d['file']}[/dim]: " if d["file"] else ""
        _err.print(f"{tag}: {location}{d['message']}")


_MISSING_LABELS = {
    "modules": "blackbox module",
    "interfaces": "interface",
    "packages": "package",
    "units": "unit",
    "includes": "include",
    "macros": "macro",
}


def _first_and_more(files: list[str]) -> str:
    """Return the base name of the first of *files*, followed by how many others there are."""
    if not files:
        return ""
    return Path(files[0]).name + (f" (+{len(files) - 1} more)" if len(files) > 1 else "")


def _print_missing(hierarchy: dict) -> None:
    """Print a table of everything that could not be found to stderr.

    Unknown modules are reported as blackboxes; packages, interfaces, include files
    and macros that could not be found follow.  Nothing is printed when the design
    is complete.
    """
    missing = hierarchy.get("missing", {})
    if not any(missing.values()):
        return
    table = Table(title="Not found", show_header=True, header_style="bold cyan")
    table.add_column("Kind", style="yellow")
    table.add_column("Name", style="bold")
    table.add_column("Referenced in", style="dim", overflow="fold")
    for kind, label in _MISSING_LABELS.items():
        for name, files in missing.get(kind, {}).items():
            table.add_row(label, name, _first_and_more(files))
    _err.print(table)


def _print_duplicates(hierarchy: dict) -> None:
    """Print a table of module, interface and package names defined in more than one file."""
    duplicates = hierarchy.get("duplicates", [])
    if not duplicates:
        return
    table = Table(title="Defined more than once", show_header=True, header_style="bold cyan")
    table.add_column("Name", style="bold")
    table.add_column("Kind", style="yellow")
    table.add_column("Used", style="green", overflow="fold")
    table.add_column("Ignored", style="dim", overflow="fold")
    for dup in duplicates:
        ignored = [f for f in dup["files"] if f != dup["used"]]
        table.add_row(dup["name"], dup["kind"], dup["used"], _first_and_more(ignored))
    _err.print(table)


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


class TreeLine(NamedTuple):
    """One line of the instance tree."""

    guide: str  # What to draw left of the name; empty for a root.
    name: str  # The top's name, or the instance path relative to the parent.
    module: str | None = None  # None for a root.
    marker: str | None = None  # "blackbox" or "cycle".


class _Level(NamedTuple):
    """A module being expanded, as the walk through the tree goes through its children."""

    key: tuple[str, object]  # (module, setting); the setting is None for the merged children.
    children: Iterator[tuple[int, dict]]
    last: int
    indent: str


class TreeGuides(NamedTuple):
    """What to draw for a child and its last sibling, and below each of them."""

    tee: str = "├── "
    elbow: str = "└── "
    pipe: str = "│   "
    gap: str = "    "


_ASCII_GUIDES = TreeGuides("+-- ", "`-- ", "|   ")


def _select_children(unit: dict, variant: object) -> tuple[object, list[dict]]:
    """Return which setting of the definition *unit* to show, and its children.

    An instance that doesn't say which setting it uses can only be exact if there is just
    one.  Otherwise, or for a plugin with no ``variants``, it gets the merged ``insts``, and
    the setting returned is ``None``.
    """
    variants = unit.get("variants", {})
    if variant is None and len(variants) == 1:
        variant = next(iter(variants))
    return (variant, variants[variant]) if variant in variants else (None, unit["insts"])


def _iter_subtree(defs: dict[str, dict], top: str, guides: TreeGuides) -> Iterator[TreeLine]:
    """Yield the lines below *top*, walking with a stack so that depth doesn't matter."""

    def level(name: str, variant: object, indent: str) -> _Level:
        variant, children = _select_children(defs[name], variant)
        return _Level((name, variant), iter(enumerate(children)), len(children) - 1, indent)

    stack = [level(top, defs[top].get("top_variant"), "")]
    expanding = {stack[0].key}
    while stack:
        current = stack[-1]
        for pos, inst in current.children:
            child = inst["mod_name"]
            is_last = pos == current.last
            guide = current.indent + (guides.elbow if is_last else guides.tee)
            if child not in defs:
                yield TreeLine(guide, inst["inst_name"], child, "blackbox")
                continue
            below = level(child, inst.get("variant"), current.indent + (guides.gap if is_last else guides.pipe))
            if below.key in expanding:
                yield TreeLine(guide, inst["inst_name"], child, "cycle")
                continue
            yield TreeLine(guide, inst["inst_name"], child)
            expanding.add(below.key)
            stack.append(below)
            break
        else:
            expanding.remove(current.key)
            stack.pop()


def iter_tree(hierarchy: dict, tops: list[str] | None = None, guides: TreeGuides = TreeGuides()) -> Iterator[TreeLine]:
    """Yield the instance tree of *hierarchy* line by line, one tree per top-level module.

    When *tops* is omitted, the roots are all modules that no other module instantiates.
    Each instance shows the children of its own parameter setting (see
    :func:`_select_children` for when that isn't known).  Instances of modules without a
    definition are marked ``blackbox``; an instance of what it's already inside of, with
    the same setting, is cut off and marked ``cycle``.

    It costs one step per instance in the tree and builds nothing up front, so a large
    design starts printing at once.
    """
    defs = {d["mod_name"]: d for f in hierarchy["files"] for d in f["defs"]}
    if tops is None:
        instantiated = {i["mod_name"] for d in defs.values() for i in d["insts"]}
        tops = [name for name in defs if name not in instantiated]
    for top in tops:
        if top in defs:
            yield TreeLine("", top)
            yield from _iter_subtree(defs, top, guides)


class TreeStyle(NamedTuple):
    """How to draw the tree: the guides, and the ANSI codes around each part of a line."""

    guides: TreeGuides = TreeGuides()
    root: str = ""
    module: str = ""
    blackbox: str = ""
    cycle: str = ""
    end: str = ""


def tree_style() -> TreeStyle:
    """Return the style for stdout.

    Rich decides what the output can show, so ``NO_COLOR``, ``FORCE_COLOR``, ``TERM=dumb``,
    pipes and encodings other than UTF-8 work like in the tables on stderr.
    """
    console = Console()
    guides = _ASCII_GUIDES if console.options.ascii_only else TreeGuides()
    if console.color_system in (None, "windows"):  # "windows" draws through the console API, not with codes.
        return TreeStyle(guides)
    colours = ("", "") if console.no_color else ("\x1b[33m", "\x1b[31m")
    return TreeStyle(guides, "\x1b[1m", "\x1b[2m", *colours, "\x1b[0m")


def render_tree_line(line: TreeLine, style: TreeStyle = TreeStyle()) -> str:
    """Return *line* as text in *style*: ``guides inst (module) (marker)``, without a line end."""
    end = style.end
    if line.module is None:
        return f"{style.root}{line.name}{end}"
    text = f"{line.guide}{line.name} {style.module}({line.module}){end}"
    if line.marker:
        code = style.blackbox if line.marker == "blackbox" else style.cycle
        text += f" {code}({line.marker}){end}" if code else f" ({line.marker})"
    return text


def _warn_unknown_tops(hierarchy: dict, tops: list[str]) -> None:
    """Report each of the requested *tops* that shows no hierarchy: it failed, or nothing defines it."""
    defined = {d["mod_name"] for f in hierarchy["files"] for d in f["defs"]}
    failed = {t.lower() for t in hierarchy.get("failed_tops", [])}
    for top in tops:
        if top.lower() in failed:
            _err.print(f"[red]error:[/red] top module {top!r} could not be elaborated, so nothing below it is shown")
        elif top not in defined:
            _err.print(f"[yellow]warning:[/yellow] top module {top!r} not found")


def _print_tree(hierarchy: dict, tops: list[str] | None) -> None:
    """Print the instance tree(s) to stdout as they are walked, so a large tree shows up at once.

    Rich's ``Tree`` takes minutes on hundreds of thousands of nodes, so this writes plain
    lines.  They are never wrapped or cut off here; a terminal wraps long ones itself.  A
    reader that goes away, such as ``head``, ends the program like it does for Rich: with
    exit code 1 and no traceback.
    """
    style = tree_style()
    try:
        for line in iter_tree(hierarchy, tops, style.guides):
            sys.stdout.write(render_tree_line(line, style) + "\n")
        sys.stdout.flush()
    except BrokenPipeError:
        # Python flushes stdout on exit too, which would complain about the same closed pipe.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(1)


def _make_yaml() -> YAML:
    """Return a configured ruamel.yaml instance."""
    yaml = YAML()
    yaml.default_flow_style = False
    return yaml


def _write_to(content: str, dest: str, label: str) -> None:
    """Write *content* to *dest* (``-`` means stdout); confirm to stderr."""
    if dest == "-":
        sys.stdout.write(content)
    else:
        try:
            Path(dest).write_text(content, encoding="utf-8")
        except OSError as exc:
            _err.print(f"[red]error:[/red] cannot write {label} to [bold]{dest}[/bold]: {exc.strerror}")
            sys.exit(1)
        _err.print(f"[green]✓[/green] {label} written to [bold]{dest}[/bold]")


def _write_yaml(hierarchy: dict, dest: str) -> None:
    """Serialise *hierarchy* as YAML and send it to *dest*."""
    buf = StringIO()
    _make_yaml().dump(_prepare_for_yaml(hierarchy), buf)
    _write_to(buf.getvalue(), dest, "YAML hierarchy")


def _write_filelist(ordered: list[str], inc_dirs: list[str], dest: str, defines: list[str] | None = None) -> None:
    """Write a dependency-ordered filelist to *dest*.

    Include directories and macro definitions (if any) are emitted as ``+incdir+<dir>``
    and ``+define+<macro>`` lines before the source files, as expected by VCS, Questa,
    and Xcelium.
    """
    lines = ["// Generated by svhier — files in compilation order"]
    for d in inc_dirs:
        lines.append(f"+incdir+{d}")
    for macro in defines or []:
        lines.append(f"+define+{macro}")
    lines.extend(ordered)
    lines.append("")
    _write_to("\n".join(lines), dest, f"filelist ({len(ordered)} files)")


def _build_parser(frontends: list[Frontend] | None = None) -> argparse.ArgumentParser:
    """Return the argument parser for the ``svhier`` CLI, with options from *frontends*.

    Without *frontends*, uses the installed ones, so the docs show the bundled plugin's options.
    """
    if frontends is None:
        frontends = discover_frontends()
    parser = argparse.ArgumentParser(
        prog="svhier",
        description="Show the module hierarchy of SystemVerilog sources, report what is missing, "
                    "and write a dependency-ordered filelist or YAML.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        metavar="PATH",
        help="Source files, or directories containing them. A file named directly wins over other "
             "files that define the same modules, interfaces or packages: those are dropped.",
    )
    parser.add_argument(
        "-r", "--recursive",
        action="store_true",
        default=False,
        help="Recurse into subdirectories when a directory is given.",
    )
    parser.add_argument(
        "--yaml",
        nargs="?",
        const="-",
        metavar="FILE",
        default=None,
        help="Write YAML hierarchy to FILE, or to stdout when FILE is omitted.",
    )
    parser.add_argument(
        "--filelist",
        nargs="?",
        const="-",
        metavar="FILE",
        default=None,
        help="Write dependency-ordered filelist to FILE, or to stdout when FILE is omitted.",
    )
    parser.add_argument(
        "-t", "--tree",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Print the instance hierarchy as a tree to stdout. "
             "On by default unless --yaml or --filelist writes to stdout.",
    )
    parser.add_argument(
        "--top",
        action="append",
        metavar="MODULE",
        default=[],
        dest="tops",
        help="Elaborate MODULE as a top module instead of finding tops automatically. Only its "
             "hierarchy is shown, and the filelist only contains the files it needs. Repeatable.",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        default=False,
        help="Print a per-file summary table to stderr.",
    )
    parser.add_argument(
        "-E", "--errors",
        action="store_true",
        default=False,
        help="Print error diagnostics to stderr (by default only a count is shown).",
    )
    parser.add_argument(
        "-W", "--warnings",
        action="store_true",
        default=False,
        help="Print error and warning diagnostics to stderr.",
    )
    parser.add_argument(
        "-x", "--ignore",
        action="append",
        metavar="PATTERN",
        default=None,
        dest="ignore",
        help="Skip files and directories whose whole name matches PATTERN, a name or a quoted glob "
             "(case-insensitive). Only names below the given paths count. Applies to -y too. Repeatable.",
    )
    parser.add_argument(
        "-y", "--libdir",
        action="append",
        metavar="DIR",
        default=None,
        dest="libdirs",
        help="Library directory, searched recursively by every language plugin. Only files defining "
             "what is otherwise missing (and dirs of missing includes) are pulled in. Repeatable.",
    )
    for frontend in frontends:
        frontend.add_arguments(parser)
    return parser


def _print_pulled(pulled: list[tuple[str, Path, int]]) -> None:
    """Print which library files were pulled in for which missing name."""
    if not pulled:
        return
    table = Table(title="Pulled in from -y", show_header=True, header_style="bold cyan")
    table.add_column("Name", style="bold")
    table.add_column("From", style="dim", overflow="fold")
    for name, path, others in pulled:
        table.add_row(name, str(path) + (f" [yellow](+{others} other candidate{'s' if others > 1 else ''})[/yellow]" if others else ""))
    _err.print(table)


def _print_overridden(hierarchy: dict) -> None:
    """Print which names files named on the command line took over, and the copies dropped for them."""
    overridden: dict[str, list[tuple[str, str]]] = hierarchy.get("overridden", {})
    if not overridden:
        return
    used = {item: e["file_name"] for e in hierarchy["files"] for item in defined_names(e)}
    dropped: dict[tuple[str, str], list[str]] = {}
    for fname, items in overridden.items():
        for item in items:
            dropped.setdefault(tuple(item), []).append(fname)
    table = Table(title="Overridden by explicit files", show_header=True, header_style="bold cyan")
    table.add_column("Name", style="bold")
    table.add_column("Kind", style="yellow")
    table.add_column("Used", style="green", overflow="fold")
    table.add_column("Dropped", style="dim", overflow="fold")
    for (kind, name), files in dropped.items():
        table.add_row(name, kind, used.get((kind, name), ""), _first_and_more(files))
    _err.print(table)


def _usable_frontends(frontends: list[Frontend]) -> list[Frontend]:
    """Return the frontends that can run; warn about the others."""
    usable = []
    for frontend in frontends:
        reason = frontend.unavailable_reason()
        if reason:
            _err.print(f"[yellow]warning:[/yellow] plugin {frontend.name!r} is disabled: {reason}")
        else:
            usable.append(frontend)
    return usable


def _route_files(frontends: list[Frontend], files: list[str]) -> list[tuple[Frontend, list[str]]]:
    """Return ``(frontend, files)`` for each usable frontend with inputs; warn about files nothing can read."""
    usable = _usable_frontends(frontends)
    routed: dict[int, list[str]] = {id(f): [] for f in usable}
    fallback = next((f for f in usable if f.catch_all), None)
    for fname in files:
        suffix = Path(fname).suffix.lower()
        owner = next((f for f in usable if suffix in f.suffixes), None)
        if owner is None and suffix in KNOWN_SUFFIXES:
            _err.print(f"[yellow]warning:[/yellow] skipping {fname}: install and enable {KNOWN_SUFFIXES[suffix]} to read it")
        elif (owner or fallback) is not None:
            routed[id(owner or fallback)].append(fname)
    if not any(routed[id(f)] or f.extra_inputs() for f in usable):
        return []
    # A frontend with only -y still joins in, in case another language instantiates what it finds.
    return [(f, routed[id(f)]) for f in usable if routed[id(f)] or f.extra_inputs() or f.has_library()]


def _report(hierarchy: dict, args) -> None:
    """Print the tables and notes."""
    _print_diagnostics(hierarchy, errors=args.errors, warnings=args.warnings)

    if args.summary:
        _print_summary(hierarchy)

    _print_pulled(hierarchy.get("pulled", []))
    _print_overridden(hierarchy)
    _print_duplicates(hierarchy)
    _print_missing(hierarchy)
    for note in hierarchy.get("frontend_notes", []):
        _err.print(f"[yellow]note:[/yellow] {note}")
    _warn_unknown_tops(hierarchy, hierarchy["tops"] or args.tops)


def _write_outputs(hierarchy: dict, args) -> None:
    """Write the requested YAML and filelist, then print the tree.

    The tree comes last so that a large tree, or stopping it, can't cost the files.
    """
    if args.yaml is not None:
        _write_yaml(hierarchy, args.yaml)

    if args.filelist is not None:
        ordered = compute_filelist(hierarchy)
        _write_filelist(ordered, _dedupe(hierarchy["inc_dirs"]), args.filelist, hierarchy["defines"])

    show_tree = args.tree
    if show_tree is None:
        show_tree = "-" not in (args.yaml, args.filelist)
    if show_tree:
        _print_tree(hierarchy, hierarchy["tops"] or args.tops or None)


def main():
    """Entry point: parse arguments, elaborate through the plugins, emit tree, YAML and filelist.

    All Rich diagnostics (spinner, tables, warnings) go to stderr so stdout stays
    clean for piped data.  Exits with code 1 if a plugin reports any error-level
    diagnostics.
    """
    frontends = discover_frontends(lambda name, exc: _err.print(f"[yellow]warning:[/yellow] plugin {name!r} failed to load: {exc}"))
    parser = _build_parser(frontends)
    args = parser.parse_intermixed_args()
    for frontend in frontends:
        frontend.configure(args)
    foreign = [path for frontend in frontends for path in frontend.foreign_files()]
    if not args.paths and not foreign and not any(f.extra_inputs() for f in frontends):
        parser.error("give at least one PATH or an input option such as -f (see --help)")

    ignore = {n.lower() for n in args.ignore} if args.ignore else set()
    suffixes = tuple(suffix for frontend in frontends for suffix in frontend.suffixes)
    inputs = _route_files(frontends, collect_source_files(args.paths + foreign, args.recursive, suffixes, ignore))
    if not inputs:
        _err.print("[red]error:[/red] no source files found")
        sys.exit(1)

    extras = "".join(f" and [bold cyan]{extra}[/bold cyan]" for f, _ in inputs for extra in f.extra_inputs())
    _err.print(Panel(
        f"Parsing [bold cyan]{sum(len(files) for _, files in inputs)}[/bold cyan] file(s){extras}…",
        border_style="dim",
        padding=(0, 1),
    ))

    try:
        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
                      console=_err, transient=True) as progress:
            progress.add_task("Elaborating…", total=None)
            hierarchy = stitch(inputs, list(args.tops or []))
    except ValueError as exc:
        _err.print(f"[red]error:[/red] {exc}")
        sys.exit(1)

    _report(hierarchy, args)
    _write_outputs(hierarchy, args)

    if hierarchy["has_errors"]:
        sys.exit(1)
