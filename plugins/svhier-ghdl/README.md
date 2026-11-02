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

Options: `--vhdl-std` (default `08`) and `--vhdl-work LIB` (default `work`). `-y` finds VHDL too.

See the [user guide](https://stfngrt.github.io/svhier/guide/vhdl.html).

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

## Libraries

Files go into libraries automatically, based on how they refer to each other:

1. `library lib; use lib.pkg.all;` or `entity lib.leaf` places the file defining that unit in `lib`.
   If several files define it, a directory named after the library wins; otherwise the first is
   used, with a note.
2. `work.unit` references, a component declaration and the entity of that name, and an architecture
   and its entity all share a library, so `lib` spreads through them.
3. Everything else goes to `--vhdl-work` (default `work`).

## Known limits

- Linux only. Windows and macOS aren't supported.
- CI tests the latest GHDL release, currently 6.0.0, with its Linux wheels for Python 3.11,
  3.12 and 3.13. A new GHDL release that changes the internals above fails CI. It also compiles and
  elaborates test designs from svhier's YAML and filelist, with GHDL's Docker image.
- YAML gives each VHDL file's `library`, but the filelist can't, so it can't compile a design with
  several libraries on its own.
- VHDL-2008 generic types or subprograms make GHDL's printing crash. The tree stays correct, but
  generics can't be read in that run, so SV modules below that top use their defaults, with a note.
- Hierarchy is keyed by name, so a design that uses entities with the same name from two libraries
  shows them as one.
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
