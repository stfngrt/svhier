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
uv run pylint src/icinst/

# Run the CLI
uv run icinst verilog/test.sv --yaml
uv run icinst -r verilog/ --yaml out.yaml --filelist out.f
```

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
