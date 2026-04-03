"""Command-line interface for icinst: parse SV hierarchies and emit YAML."""

import argparse
import sys
from io import StringIO
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table
from ruamel.yaml import YAML

from icinst.parser import compute_filelist, parse_files

# Rich consoles: stderr for all diagnostics so stdout stays clean for data
_err = Console(stderr=True)


def _prepare_for_yaml(hierarchy: dict) -> dict:
    """Return a copy of *hierarchy* with internal-only keys removed.

    Strips the ``pkgs`` key from each file entry (bookkeeping only) and
    omits ``pkg_imports`` from module defs when the list is empty.
    """
    files = []
    for entry in hierarchy["files"]:
        defs = []
        for d in entry["defs"]:
            def_out = {k: v for k, v in d.items() if not (k == "pkg_imports" and not v)}
            defs.append(def_out)
        file_out = {k: v for k, v in entry.items() if k != "pkgs"}
        file_out["defs"] = defs
        files.append(file_out)
    return {"files": files}


def collect_sv_files(paths: list[str], recursive: bool) -> list[str]:
    """Expand a mix of ``.sv``/``.v`` files and directories into a deduplicated file list.

    Directories are searched for ``*.sv`` and ``*.v`` files; when *recursive* is
    ``True`` the search descends into subdirectories (``**/``).  Duplicate paths
    (by resolved canonical path) are silently dropped.  Paths that are neither a
    file nor a directory emit a warning to stderr and are skipped.
    """
    result: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_file():
            result.append(p)
        elif p.is_dir():
            prefix = "**/" if recursive else ""
            for ext in ("*.sv", "*.v"):
                result.extend(sorted(p.glob(f"{prefix}{ext}")))
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


def collect_inc_dirs(paths: list[str], recursive: bool) -> list[str]:
    """Return directories that contain ``.svh``/``.vh`` header files under *paths*.

    The result is written as ``+incdir+<dir>`` lines at the top of the generated
    filelist, which is the format expected by VCS, Questa, and Xcelium.

    For directory inputs the search respects the *recursive* flag.
    For explicit file inputs the parent directory is checked for headers.
    Directories are returned in discovery order, deduplicated.
    """
    seen: set[Path] = set()
    result: list[str] = []

    def _add(d: Path) -> None:
        resolved = d.resolve()
        if resolved not in seen:
            seen.add(resolved)
            result.append(str(d))

    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            prefix = "**/" if recursive else ""
            for ext in ("*.svh", "*.vh"):
                for hdr in sorted(p.glob(f"{prefix}{ext}")):
                    _add(hdr.parent)
        elif p.is_file():
            parent = p.parent
            if any(parent.glob("*.svh")) or any(parent.glob("*.vh")):
                _add(parent)

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


def _print_diagnostics(hierarchy: dict) -> None:
    """Print any pyslang diagnostics to stderr using Rich formatting."""
    diags = hierarchy.get("diagnostics", [])
    if not diags:
        return
    for d in diags:
        if d["severity"] == "error":
            tag = "[bold red]error[/bold red]"
        else:
            tag = "[bold yellow]warning[/bold yellow]"
        location = f"[dim]{d['file']}[/dim]: " if d["file"] else ""
        _err.print(f"{tag}: {location}{d['message']}")


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


def _write_filelist(ordered: list[str], inc_dirs: list[str], dest: str) -> None:
    """Write a dependency-ordered filelist to *dest*.

    Include directories (if any) are emitted as ``+incdir+<dir>`` lines before
    the source files, as expected by VCS, Questa, and Xcelium.
    """
    lines = ["// Generated by icinst — files in compilation order"]
    for d in inc_dirs:
        lines.append(f"+incdir+{d}")
    lines.extend(ordered)
    lines.append("")
    _write_to("\n".join(lines), dest, f"filelist ({len(ordered)} files)")


def _build_parser() -> argparse.ArgumentParser:
    """Return the argument parser for the ``icinst`` CLI."""
    parser = argparse.ArgumentParser(
        prog="icinst",
        description="Parse SystemVerilog files and emit a module hierarchy as YAML.",
    )
    parser.add_argument(
        "paths",
        nargs="+",
        metavar="PATH",
        help="One or more .sv/.v files or directories containing .sv/.v files.",
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
    return parser


def main():
    """Entry point: parse arguments, run slang elaboration, emit YAML and optional filelist.

    All Rich diagnostics (spinner, summary table, warnings) go to stderr so
    stdout stays clean for piped data.  Exits with code 1 if pyslang reports
    any error-level diagnostics.
    """
    args = _build_parser().parse_args()

    sv_files = collect_sv_files(args.paths, args.recursive)
    inc_dirs = collect_inc_dirs(args.paths, args.recursive)
    if not sv_files:
        _err.print("[red]error:[/red] no .sv/.v files found")
        sys.exit(1)

    _err.print(Panel(
        f"Parsing [bold cyan]{len(sv_files)}[/bold cyan] file(s)…",
        border_style="dim",
        padding=(0, 1),
    ))

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
                  console=_err, transient=True) as progress:
        task = progress.add_task("Running slang elaboration…", total=None)
        hierarchy = parse_files(sv_files)
        progress.update(task, completed=True)

    _print_diagnostics(hierarchy)

    if not args.no_summary:
        _print_summary(hierarchy)

    if args.yaml is not None:
        _write_yaml(hierarchy, args.yaml)

    if args.filelist is not None:
        ordered = compute_filelist(hierarchy)
        _write_filelist(ordered, inc_dirs, args.filelist)

    if hierarchy["has_errors"]:
        sys.exit(1)
