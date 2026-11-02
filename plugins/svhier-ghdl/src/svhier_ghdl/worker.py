# SPDX-License-Identifier: MIT
"""SPIKE: run libghdl in a child process; everything that touches pyGHDL lives here.

Run as ``python -m svhier_ghdl.worker``.  It has to be a separate process because libghdl
keeps global state, an Ada assertion aborts the whole process, and the wheel's bundled
``libgnat`` only loads if ``LD_LIBRARY_PATH`` is set before Python starts.

pyGHDL wraps parsing, analysis and the node accessors, and those are used as they are.
It has no wrapper for the elaboration calls, so :func:`_elaborate` binds them by symbol
name.  They are GHDL internals, not a stable API, so expect them to change between
versions.  Same call sequence as GHDL's own ``ghdl_synth_configure``.
"""

import ctypes
import json
import sys
from ctypes import c_bool, c_int32, c_void_p

import pyGHDL.libghdl as lg
from pyGHDL.libghdl import errorout_console, files_map, flags, libghdl, name_table
from pyGHDL.libghdl.utils import list_iter
from pyGHDL.libghdl.vhdl import nodes, sem_lib

_MARKER = "@@svhier-order "
# Date_Valid'First in GHDL; analysis dates count up from here.
_FIRST_DATE = 10


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


def _load(files: list[str]) -> dict[str, str]:
    """Add *files* to the library without analyzing them, so their order doesn't matter.

    Returns ``{lowercase entity name: file}``.  Dates rise in load order, because an
    instance with no explicit architecture binds to the most recently analyzed one.
    """
    add_unit = _bind("libraries__add_design_unit_into_library", None, c_int32, c_bool)
    entities: dict[str, str] = {}
    date = _FIRST_DATE
    for path in files:
        sfe = files_map.Read_Source_File(name_table.Null_Identifier, name_table.Get_Identifier(path))
        unit = nodes.Get_First_Design_Unit(sem_lib.Load_File(sfe))
        while unit:
            following = nodes.Get_Chain(unit)
            nodes.Set_Chain(unit, 0)
            library_unit = nodes.Get_Library_Unit(unit)
            kind = nodes.Get_Kind(library_unit)
            if kind == nodes.Iir_Kind.Entity_Declaration:
                entities[name_table.Get_Name_Ptr(nodes.Get_Identifier(library_unit)).lower()] = path
            add_unit(unit, False)
            if kind == nodes.Iir_Kind.Architecture_Body:
                date += 1
                nodes.Set_Date(unit, date)
            unit = following
    return entities


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


def _elaborate(top: str, work: str, generics: dict[str, str], files: list[str]) -> int:
    """Elaborate *top*; print the file dependencies, then libghdl's hierarchy, to stdout."""
    add_override = _bind("vhdl__configuration__add_generic_override", None)
    add_override.argtypes = None
    keep: list = []
    for name, value in generics.items():
        add_override(*_ada_string(name.lower(), keep), *_ada_string(value, keep))

    config = _bind("vhdl__configuration__configure", c_int32, c_int32, c_int32, c_int32)(
        # GHDL's name table holds basic identifiers in lowercase.
        name_table.Get_Identifier(work.lower()), name_table.Get_Identifier(top.lower()), 0)
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
    _bind("elab__vhdl_debug__disp_hierarchy", None, c_void_p, c_bool, c_bool)(instance, True, False)
    return 0


def main() -> int:
    """Read a JSON request from argv: ``{op, files, std, work, top, generics}``."""
    request = json.loads(sys.argv[1])
    _setup(request["std"], request["work"])
    entities = _load(request["files"])
    if request["op"] == "units":
        print(_MARKER + json.dumps(entities), flush=True)
        return 0
    return _elaborate(request["top"], request["work"], request["generics"], request["files"])


if __name__ == "__main__":
    sys.exit(main())
