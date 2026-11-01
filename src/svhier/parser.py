# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Stefan Engert

"""Parse SystemVerilog files with pyslang and extract the module/instance hierarchy.

Elaboration works in two phases:

1. **Syntax phase** — slang's ``Driver`` loads every input (source files, ``-f``/``-F``
   command files with ``${VAR}`` expansion, ``+incdir+``, ``+define+``, ``--top``) into one
   ``Compilation``, so cross-file module instantiation and package imports resolve.
2. **Elaboration phase** — ``compilation.getRoot().topInstances`` seeds a depth-first walk
   (:func:`_walk_instances`) that visits every unique (definition, parameter values) pair
   once.  Definitions are keyed by ``(name, file)``, so duplicate module names in
   different files stay apart.  Child lists are merged per definition, so a module whose
   generate branches depend on its parameters lists the children of every setting.

Modules that are defined but never instantiated are unreachable via ``topInstances``
and appear in the output with an empty ``insts`` list.
"""

import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import networkx as nx
from pyslang import DiagnosticEngine, Diags, SourceManager, TextDiagnosticClient
from pyslang.ast import (
    Compilation,
    DefinitionKind,
    DefinitionSymbol,
    InstanceBodySymbol,
    InstanceSymbol,
    SymbolKind,
)
from pyslang.driver import Driver
from pyslang.syntax import SyntaxKind, SyntaxTree

_IMPORT_KINDS = (SymbolKind.WildcardImport, SymbolKind.ExplicitImport)
# Scopes that can hold module instances without being a module themselves.
_CONTAINER_KINDS = (SymbolKind.GenerateBlock, SymbolKind.GenerateBlockArray, SymbolKind.InstanceArray)
_COMMAND_FILE_FLAGS = ("-f", "-F")

# Diagnostic codes that mean "something referenced was not found", mapped to the
# category it is reported under.  The first diagnostic argument is the missing name.
_MISSING_CODES = {
    Diags.UnknownModule: "modules",
    Diags.UnknownInterface: "interfaces",
    Diags.UnknownPackage: "packages",
    Diags.UnknownClassOrPackage: "packages",
    Diags.CouldNotOpenIncludeFile: "includes",
    Diags.UnknownDirective: "macros",
}
MISSING_KINDS = ("modules", "interfaces", "packages", "includes", "macros")

# (name, canonical file) — identifies one definition even when the name is reused.
DefKey = tuple[str, str]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _canon(sm: SourceManager, loc) -> str:
    """Return the resolved path of the file that *loc* (possibly inside a macro) belongs to."""
    return str(Path(sm.getFullPath(sm.getFullyOriginalLoc(loc).buffer)).resolve())


def _definitions_by_file(compilation: Compilation, sm: SourceManager, kind) -> dict[str, list[DefinitionSymbol]]:
    """Return canonical file -> ``DefinitionSymbol`` s of *kind* defined there (``getDefinitions`` order)."""
    by_file: dict[str, list[DefinitionSymbol]] = {}
    for defn in compilation.getDefinitions():
        if defn.definitionKind == kind:
            by_file.setdefault(_canon(sm, defn.location), []).append(defn)
    return by_file


def _packages_by_file(compilation: Compilation, sm: SourceManager) -> dict[str, list[str]]:
    """Return canonical file -> package names defined there (duplicates in other files included)."""
    by_file: dict[str, list[str]] = {}
    for cu in compilation.getRoot().compilationUnits:
        for child in cu:
            if child.kind == SymbolKind.Package:
                by_file.setdefault(_canon(sm, child.location), []).append(child.name)
    return by_file


def _collect_syntax_refs(tree: SyntaxTree) -> set[str]:
    """Return candidate package / interface names referenced anywhere in *tree*.

    Collected from the syntax (so untaken generate branches and unelaborated modules
    count too): ``pkg::name`` prefixes, ``import pkg::*`` items, interface port headers
    (``bus_if.mst p``), plain named types (``bus_if p``) and instantiation type names.
    The caller filters the result down to names that are actually defined.
    """
    refs: set[str] = set()

    def _visit(node) -> None:
        kind = node.kind
        if kind == SyntaxKind.ScopedName:
            if node.separator.valueText == "::" and hasattr(node.left, "identifier"):
                refs.add(node.left.identifier.valueText)
        elif kind == SyntaxKind.PackageImportItem:
            refs.add(node.package.valueText)
        elif kind == SyntaxKind.InterfacePortHeader:
            refs.add(node.nameOrKeyword.valueText)
        elif kind == SyntaxKind.HierarchyInstantiation:
            refs.add(node.type.valueText)
        elif kind == SyntaxKind.NamedType and node.name.kind == SyntaxKind.IdentifierName:
            refs.add(node.name.identifier.valueText)

    tree.root.visit(_visit)
    return refs


