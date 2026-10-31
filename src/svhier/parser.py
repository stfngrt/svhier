# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Stefan Engert

"""Parse SystemVerilog files with pyslang and extract the module/instance hierarchy.

Elaboration works in two phases:

1. **Syntax phase** — all files are loaded into a shared ``SourceManager`` and added to a
   single ``Compilation``, so cross-file module instantiation and package imports resolve
   correctly.
2. **Elaboration phase** — ``compilation.getRoot().topInstances`` seeds a depth-first walk
   (:func:`_walk_instances`) that builds two maps keyed by module name: ``inst_map``
   (first elaborated ``InstanceSymbol``, used to enumerate child instances, including
   those inside generate blocks and instance arrays) and ``mod_pkg_imports`` (list of
   imported package names).

Modules that are defined but never instantiated are unreachable via ``topInstances``
and appear in the output with an empty ``insts`` list.
"""

from collections.abc import Iterator
from pathlib import Path

import networkx as nx
from pyslang import (
    Bag,
    DiagnosticEngine,
    SourceManager,
    TextDiagnosticClient,
    TimeScale,
)
from pyslang.parsing import PreprocessorOptions
from pyslang.ast import (
    Compilation,
    CompilationOptions,
    DefinitionKind,
    DefinitionSymbol,
    InstanceBodySymbol,
    InstanceSymbol,
    SymbolKind,
)
from pyslang.syntax import SyntaxKind, SyntaxTree

_IMPORT_KINDS = (SymbolKind.WildcardImport, SymbolKind.ExplicitImport)
# Scopes that can hold module instances without being a module themselves.
_CONTAINER_KINDS = (SymbolKind.GenerateBlock, SymbolKind.GenerateBlockArray, SymbolKind.InstanceArray)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _collect_file_defs(
    compilation: Compilation,
    sm: SourceManager,
    file_paths: list[str],
) -> dict[str, list]:
    """Return a map from resolved canonical path to ordered Module ``DefinitionSymbol`` s.

    Only ``DefinitionKind.Module`` entries are kept — packages and interfaces are excluded.
    """
    file_defs: dict[str, list] = {str(Path(p).resolve()): [] for p in file_paths}
    for defn in compilation.getDefinitions():
        if defn.definitionKind != DefinitionKind.Module:
            continue
        canonical = str(Path(sm.getFileName(defn.location)).resolve())
        if canonical in file_defs:
            file_defs[canonical].append(defn)
    return file_defs


def _collect_pkg_maps(
    compilation: Compilation,
    sm: SourceManager,
    file_paths: list[str],
) -> dict[str, list[str]]:
    """Return file_pkgs: resolved file path -> list of package names defined there."""
    pkg_to_file: dict[str, str] = {}
    for cu in compilation.getRoot().compilationUnits:
        for child in cu:
            if child.kind == SymbolKind.Package:
                canonical = str(Path(sm.getFileName(child.location)).resolve())
                pkg_to_file[child.name] = canonical

    file_pkgs: dict[str, list[str]] = {str(Path(p).resolve()): [] for p in file_paths}
    for pkg_name, canonical in pkg_to_file.items():
        if canonical in file_pkgs:
            file_pkgs[canonical].append(pkg_name)

    return file_pkgs


def _collect_iface_map(
    compilation: Compilation,
    sm: SourceManager,
    file_paths: list[str],
) -> dict[str, list[str]]:
    """Return file_ifaces: resolved file path -> list of interface names defined there."""
    file_ifaces: dict[str, list[str]] = {str(Path(p).resolve()): [] for p in file_paths}
    for defn in compilation.getDefinitions():
        if defn.definitionKind != DefinitionKind.Interface:
            continue
        canonical = str(Path(sm.getFileName(defn.location)).resolve())
        if canonical in file_ifaces:
            file_ifaces[canonical].append(defn.name)
    return file_ifaces


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


def _iter_module_instances(scope) -> Iterator[InstanceSymbol]:
    """Yield every module instance elaborated directly under *scope*.

    Descends into generate blocks, generate-for arrays and instance arrays, but not
    into the bodies of the instances it yields.  Generate branches that were not
    taken (``isUninstantiated``) are skipped.  Interface instances are skipped via
    the ``isModule`` guard (they share ``SymbolKind.Instance`` with module instances).
    """
    for child in scope:
        if child.kind == SymbolKind.Instance:
            if child.isModule:
                yield child
        elif child.kind in _CONTAINER_KINDS:
            if child.kind == SymbolKind.GenerateBlock and child.isUninstantiated:
                continue
            yield from _iter_module_instances(child)


def _walk_instances(
    inst: InstanceSymbol,
    inst_map: dict[str, InstanceSymbol],
    mod_pkg_imports: dict[str, list[str]],
) -> None:
    """Depth-first walk of the elaborated instance tree.

    Populates *inst_map* (first elaborated instance per module name) and
    *mod_pkg_imports* for every reachable module.  The ``name in inst_map`` guard
    prevents revisiting modules that appear in multiple instantiation sites.
    """
    name = inst.definition.name
    if name in inst_map:
        return
    inst_map[name] = inst
    mod_pkg_imports[name] = _collect_imports(inst.body)
    for child in _iter_module_instances(inst.body):
        _walk_instances(child, inst_map, mod_pkg_imports)


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


