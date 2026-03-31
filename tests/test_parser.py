from pathlib import Path

from icinst.parser import parse_files

VERILOG_DIR = Path(__file__).parent.parent / "verilog"


def _defs_by_name(result, file_index=0):
    return {d["mod_name"]: d for d in result["files"][file_index]["defs"]}


def test_simple_single_module():
    path = str(VERILOG_DIR / "simple.sv")
    result = parse_files([path])
    assert result == {
        "files": [
            {
                "file_name": path,
                "defs": [
                    {"mod_name": "Simple", "insts": []},
                ],
            }
        ]
    }


def test_leaf_modules_have_empty_insts():
    result = parse_files([str(VERILOG_DIR / "test.sv")])
    defs = _defs_by_name(result)
    assert defs["A"]["insts"] == []
    assert defs["B"]["insts"] == []


def test_module_with_instances():
    result = parse_files([str(VERILOG_DIR / "test.sv")])
    defs = _defs_by_name(result)
    assert defs["C"]["insts"] == [
        {"mod_name": "A", "inst_name": "I0"},
        {"mod_name": "B", "inst_name": "I1"},
    ]


def test_instance_order_preserved():
    result = parse_files([str(VERILOG_DIR / "test.sv")])
    defs = _defs_by_name(result)
    assert [i["inst_name"] for i in defs["D"]["insts"]] == ["I0", "I1"]
    assert [i["mod_name"] for i in defs["D"]["insts"]] == ["X", "Y"]


def test_definition_order_in_file():
    result = parse_files([str(VERILOG_DIR / "test.sv")])
    names = [d["mod_name"] for d in result["files"][0]["defs"]]
    # pyslang returns definitions in alphabetical order
    assert names == ["A", "B", "C", "D", "X", "Y"]


def test_file_name_preserved():
    path = str(VERILOG_DIR / "simple.sv")
    result = parse_files([path])
    assert result["files"][0]["file_name"] == path


def test_multi_file_input():
    simple = str(VERILOG_DIR / "simple.sv")
    test = str(VERILOG_DIR / "test.sv")
    result = parse_files([simple, test])

    assert len(result["files"]) == 2
    assert result["files"][0]["file_name"] == simple
    assert result["files"][1]["file_name"] == test

    simple_names = [d["mod_name"] for d in result["files"][0]["defs"]]
    test_names = [d["mod_name"] for d in result["files"][1]["defs"]]
    assert simple_names == ["Simple"]
    assert "A" in test_names
    assert "Simple" not in test_names


# --- Package import tests ---

def test_package_excluded_from_defs():
    """MathPkg (package) must not appear in defs — only Module definitions are reported."""
    result = parse_files([str(VERILOG_DIR / "pkg_import.sv")])
    names = [d["mod_name"] for d in result["files"][0]["defs"]]
    assert "MathPkg" not in names


def test_modules_with_package_import_present():
    """Adder and ALU (which import MathPkg) must both appear in defs."""
    result = parse_files([str(VERILOG_DIR / "pkg_import.sv")])
    names = [d["mod_name"] for d in result["files"][0]["defs"]]
    assert "Adder" in names
    assert "ALU" in names


def test_package_import_module_instances():
    """ALU instantiates Adder twice; both instances must be listed."""
    result = parse_files([str(VERILOG_DIR / "pkg_import.sv")])
    defs = _defs_by_name(result)
    assert defs["ALU"]["insts"] == [
        {"mod_name": "Adder", "inst_name": "u_add0"},
        {"mod_name": "Adder", "inst_name": "u_add1"},
    ]
    assert defs["Adder"]["insts"] == []


# --- Deep hierarchy tests ---

def test_deep_hierarchy_leaf():
    result = parse_files([str(VERILOG_DIR / "deep_hierarchy.sv")])
    defs = _defs_by_name(result)
    assert defs["Leaf"]["insts"] == []


def test_deep_hierarchy_mid():
    """Mid (level 2) instantiates Leaf; hierarchy walk must reach it."""
    result = parse_files([str(VERILOG_DIR / "deep_hierarchy.sv")])
    defs = _defs_by_name(result)
    assert defs["Mid"]["insts"] == [{"mod_name": "Leaf", "inst_name": "u_leaf"}]