def _collect_imports(body: InstanceBodySymbol) -> list[str]:
    """Return deduplicated package names imported by an instance body."""
    imports: list[str] = []
    for child in body:
        if child.kind in _IMPORT_KINDS:
            pkg = child.packageName
            if pkg and pkg not in imports:
                imports.append(pkg)
    return imports


def _iter_module_instances(scope, *, blackboxes: bool = False) -> Iterator:
    """Yield every module instance elaborated directly under *scope*.

    Descends into generate blocks, generate-for arrays and instance arrays, but not
    into the bodies of the instances it yields.  Generate branches that were not
    taken (``isUninstantiated``) are skipped.  Interface instances are skipped via
    the ``isModule`` guard (they share ``SymbolKind.Instance`` with module instances).

    With *blackboxes*, instances of modules that have no definition
    (``UninstantiatedDef`` symbols) are yielded too.
    """
    for child in scope:
        if child.kind == SymbolKind.Instance:
            if child.isModule:
                yield child
        elif child.kind == SymbolKind.UninstantiatedDef:
            if blackboxes:
                yield child
        elif child.kind in _CONTAINER_KINDS:
            if child.kind == SymbolKind.GenerateBlock and child.isUninstantiated:
                continue
            yield from _iter_module_instances(child, blackboxes=blackboxes)


def _param_signature(body: InstanceBodySymbol) -> tuple[str, ...]:
    """Return the parameter values of *body* as strings, used to spot repeated settings."""
    sig = []
    for param in body.parameters:
        if param.kind == SymbolKind.TypeParameter:
            sig.append(f"{param.name}={param.targetType.type}")
        else:
            sig.append(f"{param.name}={param.value}")
    return tuple(sig)


@dataclass
class _Walk:
    """Result of walking the elaborated tree, keyed by :data:`DefKey`."""

    sm: SourceManager
    insts: dict[DefKey, list[InstanceSymbol]] = field(default_factory=dict)
    imports: dict[DefKey, list[str]] = field(default_factory=dict)

    def key(self, defn: DefinitionSymbol) -> DefKey:
        """Return the :data:`DefKey` of *defn*."""
        return (defn.name, _canon(self.sm, defn.location))


def _walk_instances(tops, sm: SourceManager) -> _Walk:
    """Depth-first walk of the elaborated instance tree below *tops*.

    Every unique (definition, parameter values) pair is visited once; its instance is
    appended to ``walk.insts[key]`` and its imports merged into ``walk.imports[key]``.
    Instances with parameters already seen are skipped, since they elaborate the same
    way.  Iterative, so deep hierarchies cannot hit Python's recursion limit.
    """
    walk = _Walk(sm)
    seen: set[tuple[DefKey, tuple[str, ...]]] = set()
    stack: list[InstanceSymbol] = list(reversed(list(tops)))
    while stack:
        inst = stack.pop()
        key = walk.key(inst.definition)
        sig = (key, _param_signature(inst.body))
        if sig in seen:
            continue
        seen.add(sig)
        walk.insts.setdefault(key, []).append(inst)
        imports = walk.imports.setdefault(key, [])
        imports.extend(p for p in _collect_imports(inst.body) if p not in imports)
        stack.extend(reversed(list(_iter_module_instances(inst.body))))
    return walk


def _collect_missing(compilation: Compilation, sm: SourceManager, name_of: dict[str, str]) -> dict[str, dict[str, list[str]]]:
    """Return everything slang could not find, grouped by kind.

    Maps each of :data:`MISSING_KINDS` to ``{name: [referencing files]}``; names and
    files are kept in first-seen order and deduplicated.  *name_of* maps canonical
    paths to the names files are reported under.  Unknown modules are the
    blackboxes of the design (instantiated but never defined).
    """
    missing: dict[str, dict[str, list[str]]] = {kind: {} for kind in MISSING_KINDS}
    for diag in compilation.getAllDiagnostics():
        kind = _MISSING_CODES.get(diag.code)
        if kind is None or not diag.args:
            continue
        name = str(diag.args[0])
        if kind == "macros":
            name = name.lstrip("`")
        files = missing[kind].setdefault(name, [])
        if diag.location:
            canonical = _canon(sm, diag.location)
            fname = name_of.get(canonical, canonical)
            if fname not in files:
                files.append(fname)
    return missing


