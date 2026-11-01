# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Stefan Engert

"""Command-line interface for svhier: parse SV hierarchies and emit YAML."""

import argparse
import re
import sys
from io import StringIO
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table
from rich.tree import Tree
from ruamel.yaml import YAML

from svhier.libsearch import LibraryIndex, resolve_missing
from svhier.parser import compute_filelist, parse_files

# Rich consoles: stderr for all diagnostics so stdout stays clean for data
_err = Console(stderr=True)

# Per-file keys returned by parse_files that are bookkeeping only, not YAML output.
_INTERNAL_FILE_KEYS = {"pkgs", "ifaces", "refs", "shadowed"}


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


def _is_ignored(p: Path, ignore: set[str]) -> bool:
    return bool(ignore) and any(name in part.lower() for part in p.parts for name in ignore)


def collect_sv_files(paths: list[str], recursive: bool, ignore: set[str] | None = None) -> list[str]:
    """Expand a mix of ``.sv``/``.v`` files and directories into a deduplicated file list.

    Directories are searched for ``*.sv`` and ``*.v`` files; when *recursive* is
    ``True`` the search descends into subdirectories (``**/``).  Duplicate paths
    (by resolved canonical path) are silently dropped.  Paths that are neither a
    file nor a directory emit a warning to stderr and are skipped.
    """
    ign = ignore or set()
    result: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_file():
            if not _is_ignored(p, ign):
                result.append(p)
        elif p.is_dir():
            prefix = "**/" if recursive else ""
            for ext in ("*.sv", "*.v"):
                result.extend(f for f in sorted(p.glob(f"{prefix}{ext}")) if not _is_ignored(f, ign))
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


_INCLUDE_RE = re.compile(r'`include\s+["<]([^">]+)[">]')


def _glob_files(p: Path, recursive: bool, exts: tuple[str, ...], ignore: set[str]) -> list[Path]:
    prefix = "**/" if recursive else ""
    return [f for ext in exts for f in sorted(p.glob(f"{prefix}{ext}")) if not _is_ignored(f, ignore)]


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
            headers.extend(_glob_files(p, recursive, ("*.svh", "*.vh"), ign))
            sources.extend(_glob_files(p, recursive, ("*.sv", "*.v"), ign))
        elif p.is_file() and not _is_ignored(p, ign):
            sources.append(p)
            headers.extend(_glob_files(p.parent, False, ("*.svh", "*.vh"), ign))

    seen: set[Path] = set()
    result: list[str] = []
    for d in [h.parent for h in headers] + _include_base_dirs(sources + headers, headers):
        resolved = d.resolve()
        if resolved not in seen:
            seen.add(resolved)
            result.append(str(d))
    return result


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
    """Report pyslang diagnostics on stderr.

    By default only a one-line count is printed.  With *errors* every error is
    listed; with *warnings* every error and warning is listed.
    """
    diags = hierarchy.get("diagnostics", [])
    n_err = sum(1 for d in diags if d["severity"] == "error")
    n_warn = len(diags) - n_err
    if not errors and not warnings:
        if n_err or n_warn:
            parts = [f"{n} {name}{'s' if n != 1 else ''}" for n, name in ((n_err, "error"), (n_warn, "warning")) if n]
            _err.print(f"[dim]slang reported {', '.join(parts)} (use -E to list errors, -W to also list warnings)[/dim]")
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
    "includes": "include",
    "macros": "macro",
}


