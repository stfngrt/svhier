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
from ruamel.yaml import YAML

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


def _print_diagnostics(hierarchy: dict, *, warnings: bool = False) -> None:
    """Print any pyslang diagnostics to stderr using Rich formatting."""
    diags = hierarchy.get("diagnostics", [])
    if not diags:
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


def _print_duplicates(hierarchy: dict) -> None:
    """Warn about module, interface and package names defined in more than one file."""
    for dup in hierarchy.get("duplicates", []):
        others = ", ".join(f for f in dup["files"] if f != dup["used"])
        _err.print(f"[bold yellow]warning[/bold yellow]: {dup['kind']} [bold]{dup['name']}[/bold] is defined in "
                   f"{len(dup['files'])} files; using [dim]{dup['used']}[/dim], ignoring [dim]{others}[/dim]")


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


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
        description="Parse SystemVerilog files and emit a module hierarchy as YAML.",
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
        "--top",
        action="append",
        metavar="MODULE",
        default=[],
        dest="tops",
        help="Elaborate MODULE as a top module instead of finding tops automatically. "
             "The filelist then only contains the files the tops need. Repeatable.",
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
        "--no-summary",
        action="store_true",
        default=False,
        help="Suppress the summary table printed to stderr.",
    )
    parser.add_argument(
        "-W", "--warnings",
        action="store_true",
        default=False,
        help="Also print slang warning diagnostics to stderr.",
    )
    parser.add_argument(
        "-x", "--ignore",
        action="append",
        metavar="NAME",
        default=None,
        dest="ignore",
        help="Ignore files or directories whose name matches NAME. Repeatable.",
    )
    return parser


def main():
    """Entry point: parse arguments, run slang elaboration, emit YAML and optional filelist.

    All Rich diagnostics (spinner, summary table, warnings) go to stderr so
    stdout stays clean for piped data.  Exits with code 1 if pyslang reports
    any error-level diagnostics.
    """
    parser = _build_parser()
    args = parser.parse_args()
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

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
                  console=_err, transient=True) as progress:
        task = progress.add_task("Running slang elaboration…", total=None)
        try:
            hierarchy = parse_files(sv_files, inc_dirs, defines=args.defines, tops=args.tops,
                                    command_files=args.command_files)
        except ValueError as exc:
            progress.stop()
            _err.print(f"[red]error:[/red] {exc}")
            sys.exit(1)
        progress.update(task, completed=True)

    _print_diagnostics(hierarchy, warnings=args.warnings)
    _print_duplicates(hierarchy)

    if not args.no_summary:
        _print_summary(hierarchy)

    if args.yaml is not None:
        _write_yaml(hierarchy, args.yaml)

    if args.filelist is not None:
        ordered = compute_filelist(hierarchy)
        _write_filelist(ordered, _dedupe(inc_dirs + hierarchy["inc_dirs"]), args.filelist, hierarchy["defines"])

    if hierarchy["has_errors"]:
        sys.exit(1)
