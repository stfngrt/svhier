# svhier-ghdl (spike)

Lets svhier elaborate VHDL with GHDL, so one tree can cross between SystemVerilog and VHDL.
GHDL is not a dependency and isn't bundled. Install it separately, and the plugin finds it
at runtime.

## Try it

From the repository root. `uv sync` removes these two packages from the environment, so
use `uv run --no-sync` afterwards.

```bash
uv sync --extra dev
uv pip install --no-deps -e plugins/svhier-ghdl
uv pip install https://github.com/ghdl/ghdl/releases/download/v6.0.0/pyghdl-6.0.0-cp312-cp312-linux_x86_64.whl

uv run --no-sync svhier plugins/svhier-ghdl/tests/mixed --top sv_top
```

Options: `--vhdl-std` (default `08`) and `--vhdl-work LIB` (default `work`).

## How it works

`svhier_ghdl.worker` runs libghdl in a child process, so an Ada assertion can't take svhier
down and `LD_LIBRARY_PATH` can be set before Python starts. pyGHDL provides loading and the
node accessors. pyGHDL has no wrapper for elaboration, so the worker binds these GHDL
internals by symbol name; they may change between GHDL versions:

- `vhdl__configuration__configure`, `add_generic_override`, `apply_generic_override`,
  `check_entity_declaration_top`
- `elab__vhdl_insts__elab_top_unit`
- `elab__vhdl_debug__disp_hierarchy`, whose text output the plugin parses, including the
  evaluated generics of each instance

## Known limits

- CI tests the latest GHDL release, currently 6.0.0, with its Linux wheels for Python 3.11,
  3.12 and 3.13. A new GHDL release that changes the internals above fails CI.
- One VHDL library for all files, set with `--vhdl-work`. A design that names its library, such
  as `library neorv32;`, fails unless that option matches. Automatic detection has strict
  expected-failure tests in `tests/test_ghdl_libraries.py`.
- VHDL that instantiates an SV module directly (`entity work.x`) fails, because GHDL needs
  to see the entity. Instantiating through a `component` works.
- Values VHDL passes to SV modules are integers, booleans, `std_logic`, bit and logic vectors,
  strings and reals. Others, such as times, enumerations and records, keep the SV default with
  a note; if the parameter has no default, the module shows no children.
- Like SV modules with several parameter settings, a module VHDL instantiates with different
  values lists the children of every setting under each instance.
- Each setting is one more slang compilation, so many distinct values slow large designs down.
- SV to VHDL only passes named integer literals (`.N(3)`). Anything else uses the VHDL
  default, with a note.
- Elaboration evaluates constant functions in the design, so don't run it on untrusted code.
