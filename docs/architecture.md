# Architecture

svhier parses SystemVerilog files using the `pyslang` C++ extension (slang elaborator)
and turns the elaborated design into an instance tree, a report of missing
definitions, YAML and a dependency-ordered filelist.

The code is split into three modules:

| Module | Responsibility |
|--------|----------------|
| `svhier.parser` | Run slang, walk the elaborated hierarchy, collect missing names, order files |
| `svhier.libsearch` | Index `-y` library directories and resolve missing names to files |
| `svhier.cli` | Collect inputs, run the parse/resolve loop, render tree, tables and outputs |

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

### Inputs

All inputs go through slang's `Driver`, so source files, `-f`/`-F` command files
(with `+incdir+`, `+define+`, nested command files and `${VAR}` environment
variables), `-I`, `-D` and `--top` behave exactly as in slang itself.

### Parameterizations

The walk visits every unique (definition, parameter values) pair once, not every
module name once.  A module whose generate branches depend on its parameters
therefore lists the children of every setting it is instantiated with, merged
without repeats.

### Duplicate definitions

Definitions are keyed by `(name, file)`.  When a module, interface or package name
is defined in more than one file, the result's `duplicates` list names every file
and the copy slang actually uses (`used`).  The other copies are marked `shadowed`
in their file entry: they are not elaborated (no `insts`) and `compute_filelist()`
resolves the name to the used copy.  The CLI prints a warning per duplicate.

```{eval-rst}
.. autofunction:: svhier.parser.compute_filelist
```

:::{tip}
If your design has genuine circular dependencies between files, the filelist falls
back to the original input order rather than raising an error.
:::

When `--top` is given, slang's dependency trimming (`--depfile-trim`) decides which
files the tops need (it follows module, interface, package and class references);
`compute_filelist()` leaves the rest out and orders what remains.

## Library search

```{eval-rst}
.. automodule:: svhier.libsearch
   :members: LibraryIndex, resolve_missing
```

The CLI calls {func}`~svhier.libsearch.resolve_missing` after each parse and re-parses
with the returned files and include dirs until nothing new resolves (at most 20
rounds).  Candidate files are found with a regex over the source text, so the index
is cheap to build; slang still does the real parse of whatever gets pulled in.

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
  have `isModule == False`).  They still count as filelist dependencies via `refs`.
- **Untaken generate branches** — instances in generate blocks with
  `isUninstantiated == True` are not listed.  Instances inside taken generate
  blocks and instance arrays are listed with a path relative to the parent module
  (e.g. `g_loop[0].u_core`, `u_arr[1]`).
- **`pkgs`, `ifaces`, `refs`, `shadowed` keys** — internal bookkeeping stripped from YAML
  output by `_prepare_for_yaml()`.  `refs` lists the packages and interfaces a
  file references (imports, `pkg::name`, interface ports and instances), collected
  from the syntax tree; `compute_filelist()` uses it to order files.
- **`diagnostics`, `tops`, `needed`, `defines`, `inc_dirs` keys** —
  top-level bookkeeping, not part of the YAML output.  Diagnostics are summarised
  (or listed with `-E`/`-W`) on stderr; `defines` and `inc_dirs` go into the filelist.
- **`missing` key** — names slang could not resolve, built from the
  `UnknownModule`, `UnknownInterface`, `UnknownPackage`, `UnknownClassOrPackage`,
  `CouldNotOpenIncludeFile` and `UnknownDirective` diagnostics.  Printed as the
  **Not found** table and stripped from YAML output.
- **`duplicates` key** — names defined in more than one file, with the copy slang
  uses.  Printed as the **Defined more than once** table and stripped from YAML
  output; the filelist resolves dependencies to the used copy.

## Blackboxes

Instances of undefined modules are `UninstantiatedDef` symbols in slang, not
`Instance` symbols.  `_iter_module_instances(..., blackboxes=True)` yields them
when building `insts`, using `definitionName` as `mod_name`, but the hierarchy
walk never descends into them.

## Development notes

:::{warning}
pyslang exposes its API through a compiled C++ extension that pylint cannot
introspect.  `generated-members = ["pyslang.*"]` in `pyproject.toml` suppresses
the resulting false-positive `no-member` errors.  The same list is passed to
Sphinx via `autodoc_mock_imports` so the docs build without a compiled extension
present.
:::

Line length is set to 160 in the pylint configuration.
