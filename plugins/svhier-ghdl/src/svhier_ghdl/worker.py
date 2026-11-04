# SPDX-License-Identifier: MIT
"""Run libghdl in a child process; everything that touches pyGHDL lives here.

Run as ``python -m svhier_ghdl.worker``.  It has to be a separate process because libghdl
keeps global state, an Ada assertion aborts the whole process, and the wheel's bundled
``libgnat`` only loads if ``LD_LIBRARY_PATH`` is set before Python starts.

Elaborating twice in one process doesn't work: the second run stops on an assertion, and
``libghdl__finalize`` followed by a new setup ends in a storage error.  With ``--serve``, one
process therefore loads the sources and forks a child for each elaboration, so that each
starts from the same state without paying for Python and libghdl again.

pyGHDL wraps parsing, analysis and the node accessors, and those are used as they are.
It has no wrapper for the elaboration calls, so :func:`_elaborate` binds them by symbol
name.  They are GHDL internals, not a stable API, so expect them to change between
versions.  Same call sequence as GHDL's own ``ghdl_synth_configure``.
"""

import ctypes
import gc
import json
import os
import sys
from ctypes import c_bool, c_int32, c_void_p
from typing import Any

import pyGHDL.libghdl as lg
from pyGHDL.libghdl import errorout_console, files_map, flags, libghdl, libraries, name_table
from pyGHDL.libghdl.utils import list_iter, nodes_iter
from pyGHDL.libghdl.vhdl import nodes, sem_lib

_MARKER = "@@svhier-order "
# Date_Valid'First in GHDL; analysis dates count up from here.
_FIRST_DATE = 10
# ctypes variables, which pyGHDL annotates as the type of their value.
_WORK_LIBRARY: Any = libraries.Work_Library
_LIBRARY_LOCATION: Any = libraries.Library_Location


class _AdaBounds(ctypes.Structure):  # pylint: disable=too-few-public-methods
    _fields_ = [("first", ctypes.c_int), ("last", ctypes.c_int)]


def _bind(symbol: str, restype, *argtypes):
    func = getattr(libghdl, symbol)
    func.restype = restype
    func.argtypes = list(argtypes)
    return func


def _error_count() -> int:
    return ctypes.c_int.in_dll(libghdl, "errorout__nbr_errors").value


def _setup(std: str, work: str) -> None:
    lg.initialize()
    errorout_console.Install_Handler()
    lg.set_option(f"--std={std}")
    lg.set_option(f"--work={work}")
    c_bool.in_dll(libghdl, "flags__flag_elaborate").value = True
    c_bool.in_dll(libghdl, "vhdl__canon__canon_flag_concurrent_stmts").value = False
    c_bool.in_dll(libghdl, "vhdl__canon__canon_flag_add_suspend_state").value = False
    # Record which units depend on which, as `ghdl --elab-order` does.
    c_bool.in_dll(libghdl, "vhdl__configuration__flag_build_file_dependence").value = True
    flags.Flag_Elaborate_With_Outdated.value = True
    if lg.analyze_init_status() != 0:
        raise RuntimeError("libghdl failed to initialize")


def _name(identifier: Any) -> str:
    return name_table.Get_Name_Ptr(identifier).lower()


def _design_units(path: str):
    """Parse *path* without analyzing it, and yield its design units."""
    sfe = files_map.Read_Source_File(name_table.Null_Identifier, name_table.Get_Identifier(path))
    unit = nodes.Get_First_Design_Unit(sem_lib.Load_File(sfe))
    while unit:
        following = nodes.Get_Chain(unit)
        yield unit
        unit = following


def _collect_refs(root: int, named: set[tuple[str, str]], components: set[tuple[str, str]]) -> None:
    """Add every ``prefix.name`` below *root*.  Without analysis, it may not be a library.

    A component declaration counts as ``work.name``: GHDL binds a component by default to the
    entity of that name in the library declaring the component, not the one instantiating it.
    Instances can't be used for this anyway, since ``u : name;`` parses as a procedure call.
    These go in *components*, because unlike *named*, a design can compile without the entity.
    """
    for node in nodes_iter(root):
        kind = nodes.Get_Kind(node)
        if kind == nodes.Iir_Kind.Selected_Name:
            prefix = nodes.Get_Prefix(node)
            if nodes.Get_Kind(prefix) == nodes.Iir_Kind.Simple_Name:
                named.add((_name(nodes.Get_Identifier(prefix)), _name(nodes.Get_Identifier(node))))
        elif kind == nodes.Iir_Kind.Component_Declaration:
            components.add(("work", _name(nodes.Get_Identifier(node))))


