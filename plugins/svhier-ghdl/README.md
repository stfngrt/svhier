# svhier-ghdl (spike)

Lets svhier elaborate VHDL with GHDL, so one tree can cross between SystemVerilog and VHDL.
GHDL is not a dependency and isn't bundled. Install it separately, and the plugin finds it
at runtime.

## Try it

From the repository root. A plain `uv run` or `uv sync` removes these two packages from
the environment, so use `--no-sync` afterwards.

```bash
uv sync --extra dev
uv pip install --no-deps -e plugins/svhier-ghdl
uv pip install https://github.com/ghdl/ghdl/releases/download/v6.0.0/pyghdl-6.0.0-cp312-cp312-linux_x86_64.whl

cd plugins/svhier-ghdl/tests/mixed
uv run --no-sync svhier . --top sv_top
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
- `elab__vhdl_debug__disp_hierarchy`, whose text output the plugin parses

## Known limits

- Tested only with GHDL 6.0.0 and its Linux Python 3.12 wheel.
- One VHDL library for all files.
- VHDL that instantiates an SV module directly (`entity work.x`) fails, because GHDL needs
  to see the entity. Instantiating through a `component` works.
- SV modules under VHDL are elaborated with default parameters, because the values VHDL
  passes aren't read yet. A module with a required parameter shows no children, with a note.
- SV to VHDL only passes named integer literals (`.N(3)`). Anything else uses the VHDL
  default, with a note.
- Elaboration evaluates constant functions in the design, so don't run it on untrusted code.
