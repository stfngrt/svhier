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

(tree-parameters)=
## Parameters

Each instance shows the children its own parameter values elaborate. If a
module takes a different generate branch for `N=1` than for `N=2`, each
instance lists only its own branch:

```systemverilog
module mid #(parameter int N = 0);
  if (N == 1) begin : g_a  leaf_a u_a(); end
  else        begin : g_b  leaf_b u_b(); end
endmodule

module top;
  mid #(.N(1)) u_m1();
  mid #(.N(2)) u_m2();
endmodule
```

```text
top
├── u_m1 (mid)
│   └── g_a.u_a (leaf_a)
└── u_m2 (mid)
    └── g_b.u_b (leaf_b)
```

The [YAML](outputs.md) describes modules rather than instances, so there `mid`
lists both `g_a.u_a` and `g_b.u_b`.

Instances with the same parameter values count as the same, so they all show
what the first of them elaborates. A `defparam` or `bind` that reaches into just
one of them, or a generate condition that depends on a connected interface's
parameters, can make the others differ from what's shown.

### Across languages

Where another language instantiates a module with several settings, the tree
doesn't know which one that instance uses, so it lists the children of all of
them. This applies to [VHDL](vhdl.md) instances, which GHDL's results don't
tell apart by setting. With only one setting, there's nothing to choose.

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
| `(cycle)` | The module (indirectly) instantiates itself with the same parameter values. The branch is cut off here. |

Recursion that parameters end, such as a module instantiating itself with
`N - 1` until `N` is 0, isn't a cycle. It's shown as far as it goes.

## Piping and other outputs

The tree goes to stdout and everything else goes to stderr, so
`svhier -r rtl/ > tree.txt` captures just the tree. svhier never wraps or
truncates lines itself, and when piped there are no colours.

The tree is printed line by line after `--yaml` and `--filelist` are written.
On a very large design you can stop it, or pipe it to `head`, and still have
those files.

The tree is skipped automatically when `--yaml` or `--filelist` writes to
stdout, so that data stays clean. To override:

```bash
svhier -r rtl/ --yaml h.yaml --no-tree   # just write the YAML, no tree
svhier -r rtl/ --yaml --tree             # both on stdout, YAML first (you asked for it)
```
