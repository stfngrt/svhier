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

## Fixing it

- **Pass the missing files or directories** as extra paths.
- **Let svhier find them** with [library directories](libdirs.md) (`-y`).
- **Exclude what you don't need.** If only testbenches want a missing
  module, skip them with `-x tb`.
- **For duplicates, use the project's filelist** (`-f`). It usually picks one
  variant per name. See {ref}`command-files`.
