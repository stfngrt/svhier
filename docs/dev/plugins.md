# Writing a plugin

A plugin adds a source language. It's a class registered in the `svhier.frontends` entry
point group, and svhier creates it with no arguments. The bundled slang plugin registers
the same way as any other:

```toml
[project.entry-points."svhier.frontends"]
ghdl = "svhier_ghdl:GhdlFrontend"
```

## The interface

{class}`~svhier.frontends.Frontend` is the full contract. svhier calls the methods in
this order:

| When | Member | What it should do |
|------|--------|-------------------|
| Loading | `name`, `suffixes`, `catch_all` | Identify the plugin, its file types, and whether it takes files with an unknown suffix. Only one plugin should set `catch_all`, and that's normally slang |
| Parsing options | `add_arguments(parser)` | Add its own options |
| | `configure(args)` | Read them, along with core's `paths`, `recursive`, `ignore` and `libdirs` |
| Routing | `unavailable_reason()` | Say why it can't run, or return `None`. svhier then disables it with a warning |
| | `extra_inputs()` | Describe inputs other than files, such as command files |
| | `has_library()` | Say whether `-y` holds anything for it |
| Stitching | `units(files)` | Return `{lowercase name: file}` for what the sources define, without searching `-y` |
| | `provide(name, files)` | Add the `-y` file defining `name` to `files`, and say whether there was one |
| | `elaborate(files, request)` | Elaborate what the {class}`~svhier.frontends.Request` asks for |
| | `drop_resolved(fragment, names)` | Remove complaints about names another plugin defines |

`units`, `provide` and `drop_resolved` are only called when several plugins take part.

## Rules for `elaborate`

- **Return a fragment.** Start from {func}`~svhier.frontends.empty_fragment`. The keys are
  in [Result format](result-format.md).
- **Don't raise for a bad design.** Report it in `diagnostics`. Only raise `ValueError`
  for inputs you can't use at all: svhier prints the message and exits with code 1.
- **Report what you can't resolve.** Put every instantiated name you have no definition
  for in `unresolved`, with the parameter values you know. That's how another plugin gets
  to elaborate it.
- **Don't search `-y` for `request.defined_elsewhere`.** Another language's sources define
  those names, and sources win over libraries.
- **Handle `request.auto`.** It means there's no `--top`, so also find your own roots.
  Without it, elaborate only `request.roots`.
- **Set `needed`.** With `--top`, list the files the roots need. With `auto`, use `None`.
- **`insts` is enough for the tree.** If your compiler can tell parameter settings apart,
  add {ref}`variants <children-per-setting>` so that each instance shows only its own
  children. Without them, every instance shows the merged `insts`.

An empty root list without `auto` is normal: it means `--top` only named other languages'
units so far. Return an empty fragment.

## Example

This plugin reads a made-up language where each line of a `.toy` file is
`module instance child`, and treats the file's name as the module's name. It's complete
enough to stitch with slang, apart from `-y`:

```python
from pathlib import Path

from svhier.frontends import Request, empty_fragment


class ToyFrontend:
    name = "toy"
    suffixes = (".toy",)
    catch_all = False

    def add_arguments(self, parser) -> None:
        pass

    def configure(self, args) -> None:
        pass

    def unavailable_reason(self) -> str | None:
        return None

    def extra_inputs(self) -> list[str]:
        return []

    def has_library(self) -> bool:
        return False

    def provide(self, name: str, files: list[str]) -> bool:
        return False

    def units(self, files: list[str]) -> dict[str, str]:
        return {Path(f).stem.lower(): f for f in files}

    def elaborate(self, files: list[str], request: Request) -> dict:
        units = self.units(files)
        result = empty_fragment()
        wanted = set(units) if request.auto else {r.name.lower() for r in request.roots}
        for name in wanted & set(units):
            lines = (line.split() for line in Path(units[name]).read_text().splitlines())
            insts = [{"mod_name": child, "inst_name": inst} for _, inst, child in lines]
            for inst in insts:
                if inst["mod_name"].lower() not in units:
                    result["unresolved"].setdefault(inst["mod_name"], [])
                    result["missing"]["modules"].setdefault(inst["mod_name"], []).append(units[name])
            result["files"].append({
                "file_name": units[name], "pkgs": [], "ifaces": [], "shadowed": [], "refs": [],
                "defs": [{"mod_name": name, "pkg_imports": [], "insts": insts}],
            })
        result["needed"] = None if request.auto else [f["file_name"] for f in result["files"]]
        return result

    def drop_resolved(self, fragment: dict, names: set[str]) -> None:
        for name in names:
            fragment["missing"]["modules"].pop(name, None)
```

This example only reaches direct children: a real plugin also follows instances of its
own modules, which its compiler normally does for it.

## Structure

The two existing plugins share one structure, and a new plugin should follow it:

| Part | slang | GHDL |
|------|-------|------|
| Frontend | {class}`~svhier.slang_frontend.SlangFrontend` | {class}`~svhier_ghdl.GhdlFrontend` |
| `tool`: the only code that touches the language tool | {class}`~svhier.slang_frontend.PyslangTool` | {class}`~svhier_ghdl.GhdlWorkerTool` |
| `library_factory`: what `-y` holds | {class}`~svhier.libsearch.LibraryIndex` | {class}`~svhier_ghdl.VhdlLibrary` |

Take both through the constructor, with defaults:

```python
def __init__(self, tool=None, library_factory=VhdlLibrary):
    self.tool = tool or GhdlWorkerTool()
    self._library_factory = library_factory
    self.search = LibrarySearch()
```

Then:

- **Tests don't need the compiler.** Inject a stand-in tool, as
  `tests/test_slang_frontend.py` and `plugins/svhier-ghdl/tests/test_ghdl_libdirs.py` do.
- **Import the compiler late.** Import it when the tool runs, and use `unavailable_reason`
  to report that it's missing, so svhier still starts without it.
- **Use {class}`~svhier.libsearch.LibrarySearch` for `-y`.** Build it in `configure` from
  anything implementing {class}`~svhier.libsearch.Library`. It pulls files and records them
  for the "Pulled in from -y" table.

Use the same method names too: `units` caches `_read_units`, and `elaborate` ends in
`_fragment`.
