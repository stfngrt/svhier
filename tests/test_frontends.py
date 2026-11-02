"""SPIKE: core stitching with stand-in frontends, so these need neither pyslang nor GHDL."""

from pathlib import Path

import svhier.cli
from svhier.filelist import compute_filelist
from svhier.frontends import Request, discover_frontends, empty_fragment, stitch


class FakeFrontend:
    """Defines units from a table: ``{name: (file, [(inst, child)])}``."""

    suffixes = (".fake",)
    catch_all = False

    def __init__(self, name: str, table: dict, *, rejects=(), library=None):
        self.name = name
        self.table = table
        self.library = library or {}
        self.rejects = set(rejects)
        self.requests: list[Request] = []
        self.seen_elsewhere: set[str] = set()

    def add_arguments(self, parser) -> None:
        """No options."""

    def configure(self, args) -> None:
        """No options."""

    def unavailable_reason(self) -> str | None:
        return None

    def extra_inputs(self) -> list[str]:
        return []

    def units(self, files: list[str]) -> dict[str, str]:
        return {n.lower(): file for n, (file, _) in self.table.items()}

    def has_library(self) -> bool:
        return bool(self.library)

    def provide(self, name: str, files: list[str]) -> bool:
        """Move a unit from ``library`` to ``table``."""
        for known, entry in list(self.library.items()):
            if known.lower() == name.lower():
                self.table[known] = self.library.pop(known)
                files.append(entry[0])
                return True
        return False

    def elaborate(self, files: list[str], request: Request) -> dict:
        """Elaborate everything reachable from the roots (or everything, with ``auto``)."""
        self.requests.append(Request(request.auto, list(request.roots)))  # core extends roots between rounds
        self.seen_elsewhere = set(request.defined_elsewhere)
        result = empty_fragment()
        pending = list(self.table) if request.auto else [r.name for r in request.roots]
        seen: set[str] = set()
        by_file: dict[str, list[dict]] = {}
        while pending:
            name = next((n for n in self.table if n.lower() == pending[0].lower()), None)
            pending.pop(0)
            if name is None or name in seen:
                continue
            seen.add(name)
            file, insts = self.table[name]
            by_file.setdefault(file, []).append({
                "mod_name": name, "pkg_imports": [],
                "insts": [{"mod_name": c, "inst_name": i} for i, c in insts],
            })
            pending.extend(c for _, c in insts)
            for _, child in insts:
                if child.lower() not in {n.lower() for n in self.table}:
                    result["unresolved"].setdefault(child, [{"generics": {"N": "3"}, "unresolved": ["X(1+1)"]}])
                    result["missing"]["modules"].setdefault(child, []).append(file)
        result["files"] = [{"file_name": f, "pkgs": [], "ifaces": [], "shadowed": [], "refs": [], "defs": d} for f, d in by_file.items()]
        result["needed"] = None if request.auto else list(by_file)
        result["rejected_roots"] = sorted(self.rejects & {r.name for r in request.roots})
        return result

    def drop_resolved(self, fragment: dict, names: set[str]) -> None:
        for name in names:
            fragment["missing"]["modules"].pop(name, None)


def _pair():
    """a -> B (other language, different case) -> c (back in the first language)."""
    first = FakeFrontend("first", {"a": ("a.x", [("u_b", "B")]), "c": ("c.x", []), "unused": ("unused.x", [])})
    second = FakeFrontend("second", {"b": ("b.y", [("u_c", "C"), ("u_none", "nowhere")])})
    return first, second


def _insts(result: dict, name: str) -> list[tuple[str, str]]:
    return [(i["inst_name"], i["mod_name"]) for f in result["files"] for d in f["defs"] if d["mod_name"] == name for i in d["insts"]]


def test_names_resolve_across_frontends_and_case():
    first, second = _pair()
    result = stitch([(first, ["a.x", "c.x"]), (second, ["b.y"])], ["a"])
    assert _insts(result, "a") == [("u_b", "b")]
    assert _insts(result, "b") == [("u_c", "c"), ("u_none", "nowhere")]


