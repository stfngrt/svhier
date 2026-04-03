from pathlib import Path

import pytest

from icinst.parser import compute_filelist, parse_files

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
                "pkgs": [],
                "defs": [
                    {"mod_name": "Simple", "pkg_imports": [], "insts": []},
                ],
            }
        ],
        "diagnostics": [],
        "has_errors": False,
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


# --- pkg_imports field tests ---

def test_pkg_imports_populated():
    """ALU and Adder both import MathPkg — pkg_imports must list it."""
    result = parse_files([str(VERILOG_DIR / "pkg_import.sv")])
    defs = _defs_by_name(result)
    assert defs["ALU"]["pkg_imports"] == ["MathPkg"]
    assert defs["Adder"]["pkg_imports"] == ["MathPkg"]


def test_pkg_imports_empty_for_plain_modules():
    """Modules that import nothing must have an empty pkg_imports list."""
    result = parse_files([str(VERILOG_DIR / "test.sv")])
    defs = _defs_by_name(result)
    for mod in ("A", "B", "C", "D"):
        assert defs[mod]["pkg_imports"] == []


# --- pkgs field tests ---

def test_pkgs_lists_packages_defined_in_file():
    """pkg_import.sv defines MathPkg — it must appear in the file's pkgs list."""
    result = parse_files([str(VERILOG_DIR / "pkg_import.sv")])
    assert "MathPkg" in result["files"][0]["pkgs"]


def test_pkgs_empty_for_files_without_packages():
    """Files that define no packages must have pkgs: []."""
    result = parse_files([str(VERILOG_DIR / "test.sv")])
    assert result["files"][0]["pkgs"] == []


# --- compute_filelist tests ---

def test_filelist_pkg_file_before_consumer():
    """pkg_import.sv must precede itself in a single-file list (trivially),
    and when combined with a consumer it must come first."""
    pkg_path = str(VERILOG_DIR / "pkg_import.sv")
    result = parse_files([pkg_path])
    order = compute_filelist(result)
    assert order == [pkg_path]


def test_filelist_cross_file_order():
    """cross_file_sub.sv defines Sub; cross_file_top.sv depends on it —
    sub must precede top in the filelist."""
    sub_path = str(VERILOG_DIR / "cross_file_sub.sv")
    top_path = str(VERILOG_DIR / "cross_file_top.sv")
    result = parse_files([sub_path, top_path])
    order = compute_filelist(result)
    assert order.index(sub_path) < order.index(top_path)


def test_filelist_contains_all_files():
    """compute_filelist must return every input file exactly once."""
    sub_path = str(VERILOG_DIR / "cross_file_sub.sv")
    top_path = str(VERILOG_DIR / "cross_file_top.sv")
    result = parse_files([sub_path, top_path])
    order = compute_filelist(result)
    assert sorted(order) == sorted([sub_path, top_path])


def test_filelist_independent_files_all_present():
    """Files with no mutual dependencies must all appear in the filelist."""
    files = [
        str(VERILOG_DIR / "simple.sv"),
        str(VERILOG_DIR / "test.sv"),
    ]
    result = parse_files(files)
    order = compute_filelist(result)
    assert set(order) == set(files)


# --- diagnostics / broken file tests ---

def test_valid_file_has_no_errors():
    """A well-formed file must produce no diagnostics and has_errors=False."""
    result = parse_files([str(VERILOG_DIR / "simple.sv")])
    assert result["has_errors"] is False
    assert result["diagnostics"] == []


def test_broken_file_has_errors():
    """A file with syntax errors must set has_errors=True."""
    result = parse_files([str(VERILOG_DIR / "broken.sv")])
    assert result["has_errors"] is True


def test_broken_file_has_diagnostics():
    """Syntax errors must appear in the diagnostics list with severity 'error'."""
    result = parse_files([str(VERILOG_DIR / "broken.sv")])
    assert len(result["diagnostics"]) > 0
    assert any(d["severity"] == "error" for d in result["diagnostics"])


def test_broken_file_diagnostic_has_file():
    """Each diagnostic must reference the broken source file."""
    result = parse_files([str(VERILOG_DIR / "broken.sv")])
    error_diags = [d for d in result["diagnostics"] if d["severity"] == "error"]
    assert all("broken.sv" in d["file"] for d in error_diags)


def test_broken_file_returns_valid_structure():
    """Even with errors, parse_files returns a structurally valid result dict."""
    result = parse_files([str(VERILOG_DIR / "broken.sv")])
    assert "files" in result
    assert isinstance(result["files"], list)
    assert isinstance(result["diagnostics"], list)


def test_broken_pkg_ref_has_errors():
    """A module referencing a nonexistent package must produce errors."""
    result = parse_files([str(VERILOG_DIR / "broken_pkg.sv")])
    assert result["has_errors"] is True
    assert len(result["diagnostics"]) > 0


# ---------------------------------------------------------------------------
# UVM-style fixtures  (verilog/uvm/)
# ---------------------------------------------------------------------------

UVM_DIR = VERILOG_DIR / "uvm"
_UVM_FILES = [
    str(UVM_DIR / "uvm_pkg.sv"),
    str(UVM_DIR / "apb_if.sv"),
    str(UVM_DIR / "apb_agent.sv"),
    str(UVM_DIR / "tb_top.sv"),
]