def _build_def_entry(
    defn: DefinitionSymbol,
    inst_map: dict[str, InstanceSymbol],
    mod_pkg_imports: dict[str, list[str]],
) -> dict:
    """Build the output dict for a single module definition.

    ``inst_name`` is the child's hierarchical path relative to the parent module,
    so instances inside generate blocks or instance arrays get names like
    ``g_loop[0].u_core`` or ``u_arr[1]``.
    """
    insts = []
    parent = inst_map.get(defn.name)
    if parent is not None:
        prefix = parent.hierarchicalPath + "."
        for child in _iter_module_instances(parent.body):
            path = child.hierarchicalPath
            inst_name = path[len(prefix):] if path.startswith(prefix) else child.name
            insts.append({"mod_name": child.definition.name, "inst_name": inst_name})
    return {
        "mod_name": defn.name,
        "pkg_imports": mod_pkg_imports.get(defn.name, []),
        "insts": insts,
    }


def _build_compilation(
    file_paths: list[str],
    sm: SourceManager,
    inc_dirs: list[str] | None = None,
) -> tuple[Compilation, list[SyntaxTree]]:
    """Parse *file_paths* into syntax trees sharing *sm* and add them to a new ``Compilation``.

    *inc_dirs* are passed to the preprocessor as additional `` `include`` search paths.
    Returns the compilation and the syntax trees in *file_paths* order.
    """
    pp_opts = PreprocessorOptions()
    pp_opts.additionalIncludePaths = list(inc_dirs or [])
    bag = Bag([pp_opts, CompilationOptions()])
    bag.compilationOptions.defaultTimeScale = TimeScale.fromString("1ns/1ns")
    compilation = Compilation(bag)
    trees = [SyntaxTree.fromFile(path, sm, bag) for path in file_paths]
    for tree in trees:
        compilation.addSyntaxTree(tree)
    return compilation, trees


def _walk_all(compilation: Compilation) -> tuple[dict[str, InstanceSymbol], dict[str, list[str]]]:
    """Walk every top instance; return ``(inst_map, mod_pkg_imports)`` (see :func:`_walk_instances`)."""
    inst_map: dict[str, InstanceSymbol] = {}
    mod_pkg_imports: dict[str, list[str]] = {}
    for top_inst in compilation.getRoot().topInstances:
        _walk_instances(top_inst, inst_map, mod_pkg_imports)
    return inst_map, mod_pkg_imports


def _build_file_entries(
    compilation: Compilation,
    sm: SourceManager,
    file_paths: list[str],
    trees: list[SyntaxTree],
) -> list[dict]:
    """Walk the elaborated tree and build one output entry per input file (see :func:`parse_files`)."""
    file_defs = _collect_file_defs(compilation, sm, file_paths)
    file_pkgs = _collect_pkg_maps(compilation, sm, file_paths)
    file_ifaces = _collect_iface_map(compilation, sm, file_paths)
    known = {n for names in (*file_pkgs.values(), *file_ifaces.values()) for n in names}

    inst_map, mod_pkg_imports = _walk_all(compilation)

    files_list = []
    for path, tree in zip(file_paths, trees):
        # All three maps are pre-seeded with every input file's canonical path.
        canonical = str(Path(path).resolve())
        files_list.append({
            "file_name": path,
            "pkgs": file_pkgs[canonical],
            "ifaces": file_ifaces[canonical],
            "refs": sorted((_collect_syntax_refs(tree) & known) - set(file_pkgs[canonical]) - set(file_ifaces[canonical])),
            "defs": [_build_def_entry(d, inst_map, mod_pkg_imports) for d in file_defs[canonical]],
        })
    return files_list


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_files(file_paths: list[str], inc_dirs: list[str] | None = None) -> dict:
    """Parse a list of SystemVerilog files and return the module/instance hierarchy.

    All files share a single ``SourceManager`` so cross-file instantiation resolves
    correctly.  See the module docstring for the two-phase elaboration model.
    *inc_dirs* are searched for `` `include`` files.

    Returns a dict with the following top-level keys:

    ``files``
        One entry per input file, each containing:

        * ``file_name`` — original caller string (not resolved).
        * ``pkgs`` — package names defined in this file (internal bookkeeping,
          stripped from YAML output by :func:`svhier.cli._prepare_for_yaml`).
        * ``ifaces`` — interface names defined in this file (internal).
        * ``refs`` — sorted package / interface names defined in *other* input
          files that this file references, by import, ``pkg::name``, interface
          port or interface instance (internal; used by :func:`compute_filelist`).
        * ``defs`` — list of module definitions, each with ``mod_name``,
          ``pkg_imports`` (omitted from YAML when empty), and ``insts``
          (list of ``{"mod_name": str, "inst_name": str}``).

    ``diagnostics``
        List of ``{"severity": str, "file": str, "message": str}`` dicts from
        pyslang's ``DiagnosticEngine`` (internal; stripped from YAML output).

    ``has_errors``
        ``True`` if any error-level diagnostic was issued.  The CLI exits with
        code 1 when this is set.
    """
    sm = SourceManager()
    compilation, trees = _build_compilation(file_paths, sm, inc_dirs)

    files_list = _build_file_entries(compilation, sm, file_paths, trees)

    diags = _collect_diagnostics(compilation, sm)
    return {"files": files_list, "diagnostics": diags[0], "has_errors": diags[1]}


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

    ``nx.topological_sort`` produces the compilation order.  If the graph contains
    a cycle the original input order is returned unchanged.

    *result* is the dict returned by :func:`parse_files`.
    """
    files = result["files"]

    mod_to_file: dict[str, str] = {}
    pkg_to_file: dict[str, str] = {}
    for entry in files:
        fname = entry["file_name"]
        for pkg in [*entry.get("pkgs", []), *entry.get("ifaces", [])]:
            pkg_to_file[pkg] = fname
        for d in entry["defs"]:
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
