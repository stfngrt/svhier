# Missing definitions

When a design is incomplete, slang produces a wall of errors, and most of
them are consequences of a handful of files you didn't pass. svhier boils
that down to a **Not found** table on stderr:

```text
                               Not found
┏━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Kind            ┃ Name                 ┃ Referenced in              ┃
┡━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ blackbox module │ sram_cache           │ cva6_icache.sv             │
│ blackbox module │ pulp_clock_gating    │ clock_divider.sv (+2 more) │
│ package         │ axi_pkg              │ cva6.sv                    │
│ macro           │ CONF_HPDCACHE_SETS   │ hpdcache_wrapper.sv        │
└─────────────────┴──────────────────────┴────────────────────────────┘
```

The table only appears when something is missing.

## What gets reported

| Kind | Reported when |
|------|---------------|
| **blackbox module** | A module is instantiated but never defined. |
| **interface** | An interface is used but never defined. |
| **package** | A package is imported or referenced (`pkg::name`) but never defined. |
| **include** | An `` `include `` file can't be found on the include path. |
| **macro** | A `` `MACRO `` is used but never `` `define ``d. |

"Referenced in" shows the first file that needs the name, plus how many
other files need it too.

## Blackboxes stay in the hierarchy

Instances of undefined modules aren't dropped. They appear in the tree,
marked `(blackbox)`, and in the YAML `insts` list, so you can see exactly
where in the hierarchy each hole is:

```text
cva6
├── …
├── instr_tracer_i (instr_tracer) (blackbox)
└── …
```

(duplicates)=
## Defined more than once

The opposite problem is just as common: several files define the same module,
interface or package, such as per-target config packages or behavioural and
technology versions of a cell. slang uses one copy and ignores the rest.
Since that's rarely the one you meant, svhier lists these in a **Defined
more than once** table:

```text
                                  Defined more than once
┏━━━━━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Name            ┃ Kind    ┃ Used                                ┃ Ignored                           ┃
┡━━━━━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ cva6_config_pkg │ package │ core/include/cv32a60x_config_pkg.sv │ cv32a65x_config_pkg.sv (+17 more) │
│ riscv           │ package │ core/include/riscv_pkg.sv           │ riscv.sv                          │
└─────────────────┴─────────┴─────────────────────────────────────┴───────────────────────────────────┘
```

Many compiler errors in a design with duplicates come from the "wrong"
copy being used. Fix this table first, then judge the errors.

(explicit-wins)=
### Picking a copy

Name the file you want directly on the command line. A file passed by name
wins: other files found with `-r` or `-y` that define the same names are
dropped from the compilation, wherever the file appears on the command line:

```bash
svhier core/include/cv64a6_imafdc_sv39_config_pkg.sv -r core -y common -y vendor --top cva6
```

The dropped copies move from **Defined more than once** to an **Overridden
by explicit files** table:

```text
                       Overridden by explicit files
┏━━━━━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Name            ┃ Kind    ┃ Used                                          ┃ Dropped                           ┃
┡━━━━━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ cva6_config_pkg │ package │ core/include/cv64a6_imafdc_sv39_config_pkg.sv │ cv32a60x_config_pkg.sv (+17 more) │
└─────────────────┴─────────┴───────────────────────────────────────────────┴───────────────────────────────────┘
```

A file is only dropped when *everything* it defines is also defined by an
explicit file, so nothing else goes missing. A file that also defines other
names is kept, and its duplicate stays in the **Defined more than once**
table. Files listed in a `-f`/`-F` command file are never dropped. Two files
that are both passed by name don't override each other.

## Fixing it

- **Pass the missing files or directories** as extra paths.
- **Let svhier find them** with [library directories](libdirs.md) (`-y`).
- **Exclude what you don't need.** If only testbenches want a missing
  module, skip them with `-x tb`.
- **For duplicates, name the copy you want** as a file argument (see
  {ref}`explicit-wins`), or use the project's filelist (`-f`), which
  usually picks one variant per name. See {ref}`command-files`.
