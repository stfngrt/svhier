# Instance tree

The instance tree is svhier's default output. It's printed to stdout and
shows the **elaborated** hierarchy: generate blocks are evaluated, untaken
branches are dropped, and instance arrays are expanded.

```text
$ svhier -r rtl/
alu
├── u_add (adder)
└── u_mux (mux2)
```

Each line is `instance_path (module)`. Instances inside generate blocks or
arrays keep their path relative to the parent, e.g.
`gen_sram[1].data_sram (sram_cache)`.

## Choosing the roots

By default, every module that no other module instantiates becomes a root.
That includes the real top, but also testbenches and unused modules.

Use `--top` to choose the roots yourself. It can be repeated. `--top` is
also passed to slang, so **only that hierarchy is elaborated** (see
{ref}`top-modules`).

```bash
svhier -r rtl/ --top my_soc
svhier -r rtl/ --top my_soc --top my_soc_tb
```

## Markers

| Marker | Meaning |
|--------|---------|
| `(blackbox)` | The module is instantiated but no definition was found. See [](missing.md). |
| `(cycle)` | The module (indirectly) instantiates itself. The branch is cut off here. |

## Piping and other outputs

The tree goes to stdout and everything else goes to stderr, so
`svhier -r rtl/ > tree.txt` captures just the tree. When piped, lines are
never wrapped or truncated.

The tree is skipped automatically when `--yaml` or `--filelist` writes to
stdout, so that data stays clean. To override:

```bash
svhier -r rtl/ --yaml h.yaml --no-tree   # just write the YAML, no tree
svhier -r rtl/ --yaml --tree             # both on stdout (you asked for it)
```
