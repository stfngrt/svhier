# Architecture

svhier parses SystemVerilog files using the `pyslang` C++ extension (slang elaborator)
and outputs a module/instance hierarchy as YAML.

## Parser

```{eval-rst}
.. autosummary::
   :nosignatures:

   svhier.parser.parse_files
   svhier.parser.compute_filelist
```

```{eval-rst}
.. autofunction:: svhier.parser.parse_files
```

:::{note}
Modules that are defined but never instantiated are unreachable via `topInstances`
and will appear in the output with an empty `insts` list.
:::

```{eval-rst}
.. autofunction:: svhier.parser.compute_filelist
```

:::{tip}
If your design has genuine circular dependencies between files, the filelist falls
back to the original input order rather than raising an error.
:::

## CLI

```{eval-rst}
.. autosummary::
   :nosignatures:

   svhier.cli.collect_sv_files
   svhier.cli.collect_inc_dirs
   svhier.cli.main
```

```{eval-rst}
.. autofunction:: svhier.cli.collect_sv_files
```

```{eval-rst}
.. autofunction:: svhier.cli.collect_inc_dirs
```

```{eval-rst}
.. autofunction:: svhier.cli.main
```

## Filtering rules

The following symbols are intentionally excluded from the output:

- **Packages and interfaces** — `getDefinitions()` is filtered to
  `DefinitionKind.Module` only.
- **Interface instances** — excluded inside module bodies via the `child.isModule`
  guard (interface instances share `SymbolKind.Instance` with module instances but
  have `isModule == False`).
- **`pkgs` key** — internal bookkeeping stripped from YAML output by
  `_prepare_for_yaml()`.
- **`diagnostics` key** — pyslang diagnostics are printed to stderr and stripped
  from YAML output.

## Development notes

:::{warning}
pyslang exposes its API through a compiled C++ extension that pylint cannot
introspect.  `generated-members = ["pyslang.*"]` in `pyproject.toml` suppresses
the resulting false-positive `no-member` errors.  The same list is passed to
Sphinx via `autodoc_mock_imports` so the docs build without a compiled extension
present.
:::

Line length is set to 160 in the pylint configuration.
