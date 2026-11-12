# Result format

{func}`svhier.parser.parse_files` defines the dict everything else works on. Plugins
return the same shape with a few extra keys, called a *fragment* here, and stitching
merges the fragments into one result. {func}`~svhier.frontends.empty_fragment` gives you
an empty one.

## Top-level keys

| Key | Contents | In YAML |
|-----|----------|---------|
| `files` | One entry per source file (see below) | Yes, without internal keys |
| `duplicates` | `{name, kind, files, used}` for each name defined in several files | No |
| `tops` | The tops that were asked for | No |
| `needed` | With tops, the files they need. Otherwise `None` | No |
| `defines`, `inc_dirs` | Macros and include directories, for the filelist | No |
| `diagnostics` | `{severity, file, message}` dicts | No |
| `has_errors` | Whether any diagnostic is an error, which makes the exit code 1 | No |
| `missing` | `{kind: {name: [referencing files]}}`, with kinds `modules`, `interfaces`, `packages`, `units` (VHDL units, as `library.unit` outside the work library), `includes` and `macros` | No |

## Keys only plugins and stitching use

| Key | Set by | Contents |
|-----|--------|----------|
| `unresolved` | Plugin | `{name: [{"generics": {...}, "unresolved": [text]}]}` for names it has no definition for. `generics` holds the values in {class}`~svhier.frontends.Root`'s notation, and `unresolved` describes values with no such form |
| `rejected_roots` | Plugin | Roots it can't elaborate with what's known about them |
| `notes` | Plugin | Remarks for the user |
| `failed_tops` | Plugin | Optional. Requested roots it defines but couldn't elaborate, so they aren't reported as not found |
| `pulled` | Plugin | `(name, path, other candidates)` for each file taken from `-y` |
| `frontend_notes` | Stitching | All notes, printed at the end |

## File entries

| Key | Contents | In YAML |
|-----|----------|---------|
| `file_name` | The path | Yes |
| `library` | The VHDL library. Only VHDL files have it | Yes |
| `defs` | One entry per module or entity: `mod_name`, `insts`, and `pkg_imports` when not empty. Also `variants` and `top_variant`, which aren't in YAML (see below) | Yes, without internal keys |
| `pkgs`, `ifaces` | Packages and interfaces defined here | No |
| `shadowed` | Names defined here, where another file's copy is used | No |
| `refs` | Packages and interfaces this file references, from the syntax tree | No |
| `depends_on` | Files this one must be compiled after. Only VHDL files have it | No |

Each entry in `insts` is `{"mod_name": ..., "inst_name": ...}`. `inst_name` is the path
relative to the parent module, such as `g_loop[0].u_core` or `u_arr[1]`.
{func}`~svhier.cli._prepare_for_yaml` removes the internal keys.

(children-per-setting)=
## Children per parameter setting

`insts` merges the children of all parameter settings of a definition. That's the
documented YAML output and what the filelist needs, but it can't give an instance its own
children, and expanding it at every level multiplies the lists. So a definition can also
say what each setting elaborates:

| Key | Contents |
|-----|----------|
| `variants` | `{setting: [child]}`, where each child is `{"mod_name", "inst_name", "variant"}` and `variant` is the setting that child uses, or `None` if it isn't known, as for a blackbox or a unit of another language |
| `top_variant` | The setting a top was elaborated with. Only tops have it |

A setting is an opaque hashable key, which only has to be the same wherever the same
setting is meant. The slang plugin uses the tuple of `name=value` strings of
`_param_signature`. Merging compilations keeps the first copy of each setting and the first
`top_variant`, and the plain compilation comes first, so a `--top` shows its own setting.

These keys are optional and internal: a plugin needn't supply them, and
{func}`svhier.parser.parse_files` only returns them with `frontend_info=True`.
{func}`~svhier.cli.iter_tree` picks an instance's children like this:

1. If the instance names a setting the definition has, that setting's children.
2. If it names none and the definition has exactly one setting, that one's children.
3. Otherwise the merged `insts`. That's every instance of a definition without `variants`.

A line is marked `(cycle)` if the same definition is already being expanded with the same
setting, or with the merged `insts` in both places. So recursion that parameters end is
shown in full, and merged data stops at the first repeat of a name.

Instances with the same setting are assumed to elaborate the same way, as the walk visits
just the first. A `defparam` or `bind` reaching into only some of them, or a generate
condition reading a connected interface's parameters, isn't seen. `insts` has the same limit.

## What the hierarchy leaves out

- **Packages and interfaces** aren't `defs`. Interface instances aren't `insts` either,
  because slang gives them the same symbol kind as modules with `isModule == False`.
  They still count as filelist dependencies through `refs`.
- **Untaken generate branches** aren't listed: instances with `isUninstantiated == True`
  are skipped.
- **Modules nothing instantiates** appear with an empty `insts` list if no walk reaches
  them.

## Blackboxes

An instance of an undefined module is an `UninstantiatedDef` symbol in slang, not an
`Instance`. `_iter_module_instances(..., blackboxes=True)` yields these when `insts` is
built, using `definitionName` as `mod_name`. The walk doesn't descend into them. Their
parameters are recorded as `blackbox_params`, which become `unresolved` in the fragment.

`missing` comes from slang's `UnknownModule`, `UnknownInterface`, `UnknownPackage`,
`UnknownClassOrPackage`, `CouldNotOpenIncludeFile` and `UnknownDirective` diagnostics.

## Duplicate definitions

Definitions are keyed by `(name, file)`. A name defined in several files gets a
`duplicates` entry naming every file and the copy slang uses (`used`). The other copies
are `shadowed` in their file entry: they aren't elaborated, so they have no `insts`, and
{func}`~svhier.filelist.compute_filelist` resolves the name to the used copy.

## Filelist order

{func}`~svhier.filelist.compute_filelist` builds a graph of file dependencies from
instantiations, package imports, `refs` and `depends_on`, then sorts it topologically. If
the files have a real cycle, it returns the original input order instead of raising an
error.

With `--top`, `needed` decides which files to keep. For SystemVerilog, it comes from
slang's dependency trimming (`--depfile-trim`), which follows module, interface, package
and class references. For VHDL, it's the files GHDL's runs reached.