def test_deep_hierarchy_top():
    """Top (level 1) instantiates Mid twice."""
    result = parse_files([str(VERILOG_DIR / "deep_hierarchy.sv")])
    defs = _defs_by_name(result)
    assert defs["Top"]["insts"] == [
        {"mod_name": "Mid", "inst_name": "u_mid0"},
        {"mod_name": "Mid", "inst_name": "u_mid1"},
    ]


# --- Parameterized module tests ---

def test_parameterized_instances_all_listed():
    """RegBank has three Reg instances with different WIDTH parameters; all must appear."""
    result = parse_files([str(VERILOG_DIR / "parameterized.sv")])
    defs = _defs_by_name(result)
    insts = defs["RegBank"]["insts"]
    assert len(insts) == 3
    assert all(i["mod_name"] == "Reg" for i in insts)


def test_parameterized_instance_names():
    result = parse_files([str(VERILOG_DIR / "parameterized.sv")])
    defs = _defs_by_name(result)
    inst_names = [i["inst_name"] for i in defs["RegBank"]["insts"]]
    assert inst_names == ["u_reg8", "u_reg16", "u_reg32"]


# --- Interface tests ---

def test_interface_excluded_from_defs():
    """BusIf is an interface, not a module — must not appear in defs."""
    result = parse_files([str(VERILOG_DIR / "with_interface.sv")])
    names = [d["mod_name"] for d in result["files"][0]["defs"]]
    assert "BusIf" not in names


def test_interface_instance_excluded_from_insts():
    """System contains a BusIf interface instance plus Sender/Receiver.
    The interface instance must not appear in insts; only the module instances."""
    result = parse_files([str(VERILOG_DIR / "with_interface.sv")])
    defs = _defs_by_name(result)
    system_insts = defs["System"]["insts"]
    assert {"mod_name": "Sender", "inst_name": "u_tx"} in system_insts
    assert {"mod_name": "Receiver", "inst_name": "u_rx"} in system_insts
    # No interface instance
    assert all(i["mod_name"] != "BusIf" for i in system_insts)
    assert len(system_insts) == 2


# --- Cross-file instantiation tests ---

def test_cross_file_defs_isolated():
    """Each file only reports modules defined in that file."""
    sub_path = str(VERILOG_DIR / "cross_file_sub.sv")
    top_path = str(VERILOG_DIR / "cross_file_top.sv")
    result = parse_files([sub_path, top_path])

    sub_names = [d["mod_name"] for d in result["files"][0]["defs"]]
    top_names = [d["mod_name"] for d in result["files"][1]["defs"]]

    assert "Sub" in sub_names
    assert "SubWithChild" in sub_names
    assert "CrossTop" in top_names
    # Sub defined in sub file must not bleed into top file
    assert "Sub" not in top_names
    assert "CrossTop" not in sub_names


def test_cross_file_top_sees_sub_instances():
    """CrossTop (in top file) instantiates Sub (from sub file) — cross-file elaboration."""
    sub_path = str(VERILOG_DIR / "cross_file_sub.sv")
    top_path = str(VERILOG_DIR / "cross_file_top.sv")
    result = parse_files([sub_path, top_path])

    top_defs = _defs_by_name(result, file_index=1)
    assert top_defs["CrossTop"]["insts"] == [
        {"mod_name": "Sub",          "inst_name": "u_sub0"},
        {"mod_name": "Sub",          "inst_name": "u_sub1"},
        {"mod_name": "SubWithChild", "inst_name": "u_swc"},
    ]


def test_cross_file_sub_instances_from_sub_file():
    """SubWithChild (in sub file) correctly lists its own Sub instance."""
    sub_path = str(VERILOG_DIR / "cross_file_sub.sv")
    top_path = str(VERILOG_DIR / "cross_file_top.sv")
    result = parse_files([sub_path, top_path])

    sub_defs = _defs_by_name(result, file_index=0)
    assert sub_defs["Sub"]["insts"] == []
    assert sub_defs["SubWithChild"]["insts"] == [
        {"mod_name": "Sub", "inst_name": "u_sub"}
    ]
