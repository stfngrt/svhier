# Mixed SystemVerilog and VHDL

With the optional `svhier-ghdl` plugin, one tree can cross between
SystemVerilog and VHDL in both directions. slang elaborates the SystemVerilog
and [GHDL](https://github.com/ghdl/ghdl) elaborates the VHDL, so generics and
generate statements are evaluated in both languages.

```text
$ svhier . --top sv_top
sv_top
├── u_vhd (top_vhd)
│   ├── u_sv (sv_child)
│   │   └── u_gc (sv_grand)
│   │       └── u_vb (vhdl_bottom)
│   ├── u_req (sv_req)
│   ├── u_none (nowhere) (blackbox)
│   ├── g_for[0].u_g (leaf_vhd)
│   ├── g_for[1].u_g (leaf_vhd)
│   ├── g_for[2].u_g (leaf_vhd)
│   └── blk.u_b (leaf_vhd)
└── u_s (sv_used)
```

:::{warning}
The plugin is experimental. pyGHDL has no elaboration API, so the plugin binds
GHDL internals that may change between GHDL releases. It's tested against the
latest GHDL release only.

Elaboration also runs constant functions from the design, so don't use it on
code you don't trust.
:::

## Install

GHDL isn't a dependency of the plugin and isn't bundled with it. The plugin
looks for pyGHDL at runtime. From a checkout of svhier, on Linux with
Python 3.12:

```bash
uv sync --extra dev
uv pip install --no-deps -e plugins/svhier-ghdl
uv pip install https://github.com/ghdl/ghdl/releases/download/v6.0.0/pyghdl-6.0.0-cp312-cp312-linux_x86_64.whl

uv run --no-sync svhier plugins/svhier-ghdl/tests/mixed --top sv_top
```

`uv sync` removes both packages again, so use `uv run --no-sync` afterwards.

Without pyGHDL, svhier still reads SystemVerilog. It warns that the plugin is
disabled and skips `.vhd` and `.vhdl` files.

Only Linux is tested, with the GHDL 6.0.0 wheels for Python 3.11, 3.12 and 3.13.
Windows and macOS aren't supported: the plugin's library path fix is Linux-only, and
GHDL publishes no macOS wheel.

## Usage

`.vhd` and `.vhdl` files are read from the same paths as other sources, and
`--top` can name a module or an entity. SystemVerilog-only options such as
`-f` and `-I` don't apply to VHDL.

| Option | Meaning | Default |
|--------|---------|---------|
| `--vhdl-std` | VHDL standard: `87`, `93`, `02`, `08` or `19` | `08` |
| `--vhdl-work LIB` | Library for files that nothing places elsewhere | `work` |

`-y` searches for VHDL too. See [Library directories](libdirs.md).

## Parameters across languages

Both directions evaluate the value where it's set:

- **VHDL to SystemVerilog:** integers, booleans, `std_logic`, bit and logic
  vectors, strings and reals. Other types, such as times, enumerations and
  records, keep the SystemVerilog default, with a note. If the parameter has no
  default, the module shows no children.
- **SystemVerilog to VHDL:** named integer literals such as `.N(3)`. Anything
  else uses the VHDL default, with a note.

A module that VHDL instantiates with different values lists the children of
every setting under each of those instances, because the VHDL results don't say
which setting an instance uses. The same applies to a VHDL entity with different
generics. An instance in SystemVerilog shows just its own setting. Each setting
is one more slang compilation, so many distinct values slow down large designs.

## Libraries

svhier chooses each file's library from how the files refer to each other, and
YAML reports it as `library`:

1. `library lib; use lib.pkg.all;` or `entity lib.leaf` places the file defining
   that unit in `lib`. If several files define it, a directory named after the
   library wins. Otherwise the first is used, with a note.
2. `work.unit` references, a component declaration and the entity of that name,
   and an architecture and its entity all share a library, so `lib` spreads
   through them.
3. Everything else goes to `--vhdl-work`.

The filelist has no way to say which library a file belongs to, so compiling a
design with several libraries needs the YAML too.

## Known limits

- A design using entities with the same name from two libraries shows them as
  one, because the hierarchy is keyed by name.
- VHDL instantiating an SV module directly (`entity work.x`) fails, because GHDL
  needs to see the entity. Instantiating through a `component` works.
- VHDL-2008 generic types or subprograms make GHDL's printing crash. The tree
  stays correct, but generics can't be read in that run, so SV modules below
  that top use their defaults, with a note.
