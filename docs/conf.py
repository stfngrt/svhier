import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

project = "icinst"
author = ""
extensions = [
    "myst_parser",
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
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
