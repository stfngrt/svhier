import argparse
import sys
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from icinst.parser import parse_files


def _to_commented(obj):
    """Recursively convert plain dicts/lists to ruamel commented types.

    Empty lists are kept as flow-style (inline []) so they render as
    `insts: []` rather than a bare `insts:` (which would be null).
    """
    if isinstance(obj, dict):
        cm = CommentedMap(
            {k: _to_commented(v) for k, v in obj.items()}
        )
        return cm
    if isinstance(obj, list):
        if not obj:
            cs = CommentedSeq()
            cs.fa.set_flow_style()
            return cs
        cs = CommentedSeq(_to_commented(item) for item in obj)
        return cs
    return obj


def collect_sv_files(paths: list[str], recursive: bool) -> list[str]:
    """Expand a mix of .sv files and directories into a sorted file list."""
    result: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_file():
            result.append(p)
        elif p.is_dir():
            pattern = "**/*.sv" if recursive else "*.sv"
            result.extend(sorted(p.glob(pattern)))
        else:
            print(f"warning: {raw!r} is not a file or directory, skipping", file=sys.stderr)
    # deduplicate while preserving order
    seen: set[Path] = set()
    unique: list[str] = []
    for p in result:
        resolved = p.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(str(p))
    return unique


def main():
    parser = argparse.ArgumentParser(
        prog="icinst",
        description="Parse SystemVerilog files and emit a module hierarchy as YAML.",
    )
    parser.add_argument(
        "paths",
        nargs="+",
        metavar="PATH",
        help="One or more .sv files or directories containing .sv files.",
    )
    parser.add_argument(
        "-r", "--recursive",
        action="store_true",
        default=False,
        help="Recurse into subdirectories when a directory is given.",
    )
    args = parser.parse_args()

    sv_files = collect_sv_files(args.paths, args.recursive)
    if not sv_files:
        print("error: no .sv files found", file=sys.stderr)
        sys.exit(1)

    hierarchy = parse_files(sv_files)
    data = _to_commented(hierarchy)

    yaml = YAML()
    yaml.default_flow_style = False
    yaml.best_sequence_indent = 2
    yaml.best_map_flow_style = False
    yaml.dump(data, sys.stdout)
