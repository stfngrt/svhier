import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Python 3.14 argparse emits ANSI escape codes by default; suppress them so
# sphinx-argparse renders clean text instead of raw escape sequences.
os.environ["NO_COLOR"] = "1"

project = "icinst"
author = ""
extensions = [
    "myst_parser",
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinxarg.ext",
]

myst_enable_extensions = [
    "amsmath",
    "attrs_inline",
    "colon_fence",
    "deflist",
    "dollarmath",
    "fieldlist",
    "html_admonition",
    "html_image",
    # "linkify",
    "replacements",
    "smartquotes",
    "strikethrough",
    "substitution",
    "tasklist",
]

source_suffix = {".md": "myst"}
master_doc = "index"
exclude_patterns = ["_build"]
html_theme = "furo"

autodoc_mock_imports = ["pyslang", "networkx", "rich", "ruamel"]
autosummary_generate = False
autodoc_member_order = "bysource"