def _print_missing(hierarchy: dict) -> None:
    """Print a table of everything slang could not find to stderr.

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
            where = Path(files[0]).name if files else ""
            if len(files) > 1:
                where += f" (+{len(files) - 1} more)"
            table.add_row(label, name, where)
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
        shown = Path(ignored[0]).name if ignored else ""
        if len(ignored) > 1:
            shown += f" (+{len(ignored) - 1} more)"
        table.add_row(dup["name"], dup["kind"], dup["used"], shown)
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
            _err.print(f"[yellow]warning:[/yellow] top module {top!r} not found")
            continue
        tree = Tree(f"[bold]{top}[/bold]")
        add_children(tree, top, (top,))
        trees.append(tree)
    return trees


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
    yaml.best_sequence_indent = 2
    return yaml


def _write_to(content: str, dest: str, label: str) -> None:
    """Write *content* to *dest* (``-`` means stdout); confirm to stderr."""
    if dest == "-":
        sys.stdout.write(content)
    else:
        Path(dest).write_text(content, encoding="utf-8")
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


class _CommandFileAction(argparse.Action):  # pylint: disable=too-few-public-methods
    """Collect ``-f``/``-F`` options as ``(flag, path)`` pairs, in command-line order."""

    def __call__(self, parser, namespace, values, option_string=None):
        items = list(getattr(namespace, self.dest) or [])
        items.append((option_string, values))
        setattr(namespace, self.dest, items)


def _build_parser() -> argparse.ArgumentParser:
    """Return the argument parser for the ``svhier`` CLI."""
    parser = argparse.ArgumentParser(
        prog="svhier",
        description="Show the module hierarchy of SystemVerilog sources, report what is missing, "
                    "and write a dependency-ordered filelist or YAML.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        metavar="PATH",
        help=".sv/.v files or directories containing .sv/.v files.",
    )
    parser.add_argument(
        "-f",
        "-F",
        action=_CommandFileAction,
        metavar="FILE",
        dest="command_files",
        default=[],
        help="Read sources and options from a command file (filelist). Supports +incdir+, +define+, "
             "nested -f/-F and ${VAR} environment variables. With -F, paths inside FILE are relative to FILE. "
             "Repeatable.",
    )
    parser.add_argument(
        "-I", "--include-dir",
        action="append",
        metavar="DIR",
        default=[],
        dest="include_dirs",
        help="Add a directory to search for include files. Repeatable.",
    )
    parser.add_argument(
        "-D", "--define",
        action="append",
        metavar="NAME[=VALUE]",
        default=[],
        dest="defines",
        help="Define a preprocessor macro. Repeatable.",
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
        help="Print slang error diagnostics to stderr (by default only a count is shown).",
    )
    parser.add_argument(
        "-W", "--warnings",
        action="store_true",
        default=False,
        help="Print slang error and warning diagnostics to stderr.",
    )
    parser.add_argument(
        "-x", "--ignore",
        action="append",
        metavar="NAME",
        default=None,
        dest="ignore",
        help="Ignore files or directories whose name matches NAME. Repeatable.",
    )
    parser.add_argument(
        "-y", "--libdir",
        action="append",
        metavar="DIR",
        default=None,
        dest="libdirs",
        help="Library directory, searched recursively. Only files defining otherwise missing "
             "modules, interfaces or packages (and dirs of missing includes) are pulled in. Repeatable.",
    )
    return parser


# Upper bound on parse/resolve rounds for -y; each round pulls in one more layer of dependencies.
_MAX_LIB_ROUNDS = 20


def _add_pulled(
    files: dict[str, tuple[Path, int]], incs: dict[str, Path],
    sv_files: list[str], inc_dirs: list[str], pulled: list[tuple[str, Path, int]],
) -> None:
    """Extend *sv_files* / *inc_dirs* with one round of resolved library files and record them in *pulled*."""
    for name, (path, others) in files.items():
        if str(path) not in sv_files:
            sv_files.append(str(path))
        pulled.append((name, path, others))
    for include, d in incs.items():
        if str(d) not in inc_dirs:
            inc_dirs.append(str(d))
        pulled.append((include, d, 0))


def _parse_with_libs(
    sv_files: list[str], inc_dirs: list[str], index: LibraryIndex | None, options: dict,
) -> tuple[dict, list[tuple[str, Path, int]]]:
    """Parse, then keep pulling missing definitions from *index* until nothing new resolves.

    *options* are passed to :func:`parse_files` as keyword arguments.  *sv_files* and
    *inc_dirs* are extended in place.  Returns the hierarchy of the final parse (so
    diagnostics, missing names and the exit code reflect the resolved design) and
    ``(name, file, other candidates)`` for every library file pulled in.
    Raises ``ValueError`` if slang rejects the inputs.
    """
    pulled: list[tuple[str, Path, int]] = []
    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
                  console=_err, transient=True) as progress:
        task = progress.add_task("Running slang elaboration…", total=None)
        hierarchy = parse_files(sv_files, inc_dirs, **options)
        for rnd in range(1, _MAX_LIB_ROUNDS + 1):
            if index is None:
                break
            loaded = [f["file_name"] for f in hierarchy["files"]]
            files, incs = resolve_missing(hierarchy["missing"], index, loaded, inc_dirs)
            if not files and not incs:
                break
            _add_pulled(files, incs, sv_files, inc_dirs, pulled)
            progress.update(task, description=f"Resolving from library dirs (round {rnd})…")
            hierarchy = parse_files(sv_files, inc_dirs, **options)
    return hierarchy, pulled


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


def main():
    """Entry point: parse arguments, run slang elaboration, emit tree, YAML and filelist.

    All Rich diagnostics (spinner, tables, warnings) go to stderr so stdout stays
    clean for piped data.  Exits with code 1 if pyslang reports any error-level
    diagnostics.
    """
    parser = _build_parser()
    args = parser.parse_intermixed_args()
    if not args.paths and not args.command_files:
        parser.error("give at least one PATH or -f/-F command file")

    ignore = {n.lower() for n in args.ignore} if args.ignore else set()
    sv_files = collect_sv_files(args.paths, args.recursive, ignore)
    inc_dirs = _dedupe(args.include_dirs + collect_inc_dirs(args.paths, args.recursive, ignore))
    if not sv_files and not args.command_files:
        _err.print("[red]error:[/red] no .sv/.v files found")
        sys.exit(1)

    _err.print(Panel(
        f"Parsing [bold cyan]{len(sv_files)}[/bold cyan] file(s)"
        + (f" and [bold cyan]{len(args.command_files)}[/bold cyan] command file(s)" if args.command_files else "") + "…",
        border_style="dim",
        padding=(0, 1),
    ))

    index = LibraryIndex(args.libdirs, ignore) if args.libdirs else None
    options = {"defines": args.defines, "tops": args.tops, "command_files": args.command_files}
    try:
        hierarchy, pulled = _parse_with_libs(sv_files, inc_dirs, index, options)
    except ValueError as exc:
        _err.print(f"[red]error:[/red] {exc}")
        sys.exit(1)

    _print_diagnostics(hierarchy, errors=args.errors, warnings=args.warnings)

    if args.summary:
        _print_summary(hierarchy)

    _print_pulled(pulled)
    _print_duplicates(hierarchy)
    _print_missing(hierarchy)

    show_tree = args.tree
    if show_tree is None:
        show_tree = "-" not in (args.yaml, args.filelist)
    if show_tree:
        _print_tree(hierarchy, args.tops or None)

    if args.yaml is not None:
        _write_yaml(hierarchy, args.yaml)

    if args.filelist is not None:
        ordered = compute_filelist(hierarchy)
        _write_filelist(ordered, _dedupe(inc_dirs + hierarchy["inc_dirs"]), args.filelist, hierarchy["defines"])

    if hierarchy["has_errors"]:
        sys.exit(1)
