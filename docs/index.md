# svhier

<p class="sd-fs-4 sd-text-muted">
See the module hierarchy of any SystemVerilog codebase, find out what's
missing, and get a filelist that compiles.
</p>

svhier elaborates your RTL with the [slang](https://github.com/MikePopoloski/slang)
compiler and turns the result into something you can read and use. Point it at
a directory and you get the instance tree:

```text
$ svhier -r core -y common -y vendor --top cva6
cva6
├── i_frontend (frontend)
│   ├── i_instr_realign (instr_realign)
│   ├── ras_gen.i_ras (ras)
│   ├── bht_gen.i_bht (bht)
│   └── i_instr_queue (instr_queue)
│       ├── gen_instr_fifo[0].i_fifo_instr_data (cva6_fifo_v3)
│       └── i_fifo_address (cva6_fifo_v3)
├── id_stage_i (id_stage)
├── issue_stage_i (issue_stage)
│   ├── i_scoreboard (scoreboard)
│   └── i_issue_read_operands (issue_read_operands)
├── ex_stage_i (ex_stage)
│   ├── alu_wrapper_i (alu_wrapper)
│   │   └── alu_i (alu)
…
```

::::{grid} 1 2 2 2
:gutter: 3
:margin: 4 4 0 0

:::{grid-item-card} {octicon}`list-unordered;1.2em;sd-text-primary` Instance tree
:link: guide/tree
:link-type: doc

The elaborated hierarchy, with generate blocks and instance arrays.
Pick the roots with `--top`.
:::

:::{grid-item-card} {octicon}`question;1.2em;sd-text-primary` Blackbox report
:link: guide/missing
:link-type: doc

Every module, package, interface, include and macro that couldn't be
found, plus the file that needs it.
:::

:::{grid-item-card} {octicon}`search;1.2em;sd-text-primary` Library search
:link: guide/libdirs
:link-type: doc

`-y DIR` pulls in exactly the files that define what's missing, and
whatever *those* files need in turn.
:::

:::{grid-item-card} {octicon}`file-code;1.2em;sd-text-primary` Filelist & YAML
:link: guide/outputs
:link-type: doc

Dependency-ordered sources with `+incdir+` for VCS, Questa and Xcelium,
and the whole hierarchy as data.
:::
::::

## Install

::::{tab-set}
:::{tab-item} pip
```bash
pip install svhier
```
:::
:::{tab-item} uv
```bash
uv tool install svhier
```
:::
:::{tab-item} From source
```bash
git clone https://github.com/stfngrt/svhier
cd svhier
uv sync
uv run svhier --help
```
:::
::::

```{button-ref} getting-started
:ref-type: doc
:color: primary
:shadow:

Walk through a real design →
```

```{toctree}
:hidden:
:caption: Getting started

getting-started
```

```{toctree}
:hidden:
:caption: User guide

guide/inputs
guide/tree
guide/missing
guide/libdirs
guide/outputs
guide/diagnostics
```

```{toctree}
:hidden:
:caption: Reference

cli
architecture
```
