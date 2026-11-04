# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Stefan Engert

"""Command-line interface for svhier: parse SV hierarchies and emit YAML."""

import argparse
import sys
from io import StringIO
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table
from rich.tree import Tree
from ruamel.yaml import YAML

from svhier.filelist import compute_filelist
from svhier.frontends import KNOWN_SUFFIXES, Frontend, discover_frontends, stitch
from svhier.libsearch import glob_files, is_ignored

# Rich consoles: stderr for all diagnostics so stdout stays clean for data
_err = Console(stderr=True)

# Per-file keys returned by parse_files that are bookkeeping only, not YAML output.
_INTERNAL_FILE_KEYS = {"pkgs", "ifaces", "refs", "shadowed", "depends_on"}


def _prepare_for_yaml(hierarchy: dict) -> dict:
    """Return a copy of *hierarchy* with internal-only keys removed.

    Strips the ``pkgs``, ``ifaces``, ``refs`` and ``shadowed`` keys from each file entry (bookkeeping only) and
    omits ``pkg_imports`` from module defs when the list is empty.
    """
    files = []
    for entry in hierarchy["files"]:
        defs = []
        for d in entry["defs"]:
            def_out = {k: v for k, v in d.items() if not (k == "pkg_imports" and not v)}
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


def build_tree(hierarchy: dict, tops: list[str] | None = None) -> list[Tree]:
    """Return one Rich ``Tree`` per top-level module of *hierarchy*.

    When *tops* is omitted, the roots are all modules that no other module
    instantiates.  Instances of modules without a definition are marked as
    ``(blackbox)``; recursive instantiation is cut off and marked ``(cycle)``.
    """
    defs = {d["mod_name"]: d for f in hierarchy["files"] for d in f["defs"]}
    if tops is None:
        instantiated = {i["mod_name"] for d in defs.values() for i in d["insts"]}
        tops = [name for name in defs if name not in instantiated]

    def add_children(node: Tree, mod_name: str, path: tuple[str, ...]) -> None:
        for inst in defs[mod_name]["insts"]:
            child = inst["mod_name"]
            label = f"{inst['inst_name']} [dim]({child})[/dim]"
            if child not in defs:
                node.add(f"{label} [yellow](blackbox)[/yellow]")
            elif child in path:
                node.add(f"{label} [red](cycle)[/red]")
            else:
                add_children(node.add(label), child, path + (child,))

    trees = []
    for top in tops:
        if top not in defs:
            continue
        tree = Tree(f"[bold]{top}[/bold]")
        add_children(tree, top, (top,))
        trees.append(tree)
    return trees


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
    """Print the instance tree(s) to stdout.

    In a terminal, long lines wrap to the terminal width.  When piped, the width is
    unbounded so no line is wrapped or cut off.
    """
    out = Console(highlight=False)
    if not out.is_terminal:
        out = Console(highlight=False, width=100_000)
    for tree in build_tree(hierarchy, tops):
        out.print(tree)


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
        help="Source files, or directories containing them.",
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
    _print_duplicates(hierarchy)
    _print_missing(hierarchy)
    for note in hierarchy.get("frontend_notes", []):
        _err.print(f"[yellow]note:[/yellow] {note}")
    _warn_unknown_tops(hierarchy, hierarchy["tops"] or args.tops)


def _write_outputs(hierarchy: dict, args) -> None:
    """Print the tree, and write the requested YAML and filelist."""
    show_tree = args.tree
    if show_tree is None:
        show_tree = "-" not in (args.yaml, args.filelist)
    if show_tree:
        _print_tree(hierarchy, hierarchy["tops"] or args.tops or None)

    if args.yaml is not None:
        _write_yaml(hierarchy, args.yaml)

    if args.filelist is not None:
        ordered = compute_filelist(hierarchy)
        _write_filelist(ordered, _dedupe(hierarchy["inc_dirs"]), args.filelist, hierarchy["defines"])


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
