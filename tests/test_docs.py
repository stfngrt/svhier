"""The example plugin in the developer docs has to work."""

import re
from pathlib import Path

import pytest

from svhier.frontends import stitch

pytest.importorskip("pyslang")

from svhier.slang_frontend import SlangFrontend  # pylint: disable=wrong-import-position

PLUGIN_PAGE = Path(__file__).parent.parent / "docs" / "dev" / "plugins.md"


def _toy_frontend():
    code = re.search(r"## Example.*?```python\n(.*?)```", PLUGIN_PAGE.read_text(encoding="utf-8"), re.S).group(1)
    namespace: dict = {}
    exec(code, namespace)  # pylint: disable=exec-used
    return namespace["ToyFrontend"]()


def test_example_plugin_stitches_with_slang(tmp_path):
    (tmp_path / "chip.toy").write_text("chip u_core sv_core\nchip u_ghost nowhere\n", encoding="utf-8")
    (tmp_path / "toyleaf.toy").write_text("", encoding="utf-8")
    (tmp_path / "sv_core.sv").write_text("module sv_core; toyleaf u_l(); endmodule\n", encoding="utf-8")
    toy_files = [str(tmp_path / "chip.toy"), str(tmp_path / "toyleaf.toy")]

    result = stitch([(_toy_frontend(), toy_files), (SlangFrontend(), [str(tmp_path / "sv_core.sv")])], ["chip"])

    defs = {d["mod_name"]: d for f in result["files"] for d in f["defs"]}
    assert [i["mod_name"] for i in defs["chip"]["insts"]] == ["sv_core", "nowhere"]
    assert [i["mod_name"] for i in defs["sv_core"]["insts"]] == ["toyleaf"]
    assert "toyleaf" in defs
    assert list(result["missing"]["modules"]) == ["nowhere"]
    assert not result["has_errors"]