def _scan(files: list[str]) -> dict[str, dict]:
    """Report what each file defines and references, for choosing its library."""
    kinds = nodes.Iir_Kind
    primary = (kinds.Entity_Declaration, kinds.Package_Declaration, kinds.Package_Instantiation_Declaration,
               kinds.Configuration_Declaration, kinds.Context_Declaration)
    result: dict[str, dict] = {}
    for path in files:
        entry: dict[str, list] = {"units": [], "entities": [], "arch_of": [], "libs": []}
        named: set[tuple[str, str]] = set()
        components: set[tuple[str, str]] = set()
        for unit in _design_units(path):
            item = nodes.Get_Context_Items(unit)
            while item:
                if nodes.Get_Kind(item) == kinds.Library_Clause:
                    entry["libs"].append(_name(nodes.Get_Identifier(item)))
                else:
                    _collect_refs(item, named, components)
                item = nodes.Get_Chain(item)
            library_unit = nodes.Get_Library_Unit(unit)
            kind = nodes.Get_Kind(library_unit)
            if kind in primary:
                entry["units"].append(_name(nodes.Get_Identifier(library_unit)))
            if kind == kinds.Entity_Declaration:
                entry["entities"].append(_name(nodes.Get_Identifier(library_unit)))
            elif kind == kinds.Package_Body:
                entry["arch_of"].append(_name(nodes.Get_Identifier(library_unit)))
            elif kind in (kinds.Architecture_Body, kinds.Configuration_Declaration):
                entity = nodes.Get_Entity_Name(library_unit)
                if nodes.Get_Kind(entity) == kinds.Simple_Name:
                    entry["arch_of"].append(_name(nodes.Get_Identifier(entity)))
            _collect_refs(library_unit, named, components)
        entry["refs"] = sorted(named | components)
        entry["named"] = sorted(named)
        result[path] = entry
    return result


def _load(files: list[str], assigned: dict[str, str], work: str) -> None:
    """Add *files* to their libraries without analyzing them, so their order doesn't matter.

    Dates rise in load order, because an instance with no explicit architecture binds to the
    most recently analyzed one.  Loading uses the current work library, and analysis later
    takes ``work`` to mean the library of the file being analyzed.
    """
    default = _WORK_LIBRARY.value
    add_unit = _bind("libraries__add_design_unit_into_library", None, c_int32, c_bool)
    date = _FIRST_DATE
    for path in files:
        name = assigned.get(path, work)
        # GHDL answers the work library's own name with whichever library is current, so that
        # one can't be looked up.  Force: create the others, since they only exist in memory.
        _WORK_LIBRARY.value = (
            default if name == work else libraries.Get_Library(name_table.Get_Identifier(name), _LIBRARY_LOCATION.value, True))
        for unit in _design_units(path):
            nodes.Set_Chain(unit, 0)
            kind = nodes.Get_Kind(nodes.Get_Library_Unit(unit))
            add_unit(unit, False)
            if kind == nodes.Iir_Kind.Architecture_Body:
                date += 1
                nodes.Set_Date(unit, date)
    _WORK_LIBRARY.value = default


def _ada_string(text: str, keep: list):
    buffer = ctypes.create_string_buffer(text.encode())
    bounds = _AdaBounds(1, len(text))
    keep.extend([buffer, bounds])
    return buffer, ctypes.byref(bounds)


def _file_of(unit: int) -> str:
    return name_table.Get_Name_Ptr(nodes.Get_Design_File_Filename(nodes.Get_Design_File(unit)))


def _file_deps(known: set[str]) -> dict[str, list[str]]:
    """Return ``{file: [files it must be compiled after]}`` for the configured design."""
    find_unit = _bind("libraries__find_design_unit", c_int32, c_int32)
    last = _bind("vhdl__configuration__design_units__last", c_int32)()
    table = ctypes.cast(c_void_p.in_dll(libghdl, "vhdl__configuration__design_units__t"), ctypes.POINTER(c_int32))
    deps: dict[str, list[str]] = {}
    for index in range(last):
        unit = table[index]
        name = _file_of(unit)
        if name not in known:
            continue
        needs = deps.setdefault(name, [])
        for dep in list_iter(nodes.Get_Dependence_List(unit)):
            dep_unit = find_unit(dep)
            dep_name = _file_of(dep_unit) if dep_unit else None
            if dep_name in known and dep_name != name and dep_name not in needs:
                needs.append(dep_name)
    return deps


def _strip_declarations() -> None:
    """Remove declarations, so printing objects only covers interfaces such as generics.

    ``Disp_Hierarchy`` stops with an internal error on declarations it can't print, such as a
    configuration specification.  Only printing is left to do, so nothing needs them anymore.
    """
    kinds = nodes.Iir_Kind
    holders = (kinds.Entity_Declaration, kinds.Architecture_Body, kinds.Block_Statement, kinds.Generate_Statement_Body,
               kinds.Process_Statement, kinds.Sensitized_Process_Statement)
    last = _bind("vhdl__configuration__design_units__last", c_int32)()
    table = ctypes.cast(c_void_p.in_dll(libghdl, "vhdl__configuration__design_units__t"), ctypes.POINTER(c_int32))
    found: list[int] = []
    for index in range(last):
        library_unit = nodes.Get_Library_Unit(table[index])
        if nodes.Get_Kind(library_unit) in (kinds.Entity_Declaration, kinds.Architecture_Body):
            found.extend(n for n in nodes_iter(library_unit) if nodes.Get_Kind(n) in holders)
    for node in found:
        nodes.Set_Declaration_Chain(node, 0)


