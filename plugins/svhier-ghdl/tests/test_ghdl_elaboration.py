"""Compile and elaborate with the GHDL program, using only what svhier wrote.

Needs a ``ghdl`` program as well as pyGHDL: the one in ``SVHIER_GHDL_BIN`` or on the path.
"""

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from ruamel.yaml import YAML
from test_ghdl_generics import _AWKWARD_DECLARATIONS

TESTS = Path(__file__).parent
GHDL = os.environ.get("SVHIER_GHDL_BIN") or shutil.which("ghdl")
pytestmark = pytest.mark.skipif(GHDL is None or importlib.util.find_spec("pyGHDL") is None,
                                reason="needs the ghdl program and pyGHDL")


def _ghdl(build: Path, *args: str, cwd: Path) -> subprocess.CompletedProcess:
    command, *rest = args
    return subprocess.run([GHDL, command, "--std=08", f"--workdir={build}", f"-P{build}", *rest],
                          capture_output=True, text=True, cwd=cwd, check=False)


def _svhier_outputs(design: Path, top: str, out: Path, *inputs: str) -> tuple[list[str], list[dict]]:
    """Return the VHDL files in filelist order and the YAML's file entries."""
    yaml_path, filelist = out / "out.yaml", out / "out.f"
    proc = subprocess.run([sys.executable, "-c", "from svhier.cli import main; main()", *inputs, "--top", top,
                           "--yaml", str(yaml_path), "--filelist", str(filelist), "--no-tree"],
                          capture_output=True, text=True, cwd=design, check=False)
    assert proc.returncode == 0, proc.stderr
    order = [ln for ln in filelist.read_text().splitlines() if ln.endswith((".vhd", ".vhdl"))]
    return order, YAML(typ="safe").load(yaml_path.read_text())["files"]


def _analyze(design: Path, order: list[str], libraries: dict[str, str], build: Path) -> list[str]:
    """Analyze in order; return GHDL's complaints."""
    failures = []
    for path in order:
        proc = _ghdl(build, "-a", f"--work={libraries[path]}", path, cwd=design)
        if proc.returncode != 0:
            failures.append(proc.stderr)
    return failures


def _check_elaborates(design: Path, top: str, tmp_path: Path, *inputs: str) -> None:
    order, entries = _svhier_outputs(design, top, tmp_path, *inputs)
    libraries = {e["file_name"]: e["library"] for e in entries if "library" in e}
    build = tmp_path / "build"
    build.mkdir()
    assert not _analyze(design, order, libraries, build)
    top_library = next(e["library"] for e in entries for d in e["defs"] if d["mod_name"].lower() == top.lower())
    proc = _ghdl(build, "-e", f"--work={top_library}", top, cwd=build)
    assert proc.returncode == 0, proc.stderr


def test_design_with_several_libraries(tmp_path):
    _check_elaborates(TESTS / "libs", "lib_top", tmp_path, "-r", ".")


def test_mixed_design_from_its_vhdl_top(tmp_path):
    _check_elaborates(TESTS / "mixed", "top_vhd", tmp_path, ".")


def test_design_passing_generics_to_sv(tmp_path):
    _check_elaborates(TESTS / "generics", "gen_top", tmp_path, ".")


def test_component_declared_in_a_package_of_a_named_library(tmp_path):
    design = tmp_path / "design"
    design.mkdir()
    (design / "pkg.vhd").write_text("package own_comps is\n  component memo is end component;\nend package;\n")
    (design / "memo.vhd").write_text("entity memo is end entity;\narchitecture a of memo is begin end architecture;\n")
    (design / "top.vhd").write_text(
        "library own;\nuse own.own_comps.all;\nentity p_top is end entity;\n"
        "architecture a of p_top is begin\n  u_memo : memo;\nend architecture;\n")
    _check_elaborates(design, "p_top", tmp_path, ".")


def test_declarations_ghdl_cannot_print(tmp_path):
    design = tmp_path / "design"
    design.mkdir()
    (design / "awk.vhd").write_text(_AWKWARD_DECLARATIONS)
    _check_elaborates(design, "awk_top", tmp_path, ".")


def test_control_one_library_for_everything_fails(tmp_path):
    """Without this, the tests above could pass because they check nothing."""
    order, _ = _svhier_outputs(TESTS / "libs", "lib_top", tmp_path, "-r", ".")
    build = tmp_path / "build"
    build.mkdir()
    failures = _analyze(TESTS / "libs", order, dict.fromkeys(order, "work"), build)
    assert any('cannot find resource library "lib_b"' in f for f in failures)