@pytest.fixture(scope="module")
def uvm_result():
    """Parse all four UVM fixture files once and share the result."""
    return parse_files(_UVM_FILES)


def test_uvm_no_errors(uvm_result):
    """All UVM fixture files must compile without errors."""
    assert uvm_result["has_errors"] is False
    assert uvm_result["diagnostics"] == []


def test_uvm_package_defined(uvm_result):
    """uvm_pkg.sv defines the uvm_pkg package."""
    pkg_file = uvm_result["files"][0]
    assert "uvm_pkg" in pkg_file["pkgs"]


def test_uvm_package_has_no_module_defs(uvm_result):
    """uvm_pkg.sv contains only a package — no module definitions."""
    pkg_file = uvm_result["files"][0]
    assert pkg_file["defs"] == []


def test_apb_interface_excluded_from_defs(uvm_result):
    """apb_if is an interface — must not appear in any file's defs."""
    all_mod_names = [
        d["mod_name"]
        for fi in uvm_result["files"]
        for d in fi["defs"]
    ]
    assert "apb_if" not in all_mod_names


def test_apb_agent_file_defs(uvm_result):
    """apb_agent.sv defines exactly apb_agent, apb_driver, apb_monitor."""
    agent_file = uvm_result["files"][2]
    names = {d["mod_name"] for d in agent_file["defs"]}
    assert names == {"apb_agent", "apb_driver", "apb_monitor"}


def test_apb_modules_import_uvm_pkg(uvm_result):
    """apb_agent, apb_driver and apb_monitor all import uvm_pkg."""
    defs = {d["mod_name"]: d for d in uvm_result["files"][2]["defs"]}
    for mod in ("apb_agent", "apb_driver", "apb_monitor"):
        assert "uvm_pkg" in defs[mod]["pkg_imports"], f"{mod} missing uvm_pkg import"


def test_apb_agent_instances(uvm_result):
    """apb_agent instantiates exactly u_drv (apb_driver) and u_mon (apb_monitor)."""
    defs = {d["mod_name"]: d for d in uvm_result["files"][2]["defs"]}
    assert defs["apb_agent"]["insts"] == [
        {"mod_name": "apb_driver",  "inst_name": "u_drv"},
        {"mod_name": "apb_monitor", "inst_name": "u_mon"},
    ]


def test_apb_driver_monitor_are_leaves(uvm_result):
    """apb_driver and apb_monitor have no module instances."""
    defs = {d["mod_name"]: d for d in uvm_result["files"][2]["defs"]}
    assert defs["apb_driver"]["insts"] == []
    assert defs["apb_monitor"]["insts"] == []


def test_tb_top_file_defs(uvm_result):
    """tb_top.sv defines dut and tb_top."""
    tb_file = uvm_result["files"][3]
    names = {d["mod_name"] for d in tb_file["defs"]}
    assert names == {"dut", "tb_top"}


def test_tb_top_instances(uvm_result):
    """tb_top instantiates u_dut (dut) and u_agent (apb_agent).
    The interface instance u_if must be absent — it is not a module."""
    defs = {d["mod_name"]: d for d in uvm_result["files"][3]["defs"]}
    assert defs["tb_top"]["insts"] == [
        {"mod_name": "dut",       "inst_name": "u_dut"},
        {"mod_name": "apb_agent", "inst_name": "u_agent"},
    ]


def test_tb_top_no_interface_instance(uvm_result):
    """No interface instance name should appear in tb_top's insts."""
    defs = {d["mod_name"]: d for d in uvm_result["files"][3]["defs"]}
    inst_names = [i["inst_name"] for i in defs["tb_top"]["insts"]]
    assert "u_if" not in inst_names


def test_uvm_defs_isolated_to_files(uvm_result):
    """Module definitions must not bleed across file boundaries."""
    agent_names = {d["mod_name"] for d in uvm_result["files"][2]["defs"]}
    tb_names    = {d["mod_name"] for d in uvm_result["files"][3]["defs"]}
    assert agent_names.isdisjoint(tb_names)


def test_uvm_filelist_pkg_before_agent(uvm_result):
    """uvm_pkg.sv must precede apb_agent.sv (agent imports the package)."""
    order = compute_filelist(uvm_result)
    pkg_path   = str(UVM_DIR / "uvm_pkg.sv")
    agent_path = str(UVM_DIR / "apb_agent.sv")
    assert order.index(pkg_path) < order.index(agent_path)


def test_uvm_filelist_agent_before_tb(uvm_result):
    """apb_agent.sv must precede tb_top.sv (tb_top instantiates apb_agent)."""
    order = compute_filelist(uvm_result)
    agent_path = str(UVM_DIR / "apb_agent.sv")
    tb_path    = str(UVM_DIR / "tb_top.sv")
    assert order.index(agent_path) < order.index(tb_path)


def test_uvm_filelist_complete(uvm_result):
    """compute_filelist returns all four UVM files."""
    order = compute_filelist(uvm_result)
    assert set(order) == set(_UVM_FILES)