def _elaborate(request: dict) -> int:
    """Elaborate the top; print the file dependencies, then libghdl's hierarchy, to stdout."""
    top, generics, files = request["top"], request["generics"], request["files"]
    add_override = _bind("vhdl__configuration__add_generic_override", None)
    add_override.argtypes = None
    keep: list = []
    for name, value in generics.items():
        add_override(*_ada_string(name.lower(), keep), *_ada_string(value, keep))

    config = _bind("vhdl__configuration__configure", c_int32, c_int32, c_int32, c_int32)(
        # GHDL's name table holds basic identifiers in lowercase.
        name_table.Get_Identifier(request["top_library"].lower()), name_table.Get_Identifier(top.lower()), 0)
    if not config or _error_count():
        return 3
    entity = _bind("vhdl__utils__get_entity_from_configuration", c_int32, c_int32)(config)
    _bind("vhdl__configuration__apply_generic_override", None, c_int32)(entity)
    _bind("vhdl__configuration__check_entity_declaration_top", None, c_int32, c_bool)(entity, False)
    if _error_count():
        return 4
    instance = _bind("elab__vhdl_insts__elab_top_unit", c_void_p, c_int32)(nodes.Get_Library_Unit(config))
    if not instance or _error_count():
        return 5

    print(_MARKER + json.dumps(_file_deps(set(files))), flush=True)
    if request["objects"]:
        _strip_declarations()
    # recurse, with_objs: the objects include the evaluated generics of each instance.
    _bind("elab__vhdl_debug__disp_hierarchy", None, c_void_p, c_bool, c_bool)(instance, True, request["objects"])
    return 0


def _output_files() -> list[Any]:
    """Return in-memory files for the output of a stage: stdout, then stderr.

    A full disk can't cut them short, which a child would have no way to report.
    """
    return [os.fdopen(os.memfd_create(name), "w+b", buffering=0) for name in ("stdout", "stderr")]


def _redirect_output(files: list[Any]) -> None:
    for descriptor, file in enumerate(files, start=1):
        os.dup2(file.fileno(), descriptor)


def _read_all(file: Any) -> bytes:
    file.seek(0)
    return file.read()


def _serve(requests: Any, replies: Any) -> int:
    """Load the sources, then answer each elaboration request from a forked child.

    The first line of *requests* holds ``{std, work, files, libraries}``, and ``ready`` on
    *replies* says they loaded.  If they don't, this process dies and the caller does without.
    Each later line is a request.  Each reply is a line ``{returncode, stdout, stderr}`` with
    the sizes of the output that follows.  What loading printed starts every output, as it
    does when one process both loads and elaborates.
    """
    sources = json.loads(requests.readline())
    loading = _output_files()
    _redirect_output(loading)
    _setup(sources["std"], sources["work"])
    _load(sources["files"], sources["libraries"], sources["work"].lower())
    sys.stdout.flush()
    sys.stderr.flush()
    ctypes.CDLL(None).fflush(None)  # Or each child would write what libghdl has buffered again.
    loaded = [_read_all(file) for file in loading]
    replies.write(b"ready\n")
    replies.flush()
    gc.freeze()  # Keep garbage collection in the children from copying these pages.
    for line in requests:
        outputs = _output_files()
        for file, text in zip(outputs, loaded):
            file.write(text)
        pid = os.fork()
        if pid == 0:
            requests.close()
            replies.close()
            _redirect_output(outputs)
            sys.exit(_elaborate(json.loads(line)))
        code = os.waitstatus_to_exitcode(os.waitpid(pid, 0)[1])
        stdout, stderr = (_read_all(file) for file in outputs)
        for file in outputs:
            file.close()
        header = {"returncode": code, "stdout": len(stdout), "stderr": len(stderr)}
        replies.write(json.dumps(header).encode() + b"\n" + stdout + stderr)
        replies.flush()
    return 0


def main() -> int:
    """Run the request in argv: JSON ``{op, files, std, work, top, top_library, generics, libraries, objects}``.

    Or with ``--serve REQUEST_FD REPLY_FD``, answer the requests on those pipes.
    """
    if sys.argv[1] == "--serve":
        with os.fdopen(int(sys.argv[2]), "rb") as requests, os.fdopen(int(sys.argv[3]), "wb") as replies:
            return _serve(requests, replies)
    request = json.loads(sys.argv[1])
    _setup(request["std"], request["work"])
    if request["op"] == "scan":
        print(_MARKER + json.dumps(_scan(request["files"])), flush=True)
        return 0
    _load(request["files"], request["libraries"], request["work"].lower())
    return _elaborate(request)


if __name__ == "__main__":
    sys.exit(main())
