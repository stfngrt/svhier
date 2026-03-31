import pyslang
from pathlib import Path


def parse_files(file_paths: list[str]) -> dict:
    source_manager = pyslang.SourceManager()

    trees = [(p, pyslang.SyntaxTree.fromFile(p, source_manager)) for p in file_paths]

    compilation = pyslang.Compilation()
    for _, tree in trees:
        compilation.addSyntaxTree(tree)

    sm = compilation.sourceManager

    # Map resolved path -> ordered list of DefinitionSymbols (Modules only)
    file_defs: dict[str, list] = {str(Path(p).resolve()): [] for p in file_paths}

    for defn in compilation.getDefinitions():
        if defn.definitionKind != pyslang.DefinitionKind.Module:
            continue
        canonical = str(Path(sm.getFileName(defn.location)).resolve())
        if canonical in file_defs:
            file_defs[canonical].append(defn)

    # Walk elaborated instance tree to get InstanceBodySymbol per module name
    body_map: dict[str, pyslang.InstanceBodySymbol] = {}

    def collect_bodies(inst):
        name = inst.definition.name
        if name not in body_map:
            body_map[name] = inst.body
            for child in inst.body:
                if child.kind == pyslang.SymbolKind.Instance:
                    collect_bodies(child)

    for top_inst in compilation.getRoot().topInstances:
        collect_bodies(top_inst)

    # Build output
    files_list = []
    for path in file_paths:
        canonical = str(Path(path).resolve())
        defs_list = []
        for defn in file_defs.get(canonical, []):
            insts = []
            if defn.name in body_map:
                for child in body_map[defn.name]:
                    if child.kind == pyslang.SymbolKind.Instance:
                        insts.append({
                            "mod_name": child.definition.name,
                            "inst_name": child.name,
                        })
            defs_list.append({"mod_name": defn.name, "insts": insts})
        files_list.append({"file_name": path, "defs": defs_list})

    return {"files": files_list}
