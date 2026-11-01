# Library directories (`-y`)

Real projects pull modules from vendor IP, shared cell libraries and
submodules. Compiling all of those wholesale drags in hundreds of unrelated
(and often conflicting) files. Library directories solve this the way
simulators do: they are **searched, not compiled**.

```bash
svhier -r rtl/ -y vendor/ -y third_party/
```

## How it works

1. svhier parses your sources as usual.
2. Every `-y` directory is indexed **recursively** for module, interface and
   package definitions, and for `.svh`/`.vh` headers.
3. For each missing module, interface or package, the defining file is added
   to the compilation. For each missing `` `include ``, the directory it lives
   under is added as an include dir.
4. svhier re-parses and repeats until nothing new resolves, so dependencies of
   dependencies are found too.

A **Pulled in from -y** table on stderr shows what was added and why:

```text
                          Pulled in from -y
┏━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Name          ┃ From                                                                     ┃
┡━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ axi_pkg       │ vendor/pulp-platform/axi/src/axi_pkg.sv                                  │
│ tc_clk_gating │ vendor/pulp-platform/tech_cells_generic/src/rtl/tc_clk.sv (+1 other …)   │
│ tc_sram       │ vendor/pulp-platform/tech_cells_generic/src/rtl/tc_sram.sv (+1 other …)  │
└───────────────┴──────────────────────────────────────────────────────────────────────────┘
```

Pulled-in files are part of the design from then on. They show up in the
tree, the YAML and the `--filelist`.

## When a name is defined more than once

Libraries often ship several implementations of the same module, such as a
generic RTL model and an FPGA-specific one. svhier picks one per name, in
this order of preference:

1. a file named exactly `NAME.sv` / `NAME.v`
2. a file whose name is a prefix of `NAME` (`tc_clk.sv` for `tc_clk_gating`)
3. the shortest path
4. alphabetical order

The table shows how many other candidates there were, so ambiguities are
visible. To force a choice, either pass the file you want explicitly (files
already in the compilation always win), or exclude the alternatives with
`-x`:

```bash
svhier -r rtl/ -y vendor/ -x xilinx
```

:::{note}
`-x` applies to library directories too. Any path with a component that
contains the given name (case-insensitive) is skipped.
:::