def test_only_truly_missing_names_are_reported():
    first, second = _pair()
    result = stitch([(first, ["a.x", "c.x"]), (second, ["b.y"])], ["a"])
    assert result["missing"]["modules"] == {"nowhere": ["b.y"]}


def test_top_trims_files_across_frontends():
    first, second = _pair()
    result = stitch([(first, ["a.x", "c.x"]), (second, ["b.y"])], ["a"])
    assert sorted(result["needed"]) == ["a.x", "b.y", "c.x"]
    assert "unused.x" not in compute_filelist(result)


def test_top_in_second_frontend_leaves_first_idle_until_needed():
    first, second = _pair()
    result = stitch([(first, ["a.x", "c.x"]), (second, ["b.y"])], ["B"])
    assert result["tops"] == ["b"]
    assert first.requests[0].roots == []
    assert "a.x" not in compute_filelist(result)


def test_parameter_values_reach_the_other_frontend_and_unreadable_ones_are_noted():
    first, second = _pair()
    result = stitch([(first, ["a.x", "c.x"]), (second, ["b.y"])], ["a"])
    assert ("N", "3") in second.requests[-1].roots[0].generics
    assert any("X(1+1)" in note for note in result["frontend_notes"])


def test_rejected_roots_are_noted():
    first = FakeFrontend("first", {"a": ("a.x", [("u_b", "B")]), "c": ("c.x", [])}, rejects=["C"])
    second = FakeFrontend("second", {"b": ("b.y", [("u_c", "C")])})
    result = stitch([(first, ["a.x"]), (second, ["b.y"])], ["a"])
    assert any(note.startswith("C:") for note in result["frontend_notes"])


def test_single_frontend_skips_unit_discovery():
    class NoUnits(FakeFrontend):
        def units(self, files):
            raise AssertionError("not needed with one frontend")

    frontend = NoUnits("only", {"a": ("a.x", [])})
    assert stitch([(frontend, ["a.x"])], ["a"])["needed"] == ["a.x"]


def test_unresolved_name_is_pulled_from_the_library_of_the_language_that_defines_it():
    first = FakeFrontend("first", {"a": ("a.x", [("u_lib", "Lib")])})
    second = FakeFrontend("second", {}, library={"lib": ("lib.y", [("u_c", "deep")]), "other": ("other.y", [])})
    files = []
    result = stitch([(first, ["a.x"]), (second, files)], ["a"])
    assert _insts(result, "lib") == [("u_c", "deep")]
    assert files == ["lib.y"]
    assert "lib" not in result["missing"]["modules"]


def test_top_can_come_from_a_library():
    first = FakeFrontend("first", {"a": ("a.x", [])})
    second = FakeFrontend("second", {}, library={"lib": ("lib.y", [])})
    result = stitch([(first, ["a.x"]), (second, [])], ["lib"])
    assert result["tops"] == ["lib"]


def test_frontends_learn_what_others_define():
    first, second = _pair()
    stitch([(first, ["a.x", "c.x"]), (second, ["b.y"])], ["a"])
    assert first.seen_elsewhere == {"b"}


def test_slang_is_discovered_like_any_plugin():
    assert "slang" in [f.name for f in discover_frontends()]


def test_a_broken_plugin_does_not_break_discovery(monkeypatch):
    class BadEntry:
        name = "bad"

        def load(self):
            raise ImportError("boom")

    monkeypatch.setattr("svhier.frontends.entry_points", lambda group: [BadEntry()])
    errors = []
    assert not discover_frontends(lambda name, exc: errors.append((name, str(exc))))
    assert errors == [("bad", "boom")]


def test_cli_does_not_know_a_language():
    text = Path(svhier.cli.__file__).read_text()
    for name in ("slang", "ghdl"):
        assert name not in text.lower(), name


def test_cli_parser_builds_without_any_plugin():
    assert svhier.cli._build_parser([]).parse_args(["x"]).paths == ["x"]  # pylint: disable=protected-access
