# Architecture

svhier turns source files into one instance tree, a report of what's missing, YAML and a
dependency-ordered filelist. It doesn't parse any language itself. Real compilers do
that, slang for SystemVerilog and GHDL for VHDL, each behind a plugin. Core decides which
plugin reads what, connects the results, and writes the output.

This page follows one run from the command line to the output. For the code itself, see
the [developer documentation](dev/index.md).

## The parsing flow

```text
 svhier -r rtl -y lib --top chip
              │
              ▼
 ┌─────────────────────────┐
 │ 1. Load plugins         │  each one adds its options, then reads the parsed command line
 └────────────┬────────────┘
              ▼
 ┌─────────────────────────┐
 │ 2. Collect source files │  expand paths, using the file types the plugins declare
 └────────────┬────────────┘
              ▼
 ┌─────────────────────────┐
 │ 3. Route to plugins     │  each file goes to the plugin for its suffix
 └────────────┬────────────┘
              ▼
 ┌─────────────────────────┐      ┌──────────────────────────────┐
 │ 4. Elaborate and stitch │ ◄──► │ slang plugin ─► pyslang      │
 │                         │ ◄──► │ GHDL plugin  ─► worker ─► GHDL│
 └────────────┬────────────┘      └──────────────────────────────┘
              ▼
 ┌─────────────────────────┐
 │ 5. One merged result    │  the same shape whichever languages produced it
 └────────────┬────────────┘
              ▼
 ┌─────────────────────────┐
 │ 6. Report and write     │  tables, YAML, filelist, tree, exit code
 └─────────────────────────┘
```

| Stage | What happens | Code |
|-------|--------------|------|
| 1. Load plugins | Find every class in the `svhier.frontends` entry point group. A plugin that fails to load gives a warning and svhier carries on. | {func}`~svhier.frontends.discover_frontends` |
| 2. Collect source files | Expand files and directories, applying `-r` and `-x`. A file you name directly is kept whatever its suffix. | {func}`~svhier.cli.collect_source_files` |
| 3. Route to plugins | Give each file to the plugin for its suffix. A plugin whose tool isn't installed is disabled, with a warning. | {func}`~svhier.cli._route_files` |
| 4. Elaborate and stitch | Run the plugins in turn until no plugin finds anything new. | {func}`~svhier.frontends.stitch` |
| 5. Merged result | Combine what the plugins report into one dict. | {meth}`~svhier.frontends._Stitcher._merge` |
| 6. Report and write | Print the tables to stderr, write the YAML and filelist to files or stdout, then print the tree to stdout. Exit 1 if any error was reported. | {func}`~svhier.cli.main` |

Stages 1 to 3 and 6 know nothing about any language. All language knowledge sits behind
the plugin interface, including the options `-f`, `-I` and `-D`, which the slang plugin
adds itself.

## Routing

Each file has one owner:

- **A plugin's suffix matches:** that plugin reads it.
- **The suffix is unknown:** the *catch-all* plugin reads it, which is slang. This is
  how an explicitly named file such as `design.pkg` still works.
- **The suffix belongs to a plugin that isn't available:** the file is skipped, with a
  message saying what to install.

A plugin with no files of its own still takes part if `-y` holds files for it, in case
another language instantiates something it finds there.

## Elaboration and stitching

### One language

With one plugin taking part, nothing needs stitching. svhier calls its `elaborate` once
and uses the result as it is, so a pure SystemVerilog run does no extra work.

### Several languages

Each compiler can only elaborate its own language. To slang, a VHDL entity is an unknown
module. To GHDL, an SV module is a component with nothing bound to it. So each plugin
elaborates what it can and reports the names it couldn't resolve, and core passes those
names to the plugin that defines them:

```text
          ┌──────────────────────────────────────────────┐
          ▼                                              │
  each plugin elaborates its roots                       │
          │                                              │
          ▼                                              │
  it reports names it can't resolve,                     │
  with any parameter values                              │
          │                                              │
          ▼                                              │
  which plugin defines each name?                        │
    in its sources, or else under -y                     │
          │                                              │
          ├─ new root for some plugin ───────────────────┘
          │
          └─ nothing new ─► merge
```

A *root* is a name to elaborate, plus any parameter values. Before the first round, core
asks each plugin which units its sources define, and gives each `--top` to the plugin
defining it. A plugin only runs again if its roots changed. Stitching stops after 20
rounds at most.

Here's a real run on `plugins/svhier-ghdl/tests/mixed` with `--top sv_top`:

| Round | Plugin | Roots | Can't resolve |
|-------|--------|-------|---------------|
| 1 | GHDL | none | nothing |
| 1 | slang | `sv_top` | `Top_Vhd` with `N=3` |
| 2 | GHDL | `Top_Vhd` with `N=3` | `sv_child`, `sv_req` with `DEPTH=6`, `nowhere` |
| 3 | slang | `sv_top`, `sv_child`, `sv_req` with `DEPTH=6` | `vhdl_bottom`, `Top_Vhd` with `N=3` |
| 4 | GHDL | `Top_Vhd` with `N=3`, `vhdl_bottom` | `sv_child`, `sv_req` with `DEPTH=6`, `nowhere` |

After round 4 there are no new roots, so the loop ends. `N=3` is what gives `g_for` its
three copies, and `DEPTH=6` is GHDL's evaluated value of `N * 2`, which `sv_req` needs
because it has no default. No plugin defines `nowhere`, so it stays in the "Not found"
table.

When the results are merged, core removes each compiler's complaints about names another
language defines. It also settles on one spelling for each name, because VHDL names are
case-insensitive.

