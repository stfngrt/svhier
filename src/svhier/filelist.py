# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Stefan Engert

"""Order files by dependency.  Works on the merged result, whichever languages produced it."""

import networkx as nx


def _file_deps(entry: dict, mod_to_file: dict[str, str], pkg_to_file: dict[str, str]) -> set[str]:
    """Return the other input files that the file *entry* depends on."""
    names = [(mod_to_file, i["mod_name"]) for d in entry["defs"] for i in d.get("insts", [])]
    names += [(pkg_to_file, p) for d in entry["defs"] for p in d.get("pkg_imports", [])]
    names += [(pkg_to_file, r) for r in entry.get("refs", [])]
    deps = {lookup[name] for lookup, name in names if name in lookup}
    deps.update(entry.get("depends_on", []))
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