def _collect_diagnostics(
    compilation: Compilation,
    sm: SourceManager,
) -> tuple[list[dict], bool]:
    """Issue all compilation diagnostics through a DiagnosticEngine.

    Returns (diagnostics, has_errors) where diagnostics is a list of dicts
    with keys ``severity``, ``file``, and ``message``, and has_errors is True
    if any error-level diagnostic was issued.
    """
    engine = DiagnosticEngine(sm)
    client = TextDiagnosticClient()
    engine.addClient(client)

    diag_list: list[dict] = []
    for diag in compilation.getAllDiagnostics():
        engine.issue(diag)
        diag_list.append({
            "severity": "error" if diag.isError() else "warning",
            "file": sm.getFileName(diag.location) if diag.location else "",
            "message": engine.formatMessage(diag),
        })

    return diag_list, engine.numErrors > 0


def _build_def_entry(defn: DefinitionSymbol, walk: _Walk) -> dict:
    """Build the output dict for a single module definition.

    ``inst_name`` is the child's hierarchical path relative to the parent module,
    so instances inside generate blocks or instance arrays get names like
    ``g_loop[0].u_core`` or ``u_arr[1]``.  Children of every walked parameter
    setting are merged, first seen first, without repeats.
    """
    key = walk.key(defn)
    insts: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for parent in walk.insts.get(key, []):
        prefix = parent.hierarchicalPath + "."
        for child in _iter_module_instances(parent.body, blackboxes=True):
            path = child.hierarchicalPath
            inst_name = path[len(prefix):] if path.startswith(prefix) else child.name
            if child.kind == SymbolKind.UninstantiatedDef:
                mod_name = child.definitionName
            else:
                mod_name = child.definition.name
            if (mod_name, inst_name) not in seen:
                seen.add((mod_name, inst_name))
                insts.append({"mod_name": mod_name, "inst_name": inst_name})
    return {"mod_name": defn.name, "pkg_imports": walk.imports.get(key, []), "insts": insts}


def _quote(arg: str) -> str:
    """Quote *arg* for slang's command-line parser."""
    return '"' + arg.replace("\\", "\\\\").replace('"', '\\"') + '"'


@dataclass
class _Inputs:
    """Everything that goes on slang's command line."""

    files: list[str]
    inc_dirs: list[str]
    defines: list[str]
    tops: list[str]
    command_files: list[tuple[str, str]]

    def args(self) -> list[str]:
        """Return the slang command line (without the program name)."""
        # Defaults matching a plain ``Compilation``: a fixed default timescale, and a
        # top module with interface ports (a sub-design parsed alone) is not an error.
        args = ["--timescale", "1ns/1ns", "--allow-toplevel-iface-ports", *self.files]
        for flag, values in (("-I", self.inc_dirs), ("-D", self.defines), ("--top", self.tops)):
            for value in values:
                args += [flag, value]
        for flag, path in self.command_files:
            if flag not in _COMMAND_FILE_FLAGS:
                raise ValueError(f"command file flag must be -f or -F, got {flag!r}")
            args += [flag, path]
        return args


def _run_driver(inputs: _Inputs, extra: list[str] | None = None) -> tuple[Driver, Compilation, list[str]]:
    """Load *inputs* with slang's ``Driver``; return the driver, compilation and load errors.

    Raises ``ValueError`` if slang rejects the command line or a command file
    (slang prints the reason to stderr).
    """
    driver = Driver()
    driver.addStandardArgs()
    cmdline = " ".join(_quote(a) for a in ["svhier", *inputs.args(), *(extra or [])])
    if not (driver.parseCommandLine(cmdline) and driver.processOptions()):
        raise ValueError("slang rejected the inputs (see messages above)")
    load_errors = [] if driver.parseAllSources() else list(driver.sourceLoader.errors)
    return driver, driver.createCompilation(), load_errors