### Library directories

`-y` is a core option because a directory can hold several languages, and an instance in
one language can need a file in another. Each plugin indexes the directories for its own
file types, and pulls in only the files that define something missing.

Two rules keep unused files out:

- **Sources win over libraries.** A plugin leaves names that another language's sources
  define (`Request.defined_elsewhere`) to that language.
- **Listing what the sources define doesn't search `-y`.** That list covers every source,
  including modules outside `--top`'s tree, so searching for them would pull in files
  nothing uses.

## Inside a plugin

Both plugins are built from the same three parts:

```text
 ┌───────────────────────────────────┐
 │ Frontend                          │  what core talks to
 │  ├── tool             (injected)  │  the only code that touches the compiler
 │  └── library_factory  (injected)  │  builds the index of what -y holds
 └───────────────────────────────────┘
```

Both follow the same steps, but each compiler works differently:

| Step | slang | GHDL |
|------|-------|------|
| List what the sources define | Parse them with `-y` turned off | A worker run that only scans, without elaborating |
| Run the compiler | In-process through pyslang | In a child process, because libghdl keeps global state and can abort the process |
| One run covers | One parameter setting, since `-G` applies to a whole compilation | One top and one setting, cached |
| Get the hierarchy | Walk slang's elaborated objects | Parse the text of GHDL's hierarchy dump |
| Pull from `-y` | Re-parse and pull what's reported missing, until nothing new resolves | Follow `use` and `entity` references first, then pull unbound instances and run again |
| Find which files a top needs | slang's dependency trimming | The files GHDL's runs reached |
| Find file dependencies | Instances, plus references in the syntax tree | The dependencies GHDL recorded while analysing |

### The slang plugin

```text
 roots ─► one compilation per parameter setting
                    │
                    ▼
          ┌─ slang Driver loads the sources ◄───┐
          │         │                           │
          │         ▼                           │
          │  elaborate into a Compilation       │
          │         │                           │
          │         ▼                           │ files or include dirs added
          │  walk instances, collect missing ───┴── from -y, up to 20 times
          │         │
          └─────────┼─ next setting
                    ▼
       merge the settings into one fragment
```

Rounds for `-y` are cheap, because only the last one is built into a result. A round walks
the instances, since some missing names only show up while the design is elaborated, and
reports what is missing and which files are loaded. Everything else waits until a round
pulls nothing new: the definitions, the file entries with their references, the duplicates,
the diagnostics and the trimmed file list. So the diagnostics describe the final load only.

The walk visits each unique pair of definition and parameter values once, and records the
children of that pair, each with the parameter values it uses in turn. That table is what
the instance tree follows, so a design's tree costs one step per real instance however
often its modules repeat. `insts` merges the children per definition, so there a module
whose generate branches depend on its parameters lists those of every setting it's used
with.

### The GHDL plugin

```text
 files ─► worker: scan ─► choose a library for each file
                                   │
                                   ▼
                    pull what roots need from -y
                                   │
                                   ▼
 ┌─► load files once, then for each top and setting, in a forked child:
 │     elaborate ─► print file order and hierarchy text
 │                                 │
 │                                 ▼
 │          parse the text ─► one definition per entity
 │                                 │
 └── unbound instance found under -y
                                   │ nothing new
                                   ▼
                               fragment
```

pyGHDL has no elaboration API, so the worker binds GHDL internals by symbol name. Keeping
that in a child process limits the damage from a crash or from a GHDL release that
changes them. libghdl can't elaborate twice in one process, so each setting gets a child
forked from a worker holding the loaded files. Without `--top` that's one per entity, and
forking saves starting Python and libghdl for each. Where the platform can't do this, or
that worker fails, each setting gets a process of its own instead, with the same results.

## The result

Every plugin returns the same dict shape, so everything after stitching is independent of
language:

| Output | Built from | Code |
|--------|-----------|------|
| Instance tree | `files[].defs[].variants`, or `insts` for a plugin without them, starting from the tops | {func}`~svhier.cli.iter_tree` |
| "Not found" table | `missing` | {func}`~svhier.cli._print_missing` |
| "Defined more than once" table | `duplicates` | {func}`~svhier.cli._print_duplicates` |
| "Pulled in from -y" table | `pulled` | {func}`~svhier.cli._print_pulled` |
| YAML | `files`, without internal keys | {func}`~svhier.cli._prepare_for_yaml` |
| Filelist | a graph of file dependencies, plus `inc_dirs` and `defines` | {func}`~svhier.filelist.compute_filelist` |

The keys are described in [Result format](dev/result-format.md).

The tree follows parameter settings rather than `insts` because expanding merged lists at
every level multiplies them. On OpenTitan's `top_earlgrey` that gave 77,753,919 lines, where
the elaborated design has 38,350 module instances. It's also why the tree is printed last,
one line at a time and without Rich, whose `Tree` took 62 seconds to render 500,000 nodes.

## Design rules

- **Core names no language.** A test checks that `cli.py` doesn't mention slang or GHDL.
  The one exception is `KNOWN_SUFFIXES`, the install hint in `frontends.py`, because a
  plugin that isn't installed can't report that itself.
- **Compilers do the understanding.** Regexes only choose which library files to look at.
  The compiler still parses whatever gets pulled in.
- **A bad design isn't a crash.** Plugins report problems as diagnostics. The tree is
  still printed, and the exit code is 1 if there are errors.
- **stdout is for data.** Tables, warnings and the spinner go to stderr, so output can be
  piped.
