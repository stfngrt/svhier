# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install all dependencies (including dev)
uv sync --extra dev

# Run all tests
uv run pytest tests/ -v

# Run a single test
uv run pytest tests/test_parser.py::test_module_with_instances -v

# Lint
uv run pylint src/svhier/

# Run the CLI
uv run svhier verilog/test.sv --yaml
uv run svhier -r verilog/ --yaml out.yaml --filelist out.f
```

## Real-world smoke test: cva6

After changing parsing or CLI output, run svhier against the CVA6 core as well as
the unit tests. The small fixtures in `verilog/` don't exercise blackboxes, vendor
libraries, duplicate definitions or volume (~300 files).

`external/cva6` is a gitignored clone (with submodules) that exists only in the
main checkout. From a worktree, it is at `../svhier/external/cva6`; run against the
worktree's code with `uv --project <worktree>`:

First, the opt-in regression tests (they build from CVA6's own `core/Flist.cva6`):

```bash
SVHIER_CVA6_DIR=external/cva6 uv run pytest tests/test_cva6.py -v   # ../svhier/external/cva6 from a worktree
```

Then both CLI routes, from inside the cva6 checkout:

```bash
cd external/cva6   # or ../svhier/external/cva6 from a worktree

# 1. Project filelist: picks one target config.
export CVA6_REPO_DIR=$PWD HPDCACHE_DIR=$PWD/core/cache_subsystem/hpdcache TARGET_CFG=cv64a6_imafdc_sv39
uv --project <path-to-svhier-checkout> run svhier -f core/Flist.cva6 --top cva6

# 2. No filelist: scan core/, resolve the rest from library dirs.
uv --project <path-to-svhier-checkout> run svhier -r core -y common -y vendor -x test -x tb --top cva6
```

Expected for route 1: exit code 0, no slang errors, and no "Not found" or
"Defined more than once" table.

Expected for route 2: the tree rooted at `cva6`, a "Pulled in from -y" table, no
"Not found" table, and no `(blackbox)` in the tree. A "Defined more than once"
table lists `cva6_config_pkg` (one variant per target in `core/include/`), `riscv`
and the hpdcache SRAM wrappers. About 97 slang errors remain, almost all caused by
those duplicates, not by missing files.

## Architecture

See [docs/architecture.md](docs/architecture.md) for the full architecture reference
(two-phase elaboration model, helper functions, output dict shape, filelist ordering,
CLI flags, and pylint configuration).

```bash
# Install docs dependencies
uv sync --extra docs

# Build HTML docs
uv run sphinx-build -b html docs docs/_build/html
```
