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

The project parses SystemVerilog files using the `pyslang` C++ extension (slang elaborator) and outputs a module/instance hierarchy.

### Two-phase elaboration model

`parse_files()` in [src/icinst/parser.py](src/icinst/parser.py) works in two distinct phases:

1. **Syntax phase** — `SyntaxTree.fromFile()` + `Compilation.addSyntaxTree()`. All files share a single `SourceManager` so cross-file instantiation resolves correctly.
2. **Elaboration phase** — `compilation.getRoot().topInstances` seeds a DFS (`_walk_instances`) that populates two maps:
   - `body_map`: module name → `InstanceBodySymbol` (used to enumerate child instances)
   - `mod_pkg_imports`: module name → list of imported package names

Modules that are defined but never instantiated are not reachable via `topInstances`; they appear in `defs` with `insts: []`.

### What gets filtered

- `getDefinitions()` is filtered to `DefinitionKind.Module` only — packages and interfaces are excluded from `defs`.
- Interface instances inside module bodies are excluded via `child.isModule` guard (interface instances also have `SymbolKind.Instance` but `isModule == False`).
- `pkgs` (packages defined per file) is internal bookkeeping stripped from YAML output by `_prepare_for_yaml()` in [src/icinst/cli.py](src/icinst/cli.py).

### Output dict shape

```python
{
  "files": [
    {
      "file_name": str,          # original caller string, not resolved
      "pkgs": [str, ...],        # internal — stripped from YAML output
      "defs": [
        {
          "mod_name": str,
          "pkg_imports": [str],  # omitted from YAML when empty
          "insts": [
            {"mod_name": str, "inst_name": str}
          ]
        }
      ]
    }
  ]
}
```

### Filelist / dependency ordering

`compute_filelist()` builds a `networkx.DiGraph` where an edge `A → B` means file A must be compiled before file B. Dependencies come from two sources: module instantiation and package imports. `nx.topological_sort` produces the compilation order; cycles fall back to input order.

### CLI output flags

Both `--yaml [FILE]` and `--filelist [FILE]` use `nargs="?"` with `const="-"`, so the flag alone prints to stdout and `--yaml path.yaml` writes to a file. Neither is printed unless the flag is given. All Rich diagnostics (summary table, spinner, warnings) go to stderr.

### pylint configuration

pyslang exposes its API through a compiled extension that pylint cannot introspect. `generated-members = ["pyslang.*"]` in `pyproject.toml` suppresses the resulting false-positive `no-member` errors. Line length is set to 160.
