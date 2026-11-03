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
| `missing` | `{kind: {name: [referencing files]}}`, with kinds `modules`, `interfaces`, `packages`, `includes` and `macros` | No |

## Keys only plugins and stitching use

| Key | Set by | Contents |
|-----|--------|----------|
| `unresolved` | Plugin | `{name: [{"generics": {...}, "unresolved": [text]}]}` for names it has no definition for. `generics` holds the values in {class}`~svhier.frontends.Root`'s notation, and `unresolved` describes values with no such form |
| `rejected_roots` | Plugin | Roots it can't elaborate with what's known about them |
| `notes` | Plugin | Remarks for the user |
| `pulled` | Plugin | `(name, path, other candidates)` for each file taken from `-y` |
| `frontend_notes` | Stitching | All notes, printed at the end |

## File entries

| Key | Contents | In YAML |
|-----|----------|---------|
| `file_name` | The path | Yes |
| `library` | The VHDL library. Only VHDL files have it | Yes |
| `defs` | One entry per module or entity: `mod_name`, `insts`, and `pkg_imports` when not empty | Yes |
| `pkgs`, `ifaces` | Packages and interfaces defined here | No |
| `shadowed` | Names defined here, where another file's copy is used | No |
| `refs` | Packages and interfaces this file references, from the syntax tree | No |
| `depends_on` | Files this one must be compiled after. Only VHDL files have it | No |

Each entry in `insts` is `{"mod_name": ..., "inst_name": ...}`. `inst_name` is the path
relative to the parent module, such as `g_loop[0].u_core` or `u_arr[1]`.
{func}`~svhier.cli._prepare_for_yaml` removes the internal keys.

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
