# Developer documentation

For working on svhier or writing a plugin. Read [Architecture](../architecture.md) first
for the overall flow.

| Page | Contents |
|------|----------|
| [Result format](result-format.md) | The dict plugins return, and what is left out of it |
| [Writing a plugin](plugins.md) | The plugin interface, with a working example |
| [API reference](api/index.md) | Every module, generated from the docstrings |

## Setup

```bash
uv sync --extra dev --extra docs
```

To work on the GHDL plugin, also install it and pyGHDL. `uv sync` removes both again, so
use `uv run --no-sync` afterwards, and run it from the repository root:

```bash
uv pip install --no-deps -e plugins/svhier-ghdl
uv pip install https://github.com/ghdl/ghdl/releases/download/v6.0.0/pyghdl-6.0.0-cp312-cp312-linux_x86_64.whl
```

## Common commands

```bash
uv run pytest tests/ -v                     # core and slang
uv run --no-sync pytest plugins/svhier-ghdl/tests/ -v   # the GHDL plugin
uv run pylint src/svhier/                   # lint
uv run sphinx-build -W -b html docs docs/_build/html    # docs, failing on warnings
```

## Repository layout

| Path | Contents |
|------|----------|
| `src/svhier/cli.py` | Command line, routing, tables and output |
| `src/svhier/frontends.py` | The plugin interface and stitching |
| `src/svhier/slang_frontend.py` | The bundled slang plugin |
| `src/svhier/parser.py` | Everything that talks to pyslang |
| `src/svhier/libsearch.py` | The `-y` index and search |
| `src/svhier/filelist.py` | Compilation order |
| `plugins/svhier-ghdl/` | The optional GHDL plugin, as its own package |
| `verilog/` | Small SystemVerilog test designs |
| `plugins/svhier-ghdl/tests/mixed/` | A small mixed SV and VHDL design |

## Tests

| File | Covers | Needs |
|------|--------|-------|
| `tests/test_frontends.py` | Stitching, using stand-in plugins | Nothing |
| `tests/test_slang_frontend.py` | The slang plugin, some tests with a stand-in tool | pyslang for the rest |
| `tests/test_parser.py` | The hierarchy walk and results | pyslang |
| `tests/test_libsearch.py` | The `-y` index and search | Nothing |
| `tests/test_cli.py` | The command line, end to end | pyslang |
| `tests/test_docs.py` | The example plugin in [Writing a plugin](plugins.md) | pyslang |
| `tests/test_cva6.py` | The CVA6 core | `SVHIER_CVA6_DIR`, otherwise skipped |
| `plugins/svhier-ghdl/tests/` | The GHDL plugin | pyGHDL, otherwise skipped; some also need the `ghdl` program |

After changing parsing or CLI output, also run the CVA6 smoke test in `CLAUDE.md`. The
small test designs don't exercise blackboxes, vendor libraries, duplicate definitions or
volume.

## Conventions

- **pyslang and pylint.** pyslang is a compiled extension that pylint can't inspect, so
  `generated-members` in `pyproject.toml` suppresses the false `no-member` errors. Line
  length is 160.
- **Docs without compilers.** `autodoc_mock_imports` in `docs/conf.py` mocks pyslang and
  pyGHDL, among others, so the API reference builds without them.
- **Private helpers are documented.** The API reference includes private functions that
  have docstrings, so a docstring on one is published too.