def _trimmed_files(driver: Driver, depfile: Path) -> set[str]:
    """Return the canonical source files the requested tops need.

    The driver must have been given ``--top``, ``--depfile-trim`` and ``--Mmodule <depfile>``.
    slang's trimming follows module, interface, package and class references, so it is the
    authority on which files a top needs.
    """
    driver.optionallyWriteDepFiles()
    if not depfile.exists():
        return set()
    return {str(Path(line.strip()).resolve()) for line in depfile.read_text().splitlines() if line.strip()}


def _include_dirs(trees: list[SyntaxTree], sm: SourceManager) -> list[str]:
    """Return the directories that the resolved `` `include`` directives were found relative to.

    For `` `include "sub/x.svh"`` resolved to ``/a/inc/sub/x.svh`` that is ``/a/inc``.
    """
    dirs: list[str] = []
    for tree in trees:
        for inc in tree.getIncludeDirectives():
            parts = Path(inc.path).parts
            full = sm.getFullPath(inc.buffer.id) if inc.buffer else ""
            if not full or not parts or Path(inc.path).is_absolute() or ".." in parts:
                continue
            base = str(Path(full).parents[len(parts) - 1])
            if base not in dirs:
                dirs.append(base)
    return dirs


@dataclass
class _Tables:
    """What each input file defines, and which copy of each duplicated name slang used."""

    modules: dict[str, list[DefinitionSymbol]]
    ifaces: dict[str, list[DefinitionSymbol]]
    pkgs: dict[str, list[str]]
    used: dict[tuple[str, str], str]  # (kind, name) -> canonical file of the copy slang uses


def _build_tables(compilation: Compilation, sm: SourceManager) -> _Tables:
    """Collect what each file defines and look up which copy slang uses for each name."""
    modules = _definitions_by_file(compilation, sm, DefinitionKind.Module)
    ifaces = _definitions_by_file(compilation, sm, DefinitionKind.Interface)
    pkgs = _packages_by_file(compilation, sm)
    used: dict[tuple[str, str], str] = {}
    root = compilation.getRoot()
    for table, kind in ((modules, "module"), (ifaces, "interface")):
        for defs in table.values():
            for defn in defs:
                found = compilation.tryGetDefinition(defn.name, root).definition
                if found is not None:
                    used[(kind, defn.name)] = _canon(sm, found.location)
    for names in pkgs.values():
        for name in names:
            pkg = compilation.getPackage(name)
            if pkg is not None:
                used[("package", name)] = _canon(sm, pkg.location)
    return _Tables(modules, ifaces, pkgs, used)


def _defined_names(tables: _Tables, canonical: str) -> list[tuple[str, str]]:
    """Return ``(kind, name)`` for everything *canonical* defines."""
    return ([("module", d.name) for d in tables.modules.get(canonical, [])]
            + [("interface", d.name) for d in tables.ifaces.get(canonical, [])]
            + [("package", n) for n in tables.pkgs.get(canonical, [])])


def _find_duplicates(tables: _Tables, files: list[tuple[str, str]]) -> list[dict]:
    """Return one entry per name defined in more than one input file (see :func:`parse_files`)."""
    where: dict[tuple[str, str], list[str]] = {}
    name_of = {canonical: file_name for file_name, canonical in files}
    for _, canonical in files:
        for item in _defined_names(tables, canonical):
            if canonical not in where.setdefault(item, []):
                where[item].append(canonical)
    dups = []
    for (kind, name), canonicals in where.items():
        if len(canonicals) > 1:
            used = tables.used.get((kind, name), canonicals[-1])
            dups.append({"name": name, "kind": kind,
                         "files": [name_of[c] for c in canonicals],
                         "used": name_of.get(used, used)})
    return dups


def _collect_input_files(driver: Driver, file_paths: list[str]) -> list[tuple[str, str, SyntaxTree]]:
    """Return ``(file_name, canonical, tree)`` per loaded source file, in load order.

    Files given in *file_paths* keep the caller's string; files that came from a
    command file are named by the absolute path slang loaded.
    """
    sm = driver.sourceManager
    explicit = {str(Path(p).resolve()): p for p in file_paths}
    out = []
    for tree in driver.syntaxTrees:
        loc = tree.root.getLastToken().location  # EOF token: always in the file itself
        full = sm.getFullPath(sm.getFullyOriginalLoc(loc).buffer)
        canonical = str(Path(full).resolve())
        out.append((explicit.get(canonical, str(full)), canonical, tree))
    return out


