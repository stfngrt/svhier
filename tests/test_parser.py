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
