"""Parse SystemVerilog files with pyslang and extract the module/instance hierarchy."""

from pathlib import Path

import networkx as nx
import pyslang

_IMPORT_KINDS = (pyslang.SymbolKind.WildcardImport, pyslang.SymbolKind.ExplicitImport)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _collect_file_defs(
    compilation: pyslang.Compilation,
    sm: pyslang.SourceManager,
    file_paths: list[str],
) -> dict[str, list]:
    """Return a map from resolved canonical path to ordered Module DefinitionSymbols."""
    file_defs: dict[str, list] = {str(Path(p).resolve()): [] for p in file_paths}
    for defn in compilation.getDefinitions():
        if defn.definitionKind != pyslang.DefinitionKind.Module:
            continue
        canonical = str(Path(sm.getFileName(defn.location)).resolve())
        if canonical in file_defs:
            file_defs[canonical].append(defn)
    return file_defs


def _collect_pkg_maps(
    compilation: pyslang.Compilation,
    sm: pyslang.SourceManager,
    file_paths: list[str],
) -> dict[str, list[str]]:
    """Return file_pkgs: resolved file path -> list of package names defined there."""
    pkg_to_file: dict[str, str] = {}
    for cu in compilation.getRoot().compilationUnits:
        for child in cu:
            if child.kind == pyslang.SymbolKind.Package:
                canonical = str(Path(sm.getFileName(child.location)).resolve())
                pkg_to_file[child.name] = canonical

    file_pkgs: dict[str, list[str]] = {str(Path(p).resolve()): [] for p in file_paths}
    for pkg_name, canonical in pkg_to_file.items():
        if canonical in file_pkgs:
            file_pkgs[canonical].append(pkg_name)

    return file_pkgs


def _collect_imports(body: pyslang.InstanceBodySymbol) -> list[str]:
    """Return deduplicated package names imported by an instance body."""
    imports: list[str] = []
    for child in body:
        if child.kind in _IMPORT_KINDS:
            pkg = child.packageName
            if pkg and pkg not in imports:
                imports.append(pkg)
    return imports


def _walk_instances(
    inst: pyslang.InstanceSymbol,
    body_map: dict[str, pyslang.InstanceBodySymbol],
    mod_pkg_imports: dict[str, list[str]],
) -> None:
    """Depth-first walk of the elaborated instance tree.

    Populates body_map and mod_pkg_imports for every reachable module.
    """
    name = inst.definition.name
    if name in body_map:
        return
    body_map[name] = inst.body
    mod_pkg_imports[name] = _collect_imports(inst.body)
    for child in inst.body:
        if child.kind == pyslang.SymbolKind.Instance and child.isModule:
            _walk_instances(child, body_map, mod_pkg_imports)


def _collect_diagnostics(
    compilation: pyslang.Compilation,
    sm: pyslang.SourceManager,
) -> tuple[list[dict], bool]:
    """Issue all compilation diagnostics through a DiagnosticEngine.

    Returns (diagnostics, has_errors) where diagnostics is a list of dicts
    with keys ``severity``, ``file``, and ``message``, and has_errors is True
    if any error-level diagnostic was issued.
    """
    engine = pyslang.DiagnosticEngine(sm)
    client = pyslang.TextDiagnosticClient()
    engine.addClient(client)

    diag_list: list[dict] = []
    for diag in compilation.getAllDiagnostics():
        engine.issue(diag)
        diag_list.append({
            "severity": "error" if diag.isError() else "warning",
            "file": sm.getFileName(diag.location) if diag.location else "",
            "message": str(diag.code).removeprefix("DiagCode(").removesuffix(")"),
        })

    return diag_list, engine.numErrors > 0


def _build_def_entry(
    defn: pyslang.DefinitionSymbol,
    body_map: dict[str, pyslang.InstanceBodySymbol],
    mod_pkg_imports: dict[str, list[str]],
) -> dict:
    """Build the output dict for a single module definition."""
    insts = []
    if defn.name in body_map:
        for child in body_map[defn.name]:
            if child.kind == pyslang.SymbolKind.Instance and child.isModule:
                insts.append({"mod_name": child.definition.name, "inst_name": child.name})
    return {
        "mod_name": defn.name,
        "pkg_imports": mod_pkg_imports.get(defn.name, []),
        "insts": insts,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_files(file_paths: list[str]) -> dict:
    """Parse a list of SystemVerilog files and return the module hierarchy.

    Returns a dict with a ``files`` key containing one entry per input file.
    Each entry lists the module definitions found in that file together with
    their child instances and package imports.
    """
    sm = pyslang.SourceManager()
    trees = [(p, pyslang.SyntaxTree.fromFile(p, sm)) for p in file_paths]

    compilation = pyslang.Compilation()
    for _, tree in trees:
        compilation.addSyntaxTree(tree)

    file_defs = _collect_file_defs(compilation, sm, file_paths)
    file_pkgs = _collect_pkg_maps(compilation, sm, file_paths)

    body_map: dict[str, pyslang.InstanceBodySymbol] = {}
    mod_pkg_imports: dict[str, list[str]] = {}
    for top_inst in compilation.getRoot().topInstances:
        _walk_instances(top_inst, body_map, mod_pkg_imports)

    files_list = []
    for path in file_paths:
        canonical = str(Path(path).resolve())
        defs_list = [
            _build_def_entry(defn, body_map, mod_pkg_imports)
            for defn in file_defs.get(canonical, [])
        ]
        files_list.append({
            "file_name": path,
            "pkgs": file_pkgs.get(canonical, []),
            "defs": defs_list,
        })

    diags = _collect_diagnostics(compilation, sm)
    return {"files": files_list, "diagnostics": diags[0], "has_errors": diags[1]}


def compute_filelist(result: dict) -> list[str]:
    """Return file paths in topological dependency order (dependencies first).

    A file depends on another if any of its modules instantiates a module
    defined there, or imports a package defined there.
    Falls back to the original order if a dependency cycle is detected.
    """
    files = result["files"]

    mod_to_file: dict[str, str] = {}
    pkg_to_file: dict[str, str] = {}
    for entry in files:
        fname = entry["file_name"]
        for pkg in entry.get("pkgs", []):
            pkg_to_file[pkg] = fname
        for d in entry["defs"]:
            mod_to_file[d["mod_name"]] = fname

    deps: dict[str, set[str]] = {entry["file_name"]: set() for entry in files}
    for entry in files:
        fname = entry["file_name"]
        for d in entry["defs"]:
            for inst in d.get("insts", []):
                dep = mod_to_file.get(inst["mod_name"])
                if dep and dep != fname:
                    deps[fname].add(dep)
            for pkg in d.get("pkg_imports", []):
                dep = pkg_to_file.get(pkg)
                if dep and dep != fname:
                    deps[fname].add(dep)

    g: nx.DiGraph = nx.DiGraph()
    g.add_nodes_from(deps.keys())
    for f, fdeps in deps.items():
        for d in fdeps:
            g.add_edge(d, f)

    try:
        return list(nx.topological_sort(g))
    except nx.NetworkXUnfeasible:
        return [entry["file_name"] for entry in files]