def _build_file_entries(walk: _Walk, tables: _Tables, files: list[tuple[str, str, SyntaxTree]]) -> list[dict]:
    """Build one output entry per input file (see :func:`parse_files`)."""
    known = {n for names in tables.pkgs.values() for n in names}
    known |= {d.name for defs in tables.ifaces.values() for d in defs}
    entries = []
    for file_name, canonical, tree in files:
        pkgs = tables.pkgs.get(canonical, [])
        ifaces = [d.name for d in tables.ifaces.get(canonical, [])]
        shadowed = sorted({name for kind, name in _defined_names(tables, canonical)
                           if tables.used.get((kind, name), canonical) != canonical})
        entries.append({
            "file_name": file_name,
            "pkgs": pkgs,
            "ifaces": ifaces,
            "shadowed": shadowed,
            "refs": sorted((_collect_syntax_refs(tree) & known) - set(pkgs) - set(ifaces)),
            "defs": [_build_def_entry(d, walk) for d in tables.modules.get(canonical, [])],
        })
    return entries


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_files(  # pylint: disable=too-many-arguments
    file_paths: list[str],
    inc_dirs: list[str] | None = None,
    *,
    defines: list[str] | None = None,
    tops: list[str] | None = None,
    command_files: list[tuple[str, str]] | None = None,
) -> dict:
    """Parse SystemVerilog sources and return the module/instance hierarchy.

    Inputs are handed to slang's ``Driver``, so everything it understands works:

    * *file_paths* — source files.
    * *inc_dirs* — `` `include`` search directories (``-I``).
    * *defines* — macro definitions, ``NAME`` or ``NAME=VALUE`` (``-D``).
    * *tops* — top module names (``--top``).  When given, only these are elaborated
      and :func:`compute_filelist` keeps only the files they need.
    * *command_files* — ``("-f", path)`` or ``("-F", path)`` pairs.  Command files may
      use ``+incdir+``, ``+define+``, nested ``-f``/``-F`` and ``${VAR}`` environment
      variables.  ``-F`` resolves paths relative to the command file, ``-f`` relative to
      the working directory.

    See the module docstring for the elaboration model.  Raises ``ValueError`` if slang
    rejects the inputs (for example a missing command file).

    Returns a dict with the following top-level keys:

    ``files``
        One entry per loaded source file, in load order, each containing:

        * ``file_name`` — the caller's string for files in *file_paths*, the absolute
          path for files that came from a command file.
        * ``pkgs`` — package names defined in this file (internal bookkeeping,
          stripped from YAML output by :func:`svhier.cli._prepare_for_yaml`).
        * ``ifaces`` — interface names defined in this file (internal).
        * ``shadowed`` — names defined here that are also defined in another file,
          where slang uses the other copy (internal).
        * ``refs`` — sorted package / interface names defined in *other* input
          files that this file references, by import, ``pkg::name``, interface
          port or interface instance (internal; used by :func:`compute_filelist`).
        * ``defs`` — list of module definitions, each with ``mod_name``,
          ``pkg_imports`` (omitted from YAML when empty), and ``insts``
          (list of ``{"mod_name": str, "inst_name": str}``).  A shadowed copy of a
          module is not elaborated and has no ``insts``.

    ``duplicates``
        One ``{"name", "kind", "files", "used"}`` dict per module, interface or package
        name defined in more than one file.  ``kind`` is ``module``, ``interface`` or
        ``package``; ``used`` is the file slang uses.

    ``tops``
        The *tops* that were requested (empty when tops were found automatically).

    ``needed``
        With *tops*: the ``file_name`` s the tops need, from slang's dependency trimming.
        Otherwise ``None``.

    ``defines``
        All macro definitions in effect: *defines* plus ``+define+`` from command files.

    ``inc_dirs``
        Directories that `` `include`` directives were actually resolved relative to.

    ``diagnostics``
        List of ``{"severity": str, "file": str, "message": str}`` dicts from
        pyslang's ``DiagnosticEngine`` (internal; stripped from YAML output).
        Files that could not be loaded are reported here as errors.

    ``has_errors``
        ``True`` if any error-level diagnostic was issued.  The CLI exits with
        code 1 when this is set.

    ``missing``
        What slang could not find, as ``{kind: {name: [referencing files]}}``
        for each kind in :data:`MISSING_KINDS` (see :func:`_collect_missing`).
        Instances of missing modules (blackboxes) are still listed in ``insts``.
    """
    inputs = _Inputs(list(file_paths), list(inc_dirs or []), list(defines or []),
                     list(tops or []), list(command_files or []))
    with tempfile.TemporaryDirectory() as tmp:
        return _elaborate(inputs, Path(tmp) / "modules.d")


def _elaborate(inputs: _Inputs, depfile: Path) -> dict:
    """Run slang on *inputs* and build the :func:`parse_files` result.

    *depfile* is a scratch path for slang's trimmed module list (used with tops only).
    """
    extra = ["--Mmodule", str(depfile), "--depfile-trim"] if inputs.tops else []
    driver, compilation, load_errors = _run_driver(inputs, extra)
    sm = driver.sourceManager
    files = _collect_input_files(driver, inputs.files)
    tables = _build_tables(compilation, sm)
    walk = _walk_instances(compilation.getRoot().topInstances, sm)
    needed = _trimmed_files(driver, depfile) if inputs.tops else None
    diags, has_errors = _collect_diagnostics(compilation, sm)
    pairs = [(f, c) for f, c, _ in files]
    return {
        "files": _build_file_entries(walk, tables, files),
        "duplicates": _find_duplicates(tables, pairs),
        "tops": inputs.tops,
        "needed": None if needed is None else [f for f, c in pairs if c in needed],
        "defines": list(driver.createOptionBag().preprocessorOptions.predefines),
        "inc_dirs": _include_dirs(driver.syntaxTrees, sm),
        "diagnostics": [{"severity": "error", "file": "", "message": e} for e in load_errors] + diags,
        "has_errors": has_errors or bool(load_errors),
        "missing": _collect_missing(compilation, sm, dict((c, f) for f, c in pairs)),
    }


def _file_deps(entry: dict, mod_to_file: dict[str, str], pkg_to_file: dict[str, str]) -> set[str]:
    """Return the other input files that the file *entry* depends on."""
    names = [(mod_to_file, i["mod_name"]) for d in entry["defs"] for i in d.get("insts", [])]
    names += [(pkg_to_file, p) for d in entry["defs"] for p in d.get("pkg_imports", [])]
    names += [(pkg_to_file, r) for r in entry.get("refs", [])]
    deps = {lookup[name] for lookup, name in names if name in lookup}
    deps.discard(entry["file_name"])
    return deps


def compute_filelist(result: dict) -> list[str]:
    """Return file paths in topological dependency order (dependencies first).

    Builds a ``networkx.DiGraph`` where an edge ``A → B`` means file A must be
    compiled before file B.  Edges come from three sources:

    * **Module instantiation** — a file that instantiates a module depends on the
      file that defines it.
    * **Package imports** — a file that imports a package depends on the file that
      defines it.
    * **Package / interface references** — a file whose ``refs`` name a package or
      interface (``pkg::x`` without an import, packages using other packages,
      interface ports and instances) depends on the file that defines it.

    A name defined in several files (see ``duplicates``) maps to the copy slang uses.
    When ``needed`` is set (``tops`` were given), files the tops do not need are left out.

    ``nx.topological_sort`` produces the compilation order.  If the graph contains
    a cycle the original input order is returned unchanged.

    *result* is the dict returned by :func:`parse_files`.
    """
    files = result["files"]

    mod_to_file: dict[str, str] = {}
    pkg_to_file: dict[str, str] = {}
    needed = result.get("needed")
    if needed is not None:
        keep = set(needed)
        files = [entry for entry in files if entry["file_name"] in keep]
    for entry in files:
        fname = entry["file_name"]
        shadowed = set(entry.get("shadowed", []))
        for pkg in [*entry.get("pkgs", []), *entry.get("ifaces", [])]:
            if pkg not in shadowed:
                pkg_to_file[pkg] = fname
        for d in entry["defs"]:
            if d["mod_name"] not in shadowed:
                mod_to_file[d["mod_name"]] = fname

    deps = {entry["file_name"]: _file_deps(entry, mod_to_file, pkg_to_file) for entry in files}

    g: nx.DiGraph = nx.DiGraph()
    g.add_nodes_from(deps.keys())
    for f, fdeps in deps.items():
        for d in fdeps:
            g.add_edge(d, f)

    try:
        return list(nx.topological_sort(g))
    except nx.NetworkXUnfeasible:
        return [entry["file_name"] for entry in files]
